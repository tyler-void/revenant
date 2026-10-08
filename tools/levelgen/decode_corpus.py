#!/usr/bin/env python3
"""Decode every level .dat into a LOCAL corpus (decrypted game data — kept OUTSIDE the repo).

  python3 tools/levelgen/decode_corpus.py [--out ~/.cache/revenant/corpus]

Reads build/work/assets/unpack/<w>_<l>.dat and <w>_<u>_<l>.dat (universe sub-levels), decrypts them
all in ONE JVM session with the unidbg oracle (LevelCodec BR_MODE=batch, key from the editor's
keys.json — never committed), and writes <lid>.level.json per level.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools", "level-editor"))
import leveldec as ld  # noqa: E402

LEVEL_RE = re.compile(r"^(\d+)_(\d+)(?:_(\d+))?\.dat$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.expanduser("~/.cache/revenant/corpus"))
    a = ap.parse_args()
    key = ld.level_key()
    if not key:
        sys.exit("no level key in tools/level-editor/levels/keys.json (capture it first, see docs/steps.md)")
    raw_dir = os.path.join(a.out, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    dats = sorted(p for p in glob.glob(os.path.join(ld.UNPACK, "*.dat")) if LEVEL_RE.match(os.path.basename(p)))
    manifest = os.path.join(a.out, "manifest.tsv")
    with open(manifest, "w") as f:
        for p in dats:
            f.write(f"{p}\t{os.path.join(raw_dir, os.path.basename(p)[:-4] + '.raw')}\n")
    env = dict(os.environ, JAVA_HOME=os.environ.get("JAVA_HOME", "/usr/lib/jvm/java-17-openjdk"),
               BR_MODE="batch", BR_KEY=key, BR_IN=manifest)
    print(f"decrypting {len(dats)} levels (one JVM session)…", flush=True)
    r = subprocess.run(["mvn", "-q", "-Dexec.mainClass=com.resurrect.LevelCodec", "compile", "exec:java"],
                       cwd=ld.UNIDBG, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=1800)
    tail = [ln for ln in (r.stdout + r.stderr).splitlines() if "batch done" in ln or "FAIL" in ln or "ERR" in ln]
    print("\n".join(tail[-10:]))
    ok = 0
    for p in dats:
        lid = os.path.basename(p)[:-4]
        raw = os.path.join(raw_dir, lid + ".raw")
        if not os.path.exists(raw):
            print("missing", lid)
            continue
        with open(raw, "rb") as f:
            level = ld.raw_to_level(f.read())
        with open(os.path.join(a.out, lid + ".level.json"), "w") as f:
            json.dump(ld.level_to_json(level), f)
        ok += 1
    print(f"wrote {ok}/{len(dats)} levels to {a.out}")


if __name__ == "__main__":
    main()
