"""Exercise the MVP's approval and publication boundaries without provider calls."""

import asyncio
import base64
from datetime import datetime, timezone
from email.message import Message
import hashlib
import hmac
import http.server
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call, patch

from symphony.__main__ import (
    BODY_LIMIT, CONFIG_BODY_LIMIT, ISSUE_REFRESH_SECONDS, REFRESH_COALESCE_SECONDS, WEBHOOK_BODY_LIMIT,
    Handler, Symphony, WebhookHandler, issue_summary, serve,
)
from symphony.codex import CREATE_ISSUE_SCHEMA, Codex, TOOLS, TOOLS_HASH
from symphony.execution import execution_payload
from symphony.runner import (
    AGENTS_BYTES, BusyError, NOTES_LINES, REVIEW_LINES, Runner, bounded_review,
    install_agent_instructions, shared_agents, validate_review,
)
from symphony.services import (
    Linear, estimate_scale, matches_filter, proposed_plan, with_plan,
)


ISSUE = "KIAN-101"
REPO = "owner/project"
PROJECT = {"id": "project", "name": "Fixture", "archivedAt": None,
           "teams": [{"id": "team", "name": "Kian", "key": "KIAN", "archivedAt": None}]}
TEAM = {
    "id": "team", "name": "Kian", "key": "KIAN", "archivedAt": None,
    "issueEstimationType": "fibonacci", "issueEstimationExtended": False,
    "issueEstimationAllowZero": False,
    "states": [
        {"id": "triage", "name": "Triage", "type": "triage", "archivedAt": None},
        {"id": "unsure", "name": "Unsure", "type": "backlog", "archivedAt": None},
        {"id": "backlog", "name": "Backlog", "type": "backlog", "archivedAt": None},
        {"id": "todo", "name": "Todo", "type": "unstarted", "archivedAt": None},
    ],
}
CREATED_ISSUE = {
    "id": "new-issue", "identifier": "KIAN-200",
    "url": "https://linear.app/fixture/issue/KIAN-200",
    "state": {"id": "backlog", "name": "Backlog", "type": "backlog"},
    "team": {"id": "team"}, "project": {"id": "project"},
    "parent": None, "priority": 2, "estimate": 3,
}
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
        auth = self.runner.opencode_auth_path()
        auth.parent.mkdir(parents=True, exist_ok=True)
        auth.write_text(json.dumps({"opencode-go": {"type": "api", "key": "fixture-opencode-key"}}))
        auth.chmod(0o600)
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
import sqlite3
import subprocess
import sys
import time
if len(sys.argv) >= 3 and sys.argv[1] == "auth":
    sub = sys.argv[2]
    data_dir = Path(os.environ["XDG_DATA_HOME"]) / "opencode"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "opencode.db"
    fresh = not db_path.exists()
    con = sqlite3.connect(db_path)
    con.execute('''CREATE TABLE IF NOT EXISTS credential (
        id TEXT PRIMARY KEY, integration_id TEXT, label TEXT NOT NULL,
        value TEXT NOT NULL, connector_id TEXT, method_id TEXT,
        active INTEGER, time_created INTEGER NOT NULL, time_updated INTEGER NOT NULL
    )''')
    legacy = data_dir / "auth.json"
    if fresh and legacy.is_file():
        for integ, entry in json.loads(legacy.read_text()).items():
            if isinstance(entry, dict) and entry.get("key"):
                con.execute(
                    "INSERT OR IGNORE INTO credential VALUES (?,?,?,?,NULL,NULL,1,0,0)",
                    (f"cred_{integ}", integ, "API key", json.dumps({"type": "key", "key": entry["key"]})),
                )
        con.commit()
    pos = [a for a in sys.argv[3:] if not a.startswith("-")]
    if sub == "export":
        target = pos[0] if pos else None
        q = "SELECT id, integration_id, label, active, value FROM credential"
        rows = con.execute(q + (" WHERE integration_id=?" if target else ""), (target,) if target else ()).fetchall()
        print(json.dumps([{"id": r[0], "integrationID": r[1], "label": r[2], "active": bool(r[3]), "value": json.loads(r[4])} for r in rows]))
    elif sub == "import":
        for item in json.loads(sys.stdin.read()):
            con.execute(
                "INSERT OR IGNORE INTO credential VALUES (?,?,?,?,NULL,NULL,1,0,0)",
                (item["id"], item["integrationID"], item.get("label", "API key"), json.dumps(item["value"])),
            )
        con.commit()
    elif sub == "logout":
        con.execute("DELETE FROM credential WHERE integration_id=? AND id=?", (pos[0], pos[1]))
        con.commit()
    con.close()
    os.chmod(db_path, 0o600)
    sys.exit(0)
