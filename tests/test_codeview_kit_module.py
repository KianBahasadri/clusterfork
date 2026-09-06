"""The clusterfork kit module inventories launchers, skills, scripts, bins."""
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from codeview import modules_rt  # noqa: E402

KIT_PATH = REPO_ROOT / ".codeview" / "modules" / "kit.py"


def load_kit():
    spec = importlib.util.spec_from_file_location("codeview_mod_kit", KIT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class KitModuleTests(unittest.TestCase):
    def test_module_loads_and_registers(self):
        mods = modules_rt.load_modules(REPO_ROOT / ".codeview" / "modules")
        names = {m["name"]: m for m in mods}
        self.assertIn("kit", names)
        self.assertTrue(names["kit"]["ok"], names["kit"]["error"])
        self.assertIn(("GET", "/m/kit"), names["kit"]["routes"])

    def test_scan_includes_launchers_skills_and_bins(self):
        kit = load_kit()
        data = kit.scan(REPO_ROOT)
        names = {r["name"] for r in data["launchers"]}
        self.assertIn("cl", names)
        self.assertIn("cc", names)
        self.assertIn("occ", names)
        self.assertIn("rotate-claude", names)
        self.assertNotIn("_cf_tmux", names)
        by_name = {r["name"]: r["runs"] for r in data["launchers"]}
        self.assertIn("rotate_auth.py", by_name["rotate-claude"])
        self.assertIn("OpenCode Go", by_name["occ"])
        self.assertIn("codex resume", by_name["cc"])
        flags = {r["name"]: r["flags"] for r in data["launchers"]}
        self.assertIn("--dangerously-skip-permissions", flags["cl"])
        self.assertIn("--effort max", flags["cl"])
        self.assertIn("--yolo", flags["cc"])
        self.assertIn("--list", flags["rotate-claude"])
        self.assertIn("--effort", flags["occ"])
        bins = {r["name"] for r in data["bins"]}
        self.assertIn("codeview", bins)
        self.assertIn("notify", bins)
        skills = {r["name"] for r in data["skills"]}
        self.assertIn("commit_and_push", skills)
        self.assertIn("design-guide", skills)
        by_skill = {r["name"]: r["runs"] for r in data["skills"]}
        self.assertNotEqual(by_skill["generate_docs"], ">")
        self.assertIn("AGENTS.md", by_skill["generate_docs"])
        self.assertNotEqual(by_skill["create-github-action-tests"], ">")
        self.assertIn("GitHub Actions", by_skill["create-github-action-tests"])
        scripts = {r["name"] for r in data["scripts"]}
        self.assertIn("rotate_auth.py", scripts)
        self.assertIn("install-clusterfork.sh", scripts)

    def test_page_renders_inventory(self):
        mods = modules_rt.load_modules(REPO_ROOT / ".codeview" / "modules")
        handler = next(
            m["routes"][("GET", "/m/kit")] for m in mods if m["name"] == "kit")
        status, body, ctype = handler(SimpleNamespace())
        self.assertEqual(status, 200)
        self.assertIn("charset=utf-8", ctype)
        self.assertIn("cl", body)
        self.assertIn(">Launch</h2>", body)
        self.assertIn(">Rotate</h2>", body)
        self.assertIn("kit-board", body)
        self.assertIn("kit-filter", body)
        self.assertIn('data-flags="--dangerously-skip-permissions', body)
        self.assertRegex(body, r"<tr[^>]*data-flags=")


if __name__ == "__main__":
    unittest.main()
