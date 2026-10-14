#!/usr/bin/env python3
"""G4 — Generator v2: beat plan + difficulty budget -> spline terrain + corpus set pieces -> verified level.

  uv run --project mobile-modkit/mcp python tools/levelgen/gen.py gen --seed 7 --difficulty 0.5 --slot 1_24 \
        [--verify N] [--play] [--tries K]
  uv run --project mobile-modkit/mcp python tools/levelgen/gen.py batch --seeds 1-20 --difficulty 0.5 --verify 2

Data it stands on (docs/procgen.md, G1-G3; all from LOCAL corpus/traces, nothing committed):
- terrain = ONE SPLINE OBJECT PER GROUND RUN (G2), control points every ~25 units;
- ambient slopes inside the corpus ridden-surface range (uphill p90 44°, downhill p90 51°; we stay gentler);
- gaps sized from the measured ballistics: launch speed ~110-135 u/s, effective gravity ~68 u/s², range
  = v·cosθ·t_land; width = a difficulty-scaled FRACTION of the predicted range; the landing run starts
  below the lip and descends along the trajectory (no wall faces the rider except the gap's far edge);
- set pieces: a see-saw cloned from a world-1 level through levelops.js (the editor's ref semantics),
  bare prefab references (ExplosiveBarrel, Nitro) at the corpus' placement height above the ground;
- medal times: bot run_time (G3) × calibration × corpus star ratios (1★/3★ 1.33, 2★/3★ 1.12).
"""
import argparse
import copy
import json
import math
import os
import random
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import corpus_stats as cs   # noqa: E402
import lgcore as lg         # noqa: E402

STAR_RATIOS = (1.33, 1.12, 1.0)          # corpus medians: 1★/3★, 2★/3★, 3★
GRAVITY = 68.0                            # effective, from flight traces (G1)
V_LIP = 110.0                             # conservative launch speed for gap sizing (traces: 124-157)
CTRL_DX = 25.0
PREFAB_DY = {"ExplosiveBarrel": 11.1, "Nitro": 12.75}   # corpus W1 medians: position above the ground


# ── beat plan ────────────────────────────────────────────────────────────────────────────────────
# name: (cost, min_difficulty, weight)
BEATS = {
    "rollers":    (1, 0.0, 3.0),
    "climb":      (1, 0.0, 2.0),
    "descent":    (1, 0.0, 2.0),
    "kicker_gap": (2, 0.0, 2.5),
    "drop":       (2, 0.2, 1.5),
    "seesaw":     (2, 0.3, 1.0),
    "barrels":    (2, 0.5, 0.8),
}
REST = ("flat", "rollers_soft")


def beat_plan(rng, difficulty, length, allowed=None):
    """A rhythm of beats until `length` is used: hard beats are followed by a rest (rhythm-group model).
    `allowed`: {beat: max difficulty} from the feature test — beats not in it are never used."""
    budget = 4 + 16 * difficulty
    plan, used, spent = [("flat", {"len": 140})], 140, 0.0
    while used < length - 200:
        avail = [(n, w) for n, (c, md, w) in BEATS.items() if difficulty >= md and spent + c <= budget
                 and (allowed is None or n in allowed)]
        if not avail:
            name = "rollers_soft"
        else:
            tot = sum(w for _, w in avail)
            r, name = rng.random() * tot, avail[-1][0]
            for n, w in avail:
                r -= w
                if r <= 0:
                    name = n
                    break
            spent += BEATS[name][0]
        plan.append((name, {}))
        used += 220
        if name in ("kicker_gap", "drop", "seesaw", "barrels"):
            plan.append((rng.choice(REST), {}))
            used += 140
    plan.append(("flat", {"len": 220}))                       # finish straight
    return plan, round(spent, 1), budget


