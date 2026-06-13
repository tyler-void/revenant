# Vision — Revenant

> *Where we're going and why. The north star for every decision.*

## The goal

Bike Rivals (Miniclip, 2014) is a dead game — its servers are gone, its
achievements never register, it's delisted. **Revenant brings it back and turns
it into a living, community-driven modding scene** — in the spirit of the PvZ 2
and Bad Piggies mod communities and the ScummVM / Ship-of-Harkinian preservation
ethos.

The north star: **anyone can create, share, and play custom Bike Rivals content
— levels and bikes — without the original servers, on their own legally-owned copy.**

## Pillars

1. **Level editor** — decode any level, edit it visually (terrain splines,
   objects, properties), re-encrypt to a device-loadable file. *(beta)*
2. **Bike editor** — modify + clone bikes (physics, sprites). *(done)*
3. **Procedural generator** — endless, seeded, *challenging-but-fair* tracks
   that mirror the real game's difficulty progression.
4. **Delivery mechanism** — custom content into the game: the empty **World 5**
   slot, and eventually a runtime mod-loader (external `mods/` folder) so content
   is drop-in, no rebuild.
5. **Offline revival** — unlimited fuel, all worlds, and a local achievement tab
   replacing the dead Google-Play-Games hooks.
6. **A preservation org** — a public home for community levels/bikes (content
   files only, BYO game), modeled on ScummVM's BYO-assets pattern.

## Principles (non-negotiable)

- **BYO-original / methods-only.** This repo ships *no* game bytes — no APK,
  assets, decompiled code, or cipher keys. Players bring their own legally-owned
  copy. Distributing the patched APK is the legal red line. See [LEGAL.md](LEGAL.md),
  [PRESERVATION-PLAYBOOK.md](PRESERVATION-PLAYBOOK.md).
- **Evidence-based.** Every claim is verified — ideally on real hardware. We
  don't guess when we can measure.
- **Document the journey.** Hard-won knowledge goes in the repo (see
  [research.md](research.md), [lesson.md](lesson.md), [steps.md](steps.md)) so
  the next person — human or AI — doesn't re-derive it.
- **Community-refined.** Ship usable betas; let contributors push them forward.

## Definition of done (the dream demo)

A player installs a modded base APK once, opens **World 5**, and rides a feed of
community-made and procedurally-generated tracks — each one a fair, interesting
challenge — having created their own in a browser editor and shared it as a small
file. No servers. No piracy. Just a dead game, alive again.
