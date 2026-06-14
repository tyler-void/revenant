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

## Spike findings
*(appended as the data-layer RE proceeds — see the "applyGravity:" + bike/world accessor
investigation.)*
