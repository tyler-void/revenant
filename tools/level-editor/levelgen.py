#!/usr/bin/env python3
"""
Revenant Procedural Level Generator (Phase 5) — chunk-based, difficulty-curved.

Generates a *challenging but always-solvable* Bike Rivals track using the
research-grounded two-tier model (Launchpad rhythm + Sure-Footing budget +
Spelunky critical-path; see docs/research.md):

  1. Difficulty ENVELOPE over progress t: warm-up (flat) → ramp-up → climax →
     resolution (flat to finish). Escalate by hazard severity, not length.
  2. CHUNKS assembled over the spine — flat / rollers / climb / whoops / ramp+gap
     (a launch ramp followed by a jumpable gap) — chosen by a difficulty budget.
  3. SOLVABILITY by construction: gaps only follow a ramp and are sized within a
     conservative jump range; slopes are clamped rideable; landings are flat.

Terrain is emitted as many small CCW quad slabs (the real game's structure — a
single big polygon builds an invalid Box2D body). START (Moto) + FINISH
(TriggerWin + gate) + camera are cloned from a decoded template (refobjectList is
entity-index based → remapped). Deterministic: seed → track.

  levelgen.py <seed> <out.json> [length] [difficulty] --lid <slot> --template <lid>
The lid MUST match the slot the .dat is placed in (e.g. --lid 1_4 to test in W1L4).

⚠ Templates come from the local decoded cache (gitignored game data); no game
values are committed.
"""
import json, os, math, copy, random

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "levels")

MAX_SLOPE = 1.05        # rideable grade (dy/dx) for ambient terrain (~46°)
FLAT_START = 320.0      # spawn flat zone
FLAT_END = 300.0        # finish flat zone


# Decoration frames, grouped by depth layer. Only freestanding scenery (no
# structural wood/metal/bridge/finish pieces). Prefixes resolve against whatever
# the template level actually contains, so a world-N template yields world-N art.
DECOR_LAYERS = {
    "bg":   ("Tree", "for"),       # foliage/trees — behind the terrain (z<0) or far fore
    "surf": ("bor", "ter", "for"), # rocks + ground detail — sit on the surface (z>0)
}


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

    # one decoration template per distinct frame, bucketed by layer (full
    # Properties preserved → spriteSheet/anchors/color stay valid for the world)
    decor = {"tree": [], "rock": [], "fore": []}
    seen = set()
    for e in E:
        if e.get("Type") != "EditorSprite":
            continue
        f = e["Properties"].get("frame") or ""
        if f in seen or not f:
            continue
        if f.startswith("Tree"):           bucket = "tree"
        elif f.startswith("for"):          bucket = "fore"
        elif f.startswith(("bor", "ter")): bucket = "rock"
        else:                              continue
        seen.add(f); decor[bucket].append(copy.deepcopy(e))

    # an ExplosiveBarrel obstacle: the group + its child body/sprite, recentred to
    # the origin so we can drop a copy anywhere on the track.
    barrel = None
    bg = first("ExplosiveBarrel")
    if bg:
        refs_b = bg["Properties"].get("refobjectList", [])
        kids = [copy.deepcopy(E[i]) for i in refs_b if 0 <= i < len(E)]
        cx = sum(k["Properties"]["position"][0] for k in kids if "position" in k["Properties"]) / max(1, len(kids))
        cy = sum(k["Properties"]["position"][1] for k in kids if "position" in k["Properties"]) / max(1, len(kids))
        for k in kids:
            _translate_entity(k, -cx, -cy)        # recentre children to origin
        barrel = {"group": copy.deepcopy(bg), "children": kids}

    return {"terrain_props": tprops, "moto": copy.deepcopy(first("Moto")),
            "camera": copy.deepcopy(first("EditorCamera")), "win": copy.deepcopy(win),
            "finish_children": finish_children, "decor": decor, "barrel": barrel}


def _translate_entity(ent, dx, dy):
    """Shift an entity by (dx,dy): both its position and any world-coord Vertexes."""
    p = ent.get("Properties", {})
    if "position" in p:
        p["position"] = [p["position"][0] + dx, p["position"][1] + dy]
    for v in ent.get("Vertexes", []) or []:
        v["x"] += dx; v["y"] += dy


