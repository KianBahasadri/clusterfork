"""Check the Hex Linear workflow at its read/write and recording boundaries."""

from contextlib import redirect_stderr
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from queue import SimpleQueue
from subprocess import CompletedProcess, TimeoutExpired
import sys
from tempfile import TemporaryDirectory
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
DEPENDENCIES = {"KIAN-1": BEFORE, "KIAN-2": AFTER}
DICTATION = issue.Dictation("Please file this", None, NEW)


def fixture():
    catalog = (
        {"project": {"id": "project", "name": "Relay", "ref": "Relay", "slug": "relay-slug",
                     "status": "started", "team_ids": ["team"], "description": "Relay\nservice"}},
        {"team": {"id": "team", "name": "Kian", "ref": "KIAN", "backlog": "backlog-state",
                  "estimate_type": "fibonacci", "estimates": [1, 2, 3],
                  "states": [{"id": "done-state", "name": "Done", "ref": "Done", "type": "completed"}]}},
        {"label": {"id": "label", "name": "Feature", "ref": "Feature", "team_id": None,
                   "group": "Type", "description": "A new feature"},
         "old-label": {"id": "old-label", "name": "Bug", "ref": "Bug", "team_id": "team",
                       "group": "Type", "description": None}},
    )
    return catalog, {"issues": [{
        "title": "Check the relay", "description": "Cover the reported failure.",
        "project": "Relay", "team": "KIAN", "priority": 2, "estimate": 3,
        "labels": ["Feature"], "blocked_by": ["KIAN-1"], "blocks": ["KIAN-2"], "attach_image": False,
    }]}


def update_fixture(changes):
    current = {
        "id": BEFORE, "identifier": "KIAN-1", "url": "https://linear.app/workspace/issue/KIAN-1",
        "updatedAt": "2026-01-01T12:00:00.000Z", "trashed": False, "title": "Keep this title",
        "description": "Original scope.\n\n[Original reference](https://example.com)",
        "priority": 3, "estimate": 1, "dueDate": None, "labelIds": ["old-label", "keep-label"],
        "team": {"id": "team"}, "state": {"id": "backlog-state"}, "project": {"id": "project"},
    }
    draft = {"issue_id": "KIAN-1", "updated_at": current["updatedAt"], "changes": changes,
             "blocked_by": [], "blocks": [], "attach_image": False}
    return current, draft


