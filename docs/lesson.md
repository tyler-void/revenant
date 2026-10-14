# Lessons — gotchas & what works

> *Mistakes we made and rules we learned, so we never repeat them. Newest first.
> Each: the trap, the fix, why.*

## Level data / physics

- ~~Box2D ground must be wound COUNTER-CLOCKWISE.~~ **Corrected 2026-10-08:** real levels
  ship clockwise physics polygons and play (1_25: 53 CW, 41 of them dynamic). The generator's
  fall-through came from one giant concave polygon, not from its winding. CCW is still the
  safe choice for NEW terrain (the editor's draw tool winds CCW), but it is not a rule —
  measure the corpus before turning a hypothesis into a lint error.
- **A level's `lid` must match the slot it's placed in.** A `.dat` swapped into
  `1/1_4.dat` must have `lid:"1_4"`; the game keys off it and rejects a mismatched
  level (decrypt succeeds — confirmed via the RVLEN keylog — but the scene never
  builds: no terrain, no bike, frozen). The generator takes `--lid` for this.
- **`refobjectList` / `mountedSprites` are entity ARRAY INDICES, not tags.** Rebuild
  or reorder the entity list and these references break. *Fix:* remap indices on
  assembly (the generator does this for the finish group).
- **Test generated levels in a NON-tutorial slot (levels 4+).** Levels 1–3 run the
  controls tutorial (`loadTutorial`), whose overlay logic depends on the bike
  becoming "ready"; a broken level hangs the tutorial and masks the real cause.
  Use a post-tutorial slot to isolate level bugs.
- **The ground is MANY small polygons, not one big shape** (median 46 polygons/level, median
  4 vtx, 72% ≤8 vtx). ⚠ 2026-10-08: "must be CCW" and "≤8 vertices" are NOT rules — 1_25 alone has
  53 CW physics polygons, and long polygons exist. The old fall-through came from one specific
  generated polygon. G2 (procgen.md) VERIFIED that one 337-vertex concave polygon, one spline, and 55
  quad slabs all load and ride the same track; the generator now emits one spline per ground run.
- **A spline ground is a CLOSED Catmull-Rom** — the bottom corners bend the end segments (≈6-unit bump
  at a run end). Put a straight 2-unit stub (segments=1) at each run end.
- **Test rides write the slot's best time / medals into the save.** Pick the test slot knowingly.
- **Type fidelity** in plists: int vs real usually doesn't matter (the game reads
  NSNumber), but be deliberate. JSON preserves int/float via the `.0`.
- **To make generated levels feel hand-built, CLONE real prefabs — don't invent
  entities.** Bare terrain + start + finish reads as empty next to hand-placed
  levels. Pull decoration `EditorSprite`s and `ExplosiveBarrel` groups straight
  from a decoded template (full Properties preserved → valid for that world) and
  reposition copies. Layer scenery by `z` for depth: trees behind (`z<0`), rocks/
  ground-detail on the surface (`z` 3–5), darkened foreground silhouettes (`z=13`).
- **Dynamic-body Vertexes are WORLD coords, not local.** An `ExplosiveBarrel`'s
  child physics body stores its box as absolute vertices around its `position`
  (e.g. ~1388, not ~0). To relocate it you must offset BOTH `position` AND every
  `Vertex` by the same delta (terrain slabs are the same). A `_translate_entity`
  helper that shifts both is the safe primitive.

## Cipher / unidbg

- **Run the unidbg oracle with `</dev/null`.** On an emulation exception unidbg
  drops into an interactive debugger that blocks on `stdin` forever (it froze a
  16-min background run). EOF makes it bail — the codec result is already written.
  The `UC_ERR_INSN_INVALID` at teardown is harmless.
- **`module.callFunction` marshals each `Long` arg as a 64-bit register PAIR.**
  Pass `int`s (one per register) or args land in the wrong registers.
- **`vm.loadLibrary(so, true)`** (forceCallInit) is required before `objc_msgSend`
  dispatch works (it runs the ObjC class-registration init).
