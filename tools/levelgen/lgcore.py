"""Generator core: templates from the LOCAL decoded corpus, track surfaces, terrain encoders, typed export.

Nothing here carries game data: property templates are read at run time from ~/.cache/revenant/corpus
(tools/levelgen/decode_corpus.py) and generated levels are written to ~/.cache/revenant/levelgen/.
Units are level units == Box2D world units (VERIFIED, docs/procgen.md).
"""
import copy
import json
import math
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools", "level-editor"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leveldec as ld          # noqa: E402
import corpus_stats as cs      # noqa: E402

CORPUS = os.path.expanduser("~/.cache/revenant/corpus")
OUT = os.path.expanduser("~/.cache/revenant/levelgen")


def corpus_level(lid):
    p = os.path.join(CORPUS, f"{lid}.level.json")
    if not os.path.exists(p):
        raise SystemExit(f"no decoded corpus level {p} — run tools/levelgen/decode_corpus.py first")
    with open(p) as f:
        return json.load(f)


def solid_ground(e):
    """Static, colliding ground (categoryBits "0000" collides with nothing — decorative)."""
    P = e.get("Properties") or {}
    return cs.is_ground(e) and str(P.get("categoryBits", "0001")) != "0000"


def ground_y(E, x, below=None):
    """Topmost solid surface at x (or the highest one at/below y=`below`, e.g. under an overhang)."""
    edges = [ed for e in E if solid_ground(e) for ed in cs.top_edges(cs.outline(e) or [])]
    ys = [y0 + (y1 - y0) * (x - x0) / (x1 - x0) for x0, y0, x1, y1 in edges if x0 <= x <= x1]
    if below is not None:
        ys = [y for y in ys if y <= below + 0.5]
    return max(ys) if ys else None


class Templates:
    """Start / camera / finish / ground property templates from one corpus level (default 1_24 — the
    test slot itself, so the art theme matches the slot's world)."""

    def __init__(self, lid="1_24"):
        L = corpus_level(lid)
        E = L["Entities"]
        self.level = L
        self.moto = copy.deepcopy(next(e for e in E if e["Type"] in cs.BIKES))
        self.camera = copy.deepcopy(next(e for e in E if e["Type"] == "EditorCamera"))
        mx, my = self.moto["Properties"]["position"]
        self.moto_dy = my - ground_y(E, mx, below=my)            # spawn height above the surface
        cx, cy = self.camera["Properties"]["position"]
        self.cam_off = (cx - mx, cy - my)
        win = next((e for e in E if e["Type"].startswith("TriggerWin") and "prefabName" in e["Properties"]), None)
        if win is None:                                            # fall back to any world-1 bare finish
            for k in range(1, 31):
                W = corpus_level(f"1_{k}")["Entities"]
                win = next((e for e in W if e["Type"] == "TriggerWin" and "prefabName" in e["Properties"]), None)
                if win:
                    break
        self.win = copy.deepcopy(win)
        wx, wy = win["Properties"]["position"]
        gw = ground_y(E, wx, below=wy) if win in E else None
        self.win_dy = wy - gw if gw is not None else 0.0
        g = [e for e in E if solid_ground(e) and e["Properties"].get("textureFill")]
        g.sort(key=lambda e: -len(e.get("Vertexes") or []))
        self.ground_props = {k: v for k, v in g[0]["Properties"].items() if k not in ("position", "name")}
        self.schema = ld.type_schema([L] + [corpus_level(f"1_{k}") for k in (1, 5, 10, 25)])


# ── surfaces ───────────────────────────────────────────────────────────────────────────────────
def catmull_open(ctrl, step=2.0):
    """Open uniform Catmull-Rom through ctrl [(x,y)], ends clamped; sampled about every `step` units."""
    pts = [ctrl[0]] + list(ctrl) + [ctrl[-1]]
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[i + 1], pts[i + 2]
        n = max(1, int(math.dist(p1, p2) / step))
        for j in range(n):
            t = j / n
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                                    + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3) for k in (0, 1)))
    out.append(tuple(ctrl[-1]))
    return out


def surface_y(fine, x):
    for (x0, y0), (x1, y1) in zip(fine, fine[1:]):
        if x0 <= x <= x1 and x1 > x0:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return None


def resample(fine, dx):
    """Points on the polyline `fine` at x = x0, x0+dx, …, x_end (end included)."""
    x0, x1 = fine[0][0], fine[-1][0]
    n = max(1, round((x1 - x0) / dx))
    return [(x0 + (x1 - x0) * i / n, surface_y(fine, x0 + (x1 - x0) * i / n)) for i in range(n + 1)]


# ── terrain encoders (each returns a list of EditorPhysicsObject entities for one ground run) ──
def _ground(tpl, verts, name, spline):
    P = copy.deepcopy(tpl.ground_props)
    P.update(position=[sum(v["x"] for v in verts) / len(verts), sum(v["y"] for v in verts) / len(verts)],
             name=name, spline=spline, tag=1, Static=True, physics=True, sensor=False)
    return {"Selected": False, "Type": "EditorPhysicsObject", "Properties": P, "Vertexes": verts}


def _v(x, y, seg=1):
    return {"x": float(x), "y": float(y), "segments": float(seg)}


