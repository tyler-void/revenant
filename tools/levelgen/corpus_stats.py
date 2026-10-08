#!/usr/bin/env python3
"""Aggregate statistics over the LOCAL decoded corpus (see decode_corpus.py). Prints aggregate numbers
only (no level data) and writes the same aggregates as JSON next to the corpus.

  python3 tools/levelgen/corpus_stats.py [--corpus ~/.cache/revenant/corpus] [--json out.json]

Units: level coordinates == Box2D world units (VERIFIED: 1_24's Moto start x=-85.2 vs the live chassis
x=-86.3). Medal times are stored [1★, 2★, 3★], descending.
"""
import argparse
import collections
import glob
import json
import math
import os
import re
import statistics as st

STORY = re.compile(r"^(\d+)_(\d+)$")


# ── geometry ─────────────────────────────────────────────────────────────────────────────────
def catmull_closed(pts):
    """The editor's/game's closed Catmull-Rom with per-vertex `segments` subdivisions."""
    n = len(pts)
    if n < 3:
        return [(p["x"], p["y"]) for p in pts]
    out = []
    for i in range(n):
        p0, p1, p2, p3 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        seg = max(1, int(round(p1.get("segments") or 4)))
        for j in range(seg):
            t = j / seg; t2 = t * t; t3 = t2 * t
            x = 0.5 * (2 * p1["x"] + (-p0["x"] + p2["x"]) * t + (2 * p0["x"] - 5 * p1["x"] + 4 * p2["x"] - p3["x"]) * t2
                       + (-p0["x"] + 3 * p1["x"] - 3 * p2["x"] + p3["x"]) * t3)
            y = 0.5 * (2 * p1["y"] + (-p0["y"] + p2["y"]) * t + (2 * p0["y"] - 5 * p1["y"] + 4 * p2["y"] - p3["y"]) * t2
                       + (-p0["y"] + 3 * p1["y"] - 3 * p2["y"] + p3["y"]) * t3)
            out.append((x, y))
    return out


def outline(e):
    vs = e.get("Vertexes") or []
    if len(vs) < 3:
        return None
    return catmull_closed(vs) if (e.get("Properties") or {}).get("spline") else [(v["x"], v["y"]) for v in vs]


def area(poly):
    return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1] for i in range(len(poly))) / 2


def top_edges(poly):
    """Edges whose OUTWARD normal points up (winding-aware): (x0, y0, x1, y1) with x0 < x1."""
    ccw = area(poly) > 0
    out = []
    for i in range(len(poly)):
        (ax, ay), (bx, by) = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = bx - ax, by - ay
        ny = -dx if ccw else dx                       # outward normal y: CCW -> (dy,-dx), CW -> (-dy,dx)
        if ny > 1e-9 and abs(dx) > 1e-9:
            out.append((ax, ay, bx, by) if ax < bx else (bx, by, ax, ay))
    return out


def pct(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(round(q / 100 * (len(s) - 1)))))]


def summary(vals, digits=2):
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "p10": round(pct(vals, 10), digits), "median": round(pct(vals, 50), digits),
            "p90": round(pct(vals, 90), digits), "max": round(max(vals), digits)}


def weighted_pct(pairs, q):
    """pairs = [(value, weight)] -> weighted percentile."""
    pairs = sorted(pairs)
    tot = sum(w for _, w in pairs)
    acc = 0
    for v, w in pairs:
        acc += w
        if acc >= q / 100 * tot:
            return v
    return pairs[-1][0] if pairs else None


# ── per-level analysis ───────────────────────────────────────────────────────────────────────
def props(e):
    return e.get("Properties") or {}


def is_ground(e):
    P = props(e)
    return e["Type"] == "EditorPhysicsObject" and P.get("Static") and P.get("physics", True) and not P.get("sensor")


def is_dynamic_body(e):
    P = props(e)
    return e["Type"] == "EditorPhysicsObject" and not P.get("Static") and P.get("physics", True) and not P.get("sensor")


def entity_pos(E, i, seen=None):
    """Position of entity i; group entities fall back to their children (they carry no position)."""
    e = E[i]
    P = props(e)
    if isinstance(P.get("position"), list) and len(P["position"]) >= 2:
        return P["position"][:2]
    if e.get("Vertexes"):
        vs = e["Vertexes"]
        return [sum(v["x"] for v in vs) / len(vs), sum(v["y"] for v in vs) / len(vs)]
    seen = seen or set()
    for c in P.get("refobjectList") or []:
        if isinstance(c, int) and 0 <= c < len(E) and c not in seen:
            seen.add(c)
            p = entity_pos(E, c, seen)
            if p:
                return p
    return None


