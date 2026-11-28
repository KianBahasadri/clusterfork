"""Check Hex's MCP execution, trace, notification and recording boundaries."""

from contextlib import nullcontext, redirect_stderr
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
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
import urllib.error
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hex")]
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
        issue.os.environ.pop("LINEAR_API_KEY", None)
        credential = Path(state) / "credentials.env"
        credential.write_text('LINEAR_API_KEY="fixture-linear-key"\n')
        self.enterContext(patch.object(issue, "DOTENV", credential))
        self.attempts = Path(state) / "hex/linear/runs"
        self.server = issue.OpenCodeServer(
            "http://127.0.0.1:4321", "fixture-server-password", Mock(poll=Mock(return_value=None)),
        )
        self.real_private_server = issue.private_server
        self.enterContext(patch.object(
            issue, "private_server", side_effect=lambda *args, **kwargs: nullcontext(self.server),
        ))
        self.enterContext(patch.object(
            issue, "mcp_servers", return_value=[{"name": "linear", "status": {"status": "connected"}}],
        ))

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
        stages = []
        with patch.object(issue.subprocess, "run", side_effect=complete) as run, redirect_stderr(logs):
            attempt = issue.start_attempt_log(dictation)
            result = issue.run_opencode(dictation, attempt, progress=lambda *args: stages.append(args))
        self.assertEqual(stages, [(2, 4, "Connecting to Linear"), (3, 4, "Creating Linear issue")])
        self.assertEqual(result.notifications, (NOTICE,))
        self.assertEqual(result.text, "Created the issue.")
        self.assertIsNone(result.error)
        command = run.call_args.args[0]
        self.assertIn("--archived", command)
        self.assertNotIn("--standalone", command)
        self.assertEqual(command[command.index("--server") + 1], self.server.url)
        self.assertEqual(command[command.index("--model") + 1], "opencode-go/deepseek-v4.1-flash#max")
        prompt = run.call_args.kwargs["input"].decode()
        self.assertEqual(run.call_args.kwargs["env"]["LINEAR_API_KEY"], "fixture-linear-key")
        self.assertEqual(run.call_args.kwargs["env"]["OPENCODE_PASSWORD"], self.server.password)
        self.assertNotIn("fixture-linear-key", prompt)
        self.assertNotIn("fixture-linear-key", logs.getvalue())
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
        self.assertEqual({path.name: path.read_bytes() for path in attempt.iterdir()},
                         {**expected, "mcp.json": (attempt / "mcp.json").read_bytes()})
        self.assertEqual(json.loads((attempt / "mcp.json").read_text())["outcome"], "connected")
        self.assertNotIn("fixture-linear-key", (attempt / "mcp.json").read_text())
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in attempt.iterdir()))

    def test_partial_failures_keep_completed_notifications_and_exact_output(self):
        partial = (json.dumps(tool_event("hex_notify", NOTICE)) + "\n").encode() + b'{"type":"text","part":"\xc3'
        diagnostic = b"Connection lost\n"
        for timeout in (True, False):
            with self.subTest(timeout=timeout):
                def incomplete(command, *, stdout=None, stderr=None, **kwargs):
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

    def test_delayed_linear_mcp_connection_blocks_the_first_turn(self):
        states = ["pending", "pending", "connected"]

        def status(*_args, **_kwargs):
            self.assertFalse(run.called)
            return [{"name": "linear", "status": {"status": states.pop(0)}}]

        def complete(command, **_kwargs):
            return CompletedProcess(command, 0, stdout=response())

        with (patch.object(issue, "mcp_servers", side_effect=status) as check,
              patch.object(issue.time, "sleep") as sleep,
              patch.object(issue.subprocess, "run", side_effect=complete) as run):
            result = issue.run_opencode(DICTATION, None)
        self.assertIsNone(result.error)
        self.assertEqual(check.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][run.call_args.args[0].index("--server") + 1], self.server.url)

    def test_linear_mcp_startup_failure_prevents_prompt_submission(self):
        for state, message in (
            ("failed", "failed to start"),
            ("needs_auth", "needs authentication"),
            ("disabled", "disabled in the Hex OpenCode workspace"),
        ):
            with self.subTest(state=state):
                attempt = issue.start_attempt_log(DICTATION)
                status = {"status": state, "error": "fixture failure with fixture-linear-key"}
                with (patch.object(issue, "mcp_servers",
                                   return_value=[{"name": "linear", "status": status}]),
                      patch.object(issue.subprocess, "run") as run):
                    result = issue.run_opencode(DICTATION, attempt)
                self.assertIn(message, result.error)
                run.assert_not_called()
                diagnostic = (attempt / "mcp.json").read_text()
                self.assertIn(f'"outcome": "{state}"', diagnostic)
                self.assertNotIn("fixture-linear-key", diagnostic)

    def test_mcp_diagnostics_redact_bearer_text_without_breaking_json(self):
        attempt = issue.start_attempt_log(DICTATION)
        issue.save_mcp_diagnostic(
            attempt,
            outcome="failed",
            servers=[{
                "name": "linear",
                "title": "known-secret",
                "status": {"status": "failed", "error": "Unauthorized Bearer fixture-token"},
            }],
            secret="known-secret",
        )
        text = (attempt / "mcp.json").read_text()
        diagnostic = json.loads(text)
        self.assertNotIn("fixture-token", text)
        self.assertNotIn("known-secret", text)
        self.assertEqual(diagnostic["servers"][0]["status"]["error"], "Unauthorized Bearer [redacted]")
        self.assertEqual(diagnostic["servers"][0]["title"], "[redacted]")

    def test_linear_mcp_timeout_prevents_prompt_submission(self):
        attempt = issue.start_attempt_log(DICTATION)
        status = [{"name": "linear", "status": {"status": "pending"}}]
        with (patch.object(issue, "MCP_STARTUP_SECONDS", 1),
              patch.object(issue.time, "monotonic", side_effect=[0, 0, 0, 1]),
              patch.object(issue.time, "sleep"),
              patch.object(issue, "mcp_servers", return_value=status),
              patch.object(issue.subprocess, "run") as run):
            result = issue.run_opencode(DICTATION, attempt)
        self.assertIn("did not connect within 1 seconds", result.error)
        run.assert_not_called()
        self.assertEqual(json.loads((attempt / "mcp.json").read_text())["outcome"], "timeout")

    def test_private_server_stops_its_child_on_success_and_failure(self):
        for failure in (None, ValueError("fixture failure")):
            with self.subTest(failure=failure):
                process = Mock()
                process.poll.return_value = None
                with (patch.object(issue, "free_port", return_value=4321),
                      patch.object(issue.subprocess, "Popen", return_value=process)):
                    if failure is None:
                        with self.real_private_server("opencode", {}) as server:
                            self.assertEqual(server.url, "http://127.0.0.1:4321")
                    else:
                        with self.assertRaisesRegex(ValueError, "fixture failure"):
                            with self.real_private_server("opencode", {}):
                                raise failure
                process.terminate.assert_called_once()
                process.wait.assert_called()

    def test_run_opencode_stops_private_server_when_mcp_fails(self):
        process = Mock()
        process.poll.return_value = None
        with (patch.object(issue, "private_server", self.real_private_server),
              patch.object(issue, "free_port", return_value=4321),
              patch.object(issue.subprocess, "Popen", return_value=process),
              patch.object(issue, "mcp_servers",
                           return_value=[{"name": "linear", "status": {"status": "failed"}}]),
              patch.object(issue.subprocess, "run") as run):
            result = issue.run_opencode(DICTATION, None)
        self.assertIn("failed to start", result.error)
        run.assert_not_called()
        process.terminate.assert_called_once()

    def test_mcp_agent_can_request_a_reply(self):
        result = issue.parse_response(response(tool_event("hex_reply", {
            "question": "Please supply the missing detail."})))
        self.assertEqual(str(result.reply), "Please supply the missing detail.")
        self.assertEqual(result.session_id, "ses_fixture123")

    def test_prompt_includes_project_hint_and_recent_context_only_when_present(self):
        without_hint = issue.prompt_for(DICTATION, recent="")
        self.assertNotIn("Suggested project", without_hint)
        self.assertNotIn("Recent prior Hex requests", without_hint)

        with_hint = issue.prompt_for(
            replace(DICTATION, suggested_project="Clusterfork"),
            recent="- Older dictation [-> KIAN-9 (https://linear.app/w/issue/KIAN-9)]",
        )
        self.assertIn("Suggested project (non-binding hint): Clusterfork", with_hint)
        self.assertIn("Recent prior Hex requests (background reference context only;", with_hint)
        self.assertIn("- Older dictation [-> KIAN-9 (https://linear.app/w/issue/KIAN-9)]", with_hint)

    def test_fetch_linear_projects_filters_sorts_caches_and_falls_back(self):
        payload = {
            "data": {
                "projects": {
                    "nodes": [
                        {"id": "p-arch", "name": "Archived", "archivedAt": "2026-01-01T00:00:00Z", "status": {"name": "Started", "type": "started"}},
                        {"id": "p-done", "name": "DoneProj", "archivedAt": None, "status": {"name": "Completed", "type": "completed"}},
                        {"id": "p-plan", "name": "AlphaPlanned", "archivedAt": None, "status": {"name": "Planned", "type": "planned"}},
                        {"id": "p-start", "name": "ZetaStarted", "archivedAt": None, "status": {"name": "Started", "type": "started"}},
                    ],
                    "pageInfo": {"endCursor": None, "hasNextPage": False},
                }
            }
        }
        fake_response = MagicContext(BytesIO(json.dumps(payload).encode("utf-8")))
        with patch.object(issue.urllib.request, "urlopen", return_value=fake_response) as urlopen:
            projects = issue.fetch_linear_projects(timeout=5)
        self.assertEqual([p["name"] for p in projects], ["ZetaStarted", "AlphaPlanned"])
        urlopen.assert_called_once()
        cache = issue.state_dir() / "projects_cache.json"
        self.assertEqual(cache.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p["name"] for p in issue.load_cached_projects()], ["ZetaStarted", "AlphaPlanned"])

        with patch.object(issue.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")):
            fallback = issue.fetch_linear_projects(timeout=5)
        self.assertEqual([p["name"] for p in fallback], ["ZetaStarted", "AlphaPlanned"])

    def test_history_persistence_dedupes_evicts_and_prunes_orphaned_screenshots(self):
        shot_a = ("image/png", b"png-a")
        first = replace(DICTATION, issue_id="req-old", transcript="First", images=(shot_a,))
        with patch.object(issue, "HISTORY_LIMIT", 2):
            issue.record_history(first, (NOTICE,))
            old_shots = issue.load_history()[0]["screenshots"]
            self.assertEqual(len(old_shots), 1)
            old_shot_path = Path(old_shots[0])
            self.assertEqual(old_shot_path.read_bytes(), b"png-a")
            self.assertEqual(old_shot_path.stat().st_mode & 0o777, 0o600)

            # Re-recording same request ID moves it to front without duplicating.
            issue.record_history(replace(first, transcript="First updated"), (NOTICE,))
            self.assertEqual(len(issue.load_history()), 1)
            self.assertEqual(issue.load_history()[0]["transcript"], "First updated")

            issue.record_history(replace(DICTATION, issue_id="req-2", transcript="Second"), ())
            issue.record_history(replace(DICTATION, issue_id="req-3", transcript="Third"), ())
            kept = issue.load_history()
            self.assertEqual([entry["id"] for entry in kept], ["req-3", "req-2"])
            self.assertFalse(old_shot_path.exists())

    def test_recent_context_bounds_age_count_and_characters_oldest_first(self):
        now = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)
        entries = [
            {"id": "cur", "created_at": (now - timedelta(minutes=1)).isoformat(), "transcript": "Current", "issues": []},
            {"id": "e1", "created_at": (now - timedelta(minutes=5)).isoformat(), "transcript": "Newest", "issues": [NOTICE]},
            {"id": "e2", "created_at": (now - timedelta(minutes=20)).isoformat(), "transcript": "Older", "issues": []},
            {"id": "old", "created_at": (now - timedelta(hours=2)).isoformat(), "transcript": "Too old", "issues": []},
        ]
        issue._write_private_json(issue.state_dir() / "history.json", entries)
        rendered = issue.recent_context(exclude_id="cur", now=now)
        self.assertEqual(rendered, "\n".join([
            "- Older",
            f"- Newest [-> Created KIAN-3 ({URL})]",
        ]))
        with patch.object(issue, "RECENT_CONTEXT_COUNT", 1):
            self.assertEqual(issue.recent_context(exclude_id="cur", now=now), f"- Newest [-> Created KIAN-3 ({URL})]")
        with patch.object(issue, "RECENT_CONTEXT_MAX_CHARS", 10):
            self.assertEqual(issue.recent_context(exclude_id="cur", now=now), "- Older")

    def test_image_bytes_are_private_and_the_reply_uses_the_same_session(self):
        dictation = replace(DICTATION, image_use="attach", images=(("image/png", b"clipboard bytes"),),
                            session_id="ses_fixture123")

        def complete(command, *, stdout, stderr, **kwargs):
            image_path = Path(command[command.index("--file") + 1])
            self.assertEqual(image_path.read_bytes(), dictation.images[0][1])
            self.assertEqual(image_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(kwargs["env"]["HEX_LINEAR_ATTACHMENT_DIR"], str(image_path.parent))
            self.assertEqual(issue.private_server.call_args.args[1]["HEX_LINEAR_ATTACHMENT_DIR"], str(image_path.parent))
            prompt = kwargs["input"].decode()
            self.assertIn(f"Image 1 file: {image_path}", prompt)
            self.assertIn("Image 1 MIME type: image/png", prompt)
            self.assertIn("Image 1 byte length: 15", prompt)
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
        self.assertNotIn("call hex_notify", run.call_args.kwargs["input"].decode())

    def test_reference_and_preview_images_do_not_grant_upload_access(self):
        complete = CompletedProcess("opencode", 0, stdout=response())
        images = (("image/png", b"reference image"),)
        with (patch.dict(issue.os.environ, {"HEX_LINEAR_ATTACHMENT_DIR": "/tmp/stale-request"}),
              patch.object(issue.subprocess, "run", return_value=complete) as run):
            for image_use, preview in (("reference", False), ("attach", True)):
                with self.subTest(image_use=image_use, preview=preview):
                    issue.run_opencode(replace(DICTATION, image_use=image_use, images=images), None, dry_run=preview)
                    self.assertIn("--file", run.call_args.args[0])
                    self.assertNotIn("HEX_LINEAR_ATTACHMENT_DIR", run.call_args.kwargs["env"])
                    self.assertNotIn("HEX_LINEAR_ATTACHMENT_DIR", issue.private_server.call_args.args[1])

    def test_every_image_is_passed_to_opencode_with_exact_metadata_and_private_files(self):
        images = (("image/png", b"first image"), ("image/jpeg", b"second image"))
        dictation = replace(DICTATION, image_use="attach", images=images)
        paths = []

        def complete(command, *, stdout, stderr, **kwargs):
            paths.extend(Path(command[index + 1]) for index, argument in enumerate(command) if argument == "--file")
            self.assertEqual(len(paths), 2)
            prompt = kwargs["input"].decode()
            for number, (path, (mime, data)) in enumerate(zip(paths, images), 1):
                self.assertEqual(path.read_bytes(), data)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertIn(f"Image {number} file: {path}", prompt)
                self.assertIn(f"Image {number} MIME type: {mime}", prompt)
                self.assertIn(f"Image {number} byte length: {len(data)}", prompt)
            stdout.write(response(tool_event("hex_notify", NOTICE)))
            return CompletedProcess(command, 0)

        with patch.object(issue.subprocess, "run", side_effect=complete):
            self.assertFalse(issue.file_dictation(dictation).error)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_worker_notifies_completed_work_and_continues_replies_in_the_same_session(self):
        result = issue.AgentResult("Waiting", "ses_fixture123", (NOTICE,), issue.ReplyNeeded("Which issue?"))
        jobs = Mock()
        jobs.get.side_effect = [DICTATION, StopIteration]
        with (patch.object(shortcut, "file_dictation", return_value=result),
              patch.object(shortcut, "notify_saved_issue") as notify,
              patch.object(shortcut, "progress_clear") as clear,
              patch.object(shortcut, "Thread") as thread,
              self.assertRaises(StopIteration)):
            shortcut.process_jobs(jobs, False)
        notify.assert_called_once_with(NOTICE, DICTATION.issue_id)
        clear.assert_called_once_with(DICTATION.issue_id)
        pending = thread.call_args.kwargs["args"][1]
        self.assertEqual(pending.session_id, "ses_fixture123")
        self.assertEqual(pending.transcript, DICTATION.transcript)

    def test_worker_updates_progress_on_completion_and_failure(self):
        ok_result = issue.AgentResult("Done", "ses_fixture123", (NOTICE,))
        err_result = issue.AgentResult("", None, (), error="Failed")
        jobs = Mock()
        jobs.get.side_effect = [DICTATION, DICTATION, StopIteration]
        with (patch.object(shortcut, "file_dictation", side_effect=[ok_result, err_result]),
              patch.object(shortcut, "notify_saved_issue"),
              patch.object(shortcut, "record_history"),
              patch.object(shortcut, "notify"),
              patch.object(shortcut, "progress_update") as update,
              self.assertRaises(StopIteration)):
            shortcut.process_jobs(jobs, False)
        self.assertEqual(update.call_args_list[0].args, (DICTATION.issue_id, 4, 4, "Created KIAN-3", "done"))
        self.assertEqual(update.call_args_list[1].args, (DICTATION.issue_id, 4, 4, "Linear request failed", "error"))

    def test_workspace_tools_keep_project_context_and_upload_signed_image_bytes(self):
        script = r'''
import assert from "node:assert/strict";
import { readFile, mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
const source = await readFile(process.argv[2]);
const plugin = (await import(`data:text/javascript;base64,${source.toString("base64")}`)).default;
const fixture = await mkdtemp(path.join(tmpdir(), "hex-linear-"));
const directory = path.join(fixture, "request");
await mkdir(directory);
process.env.HEX_LINEAR_ATTACHMENT_DIR = directory;
const tools = new Map([["skill", {}], ["linear_list_projects", {}]]);
const hooks = new Map();
await plugin.setup({ tool: {
  transform: async (edit) => edit({
    add: (tool) => tools.set(tool.name, tool),
    remove: (name) => tools.delete(name),
  }),
  hook: async (name, callback) => hooks.set(name, callback),
}});
assert.deepEqual([...tools.keys()], ["linear_list_projects", "hex_notify", "hex_reply", "hex_upload_clipboard"]);
const notice = { title: "Created KIAN-3", message: "Saved", url: "https://linear.app/w/issue/KIAN-3" };
const notification = await tools.get("hex_notify").execute(notice);
assert.equal(notification.output, undefined);
assert.match(notification.content, /queued/);
const reply = { question: "Which project?" };
assert.equal(Object.hasOwn(tools.get("hex_reply").input.properties, "copy_image"), false);
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
const file = path.join(directory, "clipboard-1.png");
const bytes = Buffer.from([0, 255, 10, 34]);
await writeFile(file, bytes, { mode: 0o600 });
const sent = [];
globalThis.fetch = async (url, request) => { sent.push({ url, request }); return { ok: true }; };
try {
  const context = { agent: "linear-issue", sessionID: "ses_fixture" };
  const headers = { "Content-Type": "image/png", "X-Signed": "Exact Header Value" };
  const url = "https://storage.googleapis.com/linear-uploads-fixture/image?signature=fixture";
  const assetUrl = "https://uploads.linear.app/workspace/attachment";
  const upload = tools.get("hex_upload_clipboard");
  assert.deepEqual(Object.keys(upload.input.properties), ["file"]);
  await assert.rejects(upload.execute({ file }, context), /through Linear MCP/);
  const prepared = {
    ...context, tool: "linear_prepare_file_upload", status: "completed", input: { filename: path.basename(file) },
    result: { output: JSON.stringify({ assetUrl, uploadRequest: { method: "PUT", url, headers } }) },
  };
  hooks.get("execute.after")(prepared);
  assert.match(prepared.result.content[0].text, /Upload prepared/);
  assert.equal(prepared.result.output, undefined);
  await assert.rejects(upload.execute({ file }, { ...context, sessionID: "ses_other" }), /through Linear MCP/);
  await assert.rejects(upload.execute({ file }, { ...context, agent: "linear-issue-preview" }), /this Hex request/);
  await assert.rejects(upload.execute({ file: path.join(fixture, "clipboard-1.png") }, context), /this Hex request/);
  assert.equal(sent.length, 0);
  const result = await upload.execute({ file }, context);
  assert.match(result.content, /https:\/\/uploads\.linear\.app\/workspace\/attachment/);
  await assert.rejects(upload.execute({ file }, context), /through Linear MCP/);
  const [uploaded] = sent;
  assert.equal(sent.length, 1);
  assert.equal(uploaded.url.href, url);
  assert.deepEqual(uploaded.request.body, bytes);
  assert.deepEqual(uploaded.request.headers, headers);
  assert.equal(uploaded.request.method, "PUT");
  assert.equal(uploaded.request.redirect, "error");
} finally {
  await rm(fixture, { recursive: true, force: true });
}
'''
        completed = subprocess.run(["node", "--input-type=module", "-", str(ROOT / "hex/linear-search.js")],
                                   input=script, text=True, capture_output=True, timeout=15)
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_hex_control_keeps_no_speech_separate_from_errors(self):
        with patch.object(shortcut.subprocess, "run", return_value=CompletedProcess("hex", 4, stdout=json.dumps({
                "status": "error", "code": "no-speech", "message": "No speech heard",
        }))) as run:
            self.assertEqual(shortcut.hex_command("dictation", "stop", "--print", timeout=75)["text"], "")
            self.assertEqual(run.call_args.args[0], ["hex", "dictation", "stop", "--print", "--json"])
        with patch.object(shortcut.subprocess, "run", return_value=CompletedProcess("hex", 1, stdout=json.dumps({
                "status": "error", "code": "busy", "message": "Another recording is active",
        }))):
            with self.assertRaisesRegex(issue.FilingError, "Another recording is active"):
                shortcut.hex_command("dictation", "start")

    def test_notification_body_and_button_keep_waiting_and_pass_activation_to_the_browser(self):
        for action in ("default", "open", ""):
            with self.subTest(action=action):
                def execute(command, **kwargs):
                    if command[0] == "notify-send":
                        self.assertIsNone(kwargs["timeout"])
                        self.assertIn("--expire-time=0", command)
                        self.assertIn("--action=default=Open in Linear", command)
                        self.assertIn("--action=open=Open in Linear", command)
                        shortcut.os.write(kwargs["pass_fds"][0], b"fixture-activation\n")
                        return CompletedProcess(command, 0, stdout=action + "\n")
                    self.assertEqual(command, ["xdg-open", URL])
                    self.assertEqual(kwargs["env"]["XDG_ACTIVATION_TOKEN"], "fixture-activation")
                    self.assertTrue(kwargs["check"])
                    return CompletedProcess(command, 0)

                with patch.object(shortcut.subprocess, "run", side_effect=execute) as run:
                    shortcut.notify("Created KIAN-3", "Saved.", URL)
                self.assertEqual(run.call_count, 2 if action else 1)

    def test_reply_keeps_its_original_request_identity_and_image(self):
        jobs = SimpleQueue()
        dictation = replace(DICTATION, image_use="attach", images=(("image/png", b"original"),))
        question = issue.ReplyNeeded("Which project?")
        answer = "Clusterfork\nUse the relay description."
        with patch.object(shortcut.subprocess, "run", side_effect=[
            CompletedProcess("notify-send", 0, stdout="default\n"),
            CompletedProcess("linear_reply.py", 0, stdout=json.dumps({"text": answer, "images": []})),
        ]) as run:
            shortcut.offer_reply(jobs, dictation, question)
        self.assertIn("--action=default=Reply", run.call_args_list[0].args[0])
        self.assertEqual(Path(run.call_args_list[1].args[0][1]).name, "linear_reply.py")
        self.assertEqual(run.call_args_list[1].kwargs["input"], "Which project?")
        self.assertEqual(jobs.get_nowait(), replace(dictation, answers=(("Which project?", answer),)))

class MagicContext:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self.payload

    def __exit__(self, *_args):
        return False


class LinearInstructionsTests(unittest.TestCase):
    def test_new_issues_default_to_backlog_and_honor_explicit_status(self):
        instructions = (ROOT / "hex/linear-agent.md").read_text()
        self.assertRegex(instructions,
                         r"File new issues in Backlog unless the speaker requests\s+another status\.")

    def test_agent_uses_the_same_explicitly_reasoned_model(self):
        instructions = (ROOT / "hex/linear-agent.md").read_text()
        self.assertIn(f"model: {issue.MODEL}\n", instructions)
        self.assertTrue(issue.MODEL.endswith("#max"))


if __name__ == "__main__":
    unittest.main()
