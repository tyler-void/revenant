# Lessons — gotchas & what works

> *Mistakes we made and rules we learned, so we never repeat them. Newest first.
> Each: the trap, the fix, why.*

## Level data / physics

- **Box2D ground must be wound COUNTER-CLOCKWISE.** A clockwise terrain polygon
  builds collision solid-side-DOWN → the bike falls straight through → the race
  never starts. (1_1's rideable terrain is 97/104 CCW.) *Fix:* reverse the vertex
  list so signed area > 0 (Y-up).
- **A level's `lid` must match the slot it's placed in.** A `.dat` swapped into
  `1/1_4.dat` must have `lid:"1_4"`; the game keys off it and rejects a mismatched
  level (decrypt succeeds — confirmed via the RVLEN keylog — but the scene never
  builds: no terrain, no bike, frozen). The generator takes `--lid` for this.
- **`refobjectList` / `mountedSprites` are entity ARRAY INDICES, not tags.** Rebuild
  or reorder the entity list and these references break. *Fix:* remap indices on
  assembly (the generator does this for the finish group).
- **Test generated levels in a NON-tutorial slot (levels 4+).** Levels 1–3 run the
  controls tutorial (`loadTutorial`), whose overlay logic depends on the bike
  becoming "ready"; a broken level hangs the tutorial and masks the real cause.
  Use a post-tutorial slot to isolate level bugs.
- **The ground is MANY small CCW polygons, not one big shape** (CONFIRMED across
  130 levels: median 46 polygons/level, median 4 vtx, 72% ≤8 vtx). A single giant
  generated polygon builds an invalid Box2D body → bike falls through. *Fix:*
  generate the terrain as a strip of small CCW quad slabs (one per surface segment).
- **Type fidelity** in plists: int vs real usually doesn't matter (the game reads
  NSNumber), but be deliberate. JSON preserves int/float via the `.0`.
- **To make generated levels feel hand-built, CLONE real prefabs — don't invent
  entities.** Bare terrain + start + finish reads as empty next to hand-placed
  levels. Pull decoration `EditorSprite`s and `ExplosiveBarrel` groups straight
  from a decoded template (full Properties preserved → valid for that world) and
  reposition copies. Layer scenery by `z` for depth: trees behind (`z<0`), rocks/
  ground-detail on the surface (`z` 3–5), darkened foreground silhouettes (`z=13`).
- **Dynamic-body Vertexes are WORLD coords, not local.** An `ExplosiveBarrel`'s
  child physics body stores its box as absolute vertices around its `position`
  (e.g. ~1388, not ~0). To relocate it you must offset BOTH `position` AND every
  `Vertex` by the same delta (terrain slabs are the same). A `_translate_entity`
  helper that shifts both is the safe primitive.

## Cipher / unidbg

- **Run the unidbg oracle with `</dev/null`.** On an emulation exception unidbg
  drops into an interactive debugger that blocks on `stdin` forever (it froze a
  16-min background run). EOF makes it bail — the codec result is already written.
  The `UC_ERR_INSN_INVALID` at teardown is harmless.
- **`module.callFunction` marshals each `Long` arg as a 64-bit register PAIR.**
  Pass `int`s (one per register) or args land in the wrong registers.
- **`vm.loadLibrary(so, true)`** (forceCallInit) is required before `objc_msgSend`
  dispatch works (it runs the ObjC class-registration init).
- **The cipher is directional** (Blowfish, not a symmetric stream) — encrypt ≠
  decrypt; you need the encrypt-direction process fn (`0x6507d4`), found as the
  mirror of the decrypt one (`0x65085c`).
- **Frida crashes the 32-bit game** on attach → use unidbg + on-device code-cave
  ARM stubs for live capture instead.

## Native patching (libgame.so, ARM)

- **libgame.so is ARM, not Thumb.** `objc_method` layout is `{name, types, imp}`
  (the IMP is the THIRD word, at +8 from the selector-name pointer — not −4).
  Generic `objdump` reports `architecture: UNKNOWN!`; use Capstone in `CS_MODE_ARM`.
  Find an IMP by scanning PT_LOAD bytes for a 32-bit LE pointer to the selector
  string, then read the word at +8.
- **One mechanic can have MULTIPLE consume paths — patch them all.** Unlimited
  fuel looked done (gauge redirect + `useFuel:` NOP) but gas still "dried" mid-play.
  Cause: `consumeBars:` @0x6ab4b0 is a SEPARATE singleplayer per-attempt consume
  that does its own `gasBarsLeft -= count` (@0x6ab668). Fix: at entry it tests the
  game's own pause/unlimited byte flag and `bne`s to an exit returning SUCCESS
  without decrementing — force that branch unconditional (ARM cond nibble NE→AL,
  `0x1a`→`0xea`). Lesson: a display patch can mask a still-live gameplay path; trace
  the actual decrement + the caller's out-of-X check, not just the getter.
- **Reuse the game's own "skip" branch** instead of inventing one. Many gated
  routines already have a no-op/owned path guarded by a flag check; flipping that
  branch to unconditional is safer (correct return value, no side effects) than
  NOP-ing the body or forcing a different return.

## Rendering

- **The game's WebP atlases are non-standard** (12-byte VP8X, 4-byte dims, lossless
  ALPH) — ffmpeg/ImageMagick/Pillow all reject them. *Fix:* extract the standard
  VP8 (RGB) chunk, decode, chroma-key black→alpha.
- **Same frame name lives in multiple per-theme atlases** — resolve against the
  level's world or you render the wrong world's art (we drew world-3 ice in world 1).
- **Sprites are Y-up + clockwise rotation; the canvas is Y-down.** Use the proper
  composed transform, not ad-hoc `rotate()+scale(1,-1)` (which mirrored sprites).
- **Atlas art is HD texels; level coords are points** — divide sprite size by a
  content scale (~8.5 here; calibrate against the physics outline, it's the truth).
- **Sprites are trimmed in the atlas** — draw the trimmed region at its natural
  size + apply `spriteOffset`; don't stretch it to the source size (distorts ~10%).

## Build / tooling / repo

- **`build/build.sh` re-decodes from the clean original** (`rm -rf build/work`) —
  it WIPES any swapped-in files. To device-test an edited asset: swap into the
  already-decoded `build/work`, then `apktool b build/work` directly + sign +
  `adb install -r`. (Keystore must stay → `install -r` preserves the save.)
- **`.gitignore` has NO inline comments** — git treats `base/ # note` as a literal
  pattern matching nothing. Comments on their own lines. Always
  `git check-ignore -v <path>` to verify before committing.
- **Never let render-capture PNGs or decrypted caches get committed** — they're
  derived game art / circumvention material. Gitignore `tools/level-editor/{levels,
  textures}/`, `/*.png`, `*.level.json`; leak-check `git add -An | grep …` before commit.
- **`pkill -f <pattern>` can match its own shell** (exit 144). Kill a server by its
  listening port instead (`ss -ltnpH | grep :PORT`).
- **Device-verify everything.** The unidbg round-trip proves the cipher; only the
  phone proves it *plays*. Several "fixed" things were only truly confirmed on hardware.

## Working with the human

- Tyler is an expert player — **their eye on the real level is the best
  ground truth** when emulator/static analysis is ambiguous (e.g. the content scale).
- Surface honest tradeoffs and ask before sinking effort into deep RE rabbit holes.