- **The cipher is directional** (Blowfish, not a symmetric stream) — encrypt ≠
  decrypt; you need the encrypt-direction process fn (`0x6507d4`), found as the
  mirror of the decrypt one (`0x65085c`).
- **Frida crashes the 32-bit game** on attach → use unidbg + on-device code-cave
  ARM stubs for live capture instead.

## Native patching (libgame.so, ARM)

- **libgame.so is ARM, not Thumb.** `objc_method` layout is `{name, types, imp}`
  (the IMP is the THIRD word, at +8 from the selector-name pointer — not −4).
  Generic `objdump` reports `architecture: UNKNOWN!`; use Capstone in `CS_MODE_ARM`.
  Find an IMP by scanning PT_LOAD bytes for a 32-bit LE pointer to the selector
  string, then read the word at +8.
  ⚠ The −4 rule (used in older notes and the unidbg harness comments) returns the
  PREVIOUS method's IMP and mislabels silently: `0x64ea98` is
  `DataDecryptedFromData:Password:`, not `DataWithContentsOfFile:Password:` (`0x64ec3c`).
- **One mechanic can have MULTIPLE consume paths — patch them all.** Unlimited
  fuel looked done (gauge redirect + `useFuel:` NOP) but gas still "dried" mid-play.
  Cause: `consumeBars:` @0x6ab4b0 is a SEPARATE singleplayer per-attempt consume
  that does its own `gasBarsLeft -= count` (@0x6ab668). Fix: at entry it tests the
  game's own pause/unlimited byte flag and `bne`s to an exit returning SUCCESS
  without decrementing — force that branch unconditional (ARM cond nibble NE→AL,
  `0x1a`→`0xea`). Lesson: a display patch can mask a still-live gameplay path; trace
  the actual decrement + the caller's out-of-X check, not just the getter.
- **Reuse the game's own "skip" branch** instead of inventing one. Many gated
  routines already have a no-op/owned path guarded by a flag check; flipping that
  branch to unconditional is safer (correct return value, no side effects) than
  NOP-ing the body or forcing a different return.

## Rendering

- **The game's WebP atlases are non-standard** (12-byte VP8X, 4-byte dims, lossless
  ALPH) — ffmpeg/ImageMagick/Pillow all reject them. *Fix:* extract the standard
  VP8 (RGB) chunk, decode, chroma-key black→alpha.
- **Same frame name lives in multiple per-theme atlases** — resolve against the
  level's world or you render the wrong world's art (we drew world-3 ice in world 1).
- **Sprites are Y-up + clockwise rotation; the canvas is Y-down.** Use the proper
  composed transform, not ad-hoc `rotate()+scale(1,-1)` (which mirrored sprites).
