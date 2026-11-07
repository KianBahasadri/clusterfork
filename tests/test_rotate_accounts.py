"""Scoped rotator workflows with synthetic sign-ins and the real SQLite pool."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "automation")]
import accounts
import rotate_accounts
import rotate_auth
from call_budget import CallBudget, record_api_calls
from common.accounts import WorkerError, account_identity
from controller.accounts import Accounts
from controller.account_usage import snapshot
from controller.store import Database
from test_independent_accounts import login, NOW


class PoolClient:
    """Mirror the operator client's transfer owner and retry semantics."""

    def __init__(self, pools, provider):
        self.pools, self.provider = pools, provider
        self.pool = pools.pools[provider]
        self.transfer_id = str(uuid4())

    def snapshot(self):
        return snapshot(self.pools, self.provider, now=NOW)

    def enroll(self, auth, *, aliases=()):
        return self.pool.enroll(auth, aliases)

    def describe(self, identity, *, aliases=()):
        return self.pool.describe(identity, aliases=aliases)

    def get(self, identity):
        return self.pool.get(identity)

    def register_local(self, identity, session_id, fingerprints):
        return self.pool.register_local(identity, session_id, fingerprints)

    def claim(self, identity, owner, *, allow_staged=False):
        assert owner == "local-reclaim" and allow_staged
        return self.pool.claim(identity, "reclaim:" + self.transfer_id, allow_staged=True, reuse=True)

    def delete(self, identity, fence):
        return self.pool.delete(identity, fence, owner="reclaim:" + self.transfer_id)


