"""Development stage labels and per-attempt account/usage reporting."""

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
from common.reporting import QUEUED_LABEL, SELECTION_LABEL, STAGE_LABELS, attempt_comment, usage_report, validate_usage
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

    def test_stage_updates_only_owned_labels_and_reuses_team_label(self):
        client = self.client(self.label_issue([{"id": "user", "name": "bug"},
                                              {"id": "selected", "name": SELECTION_LABEL},
                                              {"id": "old", "name": STAGE_LABELS["initializing"]}]),
                             {"issueLabels": {"nodes": [{"id": "coding"}], "pageInfo": {"hasNextPage": False}}},
                             {"issueAddLabel": {"success": True}}, {"issueRemoveLabel": {"success": True}})
        client.set_stage(ISSUE, "coding")
        self.assertEqual(client.query.call_args_list[-2].args[1], {"id": ISSUE, "label": "coding"})
        self.assertEqual(client.query.call_args_list[-1].args[1], {"id": ISSUE, "label": "old"})
        self.assertIn("team: {id: {eq: $team}}", client.query.call_args_list[1].args[0])

    def test_stage_cleanup_and_repeated_stage_do_not_replace_other_labels(self):
        labels = [{"id": "user", "name": "clusterfork: user label"},
                  {"id": "selected", "name": SELECTION_LABEL}, {"id": "coding", "name": STAGE_LABELS["coding"]}]
        client = self.client(self.label_issue(labels), self.label_issue(labels), {"issueRemoveLabel": {"success": True}})
        client.set_stage(ISSUE, "coding")
        self.assertEqual(client.query.call_count, 1)
        client.set_stage(ISSUE, None)
        self.assertEqual(client.query.call_count, 3)
        self.assertEqual(client.query.call_args.args[1], {"id": ISSUE, "label": "coding"})

    def test_selection_add_and_remove_preserve_stage_labels_and_assignments(self):
        labels = [{"id": "user", "name": "bug"}, {"id": "coding", "name": STAGE_LABELS["coding"]}]
        client = self.client(self.label_issue(labels),
                             {"issueLabels": {"nodes": [{"id": "selected"}], "pageInfo": {"hasNextPage": False}}},
                             {"issueAddLabel": {"success": True}},
                             self.label_issue(labels + [{"id": "selected", "name": SELECTION_LABEL}]),
                             {"issueRemoveLabel": {"success": True}})
        client.set_selected(ISSUE, True)
        client.set_selected(ISSUE, False)
        calls = client.query.call_args_list
        self.assertEqual(calls[2].args[1], {"id": ISSUE, "label": "selected"})
        self.assertEqual(calls[4].args[1], {"id": ISSUE, "label": "selected"})
        self.assertIn("issueAddLabel", calls[2].args[0])
        self.assertIn("issueRemoveLabel", calls[4].args[0])
        self.assertEqual(calls[1].args[1]["name"], SELECTION_LABEL)
        self.assertTrue(all("issueUpdate" not in call.args[0] for call in calls))

    def test_selection_repeated_updates_and_missing_labels_are_idempotent(self):
        client = self.client(self.label_issue([{"id": "selected", "name": SELECTION_LABEL}]), self.label_issue([]))
        client.set_selected(ISSUE, True)
        client.set_selected(ISSUE, False)
        self.assertEqual(client.query.call_count, 2)

    def test_queued_label_mutations_preserve_enrollment_stage_and_human_labels(self):
        labels = [{"id": "user", "name": "bug"}, {"id": "coding", "name": STAGE_LABELS["coding"]},
                  {"id": "enrollment", "name": "clusterfork-symphony"}]
        queued = {"id": "queued", "name": QUEUED_LABEL}
        client = self.client(self.label_issue(labels),
                             {"issueLabels": {"nodes": [{"id": "queued"}], "pageInfo": {"hasNextPage": False}}},
                             {"issueAddLabel": {"success": True}}, self.label_issue(labels + [queued]),
                             self.label_issue(labels + [queued]), {"issueRemoveLabel": {"success": True}},
                             self.label_issue(labels))
        for enabled in (True, True, False, False):
            client.set_queued(ISSUE, enabled)
        calls = client.query.call_args_list
        self.assertEqual(len(calls), 7)
        self.assertEqual(calls[1].args[1], {"team": ISSUE, "name": "clusterfork: queued"})
        self.assertIn("issueAddLabel", calls[2].args[0])
        self.assertIn("issueRemoveLabel", calls[5].args[0])
        self.assertEqual(calls[2].args[1], {"id": ISSUE, "label": "queued"})
        self.assertEqual(calls[5].args[1], {"id": ISSUE, "label": "queued"})
        self.assertTrue(all("issueUpdate" not in call.args[0] for call in calls))

    def test_selection_incomplete_labels_and_failed_mutations_are_recoverable(self):
        client = self.client(self.label_issue([], more=True))
        with self.assertRaises(ValueError):
            client.set_selected(ISSUE, True)
        client = self.client(self.label_issue([{"id": "selected", "name": SELECTION_LABEL}]),
                             {"issueRemoveLabel": {"success": False}})
        with self.assertRaises(RequestError):
            client.set_selected(ISSUE, False)

    def test_concurrent_or_ambiguous_label_creation_recovers_the_same_label(self):
        client = self.client({"issueLabels": {"nodes": [], "pageInfo": {"hasNextPage": False}}},
                             RequestError(), {"issueLabels": {"nodes": [{"id": "winner"}], "pageInfo": {"hasNextPage": False}}})
        self.assertEqual(client.stage_label(ISSUE, STAGE_LABELS["coding"]), "winner")
        self.assertEqual(UUID(client.query.call_args_list[1].args[1]["id"]).version, 4)

    def test_incomplete_labels_or_failed_mutations_are_retried(self):
        client = self.client(self.label_issue([], more=True))
        with self.assertRaises(ValueError):
            client.set_stage(ISSUE, None)
        client = self.client(self.label_issue([{"id": "old", "name": STAGE_LABELS["coding"]}]),
                             {"issueRemoveLabel": {"success": False}})
        with self.assertRaises(RequestError):
            client.set_stage(ISSUE, None)


class UsageTests(unittest.TestCase):
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
        self.assertIn("window reset or changed", body)
        self.assertIn("→ unavailable; change unavailable", body)
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
