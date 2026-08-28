"""Tests for cf_dash.ci: origin slug parsing and check-run state mapping,
via a mock gh on PATH (house mock pattern, as in test_cf_dash_wrapper)."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cf_dash import ci  # noqa: E402


def git(cmd, cwd):
    return subprocess.run(["git", "-C", str(cwd), *cmd],
                          capture_output=True, text=True, check=True).stdout


class GithubSlugTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        git(["init", "-q"], self.repo)

    def _slug(self, url):
        git(["remote", "add", "origin", url], self.repo)
        try:
            return ci.github_slug(self.repo)
        finally:
            git(["remote", "remove", "origin"], self.repo)

    def test_https_url(self):
        self.assertEqual(
            self._slug("https://github.com/KianBahasadri/clusterfork.git"),
            ("KianBahasadri", "clusterfork"))

    def test_ssh_url(self):
        self.assertEqual(
            self._slug("git@github.com:KianBahasadri/clusterfork.git"),
            ("KianBahasadri", "clusterfork"))

    def test_no_dotgit_suffix(self):
        self.assertEqual(
            self._slug("https://github.com/o/r"),
            ("o", "r"))

    def test_non_github_remote(self):
        self.assertIsNone(
            self._slug("https://gitlab.com/o/r.git"))

    def test_no_remote(self):
        self.assertIsNone(ci.github_slug(self.repo))


class CiStateTests(unittest.TestCase):
    """github_ci_state with a canned-response gh mock."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.canned = self.base / "canned.json"
        self.mock_bin = self.base / "mock-bin"
        self.mock_bin.mkdir()
        (self.mock_bin / "gh").write_text(
            '#!/bin/bash\n'
            f'cat "{self.canned}"\n')
        (self.mock_bin / "gh").chmod(0o755)
        self.repo = self.base / "r"
        self.repo.mkdir()
        git(["init", "-q"], self.repo)
        git(["remote", "add", "origin",
             "https://github.com/o/r.git"], self.repo)
        self.old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.mock_bin}:{self.old_path}"
        self.addCleanup(self._restore_path)

    def _restore_path(self):
        os.environ["PATH"] = self.old_path

    def _runs(self, runs):
        self.canned.write_text(json.dumps({"check_runs": runs}))
        return ci.github_ci_state(self.repo, "abc123")

    def test_all_success_is_passing(self):
        self.assertEqual(self._runs([
            {"status": "completed", "conclusion": "success"},
            {"status": "completed", "conclusion": "success"},
        ]), "passing")

    def test_any_failure_is_failing(self):
        self.assertEqual(self._runs([
            {"status": "completed", "conclusion": "success"},
            {"status": "completed", "conclusion": "failure"},
        ]), "failing")

    def test_pending_is_running(self):
        self.assertEqual(self._runs([
            {"status": "completed", "conclusion": "success"},
            {"status": "in_progress", "conclusion": None},
        ]), "running")

    def test_no_runs_is_none(self):
        self.assertIsNone(self._runs([]))

    def test_no_gh_on_path_is_none(self):
        os.environ["PATH"] = str(self.base / "empty")  # no gh anywhere
        os.makedirs(self.base / "empty", exist_ok=True)
        self.assertIsNone(ci.github_ci_state(self.repo, "abc123"))

    def test_no_head_is_none(self):
        self.assertIsNone(ci.github_ci_state(self.repo, ""))


if __name__ == "__main__":
    unittest.main()
