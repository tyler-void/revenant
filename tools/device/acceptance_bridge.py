#!/usr/bin/env python3
"""Phase-1.5 acceptance: drive Bike Rivals entirely through the modkit MCP server (stdio, exactly as
.mcp.json launches it) — zero screenshots until the final proof shot.

  uv run --project mobile-modkit/mcp python tools/device/acceptance_bridge.py [--out DIR]

Preconditions it sets up itself (via MCP tools): mods/rvdebug.txt = reader=1 + bridge=1, and the ORIGINAL
1_24.dat (from build/work/assets/unpack, gitignored) pushed as mods/1_24.dat.
Writes a JSON transcript + the proof screenshot to --out (default ~/.cache/revenant/acceptance).
"""
import argparse
import asyncio
import json
import os
import re
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODS = "/sdcard/Android/data/com.miniclip.bikerivals/files/mods"
LEVEL = os.path.join(REPO, "build/work/assets/unpack/1_24.dat")


class Run:
    def __init__(self, session, log):
        self.s, self.log = session, log

    async def tool(self, name, **args):
        t0 = time.monotonic()
        res = await self.s.call_tool(name, args)
        texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
        images = [c for c in res.content if getattr(c, "type", "") == "image"]
        data = json.loads(texts[-1]) if texts else {}
        entry = {"tool": name, "args": args, "ms": round((time.monotonic() - t0) * 1000), "result": data}
        if images:
            entry["images"] = len(images)
        self.log.append(entry)
        crash = data.get("crashes")
        print(f"[{name}] {json.dumps(args)[:90]} -> {json.dumps(data)[:240]}")
        if crash:
            print(f"   !! crash lines: {crash}")
        return data, images

    async def bridge(self, cmd, timeout_s=5, **args):
        data, _ = await self.tool("bridge_call", cmd=cmd, args=args, timeout_s=timeout_s)
        rep = data.get("bridge", {})
        if not rep.get("ok"):
            raise RuntimeError(f"bridge {cmd} failed: {rep}")
        return rep["result"], data


