"""Tests for clusterfork's custom codeview modules: scripts, skills, statusline."""
import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from codeview import modules_rt  # noqa: E402

SCRIPTS_PATH = REPO_ROOT / ".codeview" / "modules" / "scripts.py"
SKILLS_PATH = REPO_ROOT / ".codeview" / "modules" / "skills.py"
STATUSLINE_PATH = REPO_ROOT / ".codeview" / "modules" / "statusline.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(f"codeview_mod_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CustomModulesTests(unittest.TestCase):
    def test_modules_load_and_register(self):
        mods = modules_rt.load_modules(REPO_ROOT / ".codeview" / "modules")
        names = {m["name"]: m for m in mods}
        self.assertIn("scripts", names)
        self.assertIn("skills", names)
        self.assertIn("statusline", names)
        self.assertNotIn("kit", names)
        self.assertEqual(len(mods), 3)
        self.assertTrue(names["scripts"]["ok"], names["scripts"]["error"])
        self.assertTrue(names["skills"]["ok"], names["skills"]["error"])
        self.assertTrue(names["statusline"]["ok"], names["statusline"]["error"])
        self.assertIn(("GET", "/m/scripts"), names["scripts"]["routes"])
        self.assertIn(("GET", "/m/skills"), names["skills"]["routes"])
        self.assertIn(("GET", "/m/statusline"), names["statusline"]["routes"])

    def test_scripts_scan_includes_launchers_bins_scripts_configs(self):
        mod = load_module("scripts", SCRIPTS_PATH)
        data = mod.scan(REPO_ROOT)
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
        scripts = {r["name"] for r in data["scripts"]}
        self.assertIn("rotate_auth.py", scripts)
        self.assertIn("install-clusterfork.sh", scripts)
        configs = {r["name"] for r in data["configs"]}
        self.assertIn("bash_profile.sh", configs)
        self.assertIn("tmux.conf", configs)
        self.assertNotIn("skills", data)

    def test_scripts_page_renders_sections(self):
        mods = modules_rt.load_modules(REPO_ROOT / ".codeview" / "modules")
        handler = next(
            m["routes"][("GET", "/m/scripts")] for m in mods if m["name"] == "scripts")
        status, body, ctype = handler(SimpleNamespace())
        self.assertEqual(status, 200)
        self.assertIn("charset=utf-8", ctype)
        self.assertIn("cl", body)
        self.assertIn(">Launch</h2>", body)
        self.assertIn(">Rotate</h2>", body)
        self.assertIn(">PATH bins</h2>", body)
        self.assertIn(">Scripts</h2>", body)
        self.assertIn(">Configs</h2>", body)
        self.assertIn("scripts-board", body)
        self.assertIn("scripts-filter", body)
        self.assertIn('data-flags="--dangerously-skip-permissions', body)
        self.assertRegex(body, r"<tr[^>]*data-flags=")

    def test_skills_scan_includes_all_skills(self):
        mod = load_module("skills", SKILLS_PATH)
        data = mod.scan(REPO_ROOT)
        skills = {r["name"] for r in data["skills"]}
        self.assertIn("commit_and_push", skills)
        self.assertIn("design-guide", skills)
        self.assertIn("ask-claude", skills)
        self.assertIn("ask-codex", skills)
        by_skill = {r["name"]: r["runs"] for r in data["skills"]}
        self.assertNotEqual(by_skill["generate_docs"], ">")
        self.assertIn("AGENTS.md", by_skill["generate_docs"])
        self.assertNotEqual(by_skill["create-github-action-tests"], ">")
        self.assertIn("GitHub Actions", by_skill["create-github-action-tests"])
        counts = data["counts"]
        self.assertGreaterEqual(counts["Skills"], 16)
        self.assertEqual(counts["Delegation"], 6)
        self.assertEqual(counts["Workflow & Design"], counts["Skills"] - 6)

    def test_skills_page_renders_skills_table(self):
        mods = modules_rt.load_modules(REPO_ROOT / ".codeview" / "modules")
        handler = next(
            m["routes"][("GET", "/m/skills")] for m in mods if m["name"] == "skills")
        status, body, ctype = handler(SimpleNamespace())
        self.assertEqual(status, 200)
        self.assertIn("charset=utf-8", ctype)
        self.assertIn("commit_and_push", body)
        self.assertIn("ask-claude", body)
        self.assertIn(">Skills</h2>", body)
        self.assertIn("skills-board", body)
        self.assertIn("skills-filter", body)
        self.assertIn('data-copy="commit_and_push"', body)

    def test_statusline_scan(self):
        mod = load_module("statusline", STATUSLINE_PATH)
        data = mod.scan(REPO_ROOT)
        files = {f["source"] for f in data["files"]}
        self.assertIn("statusline/claude/statusline.sh", files)
        self.assertIn("statusline/claude/usage-fetch.py", files)
        self.assertIn("statusline/cursor/statusline.sh", files)
        self.assertIn("statusline/cursor/usage-fetch.py", files)
        line_names = {l["name"] for l in data["lines"]}
        self.assertIn("Claude Code", line_names)
        self.assertIn("Claude Code (occ)", line_names)
        self.assertIn("Cursor Agent", line_names)
        self.assertIn("Codex CLI", line_names)
        codex_line = next(l for l in data["lines"] if l["name"] == "Codex CLI")
        self.assertIn("weekly-limit", codex_line["format"])
        self.assertEqual(data["counts"]["Statuslines"], 3)
        self.assertEqual(data["counts"]["Modes"], 4)
        self.assertEqual(data["counts"]["Scripts"], 4)

    def test_statusline_page_renders(self):
        mods = modules_rt.load_modules(REPO_ROOT / ".codeview" / "modules")
        handler = next(
            m["routes"][("GET", "/m/statusline")] for m in mods if m["name"] == "statusline")
        status, body, ctype = handler(SimpleNamespace())
        self.assertEqual(status, 200)
        self.assertIn("charset=utf-8", ctype)
        self.assertIn("Claude Code", body)
        self.assertIn("Cursor Agent", body)
        self.assertIn("Codex CLI", body)
        self.assertIn("Live Terminal Previews", body)
        self.assertIn("Configurations &amp; Formats", body)
        self.assertIn("Files &amp; Scripts", body)
        self.assertIn("statusline-filter", body)
        self.assertIn("term-model", body)


