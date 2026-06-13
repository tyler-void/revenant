# 🗺️ Revenant Roadmap — from revived game to community modding platform

The game is alive again (see the [README](../README.md)). This is the plan for what's next:
turning a dead 2014 trials game into a **community modding scene** in the spirit of the PvZ 2 and
Bad Piggies mod communities — custom levels, custom bikes, an in-game hub, and a place to share it all.

> Status legend: ✅ done · 🚧 in progress · ⬜ planned · 🔬 research-gated

## The keystone insight

The single thing that unlocks the whole community vision: **we don't crack the level cipher — we
borrow the game's own codec.** `libgame.so` ships the level encrypt/decrypt as ObjC methods
(`+[NSData DataWithContentsOfFile:Password:]`, `+[NSData ArchiveRootObject:ToFile:Password:]`),
and our [unidbg harness](../tools/unidbg/) already loads `libgame.so` and calls its methods. So the
level pipeline becomes *"call two functions we already have access to"* — decrypt to JSON, edit,
re-encrypt with the game's own writer. The plaintext is JSON (Catmull-Rom spline tracks → Box2D).

## Phases

| # | Phase | Difficulty | Status |
|---|---|---|---|
| 1 | **Bike editor** (modify + clone-to-create bikes) | 🟢 Easy | ✅ editor done (CLI + web UI); new-bike *roster registration* gated on Phase 2 |
| 2 | **Level-decrypt spike** (unidbg → JSON → schema) | 🟡 Medium | 🚧 blocked — cipher is per-file (many-time-pad **ruled out**); needs the binary codec via unidbg. See [ASSET-FORMATS](ASSET-FORMATS.md) |
| 3 | **Level editor** (splines + object palette → re-encrypt) | 🟡 Medium | ✅ done — viewer + drag-edit + save/export, device-verified |
| 4 | **World 5 = Custom/Community Levels** (the delivery mechanism) | 🟡 Medium | 🔬 |
| 5 | **Procedural level generator** (random/seeded tracks) | 🟡 Medium | 🔬 |
| 6 | **ImGui mod menu** (in-game hub: live tuning) | 🟠 Med-Hard | ⬜ |
| 7 | **Achievement tab** (revive the dead Google-Play achievements) | 🟡 Medium | ⬜ |

---

### Phase 1 — Bike editor 🟢

Bike physics live in **plaintext** `assets/unpack/<Bike>Pref.plist` (`Entities[0].Properties`):
`speedLimit`, `forceScale`, `nitroPerformance`, `tilt`, `maxWheelieSpeed`, `anchorY`, plus per-body
`density`/`friction`/motor forces. See [MODDING-MAP](MODDING-MAP.md).

Two capabilities:
- **Modify existing bikes** — edit the knobs, rebuild, ride. Trivial.
- **Clone-to-create a new bike** — copy an existing bike's three files (`<Bike>Pref.plist` physics +
  `<Bike>.plist` sprite atlas + `<Bike>.png` WebP texture) under a new name, then tweak. **Open
  question (small RE task):** how the bike *roster* is registered — a plist catalog vs. a hardcoded
  list in `libgame.so`. Registering a brand-new entry is the only non-trivial part; modifying and
  reskinning existing bikes is easy.

**Platform — PC for authoring, in-game for live tuning (they pair):**
- The **authoring editor is a computer tool** (it reads/writes asset files and rebuilds the APK).
  Recommended: a **local web app** (fits the TS stack) — form/sliders over the plist knobs, clone
  button, live numeric preview — that writes the plist and invokes `build.sh`.
- **Live in-game tuning** comes later via the **mod menu (Phase 6)**: drag a slider, feel the change
  immediately. Workflow: tune live → bake the good numbers into the plist via the PC editor → share.
- You can't *author/share* a bike purely on-device without the mod menu also writing the plist; the
  PC tool is the source of truth, the mod menu is the live feedback loop.

### Phase 2 — Level-decrypt spike 🚧 (blocked, well-characterized)

Use unidbg to invoke the game's own decryptor on a level → emit JSON → document the schema. This
de-risks Phases 3–5 (and unlocks `ProductList`/`Shop`/`ConditionInfo` — see below).

**Findings (this session):** the container is `"<len>\0"`+body+trailer; the cipher is a **per-file
stream cipher** — many-time-pad is **ruled out** (even the 115 files sharing `body[0:2]=0000` have
mutually-random payloads). Static xref to the password is blocked by GOT-bridged PIC (Ghidra didn't
materialize the refs or the ObjC method IMPs). **So decryption requires the binary's own codec.**

**Next step (recommended):** Route B in [ASSET-FORMATS](ASSET-FORMATS.md) — drive
`+[NSData DataWithContentsOfFile:]` (the no-password variant, file off `0xa6db5c`) or the loader in
unidbg, passing an `NSString` path, and read the returned `NSData`. The remaining work is Foundation
object construction in unidbg. Codec/loader offsets are catalogued in ASSET-FORMATS.

### Phase 3 — Level editor ✅

PC editor (web): decrypt a level → render the Catmull-Rom spline + objects → drag control points,
drop elements from the palette → export JSON → re-encrypt via the game's cipher.

**Status (2026-06-13):**
- ✅ **VIEWER works** (`tools/level-editor/`: `leveldec.py` + `server.py` :8778 + canvas `index.html`).
  Decodes a real `.dat` (decrypt→gunzip→bplist) and renders the scene — Catmull-Rom terrain, sprite/
  trigger/joint/marker layers, pan-zoom, entity inspector, medal times. Coordinate model verified:
  `EditorPhysicsObject.Vertexes` are world-space, `spline:True`→curve else polygon.
