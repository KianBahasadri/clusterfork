"""Exercise the MVP's approval and publication boundaries without provider calls."""

import asyncio
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from symphony.__main__ import Symphony
from symphony.codex import Codex
from symphony.runner import Runner
from symphony.services import matches_filter, proposed_plan, with_plan


ISSUE = "KIAN-101"
REPO = "owner/project"


class LinearFixture:
    def __init__(self):
        self.state = {"name": "Todo", "type": "unstarted"}

    async def issue(self, _):
        return {"project": {"id": "project"}, "state": self.state}

    async def set_state(self, issue, name, kind):
        if issue != ISSUE:
            raise AssertionError("Unexpected issue")
        self.state = {"name": name, "type": kind}


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

    async def test_chat_approval_routes_through_the_coordinator_tool(self):
        with patch.dict(os.environ, {"LINEAR_API_KEY": "fixture"}), \
             patch("symphony.__main__.Runner", return_value=self.runner):
            app = Symphony(self.root)
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
        self.assertEqual(self.linear.state["name"], "In Progress")
        self.assertEqual(self.runner.task(ISSUE)["plan"], "Write one verification file.")

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

    async def worker_done(self):
        await self.approve()
        self.assertEqual(self.linear.state["name"], "In Progress")
        result = self.runner.dispatch(ISSUE, REPO, "Never ship from the worker.")
        self.assertEqual(result["phase"], "working")
        with self.assertRaisesRegex(ValueError, "working"):
            self.runner.dispatch(ISSUE, REPO, "Duplicate")
        await self.phase("review")
        self.assertEqual(self.runner.task(ISSUE)["session"], "ses_fixture")
        self.assertEqual(self.runner.task(ISSUE)["check_exit"], 0)

    async def test_approval_verification_notes_push_and_ci(self):
        with self.assertRaisesRegex(ValueError, "No approved plan"):
            self.runner.dispatch(ISSUE, REPO, "")
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
                self.runner.dispatch("KIAN-102", REPO, "")
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