class ThemeHandoffTests(unittest.TestCase):
    """The dashboard frames module tabs with sandbox="allow-scripts
    allow-forms" and no allow-same-origin, so a module page has an opaque
    origin and localStorage raises SecurityError there. The host therefore
    hands the theme over — in the frame src for first paint, then by
    postMessage on every toggle. Both halves have to stay in step, so assert
    them together."""

    APP_JS = REPO_ROOT / "scripts" / "codeview" / "ui" / "app.js"
    INDEX = REPO_ROOT / "scripts" / "codeview" / "ui" / "index.html"
    MODULES = (SCRIPTS_PATH, SKILLS_PATH, STATUSLINE_PATH)

    def test_host_sends_theme_to_module_frames(self):
        js = self.APP_JS.read_text(encoding="utf-8")
        self.assertIn("?theme=${theme}", js)
        self.assertIn('type: "codeview-theme"', js)
        self.assertIn("broadcastTheme", js)
        # The sandbox must stay narrow: granting allow-same-origin would
        # "fix" the theme by dissolving the isolation instead.
        self.assertIn('"sandbox", "allow-scripts allow-forms"', js)

    def test_modules_accept_the_handoff(self):
        for path in self.MODULES:
            with self.subTest(module=path.name):
                mod = load_module(path.stem, path)
                page = mod.render(mod.scan(REPO_ROOT))
                self.assertIn('URLSearchParams(location.search).get("theme")',
                              page)
                self.assertIn('addEventListener("message"', page)
                self.assertIn('"codeview-theme"', page)

    def test_os_preference_stays_reachable_when_storage_throws(self):
        """The localStorage read must not wrap the prefers-color-scheme
        fallback: storage throws in the frame and in private mode, which
        would otherwise strand the page on its default theme."""
        paths = (self.INDEX,) + self.MODULES
        for path in paths:
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                read = text.index("localStorage.getItem")
                guarded = text[text.rindex("try", 0, read):
                               text.index("catch", read)]
                self.assertIn("localStorage.getItem", guarded)
                self.assertNotIn("prefers-color-scheme", guarded)

    def test_esc_keeps_falsy_values(self):
        for path in self.MODULES:
            with self.subTest(module=path.name):
                mod = load_module(path.stem, path)
                self.assertEqual(mod.esc(0), "0")
                self.assertEqual(mod.esc(False), "False")
                self.assertEqual(mod.esc(None), "")


if __name__ == "__main__":
    unittest.main()