def _signed_area(vtx):
    a = 0.0; n = len(vtx)
    for i in range(n):
        x1, y1 = vtx[i]["x"], vtx[i]["y"]; x2, y2 = vtx[(i + 1) % n]["x"], vtx[(i + 1) % n]["y"]
        a += x1 * y2 - x2 * y1
    return a / 2.0


# ── difficulty envelope ──────────────────────────────────────────────────────
def envelope(t):
    """Intensity in [0,1] over progress t: warm-up, ramp, climax, resolution."""
    if t < 0.12:  return 0.15
    if t < 0.80:  return 0.15 + 0.85 * (t - 0.12) / 0.68      # rising ramp
    if t < 0.95:  return 1.0                                   # climax
    return 0.10                                                # resolution


# ── chunk emitters: each appends surface points and returns (x, y, gap_width) ─
def _ramp_clamp(pts, x0, y0, x1, y1):
    """append a point, clamping the slope from the previous one to rideable."""
    dx = max(1.0, x1 - x0)
    dy = max(-MAX_SLOPE * dx, min(MAX_SLOPE * dx, y1 - y0))
    return x1, y0 + dy


def chunk_flat(rng, x, y, d, push, length=None):
    L = length or rng.uniform(140, 220)
    n = max(2, int(L / 60))
    for i in range(1, n + 1):
        nx = x + L * i / n
        ny = y + rng.uniform(-4, 4)
        nx, ny = _ramp_clamp(None, x, y, nx, ny)
        push(nx, ny); x, y = nx, ny
    return x, y, 0


def chunk_rollers(rng, x, y, d, push):
    """Smooth rolling hills: a few full sine cycles around the base height."""
    cycles = rng.randint(2, 3)
    amp = rng.uniform(28, 55) * (0.5 + 0.8 * d)
    wl = rng.uniform(150, 230)
    base = y
    n = cycles * 8
    for k in range(1, n + 1):
        nx = x + (wl * cycles) / n
        ny = base + amp * math.sin(2 * math.pi * cycles * k / n)
        nx, ny = _ramp_clamp(None, x, y, nx, ny)
        push(nx, ny); x, y = nx, ny
    return x, base, 0


def chunk_climb(rng, x, y, d, push):
    """A hill: rideable climb up to a crest, then back down most of the way."""
    h = rng.uniform(70, 150) * (0.5 + d)
    up = rng.uniform(150, 220)
    for _ in range(4):
        nx, ny = _ramp_clamp(None, x, y, x + up / 4, y + h / 4)
        push(nx, ny); x, y = nx, ny
    for _ in range(4):
        nx, ny = _ramp_clamp(None, x, y, x + up / 4, y - (h * 0.75) / 4)
        push(nx, ny); x, y = nx, ny
    return x, y, 0


def chunk_whoops(rng, x, y, d, push):
    """Rapid bumps (technical / air time): tight alternating up/down."""
    n = rng.randint(4, 7)
    amp = rng.uniform(35, 65) * (0.6 + 0.7 * d)
    wl = rng.uniform(80, 120)
    base = y
    for k in range(1, n + 1):
        nx = x + wl
        ny = base + (amp if k % 2 == 1 else -amp * 0.45)
        # subdivide so the clamp doesn't flatten the bump
        for s in range(1, 4):
            sx = x + (nx - x) * s / 3; sy = y + (ny - y) * s / 3
            sx, sy = _ramp_clamp(None, x, y, sx, sy); push(sx, sy); x, y = sx, sy
    return x, base, 0


def chunk_ramp_gap(rng, x, y, d, push):
    """A launch ramp up to a lip, then a jumpable GAP, then a flat landing.
    Gap width scales with difficulty but stays within a conservative jump range."""
    rise = rng.uniform(50, 95) * (0.6 + d)
    run = rng.uniform(110, 170)
    # ramp up to a lip (steeper allowed than ambient — it's a deliberate launch)
    steps = 4
    for k in range(1, steps + 1):
        nx = x + run / steps; ny = y + rise / steps
        push(nx, ny); x, y = nx, ny
    # GAP — conservative, escalates with difficulty
    gap = rng.uniform(90, 130) + d * 90
    return x, y, gap


