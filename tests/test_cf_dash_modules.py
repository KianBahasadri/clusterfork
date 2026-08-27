"""Tests for cf_dash.modules_rt: discovery, namespacing, broken modules."""
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cf_dash import modules_rt  # noqa: E402


class ModuleLoadingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mods = Path(self.tmp.name) / "modules"
        self.mods.mkdir()

    def write(self, name: str, body: str):
        (self.mods / name).write_text(body)

    def test_good_module_registers_route(self):
        self.write("hello.py", (
            'NAME = "hello"\n'
            'DESCRIPTION = "says hi"\n'
            'def register(reg):\n'
            '    def page(req):\n'
            '        return (200, "<h1>hi</h1>", "text/html")\n'
            '    reg.add_route("GET", "", page)\n'))
        mods = modules_rt.load_modules(self.mods)
        self.assertEqual(len(mods), 1)
        m = mods[0]
        self.assertTrue(m["ok"])
        self.assertEqual(m["name"], "hello")
        self.assertEqual(m["description"], "says hi")
        self.assertIn(("GET", "/m/hello"), m["routes"])

    def test_broken_module_captured_not_raised(self):
        self.write("bad.py", "def register(reg)\n")
        mods = modules_rt.load_modules(self.mods)
        self.assertEqual(len(mods), 1)
        self.assertFalse(mods[0]["ok"])
        self.assertIn("SyntaxError", mods[0]["error"])

    def test_missing_register_is_broken(self):
        self.write("noconf.py", "NAME = 'x'\n")
        mods = modules_rt.load_modules(self.mods)
        self.assertFalse(mods[0]["ok"])

    def test_name_defaults_to_stem_and_validated(self):
        self.write("Weird Name.py", "def register(reg):\n    pass\n")
        mods = modules_rt.load_modules(self.mods)
        self.assertFalse(mods[0]["ok"])  # stem not slug-safe? no — stem is fine
        # Stem "Weird Name" contains a space; must fail NAME validation.
        self.assertIn("invalid NAME", mods[0]["error"])

    def test_route_escaped_namespace_gets_doubled_key_but_still_namespaced(self):
        self.write("esc.py", (
            'NAME = "esc"\n'
            'def register(reg):\n'
            '    reg.add_route("GET", "/m/other/tab",\n'
            '                  lambda r: (200, "", "text/html"))\n'))
        mods = modules_rt.load_modules(self.mods)
        routes = list(mods[0]["routes"].keys())
        self.assertEqual(routes, [("GET", "/m/esc/m/other/tab")])

    def test_duplicate_route_rejected(self):
        self.write("dup.py", (
            'NAME = "dup"\n'
            'def register(reg):\n'
            '    reg.add_route("GET", "", lambda r: (200, "", ""))\n'
            '    reg.add_route("GET", "/", lambda r: (200, "", ""))\n'))
        mods = modules_rt.load_modules(self.mods)
        self.assertFalse(mods[0]["ok"])
        self.assertIn("duplicate route", mods[0]["error"])

    def test_bad_method_rejected(self):
        self.write("m.py", (
            'def register(reg):\n'
            '    reg.add_route("BREW", "/x", lambda r: (200, "", ""))\n'))
        mods = modules_rt.load_modules(self.mods)
        self.assertFalse(mods[0]["ok"])

    def test_sorted_discovery_order(self):
        self.write("b_one.py", "def register(reg):\n    pass\n")
        self.write("a_two.py", "def register(reg):\n    pass\n")
        mods = modules_rt.load_modules(self.mods)
        self.assertEqual([m["file"] for m in mods],
                         sorted(str(p) for p in self.mods.glob("*.py")))

    def test_empty_dir_returns_empty(self):
        self.assertEqual(modules_rt.load_modules(self.mods), [])

    def test_missing_dir_returns_empty(self):
        self.assertEqual(
            modules_rt.load_modules(self.mods / "nope"), [])


if __name__ == "__main__":
    unittest.main()
