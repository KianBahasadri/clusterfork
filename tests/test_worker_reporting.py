"""Development stage labels and per-attempt account/usage reporting."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation")]
import worker
from codex_runtime import CodexError, inspect_account, quota_snapshot, read_usage
from common.clients import Linear, RequestError
from common.reporting import (AUTO_REPAIR_LABEL, CLARIFICATION_LABEL, EXECUTION_STATUS_LABELS, QUEUED_LABEL,
                              SELECTION_LABEL, STAGE_LABELS, attempt_comment, timestamp, usage_report, validate_usage)
from call_budget import CallBudget
from label_api import LabelAPI
from test_remote_worker import NOW, ISSUE


def limits(short=10, weekly=20):
    return {"rateLimitsByLimitId": {"codex": {
        "primary": {"usedPercent": short, "windowDurationMins": 300, "resetsAt": NOW + 1000},
        "secondary": {"usedPercent": weekly, "windowDurationMins": 10080, "resetsAt": NOW + 10000}}}}


def usage(short=10, weekly=20):
    with patch("codex_runtime.time.time", return_value=NOW):
        return usage_report(quota_snapshot("a" * 64, "gpt-6.1-sol", limits(short, weekly)))




class LabelAdapterTests(unittest.TestCase):
    def label_issue(self, labels, more=False):
        return {"issue": {"team": {"id": ISSUE}, "labels": {"nodes": labels, "pageInfo": {"hasNextPage": more}}}}

    def client(self, *results):
        client = Linear("private-test-token")
        client.query = Mock(side_effect=results)
        return client

    def stateful_client(self, labels):
        api = LabelAPI()
        api.add_issue(ISSUE, labels)
        client = Linear('private-test-token')
        client.query = Mock(side_effect=api.query)
        client.api_calls = Mock()
        client.api_calls.attach_mock(client.query, 'query')
        return client, api

    def test_every_status_and_terminal_cleanup_remove_all_stale_owned_indicators(self):
        preserved = {'bug', 'clusterfork: user label', 'clusterfork-symphony', AUTO_REPAIR_LABEL}
        for desired in [*sorted(EXECUTION_STATUS_LABELS), None]:
            with self.subTest(desired=desired):
                client, api = self.stateful_client(sorted(preserved | EXECUTION_STATUS_LABELS))
                original = copy.deepcopy(api.issues[ISSUE])
                client.set_execution_status(ISSUE, desired)
                self.assertEqual(set(api.names(ISSUE)), preserved | ({desired} if desired else set()))
                self.assertTrue(all(operation == 'issueRemoveLabel' for operation, _, _ in api.mutations))
                for key in ('assignee', 'project', 'team'):
                    self.assertEqual(api.issues[ISSUE][key], original[key])
                self.assertTrue(all('issueUpdate' not in call.args[0] for call in client.query.call_args_list))

    def test_stage_replaces_selected_and_old_stage_before_adding_and_reuses_team_label(self):
        client, api = self.stateful_client([SELECTION_LABEL, STAGE_LABELS['initializing'], 'bug'])
        client.set_execution_status(ISSUE, STAGE_LABELS['coding'])
        self.assertEqual(api.mutations, [('issueRemoveLabel', ISSUE, SELECTION_LABEL),
                                         ('issueRemoveLabel', ISSUE, STAGE_LABELS['initializing']),
                                         ('issueAddLabel', ISSUE, STAGE_LABELS['coding'])])
        lookup = client.query.call_args_list[-2]
        self.assertIn('team: {id: {eq: $team}}', lookup.args[0])
        self.assertEqual(lookup.args[1], {'team': 'team', 'name': STAGE_LABELS['coding']})
        self.assertEqual(set(api.names(ISSUE)), {'bug', STAGE_LABELS['coding']})

    def test_queue_selection_clarification_and_each_stage_transition_never_add_overlap(self):
        client, api = self.stateful_client(['bug', AUTO_REPAIR_LABEL])
        for desired in [QUEUED_LABEL, SELECTION_LABEL, CLARIFICATION_LABEL, SELECTION_LABEL,
                        *STAGE_LABELS.values(), None]:
            client.set_execution_status(ISSUE, desired)
            self.assertEqual(set(api.names(ISSUE)) & EXECUTION_STATUS_LABELS, {desired} if desired else set())
        self.assertTrue(all(len(set(names) & EXECUTION_STATUS_LABELS) <= 1 for _, names in api.snapshots))

    def test_repeated_status_and_empty_cleanup_are_idempotent(self):
        client, api = self.stateful_client([SELECTION_LABEL])
        with CallBudget(self, linear=client, exact={'linear.query': 1}):
            client.set_execution_status(ISSUE, SELECTION_LABEL)
        self.assertFalse(api.mutations)
        with CallBudget(self, linear=client, exact={'linear.query': 2}):
            client.set_execution_status(ISSUE, None)
        with CallBudget(self, linear=client, exact={'linear.query': 1}):
            client.set_execution_status(ISSUE, None)
        self.assertEqual(api.mutations, [('issueRemoveLabel', ISSUE, SELECTION_LABEL)])
        self.assertEqual(client.query.call_count, 4)

    def test_duplicate_named_statuses_keep_only_one_indicator(self):
        client, api = self.stateful_client([SELECTION_LABEL, {'id': 'workspace-selected', 'name': SELECTION_LABEL}])
        client.set_execution_status(ISSUE, SELECTION_LABEL)
        self.assertEqual(api.names(ISSUE), [SELECTION_LABEL])

    def test_failed_removal_leaves_replacement_retryable_without_adding(self):
        for lost_reply in (False, True):
            with self.subTest(lost_reply=lost_reply):
                client, api = self.stateful_client([QUEUED_LABEL, SELECTION_LABEL])
                removals = 0
                def query(text, variables):
                    nonlocal removals
                    if 'issueRemoveLabel(' in text:
                        removals += 1
                        if removals == 2:
                            if lost_reply:
                                api.query(text, variables)
                                raise RequestError()
                            return {'issueRemoveLabel': {'success': False}}
                    return api.query(text, variables)
                client.query.side_effect = query
                with self.assertRaises(RequestError):
                    client.set_execution_status(ISSUE, STAGE_LABELS['coding'])
                self.assertFalse(any(operation == 'issueAddLabel' for operation, _, _ in api.mutations))
                client.set_execution_status(ISSUE, STAGE_LABELS['coding'])
                self.assertEqual(api.names(ISSUE), [STAGE_LABELS['coding']])

    def test_failed_or_lost_add_reply_is_idempotently_retried(self):
        for lost_reply in (False, True):
            with self.subTest(lost_reply=lost_reply):
                client, api = self.stateful_client([SELECTION_LABEL])
                failed = False
                def query(text, variables):
                    nonlocal failed
                    if 'issueAddLabel(' in text and not failed:
                        failed = True
                        if lost_reply:
                            api.query(text, variables)
                            raise RequestError()
                        return {'issueAddLabel': {'success': False}}
                    return api.query(text, variables)
                client.query.side_effect = query
                with self.assertRaises(RequestError):
                    client.set_execution_status(ISSUE, STAGE_LABELS['coding'])
                client.set_execution_status(ISSUE, STAGE_LABELS['coding'])
                self.assertEqual(api.names(ISSUE), [STAGE_LABELS['coding']])
                self.assertEqual(sum(operation == 'issueAddLabel' for operation, _, _ in api.mutations), 1)
                self.assertTrue(all(len(set(names) & EXECUTION_STATUS_LABELS) <= 1 for _, names in api.snapshots))

    def test_concurrent_or_ambiguous_label_creation_recovers_the_same_label(self):
        client = self.client({"issueLabels": {"nodes": [], "pageInfo": {"hasNextPage": False}}},
                             RequestError(), {"issueLabels": {"nodes": [{"id": "winner"}], "pageInfo": {"hasNextPage": False}}})
        self.assertEqual(client.stage_label(ISSUE, STAGE_LABELS["coding"]), "winner")
        self.assertEqual(UUID(client.query.call_args_list[1].args[1]["id"]).version, 4)

    def test_incomplete_labels_or_failed_mutations_are_retried(self):
        client = self.client(self.label_issue([], more=True))
        with self.assertRaises(ValueError):
            client.set_execution_status(ISSUE, None)
        self.assertEqual(client.query.call_count, 1)
        client = self.client(self.label_issue([{"id": "old", "name": STAGE_LABELS["coding"]}]),
                             {"issueRemoveLabel": {"success": False}})
        with self.assertRaises(RequestError):
            client.set_execution_status(ISSUE, None)

    def test_invalid_status_cannot_remove_owned_or_unrelated_labels(self):
        client = self.client()
        with self.assertRaises(ValueError):
            client.set_execution_status(ISSUE, AUTO_REPAIR_LABEL)
        client.query.assert_not_called()


class UsageTests(unittest.TestCase):
    def comment(self, before, after=None, *, finished=False):
        run = {"owner": "123:1", "account_id": "a" * 64, "model": "gpt-6.1-sol", "effort": "max",
               "status": "completed", "started_at": NOW, "finished_at": NOW + 61,
               "usage_before": json.dumps(before), "usage_after": json.dumps(after) if after else ""}
        return attempt_comment(run, "owner/repo", finished=finished)

    def test_start_comment_emphasizes_each_current_used_reading(self):
        body = self.comment(usage(4, 6))
        self.assertIn(f"Usage before coding ({timestamp(NOW)}):", body)
        self.assertIn(f"- codex/primary (300-minute): **4% used** / 96% remaining; resets {timestamp(NOW + 1000)}", body)
        self.assertIn(f"- codex/secondary (weekly): **6% used** / 94% remaining; resets {timestamp(NOW + 10000)}", body)
        self.assertEqual(body.count("**"), 4)

    def test_completion_comment_emphasizes_only_after_readings_and_preserves_details(self):
        after = usage(4, 6)
        after["observed_at"] += 61
        after["windows"][0]["resets_at"] += 1000
        body = self.comment(usage(17, 5), after, finished=True)
        self.assertIn(f"Usage before ({timestamp(NOW)}) and after ({timestamp(NOW + 61)}):", body)
        self.assertIn("- codex/primary (300-minute): 17% used / 83% remaining → **4% used** / 96% remaining"
                      "; change unavailable (window reset or changed, or reset time unknown)"
                      f"; resets {timestamp(NOW + 2000)}", body)
        self.assertIn("- codex/secondary (weekly): 5% used / 95% remaining → **6% used** / 94% remaining"
                      f"; usage change +1 percentage points; resets {timestamp(NOW + 10000)}", body)
        self.assertIn("Usage changes compare account quota readings; provider reporting may lag or include other activity.", body)
        self.assertEqual(body.count("**"), 4)

    def test_completion_comment_emphasizes_new_window_readings(self):
        after = usage(11, 21)
        after["windows"].append({**after["windows"][0], "name": "codex/new", "used_percent": 12.5})
        body = self.comment(usage(), after, finished=True)
        self.assertIn("- codex/new (300-minute): **12.5% used** / 87.5% remaining; change unavailable (new window)", body)
        self.assertEqual(body.count("**"), 6)

    def test_missing_final_reading_stays_unavailable_and_before_readings_stay_plain(self):
        body = self.comment(usage(), finished=True)
        self.assertIn("final reading unavailable; usage change unavailable.\n\nBefore:", body)
        self.assertIn(f"- codex/primary (300-minute): 10% used / 90% remaining; resets {timestamp(NOW + 1000)}", body)
        self.assertIn(f"- codex/secondary (weekly): 20% used / 80% remaining; resets {timestamp(NOW + 10000)}", body)
        self.assertNotIn("**", body)

    def test_readings_preserve_weekly_names_and_exhausted_final_usage(self):
        raw = limits(100, 100)
        raw["rateLimitsByLimitId"]["codex"]["rateLimitReachedType"] = "weekly"
        with self.assertRaises(CodexError):
            quota_snapshot("a", "gpt-6.1-sol", raw)
        final = quota_snapshot("a", "gpt-6.1-sol", raw, allow_exhausted=True)
        self.assertEqual(final.windows[1].name, "codex/secondary")
        self.assertEqual(final.windows[1].duration_minutes, 10080)
        self.assertEqual(final.windows[1].used_percent, 100)

    def test_account_details_and_final_usage_use_existing_app_server_calls(self):
        server = Mock()
        model = {"model": "gpt-6.1-sol", "supportedReasoningEfforts": [{"reasoningEffort": "max"}]}
        server.call.side_effect = [{"account": {"email": "worker@example.com", "planType": "pro", "secret": "hidden"}},
                                   {"data": [model]}, limits()]
        with patch("codex_runtime.AppServer", return_value=server):
            details, actual = inspect_account(Path("/unused"), "gpt-6.1-sol", "max")
        self.assertEqual(details["account"], {"email": "worker@example.com", "planType": "pro"})
        self.assertEqual(actual, limits())
        server.call.side_effect = [{}, limits(15, 25)]
        with patch("codex_runtime.AppServer", return_value=server):
            self.assertEqual(read_usage(Path("/unused")), limits(15, 25))

    def test_invalid_usage_is_rejected_and_unknown_fields_never_reach_public_comments(self):
        raw = {**usage(), "access_token": "hidden"}
        raw["windows"][0]["auth"] = {"refresh_token": "also-hidden"}
        sanitized = validate_usage(raw)
        self.assertNotIn("hidden", json.dumps(sanitized))
        for change in ({"used_percent": float("nan")}, {"used_percent": 101}, {"duration_minutes": -1}, {"name": "token\nsecret"}):
            invalid = usage()
            invalid["windows"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_usage(invalid)

    def test_changed_reset_or_missing_windows_never_claim_negative_or_zero_consumption(self):
        run = {"owner": "123:1", "account_id": "a" * 64, "model": "gpt-6.1-sol", "effort": "max",
               "status": "completed", "started_at": NOW, "finished_at": NOW + 61, "usage_before": json.dumps(usage())}
        after = usage(2, 20)
        after["windows"][0]["resets_at"] += 1000
        after["windows"].pop()
        run["usage_after"] = json.dumps(after)
        body = attempt_comment(run, "owner/repo", finished=True)
        self.assertIn("- codex/primary (300-minute): 10% used / 90% remaining → **2% used** / 98% remaining", body)
        self.assertIn("window reset or changed", body)
        self.assertIn("- codex/secondary (weekly): 20% used / 80% remaining → unavailable; change unavailable", body)
        self.assertEqual(body.count("**"), 2)
        self.assertNotIn("usage change -8", body)
        self.assertNotIn("usage change +0", body)


class RunnerReportingTests(unittest.TestCase):
    def test_final_reading_runs_after_caller_validation_on_success_and_failure(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                heartbeat = SimpleNamespace(failed=threading.Event())
                selection = SimpleNamespace(account_id="a" * 64, model="gpt-6.1-sol", effort="max", remaining_percent=80)
                lease = MagicMock()
                lease.__enter__.return_value = (Path(directory), heartbeat)
                broker = Mock()
                config = {"model": selection.model, "effort": selection.effort,
                          "issue": {"identifier": "KIAN-894", "title": "Test", "url": "https://linear.app/test"}}
                with patch.object(worker, "choose_account", return_value=([], {}, None)), \
                     patch.object(worker, "select_account", return_value=selection), \
                     patch.object(worker, "account_lease", return_value=lease), \
                     patch.object(worker, "inspect_account", return_value=({"account": {"email": "worker@example.com"}}, limits())), \
                     patch.object(worker, "run_agent", return_value="Completed"), \
                     patch.object(worker, "read_usage", return_value=limits(14, 23)) as read:
                    try:
                        with worker.run_codex(broker, config, heartbeat, Path(directory)):
                            read.assert_not_called()
                            lease.__exit__.assert_not_called()
                            before = broker.call.call_args.kwargs["usage_before"]
                            self.assertEqual(before["windows"][1]["used_percent"], 20)
                            if fail:
                                raise worker.WorkerFailure("validation failed")
                    except worker.WorkerFailure as exc:
                        self.assertTrue(fail)
                        self.assertEqual(str(exc), "validation failed")
                    else:
                        self.assertFalse(fail)
                read.assert_called_once()
                self.assertEqual(broker.call.call_args.kwargs["usage_after"]["windows"][1]["used_percent"], 23)
                lease.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
