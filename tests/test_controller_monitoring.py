"""Saved workbook smoke check stays on the authorized deployment/operator path."""

import io
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
import controller_monitoring as monitoring
from test_controller_workflows import workflow


def workbook():
    directory = ROOT / "infra/azure/monitoring"
    common = (directory / "events.kql").read_text()
    queries = {p.stem: p.read_text().replace("__EVENTS__", common) for p in directory.glob("*.kql")}
    text = (directory / "workbook.json.tftpl").read_text()
    text = re.sub(r"\$\{jsonencode\(queries\.(\w+)\)\}", lambda m: json.dumps(queries[m[1]]), text)
    return json.loads(text.replace("${jsonencode(workspace_id)}", '"workspace"').replace("${insights_id}", "insights"))


class ControllerMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.monitoring = {"workspace_id": "workspace", "workbook_id": "/workbook", "workbook_url": "https://portal.azure.com/workbook"}
        self.config = {"hostname": "fixture.invalid"}
        self.layout = workbook()
        self.command = Mock(side_effect=[json.dumps(self.monitoring), json.dumps(self.config),
                                        json.dumps({"properties": {"serializedData": json.dumps(self.layout)}})])
        self.url = Mock(side_effect=[io.BytesIO(json.dumps({"deployment": {"revision": "a" * 40}}).encode()),
                                    HTTPError("https://fixture.invalid", 404, "not found", {}, io.BytesIO())])

    def test_smoke_executes_each_actual_saved_query_with_run_selection(self):
        query = Mock(side_effect=lambda _workspace, text: [{"Starts": 1, "Health": 1, "Failures": 1}])
        with patch.object(monitoring, "command", self.command), patch.object(monitoring, "urlopen", self.url), \
             patch.object(monitoring, "query", query):
            monitoring.smoke("a" * 40, run_id="123", attempt="2")
        panels = [i for i in self.layout["items"] if i["type"] == 3]
        self.assertEqual(query.call_count, len(panels) + 1)
        rendered = [call.args[1] for call in query.call_args_list[1:]]
        self.assertEqual(rendered, [monitoring.render_query(p["content"]["query"], "123", "2") for p in panels])
        self.assertTrue(any("RunId == '123'" in q and "tolong('2')" in q for q in rendered))
        self.assertFalse(any("{TimeRange" in q or "{Attempt" in q or "{RunId" in q for q in rendered))
        self.assertTrue(all(call.args[0] == "workspace" for call in query.call_args_list))
        self.assertIn("management.azure.com/workbook", " ".join(self.command.call_args.args[0]))

    def test_ingestion_absence_expires_without_reporting_success(self):
        query = Mock(return_value=[{"Starts": 0, "Health": 0, "Failures": 0}])
        with patch.object(monitoring, "command", self.command), patch.object(monitoring, "urlopen", self.url), \
             patch.object(monitoring, "query", query):
            with self.assertRaisesRegex(RuntimeError, "not queryable"):
                monitoring.smoke("a" * 40, timeout=1, clock=Mock(side_effect=[0, 2]), sleep=Mock())
        self.assertEqual(query.call_count, 1)

    def test_a_broken_saved_workbook_query_cannot_be_reported_as_displayable(self):
        def execute(_workspace, text):
            if "| summarize Starts=" in text:
                return [{"Starts": 1, "Health": 1, "Failures": 1}]
            raise RuntimeError("saved query failed")
        query = Mock(side_effect=execute)
        with patch.object(monitoring, "command", self.command), patch.object(monitoring, "urlopen", self.url), \
             patch.object(monitoring, "query", query):
            with self.assertRaisesRegex(RuntimeError, "saved query failed"):
                monitoring.smoke("a" * 40)

    def test_deployment_smoke_is_bounded_non_gating_and_not_in_worker_jobs(self):
        steps = workflow("deploy-azure.yml")["jobs"]["deploy"]["steps"]
        smoke = next(s for s in steps if "controller_monitoring.py" in s.get("run", ""))
        deploy = next(s for s in steps if "controller_deploy.py" in s.get("run", ""))
        self.assertLess(steps.index(deploy), steps.index(smoke))
        self.assertEqual(deploy["id"], "deploy")
        self.assertTrue(smoke["continue-on-error"])
        self.assertEqual(smoke["if"], "steps.deploy.outputs.proceed == 'true'")
        self.assertLessEqual(smoke["timeout-minutes"], 12)
        for name in ("clusterfork-code.yml", "clusterfork-review.yml", "clusterfork-task.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            self.assertNotIn("controller_monitoring.py", text)
            self.assertNotIn("azure/login", text)

    def test_timeline_and_count_queries_keep_explicit_missing_coverage_and_deduplication(self):
        panels = {i["name"]: i["content"] for i in self.layout["items"] if i["type"] == 3}
        for name, panel in panels.items():
            if name == "historical-functions":
                self.assertNotIn("ClusterforkController_CL", panel["query"])
            else:
                self.assertNotIn("AppRequests", panel["query"])
                self.assertIn("summarize arg_max(TimeGenerated, *) by EventId", panel["query"])
        self.assertIn("No controller telemetry for this selection", panels["selection"]["query"])
        self.assertIn("arg_max(EvidenceSequence, *) by TaskId", panels["tasks"]["query"])
        for name in ("recent", "timeline", "tasks"):
            link = panels[name]["gridSettings"]["formatters"][0]
            self.assertEqual(link["columnMatch"], "ActionsUrl")
            self.assertEqual(link["formatOptions"]["linkTarget"], "Url")


if __name__ == "__main__":
    unittest.main()
