# Procedural generation & World 5 — research, design, plan

> The durable record for the data-driven generator + World-5 work. Per the
> standing directives in `CLAUDE.md`, this survives context compaction — read it
> before touching `tools/level-editor/levelgen.py` or adding World 5. Update it as
> findings land. Runbook to reproduce the corpus: `docs/steps.md` →
> "Decode the full level corpus".

## 0. The standing directive (owner, verbatim intent)

- Analyze **all ~140 levels** to refine the generator AND learn how the game
  registers levels.
- Pull in **web research** + **roguelike** level-generation principles.
- Capture **every level's challenging aspect** (so generated levels are as varied
  and challenging as hand-made ones).
- **Terrain quality bar:** no "lazy straight lines", no over-steep slopes — must
  read as hand-authored.
- Put the enhanced generator on an **additive World 5** (never touch Halloween /
  Christmas — separate worlds).

## 1. Corpus analysis — 130 decoded levels (worlds 1–4)

Decoded all 130 story levels (universal level key) → JSON, analyzed
construction. Headline numbers:

### Terrain construction (why generated terrain looked "lazy")
- **Terrain = many small polygons.** median **39 polys/level** (range 10–167),
  median **5 vertices/poly** (p90 18).
- **Vertex spacing is FINE:** median **16 units** between consecutive vertices
  (p10 = 2u, p90 = 101u). Real surfaces are finely sampled → they read as smooth
  curves. ⇒ *Our bug:* the old generator stepped ~60–120u per point → long straight
  segments = "lazy straight lines". **Fix: sample the surface every ~20–35u.**
- **34% of terrain polys use `spline=True`** (Catmull-Rom smoothing); the rest are
  polygons but finely sampled. Smoothness comes from fine sampling first, spline
  second.
- **Slopes:** all-edge |dy/dx| median 0.41 (22°), p75 1.27 (52°), p90 3.21 (73°),
  p95 5.76 (80°) — BUT this includes the near-vertical *side/closing* edges of the
  small polygons, so it overstates the *rideable* grade. Takeaway: ambient rideable
  ground should be gentle (~20–30°); steep faces exist only as deliberate
  walls/ramps. ⇒ *Our bug:* MAX_SLOPE 1.05 (46°) applied to coarse straight segments
  felt "too steep". **Fix: gentler ambient cap (~0.5–0.7) + fine smoothing; reserve
  steeper grades for explicit ramp features.**
- **Per-world fill textures** (theme-correct): W1 `t1_rock_fill`, W2 `t2_earth`/
  `t2_concrete`, W3 `T3_Rock_Texture`, W4 `t4_maintexture`, shared `S1_Terrain`/
  `S2_Ground_Texture`.

### Obstacle taxonomy (what makes levels challenging) — entities across 130 levels
The challenge is overwhelmingly **physics-joint contraptions**, not static terrain:

| Entity | Total | per-level by world (avg) | what it is |
|---|---|---|---|
| `EditorPhysicsRevoluteJoint` | **1970** | W1 9.4 · **W2 29.1** · W3 10.3 · W4 4.3 | see-saws, swinging planks, rotating arms, wheels — THE core hazard |
| `EditorPhysicsWeldJoint` | 503 | W1 3.3 · W2 5.8 · W3 2.7 · W4 3.4 | rigid multi-body structures |
| `EditorPhysicsDistanceJoint` | 491 | W1 1.6 · W2 6.3 · W3 4.2 | rope/spring bridges |
| `EditorPhysicsPrismaticJoint` | 384 | — | sliding platforms |
| `WaterTrigger` | 289 | ~1.7–2.8 everywhere | water (buoyancy/drag) |
| `ExplosiveBarrel` | 159 | W1 2.4 · W2 1.4 · W3/4 0.5 | explosive obstacle (front-loaded in W1) |
| `Spikes` | 151 | — | instant-death hazard |
| `EditorPhysicsEntity` | 103 | ~0.6–1.2 | named prefab structures (`struct1`, …) |
| `Nitro` | 60 | — | boost pads |
| `TriggerNewGravity` | 44 | — | gravity flips (W3+) |
| `Geyser` | 33 | — | launch jets |
| `Drone` | 31 | — | moving hazard |
| `Checkpoint` | 27 | — | mid-level respawn |
| `ExplosiveTimer` | 12 | — | timed bomb |

Design reading: **W1** = gentle terrain + barrels + a few see-saws (teaching).
**W2** = see-saw heavy (contraption playground, 29/level!). **W3** = gravity/water
tricks. **W4** = fewer props, tighter/faster (precision). 

### Difficulty progression
- Gold (fastest medal) time by world: **W1 26s → W2 23.5s → W3 25s → W4 20s**.
  Shorter = tighter execution. W4 is the speed/precision wall.
- Obstacle *density* peaks at W2; W4 trades density for tight timing.

## 2. Web / roguelike research

(Folded in from the background research agent — see "Generator redesign".)
Key transferable principles being applied:
- **Smooth terrain:** layered/octave sine or value-noise heightfield + Catmull-Rom,
  fine sampling, slope-limited — never piecewise-linear coarse segments.