CHUNKS = [
    ("flat",     0, lambda d: True),
    ("rollers",  1, lambda d: True),
    ("climb",    2, lambda d: d > 0.22),
    ("whoops",   2, lambda d: d > 0.30),
    ("ramp_gap", 3, lambda d: d > 0.40),
]
_EMIT = {"flat": chunk_flat, "rollers": chunk_rollers, "climb": chunk_climb,
         "whoops": chunk_whoops, "ramp_gap": chunk_ramp_gap}


def build_segments(rng, length, difficulty):
    """Returns a list of ground segments (each a list of {x,y,segments} points);
    gaps are the breaks BETWEEN segments."""
    segments = []; cur = []
    def push(px, py, seg=2): cur.append({"x": float(px), "y": float(py), "segments": seg})
    x, y = 0.0, 0.0
    push(x, y)
    x, y, _ = chunk_flat(rng, x, y, 0, push, length=FLAT_START)   # spawn zone
    last = "flat"; gaps_placed = 0

    def do_gap(x, y, d):
        segments.append(cur[:]); cur.clear()
        # conservative jump width — launched off the preceding ramp lip; landing
        # is lower so it's reachable even at modest speed.
        x += rng.uniform(75, 105) + d * 45
        y = y - rng.uniform(30, 70)                  # land lower than the lip
        push(x, y)
        return chunk_flat(rng, x, y, d, push, length=rng.uniform(140, 200))[:2]  # recovery landing

    while x < length - FLAT_END - 200:
        t = x / length
        d = difficulty * envelope(t)
        cands = [c for c, cost, ok in CHUNKS if ok(d) and not (c == "ramp_gap" and last == "ramp_gap")]
        weights = []
        for c in cands:
            if c == "flat":      w = 0.55 * (1 - d) + 0.10     # rest fades as it gets harder
            elif c == "rollers": w = 0.7
            else:                w = (0.25 + d) ** 1.3 * (2.4 if c == "ramp_gap" else 1.2)
            weights.append(w)
        chunk = rng.choices(cands, weights=weights)[0]
        x, y, gap = _EMIT[chunk](rng, x, y, d, push)
        last = chunk
        if gap > 0:
            x, y = do_gap(x, y, d); gaps_placed += 1; last = "flat"
    # guaranteed climax jump: if it's meant to be challenging but no gap landed, add one
    if difficulty >= 0.45 and gaps_placed == 0:
        x, y, gap = chunk_ramp_gap(rng, x, y, difficulty, push)
        x, y = do_gap(x, y, difficulty); gaps_placed += 1
    # finish flat zone
    x, y, _ = chunk_flat(rng, x, y, 0, push, length=FLAT_END)
    push(length, y)
    segments.append(cur[:])
    return segments


def _interp_first(segments):
    return segments[0][0]


# ── scenery & obstacles ───────────────────────────────────────────────────────
def _seg_surface(seg):
    """y = f(x) along one ground segment (x is monotonic within a segment)."""
    pts = [(p["x"], p["y"]) for p in seg]
    def y_at(x):
        if x <= pts[0][0]: return pts[0][1]
        if x >= pts[-1][0]: return pts[-1][1]
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]; x1, y1 = pts[i + 1]
            if x0 <= x <= x1:
                f = (x - x0) / (x1 - x0) if x1 > x0 else 0
                return y0 + f * (y1 - y0)
        return pts[-1][1]
    return y_at, pts[0][0], pts[-1][0]


def _place_decor(rng, tmpl, x, y, z=None, scale_mul=(0.7, 1.25), rot_jitter=0.0,
                 name="Decor"):
    """Clone a decoration template at (x,y) with jitter; returns the new entity."""
    e = copy.deepcopy(tmpl); p = e["Properties"]
    p["position"] = [float(x), float(y)]
    p["scale"] = float(p.get("scale", 1.0) * rng.uniform(*scale_mul))
    p["flipImageX"] = bool(rng.random() < 0.5)
    if rot_jitter:
        p["rotation"] = float(p.get("rotation", 0.0) + rng.uniform(-rot_jitter, rot_jitter))
    if z is not None:
        p["z"] = z
    p["name"] = name
    return e


