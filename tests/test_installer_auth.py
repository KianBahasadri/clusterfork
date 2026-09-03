"""Installer regressions for shared authentication discovery."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = REPO_ROOT / "install-clusterfork.sh"


class InstallerSharedAuthTests(unittest.TestCase):
    def test_grok_runtime_lock_is_not_treated_as_a_saved_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            grok_dir = home / ".grok"
            grok_dir.mkdir()
            auth = grok_dir / "auth.json"
            lock = grok_dir / "auth.json.lock"
            auth.write_text('{"token": "active"}\n', encoding="utf-8")
            lock.write_text("", encoding="utf-8")

            proc = subprocess.run(
                [str(INSTALLER)],
                cwd=REPO_ROOT,
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
