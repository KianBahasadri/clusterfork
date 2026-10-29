"""Worker trust boundaries, credential fencing, and real Git publication behavior."""

import copy
from collections import defaultdict
from contextlib import contextmanager, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
import worker
import accounts as account_cli
from codex_runtime import CodexError, quota_snapshot
from dispatch_core import Registry
from quota_selection import select_account
from worker_core import Accounts, WorkerError, WorkerService, authorize_claims
from repository_queue import RepositoryQueue
from readiness_policy import POLICY, PROTOCOL, RESULT_SCHEMA, REVIEW_TIMEOUT_SECONDS, digest

PROJECT = "374f597c-89f5-4e40-9798-07533f57bfba"
ISSUE = "66547614-8ed4-4928-834e-aac76f01afd2"
KEY = "a" * 64
NOW = 1_800_000_000


class MemoryTable:
    def __init__(self):
        self.rows = {}
        self.lock = threading.RLock()

    def create(self, row):
        with self.lock:
            if row["RowKey"] in self.rows:
                return False
            self.rows[row["RowKey"]] = (copy.deepcopy(row), 1)
            return True

    def get(self, key):
        with self.lock:
            if key not in self.rows:
                raise WorkerError("missing", 404)
            row, version = self.rows[key]
            return copy.deepcopy(row), version

    def list(self):
        with self.lock:
            return [copy.deepcopy(row) for row, _ in self.rows.values()]

    def replace(self, row, etag):
        with self.lock:
            if self.rows[row["RowKey"]][1] != etag:
                raise WorkerError("concurrent modification")
            self.rows[row["RowKey"]] = (copy.deepcopy(row), etag + 1)

    def transaction(self, changes):
        with self.lock:
            for row, etag in changes:
                current = self.rows.get(row["RowKey"])
                if (etag is None and current is not None) or (etag is not None and (not current or current[1] != etag)):
                    raise WorkerError("concurrent modification")
            for row, etag in changes:
                self.rows[row["RowKey"]] = (copy.deepcopy(row), 1 if etag is None else etag + 1)


class ApprovedReadiness:
    """Isolate pre-existing queue/lease tests; readiness tests use the real gate."""
    def ensure(self, issue, route):
        return {"status": "ready"}

    def require(self, issue, route):
        return {}


def repository_queue(registry, receipts, runs, linear, github, clock=lambda: NOW):
    tables = defaultdict(MemoryTable)
    return RepositoryQueue(registry, receipts, runs, tables.__getitem__, linear, github,
                           ApprovedReadiness(), clock=clock)


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


class WorkerAdmissionTests(unittest.TestCase):
    def run_worker(self, config):
        broker = Mock()
        broker.call.return_value = config
        with patch.dict(os.environ, {"CLUSTERFORK_BROKER_URL": "https://broker.example/worker",
                                     "DISPATCH_KEY": KEY}, clear=True), \
             patch.object(worker, "Broker", return_value=broker), \
             patch.object(worker, "execute", return_value=("completed", None)) as execute, \
             patch("sys.stdout", io.StringIO()), patch("sys.stderr", io.StringIO()):
            result = worker.main()
        return result, broker, execute

    def test_old_or_invalid_broker_cannot_start_agent_execution(self):
        for value in (None, False, "true", 1):
            with self.subTest(value=value):
                config = {} if value is None else {"serial_admission": value}
                result, broker, execute = self.run_worker(config)
                self.assertEqual(result, 1)
                execute.assert_not_called()
                self.assertEqual(broker.call.call_args_list[0].args, ("start",))
                self.assertEqual(broker.call.call_args_list[0].kwargs, {"protocol": worker.PROTOCOL})
                broker.call.assert_called_with("finish", status="failed")
                self.assertEqual(broker.call.call_count, 2)

    def test_confirmed_serial_admission_allows_normal_execution(self):
        result, broker, execute = self.run_worker({"serial_admission": True,
                                                "protocol": worker.PROTOCOL, "phase": "development"})
        self.assertEqual(result, 0)
        execute.assert_called_once()
        broker.call.assert_called_with("finish", status="completed", pull_request="")

    def test_worker_rejects_broker_predating_selection_reporting(self):
        previous = "readiness-v1:" + digest([POLICY, RESULT_SCHEMA, REVIEW_TIMEOUT_SECONDS])
        result, broker, execute = self.run_worker({"serial_admission": True,
                                                  "protocol": previous, "phase": "development"})
        self.assertEqual(result, 1)
        execute.assert_not_called()
        broker.call.assert_called_with("finish", status="failed")


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

    def test_revoked_login_releases_lease_without_masking_authentication_failure(self):
        class Broker:
            released = False
            def call(self, action, **data):
                if action == "claim":
                    return {"auth": auth(), "lease": "test-lease"}
                self.released = data["release"] and data["auth"] is None
        broker = Broker()
        with self.assertRaises(CodexError):
            with worker.account_lease(broker, "account") as (home, heartbeat):
                (home / "auth.json").unlink()
                raise CodexError("login revoked")
        self.assertTrue(broker.released)


