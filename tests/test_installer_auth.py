"""Installer regressions for shared authentication discovery."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from test_independent_accounts import login
from common.accounts import account_identity, login_fingerprints


REPO_ROOT = Path(__file__).resolve().parent.parent


class InstallerSharedAuthTests(unittest.TestCase):
    def test_reinstall_updates_both_guards_and_archives_legacy_cloud_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home, repo = root / "home", root / "repo"
            home.mkdir()
            shutil.copytree(REPO_ROOT, repo, ignore=shutil.ignore_patterns(
                ".git", ".pytest_cache", ".codeview", ".env", "__pycache__", ".notify-relay.sock"))
            (repo / ".env").write_text("CONTEXT7_API_KEY=test\nPIXELLAB_API_KEY=test\n")
            installed = home / ".config/clusterfork/scripts/rotate_auth.py"
            installed.parent.mkdir(parents=True)
            # Reproduce a stale installed rotator that only guards Codex.
            installed.write_text((REPO_ROOT / "scripts/rotate_auth.py").read_text().replace(
                'if self.command not in {"rotate-codex", "rotate-cursor-cli"}:',
                'if self.command != "rotate-codex":'))
            stores, links, manifests = {}, {}, {}
            for provider, agent_dir in (("codex", home / ".codex"), ("cursor", home / ".config/cursor")):
                store = home / ".local/share/clusterfork-auth" / provider
                stores[provider] = store
                store.mkdir(parents=True)
                agent_dir.mkdir(parents=True)
                for name in ("local-a", "local-b", "21kb60"):
                    (store / f"auth.json.{name}").write_text(json.dumps(login(provider, account=name)))
                (store / "current").symlink_to("auth.json.local-a")
                (agent_dir / "auth.json").symlink_to(os.path.relpath(store / "current", agent_dir))
                links[provider] = (agent_dir / "auth.json").readlink()
                source = store / "auth.json.21kb60"
                record = {"account_id": account_identity(login(provider, account="21kb60"), provider),
                          "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
                manifests[provider] = {"21kb60": {**record, "ownership": "cloud",
                    "fingerprints": login_fingerprints(login(provider, account="21kb60"), provider)}}
                if provider == "cursor":
                    backup = store / "remote-staged/auth.json.21kb60"
                    backup.parent.mkdir()
                    source.rename(backup)
                    record.update(local_backup="remote-staged/auth.json.21kb60",
                                  state="staged", staged_for_issue="KIAN-922")
                (store / "remote-profiles.json").write_text(json.dumps({"21kb60": record}))

            env = {key: value for key, value in os.environ.items() if not key.startswith("ROTATE_")}
            env["HOME"] = str(home)
            for _ in range(2):
                proc = subprocess.run([str(repo / "install-clusterfork.sh")], cwd=repo,
                                      capture_output=True, text=True, env=env, timeout=20)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertNotIn("shared auth was not configured", proc.stderr)
                for provider in ("codex", "cursor"):
                    store = stores[provider]
                    self.assertFalse((store / "auth.json.21kb60").exists())
                    self.assertTrue((store / "cloud-reserved/21kb60.json").is_file())
                    self.assertEqual((store / "current").readlink(), Path("auth.json.local-a"))
                    self.assertEqual(json.loads((store / "remote-profiles.json").read_text()), manifests[provider])
                    self.assertEqual(hashlib.sha256((store / "cloud-reserved/21kb60.json").read_bytes()).hexdigest(),
                                     manifests[provider]["21kb60"]["source_sha256"])
                    agent_dir = home / (".codex" if provider == "codex" else ".config/cursor")
                    self.assertEqual((agent_dir / "auth.json").readlink(), links[provider])

            for provider, command in (("codex", "rotate-codex"), ("cursor", "rotate-cursor-cli")):
                def run_wrapper(*args):
                    return subprocess.run([
                        "bash", "--noprofile", "--norc", "-c", 'source "$1"; shift; "$@"',
                        "test", str(home / f".config/clusterfork/shell/{provider}.sh"), command, *args],
                        cwd=root, capture_output=True, text=True, env=env, timeout=10)
                listed = run_wrapper("--list")
                self.assertEqual(listed.returncode, 0, listed.stderr)
                self.assertIn("21kb60", listed.stdout)
                self.assertIn("1 profiles reserved for remote work", listed.stdout)
                refused = run_wrapper("21kb60")
                self.assertEqual(refused.returncode, 1)
                self.assertIn("reserved for remote work", refused.stderr)
                self.assertIn(f"--provider {provider}", refused.stderr)
                self.assertEqual((stores[provider] / "current").readlink(), Path("auth.json.local-a"))

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
                    ".git",
                    ".pytest_cache",
                    ".codeview",
                    ".env",
                    "__pycache__",
                    ".notify-relay.sock",
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

    def test_antigravity_preserves_trusted_workspaces_and_runtime_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            repo = root / "repo"
            home.mkdir()
            shutil.copytree(
                REPO_ROOT,
                repo,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".pytest_cache",
                    ".codeview",
                    ".env",
                    "__pycache__",
                    ".notify-relay.sock",
                ),
            )
            (repo / ".env").write_text(
                "CONTEXT7_API_KEY=test\nPIXELLAB_API_KEY=test\n", encoding="utf-8"
            )

            agy_dir = home / ".gemini/antigravity-cli"
            agy_dir.mkdir(parents=True)
            settings_path = agy_dir / "settings.json"
            settings_path.write_text(
                json.dumps({
                    "colorScheme": "light",
                    "model": "Gemini 3.8 Flash (High)",
                    "trustedWorkspaces": ["/home/kian/custom_project"],
                }),
                encoding="utf-8",
            )

            proc = subprocess.run(
                [str(repo / "install-clusterfork.sh")],
                cwd=repo,
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": str(home)},
                timeout=20,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("preserves trustedWorkspaces", proc.stdout)

            data = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("colorScheme"), "dark")
            self.assertFalse(data.get("enableTelemetry"))
            self.assertFalse(data.get("showFeedbackSurvey"))
            self.assertEqual(data.get("model"), "Gemini 3.8 Flash (High)")
            workspaces = data.get("trustedWorkspaces", [])
            self.assertIn("/home/kian/custom_project", workspaces)
            self.assertIn("/home/kian/.config/clusterfork", workspaces)
            self.assertIn("/home/kian/steam_cart_evaluator", workspaces)

    def test_antigravity_fresh_install_creates_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            repo = root / "repo"
            home.mkdir()
            shutil.copytree(
                REPO_ROOT,
                repo,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".pytest_cache",
                    ".codeview",
                    ".env",
                    "__pycache__",
                    ".notify-relay.sock",
                ),
            )
            (repo / ".env").write_text(
                "CONTEXT7_API_KEY=test\nPIXELLAB_API_KEY=test\n", encoding="utf-8"
            )

            settings_path = home / ".gemini/antigravity-cli/settings.json"

            proc = subprocess.run(
                [str(repo / "install-clusterfork.sh")],
                cwd=repo,
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": str(home)},
                timeout=20,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(settings_path.is_file())
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("colorScheme"), "dark")
            self.assertFalse(data.get("enableTelemetry"))
            self.assertFalse(data.get("showFeedbackSurvey"))
            self.assertEqual(
                data.get("trustedWorkspaces"),
                [
                    "/home/kian/.config/clusterfork",
                    "/home/kian/steam_cart_evaluator",
                ],
            )


if __name__ == "__main__":
    unittest.main()
