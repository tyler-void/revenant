#!/usr/bin/env python3
"""G3 — run the in-game bot (bridge bot_start/bot_stop) over levels and score it against the medal times.

  uv run --project mobile-modkit/mcp python tools/levelgen/bot_eval.py --levels 1_1-1_30 [--params '{"kp":2}']
        [--trace] [--out results.json]

Per level: goto_level -> bot_start{params, px, py} (py = the level's static solid-ground upper envelope
from the LOCAL corpus, used only for airborne landing prediction) -> wait for bot_result -> stars from
the level's times [1★, 2★, 3★]. --trace also records an in-game trace per run (~/.cache/revenant/bot/).
"""
import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_stats as cs   # noqa: E402
import lgcore as lg         # noqa: E402
from rig import Rig         # noqa: E402

OUT = os.path.expanduser("~/.cache/revenant/bot")


def level_list(spec):
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            w, l0 = (int(v) for v in a.split("_"))
            _, l1 = (int(v) for v in b.split("_"))
            out += [f"{w}_{l}" for l in range(l0, l1 + 1)]
        else:
            out.append(part)
    return out


def profile_of(level, step=3.0):
    """Upper envelope of static solid ground between the start and the finish (x ascending)."""
    E = level["Entities"]
    start = next((i for i, e in enumerate(E) if e["Type"] in cs.BIKES), None)
    win = next((i for i, e in enumerate(E) if e["Type"].startswith("TriggerWin")), None)
    sp = cs.entity_pos(E, start) if start is not None else None
    fp = cs.entity_pos(E, win) if win is not None else None
    if not sp or not fp:
        return [], []
    lo, hi = min(sp[0], fp[0]) - 100, max(sp[0], fp[0]) + 100
    edges = [ed for e in E if lg.solid_ground(e) for ed in cs.top_edges(cs.outline(e) or [])]
    cs_step = cs.STEP
    cs.STEP = step
    env = cs.envelope(edges, lo, hi)
    cs.STEP = cs_step
    px, py = [], []
    for i, y in enumerate(env):
        if y is not None:
            px.append(round(lo + i * step, 2))
            py.append(round(y, 2))
    return px, py


def slices_of(E, x0, x1, dx=2.0):
    """Static solid occupancy for the in-game bot: per x (every dx) the solid y-intervals [b, t, b, t, …] of
    the level's static colliding geometry (even-odd per polygon, then the union over polygons)."""
    n = int((x1 - x0) / dx) + 1
    cross = [dict() for _ in range(n)]
    for k, e in enumerate(E):
        if not lg.solid_ground(e):
            continue
        poly = cs.outline(e)
        if not poly or len(poly) < 3:
            continue
        for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]):
            if ax == bx:
                continue
            lo, hi = min(ax, bx), max(ax, bx)
            for i in range(max(0, math.ceil((lo - x0) / dx)), min(n - 1, math.floor((hi - x0) / dx)) + 1):
                x = x0 + i * dx
                if x >= hi:                                    # half-open: a shared vertex counts once
                    continue
                cross[i].setdefault(k, []).append(ay + (by - ay) * (x - ax) / (bx - ax))
    iv = []
    for col in cross:
        spans = []
        for ys in col.values():
            ys.sort()
            spans += [(ys[j], ys[j + 1]) for j in range(0, len(ys) - 1, 2)]
        spans.sort()
        merged = []
        for b, t in spans:
            if merged and b <= merged[-1][1] + 0.01:
                merged[-1][1] = max(merged[-1][1], t)
            else:
                merged.append([b, t])
        iv.append([round(v, 2) for bt in merged for v in bt])
    return {"x0": round(x0, 2), "dx": dx, "iv": iv}


def level_span(E):
    start = next((i for i, e in enumerate(E) if e["Type"] in cs.BIKES), None)
    win = next((i for i, e in enumerate(E) if e["Type"].startswith("TriggerWin")), None)
    sp = cs.entity_pos(E, start) if start is not None else None
    fp = cs.entity_pos(E, win) if win is not None else None
    if not sp or not fp:
        return None
    return min(sp[0], fp[0]) - 150, max(sp[0], fp[0]) + 150


TRACE = ["t", "x", "y", "vx", "vy", "bx", "by", "fx", "fy", "c", "gt", "thr", "brk", "lean", "dead", "bt", "bp", "bl", "ceil"]


