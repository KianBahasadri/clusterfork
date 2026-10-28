"""Behavioral checks for the Azure-to-Actions routing pilot; no cloud credentials."""

import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "automation" / "azure"
sys.path.insert(0, str(SOURCE))
from dispatch_clients import GitHub, Linear, RequestError, request_json  # noqa: E402
from dispatch_core import (Dispatcher, DispatchError, Registry, WebhookError,
                           eligibility, webhook_event)  # noqa: E402

spec = importlib.util.spec_from_file_location("clusterfork_pilot", ROOT / "automation" / "pilot.py")
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)

PROJECT = "374f597c-89f5-4e40-9798-07533f57bfba"
ISSUE = "039a19ab-319b-484e-bbb7-e5337aef6e14"
TODO = "ace01594-2daf-44bb-9690-cb0dccc11795"
OTHER = "cb6fa6ed-110e-4452-ad77-948fbabeb756"
NOW = 1_800_000_000
SECRET = "test-only-webhook-secret"


def issue():
    return {"id": ISSUE, "identifier": "KIAN-123", "archivedAt": None,
            "state": {"id": TODO}, "labels": {"nodes": []},
            "project": {"id": PROJECT, "archivedAt": None,
                        "labels": {"nodes": [{"name": "symphony"}],
                                   "pageInfo": {"hasNextPage": False}}}}


def signed(payload=None):
    if payload is None:
        payload = {"type": "Issue", "action": "create", "data": {"id": ISSUE},
                   "webhookTimestamp": NOW * 1000}
    body = json.dumps(payload, indent=2).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"Linear-Signature": signature}


class Receipts:
    def __init__(self):
        self.rows = {}
        self.lock = threading.Lock()

    def claim(self, key, fields):
        with self.lock:
            if key in self.rows:
                return False
            self.rows[key] = {"status": "dispatching", **fields}
            return True

    def finish(self, key, status):
        self.rows[key]["status"] = status

    def release(self, key):
        del self.rows[key]


class Tracker:
    def __init__(self):
        self.current = issue()
        self.fetched = []

    def issue(self, issue_id):
        self.fetched.append(issue_id)
        return self.current

    def project_issues(self, project_id):
        return [self.current]


class Workflows:
    def __init__(self):
        self.calls = []
        self.failure = None

    def dispatch(self, route, inputs):
        if self.failure:
            raise self.failure
        self.calls.append((route, inputs))


