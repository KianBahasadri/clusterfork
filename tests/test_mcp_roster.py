"""Shared MCP roster rendering."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import mcp_config


CONTEXT7_URL = "https://mcp.context7.com/mcp"
ROSTER_PATH = REPO_ROOT / "agents" / "mcp-servers.json"


class MCPRosterTests(unittest.TestCase):
    def setUp(self):
        self.roster = mcp_config.load_roster(ROSTER_PATH)
        self.values = {
            "CONTEXT7_API_KEY": "fixture-context7",
            "PIXELLAB_API_KEY": "fixture-pixellab",
        }

    def enabled(self, name):
        return next(server["enabled"] for server in self.roster if server["name"] == name)

    def test_one_flag_reaches_every_agent(self):
        self.assertTrue(self.enabled("context7"))
        self.assertTrue(self.enabled("ElevenLabs"))
        self.assertFalse(self.enabled("linear"))
        self.assertFalse(self.enabled("chrome-devtools"))
        self.assertFalse(self.enabled("pixellab"))
        for agent in ("command-code", "opencode", "grok", "codex"):
            rendered = self.render(agent)
            for server in self.roster:
                entry = self.server(agent, rendered, server["name"])
                self.assertEqual(entry["enabled"], server["enabled"], agent)
        qwen = self.render("qwen")
        self.assertEqual(
            qwen["mcp"]["excluded"],
            [server["name"] for server in self.roster if not server["enabled"]],
        )
        antigravity = self.render("antigravity")["mcpServers"]
        for server in self.roster:
            if server["enabled"]:
                self.assertNotIn("disabled", antigravity[server["name"]])
            else:
                self.assertTrue(antigravity[server["name"]]["disabled"])
        cursor = self.render("cursor")["mcpServers"]
        self.assertEqual(set(cursor), {"context7", "ElevenLabs"})
        self.assertEqual(cursor["context7"]["url"], CONTEXT7_URL)
        self.assertEqual(cursor["context7"]["headers"]["CONTEXT7_API_KEY"], "fixture-context7")

    def test_codex_linear_stays_off_and_omits_a_missing_bearer(self):
        codex = self.server("codex", self.render("codex"), "linear")
        self.assertFalse(codex["enabled"])
        self.assertNotIn("bearer_token_env_var", codex)
        with_key = dict(self.values, LINEAR_API_KEY="fixture-linear")
        codex = self.server("codex", self.render("codex", with_key), "linear")
        self.assertEqual(codex["bearer_token_env_var"], "LINEAR_API_KEY")
        self.assertNotIn("fixture-linear", self.text("codex", with_key))

    def test_claude_splits_marketplace_user_and_disabled_plugins(self):
        settings = self.render("claude-settings")
        plugins = settings["enabledPlugins"]
        self.assertTrue(plugins["context7@claude-plugins-official"])
        self.assertFalse(plugins["linear@skills-dir"])
        self.assertNotIn("ElevenLabs@skills-dir", plugins)
        user = json.loads(mcp_config.render_claude_user(None, self.roster, self.values))
        self.assertEqual(set(user["mcpServers"]), {"ElevenLabs"})
        files = mcp_config.claude_plugin_files(self.roster, self.values)
        self.assertEqual(
            {path.split("/")[0] for path in files},
            {"linear", "chrome-devtools", "pixellab"},
        )
        linear = json.loads(files["linear/.mcp.json"])["mcpServers"]["linear"]
        self.assertEqual(linear["url"], "https://mcp.linear.app/mcp")
        self.assertNotIn("headers", linear)

    def test_agent_templates_do_not_repeat_the_roster(self):
        for path in (REPO_ROOT / "agents").rglob("*"):
            if path.suffix not in {".json", ".toml"} or path.name == "mcp-servers.json":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("mcp.linear.app", text, path.name)
            self.assertNotIn("mcp.context7.com", text, path.name)

    def test_missing_required_secret_fails_only_for_expanded_clients(self):
        values = {"PIXELLAB_API_KEY": "fixture-pixellab"}
        with self.assertRaises(SystemExit) as caught:
            self.render("cursor", values)
        self.assertIn("CONTEXT7_API_KEY", str(caught.exception))
        self.assertNotIn("fixture-pixellab", str(caught.exception))
        opencode = self.server("opencode", self.render("opencode", values), "context7")
        self.assertEqual(opencode["headers"]["CONTEXT7_API_KEY"], "{env:CONTEXT7_API_KEY}")

    def render(self, agent, values=None):
        values = self.values if values is None else values
        if agent in ("grok", "codex"):
            template = (REPO_ROOT / "agents" / f"{agent}.toml").read_text(encoding="utf-8")
            text = mcp_config.inject_toml(template, mcp_config.render_toml(agent, self.roster, values))
            parsed = tomllib.loads(text)
            self.assertIn("hooks", parsed)
            return parsed
        if agent == "claude-settings":
            template = (REPO_ROOT / "agents" / "claude.json").read_text(encoding="utf-8")
        elif agent in ("opencode", "qwen"):
            template = (REPO_ROOT / "agents" / f"{agent}.json").read_text(encoding="utf-8")
        else:
            template = None
        return json.loads(mcp_config.render_json(agent, self.roster, values, template))

    def text(self, agent, values):
        template = (REPO_ROOT / "agents" / f"{agent}.toml").read_text(encoding="utf-8")
        return mcp_config.inject_toml(template, mcp_config.render_toml(agent, self.roster, values))

    def server(self, agent, rendered, name):
        if agent == "opencode":
            return rendered["mcp"][name]
        if agent == "qwen":
            return rendered["mcpServers"][name]
        if agent in ("grok", "codex"):
            return rendered["mcp_servers"][name]
        return rendered["mcpServers"][name]

    def test_apply_writes_private_expanded_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            dest = Path(temporary) / "mcp.json"
            mcp_config.apply("cursor", self.roster, self.values, dest, None)
            self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
            self.assertIn("fixture-context7", dest.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
