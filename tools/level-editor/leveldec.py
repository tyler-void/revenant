#!/usr/bin/env python3
"""
Revenant Level Editor — decode/encode core.

A Bike Rivals level ships as `<world>_<level>.dat`:

    .dat → strip "<len>\\0" → DECRYPT (per-level key) → GUNZIP → binary plist

The DECRYPT step uses the game's own cipher and is done by the unidbg oracle
(tools/unidbg/.../LevelCodec.java, key via BR_KEY) — see decrypt_dat(). Once
decrypted, this module owns the GUNZIP ↔ bplist ↔ JSON conversions, which need
no native code.

The decoded plist is a 2D scene graph:
    { lid, type, times:[gold,silver,bronze], Entities:[ {Type, Properties, Vertexes?, Anchors?}, ... ] }

EditorPhysicsObject entities carry `Vertexes:[{x,y,segments}]` authored in world
space — `spline:True` ⇒ a Catmull-Rom curve (terrain), else a straight polygon.
Sprites / Moto / triggers position themselves via Properties.position = [x, y].

⚠️  Decrypted level data is circumvention material — it stays LOCAL (see
docs/PRESERVATION-PLAYBOOK.md / LEGAL.md). The levels/ cache is gitignored.
"""
import os, sys, gzip, json, plistlib, base64, subprocess, struct, glob, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
UNIDBG = os.path.join(ROOT, "tools", "unidbg")
CACHE = os.path.join(HERE, "levels")           # gitignored local cache
UNPACK = os.environ.get("BR_UNPACK", os.path.join(ROOT, "build", "work", "assets", "unpack"))
GZIP_MAGIC = b"\x1f\x8b\x08"


# ── texture atlases (cocos2d format-3 sprite sheets) ─────────────────────────
# The game's sprites reference a `frame` name (e.g. "metal5.png") packed into a
# .plist+texture atlas. We parse the atlas plists so the editor can draw the
# real art. Textures are WebP-in-.png (browsers decode WebP fine). Atlas art is
# © game data — served only locally, never committed.
_ATLAS_IDX = None

def _nums(s):
    return [float(x) for x in re.findall(r"-?\d+\.?\d*", s or "")]

def atlas_index():
    """frameName -> {textureFileName: frame-record}. Cached.

    The SAME frame name (bor7.png, ter5.png, …) exists in several per-theme atlases
    with DIFFERENT art (elements_default = world 1 desert, elements_t2/t3/t4 =
    worlds 2/3/4, elements_ts1/ts2 = shared specials). So we keep every atlas a
    frame appears in and pick the right theme at resolve time (see _resolve_frame).
    """
    global _ATLAS_IDX
    if _ATLAS_IDX is not None:
        return _ATLAS_IDX
    idx = {}
    for p in glob.glob(os.path.join(UNPACK, "*.plist")):
        try:
            d = plistlib.load(open(p, "rb"))
        except Exception:
            continue
        fr = d.get("frames") if isinstance(d, dict) else None
        if not isinstance(fr, dict):
            continue
        tex = os.path.basename(p)[:-6] + ".png"   # Foo.plist -> Foo.png
        for name, rec in fr.items():
            idx.setdefault(name, {})[tex] = rec
    _ATLAS_IDX = idx
    return idx

def _world_of(lid):
    try:
        return int(str(lid).split("_")[0])
    except Exception:
        return 1

def _atlas_priority(world):
    # world 1's theme atlas is elements_default (there is no elements_t1);
    # worlds 2-4 use elements_t{N}; ts1/ts2 are shared specials (lower priority).
    theme = "elements_default.png" if world == 1 else "elements_t%d.png" % world
    return [theme, "elements_default.png", "elements_ts1.png", "elements_ts2.png"]

def _resolve_frame(name, cands, world):
    """Pick the theme-correct atlas for a frame from {atlas: rec}."""
    for a in _atlas_priority(world):
        if a in cands:
            return a, cands[a]
    # fallback: a per-object sheet (Cannon/Checkpoint/…) over a wrong-theme elements_
    non_elem = [a for a in cands if not a.startswith("elements_")]
    a = non_elem[0] if non_elem else next(iter(cands))
    return a, cands[a]

def _frame_geom(rec):
    """Normalise a cocos2d format-3 (or older) frame record → pixel geometry."""
    tr = _nums(rec.get("textureRect") or rec.get("frame"))      # x, y, w, h
    rot = bool(rec.get("textureRotated") or rec.get("rotated"))
    off = _nums(rec.get("spriteOffset") or rec.get("offset") or "{0,0}")
    src = _nums(rec.get("spriteSourceSize") or rec.get("sourceSize") or "")
    return {"rect": tr[:4], "rotated": rot, "offset": off[:2], "source": src[:2]}