# ── terrain ──────────────────────────────────────────────────────────────────────────────────────
class Builder:
    def __init__(self, rng, difficulty, caps=None):
        self.rng, self.d0, self.caps = rng, difficulty, caps or {}
        self.d = difficulty
        self.runs = [[(-80.0, 0.0), (0.0, 0.0)]]
        self.features = []                                    # [{beat, x0, x1, ...}] (death attribution)
        self.setpieces = []                                   # [(kind, x)]

    @property
    def x(self):
        return self.runs[-1][-1][0]

    @property
    def y(self):
        return self.runs[-1][-1][1]

    def add(self, pts):
        self.runs[-1] += [(float(x), float(y)) for x, y in pts]

    def line(self, length, slope_deg):
        """Straight-ish section at a constant grade, control points every CTRL_DX."""
        x0, y0, s = self.x, self.y, math.tan(math.radians(slope_deg))
        n = max(1, round(length / CTRL_DX))
        self.add([(x0 + length * i / n, y0 + s * length * i / n) for i in range(1, n + 1)])

    def beat(self, name, p):
        self.d = min(self.d0, self.caps.get(name, 1.0))         # a feature never harder than the bot can verify
        rng, d = self.rng, self.d
        x0 = self.x
        info = {"beat": name, "x0": round(x0, 1)}
        if name == "flat":
            self.line(p.get("len", 140), 0)
        elif name in ("rollers", "rollers_soft"):
            amp = (3 + 4 * rng.random()) if name == "rollers_soft" else (5 + 9 * d * rng.random() + 3)
            lam = rng.uniform(180, 260)
            k = rng.choice((1, 2))
            n = round(k * lam / CTRL_DX)
            y0 = self.y
            self.add([(x0 + k * lam * i / n, y0 + amp * math.sin(2 * math.pi * i / n * k)) for i in range(1, n + 1)])
            info.update(amp=round(amp, 1), wavelength=round(lam))
        elif name == "climb":
            ang = rng.uniform(8, 14 + 16 * d)                 # <= 30°: inside the corpus uphill p90 (44°)
            self.line(25, ang / 2)
            self.line(rng.uniform(80, 160), ang)
            self.line(25, ang / 2)
            self.line(40, 0)
            info.update(angle=round(ang, 1))
        elif name == "descent":
            ang = rng.uniform(8, 14 + 18 * d)
            self.line(25, -ang / 2)
            self.line(rng.uniform(80, 180), -ang)
            self.line(25, -ang / 2)
            self.line(40, 0)
            info.update(angle=round(-ang, 1))
        elif name in ("kicker_gap", "drop"):
            if name == "kicker_gap":
                theta = rng.uniform(8, 10 + 14 * d)           # the ramp angle (old RAMP_SLOPE, now USED)
                self.line(20, theta / 2)
                self.line(rng.uniform(50, 80), theta)
                drop = rng.uniform(0, 8 + 20 * d)
            else:
                theta = 0.0
                self.line(30, 0)
                drop = rng.uniform(15, 20 + 35 * d)
            lip_x, lip_y = self.x, self.y
            th = math.radians(theta)
            vx, vy = V_LIP * math.cos(th), V_LIP * math.sin(th)
            t_land = (vy + math.sqrt(vy * vy + 2 * GRAVITY * drop)) / GRAVITY
            rng_x = vx * t_land                               # predicted range onto the landing height
            frac = rng.uniform(0.25, 0.35 + 0.3 * d)
            width = max(12.0, min(160.0, frac * rng_x))
            if name == "drop":
                width = max(8.0, min(width, 30.0))
            # landing run: starts `drop` below the lip and descends along the trajectory for the overshoot
            land_ang = min(25.0, math.degrees(math.atan2(vy - GRAVITY * t_land, vx)) * -1) if t_land else 10.0
            land_ang = max(8.0, land_ang)
            self.runs.append([(lip_x + width, lip_y - drop)])
            over = max(60.0, rng_x - width + 60)
            self.line(25, -land_ang / 2)
            self.line(over, -land_ang)
            self.line(25, -land_ang / 2)
            self.line(40, 0)
            info.update(theta=round(theta, 1), gap=round(width, 1), drop=round(drop, 1),
                        predicted_range=round(rng_x, 1), landing_deg=round(-land_ang, 1))
        elif name == "seesaw":
            # The cloned see-saw is a thick plank (~14 units): resting tipped toward the rider its low end is a
            # 14-unit face, so an approach ramp climbs to the plank's top surface there. A new ground run starts
            # under the plank (flat), leaving it room to tip the other way when the bike passes the pivot.
            src = seesaw_source()
            gy = self.y
            self.line(40, 0)
            if src:
                poly = seesaw_plank_at(0.0, gy + src["height"])
                left, right = min(x for x, _ in poly), max(x for x, _ in poly)
                top_left = max(y for x, y in poly if x < left + 4)
                ramp_len = 70.0
                self.line(ramp_len, math.degrees(math.atan2(top_left - 0.5 - gy, ramp_len)))
                pivot_x = self.x + 1.0 - left                 # the plank's left end 1 unit past the ramp lip
                self.runs.append([(self.x + 1.0, gy)])
                self.line(right - left + 40, 0)
                self.setpieces.append(("seesaw", pivot_x))
                info.update(ramp=round(top_left - gy, 1), plank=round(right - left, 1))
            self.line(60, 0)
        elif name == "barrels":
            self.line(60, 0)
            k = rng.randint(1, 2 + int(2 * d))
            for i in range(k):
                self.setpieces.append(("ExplosiveBarrel", self.x + 30 + 26 * i))
            self.line(60 + 26 * k, 0)
            self.line(60, 0)
            info.update(barrels=k)
        info["x1"] = round(self.x, 1)
        self.features.append(info)

    def nitro_spots(self):
        """Nitro on the flat before each climb / kicker (a helping hand, like the corpus' flats)."""
        for f in self.features:
            if f["beat"] in ("climb", "kicker_gap") and self.rng.random() < 0.6:
                self.setpieces.append(("Nitro", f["x0"] - 30))


