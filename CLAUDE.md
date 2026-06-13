# Revenant — project guide for Claude

Revenant is a clean revival + modding toolkit for **Bike Rivals 1.5.2**
(`com.miniclip.bikerivals`), a 2014 cocos2d / Apportable (ObjC-over-C++) mobile
trials game. Public repo: github.com/tyler-void/revenant (owner: Tyler).
Goal: turn a dead game into a community modding scene (level editor, bike editor,
custom-content loading) — in the spirit of PvZ2 / Bad Piggies mods.

## 📚 Knowledge model — READ + MAINTAIN these (how modding/decomp scenes track everything)

Four living docs in `docs/` capture the project's knowledge so it's never lost
(modeled on how decomp/modding projects survive — vision + RE-notes wiki +
gotchas log + runbook). When you learn or do something important, write it to the
right one:
- **[docs/vision.md](docs/vision.md)** — the goal, pillars, principles (the *why*).
- **[docs/research.md](docs/research.md)** — RE findings: how the game/cipher/format/
  renderer actually work + open questions (the *what we know*).
- **[docs/lesson.md](docs/lesson.md)** — gotchas & rules learned, so we don't repeat
  mistakes (the *how not to fail*).
- **[docs/steps.md](docs/steps.md)** — copy-paste runbook for every workflow (the *how-to*).

This file (CLAUDE.md) is the agent-facing quick index; the `docs/` files are the
deeper human/community-facing record. Keep both current.

## 🎯 STANDING DIRECTIVES — do NOT lose these on context compaction

Long-running owner asks that keep getting dropped when context is summarized. They
persist here so any session picks them up. Detailed findings/plan live in
**[docs/procgen.md](docs/procgen.md)** (corpus analysis + generator design +
World-5 registration) — read it before touching the generator or World 5.

1. **Data-driven generator from ALL ~140 levels.** Decode every level (worlds 1–4),
   analyze how the game actually builds terrain (segment density, slope distribution,
   spline vs polygon, fill/edge textures) and what makes each level *challenging*
   (obstacle taxonomy + placement + difficulty progression per world). Feed those
   real distributions back into `tools/level-editor/levelgen.py`. Corpus re-decode
   runbook: `docs/steps.md` → "Decode the full level corpus".
2. **Web + roguelike research.** Keep pulling in external procgen technique (smooth
   spline/noise terrain, difficulty pacing, solvability, obstacle telegraphing,
   roguelike variety/encounter-budget); record in `docs/procgen.md`.
3. **Terrain quality bar (owner feedback):** NO "lazy straight lines", NO over-steep
   slopes. Terrain must read as hand-authored — smooth rolling ground, rideable grades.
4. **World 5 = additive custom-level world** hosting the enhanced generator. RE how
   levels register (`WorldDefinition.plist`, `GameConfig_T%d.dat`, level-count + unlock
   checks); add world 5 WITHOUT touching Halloween/Christmas (separate worlds, confirmed).

## 🔴 SECURITY / LEGAL CONSTRAINTS — never violate

- **NEVER commit cipher keys or decrypted game data** (levels, configs, plaintext).
  They are DMCA §1201 circumvention material. Keys are captured *locally* and
  passed via the `BR_KEY` env var; decrypted data lives in gitignored caches.
- **NEVER commit copyrighted game bytes**: the APK, assets, atlases, textures,
  decompiled/smali code, the malware "mod" sample. `.gitignore` covers
  `base/ dist/ build/work/ ref/ *.apk *.so *.dat *.level.json tools/level-editor/{levels,textures}/`.
  Gitignore has NO inline comments (git treats them literally) — comments on own lines.
- This repo ships **methods/offsets/tooling only** (BYO-original model). Distributing
  the patched APK is the legal 🔴 line. See `LEGAL.md`, `docs/PRESERVATION-PLAYBOOK.md`.
- **Commit/push only when the user asks.** Commits are SSH-signed (key `thai-vast`).
- Before any commit: `git add -An | grep -iE 'key|\.dat|level\.json|textures/|/tmp'` → must be clean.

