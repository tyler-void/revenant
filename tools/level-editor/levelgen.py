#!/usr/bin/env python3
"""
Revenant Procedural Level Generator (Phase 5).

Generates a *logical, rideable* Bike Rivals track: a smooth rolling-hills terrain
spline with a flat spawn zone, a flat finish zone, and rideability-clamped slopes,
then drops in the working START (Moto) and FINISH (TriggerWin + gate) prefab
structures cloned from a template level. Deterministic: same seed → same track
(shareable one-line level codes).

Pipeline:  levelgen.py <seed> <out.json> [length] [difficulty]
           leveldec.py export <out.json> <out.dat> <KEYHEX>   # → device-loadable

Why template-based: the Moto and TriggerWin are prefab/grouped entities (TriggerWin
references its gate children by ENTITY INDEX via refobjectList). Cloning them from a
decoded template keeps those structures valid; we remap the indices on assembly.
Terrain is generated fresh (no cross-references).

⚠  Templates come from the local decoded cache (gitignored game data); this script
ships no game values.
"""
import json, os, sys, math, copy, random

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "levels")


# ── templates from a decoded level (valid game Properties; not committed) ────
def load_templates(template_lid="1_1"):
    p = os.path.join(CACHE, "%s.level.json" % template_lid)
    if not os.path.exists(p):
        raise SystemExit("need a decoded template at %s (import one first)" % p)
    lvl = json.load(open(p))
    E = lvl["Entities"]
    def first(t): return next((e for e in E if e.get("Type") == t), None)
    win = first("TriggerWin")
    refs = (win or {}).get("Properties", {}).get("refobjectList", []) if win else []
    finish_children = [copy.deepcopy(E[i]) for i in refs if 0 <= i < len(E)]
    terrain = first("EditorPhysicsObject")
    tprops = {k: v for k, v in terrain["Properties"].items() if k != "position"}
    return {
        "terrain_props": tprops,
        "moto": copy.deepcopy(first("Moto")),
        "camera": copy.deepcopy(first("EditorCamera")),
        "win": copy.deepcopy(win),
        "finish_children": finish_children,   # [Finish_Sprite, Finish_Sprite, win-trigger]
    }


# ── terrain: a rideable rolling-hills curve ──────────────────────────────────
def ground_curve(length, rng, difficulty, dx=64.0):
    """Top surface points (x,y) left→right: smooth ROLLING hills, flat spawn+finish
    ends, gentle rideable slopes. Wider point spacing + long wavelengths + a
    smoothing pass keep the Catmull-Rom from overshooting into spikes."""
    n = int(length / dx) + 1
    comps = []
    for _ in range(2 + int(difficulty * 3)):           # fewer, longer harmonics
        comps.append((rng.uniform(18, 40) * (0.5 + difficulty * 0.8),  # amplitude
                      rng.uniform(360, 1000),                           # long wavelength = rolling
                      rng.uniform(0, 2 * math.pi)))                     # phase
    flat = 280.0                                        # spawn/finish flat run
    raw = []
    for i in range(n):
        x = i * dx
        y = sum(a * math.sin(2 * math.pi * x / wl + ph) for a, wl, ph in comps)
        y *= min(x, length - x, flat) / flat            # ease the ends to flat
        raw.append([x, y])
    # smoothing pass (3-tap) — removes high-frequency jitter so hills flow
    for _ in range(2):
        sm = [raw[0][:]]
        for i in range(1, len(raw) - 1):
            sm.append([raw[i][0], (raw[i-1][1] + 2*raw[i][1] + raw[i+1][1]) / 4.0])
        sm.append(raw[-1][:]); raw = sm
    # clamp slope between adjacent points for rideability (~38°)
    lim = 0.78 * dx
    for i in range(1, len(raw)):
        dy = raw[i][1] - raw[i - 1][1]
        if dy > lim:    raw[i][1] = raw[i - 1][1] + lim
        elif dy < -lim: raw[i][1] = raw[i - 1][1] - lim
    return raw


