"""Check Hex's MCP execution, trace, notification and recording boundaries."""

from contextlib import redirect_stderr
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from queue import SimpleQueue
import subprocess
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

DICTATION = issue.Dictation("Please file this", None, str(UUID(int=1)))
URL = "https://linear.app/workspace/issue/KIAN-3/check-the-relay"
NOTICE = {"title": "Created KIAN-3", "message": "Blocked by KIAN-1.", "url": URL}


def tool_event(name, arguments, *, status="completed", call_id="call_fixture"):
    return {"type": "tool_use", "sessionID": "ses_fixture123", "part": {
        "id": call_id, "tool": name, "state": {"status": status, "input": arguments}}}


def response(*events, text="Created the issue."):
    events = [*events, {"type": "text", "part": {"messageID": "final", "text": text}},
              {"type": "step_finish", "part": {"messageID": "final", "reason": "stop"}}]
    return ("\n".join(map(json.dumps, events)) + "\n").encode()


class LinearDictationTests(unittest.TestCase):
    def setUp(self):
        state = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(issue.os.environ, {"XDG_STATE_HOME": state}))
        self.attempts = Path(state) / "hex/linear/runs"

    def test_saves_verbatim_stdin_and_mcp_context_without_a_json_draft(self):
        stdout_bytes = response(
            tool_event("linear_list_projects", {"fields": ["name", "summary", "description"]}),
            tool_event("linear_save_issue", {"title": "Check the relay", "priority": 2, "estimate": 3}),
            tool_event("hex_notify", NOTICE),
        )
        stderr_bytes = b"Fixture diagnostic\r\n"
        transcript = "  Réparer le relais.\r\nMore detail.\n "
        dictation = replace(DICTATION, transcript=transcript, title="Relay – Linear",
                            answers=(("Which project?", "Relay"),))

        def complete(command, *, stdout, stderr, **kwargs):
            self.assertEqual((attempt / "prompt.txt").read_bytes(), kwargs["input"])
            stdout.write(stdout_bytes)
            stderr.write(stderr_bytes)
            return CompletedProcess(command, 0)

        logs = StringIO()
        with patch.object(issue.subprocess, "run", side_effect=complete) as run, redirect_stderr(logs):
            attempt = issue.start_attempt_log(dictation)
            result = issue.run_opencode(dictation, attempt)
        self.assertEqual(result.notifications, (NOTICE,))
        self.assertEqual(result.text, "Created the issue.")
        self.assertIsNone(result.error)
        command = run.call_args.args[0]
        self.assertIn("--archived", command)
        self.assertEqual(command[command.index("--model") + 1], "opencode-go/gpt-6-luna#max")
        prompt = run.call_args.kwargs["input"].decode()
        self.assertIn(f"Verbatim transcript\n{transcript}\n\nFollow-up question\nWhich project?\nFollow-up answer\nRelay", prompt)
        self.assertIn("Foreground window: Relay – Linear", prompt)
        self.assertIn("projects, their summaries and descriptions through Linear MCP", prompt)
        self.assertIn("call hex_notify", prompt)
        self.assertIn(f"{DICTATION.issue_id} attempt={attempt}", logs.getvalue())
        self.assertIn(f"{DICTATION.issue_id} archived OpenCode session ses_fixture123", logs.getvalue())
        self.assertNotIn(transcript, logs.getvalue())
        self.assertEqual(attempt.stat().st_mode & 0o777, 0o700)
        expected = {"transcript.txt": transcript.encode(), "prompt.txt": run.call_args.kwargs["input"],
                    "stdout.jsonl": stdout_bytes, "stderr.txt": stderr_bytes}
        self.assertEqual({path.name: path.read_bytes() for path in attempt.iterdir()}, expected)
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in attempt.iterdir()))

    def test_partial_failures_keep_completed_notifications_and_exact_output(self):
        partial = (json.dumps(tool_event("hex_notify", NOTICE)) + "\n").encode() + b'{"type":"text","part":"\xc3'
        diagnostic = b"Connection lost\n"
        for timeout in (True, False):
            with self.subTest(timeout=timeout):
                def incomplete(command, *, stdout=None, stderr=None, **kwargs):
                    if command[1] == "api":
                        self.assertIn("/api/session/ses_fixture123/interrupt", command)
                        return CompletedProcess(command, 0)
                    stdout.write(partial)
                    stderr.write(diagnostic)
                    if timeout:
                        raise TimeoutExpired(command, 1200)
                    return CompletedProcess(command, 1)

                with patch.object(issue.subprocess, "run", side_effect=incomplete) as run:
                    attempt = issue.start_attempt_log(DICTATION)
                    result = issue.run_opencode(DICTATION, attempt)
                self.assertTrue(result.error)
                self.assertEqual(result.notifications, (NOTICE,))
                self.assertEqual(result.session_id, "ses_fixture123")
                self.assertEqual((attempt / "prompt.txt").read_bytes(), run.call_args_list[0].kwargs["input"])
                self.assertEqual((attempt / "stdout.jsonl").read_bytes(), partial)
                self.assertEqual((attempt / "stderr.txt").read_bytes(), diagnostic)

    def test_only_completed_notification_tools_are_used_once(self):
        completed = tool_event("hex_notify", NOTICE)
        events = response(tool_event("hex_notify", NOTICE, status="running", call_id="unfinished"),
                          tool_event("hex_notify", NOTICE, status="error", call_id="failed"),
                          completed, completed,
                          tool_event("linear_save_issue", {"description": "Arbitrary hex_notify text"}))
        self.assertEqual(issue.parse_response(events).notifications, (NOTICE,))
        for url in ("https://linear.app.evil/issue/KIAN-3", "https://user@linear.app/a/issue/KIAN-3",
                    "file:///tmp/example", "https://linear.app/a/issue/KIAN-3\n--command"):
            with self.subTest(url=url):
                result = issue.parse_response(response(tool_event("hex_notify", {**NOTICE, "url": url})))
                self.assertTrue(result.error)
                self.assertFalse(result.notifications)

    def test_only_the_final_text_is_reported_after_tools(self):
        events = [
            {"type": "text", "part": {"messageID": "early", "text": "Looking up the issue."}},
            {"type": "step_finish", "part": {"messageID": "early", "reason": "tool-calls"}},
        ]
        result = issue.parse_response(response(*events, tool_event("hex_notify", NOTICE), text="All done."))
        self.assertEqual(result.text, "All done.")
        self.assertIsNone(result.error)
        self.assertTrue(issue.parse_response(json.dumps(events[1]).encode()).error)

    def test_an_unreported_result_is_an_error(self):
        with patch.object(issue, "run_opencode", return_value=issue.AgentResult("Done", "ses_fixture123", ())):
            self.assertTrue(issue.file_dictation(DICTATION).error)
            self.assertIsNone(issue.file_dictation(DICTATION, dry_run=True).error)

    def test_missing_clipboard_image_waits_before_starting_opencode(self):
        dictation = replace(DICTATION, image_use="attach")
        with patch.object(issue, "run_opencode") as run:
            with self.assertRaises(issue.ReplyNeeded) as missing:
                issue.file_dictation(dictation)
        self.assertTrue(missing.exception.copy_image)
        run.assert_not_called()
        attempt, = self.attempts.iterdir()
        self.assertEqual({path.name: path.read_text() for path in attempt.iterdir()},
                         {"transcript.txt": dictation.transcript})

    def test_mcp_agent_can_request_a_new_image_or_an_ordinary_reply(self):
        for copy_image in (True, False):
            with self.subTest(copy_image=copy_image):
                result = issue.parse_response(response(tool_event("hex_reply", {
                    "question": "Please supply the missing detail.", "copy_image": copy_image})))
                self.assertEqual(str(result.reply), "Please supply the missing detail.")
                self.assertEqual(result.reply.copy_image, copy_image)
                self.assertEqual(result.session_id, "ses_fixture123")

    def test_image_bytes_are_private_and_the_reply_uses_the_same_session(self):
        dictation = replace(DICTATION, image_use="attach", image=("image/png", b"clipboard bytes"),
                            session_id="ses_fixture123")

        def complete(command, *, stdout, stderr, **kwargs):
            image_path = Path(command[command.index("--file") + 1])
            self.assertEqual(image_path.read_bytes(), dictation.image[1])
            self.assertEqual(image_path.stat().st_mode & 0o777, 0o600)
            prompt = kwargs["input"].decode()
            self.assertIn(f"Clipboard file: {image_path}", prompt)
            self.assertIn("Clipboard MIME type: image/png", prompt)
            self.assertIn("Clipboard byte length: 15", prompt)
            stdout.write(response(tool_event("hex_notify", NOTICE)))
            return CompletedProcess(command, 0)

        with patch.object(issue.subprocess, "run", side_effect=complete) as run:
            issue.file_dictation(dictation)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--session") + 1], "ses_fixture123")
        self.assertFalse(Path(command[command.index("--file") + 1]).exists())

    def test_preview_selects_an_agent_with_read_only_permissions(self):
        with patch.object(issue.subprocess, "run", return_value=CompletedProcess("opencode", 0, stdout=response(text="Proposed changes."))) as run:
            self.assertEqual(issue.run_opencode(DICTATION, None, dry_run=True).text, "Proposed changes.")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--agent") + 1], issue.PREVIEW_AGENT)
        self.assertIn("Preview only: true", run.call_args.kwargs["input"].decode())

    def test_worker_notifies_completed_work_and_continues_replies_in_the_same_session(self):
        result = issue.AgentResult("Waiting", "ses_fixture123", (NOTICE,), issue.ReplyNeeded("Which issue?"))
        jobs = Mock()
        jobs.get.side_effect = [DICTATION, StopIteration]
        with (patch.object(shortcut, "file_dictation", return_value=result),
              patch.object(shortcut, "notify_saved_issue") as notify,
              patch.object(shortcut, "Thread") as thread,
              self.assertRaises(StopIteration)):
            shortcut.process_jobs(jobs, False)
        notify.assert_called_once_with(NOTICE, DICTATION.issue_id)
        pending = thread.call_args.kwargs["args"][1]
        self.assertEqual(pending.session_id, "ses_fixture123")
        self.assertEqual(pending.transcript, DICTATION.transcript)

    def test_workspace_tools_keep_project_context_and_upload_signed_image_bytes(self):
        script = r'''
import assert from "node:assert/strict";
import { readFile, mkdtemp, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
const source = await readFile(process.argv[2]);
const plugin = (await import(`data:text/javascript;base64,${source.toString("base64")}`)).default;
const tools = new Map();
const hooks = new Map();
await plugin.setup({ tool: {
  transform: async (edit) => edit({ add: (tool) => tools.set(tool.name, tool) }),
  hook: async (name, callback) => hooks.set(name, callback),
}});
assert.deepEqual([...tools.keys()], ["hex_notify", "hex_reply", "hex_upload_clipboard"]);
const notice = { title: "Created KIAN-3", message: "Saved", url: "https://linear.app/w/issue/KIAN-3" };
const notification = await tools.get("hex_notify").execute(notice);
assert.equal(notification.output, undefined);
assert.match(notification.content, /queued/);
const reply = { question: "Which project?", copy_image: false };
const question = await tools.get("hex_reply").execute(reply);
assert.equal(question.output, undefined);
assert.match(question.content, /resume this session/);
for (const agent of ["linear-issue", "linear-issue-preview"]) {
  const event = { agent, tool: "linear_list_projects", input: { cursor: "next", query: "Relay" } };
  hooks.get("execute.before")(event);
  assert.equal(event.input.cursor, "next");
  assert.equal(event.input.query, "Relay");
  assert(event.input.fields.includes("summary") && event.input.fields.includes("description"));
  const result = { agent, tool: event.tool, status: "completed", result: { output: JSON.stringify({
    projects: [{ id: "P-KIAN-1", name: "Relay", status: { name: "Started" },
      teams: [{ key: "KIAN" }], summary: "Service reliability", description: "Owns\nrelay diagnostics",
      url: "https://linear.app/w/project/relay" }], hasNextPage: true, cursor: "page-two",
  }) } };
  hooks.get("execute.after")(result);
  assert.equal(result.result.output, undefined);
  assert.match(result.result.content[0].text, /Relay\tStarted\tKIAN\tService reliability\tOwns relay diagnostics/);
  assert.match(result.result.content[0].text, /Next page cursor: page-two/);
  const issues = { agent, tool: "linear_list_issues", input: { project: "Relay", limit: 3 } };
  hooks.get("execute.before")(issues);
  assert.equal(issues.input.limit, 3);
  assert.equal(issues.input.project, "Relay");
  assert(!issues.input.fields.includes("description"));
  const found = { agent, tool: issues.tool, status: "completed", result: { output: {
    issues: [{ id: "KIAN-3", title: "Diagnose relay", project: "Relay", status: "Backlog",
      statusType: "backlog", labels: ["Bug"], priority: { name: "High" }, estimate: { name: "3 Points" },
      createdAt: "2026-11-19T12:00:00Z" }], hasNextPage: false,
  } } };
  hooks.get("execute.after")(found);
  assert.match(found.result.content[0].text, /KIAN-3\tDiagnose relay\tRelay\tBacklog\tbacklog\tBug\tHigh\t3 Points\t2026-11-19/);
}
const ordinary = { agent: "build", tool: "linear_list_projects", input: {} };
hooks.get("execute.before")(ordinary);
assert.deepEqual(ordinary.input, {});
const directory = await mkdtemp(path.join(tmpdir(), "hex-linear-"));
const file = path.join(directory, "clipboard.png");
const bytes = Buffer.from([0, 255, 10, 34]);
await writeFile(file, bytes, { mode: 0o600 });
let uploaded;
globalThis.fetch = async (url, request) => { uploaded = { url, request }; return { ok: true }; };
try {
  const headers = { "Content-Type": "image/png", "X-Signed": "Exact Header Value" };
  const upload = tools.get("hex_upload_clipboard");
  await upload.execute({ file, url: "https://storage.example.org/image?signature=fixture", headers });
  assert.deepEqual(uploaded.request.body, bytes);
  assert.deepEqual(uploaded.request.headers, headers);
  assert.equal(uploaded.request.method, "PUT");
  assert.equal(uploaded.request.redirect, "error");
  await assert.rejects(upload.execute({ file, url: "http://storage.example.org/image", headers }));
  await assert.rejects(upload.execute({ file: "/tmp/another.png", url: "https://storage.example.org/image", headers }));
} finally {
  await rm(directory, { recursive: true, force: true });
}
'''
        completed = subprocess.run(["node", "--input-type=module", "-", str(ROOT / "hex/linear-search.js")],
                                   input=script, text=True, capture_output=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

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



if __name__ == "__main__":
    unittest.main()
