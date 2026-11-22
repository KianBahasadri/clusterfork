"""Stage history, recorded execution, CI retention, and Execution tab behavior."""

import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlsplit

from symphony.__main__ import Handler, Symphony
from symphony.execution import bounded_file, execution_event, execution_payload, history_summary, redact
from symphony.runner import Runner
from symphony.services import Linear, ci_jobs


ISSUE = "KIAN-101"
REPO = "owner/project"
NOW = datetime(2026, 1, 1, 7, tzinfo=timezone.utc)
TYPES = {"Backlog": "backlog", "Todo": "unstarted", "In Progress": "started", "Done": "completed"}


def status(name):
    return None if name is None else {"name": name, "type": TYPES[name], "id": name.lower()}


def transition(identifier, at, before, after):
    return {"id": identifier, "createdAt": at, "fromState": status(before), "toState": status(after)}


def issue_row(state="Done", created="2026-01-01T00:00:00Z", **fields):
    return {"identifier": ISSUE, "title": "Fixture", "url": "https://linear.app/fixture/issue/KIAN-101",
            "state": status(state), "createdAt": created, "project": {"id": "project", "name": "Project"},
            **fields}


def insert_task(runner, **fields):
    values = {"issue": ISSUE, "project": "project", "repo": REPO, "title": "Fixture",
              "plan": "Plan", "phase": "review", "attempts": 1, "updated": 1}
    values.update(fields)
    columns = ", ".join(values)
    placeholders = ", ".join(":" + name for name in values)
    with runner.db:
        runner.db.execute(f"INSERT INTO tasks({columns}) VALUES ({placeholders})", values)


class FakeLinear:
    def __init__(self):
        self.issue_state = status("Todo")

    async def issue(self, identifier):
        return {"identifier": identifier, "title": "Fixture", "description": "## Proposed Plan\n\nPlan",
                "project": {"id": "project"}, "state": self.issue_state}

    async def set_state(self, identifier, name, kind):
        self.issue_state = {"name": name, "type": kind}


class HistorySummaryTests(unittest.TestCase):
    def test_repeated_out_of_order_transitions_accumulate_stages_and_open_done(self):
        entries = [
            transition("4", "2026-01-01T04:00:00Z", "Todo", "In Progress"),
            transition("2", "2026-01-01T02:00:00Z", "Todo", "In Progress"),
            transition("1", "2026-01-01T01:00:00Z", "Backlog", "Todo"),
            transition("5", "2026-01-01T05:00:00Z", "In Progress", "Done"),
            transition("3", "2026-01-01T03:00:00Z", "In Progress", "Todo"),
        ]
        summary = history_summary(issue_row(), {"entries": entries, "complete": True}, NOW)
        stages = {stage["name"]: stage for stage in summary["stages"]}
        self.assertEqual(summary["coverage"], "complete")
        self.assertEqual(stages["Backlog"]["seconds"], 3600)
        self.assertEqual(stages["Todo"]["seconds"], 7200)
        self.assertEqual(stages["In Progress"]["seconds"], 7200)
        self.assertEqual(stages["Done"]["seconds"], 7200)
        self.assertEqual(stages["Todo"]["visits"], 2)
        self.assertTrue(stages["Done"]["open"])
        self.assertEqual(summary["current"]["startedAt"], "2026-01-01T05:00:00Z")
        self.assertEqual(summary["current"]["source"], "history")
        self.assertEqual([entry["id"] for entry in summary["transitions"]], ["1", "2", "3", "4", "5"])

    def test_creation_establishes_initial_state_and_proven_zero_time(self):
        entries = [
            transition("1", "2026-01-01T00:00:00Z", "Backlog", "Todo"),
            {"id": "edit", "createdAt": "2026-01-01T00:30:00Z", "fromTitle": "Old", "toTitle": "New"},
        ]
        summary = history_summary(issue_row("Todo"), {"entries": entries, "complete": True},
                                  datetime(2026, 1, 1, 1, tzinfo=timezone.utc))
        stages = {stage["name"]: stage for stage in summary["stages"]}
        self.assertEqual(summary["coverage"], "complete")
        self.assertEqual(stages["Backlog"]["seconds"], 0)
        self.assertEqual(stages["Todo"]["seconds"], 3600)
        self.assertEqual(stages["Done"]["seconds"], 0)
        self.assertEqual(stages["In Progress"]["visits"], 0)
        self.assertEqual(summary["current"]["startedAt"], "2026-01-01T00:00:00Z")

    def test_empty_complete_history_uses_creation_for_current_stage(self):
        summary = history_summary(issue_row("Backlog"), {"entries": [], "complete": True},
                                  datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc))
        stages = {stage["name"]: stage for stage in summary["stages"]}
        self.assertEqual(summary["coverage"], "complete")
        self.assertEqual(stages["Backlog"]["seconds"], 60)
        self.assertEqual(summary["current"]["source"], "created")

    def test_missing_partial_and_inconsistent_histories_are_distinct(self):
        missing = history_summary(issue_row(), {"entries": [], "complete": False, "error": "Unavailable"},
                                  NOW)
        self.assertEqual(missing["coverage"], "unavailable")
        self.assertTrue(all(stage["seconds"] is None for stage in missing["stages"]))
        self.assertIsNone(missing["current"])

        partial = history_summary(issue_row(), {"entries": [
            transition("1", "2026-01-01T01:00:00Z", "Backlog", "Todo"),
        ], "complete": False}, NOW)
        self.assertEqual(partial["coverage"], "partial")
        self.assertTrue(all(stage["seconds"] is None for stage in partial["stages"]))
        self.assertIsNone(partial["current"])

        inconsistent = history_summary(issue_row(), {"entries": [
            transition("1", "2026-01-01T01:00:00Z", "Backlog", "Todo"),
            transition("2", "2026-01-01T02:00:00Z", "In Progress", "Done"),
        ], "complete": True}, NOW)
        self.assertEqual(inconsistent["coverage"], "inconsistent")
        self.assertIn("previous status", inconsistent["reason"])
        self.assertIsNone(inconsistent["current"])

    def test_complete_history_without_initial_status_keeps_the_confirmed_current_start(self):
        summary = history_summary(issue_row("In Progress"), {"entries": [
            transition("1", "2026-01-01T01:00:00Z", None, "In Progress"),
        ], "complete": True}, datetime(2026, 1, 1, 2, tzinfo=timezone.utc))
        self.assertEqual(summary["coverage"], "partial")
        self.assertTrue(all(stage["seconds"] is None for stage in summary["stages"]))
        self.assertEqual(summary["current"]["startedAt"], "2026-01-01T01:00:00Z")

    def test_malformed_status_transition_timestamp_never_claims_complete_history(self):
        summary = history_summary(issue_row("Done"), {"entries": [
            {"id": "bad", "createdAt": "not-a-time", "fromState": status("Todo"),
             "toState": status("Done")},
        ], "complete": True}, NOW)
        self.assertEqual(summary["coverage"], "inconsistent")
        self.assertIn("invalid timestamp", summary["reason"])
        self.assertTrue(all(stage["seconds"] is None for stage in summary["stages"]))


class HistorySnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runner = Mock()
        self.runner.rows.return_value = []
        self.runner.wake = Mock()
        self.runner.task.side_effect = ValueError("No approved plan.")
        self.runner.artifacts.return_value = self.root / "runs" / ISSUE
        with patch.dict(os.environ, {"LINEAR_API_KEY": "fixture"}), \
             patch("symphony.__main__.Runner", return_value=self.runner):
            self.app = Symphony(self.root)
        self.repos = {REPO: [{"id": "project", "name": "Project", "icon": ""}]}

    def row(self, updated="2026-01-01T01:00:00Z"):
        return {
            "id": "uuid", "identifier": ISSUE, "title": "Fixture",
            "url": "https://linear.app/fixture/issue/KIAN-101", "description": "",
            "priority": 3, "priorityLabel": "Normal", "estimate": 8, "dueDate": None,
            "updatedAt": updated, "completedAt": None, "createdAt": "2026-01-01T00:00:00Z",
            "state": status("In Progress"), "project": {"id": "project", "name": "Project"},
            "labels": {"nodes": []},
            "history": {"nodes": [
                transition("1", "2026-01-01T01:00:00Z", "Todo", "In Progress"),
            ], "pageInfo": {"hasNextPage": False, "endCursor": None}},
        }

    async def test_snapshot_cards_use_cached_history_per_issue_version(self):
        row = self.row()
        self.app.linear.issues = AsyncMock(return_value=[row])
        self.app.linear.issue_history = AsyncMock(return_value={
            "entries": row["history"]["nodes"], "complete": True, "error": "",
        })
        with patch.object(self.app, "refresh_projects", AsyncMock(return_value=self.repos)):
            await self.app.refresh_issue_snapshot()
            card = (await self.app.state(0))["projects"][0]["issues"][0]
            self.assertEqual(card["stage"]["startedAt"], "2026-01-01T01:00:00Z")
            self.assertEqual(card["stage"]["coverage"], "complete")
            self.assertEqual(card["estimate"], 8)
            revision = self.app.issue_revision

            await self.app.refresh_issue_snapshot()
            self.assertEqual(self.app.issue_revision, revision)
            self.app.linear.issue_history.assert_awaited_once()

            row["updatedAt"] = "2026-01-01T02:00:00Z"
            await self.app.refresh_issue_snapshot()
            self.assertEqual(self.app.linear.issue_history.await_count, 2)

    async def test_execution_endpoint_reuses_snapshot_history_and_keeps_unsafe_ids_out(self):
        row = self.row()
        self.app.linear.issues = AsyncMock(return_value=[row])
        self.app.linear.issue_history = AsyncMock(return_value={
            "entries": row["history"]["nodes"], "complete": True, "error": "",
        })
        with patch.object(self.app, "refresh_projects", AsyncMock(return_value=self.repos)):
            await self.app.refresh_issue_snapshot()
            detail = await self.app.execution(ISSUE)
        self.assertEqual(detail["stages"]["coverage"], "complete")
        self.assertEqual(self.app.linear.issue_history.await_count, 1)
        with self.assertRaises(ValueError):
            await self.app.execution("../../etc/passwd")

    async def test_linear_history_paginates_and_reports_a_bounded_partial_result(self):
        linear = Linear("fixture")
        first = {"nodes": [transition("1", "2026-01-01T01:00:00Z", "Backlog", "Todo")],
                 "pageInfo": {"hasNextPage": True, "endCursor": "next"}}
        second = {"nodes": [transition("2", "2026-01-01T02:00:00Z", "Todo", "Done")],
                  "pageInfo": {"hasNextPage": False, "endCursor": None}}
        with patch.object(linear, "query", AsyncMock(return_value={"issue": {"history": second}})) as query:
            result = await linear.issue_history("uuid", first)
        self.assertTrue(result["complete"])
        self.assertEqual([entry["id"] for entry in result["entries"]], ["1", "2"])
        self.assertEqual(query.await_args.kwargs["after"], "next")

        with patch("symphony.services.HISTORY_PAGES", 0), \
             patch.object(linear, "query", AsyncMock(return_value={"issue": {"history": second}})):
            limited = await linear.issue_history("uuid", first)
        self.assertFalse(limited["complete"])
        self.assertIn("exceeds", limited["error"])

    async def test_absent_malformed_or_stalled_linear_history_is_not_complete(self):
        linear = Linear("fixture")
        with patch.object(linear, "query", AsyncMock(return_value={"issue": None})):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                await linear.issue_history("uuid")
        with patch.object(linear, "query", AsyncMock(return_value={"issue": {"history": {"nodes": []}}})):
            with self.assertRaisesRegex(RuntimeError, "malformed"):
                await linear.issue_history("uuid")
        malformed = {"nodes": [
            {"id": "bad", "createdAt": "not-a-time", "fromState": status("Todo"),
             "toState": status("Done")},
        ], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        with self.assertRaisesRegex(RuntimeError, "malformed status transition"):
            await linear.issue_history("uuid", malformed)
        stalled = {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "same"}}
        with patch.object(linear, "query", AsyncMock(return_value={"issue": {"history": stalled}})):
            with self.assertRaisesRegex(RuntimeError, "did not advance"):
                await linear.issue_history("uuid", stalled)

    async def test_incomplete_history_is_retried_instead_of_cached_as_complete(self):
        row = self.row()
        self.app.linear.issue_history = AsyncMock(side_effect=[
            RuntimeError("Linear returned malformed issue history."),
            {"entries": row["history"]["nodes"], "complete": True, "error": ""},
        ])
        first = await self.app.history_for(row, fetch_missing=True)
        self.assertFalse(first["complete"])
        second = await self.app.history_for(row, fetch_missing=True)
        self.assertTrue(second["complete"])
        third = await self.app.history_for(row, fetch_missing=True)
        self.assertEqual(third, second)
        self.assertEqual(self.app.linear.issue_history.await_count, 2)

    async def test_coordinator_time_is_recorded_only_for_explicit_issue_events(self):
        self.app.context = AsyncMock(return_value=(self.root, ""))
        turn_count = 0

        async def respond(repo, text, cwd):
            nonlocal turn_count
            turn_count += 1
            if turn_count == 1:
                self.assertEqual(self.runner.record_execution.call_count, 2)
                self.assertEqual(self.runner.close_execution.call_count, 0)
            return "completed"

        self.runner.record_execution.side_effect = [1, 2]
        self.app.coordinator.respond = AsyncMock(side_effect=respond)
        await self.app.respond(REPO, "Worker event", ("KIAN-101", "KIAN-102"))
        recorded = self.runner.record_execution.call_args_list
        self.assertEqual({call.args[0] for call in recorded}, {"KIAN-101", "KIAN-102"})
        self.assertTrue(all(call.args[1] == "coordinator" for call in recorded))
        self.assertEqual(len({call.kwargs["started"] for call in recorded}), 1)
        self.assertEqual(self.runner.close_execution.call_count, 2)

        self.runner.record_execution.reset_mock()
        self.runner.close_execution.reset_mock()
        await self.app.respond(REPO, "Unattributed project chat")
        self.runner.record_execution.assert_not_called()
        self.runner.close_execution.assert_not_called()


class RunnerExecutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.linear = FakeLinear()
        self.runner = Runner(self.root, self.linear)
        self.addCleanup(self.close)

    def close(self):
        if self.runner.db:
            self.runner.db.close()
        for job in list(self.runner.jobs):
            job.cancel()

    def test_approval_attempt_retry_failure_and_records_survive_cleanup_and_reopen(self):
        async def run():
            issue = {"identifier": ISSUE, "title": "Fixture", "project": {"id": "project"},
                     "state": status("Todo"), "description": "## Proposed Plan\n\nPlan"}
            await self.runner.approve(issue, REPO)
            self.runner.job = Mock()
            await self.runner.dispatch(ISSUE, REPO, "")
            self.runner.job.call_args.args[0].close()
            self.assertEqual(self.runner.task(ISSUE)["attempts"], 1)
            self.assertEqual(self.runner.rows("SELECT kind FROM execution WHERE kind='attempt'"),
                             [{"kind": "attempt"}])
            self.runner.block(ISSUE, "Fixture failure")
            attempt = self.runner.rows("SELECT * FROM execution WHERE kind='attempt'")[0]
            self.assertIsNotNone(attempt["ended"])
            self.assertEqual(self.runner.rows("SELECT kind FROM execution WHERE kind='failure'"),
                             [{"kind": "failure"}])

            self.linear.issue_state = {"name": "In Progress", "type": "started"}
            with self.runner.db:
                self.runner.db.execute("""UPDATE tasks SET phase='blocked', attempts=1, detail='Paused'
                                          WHERE issue=?""", (ISSUE,))
            self.runner.job = Mock()
            await self.runner.dispatch(ISSUE, REPO, "", resume=True)
            self.runner.job.call_args.args[0].close()
            self.assertEqual(self.runner.task(ISSUE)["attempts"], 2)
            self.assertEqual(len(self.runner.rows("SELECT * FROM execution WHERE kind='attempt'")), 2)

            self.runner.worktree(ISSUE).mkdir(parents=True)
            with self.runner.db:
                self.runner.db.execute("UPDATE tasks SET phase='done' WHERE issue=?", (ISSUE,))
            with patch("symphony.runner.git", AsyncMock(return_value="")):
                await self.runner.cleanup(ISSUE)
            before = self.runner.rows("SELECT COUNT(*) AS value FROM execution")[0]["value"]
            self.runner.db.close()
            reopened = Runner(self.root, self.linear)
            try:
                self.assertEqual(reopened.rows("SELECT COUNT(*) AS value FROM execution")[0]["value"], before)
                self.assertEqual(reopened.rows("SELECT phase FROM tasks WHERE issue=?", (ISSUE,)),
                                 [{"phase": "done"}])
            finally:
                reopened.db.close()
            self.runner.db = None
        asyncio.run(run())

    def test_attempt_snapshots_survive_cleanup_and_reopen(self):
        async def run():
            insert_task(self.runner, phase="review", attempts=1)
            artifacts = self.runner.artifacts(ISSUE)
            artifacts.mkdir(parents=True)
            (artifacts / "notes.md").write_text("Old attempt notes\n")
            attempt_id = self.runner.record_execution(
                ISSUE, "attempt", started=1000, data={"attempt": 1, "notesReady": True})
            (artifacts / "notes.md").write_text("Captured before overwrite\n")
            self.runner.finish_attempt(
                ISSUE, attempt_id, detail="Attempt finished. Verification exited 0.")
            data = json.loads(self.runner.rows(
                "SELECT data FROM execution WHERE id=?", (attempt_id,))[0]["data"])
            self.assertEqual(data["notes"]["text"], "Captured before overwrite")
            self.runner.record_execution(
                ISSUE, "review", started=1010, ended=1020,
                detail="Review completed: NO ISSUES.",
                data={"attempt": 1, "attemptId": attempt_id, "model": "fixture/reviewer",
                      "verdict": "NO ISSUES", "findingCount": 0,
                      "findingCountComplete": True, "report": "VERDICT: NO ISSUES",
                      "reportTruncated": False, "available": True,
                      "detail": "Review completed: NO ISSUES."})
            self.runner.worktree(ISSUE).mkdir(parents=True)

            async def fake_git(cwd, *args):
                if args[:2] == ("worktree", "remove"):
                    self.runner.worktree(ISSUE).rmdir()
                return ""

            with self.runner.db:
                self.runner.db.execute("UPDATE tasks SET phase='done' WHERE issue=?", (ISSUE,))
            with patch("symphony.runner.git", AsyncMock(side_effect=fake_git)):
                await self.runner.cleanup(ISSUE)
            self.assertFalse(self.runner.worktree(ISSUE).exists())
            self.runner.db.close()
            reopened = Runner(self.root, self.linear)
            try:
                payload = execution_payload(ISSUE, issue_row("Done"), {
                    "entries": [], "complete": False, "error": "Unavailable",
                }, reopened, now=datetime(1970, 1, 1, 0, 18, tzinfo=timezone.utc))
                attempt, = payload["execution"]["worker"]["attempts"]
                self.assertEqual(attempt["notes"]["text"], "Captured before overwrite")
                self.assertEqual(attempt["review"]["verdict"], "NO ISSUES")
                self.assertEqual(attempt["review"]["findingCount"], 0)
            finally:
                reopened.db.close()
            self.runner.db = None
        asyncio.run(run())

    def test_restart_before_notes_initialization_keeps_previous_snapshot(self):
        async def run():
            insert_task(self.runner, phase="working", attempts=2, updated=1000)
            first = self.runner.record_execution(
                ISSUE, "attempt", started=900, ended=950,
                data={"attempt": 1, "notesReady": True,
                      "notes": {"available": True, "text": "Previous attempt notes",
                                "truncated": False, "detail": ""}})
            current = self.runner.record_execution(
                ISSUE, "attempt", started=1000,
                data={"attempt": 2, "notesReady": False})
            artifacts = self.runner.artifacts(ISSUE)
            artifacts.mkdir(parents=True)
            (artifacts / "notes.md").write_text("Previous attempt notes\n")
            self.runner.record_execution(ISSUE, "worker", started=1010, detail="Worker")
            with patch("symphony.runner.time.time", return_value=2000):
                self.runner.recover()
            previous = json.loads(self.runner.rows(
                "SELECT data FROM execution WHERE id=?", (first,))[0]["data"])
            current_data = json.loads(self.runner.rows(
                "SELECT data FROM execution WHERE id=?", (current,))[0]["data"])
            attempt = self.runner.rows(
                "SELECT ended FROM execution WHERE id=?", (current,))[0]
            self.assertEqual(previous["notes"]["text"], "Previous attempt notes")
            self.assertFalse(current_data["notes"]["available"])
            self.assertEqual(current_data["notes"]["text"], "")
            self.assertIn("did not initialize notes.md", current_data["notes"]["detail"])
            self.assertEqual(attempt["ended"], 1010)
            gap = self.runner.rows("SELECT * FROM execution WHERE kind='gap'")[0]
            self.assertEqual((gap["started"], gap["ended"]), (1010, 2000))
        asyncio.run(run())

    def test_restart_ends_open_intervals_at_the_last_observation_and_marks_the_gap(self):
        async def run():
            insert_task(self.runner, phase="working", updated=1000)
            self.runner.record_execution(ISSUE, "attempt", started=1000, detail="Attempt 1")
            self.runner.record_execution(ISSUE, "worker", started=1010, detail="Worker")
            self.runner.record_execution(ISSUE, "coordinator", started=1005, detail="Coordinator")
            with patch("symphony.runner.time.time", return_value=2000):
                self.runner.recover()
            self.assertEqual(self.runner.task(ISSUE)["phase"], "blocked")
            self.assertEqual(self.runner.rows("SELECT * FROM execution WHERE ended IS NULL"), [])
            gap = self.runner.rows("SELECT * FROM execution WHERE kind='gap'")[0]
            self.assertEqual((gap["started"], gap["ended"]), (1010, 2000))
            self.assertTrue(json.loads(gap["data"])["unknown"])
            attempt = self.runner.rows("SELECT * FROM execution WHERE kind='attempt'")[0]
            self.assertEqual(attempt["ended"], 1010)
            worker = self.runner.rows("SELECT * FROM execution WHERE kind='worker'")[0]
            self.assertEqual(worker["ended"], 1010)
            coordinator = self.runner.rows("SELECT * FROM execution WHERE kind='coordinator'")[0]
            self.assertEqual(coordinator["ended"], 1010)
            self.assertEqual(self.runner.rows("SELECT kind FROM execution WHERE kind='interruption'"),
                             [{"kind": "interruption"}])
        asyncio.run(run())


class CiRecordingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runner = Runner(self.root, FakeLinear())
        self.addCleanup(self.runner.db.close)
        insert_task(self.runner, phase="ci", attempts=1, updated=1, revision="rev1",
                    workflows=json.dumps([".github/workflows/test.yml", ".github/workflows/build.yml"]))

    @staticmethod
    def ci_run(run_id, attempt, path, started, completed, conclusion, status="completed"):
        return {"id": run_id, "run_attempt": attempt, "name": path.split(".")[0],
                "path": ".github/workflows/" + path, "status": status, "conclusion": conclusion,
                "html_url": f"https://github.com/owner/project/actions/runs/{run_id}",
                "created_at": started, "run_started_at": started, "updated_at": completed,
                "completed_at": completed}

    @staticmethod
    def jobs(run_id, attempt, conclusion):
        return [{"id": run_id * 10 + attempt, "name": "test", "status": "completed",
                 "conclusion": conclusion, "started_at": "2026-01-01T00:00:00Z",
                 "completed_at": "2026-01-01T01:00:00Z",
                 "html_url": f"https://github.example/jobs/{run_id}/{attempt}",
                 "steps": [{"name": "pytest", "conclusion": conclusion}]}]

    def test_observations_dedupe_retain_failed_attempts_and_measure_overlap(self):
        async def run():
            first = [
                self.ci_run(1, 1, "test.yml", "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z", "failure"),
                self.ci_run(2, 1, "build.yml", "2026-01-01T00:30:00Z", "2026-01-01T01:30:00Z", "success"),
            ]

            async def jobs(repo, run_id, attempt):
                conclusion = "failure" if run_id == 1 and attempt == 1 else "success"
                return self.jobs(run_id, attempt, conclusion)

            with patch("symphony.runner.ci_jobs", AsyncMock(side_effect=jobs)) as job_fetch:
                await self.runner.observe_ci(ISSUE, self.runner.task(ISSUE), first)
                await self.runner.observe_ci(ISSUE, self.runner.task(ISSUE), first)
                self.assertEqual(len(self.runner.rows("SELECT * FROM ci_runs")), 2)
                self.assertEqual(len(self.runner.rows("SELECT * FROM ci_jobs")), 2)
                self.assertEqual(job_fetch.await_count, 2)

                retry = [self.ci_run(1, 2, "test.yml", "2026-01-01T02:00:00Z",
                                  "2026-01-01T02:30:00Z", "success")]
                await self.runner.observe_ci(ISSUE, self.runner.task(ISSUE), retry)
                new_revision = [self.ci_run(3, 1, "build.yml", "2026-01-01T03:00:00Z",
                                         "2026-01-01T03:30:00Z", "failure")]
                await self.runner.observe_ci(ISSUE, self.runner.task(ISSUE), new_revision)

            self.assertEqual(len(self.runner.rows("SELECT * FROM ci_runs")), 4)
            failed = self.runner.rows("""SELECT * FROM ci_runs WHERE run_id=1 AND attempt=1""")
            self.assertEqual(failed[0]["conclusion"], "failure")
            detail = execution_payload(ISSUE, issue_row("Done"), {
                "entries": [], "complete": False, "error": "Unavailable",
            }, self.runner, now=datetime(2026, 1, 1, 4, tzinfo=timezone.utc))
            ci = detail["execution"]["ci"]
            self.assertEqual(ci["attemptCount"], 4)
            self.assertEqual(ci["runCount"], 3)
            self.assertEqual(ci["failureCount"], 2)
            self.assertEqual(ci["workflowSeconds"], 10800)
            self.assertEqual(ci["wallSeconds"], 9000)
            self.assertEqual(ci["overlapSeconds"], 1800)
            self.assertEqual(ci["coverage"], "recorded")
            self.assertEqual(failed[0]["diagnostic"], "test ended with failure.")
            retry = next(run for run in ci["runs"] if run["runId"] == 1 and run["attempt"] == 2)
            self.assertEqual(retry["attemptUrl"],
                             "https://github.com/owner/project/actions/runs/1/attempts/2")
            first_attempt = next(run for run in ci["runs"] if run["runId"] == 1 and run["attempt"] == 1)
            self.assertEqual(first_attempt["attemptUrl"],
                             "https://github.com/owner/project/actions/runs/1/attempts/1")
        asyncio.run(run())

    def test_completed_run_without_completed_at_does_not_grow(self):
        async def run():
            with self.runner.db:
                self.runner.db.execute("UPDATE tasks SET workflows=? WHERE issue=?",
                                       (json.dumps([".github/workflows/test.yml"]), ISSUE))
            current = self.ci_run(5, 1, "test.yml", "2026-01-01T00:00:00Z",
                                  "2026-01-01T02:00:00Z", "success")
            current["completed_at"] = None
            current["updated_at"] = "2026-01-01T02:00:00Z"
            with patch("symphony.runner.ci_jobs", AsyncMock(return_value=self.jobs(5, 1, "success"))):
                await self.runner.observe_ci(ISSUE, self.runner.task(ISSUE), [current])
            first = execution_payload(ISSUE, issue_row("Done"), {
                "entries": [], "complete": False, "error": "Unavailable",
            }, self.runner, now=datetime(2026, 1, 1, 2, tzinfo=timezone.utc))
            later = execution_payload(ISSUE, issue_row("Done"), {
                "entries": [], "complete": False, "error": "Unavailable",
            }, self.runner, now=datetime(2026, 1, 1, 3, tzinfo=timezone.utc))
            for detail in (first, later):
                ci = detail["execution"]["ci"]
                self.assertEqual(ci["workflowSeconds"], 7200)
                self.assertEqual(ci["wallSeconds"], 7200)
                self.assertEqual(ci["boundaryGaps"], 0)
                self.assertEqual(ci["coverage"], "recorded")
            run_row = first["execution"]["ci"]["runs"][0]
            self.assertEqual(run_row["endedAt"], "2026-01-01T02:00:00Z")
            self.assertTrue(run_row["boundaryKnown"])
        asyncio.run(run())

    def test_active_run_freezes_at_its_last_observation(self):
        async def run():
            with self.runner.db:
                self.runner.db.execute("UPDATE tasks SET workflows=? WHERE issue=?",
                                       (json.dumps([".github/workflows/test.yml"]), ISSUE))
            current = self.ci_run(6, 1, "test.yml", "2026-01-01T00:00:00Z",
                                  "2026-01-01T00:30:00Z", None, status="in_progress")
            current["completed_at"] = None
            await self.runner.observe_ci(ISSUE, self.runner.task(ISSUE), [current])
            observed = datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc).timestamp()
            with self.runner.db:
                self.runner.db.execute("UPDATE ci_runs SET observed=? WHERE run_id=6", (observed,))
            first = execution_payload(ISSUE, issue_row("Done"), {
                "entries": [], "complete": False, "error": "Unavailable",
            }, self.runner, now=datetime(2026, 1, 1, 2, tzinfo=timezone.utc))
            later = execution_payload(ISSUE, issue_row("Done"), {
                "entries": [], "complete": False, "error": "Unavailable",
            }, self.runner, now=datetime(2026, 1, 1, 3, tzinfo=timezone.utc))
            for detail in (first, later):
                ci = detail["execution"]["ci"]
                self.assertEqual(ci["workflowSeconds"], 1800)
                self.assertEqual(ci["boundaryGaps"], 1)
                self.assertTrue(ci["lowerBound"])
                self.assertEqual(ci["coverage"], "partial")
                self.assertIn("last recorded observation", ci["reason"])
            with self.runner.db:
                self.runner.db.execute("UPDATE tasks SET revision='rev2' WHERE issue=?", (ISSUE,))
            replaced = execution_payload(ISSUE, issue_row("Done"), {
                "entries": [], "complete": False, "error": "Unavailable",
            }, self.runner, now=datetime(2026, 1, 1, 4, tzinfo=timezone.utc))
            self.assertEqual(replaced["execution"]["ci"]["workflowSeconds"], 1800)
            self.assertEqual(replaced["execution"]["ci"]["coverage"], "unavailable")
            self.assertEqual(replaced["execution"]["ci"]["runs"][0]["endedAt"],
                             "2026-01-01T00:30:00Z")
        asyncio.run(run())

    def test_job_api_failures_are_partial_and_do_not_break_ci_completion(self):
        async def run():
            runner = self.runner
            with runner.db:
                runner.db.execute("UPDATE tasks SET workflows=? WHERE issue=?",
                                  (json.dumps([".github/workflows/test.yml"]), ISSUE))
            current = self.ci_run(4, 1, "test.yml", "2026-01-01T00:00:00Z",
                               "2026-01-01T01:00:00Z", "success")
            runner.cleanup = AsyncMock()
            with patch("symphony.runner.ci_runs", AsyncMock(return_value=[current])), \
                 patch("symphony.runner.ci_jobs", AsyncMock(side_effect=RuntimeError("API unavailable"))):
                await runner.watch_ci(ISSUE)
            self.assertEqual(runner.task(ISSUE)["phase"], "done")
            row = runner.rows("SELECT * FROM ci_runs WHERE run_id=4")[0]
            self.assertIn("job details are unavailable", row["diagnostic"])
            detail = execution_payload(ISSUE, issue_row("Done"), {
                "entries": [], "complete": False, "error": "Unavailable",
            }, runner, now=datetime(2026, 1, 1, 2, tzinfo=timezone.utc))
            ci = detail["execution"]["ci"]
            self.assertEqual(ci["coverage"], "partial")
            self.assertIsNone(ci["jobSeconds"])
            self.assertIsNone(ci["jobWallSeconds"])
            self.assertEqual(ci["jobCoverage"], "unavailable")
        asyncio.run(run())


class CiJobPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_jobs_paginate_and_excess_jobs_are_reported_partial(self):
        pages = [
            {"jobs": [{"id": 1}], "total_count": 2},
            {"jobs": [{"id": 2}], "total_count": 2},
        ]
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fixture"}), \
             patch("symphony.services.request_json", side_effect=pages) as request:
            jobs = await ci_jobs("owner/project", 10, 2)
        self.assertEqual([job["id"] for job in jobs], [1, 2])
        self.assertEqual(request.call_count, 2)
        first = parse_qs(urlsplit(request.call_args_list[0].args[0]).query)
        second = parse_qs(urlsplit(request.call_args_list[1].args[0]).query)
        self.assertEqual(first["page"], ["1"])
        self.assertEqual(second["page"], ["2"])

        huge = {"jobs": [{"id": index} for index in range(100)], "total_count": 501}
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fixture"}), \
             patch("symphony.services.request_json", return_value=huge) as request:
            with self.assertRaisesRegex(RuntimeError, "500-job"):
                await ci_jobs("owner/project", 11, 1)
        self.assertEqual(request.call_count, 5)


class ExecutionPayloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runner = Runner(self.root, FakeLinear())
        self.addCleanup(self.runner.db.close)

    def test_new_coordinator_record_does_not_make_old_worker_categories_zero(self):
        insert_task(self.runner, phase="done")
        self.runner.record_execution(ISSUE, "coordinator", started=1000, ended=1010,
                                     detail="Coordinator handled worker events.")
        payload = execution_payload(ISSUE, issue_row("Done"), {
            "entries": [], "complete": False, "error": "Unavailable",
        }, self.runner, now=datetime(1970, 1, 1, 0, 17, tzinfo=timezone.utc))
        execution = payload["execution"]
        self.assertEqual(execution["coverage"], "partial")
        self.assertEqual(execution["breakdown"]["coordinator"], 10)
        for category in ("startup", "worker", "verification", "publication",
                         "ci_workflow", "ci_wait", "ci_discovery"):
            self.assertIsNone(execution["breakdown"][category])
        self.assertTrue(execution["lowerBound"])
        self.assertIn("worker", execution["missingCategories"])

    def test_recorded_epoch_intervals_render_as_timestamps(self):
        event = execution_event({"id": 7, "kind": "worker", "started": 1000.0, "ended": 1010.0,
                                 "detail": "Worker", "data": "{}"})
        self.assertEqual(event["startedAt"], "1970-01-01T00:16:40Z")
        self.assertEqual(event["endedAt"], "1970-01-01T00:16:50Z")
        self.assertFalse(event["open"])

        insert_task(self.runner, phase="done")
        self.runner.record_execution(ISSUE, "startup", started=900, ended=1000, detail="Startup")
        self.runner.record_execution(ISSUE, "worker", started=1000, ended=1010, detail="Worker")
        payload = execution_payload(ISSUE, issue_row("Done"), {
            "entries": [], "complete": False, "error": "Unavailable",
        }, self.runner, now=datetime(1970, 1, 1, 0, 17, tzinfo=timezone.utc))
        self.assertEqual(payload["execution"]["breakdown"]["startup"], 100)
        self.assertEqual(payload["execution"]["breakdown"]["worker"], 10)

    def test_diagnostics_are_bounded_redacted_and_limited_to_the_issue_artifacts(self):
        insert_task(self.runner, phase="done", check_exit=0, detail="Done.")
        self.runner.chat("assistant", "private unrelated conversation")
        artifacts = self.runner.artifacts(ISSUE)
        artifacts.mkdir(parents=True)
        secret = "fixture-linear-secret"
        webhook = "fixture-webhook-secret"
        (artifacts / "worker.log").write_text("x" * 30000 + "\n" + secret + "\n" + webhook + "\n")
        (artifacts / "checks.sh").write_text("echo " + secret + "\n")
        self.runner.record_execution(ISSUE, "verification", started=1000, ended=1010,
                                     detail=secret, data={"command": secret})
        with patch.dict(os.environ, {"LINEAR_API_KEY": secret, "LINEAR_WEBHOOK_SECRET": webhook}):
            payload = execution_payload(ISSUE, issue_row("Done"), {
                "entries": [], "complete": False, "error": "Unavailable",
            }, self.runner, now=NOW)
        log = payload["execution"]["diagnostics"]["workerLog"]
        self.assertTrue(log["available"])
        self.assertTrue(log["truncated"])
        self.assertIn("[redacted]", log["text"])
        self.assertNotIn(secret, log["text"])
        self.assertNotIn(webhook, log["text"])
        self.assertNotIn(secret, json.dumps(payload))
        self.assertNotIn(webhook, json.dumps(payload))
        self.assertNotIn("private unrelated conversation", json.dumps(payload))
        self.assertEqual(payload["execution"]["diagnostics"]["checks"]["command"], "echo [redacted]")
        self.assertEqual(redact("token ghp_" + "a" * 30), "token [redacted]")

        link = self.root / "outside.log"
        link.symlink_to("/etc/passwd")
        self.assertEqual(bounded_file(link, 100), (None, False))
        with self.assertRaises(ValueError):
            execution_payload("../secrets", issue_row(), {"entries": [], "complete": False},
                             self.runner, now=NOW)

    def test_notes_boundary_secret_is_not_retained_or_served(self):
        secret = "fixture-linear-secret-boundary"
        insert_task(self.runner, phase="review", attempts=1)
        attempt_id = self.runner.record_execution(
            ISSUE, "attempt", started=1000, data={"attempt": 1, "notesReady": True})
        artifacts = self.runner.artifacts(ISSUE)
        artifacts.mkdir(parents=True)
        (artifacts / "notes.md").write_text("x" * 23990 + secret + "\n")
        with patch.dict(os.environ, {"LINEAR_API_KEY": secret}):
            self.runner.finish_attempt(ISSUE, attempt_id)
            raw = json.dumps(self.runner.rows(
                "SELECT data FROM execution WHERE id=?", (attempt_id,)))
            payload = execution_payload(ISSUE, issue_row("Done"), {
                "entries": [], "complete": False, "error": "Unavailable",
            }, self.runner, now=NOW)
        attempt, = payload["execution"]["worker"]["attempts"]
        self.assertFalse(attempt["notes"]["available"])
        self.assertEqual(attempt["notes"]["text"], "")
        self.assertIn("exceeded the retained size limit", attempt["notes"]["detail"])
        self.assertNotIn(secret, raw)
        self.assertNotIn(secret, json.dumps(payload))

    def test_known_secret_is_redacted_before_notes_and_review_persistence(self):
        secret = "fixture-linear-secret-normal"
        insert_task(self.runner, phase="review", attempts=1)
        attempt_id = self.runner.record_execution(
            ISSUE, "attempt", started=1000, data={"attempt": 1, "notesReady": True})
        artifacts = self.runner.artifacts(ISSUE)
        artifacts.mkdir(parents=True)
        (artifacts / "notes.md").write_text(f"None with {secret}\n")
        with patch.dict(os.environ, {"LINEAR_API_KEY": secret}):
            self.runner.finish_attempt(ISSUE, attempt_id)
            self.runner.record_execution(
                ISSUE, "review", started=1010, ended=1020,
                detail="Review unavailable.",
                data=self.runner.review_snapshot(
                    1, attempt_id, "fixture/reviewer", "VERDICT: UNAVAILABLE",
                    f"Review detail {secret} " + "x" * 3000,
                    verdict="UNAVAILABLE"))
            raw = json.dumps(self.runner.rows("SELECT data FROM execution"))
            payload = execution_payload(ISSUE, issue_row("Done"), {
                "entries": [], "complete": False, "error": "Unavailable",
            }, self.runner, now=NOW)
        attempt, = payload["execution"]["worker"]["attempts"]
        self.assertTrue(attempt["notes"]["available"])
        self.assertEqual(attempt["notes"]["text"], "None with [redacted]")
        self.assertIn("[redacted]", attempt["review"]["detail"])
        self.assertLessEqual(len(attempt["review"]["detail"]), 2000)
        self.assertNotIn(secret, raw)
        self.assertNotIn(secret, json.dumps(payload))
        self.assertIn("[redacted]", raw)

    def test_served_snapshot_details_are_redacted_and_bounded(self):
        secret = "fixture-linear-secret-detail"
        insert_task(self.runner, phase="review", attempts=1)
        attempt_id = self.runner.record_execution(
            ISSUE, "attempt", started=1000, ended=1010,
            data={"attempt": 1, "notes": {
                "available": False, "text": "", "truncated": False,
                "detail": secret + " " + "x" * 3000}})
        self.runner.record_execution(
            ISSUE, "review", started=1010, ended=1020,
            detail="Review unavailable.",
            data={"attempt": 1, "attemptId": attempt_id, "model": "fixture/reviewer",
                  "verdict": "UNAVAILABLE", "findingCount": None,
                  "findingCountComplete": False, "report": "VERDICT: UNAVAILABLE",
                  "reportTruncated": False, "available": True,
                  "detail": secret + " " + "y" * 3000})
        with patch.dict(os.environ, {"LINEAR_API_KEY": secret}):
            payload = execution_payload(ISSUE, issue_row("Done"), {
                "entries": [], "complete": False, "error": "Unavailable",
            }, self.runner, now=NOW)
        attempt, = payload["execution"]["worker"]["attempts"]
        self.assertNotIn(secret, attempt["notes"]["detail"])
        self.assertNotIn(secret, attempt["review"]["detail"])
        self.assertIn("[redacted]", attempt["notes"]["detail"])
        self.assertIn("[redacted]", attempt["review"]["detail"])
        self.assertLessEqual(len(attempt["notes"]["detail"]), 2000)
        self.assertLessEqual(len(attempt["review"]["detail"]), 2000)
        self.assertNotIn(secret, json.dumps(payload))

    def test_attempt_payload_aggregates_findings_and_counts_review_time_once(self):
        insert_task(self.runner, phase="done", attempts=3)
        first = self.runner.record_execution(
            ISSUE, "attempt", started=1000, ended=1010,
            data={"attempt": 1, "notes": {
                "available": True, "text": "First notes", "truncated": False, "detail": ""}})
        self.runner.record_execution(
            ISSUE, "worker", started=1000, ended=1005, data={"attemptId": first})
        self.runner.record_execution(
            ISSUE, "verification", started=1005, ended=1010, data={"attemptId": first})
        self.runner.record_execution(
            ISSUE, "review", started=1010, ended=1020, detail="Review completed: ISSUES.",
            data={"attempt": 1, "attemptId": first, "model": "fixture/reviewer",
                  "verdict": "ISSUES", "findingCount": 2, "findingCountComplete": True,
                  "report": "VERDICT: ISSUES\n- a.py:1 - One.\n- b.py:2 - Two.\n",
                  "reportTruncated": False, "available": True,
                  "detail": "Review completed: ISSUES."})
        second = self.runner.record_execution(
            ISSUE, "attempt", started=1020, ended=1030,
            data={"attempt": 2, "notes": {
                "available": False, "text": "", "truncated": False,
                "detail": "No notes.md snapshot was retained for this attempt."}})
        self.runner.record_execution(
            ISSUE, "review", started=1030, ended=1040, detail="Review unavailable.",
            data={"attempt": 2, "attemptId": second, "model": "fixture/reviewer",
                  "verdict": "UNAVAILABLE", "findingCount": None,
                  "findingCountComplete": False, "report": "VERDICT: UNAVAILABLE\n",
                  "reportTruncated": False, "available": True,
                  "detail": "Review unavailable."})
        payload = execution_payload(ISSUE, issue_row("Done"), {
            "entries": [], "complete": False, "error": "Unavailable",
        }, self.runner, now=datetime(1970, 1, 1, 0, 18, tzinfo=timezone.utc))
        execution = payload["execution"]
        worker = execution["worker"]
        self.assertEqual(worker["attemptCount"], 2)
        self.assertEqual(worker["coverage"], "recorded")
        self.assertEqual([attempt["number"] for attempt in worker["attempts"]], [1, 2])
        self.assertEqual(worker["attempts"][0]["notes"]["text"], "First notes")
        self.assertEqual(worker["attempts"][0]["review"]["model"], "fixture/reviewer")
        self.assertEqual(worker["attempts"][0]["review"]["findingCount"], 2)
        self.assertFalse(worker["attempts"][1]["notes"]["available"])
        self.assertEqual(worker["findings"], {
            "available": True, "complete": False, "count": 2,
            "reviewCount": 2, "unlinkedReviews": 0,
        })
        self.assertEqual(execution["breakdown"]["review"], 20)
        self.assertEqual(execution["breakdown"]["worker"], 5)
        self.assertEqual(execution["breakdown"]["verification"], 5)
        self.assertEqual(execution["measuredSeconds"], 30)
        self.assertEqual(execution["wallSeconds"], 30)
        self.assertEqual(execution["overlapSeconds"], 0)

    async def test_real_approval_reports_known_zero_attempts_despite_approval_event(self):
        issue = {**issue_row("Todo"), "description": "## Proposed Plan\n\nPlan"}
        await self.runner.approve(issue, REPO)
        self.assertTrue(self.runner.rows("SELECT 1 FROM execution WHERE kind='approval'"))
        payload = execution_payload(ISSUE, issue_row("Todo"), {
            "entries": [], "complete": False, "error": "Unavailable",
        }, self.runner, now=NOW)
        execution = payload["execution"]
        self.assertEqual(execution["worker"]["coverage"], "not_started")
        self.assertEqual(execution["worker"]["attemptCount"], 0)
        self.assertFalse(execution["worker"]["findings"]["available"])
        self.assertEqual(execution["breakdown"]["worker"], 0)
        self.assertEqual(execution["breakdown"]["review"], 0)

    def test_not_started_worker_has_zero_attempts_and_known_zero_review_time(self):
        insert_task(self.runner, phase="approved", attempts=0)
        payload = execution_payload(ISSUE, issue_row("Todo"), {
            "entries": [], "complete": False, "error": "Unavailable",
        }, self.runner, now=NOW)
        execution = payload["execution"]
        self.assertEqual(execution["worker"]["coverage"], "not_started")
        self.assertEqual(execution["worker"]["attemptCount"], 0)
        self.assertFalse(execution["worker"]["findings"]["available"])
        self.assertEqual(execution["breakdown"]["review"], 0)
        self.assertEqual(execution["breakdown"]["worker"], 0)
        self.assertNotIn("review", execution["missingCategories"])

    def test_historical_review_time_is_unknown_and_missing_from_totals(self):
        insert_task(self.runner, phase="done", attempts=1)
        self.runner.record_execution(ISSUE, "worker", started=1000, ended=1010)
        self.runner.record_execution(ISSUE, "verification", started=1010, ended=1020)
        payload = execution_payload(ISSUE, issue_row("Done"), {
            "entries": [], "complete": False, "error": "Unavailable",
        }, self.runner, now=datetime(1970, 1, 1, 0, 17, tzinfo=timezone.utc))
        execution = payload["execution"]
        self.assertIsNone(execution["breakdown"]["review"])
        self.assertIn("review", execution["missingCategories"])
        self.assertEqual(execution["coverage"], "partial")
        self.assertEqual(execution["measuredSeconds"], 20)
        self.assertEqual(execution["wallSeconds"], 20)
        self.assertEqual(execution["worker"]["attemptCount"], 0)
        self.assertFalse(execution["worker"]["findings"]["available"])

    def test_attempt_identity_keeps_counter_resets_separate_and_flags_unlinked_reviews(self):
        insert_task(self.runner, phase="done", attempts=1)
        first = self.runner.record_execution(
            ISSUE, "attempt", started=1000, ended=1010, data={"attempt": 1})
        second = self.runner.record_execution(
            ISSUE, "attempt", started=1010, ended=1020, data={"attempt": 1})
        self.runner.record_execution(
            ISSUE, "review", started=1010, ended=1011, detail="Review completed: NO ISSUES.",
            data={"attempt": 1, "attemptId": second, "model": "fixture/reviewer",
                  "verdict": "NO ISSUES", "findingCount": 0, "findingCountComplete": True,
                  "report": "VERDICT: NO ISSUES", "reportTruncated": False,
                  "available": True, "detail": "Review completed: NO ISSUES."})
        self.runner.record_execution(
            ISSUE, "review", started=1000, ended=1001, detail="Legacy review.",
            data={"attempt": 1})
        payload = execution_payload(ISSUE, issue_row("Done"), {
            "entries": [], "complete": False, "error": "Unavailable",
        }, self.runner, now=datetime(1970, 1, 1, 0, 18, tzinfo=timezone.utc))
        worker = payload["execution"]["worker"]
        self.assertEqual([attempt["counter"] for attempt in worker["attempts"]], [1, 1])
        self.assertEqual([attempt["number"] for attempt in worker["attempts"]], [1, 2])
        self.assertNotEqual(worker["attempts"][0]["id"], worker["attempts"][1]["id"])
        self.assertFalse(worker["attempts"][0]["review"]["recorded"])
        self.assertEqual(worker["attempts"][1]["review"]["verdict"], "NO ISSUES")
        self.assertEqual(worker["findings"]["count"], 0)
        self.assertTrue(worker["findings"]["available"])
        self.assertFalse(worker["findings"]["complete"])
        self.assertEqual(worker["findings"]["unlinkedReviews"], 1)

    def test_unavailable_execution_and_ci_are_explicit_instead_of_zero(self):
        payload = execution_payload(ISSUE, issue_row("Backlog"), {
            "entries": [], "complete": False, "error": "Unavailable",
        }, self.runner, now=NOW)
        self.assertEqual(payload["execution"]["coverage"], "unavailable")
        self.assertIn("No worker execution", payload["execution"]["reason"])
        self.assertEqual(payload["execution"]["ci"]["coverage"], "unavailable")
        self.assertEqual(payload["execution"]["ci"]["attemptCount"], 0)

        insert_task(self.runner, phase="done", revision="rev1", workflows=json.dumps(["test.yml"]))
        older = execution_payload(ISSUE, issue_row("Done"), {
            "entries": [], "complete": False, "error": "Unavailable",
        }, self.runner, now=NOW)
        self.assertEqual(older["execution"]["coverage"], "partial")
        self.assertIn("earlier activity", older["execution"]["reason"])
        self.assertIsNone(older["execution"]["breakdown"]["worker"])
        self.assertIsNone(older["execution"]["ci"]["jobSeconds"])


