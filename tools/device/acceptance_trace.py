#!/usr/bin/env python3
"""Bridge trace acceptance (G1b) — per-frame bike samples recorded in-game, driven through the modkit MCP.

  uv run --project mobile-modkit/mcp python tools/device/acceptance_trace.py [--level 1_24] [--secs 4]

Checks:
  1. ground flags (`c` = the game's own Bike.backGround_/frontGround_) agree with the geometry: wheel height
     above the level's terrain envelope ~= wheel radius when grounded, larger when airborne
  2. trace_start -> throttle for N s -> trace_stop: one sample per physics step (run_time deltas = 1/60),
     sample count ~= run_time span * 60, x advances, the last sample agrees with a `bike` read
Writes the raw trace to ~/.cache/revenant/acceptance/trace-<lid>.json (local, not committed).
"""
import argparse
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def ground_check(lid, D):
    """Back-wheel height above the level's static terrain envelope, split by the game's back-ground flag.
    Needs the LOCAL decoded corpus (tools/levelgen/decode_corpus.py); returns None without it."""
    p = os.path.expanduser(f"~/.cache/revenant/corpus/{lid}.level.json")
    if not os.path.exists(p):
        return None
    sys.path.insert(0, os.path.join(REPO, "tools", "levelgen"))
    import corpus_stats as cs
    with open(p) as f:
        E = json.load(f)["Entities"]
    edges = [ed for e in E if cs.is_ground(e) for ed in cs.top_edges(cs.outline(e) or [])]

    def top(x):
        ys = [y0 + (y1 - y0) * (x - x0) / (x1 - x0) for x0, y0, x1, y1 in edges if x0 <= x <= x1]
        return max(ys) if ys else None
    on, off = [], []
    for bx, by, c in zip(D["bx"], D["by"], D["c"]):
        g = top(bx)
        if g is not None:
            (on if int(c) & 1 else off).append(by - g)

    def q(v):
        v = sorted(v)
        return (len(v), round(v[len(v) // 10], 2), round(v[len(v) // 2], 2), round(v[9 * len(v) // 10], 2)) if v else None
    return q(on), q(off)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", default="1_24")
    ap.add_argument("--secs", type=float, default=4.0)
    a = ap.parse_args()
    w, l = (int(x) for x in a.level.split("_"))
    params = StdioServerParameters(
        command="uv", args=["run", "--quiet", "--project", os.path.join(REPO, "mobile-modkit/mcp"), "modkit-mcp"],
        env={"ADB_SERIAL": "192.168.240.112:5555", "PACKAGE": "com.miniclip.bikerivals",
             "BRIDGE_PORT": "7777", "BRIDGE_SOCKET": "revenant", **os.environ})
    checks = {}
    async with stdio_client(params) as (r, wr), ClientSession(r, wr) as s:
        await s.initialize()

        async def b(cmd, timeout_s=8, **args):
            res = await s.call_tool("bridge_call", {"cmd": cmd, "args": args, "timeout_s": timeout_s})
            d = json.loads([c.text for c in res.content if getattr(c, "type", "") == "text"][-1])
            rep = d.get("bridge", {})
            if not rep.get("ok"):
                raise RuntimeError(f"bridge {cmd}: {rep}")
            return rep["result"]

        await s.call_tool("device_ensure", {})
        for _ in range(60):
            try:
                await b("ping", timeout_s=3)
                break
            except RuntimeError:
                await asyncio.sleep(0.5)
        await b("overlay", mode="hidden")
        seq = (await b("events_since", seq=10 ** 9))["next"]
        await b("goto_level", w=w, l=l)
        done = None
        for _ in range(150):
            ev = await b("events_since", seq=seq)
            seq = ev["next"]
            done = next((e for e in ev["events"] if e["type"] in ("goto_done", "goto_failed")), None)
            if done:
                break
            await asyncio.sleep(0.3)
        checks["goto_done"] = bool(done and done["type"] == "goto_done")
        st = await b("trace_start", fields=["frame", "t", "x", "y", "vx", "vy", "a", "w", "bx", "by", "c", "air", "gt", "ct",
                                            "dead", "thr"])
        print("trace_start:", st)
        await b("input", throttle=1, hold_ms=int(a.secs * 1000))
        await asyncio.sleep(a.secs + 0.3)
        live = await b("bike")
        tr = await b("trace_stop", timeout_s=15)
        D, n = tr["data"], tr["n"]
        out = os.path.expanduser(f"~/.cache/revenant/acceptance/trace-{a.level}.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            json.dump(tr, f)
        t = D["t"]
        dts = [t[i + 1] - t[i] for i in range(n - 1)]
        span = t[-1] - t[0] if n else 0
        print(f"n={n}  run_time {t[0]:.3f}..{t[-1]:.3f} (span {span:.3f}s)  frames {D['frame'][0]:.0f}..{D['frame'][-1]:.0f}")
        print(f"dt min {min(dts):.5f} max {max(dts):.5f}  x {D['x'][0]:.2f} -> {D['x'][-1]:.2f}  "
              f"max speed {max((vx * vx + vy * vy) ** 0.5 for vx, vy in zip(D['vx'], D['vy'])):.2f}")
        cbits = D["c"]
        both = sum(1 for c in cbits if int(c) & 3 == 3) / max(1, n)
        air = sum(1 for c in cbits if int(c) & 3 == 0) / max(1, n)
        print(f"game ground flags: both wheels {both:.0%}, airborne {air:.0%}; max airTime {max(D['air']):.2f}s")
        geo = ground_check(a.level, D)
        if geo:
            print("back-wheel height above the terrain envelope (n, p10, median, p90): grounded", geo[0], " airborne", geo[1])
            checks["ground_flag_matches_geometry"] = geo[0][2] < 4 and (not geo[1] or geo[1][2] > geo[0][2] + 2)
        checks["samples_match_steps"] = n > 0 and abs(n - (span * 60 + 1)) <= 2
        checks["dt_is_one_step"] = all(abs(d - 1 / 60) < 1e-3 for d in dts)
        checks["x_advances"] = D["x"][-1] - D["x"][0] > 5
        checks["matches_live_bike"] = abs(D["x"][-1] - live["torso"]["pos"][0]) < 3
        checks["throttle_recorded"] = max(D["thr"]) == 1
        await b("input", release=True)
    print("\nCHECKS:", json.dumps(checks, indent=1))
    ok = all(checks.values())
    print("TRACE ACCEPTANCE:", "PASS" if ok else "FAIL", " raw:", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
