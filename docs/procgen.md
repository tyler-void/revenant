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

## 2. Web / roguelike research (synthesized, with sources)

Researched 2D-trials/platformer + roguelike procgen. The recommended pipeline and
the principles we're applying:

### Terrain (the "lazy/harsh" fix), in priority order
1. **fBm heightfield** — sum of octaves of 1-D noise: each octave ×2 frequency
   (lacunarity≈2), ×0.5 amplitude (persistence≈0.5). High-freq detail carries LOW
   amplitude → rolling hills, not jagged noise. 3–4 octaves + persistence 0.5 = the
   gentle end we want. `base_freq` sets hill wavelength. (Our sine-octave `base()` is
   the same idea; could swap to true value-noise + warp for less regularity.)
   [aparis69 noise-for-terrains], [arpit 1D terrain], [GameGeniusLab Perlin].
2. **Neighborhood-average smoothing pass** (radius≈2, 1–3 passes) to kill residual
   spikes — cheap. [Codementor 2D-terrain-smoothing]. *(queued — not yet in our gen)*
3. **Slope clamp AND curvature clamp.** Clamping only slope then snapping flat feels
   harsh; ALSO clamp the *change in slope* between consecutive segments (2nd
   derivative), then average once more. This is what separates "rolling" from
   "kinked". *(we clamp slope; curvature clamp queued — high value)*
4. **Midpoint displacement** is an alternative whose single roughness/H exponent is a
   clean smooth↔jagged dial. [Steve Losh], [diamond-square].
5. **Centripetal Catmull-Rom (α=0.5), NOT uniform** — uniform CR overshoots and can
   form cusps/loops when control points are close/sharp → spline dives below ground or
   spikes into a wall. Centripetal provably cannot. Matters only if we move terrain to
   `spline=True` control points (we emit `spline=False` quad slabs today).
   [Centripetal Catmull-Rom (wiki)], [splines.readthedocs properties].

### Pacing — the rhythm-group model
Design the *experience* first (a sequence of "beats" = jump/ramp/barrel/see-saw and
"rests" = flats), THEN synthesize geometry to fit it. Macro arc warm-up→ramp→climax→
run-out, with an intensity curve = upward envelope × local sawtooth so there are
**rest valleys between hard sections** (constant pressure exhausts players). Drive
every parameter (roughness, gap width, ramp angle, obstacle density) off one
`intensity(progress)`. [Smith LaunchPad TCIAIG-2011], [Compton&Mateas AIIDE-2006],
[orcunnisli endless-runner curves]. *(we have an envelope; rest-valley sawtooth queued)*

### Solvability / fairness
Construction-based guarantee (only connect a feature reachable from the previous one)
+ projectile math for gaps: `range = v²·sin(2θ)/g`; validate at MIN realistic entry
speed with a 0.7× safety margin (comfortably clearable, not pixel-perfect). Headless
physics sim with an auto-driver as a reject-bad-seeds fallback. [kode80],
[GameDev.net platformer PCG], [GameDevMath projectile].

### Obstacle placement
Place relative to terrain FEATURES, not random x (barrel in a valley / just past a
landing; see-saw on a flat shelf). Density ∝ intensity. **No two hazards adjacent**
(classic impassable bug). **Telegraph**: a hazard needs visible run-up ∝ approach
speed (never behind a blind crest). **Introduce-then-test**: a new hazard type first
appears in a safe spot, later in a punishing one. [Pixelfield], [Wayline].

### Roguelike lessons
Set-pieces vs filler (build+validate skeleton, THEN drop authored prefabs —
`ComposedSprite`/`EditorPhysicsEntity` are our set-piece primitive); variety via a
template SET + down-weight recently-used; **encounter budget** (total challenge budget
scaled by difficulty, spent across segments); seeded determinism (have it);
reject-and-regenerate any track whose measured metrics fall outside target ranges.
[Cogmind procedural-layouts], [BlackShellMedia six-principles].

Sources (full URLs): aparis69.github.io noise-for-terrains · arpit.substack.com 1D
terrain · stevelosh.com midpoint-displacement · en.wikipedia.org
Centripetal_Catmull–Rom_spline · codementor.io 2D-terrain-smoothing · users.soe.ucsc.edu
Smith LaunchPad + Compton&Mateas · orcunnisli.com endless-runner-difficulty · kode80.com
level-generation · gamedev.net platformer-PCG · gamedevmath.com projectile-motion ·
pixelfield.co.uk best-practices · wayline.io game-feel · gridsagegames.com
procedural-layouts · blackshellmedia.com six-principles.

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
- ✅ decorations (trees/rocks/foreground) + ExplosiveBarrel (barrels spread + sprite
  z=6 above terrain fill).
