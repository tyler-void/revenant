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
- rotation `currentRotation_`/`displayRotation_` · speed `getSpeed`/`linearVelocity_`/
  `headLinearVelocity_` · current bike `currentBikeIndex_`/`getBikeDisplayName:` ·
  air time `airTime_` · flips (`BACKFLIP! x%d`/`FRONTFLIP!`) · FPS via cocos2d
  `_FPSLabel`/`displayStats` · system RAM/CPU (`/proc/self/statm`, `/proc/stat`).

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