class ExecutionHttpTests(unittest.TestCase):
    def test_execution_http_is_scoped_and_never_cached(self):
        handler = object.__new__(Handler)
        handler.path = "/api/execution?id=KIAN-1077"
        handler.server = SimpleNamespace(app=SimpleNamespace(execution=Mock(return_value="coroutine")))
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.run_async = Mock(return_value={"stages": {}})
        handler.do_GET()
        handler.server.app.execution.assert_called_once_with("KIAN-1077")
        headers = dict(call.args for call in handler.send_header.call_args_list)
        self.assertEqual(headers["Cache-Control"], "no-store")
        handler.send_response.assert_called_once_with(200)


class WebExecutionTests(unittest.TestCase):
    @unittest.skipUnless(__import__("shutil").which("node"), "Node is required for JavaScript checks.")
    def test_execution_helpers_preserve_live_state_and_keyboard_behavior(self):
        root = Path(__file__).resolve().parents[1] / "symphony/web"
        script = r"""const fs = require("fs");
const vm = require("vm");
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
const read = (name) => vm.runInContext(name, context);
const format = read("formatDuration");
const seconds = read("formatSeconds");
const stage = read("stageDuration");
const nextTab = read("nextIssueTab");
const needsLoad = read("executionNeedsLoad");
const shouldRefresh = read("executionShouldRefresh");
const currentRequest = read("executionRequestCurrent");
const updatePill = read("updateStagePill");
const capture = read("captureExecutionView");
const restore = read("restoreExecutionView");
const attemptTotal = read("executionAttemptTotal");
const findingTotal = read("executionFindingTotal");

class FakeNode {
  constructor(tag = "div", className = "") {
    this.tag = tag;
    this.className = className;
    this.childNodes = [];
    this.parentNode = null;
    this.dataset = {};
    this.open = false;
    this.scrollTop = 0;
  }
  append(...nodes) {
    for (const node of nodes) {
      node.parentNode = this;
      this.childNodes.push(node);
    }
  }
  querySelectorAll(selector) {
    const found = [];
    const matches = (node) => selector === "details" ? node.tag === "details"
      : selector === ".execution-log" ? node.className.split(" ").includes("execution-log")
        : selector === "[data-view-key]" ? Boolean(node.dataset.viewKey) : false;
    const walk = (node) => {
      for (const child of node.childNodes) {
        if (matches(child)) found.push(child);
        walk(child);
      }
    };
    walk(this);
    return found;
  }
  contains(node) {
    while (node) {
      if (node === this) return true;
      node = node.parentNode;
    }
    return false;
  }
  closest(selector) {
    let node = this;
    while (node) {
      if (selector === "[data-view-key]" && node.dataset?.viewKey) return node;
      node = node.parentNode;
    }
    return null;
  }
  focus() { context.document.activeElement = this; }
}

const panel = new FakeNode();
const details = new FakeNode("details");
details.dataset.viewKey = "timeline";
details.open = true;
const log = new FakeNode("pre", "execution-log");
log.dataset.viewKey = "attempt:1:review";
log.scrollTop = 123;
const text = new FakeNode("#text");
log.append(text);
const link = new FakeNode("a");
link.dataset.viewKey = "timeline:link";
panel.append(details, log, link);
const scroller = new FakeNode();
scroller.scrollTop = 777;
const selection = {
  rangeCount: 1, anchorNode: text, focusNode: text, removed: false, added: null,
  getRangeAt: () => ({startContainer: text, startOffset: 2, endContainer: text, endOffset: 5}),
  removeAllRanges() { this.removed = true; },
  addRange(range) { this.added = range; },
};
context.document = {
  activeElement: link,
  createRange: () => ({
    start: null, end: null,
    setStart(node, offset) { this.start = [node, offset]; },
    setEnd(node, offset) { this.end = [node, offset]; },
  }),
};
const state = capture(panel, scroller, selection, link);
const nextPanel = new FakeNode();
const nextDetails = new FakeNode("details");
nextDetails.dataset.viewKey = "timeline";
const nextLog = new FakeNode("pre", "execution-log");
nextLog.dataset.viewKey = "attempt:1:review";
const nextText = new FakeNode("#text");
nextLog.append(nextText);
const newAttempt = new FakeNode("details");
newAttempt.dataset.viewKey = "attempt:2";
const nextLink = new FakeNode("a");
nextLink.dataset.viewKey = "timeline:link";
nextPanel.append(nextDetails, nextLog, newAttempt, nextLink);
restore(nextPanel, state, scroller, selection);

const pill = {
  dataset: {stageStart: "2026-01-01T00:00:00Z", stageName: "In Progress",
    stageCoverage: "partial", stageReason: "Old record"},
  lastChild: {textContent: ""}, title: "",
};
updatePill(pill, Date.parse("2026-01-01T02:00:00Z"));
const firstPill = {text: pill.lastChild.textContent, title: pill.title};
updatePill(pill, Date.parse("2026-01-01T03:00:00Z"));

process.stdout.write(JSON.stringify({
  zero: format(0), subMinute: format(59_000), minute: format(60_000),
  hour: format(3_660_000), day: format(90_000_000), missing: format(null),
  seconds: seconds(3600), stageMissing: stage("not-a-date"),
  stage: stage("2026-01-01T00:00:00Z", Date.parse("2026-01-01T02:30:00Z")),
  tabs: [nextTab(["issue", "execution"], "issue", "ArrowRight"),
    nextTab(["issue", "execution"], "issue", "ArrowLeft"),
    nextTab(["issue", "execution"], "execution", "Home"),
    nextTab(["issue", "execution"], "issue", "End"),
    nextTab(["issue", "execution"], "issue", "Enter")],
  load: [needsLoad("execution", {identifier: "KIAN-1"}), needsLoad("issue", {}),
    needsLoad("execution", null)],
  refresh: [shouldRefresh(true, "execution", {}), shouldRefresh(false, "execution", {}),
    shouldRefresh(true, "issue", {})],
  current: [currentRequest(2, 2, "execution", true, "KIAN-1", "KIAN-1"),
    currentRequest(1, 2, "execution", true, "KIAN-1", "KIAN-1"),
    currentRequest(2, 2, "execution", false, "KIAN-1", "KIAN-1"),
    currentRequest(2, 2, "execution", true, "KIAN-2", "KIAN-1")],
  pill: [firstPill, pill.lastChild.textContent, pill.title],
  attempts: [
    attemptTotal({coverage: "recorded", attemptCount: 3}, false),
    attemptTotal({coverage: "not_started", attemptCount: 0}, false),
    attemptTotal({coverage: "unavailable", attemptCount: 0}, false),
    attemptTotal({coverage: "recorded", attemptCount: 3}, true),
  ],
  findings: [
    findingTotal({available: true, complete: true, count: 0, reviewCount: 1}),
    findingTotal({available: true, complete: false, count: 2, reviewCount: 2}),
    findingTotal({available: true, complete: false, count: 0, reviewCount: 2}),
    findingTotal({available: false, complete: false, count: 0, reviewCount: 0}),
  ],
  restored: [nextDetails.open, nextLog.scrollTop, scroller.scrollTop,
    context.document.activeElement === nextLink, selection.removed,
    Boolean(selection.added), selection.added?.start?.[1], selection.added?.end?.[1],
    newAttempt.open],
}));
"""
        run = subprocess.run(["node", "-e", script, str(root / "panes.js")],
                             text=True, capture_output=True, check=True)
        result = json.loads(run.stdout)
        self.assertEqual({key: result[key] for key in (
            "zero", "subMinute", "minute", "hour", "day", "missing", "seconds",
            "stageMissing", "stage")}, {
            "zero": "0s", "subMinute": "59s", "minute": "1m", "hour": "1h 1m",
            "day": "1d 1h", "missing": "Unavailable", "seconds": "1h 0m",
            "stageMissing": "Unavailable", "stage": "2h 30m",
        })
        self.assertEqual(result["tabs"], ["execution", "execution", "issue", "execution", ""])
        self.assertEqual(result["load"], [True, False, False])
        self.assertEqual(result["refresh"], [True, False, False])
        self.assertEqual(result["current"], [True, False, False, False])
        self.assertEqual(result["pill"][0], {"text": "2h 0m",
            "title": "In Progress duration: 2h 0m · Old record"})
        self.assertEqual(result["pill"][1:], ["3h 0m",
            "In Progress duration: 3h 0m · Old record"])
        self.assertEqual(result["attempts"], [
            {"value": "3", "detail": "Retained attempt records"},
            {"value": "0", "detail": "No worker attempts have started."},
            {"value": "Not recorded", "detail": "No worker attempt records were captured."},
            {"value": "Unavailable", "detail": "Execution records are unavailable."},
        ])
        self.assertEqual(result["findings"], [
            {"value": "0", "detail": "Across 1 recorded review pass"},
            {"value": "At least 2", "detail": "Partial; some attempts or finding counts are unavailable."},
            {"value": "Partial", "detail": "Partial; some attempts or finding counts are unavailable."},
            {"value": "Unavailable", "detail": "No attempt-linked reviewer findings were recorded."},
        ])
        self.assertEqual(result["restored"], [True, 123, 777, True, True, True, 2, 5, False])

    @unittest.skipUnless(__import__("shutil").which("node"), "Node is required for JavaScript checks.")
    def test_execution_renderer_metrics_timeline_and_stable_view_state(self):
        root = Path(__file__).resolve().parents[1] / "symphony/web"
        script = r"""const fs = require("fs");
const vm = require("vm");

class FakeNode {
  constructor(tag = "div", className = "") {
    this.tag = tag;
    this.id = "";
    this.className = className;
    this.childNodes = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = {};
    this.textContent = "";
    this.hidden = false;
    this.open = false;
    this.scrollTop = 0;
  }
  append(...nodes) {
    for (const node of nodes) {
      const child = typeof node === "string" ? context.document.createTextNode(node) : node;
      child.parentNode = this;
      this.childNodes.push(child);
    }
  }
  replaceChildren(...nodes) {
    this.childNodes = [];
    this.append(...nodes);
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.id = String(value);
  }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener() {}
  contains(node) {
    while (node) {
      if (node === this) return true;
      node = node.parentNode;
    }
    return false;
  }
  closest(selector) {
    let node = this;
    while (node) {
      if (selector === "[data-view-key]" && node.dataset?.viewKey) return node;
      node = node.parentNode;
    }
    return null;
  }
  querySelectorAll(selector) {
    const found = [];
    const matches = (node) => selector === "details" ? node.tag === "details"
      : selector === ".execution-log" ? node.className.split(" ").includes("execution-log")
        : selector === "[data-view-key]" ? Boolean(node.dataset.viewKey) : false;
    const walk = (node) => {
      for (const child of node.childNodes) {
        if (matches(child)) found.push(child);
        walk(child);
      }
    };
    walk(this);
    return found;
  }
  focus() { context.document.activeElement = this; }
}

const ids = {};
const context = vm.createContext({console, URL, URLSearchParams, setTimeout() {},
  clearInterval() {}, setInterval() {}, getSelection() { return null; }});
context.document = {
  activeElement: null,
  getElementById(id) { return ids[id] || (ids[id] = new FakeNode()); },
  createElement(tag) { return new FakeNode(tag); },
  createElementNS(namespace, tag) { return new FakeNode(tag); },
  createTextNode(text) { const node = new FakeNode("#text"); node.textContent = text; return node; },
  createRange() {
    return {start: null, end: null,
      setStart(node, offset) { this.start = [node, offset]; },
      setEnd(node, offset) { this.end = [node, offset]; }};
  },
};
context.location = {origin: "http://localhost"};
context.element = (tag, text, className) => {
  const node = context.document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
};
context.byId = (id) => context.document.getElementById(id);
context.issueCache = new Map();
context.repos = new FakeNode();
context.api = async () => { throw new Error("unexpected api call"); };
context.poll = () => {};
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);
vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), context);
const read = (name) => vm.runInContext(name, context);
const render = read("renderExecution");
const capture = read("captureExecutionView");
const restore = read("restoreExecutionView");

const all = (root) => {
  const nodes = [];
  const walk = (node) => {
    nodes.push(node);
    for (const child of node.childNodes) walk(child);
  };
  walk(root);
  return nodes;
};
const byKey = (panel, key) => all(panel).find((node) => node.dataset.viewKey === key);
const metric = (panel, label) => all(panel).find((node) =>
  node.className.split(" ").includes("execution-metric")
  && node.childNodes[0]?.textContent === label);
const metricValue = (panel, label) => {
  const item = metric(panel, label);
  return item ? {value: item.childNodes[1].textContent, detail: item.childNodes[2].textContent} : null;
};
const logText = (details) => all(details).find((node) =>
  node.className.split(" ").includes("execution-log"))?.textContent || "";

const stages = {coverage: "complete", reason: "", current: null, transitions: [],
  stages: ["Backlog", "Todo", "In Progress", "Done"].map((name) => ({
    name, seconds: 0, visits: 0, open: false}))};
const diagnostics = {
  verification: {available: true, exit: 0, detail: "Passed."},
  checks: {available: false, command: "", truncated: false, detail: "No command."},
  workerLog: {available: false, text: "", truncated: false, detail: "No log."},
};
const ci = {coverage: "recorded", reason: "", runs: [], runCount: 0, attemptCount: 0,
  jobCount: 0, failureCount: 0, expectedWorkflows: [], missingWorkflows: [],
  currentRevision: "", boundaryGaps: 0, workflowSeconds: 0, wallSeconds: 0,
  jobSeconds: null, jobWallSeconds: null, jobLowerBound: false, jobCoverage: "unavailable",
  waitingSeconds: 0, discoverySeconds: 0, overlapSeconds: 0, lowerBound: false};

function attempt(id, number, notes, review) {
  return {id, number, counter: number, startedAt: "2026-01-01T00:00:00Z",
    endedAt: "2026-01-01T00:10:00Z", open: false,
    notes: {available: true, text: notes, truncated: false, detail: ""},
    review: {recorded: true, available: true, verdict: "ISSUES", findingCount: 2,
      findingCountComplete: true, model: "fixture/reviewer", text: review,
      truncated: false, detail: "Review completed: ISSUES.",
      startedAt: "2026-01-01T00:05:00Z", endedAt: "2026-01-01T00:10:00Z"}};
}
function payload(attempts, events, findings) {
  return {issue: {identifier: "KIAN-1", title: "Fixture", url: "", state: {name: "In Progress", type: "started"}, project: "Fixture"},
    generatedAt: "2026-01-01T00:10:00Z", stages,
    execution: {
      coverage: "recorded", reason: "", events,
      breakdown: {startup: 1, worker: 2, verification: 3, review: 4, coordinator: 0,
        publication: 0, ci_workflow: 0, ci_wait: 0, ci_discovery: 0},
      worker: {attemptCount: attempts.length, coverage: attempts.length ? "recorded" : "unavailable",
        attempts, findings},
      missingCategories: [], lowerBound: false, measuredSeconds: 10, wallSeconds: 10,
      overlapSeconds: 0, ci, diagnostics,
    }};
}

const rawReview = "VERDICT: ISSUES\n<img src=x onerror=alert(1)>";
const rawNotes = "Notes <script>alert(1)</script>";
const first = payload(
  [attempt("10", 1, rawNotes, rawReview)],
  [{id: 1, kind: "review", startedAt: "2026-01-01T00:05:00Z",
    endedAt: "2026-01-01T00:10:00Z", detail: "Review completed.", open: false, data: {}}],
  {available: true, complete: true, count: 2, reviewCount: 1, unlinkedReviews: 0});
const panel = context.document.createElement("div");
render(first, panel);
const timeline = byKey(panel, "timeline");
const reviewDetails = byKey(panel, "attempt:10:review");
const notesDetails = byKey(panel, "attempt:10:notes");
const initial = {
  timelineSummary: timeline.childNodes[0].textContent,
  timelineOpen: timeline.open,
  timelineEvents: all(timeline).filter((node) => node.className.split(" ").includes("execution-event")).length,
  reviewText: logText(reviewDetails),
  notesText: logText(notesDetails),
  injected: all(panel).some((node) => ["img", "script"].includes(node.tag)),
  ciWorkflow: metricValue(panel, "CI workflow"),
  attempts: metricValue(panel, "Worker attempts"),
  findings: metricValue(panel, "Review findings"),
};

const attemptDetails = byKey(panel, "attempt:10");
attemptDetails.open = true;
reviewDetails.open = true;
timeline.open = true;
const reviewLog = all(reviewDetails).find((node) =>
  node.className.split(" ").includes("execution-log"));
reviewLog.scrollTop = 88;
const scroller = context.document.createElement("div");
scroller.scrollTop = 321;
reviewLog.focus();
const selection = {
  rangeCount: 1, anchorNode: reviewLog, focusNode: reviewLog, removed: false, added: null,
  getRangeAt: () => ({startContainer: reviewLog, startOffset: 0,
    endContainer: reviewLog, endOffset: 3}),
  removeAllRanges() { this.removed = true; },
  addRange(range) { this.added = range; },
};
const view = capture(panel, scroller, selection, reviewLog);
const second = payload(
  [attempt("10", 1, rawNotes, rawReview + "\nUpdated review."),
   attempt("11", 2, "Second notes", "VERDICT: NO ISSUES")],
  [{id: 1, kind: "review", startedAt: "2026-01-01T00:05:00Z",
    endedAt: "2026-01-01T00:10:00Z", detail: "Review completed.", open: false, data: {}},
   {id: 2, kind: "attempt", startedAt: "2026-01-01T00:10:00Z",
    endedAt: null, detail: "Attempt 2 started.", open: true, data: {}}],
  {available: true, complete: false, count: 2, reviewCount: 2, unlinkedReviews: 0});
render(second, panel);
restore(panel, view, scroller, selection);
const newTimeline = byKey(panel, "timeline");
const newReview = byKey(panel, "attempt:10:review");
const newReviewLog = all(newReview).find((node) =>
  node.className.split(" ").includes("execution-log"));
const refreshed = {
  timelineSummary: newTimeline.childNodes[0].textContent,
  timelineOpen: newTimeline.open,
  firstAttemptOpen: byKey(panel, "attempt:10").open,
  secondAttemptOpen: byKey(panel, "attempt:11").open,
  logScroll: newReviewLog.scrollTop,
  scroller: scroller.scrollTop,
  focus: context.document.activeElement === newReviewLog,
  selection: Boolean(selection.added)
    && selection.added.start?.[0] === newReviewLog
    && selection.added.end?.[1] === 3,
};

const emptyPanel = context.document.createElement("div");
const empty = payload([], [], {available: false, complete: false, count: 0,
  reviewCount: 0, unlinkedReviews: 0});
empty.execution.worker.coverage = "not_started";
empty.execution.worker.findings = {available: false, complete: false, count: 0,
  reviewCount: 0, unlinkedReviews: 0};
render(empty, emptyPanel);
const emptyTimeline = byKey(emptyPanel, "timeline");
const missing = {
  timelineSummary: emptyTimeline.childNodes[0].textContent,
  timelineOpen: emptyTimeline.open,
  timelineText: all(emptyTimeline).find((node) => node.tag === "p")?.textContent,
  attempts: metricValue(emptyPanel, "Worker attempts"),
  findings: metricValue(emptyPanel, "Review findings"),
};
const shapeFirst = payload(
  [attempt("30", 1, "First notes", "VERDICT: ISSUES\nfirst"),
   attempt("31", 2, "Second notes", "VERDICT: NO ISSUES\nsecond")],
  [{id: 4, kind: "review", startedAt: "2026-01-01T00:05:00Z",
    endedAt: "2026-01-01T00:10:00Z", detail: "Review completed.", open: false, data: {}}],
  {available: true, complete: true, count: 2, reviewCount: 2, unlinkedReviews: 0});
const shapePanel = context.document.createElement("div");
render(shapeFirst, shapePanel);
const firstReviewLog = byKey(shapePanel, "attempt:30:review:log");
const secondNotesLog = byKey(shapePanel, "attempt:31:notes:log");
secondNotesLog.scrollTop = 55;
firstReviewLog.focus();
const vanishSelection = {
  rangeCount: 1, anchorNode: firstReviewLog, focusNode: firstReviewLog, removed: false, added: null,
  getRangeAt: () => ({startContainer: firstReviewLog, startOffset: 0,
    endContainer: firstReviewLog, endOffset: 4}),
  removeAllRanges() { this.removed = true; },
  addRange(range) { this.added = range; },
};
byKey(shapePanel, "attempt:30").open = true;
const vanishView = capture(shapePanel, scroller, vanishSelection, firstReviewLog);
const shapeSecond = payload(
  [attempt("30", 1, "First notes", "VERDICT: ISSUES\nfirst"),
   attempt("31", 2, "Second notes", "VERDICT: NO ISSUES\nsecond"),
   attempt("32", 3, "Third notes", "VERDICT: NO ISSUES\nthird")],
  [{id: 4, kind: "review", startedAt: "2026-01-01T00:05:00Z",
    endedAt: "2026-01-01T00:10:00Z", detail: "Review completed.", open: false, data: {}}],
  {available: true, complete: false, count: 2, reviewCount: 3, unlinkedReviews: 0});
shapeSecond.execution.worker.attempts[0].notes = {available: false, text: "",
  truncated: false, detail: "No new notes."};
shapeSecond.execution.worker.attempts[0].review = {recorded: false, available: false,
  verdict: "UNAVAILABLE", findingCount: null, findingCountComplete: false,
  model: "", text: "", truncated: false, detail: "No reviewer record.",
  startedAt: null, endedAt: null};
context.document.activeElement = null;
render(shapeSecond, shapePanel);
restore(shapePanel, vanishView, scroller, vanishSelection);
const shape = {
  secondNotesScroll: byKey(shapePanel, "attempt:31:notes:log").scrollTop,
  firstAttemptOpen: byKey(shapePanel, "attempt:30").open,
  newAttemptOpen: byKey(shapePanel, "attempt:32").open,
  keyedFocusGone: context.document.activeElement === null,
  keyedSelectionGone: vanishSelection.added === null,
};

const cleanPanel = context.document.createElement("div");
const clean = payload([attempt("20", 1, "Notes", "VERDICT: NO ISSUES")],
  [{id: 3, kind: "review", startedAt: "2026-01-01T00:05:00Z",
    endedAt: "2026-01-01T00:10:00Z", detail: "Review completed: NO ISSUES.",
    open: false, data: {}}],
  {available: true, complete: true, count: 0, reviewCount: 1, unlinkedReviews: 0});
clean.execution.worker.attempts[0].review.verdict = "NO ISSUES";
clean.execution.worker.attempts[0].review.findingCount = 0;
render(clean, cleanPanel);
const cleanFindings = metricValue(cleanPanel, "Review findings");

process.stdout.write(JSON.stringify({initial, refreshed, missing, cleanFindings, shape}));
"""
        run = subprocess.run(
            ["node", "-e", script, str(root / "panes.js"), str(root / "issue.js")],
            text=True, capture_output=True, check=True)
        result = json.loads(run.stdout)
        self.assertEqual(result["initial"], {
            "timelineSummary": "Timeline · 1",
            "timelineOpen": False,
            "timelineEvents": 1,
            "reviewText": "VERDICT: ISSUES\n<img src=x onerror=alert(1)>",
            "notesText": "Notes <script>alert(1)</script>",
            "injected": False,
            "ciWorkflow": {"value": "0s", "detail": "Sum of workflow runs; may overlap"},
            "attempts": {"value": "1", "detail": "Retained attempt records"},
            "findings": {"value": "2", "detail": "Across 1 recorded review pass"},
        })
        self.assertEqual(result["refreshed"], {
            "timelineSummary": "Timeline · 2",
            "timelineOpen": True,
            "firstAttemptOpen": True,
            "secondAttemptOpen": False,
            "logScroll": 88,
            "scroller": 321,
            "focus": True,
            "selection": True,
        })
        self.assertEqual(result["missing"], {
            "timelineSummary": "Timeline · 0",
            "timelineOpen": False,
            "timelineText": "No execution events were recorded.",
            "attempts": {"value": "0", "detail": "No worker attempts have started."},
            "findings": {"value": "Unavailable",
                "detail": "No attempt-linked reviewer findings were recorded."},
        })
        self.assertEqual(result["cleanFindings"],
            {"value": "0", "detail": "Across 1 recorded review pass"})
        self.assertEqual(result["shape"], {
            "secondNotesScroll": 55,
            "firstAttemptOpen": True,
            "newAttemptOpen": False,
            "keyedFocusGone": True,
            "keyedSelectionGone": True,
        })
