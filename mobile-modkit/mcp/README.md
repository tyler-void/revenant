# modkit-mcp

MCP server (stdio, official `mcp` SDK v2) that gives an agent eyes, hands and ears on an Android game over
adb: app lifecycle, a long-lived logcat ring buffer with cursors and crash flags, letterbox-cropped
screenshots, input, and a JSON-lines bridge into the game. Generic for any package; how to use it is in
[../SKILL.md](../SKILL.md).

```bash
uv sync --project mobile-modkit/mcp                       # deps into mobile-modkit/mcp/.venv
uv run --project mobile-modkit/mcp --group dev pytest     # device-free unit tests
ADB_SERIAL=192.168.240.112:5555 PACKAGE=com.example.game \
  uv run --project mobile-modkit/mcp modkit-mcp           # what .mcp.json runs
```

Modules: `logbuf.py` (pure: parser, ring + cursors, crash classifier, reconnect de-dup), `logreader.py`
(the one `adb logcat -v epoch` thread), `adb.py` (hard timeouts, Waydroid helpers), `screen.py`
(raw screencap, crop, JPEG, coordinate transform), `bridge.py` (adb forward + JSON lines),
`server.py` (the tools).
