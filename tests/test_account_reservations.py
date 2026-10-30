"""Local Codex/Cursor reservation parity and recovery; no real credentials or Azure."""

import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure"), str(ROOT / "scripts")]
import accounts
import rotate_auth
from worker_core import Accounts, WorkerError, account_identity
from test_cursor_accounts import auth as cursor_auth
from test_remote_worker import auth as codex_auth, MemoryTable


class AccountReservationTests(unittest.TestCase):
    PROVIDERS = ("codex", "cursor")

    def login(self, provider, name="reserved", token="original"):
        if provider == "cursor":
            return cursor_auth(name, refresh=token)
        return codex_auth(name, token=token)

    def backend(self, directory, provider):
        root = Path(directory)
        backend = rotate_auth.SharedStoreBackend(
            command="rotate-codex" if provider == "codex" else "rotate-cursor-cli",
            label=provider, agent_dir_env="RESERVATION_TEST_AGENT_DIR",
            store_dir_env="RESERVATION_TEST_STORE_DIR", default_agent=root / "agent",
            default_store=root / "store")
        backend.store_dir.mkdir()
        backend.agent_dir.mkdir()
        for name in ("local-a", "21kb60", "local-b"):
            accounts.write_private(backend.store_dir / f"auth.json.{name}", self.login(provider, name))
        backend.current.symlink_to("auth.json.21kb60")
        backend.auth.symlink_to(os.path.relpath(backend.current, backend.agent_dir))
        return backend

    def command(self, action, provider, store, table=None, *names):
        output = io.StringIO()
        argv = ["accounts.py", action, "--provider", provider, "--store", str(store)]
        for name in names:
            argv.extend(["--profile", name])
        with patch.object(accounts, "connect", return_value=table) as connect, \
             patch.object(sys, "argv", argv), redirect_stdout(output):
            try:
                accounts.main()
            finally:
                if action == "list":
                    connect.assert_not_called()
                else:
                    connect.assert_called_once_with(provider)
        return output.getvalue()

    def test_enrollment_preserves_files_links_and_refreshed_cloud_logins_for_both_providers(self):
        for provider in self.PROVIDERS:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                backend = self.backend(directory, provider)
                store = backend.store_dir
                active = store / "auth.json.21kb60"
                (store / "auth.json.alias").write_bytes(active.read_bytes())
                originals = {path.name: path.read_bytes() for path in store.glob("auth.json.*")}
                links = (os.readlink(backend.current), os.readlink(backend.auth))
                table = MemoryTable()
                self.command("enroll", provider, store, table)
                manifest = json.loads((store / accounts.MANIFEST).read_text())
                self.assertEqual(set(manifest), {"21kb60", "alias", "local-a", "local-b"})
                self.assertEqual(len(table.rows), 3)
                self.assertEqual(manifest["21kb60"]["account_id"], manifest["alias"]["account_id"])
                self.assertTrue(all(set(record) == {"account_id", "source_sha256"}
                                    for record in manifest.values()))
                self.assertEqual({p.name: p.read_bytes() for p in store.glob("auth.json.*")}, originals)
                self.assertEqual((os.readlink(backend.current), os.readlink(backend.auth)), links)
                self.assertFalse((store / "remote-staged").exists())
                pool = Accounts(table, provider=provider)
                identity = manifest["21kb60"]["account_id"]
                lease = pool.claim(identity, "worker")
                latest = self.login(provider, "21kb60", "latest-cloud-login")
                pool.save(identity, "worker", lease["lease"], latest, release=True)
                cloud = copy.deepcopy(table.rows)
                self.command("enroll", provider, store, table, "21kb60")
                self.assertEqual(table.rows, cloud)
                self.assertEqual(active.read_bytes(), originals[active.name])

    def test_visibility_selection_rotation_and_kickoff_match_for_both_providers(self):
        for provider in self.PROVIDERS:
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as directory:
                backend = self.backend(directory, provider)
                profile = backend.store_dir / "auth.json.21kb60"
                reservation = {"21kb60": {"account_id": account_identity(self.login(provider, "21kb60"), provider),
                                           "source_sha256": accounts.digest(profile)}}
                accounts.write_private(backend.store_dir / accounts.MANIFEST, reservation)
                output = io.StringIO()
                with redirect_stdout(output), patch.object(rotate_auth, "ping_agent") as ping:
                    backend.list_profiles()
                    self.assertIn("* 21kb60", output.getvalue())
                    self.assertIn("1 profiles reserved for remote work", output.getvalue())
                    for operation in (lambda: backend.select("21kb60"), lambda: backend.save("21kb60"),
                                      lambda: backend.delete("21kb60"), lambda: backend.kickoff(["21kb60"])):
                        with self.assertRaisesRegex(rotate_auth.RotateError, "reserved for remote work") as error:
                            operation()
                        self.assertIn(f"--provider {provider}", str(error.exception))
                        self.assertEqual(backend.current_suffix(), "21kb60")
                    ping.assert_not_called()
                    for expected in ("local-a", "local-b", "local-a"):
                        backend.select(None)
                        self.assertEqual(backend.current_suffix(), expected)
                    backend.current.unlink()
                    backend.current.symlink_to("auth.json.21kb60")
                    visited = []
                    ping.side_effect = lambda _: (visited.append(backend.current_suffix()) or True, "ok")
                    self.assertEqual(backend.kickoff(), 0)
                    self.assertEqual(visited, ["local-a", "local-b"])
                    self.assertEqual(backend.current_suffix(), "21kb60")
                    self.assertEqual(json.loads((backend.store_dir / accounts.MANIFEST).read_text()), reservation)

    def staged_handoff(self, backend, provider, key):
        profile = backend.store_dir / "auth.json.21kb60"
        backup = backend.store_dir / "remote-staged/auth.json.21kb60"
        backup.parent.mkdir()
        profile.rename(backup)
        record = {"account_id": account_identity(self.login(provider, "21kb60"), provider),
                  "source_sha256": accounts.digest(backup), key: str(backup.relative_to(backend.store_dir)),
                  "state": "staged", "staged_for_issue": "KIAN-922", "staged_for_issue_id": "synthetic-issue"}
        accounts.write_private(backend.store_dir / accounts.MANIFEST, {"21kb60": record})
        # The existing active profile can be unrelated to the staged reservation.
        backend.current.unlink()
        backend.current.symlink_to("auth.json.local-a")
        return profile, backup, record

    def test_listing_repairs_either_legacy_backup_key_without_accessing_cloud(self):
        for provider in self.PROVIDERS:
            for key in ("local_backup", "backup_path"):
                with self.subTest(provider=provider, key=key), tempfile.TemporaryDirectory() as directory:
                    backend = self.backend(directory, provider)
                    profile, backup, record = self.staged_handoff(backend, provider, key)
                    links = (os.readlink(backend.current), os.readlink(backend.auth))
                    self.assertIn("21kb60:", self.command("list", provider, backend.store_dir))
                    self.assertEqual(profile.read_bytes(), backup.read_bytes())
                    self.assertEqual(profile.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(json.loads((backend.store_dir / accounts.MANIFEST).read_text()),
                                     {"21kb60": {k: record[k] for k in ("account_id", "source_sha256")}})
                    with redirect_stdout(io.StringIO()) as output:
                        backend.list_profiles()
                    self.assertIn("21kb60", output.getvalue())
                    self.assertIn("1 profiles reserved for remote work", output.getvalue())
                    self.assertEqual((os.readlink(backend.current), os.readlink(backend.auth)), links)
                    backup.unlink()  # Neither normal enrollment nor reclaim depends on the backup now.
                    table = MemoryTable()
                    pool = Accounts(table, provider=provider)
                    latest = self.login(provider, "21kb60", "latest-cloud-login")
                    identity = pool.enroll(latest)
                    row, etag = table.get(identity)
                    row["state"] = "staged"
                    table.replace(row, etag)
                    cloud = copy.deepcopy(table.rows)
                    self.command("enroll", provider, backend.store_dir, table, "21kb60")
                    self.assertEqual(table.rows, cloud)
                    table.delete = lambda identity, etag: table.rows.pop(identity)
                    self.command("reclaim", provider, backend.store_dir, table, "21kb60")
                    self.assertEqual(json.loads(profile.read_text()), latest)
                    self.assertEqual(json.loads((backend.store_dir / accounts.MANIFEST).read_text()), {})
                    self.assertEqual(table.rows, {})
                    self.assertEqual((os.readlink(backend.current), os.readlink(backend.auth)), links)

    def test_normal_reclaim_refuses_busy_accounts_or_changed_credentials_for_both_providers(self):
        for provider in self.PROVIDERS:
            for conflict in ("busy", "changed"):
                with self.subTest(provider=provider, conflict=conflict), tempfile.TemporaryDirectory() as directory:
                    backend = self.backend(directory, provider)
                    table = MemoryTable()
                    self.command("enroll", provider, backend.store_dir, table, "21kb60")
                    if conflict == "busy":
                        identity = account_identity(self.login(provider, "21kb60"), provider)
                        Accounts(table, provider=provider).claim(identity, "worker")
                    else:
                        accounts.write_private(backend.store_dir / "auth.json.21kb60",
                                               self.login(provider, "21kb60", "new-local-login"))
                    cloud = copy.deepcopy(table.rows)
                    with self.assertRaises(WorkerError):
                        self.command("reclaim", provider, backend.store_dir, table, "21kb60")
                    self.assertEqual(table.rows, cloud)
                    self.assertEqual(backend.current_suffix(), "21kb60")
                    self.assertEqual(backend.remote_profiles(), {"21kb60"})

    def test_legacy_repair_refuses_changed_or_escaped_files_and_keeps_reservation(self):
        for conflict in ("backup", "destination", "outside", "symlink"):
            with self.subTest(conflict=conflict), tempfile.TemporaryDirectory() as directory:
                backend = self.backend(directory, "cursor")
                profile, backup, record = self.staged_handoff(backend, "cursor", "local_backup")
                if conflict == "backup":
                    backup.write_text("changed")
                elif conflict == "destination":
                    profile.write_text("independent-login")
                elif conflict == "symlink":
                    profile.symlink_to(backup)
                else:
                    outside = Path(directory) / "outside"
                    outside.write_bytes(backup.read_bytes())
                    record["local_backup"] = "../outside"
                    accounts.write_private(backend.store_dir / accounts.MANIFEST, {"21kb60": record})
                manifest = (backend.store_dir / accounts.MANIFEST).read_bytes()
                with self.assertRaises(WorkerError):
                    self.command("list", "cursor", backend.store_dir)
                self.assertEqual((backend.store_dir / accounts.MANIFEST).read_bytes(), manifest)
                self.assertEqual(backend.current_suffix(), "local-a")
                if conflict == "destination":
                    self.assertEqual(profile.read_text(), "independent-login")
                with self.assertRaises(rotate_auth.RotateError):
                    backend.select("21kb60")

    def test_interrupted_manifest_repair_can_retry_without_the_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self.backend(directory, "cursor")
            profile, backup, record = self.staged_handoff(backend, "cursor", "local_backup")
            with patch.object(rotate_auth, "atomic_write_text", side_effect=OSError("interrupted")), \
                 self.assertRaises(rotate_auth.RotateError):
                backend.list_profiles()
            self.assertEqual(profile.read_bytes(), backup.read_bytes())
            self.assertEqual(json.loads((backend.store_dir / accounts.MANIFEST).read_text()), {"21kb60": record})
            backup.unlink()
            self.assertEqual(backend.remote_profiles(), {"21kb60"})
            self.assertEqual(backend.current_suffix(), "local-a")
            self.assertNotIn("local_backup", json.loads((backend.store_dir / accounts.MANIFEST).read_text())["21kb60"])


if __name__ == "__main__":
    unittest.main()
