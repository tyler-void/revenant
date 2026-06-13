#!/usr/bin/env python3
"""
Revenant Bike Editor — core engine.

Bike physics live in PLAINTEXT binary plists: assets/unpack/<Bike>Pref.plist,
under Entities[0].Properties. This tool reads/modifies those knobs and can clone
a bike (copy its Pref + sprite atlas + texture under a new name).

It operates on the apktool-decoded tree (default build/work/assets/unpack); after
editing, run build/build.sh to repackage + sign the APK.

NOTE: writes re-encode the bplist via plistlib. The data round-trips exactly, but
cocos2d's parser acceptance should be device-verified on your first edited build
(see docs/JOURNEY.md — "verify on real hardware").

Usage:
  bikeedit.py list
  bikeedit.py get  <Bike>
  bikeedit.py set  <Bike> speedLimit=200 forceScale=2.0 tilt=1.2
  bikeedit.py clone <SrcBike> <NewName>
Bike names are without the "Pref.plist" suffix, e.g. MainBike, PizzaBike, MX3.
"""
import sys, os, plistlib, shutil, json, argparse

UNPACK = os.environ.get("BR_UNPACK", "build/work/assets/unpack")

# tunable knobs in Entities[0].Properties, with (min, max) UI hints (from docs/MODDING-MAP.md)
KNOBS = {
    "speedLimit":      (60.0, 260.0),
    "forceScale":      (0.5, 3.0),
    "nitroPerformance":(0.0, 3.0),
    "geyserPower":     (0.0, 3.0),
    "burnoutSpeed":    (0.0, 3.0),
    "maxWheelieSpeed": (0.0, 260.0),
    "tilt":            (0.2, 1.6),
    "anchorY":         (-2.0, 2.0),
    "scale":           (0.3, 2.5),
    "spritesScale":    (0.3, 2.5),
    "poseValue":       (0.0, 1.0),
}

def pref_path(bike):
    return os.path.join(UNPACK, bike + "Pref.plist")

def list_bikes():
    out = []
    for fn in sorted(os.listdir(UNPACK)):
        if fn.endswith("Pref.plist"):
            out.append(fn[:-len("Pref.plist")])
    return out

def load_props(bike):
    p = pref_path(bike)
    if not os.path.exists(p):
        raise SystemExit("no such bike: %s (%s)" % (bike, p))
    with open(p, "rb") as f:
        d = plistlib.load(f)
    return d, d["Entities"][0]["Properties"]

def save_props(bike, d):
    p = pref_path(bike)
    with open(p, "wb") as f:
        plistlib.dump(d, f, fmt=plistlib.FMT_BINARY)

def cmd_list(_):
    bikes = list_bikes()
    print("%-18s %8s %8s %6s %6s" % ("BIKE", "speed", "force", "tilt", "nitro"))
    for b in bikes:
        try:
            _, pr = load_props(b)
            print("%-18s %8.1f %8.2f %6.2f %6.2f" % (
                b, pr.get("speedLimit", 0), pr.get("forceScale", 0),
                pr.get("tilt", 0), pr.get("nitroPerformance", 0)))
        except Exception as e:
            print("%-18s  (error: %s)" % (b, e))
    print("\n%d bikes. Knobs: %s" % (len(bikes), ", ".join(KNOBS)))

def cmd_get(a):
    d, pr = load_props(a.bike)
    print("# %s — Entities[0].Properties (tunable knobs)" % a.bike)
    for k in KNOBS:
        if k in pr:
            print("  %-18s = %s" % (k, pr[k]))

def cmd_set(a):
    d, pr = load_props(a.bike)
    changed = []
    for kv in a.assignments:
        if "=" not in kv:
            raise SystemExit("bad assignment %r (want key=value)" % kv)
        k, v = kv.split("=", 1)
        if k not in KNOBS:
            raise SystemExit("unknown knob %r (known: %s)" % (k, ", ".join(KNOBS)))
        old = pr.get(k)
        pr[k] = float(v)
        changed.append((k, old, pr[k]))
    save_props(a.bike, d)
    for k, old, new in changed:
        print("  %s: %s -> %s" % (k, old, new))
    print("wrote %s  (now run build/build.sh)" % pref_path(a.bike))

def cmd_clone(a):
    src, new = a.src, a.new
    if not os.path.exists(pref_path(src)):
        raise SystemExit("no such source bike: %s" % src)
    copied = []
    for suffix in ("Pref.plist", ".plist", ".png"):
        s = os.path.join(UNPACK, src + suffix)
        if os.path.exists(s):
            dst = os.path.join(UNPACK, new + suffix)
            shutil.copy2(s, dst)
            copied.append(os.path.basename(dst))
    print("cloned %s -> %s : %s" % (src, new, ", ".join(copied) if copied else "(no files!)"))
    print("NOTE: the bike ROSTER (which bikes the game offers) is not in a plist — it's in")
    print("      ProductList.dat/Shop.dat (encrypted) or libgame.so. Registering a NEW bike")
    print("      entry is Phase-1 open work (see docs/ROADMAP.md). Modifying/reskinning the")
    print("      copied files works; the game won't *list* the new bike until it's registered.")

def main():
    ap = argparse.ArgumentParser(description="Revenant bike editor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(fn=cmd_list)
    g = sub.add_parser("get"); g.add_argument("bike"); g.set_defaults(fn=cmd_get)
    s = sub.add_parser("set"); s.add_argument("bike"); s.add_argument("assignments", nargs="+"); s.set_defaults(fn=cmd_set)
    c = sub.add_parser("clone"); c.add_argument("src"); c.add_argument("new"); c.set_defaults(fn=cmd_clone)
    a = ap.parse_args()
    if not os.path.isdir(UNPACK):
        raise SystemExit("decoded assets not found at %s (run build/build.sh once, or set BR_UNPACK)" % UNPACK)
    a.fn(a)

if __name__ == "__main__":
    main()
