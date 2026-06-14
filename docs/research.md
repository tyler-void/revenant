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

## Level corpus analysis (all 130 decoded levels)

Decoded every level with the universal key (`LevelCodec` batch mode) and measured:
- **Terrain = MANY small polygons**: median **46** EditorPhysicsObjects/level
  (range 14–170), median **4 vertices** each, 72% ≤8 vtx. **~67% CCW.** The
  ground is a strip of small CCW quad/slab pieces — NOT one big shape. (This is
  why a single giant generated polygon fails: invalid Box2D body → bike falls
  through. Fix: generate the ground as many small CCW quad slabs, like the game.)
- **Obstacle inventory (avg/level by world)**:
  | world | levels | barrels | revolute-joints (see-saws/wheels) | distance-joints (rope) | water | triggers |
  |---|---|---|---|---|---|---|
  | 1 | 30 | 2 | 9 | 2 | 2 | 21 |
  | 2 | 40 | 1 | **29** | 6 | 3 | 21 |
  | 3 | 45 | 0 | 10 | 4 | 2 | 19 |
  | 4 | 15 | 0 | 4 | 1 | 1 | 28 |
  Barrels are an early-world hazard; **world 2 is see-saw/joint-heavy**; water is
  everywhere (~2–3/level); ~20 triggers/level (camera/slow-mo/events).
- **Difficulty curve**: length med ~2280 (w1) → ~1850 (w4) — later worlds are
  *shorter but tighter*; **gold medal time 26s (w1) → 20s (w4)**. So escalate by
  raising hazard density + tightening medal times, not by lengthening.

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

## Procgen approach (for levelgen.py — research-grounded)

The target generator (rewrite of `levelgen.py`) follows a **two-tier rhythm/chunk-
over-spine** model — "playable by construction," not by luck:

1. **Abstract action plan first** (no geometry): a sequence of intended verbs
   (ride / hop / big-jump / flip / balance / brake) with a **difficulty budget**.
   Generating the plan first guarantees each obstacle fits a verb the player can
   perform (Launchpad / Smith & Whitehead).
2. **Realize each beat as a chunk** — a pre-authored obstacle template (ramp, gap,
   see-saw, barrel cluster) at a difficulty tier — and **stitch chunks over a
   smooth terrain spine**. Pure noise is ONLY filler/rest terrain (it must never
   create an obstacle by accident).

- **Terrain spine:** sum-of-sines base (1–3 terms; smooth, controllable — Tiny
  Wings / Hill Climb Racing) + small fBm overlay (2–4 octaves, lacunarity 2,
  persistence 0.5, ≤25% amplitude). **Never midpoint-displacement** (spikes).
  Hard-clamp grade to **≤40–45° up / ≤55–60° down** before splining; re-smooth.
  Tessellate to small CCW quad slabs / Box2D chain.
- **Difficulty envelope** over progress `t`: warm-up 0–15% (flat, teach), ramp
  15–80% (budget rises, sawtooth dips = recovery zones), climax 80–95% (hardest
  set-piece), resolution 95–100% (easy run to finish — never kill at the line).
  Escalate by hazard *severity + density + tighter medal times*, NOT by length
  (matches the corpus: w1→w4 shorter but tighter).
- **Solvability = construct + verify.** Construct: size every gap to projectile
  range `R = v²·sin(2α)/g` with a 0.7 safety factor; landings flat (≤25°) with a
  recovery run. Verify (generate-and-test): a fast **kinematic reachability**
  filter, then optionally a **headless Box2D agent sim** (full-throttle + scripted
  jumps) as the gold-standard solvable/unsolvable gate; downgrade the failing
  chunk and retry (cap ~20, else fallback seed). Everything seeded → reproducible.
- **Roguelike discipline:** weighted chunk tables gated by budget; a guaranteed
  rideable *critical path* placed + certified FIRST, then non-blocking decoration
  (rings/coins/props) after (Spelunky); recently-used-chunk cooldown for variety;
  introduce each hazard type in isolation before combining.
- **Chunk contract** (for stitching): each template declares entry/exit height +
  slope, length, cost, tier, tags, min run-up, internal randomizable slots; join
  chunks with a C¹-continuous Catmull-Rom connector (match tangents, spread slope
  change over the connector — no seam spikes).
- Sources: Launchpad (Smith/Whitehead/Treanor/Mateas, IEEE TCIAIG 2011); "Sure
  Footing" budget/chunk model (Game Developer); Spelunky critical-path (PCG wiki);
  Sturgeon reachability-as-constraint (arXiv); fBm terrain (Book of Shaders ch.13);
  Tiny Wings / Hill Climb sine terrain; fairness = transparency + telegraphing +
  no surprise deaths (SuperJump).

