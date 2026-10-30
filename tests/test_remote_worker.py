"""Worker trust boundaries, credential fencing, and real Git publication behavior."""

import copy
from collections import Counter, defaultdict, deque
from contextlib import contextmanager, nullcontext, redirect_stdout
import importlib.util
import io
import json
import logging
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
from unittest.mock import ANY, MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
import worker
import agent_session
import accounts as account_cli
from codex_runtime import CodexError, quota_snapshot
from dispatch_clients import GitHub as GitHubClient
from dispatch_core import Registry
from quota_selection import select_account
from worker_core import Accounts, WorkerError, WorkerService, authorize_claims
from repository_queue import RepositoryQueue
from readiness_policy import POLICY, PROTOCOL, RESULT_SCHEMA, REVIEW_TIMEOUT_SECONDS, digest
from auto_repair import repair_instructions
from call_budget import record_api_calls

PROJECT = "374f597c-89f5-4e40-9798-07533f57bfba"
ISSUE = "66547614-8ed4-4928-834e-aac76f01afd2"
KEY = "a" * 64
NOW = 1_800_000_000


class MemoryTable:
    def __init__(self):
        self.rows = {}
        self.lock = threading.RLock()
        self.operations = Counter()
        self.mutations = []

    def create(self, row):
        with self.lock:
            self.operations["create"] += 1
            self.mutations.append(("create", (row["RowKey"],)))
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
            self.operations["replace"] += 1
            self.mutations.append(("replace", (row["RowKey"],)))
            if self.rows[row["RowKey"]][1] != etag:
                raise WorkerError("concurrent modification")
            self.rows[row["RowKey"]] = (copy.deepcopy(row), etag + 1)

    def transaction(self, changes):
        with self.lock:
            changes = list(changes)
            self.operations["transaction"] += 1
            self.mutations.append(("transaction", tuple(row["RowKey"] for row, _ in changes)))
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

    def images(self, issue):
        return []

    def prework_stopped(self, issue_id, route):
        return True


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


def selector_claims(identity=None):
    identity = identity or claims()
    return {**identity,
            "job_workflow_ref": f"{identity['repository']}/.github/workflows/clusterfork-select-account.yml@{identity['ref']}",
            "job_workflow_sha": identity["sha"]}


def receipt():
    return {"RowKey": KEY, "project_id": PROJECT, "repository": "KianBahasadri/clusterfork",
            "issue_id": ISSUE, "identifier": "KIAN-891", "status": "dispatched"}


