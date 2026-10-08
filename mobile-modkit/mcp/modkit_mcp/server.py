"""modkit-mcp — MCP server (stdio) that gives an agent eyes, hands and ears on an Android game over adb.

Generic for any package. Config (env):
  ADB_SERIAL     adb serial (e.g. 192.168.240.112:5555 for Waydroid; empty = the only device)
  PACKAGE        Android package name of the game
  BRIDGE_PORT    host TCP port for the in-game bridge forward          (default 7777)
  BRIDGE_SOCKET  abstract socket name the in-game bridge listens on    (default revenant)
  LOG_CAPACITY   lines kept in the logcat ring                         (default 50000)

Every tool response carries `log_cursor` (pass it to logs_query / logs_wait_for to see only what
happened after this call), `crashes` (crash lines not yet reported) and `device_events`
(e.g. android_restart) so nothing goes unnoticed.
"""
from __future__ import annotations

import functools
import os
import re
import threading
import time

import anyio
from mcp.server.mcpserver import Image, MCPServer

from . import screen
from .adb import Adb, waydroid, waydroid_bin, waydroid_status
from .bridge import Bridge, BridgeError
from .logbuf import LogRing
from .logreader import LogReader

SERIAL = os.environ.get("ADB_SERIAL") or None
PACKAGE = os.environ.get("PACKAGE", "")
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "7777"))
BRIDGE_SOCKET = os.environ.get("BRIDGE_SOCKET", "revenant")
LOG_CAPACITY = int(os.environ.get("LOG_CAPACITY", "50000"))

adb = Adb(SERIAL)
ring = LogRing(LOG_CAPACITY)
bridge = Bridge(adb, BRIDGE_PORT, BRIDGE_SOCKET)
_lock = threading.Lock()
_state = {"crash_cursor": 0, "system_server_pid": None, "device_events": [], "shot": None}
_reader: LogReader | None = None

mcp = MCPServer(
    "modkit",
    instructions=(
        "Android game modding device loop. Start with device_ensure. Prefer bridge_call (in-game state/events) "
        "and logs_* over screenshots; screen_shot is for visual proof. Capture `log_cursor` from a response "
        "BEFORE triggering something, then logs_wait_for(regex, since_cursor=that). Inputs accept "
        "space='device' (pixels), 'shot' (pixels of the last screen_shot) or 'frac' (0..1 of the game area)."
    ),
)


# ── plumbing ─────────────────────────────────────────────────────────────────────────────────
def _is_waydroid() -> bool:
    return bool(waydroid_bin()) and bool(SERIAL) and SERIAL.startswith("192.168.240.")


def _system_server_pid() -> int | None:
    pids = adb.pidof("system_server")
    return pids[0] if pids else None


def _check_restart() -> None:
    """Android restart detector. In Waydroid /proc/uptime and boot_id belong to the HOST, so the
    system_server PID is the signal: a new PID means Android (zygote/system_server) restarted."""
    pid = _system_server_pid()
    with _lock:
        old = _state["system_server_pid"]
        if pid and old and pid != old:
            _state["device_events"].append({"type": "android_restart", "old_system_server_pid": old,
                                            "new_system_server_pid": pid, "at": time.time()})
        if pid:
            _state["system_server_pid"] = pid


def _ensure_reader() -> None:
    global _reader
    if _reader is None:
        _reader = LogReader(adb, ring, on_reconnect=_check_restart)
    if not _reader.alive:
        _reader.start()


def _envelope(result: dict) -> dict:
    with _lock:
        crashes = ring.crashes_since(_state["crash_cursor"])
        _state["crash_cursor"] = ring.cursor
        events, _state["device_events"] = _state["device_events"], []
    result["log_cursor"] = ring.cursor
    result["crashes"] = [c.as_str() for c in crashes[-20:]]
    if events:
        result["device_events"] = events
    return result


async def _bg(fn, *a, **kw):
    return await anyio.to_thread.run_sync(functools.partial(fn, *a, **kw))


def _need_package() -> str:
    if not PACKAGE:
        raise ValueError("PACKAGE env var is not set for this server")
    return PACKAGE


def _shot_transform() -> screen.ShotTransform:
    tr = _state["shot"]
    if tr is None:                                     # no screenshot yet: measure the game area once
        img = screen.capture(adb)
        tr = screen.ShotTransform(screen.content_box(img), 1.0)
        _state["shot"] = tr
    return tr


def _xy(x: float, y: float, space: str) -> tuple[int, int]:
    return _shot_transform().to_device(x, y, space) if space != "device" else (int(round(x)), int(round(y)))


