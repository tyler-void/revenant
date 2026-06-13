# 🗂️ Bike Rivals 1.5.2 — Asset & Data Format Map

What every interesting file is, what's plaintext vs encrypted, and where the keys/logic live.
Compiled from the modding work + the Phase-2 level-decrypt spike (see [ROADMAP](ROADMAP.md)).

## The encrypted container (`*.dat`)

All encrypted data files share one container:

```
"<ascii-decimal-body-length>\0"  +  <ciphered body>  +  <short trailer (≈4–7 B, a hash)>
```

e.g. `1_1.dat` = `"80235\0"` + 80235 body bytes + 7-byte trailer.

Two families, each with a fixed 8-byte body "magic" (constant plaintext header under the cipher):

| Family | Files | Body magic |
|---|---|---|
| **Levels** | `1_1.dat`…`4_15.dat`, universe `2_1_*.dat` (130 files) | `body[2:10] = [redacted]` |
| **Config** | `GameConfig_T1..T4`, `ConditionInfo`, `ProductList`, `Shop`, `ca`, `cl` | `body[0:8] = [redacted]` |

`body[0:2]` of levels is a small per-file field (115/130 are `0000`; a few are `7dfa`, `fe7d`…).

### Cipher: per-file stream cipher — **NOT** crackable statistically (verified)

The Phase-2 spike **ruled out** every shortcut:
- **Not a short repeating-XOR** (the save's `[redacted-key]` + column-mode don't touch it;
  in-file autocorrelation is flat).
- **Not a reused keystream / many-time-pad.** Across files `c1 ⊕ c2` is ~50% hi-bit, ~0.4%
  zeros = **random**, even between files that share `body[0:2]` (tested the 115-file `0000`
  group). So the keystream is **per-file** (derived from password + per-file state/IV/content).
  The shared 8-byte magic is just a fixed marker, not evidence of a shared keystream.

**Conclusion:** decryption **requires the binary's own codec** — no pure-data attack works.

### The codec lives in `libgame.so` (NSData/NSArray category)

File offsets (== vaddr; Ghidra image base adds `0x10000`):

| Method | file off |
|---|---|
| `+[NSData DataDecryptedFromData:Password:]` | `0xa6da61` |
| `+[NSData DataWithContentsOfFile:Password:]` | `0xa6da81` |
| `+[NSData DataWithContentsOfFile:]` (no-password variant) | `0xa6db5c` |
| `+[NSData ArchiveRootObject:ToFile:Password:]` (the **writer** — re-encrypt by design) | `0xa6dbed` |
| `+[NSData ArchiveRootObject:ToFile:]` | `0xa6dc49` |
| `-[… loadLevelInfo:FileName:]` (level loader) | `0xa60b19` |
| `-[… loadWorldFrom:]` | `0xa67e52` |

Plaintext = **JSON** (TouchJSON `CJSONDeserializer`); level terrain = Catmull-Rom splines →
Box2D edge chains (see [LEVEL-MAKER](LEVEL-MAKER.md)).

### Next steps to actually decrypt (the open Phase-2 blocker)

Static xref is **blocked**: refs are GOT-bridged PIC, and Ghidra's auto-analysis materialized
neither the string xrefs nor the ObjC method IMPs (only `Label`/`PTR_` symbols for the selector
strings; the method-name pointer for the crypto category sits near `0xd0d8d8`). Two viable routes:

- **Route B — unidbg (recommended).** We already load `libgame.so` and call its methods
  ([tools/unidbg](../tools/unidbg/)). Call the **no-password `+[NSData DataWithContentsOfFile:]`**
  (cleanest — it must use a default/internal password) or the level loader, passing an `NSString`
  path; read the returned `NSData`. Needs Foundation object construction in unidbg (build an
  `NSString`/`NSData` via the runtime) — the one piece of plumbing left.
- **Route A — reverse the cipher.** Parse the ObjC method list to resolve the decryptor IMP
  (start from the method-name pointer ~`0xd0d8d8`), disassemble it for the KDF + stream algorithm
  + the constant Password, then reimplement in Python. More thorough; gives an offline codec.

> Analyzed Ghidra project is at `/tmp/revenant-re/proj1` (note: `/tmp` may not survive a reboot —
> re-import is ~minutes). Scripts: `/tmp/revenant-re/Find*.java`.

## Plaintext assets (no cipher)

| File(s) | Format | Notes |
|---|---|---|
| `<Bike>Pref.plist` (21) | binary plist | Physics tuning — `Entities[0].Properties`. **Bike editor target.** |
| `<Bike>.plist` + `<Bike>.png` | TexturePacker atlas + **WebP** | Bike sprites (`.png` is WebP: `dwebp`). |
| `WorldDefinition.plist` | binary plist | World-select catalog: **12 visual panels** (`WorldPanel1–12.ccbi`) + 3 lock positions. |
| `Halloween/ChristmasWorldDefinition.plist` | binary plist | Bonus-world templates (1 panel each). |
| `elements_default.plist` | TexturePacker atlas | **Level element palette**: 99 sprites — `ter1-20`, `bor1-17`, `for*`, `metal*`, `wood*`, `Tree*`, `checkpoint_*`, `Finish`, `KtmFinishline`, `barrel`, `rocky`, ramps. |
| `*.ccbi` (91) | CocosBuilder binary | UI scenes (`ccbi2ccb` → CocosBuilder). |
| `*.bank` | FMOD Designer/Ex | Audio (FSB Extractor). |

## World 5 — what already ships

- `WorldDefinition.plist` already lists **panel 5** (`WorldPanel5.ccbi` + `LevelSelectBK5.png`),
  and those assets **exist in the APK**. The blank "?" slot's UI is essentially ready.
- Level files exist only for worlds **1 (30), 2 (30), 3 (30), 4 (15)** + 25 universe sublevels.
- Gap for [Phase 4](ROADMAP.md): create `5_*.dat` level files (needs the level codec) + confirm/patch
  the native level-count / unlock gate so World 5 is playable.

## Jackpot config files (unlock via the level codec)

These are the same encrypted container, so the Phase-2 codec unlocks them too:
- **`ProductList.dat` / `Shop.dat`** → the **bike roster** + IAP products + prices → resolves the
  Phase-1 "register a new bike" open question.
- **`ConditionInfo.dat`** → almost certainly the **achievement / star-condition** definitions →
  directly feeds [Phase 7](ROADMAP.md).
- **`GameConfig_T1..T4.dat`** → per-world (theme) config; `GameConfig_U2/U3` = universe configs.
