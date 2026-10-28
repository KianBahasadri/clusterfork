"""Worker trust boundaries, credential fencing, and real Git publication behavior."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
import worker
from codex_runtime import CodexError, quota_snapshot
from dispatch_core import Registry
from quota_selection import select_account
from worker_core import Accounts, WorkerError, WorkerService, authorize_claims

PROJECT = "374f597c-89f5-4e40-9798-07533f57bfba"
ISSUE = "66547614-8ed4-4928-834e-aac76f01afd2"
KEY = "a" * 64
NOW = 1_800_000_000


class MemoryTable:
    def __init__(self):
        self.rows = {}

    def create(self, row):
        if row["RowKey"] in self.rows:
            return False
        self.rows[row["RowKey"]] = (copy.deepcopy(row), 1)
        return True

    def get(self, key):
        if key not in self.rows:
            raise WorkerError("missing", 404)
        row, version = self.rows[key]
        return copy.deepcopy(row), version

    def list(self):
        return [copy.deepcopy(row) for row, _ in self.rows.values()]

    def replace(self, row, etag):
        if self.rows[row["RowKey"]][1] != etag:
            raise WorkerError("concurrent modification")
        self.rows[row["RowKey"]] = (copy.deepcopy(row), etag + 1)


def auth(identity="account-a", token="access-a"):
    return {"auth_mode": "chatgpt", "tokens": {"account_id": identity, "access_token": token,
                                               "refresh_token": "refresh-a"}}


def claims():
    return {"repository": "KianBahasadri/clusterfork", "repository_id": "12",
            "ref": "refs/heads/main", "event_name": "workflow_dispatch",
            "runner_environment": "github-hosted", "run_id": "123", "run_attempt": "1",
            "sha": "b" * 40,
            "workflow_ref": "KianBahasadri/clusterfork/.github/workflows/clusterfork-worker.yml@refs/heads/main"}


def receipt():
    return {"RowKey": KEY, "project_id": PROJECT, "repository": "KianBahasadri/clusterfork",
            "issue_id": ISSUE, "identifier": "KIAN-891", "status": "dispatched"}


class AccountLeaseTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.table = MemoryTable()
        self.accounts = Accounts(self.table, clock=lambda: self.now)
        self.identity = self.accounts.enroll(auth())

    def test_aliases_cannot_enroll_the_same_provider_account_twice(self):
        with self.assertRaises(WorkerError):
            self.accounts.enroll(auth(token="another-access-token"))
        self.assertEqual(self.accounts.list(), [self.identity])

    def test_lease_prevents_a_second_worker_using_the_same_account(self):
        self.accounts.claim(self.identity, "first")
        with self.assertRaises(WorkerError):
            self.accounts.claim(self.identity, "second")

    def test_expired_writer_cannot_overwrite_newly_refreshed_credentials(self):
        old = self.accounts.claim(self.identity, "first")
        self.now += 121
        fresh = self.accounts.claim(self.identity, "second")
        self.accounts.save(self.identity, "second", fresh["lease"], auth(token="new-access"), release=True)
        with self.assertRaises(WorkerError):
            self.accounts.save(self.identity, "first", old["lease"], auth(token="stale-access"))
        self.assertEqual(self.accounts.claim(self.identity, "third")["auth"]["tokens"]["access_token"], "new-access")

    def test_refreshed_credentials_must_keep_the_same_account_identity(self):
        leased = self.accounts.claim(self.identity, "worker")
        with self.assertRaises(WorkerError):
            self.accounts.save(self.identity, "worker", leased["lease"], auth("different-account"))

    def test_heartbeat_extends_the_lease_and_release_keeps_refreshed_tokens(self):
        leased = self.accounts.claim(self.identity, "worker")
        self.now += 100
        self.accounts.save(self.identity, "worker", leased["lease"], auth(token="refreshed"))
        self.now += 100
        self.accounts.save(self.identity, "worker", leased["lease"], release=True)
        self.assertEqual(self.accounts.claim(self.identity, "next")["auth"]["tokens"]["access_token"], "refreshed")

    def test_compare_and_swap_rejects_lost_updates(self):
        row, version = self.table.get(self.identity)
        other, other_version = self.table.get(self.identity)
        other["owner"] = "new-owner"
        self.table.replace(other, other_version)
        with self.assertRaises(WorkerError):
            self.table.replace(row, version)


class WorkerAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry.load(ROOT / "automation/azure/projects.json")

    def test_only_the_enrolled_main_branch_worker_can_get_credentials(self):
        self.assertEqual(authorize_claims(claims(), self.registry, receipt()).repository,
                         "KianBahasadri/clusterfork")
        for field, value in [("repository", "attacker/clusterfork"), ("ref", "refs/heads/other"),
                             ("event_name", "pull_request"), ("runner_environment", "self-hosted"),
                             ("workflow_ref", "KianBahasadri/clusterfork/.github/workflows/test.yml@refs/heads/main"),
                             ("job_workflow_ref", "attacker/repo/.github/workflows/job.yml@refs/heads/main"),
                             ("run_id", "../other"), ("sha", "untrusted")]:
            bad = {**claims(), field: value}
            with self.subTest(field=field), self.assertRaises(WorkerError):
                authorize_claims(bad, self.registry, receipt())

    def test_unmapped_receipt_cannot_authorize_a_job(self):
        with self.assertRaises(WorkerError):
            authorize_claims(claims(), self.registry, {**receipt(), "repository": "other/repo"})

    def service(self):
        receipts, runs = MemoryTable(), MemoryTable()
        receipts.create(receipt())
        issue = {"id": ISSUE, "identifier": "KIAN-891", "title": "Test", "description": "",
                 "url": "https://linear.app/test", "archivedAt": None,
                 "project": {"id": PROJECT, "archivedAt": None, "labels": {"nodes": [{"name": "symphony"}]}},
                 "state": {"id": "ace01594-2daf-44bb-9690-cb0dccc11795"}}
        class Linear:
            def issue(self, identifier):
                return copy.deepcopy(issue)
        class GitHub:
            permissions = None
            def verify_worker_run(self, *args):
                return None
            def installation_token(self, repository, *, permissions):
                self.permissions = permissions
                return "temporary-token"
        github = GitHub()
        service = WorkerService(self.registry, receipts, runs, Accounts(MemoryTable()), Linear(), github,
                                clock=lambda: NOW)
        return service, github, issue

    def test_publication_token_has_only_contents_and_pull_requests_permissions(self):
        service, github, _ = self.service()
        config = service.handle("start", claims(), {"dispatch_key": KEY})
        self.assertEqual((config["model"], config["effort"]), ("gpt-6.1-sol", "max"))
        service.handle("github", claims(), {"dispatch_key": KEY})
        self.assertEqual(github.permissions, {"contents": "write", "pull_requests": "write"})

    def test_removing_project_enrollment_prevents_publication(self):
        service, _, issue = self.service()
        service.handle("start", claims(), {"dispatch_key": KEY})
        issue["project"]["labels"]["nodes"] = []
        with self.assertRaises(WorkerError):
            service.handle("github", claims(), {"dispatch_key": KEY})

    def test_competing_run_cannot_take_an_active_worker_lease(self):
        service, _, _ = self.service()
        service.handle("start", claims(), {"dispatch_key": KEY})
        with self.assertRaises(WorkerError):
            service.handle("start", {**claims(), "run_id": "456"}, {"dispatch_key": KEY})

    def test_completed_issues_cannot_be_executed_again(self):
        service, _, _ = self.service()
        service.handle("start", claims(), {"dispatch_key": KEY})
        service.handle("finish", claims(), {"dispatch_key": KEY, "status": "completed"})
        with self.assertRaises(WorkerError):
            service.handle("start", {**claims(), "run_attempt": "2"}, {"dispatch_key": KEY})

    def test_depleted_accounts_schedule_reconciliation_retry(self):
        service, _, _ = self.service()
        service.handle("start", claims(), {"dispatch_key": KEY})
        service.handle("finish", claims(), {"dispatch_key": KEY, "status": "waiting_for_quota"})
        self.assertEqual(service.receipts.get(KEY)[0]["retry_after"], NOW + 900)


class LiveQuotaShapeTests(unittest.TestCase):
    def test_short_and_weekly_limits_both_constrain_headroom(self):
        config = Registry.load(ROOT / "automation/azure/projects.json")
        snapshot = quota_snapshot("account", "gpt-6.1-sol", {"rateLimitsByLimitId": {"codex": {
            "primary": {"usedPercent": 1, "resetsAt": time.time() + 1000},
            "secondary": {"usedPercent": 95, "resetsAt": time.time() + 10000}}}})
        self.assertIsNone(select_account([snapshot], config.agent_profiles, config.agent_selection, now=time.time()))

    def test_unrelated_model_limits_are_not_treated_as_available_sol_quota(self):
        with self.assertRaises(CodexError):
            quota_snapshot("account", "gpt-6.1-sol", {"rateLimitsByLimitId": {
                "unrelated": {"normalModelSlug": "other-model", "primary": {"usedPercent": 0}}}})

    def test_denied_usage_and_unknown_windows_fail_closed(self):
        config = Registry.load(ROOT / "automation/azure/projects.json")
        with self.assertRaises(CodexError):
            quota_snapshot("a", "gpt-6.1-sol", {"ordinaryUsageAllowed": False})
        snapshot = quota_snapshot("a", "gpt-6.1-sol", {"rateLimits": {"primary": {"resetsAt": time.time() + 60}}})
        self.assertIsNone(select_account([snapshot], config.agent_profiles, config.agent_selection, now=time.time()))


class LocalRemoteOwnershipTests(unittest.TestCase):
    def backend(self, directory):
        spec = importlib.util.spec_from_file_location("worker_rotate_auth", ROOT / "scripts/rotate_auth.py")
        rotate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rotate)
        backend = rotate.SharedStoreBackend(
            command="rotate-codex", label="Codex", agent_dir_env="WORKER_TEST_AGENT_DIR",
            store_dir_env="WORKER_TEST_AUTH_DIR", default_agent=Path(directory) / "agent",
            default_store=Path(directory) / "store")
        backend.store_dir.mkdir()
        for name in ("local-a", "remote", "local-b"):
            (backend.store_dir / f"auth.json.{name}").write_text("{}")
        (backend.store_dir / "remote-profiles.json").write_text(json.dumps({"remote": {"account_id": "test"}}))
        return backend, rotate

    def test_local_rotation_skips_cloud_reserved_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            backend, _ = self.backend(directory)
            backend.select(None)
            self.assertEqual(backend.current_suffix(), "local-a")
            backend.select(None)
            self.assertEqual(backend.current_suffix(), "local-b")

    def test_reserved_profiles_cannot_be_selected_overwritten_deleted_or_pinged(self):
        with tempfile.TemporaryDirectory() as directory:
            backend, rotate = self.backend(directory)
            for operation in [lambda: backend.select("remote"), lambda: backend.save("remote"),
                              lambda: backend.delete("remote"), lambda: backend.kickoff(["remote"])]:
                with self.subTest(operation=operation), self.assertRaises(rotate.RotateError):
                    operation()

    def test_corrupt_ownership_information_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            backend, rotate = self.backend(directory)
            (backend.store_dir / "remote-profiles.json").write_text("not json")
            with self.assertRaises(rotate.RotateError):
                backend.select(None)

class GitPublicationTests(unittest.TestCase):
    def test_worker_pushes_a_separate_branch_and_opens_a_pr_after_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, checkout = root / "remote.git", root / "checkout"
            subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "clone", str(remote), str(checkout)], check=True, capture_output=True)
            (checkout / "file.txt").write_text("original\n")
            subprocess.run(["git", "-C", str(checkout), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(checkout), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "commit", "-m", "initial"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(checkout), "push", "origin", "main"], check=True, capture_output=True)
            main_before = subprocess.check_output(["git", "--git-dir", str(remote), "rev-parse", "main"], text=True)
            class Broker:
                key = KEY
                def call(self, action):
                    return {"token": "temporary-test-token"}
            class Heartbeat:
                failed = threading.Event()
            config = {"repository": "owner/repo", "ref": "main", "issue": {
                "id": ISSUE, "identifier": "KIAN-891", "title": "Improve file", "url": "https://linear.app/test"},
                "validation_commands": [[sys.executable, "-c", "from pathlib import Path; assert Path('file.txt').read_text() == 'updated\\n'"]]}
            def implement(*args):
                (checkout / "file.txt").write_text("updated\n")
                return "Updated the file and validated it."
            old = Path.cwd()
            try:
                os.chdir(checkout)
                with patch.object(worker, "run_codex", side_effect=implement), \
                     patch.object(worker, "find_pr", return_value=None), \
                     patch.object(worker, "json_request", return_value={"html_url": "https://github.com/owner/repo/pull/1"}) as request:
                    outcome, pull = worker.execute(Broker(), config, Heartbeat(), root)
                self.assertEqual(outcome, "completed")
                self.assertEqual(pull, "https://github.com/owner/repo/pull/1")
                self.assertEqual(subprocess.check_output(["git", "--git-dir", str(remote), "rev-parse", "main"], text=True), main_before)
                self.assertEqual(request.call_args.args[2]["head"], "clusterfork/kian-891-aaaaaaaa")
                self.assertIn("clusterfork-dispatch:" + KEY, request.call_args.args[2]["body"])
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
