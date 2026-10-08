"""ONE long-lived `adb logcat -v epoch` reader thread feeding a LogRing. Reconnects automatically
(`adb connect` + `logcat -T <last_ts>` so nothing is replayed twice)."""
from __future__ import annotations

import subprocess
import threading
import time

from .adb import Adb
from .logbuf import Deduper, LogRing, parse_epoch_line


class LogReader:
    def __init__(self, adb: Adb, ring: LogRing, on_reconnect=None) -> None:
        self.adb = adb
        self.ring = ring
        self.dedupe = Deduper()
        self.on_reconnect = on_reconnect          # called (from the reader thread) after each restart
        self.connected = False
        self.restarts = 0
        self.last_error = ""
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="logcat-reader", daemon=True)
        self._thread.start()

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def stop(self) -> None:
        self._stop.set()
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    def _loop(self) -> None:
        backoff = 1.0
        first = True
        while not self._stop.is_set():
            argv = self.adb.argv("logcat", "-v", "epoch")
            if self.dedupe.high > 0:
                argv += ["-T", f"{self.dedupe.high:.3f}"]
            try:
                self._proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                              stdin=subprocess.DEVNULL, text=True, errors="replace",
                                              bufsize=1, start_new_session=True)
            except OSError as e:
                self.last_error = str(e)
                time.sleep(backoff)
                continue
            if not first and self.on_reconnect:
                try:
                    self.on_reconnect()
                except Exception as e:            # never let a callback kill the reader
                    self.last_error = f"on_reconnect: {e}"
            first = False
            got = False
            for raw in self._proc.stdout:          # blocks until a line or EOF
                p = parse_epoch_line(raw)
                if not p:
                    continue
                ts, pid, tid, level, tag, msg = p
                if not self.dedupe.is_new(ts, (pid, tid, level, tag, msg)):
                    continue
                if not got:
                    got, self.connected, backoff = True, True, 1.0
                self.ring.append(ts, pid, tid, level, tag, msg)
            self.connected = False
            self._proc.wait()
            if self._stop.is_set():
                break
            self.restarts += 1
            self.last_error = f"logcat exited (code {self._proc.returncode})"
            self.adb.connect(timeout=8)
            time.sleep(backoff)
            backoff = min(backoff * 2, 5.0)
