#!/usr/bin/env python3
"""Push an exported level into the running game and prove it loaded — the editor's "Push to game".

  uv run --project mobile-modkit/mcp python tools/level-editor/push_to_game.py --dat X.dat --lid 1_25 [--play]

Steps (the same plumbing as the modkit MCP tools, used as a library):
  1. ensure mods/rvdebug.txt has reader=1 + bridge=1 (restart the game only if a flag had to change)
  2. adb push X.dat -> mods/<lid>.dat (size-verified)
  3. bridge: overlay hidden, a unique RVMARK so the log ring is provably caught up, goto_level(w, l)
  4. wait for "[MOD] <lid>.dat" in logcat and goto_done / level_loaded events
  5. --play: hold throttle 4 s and sample `state` twice (run_time / mono, race_start, death)
  6. ONE screenshot (letterbox-cropped JPEG)
Prints a JSON report on stdout; the screenshot goes to --out (default ~/.cache/revenant/editor).
"""
import argparse
import json
import os
import re
import sys
import threading
import time
import uuid

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mobile-modkit", "mcp"))

from modkit_mcp import screen                                   # noqa: E402
from modkit_mcp.adb import Adb, waydroid, waydroid_bin, waydroid_status   # noqa: E402
from modkit_mcp.bridge import Bridge, BridgeError              # noqa: E402
from modkit_mcp.logbuf import LogRing                          # noqa: E402
from modkit_mcp.logreader import LogReader                     # noqa: E402

PKG = "com.miniclip.bikerivals"
MODS = f"/sdcard/Android/data/{PKG}/files/mods"
MAP_SIZES = {1: 30, 2: 30, 3: 30, 4: 15}           # career-map dots per world (goto_level reach)


