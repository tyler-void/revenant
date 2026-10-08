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

## 🤖 Device work → use the modkit MCP, not raw adb

Every device session starts with the **`modkit` MCP server** (registered in `.mcp.json`, code in
`mobile-modkit/mcp/`): `device_ensure` → `app_launch` → `bridge_call` (in-game state/events/
goto_level, needs `bridge=1` in `mods/rvdebug.txt`) → `logs_wait_for` → input → ONE `screen_shot` as
proof. Don't navigate by screenshots. Rebuild libmod with `tools/device/modloop.sh` (3–8 s). Full
manual + gotchas: **[mobile-modkit/SKILL.md](mobile-modkit/SKILL.md)**; reference run:
`tools/device/acceptance_bridge.py`.

This file (CLAUDE.md) is the agent-facing quick index; the `docs/` files are the
deeper human/community-facing record. Keep both current.

## 🎯 STANDING DIRECTIVES — do NOT lose these on context compaction

Long-running owner asks that keep getting dropped on summarization. **OVERARCHING GOAL
(2026-06-14): bring this game to OTHERS to play ASAP — make people happy, no community
needed yet.** The game is genuinely lost (pulled from stores 2018, Flash/Kongregate
dead ~2020); our patched build is all-unlocked + fuel-fixed + TILT-FIXED-for-modern-
Android + offline — a great "just play" experience. Legal model stays BYO-original
(ship methods only; distributing the patched APK is the 🔴 line).

**PRIORITY NOW (owner 2026-10-08): the GENERATOR TRACK G1–G4** (corpus stats + bridge traces → terrain
encoding experiment → in-game bot verifier → `tools/levelgen/` generator v2); the level editor stays as is.
Commit locally per step, report after each, don't push. Record: [docs/procgen.md](docs/procgen.md).
State 2026-10-09: G1 ✅ (3d99a0b) · G2 ✅ spline per ground run (e3214f2) · G3 bot WIP (v0 3/30 W1; v1 ceiling
brake + slices + plan overrides; next: W1 rerun + `--search`) · G4 `tools/levelgen/gen.py` written, not run.

**ACTIVE — full speed (in order): Phase 7 then Phase 8.** (Owner 2026-06-14: move on to 7+8;
the rest are WIP.)
- **Phase 7 — offline achievements** (IDs/conditions already extracted from ConditionInfo).
- **Phase 8 — browser one-click patcher (`web/`, React+Vite+Tailwind+Bun).** Static GitHub Pages
  app, BYO-original, patches the user's own APK IN-BROWSER (RAM, JSZip + shared
  `patches/manifest.json` + in-browser v1 signing). **DONE + DEVICE-VERIFIED on Android 13:**
  native byte-patches, **tilt fix as a real DEX code-item rewrite in Rust→WASM** (`dex_tilt_rewrite`
  — NOT a byte-patch; the minimal byte-patch crashes with UnsatisfiedLinkError), **privacy cull via
  in-browser binary-AXML editing** (`web/src/axml.ts` — drops tracking perms + GCM/AdX/OpenUDID/boot
  components), Tailwind-responsive UI. The browser-built APK installs, boots, and tilt-steers (grant
  only "Motion"/sensor). Full record: **[docs/patcher.md](docs/patcher.md)**.
  Pages deploy ✅ live (tyler-void.github.io/revenant); `apply_patches.py` reads the manifest ✅ and its
  tilt fix mirrors the wasm rewrite. **REMAINING:** (optional) APK v2/v3 signing in `wasm/`.
  **DISTRIBUTION SPLIT (owner 2026-06-14):** only the
  **unlock-all browser-patched APK** is distributed (the "just play" core, free). The **mod menu**
  (Phase 6, `libmod.so`) is a personal/power-user extra the owner is considering paywalling
  (Patreon/BMC) — but note `mod/mod.cpp` + `mod/build.sh` + `docs/modmenu.md` are ALREADY PUBLIC on
  GPLv3 `origin/main`, so a closed paywall doesn't really hold; a donation/perk model fits.