## The cipher — CRACKED (decode + encode + device-verified)

- Container: `"<plaintextLen>\0"` + body. Cipher = **Blowfish** (cocos2d/Apportable).
  Pipeline: `.dat → strip header → DECRYPT → [levels: GUNZIP] → binary plist (bplist00)`.
- Cipher core (libgame.so): `cipher_init@0x650090`, `cipher_setkey@0x650570` (key = raw
  `char*`), DECRYPT `cipher_process@0x65085c` (→block 0x650ca8), **ENCRYPT
  `cipher_process@0x6507d4`** (→block 0x6508e4). Decrypt reads via
  `+[NSData DataWithContentsOfFile:Password:]@0x64ea98` = `atoi(header)` +
  `cipher_process(file+8, len-8)` (NO nibble-swap in this method) + take declLen bytes.
- ENCRYPT (proven): `file = "<declLen>\0" + filler-to-offset-8 +
  cipher_process_ENCRYPT(gzip-plaintext padded to ×8)`.
- **KEYS (captured locally, NEVER committed):** ALL 142 levels share ONE key (24-byte;
  verified across worlds 1/2/4). Config files (`ProductList`/`Shop`/`GameConfig`/
  `ConditionInfo`) share a separate 50-byte key. Capture on-device via
  `build/patch_keylog.py` (ARM stub → logcat tag RVKEY); Frida CRASHES the 32-bit game.

## unidbg oracle — `tools/unidbg/.../LevelCodec.java`

Drives the game's own cipher. JDK17 at `/usr/lib/jvm/java-17-openjdk`; natives in
`tools/unidbg/natives/`. Env: `BR_MODE=decrypt|encrypt|roundtrip`, `BR_KEY=<hex>`,
`BR_IN`, `BR_OUT`. Run: `cd tools/unidbg && JAVA_HOME=... mvn -q -Dexec.mainClass=com.resurrect.LevelCodec compile exec:java </dev/null`.
Gotchas: **run with `</dev/null`** (on an emu exception unidbg drops into an interactive
debugger that blocks on stdin forever); `module.callFunction` marshals each `Long` as a
64-bit register PAIR → pass `int`s; `vm.loadLibrary(so, true)` (forceCallInit) is required
for objc_msgSend dispatch. `leveldec.py` already wraps this with `stdin=DEVNULL, timeout=300`.

## Build & device test

