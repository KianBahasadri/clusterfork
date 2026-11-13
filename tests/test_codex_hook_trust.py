"""Characterization of Codex notification-hook trust hashing."""

import unittest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import codex_hook_trust  # noqa: E402


class CodexHookTrustTests(unittest.TestCase):
    def test_stop_command_hash_matches_codex_0_150_1(self):
        # Pinned from ~/.codex/config.toml after trusting the clusterfork Stop
        # bell in Codex 0.150.1 (2026-08-31).
        command = (
            "mpv --no-video --no-terminal "
            "/home/kian/.config/clusterfork/bell.mp3"
        )
        self.assertEqual(
            codex_hook_trust.command_trust_hash("stop", command),
            "sha256:35cf80967f6c1cad1e89fce48a059568bac047ac817eaaf4ba18dd9ee1b86e15",
        )

    def test_state_key_uses_config_path_and_stop_00(self):
        self.assertEqual(
            codex_hook_trust.hook_state_key("/home/kian/.codex/config.toml", "stop"),
            "/home/kian/.codex/config.toml:stop:0:0",
        )

    def test_timeout_default_and_async_false_change_the_hash(self):
        command = "mpv bell.mp3"
        default = codex_hook_trust.command_trust_hash("stop", command)
        sync = codex_hook_trust.command_trust_hash("stop", command, runs_async=False)
        custom_timeout = codex_hook_trust.command_trust_hash(
            "stop", command, timeout_sec=30
        )
        self.assertNotEqual(default, sync)
        self.assertNotEqual(default, custom_timeout)

    def test_handler_omitted_timeout_uses_600(self):
        command = "mpv bell.mp3"
        from_handler = codex_hook_trust.trust_hash_for_handler(
            "stop", {"command": command, "async": True}
        )
        explicit = codex_hook_trust.command_trust_hash(
            "stop", command, runs_async=True, timeout_sec=600
        )
        self.assertEqual(from_handler, explicit)

    def test_post_model_request_has_its_own_trust_identity(self):
        command = "clusterfork-notify codex --attention"
        self.assertNotEqual(
            codex_hook_trust.command_trust_hash("post_model_request", command),
            codex_hook_trust.command_trust_hash("stop", command),
        )
        self.assertEqual(
            codex_hook_trust.hook_state_key("/fixture/config.toml", "post_model_request"),
            "/fixture/config.toml:post_model_request:0:0",
        )


if __name__ == "__main__":
    unittest.main()