- ✅ **Nitro + Spikes** — SIMPLE single-prefab entities (`{position, prefabName,
  definition, ...}`, no joints/refs). World-1 prefab names: `Nitro`, `Spikes`.
  `_scan_prefab_obstacles` pulls templates from the decoded cache (1_1 lacks them, so
  it scans other imported levels; **import a rich world-1 level like `1_25`** to enable
  them — `1_25` has 13 barrels / 17 joints / 9 water / nitro / spikes). Placement:
  nitro frequent on flats (helpful), spikes sparse + telegraphed (lethal). Graceful
  fallback to none if no cache source.
- ⏳ **see-saws = ARTICULATED CHAINS, not simple planks.** In the corpus a
  `RevoluteJoint` links a chain of small ~9×8 dynamic boxes (object_id/object2_id =
  entity indices of the two linked bodies; o2=None → linked to world). A faithful
  see-saw/hinged-bridge = clone the whole body-chain + all joints + remap every index.
  Complex + fragile + needs DEVICE physics testing — deferred until there's a test home.
- ⏳ water pools (`WaterTrigger`) — simple trigger but needs a terrain BASIN to sit in.
- Density/mix per the per-world table above; difficulty-gated.
- ⚠️ **All obstacles need a device test home** (World 5 or a chosen slot) before
  fairness/feel can be validated — currently blocked on the World-5 gate.

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

### EMPIRICAL RESULT (tested on device) — drop-in does NOT work
Installed `5_1/5_2/5_3.dat` + cloned `GameConfig_T5.dat`, force-unlocked. On device
**World 5 shows "COMING SOON" with no level selector.** So per-world availability is
NOT file-driven — it's gated in native code.

### The gate (located)
- `WorldPanelComingSoon.ccbi` is shown instead of a real panel; the decision is a
  `_comingSoon` ivar on `WorldPanel`, set at panel CREATION (in `LevelSelectionMenu.mm`
  / `WorldPanel.m`), NOT via a setter (direct ivar write).
- `getWorldPanelForIndex:` @0x56bacc is only an array lookup over `_worldPanels`.
- `getFirstLockedWorld` @0x6b0ed0 + `getTotalLevels:` @0x6a6190 drive availability via
  chained `objc_msgSend` — the SAME blind-objc-dispatch wall that left the bike-select
  gate uncracked (see docs/BIKE-UNLOCK-STATUS.md / apply_patches.py note). Static
  patching can't easily reach it.

### Honest assessment + options
World 5 is a MULTI-gate native RE (comingSoon flag + availability count + level count +
theme + nav), in hard objc-dispatch territory. Three ways forward:
1. **unidbg-trace it** — we now have a working ARM exec env (the LevelCodec oracle) the
   bike-gate work lacked. Stand up the LevelSelectionMenu/WorldPanel objects in unidbg,
   call the creation path, observe where `_comingSoon` is set and on what condition,
   then patch that condition. Highest effort, highest fidelity.
2. **Mod-loader (Phase 4 roadmap)** — redirect asset loads to an external `mods/` folder
   via `CCFileUtils fullPathForFilename:`; ship generated levels as REPLACEMENTS for
   existing slots without touching world registration. Sidesteps the comingSoon gate
   entirely; the cleanest path to "custom levels load".
3. **Designate a late campaign slot** as the custom/generated slot (e.g. last level of
   W4) — pragmatic, no new world, but replaces one hand-made level.

Recommendation: pursue (2) the mod-loader OR (3) a chosen slot for shipping generated
levels now; treat (1) as a later deep-RE project. World 5 as a *visible new world* is
real work, not a quick patch.

### Static RE EXHAUSTED (2026-06-13) — it's a dynamic-only crack
Tried every static angle; all hit the objc/CCB wall:
- NOT data-driven: `MainMenu.plist` ComingSoon ref is just a sprite frame
  (`coming_soon_sticker.png`); `WorldDefinition.plist` has 12 panels but no
  availability flag; cloning `GameConfig_T5` did nothing.