class WebhookTests(unittest.TestCase):
    def test_raw_body_signature_returns_only_routing_ids(self):
        body, headers = signed()
        self.assertEqual(webhook_event(body, headers, SECRET, now=NOW),
                         {"type": "Issue", "id": ISSUE})

    def test_tampering_with_body_is_rejected(self):
        body, headers = signed()
        with self.assertRaises(WebhookError) as error:
            webhook_event(body + b" ", headers, SECRET, now=NOW)
        self.assertEqual(error.exception.status, 401)

    def test_unicode_signature_is_rejected_instead_of_crashing(self):
        body, _ = signed()
        with self.assertRaises(WebhookError) as error:
            webhook_event(body, {"linear-signature": "\u00e9"}, SECRET, now=NOW)
        self.assertEqual(error.exception.status, 401)

    def test_past_and_future_replays_are_rejected(self):
        body, headers = signed()
        for now in (NOW - 301, NOW + 301):
            with self.subTest(now=now), self.assertRaises(WebhookError) as error:
                webhook_event(body, headers, SECRET, now=now)
            self.assertEqual(error.exception.status, 401)

    def test_missing_secret_fails_closed(self):
        body, headers = signed()
        with self.assertRaises(WebhookError) as error:
            webhook_event(body, headers, "", now=NOW)
        self.assertEqual(error.exception.status, 503)

    def test_removed_issues_and_unrelated_events_are_ignored(self):
        for kind, action in [("Issue", "remove"), ("Comment", "create")]:
            body, headers = signed({"type": kind, "action": action, "data": {"id": ISSUE},
                                    "webhookTimestamp": NOW * 1000})
            self.assertIsNone(webhook_event(body, headers, SECRET, now=NOW))

    def test_malformed_signed_payloads_do_not_get_queued(self):
        for payload in ([1], {"webhookTimestamp": True},
                        {"type": "Issue", "action": "create", "data": {"id": "bad"},
                         "webhookTimestamp": NOW * 1000}):
            body, headers = signed(payload)
            with self.subTest(payload=payload), self.assertRaises(WebhookError) as error:
                webhook_event(body, headers, SECRET, now=NOW)
            self.assertEqual(error.exception.status, 400)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.registry = Registry.load(SOURCE / "projects.json")
        self.tracker, self.github, self.receipts = Tracker(), Workflows(), Receipts()
        self.dispatcher = Dispatcher(self.registry, self.tracker, self.github, self.receipts)
        self.event = {"type": "Issue", "id": ISSUE}

    def test_signed_linear_event_reaches_configured_repository(self):
        event = webhook_event(*signed(), SECRET, now=NOW)
        result = self.dispatcher.process(event)[0]
        self.assertEqual(result["status"], "dispatched")
        route, inputs = self.github.calls[0]
        self.assertEqual(route.repository, "KianBahasadri/clusterfork")
        self.assertEqual(route.ref, "main")
        self.assertEqual(inputs["linear_issue_id"], ISSUE)
        self.assertEqual(inputs["linear_project_id"], PROJECT)
        self.assertEqual(self.receipts.rows[result["dispatch_key"]]["status"], "dispatched")

    def test_issue_label_cannot_enroll_project(self):
        self.tracker.current["project"]["labels"]["nodes"] = []
        self.tracker.current["labels"]["nodes"] = [{"name": "symphony"}]
        self.assertEqual(self.dispatcher.process(self.event)[0]["status"], "project_not_enrolled")
        self.assertEqual(self.github.calls, [])

    def test_unmapped_projects_backlog_and_archived_work_do_not_run(self):
        mutations = [(lambda row: row["project"].update(id=OTHER), "unmapped_project"),
                     (lambda row: row["state"].update(id=OTHER), "not_ready"),
                     (lambda row: row.update(archivedAt="2026-01-01"), "archived"),
                     (lambda row: row["project"].update(archivedAt="2026-01-01"), "archived")]
        for mutate, reason in mutations:
            row = issue()
            mutate(row)
            with self.subTest(reason=reason):
                self.assertEqual(eligibility(row, self.registry)[1], reason)

    def test_each_event_fetches_current_linear_data(self):
        self.tracker.current["state"]["id"] = OTHER
        self.assertEqual(self.dispatcher.process(self.event)[0]["status"], "not_ready")
        self.assertEqual(self.tracker.fetched, [ISSUE])
        self.assertEqual(self.github.calls, [])

    def test_duplicate_and_concurrent_events_start_one_run(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.dispatcher.process, [self.event] * 24))
        self.assertEqual(len(self.github.calls), 1)
        self.assertEqual(sum(r[0]["status"] == "dispatched" for r in results), 1)

    def test_project_enrollment_scan_and_later_issue_event_share_claim(self):
        self.assertEqual(self.dispatcher.process({"type": "Project", "id": PROJECT})[0]["status"],
                         "dispatched")
        self.assertEqual(self.dispatcher.process(self.event)[0]["status"], "already_claimed")
        self.assertEqual(len(self.github.calls), 1)

    def test_rejected_dispatch_can_retry_after_configuration_is_fixed(self):
        self.github.failure = DispatchError("not installed")
        with self.assertRaises(DispatchError):
            self.dispatcher.process(self.event)
        self.assertEqual(self.receipts.rows, {})
        self.github.failure = None
        self.assertEqual(self.dispatcher.process(self.event)[0]["status"], "dispatched")

    def test_ambiguous_timeout_does_not_launch_duplicate_on_retry(self):
        self.github.failure = DispatchError("timed out", uncertain=True)
        result = self.dispatcher.process(self.event)[0]
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.receipts.rows[result["dispatch_key"]]["status"], "uncertain")
        self.github.failure = None
        self.assertEqual(self.dispatcher.process(self.event)[0]["status"], "already_claimed")
        self.assertEqual(self.github.calls, [])

    def test_failed_receipt_write_does_not_allow_duplicate_dispatch(self):
        with patch.object(self.receipts, "finish", side_effect=RuntimeError("storage down")):
            with self.assertRaises(RuntimeError):
                self.dispatcher.process(self.event)
        self.assertEqual(self.dispatcher.process(self.event)[0]["status"], "already_claimed")
        self.assertEqual(len(self.github.calls), 1)