def surface_at(runs_fine, x):
    for f in runs_fine:
        if f[0][0] <= x <= f[-1][0]:
            return lg.surface_y(f, x)
    return None


# ── set pieces ───────────────────────────────────────────────────────────────────────────────────
_SEESAW = None


def seesaw_source():
    """A world-1 see-saw (ONE revolute joint pinning ONE long dynamic plank to the world): the candidate whose
    pivot sits lowest above its own ground, cloned with the editor's copy semantics."""
    global _SEESAW
    if _SEESAW:
        return _SEESAW
    best = None
    for k in range(1, 31):
        lid = f"1_{k}"
        E = lg.corpus_level(lid)["Entities"]
        for i, e in enumerate(E):
            if e["Type"] != "EditorPhysicsRevoluteJoint":
                continue
            bodies = [b for b in cs.joint_slots(e) if isinstance(b, int) and 0 <= b < len(E)]
            if len(bodies) != 1 or not cs.is_dynamic_body(E[bodies[0]]):
                continue
            if any("Joint" in f["Type"] and j != i and bodies[0] in cs.joint_slots(f) for j, f in enumerate(E)):
                continue
            bb = cs.bbox(E[bodies[0]])
            if not bb or bb[2] - bb[0] < 4 * max(bb[3] - bb[1], 1e-6):
                continue
            ax, ay = e["Anchors"][0]["x"], e["Anchors"][0]["y"]
            g = lg.ground_y(E, ax, below=ay)
            if g is None:
                continue
            cand = (ay - g, lid, i, bodies[0], ax, ay, bb[2] - bb[0])
            if best is None or cand[0] < best[0]:
                best = cand
    if best is None:
        return None
    off, lid, j, plank, ax, ay, length = best
    E = lg.corpus_level(lid)["Entities"]
    bb = cs.bbox(E[plank])
    clip = lg.levelops("copy", ents=E, sel=[j, plank], srcLid=lid)
    # On flat ground: the plank's BOTTOM clears the ground by the drop of a 15° tip (½·L·sin 15°), so it can
    # tip that far before its low end touches; the pivot sits (ay - bottom) above the plank bottom (the
    # corpus see-saws hang over pits, so their own pivot height doesn't transfer).
    height = length / 2 * math.sin(math.radians(15)) + (ay - bb[1])
    tilt = math.asin(min(0.9, (height - (ay - bb[1])) / (length / 2)))
    _SEESAW = {"clip": clip, "pivot": (ax, ay), "src_offset": off, "height": height, "src": lid, "length": length,
               "pivot_above_bottom": ay - bb[1], "tilt": tilt,
               "plank_rel": [(v["x"] - ax, v["y"] - ay) for v in E[plank]["Vertexes"]]}
    return _SEESAW


