# 🛡️ The Preservation Playbook

> How modders, decompilers, and game-preservation projects keep their repos safe — distilled
> from a decade of takedowns and lawsuits, and applied to this project. **Community practice, not
> legal advice** (the Vietnam-specific copyright analysis is in [`LEGAL.md`](LEGAL.md)). This is also
> the seed of a future dedicated preservation org.

## The one rule everything reduces to

> **You can publish your own work — code, diffs, methodology, docs. You can never publish someone
> else's bytes (the APK, assets, prebuilt binaries) or DRM keys / turnkey circumvention tools.**

Every project that got crushed shipped copyrighted content or a DRM-breaker. Every project that
survived shipped only its own diff + "**bring your own legal copy.**" The SM64 *decompilation source*
has sat on GitHub untouched for years; SM64 *PC-port binaries* (which bake in Nintendo's assets) get
DMCA'd on sight. Same code knowledge — only the *binary* is the crime.

## Jurisdiction: two legal systems apply at once

- **GitHub's takedown process runs on US DMCA**, regardless of where you live (GitHub is a US company).
- **Your substantive infringement liability runs on your local law** (for this author, Vietnam — see
  [`LEGAL.md`](LEGAL.md)).

So US DMCA decides *how your repo gets taken down and how you fight it*; local law decides *whether
you're liable*.

## The risk gradient — what to host, what never to host

| What you host | Exposure | Why |
|---|---|---|
| Reimplementation / source / patch scripts (no assets) | 🟢 Low | Your own copyrightable work; user must already own the game |
| Tools (extractors, patchers, decompilers, editors) | 🟢 Low | Interoperability/functional; reverse engineering for interop is defensible |
| Documentation / metadata (file formats, write-ups, hashes) | 🟢 Very low | Facts + original editorial; no assets distributed |
| Decompiled source (matching, no assets) | 🟡 Low–med | Treated as documentation; risk rises the moment assets/ROM data are committed |
| Prebuilt binaries that bundle game assets | 🔴 High | This is what triggers the DMCA takedowns |
| Playable ROMs / APKs / ISOs | 🔴 High | Direct distribution of the copyrighted work — the primary target |
| **DRM keys / turnkey decryptors / one-click crackers** | 🔴🔴 Highest | Anti-circumvention (DMCA §1201 / VN Art. 28) — lawsuits & injunctions, not just takedowns |

**Foundational case law that makes "the engine is mine, the assets are yours" defensible:**
*Sega v. Accolade* (1992) — disassembly for interoperability can be fair use; *Sony v. Connectix*
(2000) — an emulator is legal *because it contains none of the original's code*.

## Case studies (one-line lessons)

| Project | Outcome | Lesson |
|---|---|---|
| pret / sm64decomp | Alive for years | Source + build scripts + SHA-checked BYO ROM is the safe harbor |
| SM64 PC port (binaries) | Binaries DMCA'd; source survives | The binary is illegal even when the source isn't — make users build it |
| GTA re3 / reVC | DMCA'd; lawsuit later dismissed/settled (2023) | Even clean-room code can be litigated by a determined publisher — keep clean-room records |
| AM2R · Pokémon Uranium/Prism | DMCA'd / C&D | A standalone branded product using the IP/assets/trademark = takedown, even when free |
| **yuzu (Tropic Haze)** | **$2.4M settlement + injunction (2024)** | **Shipping DRM keys / runtime decryption is the §1201 bright line — far worse than copyright alone** |
| Dolphin on Steam | Steam release blocked (2023) | Bundling a decryption key (Wii Common Key) is the dangerous part; the emulator itself lived |
| Skyline (Android) | Shut down (2023) | Association with key-dumping (Lockpick) + enabling pre-launch piracy draws the fire |
| ScummVM · OpenMW · OpenRA · devilutionX · Ship of Harkinian | Alive for years (even under Nintendo) | **BYO-assets clean-room reimplementation is the most durable model in existence** |
| The Cutting Room Floor · HG101 · VGHF | Stable, respected | Documentation / editorial / metadata is among the safest models, and builds legitimacy |
| Internet Archive (CDL) | **Lost — Hachette v. IA (2d Cir. 2024)** | Don't build on "lend/stream the actual copyrighted work." Borrow IA's *mission*, not its lending |

**Pattern:** copyrighted assets → a *takedown* (reversible, survivable if you're source-only). DRM
circumvention (keys/decryptors) → *lawsuits* and permanent injunctions. Commercial use + trademark
escalate everything.

## The anti-circumvention line (DMCA §1201 / VN Art. 28)

This is a *separate* offense from copyright infringement, and the most dangerous one. Stay on the
research/interoperability side:
- **§1201(f) interoperability**: lawfully own the copy, circumvent *only* to learn the interface you
  need, keep the result non-infringing.
- **Describe the method; don't ship the circumvention.** Document *how* to decrypt your own save on
  your own device; let the user run it on their own file. **Never bundle extracted keys or a
  one-click cracker.** A documented method ≠ a trafficked tool — that distinction is the whole game.
- For this project: the tilt fix is clean *error-correction*; the content-unlock patches are the grey
  part — framed as interop/research on your own copy, applied locally via the build script, never
  distributed as a packaged cracker or a prebuilt APK.

## ✅ DO / ❌ DON'T

**DO**
- Ship the **diff/patch + build script only**; make the user run it against **their own** original.
- **Checksum the expected original** (BPS/UPS do this; we ship `CHECKSUMS` + a `build.sh` SHA gate) so
  the artifact is provably useless without the user's own correct copy.
- Lead with a **disclaimer**: no assets, BYO legal copy, not affiliated, educational/interop/preservation.
- License *your* original work clearly (this repo: GPLv3 code / CC BY-SA docs).
- Keep clean-room / methodology notes; keep it **non-commercial**; avoid the trademark in branding.
- **Mirror off GitHub** (Codeberg/GitLab/local) so a takedown can't erase the work.

**DON'T**
- ❌ Host or bundle the **original or built APK**, or any binary that bakes in assets.
- ❌ Ship **DRM keys** or a **one-click cracker** (the §1201 bright line).
- ❌ Redistribute **extracted assets** (art, audio, fonts) or decompiled game code.
- ❌ Use the **trademark/branding** as if endorsed, or build a standalone branded product.
- ❌ Go **commercial**, or time a release to compete with the rights-holder.

## How a GitHub DMCA actually plays out

- **Notice → ~1 business day to fix** (not instant deletion). There is a documented **one-time
  second chance** to re-enable if you miss the window.
- **Forks are NOT auto-removed** — the claimant must *name* them (or invoke the >100-fork-network
  exception with specific attestation language). This is why **mirroring buys resilience**.
- **Counter-notice** re-enables your content in 10–14 days *unless the claimant sues* — but it is a
  sworn statement that hands over your real identity and consents to suit. **Do not file one casually
  on genuinely infringing content.** Use it only when you're confident the content is non-infringing.
- `github.com/github/dmca` is a public archive of every actionable notice — useful to see what
  triggers action and how winning counter-notices are worded.

## Identity & a dedicated preservation org

- A **pseudonymous handle still earns full credit** — the handle *is* the reputation. But it only
  protects you if it's clean end-to-end: every git commit embeds author name + email, so a real-name
  commit history under a pseudonymous handle is a false sense of safety.
- A **dedicated org** (vs personal account): contains blast radius (a strike doesn't endanger your
  main account), enables co-maintainers + ownership continuity, and signals legitimacy. You can own
  your work *with credit* **and** run it under an org — these aren't in tension.
- **Starting one well:** brand for preservation (*Preservation / Archive / Heritage*, never
  *ROMs/free downloads*); publish an org-wide "no copyrighted assets / BYO original / notice-and-
  takedown" policy + `CONTRIBUTING.md`; architect engine/tools separately from assets; go
  multi-maintainer; mirror everything.

## Ethics & legitimacy (your strongest asset)

Lead with **cultural preservation, not piracy.** The Video Game History Foundation found **~87% of
pre-2010 US games are "critically endangered."** Dead/delisted mobile games like Bike Rivals are the
purest case — so **document the rights-holder's absence** (Miniclip shut the servers and delisted the
title) and frame the work as letting people run software they own on modern hardware
(interoperability + preservation), not free consumption of a live product.

## How THIS repo applies the playbook

- ✅ **No game bytes** — `.gitignore` excludes the APK, assets, decompiled code, the malware sample,
  and the keystore. Everything committed is original work.
- ✅ **BYO-original + checksum gate** — `CHECKSUMS` + a `build.sh` SHA-256 check; the build refuses the
  wrong file (and the malware "mods").
- ✅ **Method, not a cracker** — we ship `build.sh` + `apply_patches.py` that run locally on *your*
  copy; we do **not** ship a built APK, extracted keys, or a one-click tool.
- ✅ **Disclaimer + scoped license** — `NOTICE`, the README disclaimer, GPLv3 (code) / CC BY-SA (docs),
  claiming only the original work.
- ⬜ **To do if it grows:** mirror to a second host; if it becomes an org, add the org-wide policy +
  `CONTRIBUTING.md` and a second maintainer.

## Sources

- RomHacking.net policy · pret/pokered + pokecrystal (`roms.sha1`, `make compare`) · decomp.me FAQ ·
  BPS format spec (CRC32 source/target).
- yuzu/Tropic Haze settlement · Ryujinx · Dolphin-on-Steam (Wii Common Key) · Skyline/Lockpick ·
  GTA re3/reVC · AM2R · Pokémon Uranium/Prism.
- EFF *Coders' Rights / Reverse Engineering FAQ* (§1201, §1201(f), clean-room) · *Sega v. Accolade* ·
  *Sony v. Connectix*.
- GitHub DMCA Takedown Policy + Counter-Notice guide + `github/dmca` archive · youtube-dl/RIAA + EFF
  reinstatement.
- License/disclaimer examples: `zeldaret/tp` (CC0), `zeldaret/botw`, devilutionX.
- ScummVM · OpenMW · OpenRA · devilutionX · Ship of Harkinian (Shipwright) · The Cutting Room Floor ·
  Hardcore Gaming 101 · Video Game History Foundation · *Hachette v. Internet Archive* (2d Cir. 2024).

> **Community practice, not legal advice.** Most case law above is US-centric (DMCA); your liability
> is local. For a real go/no-go, ask a lawyer. See [`LEGAL.md`](LEGAL.md).
