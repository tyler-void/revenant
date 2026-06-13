#!/usr/bin/env bash
# Revenant — reproducible build of the patched Bike Rivals APK.
# Requires: apktool, python3, uber-apk-signer (or apksigner+zipalign), keytool.
set -euo pipefail
cd "$(dirname "$0")/.."

ORIG="base/Bike+Rivals_1.5.2_APKPure.apk"
WORK="build/work"
KS="build/keystore/resurrect-debug.keystore"
OUT="dist/BikeRivals-1.5.2-diagnostic-unlock.apk"
DIAG="${1:-}"   # pass --no-diag for the final (logging-free) build

# ── BYO-original gate ──────────────────────────────────────────────────────
# This repo ships NO game bytes. You supply your own legally-owned copy, and we
# verify it's the correct CLEAN original (this also rejects the malware "mods").
EXPECT_SHA="a4272b06fc39e1f0335eb3dfb1b1cc846880cdde0cf49b2c7052d1a9fdcdc9e9"
[ -f "$ORIG" ] || { echo "ERROR: place your own legal copy at $ORIG — this repo ships no game bytes."; exit 1; }
GOT_SHA="$(sha256sum "$ORIG" | cut -d' ' -f1)"
if [ "$GOT_SHA" != "$EXPECT_SHA" ]; then
  echo "ERROR: $ORIG sha256 mismatch — wrong/modified APK (or a malware 'mod')."
  echo "  expected: $EXPECT_SHA"
  echo "  got:      $GOT_SHA"
  echo "  Use the clean Bike Rivals 1.5.2 original. See CHECKSUMS."
  exit 1
fi
echo "==> 0/4 verified clean original ($EXPECT_SHA)"

echo "==> 1/4 decode (clean original)"
rm -rf "$WORK"
apktool d -f -o "$WORK" "$ORIG"

echo "==> 2/4 patch (unlock${DIAG:- + tilt-diagnostic})"
python3 build/apply_patches.py "$WORK" $DIAG

echo "==> 3/4 build"
apktool b "$WORK" -o build/_unsigned.apk

echo "==> 4/4 zipalign + sign (v1+v2+v3)"
[ -f "$KS" ] || keytool -genkeypair -v -keystore "$KS" -alias resurrect \
  -keyalg RSA -keysize 2048 -validity 10000 -storepass android -keypass android \
  -dname "CN=Resurrect Debug, OU=Modding, O=Personal, L=NA, S=NA, C=US"
rm -rf build/_signed && mkdir -p build/_signed dist
uber-apk-signer --apks build/_unsigned.apk --ks "$KS" --ksAlias resurrect \
  --ksPass android --ksKeyPass android --allowResign -o build/_signed
cp build/_signed/*-aligned-signed.apk "$OUT"
rm -f build/_unsigned.apk
echo "==> done: $OUT"
