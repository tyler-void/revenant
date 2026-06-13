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

# Calibrated against the 130-level corpus (docs/procgen.md): real terrain is a
# FINELY sampled surface (median 16u between vertices) with mostly gentle rideable
# grades; steep faces are deliberate features, not ambient. The old generator used
# coarse ~60-120u steps + a 46° ambient cap → "lazy straight lines, too steep".
STEP = 26.0             # surface sample spacing (≈ corpus median) → smooth curves
AMBIENT_SLOPE = 0.62    # gentle rideable grade (~32°) for rolling ground
RAMP_SLOPE = 1.15       # deliberate launch ramps may exceed ambient (~49°)
FLAT_START = 340.0      # spawn flat zone
FLAT_END = 320.0        # finish flat zone


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
            "finish_children": finish_children, "decor": decor, "barrel": barrel,
            "obstacles": _scan_prefab_obstacles(E)}


def _scan_prefab_obstacles(primary_E):
    """Find single-prefab obstacle templates (Spikes/Nitro) — simple position-based
    entities (no joints/refs). 1_1 lacks them, so fall back to scanning the rest of the
    decoded cache (any imported level) for the first of each; theme-correct because we
    only generate world-1-themed levels. Returns {Type: entity-template or None}."""
    want = {"Spikes": None, "Nitro": None}
    def take(E):
        for e in E:
            t = e.get("Type")
            if t in want and want[t] is None:
                want[t] = copy.deepcopy(e)
    take(primary_E)
    if any(v is None for v in want.values()):
        for fn in sorted(os.listdir(CACHE)):
            if not fn.endswith(".level.json"):
                continue
            try:
                take(json.load(open(os.path.join(CACHE, fn))).get("Entities", []))
            except Exception:
                pass
            if all(v is not None for v in want.values()):
                break
    return want


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


# ── terrain: a smooth fine-sampled heightfield + deliberate jump features ─────
# (replaces the old coarse "chunk" emitters; see docs/procgen.md for the why)
def _octaves(rng):
    """A few sine octaves → natural rolling hills (big rolls + medium + small bumps)."""
    return [(rng.uniform(620, 900), rng.uniform(26, 50), rng.uniform(0, 2 * math.pi)),
            (rng.uniform(190, 300), rng.uniform(9, 19),  rng.uniform(0, 2 * math.pi)),
            (rng.uniform(75, 125),  rng.uniform(3, 8),   rng.uniform(0, 2 * math.pi))]