class ScopedRotationTests(unittest.TestCase):
    def fixture(self, provider, *, local=True):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        database = Database(root / "accounts.db")
        self.addCleanup(database.close)
        pools = Accounts(database, clock=lambda: NOW)
        client = record_api_calls(PoolClient(pools, provider))
        backend = rotate_auth.SharedStoreBackend(
            command="rotate-codex" if provider == "codex" else "rotate-cursor-cli", label=provider,
            agent_dir_env="SCOPED_TEST_AGENT", store_dir_env="SCOPED_TEST_STORE",
            default_agent=root / "agent", default_store=root / "store")
        backend.agent_dir.mkdir()
        if local:
            accounts.write_private(backend.store_dir / "auth.json.work", login(provider, "local"))
            backend.current.symlink_to("auth.json.work")
            backend.auth.symlink_to(os.path.relpath(backend.current, backend.agent_dir))
        return backend, pools, client

    def command(self, backend, client, *args):
        output = io.StringIO()
        with patch.object(rotate_auth, "make_backend", return_value=backend), \
             patch.object(accounts, "connect", return_value=client), redirect_stdout(output):
            code = rotate_auth.run(["cursor" if "cursor" in backend.command else "codex", *args])
        return code, output.getvalue()

    def provider_login(self, backend, provider, session):
        if backend.auth.is_symlink():
            backend.auth.unlink()  # Simulate --unhook followed by provider login.
        accounts.write_private(backend.auth, login(provider, session))

    def save_remote_login(self, backend, client, name="work", session="remote"):
        self.provider_login(backend, client.provider, session)
        return self.command(backend, client, "--remote", "--save", name)

    def local_state(self, backend):
        return (os.readlink(backend.auth) if backend.auth.is_symlink() else None,
                os.readlink(backend.current) if backend.current.is_symlink() else None,
                {path.name: path.read_bytes() for path in backend.store_dir.glob("auth.json.*")})

    def test_local_then_remote_same_name_keeps_both_sessions_and_refreshes_separate(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider)
                before = self.local_state(backend)
                with CallBudget(self, api=client, exact={"api.enroll": 1, "api.register_local": 1, "api.describe": 1}):
                    self.assertEqual(self.save_remote_login(backend, client)[0], 0)
                self.assertEqual(self.local_state(backend), before)
                identity = account_identity(login(provider), provider)
                pool = pools.pools[provider]
                self.assertEqual(json.loads(pool.get(identity)[0]["auth"]), login(provider, "remote"))
                self.assertEqual(client.enroll.call_args.args[0], login(provider, "remote"))
                self.assertTrue(all(len(value) == 64 for value in client.register_local.call_args.args[2]))
                row = accounts.availability(backend.store_dir, provider)["accounts"][0]
                self.assertEqual(row["aliases"], ["work"])
                self.assertTrue(row["local_available"] and row["cloud_available"])
                local = backend.store_dir / "auth.json.work"
                accounts.write_private(local, login(provider, "local", 1))
                lease = pool.claim(identity, "worker")
                pool.save(identity, "worker", lease["lease"], login(provider, "remote", 1), release=True)
                self.assertEqual(json.loads(backend.auth.read_text()), login(provider, "local", 1))
                with patch.object(rotate_auth, "ping_agent", return_value=(True, "ok")) as ping:
                    self.command(backend, client, "--local", "--kickoff", "work")
                    ping.assert_called_once()
                self.assertEqual(json.loads(pool.get(identity)[0]["auth"]), login(provider, "remote", 1))
                list_output = self.command(backend, client, "--list")[1]
                self.assertRegex(list_output, r"work\s+\[local\]\s+\[remote\]")
                self.assertEqual(len([line for line in list_output.splitlines() if "work" in line]), 1)
                self.assertNotIn("remote.work", self.command(backend, client, "--local", "--list")[1])

    def test_remote_then_local_same_name_uses_current_login_without_extra_flags(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider, local=False)
                self.save_remote_login(backend, client)
                self.assertFalse(backend.auth.exists())
                self.assertEqual(list(backend.store_dir.glob("auth.json.*")), [])
                self.provider_login(backend, provider, "local")
                with patch("subprocess.run", side_effect=AssertionError("started provider login")), \
                     CallBudget(self, api=client, exact={"api.register_local": 1, "api.describe": 1}):
                    self.assertEqual(self.command(backend, client, "--local", "--save", "work")[0], 0)
                self.assertEqual(json.loads(backend.auth.read_text()), login(provider, "local"))
                identity = account_identity(login(provider), provider)
                self.assertEqual(json.loads(pools.pools[provider].get(identity)[0]["auth"]), login(provider, "remote"))
                joined = accounts.consumer_snapshot(backend.store_dir, provider, client)["accounts"][0]
                self.assertEqual(joined["aliases"], ["work"])

    def test_save_without_scope_in_non_interactive_shell_fails_with_usage(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, _, client = self.fixture(provider, local=False)
                self.provider_login(backend, provider, "local")
                with patch("subprocess.run", side_effect=AssertionError("started provider login")), \
                     patch.object(rotate_auth, "is_interactive", return_value=False), \
                     CallBudget(self, api=client):
                    with self.assertRaises(rotate_auth.RotateError) as ctx:
                        self.command(backend, client, "--save", "personal")
                    self.assertIn("usage:", "\n".join(ctx.exception.lines))

    def test_save_without_scope_in_interactive_shell_prompts_and_saves(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider, scope="local"):
                backend, _, client = self.fixture(provider, local=False)
                self.provider_login(backend, provider, "local")
                with patch("subprocess.run", side_effect=AssertionError("started provider login")), \
                     patch.object(rotate_auth, "is_interactive", return_value=True), \
                     patch.object(rotate_auth, "prompt_scope", return_value="local"), \
                     CallBudget(self, api=client):
                    self.assertEqual(self.command(backend, client, "--save", "personal")[0], 0)
                self.assertEqual(json.loads(backend.auth.read_text()), login(provider, "local"))
                self.assertEqual(backend.current_suffix(), "personal")

            with self.subTest(provider=provider, scope="remote"):
                backend, pools, client = self.fixture(provider, local=False)
                self.provider_login(backend, provider, "remote")
                with patch("subprocess.run", side_effect=AssertionError("started provider login")), \
                     patch.object(rotate_auth, "is_interactive", return_value=True), \
                     patch.object(rotate_auth, "prompt_scope", return_value="remote"), \
                     CallBudget(self, api=client, exact={"api.enroll": 1, "api.describe": 1}):
                    self.assertEqual(self.command(backend, client, "--save", "cloudprof")[0], 0)
                identity = account_identity(login(provider), provider)
                self.assertEqual(json.loads(pools.pools[provider].get(identity)[0]["auth"]), login(provider, "remote"))

    def test_saving_a_refreshed_registered_local_login_keeps_its_session(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider, local=False)
                self.save_remote_login(backend, client)
                self.provider_login(backend, provider, "local")
                self.command(backend, client, "--local", "--save", "work")
                before = accounts.load_remote_profiles(backend.store_dir)["work"]["session_id"]
                backend.auth.unlink()
                accounts.write_private(backend.auth, login(provider, "local", 1))
                with CallBudget(self, api=client):
                    self.command(backend, client, "--local", "--save", "work")
                self.assertEqual(accounts.load_remote_profiles(backend.store_dir)["work"]["session_id"], before)
                self.assertEqual(json.loads(backend.auth.read_text()), login(provider, "local", 1))
                identity = pools.pools[provider].list()[0]
                self.assertEqual(json.loads(pools.pools[provider].get(identity)[0]["auth"]), login(provider, "remote"))

    def test_remote_save_cannot_reuse_local_session(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider)
                self.provider_login(backend, provider, "local")
                before = self.local_state(backend), backend.auth.read_bytes()
                with CallBudget(self, api=client), self.assertRaisesRegex(rotate_auth.RotateError, "reuse a local"):
                    self.command(backend, client, "--remote", "--save", "work")
                self.assertEqual((self.local_state(backend), backend.auth.read_bytes()), before)
                self.assertEqual(pools.pools[provider].list(), [])

    def test_existing_remote_login_is_never_replaced_by_reenrollment(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider)
                pool = pools.pools[provider]
                identity = pool.enroll(login(provider, "existing-remote", 2))
                with self.assertRaisesRegex(rotate_auth.RotateError, "already has remote credentials"):
                    self.save_remote_login(backend, client)
                self.assertEqual(json.loads(pool.get(identity)[0]["auth"]), login(provider, "existing-remote", 2))
                self.assertEqual(json.loads(backend.auth.read_text()), login(provider, "local"))

    def test_interrupted_remote_save_resumes_and_preserves_cloud_refreshes(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider)
                before = self.local_state(backend)
                self.provider_login(backend, provider, "remote")
                with patch.object(accounts, "write_private", side_effect=OSError("interrupted")), \
                     self.assertRaises(rotate_auth.RotateError):
                    self.command(backend, client, "--remote", "--save", "work")
                self.assertEqual(self.local_state(backend), before)
                pool = pools.pools[provider]
                identity = account_identity(login(provider), provider)
                lease = pool.claim(identity, "worker")
                pool.save(identity, "worker", lease["lease"], login(provider, "remote", 1), release=True)
                with patch("subprocess.run", side_effect=AssertionError("started provider login")):
                    self.command(backend, client, "--save", "work", "--remote")
                self.assertEqual(self.local_state(backend), before)
                self.assertEqual(json.loads(pool.get(identity)[0]["auth"]), login(provider, "remote", 1))
                self.assertTrue(accounts.availability(backend.store_dir, provider)["accounts"][0]["local_available"])

    def test_busy_local_save_retries_without_losing_either_session(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider, local=False)
                self.save_remote_login(backend, client)
                pool = pools.pools[provider]
                identity = pool.list()[0]
                lease = pool.claim(identity, "worker")
                self.provider_login(backend, provider, "local")
                before = backend.auth.read_bytes()
                with self.assertRaisesRegex(rotate_auth.RotateError, "busy"):
                    self.command(backend, client, "--local", "--save", "work")
                self.assertEqual(backend.auth.read_bytes(), before)
                self.assertFalse(backend.auth.is_symlink())
                pool.save(identity, "worker", lease["lease"], release=True)
                self.command(backend, client, "--local", "--save", "work")
                self.assertEqual(json.loads(backend.auth.read_text()), login(provider, "local"))
                self.assertEqual(json.loads(pool.get(identity)[0]["auth"]), login(provider, "remote"))

    def test_delete_remote_preserves_local_login_and_can_be_cancelled(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider)
                self.save_remote_login(backend, client)
                before = self.local_state(backend)
                with patch("builtins.input", return_value="n"), CallBudget(self, api=client):
                    self.assertEqual(self.command(backend, client, "--remote", "--delete", "work")[0], 1)
                self.assertEqual(self.local_state(backend), before)
                with patch("builtins.input", return_value="yes"), \
                     CallBudget(self, api=client, exact={"api.claim": 1, "api.delete": 1}):
                    self.assertEqual(self.command(backend, client, "--remote", "--delete", "work")[0], 0)
                self.assertEqual(pools.pools[provider].list(), [])
                self.assertEqual(self.local_state(backend), before)
                row = accounts.availability(backend.store_dir, provider)["accounts"][0]
                self.assertTrue(row["local_available"])
                self.assertFalse(row["cloud_available"])

    def test_interrupted_remote_deletion_recovers_original_transfer_owner(self):
        for provider in ("codex", "cursor"):
            for stage in ("delete", "manifest"):
                with self.subTest(provider=provider, stage=stage):
                    backend, pools, client = self.fixture(provider)
                    self.save_remote_login(backend, client)
                    before = self.local_state(backend)
                    original_write = accounts.write_private
                    writes = []
                    def write(path, value):
                        writes.append(path)
                        if len(writes) == 2:
                            raise OSError("interrupted after deletion")
                        original_write(path, value)
                    failure = (patch.object(client, "delete", side_effect=OSError("interrupted")) if stage == "delete"
                               else patch.object(accounts, "write_private", side_effect=write))
                    with failure, patch("builtins.input", return_value="y"), self.assertRaises(rotate_auth.RotateError):
                        self.command(backend, client, "--remote", "--delete", "work")
                    fresh_client = record_api_calls(PoolClient(pools, provider))
                    with patch("builtins.input", return_value="y"):
                        self.command(backend, fresh_client, "--remote", "--delete", "work")
                    self.assertEqual(self.local_state(backend), before)
                    self.assertEqual(pools.pools[provider].list(), [])

    def test_local_save_rejects_known_remote_credentials_and_keeps_source(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider, local=False)
                self.save_remote_login(backend, client)
                self.provider_login(backend, provider, "remote")
                before = backend.auth.read_bytes()
                with self.assertRaisesRegex(rotate_auth.RotateError, "known cloud token|provider session"):
                    self.command(backend, client, "--local", "--save", "work")
                self.assertEqual(backend.auth.read_bytes(), before)
                self.assertEqual(list(backend.store_dir.glob("auth.json.*")), [])
                identity = pools.pools[provider].list()[0]
                self.assertEqual(json.loads(pools.pools[provider].get(identity)[0]["auth"]), login(provider, "remote"))

    def test_remote_listing_uses_only_cached_snapshot_and_lists_other_hosts(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider, local=False)
                pools.pools[provider].enroll(login(provider, "remote"), aliases=["other-host"])
                with CallBudget(self, api=client, exact={"api.snapshot": 1}):
                    code, output = self.command(backend, client, "--list", "--remote")
                self.assertEqual(code, 0)
                self.assertIn("other-host (active;", output)
                for token in login(provider, "remote").get("tokens", login(provider, "remote")).values():
                    self.assertNotIn(token, output)

    def test_scoped_delete_can_discard_an_unfinished_signin(self):
        for provider in ("codex", "cursor"):
            for scope in ("local", "remote"):
                with self.subTest(provider=provider, scope=scope):
                    backend, _, client = self.fixture(provider)
                    before = self.local_state(backend)
                    manager = rotate_accounts.ScopedAccounts(backend)
                    home = manager.login_home(scope, "unfinished")
                    accounts.write_private(manager.auth_path(home), login(provider, "pending"))
                    expected = {"api.snapshot": 1} if scope == "remote" else {}
                    with patch("builtins.input", return_value="n"), CallBudget(self, api=client, exact=expected):
                        self.assertEqual(self.command(backend, client, "--" + scope, "--delete", "unfinished")[0], 1)
                    self.assertTrue(home.exists())
                    with patch("builtins.input", return_value="y"), CallBudget(self, api=client, exact=expected):
                        self.assertEqual(self.command(backend, client, "--" + scope, "--delete", "unfinished")[0], 0)
                    self.assertFalse(home.exists())
                    self.assertEqual(self.local_state(backend), before)

    def test_single_login_migration_deduplicates_aliases_and_is_repeatable(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider)
                accounts.write_private(backend.store_dir / "auth.json.alias", login(provider, "local"))
                with patch("subprocess.run", side_effect=AssertionError("signed in")), \
                     CallBudget(self, api=client, exact={"api.snapshot": 1, "api.enroll": 1,
                         "api.describe": 2, "api.get": 1}):
                    self.command(backend, client, "--migrate-from-local-to-remote")
                self.assertEqual(list(backend.store_dir.glob("auth.json.*")), [])
                self.assertFalse(backend.auth.is_symlink())
                self.assertFalse(backend.current.is_symlink())
                manifest = accounts.load_remote_profiles(backend.store_dir, migrate=False)
                self.assertEqual(set(manifest), {"remote:work", "remote:alias"})
                self.assertFalse((backend.store_dir / accounts.CLOUD_DIRECTORY).exists())
                row = accounts.availability(backend.store_dir, provider)["accounts"][0]
                self.assertTrue(row["cloud_available"])
                self.assertFalse(row["local_available"])
                pool = pools.pools[provider]
                identity = pool.list()[0]
                lease = pool.claim(identity, "worker")
                pool.save(identity, "worker", lease["lease"], login(provider, "local", 1), release=True)
                with CallBudget(self, api=client, exact={"api.snapshot": 1, "api.describe": 1, "api.get": 1}):
                    self.command(backend, client, "--migrate-from-local-to-remote")
                self.assertEqual(json.loads(pool.get(identity)[0]["auth"]), login(provider, "local", 1))

    def test_single_login_migration_keeps_independent_local_session(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, _, client = self.fixture(provider)
                self.save_remote_login(backend, client)
                before = self.local_state(backend)
                with CallBudget(self, api=client, exact={"api.snapshot": 1, "api.get": 2, "api.describe": 1}):
                    self.command(backend, client, "--migrate-from-local-to-remote")
                self.assertEqual(self.local_state(backend), before)
                listing = self.command(backend, client, "--list")[1]
                self.assertRegex(listing, r"work\s+\[local\]\s+\[remote\]")
                self.assertEqual(len([line for line in listing.splitlines() if "work" in line]), 1)
                self.assertNotIn("reserved", listing)

    def test_remote_token_copy_with_same_name_is_migrated_without_duplicate_metadata(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider, local=False)
                self.save_remote_login(backend, client)
                accounts.write_private(backend.store_dir / "auth.json.work", login(provider, "remote"))
                self.command(backend, client, "--migrate-from-local-to-remote")
                self.assertEqual(set(accounts.load_remote_profiles(backend.store_dir)), {"remote:work"})
                self.assertEqual(list(backend.store_dir.glob("auth.json.*")), [])
                identity = pools.pools[provider].list()[0]
                self.assertEqual(json.loads(pools.pools[provider].get(identity)[0]["auth"]), login(provider, "remote"))

    def test_multiple_independent_local_logins_are_not_single_login_accounts(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider)
                accounts.write_private(backend.store_dir / "auth.json.second", login(provider, "second-local"))
                before = self.local_state(backend)
                with CallBudget(self, api=client, exact={"api.snapshot": 1}):
                    self.command(backend, client, "--migrate-from-local-to-remote")
                self.assertEqual(self.local_state(backend), before)
                self.assertEqual(pools.pools[provider].list(), [])

    def test_interrupted_single_login_transfer_keeps_latest_remote_tokens_on_retry(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider)
                with patch.object(accounts, "write_private", side_effect=OSError("interrupted")), \
                     self.assertRaises(rotate_auth.RotateError):
                    self.command(backend, client, "--migrate-from-local-to-remote")
                self.assertTrue((backend.store_dir / "auth.json.work").exists())
                self.assertFalse(backend.auth.is_symlink())
                pool = pools.pools[provider]
                identity = pool.list()[0]
                lease = pool.claim(identity, "worker")
                pool.save(identity, "worker", lease["lease"], login(provider, "local", 1), release=True)
                self.command(backend, client, "--migrate-from-local-to-remote")
                self.assertEqual(json.loads(pool.get(identity)[0]["auth"]), login(provider, "local", 1))
                self.assertEqual(list(backend.store_dir.glob("auth.json.*")), [])
                self.assertFalse((backend.store_dir / accounts.CLOUD_DIRECTORY).exists())

    def test_legacy_remote_names_do_not_occupy_local_names(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider, local=False)
                auth = login(provider, "remote")
                path = backend.store_dir / "auth.json.work"
                accounts.write_private(path, auth)
                identity = pools.pools[provider].enroll(auth)
                accounts.write_private(backend.store_dir / accounts.MANIFEST, {"work": {
                    "account_id": identity, "ownership": "cloud", "source_sha256": accounts.digest(path)}})
                self.command(backend, client, "--migrate-from-local-to-remote")
                for name, session in (("work", "local"), ("remote.work", "other-local")):
                    self.provider_login(backend, provider, session)
                    self.assertEqual(self.command(backend, client, "--local", "--save", name)[0], 0)
                self.assertEqual(json.loads(pools.pools[provider].get(identity)[0]["auth"]), auth)
                self.assertEqual(backend.local_profiles(), ["remote.work", "work"])
                self.assertEqual(backend.remote_profiles(), {"remote:work"})

    def test_verified_legacy_backup_is_removed_and_changed_backup_is_kept(self):
        for provider in ("codex", "cursor"):
            for changed in (False, True):
                with self.subTest(provider=provider, changed=changed):
                    backend, pools, client = self.fixture(provider, local=False)
                    source = backend.store_dir / "auth.json.work"
                    accounts.write_private(source, login(provider, "remote"))
                    identity = pools.pools[provider].enroll(login(provider, "remote"))
                    backup = backend.store_dir / "remote-staged/auth.json.work"
                    accounts.write_private(backup, login(provider, "changed" if changed else "remote"))
                    original = backup.read_bytes()
                    accounts.write_private(backend.store_dir / accounts.MANIFEST, {"work": {
                        "account_id": identity, "ownership": "cloud", "source_sha256": accounts.digest(source)}})
                    if changed:
                        with self.assertRaisesRegex(rotate_auth.RotateError, "archive changed"):
                            self.command(backend, client, "--migrate-from-local-to-remote")
                        self.assertEqual(backup.read_bytes(), original)
                        self.assertTrue((backend.store_dir / accounts.CLOUD_DIRECTORY / "work.json").exists())
                    else:
                        self.command(backend, client, "--migrate-from-local-to-remote")
                        self.assertFalse(backup.exists())
                        self.assertFalse(backup.parent.exists())
                        self.assertFalse((backend.store_dir / accounts.CLOUD_DIRECTORY).exists())
                    self.assertEqual(json.loads(pools.pools[provider].get(identity)[0]["auth"]), login(provider, "remote"))

    def test_help_advertises_save_without_add_or_fresh_login_flag(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, _, client = self.fixture(provider, local=False)
                code, output = self.command(backend, client, "--help")
                self.assertEqual(code, 0)
                self.assertIn("--save name", output)
                self.assertIn("--migrate-from-local-to-remote", output)
                self.assertIn("--migrate-from-remote-to-local", output)
                self.assertNotIn("--remote --migrate", output)
                self.assertNotIn("--add", output)
                self.assertNotIn("--fresh-login", output)

    def test_bad_scope_actions_and_unsafe_names_fail_before_signin_or_network(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, _, client = self.fixture(provider)
                before = self.local_state(backend)
                invalid = [("--remote",), ("--remote", "work"), ("--remote", "--unhook"),
                           ("--remote", "--kickoff"), ("--remote", "--save", "work"),
                           ("--remote", "--migrate"), ("--local", "--migrate"),
                           ("--migrate", "work"), ("--local", "--migrate-from-local-to-remote"),
                           ("--remote", "--migrate-from-remote-to-local"),
                           ("--local", "--remote", "--list"), ("--local", "--local", "--list"),
                           ("--add", ".."), ("--remote", "--add", "."),
                           ("--add", "work"), ("--add", "../outside"), ("--fresh-login", "--list")]
                with patch("subprocess.run", side_effect=AssertionError("signed in")), \
                     CallBudget(self, api=client):
                    for args in invalid:
                        with self.subTest(args=args), self.assertRaises(rotate_auth.RotateError):
                            self.command(backend, client, *args)
                self.assertEqual(self.local_state(backend), before)

    def test_remote_to_local_migration_restores_latest_tokens_and_is_repeatable(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider)
                with CallBudget(self, api=client, exact={"api.snapshot": 1, "api.enroll": 1,
                                                         "api.describe": 2, "api.get": 1}):
                    self.command(backend, client, "--migrate-from-local-to-remote")
                pool = pools.pools[provider]
                identity = pool.list()[0]
                lease = pool.claim(identity, "worker")
                pool.save(identity, "worker", lease["lease"], login(provider, "local", 1), release=True)
                with CallBudget(self, api=client, exact={"api.claim": 1, "api.delete": 1}):
                    code, output = self.command(backend, client, "--migrate-from-remote-to-local")
                self.assertEqual(code, 0)
                self.assertIn("moved 1 remote profiles to local", output)
                self.assertEqual(json.loads((backend.store_dir / "auth.json.work").read_text()),
                                 login(provider, "local", 1))
                self.assertEqual(pool.list(), [])
                self.assertEqual(accounts.load_remote_profiles(backend.store_dir, migrate=False), {})
                self.assertFalse(backend.current.is_symlink())
                self.assertFalse(backend.auth.is_symlink())
                with CallBudget(self, api=client):
                    self.command(backend, client, "--migrate-from-remote-to-local")
                self.assertEqual(json.loads((backend.store_dir / "auth.json.work").read_text()),
                                 login(provider, "local", 1))

    def test_interrupted_remote_to_local_migration_retries(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider)
                self.command(backend, client, "--migrate-from-local-to-remote")
                identity = pools.pools[provider].list()[0]
                client.delete.side_effect = OSError("interrupted")
                with self.assertRaisesRegex(rotate_auth.RotateError, "retry the same migration"):
                    self.command(backend, client, "--migrate-from-remote-to-local")
                self.assertEqual(pools.pools[provider].list(), [identity])
                self.assertTrue((backend.store_dir / "auth.json.work").is_file())
                client.delete.side_effect = None
                with CallBudget(self, api=client, exact={"api.claim": 1, "api.delete": 1}):
                    self.assertEqual(self.command(backend, client, "--migrate-from-remote-to-local")[0], 0)
                self.assertEqual(pools.pools[provider].list(), [])
                self.assertEqual(json.loads((backend.store_dir / "auth.json.work").read_text()),
                                 login(provider, "local"))

    def test_remote_to_local_migration_keeps_independent_local_and_refuses_copies(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider, kind="independent"):
                backend, pools, client = self.fixture(provider)
                self.save_remote_login(backend, client)
                before = self.local_state(backend)
                identity = pools.pools[provider].list()[0]
                with CallBudget(self, api=client):
                    code, output = self.command(backend, client, "--migrate-from-remote-to-local")
                self.assertEqual(code, 0)
                self.assertIn("moved 0 remote profiles to local", output)
                self.assertEqual(self.local_state(backend), before)
                self.assertEqual(pools.pools[provider].list(), [identity])

            with self.subTest(provider=provider, kind="same-account"):
                backend, pools, client = self.fixture(provider)
                self.command(backend, client, "--migrate-from-local-to-remote")
                accounts.write_private(backend.store_dir / "auth.json.copy", login(provider, "local", 1))
                identity = pools.pools[provider].list()[0]
                with CallBudget(self, api=client):
                    with self.assertRaisesRegex(rotate_auth.RotateError, "refusing to overwrite"):
                        self.command(backend, client, "--migrate-from-remote-to-local")
                self.assertEqual(pools.pools[provider].list(), [identity])
                self.assertFalse((backend.store_dir / "auth.json.work").exists())
                self.assertEqual(json.loads((backend.store_dir / "auth.json.copy").read_text()),
                                 login(provider, "local", 1))

            with self.subTest(provider=provider, kind="other-account"):
                backend, pools, client = self.fixture(provider)
                self.command(backend, client, "--migrate-from-local-to-remote")
                other = login(provider, "other", account="two")
                accounts.write_private(backend.store_dir / "auth.json.work", other)
                identity = pools.pools[provider].list()[0]
                with CallBudget(self, api=client):
                    with self.assertRaisesRegex(rotate_auth.RotateError, "overwrite"):
                        self.command(backend, client, "--migrate-from-remote-to-local")
                self.assertEqual(pools.pools[provider].list(), [identity])
                self.assertEqual(json.loads((backend.store_dir / "auth.json.work").read_text()), other)

    def test_remote_to_local_migration_refuses_a_busy_account(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider)
                self.command(backend, client, "--migrate-from-local-to-remote")
                identity = pools.pools[provider].list()[0]
                pools.pools[provider].claim(identity, "worker")
                with self.assertRaisesRegex(rotate_auth.RotateError, "busy"):
                    self.command(backend, client, "--migrate-from-remote-to-local")
                self.assertEqual(pools.pools[provider].list(), [identity])
                self.assertFalse((backend.store_dir / "auth.json.work").exists())

    def test_bare_migrate_prompts_only_in_an_interactive_terminal(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider):
                backend, pools, client = self.fixture(provider)
                before = self.local_state(backend)
                with patch.object(rotate_auth, "is_interactive", return_value=False), CallBudget(self, api=client):
                    with self.assertRaises(rotate_auth.RotateError) as ctx:
                        self.command(backend, client, "--migrate")
                self.assertIn("usage:", "\n".join(ctx.exception.lines))
                self.assertIn("--migrate-from-local-to-remote", "\n".join(ctx.exception.lines))
                self.assertEqual(self.local_state(backend), before)
                with patch.object(rotate_auth, "is_interactive", return_value=True), \
                     patch.object(rotate_auth, "prompt_migration", side_effect=KeyboardInterrupt), \
                     CallBudget(self, api=client):
                    with self.assertRaisesRegex(rotate_auth.RotateError, "migration cancelled"):
                        self.command(backend, client, "--migrate")
                self.assertEqual(self.local_state(backend), before)
                with patch.object(rotate_auth, "is_interactive", return_value=True), \
                     patch.object(rotate_auth, "prompt_migration", return_value="migrate-from-local-to-remote"), \
                     patch.object(rotate_auth, "prompt_profile_selection", return_value=["work"]) as picked, \
                     CallBudget(self, api=client, exact={"api.snapshot": 1, "api.enroll": 1,
                                                         "api.describe": 2, "api.get": 1}):
                    self.assertEqual(self.command(backend, client, "--migrate")[0], 0)
                self.assertEqual(picked.call_args.args[1], ["work"])
                self.assertEqual(list(backend.store_dir.glob("auth.json.*")), [])
                with patch.object(rotate_auth, "is_interactive", return_value=True), \
                     patch.object(rotate_auth, "prompt_migration", return_value="migrate-from-remote-to-local"), \
                     patch.object(rotate_auth, "prompt_profile_selection", return_value=["work"]) as picked, \
                     CallBudget(self, api=client, exact={"api.claim": 1, "api.delete": 1}):
                    code, output = self.command(backend, client, "--migrate")
                self.assertEqual(picked.call_args.args[1], ["work"])
                self.assertEqual(code, 0)
                self.assertIn("moved 1 remote profiles to local", output)
                self.assertEqual(json.loads((backend.store_dir / "auth.json.work").read_text()), login(provider, "local"))
                self.assertEqual(pools.pools[provider].list(), [])

    def test_interactive_migration_moves_only_checked_profiles(self):
        for provider in ("codex", "cursor"):
            with self.subTest(provider=provider, direction="local"):
                backend, pools, client = self.fixture(provider)
                accounts.write_private(backend.store_dir / "auth.json.alias", login(provider, "local"))
                with patch.object(rotate_auth, "is_interactive", return_value=True), \
                     patch.object(rotate_auth, "prompt_migration", return_value="migrate-from-local-to-remote"), \
                     patch.object(rotate_auth, "prompt_profile_selection", return_value=["alias"]), \
                     CallBudget(self, api=client, exact={"api.snapshot": 1, "api.enroll": 1,
                                                         "api.describe": 2, "api.get": 1}):
                    output = self.command(backend, client, "--migrate")[1]
                self.assertIn("moved 1 single-login profiles to remote", output)
                self.assertEqual(json.loads((backend.store_dir / "auth.json.work").read_text()), login(provider, "local"))
                self.assertFalse((backend.store_dir / "auth.json.alias").exists())
                self.assertEqual(set(accounts.load_remote_profiles(backend.store_dir, migrate=False)), {"remote:alias"})
                self.assertEqual(pools.pools[provider].list(), [account_identity(login(provider, "local"), provider)])

            with self.subTest(provider=provider, direction="remote"):
                backend, pools, client = self.fixture(provider)
                accounts.write_private(backend.store_dir / "auth.json.other", login(provider, "other", account="two"))
                self.command(backend, client, "--migrate-from-local-to-remote")
                kept = account_identity(login(provider, "other", account="two"), provider)
                with patch.object(rotate_auth, "is_interactive", return_value=True), \
                     patch.object(rotate_auth, "prompt_migration", return_value="migrate-from-remote-to-local"), \
                     patch.object(rotate_auth, "prompt_profile_selection", return_value=["work"]) as picked, \
                     CallBudget(self, api=client, exact={"api.claim": 1, "api.delete": 1}):
                    output = self.command(backend, client, "--migrate")[1]
                self.assertEqual(picked.call_args.args[1], ["other", "work"])
                self.assertIn("moved 1 remote profiles to local", output)
                self.assertEqual(json.loads((backend.store_dir / "auth.json.work").read_text()), login(provider, "local"))
                self.assertFalse((backend.store_dir / "auth.json.other").exists())
                self.assertEqual(pools.pools[provider].list(), [kept])
                self.assertEqual(set(accounts.load_remote_profiles(backend.store_dir, migrate=False)), {"remote:other"})

    def test_interactive_migration_cancel_and_empty_selection_change_nothing(self):
        for provider in ("codex", "cursor"):
            for selection in (KeyboardInterrupt, []):
                with self.subTest(provider=provider, selection=selection):
                    backend, pools, client = self.fixture(provider)
                    before = self.local_state(backend)
                    prompt = patch.object(rotate_auth, "prompt_profile_selection",
                                          side_effect=selection if selection is KeyboardInterrupt else None,
                                          return_value=selection if selection == [] else None)
                    with patch.object(rotate_auth, "is_interactive", return_value=True), \
                         patch.object(rotate_auth, "prompt_migration", return_value="migrate-from-local-to-remote"), \
                         prompt, self.assertRaisesRegex(rotate_auth.RotateError, "migration cancelled"):
                        self.command(backend, client, "--migrate")
                    self.assertEqual(self.local_state(backend), before)
                    self.assertEqual(pools.pools[provider].list(), [])

    def test_other_agents_reject_migration_flags(self):
        with self.assertRaisesRegex(rotate_auth.RotateError, "Codex and Cursor"):
            rotate_auth.run(["claude", "--migrate-from-local-to-remote"])


if __name__ == "__main__":
    unittest.main()