def seesaw_plank_at(px, py):
    """The placed plank's outline (world coords) for a pivot at (px, py): the clip's plank, tipped by `tilt`
    exactly as generate() places it (rotate_about)."""
    src = seesaw_source()
    c, s_ = math.cos(src["tilt"]), math.sin(src["tilt"])
    return [(px + x * c - y * s_, py + x * s_ + y * c) for x, y in src["plank_rel"]]


def rotate_about(ents, idxs, pivot, ang):
    """Rigidly rotate entities CCW by `ang` rad about `pivot` (vertices, positions; sprites' cocos rotation is
    in degrees, clockwise-positive — INFERRED, check on device)."""
    c, s_ = math.cos(ang), math.sin(ang)
    px, py = pivot

    def rot(x, y):
        dx, dy = x - px, y - py
        return px + dx * c - dy * s_, py + dx * s_ + dy * c
    for i in idxs:
        e = ents[i]
        for v in e.get("Vertexes") or []:
            v["x"], v["y"] = rot(v["x"], v["y"])
        P = e.get("Properties") or {}
        if isinstance(P.get("position"), list) and len(P["position"]) >= 2:
            P["position"][0], P["position"][1] = rot(P["position"][0], P["position"][1])
        if e["Type"] == "EditorSprite" and isinstance(P.get("rotation"), (int, float)):
            P["rotation"] = P["rotation"] - math.degrees(ang)


_PREFABS = {}


def prefab(kind):
    if kind not in _PREFABS:
        for k in range(1, 31):
            E = lg.corpus_level(f"1_{k}")["Entities"]
            e = next((e for e in E if e["Type"] == kind and "prefabName" in e["Properties"]
                      and not e["Properties"].get("refobjectList")), None)
            if e:
                _PREFABS[kind] = copy.deepcopy(e)
                break
    return copy.deepcopy(_PREFABS.get(kind))


# ── generate ─────────────────────────────────────────────────────────────────────────────────────
def load_allowed():
    """{feature: max verified difficulty} from the last feature test (None = no test yet: allow everything)."""
    try:
        with open(os.path.join(lg.OUT, "features.json")) as f:
            return json.load(f)["allowed"]
    except (OSError, KeyError, ValueError):
        return None


def generate(seed, difficulty, slot, length=1800, times=None, plan=None, allowed="auto"):
    rng = random.Random(seed)
    tpl = lg.Templates(slot)
    if allowed == "auto":
        allowed = load_allowed()
    if plan is None:
        plan, spent, budget = beat_plan(rng, difficulty, length, allowed)
    else:
        spent, budget = 0.0, 0.0
    b = Builder(rng, difficulty, caps=allowed)
    for name, p in plan:
        b.beat(name, p)
    b.nitro_spots()
    fines = [lg.catmull_open(r) for r in b.runs]
    base = min(y for r in b.runs for _, y in r) - 80
    ents = []
    for i, (ctrl, fine) in enumerate(zip(b.runs, fines)):
        ents += lg.encode_spline(tpl, ctrl, base, f"GenGround{i}")
    placed = {"seesaw": 0, "ExplosiveBarrel": 0, "Nitro": 0}
    for kind, x in b.setpieces:
        gy = surface_at(fines, x)
        if gy is None:
            continue
        if kind == "seesaw":
            src = seesaw_source()
            if not src:
                continue
            dx, dy = x - src["pivot"][0], gy + src["height"] - src["pivot"][1]
            res = lg.levelops("paste", ents=ents, clip=src["clip"], dx=dx, dy=dy)
            ents = res["ents"]
            # rest it tipped toward the rider: rotate the plank (+ mounted sprite) CCW about the pivot until its
            # left end touches the ground, so the bike rides up it, tips it past the pivot, rides off the far end
            rotate_about(ents, [i for i in res["added"] if "Joint" not in ents[i]["Type"]],
                         (src["pivot"][0] + dx, src["pivot"][1] + dy), src["tilt"])
        else:
            e = prefab(kind)
            if not e:
                continue
            e["Properties"]["position"] = [float(x), float(gy + PREFAB_DY[kind])]
            e["Properties"]["name"] = f"Gen{kind}{placed[kind]}"
            ents.append(e)
        placed[kind] += 1
    start_x = 0.0
    finish_x = b.x - 80
    if times is None:                                          # placeholder until verified: corpus pace
        gold = (finish_x - start_x) / 70.0
        times = [round(gold * r, 1) for r in STAR_RATIOS]
    level = lg.assemble(tpl, slot, ents, (start_x, 0.0), (finish_x, surface_at(fines, finish_x)), times)
    meta = {"seed": seed, "difficulty": difficulty, "slot": slot, "plan": [n for n, _ in plan],
            "budget": round(budget, 1), "spent": spent, "features": b.features, "placed": placed,
            "length": round(finish_x - start_x), "runs": len(b.runs),
            "seesaw_src": (seesaw_source() or {}).get("src") if placed["seesaw"] else None}
    issues = lg.levelops("lint", level=level, targetLid=slot)["issues"]
    meta["lint"] = [i for i in issues if i.get("level") == "error"] or None
    meta["terrain_checks"] = terrain_checks(b)
    return level, meta, fines