# ── device / app ─────────────────────────────────────────────────────────────────────────────
def _device_ensure() -> dict:
    out: dict = {"serial": SERIAL, "package": PACKAGE}
    if _is_waydroid():
        st = waydroid_status()
        out["waydroid"] = {k: st.get(k) for k in ("Session", "Container")}
        if st.get("Session") != "RUNNING":
            out.update(ok=False, error="Waydroid session not running — ask the user to run `waydroid session start` (needs their desktop session)")
            return out
        if st.get("Container") == "FROZEN":
            waydroid(*(["app", "launch", PACKAGE] if PACKAGE else ["show-full-ui"]), timeout=20)
            for _ in range(20):
                if waydroid_status().get("Container") == "RUNNING":
                    break
                time.sleep(0.5)
            out["unfroze"] = True
            out["waydroid"]["Container"] = waydroid_status().get("Container")
    c = adb.connect()
    if c is not None:
        out["connect"] = (c.out.strip() or c.err.strip())[:120]
    st = adb.state()
    for _ in range(3):
        if st == "device":
            break
        time.sleep(1)
        adb.connect()
        st = adb.state()
    out["adb_state"] = st
    if st != "device":
        out.update(ok=False, error=f"adb state is {st!r}")
        return out
    props = adb.shell("getprop ro.build.version.release; getprop ro.product.cpu.abilist; wm size", timeout=10).out.split("\n")
    out["android"] = props[0].strip() if props else ""
    out["abilist"] = props[1].strip() if len(props) > 1 else ""
    out["wm_size"] = props[2].replace("Physical size:", "").strip() if len(props) > 2 else ""
    _check_restart()
    out["system_server_pid"] = _state["system_server_pid"]
    if PACKAGE:
        path = adb.shell(f"pm path {PACKAGE}", timeout=10).out.strip()
        out["installed"] = path.startswith("package:")
        out["pid"] = (adb.pidof(PACKAGE) or [None])[0]
    _ensure_reader()
    out["log_reader"] = {"alive": _reader.alive, "connected": _reader.connected, "restarts": _reader.restarts}
    out["ok"] = True
    return out


@mcp.tool(structured_output=False)
async def device_ensure() -> dict:
    """Connect adb, check Waydroid (auto-unfreeze a FROZEN container), start the logcat reader, and report
    device facts. Reports an `android_restart` device event if system_server's PID changed. Call first."""
    return _envelope(await _bg(_device_ensure))


@mcp.tool(structured_output=False)
async def shell(cmd: str, timeout_s: float = 20) -> dict:
    """Run `adb shell <cmd>` with a HARD timeout (the process group is killed; never hangs)."""
    r = await _bg(adb.shell, cmd, min(max(timeout_s, 1), 300))
    return _envelope({"stdout": r.out[-20000:], "stderr": r.err[-4000:], "exit_code": r.code,
                      "timed_out": r.timed_out, "elapsed_s": round(r.elapsed, 3)})


def _app_install(apk_path: str, timeout_s: float) -> dict:
    pkg = _need_package()
    if not os.path.isfile(apk_path):
        return {"ok": False, "error": f"no such file: {apk_path}"}
    before = adb.shell(f"dumpsys package {pkg} | grep -m1 lastUpdateTime", timeout=10).out.strip()
    r = adb.run("install", "-r", apk_path, timeout=timeout_s)
    after = adb.shell(f"dumpsys package {pkg} | grep -m1 lastUpdateTime", timeout=10).out.strip()
    ok = r.ok and "Success" in (r.out + r.err) and after != "" and after != before
    return {"ok": ok, "output": (r.out + r.err).strip()[-600:], "timed_out": r.timed_out,
            "last_update_before": before, "last_update_after": after}


@mcp.tool(structured_output=False)
async def app_install(apk_path: str, timeout_s: float = 180) -> dict:
    """`adb install -r <apk>` (keeps app data when the signer matches); verified by the package's
    lastUpdateTime changing."""
    return _envelope(await _bg(_app_install, apk_path, timeout_s))


def _app_launch(wait_s: float) -> dict:
    pkg = _need_package()
    if _is_waydroid():                       # also unfreezes the container and shows the window
        r = waydroid("app", "launch", pkg, timeout=20)
    else:
        r = adb.shell(f"monkey -p {pkg} -c android.intent.category.LAUNCHER 1", timeout=20)
    deadline = time.monotonic() + wait_s
    pid = None
    while time.monotonic() < deadline:
        pids = adb.pidof(pkg)
        if pids:
            pid = pids[0]
            break
        time.sleep(0.3)
    return {"ok": pid is not None, "pid": pid, "launcher_output": (r.out + r.err).strip()[-300:]}


@mcp.tool(structured_output=False)
async def app_launch(wait_s: float = 20) -> dict:
    """Launch the package (Waydroid: `waydroid app launch`, else monkey LAUNCHER); verified by its PID."""
    return _envelope(await _bg(_app_launch, wait_s))


