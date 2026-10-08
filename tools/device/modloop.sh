#!/usr/bin/env bash
# modloop.sh — fast libmod dev loop, no apktool:
#   compile (ImGui objects cached; mod sources always) → replace lib/armeabi-v7a/libmod.so inside the
#   existing libmod APK (zip update) → re-sign with the SAME debug keystore (install -r keeps the
#   save) → adb install -r → force-stop + relaunch.
#
#   tools/device/modloop.sh [--no-install] [--no-launch]
#
# Env: BASE_APK (default dist/BikeRivals-1.5.2-libmod.apk — must already contain libmod.so, i.e. the
# apktool build with System.loadLibrary("mod") injected), RV_SERIAL, ANDROID_NDK, RV_CACHE.
set -euo pipefail
cd "$(dirname "$0")/../.."

BASE_APK=${BASE_APK:-dist/BikeRivals-1.5.2-libmod.apk}
KS=build/keystore/resurrect-debug.keystore
CACHE=${RV_CACHE:-$HOME/.cache/revenant/modloop}
RV_SERIAL=${RV_SERIAL:-192.168.240.112:5555}
PKG=com.miniclip.bikerivals
INSTALL=1; LAUNCH=1
for a in "$@"; do case "$a" in --no-install) INSTALL=0 ;; --no-launch) LAUNCH=0 ;; *) echo "unknown arg $a"; exit 2 ;; esac; done

# ── preconditions ───────────────────────────────────────────────────────────────────────────────
[ -f "$BASE_APK" ] || { echo "ERROR: $BASE_APK missing — build it once via mod/build.sh + build/patch_modlib.py + apktool b"; exit 1; }
unzip -l "$BASE_APK" lib/armeabi-v7a/libmod.so >/dev/null 2>&1 || { echo "ERROR: $BASE_APK has no lib/armeabi-v7a/libmod.so (not a libmod build)"; exit 1; }
[ -f "$KS" ] || { echo "ERROR: $KS missing — the same keystore is required so install -r keeps the save"; exit 1; }
NDK="${ANDROID_NDK:-}"
if [ -z "$NDK" ]; then for d in /opt/android-ndk /opt/android-ndk-r* ~/Android/Sdk/ndk/*; do [ -d "$d" ] && NDK="$d" && break; done; fi
CXX="$(ls "$NDK"/toolchains/llvm/prebuilt/*/bin/armv7a-linux-androideabi21-clang++ 2>/dev/null | sort -V | head -1)"
[ -n "$CXX" ] || { echo "ERROR: armv7a clang++ not found (ANDROID_NDK=$NDK)"; exit 1; }

IMGUI=mod/imgui
FLAGS=(-fPIC -O2 -std=c++17 -fvisibility=hidden -Wall -Wno-unused-parameter
       -DIMGUI_IMPL_OPENGL_ES2 -DIMGUI_DISABLE_OBSOLETE_FUNCTIONS -I"$IMGUI" -I"$IMGUI/backends")
OBJ="$CACHE/obj"; mkdir -p "$OBJ"
t0=$(date +%s)

# ── 1. compile ─────────────────────────────────────────────────────────────────────────────────
objs=()
for src in $IMGUI/imgui.cpp $IMGUI/imgui_draw.cpp $IMGUI/imgui_tables.cpp $IMGUI/imgui_widgets.cpp \
           $IMGUI/backends/imgui_impl_opengl3.cpp; do
  o="$OBJ/$(basename "${src%.cpp}").o"
  if [ ! -f "$o" ] || [ "$src" -nt "$o" ]; then echo "==> cc $src (cached next time)"; "$CXX" "${FLAGS[@]}" -c "$src" -o "$o"; fi
  objs+=("$o")
done
for src in mod/mod.cpp mod/bridge.cpp; do
  o="$OBJ/$(basename "${src%.cpp}").o"
  echo "==> cc $src"; "$CXX" "${FLAGS[@]}" -c "$src" -o "$o"
  objs+=("$o")
done
SO="$CACHE/libmod.so"
"$CXX" -shared -static-libstdc++ -o "$SO" "${objs[@]}" -lGLESv2 -lEGL -llog -ldl
mkdir -p build/work/lib/armeabi-v7a && cp "$SO" build/work/lib/armeabi-v7a/libmod.so   # keep the apktool tree in sync
echo "==> libmod.so $(stat -c%s "$SO") bytes"

# ── 2. swap libmod.so inside the APK (no apktool) ──────────────────────────────────────────────
STAGE="$CACHE/stage"; rm -rf "$STAGE"; mkdir -p "$STAGE/lib/armeabi-v7a"
cp "$SO" "$STAGE/lib/armeabi-v7a/libmod.so"
cp "$BASE_APK" "$STAGE/app.apk"
zip -q -d "$STAGE/app.apk" 'META-INF/*' >/dev/null 2>&1 || true                 # old signature is invalid now
(cd "$STAGE" && zip -q app.apk lib/armeabi-v7a/libmod.so)

# ── 3. re-sign with the same debug key ─────────────────────────────────────────────────────────
rm -rf "$STAGE/signed"
uber-apk-signer --apks "$STAGE/app.apk" --ks "$KS" --ksAlias resurrect --ksPass android --ksKeyPass android \
  --allowResign -o "$STAGE/signed" >"$STAGE/sign.log" 2>&1 || { cat "$STAGE/sign.log"; exit 1; }
OUT="$CACHE/libmod-dev.apk"
cp "$STAGE"/signed/*-aligned-signed.apk "$OUT"
echo "==> signed $OUT ($(( $(date +%s) - t0 ))s so far)"

# ── 4. install + restart ───────────────────────────────────────────────────────────────────────
if [ $INSTALL = 1 ]; then
  timeout 120 adb -s "$RV_SERIAL" install -r "$OUT" | tail -1
  timeout 15 adb -s "$RV_SERIAL" shell am force-stop $PKG     # install -r can leave the OLD libmod running
  if [ $LAUNCH = 1 ]; then
    if command -v waydroid >/dev/null && [[ "$RV_SERIAL" == 192.168.240.* ]]; then waydroid app launch $PKG
    else timeout 15 adb -s "$RV_SERIAL" shell monkey -p $PKG -c android.intent.category.LAUNCHER 1 >/dev/null; fi
  fi
fi
echo "==> done in $(( $(date +%s) - t0 ))s"