class ClientTests(unittest.TestCase):
    def test_all_project_pages_are_scanned(self):
        calls = []

        def request(url, **kwargs):
            after = kwargs["data"]["variables"]["after"]
            calls.append(after)
            return {"data": {"project": {**issue()["project"], "issues": {"nodes": [issue()],
                     "pageInfo": {"hasNextPage": after is None, "endCursor": "next"}}}}}

        rows = list(Linear("test", request).project_issues(PROJECT))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["project"], issue()["project"])
        self.assertEqual(calls, [None, "next"])

    def test_graphql_errors_never_become_a_successful_empty_scan(self):
        client = Linear("test", lambda *a, **kw: {"errors": [{"message": "private response"}]})
        with self.assertRaises(RequestError) as error:
            client.issue(ISSUE)
        self.assertNotIn("private response", str(error.exception))

    def test_truncated_project_labels_fail_closed(self):
        row = issue()
        row["project"]["labels"]["pageInfo"]["hasNextPage"] = True
        with self.assertRaises(ValueError):
            Linear.complete(row)

    def test_workflow_dispatch_uses_mapped_repository_and_ref(self):
        calls = []
        client = GitHub("1", "unused", lambda url, **kw: calls.append((url, kw)))
        route = Registry.load(SOURCE / "projects.json").routes[PROJECT]
        with patch.object(client, "installation_token", return_value="test-token"):
            client.dispatch(route, {"linear_issue_id": ISSUE})
        self.assertEqual(calls[0][0], "https://api.github.com/repos/KianBahasadri/clusterfork/actions/workflows/clusterfork-worker.yml/dispatches")
        self.assertEqual(calls[0][1]["data"]["ref"], "main")

    def test_only_ambiguous_dispatch_errors_keep_claims(self):
        route = Registry.load(SOURCE / "projects.json").routes[PROJECT]
        for status, uncertain in [(None, True), (502, True), (401, False), (404, False), (429, False)]:
            client = GitHub("1", "unused")
            with self.subTest(status=status), \
                 patch.object(client, "installation_token", return_value="test-token"), \
                 patch.object(client, "request", side_effect=RequestError(status)), \
                 self.assertRaises(DispatchError) as error:
                client.dispatch(route, {})
            self.assertEqual(error.exception.uncertain, uncertain)

    def test_transport_errors_never_log_server_bodies_or_credentials(self):
        error = HTTPError("https://example.com", 403, "sensitive-server-body", {}, None)
        with patch("dispatch_clients.urlopen", side_effect=error), self.assertRaises(RequestError) as caught:
            request_json("https://example.com", token="secret-value")
        self.assertEqual(str(caught.exception), "remote request failed (HTTP 403)")


