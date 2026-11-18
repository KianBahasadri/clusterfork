"""Check the Hex Linear workflow at its read/write and recording boundaries."""

import json
from pathlib import Path
from queue import SimpleQueue
import sys
from threading import Event
import unittest
from unittest.mock import patch
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hex"), str(ROOT / "automation")]
import linear_issue as issue
import linear_shortcut as shortcut
from common.clients import RequestError


NEW, BEFORE, AFTER = (str(UUID(int=i)) for i in range(1, 4))


def fixture():
    catalog = (
        {"project": {"team_ids": ["team"]}},
        {"team": {"backlog": "backlog-state", "estimates": [1, 2, 3]}},
        {"label": {"team_id": None}},
    )
    return catalog, {"issue": {
        "title": "Check the relay", "description": "Cover the reported failure.",
        "project_id": "project", "team_id": "team", "priority": 2, "estimate": 3,
        "label_ids": ["label"], "blocked_by": [BEFORE], "blocks": [AFTER],
    }}


class LinearDictationTests(unittest.TestCase):
    def test_uses_the_final_assistant_reply_after_tool_calls(self):
        records = [
            {"type": "text", "part": {"messageID": "early", "text": "Looking up the issue."}},
            {"type": "step_finish", "part": {"messageID": "early", "reason": "tool-calls"}},
            {"type": "tool_use", "part": {"state": {"output": "An unrelated JSON object."}}},
            {"type": "text", "part": {"messageID": "final", "text": '{"clarification":'}},
            {"type": "text", "part": {"messageID": "final", "text": '"Which project?"}'}},
            {"type": "step_finish", "part": {"messageID": "final", "reason": "stop"}},
        ]
        self.assertEqual(issue.parse_draft("\n".join(map(json.dumps, records))), {"clarification": "Which project?"})
        with self.assertRaises(issue.FilingError):
            issue.parse_draft("\n".join(map(json.dumps, records[:-1])))

    def test_invalid_metadata_destination_labels_or_dependencies_stop_before_creation(self):
        for change in ({"priority": 0}, {"estimate": 5}, {"team_id": "other"}, {"label_ids": ["unavailable"]},
                       {"blocks": [BEFORE]}, {"blocks": [NEW]}):
            with self.subTest(change=change):
                catalog, draft = fixture()
                draft["issue"].update(change)
                with self.assertRaises(issue.FilingError):
                    issue.validate_draft(draft, catalog, NEW)

    def test_checks_dependency_availability_before_writing_and_dry_run_stays_read_only(self):
        catalog, draft = fixture()
        with (patch.object(issue, "configured_client") as client, patch.object(issue, "read_catalog", return_value=catalog),
              patch.object(issue, "opencode_draft", return_value=draft), patch.object(issue, "create_issue") as create,
              patch.object(issue, "add_dependencies") as dependencies):
            client.return_value.query.return_value = {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}
            with self.assertRaisesRegex(issue.FilingError, "dependency is unavailable"):
                issue.file_dictation("Please file this", None, NEW)
            client.return_value.query.return_value["issues"]["nodes"] = [
                {"id": BEFORE, "identifier": "KIAN-1", "trashed": False},
                {"id": AFTER, "identifier": "KIAN-2", "trashed": False},
            ]
            saved = issue.file_dictation("Please file this", None, NEW, dry_run=True)
            self.assertEqual((saved["blocked_by"], saved["blocks"]), (["KIAN-1"], ["KIAN-2"]))
            self.assertEqual(saved["fields"]["stateId"], "backlog-state")
            create.assert_not_called()
            dependencies.assert_not_called()

    def test_retries_same_issue_id_and_reconciles_a_lost_relation_response(self):
        catalog, draft = fixture()
        fields, blocked_by, blocks = issue.validate_draft(draft, catalog, NEW)
        with patch.object(issue, "time") as clock, patch.object(issue, "configured_client") as configured:
            client = configured.return_value
            client.create_issue.side_effect = [RequestError(503), {"id": NEW}]
            self.assertEqual(issue.create_issue(client, fields), {"id": NEW})
            self.assertEqual([call.args[0]["id"] for call in client.create_issue.call_args_list], [NEW, NEW])
            clock.sleep.assert_called_once()

            writes = []
            known = {}

            def query(document, variables):
                if document.startswith("mutation"):
                    writes.append(variables)
                    first = variables.get("r0")
                    if first:
                        known[first["id"]] = {"type": "blocks", "issue": {"id": first["issueId"]},
                                              "relatedIssue": {"id": first["relatedIssueId"]}}
                    raise RequestError(503)
                return {"issueRelation": known.get(variables["id"])}

            client.query.side_effect = query
            missing = issue.add_dependencies(client, NEW, blocked_by, blocks, {BEFORE: "KIAN-1", AFTER: "KIAN-2"})
            inbound, outbound = writes[0]["r0"], writes[0]["r1"]
            self.assertEqual((inbound["issueId"], inbound["relatedIssueId"]), (BEFORE, NEW))
            self.assertEqual((outbound["issueId"], outbound["relatedIssueId"]), (NEW, AFTER))
            self.assertEqual(writes[1], {"r1": outbound})
            self.assertEqual(missing, ["KIAN-2"])

    def test_retains_the_created_issue_link_after_dependency_failure(self):
        catalog, draft = fixture()
        url = "https://linear.app/workspace/issue/KIAN-3/check-the-relay"
        with (patch.object(issue, "configured_client") as client, patch.object(issue, "read_catalog", return_value=catalog),
              patch.object(issue, "opencode_draft", return_value=draft),
              patch.object(issue, "read_dependencies", return_value={BEFORE: "KIAN-1", AFTER: "KIAN-2"}),
              patch.object(issue, "add_dependencies", return_value=["KIAN-2"])):
            client.return_value.create_issue.return_value = {"identifier": "KIAN-3", "url": url}
            self.assertEqual(issue.file_dictation("Please file this", None, NEW), {
                "identifier": "KIAN-3", "url": url, "unverified": ["KIAN-2"],
                "blocked_by": ["KIAN-1"], "blocks": ["KIAN-2"],
            })
            client.return_value.create_issue.assert_called_once()

    def test_capture_renews_lease_finishes_on_release_and_queues_one_transcript(self):
        released, stopping, jobs = Event(), Event(), SimpleQueue()

        def post(registration, path, **kwargs):
            if path == "/dictations":
                return {"id": 7, "ownerToken": "fixture-owner"}
            self.assertEqual(kwargs["owner"], "fixture-owner")
            if path.endswith("/heartbeat"):
                released.set()
                return None
            if path.endswith("/finish"):
                self.assertTrue(released.is_set())
                return {"transcript": " File an issue "}
            self.fail(f"Unexpected recording operation {path}")

        with (patch.object(shortcut, "HEX_REGISTRATION") as registration, patch.object(shortcut, "hex_request", side_effect=post) as request,
              patch.object(shortcut, "focused_title", return_value="Clusterfork"),
              patch.object(released, "wait", side_effect=lambda _delay: released.is_set())):
            registration.read_text.return_value = '{"token":"fixture","port":1234}'
            shortcut.capture(released, stopping, jobs)
        self.assertEqual([call.args[1] for call in request.call_args_list],
                         ["/dictations", "/dictations/7/heartbeat", "/dictations/7/finish"])
        transcript, title, new_id = jobs.get_nowait()
        self.assertEqual((transcript, title), ("File an issue", "Clusterfork"))
        self.assertEqual(UUID(new_id).version, 4)
        self.assertTrue(jobs.empty())


if __name__ == "__main__":
    unittest.main()
