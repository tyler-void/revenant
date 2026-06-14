# Phase 6 — in-game live mod menu + debug HUD

> The durable record for the live mod menu. Goal: an in-game ImGui menu to **live-tune**
> bike specs / name / gravity / physics, plus a top-of-screen debug HUD. Read before
> touching the menu/HUD work.

## What's live-tunable (confirmed in libgame.so)

- **Bike specs** — live setters exist: `setSpeedLimit:`, `setNitroPerformance:`,
  `setForceScale:`, `setMaxWheelieSpeed:`, `setBurnoutSpeed:`, plus per-body
  `setDensity:`/`setRestitution:`/`setFriction:`, `setScale:`. Slider → call setter.
- **Gravity** — full **Box2D**. `b2World` struct (in the binary's ObjC type encodings)
  has `m_gravity` (a `b2Vec2`) at a fixed offset; there's also `applyGravity:` +
  `gravityLength: %f`. NOTE: the game may apply gravity per-body via `applyGravity:`
  rather than (or in addition to) `b2World::SetGravity` — confirm which during the spike.
- **Bike name** — `setName:` exists (live rename).
- **Appearance** — `bikeFrameNode_` + built-in `ChangeBikeMenu`: swapping among existing
  bike looks is feasible; fully custom art = mod-loader/custom-assets (later).

## Debug HUD telemetry (read-only, top of screen — don't clutter the drive)
- **IMPLEMENTED** (`draw_hud`): `FPS | RAM MB | CPU% | SPD`. FPS via ImGui `io.Framerate`;
  RAM/CPU via `/proc/self/statm` + `/proc/self/stat`. **SPD = `-[BikeCommon1 getSpeed]`**
  (the real override, IMP 0x67a278 — the parent `Bike` getSpeed @0x66cca8 is a 0-stub; ALWAYS
  dispatch dynamically via `objc_msgSend(g_bike_self, selReg("getSpeed"))`, returns `float`,
  Box2D m/s = back-wheel `[body] m_linearVelocity` magnitude). Read inside `hook_step` (where
  `apply_specs` already safely uses `g_bike_self`) and stored in `g_cur_speed`, so `draw_hud`
  never derefs a stale bike between levels; reset to 0 on level load. Units are raw Box2D m/s
  (no clean PTM_RATIO in the binary — calibrate a display multiplier empirically if wanted).
- Other candidates (not yet shown): rotation `currentRotation_`/`displayRotation_`, the velocity
  *vector* (back-wheel `m_linearVelocity` x/y at body+0x44/+0x48), current bike
  `currentBikeIndex_`/`getBikeDisplayName:`, air time `airTime_`, flips.

## Architecture
- **UI:** ImGui is the right tool for live sliders. Game is **GLES2/EGL**
  (`libGLESv2.so`, `glDrawElements`) → standard inject point: a small injected lib hooks
  `eglSwapBuffers` to draw ImGui + routes touch, and reads/writes game memory by offset.
  This is the single biggest new build. **Fallback:** native cocos2d UI (no injection).
- **Data layer (needed either way):** locate the **live bike object** + the **`b2World`
  pointer** at runtime (same code-cave/trace technique as the World-5 work,
  `build/patch_worldtrace.py`). Powers both the HUD (read) and the menu (write).
- **Synergy:** live menu = experiment; Phase-1 bike editor = persist. Add "save live
  tune → bike plist" to bridge them.

## Build order
1. **Data-layer spike (IN PROGRESS):** find the live bike object + `b2World`, prove
   read+write with a **live gravity change** demo (you'd feel the bike float). De-risks
   the whole menu.
2. Native debug HUD (read telemetry → top-of-screen `CCLabelTTF`; enable cocos2d FPS).
3. ImGui menu shell (inject + eglSwapBuffers hook + touch) — or native fallback.
4. Wire the live-tune fields (specs/gravity/name) + the HUD toggle.

## Persistence model (NO ROOT needed — owner concern, resolved)
The mod runs IN the game process → it can write the app's OWN storage with no root on
any Android (sandbox). Two lanes, both root-free, both NO APK re-patch:
- **Settings** (gravity multiplier, toggles, live-tune values) → mod writes a mod-config
  (JSON/plist) to the app data dir; a startup/level hook re-applies it. e.g. the
  `setGravity:` hook reads a SAVED multiplier instead of the spike's hardcoded ÷4.
- **Custom assets** (bike plists, levels, appearance) → the **MOD-LOADER**: hook
  `CCFileUtils::fullPathForFilename:` so a file in a writable `mods/` folder
  (`getExternalStoragePath`, app-writable, no root) overrides the APK asset. The menu's
  "Save" writes overrides there; players share mods by dropping files in. This is the
  linchpin for persistent custom content — elevate it alongside Phase 6.
ImGui live writes alone are SESSION-ONLY (RAM, lost on exit) — persistence comes from
the two lanes above, not from the live edit.

## DEFERRED — the "register a NEW content slot" problem (held, per owner)
Both are the same unsolved hard RE (registering brand-new content, native + config):
- **New shop bike entry** (22nd+ bike): roster lives in encrypted `ProductList.dat`/
  `Shop.dat` (config key, have it) + native shop code. Count/registration unsolved.
- **World 5** (new world): `_comingSoon` gate + registration — paused (docs/procgen.md).
Re-skinning/re-speccing the 21 EXISTING bike slots works + persists (mod-loader) — so
"custom bikes" is covered; brand-new shop rows are the held part.

## Spike findings
- **Gravity is the Box2D world gravity.** `setGravity:`@0x64d3a4 writes the b2Vec2 to
  `b2World.m_gravity` at `world+0x19240` (x) / `+0x19244` (y); gravity args arrive in
  r2(x)/r3(y) as float bits. DEMO SHIPPED: `build/patch_gravity_spike.py` hooks
  `0x64d3bc` and does `sub r3,r3,#0x01000000` (÷4 gravity.y via exponent decrement,
  VFP-free) → bike floats. Device-installed. ⇒ live physics writes PROVEN; the menu's
  gravity slider = the same hook reading a saved/live multiplier.
- Accessors located for the menu data layer: `getBikeBody`@0x66d95c…, `getSpeed`@0x66cca8,
  `heroTorso`@0x67ab98, spec setters `setSpeedLimit:`@0x66e1cc etc., `world`@0x64d81c.

## MOD-LOADER recon (2026-06-14) — path resolution + the open question
- Assets resolve under **`assets/unpack/`** (binary references it + `getResourcePath`/
  `bundlePath`/`/Contents/Resources/`); level paths built from format strings
  `%d_%d.dat` / `%@/%@.dat` / `%d/%d_%d.dat`.
- **`CCFileUtils fullPathForFilename:`@0x4d78b0 is NOT the resolver in practice** — hook
  installed + confirmed in the binary, but it NEVER fired at startup/menu. The game
  resolves via a different path (the `resolutionType:` variant @0x4d78e8 directly,
  `fullPathFromRelativePath:`, or direct construction). So the clean "prepend mods/ to
  `_searchPath`" approach is unconfirmed until we know the game consults CCFileUtils.
- cocos2d DOES have the search-path API (`setSearchPath:`@0x4d8b1c, `_searchPath` ivar,
  `getPathForFilename:withResourceDirectory:withSearchPath:`) — still the preferred
  mod-loader IF consulted.
- The encrypted reader **`+[NSData DataWithContentsOfFile:Password:]`@0x64ea98 fires on
  LEVEL LOAD** (proven via the RVLEN keylog). A read-trace hook is installed to log the
  exact `.dat` path (`[path UTF8String]`).
- `screencap` = black (GL surface) → can't navigate blind; the level-load trace needs the
  USER to load a level (same as the World-5 trace).
- **DEVICE BUILD (installed, combined, launches clean):** gravity ÷4 (cave 0xaf745c) +
  read-trace (cave 0xaf7500, tag **RVRD**). Built via `build/patch_gravity_spike.py` +
  `build/patch_pathtrace.py` re-targeted to 0x64ea98/r2/cave2.
- **NEXT (needs user):** load any level → `adb logcat | grep RVRD` → the exact `.dat` path.
  Then design the mod-loader: (a) redirect the reader's path arg to a `mods/` override if
  one exists, or (b) if CCFileUtils is consulted, prepend `mods/` to `_searchPath`.
- Trace tooling: `build/patch_pathtrace.py` / `build/patch_lvltrace.py`.

## ✅ MOD-LOADER DONE (2026-06-14) — native libmod.so + reader redirect
Switched from hand-assembled ARM stubs to a real **NDK-built native lib** (the toolchain
that also unlocks the ImGui menu). Pipeline, all device-verified:
- **Build:** `mod/build.sh` (auto-detects `/opt/android-ndk`, armv7a clang) → `libmod.so`.
- **Inject:** `build/patch_modlib.py` adds `System.loadLibrary("mod")` to
  `GameActivity.<clinit>` (try/catch, guaranteed-early — `firstRun()`/`loadNativeLibrary`
  is NOT per-launch; `<clinit>` is). libmod loads BEFORE libgame, so it polls
  `dl_iterate_phdr` until libgame is mapped, then hooks.
- **Inline hook:** minimal ARM32 prologue-relocating hook (`mod/mod.c` `inline_hook`):
  writes `LDR PC,[PC,#-4]` + abs addr over the target's first 8 bytes (prologue must be
  position-independent), trampoline = orig 8 bytes + jump to target+8. Calls the game's
  own `objc_msgSend`/`sel_registerName`/`objc_getClass` from C.
- **THE MOD-LOADER:** hook `+[NSData DataWithContentsOfFile:Password:]@0x64ec3c`
  (path,pw)→NSData — every encrypted asset (levels, configs) reads through it. Take the
  path's basename; if `<mods>/<basename>` exists, swap the path to that absolute file →
  the game reads+decrypts OUR file. `MODS_DIR =
  /sdcard/Android/data/com.miniclip.bikerivals/files/mods` (app-writable, **no root**).
- **VERIFIED:** dropped a generated `mods/1_24.dat`, loaded career level 24 →
  `[MOD] 1_24.dat -> .../mods/1_24.dat` → the game rendered our generated level and
  played, no crash, no APK re-patch. Mod files = the editor's encrypted `.dat` (same
  format); name them `<world>_<level>.dat`. Same mechanism covers bikes/configs.
