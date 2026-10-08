#!/usr/bin/env python3
"""
Revenant Level Editor — local web UI.

Run:   python3 tools/level-editor/server.py
Then:  open http://127.0.0.1:8778

Serves index.html + levelops.js + a JSON API over the locally-cached, decrypted levels in
tools/level-editor/levels/ (gitignored). Populate that cache first with:

    python3 tools/level-editor/leveldec.py import 1_1 build/work/assets/unpack/1_1.dat <KEYHEX>

A "document" is one cache file `levels/<doc>.level.json`; its `lid` field is the game slot it targets
(a copy like `1_25-copy` still says lid 1_25). Saves are TYPED: the browser's JSON loses int-vs-real,
so every save restores types from the previous typed save + a schema of all cached levels
(leveldec.retype_level). Export runs the unidbg cipher oracle; Push exports and then drives the game
through the modkit library (push_to_game.py) — needs adb + a libmod build with bridge=1.
The level key lives in levels/keys.json (gitignored, never committed): all levels share one key.
Localhost-only, no external deps (push uses the modkit uv env).
"""
import base64, glob, json, os, re, shutil, subprocess, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import leveldec as ld

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PORT = int(os.environ.get("LEVEL_EDITOR_PORT", "8778"))
DOC_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]{0,63}$")
STATIC = {"/": "index.html", "/index.html": "index.html", "/levelops.js": "levelops.js"}


def cached_docs():
    return [os.path.basename(p)[: -len(".level.json")]
            for p in sorted(glob.glob(os.path.join(ld.CACHE, "*.level.json")))]


def doc_path(doc):
    if not DOC_RE.match(doc or "") or ".." in doc:
        raise ValueError("bad document name %r" % doc)
    return ld.cache_path(doc)


def load_doc(doc):
    with open(doc_path(doc)) as f:
        return json.load(f)


def save_typed(doc, sent):
    """Browser JSON -> typed level (restores int/real from the last save + corpus schema)."""
    p = doc_path(doc)
    old = load_doc(doc) if os.path.exists(p) else None
    schema = ld.type_schema(ld.cached_levels())
    level = ld.retype_level(ld.from_wire(sent), old, schema)
    with open(p, "w") as f:
        json.dump(level, f)
    return level


def export_doc(doc, key=None):
    level = load_doc(doc)
    key = (key or "").strip() or ld.level_key(level.get("lid"))
    if not key:
        raise ValueError("no level key on file — enter it once (it is stored in levels/keys.json, gitignored)")
    out_dat = os.path.join(ld.CACHE, "%s.dat" % doc)
    ld.export_dat(doc_path(doc), out_dat, key)
    return level, out_dat, key


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in STATIC:
                with open(os.path.join(HERE, STATIC[path]), "rb") as f:
                    ctype = "text/html" if path.endswith(("/", ".html")) else "text/javascript"
                    return self._send(200, f.read(), ctype)
            if path == "/api/levels":
                return self._send(200, {"levels": cached_docs()})
            if path == "/api/keys":
                return self._send(200, {"have": bool(ld.level_key())})
            if path.startswith("/api/atlasmeta/"):
                return self._send(200, ld.atlas_meta_for_level(load_doc(path[len("/api/atlasmeta/"):])))
            if path.startswith("/api/atlas/") or path.startswith("/api/fill/"):
                opaque = path.startswith("/api/fill/")
                png = ld.transcode_atlas(path.split("/")[-1], key=not opaque)   # sprites keyed, fills opaque
                if not png:
                    return self._send(404, {"error": "no texture"})
                with open(png, "rb") as f:
                    return self._send(200, f.read(), "image/png")
            if path.startswith("/api/level/"):                    # strict JSON for the browser
                level = load_doc(path[len("/api/level/"):])
                return self._send(200, json.dumps(ld.to_wire(level), allow_nan=False))
        except FileNotFoundError:
            return self._send(404, {"error": "not found"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            if path.startswith("/api/level/"):                       # typed save
                level = save_typed(path[len("/api/level/"):], self._body())
                return self._send(200, {"ok": True, "entities": len(level.get("Entities", []))})
            if path.startswith("/api/copy/"):                        # save-as-copy of a document
                src = path[len("/api/copy/"):]
                dst = self._body().get("name", "")
                if os.path.exists(doc_path(dst)):
                    return self._send(409, {"ok": False, "error": "%s already exists" % dst})
                shutil.copyfile(doc_path(src), doc_path(dst))
                return self._send(200, {"ok": True, "doc": dst})
            if path.startswith("/api/export/"):                      # encrypted .dat (oracle, ~1 min)
                doc = path[len("/api/export/"):]
                data = self._body()
                level, out_dat, key = export_doc(doc, data.get("key"))
                if data.get("key") and data.get("remember"):
                    keys = ld.load_keys(); keys[level.get("lid", doc)] = key
                    os.makedirs(ld.CACHE, exist_ok=True)
                    with open(ld.keyfile_path(), "w") as f:
                        json.dump(keys, f)
                return self._send(200, {"ok": True, "path": out_dat, "bytes": os.path.getsize(out_dat)})
            if path.startswith("/api/push/"):                        # export + load in the running game
                doc = path[len("/api/push/"):]
                data = self._body()
                level, out_dat, _ = export_doc(doc, data.get("key"))
                cmd = ["uv", "run", "--quiet", "--project", os.path.join(REPO, "mobile-modkit", "mcp"),
                       "python", os.path.join(HERE, "push_to_game.py"), "--dat", out_dat, "--lid", level["lid"]]
                if data.get("play", True):
                    cmd.append("--play")
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
                try:
                    rep = json.loads(r.stdout.strip().splitlines()[-1])
                except Exception:
                    return self._send(500, {"ok": False, "error": "push helper failed", "stderr": r.stderr[-2000:]})
                if rep.get("shot") and os.path.exists(rep["shot"]):
                    with open(rep["shot"], "rb") as f:
                        rep["shot_data"] = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
                return self._send(200, rep)
        except FileNotFoundError:
            return self._send(404, {"ok": False, "error": "not found"})
        except (ValueError, RuntimeError, subprocess.TimeoutExpired) as e:
            return self._send(400, {"ok": False, "error": str(e)})
        return self._send(404, {"error": "not found"})


if __name__ == "__main__":
    docs = cached_docs()
    if not docs:
        print("⚠  no cached levels in %s" % ld.CACHE)
        print("   import one first, e.g.:")
        print("   python3 tools/level-editor/leveldec.py import 1_1 build/work/assets/unpack/1_1.dat <KEYHEX>")
    else:
        print("cached levels: %s" % ", ".join(docs))
    print("Revenant level editor → http://127.0.0.1:%d" % PORT)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