def terrain_checks(b):
    """The old generator's measured bugs, checked on every level: overlapping gaps (a run starting before the
    previous one ends), snap-back walls (a run starting ABOVE the previous run's end = a wall facing the rider),
    and an unused ramp angle (every kicker must carry its theta). Empty list = clean."""
    bad = []
    for i, (a, c) in enumerate(zip(b.runs, b.runs[1:])):
        if c[0][0] <= a[-1][0]:
            bad.append(f"run {i + 1} overlaps run {i} (starts at x {c[0][0]:.1f} <= {a[-1][0]:.1f})")
        if c[0][1] > a[-1][1] + 0.5:
            bad.append(f"run {i + 1} starts {c[0][1] - a[-1][1]:.1f} above run {i}'s end (wall facing the rider)")
    for f in b.features:
        if f["beat"] == "kicker_gap" and not f.get("theta"):
            bad.append(f"kicker at x {f['x0']} has no ramp angle")
    return bad


# ── device: verify / play ────────────────────────────────────────────────────────────────────────
def calibration():
    """gold / bot_time over stock world-1 levels the bot finished (latest benchmark), default 1.0."""
    for name in ("w1-benchmark.json", "baseline-v0.json"):
        try:
            with open(os.path.expanduser(f"~/.cache/revenant/bot/{name}")) as f:
                rows = json.load(f)["rows"]
        except (OSError, KeyError, ValueError):
            continue
        r = [x["times"][2] / x["run_time"] for x in rows if x["result"] == "finish" and x.get("run_time")]
        if r:
            return round(st.median(r), 3)
    return 1.0


def death_beat(meta, x):
    if x is None:
        return None
    for f in meta["features"]:
        if f["x0"] - 20 <= x <= f["x1"] + 20:
            return f["beat"]
    return None


def verify(rig, level, meta, n, record=False, stop_on_finish=True, params=None):
    """Push + up to n bot runs (bot_eval.run_level: slices, trace, optional video). One finish proves the level
    is beatable; the bot isn't run-to-run deterministic, so failures are retried. Returns {pass, run_time, tries}."""
    import bot_eval
    name = meta.get("name") or f"gen-{meta['seed']}-{meta['difficulty']}"
    dat = lg.export(level, name)
    rig.push_level(dat, meta["slot"])
    tries = []
    for k in range(max(1, n)):
        for _ in range(3):                             # a game exit (Facebook prompt -> System.exit) isn't the bot's
            r = bot_eval.run_level(rig, meta["slot"], params or {}, trace=True, record=record, level=level,
                                   tag=f"{name}-{k}")  # fault: relaunched by the rig, the run is retried
            if r["result"] != "app_exit":
                break
        tries.append({"result": r["result"], "run_time": r.get("run_time"), "x": r.get("x"),
                      "beat": death_beat(meta, r.get("x")) if r["result"] != "finish" else None,
                      "video": r.get("video"), "run_json": r.get("run_json")})
        if r["result"] == "load_failed" or (stop_on_finish and r["result"] == "finish"):
            break
    ok = [t for t in tries if t["result"] == "finish"]
    return {"pass": bool(ok), "run_time": min(t["run_time"] for t in ok) if ok else None,
            "finished": len(ok), "tries": tries, "dat": dat}


