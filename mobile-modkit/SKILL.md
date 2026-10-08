---
name: android-device-loop
description: Drive an Android game on Waydroid (or a phone) through the modkit MCP server — launch, navigate, read in-game state/events, send input, watch logs, take proof screenshots — without clicking through screenshots. Use for any device test of a modded Android game in this repo.
---

# Android device loop (modkit MCP + in-game bridge)

The point: an agent should **never navigate by screenshots**. Ask the game (bridge), read the log
(ring buffer with cursors), and only take ONE screenshot at the end as visual proof.

```
 agent ──MCP stdio──► modkit-mcp ──adb──► Waydroid / phone
                         │  logcat reader thread (ring buffer, cursors, crash flags)
                         │  screencap (raw → crop → JPEG), input, app lifecycle
                         └─ adb forward tcp:7777 ──► @revenant (abstract socket in libmod)
                                                       └─ commands run on the GL thread (swapBuffers hook)
```

## Setup (once)

- Server: `mobile-modkit/mcp/` (Python, official `mcp` SDK, `uv`). Registered in the repo's `.mcp.json`
  as `modkit` — env `ADB_SERIAL`, `PACKAGE`, `BRIDGE_PORT` (7777), `BRIDGE_SOCKET` (revenant).
  Tests (no device): `uv run --project mobile-modkit/mcp --group dev pytest`.
- Waydroid: LineageOS 20 (Android 13) + libhoudini (ARM32 translation). Set once (then restart the session):
  `waydroid prop set persist.waydroid.suspend false` (no more FROZEN container),
  `persist.waydroid.width 1560`, `persist.waydroid.height 720` (small, fast screenshots).
- Game: the libmod build (`dist/BikeRivals-1.5.2-libmod.apk`, then `tools/device/modloop.sh` for every
  rebuild) and `mods/rvdebug.txt` containing `bridge=1` (+ `reader=1` for the mod-loader). Both default
  OFF; the distributed build never opens the socket.

## The loop

1. `device_ensure` — connects adb, unfreezes a FROZEN Waydroid container, starts the log reader,
   reports Android/ABI/screen/pid. Reports `device_events: [{type: android_restart}]` when
   system_server's PID changed.
2. Build + install: `tools/device/modloop.sh` (3–8 s: compile → zip-swap libmod.so → re-sign → `install -r`
   → relaunch). Or `app_install` for a whole APK.
3. `app_stop` / `app_launch` (both verified by PID).
4. `bridge_call ping` until ok (the bridge starts once libgame is mapped and a frame renders).
5. `bridge_call overlay {mode:"hidden"}` — ImGui stops covering the screen and eating taps.
6. Remember `log_cursor` from that response, then act: `bridge_call goto_level {w:1, l:24}`.
7. Wait on facts, not pixels: `logs_wait_for {regex: "\\[MOD\\] 1_24\\.dat", since_cursor}` and/or poll
   `bridge_call events_since {seq}` for `goto_done` / `level_loaded` / `race_start` / `finish` / `death`.
8. Input: `input_long_press {x, y, duration_ms, wait:false}` holds in the background while you keep
   calling `bridge_call state`. Coordinates: `space:"device"` (pixels), `"shot"` (pixels of the last
   screen_shot) or `"frac"` (0..1 of the letterboxed game area). Throttle (Bike Rivals) = hold the right
   part of the game window (e.g. frac 0.85, 0.6).
9. One `screen_shot` at the end (JPEG, letterbox cropped, ≤960 px wide) as proof.

Reference run: `tools/device/acceptance_bridge.py` does all of the above through MCP and writes a
transcript + proof shot to `~/.cache/revenant/acceptance/`.

## MCP tools

| tool | what it does (every response also has `log_cursor`, `crashes`, maybe `device_events`) |
|---|---|
| `device_ensure` | adb connect, Waydroid status/unfreeze, log reader up, device facts, restart detection |
| `shell(cmd, timeout_s)` | `adb shell` with a hard timeout (process group killed) |
| `app_install(apk_path)` | `install -r`, verified by lastUpdateTime changing |
| `app_launch` / `app_stop` | verified by PID appearing / disappearing |
| `push` / `pull` | verified by comparing file sizes |
| `logs_query(regex, since_cursor, limit, crash_only)` | lines from the ring; `next_cursor`, `truncated`, `dropped` |
| `logs_wait_for(regex, timeout_s≤60, since_cursor, tail_on_timeout)` | blocks for a match; tail on timeout |
| `screen_shot(max_width, crop_letterbox, format, quality)` | image + crop box/scale (for `space:"shot"`) |
| `screen_wait_stable(timeout_s, threshold)` | waits for consecutive frames to stop changing (no image) |
| `input_tap` / `input_swipe` / `input_long_press` / `input_key` | `space` = device / shot / frac |
| `bridge_call(cmd, args, timeout_s)` | JSON-lines call into the game (below) |