**WIP (usable, polish later):**
- **Phase 6 — in-game LIVE mod menu (ImGui)** — DONE + device-verified: NDK `libmod.so`
  injected, ImGui overlay (swapBuffers hook) + touch, tabbed menu (Drive/Bike/System),
  live **gravity** (−30×…+30×), **camera zoom** (flexible/locked), **bike specs**
  (max speed/accel/handling/nitro[capped]/burnout, per-bike-safe), **debug HUD** (FPS/RAM/CPU +
  opt-in **speed** = chassis `heroTorso` b2Body velocity, read in the overlay hook), **Reset Progress**.
  ⚠ 2026-10-08: the HUD run timer is `Manager.time_` (VERIFIED via the bridge); `Physics.gameTime_`
  stays 0 in single-player, so the `gameTime_` mechanism below is unproven (the fix stands).
  ✅ **RUN-TIMER FREEZE FIXED (2026-06-15) + ghost fixed.** Root cause (proven by live-bisect, NOT
  theory): the **per-frame step-hook BODY** corrupted `gameTime_` — NOT anti-tamper, NOT the spec/
  gravity writes (the `dt` handed to `step:` is a perfect real-time 1/60, ratio≈1.0). The old "gate the
  writes" never worked because it still ran `[self world]` + `apply_specs()` EVERY frame. Fix:
  `hook_step` **idle fast-path** (`step_active()` → do nothing unless a feature is engaged) + the
  **speed read moved to the overlay (swap) hook** (timer-safe) so the HUD never runs the step body.
  Now timer + ghost + speed all work together (the ghost shares `gameTime_`, so it was garbled by the
  same bug → sub-1s times + wrong route). **Remaining trade-off:** only GRAVITY/BIKE-SPEC features run
  the step body, so engaging those skews the timer/ghost while on (race clean, or experiment — per
  slider). Full record: **[docs/modmenu.md](docs/modmenu.md)**.
- **Phase 3 — level editor** (editor+encode done; WYSIWYG render fidelity imperfect).
- **Phase 4 — mod-loader** done (drop `mods/<w>_<l>.dat`, no root); World-5 slot pending.
  NOTE: its decrypt-path hook was a *suspect* in the Phase-6 timer freeze, so it's now OFF by default.

**PAUSED (not abandoned):** World 5 + procedural generation — see
**[docs/procgen.md](docs/procgen.md)** (deep RE in progress: dynamic-trace toolkit works,
comingSoon gate located, needs more trace rounds).

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
  `cipher_process@0x6507d4`** (→block 0x6508e4). Decryptor
  `+[NSData DataDecryptedFromData:Password:]@0x64ea98` = `atoi(header)` +
  `cipher_process(data+8, len-8)` (NO nibble-swap in this method) + take declLen bytes.
  (The file-path reader `DataWithContentsOfFile:Password:` is `@0x64ec3c` — the mod-loader hook.)
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
- Phase-4 MVP (2026-10-08, UI-acceptance-tested with Playwright + device): ref-safe delete/move/duplicate/
  cross-level copy-paste (`levelops.js`, Node tests: `node --test tools/level-editor/tests/`), undo/redo,
  lint panel (errors gate Export/Push; issues already in the shipped level are tagged), vertex insert
  (click an edge) / delete (Alt-click), draw-terrain tool (T), Level panel (target slot + medal times),
  save-as-copy, TYPED saves (`leveldec.retype_level`; levels contain NaN → wire tags `{"__float__":"nan"}`;
  proof: `tests/roundtrip_types.py`), and **▶ Push to game** (`push_to_game.py`: export → mods/<lid>.dat →
  bridge goto_level → `[MOD]` + level_loaded → ride → screenshot shown in the editor). Node lives at
  `~/.nvm/versions/node/*/bin/node` (the nvm lazy-loader breaks non-interactive shells).

### Level data schema (decoded bplist)
`{ lid, type, times:[1★,2★,3★] (DESCENDING, e.g. [38.0,32.0,26.0] — VERIFIED), Entities:[…] }`. Entity = `{Type, Properties, Vertexes?, Anchors?}`.
Index refs (VERIFIED, -1 = none): lists `refobjectList`/`mountedSprites` (owned children) + `objects` (trigger links);
singles `refentity`/`reffollowEntity`/`refsprite1`; joints `Anchors[].object_id/object2_id` (two body slots;
a missing/-1 slot = the world). All handled by `tools/level-editor/levelops.js`.
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
- **Content scale `SPRITE_CS`** (currently 8.5, live-tunable via the toolbar `scale` box) divides sprite draw size (texels→points).
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
· 4 World-5 custom-level slot + **mod-loader** (done: libmod hooks the encrypted-file reader
`DataWithContentsOfFile:Password:@0x64ec3c` and redirects to an external `mods/` folder)
· 5 procedural gen · 6 ImGui mod menu · 7 offline achievements.

## Environment
apktool, uber-apk-signer, adb, ffmpeg, ImageMagick (`magick`), capstone, Ghidra
(`/opt/ghidra` — use Java GhidraScripts, PyGhidra is broken). No dwebp/cwebp/Pillow in
system python (a Pillow venv exists at `/tmp/webp-venv`). Method-table technique: ObjC
`{name,types,IMP}` 12-byte entries near `0xcfd5d0` — IMP at **+8** from the selector-name pointer
(the word at −4 is the PREVIOUS method's IMP); lib loads at base `0x40000000` in unidbg.
