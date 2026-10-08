#!/usr/bin/env python3
"""G2 — terrain encoding experiment. ONE generated track, encoded three ways:
  (a) polygon: one polygon per ground run (surface sampled every 4 units + 2 bottom corners)
  (b) spline:  one spline object per ground run (control points every 25 units, game-smoothed)
  (c) slabs:   one 4-vertex slab per 24 units (the old generator)
Each is loaded into a career slot via mods/ and ridden at full throttle under an in-game trace.

  uv run --project mobile-modkit/mcp python tools/levelgen/exp_encoding.py [--slot 1_24] [--only spline]

Measures: load (goto_done, Box2D bodies added), fall-through (wheel centre below the surface), finish,
and chassis angular-velocity jumps |Δw| per physics step while both wheels are grounded — at slab seams
vs elsewhere. Results -> stdout + ~/.cache/revenant/levelgen/exp_encoding.json (local).
"""
import argparse
import json
import math
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lgcore as lg    # noqa: E402
from rig import Rig    # noqa: E402

WHEEL_R = 2.2          # VERIFIED: grounded wheel centre sits ~2.2 above the surface (G1 trace)


def track():
    """Two ground runs separated by a 25-unit gap after a ~10° kicker. Returns [(ctrl, fine)], start, finish."""
    run1 = []
    for x in range(-60, 641, 25):
        if x < 100:
            y = 0.0
        elif x < 540:
            ramp = min(1.0, (x - 100) / 100)
            y = 10 * ramp * math.sin(2 * math.pi * (x - 100) / 220)
        else:
            y = 14 * (x - 540) / 100                       # kicker: +14 over 100 units (~8°)
        run1.append((float(x), y))
    run2 = []
    for x in range(665, 1301, 25):
        y = 4 - 24 * min(1.0, (x - 665) / 260) if x < 925 else -20.0
        run2.append((float(x), y))
    runs = [(c, lg.catmull_open(c)) for c in (run1, run2)]
    return runs, (0.0, 0.0), (1200.0, -20.0)


def build(tpl, kind, slot):
    runs, start, finish = track()
    base = min(y for c, _ in runs for _, y in c) - 60
    ground = []
    for i, (ctrl, fine) in enumerate(runs):
        ground += lg.encode_run(tpl, kind, ctrl, fine, base, f"Gen{kind}{i}")
    level = lg.assemble(tpl, slot, ground, start, finish, times=[60, 50, 40])
    seams = sorted({v["x"] for e in ground for v in e["Vertexes"]}) if kind == "slabs" else None
    return level, runs, seams, len(ground)


def analyse(tr, runs, seams):
    D, n = tr["data"], tr["n"]
    fines = [f for _, f in runs]

    def surf(x):
        for f in fines:
            if f[0][0] <= x <= f[-1][0]:
                return lg.surface_y(f, x)
        return None
    pen = []
    for k in ("b", "f"):
        for x, y in zip(D[k + "x"], D[k + "y"]):
            s = surf(x)
            if s is not None and x is not None:
                pen.append(y - s)
    dw = []
    for i in range(1, n):
        if int(D["c"][i]) & 3 == 3 and int(D["c"][i - 1]) & 3 == 3:
            near = seams and any(abs(D["bx"][i] - s) < 1.5 or abs(D["fx"][i] - s) < 1.5 for s in seams)
            dw.append((abs(D["w"][i] - D["w"][i - 1]), bool(near)))
    allw = sorted(v for v, _ in dw)

    def q(v, p):
        return round(v[min(len(v) - 1, int(p * (len(v) - 1)))], 3) if v else None
    out = {"samples": n, "max_x": round(max(D["x"]), 1), "run_time": round(D["t"][-1], 3) if n else None,
           "min_wheel_clearance": round(min(pen), 2) if pen else None,
           "fall_through": bool(pen and min(pen) < WHEEL_R - 1.5),
           "grounded_both_pct": round(100 * sum(1 for c in D["c"] if int(c) & 3 == 3) / max(1, n), 1),
           "dw_p50": q(allw, 0.5), "dw_p99": q(allw, 0.99), "dw_max": q(allw, 1.0)}
    if seams:
        a = [v for v, s in dw if s]
        b = [v for v, s in dw if not s]
        out["dw_mean_at_seams"] = round(st.mean(a), 4) if a else None
        out["dw_mean_elsewhere"] = round(st.mean(b), 4) if b else None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="1_24")
    ap.add_argument("--only", choices=lg.ENCODERS)
    ap.add_argument("--secs", type=float, default=16)
    a = ap.parse_args()
    tpl = lg.Templates(a.slot)
    rig = Rig()
    rig.ensure_game()
    results = {}
    _, _, slab_seams, _ = build(tpl, "slabs", a.slot)
    for kind in ([a.only] if a.only else lg.ENCODERS):
        level, runs, _, n_obj = build(tpl, kind, a.slot)
        dat = lg.export(level, f"g2-{kind}")
        rig.push_level(dat, a.slot)
        ld = rig.load(a.slot)
        r = {"objects": n_obj, "vertices": sum(len(e.get("Vertexes") or []) for e in level["Entities"]),
             "load_ok": ld["ok"], "mod_line": bool(ld["mod_line"]), "crashes": ld["crashes"][:3],
             "bodies": (ld["level_loaded"] or {}).get("bodies"), "bodies_added": (ld["goto"] or {}).get("bodies_added")}
        if ld["ok"]:
            rig.screenshot(os.path.join(lg.OUT, f"g2-{kind}.jpg"))
            tr, evs = rig.ride_throttle(a.secs)
            with open(os.path.join(lg.OUT, f"g2-{kind}.trace.json"), "w") as f:
                json.dump(tr, f)
            r.update(analyse(tr, runs, slab_seams))           # same seam x list for every encoding (control)
            r["finish"] = next((e.get("run_time") for e in evs if e["type"] == "finish"), None)
            r["death"] = any(e["type"] == "death" for e in evs)
        results[kind] = r
        print(kind, json.dumps(r), flush=True)
    with open(os.path.join(lg.OUT, "exp_encoding.json"), "w") as f:
        json.dump(results, f, indent=1)
    rig.close()


if __name__ == "__main__":
    main()
