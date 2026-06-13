# Research — how Bike Rivals works (RE notes)

> *The technical truth we've reverse-engineered. Append findings here; cite how
> we know (disasm, unidbg, device test). Keys/offsets are methods, not game bytes.*

## The engine

Bike Rivals 1.5.2 is **cocos2d + Apportable** (Objective-C runtime over C++/Box2D,
ported from iOS to Android). `libgame.so` (ARM 32-bit) holds the game logic.
Implications:
- ObjC method dispatch via `objc_msgSend`; methods live in a method table of
  12-byte `{IMP, name_ptr, types_ptr}` entries (cluster near `0xcfd5d0`). This is
  how we find function addresses — match a selector-name string offset, read the
  IMP 4 bytes before it. **Validated** against known IMPs.
- Physics is **Box2D**: Y-UP world, units are "points," **CCW winding = solid**.
- Rendering is cocos2d: Y-UP, rotation **clockwise-positive**, content-scale for
  retina/HD assets.
- Frida **crashes** the 32-bit game on attach → we use unidbg + on-device native
  patches (code caves) instead.

## The cipher (SOLVED, full round-trip, device-verified)

- **Algorithm: Blowfish** (64-bit blocks). Core fns in `libgame.so`:
  `cipher_init@0x650090`, `cipher_setkey@0x650570` (key = raw `char*`),
  DECRYPT `cipher_process@0x65085c` (→ block `0x650ca8`),
  **ENCRYPT `cipher_process@0x6507d4`** (→ block `0x6508e4`).
- **Container**: `"<plaintextLen>\0"` + body. Decrypt reader
  `+[NSData DataWithContentsOfFile:Password:]@0x64ea98` = `atoi(header)` +
  `cipher_process(file+8, len-8)` + take `declLen` bytes (no nibble-swap here).
- **Pipeline**: `.dat → strip header → DECRYPT → [levels: GUNZIP] → binary plist`.
- **Encrypt** (mirror): `file = "<declLen>\0" + filler-to-offset-8 +
  cipher_process_ENCRYPT(gzip plaintext padded ×8)`.
- **Keys** (circumvention material — captured locally, NEVER committed):
  - **ALL levels share ONE key** (24-byte). Verified cross-world (1_2, 4_7 decrypt
    with the 1_1 key). The old "per-level key" belief was wrong.
  - Config files (`ProductList`/`Shop`/`GameConfig`/`ConditionInfo`) share a
    separate 50-byte key.
  - Captured on-device via `build/patch_keylog.py` (ARM stub → logcat tag RVKEY).

## The level format (decoded bplist)

`{ lid, type, times:[t0,t1,t2] (medal times), Entities:[…] }`. Each entity:
`{ Type, Properties{…}, Vertexes?[{x,y,segments}], Anchors?[…] }`.

Entity types and how they render/behave:
- **EditorPhysicsObject** — terrain. `Vertexes` are control points in WORLD
  coords; `spline:True` ⇒ Catmull-Rom (per-vertex `segments` subdivisions), else
  straight polygon. **Must be CCW for solid rideable ground.** Carries
  `textureFill` (tiled ground texture, e.g. `t1_rock_fill`), `textureEdge`,
  `fillTile`, `textureRot`, density/friction/restitution. The real ground is
  **many small polygons** (median 4 vtx, max ~35), not one big shape.
- **EditorSprite** — decoration. `position[x,y]`, `frame`, `scale`, `rotation`
  (cocos CW°), `anchorX/Y` (0.5), `z` (layer). Frame resolves into a per-theme
  atlas (see rendering).
- **Moto** — the player start/bike (prefab: `definition:Moto`, `isAlias:true`,
  carries speedLimit/forceScale/tilt/nitro…). Self-contained (no index refs).
- **TriggerWin** — the finish. A GROUP: `refobjectList` holds **entity INDICES**
  of its children (2× `Finish_Sprite` gate posts + a win `EditorTrigger`).
- **EditorTrigger / WaterTrigger** — triggers; WaterTrigger defines a water
  region (`width`×`height`, `shape`, at `position`).
- **Joints** (Revolute/Distance/Weld) — `Anchors[{x,y,object_id,object2_id}]`;
  build see-saws, rope bridges, moving parts.
- **ComposedSprite / ExplosiveBarrel / EditorPhysicsEntity** — prefab groups via
  `refobjectList` / `prefabName`.
- **EditorCamera** — follows `objectName:"heroTorso"` (the bike).

⚠ **Index-based references** (`refobjectList`, `mountedSprites`) point to entity
ARRAY INDICES. If you rebuild/reorder the entity list you MUST remap them.

## The render model (for WYSIWYG editing)

- **Texture atlases are NON-STANDARD WebP** (12-byte VP8X w/ 4-byte dims; lossless
  ALPH no stock libwebp decodes). Workaround: extract the standard VP8 (RGB) chunk
  → ffmpeg decode → chroma-key BLACK to alpha (transparent regions are pure black).
- **Theme-aware atlas resolution**: the same frame name exists in several
  per-theme atlases (`elements_default`=world 1; `elements_t2/t3/t4`=worlds 2–4;
  `elements_ts1/ts2`=shared). Resolve against the level's world (lid first number)
  or you draw the wrong world's art.
- **Sprite transform** (cocos Y-up CW → canvas Y-down): `translate(wx,wy);
  ctx.transform(k·cos, k·sin, k·sin, −k·cos)` with `k = viewScale·spriteScale/CS`,
  draw image V-flipped. ~half of frames are texture-rotated (packed sideways).
- **Content-scale** `CS ≈ 8.5` (texels → level units), calibrated by eye against
  the physics outline. NOT exactly uniform — composed/mounted sprites differ;
  faithful per-element sizing needs the game's own renderer rules.
- **Terrain fill**: clip each polygon, tile its `textureFill` (content-scaled,
  rotated by `textureRot`). This is what makes the green outline read as ground.

## Open questions (TODO research)

- **Level registration**: where are per-world level COUNTS + unlock gates stored?
  `WorldDefinition.plist` (plaintext) only has panel `visuals` (12 WorldPanelN) +
  `locks` (3 gate x-positions). Counts/unlock likely in `GameConfig.dat`
  (config key) or `libgame.so`. Needed for an additive World 5.
- **Mod-loader hook**: `-[CCFileUtils fullPathForFilename:]` redirect to an
  external `mods/` dir (game has `getExternalStoragePath`, `/sdcard`).
- **Why a minimal generated level hangs the tutorial** — even with CCW terrain.
  Suspect: the real ground is many small polygons; a single big spline may build
  an invalid physics body, OR the bike needs a specific spawn/ground setup. To
  isolate, test in a NON-tutorial slot (levels 4+). *(under investigation)*
- **Procgen technique** — see the difficulty-curve / solvability research (feeds
  `levelgen.py`).

## Sources / tools

unidbg (JDK17), capstone, Ghidra (Java scripts; PyGhidra broken), apktool,
uber-apk-signer, adb, ffmpeg/ImageMagick. See [ASSET-FORMATS.md](ASSET-FORMATS.md)
for the deep cipher/format dump and [steps.md](steps.md) for how to use the tools.
