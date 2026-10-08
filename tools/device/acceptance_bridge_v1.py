#!/usr/bin/env python3
"""Bridge v1 acceptance — input / bike / scene_dump, driven entirely through the modkit MCP server.

  uv run --project mobile-modkit/mcp python tools/device/acceptance_bridge_v1.py

Checks (no screenshots until the final proof shot):
  1. goto_level(1,24) -> goto_done; `bike` at rest (speed ~0)
  2. throttle held via the game's own gamepad path -> race_start, chassis speed > 3
  3. brake held while moving -> the bike slows down (rider still alive)
  4. throttle + lean held SIMULTANEOUSLY: lean -1 vs lean +1 rotate the chassis in opposite directions
  5. scene_dump returns the CCNode tree with world bounding boxes (HudLayer spans the screen)
"""
import asyncio
import json
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class Run:
    def __init__(self, s):
        self.s = s

    async def tool(self, name, **args):
        res = await self.s.call_tool(name, args)
        texts = [c.text for c in res.content if getattr(c, "type", "") == "text"]
        data = json.loads(texts[-1]) if texts else {}
        if data.get("crashes"):
            print("   !! crash lines:", data["crashes"][:5])
        return data, [c for c in res.content if getattr(c, "type", "") == "image"]

    async def b(self, cmd, **args):
        d, _ = await self.tool("bridge_call", cmd=cmd, args=args, timeout_s=8)
        rep = d.get("bridge", {})
        if not rep.get("ok"):
            raise RuntimeError(f"bridge {cmd}: {rep}")
        return rep["result"]


async def main():
    params = StdioServerParameters(
        command="uv", args=["run", "--quiet", "--project", os.path.join(REPO, "mobile-modkit/mcp"), "modkit-mcp"],
        env={"ADB_SERIAL": "192.168.240.112:5555", "PACKAGE": "com.miniclip.bikerivals",
             "BRIDGE_PORT": "7777", "BRIDGE_SOCKET": "revenant", **os.environ})
    checks = {}
    async with stdio_client(params) as (r, w), ClientSession(r, w) as session:
        await session.initialize()
        run = Run(session)
        assert (await run.tool("device_ensure"))[0].get("ok")
        for _ in range(40):
            d, _ = await run.tool("bridge_call", cmd="ping", timeout_s=3)
            if d.get("bridge", {}).get("ok"):
                break
            await asyncio.sleep(0.5)
        await run.b("overlay", mode="hidden")
        seq = (await run.b("events_since", seq=10 ** 9))["next"]
        await run.b("goto_level", w=1, l=24)
        done = None
        for _ in range(120):
            ev = await run.b("events_since", seq=seq)
            seq = ev["next"]
            done = next((e for e in ev["events"] if e["type"] in ("goto_done", "goto_failed")), None) or done
            if done:
                break
            await asyncio.sleep(0.3)
        checks["goto_done"] = bool(done and done["type"] == "goto_done")
        rest = await run.b("bike")
        checks["bike_at_rest"] = rest["torso"] is not None and rest["torso"]["speed"] < 0.5
        print("rest:", rest["class"], rest["torso"]["pos"], "speed", rest["torso"]["speed"])

        # 2. throttle (gamepad path: HUD rising edge starts the race, the bike gets the held value)
        await run.b("input", throttle=1, hold_ms=6000)
        await asyncio.sleep(0.8)
        b1 = await run.b("bike")
        ev = await run.b("events_since", seq=seq)
        seq = ev["next"]
        checks["race_start"] = any(e["type"] == "race_start" for e in ev["events"])
        checks["throttle_moves_bike"] = b1["torso"]["speed"] > 3
        print("throttle 0.8 s: speed", b1["torso"]["speed"], "run_time", b1.get("run_time"), "dead", b1.get("dead"))

        # 3. brake while moving (before anything that can crash the rider)
        await run.b("input", throttle=0, brake=1, hold_ms=3000)
        s0 = await run.b("bike")
        await asyncio.sleep(0.6)
        s1 = await run.b("bike")
        checks["brake_slows"] = s1["torso"]["speed"] < s0["torso"]["speed"] and not s1.get("dead")
        print(f"brake: speed {s0['torso']['speed']:.2f} -> {s1['torso']['speed']:.2f}  dead {s1.get('dead')}")

        # 4. throttle + lean held together: back (-1) vs forward (+1) -> opposite chassis rotation
        async def lean_delta(v):
            await run.b("input", throttle=1, brake=0, lean=v, hold_ms=6000)
            a = await run.b("bike")
            await asyncio.sleep(0.5)
            b = await run.b("bike")
            return b["torso"]["angle"] - a["torso"]["angle"], b["input"]
        d_back, inp = await lean_delta(-1)
        d_fwd, _ = await lean_delta(+1)
        checks["simultaneous_input"] = inp["throttle"] == 1 and inp["lean"] == -1
        checks["lean_opposite_rotation"] = d_back * d_fwd < 0
        checks["lean_back_rotates_ccw"] = d_back > 0            # Box2D angle: + = counter-clockwise (nose up)
        print(f"lean -1: d_angle {d_back:+.3f} rad   lean +1: d_angle {d_fwd:+.3f} rad")
        await run.b("input", release=True)

        # 5. scene_dump with world bboxes
        sd = await run.b("scene_dump", depth=3, limit=60)
        hud = next((n for n in sd["nodes"] if n["class"] == "HudLayer"), None)
        checks["scene_dump_bbox"] = bool(hud and "world_bbox" in hud and hud["world_bbox"][2] - hud["world_bbox"][0] > 100)
        print("scene_dump:", len(sd["nodes"]), "nodes; HudLayer bbox", hud and hud.get("world_bbox"))

        data, imgs = await run.tool("screen_shot", max_width=960)
        if imgs:
            import base64
            out = os.path.expanduser("~/.cache/revenant/acceptance/v1-proof.jpg")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as f:
                f.write(base64.b64decode(imgs[0].data))
            checks["proof_screenshot"] = out
    print("\nCHECKS:", json.dumps(checks, indent=1))
    ok = all(v for k, v in checks.items() if k != "proof_screenshot")
    print("V1 ACCEPTANCE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