def gen_one(rig, seed, difficulty, slot, n_verify=0, play=False, record=False):
    level, meta, fines = generate(seed, difficulty, slot)
    out = dict(meta)
    if n_verify and rig:
        v = verify(rig, level, meta, n_verify, record=record)
        out["verify"] = {k: v[k] for k in ("pass", "run_time", "finished", "tries")}
        if v["pass"]:
            gold = round(v["run_time"] * calibration(), 1)
            out["times"] = [round(gold * r, 1) for r in STAR_RATIOS]
            level, meta2, _ = generate(seed, difficulty, slot, times=out["times"])
    else:
        out["times"] = level["times"]
    name = f"gen-{seed}-{difficulty}"
    dat = lg.export(level, name)
    out["dat"] = dat
    if rig and (play or (n_verify and out.get("verify", {}).get("pass"))):
        rig.push_level(dat, slot)
        if play:
            ld = rig.load(slot)
            out["play"] = {"loaded": ld["ok"], "mod_line": bool(ld["mod_line"])}
    return out


FEATURES = ["rollers", "climb", "descent", "kicker_gap", "drop", "seesaw", "barrels"]


def feature_test(rig, slot, diffs, seeds, runs, record=False, only=None):
    """The bot's gate (owner decision 2026-10-15): each generator feature, alone between a flat start and a flat
    finish, at each difficulty, for each seed, ridden `runs` times. A (feature, difficulty) cell passes when every
    run of every seed finishes. Writes features.json with `allowed` = {feature: highest difficulty passed with
    all easier difficulties passing too}; generate() then only uses those features at those difficulties."""
    rows, out = [], {"allowed": {}}
    for f in [x for x in FEATURES if not only or x in only]:
        for d in diffs:
            for sd in seeds:
                seed = 1000 * (FEATURES.index(f) + 1) + int(round(d * 100)) + sd
                level, meta, _ = generate(seed, d, slot, plan=[("flat", {"len": 160}), (f, {}), ("flat", {"len": 240})],
                                          allowed=None)
                meta["name"] = f"feat-{f}-{d}-{sd}"
                v = verify(rig, level, meta, runs, record=record, stop_on_finish=False)
                row = {"feature": f, "difficulty": d, "seed": seed, "finished": v["finished"], "runs": len(v["tries"]),
                       "best_time": v["run_time"], "info": meta["features"][1], "placed": meta["placed"],
                       "tries": [{k: t.get(k) for k in ("result", "run_time", "x", "video", "run_json")} for t in v["tries"]]}
                rows.append(row)
                print(f"{f:10s} d={d:.1f} seed={seed}  {row['finished']}/{row['runs']}  "
                      f"{[t['result'] for t in v['tries']]}  {json.dumps(row['info'])}", flush=True)
                out = save_features(rows, diffs, seeds, runs)      # after EVERY cell: a game exit loses nothing
    return out


