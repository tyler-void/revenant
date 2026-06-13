# Tilt Controls — The Actual Fix

## Root cause (confirmed on-device, not theorized)

On Android 13 / HyperOS the game's lean-to-flip controls are dead because **the
accelerometer is never registered** during gameplay. Verified with
`adb shell dumpsys sensorservice`: in a race, the only active sensors are
pedometer, SMD, ambient-light and device-orient — `android.sensor.accelerometer`
(handle `0x37`) is absent. The native game also calls `MCAccelerometer.setEnabled`
only with `false`, so the listener is never registered.

Two compounding problems:
1. The game's own logic never enables the accelerometer (`setEnabled(true)` not called).
2. Android 12+ on this hardware blocks the accelerometer registration without the
   **`HIGH_SAMPLING_RATE_SENSORS`** permission. (Note: a multi-agent research pass
   *dismissed* this permission because 50 Hz is below the documented 200 Hz cap —
   but the empirical on-device result contradicts the theory. Trust the device.)

## The fix (from the user's New-Year-2026 mod, `ref/re-stuffs`)

`build/MCAccelerometer.userfix.smali`, applied by `build/apply_patches.py`:

- **`register()`** — removed the `isEnabled` guard, so it registers the listener
  whenever called (on `onResume`/`onWindowFocusChanged`) regardless of whether the
  game enabled tilt. Rate kept at `SENSOR_DELAY_GAME` (0x1).
- **`unregister()`** — neutered to `return-void`; once registered the sensor stays
  on across the lifecycle.
- **`onSensorChanged()`** — fixed landscape axis mapping (no per-rotation branch):
  `native(values[1], -values[0], values[2], ts)` → game-X = sensor-Y (steer),
  game-Y = -sensor-X (balance/flip). The game never calls `setEnabled(true)` on
  the Mi 10s (the control-mode default lives in the encrypted save), so its
  `isEnabled` guard would keep tilt shut — we **remove the guard (always forward)**
  but **wrap the native `onSensorChanged(FFFJ)` call in `try/catch
  UnsatisfiedLinkError`**. This is load-bearing: the force-registered sensor fires
  events *before* `libgame.so` binds the native method (→ `UnsatisfiedLinkError`
  crash, the exact one the user hit); the try/catch swallows those early events
  and forwarding begins the instant the native binds. Verified on-device: 0
  crashes through a full race and the native call succeeded ~1329×/race.
- **Manifest** — added `<uses-permission android:name="android.permission.HIGH_SAMPLING_RATE_SENSORS"/>`.

## Verified

After install, `dumpsys sensorservice` shows
`+ 0x00000037 ... package=com.miniclip.input.MCAccelerometer samplingPeriod=20000us`
with no matching unregister, and the `lsm6dso` event stream is live. The
accelerometer now registers and streams to the game.

## Remaining: axis/sign tuning

The mapping is fixed for one landscape orientation. If lean-forward produces a
back-flip (or steering is mirrored), flip the sign of the relevant term in
`onSensorChanged` — e.g. `neg-float` the `values[1]` (steer) or `values[0]`
(flip) term. This is chosen by feel on the device.
