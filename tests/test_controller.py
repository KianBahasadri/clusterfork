"""Task ownership and native job ordering against real durable transactions."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation")]
from controller.core import Controller
from controller.model import (Admit, Assign, DispatchObserved, ExternalObserved, Finish, Hold,
                              IssueObserved, Phase, Progress, Rejected, Retry, Review, Role,
                              Start, TaskSpec)
from controller.store import Database
from common.policy import CRITERIA

SPEC = TaskSpec("c" * 64, "b" * 40, "a" * 40, "d" * 64, "e" * 64)
ACCOUNT_A, ACCOUNT_B = "1" * 64, "cursor:" + "2" * 64


def ready():
    return {"verdict": "ready", "summary": "Requirements and completion are clear.",
            "criteria": {k: {"status": "satisfied", "reason": "Explicit in issue."} for k in CRITERIA},
            "blockers": [], "evidence": [], "policy_references": []}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tasks.db"
        self.database = Database(self.path)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.database.close)
        self.controller = Controller(self.database)
        self.now = 1000

    def apply(self, command):
        return self.controller.apply(command, now=self.now)

    def issue(self, number=1, **fields):
        observation = IssueObserved(f"issue-{number}", "project", "Owner/Repo", "v1", f"KIAN-{number}",
                                    True, True, content_revision=SPEC.content_revision)
        observation = replace(observation, **fields)
        self.apply(observation)
        return observation

    def admit(self, number=1, **fields):
        issue = self.issue(number, **fields)
        return self.apply(Admit(issue.task_id, SPEC))

    def start(self, view, role, **fields):
        return self.apply(Start(view.task_id, view.attempt["attempt_id"], "123", 1, role, **fields))

    def review(self, view):
        self.start(view, Role.REVIEW_SELECT)
        self.apply(Assign(view.task_id, view.attempt["attempt_id"], Role.REVIEW_SELECT, ACCOUNT_A))
        self.start(view, Role.REVIEW)
        return self.apply(Review(view.task_id, view.attempt["attempt_id"], ready(), SPEC))

    def coding(self, view):
        self.review(view)
        self.start(view, Role.CODING_SELECT, review_job_succeeded=True)
        self.apply(Assign(view.task_id, view.attempt["attempt_id"], Role.CODING_SELECT, ACCOUNT_B))
        self.start(view, Role.CODING, review_job_succeeded=True)
        return self.apply(Progress(view.task_id, view.attempt["attempt_id"], "coding", SPEC.context_sha))

    def external(self, view, **fields):
        command = ExternalObserved(view.task_id, view.attempt["attempt_id"], "123", 1,
                                   True, "success", True, True, pull_state="absent")
        return self.apply(replace(command, **fields))

    def test_database_has_one_process_owner_and_survives_reopening(self):
        view = self.admit()
        with self.assertRaisesRegex(Rejected, "Another controller"):
            Database(self.path)
        self.database.close()
        with Database(self.path) as reopened:
            persisted = Controller(reopened).inspect(view.task_id)
            self.assertEqual(persisted, view)
            self.assertTrue(persisted.active)

    def test_version_one_database_migration_preserves_task_and_attempt_ownership(self):
        view=self.admit()
        self.database.close()
        with sqlite3.connect(self.path) as legacy:
            legacy.execute("ALTER TABLE attempts DROP COLUMN attempt_kind")
            legacy.execute("ALTER TABLE tasks DROP COLUMN canceled")
            legacy.execute("PRAGMA user_version=1")
        legacy.close()
        with Database(self.path) as upgraded:
            restored=Controller(upgraded).inspect(view.task_id)
            self.assertTrue(restored.active)
            self.assertEqual(restored.attempt["attempt_id"],view.attempt["attempt_id"])
            self.assertEqual(restored.attempt["attempt_kind"],"normal")
            self.assertEqual(upgraded.connection.execute("PRAGMA user_version").fetchone()[0],8)

    def test_ineligibility_is_not_cancellation_and_canceled_work_requires_stop_and_pr_resolution(self):
        issue=self.issue()
        view=self.apply(Admit(issue.task_id,SPEC))
        self.review(view)
        self.apply(replace(issue,eligible=False))
        held=self.external(view)
        self.assertTrue(held.active)
        self.apply(replace(issue,eligible=False,canceled=True))
        still_open=self.external(view,pull_state="open",pull_request="https://github.com/Owner/Repo/pull/1")
        self.assertTrue(still_open.active)
        canceled=self.external(view,pull_state="closed",pull_request="https://github.com/Owner/Repo/pull/1")
        self.assertFalse(canceled.active)
        self.assertEqual(canceled.phase,Phase.CANCELED)

    def test_priority_manual_order_dependencies_and_repository_independence(self):
        ordinary = self.issue(1, priority=3)
        urgent_blocked = self.issue(2, priority=1, dependencies_ready=False)
        later_manual = self.issue(3, priority=1, sort_order=10)
        first_manual = self.issue(4, priority=1, sort_order=-10)
        self.assertEqual([v.task_id for v in self.controller.waiting()],
                         [first_manual.task_id, later_manual.task_id, ordinary.task_id])
        with self.assertRaises(Rejected):
            self.apply(Admit(ordinary.task_id, SPEC))
        self.apply(Admit(first_manual.task_id, SPEC))
        with self.assertRaises(Rejected):
            self.apply(Admit(later_manual.task_id, SPEC))
        second = self.admit(5, repository="Owner/Other")
        self.assertEqual(len(self.controller.active()), 2)
        self.assertTrue(second.active)
        self.assertFalse(self.controller.inspect(urgent_blocked.task_id).active)

    def test_a_waiting_issue_that_leaves_and_rejoins_gets_new_fifo_position(self):
        first=self.issue(1)
        self.apply(replace(first,eligible=False))
        second=self.issue(2)
        self.apply(first)
        self.assertEqual([view.task_id for view in self.controller.waiting()],[second.task_id,first.task_id])
        self.assertTrue(self.apply(Admit(second.task_id,SPEC)).active)

    def test_parallel_admission_commits_one_grant_and_one_dispatch_intent(self):
        issue = self.issue()
        def attempt(_):
            try:
                return self.apply(Admit(issue.task_id, SPEC)).active
            except Rejected:
                return False
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(attempt, range(20)))
        self.assertEqual(sum(results), 1)
        with self.database.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM grants").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM effects WHERE kind='dispatch'").fetchone()[0], 1)

    def test_failed_start_rolls_back_run_binding(self):
        view = self.admit()
        with self.assertRaisesRegex(Rejected, "assign an account"):
            self.start(view, Role.REVIEW)
        actual = self.controller.inspect(view.task_id)
        self.assertEqual(actual.attempt["run_id"], "")
        self.assertEqual(actual.sessions, ())

    def test_review_and_coding_have_independent_phase_bound_accounts(self):
        view = self.admit()
        self.review(view)
        with self.assertRaisesRegex(Rejected, "successful review job"):
            self.start(view, Role.CODING_SELECT)
        self.start(view, Role.CODING_SELECT, review_job_succeeded=True)
        with self.assertRaisesRegex(Rejected, "assign an account"):
            self.start(view, Role.CODING, review_job_succeeded=True)
        view = self.apply(Assign(view.task_id, view.attempt["attempt_id"], Role.CODING_SELECT, ACCOUNT_B))
        self.assertEqual(view.session("review")["account_id"], ACCOUNT_A)
        self.assertEqual(view.session("coding")["account_id"], ACCOUNT_B)
        with self.assertRaisesRegex(Rejected, "Only authenticated preflight"):
            self.apply(Assign(view.task_id, view.attempt["attempt_id"], Role.CODING, ACCOUNT_A))
        with self.assertRaisesRegex(Rejected, "cannot be replaced"):
            self.apply(Assign(view.task_id, view.attempt["attempt_id"], Role.CODING_SELECT, ACCOUNT_A))

    def test_partial_manual_rerun_cannot_inherit_assignment_or_review(self):
        view = self.admit()
        self.review(view)
        with self.assertRaisesRegex(Rejected, "another GitHub attempt"):
            self.apply(Start(view.task_id, view.attempt["attempt_id"], "123", 2,
                             Role.CODING_SELECT, review_job_succeeded=True))
        self.assertEqual(self.controller.inspect(view.task_id).attempt["run_attempt"], 1)

    def test_account_handoff_and_review_deadlines_cannot_be_extended_by_repeated_start(self):
        view=self.admit()
        self.start(view,Role.REVIEW_SELECT)
        self.apply(Assign(view.task_id,view.attempt["attempt_id"],Role.REVIEW_SELECT,ACCOUNT_A))
        self.now+=899
        self.start(view,Role.REVIEW)
        deadline=self.controller.inspect(view.task_id).session("review")["deadline"]
        self.now+=10
        self.start(view,Role.REVIEW)
        self.assertEqual(self.controller.inspect(view.task_id).session("review")["deadline"],deadline)
        self.now=deadline
        with self.assertRaisesRegex(Rejected,"expired"):
            self.start(view,Role.REVIEW)

    def test_invalid_or_changed_readiness_proof_cannot_allow_coding(self):
        view = self.admit()
        self.start(view, Role.REVIEW_SELECT)
        self.apply(Assign(view.task_id, view.attempt["attempt_id"], Role.REVIEW_SELECT, ACCOUNT_A))
        self.start(view, Role.REVIEW)
        with self.assertRaisesRegex(Rejected, "binding"):
            self.apply(Review(view.task_id, view.attempt["attempt_id"], ready(), replace(SPEC, context_sha="f"*40)))
        rejected = self.apply(Review(view.task_id, view.attempt["attempt_id"], {"verdict": "ready"}, SPEC))
        with self.assertRaises(Rejected):
            self.start(view, Role.CODING_SELECT, review_job_succeeded=True)
        self.assertEqual(json.loads(rejected.attempt["review_result"])["verdict"], "rejected")
        stopped = self.external(rejected)
        self.assertFalse(stopped.active)
        self.assertEqual(stopped.phase, Phase.HELD)

    def test_finish_callback_does_not_release_repository_before_cleanup(self):
        view = self.coding(self.admit())
        view = self.apply(Finish(view.task_id, view.attempt["attempt_id"], "coding", "completed"))
        self.assertTrue(view.active)
        self.assertNotEqual(view.phase, Phase.COMPLETED)
        view = self.external(view, all_attempts_stopped=False)
        self.assertTrue(view.active)
        view = self.external(view)
        self.assertFalse(view.active)
        self.assertEqual(view.phase, Phase.COMPLETED)

    def test_publication_survives_job_cleanup_failure_and_merge_is_authoritative(self):
        view = self.coding(self.admit())
        url = "https://github.com/Owner/Repo/pull/5"
        self.apply(Finish(view.task_id, view.attempt["attempt_id"], "coding", "completed", url))
        view = self.external(view, conclusion="failure", pull_request=url, pull_state="open")
        self.assertTrue(view.active)
        self.assertEqual(view.phase, Phase.PUBLISHED)
        view = self.external(view, conclusion="failure", pull_request=url, pull_state="merged")
        self.assertEqual(view.phase, Phase.COMPLETED)
        self.assertEqual(view.attempt["pull_request"], url)
        self.assertFalse(view.active)
        with self.assertRaisesRegex(Rejected, "terminal"):
            self.apply(Finish(view.task_id, view.attempt["attempt_id"], "coding", "failed"))

    def test_uncertain_dispatch_cannot_be_retried_without_positive_stop_evidence(self):
        view = self.admit()
        view = self.apply(DispatchObserved(view.task_id, view.attempt["attempt_id"], "unknown"))
        with self.assertRaisesRegex(Rejected, "not verified"):
            self.apply(Retry(view.task_id, SPEC, "network timeout", "startup"))
        self.assertEqual(view.attempt["dispatch_status"], "unknown")
        self.assertTrue(view.active)

    def test_verified_startup_timeout_cooldown_is_stable_and_old_attempt_is_fenced(self):
        view = self.admit()
        self.review(view)
        self.start(view, Role.CODING_SELECT, review_job_succeeded=True)
        view = self.external(view, conclusion="timed_out")
        retry_at = view.attempt["retry_at"]
        self.now += 10
        self.assertEqual(self.external(view, conclusion="failure").attempt["retry_at"], retry_at)
        with self.assertRaises(Rejected):
            self.apply(Retry(view.task_id, SPEC, "startup failed", "startup"))
        self.now = retry_at
        retried = self.apply(Retry(view.task_id, SPEC, "startup timed out", "review"))
        self.assertNotEqual(retried.attempt["attempt_id"], view.attempt["attempt_id"])
        with self.assertRaisesRegex(Rejected, "superseded"):
            self.start(view, Role.REVIEW_SELECT)

    def test_readiness_timeout_uses_first_five_minute_backoff(self):
        view=self.admit()
        self.start(view,Role.REVIEW_SELECT)
        failed=self.external(view,conclusion="timed_out")
        self.assertEqual(failed.attempt["retry_kind"],"review")
        self.assertEqual(failed.attempt["retry_at"],self.now+300)

    def test_retry_before_repair_coding_preserves_repair_mode_parent_and_budget(self):
        view=self.coding(self.admit())
        self.apply(Finish(view.task_id,view.attempt["attempt_id"],"coding","failed"))
        failed=self.external(view,conclusion="failure",repairable=True)
        repair=self.apply(Retry(view.task_id,SPEC,"coding failed","repair"))
        self.apply(Start(repair.task_id,repair.attempt["attempt_id"],"124",1,Role.REVIEW_SELECT))
        failed_review=self.apply(ExternalObserved(repair.task_id,repair.attempt["attempt_id"],"124",1,
                                                 True,"timed_out",True,True,pull_state="absent"))
        self.now=failed_review.attempt["retry_at"]
        retried=self.apply(Retry(view.task_id,SPEC,"review infrastructure failed","review"))
        self.assertEqual(retried.attempt["attempt_kind"],"repair")
        self.assertEqual(self.controller.repair_parent(view.task_id)["attempt_id"],view.attempt["attempt_id"])

    def test_content_edits_hold_account_and_publication_authority(self):
        issue = self.issue()
        view = self.apply(Admit(issue.task_id, SPEC))
        self.review(view)
        self.apply(replace(issue, content_revision="f" * 64))
        with self.assertRaisesRegex(Rejected, "authorization is held"):
            self.start(view, Role.CODING_SELECT, review_job_succeeded=True)

    def test_deployment_can_clear_only_its_hold_and_operator_halt_survives_restart(self):
        self.apply(Hold("operator:rebuild", "Owner paused cloud execution"))
        self.apply(Hold("deployment:123", "Installing release"))
        self.apply(Hold("deployment:123", "", release=True))
        self.assertEqual(self.controller.holds(), {"operator:rebuild": "Owner paused cloud execution"})
        issue = self.issue()
        with self.assertRaisesRegex(Rejected, "admissions are held"):
            self.apply(Admit(issue.task_id, SPEC))
        self.assertFalse(self.controller.inspect(issue.task_id).active)

    def test_constraints_reject_duplicate_grants_and_unknown_states(self):
        view = self.admit()
        other = self.issue(2)
        with self.assertRaises(sqlite3.IntegrityError), self.database.transaction() as db:
            db.execute("INSERT INTO grants VALUES(?,?)", (view.repository, other.task_id))
        with self.assertRaises(sqlite3.IntegrityError), self.database.transaction() as db:
            db.execute("UPDATE tasks SET phase='made-up' WHERE task_id=?", (view.task_id,))
        self.assertEqual(len(self.controller.active()), 1)


if __name__ == "__main__":
    unittest.main()
