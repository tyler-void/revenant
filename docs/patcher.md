# Phase 8 — browser one-click APK patcher (`web/`)

> The durable record for the in-browser patcher. Goal: a static site where a user drops their
> OWN Bike Rivals 1.5.2 APK and gets back a patched, signed, ready-to-install APK — entirely
> in their browser (RAM). BYO-original (ship methods/offsets only). Serves the overarching
> goal: get the game into other people's hands ASAP.

## Stack
- **React + Vite + TypeScript** in `web/`. Build/run with **Bun** (`/usr/bin/bun`, v1.3.14) —
  avoids the nvm lazy-loader aliases that break `node`/`npm` in non-interactive shells.
  `bun install` then `bun run dev` / `bun run build` (output → `web/dist/`).
- `@vitejs/plugin-react`, `jszip` for APK (zip) read/write. `vite.config.ts` `base` defaults to
  `/revenant/` for GitHub Pages project sites (override with `VITE_BASE`).
- Logo: `web/public/logo.png` (the BIKE RIVALS REVENANT logo; the root source PNG is gitignored
  by `/*.png`). Manifest: `web/public/manifest.json` is COPIED from `patches/manifest.json` by
  the `predev`/`prebuild` script (single source of truth; the copy is gitignored).

## The shared patch manifest — `patches/manifest.json`
One declarative file feeds BOTH the CLI (`build/apply_patches.py`, TODO: refactor to read it) and
the browser app. Sections:
- **`native`** — in-place ARM byte-patches on `lib/armeabi-v7a/libgame.so`: `{name, off, expect,
  patch, group, desc}`. Each is verified against `expect` before writing (wrong/non-1.5.2 libgame
  → skip + warn, never corrupt). Groups: `unlock` / `fuel` / `nitro`. **DONE in the web app** —
  this is the easy, high-value core (all-unlocked + unlimited fuel/nitro).
- **`dex.tilt`** — ✅ DONE. The MCAccelerometer tilt fix, applied **atomically** by the Rust→WASM
  `dex_tilt_rewrite` (a real DEX **code-item rewrite**, not a byte-patch — see "Rust→WASM core").
- **`androidManifest`** — ✅ DONE. In-browser **binary-AXML editing** (`web/src/axml.ts`,
  `stripManifest`): drops tracking `<uses-permission>` elements AND tracking/push/ad `<service>`/
  `<receiver>`/`<permission>` components. We do NOT add `HIGH_SAMPLING_RATE_SENSORS` — `register()`
  uses `SENSOR_DELAY_GAME` (~50 Hz, below the 200 Hz gate), so it isn't needed (device-confirmed).
- **`addFiles`** — optional libmod.so mod-menu inject (deferred; not needed to "just play").

## Status (2026-06-14)
- ✅ React+Vite app builds; native byte-patches applied in-browser via JSZip; repackages the APK.
  UI: BYO disclaimer, APK picker, per-group toggles, live log, download.
- ✅ **v1 (JAR) signing DONE + verified** (`web/src/sign.ts`): WebCrypto SHA-256 digests →
  `META-INF/MANIFEST.MF` → `CERT.SF` (manifest + per-section + main-attrs digests) → node-forge
  PKCS#7 detached SignedData → `CERT.RSA`. Self-signed debug key (`CN=Revenant Debug`) generated
  once and cached in `localStorage` (same signer across re-patches → `install -r` updates work).
  **VERIFIED end-to-end on the real `Bike+Rivals_1.5.2_APKPure.apk`**: 14/14 native patches
  matched + applied, 957 entries signed, `jarsigner -verify` exit 0 (`web/sign-test.ts` +
  `web/full-test.ts` are the dev harnesses; run with `bun run sign-test.ts`).
