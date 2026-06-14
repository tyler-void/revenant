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
     Fix (from the user's proven New-Year-2026 mod, ref/re-stuffs):
       - add HIGH_SAMPLING_RATE_SENSORS permission,
       - register() always registers (no isEnabled gate),
       - unregister() neutered (sensor stays on),
       - onSensorChanged() fixed landscape mapping; we also drop its isEnabled
         gate so it processes even if the game never calls setEnabled(true).

Usage:  python3 apply_patches.py <decode_dir> [--no-tilt]
"""
import sys, os, json

HERE = os.path.dirname(os.path.abspath(__file__))
REF_ACC = os.path.join(HERE, "..", "ref", "re-stuffs", "bike-rivals", "smali",
                       "com", "miniclip", "input", "MCAccelerometer.smali")
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


def patch_tilt(root):
    # use the user's proven MCAccelerometer VERBATIM (force-register + neutered
    # unregister + onSensorChanged isEnabled guard). The isEnabled guard is load-
    # bearing: it stops the force-registered sensor from invoking the *native*
    # onSensorChanged before libgame.so has bound it (otherwise UnsatisfiedLinkError).
    ref = open(REF_ACC).read()
    # add a setEnabled log so we can confirm the game enables tilt (opens the guard)
    se = (".method public static setEnabled(Z)V\n    .locals 1\n"
          '    .param p0, "enabled"    # Z\n\n    .prologue\n')
    if ref.count(se) == 1:
        ref = ref.replace(se,
            ".method public static setEnabled(Z)V\n    .locals 2\n"
            '    .param p0, "enabled"    # Z\n\n    .prologue\n'
            "    invoke-static {p0}, Ljava/lang/String;->valueOf(Z)Ljava/lang/String;\n\n"
            "    move-result-object v1\n\n"
            '    const-string v0, "BR_TILT"\n\n'
            "    invoke-static {v0, v1}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I\n\n")
    # The game never calls setEnabled(true) here, so the isEnabled guard stays
    # shut and tilt never forwards. Remove the guard (always forward) but wrap the
    # NATIVE onSensorChanged call in try/catch UnsatisfiedLinkError, so the early
    # events that fire before libgame.so binds the native method are swallowed
    # instead of crashing; forwarding begins the moment the native binds.
    guard = ("    sget-boolean v0, Lcom/miniclip/input/MCAccelerometer;->isEnabled:Z\n"
             "    if-nez v0, :cond_go\n"
             "    return-void\n")
    assert ref.count(guard) == 1, f"onSensorChanged guard anchor count={ref.count(guard)}"
    ref = ref.replace(guard, "    # isEnabled guard removed; native call is try/catch-guarded\n")

    native_call = ("    invoke-static {v0, v1, v2, v3, v4}, Lcom/miniclip/input/MCAccelerometer;->onSensorChanged(FFFJ)V\n\n"
                   "    return-void\n.end method")
    assert ref.count(native_call) == 1, f"native-call anchor count={ref.count(native_call)}"
    ref = ref.replace(native_call,
        "    :try_start_0\n"
        "    invoke-static {v0, v1, v2, v3, v4}, Lcom/miniclip/input/MCAccelerometer;->onSensorChanged(FFFJ)V\n"
        "    :try_end_0\n"
        "    .catch Ljava/lang/UnsatisfiedLinkError; {:try_start_0 .. :try_end_0} :catch_0\n\n"
        "    return-void\n\n"
        "    :catch_0\n"
        "    move-exception v0\n\n"
        "    return-void\n.end method")

    open(os.path.join(root, ACC), "w").write(ref)
    print("[tilt] MCAccelerometer: force-register, neutered unregister, always-forward + try/catch native call")
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
        patch_tilt(root)
    if "--no-native" not in sys.argv:
        patch_native(root)
    if "--no-perms" not in sys.argv:
        patch_permissions(root)
    print("done")