async def main(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    log, checks = [], {}
    params = StdioServerParameters(
        command="uv", args=["run", "--quiet", "--project", os.path.join(REPO, "mobile-modkit/mcp"), "modkit-mcp"],
        # defaults first, so the caller's environment (e.g. ADB_SERIAL for a phone) overrides them
        env={"ADB_SERIAL": "192.168.240.112:5555", "PACKAGE": "com.miniclip.bikerivals",
             "BRIDGE_PORT": "7777", "BRIDGE_SOCKET": "revenant", **os.environ})
    async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        run = Run(session, log)

        dev, _ = await run.tool("device_ensure")
        assert dev.get("ok"), dev
        # preconditions: flags + the ORIGINAL level in mods/
        await run.tool("shell", cmd=f"mkdir -p {MODS} && printf 'reader=1\\nbridge=1\\n' > {MODS}/rvdebug.txt && cat {MODS}/rvdebug.txt")
        p, _ = await run.tool("push", local_path=LEVEL, remote_path=f"{MODS}/1_24.dat")
        assert p.get("ok"), p

        await run.tool("app_stop")
        launch, _ = await run.tool("app_launch")
        assert launch.get("ok"), launch
        # bridge comes up once libgame is mapped and the GL thread renders
        for _ in range(40):
            d, _ = await run.tool("bridge_call", cmd="ping", timeout_s=3)
            if d.get("bridge", {}).get("ok"):
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("bridge never answered ping")

        await run.bridge("overlay", mode="hidden")
        ev_seq = (await run.bridge("events_since", seq=10**9))[0]["next"]
        _, env = await run.bridge("goto_level", w=1, l=24)
        cursor = env["log_cursor"]                       # captured BEFORE the level loads

        mod, _ = await run.tool("logs_wait_for", regex=r"\[MOD\] 1_24\.dat", since_cursor=cursor, timeout_s=60)
        checks["mod_redirect_logged"] = mod.get("matched", False)

        events, done = [], None
        t_end = time.monotonic() + 60
        while time.monotonic() < t_end and not done:
            res, _ = await run.bridge("events_since", seq=ev_seq)
            ev_seq = res["next"]
            events += res["events"]
            done = next((e for e in res["events"] if e["type"] in ("goto_done", "goto_failed")), None)
            if not done:
                await asyncio.sleep(0.5)
        loaded = [e for e in events if e["type"] == "level_loaded"]
        checks["level_loaded_1_24"] = any(e.get("lid") == "1_24" for e in loaded)
        checks["goto_done"] = bool(done and done["type"] == "goto_done")

        # throttle: hold the right part of the game window (frame from dumpsys input; no screenshot)
        fr, _ = await run.tool("shell", cmd="dumpsys input | grep -m1 'GameActivity.*frame='")
        m = re.search(r"frame=\[(\d+),(\d+)\]\[(\d+),(\d+)\]", fr.get("stdout", ""))
        x0, y0, x1, y1 = map(int, m.groups()) if m else (0, 0, 1560, 720)
        tx, ty = x0 + int(0.85 * (x1 - x0)), y0 + int(0.6 * (y1 - y0))
        await run.tool("input_long_press", x=tx, y=ty, duration_ms=7000, wait=False)
        await asyncio.sleep(1.0)
        a, _ = await run.bridge("state")
        await asyncio.sleep(5.0)
        b, _ = await run.bridge("state")
        # run_time = Manager.time_ (the HUD timer); mono = CLOCK_MONOTONIC sampled on the GL thread in the
        # SAME frame, so no adb round-trip noise. The game adds exactly 1/60 s per physics step and steps
        # once per rendered frame, so the expected ratio is fps/60 (Waydroid renders ~61 fps -> ~1.018).
        dr = (b.get("run_time") or 0) - (a.get("run_time") or 0)
        dm = b["mono"] - a["mono"]
        frames = b["frame"] - a["frame"]
        ratio = dr / dm if dm > 0 else 0.0
        fps = frames / dm if dm > 0 else 0.0
        per_frame = dr / frames if frames else 0.0
        checks["timer_ratio"] = round(ratio, 4)
        checks["render_fps"] = round(fps, 2)
        checks["run_time_per_frame"] = round(per_frame, 6)
        checks["timer_ok"] = 0.95 <= ratio <= 1.05 and abs(per_frame - 1 / 60) < 0.0005
        print(f"timer: run_time {a.get('run_time')} -> {b.get('run_time')} over GL-mono {dm:.3f}s => ratio {ratio:.4f} "
              f"(fps {fps:.2f}, fps/60 {fps / 60:.4f}, {per_frame:.5f} s/frame)")
        await asyncio.sleep(1.5)
        res, _ = await run.bridge("events_since", seq=ev_seq)
        events += res["events"]
        checks["race_start_event"] = any(e["type"] == "race_start" for e in events)

        # the one screenshot: visual proof
        data, imgs = await run.tool("screen_shot", max_width=960)
        if imgs:
            import base64
            path = os.path.join(out_dir, "proof.jpg")
            with open(path, "wb") as f:
                f.write(base64.b64decode(imgs[0].data))
            checks["proof_screenshot"] = path
        # crash lines first reported AFTER the launch (the ring replays older device history at start)
        li = next(i for i, e in enumerate(log) if e["tool"] == "app_launch")
        checks["crash_lines"] = sum(len(e["result"].get("crashes", [])) for e in log[li + 1:])

    with open(os.path.join(out_dir, "transcript.json"), "w") as f:
        json.dump({"checks": checks, "events": events, "calls": log}, f, indent=1)
    print("\nCHECKS:", json.dumps(checks, indent=1))
    ok = all(checks.get(k) for k in ("mod_redirect_logged", "level_loaded_1_24", "goto_done", "timer_ok")) and checks["crash_lines"] == 0
    print("ACCEPTANCE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/.cache/revenant/acceptance"))
    sys.exit(asyncio.run(main(ap.parse_args().out)))