def encode_polygon(tpl, fine, base, name, dx=4.0):
    """(a) ONE polygon per ground run: the surface sampled every dx + two bottom corners, CCW."""
    top = resample(fine, dx)
    verts = [_v(top[0][0], base), _v(top[-1][0], base)] + [_v(x, y) for x, y in reversed(top)]
    return [_ground(tpl, verts, name, False)]


def encode_spline(tpl, ctrl, base, name, seg=6):
    """(b) ONE spline object per ground run: the control points (curved edges, `seg` subdivisions) + two
    bottom corners joined by straight (segments=1) edges, CCW. The game smooths it (closed Catmull-Rom)."""
    # The game's spline is a CLOSED Catmull-Rom, so the bottom corners are the end segments' neighbours
    # and would bulge the surface ~6 units at each run end. A straight 2-unit stub at each end (an extra
    # point on the surface, segments=1) keeps the end tangents on the surface.
    stub = lambda p, q: (p[0] + (q[0] - p[0]) * 2 / math.dist(p, q), p[1] + (q[1] - p[1]) * 2 / math.dist(p, q))
    pts = [ctrl[0], stub(ctrl[0], ctrl[1])] + list(ctrl[1:-1]) + [stub(ctrl[-1], ctrl[-2]), ctrl[-1]]
    top = [_v(x, y, seg) for x, y in reversed(pts)]            # right -> left
    top[0]["segments"] = 1.0                                    # right end -> its stub: straight
    top[-2]["segments"] = 1.0                                   # left stub -> left end: straight
    top[-1]["segments"] = 1.0                                   # left end -> bottom-left: straight
    verts = [_v(ctrl[0][0], base), _v(ctrl[-1][0], base)] + top
    return [_ground(tpl, verts, name, True)]


def encode_slabs(tpl, fine, base, name, dx=24.0):
    """(c) one 4-vertex CCW slab per dx of surface (the old generator's encoding)."""
    top = resample(fine, dx)
    out = []
    for i, ((x0, y0), (x1, y1)) in enumerate(zip(top, top[1:])):
        out.append(_ground(tpl, [_v(x0, base), _v(x1, base), _v(x1, y1), _v(x0, y0)], f"{name}_{i}", False))
    return out


ENCODERS = ("polygon", "spline", "slabs")


def encode_run(tpl, kind, ctrl, fine, base, name):
    if kind == "polygon":
        return encode_polygon(tpl, fine, base, name)
    if kind == "spline":
        return encode_spline(tpl, ctrl, base, name)
    if kind == "slabs":
        return encode_slabs(tpl, fine, base, name)
    raise ValueError(kind)


# ── level assembly + export ──────────────────────────────────────────────────────────────────
def assemble(tpl, lid, ground, start, finish, times, extra=()):
    """ground: entity list; start/finish: (x, surface_y). Start/camera/finish are bare entities (no refs)."""
    E = list(ground) + list(extra)
    moto = copy.deepcopy(tpl.moto)
    moto["Properties"]["position"] = [float(start[0]), float(start[1] + tpl.moto_dy)]
    cam = copy.deepcopy(tpl.camera)
    cam["Properties"]["position"] = [float(start[0] + tpl.cam_off[0]), float(start[1] + tpl.moto_dy + tpl.cam_off[1])]
    win = copy.deepcopy(tpl.win)
    win["Properties"]["position"] = [float(finish[0]), float(finish[1] + tpl.win_dy)]
    win["Properties"]["rotation"] = 0.0
    E += [win, moto, cam]
    level = {"lid": lid, "type": tpl.level.get("type", 2), "times": [float(t) for t in times], "Entities": E}
    return ld.retype_level(level, None, tpl.schema)


def export(level, name):
    """Write <OUT>/<name>.level.json and encrypt it to <OUT>/<name>.dat with the local level key."""
    os.makedirs(OUT, exist_ok=True)
    js = os.path.join(OUT, f"{name}.level.json")
    with open(js, "w") as f:
        json.dump(ld.level_to_json(level), f)
    key = ld.level_key()
    if not key:
        raise SystemExit("no level key (tools/level-editor/levels/keys.json) — capture it first (docs/steps.md)")
    dat = os.path.join(OUT, f"{name}.dat")
    ld.export_dat(js, dat, key)
    return dat


# ── level-editor ref semantics (levelops.js via node — the SAME code the editor runs) ───────────
def _node():
    import glob
    import shutil
    cand = [os.environ.get("NODE"), shutil.which("node")] + sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/node")))
    for c in cand:
        if c and os.path.exists(c):
            return c
    raise SystemExit("node not found (set NODE=/path/to/node)")


def levelops(op, **payload):
    """Run one levelops.js op (copy / paste / carry / lint). Non-finite reals cross as tagged objects."""
    import subprocess
    cli = os.path.join(os.path.dirname(os.path.abspath(__file__)), "levelops_cli.js")
    req = json.dumps(ld.to_wire(dict(op=op, **payload)), allow_nan=False)
    r = subprocess.run([_node(), cli], input=req, capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise RuntimeError(f"levelops {op}: {r.stderr.strip()}")
    return ld.from_wire(json.loads(r.stdout))