def validate_fake_turns(validate, summary):
    """Let publication-only fixtures finish their turns through the worker gate."""
    while validate(summary, deadline=time.monotonic() + 60, failure_signals=()) is not None:
        pass


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
                broker.call.assert_called_with("finish", status="failed", failure=ANY)
                self.assertEqual(broker.call.call_count, 2)

    def test_confirmed_serial_admission_allows_normal_execution(self):
        result, broker, execute = self.run_worker({"serial_admission": True,
                                                "protocol": worker.PROTOCOL, "phase": "development",
                                                "account_selection": False, "account_id": "a" * 64})
        self.assertEqual(result, 0)
        execute.assert_called_once()
        broker.call.assert_called_with("finish", status="completed", pull_request="")

    def test_worker_rejects_broker_predating_selection_reporting(self):
        previous = "readiness-v1:" + digest([POLICY, RESULT_SCHEMA, REVIEW_TIMEOUT_SECONDS])
        result, broker, execute = self.run_worker({"serial_admission": True,
                                                  "protocol": previous, "phase": "development"})
        self.assertEqual(result, 1)
        execute.assert_not_called()
        broker.call.assert_called_with("finish", status="failed", failure=ANY)

    def test_unbound_accounts_and_wrong_job_roles_cannot_start_agent_execution(self):
        for extra in ({}, {"account_selection": True, "account_id": "a" * 64},
                      {"account_selection": False}, {"account_selection": False, "account_id": "invalid"}):
            with self.subTest(extra=extra):
                result, _, execute = self.run_worker({"serial_admission": True, "protocol": worker.PROTOCOL,
                                                      "phase": "development", **extra})
                self.assertEqual(result, 1)
                execute.assert_not_called()


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
            publication_guard = worker.PublicationGuard({})
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
            def issue_dependencies(self, identifier):
                return []
        class GitHub:
            permissions = None
            workflows = False
            def verify_worker_run(self, *args):
                return None
            def workflow_changes_allowed(self, repository):
                return self.workflows
            def installation_token(self, repository, *, permissions):
                self.permissions = permissions
                return "temporary-token"
            def worker_result(self, *args):
                return {"status": "in_progress"}
        linear, github = record_api_calls(Linear()), record_api_calls(GitHub())
        service = WorkerService(self.registry, receipts, runs, Accounts(MemoryTable()), linear, github,
                                repository_queue(self.registry, receipts, runs, linear, github),
                                clock=lambda: NOW)
        return service, github, issue

    def test_publication_token_has_only_contents_and_pull_requests_permissions(self):
        service, github, _ = self.service()
        config = service.handle("start", claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
        self.assertEqual((config["model"], config["effort"]), ("gpt-6.1-sol", "max"))
        self.assertTrue(config["auto_merge"])
        self.assertEqual(config["agent_timeout_minutes"], 300)
        self.assertIs(config["serial_admission"], True)
        self.assertIs(config["workflow_changes_allowed"], False)
        service.handle("github", claims(), {"dispatch_key": KEY})
        self.assertEqual(github.permissions, {"contents": "write", "pull_requests": "write"})

    def test_workflow_grant_is_reported_at_start_and_rechecked_for_publication(self):
        service, github, _ = self.service()
        github.workflows = True
        config = service.handle("start", claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
        self.assertIs(config["workflow_changes_allowed"], True)
        service.handle("github", claims(), {"dispatch_key": KEY})
        self.assertEqual(github.permissions, {"contents": "write", "pull_requests": "write", "workflows": "write"})
        service.handle("checkout", claims(), {"dispatch_key": KEY})
        self.assertEqual(github.permissions, {"contents": "read", "pull_requests": "read"})
        github.workflows = False
        service.handle("github", claims(), {"dispatch_key": KEY})
        self.assertEqual(github.permissions, {"contents": "write", "pull_requests": "write"})

    def test_removing_project_enrollment_prevents_publication(self):
        service, _, issue = self.service()
        service.handle("start", claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
        issue["project"]["labels"]["nodes"] = []
        with self.assertRaises(WorkerError):
            service.handle("github", claims(), {"dispatch_key": KEY})

    def test_failed_finish_persists_bounded_wip_identity_and_scrubbed_output(self):
        for repair in (False, True):
            with self.subTest(repair=repair):
                service, _, _ = self.service()
                service.handle("start", claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
                identifier, target_key = "KIAN-891", KEY
                if repair:
                    row, etag = service.receipts.get(KEY)
                    identifier, target_key = "KIAN-925", "c" * 64
                    row.update(target_identifier=identifier, target_dispatch_key=target_key,
                               is_auto_repair=True, repair_parent_key=target_key)
                    service.receipts.replace(row, etag)
                branch = f"clusterfork-wip/{identifier.lower()}-{target_key[:8]}"
                service.handle("finish", claims(), {"dispatch_key": KEY, "status": "failed",
                    "wip_branch": branch, "wip_commit_sha": "b" * 40,
                    "failure": {"operation": "validation/1", "kind": "command_failed", "exit_code": 1,
                                "failure_output": "Traceback ghp_synthetic", "run_id": "999"}})
                saved = service.runs.get(KEY)[0]
                self.assertEqual((saved["wip_branch"], saved["wip_commit_sha"]), (branch, "b" * 40))
                diagnostic = json.loads(saved["failure"])
                self.assertEqual(diagnostic["failure_output"], "Traceback [REDACTED]")
                self.assertEqual(diagnostic["run_id"], "123")

    def test_normal_worker_still_requires_the_reviewed_coding_context(self):
        service, _, _ = self.service()
        service.queue.readiness.require = Mock(return_value={"review_key": "approved", "context_sha": "a" * 40})
        service.handle("start", claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
        with self.assertRaisesRegex(WorkerError, "reviewed repository context"):
            service.handle("progress", claims(), {"dispatch_key": KEY, "stage": "coding", "context_sha": "b" * 40})

    def test_failed_finish_rejects_foreign_or_incomplete_wip_identity(self):
        valid = {"wip_branch": "clusterfork/kian-891-aaaaaaaa", "wip_commit_sha": "b" * 40}
        for extra in ({"wip_branch": "main"}, {"wip_branch": "clusterfork/other-aaaaaaaa"},
                      {"wip_commit_sha": "not-a-sha"}, {"wip_commit_sha": None},
                      {"wip_branch": []}, {"status": "completed"}):
            with self.subTest(extra=extra):
                service, _, _ = self.service()
                service.handle("start", claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
                with self.assertRaises(WorkerError):
                    service.handle("finish", claims(), {"dispatch_key": KEY, "status": "failed", **valid, **extra})

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


class GitHubPermissionTests(unittest.TestCase):
    def test_only_the_installed_workflow_write_grant_enables_workflow_publication(self):
        for grant, allowed in (("write", True), ("read", False), (None, False)):
            with self.subTest(grant=grant):
                request = Mock(return_value={"id": 42, "permissions": {"workflows": grant}})
                client = GitHubClient("123", "fake-private-key", request=request)
                with patch.dict(sys.modules, {"jwt": SimpleNamespace(encode=lambda *a, **kw: "fake-jwt")}):
                    self.assertIs(client.workflow_changes_allowed("owner/repo"), allowed)
                request.assert_called_once_with("https://api.github.com/repos/owner/repo/installation", token="Bearer fake-jwt")

    def test_workflow_publication_token_stays_scoped_to_one_repository(self):
        request = Mock(side_effect=[{"id": 42}, {"token": "publication-token"}])
        client = GitHubClient("123", "fake-private-key", request=request)
        permissions = {"contents": "write", "pull_requests": "write", "workflows": "write"}
        with patch.dict(sys.modules, {"jwt": SimpleNamespace(encode=lambda *a, **kw: "fake-jwt")}):
            self.assertEqual(client.installation_token("owner/repo", permissions=permissions), "publication-token")
        self.assertEqual(request.call_args.kwargs["data"], {"repositories": ["repo"], "permissions": permissions})


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
                self.assertIn("no approved Workflows write permission", run_agent.call_args.args[4])

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
                worker.choose_account(Broker(), config, Heartbeat(), preflight=True)

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
    def test_workflow_edits_need_broker_permission_including_recovered_branch_changes(self):
        for allowed in (False, True):
            for source in ("new", "tracked", "recovered"):
                with self.subTest(allowed=allowed, source=source), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    remote, checkout = root / "remote.git", root / "checkout"
                    def git(*args):
                        return subprocess.check_output(["git", "-C", str(checkout), *args], text=True,
                                                       stderr=subprocess.DEVNULL).strip()
                    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
                    subprocess.run(["git", "clone", str(remote), str(checkout)], check=True, capture_output=True)
                    git("config", "user.name", "Test")
                    git("config", "user.email", "test@example.com")
                    (checkout / "file.txt").write_text("original\n")
                    workflow = checkout / ".github/workflows/example.yml"
                    workflow.parent.mkdir(parents=True)
                    if source == "tracked":
                        workflow.write_text("old workflow\n")
                    git("add", ".")
                    git("commit", "-m", "initial")
                    git("push", "origin", "main")
                    main = git("rev-parse", "main")
                    branch = "clusterfork/kian-891-aaaaaaaa"
                    if source == "recovered":
                        git("checkout", "-b", branch)
                        workflow.write_text("updated workflow\n")
                        git("add", ".")
                        git("commit", "-m", "previous attempt")
                        git("push", "origin", branch)
                        git("checkout", "main")
                        git("branch", "-D", branch)
                    before = git("ls-remote", "origin", "refs/heads/" + branch)
                    validated = root / "validated"
                    config = {"repository": "owner/repo", "ref": "main", "auto_merge": False,
                              "workflow_changes_allowed": allowed,
                              "issue": {"id": ISSUE, "identifier": "KIAN-891", "title": "Update workflow", "url": "https://linear.app/test"},
                              "validation_commands": [[sys.executable, "-c", f"from pathlib import Path; Path({str(validated)!r}).touch()"]]}
                    broker = Mock(key=KEY, publication_guard=worker.PublicationGuard({}))
                    broker.call.return_value = {"token": "test-token"}
                    @contextmanager
                    def implement(*args, validate):
                        if source != "recovered":
                            workflow.parent.mkdir(parents=True, exist_ok=True)
                            workflow.write_text("updated workflow\n")
                        validate_fake_turns(validate, "Updated workflow.")
                        yield "Updated workflow."
                    original = Path.cwd()
                    try:
                        os.chdir(checkout)
                        with patch.object(worker, "run_codex", side_effect=implement), \
                             patch.object(worker, "find_pr", return_value=None), \
                             patch.object(worker, "json_request", return_value={"html_url": "https://github.com/owner/repo/pull/1"}) as request:
                            if allowed:
                                self.assertEqual(worker.execute(broker, config, SimpleNamespace(failed=threading.Event()), root),
                                                 ("completed", "https://github.com/owner/repo/pull/1"))
                                self.assertTrue(validated.exists())
                                self.assertIn(".github/workflows/example.yml", git("diff", "--name-only", "main..." + branch))
                                request.assert_called_once()
                            else:
                                with self.assertRaises(worker.WorkerFailure) as caught:
                                    worker.execute(broker, config, SimpleNamespace(failed=threading.Event()), root)
                                self.assertEqual(caught.exception.clusterfork_failure["reason_code"], "workflows_permission_missing")
                                self.assertFalse(validated.exists())
                                self.assertEqual(git("ls-remote", "origin", "refs/heads/" + branch), before)
                                self.assertFalse(any(call.args[0] == "github" for call in broker.call.call_args_list))
                                request.assert_not_called()
                        self.assertEqual(git("ls-remote", "origin", "refs/heads/main").split()[0], main)
                    finally:
                        os.chdir(original)

    def test_repair_checks_out_parent_branch_validates_and_updates_or_creates_parent_pr(self):
        for existing_pr in (True, False):
            with self.subTest(existing_pr=existing_pr), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                remote, checkout = root / "remote.git", root / "checkout"
                def git(*args):
                    return subprocess.check_output(["git", "-C", str(checkout), *args], text=True, stderr=subprocess.DEVNULL).strip()
                subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
                subprocess.run(["git", "clone", str(remote), str(checkout)], check=True, capture_output=True)
                (checkout / "file.txt").write_text("original\n")
                git("add", ".")
                git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial")
                git("push", "origin", "main")
                main = git("rev-parse", "main")
                branch = "clusterfork/kian-891-aaaaaaaa"
                git("checkout", "-b", branch)
                (checkout / "file.txt").write_text("broken\n")
                git("add", ".")
                git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "failed parent")
                broken = git("rev-parse", "HEAD")
                git("push", "origin", branch)
                git("checkout", "main")
                git("branch", "-D", branch)
                broker = Mock(key="d" * 64, publication_guard=worker.PublicationGuard({}))
                broker.call.return_value = {"token": "temporary-token"}
                heartbeat = SimpleNamespace(failed=threading.Event())
                url = "https://github.com/owner/repo/pull/1"
                pull = {"html_url": url, "state": "open", "merged": False,
                        "user": {"login": "clusterfork[bot]"},
                        "head": {"repo": {"full_name": "owner/repo"}, "ref": branch, "sha": broken},
                        "base": {"ref": "main"}, "body": f"<!-- clusterfork-dispatch:{KEY} -->"}
                config = {"repository": "owner/repo", "ref": "main", "auto_merge": True,
                          "issue": {"id": ISSUE, "identifier": "KIAN-891", "title": "Fix original task", "url": "https://linear.app/test"},
                          "repair": {"target_branch": branch, "target_dispatch_key": KEY,
                                     "instructions": repair_instructions("main") + "\nBroker repair trigger: validation failed.",
                                     "target_identifier": "KIAN-891", "target_pull_request": url if existing_pr else ""},
                          "validation_commands": [[sys.executable, "-c", "from pathlib import Path; assert Path('file.txt').read_text() == 'fixed\\n'"]]}
                @contextmanager
                def implement(*args, validate):
                    self.assertEqual(git("branch", "--show-current"), branch)
                    self.assertEqual((checkout / "file.txt").read_text(), "broken\n")
                    (checkout / "file.txt").write_text("fixed\n")
                    validate_fake_turns(validate, "Fixed the parent's validation failure.")
                    yield "Fixed the parent's validation failure."
                original = Path.cwd()
                try:
                    os.chdir(checkout)
                    with patch.object(worker, "run_codex", side_effect=implement) as agent, \
                         patch.object(worker, "find_pr", return_value=url if existing_pr else None), \
                         patch.object(worker, "repair_check_output", return_value=""), \
                         patch.object(worker, "json_request", return_value=pull) as request, \
                         patch.object(worker, "request_auto_merge") as merge:
                        self.assertEqual(worker.execute(broker, config, heartbeat, root), ("completed", url))
                    agent.assert_called_once()
                    self.assertEqual(git("ls-remote", "origin", "refs/heads/main").split()[0], main)
                    repaired = git("ls-remote", "origin", "refs/heads/" + branch).split()[0]
                    self.assertNotEqual(repaired, broken)
                    self.assertFalse(git("ls-remote", "origin", "refs/heads/clusterfork/kian-1000-*"))
                    self.assertEqual(merge.call_args.args[3:5], (KEY, branch))
                    self.assertEqual(merge.call_args.kwargs["expected_sha"], repaired)
                    broker.call.assert_any_call("progress", stage="publishing", repair_validation_passed=True)
                    creates = [call for call in request.call_args_list if len(call.args) > 2]
                    self.assertEqual(len(creates), 0 if existing_pr else 1)
                    if creates:
                        self.assertIn("clusterfork-dispatch:" + KEY, creates[0].args[2]["body"])
                        self.assertEqual(creates[0].args[2]["head"], branch)
                finally:
                    os.chdir(original)

    def test_repair_cannot_reuse_foreign_pr_even_with_manual_merge_policy(self):
        config = {"repository": "owner/repo", "ref": "main", "auto_merge": False,
                  "issue": {"identifier": "KIAN-1000"},
                  "repair": {"target_dispatch_key": KEY, "target_identifier": "KIAN-891",
                             "target_branch": "clusterfork/kian-891-aaaaaaaa"}}
        broker = Mock(key="d" * 64, publication_guard=worker.PublicationGuard({}))
        broker.call.return_value = {"token": "temporary-token"}
        with patch.object(worker, "find_pr", return_value="https://github.com/owner/repo/pull/1"), \
             patch.object(worker, "json_request", return_value={"head": {"repo": {"full_name": "attacker/repo"}}}), \
             patch.object(worker, "run_codex") as agent, self.assertRaises(worker.WorkerFailure):
            worker.execute(broker, config, SimpleNamespace(failed=threading.Event()), Path("/tmp"))
        agent.assert_not_called()

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
                publication_guard = worker.PublicationGuard({})
                def call(self, action, **data):
                    return {"token": "temporary-test-token"}
            class Heartbeat:
                failed = threading.Event()
            config = {"repository": "owner/repo", "ref": "main", "auto_merge": False, "issue": {
                "id": ISSUE, "identifier": "KIAN-891", "title": "Improve file", "url": "https://linear.app/test"},
                "validation_commands": [[sys.executable, "-c", "from pathlib import Path; assert Path('file.txt').read_text() == 'updated\\n'"]]}
            @contextmanager
            def implement(*args, validate):
                self.assertEqual((checkout / "preceding.txt").read_text(), "preceding task merged\n")
                (checkout / "file.txt").write_text("updated\n")
                validate_fake_turns(validate, "Updated the file and validated it.")
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


class RepairEvidenceTests(unittest.TestCase):
    def test_repair_prompt_uses_broker_instructions_with_original_issue_requirements(self):
        broker = Mock(publication_guard=worker.PublicationGuard({}))
        config = {"ref": "main", "issue": {"id": ISSUE, "identifier": "KIAN-891", "title": "Original task",
                  "description": "Keep the original behavior requirements", "url": "https://linear.app/test"},
                  "repair": {"instructions": "Broker-rendered repair context: diagnose the failed first run and fix its PR."}}
        prompt = worker.development_prompt(broker, config)
        for text in ("KIAN-891: Original task", "Keep the original behavior requirements",
                     "Broker-rendered repair context", "failed first run"):
            self.assertIn(text, prompt)
        for invalid in (None, "", "  "):
            config["repair"]["instructions"] = invalid
            with self.assertRaisesRegex(worker.WorkerFailure, "broker did not supply"):
                worker.development_prompt(broker, config)

    def test_failed_required_job_tail_is_scrubbed_byte_bounded_and_kept_out_of_logs(self):
        broker = SimpleNamespace(publication_guard=worker.PublicationGuard({}))
        broker.publication_guard.remember("opaque-github-credential")
        checks = [{"name": "required-test", "bucket": "fail", "description": "Failed tests",
                   "link": "https://github.com/owner/repo/actions/runs/10/job/20"},
                  {"name": "passing", "bucket": "pass", "link": "https://github.com/owner/repo/actions/runs/10/job/21"},
                  {"name": "pending", "bucket": "pending", "link": "https://github.com/owner/repo/actions/runs/10/job/22"}]
        log = "é" * 10000 + "\nprivate-ci-marker opaque-github-credential ghp_synthetic Bearer opaque-header api_key=opaque-key\x00"
        stdout, logs = io.StringIO(), io.StringIO()
        handler = logging.StreamHandler(logs)
        logging.getLogger().addHandler(handler)
        try:
            with patch.object(worker, "command", side_effect=[json.dumps(checks), log]) as command, redirect_stdout(stdout):
                output = worker.repair_check_output(broker, {"repository": "owner/repo"},
                                                  "opaque-github-credential", "https://github.com/owner/repo/pull/1")
        finally:
            logging.getLogger().removeHandler(handler)
        self.assertLessEqual(len(output.encode("utf-8")), 16000)
        self.assertIn("required-test", output)
        self.assertIn("private-ci-marker", output)
        for secret in ("opaque-github-credential", "ghp_synthetic", "opaque-header", "opaque-key", "\x00"):
            self.assertNotIn(secret, output)
        self.assertNotIn("private-ci-marker", stdout.getvalue() + logs.getvalue())
        self.assertEqual(command.call_count, 2)
        self.assertEqual(command.call_args.args[0], ["gh", "api", "repos/owner/repo/actions/jobs/20/logs"])
        self.assertIn("--required", command.call_args_list[0].args[0])

    def test_ci_collection_falls_back_safely_for_foreign_links_unavailable_logs_and_unreadable_auth(self):
        broker = SimpleNamespace(publication_guard=worker.PublicationGuard({}))
        for mode in ("foreign", "log_failure", "malformed", "auth"):
            with self.subTest(mode=mode):
                check = {"name": "required", "bucket": "fail", "description": "token=opaque-key",
                         "link": "https://github.com/owner/repo/actions/runs/10/job/20"}
                if mode == "foreign":
                    check["link"] = "https://github.com/attacker/repo/actions/runs/10/job/20"
                responses = ["not json"] if mode == "malformed" else [json.dumps([check]),
                    worker.WorkerFailure("log unavailable") if mode == "log_failure" else "private-output"]
                with patch.object(worker, "command", side_effect=responses) as command, \
                     patch.object(broker.publication_guard, "refresh_auth",
                                  side_effect=worker.PublicationScanError() if mode == "auth" else None):
                    result = worker.repair_check_output(broker, {"repository": "owner/repo"}, "token",
                                                       "https://github.com/owner/repo/pull/1")
                self.assertNotIn("opaque-key", result)
                if mode == "foreign":
                    self.assertEqual(command.call_count, 1)
                if mode == "auth":
                    self.assertNotIn("private-output", result)

    def test_multiple_failed_checks_share_one_utf8_byte_limit(self):
        broker = SimpleNamespace(publication_guard=worker.PublicationGuard({}))
        checks = [{"name": f"check-{n}", "bucket": "fail", "description": "Failed tests",
                   "link": f"https://github.com/owner/repo/actions/runs/10/job/{n}"} for n in (20, 21)]
        with patch.object(worker, "command", side_effect=[json.dumps(checks), "é" * 10000, "é" * 10000]):
            output = worker.repair_check_output(broker, {"repository": "owner/repo"}, "token",
                                              "https://github.com/owner/repo/pull/1")
        self.assertLessEqual(len(output.encode("utf-8")), 16000)
        self.assertIn("check-20", output)
        self.assertIn("check-21", output)


class RepairRecoveryTests(unittest.TestCase):
    @contextmanager
    def scenario(self, *, wip=None, open_pr=True, keep_branch=False, advanced=False, conflicting=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, checkout = root / "origin.git", root / "checkout"
            def git(*args):
                return subprocess.check_output(["git", "-C", str(checkout), *args], text=True,
                                               stderr=subprocess.DEVNULL).strip()
            def commit(message):
                git("add", ".")
                git("-c", "user.name=clusterfork[bot]", "-c",
                    "user.email=clusterfork[bot]@users.noreply.github.com", "commit", "-m", message)
                return git("rev-parse", "HEAD")
            subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "clone", str(remote), str(checkout)], check=True, capture_output=True)
            (checkout / "file.txt").write_text("original\n")
            initial = commit("initial")
            git("push", "origin", "main")
            branch = "clusterfork/kian-891-aaaaaaaa"
            git("checkout", "-b", branch)
            (checkout / "file.txt").write_text("broken\n")
            parent = commit("failed implementation")
            git("push", "origin", branch)
            wip_branch = branch.replace("clusterfork/", "clusterfork-wip/")
            wip_sha = None
            if wip in {"descendant", "divergent"}:
                git("checkout", "-b", wip_branch, parent if wip == "descendant" else initial)
                (checkout / "saved.txt").write_text("preserved WIP\n")
                wip_sha = commit("save unfinished work")
                git("push", "origin", wip_branch)
            git("checkout", "main")
            if not open_pr and not keep_branch:
                git("push", "origin", "--delete", branch)
            if advanced:
                (checkout / "base.txt").write_text("current base\n")
                if conflicting:
                    (checkout / "file.txt").write_text("base changed this line\n")
                commit("advance base")
                git("push", "origin", "main")
                git("reset", "--hard", initial)  # workflow checkout predates base advancement
            git("branch", "-D", branch)
            broker = Mock(key="d" * 64, publication_guard=worker.PublicationGuard({}))
            broker.call.return_value = {"token": "opaque-github-credential"}
            url = "https://github.com/owner/repo/pull/1"
            pull = {"html_url": url, "state": "open", "merged": False, "title": "Original work",
                    "user": {"login": "clusterfork[bot]"}, "base": {"ref": "main"},
                    "body": f"<!-- clusterfork-dispatch:{KEY} -->",
                    "head": {"repo": {"full_name": "owner/repo"}, "ref": branch, "sha": parent}}
            config = {"repository": "owner/repo", "ref": "main", "auto_merge": False,
                      "readiness": {"context_sha": initial},
                      "issue": {"identifier": "KIAN-891", "title": "Fix original task", "url": "https://linear.app/test"},
                      "repair": {"target_branch": branch, "target_dispatch_key": KEY,
                                 "instructions": repair_instructions("main"),
                                 "target_identifier": "KIAN-891", "target_pull_request": url if open_pr else "",
                                 "failure_output": "parent-private-traceback ghp_synthetic opaque-github-credential"},
                      "validation_commands": [[sys.executable, "-c",
                          "from pathlib import Path; assert Path('file.txt').read_text() == 'fixed\\n'"]]}
            if wip:
                config["repair"].update(wip_branch=wip_branch, wip_commit_sha=wip_sha or "a" * 40)
            pushes, prompts, ci_commands = [], [], []
            real_command = worker.command
            def command(args, **kwargs):
                if args[0] == "gh":
                    ci_commands.append((args, kwargs))
                    if args[1:3] == ["pr", "checks"]:
                        return json.dumps([{"name": "validation", "bucket": "fail", "description": "Tests failed",
                                            "link": "https://github.com/owner/repo/actions/runs/10/job/20"}])
                    return "ci-private-traceback opaque-github-credential ghp_synthetic"
                if args[:2] == ["git", "push"]:
                    pushes.append(args)
                return real_command(args, **kwargs)
            def request(url, token, data=None):
                return copy.deepcopy(pull)
            @contextmanager
            def implement(broker, config, *args, validate):
                prompts.append(worker.development_prompt(broker, config))
                # Replaced by each test with its intended repair operation.
                run.implement(git, checkout)
                validate_fake_turns(validate, "Repaired and validated.")
                yield "Repaired and validated."
            run = SimpleNamespace(root=root, checkout=checkout, git=git, broker=broker, config=config,
                                  heartbeat=SimpleNamespace(failed=threading.Event()), branch=branch, parent=parent,
                                  initial=initial, wip_sha=wip_sha, wip_branch=wip_branch, pull=pull, url=url,
                                  pushes=pushes, prompts=prompts, ci_commands=ci_commands,
                                  implement=lambda git, checkout: (checkout / "file.txt").write_text("fixed\n"))
            old = Path.cwd()
            try:
                os.chdir(checkout)
                with patch.object(worker, "command", side_effect=command), \
                     patch.object(worker, "find_pr", return_value=url if open_pr else None), \
                     patch.object(worker, "json_request", side_effect=request), \
                     patch.object(worker, "run_codex", side_effect=implement):
                    yield run
            finally:
                os.chdir(old)

    def test_repair_restores_wip_and_supplies_scrubbed_worker_and_ci_evidence(self):
        for open_pr in (False, True):
            with self.subTest(open_pr=open_pr), self.scenario(wip="descendant", open_pr=open_pr) as run:
                def implement(git, checkout):
                    self.assertEqual(git("rev-parse", "HEAD"), run.wip_sha)
                    self.assertEqual(git("branch", "--show-current"), run.branch)
                    self.assertEqual((checkout / "saved.txt").read_text(), "preserved WIP\n")
                    (checkout / "file.txt").write_text("fixed\n")
                run.implement = implement
                self.assertEqual(worker.execute(run.broker, run.config, run.heartbeat, run.root), ("completed", run.url))
                prompt = run.prompts[0]
                self.assertIn("parent-private-traceback", prompt)
                self.assertIn("git log origin/main..HEAD", prompt)
                self.assertIn("git diff origin/main...HEAD", prompt)
                self.assertIn("git reset --hard origin/main", prompt)
                self.assertNotIn("opaque-github-credential", prompt)
                self.assertNotIn("ghp_synthetic", prompt)
                if open_pr:
                    self.assertIn("ci-private-traceback", prompt)
                    self.assertTrue(run.ci_commands)
                    self.assertEqual(run.ci_commands[-1][1]["env"]["GH_TOKEN"], "opaque-github-credential")
                self.assertFalse(any("--force" in arg for push in run.pushes for arg in push))

    def test_missing_or_divergent_wip_keeps_the_current_open_pr_branch(self):
        for wip in ("missing", "divergent"):
            with self.subTest(wip=wip), self.scenario(wip=wip) as run:
                def implement(git, checkout):
                    self.assertEqual(git("rev-parse", "HEAD"), run.parent)
                    self.assertFalse((checkout / "saved.txt").exists())
                    (checkout / "file.txt").write_text("fixed\n")
                run.implement = implement
                self.assertEqual(worker.execute(run.broker, run.config, run.heartbeat, run.root)[0], "completed")

    def test_foreign_or_changed_wip_is_rejected_before_agent_execution(self):
        for tamper in ("branch", "sha"):
            with self.subTest(tamper=tamper), self.scenario(wip="descendant") as run:
                run.config["repair"]["wip_branch" if tamper == "branch" else "wip_commit_sha"] = (
                    "main" if tamper == "branch" else "f" * 40)
                with self.assertRaises(worker.WorkerFailure):
                    worker.execute(run.broker, run.config, run.heartbeat, run.root)
                self.assertFalse(run.prompts)
                self.assertFalse(run.pushes)

    def test_normal_development_cannot_use_an_advanced_unreviewed_base(self):
        with self.scenario(open_pr=False, advanced=True) as run:
            del run.config["repair"]
            with self.assertRaisesRegex(worker.WorkerFailure, "readiness approval"):
                worker.execute(run.broker, run.config, run.heartbeat, run.root)
            self.assertFalse(run.prompts)
            self.assertFalse(run.pushes)

    def test_reset_before_first_pr_verifies_a_new_bot_pr_before_force_update(self):
        with self.scenario(open_pr=False, keep_branch=True, advanced=True) as run:
            def implement(git, checkout):
                git("reset", "--hard", "origin/main")
                (checkout / "file.txt").write_text("fixed\n")
            run.implement = implement
            self.assertEqual(worker.execute(run.broker, run.config, run.heartbeat, run.root), ("completed", run.url))
            self.assertIn(f"--force-with-lease=refs/heads/{run.branch}:{run.parent}", run.pushes[-1])
            self.assertEqual(run.git("rev-parse", "HEAD^"), run.git("rev-parse", "origin/main"))

    def test_conflicted_or_behind_pr_can_reset_to_advanced_base_and_force_with_exact_lease(self):
        for conflicting in (False, True):
            with self.subTest(conflicting=conflicting), self.scenario(advanced=True, conflicting=conflicting) as run:
                def implement(git, checkout):
                    self.assertNotEqual(git("rev-parse", "origin/main"), run.initial)
                    git("reset", "--hard", "origin/main")
                    (checkout / "file.txt").write_text("fixed\n")
                run.implement = implement
                self.assertEqual(worker.execute(run.broker, run.config, run.heartbeat, run.root), ("completed", run.url))
                self.assertEqual((run.checkout / "base.txt").read_text(), "current base\n")
                head = run.git("rev-parse", "HEAD")
                self.assertEqual(run.git("ls-remote", "origin", f"refs/heads/{run.branch}").split()[0], head)
                self.assertEqual(run.git("rev-parse", "HEAD^"), run.git("rev-parse", "origin/main"))
                self.assertIn(f"--force-with-lease=refs/heads/{run.branch}:{run.parent}", run.pushes[-1])
                self.assertEqual(run.git("log", "-1", "--format=%an <%ae>"),
                                 "clusterfork[bot] <clusterfork[bot]@users.noreply.github.com>")

    def test_rebase_is_allowed_only_with_bot_authors_and_committers_on_the_worker_branch(self):
        with self.scenario(advanced=True) as run:
            def implement(git, checkout):
                git("-c", "user.name=clusterfork[bot]", "-c",
                    "user.email=clusterfork[bot]@users.noreply.github.com", "rebase", "origin/main")
                (checkout / "file.txt").write_text("fixed\n")
            run.implement = implement
            self.assertEqual(worker.execute(run.broker, run.config, run.heartbeat, run.root)[0], "completed")
            self.assertIn(f"--force-with-lease=refs/heads/{run.branch}:{run.parent}", run.pushes[-1])
        for tamper in ("branch", "author", "committer"):
            with self.subTest(tamper=tamper), self.scenario(advanced=True) as run:
                def implement(git, checkout):
                    git("reset", "--hard", "origin/main")
                    (checkout / "file.txt").write_text("fixed\n")
                    if tamper == "branch":
                        git("branch", "-m", "unexpected")
                    else:
                        git("add", ".")
                        author = "Human <human@example.com>" if tamper == "author" else "clusterfork[bot] <clusterfork[bot]@users.noreply.github.com>"
                        name = "Human" if tamper == "committer" else "clusterfork[bot]"
                        email = "human@example.com" if tamper == "committer" else "clusterfork[bot]@users.noreply.github.com"
                        git("-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "--author", author, "-m", "manual")
                run.implement = implement
                with self.assertRaises(worker.WorkerFailure) as caught:
                    worker.execute(run.broker, run.config, run.heartbeat, run.root)
                self.assertEqual(caught.exception.clusterfork_failure["reason_code"], "git_integrity_changed")
                self.assertFalse(run.pushes)

    def test_force_update_refuses_changed_pr_ownership_marker_or_remote_head(self):
        for tamper in ("owner", "marker", "head", "remote"):
            with self.subTest(tamper=tamper), self.scenario(advanced=True) as run:
                def implement(git, checkout):
                    git("reset", "--hard", "origin/main")
                    (checkout / "file.txt").write_text("fixed\n")
                    if tamper == "owner":
                        run.pull["user"]["login"] = "human"
                    elif tamper == "marker":
                        run.pull["body"] = "a different dispatch"
                    elif tamper == "head":
                        run.pull["head"]["sha"] = run.initial
                    else:
                        git("push", "--force", "origin", f"origin/main:refs/heads/{run.branch}")
                run.implement = implement
                with self.assertRaises(worker.WorkerFailure):
                    worker.execute(run.broker, run.config, run.heartbeat, run.root)
                if tamper != "remote":
                    self.assertFalse(run.pushes)
                else:
                    self.assertEqual(run.git("ls-remote", "origin", f"refs/heads/{run.branch}").split()[0],
                                     run.git("rev-parse", "origin/main"))


class ValidationRecoveryTests(unittest.TestCase):
    """Exercise real sessions, broker fencing, validation, and local Git remotes."""

    @contextmanager
    def scenario(self, *, fix_on_turn=2, open_pr=False, dirty_file=None, turn_error=False,
                 validation=None, turn_hook=None, workflows_allowed=False):
        self.registry = Registry.load(ROOT / "automation/azure/projects.json")
        service, github, issue = WorkerAuthorizationTests.service(self)
        github.workflows = workflows_allowed
        service.accounts.clock = lambda: NOW
        service.sync_linear = Mock()
        service.sync_steering = Mock()
        service.steering = Mock()
        service.steering.poll.return_value = {"instruction": None}
        identity = service.accounts.enroll(auth("worker-account", "opaque-leased-login"))
        service.handle("start", selector_claims(), {"dispatch_key": KEY, "protocol": PROTOCOL})
        service.handle("bind_account", selector_claims(), {"dispatch_key": KEY, "account_id": identity})
        branch = "clusterfork/kian-891-aaaaaaaa"
        calls, servers, prompts, validation_liveness = [], [], [], []
        test = self
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, checkout = root / "origin.git", root / "checkout"
            def git(*args):
                return subprocess.check_output(["git", "-C", str(checkout), *args], text=True,
                                               stderr=subprocess.DEVNULL).strip()
            subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "clone", str(remote), str(checkout)], check=True, capture_output=True)
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            (checkout / "work.txt").write_text("initial\n")
            git("add", ".")
            git("commit", "-m", "initial")
            git("push", "origin", "main")
            base = git("rev-parse", "HEAD")
            if open_pr:
                git("checkout", "-b", branch)
                (checkout / "prior.txt").write_text("previous attempt\n")
                git("add", ".")
                git("commit", "-m", "previous attempt")
                git("push", "origin", branch)
                git("checkout", "main")
                git("branch", "-D", branch)
            prior_head = git("ls-remote", "origin", f"refs/heads/{branch}")
            url = "https://github.com/KianBahasadri/clusterfork/pull/1"
            pull = {"html_url": url, "state": "open", "merged": False, "title": "Previous work",
                    "user": {"login": "clusterfork[bot]"}, "base": {"ref": "main"},
                    "body": f"<!-- clusterfork-dispatch:{KEY} -->",
                    "head": {"repo": {"full_name": "KianBahasadri/clusterfork"}, "ref": branch,
                             "sha": prior_head.split()[0] if prior_head else base}}
            checks = validation or [[sys.executable, "-c",
                "from pathlib import Path; assert Path('work.txt').read_text() == 'fixed\\n', "
                "'traceback-marker opaque-leased-login ghp_synthetic Bearer opaque-header api_key=opaque-key'"]]

            class Broker:
                key, stage = KEY, "initializing"
                publication_guard = worker.PublicationGuard({})
                def call(inner, action, **data):
                    calls.append((action, data))
                    if data.get("stage"):
                        inner.stage = data["stage"]
                        worker.CONTEXT.set({**worker.CONTEXT.get(), "stage": inner.stage})
                    try:
                        result = service.handle(action, claims(), {"dispatch_key": KEY, **data})
                    except WorkerError as exc:
                        raise HTTPError("https://broker/" + action, exc.status, "rejected", {}, None) from None
                    if action == "start":
                        result.update(validation_commands=checks, auto_merge=False)
                        if open_pr:
                            result["repair"] = {"target_dispatch_key": KEY, "target_identifier": issue["identifier"],
                                                "target_branch": branch,
                                                "instructions": repair_instructions("main")}
                    return result

            class Server:
                closed = False
                def __init__(inner):
                    inner.events = deque()
                    servers.append(inner)
                def call(inner, method, params):
                    if method == "thread/start":
                        return {"thread": {"id": "task-thread"}}
                    test.assertEqual(method, "turn/start")
                    test.assertEqual(params["threadId"], "task-thread")
                    prompts.append(params)
                    turn = f"turn-{len(prompts)}"
                    (checkout / "work.txt").write_text("fixed\n" if len(prompts) >= fix_on_turn else "broken\n")
                    if dirty_file:
                        path = checkout / dirty_file[0]
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(dirty_file[1])
                    if turn_hook:
                        turn_hook(git, checkout, len(prompts))
                    inner.events.extend([
                        {"method": "item/completed", "params": {"threadId": "task-thread", "turnId": turn,
                            "item": {"type": "agentMessage", "text": "Updated work."}}},
                        {"method": "turn/completed", "params": {"threadId": "task-thread",
                            "turn": {"id": turn, "status": "failed" if turn_error else "completed"}}}])
                    return {"turn": {"id": turn}}
                def next_event(inner, timeout=0):
                    return inner.events.popleft() if inner.events else None
                def close(inner):
                    inner.closed = True

            limits = {"rateLimits": {"primary": {"usedPercent": 10, "windowDurationMins": 300},
                                     "secondary": {"usedPercent": 20, "windowDurationMins": 10080}}}
            real_command = worker.command
            def command(args, **kwargs):
                if args[:3] == ["gh", "pr", "checks"]:
                    return "[]"
                if kwargs.get("diagnostic_operation", "").startswith("validation/"):
                    validation_liveness.append(not servers[-1].closed)
                return real_command(args, **kwargs)
            def agent(*args, **kwargs):
                return agent_session.run_agent(*args, **kwargs, server_factory=lambda *a, **kw: Server())
            original, context_token = Path.cwd(), worker.CONTEXT.set({})
            broker = Broker()
            try:
                os.chdir(checkout)
                with patch.object(worker, "run_agent", side_effect=agent), \
                     patch.object(worker, "inspect_account", return_value=({}, limits)), \
                     patch.object(worker, "read_usage", return_value=limits), \
                     patch.object(worker.time, "time", return_value=NOW), \
                     patch.object(worker, "command", side_effect=command), \
                     patch.object(worker, "find_pr", return_value=url if open_pr else None), \
                     patch.object(worker, "json_request", return_value=pull) as request, \
                     patch.object(worker, "request_auto_merge") as merge, \
                     redirect_stdout(io.StringIO()) as output:
                    config = broker.call("start", protocol=PROTOCOL)
                    yield SimpleNamespace(root=root, git=git, broker=broker, service=service, config=config,
                        heartbeat=SimpleNamespace(failed=threading.Event()), branch=branch, base=base,
                        prior_head=prior_head, calls=calls, servers=servers, prompts=prompts, request=request,
                        merge=merge, output=output, validation_liveness=validation_liveness)
            finally:
                os.chdir(original)
                worker.CONTEXT.reset(context_token)

    def test_live_repair_session_receives_private_failure_and_ci_evidence(self):
        with self.scenario(fix_on_turn=1, open_pr=True) as run, \
             patch.object(worker, "repair_check_output", return_value="ci-private-output ghp_synthetic"):
            run.config["repair"]["failure_output"] = "parent-private-output opaque-leased-login"
            self.assertEqual(worker.execute(run.broker, run.config, run.heartbeat, run.root)[0], "completed")
            prompt = run.prompts[0]["input"][0]["text"]
            self.assertIn("parent-private-output", prompt)
            self.assertIn("ci-private-output", prompt)
            self.assertIn("Diagnose", prompt)
            self.assertNotIn("opaque-leased-login", prompt)
            self.assertNotIn("ghp_synthetic", prompt)
            self.assertNotIn("private-output", run.output.getvalue())

    def test_validation_traceback_is_scrubbed_and_corrected_in_the_same_live_session(self):
        with self.scenario() as run, self.assertLogs(level="ERROR") as logs:
            self.assertEqual(worker.execute(run.broker, run.config, run.heartbeat, run.root)[0], "completed")
            self.assertEqual(len(run.servers), 1)
            self.assertEqual(len(run.prompts), 2)
            prompt = run.prompts[1]["input"][0]["text"]
            for value in ("validation/1", "exit 1", "Traceback", "AssertionError", "[REDACTED]", "Do not commit"):
                self.assertIn(value, prompt)
            for value in ("opaque-leased-login", "ghp_synthetic", "opaque-header", "opaque-key"):
                self.assertNotIn(value, prompt)
            self.assertEqual({p["threadId"] for p in run.prompts}, {"task-thread"})
            self.assertEqual(run.validation_liveness, [True, True])
            sessions = [data for action, data in run.calls if action == "steering_open"]
            self.assertEqual([s["turn_id"] for s in sessions], ["turn-1", "turn-2"])
            stages = [data["stage"] for action, data in run.calls if action == "progress" and "stage" in data]
            self.assertEqual(stages, ["selecting_account", "coding", "testing", "coding", "testing", "publishing"])
            self.assertTrue(run.git("ls-remote", "origin", f"refs/heads/{run.branch}"))
            self.assertTrue(run.servers[0].closed)
            self.assertNotIn("traceback-marker", run.output.getvalue() + "\n".join(logs.output))

    def test_exhausted_corrections_preserve_wip_and_finish_with_private_failure_output(self):
        for open_pr in (False, True):
            with self.subTest(open_pr=open_pr), self.scenario(fix_on_turn=99, open_pr=open_pr) as run, \
                 self.assertLogs(level="ERROR") as logs, tempfile.TemporaryDirectory() as directory:
                summary = Path(directory) / "summary"
                with patch.object(worker, "Broker", return_value=run.broker), \
                     patch.dict(os.environ, {"CLUSTERFORK_BROKER_URL": "unused", "DISPATCH_KEY": KEY,
                                             "GITHUB_STEP_SUMMARY": str(summary)}), patch("sys.stderr", io.StringIO()) as stderr:
                    self.assertEqual(worker.main(), 1)
                finish = [data for action, data in run.calls if action == "finish"][-1]
                destination = run.branch.replace("clusterfork/", "clusterfork-wip/", 1) if open_pr else run.branch
                self.assertEqual(finish["wip_branch"], destination)
                sha = run.git("ls-remote", "origin", f"refs/heads/{destination}").split()[0]
                self.assertEqual(finish["wip_commit_sha"], sha)
                self.assertEqual(run.git("show", f"{sha}:work.txt"), "broken")
                self.assertEqual(run.git("show", "-s", "--format=%an <%ae>", sha),
                                 "clusterfork[bot] <clusterfork[bot]@users.noreply.github.com>")
                if open_pr:
                    self.assertEqual(run.git("ls-remote", "origin", f"refs/heads/{run.branch}"), run.prior_head)
                saved = run.service.runs.get(KEY)[0]
                self.assertEqual(saved["wip_branch"], destination)
                tail = json.loads(saved["failure"])["failure_output"]
                self.assertIn("AssertionError", tail)
                self.assertNotIn("opaque-leased-login", tail)
                self.assertEqual(len(run.prompts), 3)
                self.assertEqual(run.validation_liveness, [True, True, True])
                public = run.output.getvalue() + stderr.getvalue() + summary.read_text() + "\n".join(logs.output)
                self.assertNotIn("traceback-marker", public)
                self.assertNotIn("opaque-leased-login", public)
                self.assertEqual(run.git("ls-remote", "origin", "refs/heads/main").split()[0], run.base)
                run.merge.assert_not_called()

    def test_wip_preservation_refuses_credentials_and_unauthorized_workflows(self):
        for path, text in (("tests/live.txt", "opaque-leased-login"), ("config.txt", "ghp_synthetic"),
                           (".github/workflows/new.yml", "name: example\n")):
            with self.subTest(path=path), self.scenario(fix_on_turn=1, dirty_file=(path, text)) as run, \
                 self.assertLogs(level="ERROR"), self.assertRaises(worker.WorkerFailure) as caught:
                try:
                    worker.execute(run.broker, run.config, run.heartbeat, run.root)
                finally:
                    self.assertFalse(run.git("ls-remote", "origin", f"refs/heads/{run.branch}"))
                    self.assertFalse(run.git("ls-remote", "origin", "refs/heads/clusterfork-wip/*"))
            self.assertNotIn("wip_branch", getattr(caught.exception, "clusterfork_wip", {}))

    def test_initial_coding_error_preserves_dirty_work_without_masking_the_failure(self):
        with self.scenario(turn_error=True) as run, self.assertLogs(level="ERROR"), self.assertRaises(CodexError) as caught:
            try:
                worker.execute(run.broker, run.config, run.heartbeat, run.root)
            finally:
                self.assertTrue(run.git("ls-remote", "origin", f"refs/heads/{run.branch}"))
        self.assertEqual(caught.exception.clusterfork_wip["wip_branch"], run.branch)

    def test_wip_can_preserve_workflow_edits_with_the_broker_grant(self):
        with self.scenario(fix_on_turn=99, workflows_allowed=True,
                           dirty_file=(".github/workflows/example.yml", "name: example\n")) as run, \
             self.assertLogs(level="ERROR"), self.assertRaises(worker.WorkerFailure) as caught:
            try:
                worker.execute(run.broker, run.config, run.heartbeat, run.root)
            finally:
                sha = run.git("ls-remote", "origin", f"refs/heads/{run.branch}").split()[0]
                self.assertEqual(run.git("show", f"{sha}:.github/workflows/example.yml"), "name: example")
        self.assertEqual(caught.exception.clusterfork_wip["wip_commit_sha"], sha)

    def test_unrepaired_git_history_or_branch_changes_refuse_wip_publication(self):
        for tamper in ("branch", "head"):
            def change(git, checkout, turn):
                if turn != 1:
                    return
                if tamper == "branch":
                    git("branch", "-m", "unexpected")
                else:
                    git("add", ".")
                    git("commit", "-m", "unexpected agent commit")
            with self.subTest(tamper=tamper), self.scenario(turn_hook=change) as run, \
                 self.assertLogs(level="ERROR"), self.assertRaises(worker.WorkerFailure) as caught:
                try:
                    worker.execute(run.broker, run.config, run.heartbeat, run.root)
                finally:
                    self.assertFalse(run.git("ls-remote", "origin", f"refs/heads/{run.branch}"))
                    self.assertFalse(run.git("ls-remote", "origin", "refs/heads/clusterfork-wip/*"))
            self.assertEqual(caught.exception.clusterfork_failure["reason_code"], "git_integrity_changed")
            self.assertFalse(getattr(caught.exception, "clusterfork_wip", {}))

    def test_integrity_permission_and_publication_failures_are_correctable_before_commit(self):
        for check in ("integrity", "workflow", "publication"):
            def change(git, checkout, turn):
                if check == "integrity":
                    git("branch", "-m", "unexpected" if turn == 1 else "clusterfork/kian-891-aaaaaaaa")
                else:
                    path = checkout / (".github/workflows/generated.yml" if check == "workflow" else "generated.txt")
                    if turn == 1:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text("name: example\n" if check == "workflow" else "opaque-leased-login")
                    else:
                        path.unlink(missing_ok=True)
            with self.subTest(check=check), self.scenario(fix_on_turn=1, turn_hook=change) as run:
                with self.assertLogs(level="ERROR") if check == "publication" else nullcontext():
                    self.assertEqual(worker.execute(run.broker, run.config, run.heartbeat, run.root)[0], "completed")
                self.assertEqual(len(run.prompts), 2)
                self.assertIn({"integrity": "Git history or branch", "workflow": "Workflows write permission",
                               "publication": "credential was detected"}[check], run.prompts[1]["input"][0]["text"])

    def test_unpushed_publication_commit_is_preserved_after_push_failure(self):
        with self.scenario(fix_on_turn=1) as run, self.assertLogs(level="ERROR"), \
             self.assertRaises(worker.WorkerFailure) as caught:
            hook = run.root / "origin.git/hooks/pre-receive"
            marker = run.root / "rejected-once"
            hook.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nimport sys\n"
                            f"marker = Path({str(marker)!r})\n"
                            "if not marker.exists():\n    marker.touch()\n    sys.exit(1)\n")
            hook.chmod(0o755)
            try:
                worker.execute(run.broker, run.config, run.heartbeat, run.root)
            finally:
                self.assertFalse(run.git("status", "--porcelain"))
                self.assertEqual(run.git("rev-list", "--count", "main..HEAD"), "1")
                sha = run.git("ls-remote", "origin", f"refs/heads/{run.branch}").split()[0]
                self.assertEqual(sha, run.git("rev-parse", "HEAD"))
        self.assertEqual(caught.exception.clusterfork_failure["operation"], "command/git")
        self.assertEqual(caught.exception.clusterfork_wip["wip_commit_sha"], sha)

    def test_failed_wip_push_keeps_the_original_validation_failure(self):
        with self.scenario(fix_on_turn=99) as run, self.assertLogs(level="ERROR"), \
             self.assertRaises(worker.WorkerFailure) as caught:
            hook = run.root / "origin.git/hooks/pre-receive"
            hook.write_text("#!/bin/sh\nexit 1\n")
            hook.chmod(0o755)
            try:
                worker.execute(run.broker, run.config, run.heartbeat, run.root)
            finally:
                self.assertFalse(run.git("ls-remote", "origin", f"refs/heads/{run.branch}"))
        self.assertEqual(caught.exception.clusterfork_failure["operation"], "validation/1")
        self.assertIn("AssertionError", caught.exception.clusterfork_failure["failure_output"])
        self.assertFalse(getattr(caught.exception, "clusterfork_wip", {}))

    def test_unpushed_revert_is_preserved_even_when_its_tree_matches_main(self):
        with self.scenario(open_pr=True) as run:
            run.git("checkout", "-b", run.branch, "origin/" + run.branch)
            run.git("rm", "prior.txt")
            run.git("commit", "-m", "revert previous attempt")
            head = run.git("rev-parse", "HEAD")
            self.assertFalse(run.git("diff", "--name-only", "main...HEAD"))
            with worker.command_context(publication_guard=run.broker.publication_guard):
                preserved = worker.preserve_wip(run.broker, run.config, run.root, run.branch, head, KEY)
            destination = run.branch.replace("clusterfork/", "clusterfork-wip/", 1)
            self.assertEqual(preserved, {"wip_branch": destination, "wip_commit_sha": head})
            self.assertEqual(run.git("ls-remote", "origin", f"refs/heads/{destination}").split()[0], head)
            self.assertEqual(run.git("ls-remote", "origin", f"refs/heads/{run.branch}"), run.prior_head)


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

    def test_configured_required_checks_cannot_be_replaced_by_an_unrelated_gate(self):
        self.config["required_checks"] = ["Repository tests"]
        with patch.object(worker, "json_request", side_effect=[self.pull, self.policy]), \
             patch.object(worker, "command", return_value='[{"name":"Other check","bucket":"pass"}]') as command, \
             patch.object(worker.time, "sleep"), self.assertRaises(worker.WorkerFailure):
            self.request()
        self.assertTrue(all(call.args[0][2] == "checks" for call in command.call_args_list))
        with patch.object(worker, "json_request", side_effect=[self.pull, self.policy]), \
             patch.object(worker, "command", side_effect=['[{"name":"Repository tests","bucket":"pending"}]', '']) as command:
            self.request()
        self.assertEqual(command.call_args.args[0][2], "merge")

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
        workflow = (ROOT / ".github/workflows/clusterfork-develop.yml").read_text()
        timeout = int(re.search(r"timeout-minutes:\s*(\d+)", workflow)[1])
        self.assertLessEqual(timeout, 360)
        self.assertGreaterEqual(timeout - 330, 30)


if __name__ == "__main__":
    unittest.main()
