# Tilt Controls — Root-Cause Research (multi-agent + adversarial verification)

The cited source confirms the decompiled handler exactly as described in the research. I have everything needed.

# Bike Rivals v1.5.2 â Broken Tilt on Modern Android: Patch-Oriented Technical Report

## Confirmed code facts (load-bearing)
The decompiled handler at `/home/thai/Documents/Projects/resurrect/decompiled/original-java/com/miniclip/input/MCAccelerometer.java` does exactly this on every accelerometer event (lines 67-85):

- Line 70: re-reads `display.getRotation()` **fresh per event** (no staleness).
- Branch table (raw `event.values[0]=x`, `[1]=y`, `[2]=z` â native call):
  - `rotation==0` â `native(-x,  y, z)`
  - `rotation==2` â `native( x, -y, z)`
  - `rotation==1` â `native( y,  x, z)`
  - `rotation==3` â `native(-y, -x, z)`
- Registered at rate `1` = `SENSOR_DELAY_GAME` (~50 Hz), line 55.
- Native side (disassembled `lib/armeabi-v7a/libgame.so`, `Java_..._onSensorChanged @0x70df48`): branchless â negate x, negate y, multiply x/y/z by 0.1 (m/sÂ² â ~g scaling), store to a global via `CoreMotion_setAccelerometerData @0x70531c`. Timestamp arg is discarded. No version/OEM/arch branch.

Critical asymmetry: branch `1` is `native(y, x, z)` and branch `3` is `native(-y, -x, z)` â a **full sign inversion** of each other. If a phyiscal pose that the original tuning expected to be rotation `1` is reported as rotation `3` (or vice-versa), front/back lean â and therefore front/back flips â is inverted. This is the only internal fragility in an otherwise self-consistent table.

---

## 1. Ranked root causes

### A. getRotation() lands on the *opposite* landscape branch (1 vs 3) than the original iOS tuning assumed â or getRotation() is effectively pinned to one constant under the locked, configChanges-absorbing activity, so the remap mismatches the device's actual sensor frame. CONFIDENCE: MEDIUM (most likely of the surviving hypotheses)
- The four documented "throttling / stale-ROTATION_0 / reverse-landscape-enum / native-bug" hypotheses were all **adversarially refuted** (verdicts: refuted Ã4). What survives is the residual: the handler's correctness depends entirely on `getRotation()` returning the value the 2015 iOS-CoreMotion tuning expected, and that coupling is the documented weak point.
- `getRotation()` reports display offset from the device's **natural** orientation, not physical tilt; it does not track how the player leans the phone, and under a landscape-locked activity it is pinned to one constant (1 or 3). The remap selects from a value that no longer means what it did. Sources: https://developer.android.com/media/camera/camerax/orientation-rotation , https://android-developers.googleblog.com/2010/09/one-screen-turn-deserves-another.html
- The 1-vs-3 branches being exact sign inversions makes a wrong landscape pick produce precisely the reported "inverted / can't do flips" symptom. Source (canonical remap the table is derived from): https://github.com/googlearchive/android-AccelerometerPlay/blob/master/app/src/main/java/com/example/android/accelerometerplay/AccelerometerPlayActivity.java
- HONESTY FLAG: The specific mechanism "plain `landscape` activity on a portrait-natural Mi 10s/Pixel 3 XL reports value 3 instead of 1" was **refuted** by AOSP `DisplayRotation` analysis (plain `landscape` â `mLandscapeRotation` = ROTATION_90 by default; reverse only via `config_reverseDefaultRotation`, default false, not documented for these phones). So while this family is the most plausible, *we cannot currently name a verified mechanism that makes it happen on these two phones*. This must be settled by on-device logging (Section 3), not asserted.

### B. iOS/CoreMotion gravity sign / handedness convention vs. how the modern Android sensor stack delivers the vector. CONFIDENCE: LOWâMEDIUM
- The native layer bakes in unconditional negate-x/negate-y plus a 0.1 scale â a fixed iOS handedness convention. Android HAL normalizes axis semantics across OEMs, so this is constant, *but* it is also the one place where a sign assumption is hard-coded; if combined with a borderline rotation pick it could compound. Source: https://developer.android.com/develop/sensors-and-location/sensors/sensors_overview
- HONESTY FLAG: No evidence that this convention *changed* on modern Android; it is constant. It only matters as something to compensate from Java, not as a thing that "newly broke." Cannot be the sole cause of a regression.

