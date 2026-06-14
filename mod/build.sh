#!/usr/bin/env bash
# Build libmod.so (mod-loader + ImGui overlay) for armeabi-v7a with the Android NDK.
# Auto-detects the NDK; override with ANDROID_NDK=. Output: build/work/lib/armeabi-v7a/libmod.so
set -euo pipefail
cd "$(dirname "$0")/.."

NDK="${ANDROID_NDK:-}"
if [ -z "$NDK" ]; then
  for d in /opt/android-ndk /opt/android-ndk-r* /opt/ndk ~/Android/Sdk/ndk/* /usr/lib/android-ndk; do
    [ -d "$d" ] && NDK="$d" && break
  done
fi
[ -n "$NDK" ] && [ -d "$NDK" ] || { echo "ERROR: Android NDK not found (sudo pacman -S android-ndk) or set ANDROID_NDK="; exit 1; }
CXX="$(ls "$NDK"/toolchains/llvm/prebuilt/*/bin/armv7a-linux-androideabi21-clang++ 2>/dev/null | sort -V | head -1)"
[ -n "$CXX" ] || { echo "ERROR: armv7a clang++ not found under $NDK"; exit 1; }

IMGUI=mod/imgui
SRC="mod/mod.cpp $IMGUI/imgui.cpp $IMGUI/imgui_draw.cpp $IMGUI/imgui_tables.cpp $IMGUI/imgui_widgets.cpp $IMGUI/backends/imgui_impl_opengl3.cpp"
OUT="build/work/lib/armeabi-v7a/libmod.so"
mkdir -p "$(dirname "$OUT")"
echo "==> CXX: $CXX"
"$CXX" -shared -fPIC -O2 -std=c++17 -fvisibility=hidden -Wall -Wno-unused-parameter \
  -static-libstdc++ \
  -DIMGUI_IMPL_OPENGL_ES2 -DIMGUI_DISABLE_OBSOLETE_FUNCTIONS \
  -I"$IMGUI" -I"$IMGUI/backends" \
  -o "$OUT" $SRC \
  -lGLESv2 -lEGL -llog -ldl
NDKRE="$NDK/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf"
echo "    NEEDED:" $("$NDKRE" -d "$OUT" 2>/dev/null | grep -i NEEDED | grep -oE '\[[^]]+\]' | tr '\n' ' ')
echo "==> built $OUT ($(stat -c%s "$OUT") bytes)"
