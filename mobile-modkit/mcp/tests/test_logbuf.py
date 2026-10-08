"""Device-free tests: logcat parsing, the ring buffer + cursor model, crash flags, waiting, reconnect
de-duplication and the screenshot coordinate transform."""
import threading
import time

import pytest

from modkit_mcp.logbuf import Deduper, LogRing, parse_epoch_line
from modkit_mcp.screen import ShotTransform


def fill(ring, n, tag="T", level="I", start=0):
    for i in range(start, start + n):
        ring.append(1000.0 + i, 100, 101, level, tag, f"msg {i}")


# ── parsing ──────────────────────────────────────────────────────────────────────────────────
def test_parse_epoch_line():
    p = parse_epoch_line("  1791471631.395  1818  1830 I RVMOD   : [MOD] 1_24.dat -> /sdcard/x\n")
    assert p == (1791471631.395, 1818, 1830, "I", "RVMOD", "[MOD] 1_24.dat -> /sdcard/x")


def test_parse_keeps_colons_in_message_and_skips_headers():
    p = parse_epoch_line("1.000 1 2 E AndroidRuntime: FATAL EXCEPTION: main")
    assert p[4] == "AndroidRuntime" and p[5] == "FATAL EXCEPTION: main"
    assert parse_epoch_line("--------- beginning of main") is None
    assert parse_epoch_line("") is None


# ── cursor model ─────────────────────────────────────────────────────────────────────────────
def test_seq_is_monotonic_and_cursor_tracks_last():
    r = LogRing(10)
    assert r.cursor == 0
    fill(r, 3)
    assert [ln.seq for ln in r.tail(10)] == [1, 2, 3] and r.cursor == 3


def test_query_since_cursor_returns_only_newer():
    r = LogRing(100)
    fill(r, 5)
    q = r.query(since=3)
    assert [ln.seq for ln in q["lines"]] == [4, 5]
    assert q["next_cursor"] == 5 and not q["truncated"] and not q["dropped"]
    assert r.query(since=5)["lines"] == []


def test_eviction_keeps_seq_monotonic_and_flags_dropped():
    r = LogRing(5)
    fill(r, 12)                                   # seqs 1..12, ring keeps 8..12
    assert r.oldest == 8 and r.cursor == 12
    q = r.query(since=2)
    assert [ln.seq for ln in q["lines"]] == [8, 9, 10, 11, 12]
    assert q["dropped"] is True                   # 3..7 were lost
    assert r.query(since=7)["dropped"] is False   # nothing between 7 and the oldest (8) is missing


def test_regex_matches_level_tag_and_message():
    r = LogRing(100)
    r.append(1.0, 1, 1, "I", "RVMOD", "RVEVT {\"type\":\"level_loaded\"}")
    r.append(2.0, 1, 1, "I", "Other", "noise")
    r.append(3.0, 1, 1, "W", "RVMOD", "[MOD] 1_24.dat -> x")
    assert [ln.seq for ln in r.query(r"\[MOD\] 1_24\.dat")["lines"]] == [3]
    assert [ln.seq for ln in r.query(r"^I RVMOD:")["lines"]] == [1]


def test_limit_pages_with_next_cursor():
    r = LogRing(100)
    fill(r, 10)
    q = r.query(since=0, limit=4)
    assert [ln.seq for ln in q["lines"]] == [1, 2, 3, 4] and q["truncated"] and q["next_cursor"] == 4
    q2 = r.query(since=q["next_cursor"], limit=100)
    assert [ln.seq for ln in q2["lines"]] == list(range(5, 11)) and not q2["truncated"]


def test_filtered_query_without_matches_still_advances_cursor():
    r = LogRing(100)
    fill(r, 6)
    q = r.query("nothing-matches", since=0)
    assert q["lines"] == [] and q["next_cursor"] == 6


# ── crash flags ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("level,tag,msg,crash", [
    ("E", "AndroidRuntime", "FATAL EXCEPTION: GLThread 12", True),
    ("I", "AndroidRuntime", ">>>>>> START com.android.internal.os.ZygoteInit uid 0 <<<<<<", False),
    ("F", "libc", "Fatal signal 11 (SIGSEGV), code 1", True),
    ("F", "DEBUG", "pid: 1818, tid: 1830, name: GLThread", True),
    ("I", "DEBUG", "harmless", False),
    ("I", "RVMOD", "RVEVT {...}", False),
    ("W", "zygote", "Fatal signal 6 seen in child", True),
])
def test_crash_classification(level, tag, msg, crash):
    r = LogRing(10)
    assert r.append(1.0, 1, 1, level, tag, msg).is_crash() is crash


def test_crashes_since():
    r = LogRing(100)
    r.append(1.0, 1, 1, "I", "A", "ok")
    r.append(2.0, 1, 1, "F", "libc", "Fatal signal 11")
    r.append(3.0, 1, 1, "I", "A", "ok")
    assert [c.seq for c in r.crashes_since(0)] == [2]
    assert r.crashes_since(2) == []


# ── waiting ──────────────────────────────────────────────────────────────────────────────────
def test_wait_for_sees_line_appended_later():
    r = LogRing(100)
    fill(r, 3)
    since = r.cursor

    def later():
        time.sleep(0.15)
        r.append(9.0, 1, 1, "I", "RVMOD", "[MOD] 1_24.dat -> /x")
    threading.Thread(target=later).start()
    t0 = time.monotonic()
    line, cur = r.wait_for(r"\[MOD\] 1_24", since, timeout=3)
    assert line is not None and line.seq == 4 and cur >= 4
    assert time.monotonic() - t0 < 2


def test_wait_for_ignores_lines_before_since_and_times_out():
    r = LogRing(100)
    r.append(1.0, 1, 1, "I", "RVMOD", "[MOD] 1_24.dat -> /x")   # already happened
    line, cur = r.wait_for(r"\[MOD\]", since=r.cursor, timeout=0.2)
    assert line is None and cur == 1


def test_wait_for_finds_line_already_after_since():
    r = LogRing(100)
    since = r.cursor
    r.append(1.0, 1, 1, "I", "RVMOD", "RVEVT level_loaded")
    line, _ = r.wait_for("level_loaded", since, timeout=0.1)
    assert line is not None and line.seq == 1


# ── reconnect de-dup (logcat -T <last_ts> replays the boundary second) ──────────────────────
def test_deduper_drops_replayed_boundary_and_older_lines():
    d = Deduper()
    assert d.is_new(10.0, "a") and d.is_new(10.5, "b") and d.is_new(10.5, "c")
    # reconnect with -T 10.5: logcat replays the 10.5 lines (and maybe older ones)
    assert not d.is_new(10.0, "a")
    assert not d.is_new(10.5, "b") and not d.is_new(10.5, "c")
    assert d.is_new(10.5, "d")                    # same stamp, genuinely new line
    assert d.is_new(11.0, "e")


# ── screenshot coordinate transform ──────────────────────────────────────────────────────────
def test_shot_transform_spaces():
    tr = ShotTransform(box=(161, 0, 1400, 720), scale=0.5)
    assert tr.to_device(100, 200, "device") == (100, 200)
    assert tr.to_device(10, 20, "shot") == (181, 40)
    assert tr.to_device(0.5, 0.5, "frac") == (round(161 + 0.5 * 1239), 360)
    with pytest.raises(ValueError):
        tr.to_device(0, 0, "bogus")
