"""Pure log plumbing (no device, no threads of its own): `logcat -v epoch` parsing, a ring buffer with
monotonic cursors, crash classification and reconnect de-duplication. Unit-tested in tests/."""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from dataclasses import dataclass

# `adb logcat -v epoch`:  "  1791471631.395  1818  1830 I RVMOD   : message"
EPOCH_RE = re.compile(r"^\s*(\d+\.\d+)\s+(\d+)\s+(\d+)\s+([VDIWEFSA])\s+(.*?)\s*: ?(.*)$")
_CRASH_MSG = re.compile(r"FATAL EXCEPTION|Fatal signal \d+|Abort message:|\*\*\* \*\*\* \*\*\*")
_CRASH_TAGS = {"DEBUG", "libc", "crash_dump32", "crash_dump64", "tombstoned"}


@dataclass(frozen=True)
class LogLine:
    seq: int
    ts: float
    pid: int
    tid: int
    level: str
    tag: str
    msg: str

    @property
    def text(self) -> str:
        """What regexes match against: 'L TAG: message'."""
        return f"{self.level} {self.tag}: {self.msg}"

    def is_crash(self) -> bool:
        if self.level in ("F", "A"):
            return True
        if self.tag == "AndroidRuntime" and self.level == "E":
            return True
        if self.tag in _CRASH_TAGS and self.level in ("E", "F"):
            return True
        return bool(_CRASH_MSG.search(self.msg))

    def as_str(self) -> str:
        return f"#{self.seq} {self.ts:.3f} {self.pid}/{self.tid} {self.level} {self.tag}: {self.msg}"


def parse_epoch_line(line: str):
    """-> (ts, pid, tid, level, tag, msg) or None for headers ('--------- beginning of main') / junk."""
    m = EPOCH_RE.match(line.rstrip("\r\n"))
    if not m:
        return None
    ts, pid, tid, level, tag, msg = m.groups()
    return float(ts), int(pid), int(tid), level, tag.strip(), msg


class Deduper:
    """Reconnecting with `logcat -T <last_ts>` replays every line stamped exactly last_ts (and the
    buffer may hand back slightly older ones). Drop lines older than the high-water mark, and lines
    AT the mark that were already seen."""

    def __init__(self) -> None:
        self.high = 0.0
        self._at_high: set = set()

    def is_new(self, ts: float, key) -> bool:
        if ts < self.high:
            return False
        if ts == self.high:
            if key in self._at_high:
                return False
            self._at_high.add(key)
            return True
        self.high = ts
        self._at_high = {key}
        return True


def _compile(pattern):
    if pattern is None or pattern == "":
        return None
    return pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)


class LogRing:
    """Bounded line store. Every line gets a monotonic `seq` (never reused, survives eviction and
    reconnects). A cursor is "the last seq you have seen": query(since=cursor) returns newer lines."""

    def __init__(self, capacity: int = 20000) -> None:
        self.capacity = capacity
        self._lines: deque[LogLine] = deque(maxlen=capacity)
        self._last_seq = 0
        self._cond = threading.Condition()

    # ── state ────────────────────────────────────────────────────────────────────────────────
    @property
    def cursor(self) -> int:
        return self._last_seq

    @property
    def oldest(self) -> int:
        with self._cond:
            return self._lines[0].seq if self._lines else self._last_seq + 1

    def __len__(self) -> int:
        return len(self._lines)

    # ── write ────────────────────────────────────────────────────────────────────────────────
    def append(self, ts: float, pid: int, tid: int, level: str, tag: str, msg: str) -> LogLine:
        with self._cond:
            self._last_seq += 1
            line = LogLine(self._last_seq, ts, pid, tid, level, tag, msg)
            self._lines.append(line)
            self._cond.notify_all()
            return line

    # ── read ─────────────────────────────────────────────────────────────────────────────────
    def _after(self, since: int) -> list[LogLine]:
        """Lines with seq > since (caller holds the lock). seqs are contiguous inside the deque."""
        if not self._lines or since >= self._last_seq:
            return []
        first = self._lines[0].seq
        start = max(0, since + 1 - first)
        return [self._lines[i] for i in range(start, len(self._lines))]

    def query(self, pattern=None, since: int = 0, limit: int = 200, crash_only: bool = False) -> dict:
        rx = _compile(pattern)
        with self._cond:
            oldest = self._lines[0].seq if self._lines else self._last_seq + 1
            dropped = since + 1 < oldest and since < self._last_seq
            scanned = self._after(since)
            end_cursor = self._last_seq
        out: list[LogLine] = []
        next_cursor = end_cursor
        for ln in scanned:
            if crash_only and not ln.is_crash():
                continue
            if rx and not rx.search(ln.text):
                continue
            if len(out) >= limit:
                next_cursor = out[-1].seq          # page: resume after the last returned line
                break
            out.append(ln)
        return {
            "lines": out,
            "next_cursor": next_cursor,
            "truncated": next_cursor != end_cursor,
            "dropped": dropped,
            "oldest": oldest,
        }

    def wait_for(self, pattern, since: int, timeout: float):
        """Block until a line with seq > since matches. -> (LogLine | None, cursor_at_return)."""
        rx = _compile(pattern)
        deadline = time.monotonic() + timeout
        scanned_to = since
        with self._cond:
            while True:
                for ln in self._after(scanned_to):
                    if rx is None or rx.search(ln.text):
                        return ln, self._last_seq
                scanned_to = self._last_seq
                left = deadline - time.monotonic()
                if left <= 0:
                    return None, self._last_seq
                self._cond.wait(left)

    def tail(self, n: int) -> list[LogLine]:
        with self._cond:
            return list(self._lines)[-n:] if n > 0 else []

    def crashes_since(self, since: int) -> list[LogLine]:
        with self._cond:
            return [ln for ln in self._after(since) if ln.is_crash()]
