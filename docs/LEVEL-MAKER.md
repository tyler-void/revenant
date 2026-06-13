# Bike Rivals 1.5.2 — Level Maker Feasibility

**Verdict: feasible and *easier* than the unlock work** — the format is decryptable +
JSON (human-readable), and the engine ships a symmetric encrypting writer.

## Where level geometry lives — FOUND
Per-level files `assets/unpack/<world>_<level>.dat` (e.g. `1_1.dat` … `4_15.dat`, plus
universe sub-levels `2_1_1.dat`); ~130 files, 14–130 KB. Confirmed by path-format strings
in `libgame.so`: `%d/%d_%d.dat` and `%d_%d/%d_%d_%d.dat`.
NOT the `.ccbi` (those are all UI scenes), NOT `WorldDefinition.plist` (world-select
catalog only), NOT the `g_*.dat` ghosts (separate `GhostFromData:` path — the `.dat`
name collision was a red herring).

## Format
- **Container** (same shape as the save): `"<ascii content length>\0"` + ciphered body
  + short trailer. e.g. `1_1.dat` = `"80235\0"` + 80235 bytes + 7-byte trailer.
- **Cipher**: symmetric **keystream-XOR stream cipher** (not AES — lengths aren't
  16-aligned). Across 20 level files ciphertext bytes 2–9 are byte-identical
  (`[redacted]` = shared plaintext header under a shared keystream), then diverge.
  ⚠️ **Different, harder cipher than the save.** Confirmed empirically on `1_1.dat`
  (`"80235\0"` + 80235 body + 7-byte trailer `9c66d4a00033f9`): the body's autocorrelation
  is **flat (~0.004 ≈ random) at every shift 1–32**, and column-mode finds no key — so it is
  NOT a short repeating-XOR (the save's `[redacted-key]` won't touch it, and neither will
  the column-mode trick that cracked the save). It's a real **stream cipher with a long
  non-repeating keystream** (RC4/PRNG/hash-derived). Recovering it therefore REQUIRES reading
  the keystream derivation out of `libgame.so` (the `NSData` cipher category below) — there's
  no statistical shortcut. This is the one genuinely harder step vs. the save.
  Implemented as an `NSData` category in `libgame.so`:
  `+DataWithContentsOfFile:Password:`, `+DataDecryptedFromData:Password:`,
  and the **writer** `+ArchiveRootObject:ToFile:Password:` (so re-encryption is by design).
- **Plaintext = JSON** (TouchJSON `CJSONDeserializer`). A level ≈ JSON array of
  `gameObject` dicts (`createObjectWithTitle:andType:`, `initWithDictionary:`,
  `position`/`rotation`/`scale`), referencing element sprite names from
  `elements_default.plist` (terrain `ter1-20`, borders `bor*`, foreground `for*`, ramps,
  rings `ring`, `checkpoint`, `finish`).
- **Terrain = Catmull-Rom/cardinal spline → Box2D edge chain**:
  `createNormalSplineSegmentV1:V2:V3:V4:`, `ctrlVertexes_`/`vertexes_`,
  `getB2VertexArray:`. Track = array of control vertices sampled into a Box2D chain.

## Difficulty: MODERATE, very tractable
Container is solved (same scheme as `data.dat`), plaintext is JSON (no opaque binary).
Two bounded unknowns: (1) recover the **asset password/keystream** (the save key doesn't
carry over — but the cross-file shared 8-byte prefix enables a known-plaintext bootstrap,
or pull the constant Password from `+DataWithContentsOfFile:Password:` in Ghidra), and
(2) document the level JSON schema. Estimate: a few sessions to a readable JSON dump; a
weekend-scale GUI tool after (spline-with-handles + drag-drop element palette + export
JSON → re-encrypt via the engine's writer). No public Bike Rivals level tooling exists.

## Recommended first step
Decrypt one level (`1_1.dat`) to JSON:
1. In Ghidra, find `+[NSData DataWithContentsOfFile:Password:]` / `DataDecryptedFromData:`
   in `libgame.so`; read the constant Password + keystream derivation.
2. Apply to `1_1.dat`; confirm valid UTF-8 JSON from the shared 8-byte header.
That single artifact validates cipher + JSON + schema and exposes the spline/object
format; a read-only viewer → editor follows.
