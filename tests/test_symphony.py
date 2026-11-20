"""Exercise the MVP's approval and publication boundaries without provider calls."""

import asyncio
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from symphony.runner import Runner
from symphony.services import proposed_plan, with_plan


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
        (workflows / "test.yml").write_text("on:\n  push:\n    branches: [main]\n")
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
        await self.runner.approve({
            "identifier": ISSUE, "title": "Fixture change", "state": self.linear.state,
            "project": {"id": "project"}, "description": "## Proposed Plan\n\nWrite one verification file."
        }, REPO)

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
