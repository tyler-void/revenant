"""Device rig for generator experiments: push a level into a career slot, load it through the bridge, ride
it (bridge input or the in-game bot) under an in-game trace. Library over the modkit MCP plumbing — run
scripts with `uv run --project mobile-modkit/mcp python …`.
"""
import os
import re
import sys
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


class Rig:
    def __init__(self, serial=None):
        self.adb = Adb(serial or os.environ.get("ADB_SERIAL", "192.168.240.112:5555"))
        if waydroid_bin() and waydroid_status().get("Container") == "FROZEN":
            waydroid("app", "launch", PKG, timeout=20)
        self.adb.connect()
        if self.adb.state() != "device":
            raise SystemExit("adb device not available")
        self.ring = LogRing(20000)
        self.reader = LogReader(self.adb, self.ring)
        self.reader.start()
        self.bridge = Bridge(self.adb, 7777, "revenant")
        self.ev_seq = None

    def close(self):
        self.reader.stop()

    # ── bridge helpers ──
    def b(self, cmd, timeout=8, **args):
        r = self.bridge.call(cmd, args, timeout=timeout)
        if not r.get("ok"):
            raise RuntimeError(f"bridge {cmd}: {r.get('error')}")
        return r["result"]

    def wait_bridge(self, secs=40):
        for _ in range(int(secs * 2)):
            try:
                if self.bridge.call("ping", timeout=3).get("ok"):
                    return True
            except BridgeError:
                pass
            time.sleep(0.5)
        return False

    def ensure_game(self):
        """Game running with reader=1 + bridge=1 (relaunch only if a flag had to change or it isn't up)."""
        cur = self.adb.shell(f"cat {MODS}/rvdebug.txt 2>/dev/null", timeout=10).out
        flags = dict(re.findall(r"^([a-z_]+)=(\d+)", cur, re.M))
        restart = flags.get("reader") != "1" or flags.get("bridge") != "1"
        if restart:
            flags.update(reader="1", bridge="1")
            body = "\\n".join(f"{k}={v}" for k, v in flags.items())
            self.adb.shell(f"mkdir -p {MODS} && printf '{body}\\n' > {MODS}/rvdebug.txt", timeout=10)
        if restart or not self.adb.pidof(PKG):
            self.adb.shell(f"am force-stop {PKG}", timeout=15)
            if waydroid_bin() and self.adb.shell("getprop ro.product.device", timeout=5).out.strip().startswith("waydroid"):
                waydroid("app", "launch", PKG, timeout=20)
            else:
                self.adb.shell(f"monkey -p {PKG} -c android.intent.category.LAUNCHER 1", timeout=20)
        if not self.wait_bridge():
            raise SystemExit("bridge never answered (libmod dev build installed? bridge=1?)")
        self.b("overlay", mode="hidden")
        self.ev_seq = self.b("events_since", seq=10 ** 9)["next"]

    def events(self):
        r = self.b("events_since", seq=self.ev_seq)
        self.ev_seq = r["next"]
        return r["events"]

    def log_cursor(self):
        mark = "rig-" + uuid.uuid4().hex[:8]
        self.b("log", msg=mark)
        line, _ = self.ring.wait_for(re.escape("RVMARK " + mark), 0, 30)
        return line.seq if line else 0

    # ── level plumbing ──
    def push_level(self, dat, lid):
        remote = f"{MODS}/{lid}.dat"
        r = self.adb.run("push", dat, remote, timeout=60)
        size = self.adb.shell(f"stat -c %s {remote}", timeout=10).out.strip()
        if not r.ok or size != str(os.path.getsize(dat)):
            raise RuntimeError(f"push failed: {r.err.strip() or 'size mismatch'}")
        return remote

    def remove_level(self, lid):
        self.adb.shell(f"rm -f {MODS}/{lid}.dat", timeout=10)

    def load(self, lid, timeout=60):
        """goto_level -> {ok, goto, level_loaded, mod_line, crashes}. ok = goto_done for this lid."""
        w, l = (int(x) for x in lid.split("_"))
        cursor = self.log_cursor()
        self.events()
        self.b("goto_level", w=w, l=l, max_attempts=2)
        evs, done, t_end = [], None, time.monotonic() + timeout
        while time.monotonic() < t_end and not done:
            e = self.events()
            evs += e
            done = next((x for x in e if x["type"] in ("goto_done", "goto_failed")), None)
            if not done:
                time.sleep(0.3)
        mod, _ = self.ring.wait_for(r"\[MOD\] " + re.escape(lid) + r"\.dat", cursor, 2)
        return {"ok": bool(done and done["type"] == "goto_done" and done.get("lid") == lid), "goto": done,
                "level_loaded": next((x for x in evs if x["type"] == "level_loaded"), None),
                "mod_line": mod.as_str() if mod else None,
                "crashes": [c.as_str() for c in self.ring.crashes_since(cursor)]}

    # ── riding ──
    TRACE_FIELDS = ["t", "x", "y", "vx", "vy", "a", "w", "bx", "by", "fx", "fy", "c", "air", "gt", "dead"]

    def ride_throttle(self, secs, lean=0.0, fields=None, settle=0.6):
        """Full throttle (+ constant lean) for `secs` under a trace; returns (trace, events)."""
        time.sleep(settle)
        self.events()
        self.b("trace_start", fields=fields or self.TRACE_FIELDS)
        self.b("input", throttle=1, lean=lean, hold_ms=int(secs * 1000))
        t_end = time.monotonic() + secs + 0.4
        evs = []
        while time.monotonic() < t_end:
            evs += self.events()
            if any(e["type"] in ("finish", "death") for e in evs):
                time.sleep(0.5)
                break
            time.sleep(0.25)
        tr = self.b("trace_stop", timeout=20)
        self.b("input", release=True)
        evs += self.events()
        return tr, evs

    # ── video (local evidence; game footage — never commit it) ──
    def record_start(self, name):
        """Start `screenrecord` on the device (stopped by record_stop, max 180 s)."""
        import subprocess
        self.adb.shell("mkdir -p /sdcard/rvrec", timeout=5)
        self._rec_remote = f"/sdcard/rvrec/{name}.mp4"
        self._rec = subprocess.Popen(self.adb.argv("shell", f"screenrecord --bit-rate 2000000 --size 780x360 --time-limit 180 {self._rec_remote}"),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.4)

    def record_stop(self, local_path):
        """SIGINT so the mp4 is finalized, pull it, delete the device copy. Returns the local path or None."""
        if not getattr(self, "_rec", None):
            return None
        self.adb.shell("pkill -INT screenrecord", timeout=5)
        try:
            self._rec.wait(timeout=10)
        except Exception:
            self._rec.kill()
        self._rec = None
        time.sleep(0.6)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        r = self.adb.run("pull", self._rec_remote, local_path, timeout=60)
        self.adb.shell(f"rm -f {self._rec_remote}", timeout=5)
        return local_path if r.ok and os.path.exists(local_path) else None

    def screenshot(self, path):
        img = screen.capture(self.adb)
        data, _, _, _ = screen.render(img, 960, True, "jpeg", 82)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path