def stars(times, rt):
    if rt is None or not times or len(times) < 3:
        return None
    return 3 if rt <= times[2] else 2 if rt <= times[1] else 1 if rt <= times[0] else 0


def run_level(rig, lid, params, trace=False, timeout=None, record=False, plan=None, level=None, tag=""):
    """One bot run. Saves runs/<lid>/<stamp>.json (params, plan, result, per-step actions+state) and, with
    record, the matching .mp4 (local cache only)."""
    ld = rig.load(lid)
    if not ld["ok"]:
        return {"lid": lid, "result": "load_failed", "detail": ld["goto"]}
    level = level or lg.corpus_level(lid)
    px, py = profile_of(level)
    span = level_span(level["Entities"])
    slices = slices_of(level["Entities"], *span) if span else None
    time.sleep(0.8)
    rig.events()
    stamp = time.strftime("%Y%m%d-%H%M%S") + (f"-{tag}" if tag else "")
    run_dir = os.path.join(OUT, "runs", lid)
    if record:
        rig.record_start(f"{lid}-{stamp}")
    if trace or record:
        rig.b("trace_start", fields=TRACE)
    args = {"params": params, "px": px, "py": py}
    if slices:
        args["slices"] = slices
    if plan:
        args["plan"] = plan
    st = rig.b("bot_start", timeout=15, **args)
    max_s = params.get("max_s", 150)
    t_end = time.monotonic() + (timeout or max_s * 1.2 + 20)
    # (a recording is capped at 180 s by screenrecord; bot runs are far shorter)
    res = None
    while time.monotonic() < t_end and not res:
        res = next((e for e in rig.events() if e["type"] == "bot_result"), None)
        if not res:
            time.sleep(0.4)
    if not res:
        rig.b("bot_stop")
        res = next((e for e in rig.events() if e["type"] == "bot_result"), {"result": "host_timeout"})
    out = {"lid": lid, "result": res.get("result"), "run_time": res.get("run_time"), "x": res.get("x"),
           "progress": res.get("progress"), "max_air_s": res.get("max_air_s"), "times": level.get("times"),
           "profile_points": st.get("profile_points"), "crashes": ld["crashes"][:2]}
    out["stars"] = stars(level.get("times"), out["run_time"]) if out["result"] == "finish" else None
    out["ceil_frames"] = res.get("ceil_frames")
    if trace or record:
        time.sleep(0.6)                                    # a beat of the aftermath in the video
        tr = rig.b("trace_stop", timeout=20)
        video = rig.record_stop(os.path.join(run_dir, f"{stamp}.mp4")) if record else None
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(OUT, f"trace-{lid}.json"), "w") as f:
            json.dump(tr, f)
        with open(os.path.join(run_dir, f"{stamp}.json"), "w") as f:
            json.dump({"lid": lid, "params": params, "plan": plan, "result": out, "video": video,
                       "fields": tr["fields"], "n": tr["n"], "steps": tr["data"]}, f)
        out["run_json"] = os.path.join(run_dir, f"{stamp}.json")
        out["video"] = video
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--levels", default="1_1-1_30")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--record", action="store_true", help="screenrecord each run (mp4) next to its actions json")
    ap.add_argument("--out", default=os.path.join(OUT, "bot_eval.json"))
    a = ap.parse_args()
    params = json.loads(a.params)
    rig = Rig()
    rig.ensure_game()
    rows = []
    for lid in level_list(a.levels):
        r = run_level(rig, lid, params, trace=a.trace, record=a.record)
        rows.append(r)
        t = r.get("times") or [None] * 3
        print(f"{lid:6s} {r['result']:10s} rt={r.get('run_time') or 0:7.2f}  stars={r.get('stars')}  "
              f"progress={r.get('progress') or 0:7.1f}  medals(1/2/3★)={t}", flush=True)
    fin = [r for r in rows if r["result"] == "finish"]
    summary = {"levels": len(rows), "finished": len(fin), "rate": round(len(fin) / max(1, len(rows)), 3),
               "results": {k: sum(1 for r in rows if r["result"] == k) for k in sorted({r["result"] for r in rows})},
               "stars": {s: sum(1 for r in fin if r["stars"] == s) for s in (0, 1, 2, 3)},
               "time_over_gold_median": (sorted(r["run_time"] / r["times"][2] for r in fin)[len(fin) // 2] if fin else None),
               "params": params}
    print(json.dumps(summary, indent=1))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump({"summary": summary, "rows": rows}, f, indent=1)
    rig.close()


if __name__ == "__main__":
    main()