def _interp(top, x):
    if x <= top[0][0]: return top[0][1]
    for i in range(1, len(top)):
        if x <= top[i][0]:
            x0, y0 = top[i - 1]; x1, y1 = top[i]
            t = (x - x0) / (x1 - x0 or 1)
            return y0 + (y1 - y0) * t
    return top[-1][1]


def generate(seed, length=2600, difficulty=0.5, template_lid="1_1"):
    rng = random.Random(seed)
    tpl = load_templates(template_lid)
    top = ground_curve(length, rng, difficulty)
    baseline = min(y for _, y in top) - 320

    # terrain polygon: top surface (smooth spline) + flat bottom, closed
    vtx = [{"x": x, "y": y, "segments": 4} for x, y in top]
    vtx.append({"x": length, "y": baseline, "segments": 1})
    vtx.append({"x": 0.0, "y": baseline, "segments": 1})
    terrain = {"Type": "EditorPhysicsObject", "Selected": False, "Vertexes": vtx,
               "Properties": copy.deepcopy(tpl["terrain_props"])}
    # position=[0,0] with world-space vertexes is safe whether the game treats
    # vertexes as world-absolute OR body-local (0 + vertex = vertex either way).
    terrain["Properties"].update(position=[0.0, 0.0], name="GenGround", spline=True, tag=1)

    ents = [terrain]

    # FINISH: clone the gate children + win-trigger, shift to the finish zone
    fx = length - 140
    fy = _interp(top, fx)
    child_start = len(ents)
    fxs = [c["Properties"].get("position", [0, 0])[0] for c in tpl["finish_children"] if "position" in c["Properties"]]
    fbx = sum(fxs) / len(fxs) if fxs else 0
    fby_list = [c["Properties"]["position"][1] for c in tpl["finish_children"] if "position" in c["Properties"]]
    fby = sum(fby_list) / len(fby_list) if fby_list else 0
    for c in tpl["finish_children"]:
        c = copy.deepcopy(c)
        if "position" in c["Properties"]:
            ox = c["Properties"]["position"][0] - fbx
            oy = c["Properties"]["position"][1] - fby
            c["Properties"]["position"] = [fx + ox, fy + 30 + oy]   # gate sits on the ground
        ents.append(c)
    if tpl["win"]:
        win = copy.deepcopy(tpl["win"])
        win["Properties"]["refobjectList"] = list(range(child_start, child_start + len(tpl["finish_children"])))
        ents.append(win)

    # START: Moto + camera at the left flat zone
    sx = 90.0; sy = _interp(top, sx)
    moto = copy.deepcopy(tpl["moto"])
    moto["Properties"]["position"] = [sx, sy + 12]
    ents.append(moto)
    if tpl["camera"]:
        cam = copy.deepcopy(tpl["camera"])
        cam["Properties"]["position"] = [sx, sy + 40]
        ents.append(cam)

    # medal times scale with length (gold tightest)
    base = length / 95.0
    times = [round(base * 1.18, 1), round(base * 1.08, 1), round(base, 1)]
    return {"lid": "5_1", "type": 2, "times": times, "Entities": ents}


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="Revenant procedural level generator")
    ap.add_argument("seed")
    ap.add_argument("out")
    ap.add_argument("length", nargs="?", type=float, default=2600)
    ap.add_argument("difficulty", nargs="?", type=float, default=0.5)
    ap.add_argument("--template", default="1_1")
    a = ap.parse_args()
    seed = int(a.seed) if a.seed.lstrip("-").isdigit() else a.seed
    lvl = generate(seed, a.length, a.difficulty, a.template)
    json.dump(lvl, open(a.out, "w"))
    n = len(lvl["Entities"])
    print("generated %s  seed=%s length=%.0f diff=%.2f  (%d entities, times=%s)"
          % (a.out, a.seed, a.length, a.difficulty, n, lvl["times"]))


if __name__ == "__main__":
    _cli()
