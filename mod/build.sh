#!/usr/bin/env bash
# Build libmod.so (the injected native mod lib) for armeabi-v7a using the Android NDK.
# Auto-detects the NDK; override with ANDROID_NDK=/path. Output: build/work/lib/armeabi-v7a/libmod.so
set -euo pipefail
cd "$(dirname "$0")/.."

NDK="${ANDROID_NDK:-}"
if [ -z "$NDK" ]; then
  for d in /opt/android-ndk /opt/android-ndk-r* /opt/ndk ~/Android/Sdk/ndk/* /usr/lib/android-ndk; do
    [ -d "$d" ] && NDK="$d" && break
  done
fi
[ -n "$NDK" ] && [ -d "$NDK" ] || { echo "ERROR: Android NDK not found. Install it (sudo pacman -S android-ndk) or set ANDROID_NDK=."; exit 1; }
echo "==> NDK: $NDK"

CC="$(ls "$NDK"/toolchains/llvm/prebuilt/*/bin/armv7a-linux-androideabi*-clang 2>/dev/null | sort -V | head -1)"
[ -n "$CC" ] || { echo "ERROR: armv7a clang not found under $NDK"; exit 1; }
echo "==> CC: $CC"

OUT="build/work/lib/armeabi-v7a/libmod.so"
mkdir -p "$(dirname "$OUT")"
"$CC" -shared -fPIC -O2 -fvisibility=hidden -Wall \
  -o "$OUT" mod/mod.c -llog
echo "==> built $OUT ($(stat -c%s "$OUT") bytes)"
"$NDK"/toolchains/llvm/prebuilt/*/bin/llvm-readelf -d "$OUT" 2>/dev/null | grep -i soname || true
