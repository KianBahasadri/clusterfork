"""Exercise the MVP's approval and publication boundaries without provider calls."""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from symphony.__main__ import Symphony
from symphony.codex import Codex, TOOLS, TOOLS_HASH
from symphony.runner import Runner
from symphony.services import Linear, matches_filter, proposed_plan, with_plan


ISSUE = "KIAN-101"
REPO = "owner/project"


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
import sys
workspace = Path(os.environ["FIXTURE_ROOT"]) / "runs/KIAN-101"
assert "Write one verification file." in sys.argv[-1]
assert "Never ship from the worker." in sys.argv[-1]
assert sys.argv[sys.argv.index("--model") + 1] == "opencode-go/deepseek-v4.1-flash#max"
assert "GITHUB_TOKEN" not in os.environ
print(json.dumps({"type":"text","sessionID":"ses_fixture"}))
Path("verified.txt").write_text("Tested\\n")
(workspace / "checks.sh").write_text('test "$(cat verified.txt)" = Tested\\n')
(workspace / "notes.md").write_text("None\\n")
""")
        worker.chmod(0o755)
        context = patch.dict(os.environ, {"PATH": f"{fake_bin}:{os.environ['PATH']}",
                                         "GITHUB_TOKEN": "fixture", "FIXTURE_ROOT": str(self.root)})
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

    async def approve(self):
        await self.runner.approve(self.proposed_issue(), REPO)

    def proposed_issue(self):
        return {
            "identifier": ISSUE, "title": "Fixture change", "state": self.linear.state,
            "project": {"id": "project"}, "description": "## Proposed Plan\n\nWrite one verification file."
        }

    def app(self):
        with patch.dict(os.environ, {"LINEAR_API_KEY": "fixture"}), \
             patch("symphony.__main__.Runner", return_value=self.runner):
            return Symphony(self.root)

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
        self.assertEqual(list(app.messages), [(REPO, note, note)])
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
        alpha = {"identifier": ISSUE, "title": "Alpha issue", "url": "https://linear.app/fixture/issue/KIAN-101",
                 "state": {**self.linear.state, "position": 1}, "description": "## Proposed Plan\n\nTest alpha.",
                 "project": {"id": "alpha", "name": "Alpha"}, "priority": 2, "priorityLabel": "High",
                 "estimate": 3, "dueDate": "2026-11-23", "updatedAt": "2026-11-20T12:00:00Z",
                 "completedAt": None, "labels": {"nodes": [{"name": "Feature", "color": "#bb87fc"}]}}
        beta = {**alpha, "identifier": "KIAN-102", "title": "Beta issue", "project": {"id": "beta", "name": "Beta"}}
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
            self.assertNotIn("projects", await app.state(0, revision))
            self.assertEqual(query.await_count, 2)

            query.side_effect = [first, second]
            await app.refresh_issue_snapshot()
            self.assertNotIn("projects", await app.state(0, revision))

            query.side_effect = RuntimeError("Linear is unavailable")
            with patch("symphony.__main__.asyncio.sleep", side_effect=asyncio.CancelledError) as sleep:
                with self.assertRaises(asyncio.CancelledError):
                    await app.poll_issues()
                sleep.assert_awaited_once_with(60)
            stale = await app.state(0)
            self.assertEqual(stale["issueError"], "Linear is unavailable")
            self.assertEqual(stale["projects"], state["projects"])
            self.assertEqual(stale["issueRevision"], revision)

            beta["title"] = "Updated issue"
            query.side_effect = [first, second]
            with patch("symphony.__main__.asyncio.sleep", side_effect=asyncio.CancelledError) as sleep:
                with self.assertRaises(asyncio.CancelledError):
                    await app.poll_issues()
                sleep.assert_awaited_once_with(30)
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

    async def worker_done(self):
        await self.approve()
        self.assertEqual(self.linear.state["name"], "Todo")
        result = await self.runner.dispatch(ISSUE, REPO, "Never ship from the worker.")
        self.assertEqual(result["phase"], "working")
        self.assertEqual(self.linear.state["name"], "In Progress")
        with self.assertRaisesRegex(ValueError, "working"):
            await self.runner.dispatch(ISSUE, REPO, "Duplicate")
        await self.phase("review")
        self.assertEqual(self.runner.task(ISSUE)["session"], "ses_fixture")
        self.assertEqual(self.runner.task(ISSUE)["check_exit"], 0)

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
        with patch.object(self.linear, "set_state", side_effect=fail_after_update), \
             patch.object(self.runner, "job", side_effect=pending.append):
            await self.runner.dispatch(ISSUE, REPO, "")
        for coroutine in pending:
            coroutine.close()
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
        self.assertEqual(self.runner.read_notes(ISSUE), "None\n")

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

    async def test_pump_batches_queued_worker_events_in_one_turn(self):
        app = self.app()
        app.coordinator.account = "chatgpt"
        await self.approve()
        self.runner.change(ISSUE, phase="ci", event="Pushed to main. Waiting for GitHub Actions.")
        self.runner.change(ISSUE, phase="done", event="Done.")

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
        self.assertIn(f"- {ISSUE}: Done.", first_text)
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
process.stdout.write(JSON.stringify({
  panes: partition(rows), empty: select(projects, ""), invalid: select(projects, "missing"),
  valid: select(projects, "beta"), none: select([], ""),
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

        root = Path(__file__).resolve().parents[1] / "symphony/web"
        html = (root / "index.html").read_text()
        self.assertLess(html.index('id="backlog"'), html.index('class="chat"'))
        self.assertLess(html.index('class="chat"'), html.index('id="status-issues"'))
        app = (root / "app.js").read_text()
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
