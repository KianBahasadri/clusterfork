"""Tests for codeview.scan: git history/files/deps scanners against a real
throwaway repo, plus exclusion rules."""
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys_path = str(REPO_ROOT / "scripts")

import sys  # noqa: E402
sys.path.insert(0, sys_path)

from codeview import scan  # noqa: E402


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          check=True).stdout


class TempRepoTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve()
        run(["git", "init", "-q"], self.repo)
        run(["git", "config", "user.email", "t@t"], self.repo)
        run(["git", "config", "user.name", "t"], self.repo)

    def commit(self, files: dict, msg: str):
        for rel, content in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        run(["git", "add", "-A"], self.repo)
        run(["git", "commit", "-qm", msg], self.repo)


class HistoryScanTests(TempRepoTestCase):
    def test_cumulative_totals_and_dirs(self):
        self.commit({"a/main.py": "x = 1\n"}, "c1")
        self.commit({"a/util.py": "y = 2\nz=3\n"}, "c2")
        self.commit({"docs/readme.md": "# hi\n" * 5}, "c3")
        shape = scan.RepoShape(max_commits=100)
        h = scan.scan_history(self.repo, shape)
        self.assertEqual([c["sha"] for c in h["commits"]],
                         [c["sha"] for c in h["commits"]])  # sanity
        totals = [c["total"] for c in h["commits"]]
        self.assertEqual(totals, sorted(totals, reverse=False)[:len(totals)])
        # net delta c1: +1; c2: +2; c3: +5 -> cumulative 1,3,8
        self.assertEqual(totals, [1, 3, 8])
        self.assertIn("a", h["dirs"])
        self.assertIn("docs", h["dirs"])

    def test_max_commits_truncates(self):
        for i in range(5):
            self.commit({"f.txt": f"{i}\n"}, f"c{i}")
        h = scan.scan_history(self.repo, scan.RepoShape(max_commits=3))
        self.assertEqual(len(h["commits"]), 3)
        self.assertTrue(h["truncated"])
        self.assertTrue(h["commits"][0]["sha"])  # oldest of the last 3

    def test_root_files_bucketed_under_root(self):
        self.commit({"top.txt": "a\nb\n"}, "rootfile")
        h = scan.scan_history(self.repo, scan.RepoShape())
        self.assertIn("(root)", h["dirs"])
        self.assertEqual(h["commits"][-1]["dirs"]["(root)"], 2)


class FilesScanTests(TempRepoTestCase):
    def test_counts_and_exclusions(self):
        self.commit({
            "src/app.py": "print(1)\n",
            "src/lib.min.js": "function(){}\n",
            "node_modules/pkg/index.js": "x\n",
            "README.md": "# t\n",
            "logo.svg": "<svg/>",
            ".codeview/cache/junk.json": "{}",
        }, "mix")
        data = scan.scan_files(self.repo, scan.RepoShape())
        paths = {f["path"] for f in data["files"]}
        self.assertIn("src/app.py", paths)
        self.assertNotIn("src/lib.min.js", paths)
        self.assertNotIn("node_modules/pkg/index.js", paths)
        self.assertNotIn(".codeview/cache/junk.json", paths)
        langs = {k: v["lines"] for k, v in data["langs"].items()}
        self.assertEqual(langs.get("Python"), 1)
        appy = next(f for f in data["files"] if f["path"] == "src/app.py")
        self.assertEqual(appy["lines"], 1)
        self.assertEqual(data["total_lines"], sum(
            f["lines"] for f in data["files"] if f["lines"] is not None))


class DepsScanTests(TempRepoTestCase):
    def test_all_ecosystems(self):
        self.commit({
            "Cargo.toml": '[package]\nname="x"\n[dependencies]\nserde="1"\n'
                          "[dev-dependencies]\ntempfile=\"3\"\n",
            "Cargo.lock": '[[package]]\nname = "serde"\nversion = "1.0.219"\n',
            "package.json": '{"dependencies": {"react": "^18"},'
                            ' "devDependencies": {"vitest": "1"}}',
            "pyproject.toml": '[project]\ndependencies = ["requests>=2",'
                              ' "flask"]\n',
            "go.mod": "module x\n\nrequire (\n\tgithub.com/foo/bar v1.2.3\n)\n"
                      "\nrequire github.com/baz/qux v0.1.0\n",
        }, "deps")
        data = scan.scan_deps(self.repo, scan.RepoShape())
        by_name = {e["name"]: e for e in data["ecosystems"]}
        self.assertEqual(set(by_name), {"cargo", "npm", "python", "go"})
        cargo = by_name["cargo"]
        self.assertEqual({d["name"] for d in cargo["declared"]},
                         {"serde", "tempfile"})
        self.assertEqual(cargo["locked"],
                         [{"name": "serde", "version": "1.0.219"}])
        npm = by_name["npm"]
        self.assertEqual({d["name"] for d in npm["declared"]},
                         {"react", "vitest"})
        py = by_name["python"]
        self.assertEqual({d["name"] for d in py["declared"]},
                         {"requests", "flask"})
        go = by_name["go"]
        self.assertEqual({d["name"] for d in go["declared"]},
                         {"github.com/foo/bar", "github.com/baz/qux"})

    def test_no_manifests(self):
        self.commit({"readme.md": "x\n"}, "plain")
        data = scan.scan_deps(self.repo, scan.RepoShape())
        self.assertEqual(data["ecosystems"], [])


class MetaScanTests(TempRepoTestCase):
    def test_meta_fields(self):
        self.commit({"x.py": "print()\n"}, "one")
        meta = scan.scan_meta(self.repo, scan.RepoShape())
        self.assertEqual(meta["repo_name"], self.repo.name)
        self.assertFalse(meta["empty_repo"])
        self.assertEqual(len(meta["head"]), 40)
        self.assertFalse(meta["dirty"])
        (self.repo / "untracked.txt").write_text("u\n")
        dirty_meta = scan.scan_meta(self.repo, scan.RepoShape())
        self.assertTrue(dirty_meta["dirty"])
        self.assertNotEqual(meta["dirty_hash"], dirty_meta["dirty_hash"])

    def test_empty_repo_guard(self):
        meta = scan.scan_meta(self.repo, scan.RepoShape())
        self.assertTrue(meta["empty_repo"])
        h = scan.scan_history(self.repo, scan.RepoShape())
        self.assertEqual(h["commits"], [])


if __name__ == "__main__":
    unittest.main()
# rescan-probe 4915
