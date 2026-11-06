"""Real Linear creation and readiness metadata writes against an offline service."""

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
from common.clients import Linear, RequestError
import worker
from common.issue_metadata import estimate_values, missing_metadata
from common.policy import PROTOCOL, review_prompt, validate_result
from controller.core import Controller
from controller.model import Role
from call_budget import CallBudget, record_api_calls
import test_controller_broker as broker_fixtures
from test_controller import ready

TEAM = "22222222-2222-4222-8222-222222222222"
ISSUE = "33333333-3333-4333-8333-333333333333"


def settings(kind="fibonacci", *, extended=False, zero=False):
    return {"issueEstimationType": kind, "issueEstimationExtended": extended,
            "issueEstimationAllowZero": zero, "inheritIssueEstimation": False, "parent": None}


class MetadataService:
    def __init__(self, row=None):
        self.row = row
        self.teams = {TEAM: settings()}
        self.mutations = []
        self.lose_response = False
        self.reject_update = False
        record_api_calls(self)

    def request(self, url, **kwargs):
        query, variables = kwargs["data"]["query"], kwargs["data"]["variables"]
        if "issueEstimationType" in query:
            return {"data": {"team": copy.deepcopy(self.teams.get(variables["id"]))}}
        if "issueCreate(" in query:
            fields = variables["input"]
            self.mutations.append(copy.deepcopy(fields))
            self.row = {**fields, "team": {"id": fields["teamId"]}, "project": {"id": "project"}}
            return {"data": {"issueCreate": {"success": True, "issue": {"id": fields["id"]}}}}
        if "issueUpdate(" in query:
            if self.reject_update:
                raise RequestError(503)
            self.mutations.append(copy.deepcopy(variables["input"]))
            self.row.update(variables["input"])
            if self.lose_response:
                raise RequestError(503)
            return {"data": {"issueUpdate": {"success": True}}}
        row = copy.deepcopy(self.row)
        if row and row.get("project"):
            row["project"].setdefault("labels", {"nodes": [], "pageInfo": {"hasNextPage": False}})
        return {"data": {"issue": row}}


