# Bike Unlock — Status & Diagnosis

## Where it stands
- **Worlds (all 4 + Halloween + Christmas DLC): UNLOCKED** ✅ (confirmed on device).
  Native patch `isWorldUnlocked:`@0x6b0df0 + `isUniverseUnlocked:`@0x6a5094 → return YES.
- **Tilt: FIXED** ✅. **data.dat cipher: CRACKED** ✅ (key `[redacted-key]`).
- **Bikes: render as owned (full-color + "PURCHASED" stamp) but NOT yet selectable/rideable.**
  The store's SELECT-vs-"GET IT NOW" gate is not yet flipped. ⚠️ Don't tap "GET IT NOW" on a
  bike — it calls the dead purchase server and hangs (original-game behavior).

## The breakthrough (unidbg, 2026-06-13)
Couldn't observe the gate on-device (Frida crashes the 32-bit game; `/proc/mem` SELinux-blocked;
the emulator refuses ARM entirely; Ghidra decompiles this stripped Apportable binary to garbage).
**unidbg** (`tools/unidbg/`) loaded `libgame.so` in a JVM/Unicorn emulator and recovered the truth:

- **`BikeInfo` (instance_size 72) BOOL ivars:** `loading_`@0x8, **`purchased_`@0x9**, `loaded`@0xa,
  **`unlocked`@0xb**, **`_purchased`@0xc**; then `name`@0x10, `iapId`@0x18, `bikeId`@0x38,
  `_revealCount`@0x40, … (recovered + triple-confirmed).
- The `purchased` getter @0x5eed14 (which I'd patched) reads **`_purchased`@0xc**.
- **The store's owned-gate IGNORES `_purchased`@0xc** — it reads **`purchased_`@0x9 and/or
  `unlocked`@0xb**. *I was patching the wrong ivar all along.*
- Runtime is **GNUstep ObjC** (not Apple objc4): **non-fragile ivars** — accessor offsets are
  loaded from per-class variables that are 0 statically and filled at runtime. This is exactly
  why blind static getter/setter patches (and the earlier flat-layout memory walk) couldn't
  reach the gate, and why dynamic emulation was required.

## What was tried (and why each missed)
isBikeUnlocked→YES (moved the reveal-counter, not the button); `purchased`/`revealed`/`isRevealed`
getters→YES (changed the stamp, read `_purchased`@0xc not the gate); `locked`→NO; setter-forces
(loader sets ivars directly, not via these setters); `selectCurrentBike` NOP; `disableSelectButton`
redirect; `buyUnlock1/2` NOP. All 8 missed because none touched `purchased_`@0x9 / `unlocked`@0xb.

## Path to the fix (precise now)
Force **`purchased_`@0x9 AND `unlocked`@0xb = 1** on every BikeInfo:
1. **Patch a getter** that returns 0x9 or 0xb to `mov r0,#1;bx lr` — IF the store reads via such a
   method (unidbg agent is mapping getters→ivars).
2. **Patch the loader's set-site** for 0x9/0xb (needs the load traced; offsets are fixed constants
   0x9/0xb so a `strb rT,[obj,#9]`/`[obj,#0xb]` patch is valid).
3. **1-minute device confirmation (morning, with GameGuardian):** find a locked bike's BikeInfo in
   live memory and flip byte +0x9 and +0xb to 1 → if the store flips to SELECT, the gate is proven,
   then bake the matching static patch.
Also neutralize the buy-hang: `buyProductWithCoins`@0x5dc25c.

## Repro (unidbg)
`tools/unidbg/` — Maven project. `JAVA_HOME=/usr/lib/jvm/java-17-openjdk`, unidbg-android **0.9.8**
(0.9.9 has a broken unicorn native), `-Djava.library.path=$PWD/natives`. See `tools/unidbg/FINDINGS.md`.
