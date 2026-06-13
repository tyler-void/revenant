# Bike Rivals 1.5.2 — Reverse-Engineering Analysis

Two APKs in `base/`:
- `Bike+Rivals_1.5.2_APKPure.apk` — clean original (2015).
- `mod-bike-rivals-mod-v1-5-2-...apk` — a third-party "bikes unlocked / mod fuel" repack (2021).

Both are versionCode 31 / versionName 1.5.2, `minSdk=10`, `targetSdk=22`, `armeabi-v7a` only.

## 1. Native code is untouched — the mod is pure Java/dex

| File | original md5 | mod md5 | |
|---|---|---|---|
| `lib/armeabi-v7a/libgame.so` | `baa6906…` | `baa6906…` | **identical** |
| `lib/armeabi-v7a/libfmod.so` | `0888701…` | `0888701…` | **identical** |
| `lib/armeabi-v7a/libfmodstudio.so` | `5c5bfcc…` | `5c5bfcc…` | **identical** |
| `classes.dex` | `b18b87a…` | `ec138cd…` | **differs** |

So every change is in `classes.dex`, `AndroidManifest.xml`, and `assets/`.

## 2. Smali-level diff (deterministic)

**5 new classes:**
- `com/miniclip/bikerivals/eqkqk/{Wuvrl,Zcueh,a,b}` — **malware** (remote DEX loader).
- `com/savegame/SavesRestoringPortable` — AES save-restorer (the mod's unlock delivery).

**Patched existing classes:**
- `GameActivity` — 2 injected calls: `eqkqk/Wuvrl;->start()` in `<clinit>` (after
  `loadLibrary("fmodstudio")`), and `SavesRestoringPortable;->DoSmth(this)` at the top of
  `onCreate`.
- 18 ad-SDK classes (Flurry, AdColony, MillennialMedia, MoPub, Supersonic) — each a single
  identical edit: `Toast.show()` → `Toast.cancel()` (blanket toast suppression; cosmetic).

**Manifest:** adds the `eqkqk` `<receiver>` (BOOT_COMPLETED) + `<service>`, plus a stray
malformed `<intent-filter>`. Permissions unchanged.

**Assets:** adds `data.save` (AES-encrypted) and `extdata.save` (plain zip).

## 3. The malware (`eqkqk` package)

`Wuvrl` (service) + `Zcueh` (`BOOT_COMPLETED` receiver) implement `jatx.networkingclassloader`:
~333 s after first launch (and again on every boot), when online, it downloads
`https://dexapt.com/a/2021-05-30.dex` and runs it via `DexClassLoader` →
`jatx.networkingclassloader.dx.Module.start()`. That's a remote-code-execution channel —
whoever controls `dexapt.com` can run arbitrary code in the app. **Treat the mod APK as
hostile.**

## 4. The mod's unlock = fake-purchase save

`SavesRestoringPortable.DoSmth()` (string-obfuscated; dead `Toast.cancel()` calls spell the
modder watermark "VADIMA666") runs once on first launch, AES-128-CBC-decrypts
`assets/data.save` (key `db6378bafc42ff9a161ec584245e78b1`, iv `7b3e53f9de221d3b922a4e637f3aa739`)
and unzips it into `/data/data/com.miniclip.bikerivals/`. The payload is a captured data dir
whose `shared_prefs/OWNED_ITEMS_Google.xml` + `INAPP_PURCHASED_OWNED_EXTRAv3.xml` mark a
handful of products as owned. `extdata.save` is a plain zip for external storage.

## 5. How unlock actually works (and our clean reimplementation)

Save/progress lives in `NSUserDefaults.plist` under
`/data/data/com.miniclip.bikerivals/files/Contents/Resources/` (cocotron Foundation port).
Paid content is gated through Java `MCInAppPurchases`:

- `isItemOwned(provider,itemId)` → `OWNED_ITEMS_<provider>.xml`.getBoolean(itemId,false) — the **pull** path.
- `updateItemOwned()` → sets that pref **and** signals native `onItemOwned()` — the **push** path
  the native delegate (`MCInAppPurchasesDelegate`) unlocks on. Native is push-driven
  (`MCInAppPurchases::onItemOwned calling %@`); JNI symbol
  `Java_…_MCInAppPurchases_onItemOwned`.
- **Trap:** `GoogleWrapper.syncInventory()` loops Google's returned SKUs and, for any not a
  real purchase, calls `consumeItem()` — which *wipes* a fake `OWNED_ITEMS` entry. The save
  mod only survives because the delisted catalog returns an empty inventory.

**Our approach** (`build/apply_patches.py`): inject `resurrectUnlockAll()` into
`MCInAppPurchases.registerGoogle()` (called by the engine at startup with the full SKU list),
firing `updateItemOwned("Google", sku, false)` for every non-consumable SKU
(bikes 1-11, christmas/infernando bikes, worlds 2-4, inctank1/2, unlimitedgas). This hits
both push and pull paths, needs no Google connectivity or save file, and self-heals every
launch. No `eqkqk`, no `dexapt.com`, no `SavesRestoringPortable`.

## 6. Tilt controls

Original control scheme = lean the phone (landscape) to flip. Handler:
`com/miniclip/input/MCAccelerometer.onSensorChanged` remaps accelerometer axes by
`display.getRotation()` then calls native `onSensorChanged(x,y,z,ts)`. Native side is
branchless (negate x, negate y, ×0.1, store; timestamp discarded) — so the regression is
Java-side. Root-cause research + the on-device diagnostic plan: `docs/TILT-research.md`.
