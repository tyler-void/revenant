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
- **`dex`** — TODO (the hard part): in-place DEX bytecode patches (tilt fix in MCAccelerometer;
  IAP unlock) that today need apktool. Must become no-recompiler byte-patches for the browser.
- **`androidManifest`** — add `HIGH_SAMPLING_RATE_SENSORS` (binary AXML edit; TODO test if
  droppable).
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
- ✅ **TILT fix DONE — in-place DEX byte-patch (no apktool) + Rust→WASM checksum.** Key insight:
  `onResume()` already calls `register()` unconditionally; `register()` only skips because of its
  internal `if-eqz isEnabled` gate. So a MINIMAL same-size variant works: NOP that gate
  (`0x43fc46` `38000b00`→`00000000`) + neuter `unregister()` (`0x43fca4` `6300b943`→`0e000000`,
  return-void) — keeping the game's ORIGINAL rotation-aware `onSensorChanged`. After byte-patching,
  the DEX Adler32 (off 8) + SHA-1 (off 12) are recomputed by **`wasm/` (Rust→WASM `dex_fixup`)** —
  verified byte-identical to a Python reference. (`web/src/wasm.ts` loads the .wasm; offsets are for
  the 1.5.2 classes.dex, verified against `expect`.)

## ✅ Rust→WASM core (`wasm/`)
- Toolchain: **rustup stable + `wasm32-unknown-unknown` target** (owner installed rustup). No
  wasm-pack/wasm-bindgen needed — **raw C-ABI** (`alloc`/`dealloc` + fns over linear memory),
  built with `cargo build --target wasm32-unknown-unknown --release` (25 KB .wasm, `sha1` crate),
  loaded in the app via `WebAssembly.instantiate`. Copied to `web/public/revenant_wasm.wasm`.
- Today: `dex_fixup` (Adler32 + SHA-1 recompute). **Next: APK v2/v3 signing in this crate** — the
  genuinely-Rust-worthy part (robust installs on modern Android; the APK Signing Block is painful
  in TS). v1 signing currently in TS (node-forge); v2/v3 → Rust→WASM.

## Remaining milestones
1. **TEST INSTALL on a modern device.** v1-only should install for this APK (targetSdk=22 →
   Android 11+ accepts v1 for pre-R targets). If rejected, the Rust→WASM v2/v3 signer closes it.
2. **APK v2/v3 signing in `wasm/`** (Rust crates: `rsa`/`sha2` + the APK Signing Block).
3. **GitHub Pages deploy** (Actions build of `web/` + `wasm/` → Pages).
4. **Refactor `apply_patches.py`** to read `patches/manifest.json` (one source for CLI + web).

Verification harnesses: `web/sign-test.ts` (v1 → jarsigner), `web/full-test.ts` (native+sign on the
real APK → jarsigner exit 0), and the Rust→WASM `dex_fixup` was checked byte-identical to Python.
