#!/usr/bin/env python3
"""
Revenant Level Editor — local web UI.

Run:   python3 tools/level-editor/server.py
Then:  open http://127.0.0.1:8778

Serves index.html + a JSON API over the locally-cached, decrypted levels in
tools/level-editor/levels/ (gitignored). Populate that cache first with:

    python3 tools/level-editor/leveldec.py import 1_1 build/work/assets/unpack/1_1.dat <KEYHEX>

Phase 3 status: read-only VIEWER (renders the scene). Editing + re-encrypt land
next. Localhost-only, no external deps.
"""
import json, os, sys, glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leveldec as ld

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("LEVEL_EDITOR_PORT", "8778"))


def cached_lids():
    out = []
    for p in sorted(glob.glob(os.path.join(ld.CACHE, "*.level.json"))):
        out.append(os.path.basename(p)[: -len(".level.json")])
    return out


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html")
        if self.path == "/api/levels":
            return self._send(200, {"levels": cached_lids()})
        if self.path.startswith("/api/level/"):
            lid = self.path[len("/api/level/"):]
            p = ld.cache_path(lid)
            if not os.path.exists(p):
                return self._send(404, {"error": "no cached level %r" % lid})
            with open(p, "rb") as f:
                return self._send(200, f.read())
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    lids = cached_lids()
    if not lids:
        print("⚠  no cached levels in %s" % ld.CACHE)
        print("   import one first, e.g.:")
        print("   python3 tools/level-editor/leveldec.py import 1_1 "
              "build/work/assets/unpack/1_1.dat <KEYHEX>")
    else:
        print("cached levels: %s" % ", ".join(lids))
    print("Revenant level editor → http://127.0.0.1:%d" % PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
