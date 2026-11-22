"""Exercise the MVP's approval and publication boundaries without provider calls."""

import asyncio
import base64
from datetime import datetime, timezone
from email.message import Message
import hashlib
import hmac
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from symphony.__main__ import (
    BODY_LIMIT, ISSUE_REFRESH_SECONDS, REFRESH_COALESCE_SECONDS, WEBHOOK_BODY_LIMIT,
    Handler, Symphony, WebhookHandler, serve,
)
from symphony.codex import Codex, TOOLS, TOOLS_HASH
from symphony.execution import execution_payload
from symphony.runner import (
    NOTES_LINES, REVIEW_LINES, Runner, bounded_review, validate_review,
)
from symphony.services import Linear, matches_filter, proposed_plan, with_plan


ISSUE = "KIAN-101"
REPO = "owner/project"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
WEBP = b"RIFF" + (16).to_bytes(4, "little") + b"WEBP" + b"\x00" * 8


def image_payload(data=PNG, mime="image/png"):
    return {"type": mime, "data": base64.b64encode(data).decode()}


class LinearFixture:
    def __init__(self):
        self.state = {"name": "Todo", "type": "unstarted"}
        self.issues = {}

    def configure(self, identifier, *, state=None, project="project", priority=0):
        self.issues[identifier] = {"project": {"id": project},
                                   "state": state or self.state, "priority": priority}

    async def issue(self, identifier):
        return self.issues.setdefault(identifier, {"project": {"id": "project"},
                                                   "state": self.state, "priority": 0})

    async def set_state(self, issue, name, kind):
        entry = await self.issue(issue)
        entry["state"] = {"name": name, "type": kind}
        self.state = entry["state"]


class SymphonyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.remote = self.root / "remote.git"
        self.linear = LinearFixture()
        self.runner = Runner(self.root, self.linear)
        self.addAsyncCleanup(self.close)
        self.repo = self.runner.repo_dir(REPO)
        self.repo.parent.mkdir(parents=True)
        self.git(self.root, "init", "--bare", "--initial-branch=main", str(self.remote))
        self.git(self.repo.parent, "clone", str(self.remote), str(self.repo))
        self.git(self.repo, "config", "user.name", "Fixture")
        self.git(self.repo, "config", "user.email", "fixture@example.test")
        (self.repo / "README.md").write_text("Fixture repository\n")
        workflows = self.repo / ".github/workflows"
        workflows.mkdir(parents=True)
        (workflows / "test.yml").write_text(
            "on:\n  push:\n    branches: [main]\n"
            "    paths: ['**/*.txt', '!verified.txt', 'verified.txt']\n"
        )
        (workflows / "ignore-text.yml").write_text("on:\n  push:\n    paths-ignore: ['**/*.txt']\n")
        (workflows / "nightly.yml").write_text("on: schedule\n")
        self.git(self.repo, "add", ".")
        self.git(self.repo, "commit", "-m", "Initial fixture")
        self.git(self.repo, "push", "origin", "main")

        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        worker = fake_bin / "opencode"
        worker.write_text("""#!/usr/bin/env python3
import json
from pathlib import Path
import os
import subprocess
import sys
import time
workspace = Path(os.environ["FIXTURE_ROOT"]) / "runs/KIAN-101"
config = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT", "{}"))
if config.get("mcp") == {"servers": {}}:
    prompt = sys.stdin.read()
    with (workspace / "review-runs").open("a") as stream:
        stream.write("review\\n")
    (workspace / "review-env.json").write_text(json.dumps({
        "args": sys.argv,
        "config": config,
        "github": "GITHUB_TOKEN" in os.environ,
        "linear": "LINEAR_API_KEY" in os.environ,
        "api_key": "OPENCODE_GO_API_KEY" in os.environ,
        "webhook": "LINEAR_WEBHOOK_SECRET" in os.environ,
        "prompt": prompt,
    }))
    assert "independent read-only code reviewer" in prompt
    mode = os.environ.get("FIXTURE_REVIEW", "")
    def emit(kind, message, **part):
        print(json.dumps({"type": kind, "sessionID": "ses_review",
                          "part": {"messageID": message, **part}}))
    def finish(message, text, reason):
        emit("step_start", message, type="step-start")
        if text is not None:
            emit("text", message, type="text", text=text)
        emit("step_finish", message, type="step-finish", reason=reason)
    if mode == "timeout":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        (workspace / "review-pids").write_text(f"{os.getpid()} {child.pid}")
        time.sleep(60)
        sys.exit(0)
    if mode == "fail":
        print(json.dumps({"type": "error", "sessionID": "ses_review",
                          "error": {"type": "provider.api", "message": "Fixture review failed"}}))
        sys.exit(2)
    if mode == "toolonly":
        emit("tool_use", "msg_tool", type="tool", tool="grep")
        emit("text", "msg_tool", type="text", text="VERDICT: NO ISSUES")
        emit("step_finish", "msg_tool", type="step-finish", reason="tool-calls")
    elif mode == "nonterminal":
        finish("msg_tool", "VERDICT: NO ISSUES", "tool-calls")
    elif mode == "progress":
        finish("msg_progress", "Reviewing the diff.", "tool-calls")
        finish("msg_final", "VERDICT: NO ISSUES", "stop")
    elif mode == "intermediate":
        finish("msg_early", "VERDICT: NO ISSUES", "tool-calls")
        finish("msg_final", "VERDICT: ISSUES\\n- symphony/runner.py:1 - Final finding.", "stop")
    elif mode == "malformed":
        finish("msg_final", "Looks fine to me.", "stop")
    elif mode == "empty":
        finish("msg_final", None, "stop")
    elif mode == "findings":
        finish("msg_final", "VERDICT: ISSUES\\n- symphony/runner.py:1 - Fixture finding.\\n", "stop")
    elif mode == "large":
        finish("msg_final", "VERDICT: ISSUES\\n- symphony/runner.py:1 - Fixture finding.\\n"
               + "detail\\n" * 300, "stop")
    elif mode == "custom":
        finish("msg_final", os.environ.get("FIXTURE_REVIEW_TEXT", "VERDICT: NO ISSUES"), "stop")
    elif mode == "mutate":
        Path("verified.txt").write_text("Reviewed\\n")
        finish("msg_final", "VERDICT: NO ISSUES", "stop")
    else:
        finish("msg_final", "VERDICT: NO ISSUES", "stop")
    sys.exit(0)
assert "Write one verification file." in sys.argv[-1]
assert "Never ship from the worker." in sys.argv[-1]
assert sys.argv[sys.argv.index("--model") + 1] == "opencode-go/deepseek-v4.1-flash#max"
assert "GITHUB_TOKEN" not in os.environ
with (workspace / "worker-envs.jsonl").open("a") as stream:
    stream.write(json.dumps({
        "args": sys.argv,
        "config": config,
        "github": "GITHUB_TOKEN" in os.environ,
        "gh": "GH_TOKEN" in os.environ,
        "linear": "LINEAR_API_KEY" in os.environ,
        "openai": "OPENAI_API_KEY" in os.environ,
        "api_key": "OPENCODE_GO_API_KEY" in os.environ,
        "webhook": "LINEAR_WEBHOOK_SECRET" in os.environ,
    }) + "\\n")
if os.environ.get("FIXTURE_LARGE_DIFF"):
    Path("large.txt").write_text("x" * 60000 + "\\nEND-OF-LARGE-DIFF\\n")
print(json.dumps({"type":"text","sessionID":"ses_fixture"}))
Path("verified.txt").write_text("Tested\\n")
if not os.environ.get("FIXTURE_SKIP_NOTES"):
    notes = os.environ.get("FIXTURE_NOTES", "None")
    if not notes.endswith("\\n"):
        notes += "\\n"
    (workspace / "notes.md").write_text(notes)
if os.environ.get("FIXTURE_BLOCK"):
    (workspace / "blocked.txt").write_text(os.environ["FIXTURE_BLOCK"])
    sys.exit(0)
checks = "false\\n" if os.environ.get("FIXTURE_CHECKS_FAIL") else 'test "$(cat verified.txt)" = Tested\\n'
(workspace / "checks.sh").write_text(checks)
""")
        worker.chmod(0o755)
        context = patch.dict(os.environ, {"PATH": f"{fake_bin}:{os.environ['PATH']}",
                                         "GITHUB_TOKEN": "fixture",
                                         "LINEAR_WEBHOOK_SECRET": "fixture-webhook-secret",
                                         "FIXTURE_ROOT": str(self.root)})
        context.start()
        self.addCleanup(context.stop)

    async def close(self):
        jobs = list(self.runner.jobs)
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
        self.runner.db.close()

    @staticmethod
    def git(cwd, *args):
        return subprocess.check_output(["git", *args], cwd=cwd, stderr=subprocess.PIPE, text=True).strip()

    async def phase(self, expected):
        async with asyncio.timeout(5):
            while self.runner.task(ISSUE)["phase"] != expected:
                await asyncio.sleep(0.01)

    async def poll_once(self, app):
        async def sleep(_):
            return None

        async def stop(awaitable, timeout):
            awaitable.close()
            raise asyncio.CancelledError

        with patch("symphony.__main__.asyncio.sleep", side_effect=sleep), \
             patch("symphony.__main__.asyncio.wait_for", side_effect=stop):
            with self.assertRaises(asyncio.CancelledError):
                await app.poll_issues()

    async def approve(self):
        await self.runner.approve(self.proposed_issue(), REPO)

    def proposed_issue(self):
        return {
            "identifier": ISSUE, "title": "Fixture change", "state": self.linear.state,
            "project": {"id": "project"}, "description": "## Proposed Plan\n\nWrite one verification file."
        }

    def app(self, runner=None):
        runner = runner or self.runner
        with patch.dict(os.environ, {"LINEAR_API_KEY": "fixture"}), \
             patch("symphony.__main__.Runner", return_value=runner):
            return Symphony(self.root)

    async def pump_until(self, app, predicate):
        pump = asyncio.create_task(app.pump())
        self.addAsyncCleanup(self._cancel_task, pump)
        app.runner.wake.set()
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        return pump

    async def test_chat_approval_routes_through_the_coordinator_tool(self):
        app = self.app()
        text = "Looks good, go ahead with the plan."
        with patch.object(app, "respond") as respond:
            await app.submit({"text": text})
            await app.handle(*app.messages.popleft())
            respond.assert_awaited_once_with("", text)
        self.assertEqual(self.linear.state["name"], "Todo")

        with patch.object(app, "live_issue", return_value=(self.proposed_issue(), REPO)):
            await app.tool("approve_issue", {"issue_id": ISSUE})
            with self.assertRaisesRegex(ValueError, "already has a recorded approval"):
                await app.tool("approve_issue", {"issue_id": ISSUE})
        self.assertEqual(self.linear.state["name"], "Todo")
        self.assertEqual(self.runner.task(ISSUE)["plan"], "Write one verification file.")
        self.assertEqual(self.runner.rows("SELECT * FROM events"), [])

    async def test_chat_state_renders_saved_and_streaming_assistant_markdown(self):
        app = self.app()
        reply = (
            "**KIAN-1077** is now **In Progress**.\n\n"
            "[Linear issue](https://linear.app/acme/issue/KIAN-1077)\n\n"
            "[CI run](https://github.com/acme/project/actions/runs/123)\n\n"
            "[documented work](../docs/symphony.md#plan-and-execute)\n\n"
            "- List item with `inline code`\n\n"
            "```text\ncode block\n```"
        )
        self.runner.chat("user", "**plain text**")
        app.coordinator.stream = reply

        live = await app.state(0)
        self.assertEqual(live["messages"][0]["text"], "**plain text**")
        self.assertNotIn("html", live["messages"][0])
        for expected in (
            '<a href="https://linear.app/acme/issue/KIAN-1077">Linear issue</a>',
            '<a href="https://github.com/acme/project/actions/runs/123">CI run</a>',
            '<a href="../docs/symphony.md#plan-and-execute">documented work</a>',
            "<strong>In Progress</strong>",
        ):
            self.assertIn(expected, live["streamHtml"])
        self.assertEqual(live["streamHtml"].count("<p>"), 4)
        self.assertIn("<ul>", live["streamHtml"])
        self.assertIn("<code>inline code</code>", live["streamHtml"])
        self.assertIn("<pre><code", live["streamHtml"])
        stream_html = live["streamHtml"]

        app.coordinator.thread = "main"
        self.runner.save_setting("thread", "main")
        app.coordinator.notification("item/completed", {
            "threadId": "main", "item": {"type": "agentMessage", "text": reply},
        })
        app.coordinator.notification("turn/completed", {
            "threadId": "main", "turn": {"id": "turn", "status": "completed"},
        })
        complete = await app.state(0)
        self.assertEqual(complete["stream"], "")
        self.assertEqual(complete["streamHtml"], "")
        assistant_messages = [item for item in complete["messages"] if item["role"] == "assistant"]
        self.assertEqual(len(assistant_messages), 1)
        self.assertEqual(assistant_messages[0]["text"], reply)
        self.assertEqual(assistant_messages[0]["html"], stream_html)
        self.assertEqual(self.runner.rows("SELECT role, text FROM chat")[-1]["text"], reply)

    async def test_chat_submit_acknowledges_the_saved_row_in_server_order(self):
        app = self.app()
        app.repos["owner/second"] = [{"id": "beta", "name": "Beta"}]
        self.runner.save_setting("view", "project")
        self.runner.chat("system", "Earlier notice")
        image = image_payload(WEBP, "image/webp")
        result = await app.submit({"project": "beta", "text": "Use this mockup.",
                                   "images": [image]})
        saved = result["message"]
        rows = self.runner.rows("SELECT * FROM chat ORDER BY id")
        self.assertEqual(saved["id"], rows[-1]["id"])
        self.assertEqual(saved["role"], "user")
        self.assertEqual(saved["text"], "[Switched view: Beta]\nUse this mockup.")
        self.assertEqual([item["type"] for item in saved["images"]], ["image/webp"])
        self.assertEqual(json.loads(rows[-1]["attachments"]), saved["images"])

        first = await app.submit({"project": "beta", "text": "Repeat"})
        second = await app.submit({"project": "beta", "text": "Repeat"})
        self.assertNotEqual(first["message"]["id"], second["message"]["id"])
        state = await app.state(0)
        self.assertEqual([message["id"] for message in state["messages"]],
                         [row["id"] for row in rows] + [first["message"]["id"],
                                                        second["message"]["id"]])
        self.assertEqual([message["text"] for message in state["messages"]][-2:],
                         ["Repeat", "Repeat"])
        self.assertEqual(state["messages"][1], saved)

        image_only = await app.submit({"project": "beta", "images": [image_payload(PNG)]})
        self.assertEqual(image_only["message"]["text"], "")
        self.assertEqual([item["type"] for item in image_only["message"]["images"]],
                         ["image/png"])
        self.assertEqual(self.runner.rows("SELECT text FROM chat ORDER BY id DESC LIMIT 1"),
                         [{"text": ""}])

    async def test_chat_live_markdown_renders_only_when_the_stream_changes(self):
        app = self.app()
        with patch("symphony.__main__.markdown", return_value="<p>cached</p>") as render:
            app.coordinator.stream = "Draft one"
            first = await app.state(0)
            second = await app.state(0)
            self.assertEqual(render.call_count, 1)
            self.assertEqual(first["streamHtml"], "<p>cached</p>")
            self.assertEqual(second["streamHtml"], "<p>cached</p>")

            app.coordinator.stream = "Draft one and two"
            await app.state(0)
            self.assertEqual(render.call_count, 2)

            app.coordinator.stream = ""
            cleared = await app.state(0)
            self.assertEqual(render.call_count, 2)
            self.assertEqual((cleared["stream"], cleared["streamHtml"]), ("", ""))

    async def test_chat_images_store_attach_to_history_and_serve(self):
        app = self.app()
        payloads = [image_payload(PNG), image_payload(JPEG, "image/jpeg"),
                    image_payload(WEBP, "image/webp")]
        accepted = await app.submit({"text": "Screenshot", "images": payloads})

        repo, text, message, images = app.messages.popleft()
        self.assertEqual((repo, text, message), ("", "Screenshot", "Screenshot"))
        self.assertEqual([image["type"] for image in images],
                         ["image/png", "image/jpeg", "image/webp"])
        self.assertEqual(len({image["name"] for image in images}), 3)
        for image, expected in zip(images, (PNG, JPEG, WEBP)):
            self.assertRegex(image["name"], r"^[0-9a-f]{32}\.(?:png|jpg|webp)$")
            path = app.runner.chat_image_path(image["name"])
            self.assertEqual(path.parent, app.runner.chat_image_dir())
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(app.chat_image(image["name"]), (expected, image["type"]))

        saved = accepted["message"]
        row = self.runner.rows("SELECT * FROM chat")[-1]
        self.assertEqual(saved, {
            "id": row["id"], "role": "user", "text": "Screenshot", "images": images,
        })
        state = await app.state(0)
        self.assertEqual(state["messages"][-1], saved)
        self.assertEqual(json.loads(row["attachments"]), images)

        handler = object.__new__(Handler)
        handler.path = "/api/chat-image?name=" + images[0]["name"]
        handler.server = SimpleNamespace(app=app)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(handler.wfile.getvalue(), PNG)
        handler.send_response.assert_called_once_with(200)

    async def test_chat_images_reject_invalid_uploads_and_slash_commands(self):
        app = self.app()
        cases = [
            ({"text": "x", "images": [image_payload()] * 5}, "at most four"),
            ({"text": "x", "images": [{"type": "image/gif", "data": "R0lGODlh"}]},
             "PNG, JPEG, or WebP"),
            ({"text": "x", "images": [{"type": "image/png", "data": "!!!"}]},
             "Invalid image encoding"),
            ({"text": "x", "images": [image_payload(JPEG)]}, "does not match its type"),
            ({"text": "/status", "images": [image_payload()]}, "slash commands"),
            ({"text": "x" * 23001}, "23,000"),
        ]
        for body, message in cases:
            with self.subTest(body=body, message=message):
                before = len(self.runner.rows("SELECT * FROM chat"))
                with self.assertRaisesRegex(ValueError, message):
                    await app.submit(body)
                self.assertEqual(len(self.runner.rows("SELECT * FROM chat")), before)
        self.assertFalse(app.runner.chat_image_dir().exists())

        with patch("symphony.__main__.IMAGE_BYTES", len(PNG) - 1):
            with self.assertRaisesRegex(ValueError, "5 MiB"):
                await app.submit({"text": "x", "images": [image_payload()]})
        with patch("symphony.__main__.IMAGES_BYTES", len(PNG) + len(JPEG) - 1):
            with self.assertRaisesRegex(ValueError, "10 MiB"):
                await app.submit({"text": "x", "images": [image_payload(PNG),
                                                            image_payload(JPEG, "image/jpeg")]})
        self.assertFalse(app.runner.chat_image_dir().exists())

    async def test_chat_images_are_removed_when_saving_the_chat_entry_fails(self):
        app = self.app()
        with patch.object(app.runner, "chat", side_effect=RuntimeError("Database unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Database unavailable"):
                await app.submit({"text": "Screenshot", "images": [image_payload()]})
        self.assertEqual(list(app.runner.chat_image_dir().iterdir()), [])

    async def test_chat_images_follow_a_failed_steer_into_the_queue(self):
        app = self.app()
        app.repos[REPO] = [{"id": "project", "name": "Fixture"}]
        app.turn_repo = REPO
        app.coordinator.thread = "main"
        app.coordinator.turn = "turn"
        self.runner.save_setting("view", "project")
        note = "Continue with this screenshot."
        with patch.object(app.coordinator, "steer", new_callable=AsyncMock,
                          side_effect=RuntimeError("Steer failed")) as steer:
            await app.submit({"project": "project", "text": note,
                              "images": [image_payload()]})
        steer.assert_awaited_once()
        first = app.messages.popleft()
        self.assertEqual(first[:3], (REPO, note, note))
        self.assertEqual([image["type"] for image in first[3]], ["image/png"])

        with patch.object(app.coordinator, "respond", new_callable=AsyncMock,
                          return_value="completed") as respond:
            await app.handle(*first)
        text, cwd, attachments = respond.await_args.args
        self.assertEqual((text, cwd), (note, self.repo))
        self.assertEqual(attachments, first[3])

    async def test_chat_images_cross_a_project_switch_in_the_queue(self):
        app = self.app()
        app.repos[REPO] = [{"id": "project", "name": "Fixture"}]
        app.repos["owner/second"] = [{"id": "beta", "name": "Beta"}]
        self.runner.save_setting("view", "project")
        await app.submit({"project": "beta", "text": "Use this mockup.",
                          "images": [image_payload(WEBP, "image/webp")]})

        repo, text, message, images = app.messages.popleft()
        self.assertEqual((repo, text), ("owner/second", "Use this mockup."))
        self.assertEqual(message, "[Switched view: Beta]\nUse this mockup.")
        self.assertEqual(images[0]["type"], "image/webp")
        self.assertEqual(json.loads(self.runner.rows(
            "SELECT attachments FROM chat ORDER BY id DESC LIMIT 1")[0]["attachments"]), images)
        self.assertEqual(app.runner.setting("view"), "beta")

    async def test_codex_receives_native_image_inputs_for_start_and_steer(self):
        app = self.app()
        name = self.runner.save_chat_image("png", PNG)
        images = [{"name": name, "type": "image/png"}]
        path = str(self.runner.chat_image_path(name))
        codex = Codex(self.runner, None)
        codex.thread = "thread"
        codex.turn = "turn"
        with patch.object(codex, "ensure_thread"), \
             patch.object(codex, "turn_request", new_callable=AsyncMock,
                          return_value="completed") as turn:
            await codex.respond("Look at this", self.root, images)
            method, params = turn.await_args.args
            self.assertEqual(method, "turn/start")
            self.assertEqual(params["input"], [
                {"type": "text", "text": "Look at this", "text_elements": []},
                {"type": "localImage", "path": path},
            ])
            await codex.respond("Text only", self.root)
            _, params = turn.await_args.args
            self.assertEqual(params["input"], [
                {"type": "text", "text": "Text only", "text_elements": []},
            ])

        with patch.object(codex, "request", new_callable=AsyncMock,
                          return_value={}) as request:
            await codex.steer("Also this", images)
        method, params = request.await_args.args
        self.assertEqual(method, "turn/steer")
        self.assertEqual(params["input"], [
            {"type": "text", "text": "Also this", "text_elements": []},
            {"type": "localImage", "path": path},
        ])

    async def test_chat_image_paths_are_contained_and_traversal_is_rejected(self):
        app = self.app()
        directory = app.runner.chat_image_dir()
        directory.mkdir(parents=True)
        outside = self.root / "outside.png"
        outside.write_bytes(PNG)
        link = directory / ("a" * 32 + ".png")
        link.symlink_to(outside)
        for name in ("../outside.png", str(outside), "A" * 32 + ".png", link.name):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Image not found"):
                app.runner.chat_image_path(name)
        with self.assertRaisesRegex(ValueError, "Image not found"):
            app.chat_image(link.name)

        handler = object.__new__(Handler)
        handler.path = "/api/chat-image?name=..%2Foutside.png"
        handler.server = SimpleNamespace(app=app)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        handler.send_response.assert_called_once_with(404)

    async def test_approval_retry_wakes_the_coordinator(self):
        await self.fail_approval()
        app = self.app()
        text = f"/retry {ISSUE}"
        await app.handle("", text, text)
        self.assert_dispatch_event()

    async def test_closed_issues_cannot_be_approved(self):
        app = self.app()
        for kind in ("completed", "canceled", "duplicate"):
            with self.subTest(state=kind):
                issue = {**self.proposed_issue(), "state": {"name": kind.title(), "type": kind}}
                with patch.object(app, "live_issue", return_value=(issue, REPO)), \
                     patch.object(self.linear, "set_state") as set_state:
                    with self.assertRaisesRegex(ValueError, "already " + kind.title()):
                        await app.tool("approve_issue", {"issue_id": ISSUE})
                    set_state.assert_not_awaited()
                self.assertEqual(self.runner.rows("SELECT * FROM tasks"), [])

    async def test_closed_state_transitions_are_rejected_and_noops_are_allowed(self):
        linear = Linear("fixture")
        for kind in ("completed", "canceled", "duplicate"):
            with self.subTest(state=kind):
                state = {"name": kind.title(), "type": kind}
                with patch.object(linear, "issue", return_value={"state": state}), \
                     patch.object(linear, "query", side_effect=AssertionError("Unexpected state update")) as query:
                    with self.assertRaisesRegex(ValueError, "already " + state["name"]):
                        await linear.set_state(ISSUE, "In Progress", "started")
                    await linear.set_state(ISSUE, state["name"], kind)
                    query.assert_not_awaited()

    async def test_approval_recovery_wakes_the_coordinator(self):
        await self.fail_approval()
        await self.runner.recover_approval(ISSUE)
        self.assert_dispatch_event()

    async def fail_approval(self):
        with patch.object(self.linear, "set_state", side_effect=RuntimeError("Linear is unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Linear is unavailable"):
                await self.approve()
        self.assertEqual(self.runner.task(ISSUE)["phase"], "approving")

    def assert_dispatch_event(self):
        self.assertEqual(self.runner.task(ISSUE)["phase"], "approved")
        self.assertEqual(self.linear.state["name"], "Todo")
        self.assertEqual(self.runner.rows("SELECT issue, detail, delivered FROM events"), [
            {"issue": ISSUE, "detail": "Plan approved. Queued in Todo.", "delivered": 0},
        ])

    async def test_codex_has_native_tools_and_uses_oauth(self):
        codex = Codex(self.runner, None)
        with patch.dict(os.environ, {"GITHUB_TOKEN": "github-fixture", "OPENAI_API_KEY": "api-fixture",
                                     "LINEAR_API_KEY": "linear-fixture"}, clear=True), \
             patch("symphony.codex.asyncio.create_subprocess_exec") as launch, \
             patch.object(codex, "read"), patch.object(codex, "send"), \
             patch.object(codex, "request", side_effect=[
                 {}, {"account": {"type": "chatgpt"}}, {"thread": {"id": "test-thread"}},
             ]) as request:
            await codex.ensure_thread(self.root)
        self.assertIn('web_search="live"', launch.await_args.args)
        self.assertNotIn("features.shell_tool=false", launch.await_args.args)
        env = launch.await_args.kwargs["env"]
        self.assertEqual(env["GH_TOKEN"], "github-fixture")
        self.assertEqual(env["GITHUB_TOKEN"], "github-fixture")
        self.assertEqual(env["LINEAR_API_KEY"], "linear-fixture")
        self.assertNotIn("OPENAI_API_KEY", env)
        method, params = request.await_args.args
        self.assertEqual(method, "thread/start")
        self.assertEqual(params["sandbox"], "danger-full-access")
        self.assertEqual(params["approvalPolicy"], "never")
        self.assertIn("approve_issue", [tool["name"] for tool in params["dynamicTools"]])

    async def test_thread_refreshes_tools_and_resumes_afterward(self):
        self.runner.save_setting("thread", "previous-thread")
        self.runner.chat("user", "Earlier chat")
        codex = Codex(self.runner, None)
        codex.account = "chatgpt"
        with patch.object(codex, "connect"), \
             patch.object(codex, "request", return_value={"thread": {"id": "current-thread"}}) as request:
            await codex.ensure_thread(self.root)
            method, params = request.await_args.args
            self.assertEqual(method, "thread/start")
            self.assertEqual(params["dynamicTools"], TOOLS)
            self.assertEqual(self.runner.setting("thread"), "current-thread")
            self.assertEqual(self.runner.setting("tools_hash"), TOOLS_HASH)

            codex.thread = ""
            await codex.ensure_thread(self.root)
            method, params = request.await_args.args
            self.assertEqual(method, "thread/resume")
            self.assertEqual(params["threadId"], "current-thread")

        self.assertEqual(self.runner.rows("SELECT role, text FROM chat ORDER BY id"), [
            {"role": "user", "text": "Earlier chat"},
            {"role": "system", "text": "Started a fresh Codex thread to load updated tools."},
        ])

    async def test_codex_status_reports_active_context_and_usage_windows(self):
        app = self.app()
        codex = app.coordinator
        codex.account = "chatgpt"
        self.runner.save_setting("thread", "active")
        codex.models = {"gpt-fixture": {
            "displayName": "GPT Fixture", "isDefault": True, "defaultReasoningEffort": "medium",
            "supportedReasoningEfforts": [{"reasoningEffort": "medium"}, {"reasoningEffort": "max"}],
        }}
        self.runner.save_setting("effort", "max")
        usage = {"last": {"totalTokens": 24000}, "total": {"totalTokens": 500000}, "modelContextWindow": 112000}
        codex.notification("thread/tokenUsage/updated", {"threadId": "active", "tokenUsage": usage})
        codex.notification("thread/tokenUsage/updated", {"threadId": "other", "tokenUsage": {}})
        codex.notification("account/rateLimits/updated", {"rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": 39, "windowDurationMins": 10080},
            "secondary": {"usedPercent": 65, "windowDurationMins": 300},
        }})
        codex.notification("account/rateLimits/updated", {"rateLimits": {"limitId": "another-model"}})
        codex.notification("turn/completed", {
            "threadId": "active", "turn": {"startedAt": 100, "completedAt": 106, "durationMs": 6000},
        })

        status = (await app.state(0))["codex"]
        self.assertEqual(status["contextUsed"], 12)
        self.assertEqual(status["models"], {"gpt-fixture": "GPT Fixture"})
        self.assertEqual(status["efforts"], ["medium", "max"])
        self.assertEqual(status["turnTiming"], {"startedAt": 100, "completedAt": 106, "durationMs": 6000})
        await app.handle("", "/status", "/status")
        self.assertEqual(self.runner.rows("SELECT text FROM chat"), [{
            "text": "GPT Fixture max · Context 12% used · 5h 35% left · weekly 61% left\nNo approved work in progress.",
        }])

    async def test_codex_exit_clears_the_active_turn(self):
        app = self.app()
        codex = app.coordinator
        codex.ready = True
        codex.thread = "main"
        self.runner.save_setting("thread", "main")
        codex.process = SimpleNamespace(stdout=asyncio.StreamReader())
        codex.finished = asyncio.get_running_loop().create_future()
        codex.notification("turn/started", {"threadId": "main", "turn": {"id": "turn", "startedAt": 100}})
        codex.notification("item/agentMessage/delta", {"threadId": "main", "delta": "Incomplete answer"})
        self.assertTrue((await app.state(0))["canInterrupt"])

        codex.process.stdout.feed_eof()
        await codex.read()
        with self.assertRaisesRegex(RuntimeError, "Codex app-server exited"):
            await codex.finished
        state = await app.state(0)
        self.assertFalse(state["canInterrupt"])
        self.assertEqual(state["stream"], "")
        self.assertEqual(state["codex"]["turnTiming"], {})
        self.assertFalse(codex.ready)
        self.assertEqual(codex.thread, "")
        self.assertEqual(self.runner.setting("thread"), "main")

    async def test_broken_codex_pipe_queues_chat_and_reports_interruption(self):
        app = self.app()
        app.repos[REPO] = [{"id": "project", "name": "Fixture"}]
        app.turn_repo = REPO
        app.coordinator.thread = "main"
        app.coordinator.turn = "turn"
        self.runner.save_setting("view", "project")
        note = "Pick up where we left off."
        with patch.object(app.coordinator, "request", side_effect=BrokenPipeError("Fixture pipe closed")) as request:
            await app.submit({"project": "project", "text": note})
            with self.assertRaisesRegex(RuntimeError, "Codex disconnected"):
                await app.submit({"project": "project", "text": "/interrupt"})
        self.assertEqual([call.args[0] for call in request.await_args_list], ["turn/steer", "turn/interrupt"])
        self.assertEqual(list(app.messages), [(REPO, note, note, [])])
        self.assertTrue(self.runner.wake.is_set())
        self.assertEqual(self.runner.rows("SELECT role, text FROM chat"), [{"role": "user", "text": note}])

    async def test_other_threads_cannot_change_or_complete_the_coordinator_turn(self):
        app = self.app()
        codex = app.coordinator
        codex.thread = "main"
        self.runner.save_setting("thread", "other")
        codex.finished = asyncio.get_running_loop().create_future()
        codex.notification("turn/started", {"threadId": "main", "turn": {"id": "parent", "startedAt": 100}})
        codex.notification("item/agentMessage/delta", {"threadId": "main", "delta": "Main response"})
        before = await app.state(0)
        for method, params in (
            ("turn/started", {"turn": {"id": "child", "startedAt": 101}}),
            ("item/agentMessage/delta", {"delta": "Child response"}),
            ("item/completed", {"item": {"type": "agentMessage", "text": "Child response"}}),
            ("thread/tokenUsage/updated", {"tokenUsage": {"modelContextWindow": 112000,
                                                          "last": {"totalTokens": 24000}}}),
            ("turn/completed", {"turn": {"id": "child", "status": "completed", "durationMs": 1000}}),
        ):
            with self.subTest(method=method):
                codex.notification(method, {"threadId": "other", **params})
                self.assertEqual(await app.state(0), before)
                self.assertEqual(codex.turn, "parent")
                self.assertFalse(codex.finished.done())

        codex.notification("item/completed", {"threadId": "main",
                                              "item": {"type": "agentMessage", "text": "Main response"}})
        turn = {"id": "parent", "status": "completed", "durationMs": 6000}
        codex.notification("turn/completed", {"threadId": "main", "turn": turn})
        self.assertEqual(await codex.finished, turn)
        self.assertFalse((await app.state(0))["canInterrupt"])
        self.assertEqual(self.runner.rows("SELECT role, text FROM chat"), [
            {"role": "assistant", "text": "Main response"},
        ])

    async def test_issue_snapshots_batch_projects_and_only_send_changes(self):
        app = self.app()
        repos = {REPO: [{"id": "alpha", "name": "Alpha", "icon": "🌱"}],
                 "owner/second": [{"id": "beta", "name": "Beta", "icon": ""}]}
        plan = (
            "### Steps\n\n"
            "- **Read** the [Linear issue](https://linear.app/fixture/issue/KIAN-101)\n"
            "- Run `uv`\n"
            "- ~~Skip~~ the **unsafe** markup:\n\n"
            "```python\nprint(\"ok\")\n```\n\n"
            "<script>alert(1)</script>\n\n"
            "[bad](javascript:alert%281%29)\n\n"
            "| Check | Result |\n| --- | --- |\n| Tests | Pass |"
        )
        alpha = {"identifier": ISSUE, "title": "Alpha issue", "url": "https://linear.app/fixture/issue/KIAN-101",
                 "state": {**self.linear.state, "position": 1}, "description": "## Proposed Plan\n\n" + plan,
                 "project": {"id": "alpha", "name": "Alpha"}, "priority": 2, "priorityLabel": "High",
                 "estimate": 3, "dueDate": "2026-11-23", "updatedAt": "2026-11-20T12:00:00Z",
                 "completedAt": None, "labels": {"nodes": [{"name": "Feature", "color": "#bb87fc"}]}}
        beta = {**alpha, "identifier": "KIAN-102", "title": "Beta issue", "description": "",
                "project": {"id": "beta", "name": "Beta"}}
        first = {"issues": {"nodes": [alpha], "pageInfo": {"hasNextPage": True, "endCursor": "more"}}}
        second = {"issues": {"nodes": [beta], "pageInfo": {"hasNextPage": False}}}
        cutoff = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        with patch.object(app, "refresh_projects", return_value=repos), \
             patch.object(app.linear, "query") as query, \
             patch("symphony.__main__.done_cutoff", return_value=cutoff):
            self.assertNotIn("projects", await app.state(0))
            query.side_effect = [first, second]
            await app.refresh_issue_snapshot()
            state = await app.state(0)
            revision = state["issueRevision"]
            expected = {"ids": ["alpha", "beta"], "after": None,
                        "closed": ["canceled", "completed", "duplicate"],
                        "done": "2026-11-20T12:00:00Z"}
            self.assertEqual([call.kwargs for call in query.await_args_list], [
                expected, {**expected, "after": "more"},
            ])
            self.assertIn("completedAt: { gte: $done }", query.await_args_list[0].args[0])
            self.assertEqual([(project["repo"], project["id"], project["issues"][0]["title"])
                              for project in state["projects"]], [
                (REPO, "alpha", "Alpha issue"), ("owner/second", "beta", "Beta issue"),
            ])
            card = state["projects"][0]["issues"][0]
            self.assertEqual(card["projectId"], "alpha")
            self.assertEqual(card["updatedAt"], alpha["updatedAt"])
            self.assertIsNone(card["completedAt"])
            self.assertEqual(card["state"]["position"], 1)
            self.assertEqual((card["project"], card["priorityLabel"], card["estimate"], card["dueDate"], card["labels"]),
                             ("Alpha", "High", 3, "2026-11-23", [{"name": "Feature", "color": "#bb87fc"}]))
            self.assertEqual(card["plan"], plan)
            for expected in (
                "<h3>Steps</h3>", "<strong>Read</strong>",
                '<a href="https://linear.app/fixture/issue/KIAN-101">Linear issue</a>',
                "<code>uv</code>", "<del>Skip</del>", "<pre><code", "<table>",
                "&lt;script&gt;alert(1)&lt;/script&gt;", 'href="#harmful-link"',
            ):
                self.assertIn(expected, card["planHtml"])
            self.assertNotIn("<script>", card["planHtml"])
            self.assertNotIn('href="javascript:', card["planHtml"])
            empty = state["projects"][1]["issues"][0]
            self.assertEqual((empty["plan"], empty["planHtml"]), ("", ""))
            self.assertNotIn("projects", await app.state(0, revision))
            self.assertEqual(query.await_count, 2)

            query.side_effect = [first, second]
            await app.refresh_issue_snapshot()
            self.assertNotIn("projects", await app.state(0, revision))

            query.side_effect = RuntimeError("Linear is unavailable")
            await self.poll_once(app)
            stale = await app.state(0)
            self.assertEqual(stale["issueError"], "Linear is unavailable")
            self.assertEqual(stale["projects"], state["projects"])
            self.assertEqual(stale["issueRevision"], revision)

            beta["title"] = "Updated issue"
            query.side_effect = [first, second]
            await self.poll_once(app)
            latest = await app.state(0, revision)
            self.assertEqual(latest["issueError"], "")
            self.assertNotEqual(latest["issueRevision"], revision)
            self.assertEqual(latest["projects"][1]["issues"][0]["title"], "Updated issue")
            alpha["updatedAt"] = "2026-11-20T12:01:00Z"
            query.side_effect = [first, second]
            await app.refresh_issue_snapshot()
            updated = await app.state(0, latest["issueRevision"])
            self.assertNotEqual(updated["issueRevision"], latest["issueRevision"])
            self.assertEqual(updated["projects"][0]["issues"][0]["updatedAt"], alpha["updatedAt"])

    async def test_issue_snapshots_scope_projects_sharing_a_repository(self):
        app = self.app()
        repos = {REPO: [{"id": "alpha", "name": "Alpha", "icon": "🌱"},
                        {"id": "beta", "name": "Beta", "icon": "🍂"}]}
        cutoff = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

        def card(identifier, project):
            return {"identifier": identifier, "title": project + " issue", "url": "https://linear.app/fixture/issue/" + identifier,
                    "state": {**self.linear.state, "position": 1}, "description": "",
                    "project": {"id": project, "name": project.title()}, "priority": 3,
                    "priorityLabel": "Normal", "estimate": None, "dueDate": None,
                    "updatedAt": "2026-11-20T12:00:00Z", "completedAt": None,
                    "labels": {"nodes": []}}

        nodes = [card("KIAN-101", "alpha"), card("KIAN-102", "beta")]
        response = {"issues": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}
        with patch.object(app, "refresh_projects", return_value=repos), \
             patch.object(app.linear, "query", return_value=response), \
             patch("symphony.__main__.done_cutoff", return_value=cutoff):
            await app.refresh_issue_snapshot()
        projects = (await app.state(0))["projects"]
        self.assertEqual([(project["repo"], project["id"]) for project in projects],
                         [(REPO, "alpha"), (REPO, "beta")])
        self.assertEqual([[issue["identifier"] for issue in project["issues"]] for project in projects],
                         [["KIAN-101"], ["KIAN-102"]])

    async def test_recent_done_uses_completed_at_boundary_and_expires_without_edits(self):
        app = self.app()
        repos = {REPO: [{"id": "alpha", "name": "Alpha", "icon": ""}]}
        cutoff = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

        def card(identifier, state, completed=None):
            return {"identifier": identifier, "title": identifier, "url": "https://linear.app/fixture/issue/" + identifier,
                    "state": {**state, "position": 5}, "description": "", "project": {"id": "alpha", "name": "Alpha"},
                    "priority": 3, "priorityLabel": "Normal", "estimate": None, "dueDate": None,
                    "updatedAt": "2026-11-20T12:00:00Z", "completedAt": completed,
                    "labels": {"nodes": []}}

        nodes = [card("KIAN-101", {"name": "Backlog", "type": "backlog"}),
                 card("KIAN-102", {"name": "Todo", "type": "unstarted"}),
                 card("KIAN-103", {"name": "Done", "type": "completed"}, "2026-11-20T12:00:00Z"),
                 card("KIAN-104", {"name": "Done", "type": "completed"}, "2026-11-20T11:59:59Z"),
                 card("KIAN-105", {"name": "Canceled", "type": "canceled"}, "2026-11-20T12:00:00Z"),
                 card("KIAN-106", {"name": "Duplicate", "type": "duplicate"}, "2026-11-20T12:00:00Z")]
        response = {"issues": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}
        with patch.object(app, "refresh_projects", return_value=repos), \
             patch.object(app.linear, "query", return_value=response), \
             patch("symphony.__main__.done_cutoff", return_value=cutoff):
            await app.refresh_issue_snapshot()
            first = await app.state(0)
            self.assertEqual([issue["identifier"] for issue in first["projects"][0]["issues"]],
                             ["KIAN-101", "KIAN-102", "KIAN-103"])

            later = datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc)
            with patch("symphony.__main__.done_cutoff", return_value=later):
                await app.refresh_issue_snapshot()
            aged = await app.state(0, first["issueRevision"])
            self.assertNotEqual(aged["issueRevision"], first["issueRevision"])
            self.assertEqual([issue["identifier"] for issue in aged["projects"][0]["issues"]],
                             ["KIAN-101", "KIAN-102"])

    async def test_refresh_loop_coalesces_bursts_and_keeps_requests_during_fetch(self):
        app = self.app()
        refreshes = []
        sleeps = []
        waits = []

        async def refresh():
            refreshes.append(ISSUE)
            if len(refreshes) == 1:
                app.runner.request_issue_refresh()

        async def sleep(duration):
            sleeps.append(duration)
            if len(sleeps) == 3:
                raise asyncio.CancelledError

        async def wait(awaitable, timeout):
            waits.append(timeout)
            awaitable.close()
            return False

        app.runner.request_issue_refresh()
        app.runner.request_issue_refresh()
        with patch.object(app, "refresh_issue_snapshot", side_effect=refresh), \
             patch("symphony.__main__.asyncio.sleep", side_effect=sleep), \
             patch("symphony.__main__.asyncio.wait_for", side_effect=wait):
            with self.assertRaises(asyncio.CancelledError):
                await app.poll_issues()
        self.assertEqual(refreshes, [ISSUE, ISSUE])
        self.assertEqual(sleeps, [REFRESH_COALESCE_SECONDS] * 3)
        self.assertEqual(waits, [ISSUE_REFRESH_SECONDS, ISSUE_REFRESH_SECONDS])

    async def test_refresh_loop_retries_with_bounded_backoff_and_keeps_snapshot(self):
        app = self.app()
        app.issue_snapshot = [{"id": "last-good"}]
        starts = []

        async def refresh():
            starts.append(time.monotonic())
            raise RuntimeError("Linear is down")

        with patch("symphony.__main__.ISSUE_RETRY_SECONDS", 0.05), \
             patch("symphony.__main__.ISSUE_REFRESH_SECONDS", 0.2), \
             patch("symphony.__main__.REFRESH_COALESCE_SECONDS", 0.005), \
             patch.object(app, "refresh_issue_snapshot", side_effect=refresh):
            task = asyncio.create_task(app.poll_issues())
            try:
                async with asyncio.timeout(3):
                    while len(starts) < 6:
                        await asyncio.sleep(0.002)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        gaps = [later - earlier for earlier, later in zip(starts, starts[1:])]
        for gap, delay in zip(gaps, (0.05, 0.1, 0.2, 0.2, 0.2)):
            self.assertGreaterEqual(gap, delay)
            self.assertLess(gap, delay + 0.15)
        self.assertEqual(app.issue_snapshot, [{"id": "last-good"}])
        self.assertEqual(app.issue_error, "Linear is down")

    async def test_refresh_retry_deadline_holds_a_dirty_trigger(self):
        app = self.app()
        starts = []

        async def refresh():
            starts.append(time.monotonic())
            if len(starts) == 1:
                app.runner.request_issue_refresh()
                raise RuntimeError("Linear unavailable")

        with patch("symphony.__main__.ISSUE_RETRY_SECONDS", 0.2), \
             patch("symphony.__main__.REFRESH_COALESCE_SECONDS", 0.01), \
             patch.object(app, "refresh_issue_snapshot", side_effect=refresh):
            task = asyncio.create_task(app.poll_issues())
            try:
                async with asyncio.timeout(2):
                    while len(starts) < 2:
                        await asyncio.sleep(0.005)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertGreaterEqual(starts[1] - starts[0], 0.18)

    async def test_refresh_success_keeps_event_refreshes_prompt(self):
        app = self.app()
        starts = []

        async def refresh():
            starts.append(time.monotonic())
            if len(starts) == 1:
                app.runner.request_issue_refresh()

        with patch("symphony.__main__.REFRESH_COALESCE_SECONDS", 0.01), \
             patch.object(app, "refresh_issue_snapshot", side_effect=refresh):
            task = asyncio.create_task(app.poll_issues())
            try:
                async with asyncio.timeout(2):
                    while len(starts) < 2:
                        await asyncio.sleep(0.005)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertLess(starts[1] - starts[0], 1.0)

    async def test_project_events_invalidate_the_mapping_cache(self):
        app = self.app()
        app.fetched_projects = time.monotonic()
        app.repos = {"owner/old": [{"id": "old", "name": "Old"}]}
        repos = {"owner/new": [{"id": "new", "name": "New"}]}
        with patch.object(app.linear, "projects", new_callable=AsyncMock,
                          return_value=(repos, {})) as projects:
            app.request_issue_refresh(projects=True)
            self.assertEqual(await app.refresh_projects(), repos)
            projects.assert_awaited_once()
            self.assertFalse(self.runner.projects_changed)
            await app.refresh_projects()
            projects.assert_awaited_once()

            app.fetched_projects = time.monotonic()
            app.request_issue_refresh()
            await app.refresh_projects()
            projects.assert_awaited_once()

    async def test_successful_linear_writes_request_an_issue_refresh(self):
        app = self.app()
        refresh = Mock(side_effect=self.runner.request_issue_refresh)
        issue = {**self.proposed_issue(), "id": "issue-id"}
        with patch.object(self.runner, "request_issue_refresh", refresh), \
             patch.object(app, "live_issue", return_value=(issue, REPO)), \
             patch.object(app.linear, "update", new_callable=AsyncMock) as update:
            await app.tool("set_plan", {"issue_id": ISSUE, "plan": "Write one verification file."})
            update.assert_awaited_once()
            refresh.assert_called_once_with(projects=False)

            refresh.reset_mock()
            update.side_effect = RuntimeError("Linear rejected the update.")
            with self.assertRaisesRegex(RuntimeError, "rejected"):
                await app.tool("set_plan", {"issue_id": ISSUE, "plan": "Write one verification file."})
            refresh.assert_not_called()

        refresh.reset_mock()
        with patch.object(self.runner, "request_issue_refresh", refresh):
            await self.runner.approve(self.proposed_issue(), REPO)
            refresh.assert_called_once_with()

            refresh.reset_mock()
            pending = []
            with patch.object(self.runner, "job", side_effect=pending.append):
                await self.runner.dispatch(ISSUE, REPO, "")
            for coroutine in pending:
                coroutine.close()
            refresh.assert_called_once_with()

            refresh.reset_mock()
            with self.runner.db:
                self.runner.db.execute("UPDATE tasks SET phase='finishing' WHERE issue=?", (ISSUE,))
            await self.runner.finish(ISSUE)
            refresh.assert_called_once_with()

    async def test_approval_retry_requests_an_issue_refresh(self):
        await self.fail_approval()
        app = self.app()
        refresh = Mock(side_effect=self.runner.request_issue_refresh)
        with patch.object(self.runner, "request_issue_refresh", refresh):
            text = f"/retry {ISSUE}"
            await app.handle("", text, text)
        self.assertEqual(self.runner.task(ISSUE)["phase"], "approved")
        refresh.assert_called_once_with()

    async def test_issue_removal_and_project_move_refresh_the_snapshot(self):
        app = self.app()
        alpha = {REPO: [{"id": "alpha", "name": "Alpha", "icon": ""}]}
        issue = {"identifier": ISSUE, "title": "Alpha issue",
                 "url": "https://linear.app/fixture/issue/KIAN-101",
                 "state": {**self.linear.state, "position": 1}, "description": "",
                 "project": {"id": "alpha", "name": "Alpha"}, "priority": 3,
                 "priorityLabel": "Normal", "estimate": None, "dueDate": None,
                 "updatedAt": "2026-11-20T12:00:00Z", "completedAt": None,
                 "labels": {"nodes": []}}
        with patch.object(app, "refresh_projects", return_value=alpha), \
             patch("symphony.__main__.done_cutoff", return_value=datetime(2026, 11, 20, 12, 0,
                                                                         tzinfo=timezone.utc)):
            with patch.object(app.linear, "query", return_value={
                    "issues": {"nodes": [issue], "pageInfo": {"hasNextPage": False}}}):
                await app.refresh_issue_snapshot()
            self.assertEqual([item["identifier"] for item in (await app.state(0))["projects"][0]["issues"]],
                             [ISSUE])

            with patch.object(app.linear, "query", return_value={
                    "issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}):
                await app.refresh_issue_snapshot()
            self.assertEqual((await app.state(0))["projects"][0]["issues"], [])

            moved = {**issue, "project": {"id": "beta", "name": "Beta"}}
            with patch.object(app.linear, "query", return_value={
                    "issues": {"nodes": [moved], "pageInfo": {"hasNextPage": False}}}):
                await app.refresh_issue_snapshot()
            self.assertEqual((await app.state(0))["projects"][0]["issues"], [])

    async def worker_done(self, status=0):
        await self.approve()
        self.assertEqual(self.linear.state["name"], "Todo")
        result = await self.runner.dispatch(ISSUE, REPO, "Never ship from the worker.")
        self.assertEqual(result["phase"], "working")
        self.assertEqual(self.linear.state["name"], "In Progress")
        with self.assertRaisesRegex(ValueError, "working"):
            await self.runner.dispatch(ISSUE, REPO, "Duplicate")
        await self.phase("review")
        self.assertEqual(self.runner.task(ISSUE)["session"], "ses_fixture")
        self.assertEqual(self.runner.task(ISSUE)["check_exit"], status)

    def review_environment(self):
        return json.loads((self.runner.artifacts(ISSUE) / "review-env.json").read_text())

    def execution(self):
        issue = {
            "identifier": ISSUE, "title": "Fixture", "url": "",
            "state": self.linear.state, "createdAt": "2026-01-01T00:00:00Z",
            "project": {"id": "project", "name": "Fixture"},
        }
        return execution_payload(
            ISSUE, issue, {"entries": [], "complete": False, "error": "Unavailable"}, self.runner)

    def attempts(self):
        return self.execution()["execution"]["worker"]["attempts"]

    @staticmethod
    def pid_alive(pid):
        try:
            with open(f"/proc/{pid}/stat") as stream:
                return stream.read().split()[2] != "Z"
        except (FileNotFoundError, IndexError):
            return False

    async def test_approval_queues_todo_and_auto_dispatch_moves_to_in_progress(self):
        await self.approve()
        self.assertEqual(self.linear.state["name"], "Todo")
        self.assertEqual(self.runner.task(ISSUE)["phase"], "approved")
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 0)
        pending = []
        with patch.object(self.runner, "job", side_effect=pending.append):
            self.assertEqual(await self.runner.dispatch_approved(), [ISSUE])
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.task(ISSUE)["phase"], "working")
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 1)
        self.assertEqual(self.linear.state["name"], "In Progress")
        self.assertEqual(await self.runner.dispatch_approved(), [])

    async def test_pump_auto_dispatches_without_a_coordinator_turn(self):
        app = self.app()
        calls = []

        async def dispatch():
            calls.append(ISSUE)
            return [ISSUE] if len(calls) == 1 else []

        with patch.object(app.runner, "dispatch_approved", side_effect=dispatch), \
             patch.object(app.coordinator, "respond") as respond:
            self.runner.wake.set()
            pump = asyncio.create_task(app.pump())
            try:
                async with asyncio.timeout(2):
                    while len(calls) < 2:
                        await asyncio.sleep(0.01)
            finally:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)
        self.assertEqual(calls, [ISSUE, ISSUE])
        respond.assert_not_awaited()

    async def test_manual_dispatch_requires_live_todo_in_approved_project(self):
        await self.approve()
        for state, project, message in [
            ({"name": "Backlog", "type": "backlog"}, "project", "is Backlog"),
            ({"name": "Todo", "type": "unstarted"}, "other", "approved Linear project"),
            ({"name": "Canceled", "type": "canceled"}, "project", "is Canceled"),
        ]:
            with self.subTest(state=state):
                self.linear.configure(ISSUE, state=state, project=project)
                with self.assertRaisesRegex(ValueError, message):
                    await self.runner.dispatch(ISSUE, REPO, "")
                self.assertEqual(self.runner.task(ISSUE)["phase"], "approved")
                self.assertEqual(self.runner.task(ISSUE)["attempts"], 0)

        self.linear.configure(ISSUE, state={"name": "Todo", "type": "unstarted"})
        pending = []
        with patch.object(self.runner, "job", side_effect=pending.append):
            await self.runner.dispatch(ISSUE, REPO, "")
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.task(ISSUE)["phase"], "working")
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 1)

    async def test_resume_requires_live_in_progress_in_approved_project(self):
        await self.approve()
        with self.runner.db:
            self.runner.db.execute("""UPDATE tasks SET phase='blocked', attempts=1, detail='Paused'
                                      WHERE issue=?""", (ISSUE,))
        for state, project in [
            ({"name": "Backlog", "type": "backlog"}, "project"),
            ({"name": "In Progress", "type": "started"}, "other"),
            ({"name": "Duplicate", "type": "duplicate"}, "project"),
        ]:
            with self.subTest(state=state):
                self.linear.configure(ISSUE, state=state, project=project)
                with self.assertRaises(ValueError):
                    await self.runner.dispatch(ISSUE, REPO, "", resume=True)
                self.assertEqual(self.runner.task(ISSUE)["phase"], "blocked")
                self.assertEqual(self.runner.task(ISSUE)["attempts"], 1)

        self.linear.configure(ISSUE, state={"name": "In Progress", "type": "started"})
        pending = []
        with patch.object(self.runner, "job", side_effect=pending.append):
            await self.runner.dispatch(ISSUE, REPO, "", resume=True)
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.task(ISSUE)["phase"], "working")
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 2)

    async def test_manual_dispatch_restores_approval_after_linear_read_error(self):
        await self.approve()
        with patch.object(self.linear, "issue", side_effect=RuntimeError("Linear is down.")):
            with self.assertRaisesRegex(RuntimeError, "Linear is down"):
                await self.runner.dispatch(ISSUE, REPO, "")
        task = self.runner.task(ISSUE)
        self.assertEqual(task["phase"], "approved")
        self.assertEqual(task["attempts"], 0)

    async def test_concurrent_dispatch_reserves_one_worker(self):
        await self.approve()
        gate = asyncio.Event()
        original = self.linear.issue

        async def delayed(identifier):
            await gate.wait()
            return await original(identifier)

        pending = []
        with patch.object(self.linear, "issue", side_effect=delayed), \
             patch.object(self.runner, "job", side_effect=pending.append):
            first = asyncio.create_task(self.runner.dispatch(ISSUE, REPO, ""))
            async with asyncio.timeout(2):
                while self.runner.task(ISSUE)["phase"] != "working":
                    await asyncio.sleep(0)
            second = asyncio.create_task(self.runner.dispatch(ISSUE, REPO, ""))
            with self.assertRaisesRegex(ValueError, "working"):
                await second
            gate.set()
            await first
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.task(ISSUE)["phase"], "working")
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 1)
        self.assertEqual(len(pending), 1)

    async def test_concurrent_dispatch_respects_the_worker_limit(self):
        await self.approve()
        with self.runner.db:
            self.runner.db.execute("""INSERT INTO tasks(issue,project,repo,title,plan,phase,updated)
                                      VALUES ('KIAN-102','project','owner/second','Second','Plan','approved',0)""")
        self.linear.configure("KIAN-102", state={"name": "Todo", "type": "unstarted"})
        gate = asyncio.Event()
        original = self.linear.issue

        async def delayed(identifier):
            await gate.wait()
            return await original(identifier)

        pending = []
        with patch("symphony.runner.WORKER_LIMIT", 1), \
             patch.object(self.linear, "issue", side_effect=delayed), \
             patch.object(self.runner, "job", side_effect=pending.append):
            first = asyncio.create_task(self.runner.dispatch(ISSUE, REPO, ""))
            async with asyncio.timeout(2):
                while self.runner.task(ISSUE)["phase"] != "working":
                    await asyncio.sleep(0)
            second = asyncio.create_task(self.runner.dispatch("KIAN-102", "owner/second", ""))
            with self.assertRaisesRegex(ValueError, "worker limit"):
                await second
            gate.set()
            await first
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.task("KIAN-102")["phase"], "approved")
        self.assertEqual(self.runner.task("KIAN-102")["attempts"], 0)
        self.assertEqual(len(pending), 1)

    async def test_dispatch_confirms_in_progress_after_status_error(self):
        await self.approve()
        original = self.linear.set_state

        async def fail_after_update(identifier, name, kind):
            await original(identifier, name, kind)
            raise RuntimeError("Linear response was lost.")

        pending = []
        refresh = Mock()
        with patch.object(self.linear, "set_state", side_effect=fail_after_update), \
             patch.object(self.runner, "request_issue_refresh", refresh), \
             patch.object(self.runner, "job", side_effect=pending.append):
            await self.runner.dispatch(ISSUE, REPO, "")
        for coroutine in pending:
            coroutine.close()
        refresh.assert_called_once_with()
        self.assertEqual(self.linear.state["name"], "In Progress")
        self.assertEqual(self.runner.task(ISSUE)["phase"], "working")
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 1)
        self.assertEqual(len(pending), 1)

    async def test_uncertain_status_after_error_parks_once_linear_confirms_in_progress(self):
        await self.approve()
        calls = 0
        original = self.linear.issue

        async def confirm(identifier):
            nonlocal calls
            calls += 1
            if calls == 1:
                return await original(identifier)
            raise RuntimeError("Linear confirmation failed.")

        with patch.object(self.linear, "issue", side_effect=confirm), \
             patch.object(self.linear, "set_state", side_effect=RuntimeError("Status write failed.")):
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                await self.runner.dispatch(ISSUE, REPO, "")
        task = self.runner.task(ISSUE)
        self.assertEqual(task["phase"], "approved")
        self.assertEqual(task["attempts"], 0)
        self.assertIn("uncertain", task["detail"])

        self.linear.configure(ISSUE, state={"name": "In Progress", "type": "started"})
        self.assertEqual(await self.runner.dispatch_approved(), [])
        task = self.runner.task(ISSUE)
        self.assertEqual(task["phase"], "blocked")
        self.assertEqual(task["attempts"], 0)
        self.assertIn("without a reserved worker", task["detail"])
        self.assertEqual(len(self.runner.rows("SELECT * FROM events")), 1)

    async def test_auto_dispatch_reports_linear_errors_without_consuming_attempts(self):
        await self.approve()
        with patch.object(self.linear, "issue", side_effect=RuntimeError("Linear is down.")):
            self.assertEqual(await self.runner.dispatch_approved(), [])
            self.assertEqual(await self.runner.dispatch_approved(), [])
        task = self.runner.task(ISSUE)
        self.assertEqual(task["phase"], "approved")
        self.assertEqual(task["attempts"], 0)
        self.assertIn("Automatic dispatch could not read Linear", task["detail"])
        events = self.runner.rows("SELECT issue, detail, delivered FROM events")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["issue"], ISSUE)
        self.assertIn("Linear is down", events[0]["detail"])

    async def test_auto_dispatch_reports_status_update_errors(self):
        await self.approve()
        with patch.object(self.linear, "set_state", side_effect=RuntimeError("Status write failed.")):
            self.assertEqual(await self.runner.dispatch_approved(), [])
            self.assertEqual(await self.runner.dispatch_approved(), [])
        task = self.runner.task(ISSUE)
        self.assertEqual(task["phase"], "approved")
        self.assertEqual(task["attempts"], 0)
        self.assertIn("Automatic dispatch failed", task["detail"])
        self.assertIn("Status write failed", task["detail"])
        events = self.runner.rows("SELECT issue, detail, delivered FROM events")
        self.assertEqual(len(events), 1)
        self.assertIn("Status write failed", events[0]["detail"])

        pending = []
        with patch.object(self.runner, "job", side_effect=pending.append):
            self.assertEqual(await self.runner.dispatch_approved(), [ISSUE])
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.task(ISSUE)["phase"], "working")
        self.assertIsNone(self.runner.task(ISSUE)["detail"])

    async def test_auto_dispatch_waits_for_repository_owner_before_parking_in_progress(self):
        await self.approve()
        with self.runner.db:
            self.runner.db.execute("UPDATE tasks SET phase='ci' WHERE issue=?", (ISSUE,))
            self.runner.db.execute("""INSERT INTO tasks(issue,project,repo,title,plan,phase,updated)
                                      VALUES ('KIAN-102','project',?,'Second','Plan','approved',0)""",
                                   (REPO,))
        self.linear.configure("KIAN-102", state={"name": "In Progress", "type": "started"})
        self.assertEqual(await self.runner.dispatch_approved(), [])
        self.assertEqual(self.runner.task(ISSUE)["phase"], "ci")
        self.assertEqual(self.runner.task("KIAN-102")["phase"], "approved")
        self.assertEqual(self.runner.task("KIAN-102")["attempts"], 0)

    async def test_successful_refresh_wakes_queued_work_without_snapshot_changes(self):
        await self.approve()
        app = self.app()
        repos = {REPO: [{"id": "project", "name": "Fixture", "icon": ""}]}
        with patch.object(app, "refresh_projects", return_value=repos), \
             patch.object(app.linear, "issues", new_callable=AsyncMock, return_value=[]):
            await app.refresh_issue_snapshot()
            self.runner.wake.clear()
            await app.refresh_issue_snapshot()
        self.assertTrue(self.runner.wake.is_set())

    async def test_unapproved_todo_and_backlog_never_dispatch(self):
        self.linear.configure("KIAN-201", state={"name": "Todo", "type": "unstarted"})
        self.linear.configure("KIAN-202", state={"name": "Backlog", "type": "backlog"})
        self.assertEqual(await self.runner.dispatch_approved(), [])
        self.assertEqual(self.runner.rows("SELECT * FROM tasks"), [])
        with self.assertRaisesRegex(ValueError, "No approved plan"):
            await self.runner.dispatch("KIAN-201", REPO, "")

        await self.runner.approve({**self.proposed_issue(), "identifier": "KIAN-201"}, REPO)
        for state, project in [
            ({"name": "Backlog", "type": "backlog"}, "project"),
            ({"name": "Canceled", "type": "canceled"}, "project"),
            ({"name": "Duplicate", "type": "duplicate"}, "project"),
            ({"name": "Todo", "type": "unstarted"}, "other"),
        ]:
            with self.subTest(state=state):
                self.linear.configure("KIAN-201", state=state, project=project)
                self.assertEqual(await self.runner.dispatch_approved(), [])
                self.assertEqual(self.runner.task("KIAN-201")["phase"], "approved")
                self.assertEqual(self.runner.task("KIAN-201")["attempts"], 0)
        self.assertIsNone(self.runner.task("KIAN-201")["detail"])
        self.assertEqual(self.runner.rows("SELECT * FROM events"), [])

    async def test_auto_dispatch_waits_for_provider_capacity_and_repository(self):
        await self.approve()
        self.runner.save_setting("paused", "OpenCode Go: quota")
        self.assertEqual(await self.runner.dispatch_approved(), [])
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 0)
        self.runner.save_setting("paused", "")

        with self.runner.db:
            self.runner.db.execute("""INSERT INTO tasks(issue,project,repo,title,plan,phase,updated)
                                      VALUES ('KIAN-301','project',?,'Held','Plan','ci',0)""", (REPO,))
        self.assertEqual(await self.runner.dispatch_approved(), [])
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 0)
        with self.runner.db:
            self.runner.db.execute("DELETE FROM tasks WHERE issue='KIAN-301'")

        for index in range(3):
            with self.runner.db:
                self.runner.db.execute("""INSERT INTO tasks(issue,project,repo,title,plan,phase,updated)
                                          VALUES (?,?,?,?,?,'working',0)""",
                                       (f"KIAN-40{index}", "project", f"owner/worker{index}", "Held", "Plan"))
        self.assertEqual(await self.runner.dispatch_approved(), [])
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 0)
        self.assertEqual(self.runner.rows("SELECT * FROM events"), [])
        with self.runner.db:
            self.runner.db.execute("DELETE FROM tasks WHERE issue LIKE 'KIAN-40%'")
            self.runner.db.execute("UPDATE tasks SET attempts=3 WHERE issue=?", (ISSUE,))
        self.assertEqual(await self.runner.dispatch_approved(), [])
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 3)
        with self.runner.db:
            self.runner.db.execute("UPDATE tasks SET attempts=0 WHERE issue=?", (ISSUE,))

        pending = []
        with patch.object(self.runner, "job", side_effect=pending.append):
            self.assertEqual(await self.runner.dispatch_approved(), [ISSUE])
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.linear.state["name"], "In Progress")
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 1)

    async def test_restart_recovers_queued_approval_once(self):
        await self.approve()
        restarted = Runner(self.root, self.linear)
        self.addCleanup(restarted.db.close)
        restarted.recover()
        self.assertEqual(restarted.task(ISSUE)["phase"], "approved")
        pending = []
        with patch.object(restarted, "job", side_effect=pending.append):
            self.assertEqual(await restarted.dispatch_approved(), [ISSUE])
            self.assertEqual(await restarted.dispatch_approved(), [])
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(restarted.task(ISSUE)["phase"], "working")
        self.assertEqual(restarted.task(ISSUE)["attempts"], 1)
        self.assertEqual(restarted.rows("SELECT issue FROM tasks WHERE phase='working'"), [{"issue": ISSUE}])

    async def test_draining_holds_new_dispatch_and_cancel_resumes_queued_approval(self):
        app = self.app()
        await self.approve()
        self.runner.draining = True
        self.runner.wake.clear()
        self.assertEqual(await self.runner.dispatch_approved(), [])
        with self.assertRaisesRegex(ValueError, "draining"):
            await self.runner.dispatch(ISSUE, REPO, "")
        task = self.runner.task(ISSUE)
        self.assertEqual((task["phase"], task["attempts"]), ("approved", 0))
        self.assertEqual(self.linear.state["name"], "Todo")

        self.assertEqual(await app.set_draining(False), {"draining": False})
        self.assertTrue(self.runner.wake.is_set())
        pending = []
        with patch.object(self.runner, "job", side_effect=pending.append):
            self.assertEqual(await self.runner.dispatch_approved(), [ISSUE])
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.task(ISSUE)["phase"], "working")
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 1)

    async def test_drain_during_awaited_dispatch_keeps_reservation_and_rejects_new_work(self):
        await self.approve()
        gate = asyncio.Event()
        original = self.linear.issue

        async def delayed(identifier):
            await gate.wait()
            return await original(identifier)

        pending = []
        with patch.object(self.linear, "issue", side_effect=delayed), \
             patch.object(self.runner, "job", side_effect=pending.append):
            first = asyncio.create_task(self.runner.dispatch(ISSUE, REPO, ""))
            async with asyncio.timeout(2):
                while self.runner.task(ISSUE)["phase"] != "working":
                    await asyncio.sleep(0)
            self.runner.draining = True
            gate.set()
            await first
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.task(ISSUE)["phase"], "working")
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 1)

        with self.runner.db:
            self.runner.db.execute(
                """INSERT INTO tasks(issue,project,repo,title,plan,phase,updated)
                   VALUES ('KIAN-102','project','owner/second','Second','Plan','approved',0)"""
            )
        self.linear.configure("KIAN-102", state={"name": "Todo", "type": "unstarted"})
        with self.assertRaisesRegex(ValueError, "draining"):
            await self.runner.dispatch("KIAN-102", "owner/second", "")
        task = self.runner.task("KIAN-102")
        self.assertEqual((task["phase"], task["attempts"]), ("approved", 0))
        self.assertEqual(self.linear.state["name"], "In Progress")

    async def test_draining_preserves_resume_admission_for_existing_work(self):
        await self.approve()
        with self.runner.db:
            self.runner.db.execute(
                "UPDATE tasks SET phase='blocked', attempts=1, detail='Paused' WHERE issue=?",
                (ISSUE,),
            )
        self.linear.configure(ISSUE, state={"name": "In Progress", "type": "started"})
        self.runner.draining = True
        pending = []
        with patch.object(self.runner, "job", side_effect=pending.append):
            result = await self.runner.dispatch(ISSUE, REPO, "Resume the work.", resume=True)
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(result, {"issue": ISSUE, "phase": "working"})
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 2)

    async def test_draining_preserves_publication_admission(self):
        await self.worker_done()
        self.runner.read_notes(ISSUE)
        self.runner.draining = True
        pending = []
        with patch.object(self.runner, "job", side_effect=pending.append):
            result = self.runner.ship(ISSUE)
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(result, {"issue": ISSUE, "phase": "publishing"})
        self.assertEqual(self.runner.task(ISSUE)["phase"], "publishing")

    async def test_continue_clears_provider_pause_but_not_draining(self):
        app = self.app()
        await self.approve()
        self.runner.save_setting("paused", "OpenCode Go: quota")
        self.runner.draining = True
        await app.handle(REPO, "/continue", "/continue")
        self.assertEqual(self.runner.setting("paused"), "")
        self.assertTrue(self.runner.draining)
        self.assertEqual(await self.runner.dispatch_approved(), [])
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 0)

    async def test_draining_resets_with_a_new_runner_and_application(self):
        self.runner.draining = True
        restarted = Runner(self.root, self.linear)
        self.addCleanup(restarted.db.close)
        self.assertFalse(restarted.draining)
        app = self.app(restarted)
        self.assertFalse((await app.state(0))["draining"])
        self.assertFalse(restarted.draining)

    async def test_opencode_error_reporting_and_dispatch_pause(self):
        await self.approve()
        expired = "This API key has expired."
        throttled = "Too many requests."
        quota = "OpenCode Go quota exhausted."
        for payload, message, pauses in [
            ({"type": "provider.auth", "message": expired, "status": 401}, expired, True),
            ({"type": "provider.api", "message": throttled, "status": 429}, throttled, True),
            ({"type": "provider.api", "message": quota}, quota, True),
            ({"type": "provider.network"}, "provider.network", False),
            ("Worker disconnected.", "Worker disconnected.", False),
            (None, "OpenCode error", False),
        ]:
            with self.subTest(payload=payload):
                self.runner.save_setting("paused", "")
                event = json.dumps({"type": "error", "error": payload})
                args = [sys.executable, "-c", f"print({event!r})"]
                with (self.root / "worker.log").open("wb") as log:
                    with self.assertRaises(RuntimeError) as failure:
                        await self.runner.run_opencode(ISSUE, args, self.root, os.environ, log)
                self.assertEqual(str(failure.exception), f"{message} See worker.log.")
                self.assertEqual(self.runner.setting("paused"), f"OpenCode Go: {message}" if pauses else "")
                if pauses:
                    with self.assertRaisesRegex(ValueError, "Dispatch is paused"):
                        await self.runner.dispatch(ISSUE, REPO, "")

    async def test_approval_verification_notes_push_and_ci(self):
        with self.assertRaisesRegex(ValueError, "No approved plan"):
            await self.runner.dispatch(ISSUE, REPO, "")
        await self.worker_done()
        with self.assertRaisesRegex(ValueError, "Read the latest"):
            self.runner.ship(ISSUE)
        audit = self.runner.read_notes(ISSUE)
        self.assertIn("None\n", audit)
        self.assertIn("VERDICT: NO ISSUES", audit)

        observe = asyncio.Event()

        async def runs(repo, revision):
            self.assertEqual(repo, REPO)
            self.assertEqual(self.git(self.remote, "rev-parse", "main"), revision)
            self.assertEqual(self.linear.state["name"], "In Progress")
            await observe.wait()
            return [{"path": ".github/workflows/test.yml", "status": "completed", "conclusion": "success"}]

        with patch("symphony.runner.ci_runs", side_effect=runs):
            self.runner.ship(ISSUE)
            await self.phase("ci")
            with self.assertRaisesRegex(ValueError, "reserved"):
                with self.runner.db:
                    self.runner.db.execute("""INSERT INTO tasks(issue,project,repo,title,plan,phase,updated)
                                              VALUES ('KIAN-102','project',?,'Next','Plan','approved',0)""", (REPO,))
                await self.runner.dispatch("KIAN-102", REPO, "")
            observe.set()
            await self.phase("done")
        self.assertEqual(self.linear.state["name"], "Done")
        self.assertFalse(self.runner.worktree(ISSUE).exists())
        self.assertEqual(self.git(self.remote, "show", "main:verified.txt"), "Tested")
        execution = {row["kind"]: row for row in self.runner.rows("SELECT * FROM execution")}
        for kind in ("startup", "worker", "verification", "review", "attempt",
                     "publication", "completion"):
            self.assertIn(kind, execution)
            self.assertIsNotNone(execution[kind]["ended"])

    async def test_successful_verification_runs_one_read_only_review(self):
        await self.worker_done()
        artifacts = self.runner.artifacts(ISSUE)
        environment = self.review_environment()
        self.assertEqual((artifacts / "review-runs").read_text(), "review\n")
        self.assertEqual(environment["args"][environment["args"].index("--model") + 1],
                         "opencode-go/kimi-k3.1-pro#low")
        for flag in ("run", "--standalone", "--auto", "--format"):
            self.assertIn(flag, environment["args"])
        self.assertNotIn("--session", environment["args"])
        self.assertFalse(environment["github"])
        self.assertFalse(environment["linear"])
        self.assertFalse(environment["api_key"])
        self.assertFalse(environment["webhook"])
        permissions = environment["config"]["permissions"]
        self.assertEqual(permissions[0], {"action": "*", "resource": "*", "effect": "deny"})
        for action in ("edit", "write", "shell", "subagent", "websearch"):
            self.assertIn({"action": action, "resource": "*", "effect": "deny"}, permissions)
        self.assertIn({"action": "shell", "resource": "git push *", "effect": "deny"},
                      permissions)
        self.assertIn({"action": "shell", "resource": "gh pr create *", "effect": "deny"},
                      permissions)
        self.assertIn({"action": "read", "resource": "*", "effect": "allow"}, permissions)
        self.assertIn({"action": "external_directory", "resource": "*", "effect": "deny"},
                      permissions)
        self.assertFalse(any(rule["effect"] == "allow" and rule["action"] in
                             {"edit", "write", "shell", "subagent"} for rule in permissions))
        self.assertEqual(environment["config"]["mcp"], {"servers": {}})
        self.assertIn("Write one verification file.", environment["prompt"])
        self.assertIn("None\n", environment["prompt"])
        self.assertIn("SYMPHONY_DIFF_BEGIN", environment["prompt"])
        self.assertIn("verified.txt", environment["prompt"])
        base = self.runner.task(ISSUE)["base"]
        self.assertIsNotNone(base)
        self.assertIn(f"recorded base {base}", environment["prompt"])
        self.assertNotIn("recorded base None", environment["prompt"])
        diff = self.git(self.runner.worktree(ISSUE), "diff", "--no-ext-diff",
                        self.runner.task(ISSUE)["base"], "--")
        self.assertEqual((artifacts / "review.diff").read_text(), diff)
        self.assertIn("ses_review", (artifacts / "review.log").read_text())
        report = (artifacts / "review.md").read_text()
        self.assertIn("VERDICT: NO ISSUES", report)
        self.assertNotIn("VERDICT: UNAVAILABLE", report)
        self.assertIn("VERDICT: NO ISSUES", self.runner.read_notes(ISSUE))
        self.assertEqual(self.runner.task(ISSUE)["session"], "ses_fixture")
        review = self.runner.rows("SELECT * FROM execution WHERE kind='review'")
        self.assertEqual(len(review), 1)
        self.assertIsNotNone(review[0]["ended"])
        self.assertEqual(review[0]["detail"], "Review completed: NO ISSUES.")
        attempt, = self.attempts()
        self.assertEqual(attempt["review"]["verdict"], "NO ISSUES")
        self.assertEqual(attempt["review"]["findingCount"], 0)
        self.assertTrue(attempt["review"]["findingCountComplete"])
        self.assertTrue(attempt["notes"]["available"])

    async def test_fresh_and_resumed_workers_receive_default_mcp_and_deny_websearch(self):
        with patch.dict(os.environ, {
            "GITHUB_TOKEN": "gh-secret",
            "GH_TOKEN": "gh-cli-secret",
            "LINEAR_API_KEY": "lin-secret",
            "OPENAI_API_KEY": "oa-secret",
            "OPENCODE_GO_API_KEY": "go-secret",
            "LINEAR_WEBHOOK_SECRET": "webhook-secret",
        }):
            await self.worker_done()
            await self.runner.dispatch(ISSUE, REPO, "Never ship from the worker. Follow up.", resume=True)
            await self.phase("review")
        records = [json.loads(line) for line in
                   (self.runner.artifacts(ISSUE) / "worker-envs.jsonl").read_text().splitlines()]
        self.assertEqual(len(records), 2)
        self.assertNotIn("--session", records[0]["args"])
        self.assertIn("--session", records[1]["args"])
        self.assertEqual(records[1]["args"][records[1]["args"].index("--session") + 1], "ses_fixture")
        expected_servers = {
            "context7": {"type": "remote", "url": "https://mcp.context7.com/mcp", "disabled": False},
            "exa": {"type": "remote", "url": "https://mcp.exa.ai/mcp", "disabled": False},
        }
        for record in records:
            self.assertFalse(record["github"])
            self.assertFalse(record["gh"])
            self.assertFalse(record["linear"])
            self.assertFalse(record["openai"])
            self.assertFalse(record["api_key"])
            self.assertFalse(record["webhook"])
            self.assertEqual(record["config"]["mcp"], {"servers": expected_servers})
            permissions = record["config"]["permissions"]
            self.assertIn({"action": "websearch", "resource": "*", "effect": "deny"}, permissions)
            self.assertIn({"action": "shell", "resource": "git push *", "effect": "deny"}, permissions)
            self.assertIn({"action": "shell", "resource": "gh pr create *", "effect": "deny"}, permissions)
            self.assertIn({"action": "subagent", "resource": "*", "effect": "deny"}, permissions)
        self.assertEqual(self.review_environment()["config"]["mcp"], {"servers": {}})

    async def test_review_model_can_be_overridden(self):
        with patch.dict(os.environ, {"SYMPHONY_REVIEW_MODEL": "opencode-go/review-fixture"}):
            await self.worker_done()
        environment = self.review_environment()
        self.assertEqual(environment["args"][environment["args"].index("--model") + 1],
                         "opencode-go/review-fixture")
        self.assertIn("opencode-go/review-fixture",
                      (self.runner.artifacts(ISSUE) / "review.log").read_text())

    async def test_two_attempts_retain_their_own_notes_and_review_after_rework(self):
        first_review = ("VERDICT: ISSUES\n- symphony/a.py:1 - First finding.\n"
                        "- symphony/b.py:2 - Second finding.\n")
        with patch.dict(os.environ, {
                "FIXTURE_NOTES": "First attempt notes\n",
                "FIXTURE_REVIEW": "custom", "FIXTURE_REVIEW_TEXT": first_review}):
            await self.worker_done()
        first, = self.attempts()
        self.assertEqual(first["notes"]["text"], "First attempt notes")
        self.assertFalse(first["notes"]["truncated"])
        self.assertEqual(first["review"]["verdict"], "ISSUES")
        self.assertEqual(first["review"]["findingCount"], 2)
        self.assertTrue(first["review"]["findingCountComplete"])
        self.assertEqual(first["review"]["model"], "opencode-go/kimi-k3.1-pro#low")
        self.assertIn("First finding", first["review"]["text"])

        with patch.dict(os.environ, {
                "FIXTURE_NOTES": "Second attempt notes\n",
                "FIXTURE_REVIEW": "custom", "FIXTURE_REVIEW_TEXT": "VERDICT: NO ISSUES"}):
            await self.runner.dispatch(ISSUE, REPO, "Never ship from the worker. Follow up.",
                                       resume=True)
            await self.phase("review")
        attempts = self.attempts()
        self.assertEqual([attempt["number"] for attempt in attempts], [1, 2])
        self.assertEqual([attempt["counter"] for attempt in attempts], [1, 2])
        self.assertEqual(len({attempt["id"] for attempt in attempts}), 2)
        self.assertEqual(attempts[0]["notes"]["text"], "First attempt notes")
        self.assertEqual(attempts[0]["review"]["verdict"], "ISSUES")
        self.assertEqual(attempts[0]["review"]["findingCount"], 2)
        self.assertEqual(attempts[1]["notes"]["text"], "Second attempt notes")
        self.assertEqual(attempts[1]["review"]["verdict"], "NO ISSUES")
        self.assertEqual(attempts[1]["review"]["findingCount"], 0)
        worker = self.execution()["execution"]["worker"]
        self.assertEqual(worker["attemptCount"], 2)
        self.assertEqual(worker["findings"], {
            "available": True, "complete": True, "count": 2,
            "reviewCount": 2, "unlinkedReviews": 0,
        })
        raw = json.dumps(self.runner.rows("SELECT data FROM execution WHERE kind='attempt'"))
        self.assertIn("First attempt notes", raw)

    async def test_identical_fresh_notes_are_retained_for_each_attempt(self):
        with patch.dict(os.environ, {
                "FIXTURE_NOTES": "None\n", "FIXTURE_REVIEW": "custom",
                "FIXTURE_REVIEW_TEXT": "VERDICT: NO ISSUES"}):
            await self.worker_done()
            await self.runner.dispatch(
                ISSUE, REPO, "Never ship from the worker. Follow up.", resume=True)
            await self.phase("review")
        attempts = self.attempts()
        self.assertEqual(len(attempts), 2)
        self.assertEqual([attempt["notes"]["text"] for attempt in attempts], ["None", "None"])
        self.assertTrue(all(attempt["notes"]["available"] for attempt in attempts))
        self.assertEqual([attempt["review"]["verdict"] for attempt in attempts],
                         ["NO ISSUES", "NO ISSUES"])

    async def test_retry_counter_reset_keeps_separate_attempt_identities(self):
        with patch.dict(os.environ, {
                "FIXTURE_NOTES": "Before retry\n",
                "FIXTURE_REVIEW": "custom",
                "FIXTURE_REVIEW_TEXT": "VERDICT: ISSUES\n- a.py:1 - Before retry.\n"}):
            await self.worker_done()
        first, = self.attempts()
        self.runner.block(ISSUE, "Parked for retry")
        app = self.app()
        app.respond = AsyncMock()
        await app.handle(REPO, f"/retry {ISSUE}", f"/retry {ISSUE}")
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 0)
        with patch.dict(os.environ, {
                "FIXTURE_NOTES": "After retry\n",
                "FIXTURE_REVIEW": "custom", "FIXTURE_REVIEW_TEXT": "VERDICT: NO ISSUES"}):
            await self.runner.dispatch(ISSUE, REPO, "Never ship from the worker.", resume=True)
            await self.phase("review")
        attempts = self.attempts()
        self.assertEqual([attempt["counter"] for attempt in attempts], [1, 1])
        self.assertEqual([attempt["number"] for attempt in attempts], [1, 2])
        self.assertEqual(attempts[0]["id"], first["id"])
        self.assertNotEqual(attempts[0]["id"], attempts[1]["id"])
        self.assertEqual(attempts[0]["notes"]["text"], "Before retry")
        self.assertEqual(attempts[0]["review"]["verdict"], "ISSUES")
        self.assertEqual(attempts[1]["notes"]["text"], "After retry")
        self.assertEqual(attempts[1]["review"]["verdict"], "NO ISSUES")

    async def test_blocked_attempts_retain_their_notes_without_reusing_old_ones(self):
        with patch.dict(os.environ, {
                "FIXTURE_NOTES": "Blocked attempt notes\n",
                "FIXTURE_BLOCK": "Fixture blocked before verification."}):
            await self.approve()
            await self.runner.dispatch(ISSUE, REPO, "Never ship from the worker.")
            await self.phase("blocked")
        first, = self.attempts()
        self.assertEqual(first["notes"]["text"], "Blocked attempt notes")
        self.assertFalse(first["review"]["recorded"])
        self.assertEqual(first["review"]["verdict"], "UNAVAILABLE")

        with patch.dict(os.environ, {
                "FIXTURE_SKIP_NOTES": "1", "FIXTURE_BLOCK": "Fixture blocked again."}):
            await self.runner.dispatch(ISSUE, REPO, "Never ship from the worker.", resume=True)
            await self.phase("blocked")
        attempts = self.attempts()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["notes"]["text"], "Blocked attempt notes")
        self.assertFalse(attempts[1]["notes"]["available"])
        self.assertIn("No notes.md was produced", attempts[1]["notes"]["detail"])
        self.assertFalse(attempts[1]["review"]["recorded"])
        self.assertEqual(attempts[1]["review"]["verdict"], "UNAVAILABLE")
        self.assertNotIn("VERDICT", json.dumps(attempts[1]["review"]))

    async def test_artifact_cleanup_failure_does_not_attribute_previous_notes(self):
        with patch.dict(os.environ, {
                "FIXTURE_NOTES": "Previous attempt notes\n",
                "FIXTURE_REVIEW": "custom", "FIXTURE_REVIEW_TEXT": "VERDICT: NO ISSUES"}):
            await self.worker_done()
        first, = self.attempts()
        self.assertEqual(first["notes"]["text"], "Previous attempt notes")
        original_unlink = Path.unlink

        def fail_checks(path, *args, **kwargs):
            if path.name == "checks.sh":
                raise PermissionError("Fixture startup cleanup failed")
            return original_unlink(path, *args, **kwargs)

        with patch("pathlib.Path.unlink", new=fail_checks):
            await self.runner.dispatch(ISSUE, REPO, "Never ship from the worker.", resume=True)
            await self.phase("blocked")
        attempts = self.attempts()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["notes"]["text"], "Previous attempt notes")
        self.assertFalse(attempts[1]["notes"]["available"])
        self.assertEqual(attempts[1]["notes"]["text"], "")
        self.assertIn("did not initialize notes.md", attempts[1]["notes"]["detail"])
        self.assertIn("Fixture startup cleanup failed", self.runner.task(ISSUE)["detail"])

    async def test_notes_unlink_failure_does_not_attribute_previous_notes(self):
        with patch.dict(os.environ, {
                "FIXTURE_NOTES": "Previous attempt notes\n",
                "FIXTURE_REVIEW": "custom", "FIXTURE_REVIEW_TEXT": "VERDICT: NO ISSUES"}):
            await self.worker_done()
        first, = self.attempts()
        self.assertEqual(first["notes"]["text"], "Previous attempt notes")
        original_unlink = Path.unlink

        def fail_notes(path, *args, **kwargs):
            if path.name == "notes.md":
                raise PermissionError("Fixture notes cleanup failed")
            return original_unlink(path, *args, **kwargs)

        with patch("pathlib.Path.unlink", new=fail_notes):
            await self.runner.dispatch(ISSUE, REPO, "Never ship from the worker.", resume=True)
            await self.phase("blocked")
        attempts = self.attempts()
        self.assertEqual(len(attempts), 2)
        self.assertEqual(attempts[0]["notes"]["text"], "Previous attempt notes")
        self.assertFalse(attempts[1]["notes"]["available"])
        self.assertEqual(attempts[1]["notes"]["text"], "")
        self.assertIn("did not initialize notes.md", attempts[1]["notes"]["detail"])
        self.assertIn("Fixture notes cleanup failed", self.runner.task(ISSUE)["detail"])

    async def test_snapshots_are_redacted_bounded_and_count_findings_before_truncation(self):
        token = "ghp_" + "a" * 30
        notes = token + "\n" + "\n".join(f"note line {index}" for index in range(150))
        review = ("VERDICT: ISSUES\n"
                  + "\n".join(f"- symphony/f{index}.py:{index} - Finding {index}."
                              for index in range(250)))
        with patch.dict(os.environ, {
                "FIXTURE_NOTES": notes, "FIXTURE_REVIEW": "custom",
                "FIXTURE_REVIEW_TEXT": review}):
            await self.worker_done()
        attempt, = self.attempts()
        self.assertTrue(attempt["notes"]["truncated"])
        self.assertLessEqual(len(attempt["notes"]["text"].splitlines()), NOTES_LINES)
        self.assertIn("[redacted]", attempt["notes"]["text"])
        self.assertNotIn(token, attempt["notes"]["text"])
        self.assertEqual(attempt["review"]["verdict"], "ISSUES")
        self.assertEqual(attempt["review"]["findingCount"], 250)
        self.assertFalse(attempt["review"]["findingCountComplete"])
        self.assertTrue(attempt["review"]["truncated"])
        self.assertLessEqual(len(attempt["review"]["text"].splitlines()), REVIEW_LINES + 2)
        raw = json.dumps(self.runner.rows("SELECT data FROM execution"))
        payload = json.dumps(self.execution())
        self.assertNotIn(token, raw)
        self.assertNotIn(token, payload)
        self.assertIn("[redacted]", raw)
        findings = self.execution()["execution"]["worker"]["findings"]
        self.assertTrue(findings["available"])
        self.assertFalse(findings["complete"])
        self.assertEqual(findings["count"], 250)

    async def test_review_receives_the_full_diff_beyond_read_diff_limit(self):
        with patch.dict(os.environ, {"FIXTURE_LARGE_DIFF": "1"}):
            await self.worker_done()
        artifacts = self.runner.artifacts(ISSUE)
        diff = (artifacts / "review.diff").read_text()
        self.assertGreater(len(diff), 48000)
        self.assertIn("END-OF-LARGE-DIFF", diff)
        self.assertIn("END-OF-LARGE-DIFF", self.review_environment()["prompt"])

    async def test_failed_verification_skips_review_and_clears_a_stale_report(self):
        artifacts = self.runner.artifacts(ISSUE)
        artifacts.mkdir(parents=True)
        (artifacts / "review.md").write_text("VERDICT: NO ISSUES\nStale report.\n")
        with patch.dict(os.environ, {"FIXTURE_CHECKS_FAIL": "1"}):
            await self.worker_done(status=1)
        report = (artifacts / "review.md").read_text()
        self.assertIn("VERDICT: UNAVAILABLE", report)
        self.assertIn("verification exited 1", report)
        self.assertNotIn("Stale report", report)
        self.assertFalse((artifacts / "review-runs").exists())
        self.assertFalse((artifacts / "review.log").exists())
        review = self.runner.rows("SELECT * FROM execution WHERE kind='review'")
        self.assertEqual(len(review), 1)
        self.assertIn("skipped", review[0]["detail"])
        attempt, = self.attempts()
        self.assertEqual(attempt["review"]["verdict"], "UNAVAILABLE")
        self.assertIsNone(attempt["review"]["findingCount"])
        self.assertIn("skipped", attempt["review"]["detail"])

    async def test_review_findings_are_readable_audit_advice(self):
        with patch.dict(os.environ, {"FIXTURE_REVIEW": "findings"}):
            await self.worker_done()
        report = (self.runner.artifacts(ISSUE) / "review.md").read_text()
        self.assertIn("VERDICT: ISSUES", report)
        audit = self.runner.read_notes(ISSUE)
        self.assertIn("- symphony/runner.py:1 - Fixture finding.", audit)
        self.assertEqual(self.runner.task(ISSUE)["phase"], "review")
        attempt, = self.attempts()
        self.assertEqual(attempt["review"]["verdict"], "ISSUES")
        self.assertEqual(attempt["review"]["findingCount"], 1)
        self.assertTrue(attempt["review"]["findingCountComplete"])

    async def test_malformed_review_output_is_unavailable(self):
        with patch.dict(os.environ, {"FIXTURE_REVIEW": "malformed"}):
            await self.worker_done()
        report = (self.runner.artifacts(ISSUE) / "review.md").read_text()
        self.assertIn("VERDICT: UNAVAILABLE", report)
        self.assertIn("no valid structured verdict", report)
        self.assertIn("Looks fine to me.", (self.runner.artifacts(ISSUE) / "review.log").read_text())
        attempt, = self.attempts()
        self.assertEqual(attempt["review"]["verdict"], "UNAVAILABLE")
        self.assertIsNone(attempt["review"]["findingCount"])
        self.assertFalse(attempt["review"]["findingCountComplete"])

    async def test_empty_review_output_is_unavailable(self):
        with patch.dict(os.environ, {"FIXTURE_REVIEW": "empty"}):
            await self.worker_done()
        report = (self.runner.artifacts(ISSUE) / "review.md").read_text()
        self.assertIn("VERDICT: UNAVAILABLE", report)
        self.assertIn("terminal response contained no report", report)

    async def test_review_uses_only_the_terminal_response_after_progress(self):
        with patch.dict(os.environ, {"FIXTURE_REVIEW": "progress"}):
            await self.worker_done()
        artifacts = self.runner.artifacts(ISSUE)
        report = (artifacts / "review.md").read_text()
        self.assertIn("VERDICT: NO ISSUES", report)
        self.assertNotIn("Reviewing the diff.", report)
        log = (artifacts / "review.log").read_text()
        self.assertIn("Reviewing the diff.", log)
        self.assertIn('"reason": "stop"', log)

    async def test_review_ignores_intermediate_clean_text_before_final_issues(self):
        with patch.dict(os.environ, {"FIXTURE_REVIEW": "intermediate"}):
            await self.worker_done()
        report = (self.runner.artifacts(ISSUE) / "review.md").read_text()
        self.assertIn("VERDICT: ISSUES", report)
        self.assertIn("- symphony/runner.py:1 - Final finding.", report)
        self.assertNotIn("VERDICT: NO ISSUES", report)

    async def test_tool_call_only_review_is_unavailable(self):
        with patch.dict(os.environ, {"FIXTURE_REVIEW": "toolonly"}):
            await self.worker_done()
        report = (self.runner.artifacts(ISSUE) / "review.md").read_text()
        self.assertIn("VERDICT: UNAVAILABLE", report)
        self.assertIn("stopped before a final response", report)

    async def test_nonterminal_review_text_is_unavailable(self):
        with patch.dict(os.environ, {"FIXTURE_REVIEW": "nonterminal"}):
            await self.worker_done()
        report = (self.runner.artifacts(ISSUE) / "review.md").read_text()
        self.assertIn("VERDICT: UNAVAILABLE", report)
        self.assertIn("stopped before a final response", report)
        self.assertNotIn("VERDICT: NO ISSUES", report)

    async def test_reviewer_error_is_unavailable_and_preserves_worker_outcome(self):
        with patch.dict(os.environ, {"FIXTURE_REVIEW": "fail"}):
            await self.worker_done()
        artifacts = self.runner.artifacts(ISSUE)
        report = (artifacts / "review.md").read_text()
        self.assertIn("VERDICT: UNAVAILABLE", report)
        self.assertIn("Fixture review failed", report)
        self.assertIn("Fixture review failed", (artifacts / "review.log").read_text())
        task = self.runner.task(ISSUE)
        self.assertEqual(task["session"], "ses_fixture")
        self.assertEqual(task["check_exit"], 0)
        self.assertIn("Fixture review failed",
                      self.runner.rows("SELECT * FROM execution WHERE kind='review'")[0]["detail"])
        attempt, = self.attempts()
        self.assertEqual(attempt["review"]["verdict"], "UNAVAILABLE")
        self.assertIn("Fixture review failed", attempt["review"]["text"])

    async def test_review_timeout_cleans_up_the_process_group(self):
        with patch("symphony.runner.REVIEW_SECONDS", 0.2), \
             patch.dict(os.environ, {"FIXTURE_REVIEW": "timeout"}):
            await self.worker_done()
        artifacts = self.runner.artifacts(ISSUE)
        report = (artifacts / "review.md").read_text()
        self.assertIn("VERDICT: UNAVAILABLE", report)
        self.assertIn("exceeded", report)
        attempt, = self.attempts()
        self.assertEqual(attempt["review"]["verdict"], "UNAVAILABLE")
        self.assertIsNone(attempt["review"]["findingCount"])
        pids = [int(value) for value in (artifacts / "review-pids").read_text().split()]
        async with asyncio.timeout(2):
            while any(self.pid_alive(pid) for pid in pids):
                await asyncio.sleep(0.01)

    async def test_interrupted_review_is_retained_as_unavailable(self):
        await self.approve()
        with patch.object(self.runner, "run_review", AsyncMock(side_effect=asyncio.CancelledError)):
            await self.runner.dispatch(ISSUE, REPO, "Never ship from the worker.")
            job, = list(self.runner.jobs)
            with self.assertRaises(asyncio.CancelledError):
                await job
        review = self.runner.rows("SELECT * FROM execution WHERE kind='review'")
        self.assertEqual(len(review), 1)
        data = json.loads(review[0]["data"])
        self.assertEqual(data["verdict"], "UNAVAILABLE")
        self.assertEqual(review[0]["detail"], "Review interrupted.")
        self.assertIn("Review interrupted", data["report"])
        attempt, = self.attempts()
        self.assertEqual(attempt["review"]["verdict"], "UNAVAILABLE")

    async def test_review_process_pid_is_recorded_without_replacing_session(self):
        artifacts = self.runner.artifacts(ISSUE)
        artifacts.mkdir(parents=True)
        prompt = artifacts / "review-input.md"
        prompt.write_text("fixture")
        with self.runner.db:
            self.runner.db.execute("""INSERT INTO tasks(issue,project,repo,title,plan,phase,
                                      session,updated)
                                      VALUES (?,?,?,?,?,'working',?,?)""",
                                   (ISSUE, "project", REPO, "Fixture", "Plan",
                                    "ses_fixture", 1))
        script = ("import json; "
                  "print(json.dumps({'type':'text','sessionID':'ses_review',"
                  "'part':{'messageID':'msg_final','type':'text',"
                  "'text':'VERDICT: NO ISSUES'}})); "
                  "print(json.dumps({'type':'step_finish','sessionID':'ses_review',"
                  "'part':{'messageID':'msg_final','type':'step-finish',"
                  "'reason':'stop'}}))")
        with (artifacts / "review.log").open("ab") as log, \
             patch.object(self.runner, "change", wraps=self.runner.change) as change:
            report, error = await self.runner.run_review(
                ISSUE, [sys.executable, "-c", script], self.root, os.environ, log, prompt)
        self.assertEqual((report, error), ("VERDICT: NO ISSUES", ""))
        change.assert_called_once()
        self.assertEqual(change.call_args.args, (ISSUE,))
        self.assertIn("pid", change.call_args.kwargs)
        self.assertNotIn("session", change.call_args.kwargs)
        task = self.runner.task(ISSUE)
        self.assertEqual(task["session"], "ses_fixture")
        self.assertEqual(task["pid"], change.call_args.kwargs["pid"])

    async def test_reviewer_tree_mutation_blocks_handoff(self):
        with patch.dict(os.environ, {"FIXTURE_REVIEW": "mutate"}):
            await self.approve()
            await self.runner.dispatch(ISSUE, REPO, "Never ship from the worker.")
            await self.phase("blocked")
        task = self.runner.task(ISSUE)
        self.assertIn("changed during review", task["detail"])
        self.assertEqual(task["check_exit"], 0)
        self.assertEqual(task["session"], "ses_fixture")
        report = (self.runner.artifacts(ISSUE) / "review.md").read_text()
        self.assertIn("VERDICT: UNAVAILABLE", report)
        self.assertIn("changed during review", report)
        self.assertTrue(self.runner.worktree(ISSUE).is_dir())
        attempt, = self.attempts()
        self.assertEqual(attempt["review"]["verdict"], "UNAVAILABLE")
        self.assertNotIn("NO ISSUES", attempt["review"]["text"])
        self.assertIn("changed during review", attempt["review"]["text"])

    async def test_large_review_report_is_bounded(self):
        with patch.dict(os.environ, {"FIXTURE_REVIEW": "large"}):
            await self.worker_done()
        report = (self.runner.artifacts(ISSUE) / "review.md").read_text()
        self.assertIn("VERDICT: ISSUES", report)
        self.assertIn("[Review truncated.]", report)
        self.assertLessEqual(len(report.splitlines()), REVIEW_LINES + 2)

    def test_review_validation_requires_a_leading_verdict_and_location(self):
        self.assertEqual(validate_review("\n\nVERDICT: NO ISSUES"), "VERDICT: NO ISSUES")
        self.assertEqual(validate_review("No issues found."), "No issues found.")
        self.assertIsNone(validate_review("Looks fine to me."))
        self.assertIsNone(validate_review(""))
        self.assertIsNone(validate_review(" \n\t"))
        self.assertIsNone(validate_review("No issues found.\nExtra commentary."))
        self.assertIsNone(validate_review("Preamble.\nVERDICT: NO ISSUES"))
        self.assertIsNone(validate_review("VERDICT: NO ISSUES\n- a.py:12 - Finding."))
        self.assertIsNone(validate_review("VERDICT: NO ISSUES\nExtra commentary."))
        self.assertIsNone(validate_review("VERDICT: NO ISSUES\nVERDICT: NO ISSUES"))
        self.assertIsNone(validate_review("VERDICT: NO ISSUES\nVERDICT: ISSUES\n- a.py:12 - Finding."))
        self.assertIsNone(validate_review("VERDICT: ISSUES\n- a.py:12 - Finding.\n"
                                          "VERDICT: NO ISSUES"))
        self.assertIsNone(validate_review("VERDICT: ISSUES\n- no file location"))
        self.assertIsNotNone(validate_review("VERDICT: ISSUES\n- a.py:12 - Finding."))
        self.assertIsNotNone(validate_review("VERDICT: ISSUES\nsymphony/a.py:4 - Finding."))
        report = bounded_review("VERDICT: ISSUES\n- a.py:1 - Finding.\n" + "detail\n" * 300)
        self.assertLessEqual(len(report.splitlines()), REVIEW_LINES + 2)

    async def test_existing_work_without_a_review_report_shows_unavailable(self):
        await self.worker_done()
        (self.runner.artifacts(ISSUE) / "review.md").unlink()
        self.assertIn("Review unavailable: no reviewer report",
                      self.runner.read_notes(ISSUE))

    async def test_push_without_matching_workflows_finishes_immediately(self):
        (self.repo / ".github/workflows/test.yml").write_text("on:\n  push:\n    paths: ['docs/**']\n")
        self.git(self.repo, "commit", "-am", "Filter tests to docs")
        self.git(self.repo, "push", "origin", "main")
        await self.worker_done()
        self.runner.read_notes(ISSUE)
        with patch("symphony.runner.ci_runs") as ci:
            self.runner.ship(ISSUE)
            await self.phase("done")
            ci.assert_not_awaited()
        self.assertEqual(self.linear.state["name"], "Done")
        self.assertEqual(self.git(self.remote, "show", "main:verified.txt"), "Tested")

    async def test_rejected_direct_push_keeps_worktree_and_reports_git(self):
        await self.worker_done()
        hook = self.remote / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\necho 'fixture branch protection' >&2\nexit 1\n")
        hook.chmod(0o755)
        self.runner.read_notes(ISSUE)
        with patch("symphony.runner.ci_runs", new_callable=AsyncMock) as ci:
            self.runner.ship(ISSUE)
            await self.phase("blocked")
            ci.assert_not_awaited()
        self.assertIn("fixture branch protection", self.runner.task(ISSUE)["detail"])
        self.assertTrue(self.runner.worktree(ISSUE).is_dir())
        self.assertEqual(self.linear.state["name"], "In Progress")

    async def test_successful_dispatch_acknowledges_old_events_without_a_turn(self):
        await self.fail_approval()
        await self.runner.recover_approval(ISSUE)
        self.assertEqual(self.runner.rows("SELECT detail, delivered, phase FROM events"), [{
            "detail": "Plan approved. Queued in Todo.", "delivered": 0, "phase": "approved",
        }])
        pending = []
        with patch.object(self.runner, "job", side_effect=pending.append):
            await self.runner.dispatch(ISSUE, REPO, "")
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.task(ISSUE)["phase"], "working")
        self.assertEqual(self.runner.rows("SELECT delivered FROM events"), [{"delivered": 1}])

        app = self.app()
        app.coordinator.account = "chatgpt"
        with patch.object(app.coordinator, "respond", new_callable=AsyncMock) as respond:
            await self.pump_until(app, lambda: not self.runner.rows(
                "SELECT 1 FROM events WHERE delivered=0"))
            respond.assert_not_awaited()

    async def test_successful_resume_acknowledges_blocked_event_without_a_turn(self):
        await self.approve()
        self.runner.block(ISSUE, "Retry when ready.")
        self.assertEqual(self.runner.rows("SELECT detail, delivered, phase FROM events"), [{
            "detail": "Blocked. Retry when ready.", "delivered": 0, "phase": "blocked",
        }])
        self.linear.configure(ISSUE, state={"name": "In Progress", "type": "started"})
        pending = []
        with patch.object(self.runner, "job", side_effect=pending.append):
            await self.runner.dispatch(ISSUE, REPO, "", resume=True)
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.task(ISSUE)["phase"], "working")
        self.assertEqual(self.runner.rows("SELECT delivered FROM events"), [{"delivered": 1}])

        app = self.app()
        app.coordinator.account = "chatgpt"
        with patch.object(app.coordinator, "respond", new_callable=AsyncMock) as respond:
            await self.pump_until(app, lambda: not self.runner.rows(
                "SELECT 1 FROM events WHERE delivered=0"))
            respond.assert_not_awaited()

    async def test_continue_dispatch_acknowledges_stale_events_without_a_turn(self):
        await self.fail_approval()
        await self.runner.recover_approval(ISSUE)
        self.runner.save_setting("paused", "quota")
        app = self.app()
        app.coordinator.account = "chatgpt"
        pending = []
        with patch.object(self.runner, "job", side_effect=pending.append), \
             patch.object(app.coordinator, "respond", new_callable=AsyncMock) as respond:
            await app.handle("", "/continue", "/continue")
            await self.pump_until(
                app, lambda: self.runner.task(ISSUE)["phase"] == "working"
                and not self.runner.rows("SELECT 1 FROM events WHERE delivered=0"))
            respond.assert_not_awaited()
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.rows("SELECT delivered FROM events"), [{"delivered": 1}])

    async def test_shipping_acknowledges_review_event_but_reading_audit_does_not(self):
        await self.worker_done()
        review = {
            "detail": "Worker finished. Verification exited 0. Audit notes.md and review.md.",
            "delivered": 0, "phase": "review",
        }
        self.assertEqual(self.runner.rows("SELECT detail, delivered, phase FROM events"), [review])
        self.runner.read_notes(ISSUE)
        self.assertEqual(self.runner.rows("SELECT delivered FROM events"), [{"delivered": 0}])

        app = self.app()
        pending = []
        with patch.object(self.runner, "job", side_effect=pending.append):
            await app.tool("ship_worker", {"issue_id": ISSUE})
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.task(ISSUE)["phase"], "publishing")
        self.assertEqual(self.runner.rows("SELECT delivered FROM events"), [{"delivered": 1}])

        app.coordinator.account = "chatgpt"
        with patch.object(app.coordinator, "respond", new_callable=AsyncMock) as respond:
            await self.pump_until(app, lambda: not self.runner.rows(
                "SELECT 1 FROM events WHERE delivered=0"))
            respond.assert_not_awaited()

    async def test_parking_replaces_stale_review_event_with_blocked_notification(self):
        await self.worker_done()
        self.runner.block(ISSUE, "Parked for inspection.")
        events = self.runner.rows("SELECT detail, delivered, phase FROM events ORDER BY id")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["phase"], "review")
        self.assertEqual(events[0]["delivered"], 1)
        self.assertEqual(events[1], {
            "detail": "Blocked. Parked for inspection.", "delivered": 0, "phase": "blocked",
        })

        app = self.app()
        app.coordinator.account = "chatgpt"
        received = []

        async def respond(text, cwd):
            received.append(text)
            return "completed"

        app.coordinator.respond = AsyncMock(side_effect=respond)
        await self.pump_until(app, lambda: not self.runner.rows(
            "SELECT 1 FROM events WHERE delivered=0"))
        self.assertEqual(len(received), 1)
        self.assertIn(f"Worker event for {ISSUE}: Blocked. Parked for inspection.", received[0])
        self.assertNotIn("Worker finished", received[0])

    async def test_completion_keeps_done_and_cleanup_error_for_one_turn(self):
        await self.worker_done()
        self.runner.change(ISSUE, phase="finishing")
        with patch.object(self.runner, "cleanup",
                          side_effect=RuntimeError("Fixture cleanup failure.")):
            await self.runner.finish(ISSUE)
        events = self.runner.rows("SELECT detail, delivered, phase FROM events ORDER BY id")
        self.assertEqual(events[0]["phase"], "review")
        self.assertEqual(events[0]["delivered"], 1)
        self.assertEqual(events[1]["phase"], "done")
        self.assertEqual(events[1]["delivered"], 0)
        self.assertEqual(
            events[1]["detail"], "Done. Worktree cleanup needs attention: Fixture cleanup failure.")

        app = self.app()
        app.coordinator.account = "chatgpt"
        received = []

        async def respond(text, cwd):
            received.append(text)
            return "completed"

        app.coordinator.respond = AsyncMock(side_effect=respond)
        await self.pump_until(app, lambda: not self.runner.rows(
            "SELECT 1 FROM events WHERE delivered=0"))
        self.assertEqual(len(received), 1)
        self.assertIn(f"Worker event for {ISSUE}: {events[1]['detail']}", received[0])
        self.assertNotIn("Worker finished", received[0])

    async def test_cleanup_discards_stale_events_but_keeps_done_notification(self):
        await self.worker_done()
        self.runner.change(ISSUE, phase="finishing")
        await self.runner.finish(ISSUE)
        with self.runner.db:
            self.runner.db.execute(
                "INSERT INTO events(issue,detail,delivered,phase) VALUES (?,?,0,'ci')",
                (ISSUE, "Stale CI notice."),
            )
        await self.runner.cleanup(ISSUE)
        events = {event["detail"]: event for event in self.runner.rows(
            "SELECT detail, delivered, phase FROM events")}
        self.assertEqual(events["Stale CI notice."], {
            "detail": "Stale CI notice.", "delivered": 1, "phase": "ci",
        })
        self.assertEqual(events["Done."], {"detail": "Done.", "delivered": 0, "phase": "done"})

        app = self.app()
        app.coordinator.account = "chatgpt"
        with patch.object(app.coordinator, "respond",
                          new_callable=AsyncMock, return_value="completed") as respond:
            await self.pump_until(app, lambda: not self.runner.rows(
                "SELECT 1 FROM events WHERE delivered=0"))
        self.assertEqual(respond.await_count, 1)
        self.assertIn(f"Worker event for {ISSUE}: Done.", respond.await_args.args[0])
        self.assertNotIn("Stale CI notice", respond.await_args.args[0])

    async def test_mixed_issue_batch_filters_only_stale_events(self):
        other = "KIAN-102"
        await self.approve()
        self.runner.change(ISSUE, phase="done", supersede=True)
        with self.runner.db:
            self.runner.db.execute(
                "INSERT INTO events(issue,detail,delivered,phase) VALUES (?,?,0,'review')",
                (ISSUE, "Stale review notice."),
            )
        await self.runner.approve({**self.proposed_issue(), "identifier": other}, REPO)
        self.runner.block(other, "Second issue needs a decision.")

        app = self.app()
        app.coordinator.account = "chatgpt"
        received = []

        async def respond(text, cwd):
            received.append(text)
            return "completed"

        app.coordinator.respond = AsyncMock(side_effect=respond)
        await self.pump_until(app, lambda: not self.runner.rows(
            "SELECT 1 FROM events WHERE delivered=0"))
        self.assertEqual(len(received), 1)
        self.assertIn(f"Worker event for {other}: Blocked. Second issue needs a decision.",
                      received[0])
        self.assertNotIn("Stale review notice", received[0])
        self.assertEqual(self.runner.rows(
            "SELECT detail, delivered FROM events WHERE issue=? ORDER BY id", (ISSUE,)), [
            {"detail": "Stale review notice.", "delivered": 1},
        ])

    async def test_failed_transitions_preserve_pending_notifications(self):
        await self.fail_approval()
        await self.runner.recover_approval(ISSUE)
        self.assertEqual(self.runner.rows("SELECT delivered FROM events"), [{"delivered": 0}])
        with patch.object(self.linear, "issue", side_effect=RuntimeError("Linear is down.")):
            with self.assertRaisesRegex(RuntimeError, "Linear is down"):
                await self.runner.dispatch(ISSUE, REPO, "")
        self.assertEqual(self.runner.task(ISSUE)["phase"], "approved")
        self.assertEqual(self.runner.rows("SELECT delivered FROM events"), [{"delivered": 0}])

        self.runner.block(ISSUE, "Needs retry.")
        with self.assertRaisesRegex(ValueError, "not In Progress"):
            await self.runner.dispatch(ISSUE, REPO, "", resume=True)
        self.assertEqual(self.runner.task(ISSUE)["phase"], "blocked")
        self.assertEqual(self.runner.rows("SELECT detail, delivered FROM events ORDER BY id")[-1],
                         {"detail": "Blocked. Needs retry.", "delivered": 0})

    async def test_current_review_event_survives_metadata_updates(self):
        await self.approve()
        self.runner.change(ISSUE, phase="review", event="Worker finished.")
        for fields in ({"pid": 123}, {"session": "ses_fixture"}, {"audit": "digest"}):
            self.runner.change(ISSUE, **fields)
            self.assertEqual(self.runner.rows("SELECT delivered FROM events"), [{"delivered": 0}])

        app = self.app()
        app.coordinator.account = "chatgpt"
        with patch.object(app.coordinator, "respond",
                          new_callable=AsyncMock, return_value="completed") as respond:
            await self.pump_until(app, lambda: not self.runner.rows(
                "SELECT 1 FROM events WHERE delivered=0"))
        self.assertEqual(respond.await_count, 1)
        self.assertIn(f"Worker event for {ISSUE}: Worker finished.", respond.await_args.args[0])

    async def test_stale_event_filtering_survives_restart(self):
        await self.approve()
        self.runner.change(ISSUE, phase="review", event="Worker finished.")
        restarted = Runner(self.root, self.linear)
        self.addCleanup(restarted.db.close)
        self.assertEqual(restarted.rows("SELECT detail, delivered, phase FROM events"), [{
            "detail": "Worker finished.", "delivered": 0, "phase": "review",
        }])
        with restarted.db:
            restarted.db.execute("UPDATE tasks SET phase='done' WHERE issue=?", (ISSUE,))

        app = self.app(restarted)
        app.coordinator.account = "chatgpt"
        with patch.object(app.coordinator, "respond", new_callable=AsyncMock) as respond:
            await self.pump_until(app, lambda: not restarted.rows(
                "SELECT 1 FROM events WHERE delivered=0"))
            respond.assert_not_awaited()

    async def test_database_upgrade_resolves_pending_events_once_for_current_state(self):
        legacy = self.root / "legacy"
        legacy.mkdir()
        with sqlite3.connect(legacy / "symphony.sqlite3") as db:
            db.executescript("""
                CREATE TABLE tasks (
                    issue TEXT PRIMARY KEY, project TEXT NOT NULL, repo TEXT NOT NULL,
                    title TEXT NOT NULL, plan TEXT NOT NULL, phase TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, session TEXT, pid INTEGER,
                    base TEXT, tree TEXT, audit TEXT, check_exit INTEGER, detail TEXT,
                    revision TEXT, workflows TEXT, updated REAL NOT NULL
                );
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY, issue TEXT NOT NULL,
                    detail TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE chat (
                    id INTEGER PRIMARY KEY, role TEXT NOT NULL, text TEXT NOT NULL
                );
                INSERT INTO tasks(issue,project,repo,title,plan,phase,attempts,detail,updated)
                VALUES ('KIAN-201','project','owner/stale','Stale','Plan','working',1,NULL,0),
                       ('KIAN-202','project','owner/current','Current','Plan','blocked',0,
                        'Needs a decision.',0);
                INSERT INTO events(issue,detail) VALUES
                    ('KIAN-201','Worker finished. Verification exited 0. Audit notes.md and review.md.'),
                    ('KIAN-202','Blocked. Older notice.'),
                    ('KIAN-202','Blocked. Needs a decision.');
                INSERT INTO chat(role,text) VALUES
                    ('worker','KIAN-202 · Blocked. Needs a decision.');
            """)
        runner = Runner(legacy, self.linear)
        self.addCleanup(runner.db.close)
        self.assertEqual(runner.rows("""SELECT issue, detail, delivered, phase FROM events
                                        WHERE delivered=0"""), [{
            "issue": "KIAN-202", "detail": "Blocked. Needs a decision.",
            "delivered": 0, "phase": "blocked",
        }])
        self.assertEqual(runner.rows("SELECT role, text FROM chat"), [{
            "role": "worker", "text": "KIAN-202 · Blocked. Needs a decision.",
        }])

        reopened = Runner(legacy, self.linear)
        self.addCleanup(reopened.db.close)
        self.assertEqual(reopened.rows("SELECT COUNT(*) AS count FROM events"), [{"count": 4}])
        self.assertEqual(len(reopened.rows("SELECT 1 FROM events WHERE delivered=0")), 1)

        app = self.app(reopened)
        app.coordinator.account = "chatgpt"
        with patch.object(app, "respond",
                          new_callable=AsyncMock, return_value="completed") as respond:
            await self.pump_until(app, lambda: not reopened.rows(
                "SELECT 1 FROM events WHERE delivered=0"))
        self.assertEqual(respond.await_count, 1)
        prompt = respond.await_args.args[1]
        self.assertIn("Worker event for KIAN-202: Blocked. Needs a decision.", prompt)
        self.assertNotIn("KIAN-201", prompt)

        restarted = Runner(legacy, self.linear)
        self.addCleanup(restarted.db.close)
        self.assertEqual(restarted.rows("SELECT COUNT(*) AS count FROM events"), [{"count": 4}])
        self.assertEqual(restarted.rows(
            "SELECT 1 FROM events WHERE delivered=0 AND phase=''"), [])

    async def test_pump_batches_queued_worker_events_in_one_turn(self):
        app = self.app()
        app.coordinator.account = "chatgpt"
        await self.approve()
        self.runner.change(ISSUE, phase="ci",
                           event="Pushed to main. Waiting for GitHub Actions.", supersede=True)
        self.runner.change(ISSUE, event="GitHub Actions are still running.")

        received = []
        done = asyncio.Event()

        async def fake_respond(text, cwd):
            received.append((text, cwd))
            if len(received) == 1:
                # An event arriving mid-turn must stay undelivered for the next batch.
                self.runner.change(ISSUE, event="Follow-up notice.")
            else:
                done.set()
            return "completed"

        app.coordinator.respond = AsyncMock(side_effect=fake_respond)
        pump = asyncio.create_task(app.pump())
        self.addAsyncCleanup(self._cancel_task, pump)
        app.runner.wake.set()
        async with asyncio.timeout(5):
            await done.wait()

        self.assertEqual(len(received), 2)
        first_text, first_cwd = received[0]
        self.assertEqual(first_cwd, self.repo)
        self.assertIn("Worker events (2):", first_text)
        self.assertIn(f"- {ISSUE}: Pushed to main. Waiting for GitHub Actions.", first_text)
        self.assertIn(f"- {ISSUE}: GitHub Actions are still running.", first_text)
        self.assertEqual(first_text.count(f"Approved plan for {ISSUE}:"), 1)

        second_text, _ = received[1]
        self.assertIn(f"Worker event for {ISSUE}: Follow-up notice.", second_text)
        self.assertEqual(self.runner.rows("SELECT 1 FROM events WHERE delivered=0"), [])

    @staticmethod
    async def _cancel_task(task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class ChatHttpTests(unittest.TestCase):
    def test_chat_http_policy_allows_blob_previews_without_weakening_sources(self):
        handler = object.__new__(Handler)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.respond(b"ok", "text/plain")
        headers = dict(call.args for call in handler.send_header.call_args_list)
        policy = headers["Content-Security-Policy"]
        self.assertIn("img-src 'self' https: data: blob:", policy)
        for rule in ("default-src 'none'", "script-src 'self'", "style-src 'self'",
                     "connect-src 'self'", "base-uri 'none'", "frame-ancestors 'none'"):
            self.assertIn(rule, policy)
        self.assertNotIn("img-src *", policy)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_chat_http_returns_the_accepted_message(self):
        headers = Message()
        payload = {"message": {"id": 7, "role": "user", "text": "Saved text", "images": []}}
        raw = json.dumps({"text": "Saved text", "project": ""}).encode()
        headers["Content-Length"] = str(len(raw))
        headers["X-Symphony"] = "1"
        headers["Content-Type"] = "application/json"
        headers["Sec-Fetch-Site"] = "same-origin"
        handler = object.__new__(Handler)
        handler.path = "/api/chat"
        handler.headers = headers
        handler.rfile = BytesIO(raw)
        handler.server = SimpleNamespace(app=SimpleNamespace(submit=Mock()))
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        with patch.object(Handler, "run_async", return_value=payload):
            handler.do_POST()
        handler.send_response.assert_called_once_with(202)
        self.assertEqual(json.loads(handler.wfile.getvalue()), payload)

    def test_chat_http_rejects_bodies_above_the_bound_before_reading(self):
        headers = Message()
        headers["Content-Length"] = str(BODY_LIMIT + 1)
        headers["X-Symphony"] = "1"
        headers["Content-Type"] = "application/json"
        headers["Sec-Fetch-Site"] = "same-origin"
        handler = object.__new__(Handler)
        handler.path = "/api/chat"
        handler.headers = headers
        handler.server = SimpleNamespace(app=SimpleNamespace(submit=Mock()))
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_POST()
        handler.server.app.submit.assert_not_called()
        handler.send_response.assert_called_once_with(400)


class ServeStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_services_start_without_a_webhook_secret(self):
        servers = []

        class FakeServer:
            def __init__(self, address, handler):
                self.address = address
                self.handler = handler
                self.app = None
                servers.append(self)

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def serve_forever(self):
                pass

            def shutdown(self):
                pass

        class ImmediateEvent:
            def set(self):
                pass

            async def wait(self):
                return None

        class FakeApp:
            webhook_secret = ""

            def __init__(self):
                self.runner = SimpleNamespace(
                    jobs=set(),
                    db=SimpleNamespace(close=Mock()),
                    job=lambda coroutine: coroutine.close(),
                )
                self.coordinator = SimpleNamespace(close=AsyncMock())

            async def pump(self):
                return None

            async def start(self):
                return None

        app = FakeApp()
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = {key: value for key, value in os.environ.items()
               if key != "LINEAR_WEBHOOK_SECRET"}
        env.update({"SYMPHONY_DATA": temp.name, "HOME": str(Path(temp.name) / "home"),
                    "LINEAR_API_KEY": "fixture"})
        with patch.dict(os.environ, env, clear=True), \
             patch("symphony.__main__.ThreadingHTTPServer", FakeServer), \
             patch("symphony.__main__.Symphony", return_value=app), \
             patch("symphony.__main__.asyncio.Event", return_value=ImmediateEvent()):
            await serve()
        self.assertEqual([server.address for server in servers], [
            ("127.0.0.1", 8080), ("0.0.0.0", 8081), ("0.0.0.0", 8082),
        ])
        self.assertIs(servers[2].handler, WebhookHandler)
        self.assertEqual(app.webhook_secret, "")


class WebhookTests(unittest.TestCase):
    secret = "fixture-webhook-secret"

    def payload(self, **changes):
        payload = {
            "action": "update",
            "type": "Issue",
            "data": {"id": "2174add1-f7c8-44e3-bbf3-2d60b5ea8bc9", "identifier": ISSUE},
            "webhookTimestamp": int(time.time() * 1000),
        }
        payload.update(changes)
        return payload

    def signature(self, raw):
        return hmac.new(self.secret.encode(), raw, hashlib.sha256).hexdigest()

    def send(self, raw, *, signature="missing", length=None, reader=None,
             path="/webhooks/linear", method="POST", app=None):
        app = app or SimpleNamespace(webhook_secret=self.secret, request_issue_refresh=Mock())
        handler = object.__new__(WebhookHandler)
        handler.path = path
        handler.headers = Message()
        handler.headers["Content-Length"] = str(len(raw) if length is None else length)
        if signature != "missing":
            handler.headers["Linear-Signature"] = signature
        handler.rfile = reader or BytesIO(raw)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        loop = SimpleNamespace(
            call_soon_threadsafe=Mock(side_effect=lambda callback, *args: callback(*args)))
        handler.server = SimpleNamespace(app=app, loop=loop)
        if method == "POST":
            handler.do_POST()
        else:
            handler.do_GET()
        return handler, handler.send_response.call_args.args[0], app

    def test_valid_issue_and_project_webhooks_signal_refresh(self):
        for kind, projects in (("Issue", False), ("Project", True)):
            with self.subTest(kind=kind):
                raw = json.dumps(self.payload(type=kind)).encode()
                handler, status, app = self.send(raw, signature=self.signature(raw))
                self.assertEqual(status, 200)
                app.request_issue_refresh.assert_called_once_with(projects)
                self.assertEqual(handler.wfile.getvalue(), b'{"status": "ok"}')

    def test_rejected_webhooks_never_signal_refresh(self):
        now = int(time.time() * 1000)
        valid = json.dumps(self.payload()).encode()
        cases = [
            ("missing signature", valid, "missing", {}, 401),
            ("invalid signature", valid, "0" * 64, {}, 401),
            ("stale timestamp", json.dumps(self.payload(webhookTimestamp=now - 61_000)).encode(),
             None, {}, 400),
            ("future timestamp", json.dumps(self.payload(webhookTimestamp=now + 61_000)).encode(),
             None, {}, 400),
            ("invalid timestamp", json.dumps(self.payload(webhookTimestamp="now")).encode(),
             None, {}, 400),
            ("boolean timestamp", json.dumps(self.payload(webhookTimestamp=True)).encode(),
             None, {}, 400),
            ("huge timestamp", json.dumps(self.payload(webhookTimestamp=10 ** 400)).encode(),
             None, {}, 400),
            ("non-hex signature", valid, "not-hex", {}, 401),
            ("malformed json", b"{", None, {}, 400),
            ("unsupported type", json.dumps(self.payload(type="Comment")).encode(), None, {}, 400),
            ("list type", json.dumps(self.payload(type=[])).encode(), None, {}, 400),
            ("numeric type", json.dumps(self.payload(type=7)).encode(), None, {}, 400),
            ("unsupported action", json.dumps(self.payload(action="archive")).encode(), None, {}, 400),
            ("object action", json.dumps(self.payload(action={})).encode(), None, {}, 400),
            ("null action", json.dumps(self.payload(action=None)).encode(), None, {}, 400),
            ("missing data id", json.dumps(self.payload(data={})).encode(), None, {}, 400),
            ("unsupported data", json.dumps(self.payload(data=[])).encode(), None, {}, 400),
            ("oversized body", b"{}", "missing", {"length": WEBHOOK_BODY_LIMIT + 1}, 413),
        ]
        for name, raw, signature, options, expected in cases:
            with self.subTest(name=name):
                if signature is None:
                    signature = self.signature(raw)
                _, status, app = self.send(raw, signature=signature, **options)
                self.assertEqual(status, expected)
                app.request_issue_refresh.assert_not_called()

        class TimedOut:
            def read(self, _):
                raise TimeoutError

        _, status, app = self.send(valid, signature=self.signature(valid), reader=TimedOut())
        self.assertEqual(status, 408)
        app.request_issue_refresh.assert_not_called()

        _, status, app = self.send(valid, signature=self.signature(valid),
                                   reader=BytesIO(valid[:-1]))
        self.assertEqual(status, 400)
        app.request_issue_refresh.assert_not_called()

    def test_wrong_route_method_and_missing_secret_are_rejected(self):
        raw = json.dumps(self.payload()).encode()
        for method, path, status in (
            ("POST", "/other", 404),
            ("GET", "/webhooks/linear", 405),
        ):
            with self.subTest(method=method, path=path):
                _, observed, app = self.send(
                    raw, signature=self.signature(raw), path=path, method=method)
                self.assertEqual(observed, status)
                app.request_issue_refresh.assert_not_called()

        app = SimpleNamespace(webhook_secret="", request_issue_refresh=Mock())
        _, status, app = self.send(raw, app=app)
        self.assertEqual(status, 503)
        app.request_issue_refresh.assert_not_called()


class PaneTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_backlog_and_status_panes_partition_and_order_issues(self):
        rows = [
            {"identifier": "KIAN-10", "state": {"name": "Backlog"}, "priority": 4},
            {"identifier": "KIAN-2", "state": {"name": "Backlog"}, "priority": 1},
            {"identifier": "KIAN-3", "state": {"name": "Todo"}, "priority": 3},
            {"identifier": "KIAN-4", "state": {"name": "Todo"}, "priority": 2},
            {"identifier": "KIAN-5", "state": {"name": "In Progress"}, "priority": 2},
            {"identifier": "KIAN-6", "state": {"name": "Done"}, "priority": 0,
             "completedAt": "2026-11-20T12:00:00Z"},
            {"identifier": "KIAN-7", "state": {"name": "Done"}, "priority": 1,
             "completedAt": "2026-11-20T13:00:00Z"},
            {"identifier": "KIAN-8", "state": {"name": "Canceled"}, "priority": 1},
        ]
        script = """const fs = require("fs");
const vm = require("vm");
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const rows = JSON.parse(fs.readFileSync(0, "utf8"));
const projects = [{id: "alpha"}, {id: "beta"}];
const partition = vm.runInContext("partitionIssues", context);
const select = vm.runInContext("selectableProject", context);
const delay = vm.runInContext("pollDelay", context);
process.stdout.write(JSON.stringify({
  panes: partition(rows), empty: select(projects, ""), invalid: select(projects, "missing"),
  valid: select(projects, "beta"), none: select([], ""),
  delays: [
    delay({messages: [], busy: false}, false),
    delay({messages: [], busy: false}, true),
    delay({messages: [], busy: true}, false),
    delay({messages: [], busy: false, canInterrupt: true}, false),
    delay({messages: [], busy: false, stream: "live"}, false),
    delay({messages: [], busy: true}, true),
    delay({messages: [], busy: false, canInterrupt: true}, true),
    delay({messages: [], busy: false, stream: "live"}, true),
    delay({messages: new Array(200), busy: false}, false),
    delay({messages: new Array(200), busy: true}, true),
  ],
}));
"""
        panes = Path(__file__).resolve().parents[1] / "symphony/web/panes.js"
        run = subprocess.run(["node", "-e", script, str(panes)], input=json.dumps(rows),
                             text=True, capture_output=True, check=True)
        result = json.loads(run.stdout)
        self.assertEqual([issue["identifier"] for issue in result["panes"]["backlog"]], ["KIAN-2", "KIAN-10"])
        self.assertEqual([group["name"] for group in result["panes"]["groups"]],
                         ["Todo", "In Progress", "Done"])
        self.assertEqual([[issue["identifier"] for issue in group["issues"]]
                          for group in result["panes"]["groups"]],
                         [["KIAN-4", "KIAN-3"], ["KIAN-5"], ["KIAN-7", "KIAN-6"]])
        self.assertEqual((result["empty"], result["invalid"], result["valid"], result["none"]),
                         ("alpha", "alpha", "beta", ""))
        self.assertEqual(result["delays"], [1000, 2000, 200, 200, 200, 2000, 2000, 2000, 0, 0])

        root = Path(__file__).resolve().parents[1] / "symphony/web"
        html = (root / "index.html").read_text()
        self.assertLess(html.index('id="backlog"'), html.index('class="chat"'))
        self.assertLess(html.index('class="chat"'), html.index('id="status-issues"'))
        app = (root / "app.js").read_text()
        self.assertIn("pollDelay(state, document.hidden)", app)
        self.assertIn('"Awaiting approval"', app)
        self.assertIn("will not run", app)


class PlanTests(unittest.TestCase):
    def test_replaces_only_proposed_plan(self):
        original = "## Context\n\nKeep these lines.\n\n## Proposed Plan\n\nOld\n\n## Acceptance\n\nKeep this too."
        updated = with_plan(original, "New plan\n\n### Details\n\nRun the checks.")
        self.assertEqual(proposed_plan(updated), "New plan\n\n### Details\n\nRun the checks.")
        self.assertTrue(updated.startswith("## Context\n\nKeep these lines."))
        self.assertTrue(updated.endswith("## Acceptance\n\nKeep this too."))


class WorkflowFilterTests(unittest.TestCase):
    def test_path_patterns_use_github_globs(self):
        for value, patterns, expected in [
            ("README.md", ["**/README.md"], True),
            ("src/README.md", ["**/README.md"], True),
            ("src/nested/README.md", ["src/*.md"], False),
            ("docs/a/b.md", ["docs/**/*.md"], True),
            ("docs/file.md", ["docs/**/*.md"], True),
            ("src/button.js", ["src/*.jsx?"], True),
            ("src/button.jsx", ["src/*.jsx?"], True),
            ("src/button.jst", ["src/*.jsx?"], False),
            ("release/v12.3", ["release/v[0-9]+.[0-9]+"], True),
            ("package[old].json", [r"package\[old\].json"], True),
            ("docs/draft.md", ["**/*.md", "!docs/**"], False),
        ]:
            with self.subTest(value=value, patterns=patterns):
                self.assertEqual(matches_filter(value, patterns), expected)
