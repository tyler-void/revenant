#!/usr/bin/env bash
# rv.sh — Revenant device loop: eyes + hands for Waydroid (or a phone) over adb.
#
#   tools/device/rv.sh <command> [args]      (see `rv.sh help`)
#
# Env:
#   RV_SERIAL   adb serial (default: the Waydroid container, 192.168.240.112:5555)
#   RV_OUT      where screenshots/logs go (default ~/.cache/revenant/rv — OUTSIDE the repo:
#               screenshots are derived game art and must never be committed)
#
# Coordinates for tap/swipe/hold are PHYSICAL display pixels (`rv.sh frame` prints the
# game window). throttle/brake hold inside the game window, so they work on any display.
set -euo pipefail

PKG=com.miniclip.bikerivals
ACT=$PKG/.GameActivity
MODS=/sdcard/Android/data/$PKG/files/mods
RV_SERIAL=${RV_SERIAL:-192.168.240.112:5555}
RV_OUT=${RV_OUT:-$HOME/.cache/revenant/rv}
LOG_RE='RVMOD|AndroidRuntime|FATAL EXCEPTION|F libc|F DEBUG'

die() { echo "rv: $*" >&2; exit 1; }
adbs() { timeout "${RV_TIMEOUT:-20}" adb -s "$RV_SERIAL" "$@"; }
# Waydroid = ro.product.device waydroid_* (a FROZEN container hangs getprop -> short timeout, then fall back
# to `waydroid status` reporting the serial's IP).
is_waydroid() {
  command -v waydroid >/dev/null || return 1
  local dev; dev=$(RV_TIMEOUT=5 adbs shell getprop ro.product.device 2>/dev/null | tr -d '\r')
  [ -n "$dev" ] && { [[ "$dev" == waydroid* ]]; return; }
  waydroid status 2>/dev/null | grep -q "IP address:[[:space:]]*${RV_SERIAL%%:*}\$"
}

# Waydroid FREEZES the container when no Waydroid window is shown; every `adb shell` then
# hangs. Fail fast with a hint instead of hanging.
check_live() {
  if is_waydroid; then
    local st; st=$(waydroid status 2>/dev/null || true)
    grep -q 'Session:.*RUNNING' <<<"$st" || die "Waydroid session not running — start it (waydroid session start)"
    if grep -q 'Container:.*FROZEN' <<<"$st"; then
      die "Waydroid container FROZEN (no window shown) — run: rv.sh launch  (or waydroid show-full-ui)"
    fi
  fi
  [ "$(adbs get-state 2>/dev/null)" = device ] || die "adb: $RV_SERIAL not 'device' — run: rv.sh connect"
}

# Game window [x0,y0][x1,y1] from dumpsys input (the pillarboxed GameActivity surface).
frame() {
  adbs shell dumpsys input | grep -m1 -oE "GameActivity', id=[0-9]+, displayId=[0-9]+.*frame=\[[0-9]+,[0-9]+\]\[[0-9]+,[0-9]+\]" \
    | grep -oE 'frame=\[[0-9]+,[0-9]+\]\[[0-9]+,[0-9]+\]' | tr -c '0-9\n' ' ' | awk '{print $1, $2, $3, $4}'
}

hold() {  # hold x y ms — a long press (swipe in place); returns when released
  adbs shell input swipe "$1" "$2" "$1" "$2" "$3"
}