def atlas_meta_for_level(level):
    """Resolve every sprite `frame` to its THEME-correct atlas + pixel geometry."""
    idx = atlas_index()
    world = _world_of(level.get("lid", "1"))
    frames, atlases = {}, set()
    for e in level.get("Entities", []):
        f = (e.get("Properties") or {}).get("frame")
        if f and f in idx and f not in frames:
            tex, rec = _resolve_frame(f, idx[f], world)
            g = _frame_geom(rec); g["atlas"] = tex
            frames[f] = g; atlases.add(tex)
    return {"frames": frames, "atlases": sorted(atlases), "world": world}

def atlas_texture_path(name):
    """Safe-resolve an atlas texture name to a path under UNPACK (no traversal)."""
    name = os.path.basename(name)
    p = os.path.join(UNPACK, name)
    return p if os.path.exists(p) else None

# The game's atlas textures are a NON-STANDARD WebP (12-byte VP8X w/ 4-byte dims;
# lossless ALPH that no stock libwebp — ffmpeg/IM/Pillow — will decode). But the
# VP8 (RGB) chunk is standard, and transparent regions are encoded as pure black.
# So: extract the VP8 chunk → decode RGB via ffmpeg → chroma-key black to alpha →
# a normal RGBA PNG the browser can draw. Cached locally (© art → gitignored).
TEXCACHE = os.path.join(HERE, "textures")

def _vp8_only_webp(raw):
    off = 12
    while off + 8 <= len(raw):
        cc = raw[off:off + 4]; sz = struct.unpack("<I", raw[off + 4:off + 8])[0]
        if cc in (b"VP8 ", b"VP8L"):
            body = raw[off + 8:off + 8 + sz]
            chunk = cc + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) & 1 else b"")
            return b"RIFF" + struct.pack("<I", len(b"WEBP" + chunk)) + b"WEBP" + chunk
        off += 8 + sz + (sz & 1)
    return None

def transcode_atlas(name):
    """Game WebP atlas → browser-drawable RGBA PNG (cached). Returns path or None."""
    src = atlas_texture_path(name)
    if not src:
        return None
    os.makedirs(TEXCACHE, exist_ok=True)
    out = os.path.join(TEXCACHE, os.path.basename(name))   # keep the .png name
    if os.path.exists(out) and os.path.getsize(out) > 0 and os.path.getmtime(out) >= os.path.getmtime(src):
        return out
    vp8 = _vp8_only_webp(open(src, "rb").read())
    if not vp8:
        return None
    tmp = os.path.join("/tmp", "rv_vp8_" + os.path.basename(name) + ".webp")
    with open(tmp, "wb") as f:
        f.write(vp8)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", tmp,
                    "-vf", "colorkey=0x000000:0.10:0.02", out],
                   capture_output=True)
    return out if os.path.exists(out) and os.path.getsize(out) > 0 else None

# ── plist ↔ JSON (display) ──────────────────────────────────────────────────
# JSON can't tell int 27 from real 27.0, and the game's plist parser cares. So
# the editor's source of truth stays the *plist dict* (native Python types); the
# JSON we hand the browser is a display projection. On save we re-apply edits to
# the original dict (preserving types) — never round-trip the whole thing through
# JSON. _to_jsonable only has to survive the trip out to the canvas.

def _to_jsonable(o):
    if isinstance(o, bytes):
        return {"__data__": base64.b64encode(o).decode()}
    if isinstance(o, dict):
        return {k: _to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_to_jsonable(v) for v in o]
    return o


