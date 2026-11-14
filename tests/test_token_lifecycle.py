"""Credential refreshes must finish before another owner can transfer the login."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "scripts")]

from common.accounts import WorkerError
from call_budget import CallBudget
from test_independent_accounts import login
import test_rotate_accounts as rotation_fixtures
import accounts
import rotate_auth


class TokenLifecycleTests(unittest.TestCase):
    def fixture(self, provider):
        fixture = rotation_fixtures.ScopedRotationTests(methodName="runTest")
        self.addCleanup(fixture.doCleanups)
        backend, pools, client = fixture.fixture(provider)
        fixture.command(backend, client, "--migrate-from-local-to-remote")
        pool = pools.pools[provider]
        return fixture, backend, pool, client, pool.list()[0]

    def test_transfer_and_deletion_wait_for_quota_refresh_then_use_latest_tokens(self):
        for provider in ("codex", "cursor"):
            for action in (("--migrate-from-remote-to-local",), ("--remote", "--delete", "work")):
                with self.subTest(provider=provider, action=action):
                    fixture, backend, pool, client, identity = self.fixture(provider)
                    owner = "quota:refresh-in-progress"
                    lease = pool.claim(identity, owner)
                    before = pool.get(identity)
                    local_before = fixture.local_state(backend)
                    with patch("builtins.input", return_value="y"), \
                         CallBudget(self, api=client, exact={"api.claim": 1}), \
                         self.assertRaisesRegex(rotate_auth.RotateError, "busy"):
                        fixture.command(backend, client, *action)
                    self.assertEqual(pool.get(identity), before)
                    self.assertEqual(fixture.local_state(backend), local_before)
                    refreshed = login(provider, "local", 1)
                    pool.save(identity, owner, lease["lease"], refreshed, release=True)
                    with patch("builtins.input", return_value="y"), \
                         CallBudget(self, api=client, exact={"api.claim": 1, "api.delete": 1}):
                        self.assertEqual(fixture.command(backend, client, *action)[0], 0)
                    self.assertEqual(pool.list(), [])
                    restored = backend.store_dir / "auth.json.work"
                    if action == ("--migrate-from-remote-to-local",):
                        self.assertEqual(json.loads(restored.read_text()), refreshed)
                    else:
                        self.assertFalse(restored.exists())

    def test_worker_cannot_discard_an_in_progress_quota_refresh(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                _, _, pool, _, identity = self.fixture(provider)
                owner = "quota:refresh-in-progress"
                quota = pool.claim(identity, owner)
                before = pool.get(identity)
                with self.assertRaisesRegex(WorkerError, "busy"):
                    pool.claim(identity, "execution-worker", reuse=True)
                self.assertEqual(pool.get(identity), before)
                refreshed = login(provider, "local", 1)
                pool.save(identity, owner, quota["lease"], refreshed, release=True)
                worker = pool.claim(identity, "execution-worker", reuse=True)
                self.assertEqual(worker["auth"], refreshed)
                self.assertNotEqual(worker["lease"], quota["lease"])

    def test_migrating_one_alias_detaches_the_selected_copy_of_that_login(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                fixture = rotation_fixtures.ScopedRotationTests(methodName="runTest")
                self.addCleanup(fixture.doCleanups)
                backend, pools, client = fixture.fixture(provider)
                accounts.write_private(backend.store_dir / "auth.json.alias", login(provider, "local"))
                with patch.object(rotate_auth, "is_interactive", return_value=True), \
                     patch.object(rotate_auth, "prompt_migration", return_value="migrate-from-local-to-remote"), \
                     patch.object(rotate_auth, "prompt_profile_selection", return_value=["alias"]), \
                     CallBudget(self, api=client, exact={"api.snapshot": 1, "api.enroll": 1,
                                                       "api.describe": 2, "api.get": 1}):
                    self.assertEqual(fixture.command(backend, client, "--migrate")[0], 0)
                self.assertFalse(backend.auth.is_symlink())
                self.assertFalse(backend.current.is_symlink())
                self.assertTrue((backend.store_dir / "auth.json.work").is_file())
                self.assertEqual(backend.local_profiles(), [])
                self.assertEqual(len(pools.pools[provider].list()), 1)

    def test_shared_kickoff_keeps_refreshes_when_cli_replaces_auth_symlink(self):
        for provider in ("codex", "cursor"):
            for interrupted in (False, True):
                with self.subTest(provider=provider, interrupted=interrupted):
                    fixture = rotation_fixtures.ScopedRotationTests(methodName="runTest")
                    self.addCleanup(fixture.doCleanups)
                    backend, _, _ = fixture.fixture(provider)
                    accounts.write_private(backend.store_dir / "auth.json.alpha", login(provider, "other", account="two"))
                    refreshed = {"alpha": login(provider, "other", 1, account="two"),
                                 "work": login(provider, "local", 1)}

                    def ping(command):
                        rotate_auth.atomic_write_text(backend.auth, json.dumps(refreshed[backend.current_suffix()]))
                        if interrupted:
                            raise KeyboardInterrupt()
                        return False, "model request failed after refreshing"

                    with patch.object(rotate_auth, "ping_agent", side_effect=ping):
                        if interrupted:
                            with self.assertRaises(KeyboardInterrupt):
                                backend.kickoff()
                        else:
                            self.assertEqual(backend.kickoff(), 1)
                    self.assertEqual(json.loads((backend.store_dir / "auth.json.alpha").read_text()), refreshed["alpha"])
                    expected = login(provider, "local") if interrupted else refreshed["work"]
                    self.assertEqual(json.loads((backend.store_dir / "auth.json.work").read_text()), expected)
                    self.assertTrue(backend.auth.is_symlink())
                    self.assertEqual(backend.current_suffix(), "work")
                    self.assertEqual(json.loads(backend.auth.read_text()), expected)

    def test_migration_refuses_an_unsaved_refresh_of_the_transferred_login(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                fixture = rotation_fixtures.ScopedRotationTests(methodName="runTest")
                self.addCleanup(fixture.doCleanups)
                backend, pools, client = fixture.fixture(provider)
                backend.auth.unlink()
                current = login(provider, "local", 1)
                accounts.write_private(backend.auth, current)
                before = fixture.local_state(backend)
                with CallBudget(self, api=client, exact={"api.snapshot": 1}), \
                     self.assertRaisesRegex(rotate_auth.RotateError, "save.*before migrating"):
                    fixture.command(backend, client, "--migrate-from-local-to-remote")
                self.assertEqual(json.loads(backend.auth.read_text()), current)
                self.assertEqual(fixture.local_state(backend), before)
                self.assertEqual(pools.pools[provider].list(), [])

    def test_kickoff_preserves_an_unexpected_replacement_login(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                fixture = rotation_fixtures.ScopedRotationTests(methodName="runTest")
                self.addCleanup(fixture.doCleanups)
                backend, _, _ = fixture.fixture(provider)
                different = login(provider, "different", account="two")

                def ping(command):
                    rotate_auth.atomic_write_text(backend.auth, json.dumps(different))
                    return True, "ok"

                with patch.object(rotate_auth, "ping_agent", side_effect=ping), \
                     self.assertRaisesRegex(rotate_auth.RotateError, "active credentials were kept"):
                    backend.kickoff()
                self.assertFalse(backend.auth.is_symlink())
                self.assertEqual(json.loads(backend.auth.read_text()), different)
                self.assertEqual(json.loads((backend.store_dir / "auth.json.work").read_text()), login(provider, "local"))

    def test_claude_kickoff_saves_refreshes_and_restores_the_updated_login(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted), tempfile.TemporaryDirectory() as directory, \
                 patch.dict("os.environ", {"ROTATE_CLAUDE_DIR": directory}):
                backend = rotate_auth.ClaudeBackend()
                original = {"claudeAiOauth": {"accessToken": "old-access", "refreshToken": "old-refresh"}}
                refreshed = {"claudeAiOauth": {"accessToken": "new-access", "refreshToken": "new-refresh"}}
                profile = Path(directory) / ".credentials.json.work"
                profile.write_text(json.dumps(original))
                backend.active.write_text(json.dumps(original))
                other = Path(directory) / ".credentials.json.alpha"
                other.write_text(json.dumps({"claudeAiOauth": {"accessToken": "other-access"}}))

                def ping(command):
                    backend.active.write_text(json.dumps(refreshed))
                    if interrupted:
                        raise KeyboardInterrupt()
                    return False, "model request failed after refreshing"

                with patch.object(rotate_auth, "ping_agent", side_effect=ping):
                    if interrupted:
                        with self.assertRaises(KeyboardInterrupt):
                            backend.kickoff()
                    else:
                        self.assertEqual(backend.kickoff(), 1)
                self.assertEqual(json.loads(other.read_text()), refreshed)
                expected = original if interrupted else refreshed
                self.assertEqual(json.loads(profile.read_text()), expected)
                self.assertEqual(json.loads(backend.active.read_text()), expected)


if __name__ == "__main__":
    unittest.main()