- **Pacing:** warm-up → ramp → climax → resolution; rest beats between hard sections
  (rhythm-based, Canabalt/Spelunky lineage).
- **Solvability by construction** + optional headless verify; projectile math for
  jumpable gaps.
- **Obstacle placement:** density curve, spacing rules, telegraphing, anchor hazards
  to terrain features (after a ramp / on a flat).
- **Roguelike:** seeded determinism, encounter budget, set-pieces vs filler,
  variety/non-repetition.

## 3. Generator redesign (from the data)

**Terrain (fixes "lazy / too steep"):**
1. Build a smooth surface height function `h(x)` over the track: base = sum of 2–3
   sine octaves (rolling hills) at gentle amplitude; sample every ~25u.
2. Overlay difficulty-placed FEATURES on `h(x)`: ramps (steep allowed), jumpable
   gaps (after a ramp, projectile-sized), step-ups, whoops — from the difficulty
   envelope.
3. Clamp ambient grade gently (~0.5–0.7 ≈ 27–35°); allow steeper only inside an
   explicit ramp feature.
4. Emit a **fine quad slab per ~25u step** (CCW, vertical walls to a baseline) — the
   real game's many-small-polys structure, but finely sampled so the top reads smooth.

**Obstacles (match the hand-made feel), cloned from real prefabs:**
- ✅ decorations (trees/rocks/foreground) + ExplosiveBarrel (done; barrels need a
  device-visibility fix — see open questions).
- ⏳ **see-saws** (`EditorPhysicsRevoluteJoint` + plank body + `Anchors`) — highest
  value (the #1 hazard). Clone plank+joint, reposition, remap anchor/body refs.
- ⏳ water pools (`WaterTrigger`), nitro pads (`Nitro`), spikes (`Spikes`).
- Density/mix per the per-world table above; difficulty-gated.

## 4. World 5 registration (RE — for the additive world)

Goal: add a 5th world hosting generated levels without disturbing existing worlds
or Halloween/Christmas.

### What we know (confirmed)
- **`WorldDefinition.plist`** lists **12 visual panels** (`WorldPanel1–12.ccbi` +
  `LevelSelectBK1–12.png`) and 3 `locks`. Worlds 1–4 have levels; 5–12 are visual
  slots. Halloween/Christmas have their OWN definitions (1 panel each) — separate.
- **`WorldPanel5.ccbi` … `WorldPanel12.ccbi` all SHIP in the assets** — the
  level-select panels for the empty worlds already exist. World 5's UI is present.
- Level files load by the path format **`%d/%d_%d.dat`** (world / world_level) — also
  `%d_%d/%d_%d_%d.dat`. Our levels are `<w>_<l>.dat`; lid must match (`5_1`, `5_2`, …).
- **Unlock gate** `isWorldUnlocked:` @0x6b0df0 + `isUniverseUnlocked:` @0x6a5094 are
  already force-YES in `apply_patches.py`.
- Per-world setup goes through **`createLevelInfo:universe:levels:starting:`**
  @0x6b643c — each world is registered with a `levels:` COUNT and `starting:` global
  offset. The CALLER of this holds the per-world counts (corpus shows W1=30, W2=40,
  W3=45, W4=15). **Open: find that caller (xref to 0x6b643c) — that's where to add a
  world-5 entry, or patch a count.**
- `getTotalLevels:` @0x6a6190 computes a total per **universe** (arg: 0/1/2 = game
  mode, NOT world) via msgSend×multiplier (×10, ×15) — universe-level, not the
  per-world count we need. The per-world count lives at the createLevelInfo caller.

### Open questions / risks
- **Is per-world level count a fixed table or derived from which `<w>_<l>.dat` exist?**
  Fastest answer = EMPIRICAL: drop in `5_1.dat` (+`5_2`…), force unlock, navigate to
  world 5 on-device, watch logcat. If it shows the level(s), counts may be
  file-driven or the panel reads them directly; if not, trace the createLevelInfo
  caller and add/patch the world-5 count.
- **Theme/config:** worlds 1–4 use `GameConfig_T1–T4.dat` (50-byte config key). World 5
  may need a `GameConfig_T5.dat` (mint by cloning/re-encrypting T1) or it falls back.
  A missing theme config is a likely crash source on first world-5 load — test for it.
- **Level-select navigation** to world 5 is a UI action (user taps) — verify on device.

### Plan
1. Generate `5_1.dat … 5_N.dat` (enhanced generator, lid `5_M`, rising difficulty).
2. Drop into `assets/unpack/`, build, install, navigate to world 5, read logcat.
3. If it loads → done (file-driven). If not → trace createLevelInfo caller, patch the
   world-5 count (and/or mint `GameConfig_T5.dat`), retest.

## 5. Open questions / bugs
- **Barrels not visible on device** (generated W1L4 reported 2 barrels, none seen).
  Check: dynamic-body collision filter vs generated slabs, spawn embedding/ejection,
  or render layer. (Investigating.)
- Exact rideable-surface slope cap (measure the *top profile* only, not all edges).
- See-saw anchor/body reference model (how `Anchors` bind the joint to two bodies).