- ✅ **Decrypt oracle solid**: `tools/unidbg/.../LevelCodec.java` (`BR_MODE=decrypt`), driven by
  `leveldec.py import`. Per-level key via `BR_KEY` (never committed).
- ✅ **Re-encrypt SOLVED** (`leveldec.py export`). The cipher is **Blowfish** with mirror-image
  process pairs: DECRYPT `cipher_process` @ 0x65085c (block 0x650ca8) and **ENCRYPT** `cipher_process`
  @ 0x6507d4 (block 0x6508e4). Framing: `file = "<declLen>\0" + filler-to-offset-8 +
  cipher_process_ENCRYPT(gzip plaintext padded ×8)` (cipher region always starts at file[8]).
  **Round-trip verified**: an edited level (changed medal times + moved start) re-encrypts to a `.dat`
  that the game's own decryptor reads back with the edits intact and zero collateral changes.
- ✅ **Editing UI** (`server.py` :8778 + `index.html`): select an entity → editable inspector,
  drag control points to reshape terrain, drag bodies to move, edit medal times, **Save** (→ cache)
  and **Export** (→ encrypted `.dat`). Browser-tested end to end.
- ✅ **Device-verified** (2026-06-13): an edited 1_1 (start moved up, medal times set to 999) was
  re-encrypted, rebuilt, and installed — on real hardware the bike visibly drops in at spawn and all
  medal times read 999. The full `decode → edit → re-encrypt → device-loadable .dat` loop works.

### Phase 4 — World 5 = Custom / Community Levels ⭐🔬

**The delivery mechanism.** The world-select screen shows a **blank "?" slot after World 4** — the
natural home for community content. Repurpose it as the **Custom Levels** world:
- Provide level files at the `5_*.dat` paths (the engine's path format is `%d/%d_%d.dat`).
- Enable the slot via `WorldDefinition.plist` (the world-select catalog).
- This is how players *get* community content: drop `.dat` files into World 5 (and, later, a mod-menu
  browser to install/select them).

### Phase 5 — Procedural level generator 🔬

Because levels are just JSON spline tracks, we can **generate** them: sample a control-vertex curve
(seeded/random difficulty params), scatter rings/ramps/checkpoints, emit JSON → encrypt via the
game's writer → drop into World 5. Endless randomly/procedurally generated tracks. A "seed → track"
generator also makes shareable one-line level codes possible.

### Phase 6 — ImGui mod menu 🟠

Inject a 32-bit `.so` → hook `eglSwapBuffers` → ImGui overlay + touch input (rooted device). The
in-game hub: live bike-handling sliders, god-mode toggles, the **custom-level browser**, and the
**achievement tab**.

### Phase 7 — Achievement tab 🟡

The game has an internal achievement system wired to **Google Play Games Services** — dead servers,
so achievements never register. Plan: RE the achievement IDs + the in-game unlock call-sites → hook
them → record to a **local** store → render a **local achievement tab** inside the mod menu. Brings
the dead achievements back, offline.

---

## Sharing & community (the "like PvZ 2 / Bad Piggies" part)

The community needs: a content format people can edit (level/bike JSON+assets), tools to make it
(Phases 1, 3, 5), and a way to load it (Phase 4). The sharing channel is the planned **preservation
org** (see [PRESERVATION-PLAYBOOK](PRESERVATION-PLAYBOOK.md)) — a repo of community levels & bikes
(content files only; still **no game bytes**, players bring their own copy).

**Promotion note:** promote the *project, the story, and the tools* — not the patched APK
(redistributing the APK is the 🔴 line in [LEGAL.md](LEGAL.md)). The revival war-story
([JOURNEY.md](JOURNEY.md)) + the preservation angle is the safe, and more interesting, pitch.

## Open questions to resolve

- **Config codec — SOLVED.** unidbg decryption oracle (`tools/unidbg/.../LevelDecrypt.java`, key via
  `BR_KEY`) + on-device key capture (`build/patch_keylog.py` → logcat). The config key decrypts
  `ProductList`/`Shop`/`GameConfig`/`ConditionInfo` to 100% XML.
- **Level codec — OPEN.** Levels use a **separate** cipher (the config key/cipher decrypt none of the
  142 `.dat` files). Lead: `+[NSArray ArrayWithContentsOfFilePass2:]` ("second pass"). Next: find its
  `setkey`, re-spin the keylog patch onto it, capture a level's key, decrypt via that path.
- **Bike roster — ANSWERED.** It's the IAP product list in decrypted `ProductList.dat`
  (`com.miniclip.bikerivalsbike1–14`, packs, coins).
- Whether **World 5** needs a native patch beyond `WorldDefinition.plist` (level-count/unlock gate).
- Custom-level **install UX** — drop-in path vs. mod-menu browser.

> Cipher keys + decrypted game data are **circumvention material** → kept local, never committed
> (see [PRESERVATION-PLAYBOOK](PRESERVATION-PLAYBOOK.md) / [LEGAL.md](LEGAL.md)).

## Session log (2026-06-13)

- ✅ **Phase 1 bike editor** built + tested (CLI `bikeedit.py` + web UI). Roster question answered.
- ✅ Mapped the asset/data formats → [ASSET-FORMATS.md](ASSET-FORMATS.md).
- ✅ **Config cipher CRACKED** end-to-end: dispatch in unidbg via `forceCallInit`; on-device
  key capture (Frida is dead here → ARM keylog stub → logcat); decrypts the roster (Phase 1),
  achievements `ConditionInfo` (Phase 7), shop, game config.
- ⬜ **Level cipher** is the remaining Phase-2 piece (separate `Pass2` cipher — see above). Banked here.
