# 🏍️ Revenant Bike Editor

Edit bike physics (and clone bikes) for Bike Rivals. Bike tuning lives in **plaintext**
binary plists — `assets/unpack/<Bike>Pref.plist` → `Entities[0].Properties` — so no cipher
work is needed here. Two front-ends over the same engine:

## Web UI (recommended)
```bash
python3 tools/bike-editor/server.py      # localhost-only, stdlib, no deps
# open http://127.0.0.1:8777
```
Pick a bike, drag sliders for the 11 knobs, **Save**. Then repackage:
```bash
bash build/build.sh
adb install -r dist/BikeRivals-1.5.2-diagnostic-unlock.apk
```

## CLI
```bash
python3 tools/bike-editor/bikeedit.py list
python3 tools/bike-editor/bikeedit.py get  PizzaBike
python3 tools/bike-editor/bikeedit.py set  PizzaBike speedLimit=200 forceScale=2.0 tilt=1.2
python3 tools/bike-editor/bikeedit.py clone MainBike RocketBike
```

## Knobs (`Entities[0].Properties`)
`speedLimit` · `forceScale` (engine power) · `nitroPerformance` · `geyserPower` ·
`burnoutSpeed` · `maxWheelieSpeed` · `tilt` (lean/flip sensitivity) · `anchorY` (center of
mass) · `scale` · `spritesScale` · `poseValue`. (Deeper per-body density/friction/motor
values exist in the other entities — not exposed here yet.)

## Notes & limits
- Operates on the apktool-decoded tree (`build/work/assets/unpack`, override with `BR_UNPACK`).
  Run `build/build.sh` once first so the tree exists.
- Writes **re-encode** the bplist via plistlib. The data round-trips exactly, but
  **device-verify your first edited build** (cocos2d parser acceptance — see `docs/JOURNEY.md`).
- **`clone`** copies the three files (`Pref.plist` + sprite atlas `.plist` + `.png`). It does
  **not** yet register the bike in the *roster* — the game won't list a brand-new bike until
  it's registered, and the roster is in `ProductList.dat`/`Shop.dat` (encrypted) or
  `libgame.so`. Modifying/reskinning copied files works; full new-bike registration is
  Phase-1 open work (see [`docs/ROADMAP.md`](../../docs/ROADMAP.md)).