- ✅ **TILT fix DONE — full DEX code-item rewrite in Rust→WASM (no apktool), device-verified.**
  The minimal same-size byte-patch (NOP the `register()` `isEnabled` gate + neuter `unregister()`)
  CRASHES: with the sensor force-registered, the game's `onSensorChanged(SensorEvent)` forwards to
  the **native** `onSensorChanged(FFFJ)` before `libgame.so` binds it → `UnsatisfiedLinkError`. The
  proper fix needs the native call wrapped in try/catch, plus the modern-landscape axis remap — i.e.
  a **method-body REWRITE** (registers 6→7, different insns), which can't be a same-size patch.
  `dex_tilt_rewrite` (wasm/src/lib.rs) does it atomically:
  1. Resolves the 5 field/method refs (`SensorEvent.sensor/values/timestamp`, `Sensor.getType`,
     native `onSensorChanged(FFFJ)`) by descriptor — all already in the 1.5.2 pool.
  2. Builds a NEW `code_item` (landscape map: gameX=sensorY, gameY=−sensorX, gameZ=sensorZ; native
     call bracketed by a **catch-all** handler — no new type_id needed) and **appends** it at the end
     of the code section, repointing the method's `code_off` (uleb, same width) to it.
  3. Shifts every u32 file-offset ≥ the insertion point by +K (string_data_off[], proto params,
     class_def offsets, code_item debug_off[] over all 36 976 items, annotation dirs/sets/refs, map
     offsets) and bumps the map's `TYPE_CODE_ITEM` count; the old code_item is left as harmless dead
     data. Plus the two same-size byte-patches (register/unregister) and a final Adler32+SHA-1.
  - If the dex isn't the patchable 1.5.2 layout (descriptor/`expect` checks fail) it returns 0 and
    classes.dex is left **untouched** — never half-patched (a half fix is the crashing config).
  - **Verified**: the Rust output is **byte-identical** to a Python oracle; the oracle was confirmed
    by **baksmali (dexlib2, strict parser)** showing the correct smali for all three methods; the
    final APK's `classes.dex` re-baksmali's cleanly and `jarsigner -verify` passes; and the
    browser-built APK was **installed on Android 13 and tilt-steered in-game** (only "Motion"/sensor
    access granted).
- ✅ **PRIVACY cull DONE — in-browser binary-AXML editing (`web/src/axml.ts`).** Splices out
  `<uses-permission>` (location/accounts/billing/push) and tracking **components** (GCM
  service+receiver+permission = MIUI's "Send MMS", AdX ad tracker, OpenUDID device-ID, BOOT receiver)
  by removing each element's START→matching-END chunk span and decrementing the root chunk size — no
  other fixups (AXML chunks reference the pool by index and each other by order). Result verified with
  **aapt2 dump badging** (7 benign perms remain; trackers gone). NOTE: MIUI's first-launch review also
  lists generic AppOps (clipboard, installed-apps, background-windows) that are NOT manifest-backed
  for a legacy targetSdk-22 app and can't be removed without a risky targetSdk bump; they default OFF.
- 📱 **End-user note (in the patcher UI + below):** after install, on first launch grant **only
  "Motion"/sensor access** (for tilt) and leave every other toggle OFF — the game is fully offline.

## ✅ Rust→WASM core (`wasm/`)
- Toolchain: **rustup stable + `wasm32-unknown-unknown` target** (owner installed rustup). No
  wasm-pack/wasm-bindgen needed — **raw C-ABI** (`alloc`/`dealloc` + fns over linear memory),
  built with `cargo build --target wasm32-unknown-unknown --release` (25 KB .wasm, `sha1` crate),
  loaded in the app via `WebAssembly.instantiate`. Copied to `web/public/revenant_wasm.wasm`.
- Today: `dex_fixup` (Adler32 + SHA-1 recompute) **and `dex_tilt_rewrite`** (the full code-item
  rewrite above). **Next (optional): APK v2/v3 signing in this crate** — robust installs on modern
  Android; the APK Signing Block is painful in TS. v1 signing currently in TS (node-forge) and it
  already installs on Android 13, so v2/v3 is a nice-to-have, not a blocker.

## Remaining milestones
1. ✅ ~~TEST INSTALL on a modern device~~ — done: browser-built APK installs on Android 13, boots,
   and tilt-steers in-game (only "Motion"/sensor access granted).
2. **GitHub Pages deploy** (Actions build of `web/` + `wasm/` → Pages).
3. **Refactor `apply_patches.py`** to read `patches/manifest.json` (one source for CLI + web).
4. (Optional) **APK v2/v3 signing in `wasm/`** (Rust `rsa`/`sha2` + the APK Signing Block) — v1
   already installs on Android 13, so this is hardening, not required.

Verification harnesses: `web/sign-test.ts` (v1 → jarsigner), `web/full-test.ts` (native+sign on the
real APK → jarsigner exit 0), and the Rust→WASM `dex_fixup` was checked byte-identical to Python.