def joint_slots(e):
    s = []
    for a in e.get("Anchors") or []:
        for k in ("object_id", "object2_id"):
            if k in a:
                s.append(a[k])
    while len(s) < 2:
        s.append(-1)
    return s


def ref_signature(E, i, depth=0):
    """Type + owned-children structure, e.g. ExplosiveBarrel[EditorPhysicsObject{EditorSprite},EditorSprite]."""
    e = E[i]
    P = props(e)
    kids = [c for c in (P.get("refobjectList") or []) if isinstance(c, int) and 0 <= c < len(E)]
    mounted = [c for c in (P.get("mountedSprites") or []) if isinstance(c, int) and 0 <= c < len(E)]
    s = e["Type"]
    if mounted:
        s += "{" + ",".join(sorted(E[c]["Type"] for c in mounted)) + "}"
    if kids and depth < 3:
        s += "[" + ",".join(sorted(ref_signature(E, c, depth + 1) for c in kids)) + "]"
    return s


BIKES = ("Moto", "XtremeBike", "SuperDuke", "NeonBike")          # player-start prefabs (one per level)
SET_PIECES = ("Nitro", "Spikes", "Geyser", "Checkpoint", "Checkpoint_T4", "TriggerNewGravity", "TriggerClock",
              "Drone", "ExplosiveBarrel", "WaterTrigger", "ComposedSprite", "EditorPhysicsEntity")
STEP = 1.0          # envelope sample spacing (world units)
WALL = 3.0          # a rise/fall of >= WALL between two samples is a step/wall, not a ramp


def envelope(edges, x0, x1):
    """Upper envelope (topmost upward-facing surface) sampled every STEP from x0 to x1; None = no ground."""
    n = int((x1 - x0) / STEP) + 1
    env = [None] * n
    for ax, ay, bx, by in edges:
        i0, i1 = max(0, math.ceil((ax - x0) / STEP)), min(n - 1, math.floor((bx - x0) / STEP))
        for i in range(i0, i1 + 1):
            x = x0 + i * STEP
            y = ay + (by - ay) * (x - ax) / (bx - ax)
            if env[i] is None or y > env[i]:
                env[i] = y
    return env


def bbox(e):
    vs = e.get("Vertexes") or []
    if not vs:
        return None
    xs, ys = [v["x"] for v in vs], [v["y"] for v in vs]
    return min(xs), min(ys), max(xs), max(ys)


def analyse(level):
    E = level["Entities"]
    out = {"types": collections.Counter(e["Type"] for e in E)}
    t = level.get("times") or []
    if len(t) == 3 and t[2]:
        out["ratio_2_3"] = t[1] / t[2]
        out["ratio_1_3"] = t[0] / t[2]
        out["gold"] = t[2]
        out["descending"] = t[0] >= t[1] >= t[2]

    start = next((i for i, e in enumerate(E) if e["Type"] in BIKES), None)
    win = next((i for i, e in enumerate(E) if e["Type"].startswith("TriggerWin")), None)
    sp = entity_pos(E, start) if start is not None else None
    fp = entity_pos(E, win) if win is not None else None
    sx, fx = (sp[0] if sp else None), (fp[0] if fp else None)
    out["length"] = (fx - sx) if sx is not None and fx is not None else None

    ground_edges, dyn_edges = [], []
    for e in E:
        if is_ground(e) or is_dynamic_body(e):
            poly = outline(e)
            if poly and len(poly) >= 3:
                (ground_edges if is_ground(e) else dyn_edges).extend(top_edges(poly))
    # every upward-facing static edge (incl. off-path platforms), length-weighted
    out["edge_slopes"] = [(math.degrees(math.atan(abs((y1 - y0) / (x1 - x0)))), math.hypot(x1 - x0, y1 - y0))
                          for x0, y0, x1, y1 in ground_edges]

    out.update(ride_slopes=[], step_ups=[], step_downs=[], pits=[], voids=0)
    if sx is None or fx is None or fx <= sx:
        return out | finish_setpieces(E)
    lo, hi = min(sx, fx), max(sx, fx)
    env = envelope(ground_edges, lo, hi)
    denv = envelope(dyn_edges, lo, hi)
    water = []
    for e in E:
        if e["Type"] == "WaterTrigger":
            P = props(e)
            if isinstance(P.get("position"), list):
                c = P["position"][0] + (P.get("xOffset") or 0)
                water.append((c - (P.get("width") or 0) / 2, c + (P.get("width") or 0) / 2))
    # ridden-surface slopes (signed: + = uphill in the direction of travel), arc-length weighted
    for i in range(len(env) - 1):
        a, b = env[i], env[i + 1]
        if a is None or b is None:
            continue
        dy = b - a
        if dy >= WALL:
            out["step_ups"].append(dy)
        elif dy <= -WALL:
            out["step_downs"].append(-dy)
        else:
            out["ride_slopes"].append((math.degrees(math.atan2(dy, STEP)), math.hypot(STEP, dy)))
    # pits: a lip, then the surface stays >= WALL below the lip (or absent), then comes back up to within
    # 2*WALL of the lip height. width = horizontal span, drop = lip - landing.
    i, n = 0, len(env)
    while i < n - 1:
        y0 = env[i]
        if y0 is not None and (env[i + 1] is None or env[i + 1] <= y0 - WALL):
            j = i + 1
            while j < n and (env[j] is None or env[j] < y0 - 2 * WALL) and (j - i) * STEP <= 200:
                j += 1
            if j < n and env[j] is not None and (j - i) * STEP <= 200 and j - i > 1:
                x0, x1 = lo + i * STEP, lo + j * STEP
                mid = range(i + 1, j)
                out["pits"].append({"width": x1 - x0, "drop": y0 - env[j],
                                    "depth": y0 - min((env[k] for k in mid if env[k] is not None), default=-1e9),
                                    "void": any(env[k] is None for k in mid),
                                    "bridged": any(denv[k] is not None and denv[k] > y0 - 2 * WALL for k in mid),
                                    "water": any(a0 <= (x0 + x1) / 2 <= a1 for a0, a1 in water)})
                i = j
                continue
        i += 1
    out["voids"] = sum(1 for k in range(1, n) if env[k] is None and env[k - 1] is not None)
    return out | finish_setpieces(E)


