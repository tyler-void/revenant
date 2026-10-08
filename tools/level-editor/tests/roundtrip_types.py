#!/usr/bin/env python3
"""Type-fidelity proof on a REAL level (local only: needs the decoded cache, the level key and the
unidbg oracle; nothing it touches is committed):

    decode (cache) -> browser-style save via the editor API -> export (.dat, real cipher) -> decrypt -> compare

  python3 tools/level-editor/tests/roundtrip_types.py --doc 1_25      (editor server must be running)

Passes when the re-decoded level is TYPE-identical (int vs real, every value) to the original decode.
Also prints the negative control: how many values the old untyped save path would have turned real->int.
"""
import argparse
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import leveldec as ld                                         # noqa: E402
from test_retype import browser, typed_equal                  # noqa: E402


def api(port, path, body):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def drift(a, b, n=0):
    """count values whose numeric type differs (float in a, int in b)"""
    if isinstance(a, dict):
        return sum(drift(v, b.get(k)) for k, v in a.items())
    if isinstance(a, list):
        return sum(drift(x, y) for x, y in zip(a, b))
    return int(isinstance(a, float) and type(b) is int)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doc", default="1_25")
    ap.add_argument("--port", type=int, default=int(os.environ.get("LEVEL_EDITOR_PORT", "8778")))
    a = ap.parse_args()
    key = ld.level_key()
    if not key:
        print("SKIP: no level key in levels/keys.json")
        return 0
    with open(ld.cache_path(a.doc)) as f:
        orig = json.load(f)
    rt = a.doc + "-rt"
    p = ld.cache_path(rt)
    if os.path.exists(p):                                     # our own scratch doc from a previous run
        os.remove(p)
    print("copy:", api(a.port, f"/api/copy/{a.doc}", {"name": rt}))

    # what the browser really receives: GET the level and parse it STRICTLY (a bare NaN would throw)
    with urllib.request.urlopen(f"http://127.0.0.1:{a.port}/api/level/{rt}", timeout=60) as r:
        def no_nan(c):
            raise ValueError("non-strict JSON token " + c)
        wire = json.loads(r.read(), parse_constant=no_nan)
    sent = browser(wire)                                      # what index.html POSTs (27.0 -> 27)
    for i, e in enumerate(sent["Entities"]):
        e["__src"] = i
    print("negative control: an untyped save would turn", drift(orig, sent), "real values into ints")
    print("save:", api(a.port, f"/api/level/{rt}", sent))
    with open(p) as f:
        saved = json.load(f)
    r1 = typed_equal(saved, orig)
    print("saved file vs original decode:", r1 or "TYPE-IDENTICAL")

    exp = api(a.port, f"/api/export/{rt}", {})
    print("export:", exp)
    raw = ld.decrypt_dat(exp["path"], key)
    with open(raw, "rb") as f:
        back = ld.raw_to_level(f.read())
    back = ld.level_to_json(back)
    r2 = typed_equal(back, orig)
    print("re-decoded .dat vs original decode:", r2 or "TYPE-IDENTICAL")
    os.remove(p)
    ok = r1 is None and r2 is None
    print("ROUND TRIP:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