def _app_stop() -> dict:
    pkg = _need_package()
    adb.shell(f"am force-stop {pkg}", timeout=15)
    for _ in range(25):
        if not adb.pidof(pkg):
            return {"ok": True}
        time.sleep(0.2)
    return {"ok": False, "error": "process still alive after force-stop", "pid": adb.pidof(pkg)}


@mcp.tool(structured_output=False)
async def app_stop() -> dict:
    """`am force-stop` the package; verified by its PID disappearing."""
    return _envelope(await _bg(_app_stop))


def _remote_size(path: str) -> int | None:
    r = adb.shell(f"stat -c %s '{path}'", timeout=10)
    s = r.out.strip()
    return int(s) if r.ok and s.isdigit() else None


def _push(local_path: str, remote_path: str) -> dict:
    if not os.path.isfile(local_path):
        return {"ok": False, "error": f"no such file: {local_path}"}
    r = adb.run("push", local_path, remote_path, timeout=120)
    size = os.path.getsize(local_path)
    rsize = _remote_size(remote_path)
    return {"ok": r.ok and rsize == size, "bytes": size, "remote_bytes": rsize, "output": (r.out + r.err).strip()[-300:]}


@mcp.tool(structured_output=False)
async def push(local_path: str, remote_path: str) -> dict:
    """adb push, verified by comparing the remote file size."""
    return _envelope(await _bg(_push, local_path, remote_path))


def _pull(remote_path: str, local_path: str) -> dict:
    rsize = _remote_size(remote_path)
    r = adb.run("pull", remote_path, local_path, timeout=120)
    size = os.path.getsize(local_path) if os.path.isfile(local_path) else None
    return {"ok": r.ok and size is not None and size == rsize, "bytes": size, "remote_bytes": rsize,
            "output": (r.out + r.err).strip()[-300:]}


@mcp.tool(structured_output=False)
async def pull(remote_path: str, local_path: str) -> dict:
    """adb pull, verified by comparing the local file size with the remote one."""
    return _envelope(await _bg(_pull, remote_path, local_path))


# ── logs ─────────────────────────────────────────────────────────────────────────────────────
@mcp.tool(structured_output=False)
async def logs_query(regex: str | None = None, since_cursor: int = 0, limit: int = 200, crash_only: bool = False) -> dict:
    """Lines from the always-on logcat ring (newest-cursor model). regex matches 'L TAG: message'.
    Returns next_cursor (pass it back to page/continue), dropped=true if lines aged out of the ring."""
    _ensure_reader()
    q = ring.query(re.compile(regex) if regex else None, since_cursor, max(1, min(limit, 2000)), crash_only)
    return _envelope({"lines": [ln.as_str() for ln in q["lines"]], "next_cursor": q["next_cursor"],
                      "truncated": q["truncated"], "dropped": q["dropped"]})


@mcp.tool(structured_output=False)
async def logs_wait_for(regex: str, timeout_s: float = 30, since_cursor: int | None = None, tail_on_timeout: int = 20) -> dict:
    """Wait (≤60 s) for a log line matching regex after since_cursor (default: now). On timeout returns
    the last tail_on_timeout lines so you can see what DID happen."""
    _ensure_reader()
    since = ring.cursor if since_cursor is None else since_cursor
    t0 = time.monotonic()
    line, cur = await _bg(ring.wait_for, re.compile(regex), since, min(max(timeout_s, 0.1), 60))
    res = {"matched": line is not None, "waited_s": round(time.monotonic() - t0, 3), "next_cursor": line.seq if line else cur}
    if line:
        res["line"] = line.as_str()
    else:
        res["tail"] = [ln.as_str() for ln in ring.tail(max(0, min(tail_on_timeout, 200)))]
    return _envelope(res)


# ── screen ───────────────────────────────────────────────────────────────────────────────────
def _shot(max_width: int, crop_letterbox: bool, fmt: str, quality: int):
    img = screen.capture(adb)
    data, fmt, size, tr = screen.render(img, max_width, crop_letterbox, fmt, quality)
    _state["shot"] = tr
    meta = {"device_size": list(img.size), "crop_box": list(tr.box), "shot_size": list(size),
            "scale": round(tr.scale, 5), "bytes": len(data),
            "hint": "input_* with space='shot' takes pixels of THIS image; space='frac' takes 0..1 of crop_box"}
    return data, fmt, meta


@mcp.tool(structured_output=False)
async def screen_shot(max_width: int = 960, crop_letterbox: bool = True, format: str = "jpeg", quality: int = 80) -> list:
    """Screenshot (raw screencap → letterbox crop → downscale → JPEG by default). Remembers the transform so
    later input_* calls can use space='shot' or 'frac'."""
    data, fmt, meta = await _bg(_shot, max_width, crop_letterbox, format, quality)
    return [Image(data=data, format=fmt), _envelope(meta)]


