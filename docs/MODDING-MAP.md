# Bike Rivals 1.5.2 — Modding Map ("what to tinker with when bored")

Engine: **cocos2d-iphone (plist v1.6.0) via Apportable/cocotron**, **Box2D** physics,
**CocosBuilder** UI, **FMOD Designer/Ex** audio. Native logic in 32-bit `libgame.so`.

**Key insight:** most fun-to-mod things are NOT in `libgame.so` or the encrypted
`data.dat` — they're **plaintext binary-plist assets** in `build/work/assets/unpack/`.
Edit with `plistutil -i in.plist -o out.xml` → edit → `plistutil -i out.xml -o in.plist`
→ rebuild via `build/build.sh`. No Ghidra needed for these.

## 1. Bike physics / handling — EASY ⭐ (best fun-per-effort)
Per-bike `assets/unpack/<Bike>Pref.plist` (21 bikes: `MainBikePref`, `GasBikePref`,
`SuperDukePref`, `MonoCyclePref`, `HarleyBikePref`, `MX1-5Pref`, DLC bikes…).
`Entities[0].Properties` = tuning block; other entities = Box2D bodies/motors/joints.

Top-level knobs: `speedLimit` (127→200), `forceScale` (engine power, 1.0→2.5),
`nitroPerformance`, `geyserPower`, `burnoutSpeed`, `maxWheelieSpeed`, `tilt`
(lean/flip sensitivity 0.4→1.3), `anchorY` (center of mass).
Deeper: per-body `density`(=mass), `friction`, `restitution`, motor `maxForce`/`speed`,
joint `damping`. Change one float, rebuild, ride. → Make the Pizza bike a rocket.

## 2. Bike skins / sprites — EASY-MEDIUM
`assets/unpack/<Bike>.plist` (binary plist, format 3) + `<Bike>.png` (**actually WebP** —
`dwebp x.png -o x_real.png`). Frames are body parts (torso/wheel/quadro/helmet…).
- Recolor in-place (keep frame rects): edit the WebP texture, no repack → EASY.
- Reshape: unpack (texture-unpacker / Sprite-Extractor-On-The-Go), edit, **repack with
  TexturePacker (cocos2d-x data format → format-3 plist+png) or Zwoptex**. Keep frame
  names identical, `premultipliedAlpha=false`, same atlas size.

## 3. FMOD audio — MEDIUM/HARD
`assets/*.bank` are **FMOD Designer/Ex** (header `RIFF…FEV…PROJBNKI`, samples FSB2/3/4),
NOT FMOD Studio (so Fmod5Sharp/python-fsb5 won't work).
- Extract/listen: **FSB Extractor (aezay)** (supports FSB3/4) → EASY.
- Replace one sound by **equal-length splice** → MEDIUM.
- Rebuild bank from scratch → HARD (needs dead FMOD Designer toolchain).

## 4. UI / text — MEDIUM
- Android `res/values-*/strings.xml`: EASY but low impact (most in-game text is drawn
  from `.ccbi`, not Android resources).
- `.ccbi` CocosBuilder layouts (91 files, magic `ibcc`): decompile with **ccbi2ccb** →
  edit in CocosBuilder → re-export. Hand-editing the binary is HARD (gamma-encoded ints +
  string cache). Same-length string swaps in place = OK.

## 5. Gravity / other — mixed
- **Gravity zones** (`assets/unpack/TriggerGravity.plist`, `TriggerNewGravity.plist`):
  `speed`/`acceleration`/`radius`/`angle` floats → moon-gravity levels. EASY.
- **Global gravity**, input→torque curve: hardcoded in `libgame.so` → HARD.
- **Economy/rewards/fuel-regen/prices**: `libgame.so` + encrypted `data.dat` → HARD
  (same track as the unlock work; see BIKE-UNLOCK-STATUS.md).

## Ranked "bored afternoon" targets
1. Bike handling (`<Bike>Pref.plist`) — easiest + most fun.
2. Gravity zones (`TriggerGravity.plist`) — moon levels.
3. Bike recolor in-place — easy.
4. New bike art (unpack→repack) — medium.
5. Sound swaps (FSB Extractor + splice) — medium.
6. UI text/layout (ccbi2ccb→CocosBuilder) — medium.
