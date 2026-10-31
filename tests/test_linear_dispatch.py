"""Behavioral checks for the Azure-to-Actions routing pilot; no cloud credentials."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "automation" / "controller"
sys.path.insert(0,str(ROOT/"automation"))
from common.clients import GitHub, Linear, RequestError, request_json  # noqa: E402
from common.routing import DispatchError, Registry, WebhookError, dependency_status, webhook_event
from call_budget import record_api_calls

from common import configuration as pilot

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
                        "labels": {"nodes": [{"name": "clusterfork-symphony"}],
                                   "pageInfo": {"hasNextPage": False}}}}


def signed(payload=None):
    if payload is None:
        payload = {"type": "Issue", "action": "create", "data": {"id": ISSUE},
                   "webhookTimestamp": NOW * 1000}
    body = json.dumps(payload, indent=2).encode()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, {"Linear-Signature": signature}


class Tracker:
    def __init__(self):
        self.current = issue()
        self.fetched = []
        record_api_calls(self)

    def issue(self, issue_id):
        self.fetched.append(issue_id)
        return self.current

    def issue_dependencies(self, issue_id):
        return []

    def project_issues(self, project_id):
        return [self.current]

    def status_comment(self, *args):
        pass

    def set_execution_status(self, issue_id, name):
        pass


class Workflows:
    def __init__(self):
        self.calls = []
        self.failure = None
        record_api_calls(self)

    def dispatch(self, route, inputs):
        if self.failure:
            raise self.failure
        self.calls.append((route, inputs))

    def dispatch_runs(self, route, key):
        return [{"id": 1, "run_attempt": 1, "status": "in_progress"}] if self.calls else []

    def worker_pull(self, *args):
        return None


class WebhookTests(unittest.TestCase):
    def test_only_a_signed_human_state_transition_retains_submission_evidence(self):
        payload = {'type': 'Issue', 'action': 'update', 'webhookTimestamp': NOW*1000,
                   'createdAt': '2026-10-31T00:00:01Z', 'actor': {'type': 'user', 'id': OTHER},
                   'updatedFrom': {'stateId': OTHER}, 'data': {'id': ISSUE, 'stateId': TODO}}
        event = webhook_event(*signed(payload), SECRET, now=NOW)
        self.assertEqual(event['submission']['actor_id'], OTHER)
        self.assertEqual(event['submission']['to_state'], TODO)
        first = event['submission']
        payload['webhookTimestamp'] += 1000
        self.assertEqual(webhook_event(*signed(payload), SECRET, now=NOW)['submission'], first)
        for change in ({'actor': {'type': 'integration', 'id': OTHER}},
                       {'actor': None}, {'updatedFrom': {'labelIds': []}},
                       {'updatedFrom': {'stateId': TODO}}, {'type': 'Comment'},
                       {'createdAt': 'invalid'}, {'actor': {'type': 'user', 'id': OTHER, 'isBot': True}}):
            with self.subTest(change=change):
                self.assertNotIn('submission', webhook_event(*signed({**payload, **change}), SECRET, now=NOW))

    def test_dependency_wakeup_events_keep_only_ids_and_always_require_fresh_reads(self):
        event = webhook_event(*signed({"type": "IssueRelation", "action": "remove", "webhookTimestamp": NOW * 1000,
                                       "data": {"id": OTHER, "issueId": ISSUE, "relatedIssueId": TODO,
                                                "type": "blocks", "title": "private"}}), SECRET, now=NOW)
        self.assertEqual(event, {"type": "IssueRelation", "id": OTHER, "issue_ids": [ISSUE, TODO], "webhook_at": NOW})
        removed = webhook_event(*signed({"type": "Issue", "action": "remove", "data": {"id": ISSUE},
                                         "webhookTimestamp": NOW * 1000}), SECRET, now=NOW)
        self.assertEqual(removed["type"], "Issue")

    def test_raw_body_signature_returns_routing_ids_and_timestamp(self):
        body, headers = signed()
        self.assertEqual(webhook_event(body, headers, SECRET, now=NOW),
                         {"type": "Issue", "id": ISSUE, "webhook_at": NOW})

    def test_source_timestamp_is_diagnostic_without_copying_issue_content(self):
        payload = {"type": "Issue", "action": "update", "webhookTimestamp": NOW * 1000,
                   "data": {"id": ISSUE, "updatedAt": "2026-10-29T18:23:05Z",
                            "title": "private title", "description": "private description"}}
        event = webhook_event(*signed(payload), SECRET, now=NOW)
        self.assertEqual(event, {"type": "Issue", "id": ISSUE, "webhook_at": NOW,
                                 "source_updated_at": 1793298185.0})
        for updated in (None, 12, "bad", "2026-10-29T18:23:05"):
            with self.subTest(updated=updated):
                payload["data"]["updatedAt"] = updated
                self.assertEqual(webhook_event(*signed(payload), SECRET, now=NOW),
                                 {"type": "Issue", "id": ISSUE, "webhook_at": NOW})

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
        for kind, action in [("Project", "remove"), ("Reaction", "create"), ("Comment", "remove")]:
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




class ClientTests(unittest.TestCase):
    def test_issue_creation_recovers_committed_and_conflicting_creates_using_same_uuid(self):
        stored = {}
        inputs = {"id": ISSUE, "teamId": OTHER, "projectId": PROJECT, "parentId": TODO,
                  "title": "Auto-repair: KIAN-1 (worker failed)", "stateId": TODO,
                  "priority": 2, "labelIds": [OTHER], "description": "Sanitized failure context"}
        lost = True
        def request(url, **kwargs):
            nonlocal lost
            query, variables = kwargs["data"]["query"], kwargs["data"]["variables"]
            if "issueCreate(" in query:
                fields = variables["input"]
                self.assertEqual(fields, inputs)
                if fields["id"] not in stored:
                    stored[fields["id"]] = {**issue(), "title": fields["title"]}
                if lost:
                    lost = False
                    raise RequestError(503)
                return {"errors": [{"extensions": {"code": "BAD_USER_INPUT"}}]}
            return {"data": {"issue": stored.get(variables["id"])}}
        self.assertEqual(Linear("test", request).create_issue(inputs)["id"], ISSUE)
        with self.assertLogs(level="ERROR"):
            self.assertEqual(Linear("test", request).create_issue(inputs)["id"], ISSUE)
        self.assertEqual(len(stored), 1)

    def test_failed_issue_creation_does_not_invent_another_issue_id(self):
        def request(url, **kwargs):
            if "issueCreate(" in kwargs["data"]["query"]:
                raise RequestError(503)
            return {"data": {"issue": None}}
        with self.assertRaises(RequestError):
            Linear("test", request).create_issue({"id": ISSUE})

    def test_truncated_repair_labels_fail_closed(self):
        row = issue()
        row["labels"]["pageInfo"] = {"hasNextPage": True}
        with self.assertRaises(ValueError):
            Linear.complete(row)

    def test_incoming_dependency_pages_include_cross_project_and_archived_sources(self):
        calls = []
        completed = {"id": ISSUE, "identifier": "KIAN-905", "trashed": None,
                     "state": {"type": "completed", "name": "Shipped"}}
        unfinished = {"id": OTHER, "identifier": "OTHER-1", "trashed": False,
                      "state": {"type": "started", "name": "Done"}}
        pages = [
            [{"id": TODO, "type": "related", "issue": unfinished}],
            [{"id": TODO, "type": "blocks", "issue": completed}],
            [{"id": TODO, "type": "blocks", "issue": unfinished}],
        ]

        def request(url, **kwargs):
            query, variables = kwargs["data"]["query"], kwargs["data"]["variables"]
            self.assertIn("inverseRelations(first: 50, after: $after, includeArchived: true)", query)
            self.assertNotIn("project", query)
            self.assertNotIn("filter", query)
            self.assertIn("trashed", query)
            calls.append(variables)
            page = 0 if variables["after"] is None else int(variables["after"])
            return {"data": {"issue": {"inverseRelations": {"nodes": pages[page], "pageInfo": {
                "hasNextPage": page < 2, "endCursor": str(page + 1)}}}}}

        rows = Linear("test", request).issue_dependencies(TODO)
        ids, reason = dependency_status(rows)
        self.assertEqual([call["after"] for call in calls], [None, "1", "2"])
        self.assertEqual({call["id"] for call in calls}, {TODO})
        self.assertEqual(set(json.loads(ids)), {ISSUE, OTHER})
        self.assertIn("OTHER-1 (started)", reason)
        self.assertNotIn("KIAN-905", reason)
        self.assertEqual(dependency_status(rows[:-1])[1], "")

    def test_incomplete_dependency_connections_and_missing_issue_fail_closed(self):
        for data in ({"issue": None}, {"issue": {}},
                     {"issue": {"inverseRelations": {"nodes": None, "pageInfo": {"hasNextPage": False}}}},
                     {"issue": {"inverseRelations": {"nodes": [], "pageInfo": {}}}},
                     {"issue": {"inverseRelations": {"nodes": [], "pageInfo": {"hasNextPage": "false"}}}}):
            with self.subTest(data=data), self.assertRaises((ValueError, KeyError)):
                Linear("test", lambda *a, **kw: {"data": data}).issue_dependencies(ISSUE)

    def test_dependency_pagination_rejects_missing_repeated_and_cyclic_cursors(self):
        for cursors in ([None], [""], ["one", "one"], ["one", "two", "one"]):
            calls = []

            def request(url, **kwargs):
                calls.append(kwargs["data"]["variables"]["after"])
                return {"data": {"issue": {"inverseRelations": {"nodes": [], "pageInfo": {
                    "hasNextPage": True, "endCursor": cursors[len(calls) - 1]}}}}}

            with self.subTest(cursors=cursors), self.assertRaises(ValueError):
                Linear("test", request).issue_dependencies(ISSUE)
            self.assertEqual(len(calls), len(cursors))

    def test_error_on_later_dependency_page_does_not_return_a_partial_pass(self):
        responses = iter([
            {"data": {"issue": {"inverseRelations": {"nodes": [], "pageInfo": {
                "hasNextPage": True, "endCursor": "next"}}}}},
            {"data": {"issue": None}, "errors": [{"message": "inaccessible source"}]},
        ])
        with self.assertRaises(RequestError):
            Linear("test", lambda *a, **kw: next(responses)).issue_dependencies(ISSUE)

    def test_issue_fetch_requests_and_preserves_priority_and_sort_order(self):
        def request(url, **kwargs):
            self.assertRegex(kwargs["data"]["query"], r"\bpriority\b")
            self.assertRegex(kwargs["data"]["query"], r"\bsortOrder\b")
            return {"data": {"issue": {**issue(), "priority": 2, "sortOrder": -1.75}}}

        row = Linear("test", request).issue(ISSUE)
        self.assertEqual(row["priority"], 2)
        self.assertEqual(row["sortOrder"], -1.75)

    def test_all_project_pages_are_scanned(self):
        calls = []

        def request(url, **kwargs):
            self.assertRegex(kwargs["data"]["query"], r"\bpriority\b")
            self.assertRegex(kwargs["data"]["query"], r"\bsortOrder\b")
            after = kwargs["data"]["variables"]["after"]
            calls.append(after)
            row = {**issue(), "priority": 1 if after is None else 4,
                   "sortOrder": 1.75 if after is None else -2.5}
            return {"data": {"project": {**issue()["project"], "issues": {"nodes": [row],
                     "pageInfo": {"hasNextPage": after is None, "endCursor": "next"}}}}}

        rows = list(Linear("test", request).project_issues(PROJECT))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["project"], issue()["project"])
        self.assertEqual([row["priority"] for row in rows], [1, 4])
        self.assertEqual([row["sortOrder"] for row in rows], [1.75, -2.5])
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
        self.assertEqual(calls[0][0], "https://api.github.com/repos/KianBahasadri/clusterfork/actions/workflows/clusterfork-task.yml/dispatches")
        self.assertEqual(calls[0][1]["data"]["ref"], "main")

    def test_dispatch_run_listing_paginates_and_observes_latest_manual_attempt(self):
        route = Registry.load(SOURCE / "projects.json").routes[PROJECT]
        key = "a" * 64
        run = {"id": 123, "run_attempt": 4, "status": "in_progress", "display_title": key,
               "event": "workflow_dispatch", "path": ".github/workflows/clusterfork-task.yml",
               "actor": {"login": "clusterfork[bot]"}, "repository": {"full_name": route.repository}}
        calls = []
        def request(url, **kwargs):
            calls.append(url)
            return {"total_count": 101, "workflow_runs": ([{"display_title": "unrelated"}] * 100
                                                          if "&page=1" in url else [run])}
        client = GitHub("1", "unused", request)
        with patch.object(client, "installation_token", return_value="test-token"):
            self.assertEqual(client.dispatch_runs(route, key), [run])
        self.assertEqual(len(calls), 2)
        self.assertFalse(any("status=" in url or "event=" in url or "created=" in url for url in calls))

    def test_partial_or_untrusted_run_listings_cannot_prove_completion(self):
        route = Registry.load(SOURCE / "projects.json").routes[PROJECT]
        key = "a" * 64
        for response in ({"total_count": 3, "workflow_runs": []},
                         {"total_count": 1, "workflow_runs": [{"display_title": key, "event": "push"}]}):
            client = GitHub("1", "unused", lambda *a, **kw: response)
            with self.subTest(response=response), \
                 patch.object(client, "installation_token", return_value="test-token"), \
                 self.assertRaises(RequestError):
                client.dispatch_runs(route, key)

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
            if status is not None:
                self.assertEqual(error.exception.clusterfork_failure['http_status'], status)
                self.assertEqual(error.exception.clusterfork_failure['kind'], 'http_error')

    def test_transport_errors_never_log_server_bodies_or_credentials(self):
        error = HTTPError("https://example.com", 403, "sensitive-server-body", {}, None)
        with patch("common.clients.urlopen", side_effect=error), self.assertRaises(RequestError) as caught:
            request_json("https://example.com", token="secret-value")
        self.assertEqual(str(caught.exception), "remote request failed (HTTP 403)")






class LinearOAuthTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.exchange = Mock(return_value=self.credentials())
        self.graphql = Mock(return_value={"data": {"issue": issue()}})
        self.client = self.client_credentials()
        self.transport = patch("common.clients.urlopen", side_effect=self.urlopen)
        self.open = self.transport.start()
        self.addCleanup(self.transport.stop)

    @staticmethod
    def credentials(token="test-only-access-token", expires_in=3600):
        return {"access_token": token, "token_type": "Bearer", "expires_in": expires_in}

    def client_credentials(self):
        return Linear("test-only-api-key", client_id="test-client-id", client_secret="test-only-client&secret=+",
                      clock=lambda: self.now)

    def urlopen(self, request, *, timeout):
        self.assertEqual(timeout, 20)
        if request.full_url == "https://api.linear.app/oauth/token":
            result = self.exchange(request)
        else:
            self.assertEqual(request.full_url, "https://api.linear.app/graphql")
            result = self.graphql(request)
        return io.BytesIO(result if isinstance(result, bytes) else json.dumps(result).encode())

    def headers(self):
        return [call.args[0].get_header("Authorization") for call in self.graphql.call_args_list]

    def test_client_credentials_exchange_prefers_oauth_and_reuses_token(self):
        for _ in range(2):
            self.assertEqual(self.client.issue(ISSUE), issue())
        self.exchange.assert_called_once()
        request = self.exchange.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual(parse_qs(request.data.decode()), {
            "grant_type": ["client_credentials"], "client_id": ["test-client-id"],
            "client_secret": ["test-only-client&secret=+"],
            "scope": ["read,write,comments:create,issues:create"]})
        self.assertEqual(self.headers(), ["Bearer test-only-access-token"] * 2)
        self.assertTrue(all(call.args[0].get_header("Content-type") == "application/json"
                            for call in self.graphql.call_args_list))

    def test_token_refreshes_five_minutes_before_reported_expiry(self):
        self.client.issue(ISSUE)
        self.now += 3299
        self.client.issue(ISSUE)
        self.exchange.assert_called_once()
        self.now += 1
        self.exchange.return_value = self.credentials("test-refreshed-token")
        self.client.issue(ISSUE)
        self.assertEqual(self.exchange.call_count, 2)
        self.assertEqual(self.headers(), ["Bearer test-only-access-token"] * 2 + ["Bearer test-refreshed-token"])

    def test_short_lived_tokens_are_reused_and_refreshed_before_expiry(self):
        self.exchange.return_value = self.credentials(expires_in=120)
        self.client.issue(ISSUE)
        self.now += 59
        self.client.issue(ISSUE)
        self.exchange.assert_called_once()
        self.now += 1
        self.client.issue(ISSUE)
        self.assertEqual(self.exchange.call_count, 2)

    def test_concurrent_graphql_calls_share_one_token(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.client.issue, [ISSUE] * 24))
        self.assertEqual(results, [issue()] * 24)
        self.exchange.assert_called_once()
        self.assertEqual(self.headers(), ["Bearer test-only-access-token"] * 24)

    def test_unauthorized_graphql_refreshes_and_retries_the_same_request_once(self):
        self.exchange.side_effect = [self.credentials(), self.credentials("test-refreshed-token")]
        self.graphql.side_effect = [HTTPError("https://api.linear.app/graphql", 401, "unauthorized", {}, None),
                                    {"data": {"issue": issue()}}, {"data": {"issue": issue()}}]
        with self.assertLogs(level="ERROR"):
            self.assertEqual(self.client.issue(ISSUE), issue())
        self.client.issue(ISSUE)
        self.assertEqual(self.exchange.call_count, 2)
        self.assertEqual(self.headers(), ["Bearer test-only-access-token"] + ["Bearer test-refreshed-token"] * 2)
        self.assertEqual(self.graphql.call_args_list[0].args[0].data, self.graphql.call_args_list[1].args[0].data)

    def test_second_unauthorized_response_stops_and_discards_rejected_token(self):
        self.graphql.side_effect = HTTPError("https://api.linear.app/graphql", 401, "unauthorized", {}, None)
        with self.assertLogs(level="ERROR"), self.assertRaises(RequestError) as caught:
            self.client.issue(ISSUE)
        self.assertEqual(caught.exception.status, 401)
        self.assertEqual(self.exchange.call_count, 2)
        self.assertEqual(self.graphql.call_count, 2)
        self.graphql.side_effect = None
        self.client.issue(ISSUE)
        self.assertEqual(self.exchange.call_count, 3)

    def test_api_key_fallback_when_either_oauth_credential_is_missing(self):
        for client_id, client_secret in ((None, None), ("test-client-id", None),
                                         (None, "test-only-client-secret"), ("", "test-only-client-secret"),
                                         ("test-client-id", "")):
            with self.subTest(client_id=bool(client_id), client_secret=bool(client_secret)):
                self.graphql.reset_mock()
                client = Linear("test-only-api-key", client_id=client_id, client_secret=client_secret)
                self.assertEqual(client.issue(ISSUE), issue())
                self.assertEqual(self.headers(), ["test-only-api-key"])
                self.exchange.assert_not_called()

    def test_api_key_unauthorized_response_is_not_retried(self):
        self.graphql.side_effect = HTTPError("https://api.linear.app/graphql", 401, "unauthorized", {}, None)
        with self.assertLogs(level="ERROR"), self.assertRaises(RequestError):
            Linear("test-only-api-key").issue(ISSUE)
        self.exchange.assert_not_called()
        self.graphql.assert_called_once()

    def test_other_graphql_failures_do_not_refresh_or_retry(self):
        for status in (403, 429, 500, 503):
            with self.subTest(status=status):
                self.graphql.reset_mock()
                self.exchange.reset_mock()
                self.graphql.side_effect = HTTPError("https://api.linear.app/graphql", status, "failed", {}, None)
                with self.assertLogs(level="ERROR"), self.assertRaises(RequestError) as caught:
                    self.client_credentials().issue(ISSUE)
                self.assertEqual(caught.exception.status, status)
                self.exchange.assert_called_once()
                self.graphql.assert_called_once()

    def test_token_exchange_and_graphql_failures_redact_secrets_and_response_bodies(self):
        secrets = ("test-only-client&secret=+", "test-only-access-token", "test-only-api-key", "private-response-body")
        private = " ".join(secrets)
        for endpoint in ("token", "graphql"):
            for mode in ("http", "transport", "json", "response"):
                with self.subTest(endpoint=endpoint, mode=mode):
                    self.exchange.reset_mock(side_effect=True)
                    self.graphql.reset_mock(side_effect=True)
                    self.exchange.return_value = self.credentials()
                    self.graphql.return_value = {"data": {"issue": issue()}}
                    target = self.exchange if endpoint == "token" else self.graphql
                    if mode == "http":
                        target.side_effect = HTTPError("https://api.linear.app/" + endpoint, 403, private,
                                                       {"Private": private}, io.BytesIO(private.encode()))
                    elif mode == "transport":
                        target.side_effect = URLError(private)
                    elif mode == "json":
                        target.return_value = private.encode()
                    else:
                        target.return_value = {"errors": [{"message": private}]}
                    with self.assertLogs(level="INFO") as logs, self.assertRaises(RequestError) as caught:
                        self.client_credentials().issue(ISSUE)
                    rendered = ("\n".join(logs.output) + str(caught.exception) + repr(caught.exception)
                                + json.dumps(getattr(caught.exception, "clusterfork_failure", {}))
                                + "".join(traceback.format_exception(caught.exception)))
                    for secret in secrets:
                        self.assertNotIn(secret, rendered)
                    self.exchange.assert_called_once()
                    self.assertEqual(self.graphql.call_count, 0 if endpoint == "token" else 1)

    def test_malformed_token_responses_fail_closed_without_exposing_values(self):
        for response in (None, [], {"access_token": "test-only-access-token"},
                         self.credentials(token=""), self.credentials(token="test-only-access-token\r\nprivate"),
                         self.credentials(expires_in="private-expiry"), self.credentials(expires_in=True),
                         self.credentials(expires_in=0), self.credentials(expires_in=-1),
                         self.credentials(expires_in=float("nan")), self.credentials(expires_in=float("inf"))):
            with self.subTest(response_type=type(response).__name__):
                self.exchange.return_value = response
                with self.assertRaises(RequestError) as caught:
                    self.client_credentials().issue(ISSUE)
                self.assertEqual(str(caught.exception), "remote request failed (HTTP unavailable)")
                self.graphql.assert_not_called()


class SetupTests(unittest.TestCase):
    def run_check(self, data, linear_config="LINEAR_API_KEY=check-only-api-key\n"):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / "projects.json").write_text(json.dumps(data))
            key = source / "app.pem"
            key.write_text("local check only; not a live private key")
            env_file = source / ".env"
            env_file.write_text(linear_config + "GITHUB_APP_ID=123\n"
                                f"GITHUB_APP_PRIVATE_KEY_FILE='{key}'\n")
            output = io.StringIO()
            with patch.dict(os.environ, {}, clear=True):
                missing = pilot.check(pilot.settings(env_file))
                result = int(bool(missing))
                print("Required before deployment" if missing else "Configured", file=output)
                for name in pilot.CONFIG_NAMES:
                    print(f"{name}: configured" if name in pilot.settings(env_file) else f"{name}: missing", file=output)
        return result, output.getvalue()

    def test_check_accepts_either_linear_authentication_mode_and_rejects_partial_oauth(self):
        data = json.loads((SOURCE / "projects.json").read_text())
        oauth = "LINEAR_OAUTH_CLIENT_ID=check-client-id\nLINEAR_OAUTH_CLIENT_SECRET=check-only-client-secret\n"
        key = "LINEAR_API_KEY=check-only-api-key\n"
        for config, valid in ((oauth, True), (key, True), (oauth + key, True),
                              ("LINEAR_OAUTH_CLIENT_ID=check-client-id\n", False),
                              ("LINEAR_OAUTH_CLIENT_SECRET=check-only-client-secret\n", False),
                              (key + "LINEAR_OAUTH_CLIENT_ID=check-client-id\n", True), ("", False)):
            with self.subTest(config_names=[line.split("=")[0] for line in config.splitlines()]):
                result, output = self.run_check(data, config)
                self.assertEqual(result, 0 if valid else 1)
                self.assertEqual("Required before deployment" in output, not valid)
                for credential in ("check-client-id", "check-only-client-secret", "check-only-api-key"):
                    self.assertNotIn(credential, output)
                self.assertIn("LINEAR_OAUTH_CLIENT_ID: ", output)
                self.assertIn("LINEAR_OAUTH_CLIENT_SECRET: ", output)




    def test_dotenv_parser_reads_literals_without_shell_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("LINEAR_API_KEY='$(this must not execute)'\nGITHUB_APP_ID=\nOTHER=$(ignored)\n")
            with patch.dict(os.environ, {}, clear=True):
                values = pilot.settings(path)
            self.assertEqual(values, {"LINEAR_API_KEY": "$(this must not execute)", "GITHUB_APP_ID": ""})

    def test_oauth_dotenv_values_are_literal_and_environment_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("LINEAR_OAUTH_CLIENT_ID=file-client-id\n"
                            "LINEAR_OAUTH_CLIENT_SECRET='$(literal secret & value)'\n")
            with patch.dict(os.environ, {"LINEAR_OAUTH_CLIENT_ID": "environment-client-id"}, clear=True):
                values = pilot.settings(path)
            self.assertEqual(values, {"LINEAR_OAUTH_CLIENT_ID": "environment-client-id",
                                      "LINEAR_OAUTH_CLIENT_SECRET": "$(literal secret & value)"})

    def test_blank_credentials_are_reported_without_values(self):
        missing = pilot.check({"LINEAR_API_KEY": "", "GITHUB_APP_ID": ""})
        self.assertIn("GITHUB_APP_ID", missing)
        self.assertTrue(any("LINEAR_API_KEY" in name for name in missing))


if __name__ == "__main__":
    unittest.main()