cmd=${1:-help}; shift || true
case "$cmd" in
  connect)
    timeout 15 adb connect "$RV_SERIAL"; adbs get-state ;;
  status)
    is_waydroid && waydroid status || true
    echo "adb: $(adbs get-state 2>&1)"
    echo "pid: $(adbs shell pidof $PKG 2>/dev/null || echo '-')" ;;
  install)  # install [apk]  — `-r` keeps app data when the signer matches
    check_live; adbs install -r "${1:?apk path}" ;;
  launch)   # Waydroid: `waydroid app launch` also unfreezes the container + shows the window
    if is_waydroid; then waydroid app launch $PKG; else check_live; adbs shell am start -n $ACT; fi ;;
  stop)
    check_live; adbs shell am force-stop $PKG ;;
  restart)  # force-stop first: `install -r` alone can leave the OLD libmod running
    check_live; adbs shell am force-stop $PKG; "$0" launch ;;
  flags)    # flags reader=1 step=1 ...  → mods/rvdebug.txt (read once at hook install → restart)
    check_live; adbs shell "mkdir -p $MODS && printf '%s\n' $* > $MODS/rvdebug.txt && cat $MODS/rvdebug.txt" ;;
  push-level)  # push-level <file.dat> [lid]  → mods/<lid>.dat (+ ensures reader=1)
    check_live
    f=${1:?level .dat}; lid=${2:-$(basename "$f" .dat)}
    [[ "$lid" =~ ^[0-9]+_[0-9]+$ ]] || die "lid '$lid' must look like <world>_<level> (e.g. 1_24)"
    adbs shell "mkdir -p $MODS"
    adbs push "$f" "$MODS/$lid.dat" >/dev/null
    adbs shell "grep -q '^reader=1' $MODS/rvdebug.txt 2>/dev/null || echo reader=1 >> $MODS/rvdebug.txt"
    adbs shell "ls -l $MODS/$lid.dat; cat $MODS/rvdebug.txt" ;;
  unpush)   # unpush <lid>  → remove one override from mods/ (stock level loads again)
    check_live; adbs shell "rm -f $MODS/${1:?lid}.dat; ls -l $MODS" ;;
  mods)
    check_live; adbs shell "ls -l $MODS; echo '--- rvdebug.txt'; cat $MODS/rvdebug.txt 2>/dev/null" ;;
  shot)     # shot [name] — PNG of the full display; prints the path
    check_live; mkdir -p "$RV_OUT"
    out="$RV_OUT/${1:-shot-$(date +%H%M%S)}.png"
    adbs exec-out screencap -p > "$out"; echo "$out" ;;
  frame)
    check_live; frame ;;
  tap)      check_live; adbs shell input tap "${1:?x}" "${2:?y}" ;;
  swipe)    check_live; adbs shell input swipe "${1:?x1}" "${2:?y1}" "${3:?x2}" "${4:?y2}" "${5:-400}" ;;
  hold)     check_live; hold "${1:?x}" "${2:?y}" "${3:-1000}" ;;
  throttle|brake)  # throttle|brake [ms] — hold the right|left half of the game window
    check_live; read -r x0 y0 x1 y1 < <(frame)
    w=$((x1 - x0)); y=$(( (y0 + y1) * 3 / 5 ))
    if [ "$cmd" = throttle ]; then x=$((x0 + w * 4 / 5)); else x=$((x0 + w / 5)); fi
    RV_TIMEOUT=$(( ${1:-1000} / 1000 + 10 )) hold "$x" "$y" "${1:-1000}" ;;
  key)      # key KEYCODE [ms] — tap a key, or hold it for ms (input keycombination -t)
    check_live
    if [ -n "${2:-}" ]; then adbs shell input keycombination -t "$2" "$1"; else adbs shell input keyevent "$1"; fi ;;
  logcat)   # stream RVMOD + crash lines (never `adb logcat -s TAG:*` in zsh: glob expansion)
    check_live; RV_TIMEOUT=86400 adbs logcat -v time | grep --line-buffered -E "$LOG_RE" ;;
  log)      # log [n] — last n RVMOD/crash lines from the buffer
    check_live; adbs logcat -d -v time | grep -E "$LOG_RE" | tail -n "${1:-40}" ;;
  help|*)
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    echo "commands: connect status install launch stop restart flags push-level unpush mods"
    echo "          shot frame tap swipe hold throttle brake key logcat log" ;;
esac