def decorate(rng, segments, ents, tpl, difficulty):
    """Scatter depth-layered scenery so the track reads as hand-built, not bare."""
    decor = tpl.get("decor") or {}
    trees, rocks, fores = decor.get("tree", []), decor.get("rock", []), decor.get("fore", [])
    n0 = len(ents)
    for seg in segments:
        y_at, x0, x1 = _seg_surface(seg)
        if x1 - x0 < 80:
            continue
        # surface rocks / ground detail — sit ON the ground, in front of terrain
        if rocks:
            x = x0 + rng.uniform(40, 120)
            while x < x1 - 40:
                t = _place_decor(rng, rng.choice(rocks), x, y_at(x) + rng.uniform(2, 16),
                                 z=rng.choice([3, 4, 5]), rot_jitter=6, name="GenRock")
                ents.append(t)
                x += rng.uniform(110, 230)
        # background trees — behind the terrain, rooted near the surface
        if trees:
            x = x0 + rng.uniform(60, 200)
            while x < x1 - 40:
                t = _place_decor(rng, rng.choice(trees), x, y_at(x) + rng.uniform(35, 85),
                                 z=rng.choice([-5, -6, -8]), scale_mul=(0.8, 1.4),
                                 rot_jitter=5, name="GenTree")
                ents.append(t)
                x += rng.uniform(200, 380)
    # foreground silhouettes — a few big dark bushes for parallax depth
    if fores:
        x0 = segments[0][0]["x"]; x1 = segments[-1][-1]["x"]
        for _ in range(rng.randint(3, 6)):
            x = rng.uniform(x0 + 300, x1 - 300)
            seg = next((s for s in segments if s[0]["x"] <= x <= s[-1]["x"]), None)
            yb = _seg_surface(seg)[0](x) if seg else 0
            ents.append(_place_decor(rng, rng.choice(fores), x, yb - rng.uniform(0, 50),
                                     z=13, scale_mul=(0.8, 1.2), name="GenFore"))
    return len(ents) - n0


def place_barrels(rng, segments, ents, tpl, difficulty):
    """Drop ExplosiveBarrel obstacles on gentle ground; count scales with difficulty."""
    barrel = tpl.get("barrel")
    if not barrel:
        return 0
    count = int(round(difficulty * rng.uniform(2, 5)))
    placed = 0
    spots = []
    for seg in segments:
        y_at, x0, x1 = _seg_surface(seg)
        if x1 - x0 < 200:
            continue
        x = x0 + 150
        while x < x1 - 150:
            if abs(y_at(x + 20) - y_at(x - 20)) < 22:   # near-flat → a fair barrel spot
                spots.append((x, y_at(x)))
            x += rng.uniform(120, 220)
    rng.shuffle(spots)
    for (bx, by) in spots[:count]:
        kids = [copy.deepcopy(k) for k in barrel["children"]]
        idxs = []
        for k in kids:
            _translate_entity(k, bx, by + 10)          # barrel sits on the surface
            idxs.append(len(ents)); ents.append(k)
        grp = copy.deepcopy(barrel["group"])
        grp["Properties"]["refobjectList"] = idxs
        grp["Properties"]["name"] = "GenBarrel%d" % placed
        ents.append(grp); placed += 1
    return placed