class IssueMetadataTests(unittest.TestCase):
    def test_all_team_scales_extended_zero_and_inherited_settings(self):
        for kind, values in (("linear", [1, 2, 3, 4, 5, 6, 7]),
                             ("fibonacci", [1, 2, 3, 5, 8, 13, 21]),
                             ("tShirt", [1, 2, 3, 5, 8, 13, 21]),
                             ("exponential", [1, 2, 4, 8, 16, 32, 64])):
            with self.subTest(kind=kind):
                self.assertEqual(estimate_values(settings(kind)), values[:5])
                self.assertEqual(estimate_values(settings(kind, extended=True, zero=True)), [0, *values])
        service = MetadataService()
        service.teams[TEAM].update(inheritIssueEstimation=True, parent={"id": "parent"})
        service.teams["parent"] = settings("exponential", extended=True)
        linear = Linear("test-only", service.request)
        with CallBudget(self, linear=service, exact={"linear.request": 2}):
            self.assertEqual(linear.team_estimates(TEAM), [1, 2, 4, 8, 16, 32, 64])
        service.teams["parent"].update(inheritIssueEstimation=True, parent={"id": TEAM})
        with CallBudget(self, linear=service, exact={"linear.request": 2}), self.assertRaises(ValueError):
            linear.team_estimates(TEAM)
        service.teams[TEAM] = {**settings(), "inheritIssueEstimation": True}
        with CallBudget(self, linear=service, exact={"linear.request": 1}):
            self.assertEqual(linear.team_estimates(TEAM), [1, 2, 3, 5, 8])
        for invalid in (None, {}, settings("notUsed"), settings("unknown")):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                estimate_values(invalid)

    def test_new_and_followup_creation_keep_supplied_fields_and_use_team_scale(self):
        for parent in (None, "parent-issue"):
            service = MetadataService()
            service.teams[TEAM] = settings("exponential")
            fields = {"id": ISSUE, "teamId": TEAM, "title": "Restore production workers",
                      "description": "The production queue is stalled; repair dispatch and verify recovery.",
                      "priority": 1, "estimate": 4, "labelIds": ["unrelated"]}
            if parent:
                fields["parentId"] = parent
            with self.subTest(parent=parent), CallBudget(self, linear=service, exact={"linear.request": 3}):
                result = Linear("test-only", service.request).create_issue(fields)
            self.assertEqual(service.mutations, [fields])
            self.assertEqual((result["priority"], result["estimate"]), (1, 4))
        for fields in ({"priority": 0, "estimate": 2}, {"priority": 3}, {"estimate": 2}):
            service = MetadataService()
            with CallBudget(self, linear=service), self.assertRaises(ValueError):
                Linear("test-only", service.request).create_issue({"id": ISSUE, "teamId": TEAM, **fields})
        service = MetadataService()
        for fields in ({"priority": 3, "estimate": 4}, {"priority": True, "estimate": 2},
                       {"priority": 3, "estimate": 0}, {"priority": 3, "estimate": 1.5}):
            with CallBudget(self, linear=service, exact={"linear.request": 1}), self.assertRaises(ValueError):
                Linear("test-only", service.request).create_issue({"id": ISSUE, "teamId": TEAM, **fields})
        self.assertFalse(service.mutations)

    def test_completion_preserves_values_and_recovers_an_ambiguous_write_without_repeating_it(self):
        for existing, expected in (({"priority": 1}, {"estimate": 3}),
                                    ({"estimate": 0}, {"priority": 3}),
                                    ({"priority": 0, "estimate": None}, {"priority": 3, "estimate": 3})):
            row = {"id": ISSUE, "title": "Implement bounded queue repair", "team": {"id": TEAM}, **existing}
            service = MetadataService(row)
            service.lose_response = True
            linear = Linear("test-only", service.request)
            with self.subTest(existing=existing), CallBudget(self, linear=service,
                    exact={"linear.request": 4 if "estimate" in expected else 3}):
                result = linear.complete_issue_metadata(copy.deepcopy(row), {"priority": 3, "estimate": 3})
            self.assertEqual(service.mutations, [expected])
            self.assertFalse(missing_metadata(result))
            with CallBudget(self, linear=service):
                self.assertEqual(linear.complete_issue_metadata(result, {"priority": 2, "estimate": 8}), result)

    def test_reread_preserves_user_edits_and_rejects_changed_scope_before_writing(self):
        row = {"id": ISSUE, "title": "Fix queue", "team": {"id": TEAM}}
        service = MetadataService({**row, "priority": 1, "estimate": 8})
        linear = Linear("test-only", service.request)
        with CallBudget(self, linear=service, exact={"linear.request": 1}):
            result = linear.complete_issue_metadata(row, {"priority": 3, "estimate": 2})
        self.assertEqual((result["priority"], result["estimate"]), (1, 8))
        service.row = {**row, "title": "A different scope"}
        with CallBudget(self, linear=service, exact={"linear.request": 1}), self.assertRaises(ValueError):
            linear.complete_issue_metadata(row, {"priority": 3, "estimate": 2})
        self.assertFalse(service.mutations)

    def test_metadata_prompt_and_result_validation_apply_to_both_readiness_modes(self):
        for autonomous in (False, True):
            prompt = review_prompt({"priority": 1, "estimate": None, "estimate_values": [1, 2, 4, 8, 16]},
                                   autonomous=autonomous)
            for text in ("follow-up", "preserve", "rationale", "read-only assessment", '"priority": 1', "1, 2, 4, 8, 16"):
                self.assertIn(text, prompt)
        for field, values in (("priority", [0, 5, True, "3"]), ("estimate", [None, -1, 1.5, True, 65])):
            for value in values:
                result = ready()
                result["issue_metadata"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_result(result)
        for repair in ({}, {"repair": {"instructions": "Repair the failed validation."}}):
            prompt = worker.development_prompt(None, {"issue": {
                "identifier": "KIAN-1", "title": "Fix queue", "url": "https://linear.app/test/issue/KIAN-1"}, **repair})
            for text in ("follow-up", "explicit priority", "configured estimate scale", "preserve"):
                self.assertIn(text, prompt)


class ReadinessMetadataTests(unittest.TestCase):
    def setUp(self):
        self.f = broker_fixtures.ControllerBrokerTests(methodName="runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        f.controller = Controller(f.database, autonomous=True)
        f.broker.controller = f.controller
        self.service = MetadataService(f.issue)
        linear = Linear("test-only", self.service.request)
        f.linear.issue.side_effect = linear.issue
        f.linear.team_estimates.side_effect = linear.team_estimates
        f.linear.complete_issue_metadata.side_effect = linear.complete_issue_metadata

    def assess(self):
        f = self.f
        f.preflight(Role.REVIEW_SELECT, f.account_a)
        return f.call(Role.REVIEW, "start", protocol=PROTOCOL)

    def test_missing_fields_are_completed_before_a_ready_result_is_accepted(self):
        f = self.f
        f.issue.update(priority=0, estimate=None)
        config = self.assess()
        self.assertEqual(config["issue"]["estimate_values"], [1, 2, 3, 5, 8])
        with CallBudget(self, linear=self.service, exact={"linear.request": 5}):
            self.assertEqual(f.call(Role.REVIEW, "review", binding=config["binding"], result=ready()), {"status": "passed"})
        current = f.controller.inspect(f.view.task_id)
        self.assertFalse(current.review_attention)
        self.assertFalse(current.attempt["coding_started"])
        self.assertEqual((f.issue["priority"], f.issue["estimate"]), (3, 2))
        self.assertEqual(self.service.mutations, [{"priority": 3, "estimate": 2}])

    def test_missing_estimate_keeps_the_user_priority(self):
        f = self.f
        f.issue.update(priority=1, estimate=None)
        config = self.assess()
        with CallBudget(self, linear=self.service, exact={"linear.request": 5}):
            f.call(Role.REVIEW, "review", binding=config["binding"], result=ready())
        self.assertEqual(self.service.mutations, [{"estimate": 2}])
        self.assertEqual((f.issue["priority"], f.issue["estimate"]), (1, 2))

    def test_missing_priority_keeps_an_explicit_zero_estimate_without_fetching_a_scale(self):
        f = self.f
        f.issue.update(priority=0, estimate=0)
        self.service.teams[TEAM] = settings(zero=True)
        config = self.assess()
        self.assertEqual(config["issue"]["estimate_values"], [0])
        with CallBudget(self, linear=self.service, exact={"linear.request": 4}):
            f.call(Role.REVIEW, "review", binding=config["binding"], result=ready())
        self.assertEqual(self.service.mutations, [{"priority": 3}])
        self.assertEqual((f.issue["priority"], f.issue["estimate"]), (3, 0))

    def test_failed_metadata_write_cannot_authorize_coding_and_failure_remains_automatic(self):
        f = self.f
        f.issue.update(priority=0, estimate=None)
        config = self.assess()
        self.service.reject_update = True
        with CallBudget(self, linear=self.service, exact={"linear.request": 5}), self.assertRaises(RequestError):
            f.call(Role.REVIEW, "review", binding=config["binding"], result=ready())
        current = f.controller.inspect(f.view.task_id)
        self.assertEqual(current.phase, "reviewing")
        self.assertFalse(current.attempt["coding_started"] or current.review_attention)
        f.call(Role.REVIEW, "finish", status="failed")
        current = f.controller.inspect(f.view.task_id)
        self.assertFalse(current.review_attention)
        self.assertEqual(current.attempt["retry_at"], 1060)

    def test_scale_mismatch_and_invalid_results_do_not_write_metadata(self):
        f = self.f
        f.issue.update(priority=0, estimate=None)
        config = self.assess()
        self.service.teams[TEAM] = settings("exponential")
        result = ready()
        result["issue_metadata"]["estimate"] = 3
        with CallBudget(self, linear=self.service, exact={"linear.request": 3}), self.assertRaises(ValueError):
            f.call(Role.REVIEW, "review", binding=config["binding"], result=result)
        self.assertFalse(self.service.mutations)
        result["issue_metadata"]["priority"] = 0
        with CallBudget(self, linear=self.service, exact={"linear.request": 1}):
            f.call(Role.REVIEW, "review", binding=config["binding"], result=result)
        self.assertFalse(self.service.mutations)
        self.assertFalse(f.controller.inspect(f.view.task_id).review_attention)

    def test_complete_metadata_is_a_noop_and_fields_cleared_after_review_are_refilled_before_start(self):
        f = self.f
        f.issue.update(priority=1, estimate=8)
        f.review()
        self.assertFalse(self.service.mutations)
        self.assertEqual((f.issue["priority"], f.issue["estimate"]), (1, 8))
        f.issue.update(priority=0, estimate=None)
        f.preflight(Role.CODING_SELECT, f.account_b)
        f.call(Role.CODING, "start", protocol=PROTOCOL)
        self.assertEqual(self.service.mutations, [{"priority": 3, "estimate": 2}])
        self.assertFalse(missing_metadata(f.issue))


if __name__ == "__main__":
    unittest.main()
