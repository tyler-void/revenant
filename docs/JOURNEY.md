# 📖 The Journey — how a dead game came back

> The README is the highlight reel. This is the director's cut: every dead end, the overnight siege,
> and why the bikes were a genuinely vicious final boss. Roughly **two days** (2026-06-11 → 06-13),
> including one **overnight autonomous run** while the human slept and the phone sat disconnected on a
> desk (OLED burn-in paranoia). Dozens of build-test cycles, ~6 research agents, one JVM emulator that
> saved the whole thing.

```
 11 Jun  ──●  Triage & malware autopsy
            │
 11–12 Jun ──●  Tilt fix (confirmed: "wtf I can lean now, yoooo")
            │
 12 Jun    ──●  Save-cipher rabbit hole → key cracked
            │
 12 Jun    ──●  Unlock war: 8 native patches, all wrong
            │
 12 Jun    ──●  Tooling apocalypse: every dynamic tool refuses
            │       (Frida ✗  /proc/mem ✗  x86-emu ✗  arm-emu ✗  Waydroid ✗  Ghidra ✗)
            │
 12–13 Jun ──●  🌙 OVERNIGHT: unidbg cracks the diagnosis
            │
 13 Jun AM ──●  Bikes ✓  Fuel ✓  Nitro ✓  Helmets ✓  →  RESURRECTED
```

---

## Act I — The Autopsy 🦠

Step one wasn't the game; it was the *"mods"* everyone shares for it. Diff the original APK against the
"modded" ones and a beautiful shortcut appears: **all three native `.so` files are byte-identical.**
The entire "mod" lives in `classes.dex`. Decompile it and there it is — `com.miniclip.bikerivals.eqkqk`,
a `BOOT_COMPLETED` receiver + background service that, ~333 seconds after launch, downloads and executes
`https://dexapt.com/a/2021-05-30.dex`. **Remote code execution dressed up as a free unlock.** Its actual
"unlock" was a fake-purchase save restored by a bundled `SavesRestoringPortable`.

Verdict: the street cure is poison. We build our own from the clean original. *(Full writeup:
[`ANALYSIS.md`](ANALYSIS.md).)*

## Act II — Teaching It to Lean Again 🤸

The lean-to-flip controls were dead on Android 13/HyperOS. `adb dumpsys sensorservice` told the story:
during a race, the accelerometer (handle `0x37`) **never registers**. Two compounding causes: the game
never calls `setEnabled(true)`, and Android 12+ blocks accelerometer registration without
`HIGH_SAMPLING_RATE_SENSORS`. The fix force-registers the sensor, neuters the unregister, drops the
`isEnabled` gate, and — the load-bearing bit — wraps the native callback in `try/catch
UnsatisfiedLinkError`, because the force-registered sensor fires *before* `libgame.so` binds the native
method. Built it, shipped it, and got the best bug report of the project:

> **"wtf I can lean now, yoooooooooooooooo"**

## Act III — The Save-Cipher Rabbit Hole 🔐

To unlock content, surely we just edit the save? The save (`data.dat`) is encrypted. Days of the
project evaporated here. Every "known-plaintext" handle failed: coins are server-gated and don't even
persist; lap times aren't stored as any float/int/centisecond encoding; nitro `5→4` isn't a findable
int; the "cumulative" records are an interned hash-consed object graph that **re-orders itself on every
save**, so byte-diffing two saves is useless.

The break came from a **column-mode attack**: the decrypted body is ~46% zero bytes, so the most-common
byte in each of the 8 columns *is* the key byte. Out popped the 8-byte key — and decrypting with
it yields exactly 45.9% zeros, proving it's an 8-byte repeating XOR (not XXTEA). We cracked the save…
and then discovered the unlock keys aren't hashes of *any* string (brute-forced **71,741** binary
strings × FNV/Murmur/CRC64/djb2/CFString → **0 hits**). The save was a dead end for the bikes. The real
gate was in native code.

## Act IV — The Unlock War (8 wrong patches) ⚔️

The native store-check is Objective-C. We mapped 7,105 selector→IMP pairs from the binary and started
forcing things to `YES`. And missed. **Eight times.**

`isBikeUnlocked:` (moved a counter, not the button) · the `purchased` / `revealed` / `isRevealed`
getters (changed the *stamp*, not the button) · `locked` → NO · forcing the setters (the loader writes
ivars directly) · NOP-ing `selectCurrentBike` · redirecting `disableSelectButton`. Every patch was
*reasonable* and every patch was wrong, because the runtime is **Apportable/cocotron GNUstep** with
**non-fragile ivars**: the getter offsets are filled in at runtime, so a static patch reads a phantom.
To find the real gate, we had to **run the runtime**. Which meant dynamic analysis. Which meant…

