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
import sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
REF_ACC = os.path.join(HERE, "..", "ref", "re-stuffs", "bike-rivals", "smali",
                       "com", "miniclip", "input", "MCAccelerometer.smali")

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
# The real bike/world ownership is gated by native Objective-C checks in
# libgame.so, NOT by the encrypted save or NSUserDefaults (those only hold
# "notification-shown" flags). We force the unlock-check methods to return YES.
# Offsets located via ObjC method-metadata scan; (offset, expected original 8
# bytes) pairs are asserted before patching so a different libgame.so aborts
# loudly instead of corrupting. Patch = `mov r0,#1 ; bx lr` (ARM).
SO_REL = os.path.join("lib", "armeabi-v7a", "libgame.so")
RET_YES = "0100a0e31eff2fe1"   # mov r0,#1 ; bx lr
RET_NO  = "0000a0e31eff2fe1"   # mov r0,#0 ; bx lr
# Only CONFIRMED-EFFECTIVE patches are kept. The bike *ride/select* gate (the
# carousel's SELECT-vs-GET-IT-NOW decision) was NOT cracked: setter-force,
# selectCurrentBike-NOP, buyUnlock-NOP, and disableSelectButton-redirect all had
# no visible effect (the store reads owned-state through an objc_msgSend path that
# blind static patching can't reach, and no ARM exec env exists to trace it). See
# docs/BIKE-UNLOCK-STATUS.md. WORLDS + TILT + bike-display are confirmed working.
NATIVE_PATCHES = [
    # name,                       offset,   expected original bytes, patch bytes
    # --- WORLDS: all story worlds + DLCs (CONFIRMED by user) ---
    ("isWorldUnlocked:",          0x6b0df0, "f0492de914b08de2", RET_YES),
    ("isUniverseUnlocked:",       0x6a5094, "f04b2de90060a0e1", RET_YES),
    # --- BIKE GATE (unidbg-confirmed): the store's owned-check message-dispatches the
    # `unlocked` selector -> getter @0x5eea94 (reads BikeInfo.unlocked ivar @0xb). This is
    # THE one that flips SELECT-vs-GET-IT-NOW; forcing it YES should make bikes ownable.
    # (The earlier `purchased` getter reads a different ivar @0xc the gate ignores.) ---
    ("bike.unlocked",             0x5eea94, "14109fe514209fe5", RET_YES),
    # --- supporting bike-display patches (cosmetic: full-color + PURCHASED stamp) ---
    ("isBikeUnlocked:ArrayFile:", 0x6a3f24, "f04f2de91cb08de2", RET_YES),
    ("bike.purchased",            0x5eed14, "14109fe514209fe5", RET_YES),
    ("bike.revealed",             0x50d93c, "14109fe514209fe5", RET_YES),
    ("bike.isRevealed",           0x5edeb0, "f0492de914b08de2", RET_YES),
    ("bike.locked#1",             0x6b6124, "14109fe514209fe5", RET_NO),
    ("bike.locked#2",             0x6b77e4, "14109fe514209fe5", RET_NO),
    # --- UNLIMITED FUEL ---
    # There are TWO consume paths, and the first patch alone was insufficient:
    #   useFuel: @0x6b1ca8       — `gasBarsLeft -= amount; bx lr` (a leaf; MP/other path).
    #   consumeBars: @0x6ab4b0   — the SINGLEPLAYER per-attempt consume. THIS is the one
    #                              that actually drained the tank in play (user-reported:
    #                              gauge showed full but it "dried" and demanded a refill).
    # 1) NOP useFuel: to `bx lr` so its decrement never runs.
    ("useFuel:.noop",             0x6b1ca8, "30109fe5", "1eff2fe1"),  # bx lr
    # 2) consumeBars: does gasBarsLeft -= count (@0x6ab668) and returns success/failure;
    #    the caller shows OutOfGasPopup on failure. At entry it tests a byte flag (the
    #    game's OWN pause/unlimited path): `bne 0x6ab6d4` jumps to an exit that returns
    #    SUCCESS (r0=1) WITHOUT decrementing. Force that branch unconditional (cond NE->AL,
    #    0x1a->0xea) so the consume is always skipped-and-succeeds — no spend, no popup,
    #    even at 0 bars. This is the surgical fix the gauge redirect couldn't reach.
    ("consumeBars:.no-spend",     0x6ab4e4, "7a00001a", "7a0000ea"),  # bne->b skip-consume
    # 3) Belt-and-suspenders gauge: redirect gasBarsLeft getter @0x6b4e24 -> `b gasBarsTotal
    #    @0x6b4e6c`, so the reported fuel ALWAYS equals the full tank (gauge shows full) even
    #    if the stored ivar is momentarily low. off=0x6b4e6c-0x6b4e24-8=0x40 -> imm 0x10.
    ("gasBarsLeft->Total",        0x6b4e24, "14109fe5", "100000ea"),  # b gasBarsTotal
    # --- UNLIMITED NITRO + HELMETS (generic consumable manager; type1=nitro, type2=helmet):
    #   consumableCount: -> always 99 (HUD/store/"do I have any" always satisfied)
    #   useConsumable:   -> always return YES without decrementing (effect fires, count never drops)
    ("consumableCount:.=99",      0x6b1cf0, "010052e30300001a", "6300a0e31eff2fe1"),  # mov r0,#0x63;bx lr
    ("useConsumable:.no-spend",   0x6b1dc0, "0010a0e10000a0e3", "0100a0e31eff2fe1"),  # mov r0,#1;bx lr
]


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
    print("done")