class AccountEnrollmentTests(unittest.TestCase):
    def enroll(self, store, table, *args):
        output = io.StringIO()
        with patch.object(account_cli, "connect", return_value=table), \
             patch.object(sys, "argv", ["accounts.py", "enroll", "--store", str(store), *args]), \
             redirect_stdout(output):
            account_cli.main()
        return output.getvalue()

    def test_default_enrollment_includes_active_profile_and_deduplicates_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            active = store / "auth.json.active"
            active.write_text(json.dumps(auth("active-account")))
            (store / "auth.json.alias").write_bytes(active.read_bytes())
            (store / "auth.json.other").write_text(json.dumps(auth("other-account")))
            (store / "current").symlink_to(active.name)
            original = active.read_bytes()
            table = MemoryTable()
            output = self.enroll(store, table)
            manifest = json.loads((store / account_cli.MANIFEST).read_text())
            self.assertEqual(set(manifest), {"active", "alias", "other"})
            self.assertEqual(len(table.rows), 2)
            self.assertEqual(manifest["active"]["account_id"], manifest["alias"]["account_id"])
            self.assertEqual((store / "current").resolve(), active)
            self.assertEqual(active.read_bytes(), original)
            self.assertIn("includes the active local account", output)

    def test_explicit_active_enrollment_is_idempotent_and_retains_refreshed_cloud_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            active = store / "auth.json.active"
            active.write_text(json.dumps(auth("active-account")))
            (store / "current").symlink_to(active.name)
            table = MemoryTable()
            self.enroll(store, table, "--profile", "active")
            pool = Accounts(table)
            identity = pool.list()[0]
            leased = pool.claim(identity, "worker")
            pool.save(identity, "worker", leased["lease"], auth("active-account", "cloud-refreshed"), release=True)
            self.enroll(store, table, "--profile", "active")
            self.assertEqual(len(pool.list()), 1)
            self.assertEqual(pool.claim(identity, "check")["auth"]["tokens"]["access_token"], "cloud-refreshed")


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
                 "project": {"id": PROJECT, "archivedAt": None, "labels": {"nodes": [{"name": "clusterfork-symphony"}]}},
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
            def worker_result(self, *args):
                return {"status": "in_progress"}
        github = GitHub()
        service = WorkerService(self.registry, receipts, runs, Accounts(MemoryTable()), Linear(), github,
                                repository_queue(self.registry, receipts, runs, Linear(), github),
                                clock=lambda: NOW)
        return service, github, issue

    def test_publication_token_has_only_contents_and_pull_requests_permissions(self):
        service, github, _ = self.service()
        config = service.handle("start", claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
        self.assertEqual((config["model"], config["effort"]), ("gpt-6.1-sol", "max"))
        self.assertTrue(config["auto_merge"])
        self.assertEqual(config["agent_timeout_minutes"], 300)
        self.assertIs(config["serial_admission"], True)
        service.handle("github", claims(), {"dispatch_key": KEY})
        self.assertEqual(github.permissions, {"contents": "write", "pull_requests": "write"})

    def test_removing_project_enrollment_prevents_publication(self):
        service, _, issue = self.service()
        service.handle("start", claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
        issue["project"]["labels"]["nodes"] = []
        with self.assertRaises(WorkerError):
            service.handle("github", claims(), {"dispatch_key": KEY})

    def test_competing_run_cannot_take_an_active_worker_lease(self):
        service, _, _ = self.service()
        service.handle("start", claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
        with self.assertRaises(WorkerError):
            service.handle("start", {**claims(), "run_id": "456"}, {"dispatch_key": KEY, "protocol": PROTOCOL})

    def test_completed_issues_cannot_be_executed_again(self):
        service, _, _ = self.service()
        service.handle("start", claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
        service.handle("finish", claims(), {"dispatch_key": KEY, "status": "completed"})
        with self.assertRaises(WorkerError):
            service.handle("start", {**claims(), "run_attempt": "2"}, {"dispatch_key": KEY, "protocol": PROTOCOL})

    def test_depleted_accounts_schedule_reconciliation_retry(self):
        service, _, _ = self.service()
        service.handle("start", claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
        service.handle("finish", claims(), {"dispatch_key": KEY, "status": "waiting_for_quota"})
        self.assertEqual(service.receipts.get(KEY)[0]["retry_after"], NOW + 900)


class LiveQuotaShapeTests(unittest.TestCase):
    def test_codex_execution_uses_project_timeout_and_defaults_to_five_hours(self):
        selection = SimpleNamespace(account_id="a" * 64, model="gpt-6.1-sol", effort="max", remaining_percent=100)
        heartbeat = SimpleNamespace(failed=threading.Event())
        for override, expected in [({}, 18_000), ({"agent_timeout_minutes": 17}, 1020)]:
            with self.subTest(override=override), tempfile.TemporaryDirectory() as directory:
                lease = MagicMock()
                lease.__enter__.return_value = (Path(directory), heartbeat)
                config = {"model": selection.model, "effort": selection.effort,
                          "issue": {"identifier": "KIAN-891", "title": "Test", "url": "https://linear.app/test"}, **override}
                with patch.object(worker, "choose_account", return_value=([], {}, None)), \
                     patch.object(worker, "select_account", return_value=selection), \
                     patch.object(worker, "account_lease", return_value=lease), \
                     patch.object(worker, "inspect_account", return_value=({}, {})), \
                     patch.object(worker, "quota_snapshot"), patch.object(worker, "read_usage", return_value={}), \
                     patch.object(worker, "run_agent", return_value="Completed") as run_agent:
                    with worker.run_codex(Mock(), config, heartbeat, Path(directory)):
                        pass
                self.assertEqual(run_agent.call_args.kwargs["timeout"], expected)
                self.assertEqual(run_agent.call_args.args[3], Path.cwd())

    def test_broker_authentication_or_storage_failure_is_not_reported_as_exhausted_quota(self):
        class Broker:
            def call(self, action):
                return {"accounts": ["a" * 64]}
        class Heartbeat:
            failed = threading.Event()
        config = {"model": "gpt-6.1-sol", "effort": "max",
                  "selection": {"min_remaining_percent": 20, "max_snapshot_age_seconds": 60}}
        for status in (401, 503):
            with self.subTest(status=status), \
                 patch.object(worker, "account_lease", side_effect=HTTPError("https://broker", status, "failure", {}, None)), \
                 self.assertRaises(HTTPError):
                worker.choose_account(Broker(), config, Heartbeat())

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
            # The workflow checkout predates a preceding task's merge. The
            # admitted worker must fetch that merge before starting its agent.
            upstream = root / "upstream"
            subprocess.run(["git", "clone", str(remote), str(upstream)], check=True, capture_output=True)
            (upstream / "preceding.txt").write_text("preceding task merged\n")
            subprocess.run(["git", "-C", str(upstream), "add", "."], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(upstream), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "commit", "-m", "preceding task"], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(upstream), "push", "origin", "main"], check=True, capture_output=True)
            main_before = subprocess.check_output(["git", "--git-dir", str(remote), "rev-parse", "main"], text=True)
            class Broker:
                key = KEY
                def call(self, action, **data):
                    return {"token": "temporary-test-token"}
            class Heartbeat:
                failed = threading.Event()
            config = {"repository": "owner/repo", "ref": "main", "auto_merge": False, "issue": {
                "id": ISSUE, "identifier": "KIAN-891", "title": "Improve file", "url": "https://linear.app/test"},
                "validation_commands": [[sys.executable, "-c", "from pathlib import Path; assert Path('file.txt').read_text() == 'updated\\n'"]]}
            @contextmanager
            def implement(*args):
                self.assertEqual((checkout / "preceding.txt").read_text(), "preceding task merged\n")
                (checkout / "file.txt").write_text("updated\n")
                yield "Updated the file and validated it."
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


class AutoMergeTests(unittest.TestCase):
    def setUp(self):
        self.config = {"repository": "owner/repo", "ref": "main"}
        self.branch = "clusterfork/kian-891-aaaaaaaa"
        self.url = "https://github.com/owner/repo/pull/1"
        self.sha = "c" * 40
        self.pull = {"html_url": self.url, "state": "open", "draft": False, "merged": False,
                     "user": {"login": "clusterfork[bot]"},
                     "head": {"repo": {"full_name": "owner/repo"}, "ref": self.branch, "sha": self.sha},
                     "base": {"ref": "main"}, "body": f"<!-- clusterfork-dispatch:{KEY} -->"}
        self.policy = {"data": {"repository": {"autoMergeAllowed": True}}}

    def request(self, **kwargs):
        worker.request_auto_merge(self.config, "private-test-token", self.url, KEY, self.branch, **kwargs)

    def test_default_auto_merge_uses_exact_commit_and_never_bypasses_checks(self):
        with patch.object(worker, "json_request", side_effect=[self.pull, self.policy]), \
             patch.object(worker, "command", side_effect=['[{"name":"test","bucket":"pending"}]', '']) as command:
            self.request(expected_sha=self.sha)
        args = command.call_args.args[0]
        self.assertEqual(args, ["gh", "pr", "merge", self.url, "--auto", "--squash", "--match-head-commit", self.sha])
        self.assertNotIn("--admin", args)
        self.assertNotIn("private-test-token", args)
        self.assertEqual(command.call_args.kwargs["env"]["GH_TOKEN"], "private-test-token")
        self.assertEqual(command.call_args_list[0].args[0],
                         ["gh", "pr", "checks", self.url, "--required", "--json", "name,bucket"])
        self.assertIn(8, command.call_args_list[0].kwargs["allowed_returncodes"])

    def test_project_can_disable_auto_merge(self):
        self.config["auto_merge"] = False
        with patch.object(worker, "json_request") as request, patch.object(worker, "command") as command:
            self.request()
        request.assert_not_called()
        command.assert_not_called()

    def test_missing_ci_or_repository_auto_merge_prevents_merge(self):
        with patch.object(worker, "json_request", side_effect=[self.pull, {"data": {"repository": {"autoMergeAllowed": False}}}]), \
             patch.object(worker, "command") as command, self.assertRaises(worker.WorkerFailure):
            self.request()
        command.assert_not_called()
        for result in ['[]', '{"error":"unavailable"}', 'not json']:
            with self.subTest(result=result), \
                 patch.object(worker, "json_request", side_effect=[self.pull, self.policy]), \
                 patch.object(worker, "command", return_value=result) as command, \
                 patch.object(worker.time, "sleep"), self.assertRaises(worker.WorkerFailure):
                self.request()
            self.assertTrue(all(call.args[0][2] == "checks" for call in command.call_args_list))

    def test_foreign_fork_changed_head_or_unrelated_pr_cannot_be_auto_merged(self):
        cases = []
        for field, value in [("user", {"login": "someone-else"}), ("body", "unrelated PR"),
                             ("base", {"ref": "another-branch"}), ("draft", True)]:
            cases.append({**self.pull, field: value})
        cases.append({**self.pull, "head": {**self.pull["head"], "repo": {"full_name": "attacker/repo"}}})
        cases.append({**self.pull, "head": {**self.pull["head"], "sha": "d" * 40}})
        for pull in cases:
            with self.subTest(pull=pull), patch.object(worker, "json_request", return_value=pull), \
                 patch.object(worker, "command") as command, self.assertRaises(worker.WorkerFailure):
                self.request(expected_sha=self.sha)
            command.assert_not_called()

    def test_retry_recovers_merged_pr_without_repeating_work_or_requesting_merge(self):
        merged = {**self.pull, "state": "closed", "merged": True, "merged_at": "2026-10-28T20:00:00Z"}
        with patch.object(worker, "json_request", return_value=[merged]) as request:
            self.assertEqual(worker.find_pr(self.config, "token", self.branch), self.url)
            self.assertIn("state=all", request.call_args.args[0])
        with patch.object(worker, "json_request", return_value=merged), patch.object(worker, "command") as command:
            self.request()
        command.assert_not_called()

    def test_closed_unmerged_pr_is_not_treated_as_completed_work(self):
        closed = {**self.pull, "state": "closed", "merged_at": None}
        with patch.object(worker, "json_request", return_value=[closed]):
            self.assertIsNone(worker.find_pr(self.config, "token", self.branch))

    def test_registry_defaults_to_auto_merge_and_rejects_non_boolean_override(self):
        config = json.loads((ROOT / "automation/azure/projects.json").read_text())
        config["projects"][0].pop("auto_merge", None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(config))
            self.assertTrue(Registry.load(path).routes[PROJECT].auto_merge)
            config["projects"][0]["auto_merge"] = False
            path.write_text(json.dumps(config))
            self.assertFalse(Registry.load(path).routes[PROJECT].auto_merge)
            config["projects"][0]["auto_merge"] = "true"
            path.write_text(json.dumps(config))
            with self.assertRaisesRegex(ValueError, "boolean"):
                Registry.load(path)

    def test_project_timeout_defaults_and_bounds_leave_time_for_publication(self):
        config = json.loads((ROOT / "automation/azure/projects.json").read_text())
        config["projects"][0].pop("agent_timeout_minutes", None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(config))
            self.assertEqual(Registry.load(path).routes[PROJECT].agent_timeout_minutes, 300)
            for value in (1, 330):
                config["projects"][0]["agent_timeout_minutes"] = value
                path.write_text(json.dumps(config))
                self.assertEqual(Registry.load(path).routes[PROJECT].agent_timeout_minutes, value)
            for value in (0, -1, 331, True, 1.5, "300"):
                config["projects"][0]["agent_timeout_minutes"] = value
                path.write_text(json.dumps(config))
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "agent_timeout_minutes"):
                    Registry.load(path)

    def test_hosted_job_budget_covers_the_longest_allowed_agent_attempt(self):
        workflow = (ROOT / ".github/workflows/clusterfork-worker.yml").read_text()
        timeout = int(re.search(r"timeout-minutes:\s*(\d+)", workflow)[1])
        self.assertLessEqual(timeout, 360)
        self.assertGreaterEqual(timeout - 330, 30)


if __name__ == "__main__":
    unittest.main()
