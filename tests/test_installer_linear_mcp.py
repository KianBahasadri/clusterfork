"""Shared Linear authentication through real installs in isolated fake homes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import tomllib
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
LINEAR_URL = "https://mcp.linear.app/mcp"


class InstallerLinearMCPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.repo = self.root / "repo"
        shutil.copytree(REPO_ROOT, self.repo, ignore=shutil.ignore_patterns(
            ".git", ".pytest_cache", ".codeview", ".env", "__pycache__", ".notify-relay.sock"))
        self.env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("LINEAR_", "ROTATE_"))
        }
        self.env.update(HOME=str(self.home), CODEX_HOME=str(self.home / ".codex"))
        self.paths = {
            "codex": ".codex/config.toml",
            "antigravity": ".gemini/config/mcp_config.json",
            "command-code": ".commandcode/mcp.json",
            "opencode": ".config/opencode/opencode.json",
            "qwen": ".qwen/settings.json",
            "grok": ".grok/config.toml",
            "claude": ".claude/skills/linear/.mcp.json",
        }
        self.known_secrets = ["fixture-context7", "fixture-pixellab"]

    def write_dotenv(self, key=None, *, include_context7=True):
        lines = ["PIXELLAB_API_KEY=fixture-pixellab"]
        if include_context7:
            lines.append("CONTEXT7_API_KEY=fixture-context7")
        if key is not None:
            self.known_secrets.append(key)
            lines.append(f"LINEAR_API_KEY='{key}'")
        # Controller app credentials must not select interactive MCP key auth.
        lines.extend(("LINEAR_OAUTH_CLIENT_ID=fixture-app-id",
                      "LINEAR_OAUTH_CLIENT_SECRET=fixture-app-secret"))
        (self.repo / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def install(self, *, success=True):
        proc = subprocess.run(
            [str(self.repo / "install-clusterfork.sh")], cwd=self.repo,
            env=self.env, capture_output=True, text=True, timeout=20,
        )
        for secret in self.known_secrets + ["fixture-app-secret"]:
            if secret.strip():
                self.assertNotIn(secret, proc.stdout + proc.stderr)
        if success:
            self.assertEqual(proc.returncode, 0, proc.stderr)
        else:
            self.assertNotEqual(proc.returncode, 0)
        return proc

    def configs(self):
        data = {}
        for name, relative in self.paths.items():
            path = self.home / relative
            text = path.read_text(encoding="utf-8")
            data[name] = tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)
        return data

    def assert_auth(self, key):
        data = self.configs()
        for name, config in data.items():
            with self.subTest(client=name):
                root = "mcp_servers" if name in ("codex", "grok") else "mcpServers"
                if name == "opencode":
                    root = "mcp"
                server = config[root]["linear"]
                url_field = {"antigravity": "serverUrl", "qwen": "httpUrl"}.get(name, "url")
                self.assertEqual(server[url_field], LINEAR_URL)
                if name == "codex":
                    self.assertTrue(server["enabled"])
                    self.assertEqual(server.get("bearer_token_env_var"), "LINEAR_API_KEY" if key else None)
                    self.assertFalse(server.get("http_headers"))
                else:
                    expected = None
                    if key:
                        reference = "{env:LINEAR_API_KEY}" if name == "opencode" else "${LINEAR_API_KEY}"
                        expected = "Bearer " + (key if name in ("antigravity", "command-code") else reference)
                    self.assertEqual(server.get("headers", {}).get("Authorization"), expected)
                if name in ("opencode", "command-code", "grok"):
                    self.assertFalse(server["enabled"])
                elif name == "antigravity":
                    self.assertTrue(server["disabled"])
                elif name == "qwen":
                    self.assertIn("linear", config["mcp"]["excluded"])
        settings = json.loads((self.home / ".claude/settings.json").read_text())
        self.assertFalse(settings["enabledPlugins"]["linear@skills-dir"])
        # Linear remains absent in Cursor, which has no shipped Linear entry.
        cursor = json.loads((self.home / ".cursor/mcp.json").read_text())
        self.assertNotIn("linear", cursor["mcpServers"])
        return data

    def exported_key(self):
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c",
             'source "$1"; python3 -c \'import os; print(os.environ.get("LINEAR_API_KEY", ""))\'',
             "test", str(self.home / ".config/clusterfork/bash_profile.sh")],
            cwd=self.root, env=self.env, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.rstrip("\n")

    def test_shared_key_uses_client_secret_resolution_without_shell_or_recursive_expansion(self):
        key = 'fixture-linear-$(touch EXECUTED)-`touch EXECUTED`-${MISSING}-"quoted"-\\slash'
        self.write_dotenv(key)
        sources = {path: path.read_bytes() for path in (self.repo / "agents").rglob("*") if path.is_file()}
        self.install()
        self.assert_auth(key)
        self.assertEqual(self.exported_key(), key)
        self.assertFalse((self.root / "EXECUTED").exists())
        self.assertFalse((self.repo / "EXECUTED").exists())
        for name in ("codex", "grok", "opencode", "qwen", "claude"):
            self.assertNotIn(key, (self.home / self.paths[name]).read_text())
        for relative in (".config/clusterfork/.env", ".gemini/config/mcp_config.json", ".commandcode/mcp.json", ".cursor/mcp.json"):
            self.assertEqual(stat.S_IMODE((self.home / relative).stat().st_mode), 0o600)
        self.assertEqual((self.home / ".config/clusterfork/.env").read_bytes(), (self.repo / ".env").read_bytes())
        for path, contents in sources.items():
            self.assertEqual(path.read_bytes(), contents)

    def test_missing_empty_and_whitespace_key_leave_oauth_usable_despite_ambient_key(self):
        self.env["LINEAR_API_KEY"] = "fixture-ambient-key-must-not-select-auth"
        self.known_secrets.append(self.env["LINEAR_API_KEY"])
        for key in (None, "", "   "):
            with self.subTest(key=key):
                self.write_dotenv(key)
                self.install()
                self.assert_auth(None)
                for relative in self.paths.values():
                    self.assertNotIn("fixture-ambient-key", (self.home / relative).read_text())

    def test_rotation_removal_and_reinstall_preserve_runtime_settings_and_oauth_caches(self):
        codex_path = self.home / self.paths["codex"]
        codex_path.parent.mkdir()
        codex_path.write_text('model = "fixture-model"\napproval_policy = "on-request"\n'
                              'notify = ["retired-bell"]\n\n'
                              '[projects."/fixture-project"]\ntrust_level = "trusted"\n\n'
                              '[hooks.state."fixture-other-hook"]\ntrusted_hash = "fixture-hash"\n')
        agy_path = self.home / ".gemini/antigravity-cli/settings.json"
        agy_path.parent.mkdir(parents=True)
        agy_path.write_text(json.dumps({"trustedWorkspaces": ["/fixture-workspace"], "model": "fixture-model"}))
        grok_path = self.home / self.paths["grok"]
        grok_path.parent.mkdir()
        grok_path.write_text('[ui]\ntheme = "fixture-theme"\n')
        caches = [self.home / ".codex/.credentials.json",
                  self.home / ".gemini/antigravity/mcp_oauth_tokens.json"]
        for cache in caches:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text('{"fixture": "existing-client-oauth"}\n')
        for key in ("fixture-linear-first", "fixture-linear-rotated", None):
            with self.subTest(key=key):
                self.write_dotenv(key)
                self.install()
                first = {relative: (self.home / relative).read_bytes() for relative in self.paths.values()}
                self.install()
                self.assertEqual(first, {relative: (self.home / relative).read_bytes() for relative in first})
                data = self.assert_auth(key)
                self.assertEqual(self.exported_key(), key or "")
                codex = data["codex"]
                self.assertEqual(codex["model"], "fixture-model")
                self.assertEqual(codex["approval_policy"], "on-request")
                self.assertEqual(codex["projects"]["/fixture-project"]["trust_level"], "trusted")
                self.assertEqual(codex["hooks"]["state"]["fixture-other-hook"]["trusted_hash"], "fixture-hash")
                self.assertNotIn("notify", codex)
                self.assertEqual(data["grok"]["ui"]["theme"], "fixture-theme")
                agy = json.loads(agy_path.read_text())
                self.assertIn("/fixture-workspace", agy["trustedWorkspaces"])
                self.assertEqual(agy["model"], "fixture-model")
                for cache in caches:
                    self.assertEqual(cache.read_text(), '{"fixture": "existing-client-oauth"}\n')
                if key != "fixture-linear-first":
                    for relative in self.paths.values():
                        self.assertNotIn("fixture-linear-first", (self.home / relative).read_text())
                if key is None:
                    for relative in self.paths.values():
                        self.assertNotIn("fixture-linear-rotated", (self.home / relative).read_text())

    def test_other_required_placeholders_still_fail_without_printing_secrets(self):
        self.write_dotenv("fixture-linear-do-not-log", include_context7=False)
        proc = self.install(success=False)
        self.assertIn("CONTEXT7_API_KEY", proc.stderr)


if __name__ == "__main__":
    unittest.main()