workspace = Path(os.environ["FIXTURE_ROOT"]) / "runs/KIAN-101"
config = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT", "{}"))
agents_path = Path(os.environ["XDG_CONFIG_HOME"]) / "opencode" / "AGENTS.md"
agents = agents_path.read_text() if agents_path.is_file() else None
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
        "agents": agents,
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
        "agents": agents,
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

    def add_repo(self, repo, rules=None):
        path = self.runner.repo_dir(repo)
        remote = self.root / (repo.replace("/", "-") + ".git")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.git(self.root, "init", "--bare", "--initial-branch=main", str(remote))
        self.git(path.parent, "clone", str(remote), str(path))
        self.git(path, "config", "user.name", "Fixture")
        self.git(path, "config", "user.email", "fixture@example.test")
        (path / "README.md").write_text("Second fixture repository\n")
        if rules is not None:
            (path / "AGENTS.md").write_text(rules)
        self.git(path, "add", ".")
        self.git(path, "commit", "-m", "Initial fixture")
        self.git(path, "push", "origin", "main")
        return path

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

    @staticmethod
    def create_issue_args(**overrides):
        args = {"repo": REPO, "title": "Cross-repository change",
                "description": "Implement the fixture change.", "priority": 2, "estimate": 3}
        args.update(overrides)
        return args

    def create_issue_fixture(self, *, repos=None, project=None, team=None, labels=None,
                             create=None, relation=None, live=None):
        app = self.app()
        mocks = SimpleNamespace(
            create=create or AsyncMock(return_value=CREATED_ISSUE),
            relation=relation or AsyncMock(),
            project=AsyncMock(return_value=project or PROJECT),
            team=AsyncMock(return_value=team or TEAM),
            labels=AsyncMock(return_value=(
                [{"id": "bug", "name": "Bug"}, {"id": "ui", "name": "UI"}]
                if labels is None else labels)),
            live=live or AsyncMock(return_value=({"id": "related-issue", "identifier": "KIAN-150"}, REPO)),
            refresh=Mock(side_effect=self.runner.request_issue_refresh),
        )
        patches = [
            patch.object(app, "refresh_projects", new_callable=AsyncMock,
                         return_value=repos or {REPO: [{"id": "project", "name": "Fixture"}]}),
            patch.object(app.linear, "project_context", new=mocks.project),
            patch.object(app.linear, "team_context", new=mocks.team),
            patch.object(app.linear, "team_labels", new=mocks.labels),
            patch.object(app.linear, "create_issue", new=mocks.create),
            patch.object(app.linear, "create_blocks_relation", new=mocks.relation),
            patch.object(app, "live_issue", new=mocks.live),
            patch.object(self.runner, "request_issue_refresh", new=mocks.refresh),
        ]
        for patcher in patches:
            patcher.start()
        self.addCleanup(lambda: [patcher.stop() for patcher in patches])
        return app, mocks

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
        app.coordinator.streams[""] = reply

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

        app.coordinator.set_thread("", "main")
        self.runner.save_thread("", "main", TOOLS_HASH)
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
        self.assertEqual(saved["text"], "Use this mockup.")
        self.assertEqual(saved["repo"], "owner/second")
        self.assertEqual([item["type"] for item in saved["images"]], ["image/webp"])
        self.assertEqual(json.loads(rows[-1]["attachments"]), saved["images"])

        first = await app.submit({"project": "beta", "text": "Repeat"})
        second = await app.submit({"project": "beta", "text": "Repeat"})
        self.assertNotEqual(first["message"]["id"], second["message"]["id"])
        state = await app.state(0)
        self.assertEqual([message["id"] for message in state["messages"]],
                         [saved["id"], first["message"]["id"], second["message"]["id"]])
        self.assertEqual([message["text"] for message in state["messages"]][-2:],
                         ["Repeat", "Repeat"])
        self.assertEqual(state["messages"][0], saved)

        image_only = await app.submit({"project": "beta", "images": [image_payload(PNG)]})
        self.assertEqual(image_only["message"]["text"], "")
        self.assertEqual([item["type"] for item in image_only["message"]["images"]],
                         ["image/png"])
        self.assertEqual(self.runner.rows("SELECT text FROM chat ORDER BY id DESC LIMIT 1"),
                         [{"text": ""}])

    async def test_chat_live_markdown_renders_only_when_the_stream_changes(self):
        app = self.app()
        with patch("symphony.__main__.markdown", return_value="<p>cached</p>") as render:
            app.coordinator.streams[""] = "Draft one"
            first = await app.state(0)
            second = await app.state(0)
            self.assertEqual(render.call_count, 1)
            self.assertEqual(first["streamHtml"], "<p>cached</p>")
            self.assertEqual(second["streamHtml"], "<p>cached</p>")

            app.coordinator.streams[""] = "Draft one and two"
            await app.state(0)
            self.assertEqual(render.call_count, 2)

            app.coordinator.streams[""] = ""
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
            "id": row["id"], "role": "user", "text": "Screenshot", "images": images, "repo": "",
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
        app.coordinator.set_thread(REPO, "main")
        app.coordinator.turns[REPO] = "turn"
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
        repo_arg, text, cwd, attachments = respond.await_args.args
        self.assertEqual(repo_arg, REPO)
        self.assertEqual(cwd, self.repo)
        self.assertTrue(text.startswith(note + "\n\n--- Current Symphony context ---\n"))
        self.assertIn("Global AGENTS.md defaults:", text)
        self.assertEqual(attachments, first[3])

    async def test_chat_images_cross_a_project_switch_in_the_queue(self):
        app = self.app()
        app.repos[REPO] = [{"id": "project", "name": "Fixture"}]
        app.repos["owner/second"] = [{"id": "beta", "name": "Beta"}]
        self.runner.save_setting("view", "project")
        await app.submit({"project": "beta", "text": "Use this mockup.",
                          "images": [image_payload(WEBP, "image/webp")]})

        repo, text, message, images = app.messages.popleft()
        self.assertEqual((repo, text, message), ("owner/second", "Use this mockup.", "Use this mockup."))
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
        codex.set_thread("", "thread")
        codex.turns[""] = "turn"
        with patch.object(codex, "ensure_thread"), \
             patch.object(codex, "turn_request", new_callable=AsyncMock,
                          return_value="completed") as turn:
            await codex.respond("", "Look at this", self.root, images)
            method, params = turn.await_args.args
            self.assertEqual(method, "turn/start")
            self.assertEqual(params["input"], [
                {"type": "text", "text": "Look at this", "text_elements": []},
                {"type": "localImage", "path": path},
            ])
            await codex.respond("", "Text only", self.root)
            _, params = turn.await_args.args
            self.assertEqual(params["input"], [
                {"type": "text", "text": "Text only", "text_elements": []},
            ])

        with patch.object(codex, "request", new_callable=AsyncMock,
                          return_value={}) as request:
            await codex.steer("", "Also this", images)
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
            await codex.ensure_thread("", self.root)
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
        self.runner.save_thread("", "previous-thread", "outdated-tools")
        self.runner.chat("user", "Earlier chat")
        codex = Codex(self.runner, None)
        codex.account = "chatgpt"
        with patch.object(codex, "connect"), \
             patch.object(codex, "request", return_value={"thread": {"id": "current-thread"}}) as request:
            await codex.ensure_thread("", self.root)
            method, params = request.await_args.args
            self.assertEqual(method, "thread/start")
            self.assertEqual(params["dynamicTools"], TOOLS)
            self.assertEqual(self.runner.thread("")["thread_id"], "current-thread")
            self.assertEqual(self.runner.thread("")["tools_hash"], TOOLS_HASH)

            codex.threads.clear()
            codex.thread_repos.clear()
            await codex.ensure_thread("", self.root)
            method, params = request.await_args.args
            self.assertEqual(method, "thread/resume")
            self.assertEqual(params["threadId"], "current-thread")

        self.assertEqual(self.runner.rows("SELECT role, text FROM chat ORDER BY id"), [
            {"role": "user", "text": "Earlier chat"},
            {"role": "system", "text": "Started a fresh Codex thread to load updated tools."},
        ])

    async def test_shared_defaults_precede_conflicting_repository_rules(self):
        second = "owner/second"
        self.add_repo(second, "# Second rules\n\n- REPO-B-RULE\n")
        (self.repo / "AGENTS.md").write_text("# First rules\n\n- REPO-A-RULE\n")
        self.git(self.repo, "add", "AGENTS.md")
        self.git(self.repo, "commit", "-m", "Add first repository rules")
        self.git(self.repo, "push", "origin", "main")
        shared = self.root / "shared-AGENTS.md"
        shared.write_text("# Shared defaults\n\n- GLOBAL-RULE\n")
        app = self.app()
        with patch("symphony.runner.SHARED_AGENTS", shared), \
             patch.object(app.coordinator, "respond", new_callable=AsyncMock) as respond:
            await app.respond(REPO, "First repository turn")
            first = respond.await_args.args[1]
            await app.respond(second, f"Worker event for {ISSUE}: Follow-up notice.")
            second_text = respond.await_args.args[1]
        for text, own, other in ((first, "REPO-A-RULE", "REPO-B-RULE"),
                                 (second_text, "REPO-B-RULE", "REPO-A-RULE")):
            self.assertLess(text.index("GLOBAL-RULE"), text.index(own))
            self.assertIn("Global AGENTS.md defaults:", text)
            self.assertIn("Active repository AGENTS.md:", text)
            self.assertNotIn(other, text)

    async def test_empty_shared_defaults_leave_repository_rules_only(self):
        (self.repo / "AGENTS.md").write_text("# First rules\n\n- REPO-ONLY-RULE\n")
        self.git(self.repo, "add", "AGENTS.md")
        self.git(self.repo, "commit", "-m", "Add repository rules")
        self.git(self.repo, "push", "origin", "main")
        shared = self.root / "empty-AGENTS.md"
        shared.write_text("")
        app = self.app()
        with patch("symphony.runner.SHARED_AGENTS", shared):
            _, text = await app.context(REPO)
        self.assertIn("Active repository AGENTS.md:", text)
        self.assertIn("REPO-ONLY-RULE", text)
        self.assertNotIn("Global AGENTS.md defaults:", text)

    async def test_changed_or_removed_shared_defaults_do_not_linger(self):
        shared = self.root / "changing-AGENTS.md"
        shared.write_text("# Shared\n\n- OLD-GLOBAL-RULE\n")
        app = self.app()
        with patch("symphony.runner.SHARED_AGENTS", shared), \
             patch.object(app.coordinator, "respond", new_callable=AsyncMock) as respond:
            await app.respond(REPO, "First coordinator turn")
            first = respond.await_args.args[1]
            shared.write_text("# Shared\n\n- NEW-GLOBAL-RULE\n")
            await app.respond(REPO, "Second coordinator turn")
            second = respond.await_args.args[1]
            shared.unlink()
            with self.assertRaisesRegex(RuntimeError, "Global AGENTS.md is missing"):
                await app.respond(REPO, "Third coordinator turn")
        self.assertIn("OLD-GLOBAL-RULE", first)
        self.assertIn("NEW-GLOBAL-RULE", second)
        self.assertNotIn("OLD-GLOBAL-RULE", second)

    def test_agent_homes_link_the_canonical_shared_file(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            shared = root / "shared-AGENTS.md"
            shared.write_text("# Shared\n\n- GLOBAL-LINK-RULE\n")
            with patch("symphony.runner.SHARED_AGENTS", shared):
                runner = Runner(root / "data", self.linear)
            try:
                paths = (root / "data/codex/AGENTS.md",
                         root / "data/worker-config/opencode/AGENTS.md")
                for path in paths:
                    self.assertTrue(path.is_symlink())
                    self.assertEqual(path.resolve(), shared.resolve())
                    self.assertIn("GLOBAL-LINK-RULE", path.read_text())
                shared.write_text("# Shared\n\n- GLOBAL-LINK-UPDATED\n")
                for path in paths:
                    self.assertIn("GLOBAL-LINK-UPDATED", path.read_text())
            finally:
                runner.db.close()

    def test_missing_and_unreadable_shared_defaults_are_reported(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            missing = root / "missing-AGENTS.md"
            with patch("symphony.runner.SHARED_AGENTS", missing), \
                 self.assertRaisesRegex(RuntimeError, "Global AGENTS.md is missing"):
                Runner(root / "data", self.linear)
            unreadable = root / "unreadable-AGENTS.md"
            unreadable.write_text("# Shared\n")
            unreadable.chmod(0)
            try:
                with patch("symphony.runner.SHARED_AGENTS", unreadable), \
                     self.assertRaisesRegex(RuntimeError, "could not be read"):
                    Runner(root / "other", self.linear)
            finally:
                unreadable.chmod(0o600)
            with patch("symphony.runner.SHARED_AGENTS", unreadable), \
                 patch("symphony.runner.AGENTS_BYTES", 1), \
                 self.assertRaisesRegex(RuntimeError, "too large"):
                shared_agents()
            invalid = root / "invalid-AGENTS.md"
            invalid.write_bytes(b"\xff\xfe")
            with patch("symphony.runner.SHARED_AGENTS", invalid), \
                 self.assertRaisesRegex(RuntimeError, "could not be read"):
                shared_agents()

    def test_nonregular_shared_defaults_fail_without_blocking(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("FIFOs are unavailable on this platform.")
        root = Path(__file__).resolve().parents[1]
        script = """
import sys
from pathlib import Path
import symphony.runner as runner
runner.SHARED_AGENTS = Path(sys.argv[1])
try:
    runner.shared_agents()
except RuntimeError as error:
    print(error)
else:
    raise SystemExit("shared_agents unexpectedly succeeded")
"""
        with TemporaryDirectory() as temp:
            temp = Path(temp)
            fifo = temp / "fifo-AGENTS.md"
            os.mkfifo(fifo)
            directory = temp / "directory-AGENTS.md"
            directory.mkdir()
            target = temp / "target-AGENTS.md"
            target.write_text("# Shared\n")
            link = temp / "symlink-AGENTS.md"
            link.symlink_to(target)
            for path in (fifo, directory, link):
                with self.subTest(path=path.name):
                    result = subprocess.run(
                        [sys.executable, "-c", script, str(path)],
                        cwd=root, text=True, capture_output=True, timeout=2,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        result.stdout.strip(),
                        f"Global AGENTS.md is unavailable: {path.name} must be a regular file.",
                    )

    async def test_fresh_resumed_workers_and_reviewer_receive_shared_defaults(self):
        shared = self.root / "shared-AGENTS.md"
        shared.write_text("# Shared\n\n- GLOBAL-WORKER-RULE\n")
        with patch("symphony.runner.SHARED_AGENTS", shared):
            install_agent_instructions(self.root)
            await self.worker_done()
            await self.runner.dispatch(
                ISSUE, REPO, "Never ship from the worker. Follow up.", resume=True)
            await self.phase("review")
        records = [json.loads(line) for line in
                   (self.runner.artifacts(ISSUE) / "worker-envs.jsonl").read_text().splitlines()]
        self.assertEqual(len(records), 2)
        self.assertNotIn("--session", records[0]["args"])
        self.assertIn("--session", records[1]["args"])
        for record in records:
            self.assertIn("GLOBAL-WORKER-RULE", record["agents"])
        self.assertIn("GLOBAL-WORKER-RULE", self.review_environment()["agents"])

    async def test_create_issue_schema_preserves_string_only_tool_validation(self):
        tools = {tool["name"]: tool for tool in TOOLS}
        self.assertEqual(tools["create_issue"]["inputSchema"], CREATE_ISSUE_SCHEMA)
        self.assertEqual(CREATE_ISSUE_SCHEMA["required"],
                         ["repo", "title", "description", "priority", "estimate"])
        self.assertEqual(CREATE_ISSUE_SCHEMA["properties"]["priority"],
                         {"type": "integer", "minimum": 1, "maximum": 4})
        self.assertEqual(CREATE_ISSUE_SCHEMA["properties"]["estimate"], {"type": "integer"})
        for name in ("repo", "title", "description", "project_id", "parent_id"):
            self.assertEqual(CREATE_ISSUE_SCHEMA["properties"][name], {"type": "string"})
        for name in ("blocked_by", "labels"):
            self.assertEqual(CREATE_ISSUE_SCHEMA["properties"][name],
                             {"type": "array", "items": {"type": "string"}})
        without_create = hashlib.sha256(json.dumps(
            [tool for tool in TOOLS if tool["name"] != "create_issue"], sort_keys=True).encode()).hexdigest()
        self.assertNotEqual(without_create, TOOLS_HASH)
        for name in ("set_plan", "approve_issue", "spawn_worker"):
            schema = tools[name]["inputSchema"]
            self.assertTrue(all(value == {"type": "string"} for value in schema["properties"].values()))
            self.assertFalse(schema["additionalProperties"])

        app = self.app()
        for name, args in [
            ("set_plan", {"issue_id": ISSUE, "plan": 1}),
            ("list_issues", {"repo": REPO, "extra": "x"}),
            ("approve_issue", {"issue_id": "kian-101"}),
        ]:
            with self.subTest(name=name, args=args), \
                 self.assertRaisesRegex(ValueError, "Invalid tool arguments|uppercase"):
                await app.tool(name, args)
        with self.assertRaisesRegex(ValueError, "Unknown tool"):
            await app.tool("missing_tool", {})

    async def test_codex_status_reports_active_context_and_usage_windows(self):
        app = self.app()
        codex = app.coordinator
        codex.account = "chatgpt"
        self.runner.save_thread("", "active", TOOLS_HASH)
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
        codex.set_thread("", "main")
        self.runner.save_thread("", "main", TOOLS_HASH)
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
        self.assertEqual(codex.threads, {})
        self.assertEqual(self.runner.thread("")["thread_id"], "main")

    async def test_broken_codex_pipe_queues_chat_and_reports_interruption(self):
        app = self.app()
        app.repos[REPO] = [{"id": "project", "name": "Fixture"}]
        app.turn_repo = REPO
        app.coordinator.set_thread(REPO, "main")
        app.coordinator.turns[REPO] = "turn"
        self.runner.save_setting("view", "project")
        note = "Pick up where we left off."
        with patch.object(app.coordinator, "request", side_effect=BrokenPipeError("Fixture pipe closed")) as request:
            await app.submit({"project": "project", "text": note})
            with self.assertRaisesRegex(RuntimeError, "Codex disconnected"):
                await app.submit({"project": "project", "text": "/interrupt"})
        self.assertEqual([call.args[0] for call in request.await_args_list], ["turn/steer", "turn/interrupt"])
        self.assertEqual(list(app.messages), [(REPO, note, note, [])])
        self.assertTrue(self.runner.wake.is_set())
        self.assertEqual(self.runner.rows("SELECT role, text, repo FROM chat"),
                         [{"role": "user", "text": note, "repo": REPO}])

    async def test_other_threads_cannot_change_or_complete_the_coordinator_turn(self):
        app = self.app()
        codex = app.coordinator
        codex.set_thread("", "main")
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
                self.assertEqual(codex.turn_for(""), "parent")
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

    async def test_create_issue_creates_backlog_issues_in_each_enrolled_repository(self):
        for repo, project_id, created in [
            (REPO, "project", CREATED_ISSUE),
            ("owner/second", "beta", {**CREATED_ISSUE, "id": "second-issue",
                                      "identifier": "KIAN-201",
                                      "url": "https://linear.app/fixture/issue/KIAN-201",
                                      "project": {"id": "beta"}}),
        ]:
            with self.subTest(repo=repo):
                app, mocks = self.create_issue_fixture(
                    repos={repo: [{"id": project_id, "name": "Fixture"}]},
                    project={**PROJECT, "id": project_id},
                    create=AsyncMock(return_value=created),
                )
                result = await app.tool("create_issue", self.create_issue_args(repo=repo))
                self.assertEqual((result["id"], result["identifier"], result["url"]),
                                 (created["id"], created["identifier"], created["url"]))
                self.assertEqual(result["state"], "Backlog")
                self.assertEqual(mocks.create.await_args.args[:3],
                                 (project_id, "team", "backlog"))
                self.assertEqual(mocks.create.await_args.kwargs,
                                 {"parent_id": None, "label_ids": []})
                self.assertEqual(mocks.refresh.call_args_list, [call(projects=False)])
                self.assertEqual(self.runner.rows("SELECT * FROM tasks"), [])
                self.assertEqual(self.runner.rows("SELECT * FROM events"), [])
                self.assertEqual(self.runner.status(), [])
                self.assertEqual(await self.runner.dispatch_approved(), [])
                with self.assertRaisesRegex(ValueError, "No approved plan"):
                    self.runner.task(result["identifier"])

    async def test_create_issue_selects_an_explicit_project_among_several(self):
        repos = {REPO: [{"id": "alpha", "name": "Alpha"}, {"id": "beta", "name": "Beta"}]}
        app, mocks = self.create_issue_fixture(
            repos=repos,
            project={**PROJECT, "id": "beta", "name": "Beta"},
            create=AsyncMock(return_value={**CREATED_ISSUE, "project": {"id": "beta"}}),
        )
        result = await app.tool("create_issue", self.create_issue_args(project_id="beta"))
        self.assertEqual(mocks.create.await_args.args[:2], ("beta", "team"))
        self.assertEqual(result["project"], "Beta")
        self.assertEqual(result["identifier"], CREATED_ISSUE["identifier"])

    async def test_create_issue_requires_one_active_mapped_project_and_team(self):
        repos = {REPO: [{"id": "alpha", "name": "Alpha"}, {"id": "beta", "name": "Beta"}]}
        app, mocks = self.create_issue_fixture(repos=repos)
        for args, message in [
            (self.create_issue_args(), "maps to several"),
            (self.create_issue_args(project_id="other"), "not mapped"),
            (self.create_issue_args(repo="owner/unknown"), "no mapped"),
        ]:
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, message):
                await app.tool("create_issue", args)
        mocks.create.assert_not_awaited()

        cases = [
            ({**PROJECT, "id": "alpha", "archivedAt": "2026-01-01T00:00:00Z"}, TEAM, "archived"),
            ({**PROJECT, "id": "alpha", "teams": []}, TEAM, "exactly one active"),
            ({**PROJECT, "id": "alpha", "teams": [
                PROJECT["teams"][0], {"id": "other", "name": "Other", "archivedAt": None},
            ]}, TEAM, "exactly one active"),
            ({**PROJECT, "id": "alpha"}, {**TEAM, "archivedAt": "2026-01-01T00:00:00Z"},
             "archived"),
        ]
        for project, team, message in cases:
            with self.subTest(project=project["id"], message=message):
                mocks.project.return_value = project
                mocks.team.return_value = team
                with self.assertRaisesRegex(ValueError, message):
                    await app.tool("create_issue", self.create_issue_args(project_id="alpha"))
        mocks.create.assert_not_awaited()

    async def test_create_issue_rejects_invalid_metadata_without_writing(self):
        app, mocks = self.create_issue_fixture()
        for overrides, message in [
            ({"priority": True}, "priority"),
            ({"priority": "2"}, "priority"),
            ({"priority": 0}, "priority"),
            ({"priority": 5}, "priority"),
            ({"estimate": True}, "estimate"),
            ({"estimate": "3"}, "estimate"),
            ({"estimate": 4}, "estimate must be one of"),
            ({"estimate": 0}, "estimate must be one of"),
            ({"title": "   "}, "title"),
            ({"title": "x" * 256}, "title"),
            ({"description": "   "}, "description"),
            ({"description": "x" * 24001}, "description"),
            ({"parent_id": "kian-1"}, "uppercase"),
            ({"blocked_by": ["KIAN-1", "bad"]}, "uppercase"),
            ({"labels": [""]}, "Label names"),
        ]:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, message):
                await app.tool("create_issue", self.create_issue_args(**overrides))
        for args in [
            {"repo": REPO, "title": "Title", "description": "Description", "priority": 2},
            self.create_issue_args(extra="x"),
            self.create_issue_args(estimate=3.0),
        ]:
            with self.subTest(args=args), \
                 self.assertRaisesRegex(ValueError, "Invalid create_issue|estimate"):
                await app.tool("create_issue", args)
        mocks.create.assert_not_awaited()
        self.assertEqual(self.runner.rows("SELECT * FROM tasks"), [])

    async def test_create_issue_uses_the_configured_estimate_scale(self):
        team = {**TEAM, "issueEstimationExtended": True, "issueEstimationAllowZero": True}
        self.assertEqual(estimate_scale(team), [0, 1, 2, 3, 5, 8, 13, 21])
        app, mocks = self.create_issue_fixture(team=team)
        accepted = (0, 1, 2, 3, 5, 8, 13, 21)
        for estimate in accepted:
            await app.tool("create_issue", self.create_issue_args(estimate=estimate))
            self.assertEqual(mocks.create.await_args.args[6], estimate)
        for estimate in (4, 6, 7, 16, 34, -1):
            with self.subTest(estimate=estimate), self.assertRaisesRegex(ValueError, "estimate must be one of"):
                await app.tool("create_issue", self.create_issue_args(estimate=estimate))
        self.assertEqual(mocks.create.await_count, len(accepted))

        disabled = {**TEAM, "issueEstimationType": "notUsed"}
        with self.assertRaisesRegex(ValueError, "supported estimate scale"):
            estimate_scale(disabled)
        app, mocks = self.create_issue_fixture(team=disabled)
        with self.assertRaisesRegex(ValueError, "supported estimate scale"):
            await app.tool("create_issue", self.create_issue_args())
        mocks.create.assert_not_awaited()

    async def test_create_issue_resolves_existing_labels_and_rejects_unknown_or_ambiguous(self):
        labels = [{"id": "bug", "name": "Bug"}, {"id": "bug-copy", "name": "Bug"},
                  {"id": "ui", "name": "UI"}]
        app, mocks = self.create_issue_fixture(labels=labels)
        for requested, message in [(["Bug"], "ambiguous"), (["Missing"], "Unknown")]:
            with self.subTest(requested=requested), self.assertRaisesRegex(ValueError, message):
                await app.tool("create_issue", self.create_issue_args(labels=requested))
        mocks.create.assert_not_awaited()

        mocks.labels.return_value = [{"id": "bug", "name": "Bug"}, {"id": "ui", "name": "UI"}]
        await app.tool("create_issue", self.create_issue_args(labels=["Bug", "UI", "Bug"]))
        self.assertEqual(mocks.create.await_args.kwargs["label_ids"], ["bug", "ui"])

    async def test_linear_team_labels_paginates_workspace_and_team_labels(self):
        linear = Linear("fixture")
        pages = [
            {"issueLabels": {
                "nodes": [{"id": "bug", "name": "Bug", "archivedAt": None}],
                "pageInfo": {"endCursor": "cursor-1", "hasNextPage": True},
            }},
            {"issueLabels": {
                "nodes": [{"id": "ui", "name": "UI", "archivedAt": None},
                          {"id": "old", "name": "Old", "archivedAt": "2026-01-01T00:00:00Z"}],
                "pageInfo": {"endCursor": "cursor-2", "hasNextPage": False},
            }},
        ]
        with patch.object(linear, "query", new_callable=AsyncMock,
                          side_effect=pages) as query:
            labels = await linear.team_labels("team")
        self.assertEqual(labels, [{"id": "bug", "name": "Bug", "archivedAt": None},
                                  {"id": "ui", "name": "UI", "archivedAt": None}])
        self.assertEqual(query.await_args_list[0].kwargs, {"team": "team", "after": None})
        self.assertEqual(query.await_args_list[1].kwargs, {"team": "team", "after": "cursor-1"})

    async def test_create_issue_links_parent_and_blockers_and_reports_partial_links(self):
        related = {
            "KIAN-150": {"id": "parent-issue", "identifier": "KIAN-150"},
            "KIAN-151": {"id": "blocker-one", "identifier": "KIAN-151"},
            "KIAN-152": {"id": "blocker-two", "identifier": "KIAN-152"},
        }

        async def live(identifier):
            return related[identifier], "owner/second"

        relation = AsyncMock(side_effect=[
            None, RuntimeError("Linear rejected the blocking link."),
        ])
        app, mocks = self.create_issue_fixture(live=AsyncMock(side_effect=live),
                                               relation=relation)
        result = await app.tool("create_issue", self.create_issue_args(
            parent_id="KIAN-150",
            blocked_by=["KIAN-151", "KIAN-151", "KIAN-152"],
        ))
        self.assertEqual((result["id"], result["identifier"], result["url"]),
                         (CREATED_ISSUE["id"], CREATED_ISSUE["identifier"], CREATED_ISSUE["url"]))
        self.assertEqual(mocks.create.await_args.kwargs["parent_id"], "parent-issue")
        self.assertEqual(relation.await_args_list, [
            call("blocker-one", "new-issue"), call("blocker-two", "new-issue"),
        ])
        self.assertEqual(result["blockedBy"], {
            "succeeded": ["KIAN-151"],
            "failed": [{"identifier": "KIAN-152",
                        "error": "Linear rejected the blocking link."}],
        })
        self.assertIn("not recreated", result["message"])
        self.assertEqual(mocks.create.await_count, 1)
        self.assertEqual(mocks.refresh.call_args_list, [call(projects=False)])

    async def test_create_issue_rejects_unmapped_related_issue_before_creation(self):
        async def live(identifier):
            raise ValueError(f"{identifier} is not in a mapped Linear project.")

        app, mocks = self.create_issue_fixture(live=AsyncMock(side_effect=live))
        for overrides in ({"parent_id": "KIAN-999"},
                          {"blocked_by": ["KIAN-999"]}):
            with self.subTest(overrides=overrides), \
                 self.assertRaisesRegex(ValueError, "not in a mapped"):
                await app.tool("create_issue", self.create_issue_args(**overrides))
        mocks.create.assert_not_awaited()
        mocks.refresh.assert_not_called()

    async def test_create_issue_does_not_recreate_after_an_uncertain_failure(self):
        create = AsyncMock(side_effect=RuntimeError(
            "The Linear issue creation outcome is uncertain after an error. "
            "Check existing issues in Linear and reconcile before creating another."))
        app, mocks = self.create_issue_fixture(create=create)
        with self.assertRaisesRegex(RuntimeError, "uncertain.*reconcile"):
            await app.tool("create_issue",
                           self.create_issue_args(blocked_by=["KIAN-151"]))
        create.assert_awaited_once()
        mocks.relation.assert_not_awaited()
        mocks.refresh.assert_not_called()

    async def test_create_issue_refreshes_confirmed_validation_failures(self):
        app = self.app()
        app.repos = {REPO: [{"id": "project", "name": "Fixture"}]}
        related = {"id": "blocker-id", "identifier": "KIAN-150", "project": {"id": "project"}}
        project = {**PROJECT, "teams": {
            "nodes": PROJECT["teams"], "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}
        team = {**TEAM, "states": {
            "nodes": TEAM["states"], "pageInfo": {"endCursor": None, "hasNextPage": False},
        }}
        for response, message, identity in [
            ({"issueCreate": {"success": True}}, "without returning the issue", ""),
            ({"issueCreate": {"success": True, "issue": {**CREATED_ISSUE, "url": ""}}},
             "incomplete identity", "KIAN-200"),
            ({"issueCreate": {"success": True, "issue": {
                **CREATED_ISSUE, "project": {"id": "other"}}}}, "wrong project", "KIAN-200"),
            ({"issueCreate": {"success": True, "issue": {
                **CREATED_ISSUE, "team": {"id": "other"}}}}, "wrong team", "KIAN-200"),
            ({"issueCreate": {"success": True, "issue": {
                **CREATED_ISSUE, "state": {"name": "Unsure", "type": "backlog"}}}},
             "outside Backlog", "KIAN-200"),
            ({"issueCreate": {"success": True, "issue": {
                **CREATED_ISSUE, "priority": 3}}}, "priority and estimate", "KIAN-200"),
        ]:
            with self.subTest(message=message):
                attempts = []
                relation_writes = []

                async def query(statement, **variables):
                    if "issueCreate" in statement:
                        attempts.append(variables)
                        return response
                    if "issueRelationCreate" in statement:
                        relation_writes.append(variables)
                        return {"issueRelationCreate": {"success": False}}
                    if "issue(id: $id)" in statement:
                        return {"issue": related}
                    if "project(id: $id)" in statement:
                        return {"project": project}
                    if "team(id: $id)" in statement:
                        return {"team": team}
                    raise AssertionError(statement)

                refresh = Mock(side_effect=self.runner.request_issue_refresh)
                with patch.object(app, "refresh_projects", new_callable=AsyncMock,
                                  return_value={REPO: [{"id": "project", "name": "Fixture"}]}), \
                     patch.object(app.linear, "query", side_effect=query), \
                     patch.object(self.runner, "request_issue_refresh", refresh):
                    with self.assertRaisesRegex(RuntimeError, message) as raised:
                        await app.tool("create_issue",
                                       self.create_issue_args(blocked_by=["KIAN-150"]))
                if identity:
                    self.assertIn(identity, str(raised.exception))
                self.assertEqual(len(attempts), 1)
                self.assertIs(attempts[0]["input"]["useDefaultTemplate"], False)
                self.assertEqual(relation_writes, [])
                refresh.assert_called_once_with(projects=False)
                self.assertEqual(self.runner.rows("SELECT * FROM tasks"), [])
                self.assertEqual(self.runner.rows("SELECT * FROM events"), [])
                self.assertEqual(self.runner.status(), [])
                self.assertEqual(await self.runner.dispatch_approved(), [])

    async def test_linear_create_issue_verifies_explicit_backlog_result(self):
        linear = Linear("fixture")
        issue = {**CREATED_ISSUE, "parent": {"id": "parent-issue", "identifier": "KIAN-150"}}

        async def create():
            return await linear.create_issue(
                "project", "team", "backlog", "Title", "Description", 2, 3,
                parent_id="parent-issue", label_ids=["bug"])

        with patch.object(linear, "query", new_callable=AsyncMock, return_value={
                "issueCreate": {"success": True, "issue": issue}}) as query:
            self.assertEqual(await create(), issue)
        self.assertEqual(query.await_args.kwargs["input"], {
            "teamId": "team", "projectId": "project", "stateId": "backlog",
            "title": "Title", "description": "Description", "priority": 2,
            "estimate": 3, "parentId": "parent-issue", "labelIds": ["bug"],
            "useDefaultTemplate": False,
        })
        self.assertIs(query.await_args.kwargs["input"]["useDefaultTemplate"], False)
        self.assertNotIn("templateId", query.await_args.kwargs["input"])

        for response, message in [
            ({"issueCreate": {"success": False}}, "rejected"),
            ({"issueCreate": {"success": True, "issue": {
                **issue, "project": {"id": "other"}}}}, "wrong project"),
            ({"issueCreate": {"success": True, "issue": {
                **issue, "state": {"name": "Unsure", "type": "backlog"}}}}, "outside Backlog"),
            ({"issueCreate": {"success": True, "issue": {
                **issue, "priority": 3}}}, "priority and estimate"),
            ({"issueCreate": {"success": True, "issue": {
                **issue, "parent": {"id": "other"}}}}, "requested parent"),
            ({"issueCreate": {"success": True, "issue": {**issue, "id": ""}}},
             "incomplete identity"),
            ({}, "no issue creation result"),
        ]:
            with self.subTest(response=response), \
                 patch.object(linear, "query", new_callable=AsyncMock,
                              return_value=response), \
                 self.assertRaisesRegex(RuntimeError, message):
                await create()

        with patch.object(linear, "query", new_callable=AsyncMock,
                          side_effect=RuntimeError("connection reset")):
            with self.assertRaisesRegex(RuntimeError, "uncertain.*reconcile"):
                await create()

    async def test_linear_blocks_relation_uses_existing_issue_as_source(self):
        linear = Linear("fixture")
        relation = {"id": "relation", "type": "blocks", "issue": {"id": "blocker"},
                    "relatedIssue": {"id": "new-issue"}}
        with patch.object(linear, "query", new_callable=AsyncMock, return_value={
                "issueRelationCreate": {"success": True, "issueRelation": relation}}) as query:
            self.assertEqual(await linear.create_blocks_relation("blocker", "new-issue"),
                             relation)
        self.assertEqual(query.await_args.kwargs["input"], {
            "issueId": "blocker", "relatedIssueId": "new-issue", "type": "blocks",
        })
        for response, message in [
            ({"issueRelationCreate": {"success": False}}, "rejected"),
            ({"issueRelationCreate": {"success": True, "issueRelation": {
                **relation, "type": "related"}}}, "mismatched"),
            ({"issueRelationCreate": {"success": True, "issueRelation": {
                **relation, "relatedIssue": {"id": "other"}}}}, "mismatched"),
            ({}, "no issue relation result"),
        ]:
            with self.subTest(response=response), \
                 patch.object(linear, "query", new_callable=AsyncMock,
                              return_value=response), \
                 self.assertRaisesRegex(RuntimeError, message):
                await linear.create_blocks_relation("blocker", "new-issue")
        with patch.object(linear, "query", new_callable=AsyncMock,
                          side_effect=RuntimeError("connection reset")):
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                await linear.create_blocks_relation("blocker", "new-issue")

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

        async def respond(repo, text, cwd):
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

        async def respond(repo, text, cwd):
            received.append(text)
            return "completed"

        app.coordinator.respond = AsyncMock(side_effect=respond)
        await self.pump_until(app, lambda: not self.runner.rows(
            "SELECT 1 FROM events WHERE delivered=0"))
        self.assertEqual(len(received), 1)
        self.assertIn(f"Worker event for {ISSUE}: {events[1]['detail']}", received[0])
        self.assertNotIn("Worker finished", received[0])

    async def test_cleanup_discards_stale_events_without_waking_coordinator_on_clean_done(self):
        await self.worker_done()
        self.runner.change(ISSUE, phase="finishing")
        await self.runner.finish(ISSUE)
        with self.runner.db:
            self.runner.db.execute(
                "INSERT INTO events(issue,detail,delivered,phase) VALUES (?,?,0,'ci')",
                (ISSUE, "Stale CI notice."),
            )
        await self.runner.cleanup(ISSUE)
        self.assertEqual(self.runner.rows("SELECT detail, delivered, phase FROM events ORDER BY id"), [
            {"detail": "Worker finished. Verification exited 0. Audit notes.md and review.md.",
             "delivered": 1, "phase": "review"},
            {"detail": "Stale CI notice.", "delivered": 1, "phase": "ci"},
        ])
        self.assertEqual(self.runner.rows("SELECT role, text FROM chat ORDER BY id")[-1], {
            "role": "worker", "text": f"{ISSUE} · Done.",
        })

        app = self.app()
        app.coordinator.account = "chatgpt"
        with patch.object(app.coordinator, "respond",
                          new_callable=AsyncMock, return_value="completed") as respond:
            await self.pump_until(app, lambda: not self.runner.rows(
                "SELECT 1 FROM events WHERE delivered=0"))
        respond.assert_not_awaited()

    async def test_push_and_clean_done_skip_coordinator_wakeups_and_keep_dispatch_moving(self):
        second = "KIAN-102"
        await self.approve()
        self.runner.change(ISSUE, phase="review", event="Worker finished.")
        self.runner.change(ISSUE, workflows=json.dumps([".github/workflows/test.yml"]))
        with patch.object(self.runner, "job", side_effect=lambda coro: coro.close()):
            await self.runner.after_push(ISSUE)
        self.assertEqual(self.runner.rows("SELECT delivered FROM events"), [{"delivered": 1}])
        self.assertEqual(self.runner.rows("SELECT role, text FROM chat ORDER BY id")[-1], {
            "role": "worker", "text": f"{ISSUE} · Pushed to main. Waiting for GitHub Actions.",
        })

        # Unphased legacy CI / clean Done rows also resolve without synthesizing wakeups on upgrade/recovery.
        with self.runner.db:
            self.runner.db.execute(
                "INSERT INTO events(issue,detail,delivered,phase) VALUES (?,?,0,'')",
                (ISSUE, "Old unphased CI"),
            )
        self.runner.migrate_unphased_events()
        self.assertEqual(self.runner.rows("SELECT 1 FROM events WHERE delivered=0"), [])

        # Completing cleanly logs to chat, clears stale events, skips coordinator turn, and auto-dispatches next Todo.
        await self.runner.approve({**self.proposed_issue(), "identifier": second}, REPO)
        self.linear.configure(ISSUE, state={"name": "In Progress", "type": "started"})
        self.linear.configure(second, state={"name": "Todo", "type": "unstarted"})
        self.runner.change(ISSUE, phase="finishing")
        pending = []
        app = self.app()
        app.coordinator.account = "chatgpt"
        with patch.object(self.runner, "job", side_effect=pending.append), \
             patch.object(app.coordinator, "respond", new_callable=AsyncMock) as respond:
            await self.runner.finish(ISSUE)
            await self.pump_until(app, lambda: self.runner.task(second)["phase"] == "working")
            respond.assert_not_awaited()
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.rows("SELECT 1 FROM events WHERE delivered=0"), [])
        self.assertEqual(self.runner.rows("SELECT role, text FROM chat ORDER BY id")[-1], {
            "role": "worker", "text": f"{ISSUE} · Done.",
        })

        with self.runner.db:
            self.runner.db.execute(
                "INSERT INTO events(issue,detail,delivered,phase) VALUES (?,?,0,'')",
                (ISSUE, "Old unphased done"),
            )
        self.runner.migrate_unphased_events()
        self.assertEqual(self.runner.rows("SELECT 1 FROM events WHERE delivered=0"), [])

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

        async def respond(repo, text, cwd):
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
        self.assertIn(f"Worker event for {ISSUE}: Worker finished.", respond.await_args.args[1])

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
        self.runner.change(ISSUE, phase="review",
                           event="Worker finished. Verification exited 0. Audit notes.md and review.md.",
                           supersede=True)
        self.runner.change(ISSUE, event="Review completed cleanly.")

        received = []
        done = asyncio.Event()

        async def fake_respond(repo, text, cwd):
            received.append((repo, text, cwd))
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
        first_repo, first_text, first_cwd = received[0]
        self.assertEqual(first_repo, REPO)
        self.assertEqual(first_cwd, self.repo)
        self.assertIn("Worker events (2):", first_text)
        self.assertIn(f"- {ISSUE}: Worker finished. Verification exited 0. Audit notes.md and review.md.", first_text)
        self.assertIn(f"- {ISSUE}: Review completed cleanly.", first_text)
        self.assertNotIn(f"Approved plan for {ISSUE}:", first_text)
        self.assertNotIn("Current approved work:", first_text)

        second_repo, second_text, _ = received[1]
        self.assertEqual(second_repo, REPO)
        self.assertIn(f"Worker event for {ISSUE}: Follow-up notice.", second_text)
        self.assertEqual(self.runner.rows("SELECT 1 FROM events WHERE delivered=0"), [])

    async def test_per_repo_sessions_isolate_threads_chat_events_and_new_command(self):
        second = "owner/second"
        second_path = self.add_repo(second, "# Second rules\n\n- ONLY-SECOND-RULE\n")
        (self.repo / "AGENTS.md").write_text("# First rules\n\n- ONLY-FIRST-RULE\n")
        self.git(self.repo, "add", "AGENTS.md")
        self.git(self.repo, "commit", "-m", "Add first rules")
        self.git(self.repo, "push", "origin", "main")

        app = self.app()
        app.repos = {
            REPO: [{"id": "alpha", "name": "Alpha"}, {"id": "alpha-2", "name": "Alpha Two"}],
            second: [{"id": "beta", "name": "Beta"}],
        }
        # Legacy global row with repo='' must not leak into per-repo chat views.
        self.runner.chat("system", "Legacy global entry", repo="")

        await app.submit({"project": "alpha", "text": "Hello first"})
        await app.submit({"project": "beta", "text": "Hello second"})
        self.runner.chat("assistant", "Reply first", repo=REPO)
        self.runner.chat("assistant", "Reply second", repo=second)

        first_state = await app.state(0, project="alpha")
        shared_state = await app.state(0, project="alpha-2")
        second_state = await app.state(0, project="beta")
        self.assertEqual([m["text"] for m in first_state["messages"]], ["Hello first", "Reply first"])
        self.assertEqual([m["text"] for m in shared_state["messages"]], ["Hello first", "Reply first"])
        self.assertEqual([m["text"] for m in second_state["messages"]], ["Hello second", "Reply second"])
        self.assertEqual(first_state["repoActivity"][REPO], first_state["messages"][-1]["id"])
        self.assertEqual(first_state["repoActivity"][second], second_state["messages"][-1]["id"])

        # Per-repo Codex threads and /new command.
        codex = app.coordinator
        codex.account = "chatgpt"
        started_ids = ["thread-a1", "thread-b1", "thread-a2"]
        with patch.object(codex, "connect"), \
             patch.object(codex, "turn_request", new_callable=AsyncMock, return_value="completed"), \
             patch.object(codex, "request", side_effect=lambda m, p: {"thread": {"id": started_ids.pop(0)}}) as req:
            await codex.respond(REPO, "Turn A", self.repo)
            await codex.respond(second, "Turn B", second_path)
            codex.notification("thread/tokenUsage/updated", {
                "threadId": "thread-a1",
                "tokenUsage": {"last": {"totalTokens": 32000}, "modelContextWindow": 112000},
            })
            self.assertEqual((await app.state(0, project="alpha"))["codex"]["contextUsed"], 20)
            self.assertEqual((await app.state(0, project="beta"))["codex"]["contextUsed"], 0)
            await app.handle(REPO, "/new", "/new")
        self.assertEqual((await app.state(0, project="alpha"))["codex"]["contextUsed"], 0)
        self.assertEqual(self.runner.thread(REPO)["thread_id"], "thread-a2")
        self.assertEqual(self.runner.thread(second)["thread_id"], "thread-b1")
        self.assertEqual((await app.state(0, project="alpha"))["messages"][-1]["text"],
                         "Started a new coordinator session.")
        self.assertEqual(req.call_args.args[0], "thread/start")
        self.assertEqual(req.call_args.args[1]["cwd"], str(self.repo))

        # Pending events across two repos flush in separate per-repo batches with exact repo AGENTS.md.
        self.runner.db.execute(
            """INSERT INTO tasks(issue,project,repo,title,plan,phase,updated)
               VALUES ('KIAN-101','alpha',?,'A','Plan A','review',0),
                      ('KIAN-102','beta',?,'B','Plan B','blocked',1)""",
            (REPO, second),
        )
        self.runner.db.commit()
        self.runner.change("KIAN-101", event="Review A ready.")
        self.runner.change("KIAN-102", event="Blocked B.")
        turns = []

        async def record_respond(repo, text, cwd):
            turns.append((repo, text, cwd))
            return "completed"

        app.coordinator.respond = AsyncMock(side_effect=record_respond)
        app.messages.clear()
        await self.pump_until(app, lambda: not self.runner.rows("SELECT 1 FROM events WHERE delivered=0"))
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0][0], REPO)
        self.assertEqual(turns[0][2], self.repo)
        self.assertIn("ONLY-FIRST-RULE", turns[0][1])
        self.assertNotIn("KIAN-102", turns[0][1])
        self.assertNotIn("Current approved work:", turns[0][1])
        self.assertNotIn("Approved plan for", turns[0][1])
        self.assertEqual(turns[1][0], second)
        self.assertEqual(turns[1][2], second_path)
        self.assertIn("ONLY-SECOND-RULE", turns[1][1])
        self.assertNotIn("KIAN-101", turns[1][1])
        self.assertNotIn("Current approved work:", turns[1][1])
        self.assertNotIn("Approved plan for", turns[1][1])

    async def test_lean_turns_omit_unchanged_rules_and_include_plan_in_read_notes(self):
        (self.repo / "AGENTS.md").write_text("# Repo rules\n\n- INITIAL-REPO-RULE\n")
        self.git(self.repo, "add", "AGENTS.md")
        self.git(self.repo, "commit", "-m", "Add initial rules")
        self.git(self.repo, "push", "origin", "main")
        shared = self.root / "shared-lean-AGENTS.md"
        shared.write_text("# Global\n\n- LEAN-GLOBAL-RULE\n")
        await self.runner.approve({
            **self.proposed_issue(),
            "description": "## Proposed Plan\n\nLock step 1\nLock step 2",
        }, REPO)
        self.runner.change(ISSUE, phase="review", check_exit=0)
        artifacts = self.runner.artifacts(ISSUE)
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "notes.md").write_text("Implemented cleanly.\n")
        (artifacts / "review.md").write_text("VERDICT: NO ISSUES\n")

        audit = self.runner.read_notes(ISSUE)
        self.assertIn("--- Approved plan ---\nLock step 1\nLock step 2", audit)
        self.assertIn("Implemented cleanly.", audit)
        self.assertIn("VERDICT: NO ISSUES", audit)
        with self.runner.db:
            self.runner.db.execute("UPDATE tasks SET plan=? WHERE issue=?", ("Changed plan", ISSUE))
        with self.assertRaisesRegex(ValueError, "Read the latest notes.md and review.md"):
            self.runner.ship(ISSUE)

        app = self.app()
        turns = []
        async def capture(repo, text, cwd):
            turns.append(text)
            return "completed"

        with patch("symphony.runner.SHARED_AGENTS", shared), \
             patch.object(app.coordinator, "respond", new_callable=AsyncMock, side_effect=capture):
            await app.respond(REPO, "First turn", (ISSUE,))
            await app.respond(REPO, "Second turn", (ISSUE,))
            (self.repo / "AGENTS.md").write_text("# Repo rules\n\n- UPDATED-REPO-RULE\n")
            self.git(self.repo, "add", "AGENTS.md")
            self.git(self.repo, "commit", "-m", "Update rules")
            self.git(self.repo, "push", "origin", "main")
            await app.respond(REPO, "Third turn")
            await app.respond(REPO, "Fourth turn")

            # Simulate compaction with matching native rules: emits reminder only.
            app.coordinator.set_thread(REPO, "thread-lean")
            app.coordinator.native_rules[REPO] = app.coordinator.injected_rules[REPO]
            app.coordinator.notification("item/completed", {
                "threadId": "thread-lean",
                "item": {"id": "c1", "type": "contextCompaction"},
            })
            await app.respond(REPO, "Post-compaction turn")
            await app.respond(REPO, "After recovery turn")

        self.assertIn("INITIAL-REPO-RULE", turns[0])
        self.assertNotIn("Approved plan for", turns[0])
        self.assertNotIn("Current approved work:", turns[0])
        self.assertEqual(turns[1], "Second turn")
        self.assertIn("UPDATED-REPO-RULE", turns[2])
        self.assertEqual(turns[3], "Fourth turn")
        self.assertIn("Context was compacted. Call worker_status", turns[4])
        self.assertNotIn("UPDATED-REPO-RULE", turns[4])
        self.assertEqual(turns[5], "After recovery turn")

    async def test_signed_in_new_session_reports_zero_context_usage(self):
        app = self.app()
        self.assertIsNone((await app.state(0))["codex"]["contextUsed"])
        app.coordinator.account = "chatgpt"
        self.assertEqual((await app.state(0))["codex"]["contextUsed"], 0)
        await app.handle("", "/status", "/status")
        self.assertEqual(
            self.runner.rows("SELECT text FROM chat ORDER BY id DESC LIMIT 1")[0]["text"],
            "Context 0% used\nNo approved work in progress.",
        )
        with patch.object(app.coordinator, "connect"), \
             patch.object(app.coordinator, "request", return_value={"thread": {"id": "thread-fresh"}}):
            await app.coordinator.new_session(REPO, self.repo)
        self.assertEqual(self.runner.thread(REPO)["context_used"], 0)
        self.assertEqual(app.coordinator.status(REPO)["contextUsed"], 0)

    async def test_compact_reports_queued_and_running_status(self):
        app = self.app()
        app.repos[REPO] = [{"id": "project", "name": "Fixture"}]
        app.busy = True
        app.coordinator.turns[REPO] = "turn-1"

        await app.submit({"project": "project", "text": "/compact"})
        queued_state = await app.state(0, project="project")
        self.assertEqual(queued_state["compacting"], "queued")
        self.assertEqual(queued_state["messages"][-1]["role"], "system")
        self.assertEqual(queued_state["messages"][-1]["text"],
                         "Compaction queued after the current turn.")

        app.messages.clear()
        app.busy = False
        app.coordinator.turns.clear()
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocking_compact(repo, cwd):
            started.set()
            await release.wait()
            return "completed"

        with patch.object(app.coordinator, "compact", side_effect=blocking_compact):
            task = asyncio.create_task(app.handle(REPO, "/compact", "/compact"))
            await started.wait()
            running_state = await app.state(0, project="project")
            self.assertEqual(running_state["compacting"], "running")
            release.set()
            await task
        self.assertEqual((await app.state(0, project="project"))["compacting"], "")

        with patch.object(app.coordinator, "compact", side_effect=RuntimeError("Compact failed")):
            with self.assertRaisesRegex(RuntimeError, "Compact failed"):
                await app.handle(REPO, "/compact", "/compact")
        self.assertEqual((await app.state(0, project="project"))["compacting"], "")

    async def test_opencode_go_pvc_credentials_lifecycle_and_admission_guards(self):
        canary = "canary-opencode-secret-999"
        replacement = "canary-opencode-secret-next"
        auth = self.runner.opencode_auth_path()
        db_path = self.runner.opencode_db_path()
        auth.unlink()
        self.assertEqual(self.runner.opencode_go_status(), {"configured": False, "malformed": False})

        await self.approve()
        self.linear.state = {"name": "Todo", "type": "unstarted"}
        self.assertEqual(await self.runner.dispatch_approved(), [])
        with self.assertRaisesRegex(ValueError, "OpenCode Go API key is not configured"):
            await self.runner.dispatch(ISSUE, REPO, "")
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 0)
        self.assertEqual(self.runner.setting("paused"), "")

        # Seed a legacy pre-migration auth.json with an unrelated provider before setting OpenCode Go.
        auth.parent.mkdir(parents=True, exist_ok=True)
        auth.write_text(json.dumps({"anthropic": {"type": "api", "key": "keep-other-provider"}}))
        await self.runner.set_opencode_go_key(f"  {canary}  ")
        self.assertEqual(db_path.stat().st_mode & 0o777, 0o600)
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            creds = {r[0]: json.loads(r[1])["key"] for r in con.execute(
                "SELECT integration_id, value FROM credential"
            ).fetchall()}
        finally:
            con.close()
        self.assertEqual(creds, {"anthropic": "keep-other-provider", "opencode-go": canary})

        app = self.app()
        cfg = await app.config_state()
        self.assertEqual(cfg["opencodeGo"], {"configured": True, "malformed": False, "busy": False})
        self.assertNotIn(canary, json.dumps(cfg))
        self.assertNotIn(canary, json.dumps(await app.state(0)))
        self.assertEqual(self.runner.rows("SELECT * FROM chat WHERE instr(text, ?) > 0", (canary,)), [])
        self.assertEqual(self.runner.rows("SELECT * FROM settings WHERE instr(value, ?) > 0", (canary,)), [])

        # Existing pause must NOT be cleared automatically by saving credentials.
        self.runner.save_setting("paused", "Existing pause reason")
        await app.update_config({"action": "set_opencode_key", "apiKey": replacement})
        self.assertEqual(self.runner.setting("paused"), "Existing pause reason")

        # Update failure preserves the previous stored credential in opencode.db intact.
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            before_rows = con.execute("SELECT id, integration_id, value FROM credential ORDER BY integration_id").fetchall()
        finally:
            con.close()
        with patch.object(self.runner, "_run_opencode_auth", side_effect=RuntimeError("Could not update OpenCode Go credentials.")):
            with self.assertRaisesRegex(RuntimeError, "Could not update OpenCode Go credentials"):
                await self.runner.set_opencode_go_key("canary-never-written")
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            after_fail_rows = con.execute("SELECT id, integration_id, value FROM credential ORDER BY integration_id").fetchall()
        finally:
            con.close()
        self.assertEqual(after_fail_rows, before_rows)

        # Clearing removes only opencode-go, preserves anthropic, and survives Runner restart even with env canary.
        await app.update_config({"action": "clear_opencode_key"})
        self.assertEqual(db_path.stat().st_mode & 0o777, 0o600)
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            remaining = {r[0]: json.loads(r[1])["key"] for r in con.execute(
                "SELECT integration_id, value FROM credential"
            ).fetchall()}
        finally:
            con.close()
        self.assertEqual(remaining, {"anthropic": "keep-other-provider"})
        with patch.dict(os.environ, {"OPENCODE_GO_API_KEY": "env-ignored-canary"}):
            restarted = Runner(self.root, self.linear)
            self.addCleanup(restarted.db.close)
            self.assertFalse(restarted.has_opencode_go_key())
            self.assertEqual(await restarted.dispatch_approved(), [])
            self.assertEqual(restarted.task(ISSUE)["attempts"], 0)

        # Malformed opencode.db is reported cleanly and never overwritten destructively.
        saved_db = db_path.read_bytes()
        db_path.write_text("{broken sqlite")
        self.assertEqual(self.runner.opencode_go_status(), {"configured": False, "malformed": True})
        with self.assertRaisesRegex(RuntimeError, "invalid and cannot be updated safely"):
            await self.runner.set_opencode_go_key(canary)
        self.assertEqual(db_path.read_text(), "{broken sqlite")
        db_path.write_bytes(saved_db)

        # Racing key update during awaited worker admission or active work returns BusyError.
        await app.update_config({"action": "set_opencode_key", "apiKey": canary})
        self.runner.save_setting("paused", "")
        in_linear = asyncio.Event()
        release_linear = asyncio.Event()
        original_issue = self.linear.issue

        async def slow_issue(_issue):
            in_linear.set()
            await release_linear.wait()
            return await original_issue(ISSUE)

        with patch.object(self.linear, "issue", side_effect=slow_issue), \
             patch.object(self.runner, "job", side_effect=lambda cor: cor.close()):
            dispatch_task = asyncio.create_task(self.runner.dispatch(ISSUE, REPO, ""))
            await in_linear.wait()
            with self.assertRaises(BusyError):
                await app.update_config({"action": "set_opencode_key", "apiKey": replacement})
            with self.assertRaises(BusyError):
                await app.update_config({"action": "clear_opencode_key"})
            release_linear.set()
            await dispatch_task
        self.assertEqual(self.runner.opencode_go_busy(), True)
        with self.assertRaises(BusyError):
            await app.update_config({"action": "clear_opencode_key"})

    async def test_pinned_opencode_reads_pvc_auth_and_ignores_env(self):
        real_path = os.pathsep.join(
            entry for entry in os.environ["PATH"].split(os.pathsep) if Path(entry) != self.root / "bin"
        )
        opencode_bin = shutil.which("opencode", path=real_path)
        if not opencode_bin:
            self.skipTest("OpenCode CLI is required for PVC store verification.")

        seen_auth = []

        class ProbeHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                seen_auth.append(self.headers.get("Authorization"))
                body = b'{"error":{"message":"probe-stop"}}'
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ProbeHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)

        auth = self.runner.opencode_auth_path()
        auth.unlink()
        auth.parent.mkdir(parents=True, exist_ok=True)
        auth.write_text(json.dumps({"anthropic": {"type": "api", "key": "preserve-unrelated"}}))
        auth.chmod(0o600)

        canary_1 = "canary-real-opencode-1"
        canary_2 = "canary-real-opencode-2"
        env = self.runner.opencode_auth_env()
        env["HOME"] = str(self.root / "home")
        env["PATH"] = real_path
        env["OPENCODE_GO_API_KEY"] = "env-fallback-must-not-be-used"
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"provider": {"opencode-go": {
            "options": {"baseURL": f"http://127.0.0.1:{server.server_port}/v1"},
        }}})

        with patch.dict(os.environ, {"PATH": real_path, "HOME": str(self.root / "home")}):
            await self.runner.set_opencode_go_key(canary_1)
            subprocess.run(
                [opencode_bin, "run", "--standalone", "--auto", "--format", "json",
                 "--model", "opencode-go/deepseek-v4.1-flash#max", "ping"],
                env=env, capture_output=True, text=True, check=False, timeout=20,
            )
            self.assertIn(f"Bearer {canary_1}", seen_auth)
            seen_auth.clear()

            await self.runner.set_opencode_go_key(canary_2)
            subprocess.run(
                [opencode_bin, "run", "--standalone", "--auto", "--format", "json",
                 "--model", "opencode-go/deepseek-v4.1-flash#max", "ping"],
                env=env, capture_output=True, text=True, check=False, timeout=20,
            )
            self.assertIn(f"Bearer {canary_2}", seen_auth)
            self.assertNotIn(f"Bearer {canary_1}", seen_auth)

            exported = json.loads(subprocess.check_output(
                [opencode_bin, "auth", "export", "--standalone"], env=env, text=True, timeout=20,
            ))
            self.assertEqual(
                {item["integrationID"]: item["value"]["key"] for item in exported},
                {"anthropic": "preserve-unrelated", "opencode-go": canary_2},
            )

            await self.runner.clear_opencode_go_key()
            listed = subprocess.run(
                [opencode_bin, "auth", "list", "--standalone"],
                env=env, text=True, capture_output=True, check=False, timeout=20,
            )
            self.assertEqual(listed.returncode, 0, listed.stderr)
            self.assertNotIn("OpenCode Go", listed.stdout)
            self.assertFalse(self.runner.has_opencode_go_key())

    async def test_codex_account_lifecycle_shares_login_and_preserves_threads(self):
        app = self.app()
        codex = app.coordinator
        self.runner.save_thread(REPO, "thread-keep", TOOLS_HASH, 15)
        with patch.object(codex, "connect", new_callable=AsyncMock), \
             patch.object(codex, "request", new_callable=AsyncMock) as req:
            req.side_effect = [
                {"loginId": "login-1", "verificationUrl": "https://example.test/dev", "userCode": "CODE-1"},
                {},
                {"loginId": "login-2", "verificationUrl": "https://example.test/dev", "userCode": "CODE-2"},
            ]
            # First /login starts login-1; config login reuses the same pending login without duplicate RPC.
            await app.handle(REPO, "/login", "/login")
            cfg = await app.update_config({"action": "codex_login"})
            self.assertEqual(cfg["codex"]["login"], {"url": "https://example.test/dev", "code": "CODE-1"})
            self.assertEqual(req.await_count, 1)

            # Canceling clears login state and ignores stale completion for login-1.
            await app.update_config({"action": "codex_cancel_login"})
            self.assertIsNone((await app.config_state())["codex"]["login"])
            codex.notification("account/login/completed", {"loginId": "login-1", "success": True})
            self.assertEqual(codex.account, "")

            # Starting login-2 then ignoring stale login-1 completion before accepting login-2.
            cfg2 = await app.update_config({"action": "codex_login"})
            self.assertEqual(cfg2["codex"]["login"]["code"], "CODE-2")
            codex.notification("account/login/completed", {"loginId": "login-1", "success": False, "error": "stale"})
            self.assertIsNotNone((await app.config_state())["codex"]["login"])

        with patch.object(codex, "refresh_account", new_callable=AsyncMock) as refresh:
            codex.notification("account/login/completed", {"loginId": "login-2", "success": True})
            await asyncio.sleep(0)
        refresh.assert_awaited_once()
        codex.ready = True
        with patch.object(codex, "request", new_callable=AsyncMock, return_value={
            "account": {"type": "chatgpt", "email": "op@example.test", "planType": "pro", "workspaceName": "Team"},
        }):
            await codex.read_account()
        signed_in_cfg = await app.config_state()
        self.assertTrue(signed_in_cfg["codex"]["signedIn"])
        self.assertEqual(signed_in_cfg["codex"]["account"], {
            "type": "chatgpt", "email": "op@example.test", "plan": "pro", "workspace": "Team",
        })

        # Active coordinator turn blocks Codex account mutations with BusyError.
        app.coordinator.turns[REPO] = "turn-busy"
        with self.assertRaises(BusyError):
            await app.update_config({"action": "codex_logout"})
        app.coordinator.turns.clear()

        # Logout clears account state cleanly while preserving saved SQLite threads.
        with patch.object(codex, "connect", new_callable=AsyncMock), \
             patch.object(codex, "request", new_callable=AsyncMock, return_value={}) as req:
            logged_out = await app.update_config({"action": "codex_logout"})
        req.assert_awaited_once_with("account/logout", {})
        self.assertFalse(logged_out["codex"]["signedIn"])
        self.assertEqual(logged_out["codex"]["account"], {})
        self.assertEqual(self.runner.thread(REPO)["thread_id"], "thread-keep")

    async def test_set_plan_emits_proposal_and_api_approve_locks_without_codex_turn(self):
        app = self.app()
        live_issue = {
            "id": "lin-101", "identifier": ISSUE, "title": "Proposal card",
            "state": {"name": "Backlog", "type": "backlog"},
            "project": {"id": "project"}, "description": "Original body",
        }
        updated_descriptions = []

        async def fake_update(_id, *, description):
            updated_descriptions.append(description)
            live_issue["description"] = description

        app.repos[REPO] = [{"id": "project", "name": "Fixture"}]
        with patch.object(app, "refresh_projects", new_callable=AsyncMock, return_value=app.repos), \
             patch.object(app.linear, "issue", new_callable=AsyncMock, return_value=live_issue), \
             patch.object(app.linear, "update", side_effect=fake_update):
            await app.tool("set_plan", {"issue_id": ISSUE, "plan": "### Steps\n\n1. Ship it."})

        expected_hash = hashlib.sha256(b"### Steps\n\n1. Ship it.").hexdigest()
        msgs = (await app.state(0, project="project"))["messages"]
        self.assertEqual(msgs[-1]["role"], "system")
        self.assertEqual(msgs[-1]["meta"], {
            "kind": "plan_proposal", "issue": ISSUE,
            "title": "Proposal card", "planHash": expected_hash,
        })

        # issue_summary carries planHash cleanly.
        live_issue["labels"] = {"nodes": []}
        self.assertEqual(
            (await app.issues(REPO)) if False else issue_summary(live_issue)["planHash"],
            expected_hash,
        )

        # Stale hash fails with BusyError (409).
        with patch.object(app, "refresh_projects", new_callable=AsyncMock, return_value=app.repos), \
             patch.object(app.linear, "issue", new_callable=AsyncMock, return_value=live_issue):
            with self.assertRaisesRegex(BusyError, "changed"):
                await app.approve_plan({"issue": ISSUE, "planHash": "0" * 64})

        # Matching hash locks the plan, posts confirmation, and queues in Todo with zero Codex turns.
        with patch.object(app, "refresh_projects", new_callable=AsyncMock, return_value=app.repos), \
             patch.object(app.linear, "issue", new_callable=AsyncMock, return_value=live_issue), \
             patch.object(self.linear, "set_state", new_callable=AsyncMock) as set_state, \
             patch.object(app.coordinator, "respond", new_callable=AsyncMock) as coord_respond:
            result = await app.approve_plan({"issue": ISSUE, "planHash": expected_hash})
        self.assertEqual(result, {"approved": ISSUE, "repo": REPO})
        set_state.assert_awaited_once_with(ISSUE, "Todo", "unstarted")
        coord_respond.assert_not_awaited()
        self.assertEqual(self.runner.task(ISSUE)["phase"], "approved")
        self.assertEqual((await app.state(0, project="project"))["messages"][-1]["text"],
                         f"Approved {ISSUE} and queued in Todo.")

        # Duplicate approve is rejected cleanly with BusyError.
        with self.assertRaisesRegex(BusyError, "already has a recorded approval"):
            await app.approve_plan({"issue": ISSUE, "planHash": expected_hash})

    async def test_mcp_command_lists_all_dynamic_and_paginated_mcp_tools(self):
        app = self.app()
        pages = [
            {"data": [{"name": "linear", "authStatus": "oAuth", "tools": {
                "get_issue": {"description": "Fetch an issue.\nDetails ignored."},
                "save_issue": {"description": "Create or update an issue."},
            }}], "nextCursor": "page-2"},
            {"data": [{"name": "context7", "authStatus": "unsupported", "tools": [
                {"name": "query-docs", "description": "Query library documentation."},
            ]}], "nextCursor": None},
        ]
        seen_params = []

        async def fake_request(method, params):
            self.assertEqual(method, "mcpServerStatus/list")
            seen_params.append(params)
            return pages.pop(0)

        with patch.object(app.coordinator, "connect", new_callable=AsyncMock), \
             patch.object(app.coordinator, "request", side_effect=fake_request):
            await app.handle(REPO, "/mcp", "/mcp")

        self.assertEqual(seen_params, [{"detail": "Full"}, {"detail": "Full", "cursor": "page-2"}])
        output = self.runner.rows("SELECT text, repo FROM chat ORDER BY id DESC LIMIT 1")[0]
        self.assertEqual(output["repo"], REPO)
        for name in ("list_repos", "set_plan", "approve_issue", "read_notes", "ship_worker", "create_issue"):
            self.assertIn(name, output["text"])
        self.assertIn("linear [oAuth]", output["text"])
        self.assertIn("get_issue — Fetch an issue.", output["text"])
        self.assertIn("context7 [unsupported]", output["text"])
        self.assertIn("query-docs — Query library documentation.", output["text"])

        with patch.object(app.coordinator, "mcp_servers", side_effect=RuntimeError("Codex signed out")):
            await app.handle(REPO, "/mcp", "/mcp")
        degraded = self.runner.rows("SELECT text FROM chat ORDER BY id DESC LIMIT 1")[0]["text"]
        self.assertIn("Symphony dynamic tools:", degraded)
        self.assertIn("MCP servers unavailable: Codex signed out", degraded)

    async def test_repo_buttons_persist_validate_and_scope_per_repo_state(self):
        app = self.app()
        second = "owner/second"
        app.repos = {REPO: [{"id": "alpha", "name": "Alpha"}], second: [{"id": "beta", "name": "Beta"}]}
        with patch.object(app, "refresh_projects", new_callable=AsyncMock, return_value=app.repos):
            with self.assertRaisesRegex(ValueError, "Unknown repository"):
                await app.tool("set_repo_button", {"repo": "owner/missing", "label": "X", "prompt": "Y", "mode": "attach"})
            with self.assertRaisesRegex(ValueError, "attach' or 'send"):
                await app.tool("set_repo_button", {"repo": REPO, "label": "X", "prompt": "Y", "mode": "invalid"})
            saved = await app.tool("set_repo_button", {
                "repo": REPO.upper(), "label": "Idle reload",
                "prompt": "Keep workers idle for auto-deploy.", "mode": "attach",
            })
            self.assertEqual(saved["repo"], REPO)
            await app.tool("set_repo_button", {
                "repo": second, "label": "Quick check",
                "prompt": "Run checks", "mode": "send",
            })
            listed = await app.tool("list_repo_buttons", {"repo": REPO})
            self.assertEqual([b["label"] for b in listed], ["Idle reload"])
            self.assertEqual(
                [b["label"] for b in (await app.state(0, project="alpha"))["buttons"]],
                ["Idle reload"],
            )
            self.assertEqual(
                [b["label"] for b in (await app.state(0, project="beta"))["buttons"]],
                ["Quick check"],
            )
            reopened = Runner(self.root, self.linear)
            self.addCleanup(reopened.db.close)
            self.assertEqual(reopened.repo_buttons(REPO)[0]["prompt"], "Keep workers idle for auto-deploy.")
            await app.tool("delete_repo_button", {"repo": REPO, "label": "Idle reload"})
            self.assertEqual((await app.state(0, project="alpha"))["buttons"], [])

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

    @staticmethod
    def get_handler(path):
        handler = object.__new__(Handler)
        handler.path = path
        handler.headers = Message()
        handler.server = SimpleNamespace(app=SimpleNamespace())
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        return handler

    def test_agents_http_serves_only_the_fixed_canonical_file(self):
        content = "# Global\n\n<script>alert('literal')</script>\n- *not emphasis*\n"
        handler = self.get_handler("/api/agents?path=/etc/passwd")
        with patch("symphony.__main__.shared_agents", return_value=content) as load:
            handler.do_GET()
        load.assert_called_once_with()
        handler.send_response.assert_called_once_with(200)
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"content": content})

        handler = self.get_handler("/api/agents/../../etc/passwd")
        with patch("symphony.__main__.shared_agents") as load:
            handler.do_GET()
        load.assert_not_called()
        handler.send_response.assert_called_once_with(404)

    def test_agents_http_reports_empty_missing_and_oversized_files(self):
        handler = self.get_handler("/api/agents")
        with patch("symphony.__main__.shared_agents", return_value=""):
            handler.do_GET()
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"content": ""})

        handler = self.get_handler("/api/agents")
        with patch("symphony.__main__.shared_agents",
                   side_effect=RuntimeError("Global AGENTS.md is missing at /app/agents/AGENTS.md.")):
            handler.do_GET()
        handler.send_response.assert_called_once_with(503)
        self.assertIn("Global AGENTS.md is missing", json.loads(handler.wfile.getvalue())["error"])

        with TemporaryDirectory() as temp:
            path = Path(temp) / "AGENTS.md"
            path.write_text("x" * (AGENTS_BYTES + 1))
            handler = self.get_handler("/api/agents")
            with patch("symphony.runner.SHARED_AGENTS", path):
                handler.do_GET()
        handler.send_response.assert_called_once_with(503)
        self.assertIn("too large", json.loads(handler.wfile.getvalue())["error"])

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

    def test_config_http_enforces_origin_size_actions_busy_and_redaction(self):
        canary = "canary-secret-never-echo"
        handler = self.get_handler("/api/config")
        handler.server = SimpleNamespace(app=SimpleNamespace(
            config_state=AsyncMock(return_value={
                "opencodeGo": {"configured": True, "malformed": False, "busy": False},
                "codex": {"signedIn": False, "account": {}, "login": None, "busy": False},
            }),
        ))
        with patch.object(Handler, "run_async", side_effect=asyncio.run):
            handler.do_GET()
        handler.send_response.assert_called_once_with(200)
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("canary", handler.wfile.getvalue().decode())

        def post(raw, *, origin=None, host="symphony.tail:443", sym="1", app=None):
            h = self.get_handler("/api/config")
            h.headers["Content-Length"] = str(len(raw))
            h.headers["Content-Type"] = "application/json"
            h.headers["Sec-Fetch-Site"] = "same-origin"
            h.headers["Host"] = host
            if sym is not None:
                h.headers["X-Symphony"] = sym
            if origin is not None:
                h.headers["Origin"] = origin
            h.rfile = BytesIO(raw)
            h.server = SimpleNamespace(app=app or SimpleNamespace(approve_plan=Mock()))
            h.do_POST()
            return h.send_response.call_args.args[0], h.wfile.getvalue().decode()

        raw_ok = json.dumps({"action": "set_opencode_key", "apiKey": canary}).encode()
        self.assertEqual(post(raw_ok, sym=None)[0], 403)
        self.assertEqual(post(raw_ok, origin="https://evil.example")[0], 403)

        bad_json = b'{"action":"set_opencode_key","apiKey":"' + canary.encode()
        status, body = post(bad_json)
        self.assertEqual(status, 400)
        self.assertNotIn(canary, body)

        status, _ = post(b"x" * (CONFIG_BODY_LIMIT + 1))
        self.assertEqual(status, 400)

        busy_app = SimpleNamespace(update_config=AsyncMock(side_effect=BusyError("Worker running.")))
        with patch.object(Handler, "run_async", side_effect=BusyError("Worker running.")):
            status, body = post(raw_ok, origin="https://symphony.tail:443", app=busy_app)
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body), {"error": "Worker running."})

    def test_approve_http_enforces_csrf_and_handles_conflicts(self):
        def call(raw, *, origin=None, host="symphony.tail:443", sym="1", app=None):
            h = self.get_handler("/api/approve")
            h.headers["Content-Length"] = str(len(raw))
            h.headers["Content-Type"] = "application/json"
            h.headers["Sec-Fetch-Site"] = "same-origin"
            h.headers["Host"] = host
            if sym is not None:
                h.headers["X-Symphony"] = sym
            if origin is not None:
                h.headers["Origin"] = origin
            h.rfile = BytesIO(raw)
            h.server = SimpleNamespace(app=app or SimpleNamespace(approve_plan=Mock()))
            h.do_POST()
            return h.send_response.call_args.args[0], json.loads(h.wfile.getvalue())

        raw = json.dumps({"issue": ISSUE, "planHash": "a" * 64}).encode()
        self.assertEqual(call(raw, sym=None)[0], 403)
        self.assertEqual(call(raw, origin="https://evil.example")[0], 403)
        with patch.object(Handler, "run_async", return_value={"approved": ISSUE, "repo": REPO}):
            status, body = call(raw, origin="https://symphony.tail:443")
        self.assertEqual((status, body), (200, {"approved": ISSUE, "repo": REPO}))
        with patch.object(Handler, "run_async", side_effect=BusyError("Already approved.")):
            status, body = call(raw)
        self.assertEqual((status, body), (409, {"error": "Already approved."}))


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

    async def test_serve_leaves_pvc_opencode_auth_untouched_when_env_canary_set(self):
        class FakeServer:
            def __init__(self, *_):
                self.app = None

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

        fake_app = SimpleNamespace(
            runner=SimpleNamespace(jobs=set(), db=SimpleNamespace(close=Mock()), job=lambda c: c.close()),
            coordinator=SimpleNamespace(close=AsyncMock()),
            pump=AsyncMock(),
            start=AsyncMock(),
        )
        for existing in (None, '{"anthropic":{"type":"api","key":"keep"},"opencode-go":{"type":"api","key":"pvc-key"}}'):
            temp = TemporaryDirectory()
            self.addCleanup(temp.cleanup)
            auth = Path(temp.name) / "worker-data/opencode/auth.json"
            if existing is not None:
                auth.parent.mkdir(parents=True, exist_ok=True)
                auth.write_text(existing)
            env = {
                "SYMPHONY_DATA": temp.name,
                "HOME": str(Path(temp.name) / "home"),
                "LINEAR_API_KEY": "fixture",
                "OPENCODE_GO_API_KEY": "env-canary-must-never-seed-or-overwrite",
            }
            with patch.dict(os.environ, env, clear=True), \
                 patch("symphony.__main__.ThreadingHTTPServer", FakeServer), \
                 patch("symphony.__main__.Symphony", return_value=fake_app), \
                 patch("symphony.__main__.asyncio.Event", return_value=ImmediateEvent()):
                await serve()
            if existing is None:
                self.assertFalse(auth.exists())
            else:
                self.assertEqual(auth.read_text(), existing)


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
        self.assertIn('id="backlog-toggle"', html)
        self.assertIn('aria-controls="backlog"', html)
        self.assertIn('id="queue-toggle"', html)
        self.assertIn('aria-controls="status-issues"', html)
        self.assertIn(">Queue</h2>", html)
        self.assertLess(html.index('id="repos"'), html.index('id="config-open"'))
        self.assertLess(html.index('id="config-open"'), html.index('id="config-dialog"'))
        self.assertIn('aria-controls="config-dialog"', html)
        self.assertIn('id="config-agents-content"', html)
        app = (root / "app.js").read_text()
        self.assertIn("pollDelay(state, document.hidden)", app)
        self.assertIn('api("/api/agents")', app)
        self.assertIn("configContent.textContent = content", app)
        self.assertIn('"Awaiting approval"', app)
        self.assertIn("will not run", app)
        self.assertIn("let backlogCollapsed = false;", app)
        self.assertIn("let queueCollapsed = false;", app)
        self.assertIn("function setPaneCollapsed", app)


class SharedAgentsImageTests(unittest.TestCase):
    def test_image_ships_the_canonical_shared_file(self):
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "symphony/Dockerfile").read_text()
        self.assertIn("COPY agents/AGENTS.md /app/agents/AGENTS.md", dockerfile)
        self.assertLess(dockerfile.index("COPY symphony /app/symphony"),
                        dockerfile.index("COPY agents/AGENTS.md /app/agents/AGENTS.md"))
        ignore = (root / ".dockerignore").read_text()
        self.assertIn("!agents/", ignore)
        self.assertIn("!agents/AGENTS.md", ignore)
        canonical = (root / "agents/AGENTS.md").read_text()
        self.assertIn("# Symphony global AGENTS.md", canonical)
        self.assertIn("instructions take precedence", canonical)
        self.assertIn("Symphony's approval", canonical)


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
