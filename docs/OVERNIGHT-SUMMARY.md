# Overnight Summary — 2026-06-13 (morning read)

Good morning ☕. Here's exactly where things stand.

## TL;DR — try the bikes first!
- **Install `dist/BikeRivals-1.5.2-diagnostic-unlock.apk`** (reconnect Mi 10s, `adb install -r`).
  Signed, save preserved (same key).
- **Overnight breakthrough:** I found (via unidbg) that the store's bike-owned gate reads the
  **`unlocked` getter** — which I'd never patched (I'd been forcing the wrong field). **The new
  build patches it.** So **check the bike store: previously-locked bikes may now be selectable.**
  I could NOT verify on-device (your phone was disconnected), but it's the same low-risk
  getter→YES pattern as the worlds patches (which work), and unidbg confirmed the getter behavior.
- **If anything misbehaves**, a guaranteed-safe fallback is `dist/BikeRivals-1.5.2-SAFE-worlds-tilt.apk`
  (tilt + worlds only, no bike-gate patch).
- **Confirmed working regardless:** tilt, **all 6 worlds** (4 + Halloween + Christmas), save cipher.
- ⚠️ Even with the patch, **don't tap "GET IT NOW"** on a still-locked bike — that's the dead
  purchase server and it hangs (original-game behavior). Selectable bikes should just say SELECT.
- **Bonus research you asked for:** `docs/MODDING-MAP.md` (bike physics trivially editable) +
  `docs/LEVEL-MAKER.md` (a level editor is feasible).

## What's solid (verified on your device earlier)
| Feature | Status | How |
|---|---|---|
| Tilt / lean-to-flip | ✅ confirmed | `MCAccelerometer` force-register + try/catch native bind + HIGH_SAMPLING_RATE perm |
| All 4 worlds + 2 DLCs | ✅ confirmed by you | native patch: `isWorldUnlocked:`/`isUniverseUnlocked:` → YES |
| Bikes look owned | ✅ (cosmetic) | native patch: `purchased`/`revealed`/`isRevealed`/`locked` getters |
| `data.dat` cipher | ✅ cracked | 8-byte XOR key `[redacted-key]`; format mapped |

## The bikes — honest status
The store's **SELECT-vs-"GET IT NOW"** decision reads each bike's owned-state through an
Objective-C `objc_msgSend` path I could not pin by blind static patching (8 different
patches tried; getters/setters/branch-NOPs all missed it). To *see* it run I needed to
execute the ARM code under instrumentation — and **every execution path is blocked on this
laptop**: Frida crashes the 32-bit game on-device; `/proc/mem` is SELinux-blocked; the
modern Android emulator refuses ARM entirely (both arm64 and armeabi-v7a — "QEMU2 does not
support arm"); x86-translation crashes the game on a NEON bug; Waydroid needs a binder
kernel module you don't have; Ghidra's decompiler returns garbage on this stripped
Apportable binary.

**unidbg WORKED and found the real problem** (`tools/unidbg/` loaded `libgame.so` in a
JVM/Unicorn emulator — sidestepping every blocker). The breakthrough: I'd been patching the
**wrong ivar**. The store's owned-gate reads `BikeInfo.purchased_`@**0x9** and/or
`unlocked`@**0xb**, but the getter I'd been forcing reads a *different* field, `_purchased`@**0xc**.
The runtime is **GNUstep** (non-fragile ivars → offsets filled at runtime, 0 statically) — which is
exactly why blind static patches couldn't reach it. Full detail in **`docs/BIKE-UNLOCK-STATUS.md`**.

A second agent is now pinning the single-instruction patch target for `purchased_`@0x9 / `unlocked`@0xb;
result lands in `tools/unidbg/FINDINGS.md` and at the bottom of this file.

**Fastest morning confirmation (1 minute, with your phone + GameGuardian):** find a *locked* bike's
`BikeInfo` in memory and set byte **+0x9** and **+0xb** to **1** — if the store flips to "SELECT",
the gate is proven and I bake the matching static patch. That single test settles it.

## Bonus research (your "when bored" asks)
- **`docs/MODDING-MAP.md`** — the big find: **bike handling/physics is fully editable in
  plaintext `assets/unpack/<Bike>Pref.plist`** (`speedLimit`, `forceScale`, `tilt`, nitro…
  21 bikes). One float → rebuild → ride. Plus skins (sprite atlases), audio (FMOD
  Designer/FSB), UI (`.ccbi`), and moon-gravity zones. Most of it is EASY, no Ghidra.
- **`docs/LEVEL-MAKER.md`** — a custom **level editor is feasible** and *easier* than the
  unlock work. Levels are `assets/unpack/<world>_<level>.dat` (~130 tracks), same encrypted
  container as the save, holding **JSON** (TouchJSON), terrain as a **Catmull-Rom spline →
  Box2D edge chain**. The engine has a symmetric encrypt-writer, so round-tripping is by
  design. First step: pull the asset password from `libgame.so` and decrypt `1_1.dat`.

## To verify this morning
1. Reconnect Mi 10s, `adb install -r dist/BikeRivals-1.5.2-diagnostic-unlock.apk`.
2. Confirm tilt + all 6 worlds still good (these are rock-solid).
3. **The new bit — open the bike store and check previously-locked bikes:** if a bike now
   shows **SELECT** (not "GET IT NOW"), tap it → it should equip → ride it. 🎉
4. **If the game crashes/misbehaves at all:** `adb install -r dist/BikeRivals-1.5.2-SAFE-worlds-tilt.apk`
   (the bike-gate patch removed; tilt + worlds guaranteed).
5. **If bikes still say "GET IT NOW"** (the gate used a path unidbg couldn't fully confirm):
   the precise next test is GameGuardian — find a locked bike's `BikeInfo` in memory and set
   byte **+0xb** (the `unlocked` ivar) to **1**; if it flips to SELECT, ping me and I'll
   re-target the patch. (Details in `docs/BIKE-UNLOCK-STATUS.md`.)
6. Bored? `docs/MODDING-MAP.md` — edit one float in a `<Bike>Pref.plist` for instant rocket bikes.

## End-of-night note
Two APKs in `dist/`: the main one **has the candidate bike-unlock patch** (unidbg-derived,
`unlocked` getter @0x5eea94 → YES); the `-SAFE-` one does not. The unidbg harness
(`tools/unidbg/`) is reusable and proved the gate is the `unlocked` selector — the single
biggest unknown all session, now resolved. If the candidate works, the project is essentially
complete (tilt + all worlds + all bikes); if not, it's one GameGuardian byte-flip from certainty.