class SetupTests(unittest.TestCase):
    def run_check(self, data):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "projects.json").write_text(json.dumps(data))
            key = source / "app.pem"
            key.write_text("local check only; not a live private key")
            env_file = source / ".env"
            env_file.write_text("LINEAR_API_KEY=check-only-api-key\nGITHUB_APP_ID=123\n"
                                f"GITHUB_APP_PRIVATE_KEY_FILE='{key}'\n")
            output = io.StringIO()
            with patch.object(pilot, "SOURCE", source), \
                 patch.object(sys, "argv", ["pilot.py", "check", "--env-file", str(env_file)]), \
                 patch.dict(os.environ, {}, clear=True), \
                 patch.object(pilot.subprocess, "run", side_effect=AssertionError("unexpected external command")), \
                 redirect_stdout(output):
                result = pilot.main()
        return result, output.getvalue()

    def test_check_reports_configured_codex_worker_without_launching_it(self):
        data = json.loads((SOURCE / "projects.json").read_text())
        result, output = self.run_check(data)
        self.assertEqual(result, 0)
        self.assertIn("Mode: Codex worker\n", output)
        self.assertIn("Agent profile: codex -> gpt-6.1-sol (effort: max)", output)
        self.assertIn("local configuration only", output)
        self.assertIn("does not launch a worker or verify a live agent login", output)
        self.assertNotIn("routing pilot (no coding agent)", output)
        self.assertNotIn("check-only-api-key", output)
        for route in data["projects"]:
            self.assertIn(f"Mapping: {route['project_id']} -> {route['repository']} "
                          f"({route['workflow']} @ {route['ref']})", output)

    def test_check_mode_uses_all_workflow_mappings(self):
        cases = [
            (["clusterfork-pilot.yml"], "routing pilot (no coding agent)"),
            (["clusterfork-pilot.yml", "clusterfork-pilot.yml"], "routing pilot (no coding agent)"),
            (["clusterfork-pilot.yml", "clusterfork-worker.yml"], "Codex worker (with other workflow mappings)"),
            (["clusterfork-worker.yml", "clusterfork-pilot.yml"], "Codex worker (with other workflow mappings)"),
            (["custom.yml"], "custom workflow mappings"),
            ([], "no workflow mappings"),
        ]
        for workflows, mode in cases:
            with self.subTest(workflows=workflows):
                data = json.loads((SOURCE / "projects.json").read_text())
                route = data["projects"][0]
                data["projects"] = [dict(route, project_id=project, workflow=workflow)
                                    for project, workflow in zip((PROJECT, OTHER), workflows)]
                result, output = self.run_check(data)
                self.assertEqual(result, 0)
                self.assertIn(f"Mode: {mode}\n", output)
                for workflow in workflows:
                    self.assertIn(f"({workflow} @ main)", output)

    def test_azure_hostname_supports_flat_and_arm_response_shapes(self):
        for response in [{"defaultHostName": "pilot.azurewebsites.net"},
                         {"properties": {"defaultHostName": "pilot.azurewebsites.net"}}]:
            with self.subTest(response=response):
                self.assertEqual(pilot.function_hostname(response), "pilot.azurewebsites.net")
        with self.assertRaisesRegex(ValueError, "hostname"):
            pilot.function_hostname({"properties": {}})

    def test_dotenv_parser_reads_literals_without_shell_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("LINEAR_API_KEY='$(this must not execute)'\nGITHUB_APP_ID=\nOTHER=$(ignored)\n")
            with patch.dict(os.environ, {}, clear=True):
                values = pilot.settings(path)
            self.assertEqual(values, {"LINEAR_API_KEY": "$(this must not execute)", "GITHUB_APP_ID": ""})

    def test_blank_credentials_are_reported_without_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("LINEAR_API_KEY=\nGITHUB_APP_ID=\n")
            result = subprocess.run([sys.executable, str(ROOT / "automation" / "pilot.py"),
                                     "check", "--env-file", str(path)],
                                    capture_output=True, text=True, env={"PATH": os.environ["PATH"]})
        self.assertEqual(result.returncode, 1)
        self.assertIn("Required before deployment", result.stdout)

    def test_deployment_archive_contains_only_allowed_source_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pilot.zip"
            pilot.package(path)
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                self.assertEqual(names, set(pilot.PACKAGE_FILES))
                self.assertIn("host.json", names)
                self.assertFalse(any(name.endswith((".pem", ".env")) for name in names))

    def test_duplicate_project_mappings_are_rejected(self):
        data = json.loads((SOURCE / "projects.json").read_text())
        data["projects"].append(copy.deepcopy(data["projects"][0]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "duplicate"):
                Registry.load(path)


if __name__ == "__main__":
    unittest.main()