### C. No-user-report void. CONFIDENCE: HIGH that reports are absent, not that the bug is absent.
- Bike Rivals is discontinued; Play Store review corpus and Miniclip support are gone. The exact symptom (dead vs inverted vs wrong-axis) is **not retrievable from public sources** and must be observed on-device. Sources: https://support.miniclip.com/hc/en-us/articles/360019766958-Bike-Rivals (403), https://bike-rivals.en.uptodown.com/android

### Refuted (do not pursue)
- Reverse-landscape enum flip to ROTATION_3 on these phones â REFUTED (plain `landscape` enum; AOSP `DisplayRotation` excludes LANDSCAPE from the sensor branch). https://github.com/aosp-mirror/platform_frameworks_base/blob/master/services/core/java/com/android/server/wm/DisplayRotation.java
- Android 12 200 Hz sampling cap / `HIGH_SAMPLING_RATE_SENSORS` â REFUTED (targetSdk=22 exempt; 50 Hz << 200 Hz ceiling; symptom is inversion not lag). https://developer.android.com/about/versions/12/behavior-changes-12
- Stale/ROTATION_0 in landscape â REFUTED (portrait-natural phone in landscape reports 90/270, never 0; the cited Expo #2430 is JS DeviceMotion, not native `getRotation()`).
- Native/.so/32-bit/timestamp â REFUTED (native transform is a branchless constant; game runs, so the .so loaded; timestamp discarded).

---

## 2. The fix

### The fix MUST be empirically chosen on-device. Do Section 3 first. Do not guess a branch blind.

Because every clean mechanistic hypothesis was refuted, you cannot derive the correct signs from theory â you must read the actual `getRotation()` value and the actual sign of the lean axis on the Mi 10s, then patch the matching branch in smali.

### Most likely fix (pending diagnostic confirmation)
Once logging shows which rotation value the Mi 10s reports in gameplay (expected: `1`, possibly `3`) **and** whether the resulting front/back lean sign is inverted relative to flip direction:

- If the device reports the **expected** value but lean is **inverted**: flip the sign of the lean-controlling axis in that one branch. In landscape the bike's front/back lean is the `y`-derived term; e.g. for the `rotation==1` branch `native(event.values[1], event.values[0], â¦)`, invert the first argument â `native(-event.values[1], event.values[0], â¦)` (or the second, depending on which axis the diagnostic shows drives flips). Patch **only the branch that actually executes** on-device.
- If the device reports the **unexpected** landscape value (e.g. `3` where tuning wanted `1`): copy the working branch's signs into the executing branch so both landscape branches yield identical, correct behavior. Concretely, make the `rotation==3` body equal the (verified-correct) `rotation==1` body, or vice-versa.

### Fallbacks (in order)
1. **Collapse both landscape branches to the empirically-correct one.** Since a phone is physically held in essentially one landscape pose during play, make `rotation==1` and `rotation==3` produce the same mapping that tested correct. This removes the 1-vs-3 sign-inversion trap entirely. Lowest-risk smali edit.
2. **User-facing invert toggle.** The community-standard mitigation for exactly this class of old Cocos2d-x tilt game. Add a preference that conditionally negates the lean axis, so users self-correct any residual per-device sign. Sources: https://forum.gideros.rocks/discussion/4244/inverted-accelerometer-on-android , https://xdaforums.com/t/gyroscope-accelerometer-inverted.3435572/
3. **Hardcode the rotation used for remap** to the value the Mi 10s/Pixel report, bypassing live `getRotation()` (the locked activity never legitimately changes it anyway). Set `mRotation` to the verified constant and remove the per-event read.
4. **Manifest:** leave `screenOrientation="landscape"` as-is. Do **not** switch to `sensorLandscape` â that would *introduce* reverse-landscape ROTATION_3 flips that plain `landscape` provably cannot produce. No manifest change is indicated.

---

## 3. On-device diagnostic plan (Mi 10s; repeat on Pixel 3 XL)

Goal: pin the exact `getRotation()` value during gameplay and the sign relationship between physical lean and the mapped axis, so the patch is confirmed not guessed.

### Step 1 â Instrument MCAccelerometer (one smali edit, temporary)
In `onSensorChanged` after line 70 (`mRotation = display.getRotation();`), insert an `android.util.Log.d` that prints, per event (throttle to ~1/sec to avoid log flood):
- `mRotation` (the live rotation int)
- raw `event.values[0]`, `[1]`, `[2]`
- the mapped pair actually passed to native for the selected branch

Tag it e.g. `BR_TILT`. Rebuild/sign/install the patched APK.

### Step 2 â Capture
```
adb logcat -c
adb logcat -s BR_TILT:D
```
During capture, hold the phone in the natural gameplay landscape grip and perform the in-game lean-forward and lean-back gestures distinctly. Record which physical lean you did against the timestamps.

### Step 3 â Read off the answers
- **Which branch executes?** The logged `mRotation` tells you definitively whether the Mi 10s is on branch `1` or `3` (or unexpectedly `0`/`2`). This is the value that all the web evidence could only speculate about.
- **Is lean inverted?** Correlate "I leaned forward" vs the sign of the mapped lean-axis value. If leaning forward yields the value the game treats as "back flip," the axis is inverted â negate that term in the executing branch.
- **Which raw axis carries lean?** Confirm whether `values[0]` or `values[1]` changes most when you lean forward/back in the gameplay grip; that identifies which native argument is the flip control.

### Step 4 â Cross-check against the OS (sanity)
Independently confirm the rotation value with a stock sensor/orientation utility or `adb shell dumpsys window | grep -i rotation` while the game is foregrounded, to verify the logged `getRotation()` matches what the window manager reports (rules out any display-context discrepancy on API 30+).

### Step 5 â Confirm the fix
Apply the Section 2 edit to the **branch the log proved executes**, reinstall, and re-run Step 1-2 logging to verify leaning forward now maps to a front flip and the values have the corrected sign. Only then remove the logging and ship.

---

## 4. Secondary issues worth pre-empting
- **Android 12 sampling cap â non-issue, but pre-empt anyway:** targetSdk=22 + 50 Hz request is exempt and far under the 200 Hz ceiling. If you ever bump targetSdk during repackaging, do **not** cross 31 without re-checking, and never request `SENSOR_DELAY_FASTEST` (would trigger the `HIGH_SAMPLING_RATE_SENSORS` `SecurityException` on targetSdkâ¥31). https://developer.android.com/develop/sensors-and-location/sensors/sensors_overview
- **Background sensor restriction (API 28+):** only affects background; `GameActivity` is foreground during play, so unaffected. Confirm any patch doesn't move sensor registration off the foreground path. Same source.
- **Newer-Android screenOrientation override (Android 16/17):** games (`android:appCategory game`) are currently exempt and these phones are <600dp, so the landscape lock is honored. Not a current cause; only a future fragility if Miniclip's category flag is absent. https://android-developers.googleblog.com/2026/02/prepare-your-app-for-resizability-and.html
- **Don't "fix" the native 0.1 scale or the native negate-x/negate-y:** they are constant and correct as a pair with the Java table; changing them requires binary patching and is unnecessary â all correction is reachable from MCAccelerometer's Java/smali.

---

## Bottom line
Every crisp theory was refuted; the honest position is that the surviving cause (Root Cause A â a `getRotation()`/branch mismatch landing on the wrong or pinned landscape mapping, with the 1â3 sign inversion as the damage mechanism) is **medium-confidence and currently lacks a verified mechanism on these two specific phones**. The fix is a one-branch smali sign/axis edit (or collapsing both landscape branches), but **which** branch and **which** sign cannot be derived from the evidence â it must be read directly off the Mi 10s via the logcat plan in Section 3. Treat any pre-diagnostic branch choice as a guess.

Key file: `/home/thai/Documents/Projects/resurrect/decompiled/original-java/com/miniclip/input/MCAccelerometer.java` (lines 67-85 are the patch site).
