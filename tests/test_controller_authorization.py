"""Signed phase identity, target scope and real review-job evidence contracts."""

from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
from controller.authorization import authorize_job, release_ref, verify_review_job
from controller.model import Rejected, Role
from controller.workflows import ENGINE, WORKFLOW, ROLE_FILES
from test_controller import SPEC


def claims(role):
    ref = release_ref(SPEC.runtime_sha)
    return {"repository": ENGINE, "ref": ref, "sha": SPEC.runtime_sha,
            "event_name": "workflow_dispatch", "runner_environment": "github-hosted",
            "workflow_ref": f"{ENGINE}/.github/workflows/{WORKFLOW}@{ref}",
            "job_workflow_sha": SPEC.runtime_sha,
            "job_workflow_ref": f"{ENGINE}/.github/workflows/{ROLE_FILES[role.value]}@{ref}",
            "run_id": "123", "run_attempt": "1", "repository_id": "12"}


class JobAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.view = SimpleNamespace(spec=SPEC, project_id="project", repository="owner/target")
        self.route = SimpleNamespace(project_id="project", repository="Owner/Target")

    def test_all_four_roles_require_their_own_signed_workflow_identity(self):
        for role in Role:
            with self.subTest(role=role):
                self.assertEqual(authorize_job(claims(role), self.view, self.route, "start"), role)

    def test_reviewer_cannot_publish_and_preflight_cannot_execute(self):
        for role, action in ((Role.REVIEW, "github"), (Role.REVIEW, "progress"),
                             (Role.REVIEW_SELECT, "review"), (Role.CODING_SELECT, "progress"),
                             (Role.REVIEW_SELECT, "checkout"), (Role.CODING_SELECT, "checkout"),
                             (Role.CODING_SELECT, "github"), (Role.CODING, "review")):
            with self.subTest(role=role, action=action), self.assertRaises(Rejected):
                authorize_job(claims(role), self.view, self.route, action)

    def test_forged_phase_field_never_grants_another_signed_role(self):
        evidence = {**claims(Role.REVIEW), "phase": "coding", "account_selection": True}
        self.assertEqual(authorize_job(evidence, self.view, self.route, "start"), Role.REVIEW)
        with self.assertRaises(Rejected):
            authorize_job(evidence, self.view, self.route, "github")

    def test_wrong_release_caller_ref_or_role_is_rejected(self):
        original = claims(Role.CODING)
        for field, wrong in (("sha", "f"*40), ("job_workflow_sha", "f"*40),
                             ("ref", "refs/heads/main"), ("event_name", "pull_request"),
                             ("runner_environment", "self-hosted"), ("repository", "Owner/Target"),
                             ("workflow_ref", "another-workflow"), ("job_workflow_ref", "unknown-role"),
                             ("run_attempt", "0")):
            with self.subTest(field=field), self.assertRaises(Rejected):
                authorize_job({**original, field: wrong}, self.view, self.route, "start")

    def test_dispatch_cannot_claim_an_unmapped_target(self):
        with self.assertRaises(Rejected):
            authorize_job(claims(Role.CODING), self.view,
                          SimpleNamespace(project_id="other", repository="Owner/Target"), "start")

    def test_only_exact_successful_review_job_counts(self):
        for name, status, conclusion, expected in (
                ("review / run", "completed", "success", True),
                ("review-account / run", "completed", "success", False),
                ("review / run", "in_progress", None, False),
                ("review / run", "completed", "failure", False),
                ("review / run", "completed", "skipped", False)):
            with self.subTest(name=name, status=status, conclusion=conclusion):
                github = Mock()
                github.request.return_value = {"total_count": 1, "jobs": [dict(name=name,status=status,conclusion=conclusion)]}
                self.assertEqual(verify_review_job(github, "123", 1), expected)
                self.assertIn("/attempts/1/jobs?", github.request.call_args.args[0])

    def test_truncated_review_jobs_cannot_authorize_coding(self):
        github = Mock()
        github.request.return_value = {"total_count": 2, "jobs": [{"name": "review / run", "status": "completed", "conclusion": "success"}]}
        with self.assertRaisesRegex(Rejected, "Incomplete"):
            verify_review_job(github, "123", 1)


if __name__ == "__main__":
    unittest.main()
