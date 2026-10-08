"""Type fidelity (synthetic data, no game files):  python3 -m unittest discover tools/level-editor/tests"""
import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import leveldec as ld  # noqa: E402


def browser(level):
    """What the editor sends back: JSON.parse/stringify turns 27.0 into 27."""
    def strip(o):
        if isinstance(o, float) and o.is_integer():
            return int(o)
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items()}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o
    return strip(json.loads(json.dumps(level)))


ORIG = {
    "lid": "1_5", "type": 0, "times": [30.0, 25.0, 20.0],
    "Entities": [
        {"Type": "EditorPhysicsObject",
         "Properties": {"position": [1.0, 2.0], "tag": 3, "spline": True, "mountedSprites": [1], "density": 1.0},
         "Vertexes": [{"x": 0.0, "y": 0.0, "segments": 4.0}, {"x": 10.0, "y": 0.0, "segments": 4.0},
                      {"x": 10.0, "y": 10.0, "segments": 4.0}]},
        {"Type": "EditorSprite", "Properties": {"position": [5.0, 5.0], "z": 3, "scale": 1.0, "frame": "a.png"}},
        {"Type": "EditorPhysicsObject",             # the one mixed path: segments int here
         "Properties": {"position": [0.0, 0.0], "tag": 0},
         "Vertexes": [{"x": 1.0, "y": 1.0, "segments": 1}, {"x": 2.0, "y": 1.0, "segments": 1},
                      {"x": 2.0, "y": 2.0, "segments": 1}]},
    ],
}


def typed_equal(a, b, path="$"):
    if type(a) is not type(b):
        return f"{path}: {type(a).__name__} {a!r} != {type(b).__name__} {b!r}"
    if isinstance(a, dict):
        for k in set(a) | set(b):
            r = typed_equal(a.get(k), b.get(k), f"{path}.{k}")
            if r:
                return r
    elif isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: len {len(a)} != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            r = typed_equal(x, y, f"{path}[{i}]")
            if r:
                return r
    elif isinstance(a, float) and math.isnan(a) and math.isnan(b):
        return None
    elif a != b:
        return f"{path}: {a!r} != {b!r}"
    return None


class Retype(unittest.TestCase):
    def setUp(self):
        self.schema = ld.type_schema([ORIG])

    def sent(self, level):
        b = browser(level)
        for i, e in enumerate(b["Entities"]):
            e["__src"] = i
        return b

    def test_unedited_round_trip_is_type_identical(self):
        out = ld.retype_level(self.sent(ORIG), ORIG, self.schema)
        self.assertIsNone(typed_equal(out, ORIG))

    def test_mixed_path_follows_its_source_row(self):
        out = ld.retype_level(self.sent(ORIG), ORIG, self.schema)
        self.assertIs(type(out["Entities"][0]["Vertexes"][0]["segments"]), float)
        self.assertIs(type(out["Entities"][2]["Vertexes"][0]["segments"]), int)

    def test_edits_keep_types(self):
        s = self.sent(ORIG)
        s["times"] = [40, 30, 20]                                   # medal edit typed in as ints
        s["Entities"][0]["Vertexes"].insert(1, {"x": 5, "y": -3, "segments": 4})   # inserted vertex
        s["Entities"][1]["Properties"]["position"] = [7.5, 8]       # moved sprite
        out = ld.retype_level(s, ORIG, self.schema)
        self.assertEqual(out["times"], [40.0, 30.0, 20.0])
        self.assertTrue(all(type(t) is float for t in out["times"]))
        v = out["Entities"][0]["Vertexes"][1]
        self.assertEqual((type(v["x"]), type(v["y"]), type(v["segments"])), (float, float, float))
        self.assertEqual(out["Entities"][1]["Properties"]["position"], [7.5, 8.0])
        self.assertIs(type(out["Entities"][1]["Properties"]["z"]), int)

    def test_new_entity_without_src_uses_schema(self):
        s = self.sent(ORIG)
        s["Entities"].append({"Type": "EditorSprite", "Properties": {"position": [1, 2], "z": 4, "scale": 2, "frame": "b.png"}})
        out = ld.retype_level(s, ORIG, self.schema)
        P = out["Entities"][-1]["Properties"]
        self.assertEqual((type(P["position"][0]), type(P["z"]), type(P["scale"])), (float, int, float))
        self.assertNotIn("__src", out["Entities"][-1])

    def test_non_finite_reals_survive_the_wire(self):
        lv = {"lid": "1_1", "times": [1.0, 2.0, 3.0], "Entities": [{"Type": "T", "Properties": {"radius": float("nan"), "far": float("inf")}}]}
        wire = json.dumps(ld.to_wire(lv), allow_nan=False)           # strict JSON: no bare NaN token
        back = ld.from_wire(json.loads(wire))
        self.assertTrue(math.isnan(back["Entities"][0]["Properties"]["radius"]))
        self.assertEqual(back["Entities"][0]["Properties"]["far"], float("inf"))

    def test_src_of_another_type_is_ignored(self):
        s = self.sent(ORIG)
        s["Entities"][1]["__src"] = 0                               # wrong-type template must not apply
        out = ld.retype_level(s, ORIG, self.schema)
        self.assertIs(type(out["Entities"][1]["Properties"]["z"]), int)


if __name__ == "__main__":
    unittest.main()