def _from_jsonable(o):
    if isinstance(o, dict):
        if set(o.keys()) == {"__data__"}:
            return base64.b64decode(o["__data__"])
        return {k: _from_jsonable(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_from_jsonable(v) for v in o]
    return o


def raw_to_level(raw):
    """Decrypted bytes (gzip stream or bare bplist) → plist dict (native types)."""
    if raw[:3] == GZIP_MAGIC:
        raw = gzip.decompress(raw)
    if raw[:8] != b"bplist00" and raw[:5] != b"<?xml":
        raise ValueError("not a plist after gunzip (head=%r)" % raw[:8])
    return plistlib.loads(raw)


def level_to_raw(level, *, gz=True):
    """plist dict → binary plist (+ gzip) — the pre-encrypt payload."""
    body = plistlib.dumps(level, fmt=plistlib.FMT_BINARY)
    if not gz:
        return body
    # match the game: gzip member, mtime=0 so output is deterministic
    import io
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as g:
        g.write(body)
    return buf.getvalue()


def level_to_json(level):
    return _to_jsonable(level)


def json_to_level(js):
    return _from_jsonable(js)


# ── decryption oracle bridge (unidbg) ────────────────────────────────────────
def _run_codec(mode, key_hex, in_path, out_path):
    """Invoke the unidbg LevelCodec oracle in the given BR_MODE.

    Needs JAVA_HOME (JDK17) and the prebuilt unidbg module. The key is supplied
    via BR_KEY and is NEVER persisted by this tool.
    """
    env = dict(os.environ)
    env.setdefault("JAVA_HOME", "/usr/lib/jvm/java-17-openjdk")
    env["BR_MODE"] = mode
    env["BR_KEY"] = key_hex
    env["BR_IN"] = os.path.abspath(in_path)
    env["BR_OUT"] = os.path.abspath(out_path)
    cmd = ["mvn", "-q", "-Dexec.mainClass=com.resurrect.LevelCodec", "compile", "exec:java"]
    # stdin=DEVNULL: on an emulation exception unidbg drops into an interactive
    # debugger that reads stdin — an open pipe would block forever. EOF makes it
    # bail (the codec result is already written by then).
    r = subprocess.run(cmd, cwd=UNIDBG, env=env, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=300)
    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise RuntimeError("oracle (%s) produced no output\n%s\n%s"
                           % (mode, r.stdout[-2000:], r.stderr[-1000:]))
    return out_path


def decrypt_dat(dat_path, key_hex, out_path=None):
    """Decrypt a .dat → raw bytes (gzip stream) via the oracle."""
    if out_path is None:
        out_path = os.path.join("/tmp", "rv_" + os.path.basename(dat_path) + ".raw")
    return _run_codec("decrypt", key_hex, dat_path, out_path)


def encrypt_to_dat(gz_payload_path, key_hex, out_path):
    """Encrypt a gzip(bplist) payload → device-loadable .dat via the oracle.

    Uses cipher_process_ENCRYPT (0x6507d4) + the "<len>\\0"+filler+ciphered framing.
    Proven to round-trip: the game's own decryptor reads it back byte-identical.
    """
    return _run_codec("encrypt", key_hex, gz_payload_path, out_path)


def keyfile_path():
    """Local, gitignored map of level → captured key (hex)."""
    return os.path.join(CACHE, "keys.json")


def load_keys():
    p = keyfile_path()
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}


# ── high-level: import a .dat into the local JSON cache ───────────────────────
def cache_path(lid):
    return os.path.join(CACHE, "%s.level.json" % lid)


def import_dat(lid, dat_path, key_hex):
    os.makedirs(CACHE, exist_ok=True)
    raw_path = decrypt_dat(dat_path, key_hex)
    with open(raw_path, "rb") as f:
        level = raw_to_level(f.read())
    out = cache_path(lid)
    with open(out, "w") as f:
        json.dump(level_to_json(level), f)
    return out, level


def export_dat(level_json_path, out_dat, key_hex):
    """Edited level JSON → bplist → gzip → encrypt → device-loadable .dat."""
    with open(level_json_path) as f:
        level = json_to_level(json.load(f))
    gz = level_to_raw(level, gz=True)                 # bplist + gzip (the cipher plaintext)
    tmp = os.path.join("/tmp", "rv_enc_payload.gz")
    with open(tmp, "wb") as f:
        f.write(gz)
    encrypt_to_dat(tmp, key_hex, out_dat)
    return out_dat, level


def _cli():
    import argparse
    ap = argparse.ArgumentParser(description="Revenant level decode/encode core")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("decode-raw", help="decrypted raw/gzip file → level JSON")
    d.add_argument("raw"); d.add_argument("out", nargs="?")
    i = sub.add_parser("import", help="decrypt a .dat (BR_KEY) → cache JSON")
    i.add_argument("lid"); i.add_argument("dat"); i.add_argument("key_hex")
    e = sub.add_parser("export", help="edited level JSON → encrypted .dat")
    e.add_argument("json"); e.add_argument("out_dat"); e.add_argument("key_hex")
    a = ap.parse_args()
    if a.cmd == "decode-raw":
        with open(a.raw, "rb") as f:
            level = raw_to_level(f.read())
        js = json.dumps(level_to_json(level))
        if a.out:
            open(a.out, "w").write(js)
            n = len(level.get("Entities", []))
            print("wrote %s  (lid=%s, %d entities)" % (a.out, level.get("lid"), n))
        else:
            print(js)
    elif a.cmd == "import":
        out, level = import_dat(a.lid, a.dat, a.key_hex)
        print("cached %s  (%d entities)" % (out, len(level.get("Entities", []))))
    elif a.cmd == "export":
        out, level = export_dat(a.json, a.out_dat, a.key_hex)
        print("wrote %s  (lid=%s, %d entities)" % (out, level.get("lid"), len(level.get("Entities", []))))


if __name__ == "__main__":
    _cli()