## Act V — The Tooling Apocalypse 💀

Every way to execute or inspect this ARM code, in order, refused:

| Tool | Outcome |
|---|---|
| **Frida** (inject) | 💀 crashes the 32-bit game process on this device |
| **`/proc/<pid>/mem`** | 💀 SELinux-blocked; `setenforce 0` denied |
| **x86 emulator + ARM translation** | 💀 game runs… then `SIGFPE` in `libndk_translation` on a NEON `VADD` |
| **Native arm64 emulator** | 💀 `FATAL: QEMU2 emulator does not support arm64` |
| **Native armeabi-v7a emulator** | 💀 `CPU Architecture 'arm' is not supported` |
| **Waydroid** | 💀 host kernel has no `CONFIG_ANDROID_BINDER_IPC` |
| **Ghidra (headless)** | 💀 decompiles this stripped Apportable ARM to garbage |

Six tools. Zero cooperation. This is where most projects die.

## Act VI — 🌙 The Overnight Breakthrough: unidbg

The insight that saved everything: **don't emulate the *device* — emulate the *library*.**
[**unidbg**](https://github.com/zhkl0228/unidbg) loads a single Android `.so` into a JVM on top of
Unicorn Engine and lets you *call its functions directly* — no device, no SELinux, no NEON translator,
no Frida injection. Every blocker, sidestepped at once.

Overnight, while the human slept, a chain of agents stood up unidbg (`for32Bit()`, `AndroidResolver`,
pin `unidbg-android 0.9.8` — `0.9.9` ships a broken Unicorn native), loaded `libgame.so` cleanly, and
walked the live GNUstep ObjC runtime. It recovered the `BikeInfo` ivar layout — `purchased_`@`0x9`,
`unlocked`@`0xb`, `_purchased`@`0xc` — and proved, by toggling each on a synthetic instance, that the
store's owned-check reads the **`unlocked` getter** (`@0x5eea94`, ivar `0xb`). **The field I'd patched
eight times (`_purchased`@`0xc`) was a decoy the gate never reads.** One patch later — `unlocked` getter
→ `YES` — the human woke to:

> **"lol all bike are unlocked"**

*(Full diagnosis: [`BIKE-UNLOCK-STATUS.md`](BIKE-UNLOCK-STATUS.md).)*

## Act VII — The Morning Mop-Up (and one more lesson) ☀️

- **Worlds:** `isWorldUnlocked:` / `isUniverseUnlocked:` → YES. All 4 + Halloween + Christmas.
- **Fuel — the humbling one.** First patch NOP'd `useFuel:` and *looked* perfect in my quit-test. The
  human played level 4 and came back with one bar. Wrong consume path. The robust fix doesn't chase
  consume paths at all: **redirect the `gasBarsLeft` getter → `gasBarsTotal`** so the tank *reports*
  full no matter what drains the stored value. Proof: the gauge reads full even with the regen timer
  still ticking on a near-empty save.
- **Nitro + Helmets:** one generic consumable manager — `consumableCount:` → 99, `useConsumable:` →
  succeed-without-spending. The store now shows 99 of each, forever.

---

## 🧮 Scoreboard

**What we threw at it:** 71,741 strings brute-forced · 7,105 ObjC methods mapped · 8 wrong bike patches ·
6 dead dynamic-analysis tools · ~6 research agents · dozens of build→install→screenshot cycles · 1 JVM
emulator that cracked it · **13 surgical byte-patches** in the final build.

**What stuck (the final 13 patches):** worlds ×2, bike gate + display ×6, fuel ×2, nitro/helmets ×2,
plus the tilt smali fix. Each one asserts the original bytes before cutting.

## 🎓 Lessons

1. **Emulate the library, not the device.** When Frida, `/proc/mem`, every Android emulator, and
   Waydroid all fail, `unidbg` runs the one `.so` you care about in a JVM. It was the whole ballgame.
2. **GNUstep non-fragile ivars make static patching lie to you.** Runtime-resolved offsets mean the
   "obvious" getter reads a phantom field. You have to execute the runtime to see the truth.
3. **Always verify on real hardware.** The fuel patch that passed my synthetic test failed the user's
   first real level. My quit-test and their play-test hit different consume paths.
4. **Prefer the robust hack to the clever one.** `gasBarsLeft → gasBarsTotal` (one branch) beats
   hunting "the" consume function — it's immune to paths you haven't found.
5. **Abandonware revival is a research problem, not a piracy one** — and the cleanest mod is the one
   that *removes* malware instead of adding it.

> From toe-tag to throttle. 🏍️