`build/build.sh` rebuilds the patched APK FROM the clean original (`rm -rf build/work`
then `apktool d` + `apply_patches.py`) — it **wipes** any swapped-in files. To device-test
an edited asset: swap into the *already-decoded* `build/work/assets/unpack/`, then
`apktool b build/work -o /tmp/x.apk` directly (don't run build.sh), sign with
`uber-apk-signer --ks build/keystore/resurrect-debug.keystore --ksAlias resurrect
--ksPass android`, `adb install -r`. Keystore MUST stay (same signer → `install -r`
preserves save). Device: Redmi Note 10S (`thyme`, serial 6d498557) — may be disconnected.
Verified: an edited 1_1 (moved start + medal times) loaded & played on device.

## Level editor — `tools/level-editor/` (Phase 3, mostly done)

`leveldec.py` (decode/encode core + `import`/`export`/`decode-raw` CLI),
`server.py` (localhost :8778 JSON API), `index.html` (canvas editor, Unity-dark theme).
Run: `python3 tools/level-editor/server.py`. Restart it by killing the `:8778` listener
(`ss -ltnpH | grep :8778`) — NOT `pkill -f server.py` (self-matches → exit 144).
- `import 1_1 build/work/assets/unpack/1_1.dat <KEYHEX>` → gitignored `levels/<lid>.level.json`.
- `export <lid>.level.json <out.dat> <KEYHEX>` → device-loadable .dat (JSON→bplist→gzip→encrypt).
- UI: grouped/searchable hierarchy, foldout inspector (Transform/Identity/Physics/...),
  move/rotate gizmos, terrain control-point drag, Save (→cache) + Export (→.dat).

### Level data schema (decoded bplist)
`{ lid, type, times:[gold,silver,bronze], Entities:[…] }`. Entity = `{Type, Properties, Vertexes?, Anchors?}`.
Types: `EditorPhysicsObject` (terrain — `Vertexes:[{x,y,segments}]` in WORLD coords,
`spline:True`→Catmull-Rom else polygon; `textureFill`/`textureEdge`/`fillTile`/`textureRot`),
`EditorSprite` (decoration — `position[x,y]`, `frame`, `scale`, `rotation`, `anchorX/Y=0.5`, `z`),
`Moto` (player start), `TriggerWin` (finish), `EditorTrigger`/`WaterTrigger`, joints,
`ComposedSprite`/`ExplosiveBarrel`/`EditorPhysicsEntity` (prefab groups via `refobjectList`/`prefabName`),
`EditorCamera`. Type fidelity: int vs real matters less (game reads NSNumber), but be careful.

### ⚠️ RENDERING NOTES (the editor's WYSIWYG viewport — still imperfect)
The game is a **2014 cocos2d mobile game for sub-FHD / retina screens**, so there is a
content-scale between atlas texels and level points. Hard-won render rules:
- **Atlas textures are NON-STANDARD WebP** (12-byte VP8X w/ 4-byte dims; lossless ALPH no
  stock libwebp decodes). Fix: extract the standard VP8 (RGB) chunk → ffmpeg decode →
  chroma-key BLACK to alpha (transparent regions are pure black). `transcode_atlas(key=)`;
  served at `/api/atlas/<name>` (keyed) and `/api/fill/<name>` (opaque, for ground fills).
- **THEME-AWARE atlas resolution**: the same frame name exists in multiple per-theme atlases
  (`elements_default`=world 1, `elements_t2/t3/t4`=worlds 2-4, `elements_ts1/ts2`=shared).
  Resolve against the LEVEL'S world (lid first number; world1→elements_default, no elements_t1)
  — else you draw the wrong world's art (a real bug we hit: world-3 ice in world 1).
- **Sprites are Y-up** vs canvas Y-down → `ctx.scale(1,-1)` before drawImage (else upside-down).
- **Content scale `SPRITE_CS`** (currently 2) divides sprite draw size (texels→points).
  ⚠️ STILL NOT EXACT — composed structures (bridges, doors, finish gates, rope bridges)
  render at the wrong size; the green physics outline is the GROUND-TRUTH scale and sprites
  must match it. Likely needs per-type handling (ComposedSprite children may be positioned/
  scaled relative to a parent transform) + the real cocos2d content-scale factor.
- **Terrain fill** is rendered (clip polygon + tiled `textureFill`); this is what makes the
  outline look like solid ground. STILL MISSING: `textureEdge` strips, **water**
  (WaterTrigger → the game uses CCLiquid/CCWaves; we draw a trigger circle), faithful
  composed-sprite/prefab layout, per-sprite rotation-sign verification.
- Faithful WYSIWYG = reverse-engineering the game's renderer (loader `loadEntity@0x64c644`,
  `WaterTrigger.mm`, `ComposedSprite.mm`) class-by-class — a deep, ongoing effort.

## Roadmap / phases (see `docs/ROADMAP.md`)
1 Bike editor ✅ · 2 Level decrypt ✅ · 3 Level editor 🚧 (editor+encode done; render fidelity WIP)
· 4 World-5 custom-level slot + **mod-loader** (redirect asset loads to an external `mods/`
folder via a native patch on the cocos2d path resolver `CCFileUtils fullPathForFilename:`;
game already has `getExternalStoragePath`) · 5 procedural gen · 6 ImGui mod menu · 7 offline achievements.

## Environment
apktool, uber-apk-signer, adb, ffmpeg, ImageMagick (`magick`), capstone, Ghidra
(`/opt/ghidra` — use Java GhidraScripts, PyGhidra is broken). No dwebp/cwebp/Pillow in
system python (a Pillow venv exists at `/tmp/webp-venv`). Method-table technique: ObjC
`{IMP,name,types}` 12-byte entries near `0xcfd5d0`; lib loads at base `0x40000000` in unidbg.
