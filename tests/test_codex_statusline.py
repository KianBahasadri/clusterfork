"""Shared Codex status-line settings without starting Codex or a tmux server."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parent.parent


class CodexStatuslineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.codex_home = self.home / ".codex"
        self.codex_home.mkdir()
        self.shared = self.home / "shared-statusline.toml"
        self.shared.write_text('[tui]\nstatus_line = ["weekly-limit"]\n')
        (self.codex_home / "clusterfork-statusline.toml").symlink_to(self.shared)
        self.overrides = ["--config", 'tui.status_line=["weekly-limit"]']
        mock_bin = self.home / "bin"
        mock_bin.mkdir()
        self.binary = mock_bin / "codex"
        self.binary.write_text(
            '#!/usr/bin/env python3\n'
            'import json, sys\n'
            'print(json.dumps(sys.argv[1:]))\n'
        )
        self.binary.chmod(0o755)
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "CODEX_HOME": str(self.codex_home),
            "PATH": str(mock_bin) + os.pathsep + os.environ["PATH"],
        }

    def run_launch(self, *arguments):
        return subprocess.run(
            ["bash", "-c",
             'source shell/codex.sh; _cf_tmux() { "$@"; }; "$@"',
             "statusline-test", *arguments],
            cwd=REPO_ROOT, env=self.env, capture_output=True, text=True, timeout=5,
        )
    def launch(self, *arguments):
        proc = self.run_launch(*arguments)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_both_launchers_read_shared_settings_and_preserve_arguments(self):
        self.assertEqual(
            self.launch("codex", "--model", "chosen-model", "a prompt with spaces"),
            [*self.overrides, "--model", "chosen-model",
             "a prompt with spaces"],
        )
        self.assertEqual(
            self.launch("cc", "--last", "-c", "model_reasoning_effort=max"),
            [*self.overrides, "resume", "--yolo", "--last",
             "-c", "model_reasoning_effort=max"],
        )

    def test_explicit_profiles_are_forwarded_without_selecting_a_shared_profile(self):
        for args in (["-p", "custom"], ["--profile", "custom"],
                     ["--profile=custom"], ["-pcustom"]):
            with self.subTest(args=args):
                self.assertEqual(self.launch("codex", *args), [*self.overrides, *args])
                self.assertEqual(self.launch("cc", *args), [*self.overrides, "resume", "--yolo", *args])

    def test_explicit_config_overrides_and_prompt_are_preserved(self):
        self.assertEqual(
            self.launch("codex", "--config", 'tui.status_line=[]', "--", "--profile=prompt"),
            [*self.overrides, "--config", 'tui.status_line=[]', "--", "--profile=prompt"],
        )

    def test_edits_are_loaded_without_installing_and_other_settings_are_ignored(self):
        self.assertEqual(self.launch("codex"), self.overrides)
        self.shared.write_text(
            'model = "must-not-be-shared"\n[tui]\n'
            'status_line = ["context-used", "weekly-limit"]\n'
            'status_line_use_colors = false\n'
            'screen_reader_detection_done = true\n'
        )
        self.assertEqual(self.launch("codex"), [
            "--config", 'tui.status_line=["context-used", "weekly-limit"]',
            "--config", 'tui.status_line_use_colors=false',
        ])

    def test_invalid_settings_fail_without_launching_codex(self):
        for invalid in ('bad = [', '[tui]\nstatus_line = "invalid"',
                        '[tui]\nstatus_line = [1]', '[tui]\nstatus_line_use_colors = 1'):
            with self.subTest(invalid=invalid):
                self.shared.write_text(invalid)
                proc = self.run_launch("codex")
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout, "")
                self.assertIn("codex status line:", proc.stderr)

    def test_missing_shared_file_keeps_normal_launch_behavior(self):
        self.shared.unlink()
        self.assertEqual(self.launch("codex", "--help"), ["--help"])
        self.assertEqual(self.launch("cc", "--last"), ["resume", "--yolo", "--last"])

    def test_custom_codex_home_does_not_load_the_default_home_settings(self):
        self.env["CODEX_HOME"] = str(self.home / "isolated")
        self.assertEqual(self.launch("codex", "--help"), ["--help"])


if __name__ == "__main__":
    unittest.main()
