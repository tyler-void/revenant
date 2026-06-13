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

### Crypto internals (recovered — file offsets == vaddr)

Method table (12-byte entries `{IMP, name_ptr, types_ptr}` at `.data` ~`0xcfd5d0`):

| Method | IMP |
|---|---|
| `+[NSData DataDecryptedFromData:Password:]` | `0x64e93c` |
| `+[NSData DataWithContentsOfFile:Password:]` | `0x64ea98` |
| `+[NSData DataWithContentsOfFile:]` (no-password) | `0x64f378` |

- **Cipher is layered, NOT a trivial transform.** `DataDecryptedFromData:` includes a **nibble-swap**
  pass (`b=(b>>4)|(b<<4)` at `0x64e9a0–0x64e9c4`, gated even/odd by `tst r0,#1`) — but nibble-swap
  alone does NOT yield JSON (tested: stays 38% printable). The real keystream is derived in the
  **cipher-core functions** `0x650090`, `0x650570`, `0x65085c` (called from
  `DataWithContentsOfFile:Password:`), keyed by the Password. Full static reverse = reversing those.
- **PIC model** (why flat xref failed): globals are reached as `ldr rX,[pc,#imm]` (a *PC-relative
  displacement*) then `add rX, pc, rX`, often `add r0, GOTbase, r0` (a 2nd const-offset register);
  selectors are interned via `sel_getUid`-like **`0x37295c`** then dispatched through
  **`0x3783d4`** (the `objc_msgSend` equivalent). No named `objc_msgSend`/`objc_getClass` export
  (Apportable inlines/renames); `sel_registerName` @ `0x3775e0` IS exported.
- **The cipher is a 3-call stream API** (recovered from `DataWithContentsOfFile:Password:` @ `0x64ea98`):
  ```
  ctx = stack buffer (~0x1080, from `sub sp,#0x4c` + `sub sp,#0x1000`)
  0x650090(ctx)                 ; cipher_init
  0x650570(ctx, password)       ; cipher_setkey   (r1 = the Password NSString/bytes)
  0x65085c(ctx, data+8, len-8)  ; cipher_process  (decrypts in place; skips the 8-byte magic)
  ```
  `DataDecryptedFromData:` also applies the nibble-swap pass. **If `0x650090/0x650570/0x65085c` are
  msgSend-free leaves, call them directly** in unidbg (`module.callFunction`, the proven getter
  pattern) with a malloc'd ctx + the level body + the key — no ObjC dispatch needed. The one missing
  input is the **password value** (the constant the caller passes); recover it by resolving the no-pw
  method's PIC load statically, or by getting unidbg's `objc_msgSend` to dispatch (try forcing
  init / `__objc_exec_class`) and hooking `0x650570`'s `r1`.

### Next steps — two routes (Phase-2 blocker)

- **Route B — unidbg dynamic (DISPATCH NOW WORKS — see breakthrough below). RECOMMENDED.**
  *Breakthrough (this session):* unidbg ObjC `msgSend` dispatch failed only because `vm.loadLibrary`
  was called with `forceCallInit=false` → `init_array`/`__objc_exec_class` never ran → classes
  unregistered. **`loadLibrary(so, true)` registers the classes and dispatch works.** Two more gotchas
  solved: (1) `module.callFunction` treats each `Long` arg as a **64-bit value spanning a register
  pair** (arg0→r0:r1, arg1→r2:r3) — pass **`int`s** so each lands in one register (r0,r1,r2,r3);
  (2) `DataWithContentsOfFile:Password:` (IMP `0x64ea98`) ignores `self`, so call it with
  `(0, 0, pathNSString, pwNSString)`; it has an early `[path length] >= 8` check so the path must be
  ≥8 chars (use `"1/1_1.dat"`). With a fabricated `NSConstantString` path + `forceCallInit`, the IMP
  now runs the FULL decrypt path: **`cipher_setkey` and `cipher_process` both fire.** Set the VFS via
  `AndroidEmulatorBuilder.setRootDir(rootfs)` (builder mutates; call as a statement, not chained).
  *Refined finding:* `0x64ea98` is really the **data decryptor** — it calls `[arg bytes]`/`[arg length]`
  on r2 and decrypts that buffer (it does NOT read a file; passing a path string made it "decrypt" the
  path). Passing a fabricated object over the real level bytes, `cipher_process` runs on real data
  (`len≈55k`). *Last mile:* the fabricated object reused the **NSConstantString** isa (a *string*
  class), so `bytes`/`length` returned string-semantic values (≈0 ptr) for binary data. **Need a real
  `NSData`:** get the NSData class (`objc_getClass("NSData")` — locate it near the `'NSData'` cstr use;
  `0x37295c` is called with both selectors and `"NSData"`, so it may be a generic intern/lookup —
  probe its return), then `+[NSData dataWithBytes:length:]` (IMP `0x398c48`) to wrap the level bytes,
  pass to `0x64ea98`, and read the result via `objc_msgSend(result, sel_registerName("bytes"/"length"))`.
  Password nil → `setkey(key=0)` default branch; if wrong, hook `cipher_setkey` (`0x650570`) `r1` to
  capture the constant. Harness `LevelDecrypt.java` implements everything up to this point.

- **Route B (old framing) — unidbg, calling the cipher-core C functions directly.**
  *Finding from the WIP harness* ([tools/unidbg/.../LevelDecrypt.java](../tools/unidbg/src/main/java/com/resurrect/LevelDecrypt.java)):
  the harness loads the lib, fabricates a valid `NSConstantString` path (`isa=0xb9ae00` read from the
  const string at `0xc54ad0`), and **enters** the no-pw `DataWithContentsOfFile:` (`0x64f378`) — but it
  returns nil **without any file IO**, and the hook on `DataWithContentsOfFile:Password:` never fires.
  Conclusion: **ObjC `msgSend` dispatch doesn't reach IMPs in unidbg** for this Apportable runtime (the
  original crack only ever called *leaf getter IMPs directly*, never via dispatch). So every high-level
  Foundation method bails the same way.
  **→ Next: call the cipher-core leaf functions `0x650090` / `0x650570` / `0x65085c` DIRECTLY** (raw
  buffers + key, no dispatch — the proven `module.callFunction` pattern). First disassemble them to get
  signatures + confirm they're msgSend-free, and trace how the password becomes the keystream. Run
  `mvn -q exec:java -Dexec.mainClass=com.resurrect.LevelDecrypt` (JDK17, `MAVEN_OPTS=-Djava.library.path=$PWD/natives`).
- **Route A — full static reverse** of those same core functions for an offline Python codec (no unidbg).
  Harder (the keystream KDF), but yields a portable codec. Fabrication/IMP anchors above still apply.

> Analyzed Ghidra project: `/tmp/revenant-re/proj1` (re-import ~minutes if `/tmp` is cleared).
> Scripts: `/tmp/revenant-re/Find*.java`. The writer `+[NSData ArchiveRootObject:ToFile:Password:]`
> (`0xa6dbed` selector) is the re-encrypt path for the level editor.

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