Cursor model: every log line gets a monotonic `seq`; a cursor = "the last seq you have seen". Grab
`log_cursor` BEFORE you trigger something, then wait/query `since_cursor` it, or you can miss a line that
arrives before your wait starts. `regex` matches `"L TAG: message"`.

## Bridge commands (Revenant libmod, `bridge=1`)

| cmd | args | result |
|---|---|---|
| `ping` | | `{pong, frame, mono, build}` |
| `state` | | scene + children, `lid`, `run_time` (HUD timer = `Manager.time_`), `mono` (GL-thread CLOCK_MONOTONIC, same frame), `frame`, `bodies`, `in_level`, `race_started`, `bike_gen`, goto phase |
| `events_since` | `seq` | `{events, next, dropped}` |
| `overlay` | `mode: hidden\|open\|toggle` | |
| `goto_level` | `w, l [, n, max_attempts]` | starts a job; watch `goto_progress` → `goto_done` / `goto_retry` / `goto_failed`. Career-map number n = (w−1)·30 + l (map worlds are 30/30/30/15). Works from menus or from inside a level (backs out via the pause menu's Exit) |
| `find` | `class, depth, limit [, ivars[]]` | scene-graph nodes `{ptr, class, depth}` (+ the named ivars per node) |
| `children` | `ptr` | direct children |
| `ivar` | `ptr, name` or `names[]` | values read BY NAME at runtime-realized offsets |
| `call` | `target ("0x…" or "+Class"), sel, args[≤4], ret (v,i,f,B,@,s)` | arbitrary method call (dev only). Args: numbers, `"0x…"`, `{"f":1.5}`, `{"nsnumber":24}` |
| `log` | `msg` | writes `RVMARK msg` to logcat (align bridge + log timelines) |

Events (ring of 512 + logcat line `RVEVT {"seq":N,"type":"…","mono":T,…}` under tag `RVMOD`):
`scene_ready{class}`, `goto_progress{step}`, `goto_done{lid,bodies,bodies_added,bike,attempt}`,
`goto_retry{reason}`, `goto_failed`, `level_loaded{lid,bodies,bike_gen}`, `race_start{run_time}`,
`finish{run_time}`, `death{kill,dead,exploded}`, `alive`, `menu_did_finish_loading`.

Timer checks: use two `state` calls and compare Δ`run_time` / Δ`mono` (both sampled on the GL thread in
the same frame — no adb noise). The game adds exactly 1/60 s per physics step and steps once per rendered
frame, so the expected ratio is **fps/60** (Waydroid renders ~61 fps → ≈1.016–1.018). Frozen = 0, crawling
≪ 1.

## Gotchas (all hit for real)

- **FROZEN container** → every `adb shell` hangs. Fixed by `persist.waydroid.suspend false`;
  `device_ensure` also unfreezes via `waydroid app launch <pkg>`.
- **`waydroid` is a Python script** (`#!/usr/bin/env python3`). Inside a venv (`uv run`) it picks the venv
  python, which has no `dbus` → `ModuleNotFoundError: dbus`. Run it with the venv stripped from PATH
  (`modkit_mcp.adb.host_env()`).
- **In Waydroid, `/proc/uptime` and `boot_id` are the HOST's.** Detect an Android restart by a new
  `system_server` PID (device_ensure does), or SystemUI cold-start / BOOT_COMPLETED lines in logcat.
- **zsh: never `adb logcat -s TAG:*`** — the `*` is glob-expanded and the command dies. Use the MCP log tools
  or `adb logcat | grep --line-buffered TAG`.
- **logcat tag is `RVMOD`** for libmod; events are `RVEVT {json}` lines on that tag.
- `AndroidRuntime` at INFO is boot noise; the crash flag keys on severity (E/F), `FATAL EXCEPTION`,
  `Fatal signal`.
- **Bash tool calls die at 2 min** unless given a timeout; long holds/waits belong in MCP tools or the
  background.
- **`screencap` works on Waydroid** (it is black on the phone's GL surface). Raw `screencap` (no `-p`) is
  much faster than PNG.
- Synthetic `input keyevent` DPAD keys don't reach this game's key handler (no `MCInput onKeyDown` log);
  lean input is an open question (bridge v1 will inject input directly).
- `install -r` does not always kill the old process → `app_stop` before `app_launch` (modloop.sh does).
- The bridge accepts only uid 0/2000 peers (SO_PEERCRED): `call` executes arbitrary methods, and an abstract
  socket has no filesystem permissions.
