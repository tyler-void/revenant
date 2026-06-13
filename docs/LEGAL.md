# ⚖️ Legal Notes — Vietnamese Software Copyright (as of 2026)

> **RESEARCH, NOT LEGAL ADVICE.** This is a researched summary of Vietnamese statutes and reputable
> legal commentary as they stand in mid-2026. It is **not legal advice**, creates no lawyer–client
> relationship, and is no substitute for a licensed Vietnamese IP lawyer (*luật sư sở hữu trí tuệ*)
> reviewing the specific files before any real go/no-go. Copyright outcomes are fact-specific.

## TL;DR

| Scenario | Risk | Why |
|---|---|---|
| **(a)** Play it yourself, publish nothing | 🟢 **Low** | No distribution. Private modification of a game you own is essentially never enforced. |
| **(b)** Public repo of **only original work** — analysis, byte offsets, patch scripts, tooling; **no APK, no assets, no decompiled game code, no one-click cracker** | 🟡 **Low–moderate** | Your prose/scripts are *your* copyright. Residual risk is the **anti-circumvention** angle (Art. 28) if the method is seen as a circumvention tool. **This repo is built to be exactly this path.** |
| **(c)** Distribute the **patched/modified APK** | 🔴 **High** | Unauthorized reproduction + distribution of a derivative of the whole work. |
| **(d)** Distribute **original game assets** (art/audio/decrypted data/unmodified APK) | 🔴 **High** | Verbatim copying of protected expression. The classic infringement. |

**Two facts help us:** the project is **non-commercial** (keeps it out of criminal scope) and the
**rights-holder abandoned the title** (no one to complain — legally irrelevant, practically decisive).
**Two facts cut against:** software is **excluded** from the personal-study copying exception, and
Vietnam **has TPM/anti-circumvention provisions** that the save-decryption + binary-patching (the
*content-unlock* part especially) could implicate.

## The governing law (Vietnam, 2026)

Software is protected as a **literary work** (source *and* object code).

- **IP Law 50/2005/QH11**, amended **36/2009**, **42/2019**, and substantially **07/2022/QH15** (in
  force 1 Jan 2023).
- **Law 131/2025/QH15** — passed 10 Dec 2025, **effective 1 April 2026**. *This is the real "2026"
  instrument*, but its copyright content targets the **digital environment**: platform accountability,
  takedown duties, more civil remedies, higher statutory damages (up to ~1B VND). It **does not change
  the hobbyist analysis** — it tightens enforcement against platforms and commercial infringers.
- **Decree 17/2023/NĐ-CP** — implementing decree on copyright (116 articles).
- **Decree 131/2013/NĐ-CP** (amd. 28/2017) — *administrative* penalties (up to ~250M VND individual).
- **Penal Code Art. 225** — *criminal* infringement, but only at **commercial scale** (profit ≥50M
  VND, or damage ≥100M, or goods value ≥100M). A free hobby project doesn't reach these.

## Why software is on thinner ice than a book or song

- **No decompilation / interoperability exception.** Unlike the EU Software Directive (2009/24/EC)
  Art. 6, Vietnam has **no statutory right to decompile** for interoperability or study.
- **Software is excluded from the personal-study copying exception.** Art. **25(3)** expressly says the
  self-use copying exception "does **not apply** to … computer programs" (*"…không áp dụng đối với …
  chương trình máy tính."*). So "I'm just researching it" doesn't cover software the way it covers a book.
- **The only software-user rights** are **Art. 22(3)**: make **one backup copy** + **correct errors
  necessary for use**. Fixing the genuinely-broken motion controls is the closest thing to a real legal
  footing here (error-correction necessary for use). **Unlocking purchasable content is *not* error
  correction** — it's the weaker-ground part, even with the payment server dead.

## Anti-circumvention (Art. 28) — the riskiest technical element

The 2022 amendment added TPM/RMI provisions (Art. 28): it's infringement to **intentionally disable an
effective technological protection measure** *in order to commit an infringing act*, or to deal in
circumvention tools. The save-file **encryption is plausibly a TPM**; decrypting it and patching the
binary **to unlock paid content** is the framing most exposed to this. The statutory qualifier — *"in
order to commit an infringing act"* — means a **pure error-correction / interoperability motive is a
materially better posture** than "to unlock paid content."

## Abandonware changes nothing (legally)

Vietnam recognizes **no "abandonware" doctrine**. Copyright subsists for the full term regardless of
whether the publisher supports, sells, or lists the product. Miniclip's dead servers and delisting
**change nothing legally** — but they mean there is **realistically no complainant**, and Vietnamese
copyright enforcement is **complaint-driven and overwhelmingly commercial** (2026's nationwide crackdown
targets piracy platforms and commercial infringers, not hobbyists modding a game they own).

## Practical guidance for this repo

1. **Keep it to your own legally-acquired copy.** The build needs *you* to supply the original APK.
2. **Never commit / distribute**: the original APK, any game assets, decompiled game code, or the built
   modified APK. The `.gitignore` enforces this — the repo is methodology + offsets + tooling only.
3. **Frame as research / error-correction**, which it largely is (the tilt fix especially).
4. **Don't ship a polished one-click "cracker"** aimed at the public — that's the move that turns
   "research write-up" into "trafficking in a circumvention method." A documented pipeline that needs
   your own copy is materially different from a packaged cracker.
5. **If in any doubt, keep the repo private** (scenario **a**, 🟢) — or get a VN IP lawyer to review
   before going public.

### Sources
- IP Law 07/2022/QH15 · Law 131/2025/QH15 (eff. 1 Apr 2026, thuvienphapluat van-ban 675267) ·
  Decree 17/2023/NĐ-CP · Decree 131/2013/NĐ-CP · Penal Code Art. 225.
- Art. 22 (backup/error-correction), Art. 25(3) (software excluded from copying exception), Art. 28
  (TPM/RMI) — luathoangphi / apolatlegal / qdnd.vn analyses.
- Enforcement reality + 2026 crackdown — Rouse, Lexology, USTR. EU comparison — WIPO Lex (2009/24/EC).

> **RESEARCH, NOT LEGAL ADVICE.** Talk to a licensed Vietnamese IP lawyer before publishing.
