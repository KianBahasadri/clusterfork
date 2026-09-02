"""Shared completion notifier and hook wiring."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
NOTIFIER = REPO_ROOT / "bin" / "clusterfork-notify"


class NotifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cf_dir = self.root / "clusterfork"
        self.cf_dir.mkdir()
        (self.cf_dir / "bell.mp3").write_bytes(b"fake bell")
        self.env_file = self.cf_dir / ".env"
        self.mock_bin = self.root / "bin"
        self.mock_bin.mkdir()
        self.mpv_log = self.root / "mpv.log"
        self.curl_log = self.root / "curl.log"
        self.curl_stdin = self.root / "curl.stdin"

        self._write_mock(
            "mpv",
            'printf "%s\\n" "$@" >"$FAKE_MPV_LOG"\n'
            'printf "mpv noise\\n"\n'
            'exit "${FAKE_MPV_EXIT:-0}"\n',
        )
        self._write_mock(
            "curl",
            'printf "%s\\n" "$@" >"$FAKE_CURL_LOG"\n'
            'cat >"$FAKE_CURL_STDIN"\n'
            'printf "curl noise\\n" >&2\n'
            'exit "${FAKE_CURL_EXIT:-0}"\n',
        )

    def _write_mock(self, name: str, body: str) -> None:
        path = self.mock_bin / name
        path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        path.chmod(0o755)

    def run_notifier(
        self,
        source: str = "codex",
        *,
        dotenv: str = "",
        stdin: str = "",
        extra_env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.env_file.write_text(dotenv, encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{self.mock_bin}:{os.environ['PATH']}",
            "CLUSTERFORK_DIR": str(self.cf_dir),
            "CLUSTERFORK_ENV_FILE": str(self.env_file),
            "FAKE_MPV_LOG": str(self.mpv_log),
            "FAKE_CURL_LOG": str(self.curl_log),
            "FAKE_CURL_STDIN": str(self.curl_stdin),
            **(extra_env or {}),
        }
        return subprocess.run(
            [str(NOTIFIER), source],
            cwd=cwd or REPO_ROOT,
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
        )

    def test_always_rings_and_skips_unconfigured_phone(self):
        proc = self.run_notifier(stdin='{"last_assistant_message":"private"}')
        self.assertEqual(proc.returncode, 0)
        self.assertEqual((proc.stdout, proc.stderr), ("", ""))
        self.assertIn(str(self.cf_dir / "bell.mp3"), self.mpv_log.read_text())
        self.assertFalse(self.curl_log.exists())

    def test_posts_fixed_project_message_with_optional_token(self):
        project = self.root / "secret-project"
        project.mkdir()
        proc = self.run_notifier(
            "claude",
            dotenv=(
                "CLUSTERFORK_NTFY_URL=http://127.0.0.1:2586/clusterfork\n"
                "CLUSTERFORK_NTFY_TOKEN=test-token\n"
            ),
            stdin="raw hook transcript must not escape",
            cwd=project,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual((proc.stdout, proc.stderr), ("", ""))
        args = self.curl_log.read_text()
        self.assertIn("X-Title: Claude finished", args)
        self.assertIn("Turn complete in secret-project", args)
        self.assertIn("Authorization: Bearer test-token", args)
        self.assertIn("http://127.0.0.1:2586/clusterfork", args)
        self.assertNotIn("raw hook transcript", args)
        self.assertEqual(self.curl_stdin.read_text(), "")

    def test_audio_and_network_failures_are_silent_and_fail_open(self):
        proc = self.run_notifier(
            dotenv="CLUSTERFORK_NTFY_URL=http://127.0.0.1:9/clusterfork\n",
            extra_env={"FAKE_MPV_EXIT": "9", "FAKE_CURL_EXIT": "22"},
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual((proc.stdout, proc.stderr), ("", ""))


class HookWiringTests(unittest.TestCase):
    def test_every_existing_stop_hook_uses_shared_notifier(self):
        claude = json.loads((REPO_ROOT / "agents/claude.json").read_text())
        command = json.loads(
            (REPO_ROOT / "agents/command-code-settings.json").read_text()
        )
        with (REPO_ROOT / "agents/grok.toml").open("rb") as file:
            grok = tomllib.load(file)
        with (REPO_ROOT / "agents/codex.toml").open("rb") as file:
            codex = tomllib.load(file)

        commands = [
            claude["hooks"]["Stop"][0]["hooks"][0]["command"],
            command["hooks"]["Stop"][0]["hooks"][0]["command"],
            grok["hooks"]["Stop"][0]["hooks"][0]["command"],
            codex["hooks"]["Stop"][0]["hooks"][0]["command"],
        ]
        self.assertTrue(all("clusterfork-notify" in item for item in commands))
        self.assertTrue(all("mpv" not in item for item in commands))

    def test_codex_repo_defaults_are_sol_ultra(self):
        with (REPO_ROOT / "agents/codex.toml").open("rb") as file:
            codex = tomllib.load(file)
        self.assertEqual(codex["model"], "gpt-5.6-sol")
        self.assertEqual(codex["model_reasoning_effort"], "ultra")


if __name__ == "__main__":
    unittest.main()
