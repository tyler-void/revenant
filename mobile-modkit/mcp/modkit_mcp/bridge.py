"""Client for an in-game agent bridge: JSON lines over `adb forward tcp:<port> localabstract:<name>`.
Request {"id", "cmd", "args"} -> reply {"id", "ok", "result"|"error"} (one line each)."""
from __future__ import annotations

import json
import socket
import time

from .adb import Adb


class BridgeError(RuntimeError):
    pass


class Bridge:
    def __init__(self, adb: Adb, port: int, name: str) -> None:
        self.adb = adb
        self.port = port
        self.name = name
        self._id = 0

    def ensure_forward(self, force: bool = False) -> None:
        spec = f"tcp:{self.port} localabstract:{self.name}"
        if not force:
            r = self.adb.run("forward", "--list", timeout=8)
            if r.ok and any(spec in ln and (not self.adb.serial or ln.startswith(self.adb.serial))
                            for ln in r.out.splitlines()):
                return
        r = self.adb.run("forward", f"tcp:{self.port}", f"localabstract:{self.name}", timeout=8)
        if not r.ok:
            raise BridgeError(f"adb forward failed: {r.err.strip() or 'timeout'}")

    def _once(self, line: bytes, timeout: float) -> dict:
        with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(line)
            buf = b""
            deadline = time.monotonic() + timeout
            while b"\n" not in buf:
                if time.monotonic() > deadline:
                    raise TimeoutError("bridge reply timeout")
                chunk = s.recv(65536)
                if not chunk:
                    raise ConnectionError("bridge closed the connection (app not running, or bridge=1 not set?)")
                buf += chunk
        return json.loads(buf.split(b"\n", 1)[0])

    def call(self, cmd: str, args: dict | None = None, timeout: float = 5.0) -> dict:
        self._id += 1
        line = (json.dumps({"id": self._id, "cmd": cmd, "args": args or {}}) + "\n").encode()
        self.ensure_forward()
        try:
            return self._once(line, timeout)
        except (ConnectionError, OSError) as first:
            # stale forward after an adb/device restart: re-forward once and retry
            try:
                self.ensure_forward(force=True)
                return self._once(line, timeout)
            except Exception as e:
                raise BridgeError(f"{type(e).__name__}: {e} (first try: {first})") from e
