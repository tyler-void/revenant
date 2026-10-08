#!/usr/bin/env python3
"""
Revenant — Bike Rivals 1.5.2 smali patcher.

  1. UNLOCK  (MCInAppPurchases.smali + GoogleWrapper.smali)
     resurrectUnlockAll(): for every non-consumable SKU, consumeItem() then
     updateItemOwned(...,false) so updateItemOwned emits a restoration → native
     onItemOwned unlock. Called from the end of GoogleWrapper.syncInventory()
     (delegate ready, online). Offline + self-healing. (Note: in-game bikes are
     also coin-gated; the IAP flag only covers IAP content.)

  2. TILT  (MCAccelerometer.smali + AndroidManifest.xml)
     The game never enables the accelerometer on Android 13+ (confirmed via
     dumpsys: accelerometer never registers in-race), and Android 12+ blocks
     accelerometer access without HIGH_SAMPLING_RATE_SENSORS on this hardware.
     Fix (anchored edits on the decoded ORIGINAL smali — same semantics as the
     browser patcher's wasm dex_tilt_rewrite, so the CLI and web builds match):
       - add HIGH_SAMPLING_RATE_SENSORS permission,
       - register() always registers (no isEnabled gate),
       - unregister() neutered (sensor stays on),
       - onSensorChanged() rewritten: fixed landscape mapping, no isEnabled
         gate, native call wrapped in a catch-all (see patch_tilt).

Usage:  python3 apply_patches.py <decode_dir> [--no-tilt] [--no-native] [--no-perms] [--no-diag]
        --no-diag drops the BR_TILT setEnabled logcat line (the final, logging-free build).
"""
import sys, os, json

HERE = os.path.dirname(os.path.abspath(__file__))
# Shared declarative manifest (single source for CLI + browser patcher). Native byte-patches and
# the dropped-permissions list come from here so the two paths can never drift apart.
PATCH_MANIFEST = json.load(open(os.path.join(HERE, "..", "patches", "manifest.json")))

NON_CONSUMABLE_SKUS = (
    [f"com.miniclip.bikerivalsbike{i}" for i in range(1, 12)] +
    ["com.miniclip.bikerivals.christmasbike", "com.miniclip.bikerivals.infernandobike",
     "com.miniclip.bikerivalsworld2", "com.miniclip.bikerivalsworld3", "com.miniclip.bikerivalsworld4",
     "com.miniclip.bikerivalsinctank1", "com.miniclip.bikerivalsinctank2",
     "com.miniclip.bikerivals.unlimitedgas"]
)

MCP = "smali/com/miniclip/inapppurchases/MCInAppPurchases.smali"
GW  = "smali/com/miniclip/inapppurchases/providers/GoogleWrapper.smali"
ACC = "smali/com/miniclip/input/MCAccelerometer.smali"
MANIFEST = "AndroidManifest.xml"


def patch_unlock(root):
    p = os.path.join(root, MCP)
    s = open(p).read()
    L = [".method public static resurrectUnlockAll()V", "    .locals 3", "",
         '    const-string v0, "Google"', "", "    const/4 v2, 0x0", ""]
    for sku in NON_CONSUMABLE_SKUS:
        L += [f'    const-string v1, "{sku}"', "",
              "    invoke-static {v0, v1}, Lcom/miniclip/inapppurchases/MCInAppPurchases;->consumeItem(Ljava/lang/String;Ljava/lang/String;)V", "",
              "    invoke-static {v0, v1, v2}, Lcom/miniclip/inapppurchases/MCInAppPurchases;->updateItemOwned(Ljava/lang/String;Ljava/lang/String;Z)V", ""]
    L += ["    return-void", ".end method"]
    s = s.rstrip() + "\n\n" + "\n".join(L) + "\n"
    open(p, "w").write(s)

    p = os.path.join(root, GW)
    g = open(p).read()
    anchor = ("    invoke-interface {v0}, Landroid/content/SharedPreferences$Editor;->commit()Z\n\n"
              "    .line 131\n    return-void\n.end method")
    assert g.count(anchor) == 1, f"syncInventory anchor count={g.count(anchor)}"
    g = g.replace(anchor,
        "    invoke-interface {v0}, Landroid/content/SharedPreferences$Editor;->commit()Z\n\n"
        "    invoke-static {}, Lcom/miniclip/inapppurchases/MCInAppPurchases;->resurrectUnlockAll()V\n\n"
        "    .line 131\n    return-void\n.end method")
    open(p, "w").write(g)
    print(f"[unlock] resurrectUnlockAll ({len(NON_CONSUMABLE_SKUS)} SKUs) -> syncInventory")


