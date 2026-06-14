#!/usr/bin/env bash
# Live-bisect which libmod game-hook deterministically freezes the run timer — WITHOUT rebuilding.
# libmod reads these flags from rvdebug.txt at startup; each game hook passes straight through when
# its flag is 0. So: pick flags, this script writes the file + cold-relaunches the game, you drive
# one level and watch the timer. Flip flags until the timer comes back -> that hook is the culprit.
#
# Usage:   build/bisect_modhooks.sh step=0 draw=0 reader=0 ach=0 specs=0   # all game hooks OFF
#          build/bisect_modhooks.sh step=1                                  # only step ON (rest default ON)
# Keys (default 1=on): step draw reader ach specs   (overlay + touch are always on)
set -euo pipefail
PKG=com.miniclip.bikerivals
FLAGS_DIR="/sdcard/Android/data/$PKG/files/mods"
TMP="$(mktemp)"
for kv in "$@"; do echo "$kv" >> "$TMP"; done
echo "==> flags:"; cat "$TMP"
adb shell "mkdir -p $FLAGS_DIR" >/dev/null 2>&1 || true
adb push "$TMP" "$FLAGS_DIR/rvdebug.txt" >/dev/null
rm -f "$TMP"
adb shell am force-stop "$PKG"
adb logcat -c 2>/dev/null || true
adb shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
echo "==> relaunched. confirming libmod read the flags:"
sleep 6
adb logcat -d 2>/dev/null | grep -iE 'rvdebug flags' | tail -1
echo "==> now drive ONE level and watch the level timer (top-right)."