def _wait_stable(timeout_s: float, interval_s: float, threshold: float, stable_frames: int):
    t0 = time.monotonic()
    prev = screen.capture(adb)
    streak, diffs = 0, []
    while time.monotonic() - t0 < timeout_s:
        time.sleep(interval_s)
        cur = screen.capture(adb)
        d = screen.diff_ratio(prev, cur)
        diffs.append(round(d, 4))
        streak = streak + 1 if d <= threshold else 0
        prev = cur
        if streak >= stable_frames:
            return True, time.monotonic() - t0, diffs[-6:]
    return False, time.monotonic() - t0, diffs[-6:]


@mcp.tool(structured_output=False)
async def screen_wait_stable(timeout_s: float = 10, interval_s: float = 0.3, threshold: float = 0.01, stable_frames: int = 2) -> dict:
    """Wait until consecutive frames differ by ≤ threshold (mean abs diff 0..1) for stable_frames in a row.
    No image is returned — follow with screen_shot if you need to look."""
    ok, waited, diffs = await _bg(_wait_stable, min(timeout_s, 60), max(interval_s, 0.05), threshold, max(1, stable_frames))
    return _envelope({"stable": ok, "waited_s": round(waited, 3), "recent_diffs": diffs})


# ── input ────────────────────────────────────────────────────────────────────────────────────
@mcp.tool(structured_output=False)
async def input_tap(x: float, y: float, space: str = "device") -> dict:
    """Tap. space: device (pixels) | shot (pixels of the last screen_shot) | frac (0..1 of the game area)."""
    dx, dy = await _bg(_xy, x, y, space)
    r = await _bg(adb.shell, f"input tap {dx} {dy}", 10)
    return _envelope({"ok": r.ok, "device_xy": [dx, dy]})


@mcp.tool(structured_output=False)
async def input_swipe(x1: float, y1: float, x2: float, y2: float, duration_ms: int = 300, space: str = "device") -> dict:
    """Swipe from (x1,y1) to (x2,y2) over duration_ms."""
    a = await _bg(_xy, x1, y1, space)
    b = await _bg(_xy, x2, y2, space)
    r = await _bg(adb.shell, f"input swipe {a[0]} {a[1]} {b[0]} {b[1]} {int(duration_ms)}", duration_ms / 1000 + 10)
    return _envelope({"ok": r.ok, "from": list(a), "to": list(b)})


def _hold(dx: int, dy: int, ms: int):
    return adb.shell(f"input swipe {dx} {dy} {dx} {dy} {ms}", ms / 1000 + 10)


@mcp.tool(structured_output=False)
async def input_long_press(x: float, y: float, duration_ms: int = 1000, space: str = "device", wait: bool = True) -> dict:
    """Press and hold at (x,y) for duration_ms (e.g. hold throttle). wait=false returns immediately and
    keeps holding in the background, so you can call bridge_call/logs while the press is held."""
    dx, dy = await _bg(_xy, x, y, space)
    ms = int(max(1, min(duration_ms, 60000)))
    if wait:
        r = await _bg(_hold, dx, dy, ms)
        return _envelope({"ok": r.ok, "device_xy": [dx, dy], "held_ms": ms})
    threading.Thread(target=_hold, args=(dx, dy, ms), daemon=True).start()
    return _envelope({"ok": True, "device_xy": [dx, dy], "holding_ms": ms, "background": True})


@mcp.tool(structured_output=False)
async def input_key(key: str, hold_ms: int = 0) -> dict:
    """Key press (KEYCODE_* name or number); hold_ms>0 holds it (input keycombination -t)."""
    cmd = f"input keycombination -t {int(hold_ms)} {key}" if hold_ms > 0 else f"input keyevent {key}"
    r = await _bg(adb.shell, cmd, hold_ms / 1000 + 10)
    return _envelope({"ok": r.ok, "stderr": r.err.strip()[-200:]})


# ── in-game bridge ───────────────────────────────────────────────────────────────────────────
@mcp.tool(structured_output=False)
async def bridge_call(cmd: str, args: dict | None = None, timeout_s: float = 5) -> dict:
    """Call the in-game bridge (JSON lines over adb forward tcp:BRIDGE_PORT localabstract:BRIDGE_SOCKET).
    Returns the bridge reply {ok, result|error}. For Revenant: ping, state, events_since{seq},
    overlay{mode}, goto_level{w,l}, find{class}, children{ptr}, ivar{ptr,name|names}, call{target,sel,args,ret}, log{msg}."""
    try:
        rep = await _bg(bridge.call, cmd, args or {}, min(max(timeout_s, 0.5), 30))
    except BridgeError as e:
        rep = {"ok": False, "error": str(e)}
    return _envelope({"bridge": rep})


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