def build_segments(rng, length, difficulty):
    """Build the rideable surface as a smooth, finely sampled heightfield, then
    carve deliberate jump gaps (ramp lip → void → lower landing). Returns a list of
    ground segments (each a list of {x,y,segments} points); gaps are the breaks
    between segments. Smoothness comes from fine sampling (STEP≈corpus median) and a
    gentle ambient slope cap; only carved ramps exceed it."""
    octs = _octaves(rng)
    def base(x):
        return sum(A * math.sin(2 * math.pi * x / W + p) for (W, A, p) in octs)

    # 1) fine smooth points: amplitude follows the difficulty envelope; flat ends.
    pts = []  # list of [x, y]
    x = 0.0; prevy = 0.0
    while x <= length + 0.5:
        if x <= FLAT_START:
            y = 0.0
        elif x >= length - FLAT_END:
            y = prevy                                   # hold flat into the finish
        else:
            amp = (0.45 + 0.95 * envelope(x / length)) * (0.55 + 0.75 * difficulty)
            y = base(x) * amp
        if pts:                                         # gentle ambient slope clamp
            md = AMBIENT_SLOPE * (x - pts[-1][0])
            y = max(prevy - md, min(prevy + md, y))
        pts.append([x, y]); prevy = y
        x += STEP

    # 2) choose jump-gap centres in the ramp→climax region; escalate with difficulty
    gaps = []
    if difficulty >= 0.32:
        ngaps = 1 + int(difficulty * 2.4)
        cand_t = sorted(rng.uniform(0.40, 0.90) for _ in range(ngaps))
        last_t = -1
        for t in cand_t:
            if t - last_t < 0.12:        # keep jumps spaced out
                continue
            last_t = t
            gaps.append(t * length)

    # 3) carve each gap: raise a takeoff ramp into the lip, void, lower flat landing
    removed = [False] * len(pts)
    def idx_at(xx):
        return min(range(len(pts)), key=lambda i: abs(pts[i][0] - xx))
    for gx in gaps:
        d = difficulty * envelope(gx / length)
        lip = idx_at(gx)
        if lip < 3 or lip > len(pts) - 6:
            continue
        gap_w = rng.uniform(95, 130) + d * 70           # jumpable void width
        rise = rng.uniform(45, 80) * (0.6 + d)          # takeoff ramp height
        n_ramp = 3                                       # ramp up over the last few pts
        lip_y = pts[lip][1] + rise
        for k in range(n_ramp + 1):                      # linear takeoff ramp to the lip
            i = lip - n_ramp + k
            if i >= 0:
                pts[i][1] = max(pts[i][1], pts[lip - n_ramp][1] + rise * k / n_ramp)
        pts[lip][1] = lip_y
        land = idx_at(gx + gap_w)
        land = max(land, lip + 1)
        for i in range(lip + 1, land):                   # remove points over the void
            removed[i] = True
        land_y = lip_y - rng.uniform(30, 70)             # land lower than the lip
        for j in range(land, min(land + 4, len(pts))):   # flat-ish landing run
            pts[j][1] = land_y

    # 4) split into contiguous (non-removed) ground segments
    segments = []; cur = []
    for i, p in enumerate(pts):
        if removed[i]:
            if cur:
                segments.append(cur); cur = []
            continue
        cur.append({"x": float(p[0]), "y": float(p[1]), "segments": 2})
    if cur:
        segments.append(cur)
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
    """Drop ExplosiveBarrel obstacles, spread along the track on ridable ground.
    Count scales with difficulty; spots are spaced so barrels are clearly
    encountered (not clustered at spawn). The barrel sprite is forced ABOVE the
    terrain z so the ground fill can never occlude it."""
    barrel = tpl.get("barrel")
    if not barrel:
        return 0
    count = max(2, int(round(difficulty * rng.uniform(4, 7))))
    # candidate spots on gentle ground, away from segment ends (gaps/finish)
    spots = []
    for seg in segments:
        y_at, x0, x1 = _seg_surface(seg)
        if x1 - x0 < 180:
            continue
        x = max(x0 + 120, FLAT_START + 60)              # keep barrels out of the spawn flat
        while x < x1 - 120:
            flat = abs(y_at(x + 20) - y_at(x - 20)) < 30   # near-flat = fair landing
            if flat:
                spots.append((x, y_at(x)))
            x += rng.uniform(90, 150)
    if not spots:
        return 0
    spots.sort()                                            # spread evenly by x, not clustered
    stride = max(1, len(spots) // count)
    chosen = spots[::stride][:count]
    placed = 0
    for (bx, by) in chosen:
        kids = [copy.deepcopy(k) for k in barrel["children"]]
        idxs = []
        for k in kids:
            _translate_entity(k, bx, by + 8)               # rest on the surface
            if k.get("Type") == "EditorSprite":            # keep the barrel art in front
                k["Properties"]["z"] = 6
            idxs.append(len(ents)); ents.append(k)
        grp = copy.deepcopy(barrel["group"])
        grp["Properties"]["refobjectList"] = idxs
        grp["Properties"]["name"] = "GenBarrel%d" % placed
        grp["Properties"]["z"] = 5
        ents.append(grp); placed += 1
    return placed


def place_obstacles(rng, segments, ents, tpl, difficulty):
    """Place single-prefab obstacles: Nitro boost pads (safe/helpful, frequent) and
    Spikes (lethal — conservative, telegraphed on open flats with clear run-up, spaced
    from each other). Difficulty gates counts. Returns count placed."""
    obs = tpl.get("obstacles") or {}
    spike_t, nitro_t = obs.get("Spikes"), obs.get("Nitro")
    # flat, well-spaced candidate spots away from the spawn flat
    spots = []
    for seg in segments:
        y_at, x0, x1 = _seg_surface(seg)
        if x1 - x0 < 220:
            continue
        x = max(x0 + 160, FLAT_START + 90)
        while x < x1 - 160:
            if abs(y_at(x + 28) - y_at(x - 28)) < 24:    # flat + telegraphed run-up
                spots.append((x, y_at(x)))
            x += rng.uniform(120, 200)
    rng.shuffle(spots)
    used = []
    def far(x, mind):
        return all(abs(x - u) > mind for u in used)
    placed = 0
    # Nitro: helpful, scales freely with difficulty
    n_nitro = int(round(difficulty * rng.uniform(2, 4))) if nitro_t else 0
    for (sx, sy) in spots:
        if placed >= n_nitro:
            break
        if not far(sx, 200):
            continue
        e = copy.deepcopy(nitro_t); e["Properties"].update(position=[float(sx), float(sy + 10)],
                                                            name="GenNitro%d" % placed)
        ents.append(e); used.append(sx); placed += 1
    # Spikes: lethal — sparse, only when it's meant to be hard
    n_spike = int(round(difficulty * rng.uniform(0, 2.2))) if spike_t else 0
    sp = 0
    for (sx, sy) in spots:
        if sp >= n_spike:
            break
        if not far(sx, 240):
            continue
        e = copy.deepcopy(spike_t); e["Properties"].update(position=[float(sx), float(sy + 6)],
                                                           name="GenSpike%d" % sp)
        ents.append(e); used.append(sx); sp += 1
    return placed + sp
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
    n_obs = place_obstacles(rng, segments, ents, tpl, difficulty) if obstacles else 0

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
    nnit = sum(1 for e in E if e["Type"] == "Nitro")
    nspk = sum(1 for e in E if e["Type"] == "Spikes")
    print("generated %s  seed=%s len=%.0f diff=%.2f lid=%s\n  %d entities: %d slabs, %d decor, %d barrels, %d nitro, %d spikes  times=%s"
          % (a.out, a.seed, a.length, a.difficulty, a.lid, len(E), nslab, ndecor, nbar, nnit, nspk, lvl["times"]))


if __name__ == "__main__":
    _cli()