def patch_manifest(root):
    p = os.path.join(root, MANIFEST)
    m = open(p).read()
    perm = '<uses-permission android:name="android.permission.HIGH_SAMPLING_RATE_SENSORS"/>'
    if perm in m:
        print("[manifest] permission already present"); return
    anchor = '<uses-permission android:name="android.permission.INTERNET"/>'
    assert anchor in m, "INTERNET permission anchor missing"
    m = m.replace(anchor, anchor + "\n    " + perm, 1)
    open(p, "w").write(m)
    print("[manifest] added HIGH_SAMPLING_RATE_SENSORS")


# Tilt fix as anchored edits on the decoded ORIGINAL MCAccelerometer (no external smali), mirroring
# the browser patcher's wasm dex_tilt_rewrite so the CLI and web builds ship the same fix. Every
# anchor is asserted before the single write: a half-applied fix is the CRASHING config — a
# force-registered sensor fires before libgame.so binds the native onSensorChanged(FFFJ)
# (UnsatisfiedLinkError), which the catch-all below swallows. See docs/TILT-FIX.md.
ON_SENSOR_CHANGED = """.method public onSensorChanged(Landroid/hardware/SensorEvent;)V
    .locals 7
    .param p1, "event"    # Landroid/hardware/SensorEvent;

    iget-object v0, p1, Landroid/hardware/SensorEvent;->sensor:Landroid/hardware/Sensor;

    invoke-virtual {v0}, Landroid/hardware/Sensor;->getType()I

    move-result v0

    const/4 v1, 0x1

    if-eq v0, v1, :cond_process

    return-void

    :cond_process
    iget-object v6, p1, Landroid/hardware/SensorEvent;->values:[F

    const/4 v0, 0x1

    aget v0, v6, v0

    const/4 v1, 0x0

    aget v1, v6, v1

    neg-float v1, v1

    const/4 v2, 0x2

    aget v2, v6, v2

    iget-wide v3, p1, Landroid/hardware/SensorEvent;->timestamp:J

    :try_start_0
    invoke-static {v0, v1, v2, v3, v4}, Lcom/miniclip/input/MCAccelerometer;->onSensorChanged(FFFJ)V
    :try_end_0
    .catchall {:try_start_0 .. :try_end_0} :catchall_0

    return-void

    :catchall_0
    move-exception v0

    return-void
.end method"""


def _method_span(s, header):
    # (start, end) of the method whose `.method` line is exactly `header`; end is past `.end method`.
    n = s.count(header + "\n")
    assert n == 1, f"[tilt] method {header!r} count={n} (not the 1.5.2 MCAccelerometer?)"
    a = s.index(header + "\n")
    return a, s.index(".end method", a) + len(".end method")


def _edit_method(s, header, old, new):
    a, b = _method_span(s, header)
    body = s[a:b]
    assert body.count(old) == 1, f"[tilt] anchor count={body.count(old)} in {header!r}"
    return s[:a] + body.replace(old, new) + s[b:]


