"""Exercise Symphony's Antigravity coordinator, 2-tier Flash-to-Hydrogen runner, and HTTP/webhook boundaries."""

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
from symphony.antigravity import (
    Antigravity, CREATE_ISSUE_SCHEMA, ENQUEUE_TASK_SCHEMA, ESCALATION_MODEL, INSTRUCTIONS,
    TOOLS, TOOLS_HASH, fetch_code_assist_quota,
)
from symphony.execution import execution_payload
from symphony.mcp_bridge import handle_message as mcp_handle_message
from symphony.runner import (
    AGENTS_BYTES, BusyError, NOTES_LINES, Runner,
    install_agent_instructions, shared_agents, worker_prompt,
)
from symphony.services import (
    Linear, ci_workflows, estimate_scale, matches_filter, proposed_plan, with_plan,
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
        self.state_calls = []

    def configure(self, identifier, *, state=None, project="project", priority=2,
                  title="Fixture change", description="## Proposed Plan\n\nWrite one verification file."):
        self.issues[identifier] = {
            "identifier": identifier,
            "project": {"id": project},
            "state": state or self.state,
            "priority": priority,
            "title": title,
            "description": description,
        }

    async def issue(self, identifier):
        return self.issues.setdefault(identifier, {
            "identifier": identifier,
            "project": {"id": "project"},
            "state": self.state,
            "priority": 2,
            "title": "Fixture change",
            "description": "## Proposed Plan\n\nWrite one verification file.",
        })

    async def set_state(self, issue, name, kind):
        self.state_calls.append((issue, name, kind))
        entry = await self.issue(issue)
        entry["state"] = {"name": name, "type": kind}
        self.state = entry["state"]


FAKE_OPENCODE = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import sqlite3
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

prompt = sys.argv[-1]
match = re.search(r"Implement ([A-Z]+-\d+) ", prompt)
issue_id = match.group(1) if match else "KIAN-101"
workspace = Path(os.environ["FIXTURE_ROOT"]) / "runs" / issue_id
workspace.mkdir(parents=True, exist_ok=True)
config = json.loads(os.environ.get("OPENCODE_CONFIG_CONTENT", "{}"))
agents_path = Path(os.environ["XDG_CONFIG_HOME"]) / "opencode" / "AGENTS.md"
agents = agents_path.read_text() if agents_path.is_file() else None
assert "Do not push, change Linear, or open a pull request." in prompt
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
        "webhook_path": "LINEAR_WEBHOOK_PATH_TOKEN" in os.environ,
        "internal": "SYMPHONY_INTERNAL_TOKEN" in os.environ,
        "agents": agents,
    }) + "\n")

if os.environ.get("FIXTURE_FLASH_SLEEP"):
    Path("flash-dirty.txt").write_text("dirty-from-flash\n")
    time.sleep(float(os.environ["FIXTURE_FLASH_SLEEP"]))

print(json.dumps({"type": "text", "sessionID": "ses_fixture"}))
print(json.dumps({
    "type": "step_finish", "sessionID": "ses_fixture",
    "part": {"tokens": {"input": 100, "output": 20, "total": 120}, "cost": 0.001},
}))

if os.environ.get("FIXTURE_FLASH_429"):
    print(json.dumps({"type": "error", "error": {"type": "provider.api", "message": "Rate limited", "status": 429}}))
    sys.exit(1)
if os.environ.get("FIXTURE_FLASH_ERROR"):
    Path("flash-dirty.txt").write_text("dirty-from-flash\n")
    print(json.dumps({"type": "error", "error": {"type": "provider.network", "message": os.environ["FIXTURE_FLASH_ERROR"]}}))
    sys.exit(1)

if not os.environ.get("FIXTURE_NO_CHANGES"):
    Path("verified.txt").write_text(os.environ.get("FIXTURE_CONTENT", "Tested\n"))
if os.environ.get("FIXTURE_DIRTY_FILE"):
    Path("flash-dirty.txt").write_text("dirty-from-flash\n")
if not os.environ.get("FIXTURE_SKIP_NOTES"):
    notes = os.environ.get("FIXTURE_NOTES", "None")
    if not notes.endswith("\n"):
        notes += "\n"
    (workspace / "notes.md").write_text(notes)
if os.environ.get("FIXTURE_BLOCK"):
    (workspace / "blocked.txt").write_text(os.environ["FIXTURE_BLOCK"])
    sys.exit(0)
if not os.environ.get("FIXTURE_SKIP_CHECKS"):
    checks = "echo flash-check-fail; exit 2\n" if os.environ.get("FIXTURE_CHECKS_FAIL") else 'test "$(cat verified.txt)" = Tested\n'
    (workspace / "checks.sh").write_text(checks)
"""

FAKE_AGY = r"""#!/usr/bin/env python3
import json
import os
from pathlib import Path
import re
import sys
import time

home = Path(os.environ["HOME"])
token_path = home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
fixture_root = Path(os.environ.get("FIXTURE_ROOT", str(home)))

if len(sys.argv) >= 2 and sys.argv[1] == "models":
    print("gemini-4-neon-high         Gemini 4 Neon (High)")
    print("gemini-4-hydrogen-xhigh    Gemini 4 Hydrogen (XHigh)")
    sys.exit(0)

# Log every non-models invocation for assertions.
with (fixture_root / "agy-calls.jsonl").open("a") as stream:
    stream.write(json.dumps({
        "args": sys.argv,
        "cwd": os.getcwd(),
        "dbus": os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
        "github": "GITHUB_TOKEN" in os.environ,
        "linear": "LINEAR_API_KEY" in os.environ,
        "internal": "SYMPHONY_INTERNAL_TOKEN" in os.environ,
        "pythonpath": os.environ.get("PYTHONPATH", ""),
    }) + "\n")

if not token_path.exists():
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("Error: authentication required. Run 'agy' to log in, then retry.", flush=True)
        sys.exit(1)
    print("Authentication required. Please visit the URL to log in:", flush=True)
    print("  https://accounts.google.com/o/oauth2/auth?client_id=fixture&state=abc123", flush=True)
    print("Or, paste the authorization code here and press Enter:", flush=True)
    code = sys.stdin.readline().strip()
    if not code or code == "bad-code":
        print("Authorization failed", flush=True)
        sys.exit(1)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps({
        "token": {"access_token": "fixture-access-token", "refresh_token": "fixture-refresh-token"},
        "email": "kian@example.test",
    }))
    os.chmod(token_path, 0o600)
    print(json.dumps({"conversation_id": "conv-login", "status": "SUCCESS", "response": "ok\n"}))
    sys.exit(0)

prompt = sys.argv[sys.argv.index("--print") + 1] if "--print" in sys.argv else ""
conv = sys.argv[sys.argv.index("--conversation") + 1] if "--conversation" in sys.argv else "conv-1"

if os.environ.get("FIXTURE_AGY_SLEEP"):
    print(json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "Working..."}}), flush=True)
    time.sleep(float(os.environ["FIXTURE_AGY_SLEEP"]))

if os.environ.get("FIXTURE_AGY_ERROR"):
    print(json.dumps({"event": "result", "result": {"status": "ERROR", "error": os.environ["FIXTURE_AGY_ERROR"]}}), flush=True)
    sys.exit(1)

if "Prior Tier-1 (DeepSeek-V4.1-Flash) failure receipt" in prompt:
    match = re.search(r"Implement ([A-Z]+-\d+) ", prompt)
    issue_id = match.group(1) if match else "KIAN-101"
    workspace = fixture_root / "runs" / issue_id
    # Prove cold reset wiped any dirty file left by Flash.
    assert not Path("flash-dirty.txt").exists(), "Worktree was not cold-reset before escalation!"
    (workspace / "escalation-prompt.txt").write_text(prompt)
    Path("verified.txt").write_text("Escalated\n")
    if os.environ.get("FIXTURE_ESC_BLOCK"):
        (workspace / "blocked.txt").write_text(os.environ["FIXTURE_ESC_BLOCK"])
    elif os.environ.get("FIXTURE_ESC_FAIL"):
        print(json.dumps({"event": "result", "result": {"status": "ERROR", "error": "Hydrogen failed"}}), flush=True)
        sys.exit(1)
    elif not os.environ.get("FIXTURE_ESC_SKIP_CHECKS"):
        (workspace / "checks.sh").write_text('test "$(cat verified.txt)" = Escalated\n')
    print(json.dumps({
        "event": "result",
        "result": {
            "conversation_id": "conv-esc",
            "status": "SUCCESS",
            "response": "Escalation completed.",
            "usage": {"input_tokens": 500, "output_tokens": 80, "total_tokens": 580},
        },
    }), flush=True)
    sys.exit(0)

if prompt == "/compact":
    print(json.dumps({
        "event": "result",
        "result": {"conversation_id": conv, "status": "SUCCESS", "response": "Compacted."},
    }), flush=True)
    sys.exit(0)