def generate(seed, length=2600, difficulty=0.6, template_lid="1_1", lid="5_1",
             decor=True, obstacles=True):
    rng = random.Random(seed)
    tpl = load_templates(template_lid)
    segments = build_segments(rng, length, difficulty)
    all_y = [p["y"] for seg in segments for p in seg]
    baseline = min(all_y) - 340

    ents = []
    # GROUND = small CCW quad slabs per segment (gaps = no slab between segments)
    for si, seg in enumerate(segments):
        for i in range(len(seg) - 1):
            x0, y0 = seg[i]["x"], seg[i]["y"]; x1, y1 = seg[i + 1]["x"], seg[i + 1]["y"]
            if x1 - x0 < 1:  # skip degenerate
                continue
            vtx = [{"x": x0, "y": y0, "segments": 1}, {"x": x0, "y": baseline, "segments": 1},
                   {"x": x1, "y": baseline, "segments": 1}, {"x": x1, "y": y1, "segments": 1}]
            if _signed_area(vtx) < 0:
                vtx.reverse()
            slab = {"Type": "EditorPhysicsObject", "Selected": False, "Vertexes": vtx,
                    "Properties": copy.deepcopy(tpl["terrain_props"])}
            slab["Properties"].update(position=[0.0, 0.0], name="GenGround_%d_%d" % (si, i),
                                      spline=False, tag=1)
            ents.append(slab)

    # SCENERY + OBSTACLES (cloned real prefabs) — appended before the finish/start so
    # their (index-based) refs stay self-contained; nothing reorders ents afterward.
    n_decor = decorate(rng, segments, ents, tpl, difficulty) if decor else 0
    n_barrel = place_barrels(rng, segments, ents, tpl, difficulty) if obstacles else 0

    # FINISH at the end of the last segment
    fp = segments[-1][-2] if len(segments[-1]) >= 2 else segments[-1][-1]
    fx, fy = fp["x"] - 80, fp["y"]
    child_start = len(ents)
    fxs = [c["Properties"]["position"][0] for c in tpl["finish_children"] if "position" in c["Properties"]]
    fby = [c["Properties"]["position"][1] for c in tpl["finish_children"] if "position" in c["Properties"]]
    fbx = sum(fxs) / len(fxs) if fxs else 0
    fbyc = sum(fby) / len(fby) if fby else 0
    for c in tpl["finish_children"]:
        c = copy.deepcopy(c)
        if "position" in c["Properties"]:
            ox = c["Properties"]["position"][0] - fbx; oy = c["Properties"]["position"][1] - fbyc
            c["Properties"]["position"] = [fx + ox, fy + 30 + oy]
        ents.append(c)
    if tpl["win"]:
        win = copy.deepcopy(tpl["win"])
        win["Properties"]["refobjectList"] = list(range(child_start, child_start + len(tpl["finish_children"])))
        ents.append(win)

    # START: Moto + camera at the spawn flat
    sp = segments[0][1] if len(segments[0]) > 1 else segments[0][0]
    sx, sy = sp["x"], sp["y"]
    moto = copy.deepcopy(tpl["moto"]); moto["Properties"]["position"] = [sx, sy + 12]
    ents.append(moto)
    if tpl["camera"]:
        cam = copy.deepcopy(tpl["camera"]); cam["Properties"]["position"] = [sx, sy + 40]
        ents.append(cam)

    base = length / 92.0
    times = [round(base * 1.20, 1), round(base * 1.09, 1), round(base, 1)]
    return {"lid": lid, "type": 2, "times": times, "Entities": ents}


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="Revenant procedural level generator")
    ap.add_argument("seed"); ap.add_argument("out")
    ap.add_argument("length", nargs="?", type=float, default=2600)
    ap.add_argument("difficulty", nargs="?", type=float, default=0.6)
    ap.add_argument("--template", default="1_1")
    ap.add_argument("--lid", default="5_1", help="MUST match the target slot (e.g. 1_4)")
    ap.add_argument("--no-decor", action="store_true", help="skip scenery")
    ap.add_argument("--no-obstacles", action="store_true", help="skip barrels")
    a = ap.parse_args()
    seed = int(a.seed) if a.seed.lstrip("-").isdigit() else a.seed
    lvl = generate(seed, a.length, a.difficulty, a.template, a.lid,
                   decor=not a.no_decor, obstacles=not a.no_obstacles)
    json.dump(lvl, open(a.out, "w"))
    E = lvl["Entities"]
    nslab = sum(1 for e in E if e["Type"] == "EditorPhysicsObject" and e["Properties"].get("tag") == 1)
    ndecor = sum(1 for e in E if str(e["Properties"].get("name", "")).startswith(("GenRock", "GenTree", "GenFore")))
    nbar = sum(1 for e in E if e["Type"] == "ExplosiveBarrel")
    print("generated %s  seed=%s len=%.0f diff=%.2f lid=%s\n  %d entities: %d slabs, %d decor, %d barrels  times=%s"
          % (a.out, a.seed, a.length, a.difficulty, a.lid, len(E), nslab, ndecor, nbar, lvl["times"]))


if __name__ == "__main__":
    _cli()