def patch_tilt(root, diag=True):
    p = os.path.join(root, ACC)
    s = open(p).read()
    # register(): drop the isEnabled gate — the game only ever calls setEnabled(false) here, so the
    # gate kept the listener off; now it registers whenever onResume/onWindowFocusChanged call it.
    s = _edit_method(s, ".method private register()V",
                     "    sget-boolean v0, Lcom/miniclip/input/MCAccelerometer;->isEnabled:Z\n\n"
                     "    if-eqz v0, :cond_0\n\n", "")
    # unregister(): no-op, so once registered the sensor stays on across the lifecycle.
    a, b = _method_span(s, ".method private unregister()V")
    s = s[:a] + ".method private unregister()V\n    .locals 0\n\n    return-void\n.end method" + s[b:]
    # onSensorChanged(SensorEvent): fixed landscape map (gameX=sensorY, gameY=-sensorX, gameZ=sensorZ),
    # no isEnabled gate, native call wrapped in a catch-all.
    a, b = _method_span(s, ".method public onSensorChanged(Landroid/hardware/SensorEvent;)V")
    s = s[:a] + ON_SENSOR_CHANGED + s[b:]
    if diag:
        # BR_TILT logcat line on setEnabled, to see whether/how the game toggles tilt.
        s = _edit_method(s, ".method public static setEnabled(Z)V", "    .locals 1\n", "    .locals 2\n")
        s = _edit_method(s, ".method public static setEnabled(Z)V",
                         "    sput-boolean p0, Lcom/miniclip/input/MCAccelerometer;->isEnabled:Z\n",
                         "    invoke-static {p0}, Ljava/lang/String;->valueOf(Z)Ljava/lang/String;\n\n"
                         "    move-result-object v1\n\n"
                         '    const-string v0, "BR_TILT"\n\n'
                         "    invoke-static {v0, v1}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I\n\n"
                         "    sput-boolean p0, Lcom/miniclip/input/MCAccelerometer;->isEnabled:Z\n")
    open(p, "w").write(s)
    print("[tilt] MCAccelerometer: force-register, neutered unregister, landscape map + catch-all native call"
          + (" (+BR_TILT log)" if diag else ""))
    patch_manifest(root)


# --- NATIVE UNLOCK (libgame.so) ---------------------------------------------
# The real bike/world ownership is gated by native Objective-C checks in libgame.so, NOT by the
# encrypted save or NSUserDefaults. We force the unlock-check methods to return YES (mov r0,#1;bx lr)
# plus fuel/nitro patches. Each patch is asserted against its `expect` bytes before writing, so a
# different/non-1.5.2 libgame.so aborts loudly instead of corrupting.
#
# These offsets/bytes now come from the SHARED patches/manifest.json `native` section (single source
# for this CLI and the in-browser patcher — they can't drift). The manifest's per-patch `group`/`desc`
# document each; the CLI applies them all. (Bike ride/select GET-IT-NOW gate was NOT cracked — see
# docs/BIKE-UNLOCK-STATUS.md; worlds + tilt + bike-display + fuel + nitro are confirmed working.)
SO_REL = os.path.join(*PATCH_MANIFEST["native"]["file"].split("/"))
NATIVE_PATCHES = [
    (p["name"], int(p["off"], 16), p["expect"], p["patch"])
    for p in PATCH_MANIFEST["native"]["patches"]
]


# --- PERMISSION CULL (privacy, 2026) -----------------------------------------
# A 2014 game requests a pile of tracking/PII/ads/IAP/push permissions. Drop the sketchy + unused
# ones (same list the in-browser patcher uses — from the shared manifest's androidManifest section).
# KEEP the network trio — GameActivity does a startup connectivity check and throws SecurityException
# without ACCESS_NETWORK_STATE (device-confirmed) — plus sensors (tilt), vibrate, wake-lock, storage.
DROP_PERMS = PATCH_MANIFEST["androidManifest"]["dropPermissions"]


def patch_permissions(root):
    p = os.path.join(root, MANIFEST)
    lines = open(p).read().splitlines(keepends=True)
    out, removed = [], []
    for ln in lines:
        if "uses-permission" in ln and any(d in ln for d in DROP_PERMS):
            removed.append(ln.strip())
            continue
        out.append(ln)
    open(p, "w").write("".join(out))
    print(f"[perms] dropped {len(removed)} sketchy permissions "
          "(location/accounts/credentials/billing/push); network+sensors+storage kept")


def patch_native(root):
    p = os.path.join(root, SO_REL)
    data = bytearray(open(p, "rb").read())
    for name, off, orig, patch in NATIVE_PATCHES:
        n = len(orig) // 2
        cur = bytes(data[off:off + n]).hex()
        assert cur == orig, f"[native] {name} @ {hex(off)} bytes {cur} != expected {orig} (wrong libgame.so?)"
        pb = bytes.fromhex(patch)
        data[off:off + len(pb)] = pb
        print(f"[native] {name} @ {hex(off)} patched ({len(pb)}B)")
    open(p, "wb").write(data)


if __name__ == "__main__":
    root = sys.argv[1]
    patch_unlock(root)
    if "--no-tilt" not in sys.argv:
        patch_tilt(root, diag="--no-diag" not in sys.argv)
    if "--no-native" not in sys.argv:
        patch_native(root)
    if "--no-perms" not in sys.argv:
        patch_permissions(root)
    print("done")
