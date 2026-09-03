"""Tests for codeview.scan: git history/files/deps scanners against a real
throwaway repo, plus exclusion rules."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys_path = str(REPO_ROOT / "scripts")

import sys  # noqa: E402
sys.path.insert(0, sys_path)

from codeview import metrics, scan  # noqa: E402


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

    def commit(self, files: dict[str, str | bytes], msg: str):
        for rel, content in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                p.write_bytes(content)
            else:
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
        self.assertEqual(h["churn"]["total"], 6)
        self.assertEqual(h["churn"]["files"][0]["commits"], 3)

    def test_root_files_bucketed_under_root(self):
        self.commit({"top.txt": "a\nb\n"}, "rootfile")
        h = scan.scan_history(self.repo, scan.RepoShape())
        self.assertIn("(root)", h["dirs"])
        self.assertEqual(h["commits"][-1]["dirs"]["(root)"], 2)

    def test_churn_counts_rewrites_orders_and_skips_non_code(self):
        self.commit({
            "src/hot.py": "1\n2\n3\n4\n5\n",
            "a.py": "one\ntwo\n",
            "lib/b.py": "1\n2\n3\n4\n",
            "lib/c.py": "1\n2\n3\n4\n",
            "package-lock.json": "generated\n" * 20,
            "assets/blob.bin": b"\x00\x01\x02",
        }, "baseline")
        self.commit({"a.py": "ONE\ntwo\n"}, "rewrite one line")

        churn = scan.scan_history(self.repo, scan.RepoShape())["churn"]
        self.assertEqual(churn["total"], 17)
        self.assertEqual(churn["file_count"], 4)
        self.assertEqual(
            [row["path"] for row in churn["files"]],
            ["src/hot.py", "a.py", "lib/b.py", "lib/c.py"],
        )
        self.assertEqual(
            churn["files"][1],
            {
                "path": "a.py", "additions": 3, "deletions": 1,
                "churn": 4, "commits": 2,
                "last_date": run(
                    ["git", "log", "-1", "--format=%cI", "--", "a.py"],
                    self.repo,
                ).strip()[:16],
            },
        )
        self.assertEqual(
            [(row["name"], row["churn"], row["commits"])
             for row in churn["dirs"]],
            [("lib", 8, 1), ("src", 5, 1), ("(root)", 4, 2)],
        )
        self.assertNotIn("package-lock.json",
                         {row["path"] for row in churn["files"]})
        self.assertNotIn("assets/blob.bin",
                         {row["path"] for row in churn["files"]})

    def test_same_size_rewrite_has_churn_despite_zero_net_delta(self):
        self.commit({"same.py": "before\n"}, "before")
        self.commit({"same.py": "after\n"}, "after")

        history = scan.scan_history(
            self.repo, scan.RepoShape(max_commits=1))
        self.assertEqual(history["commits"][0]["delta"], 0)
        self.assertEqual(history["churn"]["total"], 2)
        self.assertEqual(history["churn"]["files"], [{
            "path": "same.py", "additions": 1, "deletions": 1,
            "churn": 2, "commits": 1,
            "last_date": history["commits"][0]["date"],
        }])

    def test_churn_caps_file_rows_without_truncating_totals(self):
        files = {f"files/f{i:03}.py": "x\n" for i in range(205)}
        self.commit(files, "many files")

        churn = scan.scan_history(self.repo, scan.RepoShape())["churn"]
        self.assertEqual(churn["total"], 205)
        self.assertEqual(churn["file_count"], 205)
        self.assertEqual(len(churn["files"]), scan.MAX_CHURN_FILES)
        self.assertEqual(churn["files"][0]["path"], "files/f000.py")
        self.assertEqual(churn["files"][-1]["path"], "files/f199.py")

    def test_repo_shape_includes_scan_schema_version(self):
        self.assertEqual(scan.RepoShape(max_commits=42).as_dict(), {
            "max_commits": 42,
            "schema_version": scan.SCAN_SCHEMA_VERSION,
        })

    def test_churn_normalizes_rename_paths_to_destinations(self):
        raw = (
            "\x01" + "a" * 40
            + "\t2026-01-02T03:04:05+00:00\trenames\n"
            + "1\t2\told.py => new.py\n"
            + "3\t4\tdir/{old => new}/x.py\n"
            + "0\t0\tempty.py => renamed.py\n"
        )
        with mock.patch.object(scan, "try_git", return_value=raw):
            churn = scan.scan_history(
                self.repo, scan.RepoShape())["churn"]

        self.assertEqual(churn["total"], 10)
        self.assertEqual(
            [row["path"] for row in churn["files"]],
            ["dir/new/x.py", "new.py"],
        )
        self.assertEqual(
            [(row["name"], row["churn"]) for row in churn["dirs"]],
            [("dir", 7), ("(root)", 3)],
        )


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


class MetricsTests(unittest.TestCase):
    def test_python_source_metrics_ignore_strings_and_count_structure(self):
        source = (
            "import os\n"
            "# TODO: review\n"
            "class Thing:\n"
            "    def run(self, x):\n"
            "        text = \"# not a comment\"\n"
            "        if x and x > 1:  # inline NOTE\n"
            "            return True\n"
            "        else:\n"
            "            return False\n"
            "\n"
        )
        result = metrics.analyze_source(source, "Python")
        self.assertTrue(result["analyzed"])
        self.assertEqual(result["analysis"], "Python AST + lexical metrics")
        self.assertEqual(result["total_lines"], 10)
        self.assertEqual(result["code_lines"], 8)
        self.assertEqual(result["blank_lines"], 1)
        self.assertEqual(result["comment_lines"], 2)
        self.assertEqual(result["comment_only_lines"], 1)
        self.assertEqual(result["inline_comment_lines"], 1)
        self.assertEqual(result["functions"], 1)
        self.assertEqual(result["classes"], 1)
        self.assertEqual(result["imports"], 1)
        self.assertEqual(result["function_names"], ["run"])
        self.assertEqual(result["class_names"], ["Thing"])
        self.assertEqual(result["import_names"], ["os"])
        self.assertEqual(result["decision_points"], 2)
        self.assertEqual(result["cyclomatic_complexity"], 3)
        self.assertEqual(result["returns"], 2)
        self.assertEqual(result["todo_markers"], {"NOTE": 1, "TODO": 1})
        self.assertGreater(result["halstead"]["volume"], 0)
        self.assertIsNotNone(result["maintainability_index"])

    def test_javascript_metrics_include_arrow_functions_and_import_names(self):
        source = (
            "import tool from \"tool\";\n"
            "const run = (value) => {\n"
            "  // TODO: handle zero\n"
            "  if (value && value > 0) return tool(value);\n"
            "  return 0;\n"
            "};\n"
        )
        result = metrics.analyze_source(source, "JavaScript")
        self.assertEqual(result["functions"], 1)
        self.assertEqual(result["imports"], 1)
        self.assertEqual(result["import_names"], ["tool"])
        self.assertEqual(result["comment_only_lines"], 1)
        self.assertEqual(result["decision_points"], 2)
        self.assertEqual(result["cyclomatic_complexity"], 3)
        self.assertEqual(result["todo_markers"], {"TODO": 1})

    def test_files_scan_attaches_metrics_and_aggregate_totals(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        repo = Path(self.tmp.name).resolve()
        run(["git", "init", "-q"], repo)
        run(["git", "config", "user.email", "t@t"], repo)
        run(["git", "config", "user.name", "t"], repo)
        (repo / "app.py").write_text("def f():\n    return 1\n")
        run(["git", "add", "-A"], repo)
        run(["git", "commit", "-qm", "metrics"], repo)
        data = scan.scan_files(repo, scan.RepoShape())
        entry = data["files"][0]
        self.assertEqual(entry["metrics"]["functions"], 1)
        self.assertEqual(data["metric_totals"]["functions"], 1)
        self.assertEqual(data["total_code_lines"], 2)


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