- DEAD END that wasted a round: `loadLevelInfo:FileName:`@0x6e25dc only processes the
  Info object's medal-time floats — it IGNORES FileName. The geometry is read via 0x64ec3c.
- **Autonomous testing rig:** `adb shell screenrecord` captures the GL surface (unlike
  `screencap` = black) → `ffmpeg` 1 frame → readable; `adb shell input tap/swipe` in
  landscape 2340×1080 coords. Lets the agent see + drive the game without the user.

## ✅ IMGUI MENU LIVE + INTERACTIVE (2026-06-14) — device-verified by owner
The in-game ImGui menu now renders AND takes touch (owner confirmed: "tap, resize and
checkbox works fine"). All in `mod/mod.cpp`:
- **Draw:** hook cocos2d `swapBuffers`@0x4d095c (ARM, in libgame) — scene drawn + GL
  current; draw ImGui on top, then call orig (presents). NOT `eglSwapBuffers` (libEGL is
  Thumb + not in libgame's dynsym → inline-hook crash, dead end). GLES2 backend,
  `#version 100`, `-static-libstdc++` (no libc++_shared in the APK).
- **Readable on a phone:** `MENU_SCALE 3.0` via `io.FontGlobalScale` +
  `GetStyle().ScaleAllSizes()` at init (owner: default was "too small").
- **TOUCH — the real handlers are Miniclip C funcs, NOT ObjC.** `MtouchDown`@0x701958 /
  `MtouchMove`@0x702000 / `MtouchUp`@0x701c20 (libgame dynsym), called by the JNI
  `Java_com_miniclip_input_MCInput_nativeTouches{Begin,Move,End}`. Signature
  `M*(int id=r0, int x=r1, int y=r2, int d=r3)`; x/y are **screen pixels = GL viewport
  space** (1:1, scale calibrated to 1.0 via the on-screen cursor landing on the tap).
  The JNI converts MotionEvent floats→ints then calls these. (The earlier guess that
  0x4d0b54/0x4d0bac/0x4d0c04 were `-[CCView touchesBegan:…]` was WRONG — those are
  sub-functions of MtouchDown/Up; hooking them as ObjC methods crashed. The crash
  backtrace `nativeTouchesEnd → MtouchUp → 0x4d0c04` is what revealed the real path.)
- **Same-thread input:** MtouchDown/Move/Up run on the **GLThread** (same as swapBuffers,
  via GLSurfaceView.queueEvent) → feed ImGui's input queue DIRECTLY from the touch hooks
  (`io.AddMousePosEvent`/`AddMouseButtonEvent`). ImGui **event-trickling** (default on)
  splits a fast down+up tap across two NewFrames so the press is never lost — per-frame
  sampling of a `g_tdown` flag DID lose it (tap toggles between frames).
- **Menu-region ownership (no 1-frame lag):** `draw_menu` publishes its window rect each
  frame; a touch-DOWN inside the rect → the menu "owns" the whole down→move→up sequence
  (fed to ImGui, NOT passed to the game) so the game UI under the menu doesn't react.
  Touches outside the menu pass straight through to the game.
- **`objc_msgSend_stret`@0x378578** (immediately after `objc_msgSend`@0x3783d4; loads isa
  from r1, r0 = hidden struct-return ptr). Found while debugging touch: CGPoint/CGRect
  returns use the stret ABI — calling such a method through plain `objc_msgSend` shifts
  args by one register → crash. Resolved in libmod (`msgSend_stret`) for future
  CGRect/CGPoint reads (e.g. view bounds); the touch path no longer needs it.
- **Debug HUD (owner ask):** top-center overlay (`NoDecoration|NoInputs`), toggled by a
  menu checkbox. Shows **FPS** (`io.Framerate`) + **RAM** (RSS, `/proc/self/statm`) +
  **CPU%** (`/proc/self/stat` utime+stime delta over CLOCK_MONOTONIC) — all process-level,
  NO game internals. Refreshed ~2/s. (Verified: "RAM 210 MB", "FPS 60" on device.)

## ✅ RELOCATING INLINE-HOOK + LIVE GRAVITY (2026-06-14)
- **`inline_hook` upgraded to a prologue-relocating trampoline** (`mod/mod.cpp`). The old
  hook copied the displaced 8 bytes verbatim → broke on PC-relative prologues. Now it
  decodes the 2 displaced ARM insns and:
  - `ldr Rt,[pc,#imm]` (literal load) → reads the link-time constant at install time, puts
    it in the trampoline's own literal pool, emits `ldr Rt,[pc,#newimm]` to reload it. The
    in-place `add Rt,pc,Rt` that usually follows runs AFTER the jump-back, so its PC (and
    thus the reconstructed GOT address) is correct.
  - `bx/blx Rm` and other PC-INDEPENDENT insns → copied verbatim.
  - any genuinely PC-relative insn left (b/bl, Rn=pc, Rm=pc) → ABORT (return 0) + LOGE,
    never silent corruption. **GOTCHA:** `bx lr` (0xe12fff1e) has SBO `1111` in bits[19:16]
    that look like Rn=pc — must whitelist `bx/blx` `(w & 0x0FFFFFD0)==0x012FFF10` BEFORE the
    Rn=pc test, else it false-aborts (it did, killing the swap hook the first time).
  This unlocks hooking ANY ObjC method (most start with `ldr [pc]` ivar/selector loads).
- **LIVE GRAVITY — WORKING (owner-confirmed).** Key insight: the level's `b2World` is
  CONSTRUCTED with its gravity, so `setGravity:`@0x64d3a4 is NOT called on a normal level
  (hooking it directly = 0 calls = no effect — the first failed attempt). Driver = the
  per-frame physics tick **`-[World step:(ccTime)]`@0x64d510** (same class as `world`
  @0x64d81c / `gravity`@0x64d3e0 / `setGravity:`). Each frame: read the level's base via
  the game's own `gravity` getter (b2Vec2 → **stret**), then `[self setGravity:(0, baseY*mult)]`.
  Base read once per level (detect `[self world]` pointer change). Slider **−30×…+30×**
  (negative = inverted → bike flies up) + −/+ (0.1 step) + Reset. Confirmed on device:
  `base gravity (0, -70)`, negative floats the bike, +30× slams it.
- **LIVE CAMERA ZOOM — WORKING (owner-confirmed), two modes.** `setCameraZoom:`@0x649e3c
  just stores an ivar (`str r2,[r0,r1]`); it lives on the **GameLayer** (a DIFFERENT class
  from the physics World — `step:`'s self gives `respondsToSelector:setCameraZoom:`=NO).
  Driver = **`-[GameLayer draw]`@0x648eec** (per-frame; identified via
  `respondsToSelector:`). The game writes `cameraZoom` itself each frame for a per-level
  DYNAMIC zoom (in/out at map sections), so two modes: **Flexible** = read the game's live
  value, multiply by `g_zoom_mult` (don't compound — detect whether the game changed it vs
  our own last write; at mult==1 don't touch it so dynamic zoom is untouched); **Locked** =
  force a constant `g_zoom_mult` each frame (overrides the dynamic zoom). Radio toggle +
  slider 0.2×…5× + −/+ (0.1) + Reset. (Locking it with a fixed value looked "trippy" =
  fighting the game's dynamic zoom — hence the two modes.)
- **Menu polish:** auto-fit was tried but it DISABLES manual resize (owner wanted resize) →
  reverted to a normal resizable+scrollable window with a tall default. Fine-tune −/+
  buttons on both sliders. Debug HUD (FPS/RAM/CPU) toggle.

## ✅ RESET PROGRESS + BIKE SPECS + TABS (2026-06-14)
- **Save layout found** (rooted `adb` exploration): the Apportable save lives in
  `/data/data/com.miniclip.bikerivals/files/Contents/Resources/` —
  **`g_<w>_<l>.dat` = per-level GHOST files**, **`data.dat` = progress/medals**,
  `ConditionState.dat` = achievements, `NSUserDefaults.plist` = settings. The mod runs as
  the app UID → can delete these with **NO root**.
- **RESET PROGRESS button** (System tab, 2-tap confirm): `do_reset_progress()` `unlink`s all
  `g_*.dat` + `data.dat` + `ConditionState.dat`, then `_exit(0)` so the in-memory copy can't
  re-save over the deletion (user reopens → fresh medals/ghosts). Keeps NSUserDefaults
  (settings); unlocks survive (native patches, not save data).
- **BIKE SPEC multipliers** (Bike tab): hook the 5 setters
  `setSpeedLimit:`@0x66e1cc / `setNitroPerformance:`@0x66e2a4 / `setForceScale:`@0x66e214 /
  `setBurnoutSpeed:`@0x66e2ec / `setMaxWheelieSpeed:`@0x66e37c (all LDR-PC prologues →
  relocating hook). Each captures the bike object + its CONFIG base value and applies
  `base*mult`; live re-apply in `step:`. **Edge-case-safe BY DESIGN** (owner's pizza→regular
  worry): each bike scales its OWN base at its OWN setup, so there is no stored per-bike
  state to bleed; switching bikes re-captures the new bike's base. Sliders show
  `base -> base*mult`, range **x0.1…x30** (no negative), ±0.1 fine-tune + Reset.
  NOTE: the shown stat is the captured raw spec (physics units), not the shop's 0–N bar
  count — the bar mapping isn't reversed yet; and the base reflects the LAST-driven bike
  (shop-selected-but-not-driven bikes need a shop hook). Good enough to compare/tune.
- **Tabs**: menu reorganized into **Drive** (gravity + zoom) / **Bike** (specs) / **System**
  (Debug HUD, mod-loader status, Reset Progress) via `BeginTabBar`. Opens expanded
  (`SetNextWindowCollapsed(false, FirstUseEver)`), resizable+scrollable.
- **GOTCHA:** the libgame waiter timed out (30s) on a slow launch (Google Play sign-in
  retries delayed libgame load past 30s → "gave up waiting for libgame", no hooks). Bumped
  to **120s** (2400×50ms; a sleeping thread costs nothing). libgame was already mapped — it
  just loaded late.

## NEXT (Phase 6 continued) — owner requests queued
- **Multiple ghosts** — owner wants several ghosts racing at once. PROMISING: the game
  already has multi-ghost slots — `ghost1_…ghost4_`, `ghostNode1_…4_`, `ghostSprites_`
  (plural), `setGhostSprites:`, `recordGhostSprites:`, `hideAllGhosts`, `MiniGhosts`,
  `GhostRecorder`/`loadGhostFromFile:`/`updateWithRecordedGhost:`. Deep feature (record N
  runs, load N ghost files, drive N ghost sprites) — prototype after reset-progress.
- **SHOP STATS vs PHYSICS SPECS (2026-06-14 research).** The shop shows 4 bars:
  **max speed / acceleration / handling / nitro**. These are bike-PRODUCT properties with
  their own setters: `setMaxSpeed:`@0x69e918, `setAcceleration:`@0x63f380/0x679db8,
  `setHandling:`@0x5eeb90, `setTopSpeed:`@0x5eeb00, + `statSprite{Speed,Accel,Handling,Nitro}_`
  and `updateStatBar:Number:`@0x5e7c4c / `adjustStat:`@0x5ea528 (shop class ~0x5e). They are
  SEPARATE from the gameplay PHYSICS specs the menu tunes (`speedLimit`/`forceScale`/
  `nitroPerformance`/`maxWheelieSpeed`/`burnoutSpeed` @ ~0x66e). The menu now LABELS the
  physics sliders with the shop names (inferred map: Max speed→speedLimit,
  Acceleration→forceScale, Handling→maxWheelieSpeed, Nitro→nitroPerformance, +Burnout extra)
  — affects FEEL but does NOT move the shop bars (the product stat is a different field).
  **NITRO BUG (owner-found + fixed):** `nitroPerformance` also scales the nitro UI sprite,
  so ×30 inflated it to fill the screen → nitro slider clamped to **×3**.
  TODO to make bars update + confirm the exact map: hook the product setters / find the
  product→physics conversion (where setup reads maxSpeed and calls setSpeedLimit:), and read
  the SHOP-selected bike (not just last-driven).
- "Save" persists live tunes → mod-config / `mods/` files (root-free) so they survive exit.
- Integrate the modlib inject into `apply_patches.py` + bundle `libmod.so` in `build.sh`.
- **CONFIRMED (device, 2026-06-14): levels requested as the BARE filename `1_16.dat`**
  (`<world>_<level>.dat`) via `loadLevelInfo:FileName:`@0x6e25dc (FileName = r3 NSString).
  No `unpack/` prefix, no subdir — the resource layer prepends `assets/unpack/`. So a
  mods/ override file is named exactly `<w>_<l>.dat`. THIS is the mod-loader's match key.
- Implementation reality: the REDIRECT (build mods path + fileExistsAtPath + swap the
  read/data) is complex hand-assembly. Better long-term = an injected native lib
  compiled with the Android NDK (also needed for the ImGui menu). NDK is installable
  (chaotic-aur android-ndk r29); cross-compiler not yet present.