- **Atlas art is HD texels; level coords are points** — divide sprite size by a
  content scale (~8.5 here; calibrate against the physics outline, it's the truth).
- **Sprites are trimmed in the atlas** — draw the trimmed region at its natural
  size + apply `spriteOffset`; don't stretch it to the source size (distorts ~10%).

## Build / tooling / repo

- **`build/build.sh` re-decodes from the clean original** (`rm -rf build/work`) —
  it WIPES any swapped-in files. To device-test an edited asset: swap into the
  already-decoded `build/work`, then `apktool b build/work` directly + sign +
  `adb install -r`. (Keystore must stay → `install -r` preserves the save.)
- **The reproducible build must never read gitignored files.** `patch_tilt` used to
  load the owner's tilt smali from `ref/` (gitignored), so `build.sh` on a fresh clone
  died at the tilt step — it only ever worked on the owner's machine. *Fix:* express
  patches as anchored edits on the decoded original, and test the build from a clean
  decode in a scratch dir.
- **A modified decompiled class is still decompiled code.** A whole tweaked
  `MCAccelerometer.smali` (mostly Miniclip's class) was committed in the initial commit
  despite the no-smali rule. Ship patches as anchored edits, never whole classes.
- **`.gitignore` has NO inline comments** — git treats `base/ # note` as a literal
  pattern matching nothing. Comments on their own lines. Always
  `git check-ignore -v <path>` to verify before committing.
- **Never let render-capture PNGs or decrypted caches get committed** — they're
  derived game art / circumvention material. Gitignore `tools/level-editor/{levels,
  textures}/`, `/*.png`, `*.level.json`; leak-check `git add -An | grep …` before commit.
- **`pkill -f <pattern>` can match its own shell** (exit 144). Kill a server by its
  listening port instead (`ss -ltnpH | grep :PORT`).
- **Device-verify everything.** The unidbg round-trip proves the cipher; only the
  phone proves it *plays*. Several "fixed" things were only truly confirmed on hardware.

## Working with the human

- Tyler is an expert player — **their eye on the real level is the best
  ground truth** when emulator/static analysis is ambiguous (e.g. the content scale).
- Surface honest tradeoffs and ask before sinking effort into deep RE rabbit holes.

## Browser APK patching — DEX + AXML surgery (Phase 8)

- **A half-applied tilt fix is the *crashing* config.** Force-registering the sensor
  (register/unregister byte-patches) WITHOUT the `onSensorChanged` try/catch makes the
  game forward to the NATIVE `onSensorChanged(FFFJ)` before `libgame.so` binds it →
  `UnsatisfiedLinkError`. So the tilt fix must be ATOMIC: all three method edits or none.
  `dex_tilt_rewrite` resolves+verifies everything first and returns 0 (no-op) on any
  mismatch, so a wrong/non-1.5.2 dex is left untouched rather than half-patched.
- **Growing a method = append a new `code_item`, don't rewrite in place.** Appending at
  the END of the code section + repointing the method's `code_off` keeps contiguity and
  means you only shift u32 offsets ≥ the insertion point (+K) and bump the map's
  CODE count by 1. The old code_item is left as harmless dead data. Far less error-prone
  than a full re-serialize. The ONE uleb offset (`code_off`) stays same-width here (both
  ends near EOF) so it's an in-place overwrite — assert that.
- **`map_list` is NOT always last.** This dx-built dex puts MAP_LIST at the *start* of the
  data section (it = `data_off`), well before the strings/code. Compute "does it move?"
  from `off >= P`, never assume it's at EOF. (Cost me a corrupted map the first run.)
- **Use a catch-ALL handler to avoid touching the type pool.** `.catchall` needs no
  `type_idx`, so no new string/type ids → no sorted-table reindex. (Typed `.catch ULE`
  would've forced adding `UnsatisfiedLinkError` to the pool — a much bigger edit.)
- **DEX opcode gotcha: `iget-wide` is 0x53; 0x5a is `iput-wide` (store!).** Hand-assembled
  bytecode passed structural checks but baksmali revealed it was *storing* garbage into
  the event's timestamp. **Always round-trip hand-built dex through baksmali (dexlib2, a
  strict parser) — it catches semantic bugs a structural validator won't.**
- **Verify the whole chain with real tools:** Python oracle → Rust→WASM **byte-identical**
  diff → baksmali disasm of all touched methods → `aapt2 dump badging` on the final APK →
  `jarsigner -verify` → install + boot on device. Each layer caught a different class of bug.
- **Binary AXML editing is easy (vs DEX):** chunks reference the string pool by INDEX and
  each other by ORDER — no byte-offset cross-refs. Removing a `<uses-permission>` or a whole
  nested `<service>/<receiver>` (START→matching-END by depth) is just splice-out + decrement
  the root chunk size (offset 4). String pool left untouched (orphan strings are fine).
- **MIUI's first-launch "review" lists generic AppOps, not just manifest perms.** Dropping
  `ACCESS_COARSE_LOCATION`/`GET_ACCOUNTS` removed the location/accounts rows; but "Send MMS",
  clipboard, installed-apps, background-windows persist — they're MIUI behavior toggles shown
  for any legacy (targetSdk-22) app, NOT manifest-backed, and default OFF. Don't chase them
  with manifest surgery (a targetSdk bump would, but that breaks a 2014 game). Removing the
  GCM components is still worth it (kills the actual push/"MMS" capability + the tracker).
- **Tilt needs no `HIGH_SAMPLING_RATE_SENSORS`:** `register()` uses `SENSOR_DELAY_GAME`
  (~50 Hz, under the 200 Hz gate). End users only grant "Motion"/sensor access; nothing else.

## Mod menu vs the run timer (Phase 6) — RESOLVED 2026-06-15, and a lesson in not over-claiming

- **History (2026-06-14, since RESOLVED — see the last bullet):** with `libmod` active the in-race level timer freezes at 0.00 on normal
  single-player levels. Overlay-only (hooks installed but passing through) counts; running the
  game-logic hook bodies freezes it — but **no single culprit was consistently isolated** (the same
  config that counted in one round froze in a later build). Leaderboard anti-tamper is *plausible*
  (online ghost-racing + `SubmitGhost`/`submitTime`) but was **NOT proven**. Full record: modmenu.md.
- **Don't write up an unproven theory as fact.** I prematurely documented "it's anti-tamper on
  per-frame writes, fixed by gating" — but the gated, write-nothing build STILL freezes, so that was
  wrong. State what you *measured*; label hypotheses as hypotheses. (The owner rightly called this
  out: "don't fabricate.")
- **Single noisy runs make spurious correlations.** The "speed readout freezes it" belief held for
  ~15 cycles, then collapsed — every full-mod build had other hooks active too. **Before bisecting,
  establish deterministic-vs-intermittent (retry the SAME build 3–4×).** Inconsistent results across
  near-identical configs = you're not measuring what you think; instrument the actual value (the
  timer ivar), don't infer it from a feature toggle.
- **Bisect with RUNTIME-TOGGLEABLE hooks, not rebuilds** (the one unambiguous win here). A flags file
  (`rvdebug.txt`) each hook
  reads at startup — pass-through when its flag is 0 — turns a 5-minute rebuild/reinstall per bisect
  step into a push-file + relaunch (seconds). `build/bisect_modhooks.sh` automates it. This is the
  single biggest debugging-velocity win for native mods.
- **`adb install -r` doesn't reliably kill the running process** → you can end up testing the OLD
  libmod. Always `am force-stop` before relaunch, and log a BUILDTAG so you can confirm from logcat
  which build is actually live.
- **Apportable realizes ObjC ivar offsets at runtime** — the static offset (e.g. backWheel_ @0x54)
  is wrong; read the realized offset from the `_OBJC_IVAR_$_…` variable at runtime (`*(g_base+var)`).
- **Frida is unstable on 32-bit/thumb Apportable and trips anti-tamper** — the in-process NDK
  `libmod` (inline hooks + ImGui) is the right instrument here, not Frida.
- **Correction (2026-10-08):** the bullet below names `gameTime_` as the corrupted clock. Measured live
  via the bridge, the HUD run timer is `Manager.time_` and `Physics.gameTime_` stays 0 in a
  single-player race — so that mechanism is unproven; the idle-fast-path fix itself stands.
- **Don't do per-frame work inside the physics-step hook — read game state from the OVERLAY hook.**
  (The run-timer "freeze" — RESOLVED 2026-06-15.) Running ANY body in `-[World step:]` each frame
  (even a read like `[self world]` or a chassis-velocity `msgSend`) corrupted the game's `gameTime_`,
  which froze/crawled the in-race timer AND garbled ghost replay (the ghost interpolates on `gameTime_`
  → sub-1s times + wrong route, same root cause). It was NOT anti-tamper: the `dt` handed to `step:`
  was a perfect real-time 1/60 (ratio≈1.0 — *measured* via a probe, which is what finally killed the
  anti-tamper theory). The earlier "gate the writes" fix failed because it still ran the step body
  every frame. Two-part fix: (1) **idle fast-path** — `hook_step` returns immediately unless a feature
  is actually engaged (`step_active()`); (2) **move read-only HUD work (speed) to the swap/overlay
  hook**, which is timer-safe. Rule: the physics step is sacred — run it only when the user is actively
  modifying physics (gravity/specs); everything read-only belongs in the overlay.

## Agent tooling on Waydroid (Phase 1.5, 2026-10-08)

- **Don't guess readiness flags — call what the buttons call, then verify a postcondition.** Three guesses
  failed in a row (`entered_` never flips; `inputEnabled_`/`_levelDotData` are set too early;
  `-[LevelSelectionMenu didFinishLoading]` actually fires right AFTER a level loads). What worked:
  `loadLevelSelectionMenu` → `-[LevelDot selectAndDisplayDetail]` → `-[LevelDetail race]` (the RACE
  button's own selector, read from its disassembly), then check the result: the lid the reader saw, the
  bike was created, the Box2D world gained bodies — retry via `-[MotoXGame exitToMenu]` if not.
- **`startLevelWithNumber:` with no selected dot loads an EMPTY level** (no level file read, no terrain, no
  bike) — a failure that looks like success unless you check the postcondition.
- **Instrument before you theorise.** Emitting `goto_wait` events with the candidate flags exposed the wrong
  readiness guess in one run; a 3-candidate `state` sample exposed that the HUD timer is `Manager.time_`,
  not `Physics.gameTime_` (which stays 0) — overturning a mechanism the docs had stated as fact.
- **Measure rates with paired samples from ONE clock domain.** `state` returns `run_time` and
  CLOCK_MONOTONIC sampled on the GL thread in the same frame; adb round-trips would add noise bigger than
  the effect. Result: the game adds exactly 1/60 s per frame, so the timer runs at fps/60 (≈1.016 at
  Waydroid's ~61 fps) — a game property, not a mod bug.
- **Read ivars by name through the game's own ObjC runtime** (`object_getInstanceVariable` +
  `ivar_getOffset`, exported by libgame, via `dlsym`) — realized offsets, no hardcoding, works under
  houdini. It turned "find the offset" sessions into one bridge call.
- **Waydroid gotchas:** the container FREEZES when no window is shown (every `adb shell` hangs) →
  `persist.waydroid.suspend false`; `/proc/uptime` and `boot_id` are the HOST's → detect an Android restart
  by a new `system_server` PID; `waydroid` is a `#!/usr/bin/env python3` script → inside a venv it loses the
  system `dbus` module (strip the venv from PATH); `screencap` works (black on the phone).
- **An abstract unix socket has no filesystem permissions.** A dev bridge that can execute arbitrary
  methods must check `SO_PEERCRED` (accept uid 0/2000 only) and stay off by default.
- **Fast native loop = swap one file inside the APK.** `zip` the new `libmod.so` over the old entry, re-sign
  with the same key, `install -r`: 3–8 s per iteration instead of an apktool rebuild.

## Level editor MVP (Phase 4, 2026-10-08)

- **Check a rule against shipped levels before linting for it.** "Terrain must be CCW" would have
  flagged 58 of 1_25's 76 polygons in a level that plays; it is `info`, not an error. Likewise the
  ">8 vertices / concave" warnings: tag issues already present in the shipped level ("in original")
  so authors only see what THEY introduced.
- **JSON round trips lose int vs real.** A browser save turned 3 087 reals of 1_25 into ints. Fix:
  every entity carries `__src` (its row in the last typed save) and the server restores types from it,
  with a (Type, path) schema from all cached levels as the fallback — only ONE numeric path in the
  corpus mixes int/real (`Vertexes[].segments`). Proven by decode → save → export → decode.
- **Python's json writes bare `NaN`; browsers reject it.** Real levels contain NaN, so the editor
  couldn't even load 1_25 before the wire format tagged non-finite reals.
- **Handles overlap on real data.** A terrain vertex sat exactly on the entity's `position`, so a
  "move" drag on the gizmo centre dragged the vertex instead. With the Move tool the gizmo wins.
- **Group entities have no position** (barrels, finish, prefab groups) — anything that needs a point
  must fall back to the children.
- **Prove "loaded" by the postcondition, not the request:** the push helper waits for the game to log
  `[MOD] <lid>.dat` (it read OUR file) and `goto_done` with the right lid + bodies + bike, after a
  unique `RVMARK` proves the log ring has caught up (the ring replays old logcat history first).

## Bridge v1 — driving the game through its own input handlers (2026-10-08)

- **Never call `object_getInstanceVariable` with a name that may not exist.** In Apportable's runtime it
  crashes BOTH ways: with an out-pointer it dereferences a NULL Ivar for an absent name (SIGSEGV on the
  first `bike` call), and it writes `*outValue` unconditionally, so NULL crashes too (SIGSEGV at startup).
  The bridge walks the class chain's ivar lists itself and hands the entry to the runtime's
  `ivar_getOffset`, so a missing name just returns null.
- **One input event has several listeners — inject at all of them.** The HUD's gamepad handler only fires
  the "tap" that starts physics; the bike's handler stores the throttle value. Calling either alone looks
  half-broken (no race, or a race where the bike just rolls). Send the HUD only the rising edge (a re-press
  while dead is "retry").
- **The crash flag in every MCP response paid for itself:** both bridge crashes surfaced in the very
  response of the call that caused them, with the tombstone lines attached.
- **Agent latency is seconds.** A 4 s `hold_ms` looked "13 s late" because the next interactive call went
  out after the agent's own thinking time. Time-sensitive checks belong in one script (or `hold_ms`).

## In-game bot (G3, 2026-10-09 .. 10-15)

- **A 32-bit app dies at launch once its PID passes 65535** — bionic aborts with `Limited by the size of
  pthread_mutex_t, 32 bit bionic libc only accepts pid <= 65535`. Waydroid inherits the host's
  `pid_max` (4194304), and every `adb shell`, `screenrecord` and relaunch burns PIDs inside the container,
  so after a long session the game "crashes on start" for no visible reason. Fix: restart the Waydroid
  session (`waydroid session stop && waydroid session start` — fresh PID namespace). `device_ensure` now
  reports `last_pid` and warns above 60000.
- **The game is NOT run-to-run deterministic under bot input.** Same build, same level, no plan: 1_1 finished
  (26.67 s) and two minutes later died at x 1401. Two identical 1_24 runs happened once — luck, not a rule.
  Never judge a controller change from one run per level; the bot's gate rides every cell 3 times.
- **Per-frame lean corrections make the bike jiggle and bleed momentum** (owner, watching the videos). On the
  ground: lean only outside a deadband (neutral 0% → 56% of frames, sign flips 8.3/s → 2.0/s on 1_1). In the
  air: one held action per decision window, sized from the measured rotation model — not a PD at 60 Hz.
- **A full lean is strong:** airborne angular acceleration ≈ −21.9 rad/s² per unit lean (9.8k samples), i.e.
  ~2.7 rad in 0.5 s at full lean. Open-loop holds of 0.5–1 s overshoot; small held leans re-decided every
  ~0.25 s work.
- **Ceiling heuristics need the map, not just the current velocity.** "Launch now with the current velocity"
  braked under arches the bike passes beneath and on ramps it simply follows (1_1 parked itself). Walk the
  mapped surface ahead to the real takeoff point, then check THAT flight; skip geometry the rider is already
  inside (it evidently doesn't collide).
- **Don't gate the generator on beating hand-made levels** (owner decision 2026-10-15). Stock W1 levels are
  puzzles (boats, water gaps, overhangs); the bot only has to beat what the generator emits. Gate = every
  generator feature × difficulty, alone, ridden 3× (`gen.py features`); W1 stays a benchmark.
- **The corpus see-saws are thick mechanical arms (~14 units)** hung over pits. On flat ground the low end is a
  wall the bike pushes against — the generator builds an approach ramp to the plank's top.

