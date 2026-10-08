# Steps — runbook

> *Copy-paste procedures for the common workflows. Keep these current; if a step
> changes, fix it here. Cipher keys come from your own local capture (env `BR_KEY`,
> never committed).*

Prereqs: JDK17 at `/usr/lib/jvm/java-17-openjdk`, apktool, uber-apk-signer, adb,
ffmpeg, a built `build/work` (run `build/build.sh` once), the prebuilt unidbg
module, and your captured level key in `$K`.

## 0. Capture a cipher key (once)

Frida is dead here, so capture on-device:
```bash
python3 build/patch_keylog.py build/work/lib/armeabi-v7a/libgame.so   # inject keylog stub
# rebuild + install (see §5), then PLAY a level; the key prints to logcat:
adb logcat -s RVKEY
```
The level key is universal (one key for all levels); the config files use a
separate key. Store it locally only.

## 1. Decode a level → editable JSON
```bash
python3 tools/level-editor/leveldec.py import 1_1 build/work/assets/unpack/1_1.dat $K
# → tools/level-editor/levels/1_1.level.json  (gitignored)
```

## 2. Edit a level (visual editor)
```bash
python3 tools/level-editor/server.py        # → http://127.0.0.1:8778
```
Hierarchy (left) to select, drag handles to reshape terrain / drag bodies to move,
Inspector (right) to edit properties + medal times, **Save** (→ cache), **Export**
(→ encrypted `.dat`; needs the key in the toolbar field). Tune the `scale` box to
match the green outline.

Editing (Phase-4 MVP): shift-click multi-select; drag moves the selection WITH its children and
the joints bound only to it; **Del** deletes the selection + children + joints on removed bodies
(confirm lists them; every other ref is remapped); **Ctrl+D** duplicate; **Ctrl+C / Ctrl+V** copy and
paste between levels; click an edge to insert a vertex, **Alt-click** a vertex to delete it; **T**
draw terrain (click points, Enter); **Ctrl+Z / Ctrl+Shift+Z** undo/redo. With nothing selected the
Inspector shows the Level panel (target slot = `lid`, medal times 1★/2★/3★ descending, Save as copy).
The Lint panel gates Export/Push on errors.

**▶ Push to game** (needs the libmod build installed, adb, and `uv`): save → lint → export → push
`mods/<lid>.dat` → bridge `goto_level` → wait for `[MOD] <lid>.dat` + `goto_done` → optional 4 s
throttle ride → one screenshot, shown in the editor (~15 s). Command-line equivalent:
```bash
uv run --project mobile-modkit/mcp python tools/level-editor/push_to_game.py --dat X.dat --lid 1_25 --play
```
Tests: `~/.nvm/versions/node/*/bin/node --test tools/level-editor/tests/` (ref ops, synthetic) ·
`python3 -m unittest discover -s tools/level-editor/tests -p 'test_*.py'` (types) ·
`python3 tools/level-editor/tests/roundtrip_types.py --doc 1_25` (real level, local only).

## 3. Generate a procedural level
```bash
python3 tools/level-editor/levelgen.py <seed> tools/level-editor/levels/5_1.level.json [length] [difficulty]
# deterministic: same seed → same track
```

## 4. Encode an edited/generated level → device-loadable .dat
```bash
python3 tools/level-editor/leveldec.py export tools/level-editor/levels/5_1.level.json /tmp/out.dat $K
```

## 5. Device-test a custom level (swap into a slot)

⚠ Use a **post-tutorial slot** (e.g. `1_4`+) to avoid the level-1 tutorial confound.
⚠ Do NOT run `build/build.sh` — it re-decodes from the clean original and wipes the swap.
```bash
cp build/work/assets/unpack/1_4.dat /tmp/1_4_orig.dat                 # backup
cp /tmp/out.dat build/work/assets/unpack/1_4.dat                      # swap in
apktool b build/work -o /tmp/mod.apk                                  # rebuild (~1-2 min)
uber-apk-signer --apks /tmp/mod.apk --ks build/keystore/resurrect-debug.keystore \
  --ksAlias resurrect --ksPass android --ksKeyPass android --allowResign -o /tmp/signed
adb install -r /tmp/signed/*-aligned-signed.apk                       # same keystore → keeps save
cp /tmp/1_4_orig.dat build/work/assets/unpack/1_4.dat                 # restore build/work
```
Then play that level on the device. Restore the device to stock levels with
`adb install -r dist/BikeRivals-1.5.2-diagnostic-unlock.apk`.

## 6. Run the unidbg cipher oracle directly
```bash
cd tools/unidbg && JAVA_HOME=/usr/lib/jvm/java-17-openjdk \
  BR_MODE=decrypt BR_KEY=$K BR_IN=<path.dat> BR_OUT=/tmp/raw.bin \
  mvn -q -Dexec.mainClass=com.resurrect.LevelCodec compile exec:java </dev/null
# BR_MODE = decrypt | encrypt | roundtrip.  ALWAYS </dev/null (debugger-on-stdin hangs).
```

## 7. Find a function address in libgame.so (method table)
Find the selector-name string, find the 32-bit LE pointer(s) to it, and read the IMP
at **+8** from that pointer (entries are 12-byte `{name, types, IMP}`, e.g. near
`0xcfd5d0`). NOT −4 — that word is the previous method's IMP. Validate against a
known IMP (e.g. `isWorldUnlocked:` → `0x6b0df0`). `libgame.so` is ARM (not Thumb);
vaddr == file offset in both PT_LOAD segments. See `tools/unidbg/.../LevelCodec.java` for the offsets we use.

## 8. Edit a bike
```bash
python3 tools/bike-editor/server.py          # web UI, or:
python3 tools/bike-editor/bikeedit.py set MainBike speedLimit=200 tilt=1.2
# bikes are PLAINTEXT plists in build/work/assets/unpack/<Bike>Pref.plist; then build (§5).
```