print(json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "Hello "}}), flush=True)
print(json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "text_delta": "world"}}), flush=True)
print(json.dumps({
    "event": "result",
    "result": {
        "conversation_id": conv,
        "status": "SUCCESS",
        "response": "Hello world",
        "usage": {"input_tokens": 250, "output_tokens": 40, "total_tokens": 290},
    },
}), flush=True)
"""


class SymphonyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
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
        worker.write_text(FAKE_OPENCODE)
        worker.chmod(0o755)
        agy = fake_bin / "agy"
        agy.write_text(FAKE_AGY)
        agy.chmod(0o755)

        context = patch.dict(os.environ, {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "GITHUB_TOKEN": "fixture",
            "LINEAR_WEBHOOK_SECRET": "fixture-webhook-secret",
            "FIXTURE_ROOT": str(self.root),
        })
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

    def seed_agy_token(self, home=None):
        target = (home or self.home) / ".gemini" / "antigravity-cli" / "antigravity-oauth-token"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({
            "token": {"access_token": "fixture-access", "refresh_token": "fixture-refresh"},
            "email": "kian@example.test",
        }))
        target.chmod(0o600)
        return target

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

    async def phase(self, expected, issue=ISSUE):
        async with asyncio.timeout(10):
            while self.runner.task(issue)["phase"] != expected:
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

    def proposed_issue(self, identifier=ISSUE):
        return {
            "identifier": identifier, "title": "Fixture change", "state": self.linear.state,
            "project": {"id": "project"}, "description": "## Proposed Plan\n\nWrite one verification file.",
        }

    def app(self, runner=None):
        runner = runner or self.runner
        with patch.dict(os.environ, {"LINEAR_API_KEY": "fixture"}), \
             patch("symphony.__main__.Runner", return_value=runner):
            return Symphony(self.root)

    def queue_linear_todo(self, identifier=ISSUE, *, repo=REPO, project="project",
                          title="Fixture change", plan="Write one verification file.", priority=2):
        snapshot = [{
            "id": project,
            "repo": repo,
            "issues": [{
                "identifier": identifier,
                "title": title,
                "plan": plan,
                "priority": priority,
                "state": {"name": "Todo", "type": "unstarted"},
            }],
        }]
        return self.runner.sync_todo_issues(snapshot)

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

    # --- Chat & UI State ---

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

        app.coordinator.streams.pop("", None)
        self.runner.chat("assistant", reply)
        complete = await app.state(0)
        self.assertEqual(complete["stream"], "")
        self.assertEqual(complete["streamHtml"], "")
        assistant_messages = [item for item in complete["messages"] if item["role"] == "assistant"]
        self.assertEqual(len(assistant_messages), 1)
        self.assertEqual(assistant_messages[0]["text"], reply)
        self.assertEqual(assistant_messages[0]["html"], stream_html)

    async def test_chat_submit_acknowledges_the_saved_row_in_server_order(self):
        app = self.app()
        app.repos["owner/second"] = [{"id": "beta", "name": "Beta"}]
        self.runner.save_setting("view", "project")
        self.runner.chat("system", "Earlier notice")
        image = image_payload(WEBP, "image/webp")
        result = await app.submit({"project": "beta", "text": "Use this mockup.", "images": [image]})
        saved = result["message"]
        rows = self.runner.rows("SELECT * FROM chat ORDER BY id")
        self.assertEqual(saved["id"], rows[-1]["id"])
        self.assertEqual(saved["role"], "user")
        self.assertEqual(saved["text"], "Use this mockup.")
        self.assertEqual(saved["repo"], "owner/second")
        self.assertEqual([item["type"] for item in saved["images"]], ["image/webp"])

        first = await app.submit({"project": "beta", "text": "Repeat"})
        second = await app.submit({"project": "beta", "text": "Repeat"})
        self.assertNotEqual(first["message"]["id"], second["message"]["id"])
        state = await app.state(0)
        self.assertEqual([message["id"] for message in state["messages"]],
                         [saved["id"], first["message"]["id"], second["message"]["id"]])

        image_only = await app.submit({"project": "beta", "images": [image_payload(PNG)]})
        self.assertEqual(image_only["message"]["text"], "")
        self.assertEqual([item["type"] for item in image_only["message"]["images"]], ["image/png"])

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
        for image, expected in zip(images, (PNG, JPEG, WEBP)):
            self.assertRegex(image["name"], r"^[0-9a-f]{32}\.(?:png|jpg|webp)$")
            path = app.runner.chat_image_path(image["name"])
            self.assertEqual(path.read_bytes(), expected)
            self.assertEqual(app.chat_image(image["name"]), (expected, image["type"]))

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
        self.assertEqual(handler.wfile.getvalue(), PNG)
        handler.send_response.assert_called_once_with(200)
        self.assertEqual(accepted["message"]["images"], images)

    async def test_chat_images_reject_invalid_uploads_and_slash_commands(self):
        app = self.app()
        cases = [
            ({"text": "x", "images": [image_payload()] * 5}, "at most four"),
            ({"text": "x", "images": [{"type": "image/gif", "data": "R0lGODlh"}]}, "PNG, JPEG, or WebP"),
            ({"text": "x", "images": [{"type": "image/png", "data": "!!!"}]}, "Invalid image encoding"),
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
                await app.submit({"text": "x", "images": [image_payload(PNG), image_payload(JPEG, "image/jpeg")]})

    async def test_chat_images_are_removed_when_saving_the_chat_entry_fails(self):
        app = self.app()
        with patch.object(app.runner, "chat", side_effect=RuntimeError("Database unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Database unavailable"):
                await app.submit({"text": "Screenshot", "images": [image_payload()]})
        self.assertEqual(list(app.runner.chat_image_dir().iterdir()), [])

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
        self.assertEqual(app.runner.setting("view"), "beta")

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
        handler = object.__new__(Handler)
        handler.path = "/api/chat-image?name=..%2Foutside.png"
        handler.server = SimpleNamespace(app=app)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        handler.send_response.assert_called_once_with(404)

    # --- Antigravity Coordinator, OAuth, MCP Bridge, Quota, Usage Alerts ---

    async def test_antigravity_env_mcp_config_and_tool_schemas(self):
        app = self.app()
        agy = app.coordinator
        with patch.dict(os.environ, {
            "GITHUB_TOKEN": "gh-tok",
            "LINEAR_API_KEY": "lin-tok",
            "OPENAI_API_KEY": "oa-secret",
            "OPENCODE_GO_API_KEY": "go-secret",
            "LINEAR_WEBHOOK_SECRET": "wh-secret",
            "DISPLAY": ":0",
        }, clear=True):
            coord_env = agy.agy_env(worker=False)
            worker_env = agy.agy_env(worker=True)
        self.assertEqual(coord_env["HOME"], str(self.home))
        self.assertEqual(coord_env["DBUS_SESSION_BUS_ADDRESS"], f"unix:path={self.home}/.no-dbus")
        self.assertEqual(coord_env["GH_TOKEN"], "gh-tok")
        self.assertEqual(coord_env["GITHUB_TOKEN"], "gh-tok")
        self.assertEqual(coord_env["LINEAR_API_KEY"], "lin-tok")
        self.assertEqual(coord_env["SYMPHONY_INTERNAL_TOKEN"], app.internal_token)
        self.assertIn(str(Path(__file__).resolve().parents[1]), coord_env["PYTHONPATH"].split(os.pathsep))
        for secret in ("OPENAI_API_KEY", "OPENCODE_GO_API_KEY", "LINEAR_WEBHOOK_SECRET", "DISPLAY"):
            self.assertNotIn(secret, coord_env)
            self.assertNotIn(secret, worker_env)
        for secret in ("GITHUB_TOKEN", "GH_TOKEN", "LINEAR_API_KEY", "SYMPHONY_INTERNAL_TOKEN"):
            self.assertNotIn(secret, worker_env)

        # install_mcp_config writes 0600 JSON and preserves unrelated servers.
        agy.mcp_config_path.parent.mkdir(parents=True, exist_ok=True)
        agy.mcp_config_path.write_text(json.dumps({"mcpServers": {"other": {"command": "other"}}}))
        agy.install_mcp_config()
        self.assertEqual(agy.mcp_config_path.stat().st_mode & 0o777, 0o600)
        saved_cfg = json.loads(agy.mcp_config_path.read_text())
        self.assertEqual(saved_cfg["mcpServers"]["other"], {"command": "other"})
        self.assertEqual(saved_cfg["mcpServers"]["symphony"]["args"], ["-m", "symphony.mcp_bridge"])

        # Tool schemas enforce strict validation.
        tools = {tool["name"]: tool for tool in TOOLS}
        self.assertEqual(tools["create_issue"]["inputSchema"], CREATE_ISSUE_SCHEMA)
        self.assertEqual(tools["enqueue_task"]["inputSchema"], ENQUEUE_TASK_SCHEMA)
        self.assertNotIn("approve_issue", tools)
        self.assertNotIn("spawn_worker", tools)
        self.assertNotIn("ship_worker", tools)
        self.assertIn("enqueue_task", INSTRUCTIONS)
        for name, args in [
            ("set_plan", {"issue_id": ISSUE, "plan": 1}),
            ("list_issues", {"repo": REPO, "extra": "x"}),
            ("enqueue_task", {"repo": REPO}),
            ("get_issue", {"issue_id": "kian-101"}),
        ]:
            with self.subTest(name=name, args=args), \
                 self.assertRaisesRegex(ValueError, "Invalid|uppercase|required"):
                await app.tool(name, args)
        with self.assertRaisesRegex(ValueError, "Unknown tool"):
            await app.tool("missing_tool", {})

    async def test_antigravity_headless_oauth_login_cancel_and_logout_preserve_threads(self):
        app = self.app()
        self.runner.save_thread(REPO, "conv-keep", TOOLS_HASH, 10)

        # Start login via /login, confirm config action reuses active pending login.
        await app.handle(REPO, "/login", "/login")
        cfg = await app.update_config({"action": "antigravity_login"})
        self.assertEqual(
            cfg["antigravity"]["login"],
            {"url": "https://accounts.google.com/o/oauth2/auth?client_id=fixture&state=abc123"},
        )
        self.assertFalse(cfg["antigravity"]["signedIn"])

        # Cancel clears pending login and terminates child process cleanly.
        cancelled = await app.update_config({"action": "antigravity_cancel_login"})
        self.assertIsNone(cancelled["antigravity"]["login"])

        # Signed out status omits model name and model list cleanly.
        signed_out_status = (await app.state(0))["coordinator"]
        self.assertEqual(signed_out_status["model"], "")
        self.assertEqual(signed_out_status["models"], {})

        # Start again and submit valid authorization code -> saves 0600 token & signs in.
        await app.update_config({"action": "antigravity_login"})
        completed = await app.update_config({"action": "antigravity_complete_login", "code": "4/0A-fixture-code"})
        self.assertTrue(completed["antigravity"]["signedIn"])
        self.assertEqual(completed["antigravity"]["account"]["email"], "kian@example.test")
        self.assertIsNone(completed["antigravity"]["login"])
        self.assertEqual(app.coordinator.token_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((await app.state(0))["coordinator"]["model"], "Gemini 4 Neon (High)")

        # Active turn blocks logout with BusyError.
        app.coordinator.turns[REPO] = "turn-busy"
        with self.assertRaises(BusyError):
            await app.update_config({"action": "antigravity_logout"})
        app.coordinator.turns.clear()

        # Logout removes token while preserving saved SQLite conversation rows.
        logged_out = await app.update_config({"action": "antigravity_logout"})
        self.assertFalse(logged_out["antigravity"]["signedIn"])
        self.assertFalse(app.coordinator.token_path.exists())
        self.assertEqual(self.runner.thread(REPO)["thread_id"], "conv-keep")

    async def test_antigravity_respond_streams_persists_conversation_handles_images_and_interrupts(self):
        self.seed_agy_token()
        app = self.app()
        app.repos[REPO] = [{"id": "project", "name": "Fixture"}]
        await app.coordinator.connect()

        name = self.runner.save_chat_image("png", PNG)
        images = [{"name": name, "type": "image/png"}]
        outcome = await app.respond(REPO, "Inspect this layout.", attachments=images)
        self.assertEqual(outcome, "completed")
        self.assertEqual(self.runner.thread(REPO)["thread_id"], "conv-1")
        rows = self.runner.rows("SELECT role, text FROM chat WHERE repo=? ORDER BY id", (REPO,))
        self.assertEqual(rows[-1], {"role": "assistant", "text": "Hello world"})
        calls = [json.loads(line) for line in (self.root / "agy-calls.jsonl").read_text().splitlines()]
        self.assertEqual(len(calls), 1)
        self.assertNotIn("--conversation", calls[0]["args"])
        prompt_arg = calls[0]["args"][calls[0]["args"].index("--print") + 1]
        self.assertIn(str(self.runner.chat_image_path(name)), prompt_arg)
        self.assertEqual(calls[0]["dbus"], f"unix:path={self.home}/.no-dbus")
        usage = self.runner.rows("SELECT * FROM token_usage WHERE source='coordinator'")
        self.assertEqual((usage[0]["input_tokens"], usage[0]["output_tokens"], usage[0]["total_tokens"]), (250, 40, 290))

        # Second turn resumes existing conversation ID via --conversation.
        await app.respond(REPO, "Follow-up")
        calls = [json.loads(line) for line in (self.root / "agy-calls.jsonl").read_text().splitlines()]
        self.assertIn("--conversation", calls[1]["args"])
        self.assertEqual(calls[1]["args"][calls[1]["args"].index("--conversation") + 1], "conv-1")

        # /interrupt kills a live agy turn cleanly and clears active turn state.
        with patch.dict(os.environ, {"FIXTURE_AGY_SLEEP": "10"}):
            turn_task = asyncio.create_task(app.respond(REPO, "Long running turn"))
            async with asyncio.timeout(3):
                while not app.coordinator.turn_for(REPO):
                    await asyncio.sleep(0.01)
            await app.submit({"project": "project", "text": "/interrupt"})
            result = await turn_task
        self.assertEqual(result, "interrupted")
        self.assertFalse((await app.state(0, project="project"))["canInterrupt"])

    async def test_antigravity_quota_parsing_and_80_percent_escalation_guard(self):
        future_short = "2099-01-01T00:00:00Z"
        responses = [
            {"cloudaicompanionProject": "proj-1", "currentTier": {"name": "Pro"}},
            {"groups": [
                {"groupId": "gemini", "buckets": [
                    {"bucketId": "gemini-5h", "remainingFraction": 0.72, "resetTime": future_short},
                    {"bucketId": "gemini-weekly", "remainingFraction": 0.45, "resetTime": future_short},
                ]},
                {"groupId": "other", "buckets": [
                    {"bucketId": "other-weekly", "remainingFraction": 0.90, "resetTime": future_short},
                ]},
            ]},
        ]
        with patch("symphony.antigravity._code_assist_post", side_effect=responses):
            limits, tier = fetch_code_assist_quota("fixture-token")
        self.assertEqual(tier, "Pro")
        self.assertEqual(limits["5h"]["left"], 72)
        self.assertEqual(limits["weekly"]["left"], 45)
        self.assertEqual(limits["otherWeekly"]["left"], 90)

        agy = Antigravity(self.runner, None, home_dir=self.home)
        self.assertEqual(agy.can_auto_escalate()[0], False)
        agy.account = "antigravity"
        agy.rate_limits = {"5h": {"left": 21, "resetsAt": None}, "weekly": {"left": 50, "resetsAt": None}}
        self.assertEqual(agy.can_auto_escalate(), (True, ""))
        agy.rate_limits = {"5h": {"left": 20, "resetsAt": None}, "weekly": {"left": 50, "resetsAt": None}}
        allowed, reason = agy.can_auto_escalate()
        self.assertFalse(allowed)
        self.assertIn("20% left", reason)
        agy.rate_limits = {"5h": {"left": 80, "resetsAt": None}, "weekly": {"left": 15, "resetsAt": None}}
        allowed, reason = agy.can_auto_escalate()
        self.assertFalse(allowed)
        self.assertIn("weekly", reason)

    async def test_antigravity_usage_exhaustion_alerts_once_recovers_and_handles_turn_failures(self):
        self.seed_agy_token()
        app = self.app()
        app.repos[REPO] = [{"id": "project", "name": "Fixture"}]
        agy = app.coordinator
        await agy.connect()
        self.runner.save_setting("view", "project")

        exhausted_limits = ({"5h": {"left": 0, "resetsAt": 1800000000}, "weekly": {"left": 40, "resetsAt": 1800500000}}, "Pro")
        with patch("symphony.antigravity.fetch_code_assist_quota", return_value=exhausted_limits):
            await agy.refresh_quota()
            await agy.refresh_quota()
        state = await app.state(0, project="project")
        self.assertTrue(state["usageAlert"]["active"])
        self.assertEqual(state["usageAlert"]["windows"], ["5h"])
        self.assertEqual(state["usageAlert"]["resetsAt"], 1800000000)
        notices = [m for m in state["messages"] if m["role"] == "system"]
        self.assertEqual(len(notices), 1)
        self.assertIn("5h at 0%", notices[0]["text"])

        recovered_limits = ({"5h": {"left": 65, "resetsAt": 1800000000}, "weekly": {"left": 40, "resetsAt": 1800500000}}, "Pro")
        with patch("symphony.antigravity.fetch_code_assist_quota", return_value=recovered_limits):
            await agy.refresh_quota()
        self.assertIsNone((await app.state(0, project="project"))["usageAlert"])

        # Turn hitting quota records usageAlert once without pausing OpenCode Go Flash workers.
        with patch.object(app, "respond", new_callable=AsyncMock,
                          side_effect=RuntimeError("RESOURCE_EXHAUSTED: quota exceeded")):
            await app.submit({"project": "project", "text": "Hello"})
            await self.pump_until(app, lambda: app.coordinator.usage_error is not None)
        err_state = await app.state(0, project="project")
        self.assertTrue(err_state["usageAlert"]["active"])
        self.assertEqual(err_state["usageAlert"]["source"], "turn_error")
        self.assertEqual(self.runner.setting("paused"), "")
        self.assertFalse(agy.can_auto_escalate()[0])

        await agy.logout()
        self.assertIsNone((await app.state(0, project="project"))["usageAlert"])

    async def test_mcp_bridge_and_internal_tool_endpoint_enforce_bearer_auth(self):
        init = mcp_handle_message({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "symphony")
        listed = mcp_handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tool_names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertIn("enqueue_task", tool_names)
        self.assertIn("create_issue", tool_names)
        self.assertIsNone(mcp_handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}))

        app = self.app()
        app.repos = {REPO: [{"id": "project", "name": "Fixture"}]}
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.loop = asyncio.get_running_loop()
        server.app = app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/internal/tool"

        # Wrong token fails closed with isError=True.
        with patch.dict(os.environ, {"SYMPHONY_INTERNAL_URL": url, "SYMPHONY_INTERNAL_TOKEN": "wrong"}):
            denied = await asyncio.to_thread(mcp_handle_message, {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "worker_status", "arguments": {}},
            })
        self.assertTrue(denied["result"]["isError"])

        # Valid token dispatches enqueue_task cleanly into Symphony's runner.
        with patch.object(app, "refresh_projects", new_callable=AsyncMock, return_value=app.repos), \
             patch.dict(os.environ, {"SYMPHONY_INTERNAL_URL": url, "SYMPHONY_INTERNAL_TOKEN": app.internal_token}):
            ok = await asyncio.to_thread(mcp_handle_message, {
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "enqueue_task", "arguments": {"repo": REPO, "prompt": "Fix typo", "title": "Fix typo"}},
            })
        self.assertFalse(ok["result"]["isError"])
        payload = json.loads(ok["result"]["content"][0]["text"])
        self.assertEqual(payload["issue"], "ADHOC-1")
        self.assertEqual(self.runner.task("ADHOC-1")["phase"], "queued")

    async def test_mcp_command_lists_symphony_and_configured_servers(self):
        app = self.app()
        await app.coordinator.connect()
        await app.handle(REPO, "/mcp", "/mcp")
        output = self.runner.rows("SELECT text, repo FROM chat ORDER BY id DESC LIMIT 1")[0]
        self.assertEqual(output["repo"], REPO)
        for name in ("enqueue_task", "list_repos", "set_plan", "worker_status", "retry_task", "create_issue"):
            self.assertIn(name, output["text"])
        self.assertIn("symphony [configured]", output["text"])

    async def test_per_repo_sessions_isolate_threads_chat_and_new_command(self):
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

        self.runner.save_thread(REPO, "conv-a1", TOOLS_HASH)
        self.runner.save_thread(second, "conv-b1", TOOLS_HASH)
        await app.handle(REPO, "/new", "/new")
        self.assertIsNone(self.runner.thread(REPO))
        self.assertEqual(self.runner.thread(second)["thread_id"], "conv-b1")
        self.assertEqual((await app.state(0, project="alpha"))["messages"][-1]["text"],
                         "Started a new coordinator session.")
        self.assertTrue(second_path.is_dir())

    async def test_lean_turns_omit_unchanged_rules_and_include_compaction_reminder(self):
        (self.repo / "AGENTS.md").write_text("# Repo rules\n\n- INITIAL-REPO-RULE\n")
        self.git(self.repo, "add", "AGENTS.md")
        self.git(self.repo, "commit", "-m", "Add initial rules")
        self.git(self.repo, "push", "origin", "main")
        shared = self.root / "shared-lean-AGENTS.md"
        shared.write_text("# Global\n\n- LEAN-GLOBAL-RULE\n")
        app = self.app()
        turns = []

        async def capture(repo, text, cwd, attachments=()):
            turns.append(text)
            return "completed"

        with patch("symphony.runner.SHARED_AGENTS", shared), \
             patch.object(app.coordinator, "respond", new_callable=AsyncMock, side_effect=capture):
            await app.respond(REPO, "First turn")
            await app.respond(REPO, "Second turn")
            (self.repo / "AGENTS.md").write_text("# Repo rules\n\n- UPDATED-REPO-RULE\n")
            self.git(self.repo, "add", "AGENTS.md")
            self.git(self.repo, "commit", "-m", "Update rules")
            self.git(self.repo, "push", "origin", "main")
            await app.respond(REPO, "Third turn")
            await app.respond(REPO, "Fourth turn")
            app.coordinator.compacted_repos.add(REPO)
            await app.respond(REPO, "Post-compaction turn")
            await app.respond(REPO, "After recovery turn")

        self.assertIn("INITIAL-REPO-RULE", turns[0])
        self.assertEqual(turns[1], "Second turn")
        self.assertIn("UPDATED-REPO-RULE", turns[2])
        self.assertEqual(turns[3], "Fourth turn")
        self.assertIn("Context was compacted. Call worker_status", turns[4])
        self.assertEqual(turns[5], "After recovery turn")

    async def test_compact_reports_queued_and_running_status(self):
        app = self.app()
        app.repos[REPO] = [{"id": "project", "name": "Fixture"}]
        app.busy = True
        app.coordinator.turns[REPO] = "turn-1"

        await app.submit({"project": "project", "text": "/compact"})
        queued_state = await app.state(0, project="project")
        self.assertEqual(queued_state["compacting"], "queued")
        self.assertEqual(queued_state["messages"][-1]["text"], "Compaction queued after the current turn.")

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
            self.assertEqual((await app.state(0, project="project"))["compacting"], "running")
            release.set()
            await task
        self.assertEqual((await app.state(0, project="project"))["compacting"], "")

    async def test_compact_captures_checkpoint_text_and_escapes_markdown_in_state(self):
        self.seed_agy_token()
        app = self.app()
        app.repos[REPO] = [{"id": "project", "name": "Fixture"}]
        conv_id = "conv-compact-1"
        self.runner.save_thread(REPO, conv_id, TOOLS_HASH)
        logs = self.home / ".gemini" / "antigravity-cli" / "brain" / conv_id / ".system_generated" / "logs"
        logs.mkdir(parents=True)
        (logs / "transcript_full.jsonl").write_text("\n".join([
            json.dumps({"step_index": 1, "type": "USER_INPUT", "content": "hi"}),
            json.dumps({"step_index": 2, "type": "CHECKPOINT", "content": "Old summary"}),
        ]) + "\n")

        async def append_checkpoint(repo, prompt, cwd, *, conv_id="", record_chat=True):
            with (logs / "transcript_full.jsonl").open("a") as stream:
                stream.write(json.dumps({
                    "step_index": 5, "type": "CHECKPOINT",
                    "content": "**New summary**\n\n<script>alert(1)</script>",
                }) + "\n")
            app.coordinator.last_replies[repo] = "Compacted."
            return "completed"

        with patch.object(app.coordinator, "_run_turn", side_effect=append_checkpoint):
            await app.handle(REPO, "/compact", "/compact")
        msgs = (await app.state(0, project="project"))["messages"]
        self.assertEqual(msgs[-1]["text"], "Coordinator context compacted.")
        self.assertEqual(msgs[-1]["meta"]["kind"], "compaction")
        self.assertIn("**New summary**", msgs[-1]["meta"]["compactionText"])
        self.assertIn("<strong>New summary</strong>", msgs[-1]["meta"]["compactionHtml"])
        self.assertNotIn("<script>", msgs[-1]["meta"]["compactionHtml"])
        self.assertIn("&lt;script&gt;", msgs[-1]["meta"]["compactionHtml"])

    async def test_all_repositories_project_resolution_and_linear_project_status(self):
        app = self.app()
        app.repos = {REPO: [{"id": "alpha", "name": "Alpha", "status": {"name": "In Progress", "type": "started", "color": "#0f0"}}]}
        self.runner.save_setting("view", "alpha")
        self.runner.chat("system", "Global notice", repo="")
        self.runner.chat("user", "Alpha msg", repo=REPO)
        # Omitted project uses saved view; explicit project="" selects global cross-repo session.
        self.assertEqual((await app.state(0))["repo"], REPO)
        global_state = await app.state(0, project="")
        self.assertEqual(global_state["repo"], "")
        self.assertEqual([m["text"] for m in global_state["messages"]], ["Global notice"])
        self.assertIn(REPO, global_state["repoActivity"])

        handler = object.__new__(Handler)
        handler.path = "/api/state?after=0&issues=r1&project="
        sentinel = object()
        handler.server = SimpleNamespace(app=SimpleNamespace(state=Mock(return_value=sentinel)))
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        with patch.object(Handler, "run_async", return_value={"messages": []}) as run_async:
            handler.do_GET()
        handler.server.app.state.assert_called_once_with(0, "r1", project="", repo="")
        run_async.assert_called_once_with(sentinel)

        linear = Linear("fixture")
        with patch.object(linear, "query", AsyncMock(return_value={
            "projects": {
                "nodes": [{
                    "id": "p1", "name": "Clusterfork", "icon": ":rocket:", "description": "https://github.com/o/r",
                    "archivedAt": None, "status": {"id": "s1", "name": "Paused", "type": "backlog", "color": "#999"},
                    "externalLinks": {"nodes": [], "pageInfo": {"hasNextPage": False}},
                }],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        })):
            repos, _ = await linear.projects()
        self.assertEqual(repos["o/r"][0]["status"], {"name": "Paused", "type": "backlog", "color": "#999"})

    async def test_update_issue_status_validates_guards_active_workers_and_mutates_linear(self):
        app = self.app()
        app.repos = {REPO: [{"id": "project", "name": "Fixture"}]}
        live_issue = {
            "id": "issue-uuid", "identifier": ISSUE, "updatedAt": "2026-11-28T10:00:00Z",
            "state": {"id": "backlog", "name": "Backlog", "type": "backlog", "color": "#bbb"},
            "team": {"id": "team"}, "project": {"id": "project"},
        }
        updated_issue = {
            **live_issue, "updatedAt": "2026-11-28T10:01:00Z",
            "state": {"id": "todo", "name": "Todo", "type": "unstarted", "color": "#ccc"},
        }
        with patch.object(app, "refresh_projects", new_callable=AsyncMock, return_value=app.repos), \
             patch.object(app.linear, "issue", new_callable=AsyncMock, return_value=live_issue), \
             patch.object(app.linear, "team_context", new_callable=AsyncMock, return_value=TEAM), \
             patch.object(app.linear, "update", new_callable=AsyncMock, return_value=updated_issue) as update:
            res = await app.update_issue_status({"issue": ISSUE, "stateId": "todo"})
        self.assertEqual(res["issue"], ISSUE)
        self.assertEqual(res["state"]["id"], "todo")
        self.assertTrue(app.runner.issue_refresh.is_set())
        update.assert_awaited_once_with("issue-uuid", stateId="todo")

        with self.assertRaisesRegex(ValueError, "Unknown Linear workflow state"):
            with patch.object(app, "refresh_projects", new_callable=AsyncMock, return_value=app.repos), \
                 patch.object(app.linear, "issue", new_callable=AsyncMock, return_value=live_issue), \
                 patch.object(app.linear, "team_context", new_callable=AsyncMock, return_value=TEAM):
                await app.update_issue_status({"issue": ISSUE, "stateId": "nonexistent"})

        self.runner.enqueue_adhoc(REPO, "Working task")
        with self.runner.db:
            self.runner.db.execute(
                "INSERT INTO tasks(issue,project,repo,title,plan,phase,updated) VALUES (?,'project',?,'T','P','working',1)",
                (ISSUE, REPO),
            )
        with self.assertRaises(BusyError):
            await app.update_issue_status({"issue": ISSUE, "stateId": "todo"})

    # --- Shared AGENTS.md ---

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
            await app.respond(second, "Second repository turn")
            second_text = respond.await_args.args[1]
        for text, own, other in ((first, "REPO-A-RULE", "REPO-B-RULE"),
                                 (second_text, "REPO-B-RULE", "REPO-A-RULE")):
            self.assertLess(text.index("GLOBAL-RULE"), text.index(own))
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
                path = root / "data/worker-config/opencode/AGENTS.md"
                self.assertTrue(path.is_symlink())
                self.assertEqual(path.resolve(), shared.resolve())
                self.assertIn("GLOBAL-LINK-RULE", path.read_text())
                self.assertFalse((root / "data/codex/AGENTS.md").exists())
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

    # --- Linear Snapshots, Refresh Loop, Create Issue, Set Plan, Buttons, Diagrams ---

    async def test_issue_snapshots_batch_projects_and_auto_queue_todos(self):
        app = self.app()
        repos = {REPO: [{"id": "alpha", "name": "Alpha", "icon": "🌱"}],
                 "owner/second": [{"id": "beta", "name": "Beta", "icon": ""}]}
        plan = "### Steps\n\n- **Read** the [Linear issue](https://linear.app/fixture/issue/KIAN-101)\n"
        alpha = {"identifier": ISSUE, "title": "Alpha issue", "url": "https://linear.app/fixture/issue/KIAN-101",
                 "state": {**self.linear.state, "position": 1}, "description": "## Proposed Plan\n\n" + plan,
                 "project": {"id": "alpha", "name": "Alpha"}, "priority": 2, "priorityLabel": "High",
                 "estimate": 3, "dueDate": "2026-11-23", "updatedAt": "2026-11-20T12:00:00Z",
                 "completedAt": None, "labels": {"nodes": [{"name": "Feature", "color": "#bb87fc"}]}}
        beta = {**alpha, "identifier": "KIAN-102", "title": "Beta issue", "description": "",
                "state": {"name": "Backlog", "type": "backlog", "position": 2},
                "project": {"id": "beta", "name": "Beta"}}
        first = {"issues": {"nodes": [alpha], "pageInfo": {"hasNextPage": True, "endCursor": "more"}}}
        second = {"issues": {"nodes": [beta], "pageInfo": {"hasNextPage": False}}}
        cutoff = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        with patch.object(app, "refresh_projects", return_value=repos), \
             patch.object(app.linear, "query") as query, \
             patch("symphony.__main__.done_cutoff", return_value=cutoff):
            query.side_effect = [first, second]
            await app.refresh_issue_snapshot()
            state = await app.state(0)
            revision = state["issueRevision"]
            self.assertEqual(self.runner.task(ISSUE)["phase"], "queued")
            with self.assertRaises(ValueError):
                self.runner.task("KIAN-102")  # Backlog issue is NOT auto-queued.

            # Moving KIAN-101 out of Todo removes the unstarted queued row cleanly.
            alpha["state"] = {"name": "Backlog", "type": "backlog", "position": 1}
            query.side_effect = [first, second]
            await app.refresh_issue_snapshot()
            with self.assertRaises(ValueError):
                self.runner.task(ISSUE)

            query.side_effect = RuntimeError("Linear is unavailable")
            await self.poll_once(app)
            stale = await app.state(0)
            self.assertEqual(stale["issueError"], "Linear is unavailable")
            self.assertNotEqual(stale["issueRevision"], revision)

    async def test_recent_done_and_canceled_use_24h_cutoff_and_expire_without_edits(self):
        app = self.app()
        repos = {REPO: [{"id": "alpha", "name": "Alpha", "icon": ""}]}
        cutoff = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

        def card(identifier, state, completed=None, canceled=None):
            return {"identifier": identifier, "title": identifier, "url": "https://linear.app/fixture/issue/" + identifier,
                    "state": {**state, "position": 5}, "description": "", "project": {"id": "alpha", "name": "Alpha"},
                    "priority": 3, "priorityLabel": "Normal", "estimate": None, "dueDate": None,
                    "updatedAt": "2026-11-20T12:00:00Z", "completedAt": completed, "canceledAt": canceled,
                    "labels": {"nodes": []}}

        nodes = [card("KIAN-100", {"name": "Unsure", "type": "backlog"}),
                 card("KIAN-101", {"name": "Backlog", "type": "backlog"}),
                 card("KIAN-102", {"name": "Todo", "type": "unstarted"}),
                 card("KIAN-103", {"name": "Done", "type": "completed"}, completed="2026-11-20T12:00:00Z"),
                 card("KIAN-104", {"name": "Done", "type": "completed"}, completed="2026-11-20T11:59:59Z"),
                 card("KIAN-105", {"name": "Canceled", "type": "canceled"}, canceled="2026-11-20T12:00:00Z"),
                 card("KIAN-106", {"name": "Duplicate", "type": "canceled"}, canceled="2026-11-20T12:00:00Z"),
                 card("KIAN-107", {"name": "Canceled", "type": "canceled"}, canceled="2026-11-20T11:59:59Z")]
        response = {"issues": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}
        with patch.object(app, "refresh_projects", return_value=repos), \
             patch.object(app.linear, "query", return_value=response), \
             patch("symphony.__main__.done_cutoff", return_value=cutoff):
            await app.refresh_issue_snapshot()
            first = await app.state(0)
            self.assertEqual([issue["identifier"] for issue in first["projects"][0]["issues"]],
                             ["KIAN-100", "KIAN-101", "KIAN-102", "KIAN-103", "KIAN-105", "KIAN-106"])
            later = datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc)
            with patch("symphony.__main__.done_cutoff", return_value=later):
                await app.refresh_issue_snapshot()
            aged = await app.state(0, first["issueRevision"])
            self.assertEqual([issue["identifier"] for issue in aged["projects"][0]["issues"]],
                             ["KIAN-100", "KIAN-101", "KIAN-102"])

    async def test_refresh_loop_coalesces_bursts_and_keeps_requests_during_fetch(self):
        app = self.app()
        refreshes, sleeps, waits = [], [], []

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

    async def test_project_events_invalidate_the_mapping_cache(self):
        app = self.app()
        app.fetched_projects = time.monotonic()
        app.repos = {"owner/old": [{"id": "old", "name": "Old"}]}
        repos = {"owner/new": [{"id": "new", "name": "New"}]}
        with patch.object(app.linear, "projects", new_callable=AsyncMock, return_value=(repos, {})) as projects:
            app.request_issue_refresh(projects=True)
            self.assertEqual(await app.refresh_projects(), repos)
            projects.assert_awaited_once()
            await app.refresh_projects()
            projects.assert_awaited_once()

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

    async def test_create_issue_validates_metadata_scale_labels_and_relations(self):
        app, mocks = self.create_issue_fixture()
        result = await app.tool("create_issue", self.create_issue_args(labels=["Bug", "UI", "Bug"]))
        self.assertEqual(result["identifier"], CREATED_ISSUE["identifier"])
        self.assertEqual(result["state"], "Backlog")
        self.assertEqual(mocks.create.await_args.kwargs["label_ids"], ["bug", "ui"])
        self.assertEqual(self.runner.rows("SELECT * FROM tasks"), [])

        for overrides, message in [
            ({"priority": True}, "priority"),
            ({"priority": 5}, "priority"),
            ({"estimate": 4}, "estimate must be one of"),
            ({"title": "   "}, "title"),
            ({"description": "   "}, "description"),
            ({"parent_id": "kian-1"}, "uppercase"),
            ({"labels": ["Missing"]}, "Unknown"),
        ]:
            with self.subTest(overrides=overrides), self.assertRaisesRegex(ValueError, message):
                await app.tool("create_issue", self.create_issue_args(**overrides))

        team = {**TEAM, "issueEstimationExtended": True, "issueEstimationAllowZero": True}
        self.assertEqual(estimate_scale(team), [0, 1, 2, 3, 5, 8, 13, 21])

        # Parent and blocker relations report partial failures without recreating the issue.
        relation = AsyncMock(side_effect=[{"id": "rel-1"}, RuntimeError("Linear rejected link.")])
        live_map = {"KIAN-150": "parent-issue", "KIAN-151": "blocker-one", "KIAN-152": "blocker-two"}
        app2, mocks2 = self.create_issue_fixture(
            live=AsyncMock(side_effect=lambda i: ({"id": live_map[i], "identifier": i}, REPO)),
            relation=relation,
        )
        linked = await app2.tool("create_issue", self.create_issue_args(
            parent_id="KIAN-150", blocked_by=["KIAN-151", "KIAN-152"],
        ))
        self.assertEqual(linked["blockedBy"]["succeeded"], ["KIAN-151"])
        self.assertEqual(linked["blockedBy"]["failed"][0]["identifier"], "KIAN-152")
        self.assertEqual(mocks2.create.await_count, 1)

    async def test_set_plan_emits_proposal_and_api_approve_moves_backlog_to_todo(self):
        app = self.app()
        live_issue = {
            "id": "lin-101", "identifier": ISSUE, "title": "Proposal card",
            "state": {"name": "Backlog", "type": "backlog"},
            "project": {"id": "project"}, "description": "Original body",
        }

        async def fake_update(_id, *, description):
            live_issue["description"] = description

        app.repos[REPO] = [{"id": "project", "name": "Fixture"}]
        with patch.object(app, "refresh_projects", new_callable=AsyncMock, return_value=app.repos), \
             patch.object(app.linear, "issue", new_callable=AsyncMock, return_value=live_issue), \
             patch.object(app.linear, "update", side_effect=fake_update):
            await app.tool("set_plan", {"issue_id": ISSUE, "plan": "### Steps\n\n1. Ship it."})

        expected_hash = hashlib.sha256(b"### Steps\n\n1. Ship it.").hexdigest()
        msgs = (await app.state(0, project="project"))["messages"]
        self.assertEqual(msgs[-1]["meta"], {
            "kind": "plan_proposal", "issue": ISSUE, "title": "Proposal card", "planHash": expected_hash,
        })
        live_issue["labels"] = {"nodes": []}
        self.assertEqual(issue_summary(live_issue)["planHash"], expected_hash)

        with patch.object(app, "refresh_projects", new_callable=AsyncMock, return_value=app.repos), \
             patch.object(app.linear, "issue", new_callable=AsyncMock, return_value=live_issue):
            with self.assertRaisesRegex(BusyError, "changed"):
                await app.approve_plan({"issue": ISSUE, "planHash": "0" * 64})

        with patch.object(app, "refresh_projects", new_callable=AsyncMock, return_value=app.repos), \
             patch.object(app.linear, "issue", new_callable=AsyncMock, return_value=live_issue), \
             patch.object(app.linear, "set_state", new_callable=AsyncMock) as set_state, \
             patch.object(app.coordinator, "respond", new_callable=AsyncMock) as coord_respond:
            result = await app.approve_plan({"issue": ISSUE, "planHash": expected_hash})
        self.assertEqual(result, {"approved": ISSUE, "repo": REPO})
        set_state.assert_awaited_once_with(ISSUE, "Todo", "unstarted")
        coord_respond.assert_not_awaited()

    async def test_repo_buttons_and_diagram_persist_and_scope_per_repo_state(self):
        app = self.app()
        second = "owner/second"
        app.repos = {REPO: [{"id": "alpha", "name": "Alpha"}], second: [{"id": "beta", "name": "Beta"}]}
        with patch.object(app, "refresh_projects", new_callable=AsyncMock, return_value=app.repos):
            with self.assertRaisesRegex(ValueError, "Unknown repository"):
                await app.tool("set_repo_button", {"repo": "owner/missing", "label": "X", "prompt": "Y", "mode": "attach"})
            await app.tool("set_repo_button", {
                "repo": REPO.upper(), "label": "Idle reload",
                "prompt": "Keep workers idle.", "mode": "attach",
            })
            await app.tool("render_diagram", {
                "repo": REPO.upper(), "title": "Flow", "mermaid": "flowchart LR\nA-->B",
            })
            state_a = await app.state(0, project="alpha")
            state_b = await app.state(0, project="beta")
            self.assertEqual([b["label"] for b in state_a["buttons"]], ["Idle reload"])
            self.assertEqual(state_b["buttons"], [])
            self.assertEqual(state_a["diagram"]["title"], "Flow")
            self.assertIsNone(state_b["diagram"])
            await app.tool("delete_repo_button", {"repo": REPO, "label": "Idle reload"})
            self.assertEqual((await app.state(0, project="alpha"))["buttons"], [])

    # --- Unified Sequential Queue + 2-Tier Flash -> Hydrogen Pipeline ---

    async def test_linear_todo_auto_queues_and_runs_flash_to_main_with_zero_agy_calls(self):
        self.seed_agy_token()
        app = self.app()
        await app.coordinator.connect()
        self.queue_linear_todo(ISSUE, plan="")  # No ## Proposed Plan required!
        with patch("symphony.runner.ci_runs", AsyncMock(return_value=[
                {"path": ".github/workflows/test.yml", "status": "completed", "conclusion": "success"}])):
            dispatched = await self.runner.dispatch_queued()
            self.assertEqual(dispatched, [ISSUE])
            await self.phase("done")

        self.assertEqual(self.linear.state["name"], "Done")
        self.assertEqual(self.git(self.remote, "show", "main:verified.txt"), "Tested")
        self.assertFalse(self.runner.worktree(ISSUE).exists())
        task = self.runner.task(ISSUE)
        self.assertEqual((task["tier"], task["escalated"], task["detail"]), ("flash", 0, "Done (Flash)."))
        # Zero agy subprocess calls occurred!
        self.assertFalse((self.root / "agy-calls.jsonl").exists())

    async def test_adhoc_run_and_enqueue_task_ship_without_touching_linear(self):
        app = self.app()
        app.repos = {REPO: [{"id": "project", "name": "Fixture"}]}
        await app.handle(REPO, "/run Write one verification file.", "/run Write one verification file.")
        self.assertEqual(self.runner.task("ADHOC-1")["phase"], "queued")
        with patch.object(app, "refresh_projects", new_callable=AsyncMock, return_value=app.repos):
            second = await app.tool("enqueue_task", {"repo": REPO, "prompt": "Second adhoc", "title": "Second"})
        self.assertEqual(second["issue"], "ADHOC-2")

        with patch("symphony.runner.ci_runs", AsyncMock(return_value=[
                {"path": ".github/workflows/test.yml", "status": "completed", "conclusion": "success"}])):
            # Sequential per-repo lock dispatches only ADHOC-1 first.
            self.assertEqual(await self.runner.dispatch_queued(), ["ADHOC-1"])
            self.assertEqual(await self.runner.dispatch_queued(), [])
            await self.phase("done", issue="ADHOC-1")
            # Now ADHOC-2 dispatches cleanly!
            self.assertEqual(await self.runner.dispatch_queued(), ["ADHOC-2"])
            await self.phase("done", issue="ADHOC-2")

        self.assertEqual(self.linear.state_calls, [])
        self.assertEqual(self.git(self.remote, "show", "main:verified.txt"), "Tested")

    async def test_optional_checks_allows_omitted_checks_sh(self):
        self.queue_linear_todo(ISSUE)
        with patch.dict(os.environ, {"FIXTURE_SKIP_CHECKS": "1"}), \
             patch("symphony.runner.ci_runs", AsyncMock(return_value=[
                 {"path": ".github/workflows/test.yml", "status": "completed", "conclusion": "success"}])):
            await self.runner.dispatch_queued()
            await self.phase("done")
        self.assertEqual(self.runner.task(ISSUE)["check_exit"], 0)
        self.assertEqual(self.linear.state["name"], "Done")

    async def test_worker_env_strips_secrets_sets_mcp_and_denies_websearch_and_push(self):
        shared = self.root / "shared-AGENTS.md"
        shared.write_text("# Shared\n\n- GLOBAL-WORKER-RULE\n")
        self.queue_linear_todo(ISSUE)
        with patch("symphony.runner.SHARED_AGENTS", shared), \
             patch.dict(os.environ, {
                 "GITHUB_TOKEN": "gh-secret",
                 "GH_TOKEN": "gh-cli-secret",
                 "LINEAR_API_KEY": "lin-secret",
                 "OPENAI_API_KEY": "oa-secret",
                 "OPENCODE_GO_API_KEY": "go-secret",
                 "LINEAR_WEBHOOK_SECRET": "webhook-secret",
                 "LINEAR_WEBHOOK_PATH_TOKEN": "webhook-path-token-0123456789abcdef0123",
                 "SYMPHONY_INTERNAL_TOKEN": "internal-secret",
             }), \
             patch("symphony.runner.ci_runs", AsyncMock(return_value=[
                 {"path": ".github/workflows/test.yml", "status": "completed", "conclusion": "success"}])):
            install_agent_instructions(self.root)
            await self.runner.dispatch_queued()
            await self.phase("done")

        record = json.loads((self.runner.artifacts(ISSUE) / "worker-envs.jsonl").read_text().splitlines()[0])
        for key in ("github", "gh", "linear", "openai", "api_key", "webhook", "webhook_path", "internal"):
            self.assertFalse(record[key], key)
        self.assertIn("GLOBAL-WORKER-RULE", record["agents"])
        permissions = record["config"]["permissions"]
        self.assertIn({"action": "websearch", "resource": "*", "effect": "deny"}, permissions)
        self.assertIn({"action": "shell", "resource": "git push *", "effect": "deny"}, permissions)
        self.assertIn({"action": "shell", "resource": "gh pr create *", "effect": "deny"}, permissions)
        self.assertIn({"action": "subagent", "resource": "*", "effect": "deny"}, permissions)

    async def test_auto_rebase_pushes_cleanly_and_parks_on_merge_conflict(self):
        # 1. Clean auto-rebase when origin/main advances on a non-conflicting file.
        self.runner.enqueue_adhoc(REPO, "Write verified.txt")
        original_publish = self.runner.publish

        async def advance_then_publish(issue):
            other = self.root / "other-clone"
            self.git(self.root, "clone", str(self.remote), str(other))
            self.git(other, "config", "user.name", "Other")
            self.git(other, "config", "user.email", "other@example.test")
            (other / "CHANGELOG.md").write_text("Unrelated upstream commit\n")
            self.git(other, "add", "CHANGELOG.md")
            self.git(other, "commit", "-m", "Upstream change")
            self.git(other, "push", "origin", "main")
            return await original_publish(issue)

        with patch.object(self.runner, "publish", side_effect=advance_then_publish), \
             patch("symphony.runner.ci_runs", AsyncMock(return_value=[
                 {"path": ".github/workflows/test.yml", "status": "completed", "conclusion": "success"}])):
            await self.runner.dispatch_queued()
            await self.phase("done", issue="ADHOC-1")
        self.assertEqual(self.git(self.remote, "show", "main:verified.txt"), "Tested")
        self.assertEqual(self.git(self.remote, "show", "main:CHANGELOG.md"), "Unrelated upstream commit")

        # 2. Merge conflict on verified.txt -> aborts rebase, pushes symphony/parked-ADHOC-2, parks.
        self.runner.enqueue_adhoc(REPO, "Conflicting update")

        async def conflict_then_publish(issue):
            other = self.root / "conflict-clone"
            self.git(self.root, "clone", str(self.remote), str(other))
            self.git(other, "config", "user.name", "Other")
            self.git(other, "config", "user.email", "other@example.test")
            (other / "verified.txt").write_text("Upstream conflict\n")
            self.git(other, "add", "verified.txt")
            self.git(other, "commit", "-m", "Conflicting commit")
            self.git(other, "push", "origin", "main")
            return await original_publish(issue)

        with patch.dict(os.environ, {"FIXTURE_CONTENT": "Worker conflict\n", "FIXTURE_SKIP_CHECKS": "1"}), \
             patch.object(self.runner, "publish", side_effect=conflict_then_publish):
            await self.runner.dispatch_queued()
            await self.phase("parked", issue="ADHOC-2")
        task = self.runner.task("ADHOC-2")
        self.assertIn("merge conflict", task["detail"])
        self.assertEqual(task["branch"], "symphony/parked-ADHOC-2")
        self.assertEqual(self.git(self.remote, "show", "symphony/parked-ADHOC-2:verified.txt"), "Worker conflict")

    async def test_flash_failures_cold_reset_and_auto_escalate_to_hydrogen(self):
        self.seed_agy_token()
        app = self.app()
        await app.coordinator.connect()

        for index, (env_patch, expected_trigger) in enumerate([
            ({"FIXTURE_CHECKS_FAIL": "1", "FIXTURE_DIRTY_FILE": "1"}, "check_failed"),
            ({"FIXTURE_BLOCK": "Need deeper reasoning", "FIXTURE_DIRTY_FILE": "1"}, "blocked"),
            ({"FIXTURE_FLASH_SLEEP": "5"}, "timeout"),
        ], start=1):
            with self.subTest(trigger=expected_trigger):
                task_id = f"ADHOC-{index}"
                self.runner.enqueue_adhoc(REPO, f"Task {index}")
                timeout_patch = patch("symphony.runner.FLASH_TIMEOUT_SECONDS", 0.25 if expected_trigger == "timeout" else 900)
                with timeout_patch, \
                     patch.dict(os.environ, env_patch), \
                     patch("symphony.runner.ci_runs", AsyncMock(return_value=[
                         {"path": ".github/workflows/test.yml", "status": "completed", "conclusion": "success"}])):
                    await self.runner.dispatch_queued()
                    await self.phase("done", issue=task_id)

                task = self.runner.task(task_id)
                self.assertEqual(task["tier"], "agy")
                self.assertEqual(task["escalated"], 1)
                self.assertEqual(task["trigger"], expected_trigger)
                self.assertEqual(task["detail"], "Done (Hydrogen).")
                self.assertEqual(self.git(self.remote, "show", "main:verified.txt"), "Escalated")
                esc_prompt = (self.runner.artifacts(task_id) / "escalation-prompt.txt").read_text()
                self.assertIn(f"Trigger: {expected_trigger}", esc_prompt)
                self.assertIn("flash-dirty.txt", esc_prompt)

        # Verify worker escalation agy call used ESCALATION_MODEL and stripped secrets.
        calls = [json.loads(line) for line in (self.root / "agy-calls.jsonl").read_text().splitlines()]
        self.assertEqual(len(calls), 3)
        for call_rec in calls:
            self.assertEqual(call_rec["args"][call_rec["args"].index("--model") + 1], ESCALATION_MODEL)
            self.assertFalse(call_rec["github"])
            self.assertFalse(call_rec["linear"])
            self.assertFalse(call_rec["internal"])

    async def test_both_tiers_failing_pushes_parked_branch_and_unblocks_next_queued_task(self):
        self.seed_agy_token()
        app = self.app()
        await app.coordinator.connect()
        self.runner.enqueue_adhoc(REPO, "Failing task")
        self.runner.enqueue_adhoc(REPO, "Next healthy task")

        with patch.dict(os.environ, {"FIXTURE_CHECKS_FAIL": "1", "FIXTURE_ESC_FAIL": "1"}):
            self.assertEqual(await self.runner.dispatch_queued(), ["ADHOC-1"])
            await self.phase("parked", issue="ADHOC-1")

        parked = self.runner.task("ADHOC-1")
        self.assertEqual(parked["phase"], "parked")
        self.assertEqual(parked["branch"], "symphony/parked-ADHOC-1")
        self.assertEqual(self.git(self.remote, "show", "symphony/parked-ADHOC-1:verified.txt"), "Escalated")
        self.assertFalse(self.runner.worktree("ADHOC-1").exists())

        # Parked task releases the repo lock so ADHOC-2 dispatches and ships cleanly!
        with patch("symphony.runner.ci_runs", AsyncMock(return_value=[
                {"path": ".github/workflows/test.yml", "status": "completed", "conclusion": "success"}])):
            self.assertEqual(await self.runner.dispatch_queued(), ["ADHOC-2"])
            await self.phase("done", issue="ADHOC-2")
        self.assertEqual(self.git(self.remote, "show", "main:verified.txt"), "Tested")

    async def test_ci_failure_after_push_parks_without_duplicate_wip_branch(self):
        self.runner.enqueue_adhoc(REPO, "CI failing task")
        with patch("symphony.runner.ci_runs", AsyncMock(return_value=[{
            "id": 99, "run_attempt": 1, "name": "test",
            "path": ".github/workflows/test.yml", "status": "completed",
            "conclusion": "failure", "html_url": "https://github.com/owner/project/actions/runs/99",
        }])), patch("symphony.runner.ci_jobs", AsyncMock(return_value=[])):
            await self.runner.dispatch_queued()
            await self.phase("parked", issue="ADHOC-1")
        parked = self.runner.task("ADHOC-1")
        self.assertEqual(parked["branch"], "")
        self.assertIn("ended with failure", parked["detail"])
        branches = self.git(self.remote, "branch", "--list", "symphony/parked-*")
        self.assertEqual(branches, "")

    async def test_quota_guard_and_provider_pause_prevent_unwanted_escalation(self):
        self.seed_agy_token()
        app = self.app()
        await app.coordinator.connect()

        # 1. Antigravity quota <= 20% left -> skips Hydrogen escalation and parks cleanly.
        app.coordinator.rate_limits = {"5h": {"left": 15, "resetsAt": None}, "weekly": {"left": 80, "resetsAt": None}}
        self.runner.enqueue_adhoc(REPO, "Low quota task")
        with patch.dict(os.environ, {"FIXTURE_CHECKS_FAIL": "1"}):
            await self.runner.dispatch_queued()
            await self.phase("parked", issue="ADHOC-1")
        self.assertIn("Auto-escalation skipped", self.runner.task("ADHOC-1")["detail"])
        self.assertEqual(self.runner.task("ADHOC-1")["escalated"], 0)
        self.assertFalse((self.root / "agy-calls.jsonl").exists())

        # 2. OpenCode Go 429 pauses Go and parks WITHOUT wasting Antigravity escalation.
        app.coordinator.rate_limits = {"5h": {"left": 90, "resetsAt": None}, "weekly": {"left": 90, "resetsAt": None}}
        self.runner.enqueue_adhoc(REPO, "Throttled task")
        with patch.dict(os.environ, {"FIXTURE_FLASH_429": "1"}):
            await self.runner.dispatch_queued()
            await self.phase("parked", issue="ADHOC-2")
        self.assertIn("OpenCode Go", self.runner.setting("paused"))
        self.assertEqual(self.runner.task("ADHOC-2")["escalated"], 0)
        self.assertFalse((self.root / "agy-calls.jsonl").exists())

    async def test_stats_and_retry_commands_report_and_requeue_tasks(self):
        app = self.app()
        with self.runner.db:
            self.runner.db.execute(
                """INSERT INTO tasks(issue,project,repo,title,plan,phase,tier,escalated,trigger,updated)
                   VALUES ('KIAN-1','p',?,'A','P','done','flash',0,'',1),
                          ('KIAN-2','p',?,'B','P','done','agy',1,'timeout',2),
                          ('ADHOC-1','',?,'C','P','parked','agy',1,'check_failed',3)""",
                (REPO, REPO, REPO),
            )
        await app.handle(REPO, "/stats", "/stats")
        stats_msg = self.runner.rows("SELECT text FROM chat WHERE repo=? ORDER BY id DESC LIMIT 1", (REPO,))[0]["text"]
        self.assertIn("1 passed on Flash", stats_msg)
        self.assertIn("1 auto-escalated & passed on Hydrogen", stats_msg)
        self.assertIn("1 parked", stats_msg)
        self.assertIn("timeout: 1", stats_msg)

        await app.handle(REPO, "/retry ADHOC-1", "/retry ADHOC-1")
        retried = self.runner.task("ADHOC-1")
        self.assertEqual((retried["phase"], retried["escalated"], retried["trigger"]), ("queued", 0, ""))

    async def test_push_without_matching_workflows_finishes_immediately(self):
        (self.repo / ".github/workflows/test.yml").write_text("on:\n  push:\n    paths: ['docs/**']\n")
        self.git(self.repo, "commit", "-am", "Filter tests to docs")
        self.git(self.repo, "push", "origin", "main")
        self.queue_linear_todo(ISSUE)
        with patch("symphony.runner.ci_runs") as ci:
            await self.runner.dispatch_queued()
            await self.phase("done")
            ci.assert_not_awaited()
        self.assertEqual(self.linear.state["name"], "Done")
        self.assertEqual(self.git(self.remote, "show", "main:verified.txt"), "Tested")

    async def test_workflow_run_image_workflow_does_not_block_push_ci(self):
        (self.repo / ".github/workflows/image.yml").write_text(
            "name: image\non:\n  workflow_run:\n    workflows: ['test']\n    types: [completed]\n"
        )
        self.git(self.repo, "add", ".github/workflows/image.yml")
        self.git(self.repo, "commit", "-m", "Add workflow_run image workflow")
        (self.repo / "verified.txt").write_text("Triggered\n")
        self.git(self.repo, "add", "verified.txt")
        self.git(self.repo, "commit", "-m", "Change verified file")
        base = self.git(self.repo, "rev-parse", "HEAD~1")
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(await ci_workflows(self.repo, base, head), [".github/workflows/test.yml"])

    async def test_draining_holds_new_dispatch_and_continue_clears_pause_only(self):
        app = self.app()
        self.queue_linear_todo(ISSUE)
        self.runner.save_setting("paused", "OpenCode Go: quota")
        self.runner.draining = True
        await app.handle(REPO, "/continue", "/continue")
        self.assertEqual(self.runner.setting("paused"), "")
        self.assertTrue(self.runner.draining)
        self.assertEqual(await self.runner.dispatch_queued(), [])
        self.assertEqual(self.runner.task(ISSUE)["attempts"], 0)

        await app.set_draining(False)
        pending = []
        with patch.object(self.runner, "job", side_effect=pending.append):
            self.assertEqual(await self.runner.dispatch_queued(), [ISSUE])
        for coroutine in pending:
            coroutine.close()
        self.assertEqual(self.runner.task(ISSUE)["phase"], "working")

    async def test_database_schema_migration_upgrades_legacy_phases_and_index(self):
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
                CREATE UNIQUE INDEX repository_owner
                    ON tasks(repo)
                    WHERE phase IN ('working', 'review', 'blocked', 'publishing', 'ci', 'finishing', 'finish_failed');
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO settings(key, value) VALUES
                    ('model', 'gpt-6.1-sol'),
                    ('effort', 'high'),
                    ('thread', 'old-codex-thread'),
                    ('tools_hash', 'old-hash'),
                    ('view', 'proj-keep');
                INSERT INTO tasks(issue,project,repo,title,plan,phase,updated) VALUES
                    ('KIAN-1','p','owner/a','A','P','approved',1),
                    ('KIAN-2','p','owner/a','B','P','review',2),
                    ('KIAN-3','p','owner/b','C','P','blocked',3);
            """)
        migrated = Runner(legacy, self.linear)
        self.addCleanup(migrated.db.close)
        self.assertEqual(migrated.task("KIAN-1")["phase"], "queued")
        self.assertEqual(migrated.task("KIAN-2")["phase"], "parked")
        self.assertEqual(migrated.task("KIAN-3")["phase"], "parked")
        self.assertEqual(migrated.setting("model"), "")
        self.assertEqual(migrated.setting("effort"), "")
        self.assertEqual(migrated.setting("thread"), "")
        self.assertEqual(migrated.setting("view"), "proj-keep")
        agy = Antigravity(migrated, None, home_dir=self.home)
        self.assertEqual(agy.current_model(), "gemini-4-neon-high")
        migrated.save_setting("model", "gpt-5.4")
        self.assertEqual(agy.current_model(), "gemini-4-neon-high")
        # Two parked tasks in the same repo are allowed now (repo_active_lock only guards active phases).
        with migrated.db:
            migrated.db.execute(
                """INSERT INTO tasks(issue,project,repo,title,plan,phase,updated)
                   VALUES ('KIAN-4','p','owner/a','D','P','parked',4)"""
            )

    async def test_opencode_error_reporting_and_dispatch_pause(self):
        self.queue_linear_todo(ISSUE)
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

    async def test_opencode_go_pvc_credentials_lifecycle_and_admission_guards(self):
        canary = "canary-opencode-secret-999"
        replacement = "canary-opencode-secret-next"
        auth = self.runner.opencode_auth_path()
        db_path = self.runner.opencode_db_path()
        auth.unlink()
        self.assertEqual(self.runner.opencode_go_status(), {"configured": False, "malformed": False})

        self.queue_linear_todo(ISSUE)
        self.assertEqual(await self.runner.dispatch_queued(), [])

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

        self.runner.save_setting("paused", "Existing pause reason")
        await app.update_config({"action": "set_opencode_key", "apiKey": replacement})
        self.assertEqual(self.runner.setting("paused"), "Existing pause reason")

        await app.update_config({"action": "clear_opencode_key"})
        with patch.dict(os.environ, {"OPENCODE_GO_API_KEY": "env-ignored-canary"}):
            restarted = Runner(self.root, self.linear)
            self.addCleanup(restarted.db.close)
            self.assertFalse(restarted.has_opencode_go_key())
            self.assertEqual(await restarted.dispatch_queued(), [])

        # Active worker blocks key mutation with BusyError.
        await app.update_config({"action": "set_opencode_key", "apiKey": canary})
        with self.runner.db:
            self.runner.db.execute("UPDATE tasks SET phase='working' WHERE issue=?", (ISSUE,))
        self.assertTrue(self.runner.opencode_go_busy())
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
            await self.runner.clear_opencode_go_key()
            self.assertFalse(self.runner.has_opencode_go_key())

    async def test_state_exposes_commit_or_static_asset_hash_version(self):
        sha = "a" * 40
        with patch.dict(os.environ, {"SYMPHONY_COMMIT": sha.upper()}):
            app = self.app()
        self.assertEqual((await app.state(0))["version"], sha)

        with TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SYMPHONY_COMMIT", None)
            web = Path(temp)
            for name in ("index.html", "app.js", "issue.js", "panes.js", "style.css", "favicon.ico"):
                (web / name).write_text(f"v1-{name}")
            with patch("symphony.__main__.WEB", web):
                app_v1 = self.app()
                (web / "app.js").write_text("v2-app.js")
                app_v2 = self.app()
        self.assertTrue(app_v1.version.startswith("web-"))
        self.assertNotEqual(app_v1.version, app_v2.version)

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
        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual(payload["content"], content)
        self.assertIn("&lt;script&gt;", payload["html"])
        self.assertNotIn("<script>", payload["html"])

        handler = self.get_handler("/api/agents/../../etc/passwd")
        with patch("symphony.__main__.shared_agents") as load:
            handler.do_GET()
        load.assert_not_called()
        handler.send_response.assert_called_once_with(404)

    def test_agents_http_reports_empty_missing_and_oversized_files(self):
        handler = self.get_handler("/api/agents")
        with patch("symphony.__main__.shared_agents", return_value=""):
            handler.do_GET()
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"content": "", "html": ""})

        handler = self.get_handler("/api/agents")
        with patch("symphony.__main__.shared_agents",
                   side_effect=RuntimeError("Global AGENTS.md is missing at /app/agents/AGENTS.md.")):
            handler.do_GET()
        handler.send_response.assert_called_once_with(503)

        with TemporaryDirectory() as temp:
            path = Path(temp) / "AGENTS.md"
            path.write_text("x" * (AGENTS_BYTES + 1))
            handler = self.get_handler("/api/agents")
            with patch("symphony.runner.SHARED_AGENTS", path):
                handler.do_GET()
        handler.send_response.assert_called_once_with(503)

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
                "antigravity": {"signedIn": False, "account": {}, "login": None, "busy": False},
            }),
        ))
        with patch.object(Handler, "run_async", side_effect=asyncio.run):
            handler.do_GET()
        handler.send_response.assert_called_once_with(200)
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
        self.assertEqual(post(b"x" * (CONFIG_BODY_LIMIT + 1))[0], 400)

        busy_app = SimpleNamespace(update_config=AsyncMock(side_effect=BusyError("Worker running.")))
        with patch.object(Handler, "run_async", side_effect=BusyError("Worker running.")):
            status, body = post(raw_ok, origin="https://symphony.tail:443", app=busy_app)
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body), {"error": "Worker running."})

    def test_approve_http_enforces_csrf_and_handles_conflicts(self):
        def post(raw, *, origin=None, host="symphony.tail:443", sym="1", app=None):
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
        self.assertEqual(post(raw, sym=None)[0], 403)
        self.assertEqual(post(raw, origin="https://evil.example")[0], 403)
        with patch.object(Handler, "run_async", return_value={"approved": ISSUE, "repo": REPO}):
            status, body = post(raw, origin="https://symphony.tail:443")
        self.assertEqual((status, body), (200, {"approved": ISSUE, "repo": REPO}))
        with patch.object(Handler, "run_async", side_effect=BusyError("Already closed.")):
            status, body = post(raw)
        self.assertEqual((status, body), (409, {"error": "Already closed."}))

    def test_mermaid_asset_route_serves_when_present_and_404_otherwise(self):
        with TemporaryDirectory() as temp:
            web = Path(temp)
            handler = self.get_handler("/mermaid.min.js")
            with patch("symphony.__main__.WEB", web):
                handler.do_GET()
            handler.send_response.assert_called_once_with(404)
            (web / "mermaid.min.js").write_text("globalThis.mermaid = {};")
            handler2 = self.get_handler("/mermaid.min.js")
            with patch("symphony.__main__.WEB", web):
                handler2.do_GET()
            handler2.send_response.assert_called_once_with(200)

    def test_static_ui_assets_never_cache(self):
        for route in ("/", "/app.js", "/issue.js", "/panes.js", "/style.css"):
            with self.subTest(route=route):
                handler = self.get_handler(route)
                handler.do_GET()
                handler.send_response.assert_called_once_with(200)
                headers = dict(call.args for call in handler.send_header.call_args_list)
                self.assertEqual(headers["Cache-Control"], "no-store")


class ServeStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_services_start_without_a_webhook_secret(self):
        servers = []

        class FakeServer:
            def __init__(self, address, handler):
                self.address = address
                self.handler = handler
                self.app = None
                servers.append(self)
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def serve_forever(self): pass
            def shutdown(self): pass

        class ImmediateEvent:
            def set(self): pass
            async def wait(self): return None

        class FakeApp:
            webhook_secret = ""
            def __init__(self):
                self.runner = SimpleNamespace(jobs=set(), db=SimpleNamespace(close=Mock()), job=lambda c: c.close())
                self.coordinator = SimpleNamespace(close=AsyncMock())
            async def pump(self): return None
            async def start(self): return None

        app = FakeApp()
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        env = {key: value for key, value in os.environ.items() if key != "LINEAR_WEBHOOK_SECRET"}
        env.update({"SYMPHONY_DATA": temp.name, "HOME": str(Path(temp.name) / "home"), "LINEAR_API_KEY": "fixture"})
        with patch.dict(os.environ, env, clear=True), \
             patch("symphony.__main__.ThreadingHTTPServer", FakeServer), \
             patch("symphony.__main__.Symphony", return_value=app), \
             patch("symphony.__main__.asyncio.Event", return_value=ImmediateEvent()):
            await serve()
        self.assertEqual([server.address for server in servers], [
            ("127.0.0.1", 8080), ("0.0.0.0", 8081), ("0.0.0.0", 8082),
        ])
        self.assertIs(servers[2].handler, WebhookHandler)

    async def test_serve_leaves_pvc_opencode_auth_untouched_when_env_canary_set(self):
        class FakeServer:
            def __init__(self, *_): self.app = None
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def serve_forever(self): pass
            def shutdown(self): pass

        class ImmediateEvent:
            def set(self): pass
            async def wait(self): return None

        fake_app = SimpleNamespace(
            runner=SimpleNamespace(jobs=set(), db=SimpleNamespace(close=Mock()), job=lambda c: c.close()),
            coordinator=SimpleNamespace(close=AsyncMock()),
            pump=AsyncMock(), start=AsyncMock(),
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
    token = "a_valid_opaque_webhook_path_token_0123456789"
    route = f"/webhooks/linear/{token}"

    def make_app(self, *, secret=None, token=None):
        from collections import deque
        from threading import Lock
        return SimpleNamespace(
            webhook_secret=self.secret if secret is None else secret,
            webhook_path_token=self.token if token is None else token,
            webhook_lock=Lock(),
            webhook_hits=deque(),
            webhook_seen={},
            request_issue_refresh=Mock(),
        )

    def payload(self, **changes):
        payload = {
            "action": "update", "type": "Issue",
            "data": {"id": "2174add1-f7c8-44e3-bbf3-2d60b5ea8bc9", "identifier": ISSUE},
            "webhookTimestamp": int(time.time() * 1000),
        }
        payload.update(changes)
        return payload

    def signature(self, raw):
        return hmac.new(self.secret.encode(), raw, hashlib.sha256).hexdigest()

    def send(self, raw, *, signature="missing", length=None, reader=None,
             path=None, method="POST", app=None, content_type="application/json",
             headers=None):
        app = app or self.make_app()
        handler = object.__new__(WebhookHandler)
        handler.path = self.route if path is None else path
        handler.headers = Message()
        if content_type is not None:
            handler.headers["Content-Type"] = content_type
        for name, value in (headers or {}).items():
            handler.headers[name] = value
        handler.headers["Content-Length"] = str(len(raw) if length is None else length)
        if signature != "missing":
            handler.headers["Linear-Signature"] = signature
        handler.rfile = reader or BytesIO(raw)
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        loop = SimpleNamespace(call_soon_threadsafe=Mock(side_effect=lambda cb, *a: cb(*a)))
        handler.server = SimpleNamespace(app=app, loop=loop)
        if method == "POST":
            handler.do_POST()
        else:
            handler.do_GET()
        return handler, handler.send_response.call_args.args[0], app

    def test_valid_issue_and_project_webhooks_signal_refresh_and_dedupe_replays(self):
        for kind, projects in (("Issue", False), ("Project", True)):
            with self.subTest(kind=kind):
                app = self.make_app()
                raw = json.dumps(self.payload(type=kind)).encode()
                sig = self.signature(raw)
                _, status, app = self.send(raw, signature=sig, app=app, headers={"Linear-Delivery": f"del-{kind}"})
                self.assertEqual(status, 200)
                app.request_issue_refresh.assert_called_once_with(projects)
                _, dup_status, app = self.send(raw, signature=sig, app=app, headers={"Linear-Delivery": f"del-{kind}"})
                self.assertEqual(dup_status, 200)
                app.request_issue_refresh.assert_called_once_with(projects)

    def test_rejected_webhooks_never_signal_refresh(self):
        now = int(time.time() * 1000)
        valid = json.dumps(self.payload()).encode()
        cases = [
            ("missing signature", valid, "missing", {}, 401),
            ("invalid signature", valid, "0" * 64, {}, 401),
            ("short signature", valid, "00", {}, 401),
            ("stale timestamp", json.dumps(self.payload(webhookTimestamp=now - 61_000)).encode(), None, {}, 400),
            ("future timestamp", json.dumps(self.payload(webhookTimestamp=now + 61_000)).encode(), None, {}, 400),
            ("malformed json", b"{", None, {}, 400),
            ("nan constant", b'{"type":"Issue","action":"update","data":{"id":"x"},"webhookTimestamp":NaN}', None, {}, 400),
            ("unsupported type", json.dumps(self.payload(type="Comment")).encode(), None, {}, 400),
            ("oversized body", b"{}", "missing", {"length": WEBHOOK_BODY_LIMIT + 1}, 413),
            ("wrong content type", valid, None, {"content_type": "text/plain"}, 415),
            ("transfer encoding", valid, None, {"headers": {"Transfer-Encoding": "chunked"}}, 400),
        ]
        for name, raw, signature, options, expected in cases:
            with self.subTest(name=name):
                sig = self.signature(raw) if signature is None else signature
                _, status, app = self.send(raw, signature=sig, **options)
                self.assertEqual(status, expected)
                app.request_issue_refresh.assert_not_called()

    def test_wrong_route_method_rate_limit_and_missing_config_are_rejected(self):
        raw = json.dumps(self.payload()).encode()
        for method, path, status in (
            ("POST", "/webhooks/linear", 404),
            ("POST", "/webhooks/linear/wrong", 404),
            ("POST", "/other", 404),
            ("GET", "/webhooks/linear", 404),
            ("GET", self.route, 405),
        ):
            with self.subTest(method=method, path=path):
                _, observed, app = self.send(raw, signature=self.signature(raw), path=path, method=method)
                self.assertEqual(observed, status)
                app.request_issue_refresh.assert_not_called()
        for secret, token in (("", self.token), (self.secret, ""), (self.secret, "too-short")):
            app = self.make_app(secret=secret, token=token)
            _, status, app = self.send(raw, signature=self.signature(raw), app=app)
            self.assertEqual(status, 404)
        app = self.make_app()
        app.webhook_hits.extend([time.monotonic()] * 60)
        _, limited_status, app = self.send(raw, signature=self.signature(raw), app=app)
        self.assertEqual(limited_status, 429)
        app.request_issue_refresh.assert_not_called()


class PaneTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for JavaScript checks.")
    def test_backlog_and_status_panes_partition_and_order_issues(self):
        rows = [
            {"identifier": "KIAN-11", "state": {"name": "Unsure"}, "priority": 3},
            {"identifier": "KIAN-1", "state": {"name": "Unsure"}, "priority": 1},
            {"identifier": "KIAN-10", "state": {"name": "Backlog"}, "priority": 4},
            {"identifier": "KIAN-2", "state": {"name": "Backlog"}, "priority": 1},
            {"identifier": "KIAN-3", "state": {"name": "Todo"}, "priority": 3},
            {"identifier": "KIAN-4", "state": {"name": "Todo"}, "priority": 2},
            {"identifier": "KIAN-5", "state": {"name": "In Progress"}, "priority": 2},
            {"identifier": "KIAN-6", "state": {"name": "Done"}, "priority": 0, "completedAt": "2026-11-20T12:00:00Z"},
            {"identifier": "KIAN-7", "state": {"name": "Done"}, "priority": 1, "completedAt": "2026-11-20T13:00:00Z"},
            {"identifier": "KIAN-8", "state": {"name": "Canceled"}, "priority": 1, "canceledAt": "2026-11-20T14:00:00Z"},
            {"identifier": "KIAN-9", "state": {"name": "Canceled"}, "priority": 2, "updatedAt": "2026-11-20T15:00:00Z"},
            {"identifier": "KIAN-12", "state": {"name": "Duplicate"}, "priority": 1, "canceledAt": "2026-11-20T16:00:00Z"},
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
                             text=True, capture_output=True, check=True, timeout=15)
        result = json.loads(run.stdout)
        self.assertEqual([group["name"] for group in result["panes"]["backlogGroups"]], ["Unsure", "Backlog"])
        self.assertEqual([group["name"] for group in result["panes"]["groups"]],
                         ["Todo", "In Progress", "Done", "Canceled", "Duplicate"])
        self.assertEqual(result["empty"], "")
        self.assertEqual(result["invalid"], "")
        self.assertEqual(result["valid"], "beta")
        self.assertEqual(result["none"], "")
        self.assertEqual(result["delays"], [1000, 2000, 200, 200, 200, 2000, 2000, 2000, 0, 0])

        order_script = """const fs = require("fs");
const vm = require("vm");
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const projects = [
  {id: "a", name: "Alpha", status: {name: "In Progress", type: "started", color: "#0f0"}},
  {id: "p1", name: "Paused One", status: {name: "Paused", type: "backlog", color: "#888"}},
  {id: "b", name: "Beta", status: {name: "Backlog", type: "backlog", color: "#fa0"}},
  {id: "c", name: "Gamma", status: null},
  {id: "p2", name: "Paused Two", status: {name: "Backlog", type: "paused", color: "#777"}},
];
const orderProjects = vm.runInContext("orderProjects", context);
const isPaused = vm.runInContext("isPausedProject", context);
process.stdout.write(JSON.stringify({
  pausedFlags: projects.map(isPaused),
  ordered: orderProjects(projects, ["b", "p2", "a"]),
}));
"""
        order_run = subprocess.run(["node", "-e", order_script, str(panes)],
                                   text=True, capture_output=True, check=True, timeout=15)
        order_res = json.loads(order_run.stdout)
        self.assertEqual(order_res["pausedFlags"], [False, True, False, False, True])
        self.assertEqual([p["id"] for p in order_res["ordered"]["active"]], ["b", "a", "c"])
        self.assertEqual([p["id"] for p in order_res["ordered"]["paused"]], ["p2", "p1"])

        root = Path(__file__).resolve().parents[1] / "symphony/web"
        html = (root / "index.html").read_text()
        self.assertLess(html.index('id="backlog"'), html.index('class="chat"'))
        self.assertLess(html.index('class="chat"'), html.index('id="status-issues"'))
        self.assertIn('id="coordinator-status"', html)
        self.assertIn('id="config-antigravity-signin"', html)
        self.assertIn('id="config-antigravity-code-form"', html)
        self.assertNotIn("codex", html.lower())
        app = (root / "app.js").read_text()
        self.assertIn("pollDelay(state, document.hidden)", app)
        self.assertIn('"/run "', app)
        self.assertIn('"/stats"', app)
        self.assertNotIn("Awaiting approval", app)
        self.assertNotIn("scrollIntoView", app)
        css = (root / "style.css").read_text()
        self.assertIn("html { height: 100%; overflow: clip;", css)
        self.assertIn("position: fixed;", css)


class SharedAgentsImageTests(unittest.TestCase):
    def test_image_ships_the_canonical_shared_file_and_agy(self):
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "symphony/Dockerfile").read_text()
        self.assertIn("COPY agents/AGENTS.md /app/agents/AGENTS.md", dockerfile)
        self.assertIn("agy --version", dockerfile)
        self.assertNotIn("@openai/codex", dockerfile)
        ignore = (root / ".dockerignore").read_text()
        self.assertIn("!agents/", ignore)
        self.assertIn("!agents/AGENTS.md", ignore)
        canonical = (root / "agents/AGENTS.md").read_text()
        self.assertIn("# Symphony global AGENTS.md", canonical)


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
            ("src/button.js", ["src/*.jsx?"], True),
            ("src/button.jsx", ["src/*.jsx?"], True),
            ("src/button.jst", ["src/*.jsx?"], False),
            ("release/v12.3", ["release/v[0-9]+.[0-9]+"], True),
            ("package[old].json", [r"package\[old\].json"], True),
            ("docs/draft.md", ["**/*.md", "!docs/**"], False),
        ]:
            with self.subTest(value=value, patterns=patterns):
                self.assertEqual(matches_filter(value, patterns), expected)