- NO hardcoded per-world level-count table (searched 30/40/45/15 as int32/byte/float
  /cumulative — absent). Counts are computed at runtime (likely by probing which
  `<w>_<l>.dat` exist).
- NO static callers of `createLevelInfo:`/`getTotalLevels:`/`getFirstLockedWorld`/
  `getWorldPanelForIndex:` — all `objc_msgSend`-dispatched.
- `_comingSoon` accessed via non-fragile ivar (offset 0x1c, loaded through a global,
  not an immediate); "WorldDefinition" isn't even a C-string; `_worldPanels` is an
  ivar with no string xref. Same wall as the uncracked bike-select gate.

### Dynamic plan (the real path — proven on-device ARM-stub method)
Reuse the `build/patch_keylog.py` code-cave technique (cave @0xaf745c; hook = replace
an insn with a branch to the cave, do work + displaced insn + branch back; the project
captured the cipher keys this way — Frida crashes the 32-bit game).
Hypothesis: `WorldDefinition.plist` lists 12 panels, so the level-select probably
CREATES all 12 and flags 5–12 `comingSoon=YES`. If so:
1. **comingSoon-clear hook** at `getWorldPanelForIndex:` epilogue (~0x56bc14, panel in
   r4/r0): `cmp r4,#0; strbne #0,[r4,#0x1c]` → clear `_comingSoon` on returned panels.
   Test on device: does World 5 stop showing "COMING SOON" / become selectable?
2. If yes, remaining gates to clear (each its own hook/experiment): per-world LEVEL
   COUNT (so W5 shows N level buttons — trace where it's computed), tapping a W5 level
   actually loads `5_M.dat` (navigation), and a theme/parallax for W5 (clone T1/t1).
3. Re-add generated `5_1..5_N.dat` so W5 has content before testing (else a cleared
   comingSoon on an empty world likely crashes on tap).
This is iterative device work (build hook → install → user navigates → logcat/observe →
refine), multi-session. Crash risk on partial hacks — build each hook carefully + assert
the hook-site bytes (like patch_keylog does) before patching.

### DYNAMIC TRACE WORKS (2026-06-13) — toolkit + foothold
`build/patch_worldtrace.py` = a reusable diagnostic ARM-stub hook (code cave @0xaf745c,
logs to logcat via the proven `__android_log_print` PLT @0x36b55c). **Gotcha that cost
several round-trips: the capture command `adb logcat -s RVxx:*` — zsh GLOB-EXPANDS the
`*` → command aborts → empty file. Use `adb logcat | grep --line-buffered RVxx > f`.**
- First target `initWithTarget:worldBoundary:`@0x456ff4 NEVER fired at level-select —
  "worldBoundary" = the **Box2D ride-scene** init (fires on level START), not a UI panel.
- Re-targeted `getWorldPanelForIndex:`@0x56bacc → **FIRED** at world-select: 3 calls, all
  index=**105** (≈ a current global LEVEL index, world-3 range — so this is a
  current-position lookup, NOT the per-world panel iterator), callers (runtime) →
  static via **load base 0xb8fc0000** (from `/proc/<pid>/maps`, `r-xp …libgame.so`):
  `0x562b80`, `0x56ac88`, `0x5669c4` — all in the LevelSelectionMenu display region
  `0x562xxx–0x56bxxx`.
- comingSoon is written via the non-fragile-ivar global (no immediate `strb [_,#0x1c]`)
  and the world limit isn't a plain `cmp #4/#5` in that region → both are runtime/ivar.
- **NEXT trace target: panel CREATION** (where `_comingSoon` is SET) — hook the
  LevelSelectionMenu scene setup / `WorldPanel`'s REAL init (find via the WorldPanel
  class method list; `initWithTarget:worldBoundary:` was a red herring). Log LR there to
  find the population loop, then the comingSoon write + world-index condition to patch.
- Toolkit + base-mapping technique are reusable; the trace pipeline is proven end-to-end.

## 5. Open questions / bugs
- **Barrels not visible on device** (generated W1L4 reported 2 barrels, none seen).
  Check: dynamic-body collision filter vs generated slabs, spawn embedding/ejection,
  or render layer. (Investigating.)
- Exact rideable-surface slope cap (measure the *top profile* only, not all edges).
- See-saw anchor/body reference model (how `Anchors` bind the joint to two bodies).