class Report:
    def __init__(self):
        self.d = {"ok": False, "steps": []}
        self.t0 = time.monotonic()

    def step(self, name, ok, **detail):
        self.d["steps"].append({"step": name, "ok": bool(ok), "t": round(time.monotonic() - self.t0, 2), **detail})
        return ok

    def fail(self, why):
        self.d["error"] = why
        print(json.dumps(self.d))
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dat", required=True)
    ap.add_argument("--lid", required=True)
    ap.add_argument("--serial", default=os.environ.get("ADB_SERIAL", "192.168.240.112:5555"))
    ap.add_argument("--play", action="store_true")
    ap.add_argument("--out", default=os.path.expanduser("~/.cache/revenant/editor"))
    a = ap.parse_args()
    rep = Report()
    rep.d["lid"] = a.lid

    m = re.fullmatch(r"(\d+)_(\d+)", a.lid)
    if not m:
        rep.fail(f"lid {a.lid!r} is not <world>_<level>")
    w, l = int(m.group(1)), int(m.group(2))
    if w not in MAP_SIZES or not 1 <= l <= MAP_SIZES[w]:
        rep.fail(f"{a.lid} is not on the career map (worlds 30/30/30/15) — goto_level can't reach it")
    if not os.path.isfile(a.dat):
        rep.fail(f"no such file {a.dat}")

    adb = Adb(a.serial)
    if waydroid_bin() and waydroid_status().get("Container") == "FROZEN":
        waydroid("app", "launch", PKG, timeout=20)
    adb.connect()
    if not rep.step("adb", adb.state() == "device", serial=a.serial):
        rep.fail("adb device not available")

    ring = LogRing(20000)
    reader = LogReader(adb, ring)
    reader.start()

    # 1. flags
    cur = adb.shell(f"cat {MODS}/rvdebug.txt 2>/dev/null", timeout=10).out
    flags = dict(re.findall(r"^([a-z_]+)=(\d+)", cur, re.M))
    need_restart = flags.get("reader") != "1" or flags.get("bridge") != "1"
    if need_restart:
        flags.update(reader="1", bridge="1")
        body = "\\n".join(f"{k}={v}" for k, v in flags.items())
        adb.shell(f"mkdir -p {MODS} && printf '{body}\\n' > {MODS}/rvdebug.txt", timeout=10)
    rep.step("flags", True, flags=flags, changed=need_restart)

    # 2. push
    remote = f"{MODS}/{a.lid}.dat"
    adb.shell(f"mkdir -p {MODS}", timeout=10)
    r = adb.run("push", a.dat, remote, timeout=60)
    rsize = adb.shell(f"stat -c %s {remote}", timeout=10).out.strip()
    size = os.path.getsize(a.dat)
    if not rep.step("push", r.ok and rsize == str(size), remote=remote, bytes=size):
        rep.fail("push failed: " + (r.err.strip() or "size mismatch"))

    # 3. app + bridge
    if need_restart or not adb.pidof(PKG):
        adb.shell(f"am force-stop {PKG}", timeout=15)
        if waydroid_bin() and adb.shell("getprop ro.product.device", timeout=5).out.strip().startswith("waydroid"):
            waydroid("app", "launch", PKG, timeout=20)
        else:
            adb.shell(f"monkey -p {PKG} -c android.intent.category.LAUNCHER 1", timeout=20)
        rep.step("launch", True, reason="flags changed" if need_restart else "not running")
    bridge = Bridge(adb, 7777, "revenant")
    pong = None
    for _ in range(60):
        try:
            rr = bridge.call("ping", timeout=3)
            if rr.get("ok"):
                pong = rr["result"]
                break
        except BridgeError:
            pass
        time.sleep(0.5)
    if not rep.step("bridge", pong is not None, build=(pong or {}).get("build")):
        rep.fail("bridge never answered (is the libmod build installed?)")

    bridge.call("overlay", {"mode": "hidden"})
    mark = "push-" + uuid.uuid4().hex[:8]
    bridge.call("log", {"msg": mark})
    line, _ = ring.wait_for(re.escape("RVMARK " + mark), 0, 30)      # ring caught up to "now"
    if not rep.step("log_sync", line is not None):
        rep.fail("log reader never caught up (logcat not streaming?)")
    cursor = line.seq
    ev_seq = bridge.call("events_since", {"seq": 10 ** 9})["result"]["next"]

    # 4. load
    g = bridge.call("goto_level", {"w": w, "l": l, "max_attempts": 2})
    rep.step("goto_level", g.get("ok"), reply=g.get("result") or g.get("error"))
    mod, _ = ring.wait_for(r"\[MOD\] " + re.escape(a.lid) + r"\.dat", cursor, 60)
    rep.d["mod_line"] = mod.as_str() if mod else None
    rep.step("mod_redirect", mod is not None)
    events, done, deadline = [], None, time.monotonic() + 60
    while time.monotonic() < deadline and not done:
        res = bridge.call("events_since", {"seq": ev_seq})["result"]
        ev_seq = res["next"]
        events += res["events"]
        done = next((e for e in res["events"] if e["type"] in ("goto_done", "goto_failed")), None)
        if not done:
            time.sleep(0.4)
    rep.d["goto"] = done
    rep.d["level_loaded"] = next((e for e in events if e["type"] == "level_loaded"), None)
    loaded = bool(done and done["type"] == "goto_done" and done.get("lid") == a.lid)
    rep.step("level_loaded", loaded)

    # 5. smoke ride
    if a.play and loaded:
        fr = adb.shell("dumpsys input | grep -m1 'GameActivity.*frame='", timeout=10).out
        mm = re.search(r"frame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]", fr)
        x0, y0, x1, y1 = map(int, mm.groups()) if mm else (0, 0, 1560, 720)
        tx, ty = x0 + int(0.85 * (x1 - x0)), y0 + int(0.6 * (y1 - y0))
        hold = threading.Thread(target=adb.shell, args=(f"input swipe {tx} {ty} {tx} {ty} 4000", 20), daemon=True)
        hold.start()
        time.sleep(1.0)
        s1 = bridge.call("state")["result"]
        time.sleep(2.5)
        s2 = bridge.call("state")["result"]
        res = bridge.call("events_since", {"seq": ev_seq})["result"]
        events += res["events"]
        dm = s2["mono"] - s1["mono"]
        dr = (s2.get("run_time") or 0) - (s1.get("run_time") or 0)
        rep.d["play"] = {"race_start": any(e["type"] == "race_start" for e in events),
                         "death": any(e["type"] == "death" for e in events),
                         "run_time": [s1.get("run_time"), s2.get("run_time")],
                         "ratio": round(dr / dm, 4) if dm > 0 else None}
        rep.step("play", rep.d["play"]["race_start"] and dr > 0)
        hold.join(timeout=8)

    # 6. proof screenshot
    os.makedirs(a.out, exist_ok=True)
    img = screen.capture(adb)
    data, fmt, size, _ = screen.render(img, 960, True, "jpeg", 82)
    shot = os.path.join(a.out, f"push-{a.lid}.jpg")
    with open(shot, "wb") as f:
        f.write(data)
    rep.d["shot"] = shot
    rep.d["events"] = [e for e in events if e["type"] not in ("scene_ready",)]
    rep.d["crashes"] = [c.as_str() for c in ring.crashes_since(cursor)]
    rep.d["ok"] = loaded and not rep.d["crashes"] and (not a.play or rep.d.get("play", {}).get("race_start", False))
    reader.stop()
    print(json.dumps(rep.d))
    sys.exit(0 if rep.d["ok"] else 1)


if __name__ == "__main__":
    main()
