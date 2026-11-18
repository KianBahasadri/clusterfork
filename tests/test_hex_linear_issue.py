"""Check the Hex Linear workflow at its read/write and recording boundaries."""

from dataclasses import replace
import json
from pathlib import Path
from queue import SimpleQueue
from subprocess import CompletedProcess, TimeoutExpired
import sys
from threading import Event
import unittest
from unittest.mock import Mock, patch
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hex"), str(ROOT / "automation")]
import linear_issue as issue
import linear_shortcut as shortcut
from common.clients import RequestError


NEW, BEFORE, AFTER = (str(UUID(int=i)) for i in range(1, 4))
DICTATION = issue.Dictation("Please file this", None, NEW)


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
            with self.assertRaisesRegex(issue.ReplyNeeded, "dependency is unavailable"):
                issue.file_dictation(DICTATION)
            client.return_value.query.return_value["issues"]["nodes"] = [
                {"id": BEFORE, "identifier": "KIAN-1", "trashed": False},
                {"id": AFTER, "identifier": "KIAN-2", "trashed": False},
            ]
            saved = issue.file_dictation(DICTATION, dry_run=True)
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
            self.assertEqual((UUID(inbound["id"]).version, UUID(outbound["id"]).version), (4, 4))
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
            self.assertEqual(issue.file_dictation(DICTATION), {
                "identifier": "KIAN-3", "url": url, "unverified": ["KIAN-2"],
                "blocked_by": ["KIAN-1"], "blocks": ["KIAN-2"],
            })
            client.return_value.create_issue.assert_called_once()

    def test_capture_renews_lease_copies_on_release_and_queues_one_transcript(self):
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
              patch.object(shortcut, "copy_transcript") as copy, patch.object(shortcut, "clipboard_image") as image,
              patch.object(released, "wait", side_effect=lambda _delay: released.is_set())):
            registration.read_text.return_value = '{"token":"fixture","port":1234}'
            shortcut.capture(released, stopping, jobs)
        self.assertEqual([call.args[1] for call in request.call_args_list],
                         ["/dictations", "/dictations/7/heartbeat", "/dictations/7/finish"])
        dictation = jobs.get_nowait()
        self.assertEqual((dictation.transcript, dictation.title), ("File an issue", "Clusterfork"))
        self.assertEqual(UUID(dictation.issue_id).version, 4)
        copy.assert_called_once_with(dictation.transcript)
        image.assert_not_called()
        self.assertTrue(jobs.empty())

    def test_capture_snapshots_an_explicit_image_before_copying_the_transcript(self):
        released, stopping, jobs = Event(), Event(), SimpleQueue()
        released.set()
        transcript = "File an issue and copy the image currently in my clipboard."
        sequence = []
        with (patch.object(shortcut, "HEX_REGISTRATION") as registration,
              patch.object(shortcut, "hex_request", side_effect=[{"id": 7, "ownerToken": "fixture"}, {"transcript": transcript}]),
              patch.object(shortcut, "clipboard_image", side_effect=lambda: sequence.append("image") or ("image/png", b"image")),
              patch.object(shortcut, "copy_transcript", side_effect=lambda text: sequence.append(text))):
            registration.read_text.return_value = '{"token":"fixture","port":1234}'
            shortcut.capture(released, stopping, jobs)
        self.assertEqual(sequence, ["image", transcript])
        dictation = jobs.get_nowait()
        self.assertTrue(dictation.wants_image)
        self.assertEqual(dictation.image, ("image/png", b"image"))
        self.assertIsNone(shortcut.IMAGE_REQUEST.search("Add a feature to copy the image in my clipboard."))
        self.assertIsNone(shortcut.IMAGE_REQUEST.search("Do not attach the image in my clipboard."))

    def test_clipboard_uses_utf8_input_and_treats_copy_failure_as_nonfatal(self):
        with patch.object(shortcut.subprocess, "run") as run, patch.object(shortcut, "notify") as notice:
            shortcut.copy_transcript("First line\nCafé")
            self.assertEqual(run.call_args.args[0], ["wl-copy", "--type", "text/plain;charset=utf-8"])
            self.assertEqual(run.call_args.kwargs["input"], b"First line\nCaf\xc3\xa9")
            run.side_effect = TimeoutExpired("wl-copy", 5)
            shortcut.copy_transcript("Still file this")
            notice.assert_called_once()

    def test_reply_keeps_its_original_request_identity_and_image(self):
        jobs = SimpleQueue()
        dictation = replace(DICTATION, wants_image=True, image=("image/png", b"original"))
        question = issue.ReplyNeeded("Which project?")
        with patch.object(shortcut.subprocess, "run", side_effect=[
            CompletedProcess("notify-send", 0, stdout="default\n"),
            CompletedProcess("zenity", 0, stdout="Clusterfork\n"),
        ]) as run:
            shortcut.offer_reply(jobs, dictation, question)
        self.assertIn("--action=default=Reply", run.call_args_list[0].args[0])
        self.assertEqual(run.call_args_list[1].args[0][0], "zenity")
        self.assertEqual(jobs.get_nowait(), replace(dictation, answers=(("Which project?", "Clusterfork"),)))

    def test_image_reply_reads_the_new_clipboard_and_keeps_prior_answers(self):
        jobs = SimpleQueue()
        dictation = replace(DICTATION, wants_image=True, answers=(("Which project?", "Clusterfork"),))
        image = ("image/jpeg", b"replacement")
        with (patch.object(shortcut.subprocess, "run", return_value=CompletedProcess("notify-send", 0, stdout="reply\n")) as run,
              patch.object(shortcut, "clipboard_image", return_value=image)):
            shortcut.offer_reply(jobs, dictation, issue.ReplyNeeded("Copy a new image.", copy_image=True))
        self.assertEqual(jobs.get_nowait(), replace(dictation, image=image))
        run.assert_called_once()

    def test_holds_for_missing_and_unrelated_images_before_upload_or_creation(self):
        catalog, _ = fixture()
        dictation = replace(DICTATION, wants_image=True)
        with (patch.object(issue, "configured_client") as client, patch.object(issue, "read_catalog", return_value=catalog),
              patch.object(issue, "opencode_draft", return_value={"image_relevant": False}),
              patch.object(issue, "upload_image") as upload, patch.object(issue, "create_issue") as create):
            with self.assertRaises(issue.ReplyNeeded) as missing:
                issue.file_dictation(dictation)
            client.assert_not_called()
            with self.assertRaises(issue.ReplyNeeded) as unrelated:
                issue.file_dictation(replace(dictation, image=("image/png", b"wrong image")))
        self.assertTrue(missing.exception.copy_image and unrelated.exception.copy_image)
        upload.assert_not_called()
        create.assert_not_called()

    def test_relevant_image_waits_with_the_draft_for_other_clarification(self):
        catalog, _ = fixture()
        dictation = replace(DICTATION, wants_image=True, image=("image/png", b"matching image"))
        with (patch.object(issue, "configured_client"), patch.object(issue, "read_catalog", return_value=catalog),
              patch.object(issue, "opencode_draft", return_value={"image_relevant": True, "clarification": "Which project?"})):
            with self.assertRaisesRegex(issue.ReplyNeeded, "Which project") as reply:
                issue.file_dictation(dictation)
        self.assertFalse(reply.exception.copy_image)

    def test_relevant_image_is_uploaded_and_embedded_in_the_created_issue(self):
        catalog, draft = fixture()
        draft["image_relevant"] = True
        dictation = replace(DICTATION, wants_image=True, image=("image/png", b"correct image"))
        asset = "https://uploads.linear.app/workspace/asset/clipboard.png"
        with (patch.object(issue, "configured_client") as client, patch.object(issue, "read_catalog", return_value=catalog),
              patch.object(issue, "opencode_draft", return_value=draft),
              patch.object(issue, "read_dependencies", return_value={BEFORE: "KIAN-1", AFTER: "KIAN-2"}),
              patch.object(issue, "upload_image", return_value=asset) as upload, patch.object(issue, "add_dependencies", return_value=[])):
            client.return_value.create_issue.return_value = {"identifier": "KIAN-3", "url": "https://linear.app/workspace/issue/KIAN-3"}
            self.assertTrue(issue.file_dictation(dictation, dry_run=True)["clipboard_image"])
            upload.assert_not_called()
            issue.file_dictation(dictation)
        upload.assert_called_once_with(client.return_value, dictation.image)
        fields = client.return_value.create_issue.call_args.args[0]
        self.assertTrue(fields["description"].endswith(f"![Clipboard image](<{asset}>)"))
        self.assertEqual(fields["id"], NEW)

    def test_image_upload_uses_the_signed_put_headers_and_returns_the_linear_asset(self):
        asset = "https://uploads.linear.app/workspace/asset/clipboard.webp"
        linear = Mock()
        linear.query.return_value = {"fileUpload": {
            "success": True, "uploadFile": {"assetUrl": asset,
                                             "uploadUrl": "https://storage.example.org/img?signature=test",
                                             "headers": [{"key": "X-Upload", "value": "fixture"}]},
        }}
        with patch.object(issue, "HTTPSConnection") as connection:
            put = connection.return_value
            put.getresponse.return_value.status = 200
            self.assertEqual(issue.upload_image(linear, ("image/webp", b"abc")), asset)
        self.assertEqual(linear.query.call_args.args[1], {"type": "image/webp", "name": "clipboard.webp", "size": 3})
        connection.assert_called_once_with("storage.example.org", timeout=20)
        put.request.assert_called_once_with("PUT", "/img?signature=test", body=b"abc", headers={
            "Content-Type": "image/webp", "Cache-Control": "public, max-age=31536000", "X-Upload": "fixture",
        })
        put.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