def save_features(rows, diffs, seeds, runs):
    """Merge `rows` into features.json (rows of features not in `rows` are kept) and recompute `allowed`."""
    try:
        with open(os.path.join(lg.OUT, "features.json")) as fh:
            prev = json.load(fh)
        cells = {(r["feature"], r["difficulty"], r["seed"]) for r in rows}
        rows = [r for r in prev.get("rows", []) if (r["feature"], r["difficulty"], r["seed"]) not in cells] + rows
    except (OSError, ValueError):
        pass
    allowed = {}
    for f in {r["feature"] for r in rows}:
        cap = None
        for d in sorted(diffs):
            cell = [r for r in rows if r["feature"] == f and r["difficulty"] == d]
            if cell and all(r["finished"] == r["runs"] for r in cell):
                cap = d
            else:
                break
        if cap is not None:
            allowed[f] = cap
    out = {"allowed": allowed, "diffs": diffs, "seeds": seeds, "runs": runs, "rows": rows}
    os.makedirs(lg.OUT, exist_ok=True)
    with open(os.path.join(lg.OUT, "features.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    return out


def seeds_of(spec):
    out = []
    for part in str(spec).split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gen")
    g.add_argument("--seed", type=int, required=True)
    g.add_argument("--difficulty", type=float, default=0.5)
    g.add_argument("--slot", default="1_24")
    g.add_argument("--verify", type=int, default=0, metavar="N", help="up to N bot runs; one finish = beatable (0 = no verify)")
    g.add_argument("--tries", type=int, default=1, help="with --verify: advance the seed until a pass (max K seeds)")
    g.add_argument("--play", action="store_true")
    bt = sub.add_parser("batch")
    bt.add_argument("--seeds", default="1-20")
    bt.add_argument("--difficulty", type=float, default=0.5)
    bt.add_argument("--slot", default="1_24")
    bt.add_argument("--verify", type=int, default=2)
    bt.add_argument("--out", default=os.path.join(lg.OUT, "batch.json"))
    ft = sub.add_parser("features", help="the bot's gate: every feature x difficulty x seed, ridden N times")
    ft.add_argument("--slot", default="1_24")
    ft.add_argument("--diffs", default="0.2,0.5,0.8")
    ft.add_argument("--seeds", default="1,2")
    ft.add_argument("--runs", type=int, default=3)
    ft.add_argument("--only", default="", help="comma list of features")
    for sp in (g, bt, ft):
        sp.add_argument("--record", action="store_true", help="screenrecord every bot run (mp4 + actions json)")
    a = ap.parse_args()

    rig = None
    if a.cmd == "features" or a.verify or getattr(a, "play", False):
        from rig import Rig
        rig = Rig()
        rig.ensure_game()
    if a.cmd == "features":
        res = feature_test(rig, a.slot, [float(x) for x in a.diffs.split(",")], seeds_of(a.seeds), a.runs,
                           record=a.record, only=[x for x in a.only.split(",") if x])
        print(json.dumps({"allowed": res["allowed"]}, indent=1))
    elif a.cmd == "gen":
        for k in range(max(1, a.tries)):
            out = gen_one(rig, a.seed + k, a.difficulty, a.slot, a.verify, a.play, record=a.record)
            print(json.dumps(out, indent=1))
            if not a.verify or out["verify"]["pass"]:
                break
            print(f"seed {a.seed + k} REJECTED (bot failed)", file=sys.stderr)
    else:
        rows = []
        for s in seeds_of(a.seeds):
            out = gen_one(rig, s, a.difficulty, a.slot, a.verify, record=a.record)
            v = out.get("verify") or {}
            last = (v.get("tries") or [{}])[-1]
            rows.append(out)
            print(f"seed {s:3d} len {out['length']:5d}  {'PASS' if v.get('pass') else 'FAIL':4s}  "
                  f"rt={v.get('run_time') or 0:6.2f}  times={out.get('times')}  "
                  f"last={last.get('result')} x={last.get('x')} beat={last.get('beat')}  plan={'/'.join(out['plan'])}", flush=True)
        passed = [r for r in rows if (r.get("verify") or {}).get("pass")]
        deaths = [t for r in rows for t in (r.get("verify") or {}).get("tries", []) if t["result"] != "finish"]
        summary = {"seeds": len(rows), "passed": len(passed), "pass_rate": round(len(passed) / max(1, len(rows)), 3),
                   "run_time": {"median": st.median([r["verify"]["run_time"] for r in passed]) if passed else None,
                                "min": min((r["verify"]["run_time"] for r in passed), default=None),
                                "max": max((r["verify"]["run_time"] for r in passed), default=None)},
                   "failed_attempts_by_beat": {k: sum(1 for t in deaths if t.get("beat") == k)
                                               for k in sorted({str(t.get("beat")) for t in deaths})},
                   "failed_attempts_by_result": {k: sum(1 for t in deaths if t["result"] == k)
                                                 for k in sorted({t["result"] for t in deaths})},
                   "difficulty": a.difficulty, "calibration_gold_over_bot": calibration()}
        print(json.dumps(summary, indent=1))
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        with open(a.out, "w") as f:
            json.dump({"summary": summary, "rows": rows}, f, indent=1)
    if rig:
        rig.close()


if __name__ == "__main__":
    main()
