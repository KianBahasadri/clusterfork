"""Installer regressions for shared authentication discovery."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


class InstallerSharedAuthTests(unittest.TestCase):
    def test_grok_runtime_lock_is_not_treated_as_a_saved_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            repo = root / "repo"
            home.mkdir()
            shutil.copytree(
                REPO_ROOT,
                repo,
                ignore=shutil.ignore_patterns(
                    ".git", ".pytest_cache", ".codeview", ".env", "__pycache__"
                ),
            )
            (repo / ".env").write_text(
                "CONTEXT7_API_KEY=test\nPIXELLAB_API_KEY=test\n", encoding="utf-8"
            )
            grok_dir = home / ".grok"
            grok_dir.mkdir()
            auth = grok_dir / "auth.json"
            lock = grok_dir / "auth.json.lock"
            auth.write_text('{"token": "active"}\n', encoding="utf-8")
            lock.write_text("", encoding="utf-8")

            proc = subprocess.run(
                [str(repo / "install-clusterfork.sh")],
                cwd=repo,
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": str(home)},
                timeout=20,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn("Grok shared auth was not configured", proc.stderr)
            self.assertNotIn("auth.json must be a symlink", proc.stderr)
            self.assertTrue(auth.is_file())
            self.assertFalse(auth.is_symlink())
            self.assertTrue(lock.is_file())
            self.assertFalse(
                (home / ".local/share/clusterfork-auth/grok").exists()
            )


if __name__ == "__main__":
    unittest.main()