class LinearDictationTests(unittest.TestCase):
    def setUp(self):
        state = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(issue.os.environ, {"XDG_STATE_HOME": state}))
        self.attempts = Path(state) / "hex/linear/runs"

    def test_saves_verbatim_transcript_stdin_and_archived_response(self):
        assistant_reply = {"clarification": "Which Linear issue should I update?"}
        events = [
            {"type": "step_start", "sessionID": "ses_fixture123"},
            {"type": "tool_use", "part": {"state": {"input": {"query": "Relay"},
                                                    "content": [{"type": "text", "text": "KIAN-1\tRelay"}]}}},
            {"type": "text", "part": {"messageID": "final", "text": json.dumps(assistant_reply)}},
            {"type": "step_finish", "part": {"messageID": "final", "reason": "stop"}},
        ]
        stdout_bytes = ("\n".join(map(json.dumps, events)) + "\n").encode()
        stderr_bytes = b"Fixture diagnostic\r\n"
        transcript = "  Réparer le relais.\r\nMore detail.\n "
        dictation = replace(DICTATION, transcript=transcript, title="Relay – Linear", answers=(("Which project?", "Relay"),))

        def complete(command, *, stdout, stderr, **kwargs):
            self.assertEqual((attempt / "prompt.txt").read_bytes(), kwargs["input"])
            stdout.write(stdout_bytes)
            stderr.write(stderr_bytes)
            return CompletedProcess(command, 0)

        logs = StringIO()
        with patch.object(issue.subprocess, "run", side_effect=complete) as run, redirect_stderr(logs):
            attempt = issue.start_attempt_log(dictation)
            self.assertEqual(issue.opencode_draft(dictation, fixture()[0], attempt), assistant_reply)
        self.assertIn("--archived", run.call_args.args[0])
        prompt = run.call_args.kwargs["input"].decode()
        self.assertIn(f"Transcript\n{transcript}\n\nFollow-up question\nWhich project?\nFollow-up answer\nRelay", prompt)
        self.assertIn("Focused project: Relay", prompt)
        self.assertIn("Relay\tstarted\tKIAN\t-\tRelay service", prompt)
        self.assertIn("Feature\tworkspace\tType\tA new feature", prompt)
        self.assertIn("KIAN\tDone\tcompleted", prompt)
        for internal_value in ("backlog-state", "old-label", "done-state", "relay-slug", BEFORE, AFTER):
            self.assertNotIn(internal_value, prompt)
        self.assertIn(f"{NEW} attempt={attempt}", logs.getvalue())
        self.assertIn(f"{NEW} archived OpenCode session ses_fixture123", logs.getvalue())
        self.assertNotIn(transcript, logs.getvalue())
        self.assertEqual(attempt.stat().st_mode & 0o777, 0o700)
        expected = {"transcript.txt": transcript.encode(), "prompt.txt": run.call_args.kwargs["input"],
                    "stdout.jsonl": stdout_bytes, "stderr.txt": stderr_bytes}
        self.assertEqual({path.name: path.read_bytes() for path in attempt.iterdir()}, expected)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in attempt.iterdir()))

    def test_preserves_partial_output_when_the_cli_times_out_or_exits(self):
        partial = b'{"type":"step_start","sessionID":"ses_fixture123"}\n{"type":"text","part":"\xc3'
        diagnostic = b"Connection lost\n"
        attempts = set()
        for failure in (TimeoutExpired, issue.FilingError):
            with self.subTest(failure=failure):
                def incomplete(command, *, stdout, stderr, **kwargs):
                    stdout.write(partial)
                    stderr.write(diagnostic)
                    if failure is TimeoutExpired:
                        raise TimeoutExpired(command, 300)
                    return CompletedProcess(command, 1)

                logs = StringIO()
                with (patch.object(issue.subprocess, "run", side_effect=incomplete) as run, redirect_stderr(logs),
                      self.assertRaises(failure)):
                    attempt = issue.start_attempt_log(DICTATION)
                    attempts.add(attempt)
                    issue.opencode_draft(DICTATION, fixture()[0], attempt)
                self.assertIn(f"{NEW} archived OpenCode session ses_fixture123", logs.getvalue())
                self.assertEqual((attempt / "prompt.txt").read_bytes(), run.call_args.kwargs["input"])
                self.assertEqual((attempt / "stdout.jsonl").read_bytes(), partial)
                self.assertEqual((attempt / "stderr.txt").read_bytes(), diagnostic)
        self.assertEqual(len(attempts), 2)

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
        for change in ({"priority": 0}, {"estimate": 5}, {"team": "OTHER"}, {"labels": ["unavailable"]},
                       {"blocks": ["KIAN-1"]}, {"blocks": [NEW]}):
            with self.subTest(change=change):
                catalog, draft = fixture()
                draft["issues"][0].update(change)
                with self.assertRaises(issue.FilingError):
                    issue.validate_issue(draft["issues"][0], catalog, NEW)

    def test_duplicate_names_use_distinct_references_with_team_scope(self):
        catalog, draft = fixture()
        projects, _, labels = catalog
        projects["other-project"] = {**projects["project"], "id": "other-project", "slug": "other-relay-slug"}
        labels["other-label"] = {**labels["label"], "id": "other-label", "team_id": "other-team"}
        labels["literal-l1"] = {**labels["label"], "id": "literal-l1", "name": "L1"}
        issue.set_catalog_refs(projects.values(), "P")
        issue.set_catalog_refs(labels.values(), "L")
        self.assertEqual([row["ref"] for row in labels.values()], ["L2", "Bug", "L3", "L1"])
        new = draft["issues"][0]
        new.update(project="P2", labels=["L2", "L1"])
        saved = issue.validate_issue(new, catalog, NEW)
        self.assertEqual((saved.fields["projectId"], saved.fields["labelIds"]), ("other-project", ["label", "literal-l1"]))
        prompt = issue.prompt_for(DICTATION, catalog)
        self.assertIn("P2 = Relay\tstarted\tKIAN\tother-relay-slug", prompt)
        self.assertIn("L2 = Feature\tworkspace\tType", prompt)
        for unavailable in ("Feature", "L3"):
            with self.subTest(unavailable=unavailable), self.assertRaises(issue.FilingError):
                issue.validate_issue({**new, "labels": [unavailable]}, catalog, NEW)

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
            saved = issue.file_dictation(DICTATION, dry_run=True)["issues"][0]
            self.assertEqual((saved["blocked_by"], saved["blocks"]), (["KIAN-1"], ["KIAN-2"]))
            self.assertEqual(saved["fields"]["stateId"], "backlog-state")
            self.assertEqual(client.return_value.query.call_args.args[1], {"ids": ["KIAN-1", "KIAN-2"]})
            create.assert_not_called()
            dependencies.assert_not_called()

    def test_validates_all_drafts_before_writing_any_issue(self):
        catalog, draft = fixture()
        draft["issues"].append({**draft["issues"][0], "title": "Another issue", "estimate": 5})
        with (patch.object(issue, "configured_client"), patch.object(issue, "read_catalog", return_value=catalog),
              patch.object(issue, "opencode_draft", return_value=draft), patch.object(issue, "create_issue") as create,
              patch.object(issue, "add_dependencies") as dependencies):
            with self.assertRaises(issue.FilingError):
                issue.file_dictation(DICTATION)
        create.assert_not_called()
        dependencies.assert_not_called()

    def test_files_independent_issues_after_a_failed_create(self):
        catalog, draft = fixture()
        draft["issues"] += [
            {**draft["issues"][0], "title": "Unconfirmed issue", "blocked_by": [], "blocks": []},
            {**draft["issues"][0], "title": "Another issue", "priority": 4, "estimate": 1, "blocks": []},
        ]
        with (patch.object(issue, "configured_client") as client, patch.object(issue, "read_catalog", return_value=catalog),
              patch.object(issue, "opencode_draft", return_value=draft),
              patch.object(issue, "read_dependencies", return_value=DEPENDENCIES) as read,
              patch.object(issue, "add_dependencies", return_value=[]) as link):
            client.return_value.create_issue.side_effect = [
                {"identifier": "KIAN-3", "url": "https://linear.app/workspace/issue/KIAN-3"},
                RequestError(400),
                {"identifier": "KIAN-4", "url": "https://linear.app/workspace/issue/KIAN-4"},
            ]
            saved = issue.file_dictation(DICTATION)
        self.assertEqual([item["identifier"] for item in saved["issues"]], ["KIAN-3", "KIAN-4"])
        self.assertEqual(saved["unconfirmed"], ["Unconfirmed issue"])
        first, failed, last = (call.args[0] for call in client.return_value.create_issue.call_args_list)
        self.assertEqual(len({first["id"], failed["id"], last["id"]}), 3)
        self.assertEqual((last["priority"], last["estimate"]), (4, 1))
        read.assert_called_once_with(client.return_value, ["KIAN-1", "KIAN-2"])
        self.assertEqual([call.args[1:4] for call in link.call_args_list],
                         [(first["id"], [BEFORE], [AFTER]), (last["id"], [BEFORE], [])])

    def test_retries_same_issue_id_and_reconciles_a_lost_relation_response(self):
        catalog, draft = fixture()
        validated = issue.validate_issue(draft["issues"][0], catalog, NEW)
        fields = validated.fields
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
            missing = issue.add_dependencies(client, NEW, [BEFORE], [AFTER], {BEFORE: "KIAN-1", AFTER: "KIAN-2"})
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
              patch.object(issue, "read_dependencies", return_value=DEPENDENCIES),
              patch.object(issue, "add_dependencies", return_value=["KIAN-2"])):
            client.return_value.create_issue.return_value = {"identifier": "KIAN-3", "url": url}
            self.assertEqual(issue.file_dictation(DICTATION)["issues"][0], {
                "action": "Created", "identifier": "KIAN-3", "url": url, "unverified": ["KIAN-2"],
                "blocked_by": ["KIAN-1"], "blocks": ["KIAN-2"],
            })
            client.return_value.create_issue.assert_called_once()

    def test_mixed_dictation_patches_only_requested_fields_on_the_existing_issue(self):
        catalog, reply = fixture()
        reply["issues"][0].update(blocked_by=[], blocks=[])
        current, draft = update_fixture({"priority": 2, "status": "Done", "dueDate": "2026-11-24",
                                         "append_description": "Include timeouts.", "project": None,
                                         "addedLabels": ["Feature"], "removedLabels": ["Bug"]})
        reply["issues"].append(draft)
        expected = {"priority": 2, "stateId": "done-state", "dueDate": "2026-11-24", "projectId": None,
                    "addedLabelIds": ["label"], "removedLabelIds": ["old-label"],
                    "description": current["description"] + "\n\nInclude timeouts."}
        updated = {**current, "priority": 2, "state": {"id": "done-state"}, "dueDate": "2026-11-24",
                   "project": None, "description": expected["description"], "labelIds": ["keep-label", "label"]}
        with (patch.object(issue, "configured_client") as configured,
              patch.object(issue, "read_catalog", return_value=catalog),
              patch.object(issue, "opencode_draft", return_value=reply)):
            linear = configured.return_value
            linear.query.return_value = {"issue": current}
            preview = issue.file_dictation(DICTATION, dry_run=True)["issues"][1]
            self.assertEqual(preview["fields"], {"id": BEFORE, **expected})
            self.assertEqual((preview["action"], preview["identifier"]), ("Updated", "KIAN-1"))
            linear.create_issue.assert_not_called()
            linear.query.assert_called_once()
            linear.query.reset_mock()
            linear.create_issue.return_value = {"identifier": "KIAN-3", "url": None}
            linear.query.side_effect = [{"issue": current}, {"issue": current}, {"issueUpdate": {"issue": updated}}]
            saved = issue.file_dictation(DICTATION)
        self.assertEqual([(item["action"], item["identifier"]) for item in saved["issues"]],
                         [("Created", "KIAN-3"), ("Updated", "KIAN-1")])
        self.assertEqual(saved["issues"][1]["url"], current["url"])
        self.assertEqual(saved["unconfirmed"], [])
        linear.create_issue.assert_called_once()
        self.assertIn("issueUpdate", linear.query.call_args.args[0])
        self.assertEqual(linear.query.call_args.args[1], {"id": BEFORE, "input": expected})

    def test_unverified_update_target_stops_the_batch_before_writes(self):
        catalog, reply = fixture()
        current, draft = update_fixture({"priority": 2})
        reply["issues"].append(draft)
        for snapshot, question in ((None, "Which Linear issue"),
                                   ({**current, "updatedAt": "2026-01-02T12:00:00.000Z"}, "changed while I was drafting")):
            with (self.subTest(snapshot=snapshot), patch.object(issue, "configured_client") as configured,
                  patch.object(issue, "read_catalog", return_value=catalog),
                  patch.object(issue, "opencode_draft", return_value=reply)):
                linear = configured.return_value
                linear.query.return_value = {"issue": snapshot}
                with self.assertRaisesRegex(issue.ReplyNeeded, question):
                    issue.file_dictation(DICTATION)
                linear.create_issue.assert_not_called()
                linear.query.assert_called_once()
                self.assertTrue(linear.query.call_args.args[0].startswith("query"))

    def test_update_rejects_invalid_and_unsupported_changes(self):
        catalog, _ = fixture()
        invalid = ({"teamId": "other"}, {"priority": 0}, {"estimate": 5}, {"status": "Other state"},
                   {"project": "Other project"}, {"addedLabels": ["unknown"]}, {"dueDate": "2026-02-30"},
                   {"addedLabels": ["Bug"], "removedLabels": ["Bug"]},
                   {"description": "Replacement", "append_description": "New details"})
        linear = Mock()
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(issue.FilingError):
                current, draft = update_fixture(changes)
                linear.query.return_value = {"issue": current}
                issue.validate_update(linear, draft, catalog)

    def test_lost_update_response_confirms_the_append_once(self):
        current, _ = update_fixture({})
        fields = {"id": BEFORE, "description": current["description"] + "\n\nMore details."}
        saved = {**current, "description": fields["description"], "updatedAt": "2026-01-01T12:01:00.000Z"}
        linear = Mock()
        linear.query.side_effect = [{"issue": current}, RequestError(503), {"issue": saved}]
        with patch.object(issue.time, "sleep"):
            self.assertEqual(issue.update_issue(linear, current, fields), saved)
        writes = [call for call in linear.query.call_args_list if call.args[0].startswith("mutation")]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].args[1], {"id": BEFORE, "input": {"description": fields["description"]}})

    def test_concurrent_edit_is_preserved_before_saving_an_append(self):
        current, _ = update_fixture({})
        linear = Mock()
        linear.query.return_value = {"issue": {**current, "description": "Another writer added details.",
                                               "updatedAt": "2026-01-01T12:01:00.000Z"}}
        with self.assertRaisesRegex(issue.FilingError, "changed before the update could be saved"):
            issue.update_issue(linear, current, {"id": BEFORE, "description": current["description"] + "\n\nMy details."})
        linear.query.assert_called_once()

    def test_update_can_attach_an_image_without_recreating_an_existing_dependency(self):
        catalog, _ = fixture()
        current, draft = update_fixture({})
        draft.update(attach_image=True, blocked_by=["KIAN-2"])
        dictation = replace(DICTATION, image_use="attach", image=("image/png", b"fixture"))
        asset = "https://uploads.linear.app/workspace/asset/clipboard.png"
        with (patch.object(issue, "configured_client") as configured,
              patch.object(issue, "read_catalog", return_value=catalog),
              patch.object(issue, "opencode_draft", return_value={"issues": [draft], "image_relevant": True}),
              patch.object(issue, "read_dependencies", return_value={"KIAN-2": AFTER}),
              patch.object(issue, "upload_image", return_value=asset),
              patch.object(issue, "update_issue", return_value=current) as update):
            linear = configured.return_value
            linear.issue_dependencies.return_value = [{"type": "blocks", "issue": {"id": AFTER}}]
            linear.query.return_value = {"issue": current}
            saved = issue.file_dictation(dictation)["issues"][0]
        self.assertEqual((saved["blocked_by"], saved["unverified"]), (["KIAN-2"], []))
        update.assert_called_once_with(linear, current, {"id": BEFORE,
                                       "description": current["description"] + f"\n\n![Clipboard image](<{asset}>)"})
        linear.issue_dependencies.assert_called_once_with(BEFORE)
        linear.query.assert_called_once()
        linear.create_issue.assert_not_called()

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
        self.assertEqual((dictation.transcript, dictation.title), (" File an issue ", "Clusterfork"))
        self.assertEqual(UUID(dictation.issue_id).version, 4)
        copy.assert_called_once_with("File an issue")
        image.assert_not_called()
        self.assertTrue(jobs.empty())

    def test_capture_snapshots_an_explicit_image_before_copying_the_transcript(self):
        released, stopping, jobs = Event(), Event(), SimpleQueue()
        released.set()
        for transcript, image_use in (
            ("File an issue and copy the image currently in my clipboard.", "attach"),
            ("File an issue. Check the image of my clipboard.", "reference"),
        ):
            with self.subTest(transcript=transcript):
                sequence = []
                with (patch.object(shortcut, "HEX_REGISTRATION") as registration,
                      patch.object(shortcut, "hex_request", side_effect=[{"id": 7, "ownerToken": "fixture"}, {"transcript": transcript}]),
                      patch.object(shortcut, "clipboard_image", side_effect=lambda: sequence.append("image") or ("image/png", b"image")),
                      patch.object(shortcut, "copy_transcript", side_effect=lambda text: sequence.append(text))):
                    registration.read_text.return_value = '{"token":"fixture","port":1234}'
                    shortcut.capture(released, stopping, jobs)
                self.assertEqual(sequence, ["image", transcript])
                dictation = jobs.get_nowait()
                self.assertEqual(dictation.image_use, image_use)
                self.assertEqual(dictation.image, ("image/png", b"image"))
        self.assertIsNone(shortcut.IMAGE_REQUEST.search("Add a feature to copy the image in my clipboard."))
        self.assertIsNone(shortcut.IMAGE_REQUEST.search("Add a feature to check the image of my clipboard."))
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
        dictation = replace(DICTATION, image_use="attach", image=("image/png", b"original"))
        question = issue.ReplyNeeded("Which project?")
        answer = "Clusterfork\nUse the relay description."
        with patch.object(shortcut.subprocess, "run", side_effect=[
            CompletedProcess("notify-send", 0, stdout="default\n"),
            CompletedProcess("linear_reply.py", 0, stdout=answer + "\n"),
        ]) as run:
            shortcut.offer_reply(jobs, dictation, question)
        self.assertIn("--action=default=Reply", run.call_args_list[0].args[0])
        self.assertEqual(Path(run.call_args_list[1].args[0][1]).name, "linear_reply.py")
        self.assertEqual(run.call_args_list[1].kwargs["input"], "Which project?")
        self.assertEqual(jobs.get_nowait(), replace(dictation, answers=(("Which project?", answer),)))

    def test_image_reply_reads_the_new_clipboard_and_keeps_prior_answers(self):
        jobs = SimpleQueue()
        image = ("image/jpeg", b"replacement")
        for image_use, expected in (("attach", "attach"), (None, "reference")):
            with self.subTest(image_use=image_use):
                dictation = replace(DICTATION, image_use=image_use, answers=(("Which project?", "Clusterfork"),))
                with (patch.object(shortcut.subprocess, "run", return_value=CompletedProcess("notify-send", 0, stdout="reply\n")) as run,
                      patch.object(shortcut, "clipboard_image", return_value=image)):
                    shortcut.offer_reply(jobs, dictation, issue.ReplyNeeded("Copy a new image.", copy_image=True))
                self.assertEqual(jobs.get_nowait(), replace(dictation, image_use=expected, image=image))
                self.assertIn("--action=default=Reply", run.call_args.args[0])
                run.assert_called_once()

    def test_image_reply_opens_a_dialog_until_an_image_can_be_read(self):
        jobs = SimpleQueue()
        image = ("image/png", b"copied while the dialog was open")
        with (patch.object(shortcut.subprocess, "run", side_effect=[
            CompletedProcess("notify-send", 0, stdout="reply\n"),
            CompletedProcess("zenity", 0),
            CompletedProcess("zenity", 0),
        ]) as run, patch.object(shortcut, "clipboard_image", side_effect=[None, None, image])):
            shortcut.offer_reply(jobs, DICTATION, issue.ReplyNeeded(issue.IMAGE_QUESTION, copy_image=True))
        for call in run.call_args_list[1:]:
            self.assertEqual(call.args[0][0], "zenity")
            self.assertIn("--ok-label=Use Clipboard Image", call.args[0])
        self.assertEqual(jobs.get_nowait(), replace(DICTATION, image_use="reference", image=image))
        self.assertTrue(jobs.empty())

    def test_holds_for_missing_and_unrelated_images_before_upload_or_creation(self):
        catalog, _ = fixture()
        dictation = replace(DICTATION, image_use="attach")
        with (patch.object(issue, "configured_client") as client, patch.object(issue, "read_catalog", return_value=catalog),
              patch.object(issue, "opencode_draft", return_value={"image_relevant": False}),
              patch.object(issue, "upload_image") as upload, patch.object(issue, "create_issue") as create):
            with self.assertRaises(issue.ReplyNeeded) as missing:
                issue.file_dictation(dictation)
            client.assert_not_called()
            before_drafting, = self.attempts.iterdir()
            self.assertEqual({path.name: path.read_text() for path in before_drafting.iterdir()},
                             {"transcript.txt": dictation.transcript})
            with self.assertRaises(issue.ReplyNeeded) as unrelated:
                issue.file_dictation(replace(dictation, image=("image/png", b"wrong image")))
        self.assertTrue(missing.exception.copy_image and unrelated.exception.copy_image)
        upload.assert_not_called()
        create.assert_not_called()

    def test_an_image_request_missed_during_capture_gets_an_actionable_reply(self):
        catalog, _ = fixture()
        dictation = replace(DICTATION, transcript="File a task for the meeting check the image of my clipboard")
        response = {"clarification": "Could you attach the clipboard image?", "image_relevant": False}
        with (patch.object(issue, "configured_client"), patch.object(issue, "read_catalog", return_value=catalog),
              patch.object(issue, "opencode_draft", return_value=response), patch.object(issue, "create_issue") as create):
            self.assertIsNone(shortcut.IMAGE_REQUEST.search(dictation.transcript))
            with self.assertRaisesRegex(issue.ReplyNeeded, "Copy an image.*click Reply") as reply:
                issue.file_dictation(dictation)
        self.assertTrue(reply.exception.copy_image)
        create.assert_not_called()

    def test_relevant_image_waits_with_the_draft_for_other_clarification(self):
        catalog, _ = fixture()
        dictation = replace(DICTATION, image_use="attach", image=("image/png", b"matching image"))
        with (patch.object(issue, "configured_client"), patch.object(issue, "read_catalog", return_value=catalog),
              patch.object(issue, "opencode_draft", return_value={"image_relevant": True, "clarification": "Which project?"})):
            with self.assertRaisesRegex(issue.ReplyNeeded, "Which project") as reply:
                issue.file_dictation(dictation)
        self.assertFalse(reply.exception.copy_image)

    def test_relevant_image_is_uploaded_only_for_an_attachment_request(self):
        catalog, draft = fixture()
        draft["image_relevant"] = True
        draft["issues"].append({**draft["issues"][0], "title": "Another issue"})
        asset = "https://uploads.linear.app/workspace/asset/clipboard.png"
        for image_use in ("reference", "attach"):
            with self.subTest(image_use=image_use):
                draft["issues"][0]["attach_image"] = image_use == "attach"
                dictation = replace(DICTATION, image_use=image_use, image=("image/png", b"correct image"))
                with (patch.object(issue, "configured_client") as client, patch.object(issue, "read_catalog", return_value=catalog),
                      patch.object(issue, "opencode_draft", return_value=draft),
                      patch.object(issue, "read_dependencies", return_value=DEPENDENCIES),
                      patch.object(issue, "upload_image", return_value=asset) as upload, patch.object(issue, "add_dependencies", return_value=[])):
                    client.return_value.create_issue.return_value = {"identifier": "KIAN-3", "url": "https://linear.app/workspace/issue/KIAN-3"}
                    self.assertTrue(issue.file_dictation(dictation, dry_run=True)["clipboard_image"])
                    upload.assert_not_called()
                    issue.file_dictation(dictation)
                fields, other = [call.args[0] for call in client.return_value.create_issue.call_args_list]
                self.assertEqual(fields["id"], NEW)
                self.assertEqual(other["description"], draft["issues"][1]["description"])
                if image_use == "attach":
                    upload.assert_called_once_with(client.return_value, dictation.image)
                    self.assertTrue(fields["description"].endswith(f"![Clipboard image](<{asset}>)"))
                else:
                    upload.assert_not_called()
                    self.assertEqual(fields["description"], draft["issues"][0]["description"])

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
