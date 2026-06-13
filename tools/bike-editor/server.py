#!/usr/bin/env python3
"""
Revenant Bike Editor — local web UI.

Run:   python3 tools/bike-editor/server.py
Then:  open http://127.0.0.1:8777

Serves index.html + a tiny JSON API backed by bikeedit.py. Edits the decoded
assets in build/work/assets/unpack; run build/build.sh afterwards to repackage.
Localhost-only, no external deps.
"""
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bikeedit as be

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("BIKE_EDITOR_PORT", "8777"))


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

    def log_message(self, *a):  # quiet
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html")
        if self.path == "/api/bikes":
            out = []
            for b in be.list_bikes():
                try:
                    _, pr = be.load_props(b)
                    out.append({"name": b, "props": {k: pr.get(k) for k in be.KNOBS if k in pr}})
                except Exception as e:
                    out.append({"name": b, "error": str(e)})
            return self._send(200, {"bikes": out, "knobs": be.KNOBS})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.startswith("/api/bike/"):
            name = self.path[len("/api/bike/"):]
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
            try:
                d, pr = be.load_props(name)
                changed = {}
                for k, v in data.items():
                    if k in be.KNOBS:
                        pr[k] = float(v)
                        changed[k] = pr[k]
                be.save_props(name, d)
                return self._send(200, {"ok": True, "changed": changed,
                                        "hint": "run build/build.sh to repackage"})
            except Exception as e:
                return self._send(400, {"ok": False, "error": str(e)})
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    if not os.path.isdir(be.UNPACK):
        sys.exit("decoded assets not at %s (run build/build.sh once, or set BR_UNPACK)" % be.UNPACK)
    print("Revenant bike editor → http://127.0.0.1:%d   (editing %s)" % (PORT, be.UNPACK))
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