def finish_setpieces(E):
    out = {}
    # joint contraptions = connected components of bodies through joints
    parent = {}

    def find(a):
        while parent.setdefault(a, a) != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    joints = [(i, e) for i, e in enumerate(E) if "Joint" in e["Type"]]
    for i, e in joints:
        nodes = [("j", i)] + [("b", b) for b in joint_slots(e) if isinstance(b, int) and 0 <= b < len(E)]
        for nd in nodes[1:]:
            parent[find(nd)] = find(nodes[0])
    comps = collections.defaultdict(lambda: {"bodies": set(), "joints": collections.Counter(), "world": 0})
    for i, e in joints:
        c = comps[find(("j", i))]
        c["joints"][e["Type"].replace("EditorPhysics", "").replace("Joint", "")] += 1
        for b in joint_slots(e):
            if isinstance(b, int) and 0 <= b < len(E):
                c["bodies"].add(b)
            else:
                c["world"] += 1
    out["contraptions"] = []
    out["seesaws"] = []
    for c in comps.values():
        dyn = [b for b in c["bodies"] if is_dynamic_body(E[b])]
        sig = "+".join(f"{k}x{v}" for k, v in sorted(c["joints"].items())) + \
            f"|bodies={len(c['bodies'])}|dyn={len(dyn)}|world={min(c['world'], 1)}"
        out["contraptions"].append({"sig": sig, "bodies": len(c["bodies"]), "joints": sum(c["joints"].values())})
        # see-saw candidate: ONE revolute joint pinning ONE long dynamic plank to the world
        if dict(c["joints"]) == {"Revolute": 1} and len(dyn) == 1 and c["world"]:
            bb = bbox(E[dyn[0]])
            if bb:
                w, h = bb[2] - bb[0], bb[3] - bb[1]
                if w >= 4 * max(h, 1e-6):
                    out["seesaws"].append({"length": w, "thick": h, "density": props(E[dyn[0]]).get("density")})
    out["barrel_clusters"] = []
    bpos = [p for p in (entity_pos(E, i) for i, e in enumerate(E) if e["Type"] == "ExplosiveBarrel") if p]
    used = set()
    for a in range(len(bpos)):
        if a in used:
            continue
        stack, size = [a], 0
        used.add(a)
        while stack:
            u = stack.pop()
            size += 1
            for v in range(len(bpos)):
                if v not in used and math.dist(bpos[u], bpos[v]) < 150:
                    used.add(v)
                    stack.append(v)
        out["barrel_clusters"].append(size)
    out["water"] = [props(e).get("width") for e in E if e["Type"] == "WaterTrigger"]
    out["group_sigs"] = collections.Counter(ref_signature(E, i) for i, e in enumerate(E)
                                            if e["Type"] in SET_PIECES or props(e).get("refobjectList"))
    out["trigger_links"] = sum(1 for e in E if props(e).get("objects"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=os.path.expanduser("~/.cache/revenant/corpus"))
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    files = sorted(glob.glob(os.path.join(a.corpus, "*.level.json")))
    per_world = collections.defaultdict(list)
    for p in files:
        lid = os.path.basename(p)[: -len(".level.json")]
        m = STORY.match(lid)
        if not m:
            continue                                   # universe sub-levels reported separately below
        with open(p) as f:
            per_world[int(m.group(1))].append(analyse(json.load(f)))

    R = {"levels": {w: len(v) for w, v in per_world.items()}, "worlds": {}}
    allv = [x for v in per_world.values() for x in v]
    for w, L in sorted(per_world.items()) + [("all", allv)]:
        types = collections.Counter()
        for x in L:
            types.update(x["types"])
        es = [s for x in L for s in x["edge_slopes"]]
        rs = [s for x in L for s in x["ride_slopes"]]
        pits = [g for x in L for g in x["pits"]]
        open_pits = [g for g in pits if not g["bridged"] and not g["water"]]
        contr = [c for x in L for c in x["contraptions"]]
        nL = max(1, len(L))
        R["worlds"][str(w)] = {
            "levels": len(L),
            "medals": {"ratio_2star_over_3star": summary([x["ratio_2_3"] for x in L if "ratio_2_3" in x], 3),
                       "ratio_1star_over_3star": summary([x["ratio_1_3"] for x in L if "ratio_1_3" in x], 3),
                       "gold_s": summary([x["gold"] for x in L if "gold" in x], 1),
                       "descending": sum(1 for x in L if x.get("descending"))},
            "length": summary([x["length"] for x in L if x["length"]], 0),
            "speed_gold_units_per_s": summary([x["length"] / x["gold"] for x in L if x["length"] and x.get("gold")], 1),
            "ride_slope_deg": {"uphill_p50_p90_p99": [round(weighted_pct([(a, l) for a, l in rs if a > 0], q), 1) for q in (50, 90, 99)],
                               "downhill_p50_p90_p99": [round(-weighted_pct([(-a, l) for a, l in rs if a < 0], q), 1) for q in (50, 90, 99)],
                               "share_abs_gt": {d: round(sum(l for a, l in rs if abs(a) > d) / max(1e-9, sum(l for _, l in rs)), 3)
                                                for d in (10, 20, 30, 45)}} if rs else {},
            "edge_slope_share": {f"{a}-{b}": round(sum(l for s_, l in es if a <= s_ < b) / max(1e-9, sum(l for _, l in es)), 3)
                                 for a, b in ((0, 15), (15, 30), (30, 45), (45, 60), (60, 75), (75, 91))},
            "step_up": summary([h for x in L for h in x["step_ups"]], 1),
            "step_down": summary([h for x in L for h in x["step_downs"]], 1),
            "pits_per_level": round(len(pits) / nL, 2),
            "open_pits": {"width": summary([g["width"] for g in open_pits], 1),
                          "drop": summary([g["drop"] for g in open_pits], 1),
                          "depth": summary([g["depth"] for g in open_pits if not g["void"]], 1),
                          "void": sum(1 for g in open_pits if g["void"])},
            "bridged_pits": sum(1 for g in pits if g["bridged"]), "water_pits": sum(1 for g in pits if g["water"]),
            "entity_mix_per_level": {t: round(c / nL, 2) for t, c in types.most_common(14)},
            "setpieces_per_level": {t: round(types[t] / nL, 2) for t in SET_PIECES if types[t]},
            "contraptions_per_level": round(len(contr) / nL, 2),
            "contraption_bodies": summary([c["bodies"] for c in contr], 0),
            "contraption_top": collections.Counter(c["sig"] for c in contr).most_common(6),
            "seesaws": {"per_level": round(sum(len(x["seesaws"]) for x in L) / nL, 2),
                        "length": summary([s_["length"] for x in L for s_ in x["seesaws"]], 1),
                        "thick": summary([s_["thick"] for x in L for s_ in x["seesaws"]], 1)},
            "barrel_clusters": {"per_level": round(sum(len(x["barrel_clusters"]) for x in L) / nL, 2),
                                "size": summary([s_ for x in L for s_ in x["barrel_clusters"]], 0)},
            "water": {"per_level": round(sum(len(x["water"]) for x in L) / nL, 2),
                      "width": summary([ww for x in L for ww in x["water"] if ww], 0)},
        }
    sigs = collections.Counter()
    for x in allv:
        sigs.update(x["group_sigs"])
    R["group_ref_structures"] = sigs.most_common(12)
    R["group_ref_structures"] = [(sig if len(sig) < 160 else sig[:157] + "...", c) for sig, c in R["group_ref_structures"]]
    R["trigger_links_per_level"] = round(sum(x["trigger_links"] for x in allv) / max(1, len(allv)), 2)
    R["universe_sublevels"] = len([p for p in files if not STORY.match(os.path.basename(p)[: -len(".level.json")])])

    print(json.dumps(R, indent=1, default=str))
    out = a.json or os.path.join(a.corpus, "corpus_stats.json")
    with open(out, "w") as f:
        json.dump(R, f, indent=1, default=str)
    print("\nwrote", out)


if __name__ == "__main__":
    main()
