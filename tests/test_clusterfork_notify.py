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
NOTIFY_CMD = REPO_ROOT / "bin" / "notify"
PHONE_URL = "http://127.0.0.1:2586/clusterfork"


class NotifyFixture(unittest.TestCase):
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

    def write_prefs(self, text: str) -> None:
        (self.cf_dir / "notify-prefs").write_text(text, encoding="utf-8")

    def env(self, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        return {
            **os.environ,
            "PATH": f"{self.mock_bin}:{os.environ['PATH']}",
            "CLUSTERFORK_DIR": str(self.cf_dir),
            "CLUSTERFORK_ENV_FILE": str(self.env_file),
            "FAKE_MPV_LOG": str(self.mpv_log),
            "FAKE_CURL_LOG": str(self.curl_log),
            "FAKE_CURL_STDIN": str(self.curl_stdin),
            **(extra_env or {}),
        }

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
        return subprocess.run(
            [str(NOTIFIER), source],
            cwd=cwd or REPO_ROOT,
            input=stdin,
            capture_output=True,
            text=True,
            env=self.env(extra_env),
            timeout=5,
        )

    def run_cli(
        self,
        *args: str,
        dotenv: str = "",
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.env_file.write_text(dotenv, encoding="utf-8")
        return subprocess.run(
            [str(NOTIFY_CMD), *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env=self.env(extra_env),
            timeout=5,
        )


class NotifierTests(NotifyFixture):
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

    def test_bell_pref_off_skips_mpv_but_still_posts(self):
        self.write_prefs("bell=0\nphone=1\n")
        proc = self.run_notifier(dotenv=f"CLUSTERFORK_NTFY_URL={PHONE_URL}\n")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual((proc.stdout, proc.stderr), ("", ""))
        self.assertFalse(self.mpv_log.exists())
        self.assertTrue(self.curl_log.exists())

    def test_phone_pref_off_skips_curl_but_still_rings(self):
        self.write_prefs("phone=0\n")
        proc = self.run_notifier(dotenv=f"CLUSTERFORK_NTFY_URL={PHONE_URL}\n")
        self.assertEqual(proc.returncode, 0)
        self.assertIn(str(self.cf_dir / "bell.mp3"), self.mpv_log.read_text())
        self.assertFalse(self.curl_log.exists())

    def test_disabled_agent_is_silent_other_agents_still_notify(self):
        self.write_prefs("grok=0\n")
        grok = self.run_notifier("grok", dotenv=f"CLUSTERFORK_NTFY_URL={PHONE_URL}\n")
        self.assertEqual(grok.returncode, 0)
        self.assertEqual((grok.stdout, grok.stderr), ("", ""))
        self.assertFalse(self.mpv_log.exists())
        self.assertFalse(self.curl_log.exists())

        claude = self.run_notifier(
            "claude", dotenv=f"CLUSTERFORK_NTFY_URL={PHONE_URL}\n"
        )
        self.assertEqual(claude.returncode, 0)
        self.assertTrue(self.mpv_log.exists())
        self.assertTrue(self.curl_log.exists())

    def test_both_channels_off_is_silent(self):
        self.write_prefs("bell=0\nphone=0\n")
        proc = self.run_notifier(dotenv=f"CLUSTERFORK_NTFY_URL={PHONE_URL}\n")
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(self.mpv_log.exists())
        self.assertFalse(self.curl_log.exists())

    def test_antigravity_notifies_only_when_fully_idle(self):
        project = self.root / "secret-project"
        project.mkdir()
        payload = {
            "fullyIdle": False,
            "workspacePaths": [str(project)],
            "lastAssistantMessage": "should not escape",
        }
        quiet = self.run_notifier(
            "antigravity",
            dotenv=f"CLUSTERFORK_NTFY_URL={PHONE_URL}\n",
            stdin=json.dumps(payload),
        )
        self.assertEqual(quiet.returncode, 0)
        self.assertEqual((quiet.stdout, quiet.stderr), ("", ""))
        self.assertFalse(self.mpv_log.exists())
        self.assertFalse(self.curl_log.exists())

        payload["fullyIdle"] = True
        wrong_cwd = self.root / "wrong-cwd"
        wrong_cwd.mkdir()
        proc = self.run_notifier(
            "antigravity",
            dotenv=f"CLUSTERFORK_NTFY_URL={PHONE_URL}\n",
            stdin=json.dumps(payload),
            cwd=wrong_cwd,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual((proc.stdout, proc.stderr), ("", ""))
        self.assertTrue(self.mpv_log.exists())
        args = self.curl_log.read_text()
        self.assertIn("X-Title: Antigravity finished", args)
        self.assertIn("Turn complete in secret-project", args)
        self.assertNotIn("should not escape", args)
        self.assertNotIn("wrong-cwd", args)
        self.assertEqual(self.curl_stdin.read_text(), "")


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

        antigravity = json.loads(
            (REPO_ROOT / "agents/antigravity-hooks.json").read_text()
        )

        commands = [
            claude["hooks"]["Stop"][0]["hooks"][0]["command"],
            command["hooks"]["Stop"][0]["hooks"][0]["command"],
            grok["hooks"]["Stop"][0]["hooks"][0]["command"],
            codex["hooks"]["Stop"][0]["hooks"][0]["command"],
            antigravity["turn-bell"]["Stop"][0]["command"],
        ]
        self.assertTrue(all("clusterfork-notify" in item for item in commands))
        self.assertTrue(all("mpv" not in item for item in commands))
        self.assertIn("antigravity", antigravity["turn-bell"]["Stop"][0]["command"])

    def test_codex_repo_defaults_are_sol_ultra(self):
        with (REPO_ROOT / "agents/codex.toml").open("rb") as file:
            codex = tomllib.load(file)
        self.assertEqual(codex["model"], "gpt-5.6-sol")
        self.assertEqual(codex["model_reasoning_effort"], "ultra")


class NotifyCommandTests(NotifyFixture):
    def test_bash_n(self):
        for path in (NOTIFIER, NOTIFY_CMD):
            proc = subprocess.run(
                ["bash", "-n", str(path)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_no_args_prints_default_on_status(self):
        proc = self.run_cli(dotenv=f"CLUSTERFORK_NTFY_URL={PHONE_URL}\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(
            proc.stdout,
            "  notify  ›  on\n"
            "\n"
            "       bell           on\n"
            f"       phone          on  {PHONE_URL}\n"
            "       claude         on\n"
            "       codex          on\n"
            "       command-code   on\n"
            "       grok           on\n"
            "       antigravity    on\n",
        )

    def test_status_without_ntfy_url(self):
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("phone          on  no ntfy url", proc.stdout)

    def test_toggle_and_explicit_set_persist_for_the_hook(self):
        first = self.run_cli("bell")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, "  ✓  bell  off\n")
        self.assertEqual(
            (self.cf_dir / "notify-prefs").read_text(),
            "bell=0\nphone=1\nclaude=1\ncodex=1\ncommand-code=1\ngrok=1\nantigravity=1\n",
        )

        silent = self.run_notifier(dotenv=f"CLUSTERFORK_NTFY_URL={PHONE_URL}\n")
        self.assertEqual(silent.returncode, 0)
        self.assertFalse(self.mpv_log.exists())
        self.assertTrue(self.curl_log.exists())
        self.curl_log.unlink()

        again = self.run_cli("bell")
        self.assertEqual(again.stdout, "  ✓  bell  on\n")

        off = self.run_cli("grok", "off")
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertEqual(off.stdout, "  ✓  grok  off\n")
        grok = self.run_notifier("grok", dotenv=f"CLUSTERFORK_NTFY_URL={PHONE_URL}\n")
        self.assertEqual(grok.returncode, 0)
        self.assertFalse(self.mpv_log.exists())

        on = self.run_cli("grok", "on")
        self.assertEqual(on.stdout, "  ✓  grok  on\n")
        status = self.run_cli("status")
        self.assertIn("  notify  ›  on\n", status.stdout)
        self.assertIn("       grok           on\n", status.stdout)

    def test_all_on_and_all_off(self):
        off = self.run_cli("all", "off")
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertEqual(off.stdout, "  ✓  all  off\n")
        self.assertEqual(
            (self.cf_dir / "notify-prefs").read_text(),
            "bell=0\nphone=0\nclaude=0\ncodex=0\ncommand-code=0\ngrok=0\nantigravity=0\n",
        )
        status = self.run_cli()
        self.assertTrue(status.stdout.startswith("  notify  ›  off\n"))
        silent = self.run_notifier(dotenv=f"CLUSTERFORK_NTFY_URL={PHONE_URL}\n")
        self.assertEqual(silent.returncode, 0)
        self.assertFalse(self.mpv_log.exists())
        self.assertFalse(self.curl_log.exists())

        on = self.run_cli("all", "on")
        self.assertEqual(on.returncode, 0, on.stderr)
        self.assertEqual(on.stdout, "  ✓  all  on\n")
        restored = self.run_cli()
        self.assertTrue(restored.stdout.startswith("  notify  ›  on\n"))
        for line in (
            "       bell           on\n",
            "       claude         on\n",
            "       grok           on\n",
        ):
            self.assertIn(line, restored.stdout)

        missing = self.run_cli("all")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("notify all requires on or off", missing.stderr)

    def test_all_agents_off_status_is_off(self):
        for agent in ("claude", "codex", "command-code", "grok", "antigravity"):
            proc = self.run_cli(agent, "off")
            self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(proc.stdout.startswith("  notify  ›  off\n"))

    def test_unknown_target_and_bad_value_fail(self):
        unknown = self.run_cli("particle")
        self.assertEqual(unknown.returncode, 1)
        self.assertIn("unknown target: particle", unknown.stderr)
        bad = self.run_cli("bell", "maybe")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("expected on or off, not maybe", bad.stderr)
        extra = self.run_cli("bell", "on", "now")
        self.assertEqual(extra.returncode, 1)
        self.assertIn("unexpected argument: now", extra.stderr)

    def test_help_exits_zero(self):
        proc = self.run_cli("--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("notify bell [on|off]", proc.stdout)
        self.assertIn("notify all on|off", proc.stdout)


if __name__ == "__main__":
    unittest.main()