## Open questions (TODO research)

- **World structure (confirmed):** the MAIN world map = `WorldDefinition.plist`
  (plaintext: 12 `visuals` panels `WorldPanel1-12` + 3 `locks`). **Only worlds 1–4
  have level files** (`<w>_<l>.dat`: 30/40/45/15 = 130); **World 5 = the empty "?"
  slot** (`WorldPanel5`/`LevelSelectBK5` exist, no `5_*.dat`). **Halloween &
  Christmas are SEPARATE worlds** — their own `HalloweenWorldDefinition.plist` /
  `ChristmasWorldDefinition.plist` (1 panel each), `h_*`/`c_*` level naming,
  reached via `gotoHalloween/ChristmasWorld` menu items. So `5_*.dat` is safe —
  it never touches the seasonal worlds.
- **Level registration (open):** per-world level COUNTS + unlock aren't in
  `WorldDefinition.plist`. Per-theme `GameConfig_T1..T4.dat` (config key) are the
  prime suspects (no `GameConfig_T5` exists → World 5 unconfigured). Decode them
  to learn how to register/enable an additive World 5.
- **Mod-loader hook**: `-[CCFileUtils fullPathForFilename:]` redirect to an
  external `mods/` dir (game has `getExternalStoragePath`, `/sdcard`).
- **Why a minimal generated level hung** — ANSWERED by the corpus analysis: the
  real ground is many small CCW polygons; our single 43-vtx polygon built an
  invalid Box2D body → bike fell through → tutorial hung. Generator now emits a
  strip of small CCW quad slabs. Also: ALWAYS test in a non-tutorial slot (4+).
- **Procgen technique** — see the difficulty-curve / solvability research (feeds
  `levelgen.py`).

## Ghost system — record + render (2026-06-15, libgame.so RE)

How the racing ghost works, and why it rendered garbled when `gameTime_` was corrupted
(the mod-menu step-hook bug — see [modmenu.md](modmenu.md)).

- **Record** (during a run, while `ghostRecording_`): a `GhostRecorder`
  (`createGhostRecorder`@0x6253c4 → `ghostRecorder_`, `getGhostRecorder`@0x6e2818).
  `recordGhostSprites:`@0x61b5f0 samples the rider's part transforms over time into
  `ghostData_` — a **time-indexed keyframe stream** (`setGhostData:`/`getExtraGhostData:`).
  Cap: **"Ghosts over 60 seconds are not available"** (60s max). Saved per level as
  `g_<w>_<l>.dat` (the ghost save files).
- **Load**: `GhostFromFile:`@0x616eb0 / `GhostFromData:`@0x616e1c → a `GhostEntity`/
  `SpriteGhost` (`ghostEntity_`, `ghostSprite_`).
- **Render / playback (per frame)**: **`updateWithRecordedGhost:`@0x61a20c** looks up the
  recorded keyframe at the **current run time = `gameTime_`** (the same clock the on-screen
  timer uses), interpolates, and sets the ghost sprite transform → the ghost replays in sync
  with the live run. `clearOldGhost`@0x61a640 tears it down on restart.
- **Multi-ghost (4 slots)**: `ghost1_..4_` / `ghostNode1_..4_` / `clickGhost1_..4_` /
  `setGhostSprites:`@0x66a54c / `MiniGhosts.mm`. The owner's "several ghosts at once" is
  feasible: load N ghost files → N `GhostEntity`s → drive each via `updateWithRecordedGhost:`.
- **Why a bad `gameTime_` garbles it (confirmed):** playback is **time-indexed by `gameTime_`**.
  The mod-menu step-hook bug corrupted `gameTime_` (froze/crawled), so the keyframe lookup was
  wrong/stuck → the ghost drew at the wrong recorded frame (scrambled body, wrong route) AND
  finish times came out sub-1s. **One root cause (`gameTime_`), two symptoms (run timer + ghost).**
  Fixed by the idle fast-path; the ghost only re-garbles while a step-hook feature (gravity/
  bike-specs) is engaged (re-skews `gameTime_`). Speed HUD + dismount don't touch the step → ghost OK.

## Sources / tools

unidbg (JDK17), capstone, Ghidra (Java scripts; PyGhidra broken), apktool,
uber-apk-signer, adb, ffmpeg/ImageMagick. See [ASSET-FORMATS.md](ASSET-FORMATS.md)
for the deep cipher/format dump and [steps.md](steps.md) for how to use the tools.
