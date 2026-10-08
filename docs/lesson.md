# Lessons — gotchas & what works

> *Mistakes we made and rules we learned, so we never repeat them. Newest first.
> Each: the trap, the fix, why.*

## Level data / physics

- **Box2D ground must be wound COUNTER-CLOCKWISE.** A clockwise terrain polygon
  builds collision solid-side-DOWN → the bike falls straight through → the race
  never starts. (1_1's rideable terrain is 97/104 CCW.) *Fix:* reverse the vertex
  list so signed area > 0 (Y-up).
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
- **The ground is MANY small CCW polygons, not one big shape** (CONFIRMED across
  130 levels: median 46 polygons/level, median 4 vtx, 72% ≤8 vtx). A single giant
  generated polygon builds an invalid Box2D body → bike falls through. *Fix:*
  generate the terrain as a strip of small CCW quad slabs (one per surface segment).
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
