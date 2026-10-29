"""Webhook-to-label timestamps using real handlers and simulated service delays."""

import copy
import importlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation/azure"))
from test_linear_dispatch import NOW, SECRET, signed
import test_repository_queue as fixtures


class QueueConfigurationTests(unittest.TestCase):
    def test_polling_is_prompt_and_events_can_progress_concurrently(self):
        queues = json.loads((ROOT / "automation/azure/host.json").read_text())["extensions"]["queues"]
        self.assertEqual(queues["maxPollingInterval"], "00:00:01")
        self.assertGreater(queues["batchSize"], 1)
        self.assertEqual(queues["maxDequeueCount"], 5)
        self.assertEqual(queues["visibilityTimeout"], "00:01:00")


class HandlerTimingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.app = importlib.import_module("function_app")
        except ModuleNotFoundError as exc:
            if exc.name in {"azure", "azure.functions"}:
                raise unittest.SkipTest("Run with automation/azure/requirements.txt for handler integration") from exc
            raise

    def setUp(self):
        self.case = fixtures.RepositoryQueueTests()
        self.case.setUp()
        self.issue = self.case.issue(1)
        self.case.now = NOW + 0.2
        payload = {"type": "Issue", "action": "update", "webhookTimestamp": (NOW + 0.1) * 1000,
                   "data": {"id": self.issue["id"], "updatedAt": "2027-01-15T08:00:00Z",
                            "description": "private issue text"}}
        body, headers = signed(payload)
        self.request = self.app.func.HttpRequest("POST", "https://example.test/api/linear", body=body,
                                                 headers=headers)

    def test_timestamps_cover_delivery_queue_admission_and_label_acknowledgement(self):
        sent = []
        client = Mock()

        def send(body):
            sent.append(json.loads(body))
            self.case.now += 0.05

        def issue(issue_id):
            self.case.now += 0.1
            return copy.deepcopy(self.case.issues[issue_id])

        def selected(issue_id, enabled):
            table = self.case.tables[self.case.repository.lower()]
            self.assertEqual(table.get(self.case.slot()["active_key"])[0]["issue_id"], issue_id)
            self.case.now += 0.3
            self.case.select(issue_id, enabled)

        client.send_message.side_effect = send
        self.case.linear.issue.side_effect = issue
        self.case.linear.set_selected.side_effect = selected
        with patch.dict(os.environ, {"LINEAR_WEBHOOK_SECRET": SECRET}), \
             patch.object(self.app.time, "time", side_effect=lambda: self.case.now), \
             patch.object(self.app, "queue_client", return_value=client), \
             patch.object(self.app, "dispatcher", return_value=self.case.dispatcher), \
             self.assertLogs(level="INFO") as logs:
            response = self.app.linear_webhook(self.request)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.case.selected, set())  # Receipt and queue send do not select.
            self.assertNotIn("description", sent[0])
            self.case.now += 0.5
            message = Mock()
            message.get_body.return_value = json.dumps(sent[0]).encode()
            self.app.route_event(message)

        records = [json.loads(line.split("Clusterfork timing: ", 1)[1])
                   for line in logs.output if "Clusterfork timing: " in line]
        phases = {row["phase"]: row for row in records}
        self.assertAlmostEqual(phases["webhook_received"]["at"] - NOW, 0.2, places=5)
        self.assertAlmostEqual(phases["event_enqueued"]["at"] - NOW, 0.25, places=5)
        self.assertAlmostEqual(phases["event_processing"]["at"] - NOW, 0.75, places=5)
        self.assertAlmostEqual(phases["repository_admitted"]["at"] - NOW, 0.85, places=5)
        self.assertAlmostEqual(phases["selection_label_written"]["at"] - NOW, 1.15, places=5)
        key = self.case.slot()["active_key"]
        row, _ = self.case.tables[self.case.repository.lower()].get(key)
        self.assertEqual(row["source_updated_at"], NOW)
        self.assertEqual(row["webhook_at"], NOW + 0.1)
        self.assertAlmostEqual(row["selection_reported_at"] - NOW, 1.15, places=5)
        self.assertTrue(phases["selection_label_written"]["selected"])
        self.assertFalse(row["selection_pending"])
        self.assertNotIn("private issue text", "\n".join(logs.output))

    def test_failed_queue_send_retains_linear_retry_and_does_not_admit(self):
        client = Mock()
        client.send_message.side_effect = OSError("private service response")
        with patch.dict(os.environ, {"LINEAR_WEBHOOK_SECRET": SECRET}), \
             patch.object(self.app.time, "time", return_value=NOW + 0.2), \
             patch.object(self.app, "queue_client", return_value=client), \
             self.assertLogs(level="ERROR") as logs:
            response = self.app.linear_webhook(self.request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.case.selected, set())
        self.assertNotIn("private service response", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
