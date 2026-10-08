"""adb / waydroid subprocess helpers with HARD timeouts: on timeout the whole process group is killed,
so a frozen device (e.g. a FROZEN Waydroid container) can never hang a tool call."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass


@dataclass
class Result:
    stdout: bytes
    stderr: bytes
    code: int | None
    timed_out: bool
    elapsed: float

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.code == 0

    @property
    def out(self) -> str:
        return self.stdout.decode(errors="replace")

    @property
    def err(self) -> str:
        return self.stderr.decode(errors="replace")


def run(argv: list[str], timeout: float = 20.0, stdin: bytes | None = None, env: dict | None = None) -> Result:
    t0 = time.monotonic()
    try:
        p = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, env=env)
    except FileNotFoundError as e:
        return Result(b"", str(e).encode(), 127, False, 0.0)
    try:
        out, err = p.communicate(stdin, timeout=timeout)
        return Result(out, err, p.returncode, False, time.monotonic() - t0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate()
        return Result(out, err, None, True, time.monotonic() - t0)


class Adb:
    def __init__(self, serial: str | None) -> None:
        self.serial = serial or None
        self.bin = shutil.which("adb") or "adb"

    def argv(self, *args: str) -> list[str]:
        return [self.bin] + (["-s", self.serial] if self.serial else []) + list(args)

    def run(self, *args: str, timeout: float = 20.0, stdin: bytes | None = None) -> Result:
        return run(self.argv(*args), timeout=timeout, stdin=stdin)

    def shell(self, cmd: str, timeout: float = 20.0) -> Result:
        return self.run("shell", cmd, timeout=timeout)

    def connect(self, timeout: float = 10.0) -> Result | None:
        """`adb connect` for TCP serials (host:port); no-op for USB serials."""
        if self.serial and ":" in self.serial:
            return run([self.bin, "connect", self.serial], timeout=timeout)
        return None

    def state(self) -> str:
        r = self.run("get-state", timeout=8)
        return r.out.strip() if r.ok else (r.err.strip() or ("timeout" if r.timed_out else "unknown"))

    def pidof(self, name: str) -> list[int]:
        r = self.shell(f"pidof {name}", timeout=8)
        return [int(x) for x in r.out.split() if x.isdigit()] if r.ok else []


def host_env() -> dict:
    """Environment for HOST tools that are themselves Python scripts (waydroid: `#!/usr/bin/env python3`).
    Inside a venv (`uv run`) the venv's python3 is first on PATH and lacks the system `dbus` module, so
    waydroid dies with ModuleNotFoundError. Strip the venv from PATH and drop VIRTUAL_ENV."""
    env = dict(os.environ)
    venv = env.pop("VIRTUAL_ENV", None) or sys.prefix
    parts = [p for p in env.get("PATH", "").split(os.pathsep) if p and not p.startswith(venv)]
    env["PATH"] = os.pathsep.join(parts) or "/usr/local/bin:/usr/bin:/bin"
    return env


def waydroid_bin() -> str | None:
    return shutil.which("waydroid", path=host_env()["PATH"])


def waydroid(*args: str, timeout: float = 20.0) -> Result:
    wd = waydroid_bin()
    if not wd:
        return Result(b"", b"waydroid not installed", 127, False, 0.0)
    return run([wd, *args], timeout=timeout, env=host_env())


def waydroid_status() -> dict:
    """Parse `waydroid status` -> {'Session': 'RUNNING', 'Container': 'FROZEN', ...} ({} if absent)."""
    if not waydroid_bin():
        return {}
    r = waydroid("status", timeout=10)
    out = {}
    for line in r.out.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out
