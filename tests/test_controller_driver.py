"""Task sequences cross the same controller/broker/driver seams used in production."""

import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
from controller.driver import Driver
from controller.model import Phase, Role
from dispatch_core import DispatchError
from readiness_policy import PROTOCOL
import test_controller_broker as fixtures
from test_controller_authorization import claims
from worker_reporting import worker_branch


class ControllerDriverTests(unittest.TestCase):
    def setUp(self):
        # Reuse the credential/issue fixture without inheriting and rerunning its tests.
        self.fixture = fixtures.ControllerBrokerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.issue.update(state={"id":f.route.ready_state_ids[0],"type":"unstarted"},archivedAt=None,
                       priority=0,sortOrder=0,createdAt="2026-10-30T00:00:00Z")
        f.issue["project"].update(archivedAt=None,labels={"nodes":[{"name":f.registry.enrollment_label}]})
        second=copy.deepcopy(f.issue)
        second.update(id=str(UUID(int=2)),identifier="KIAN-2",createdAt="2026-10-30T00:00:01Z")
        self.issues={f.issue["id"]:f.issue,second["id"]:second}
        self.second_issue=second
        f.linear.issue.side_effect=lambda key:self.issues.get(key)
        f.linear.project_issues.side_effect=lambda project:list(self.issues.values())
        f.linear.issue_dependencies.return_value=[]
        f.linear.set_state.side_effect=lambda key,state:self.issues[key].update(state={"id":state,"type":"started"})
        self.runs={}
        self.counter=122
        def dispatch(route,inputs):
            self.counter+=1
            self.runs[inputs["dispatch_key"]]={"id":self.counter,"run_attempt":1,"status":"in_progress","conclusion":None}
        f.github.dispatch.side_effect=dispatch
        f.github.dispatch_runs.side_effect=lambda route,key:[copy.deepcopy(self.runs[key])] if key in self.runs else []
        f.github.worker_result.side_effect=lambda repo,owner,**kwargs:copy.deepcopy(next(r for r in self.runs.values() if str(r["id"])==owner.split(":")[0]))
        f.github.worker_pull.return_value=None
        self.ready=True
        self.driver=Driver(f.controller,f.registry,f.linear,f.github,f.spec.runtime_sha,
                           runtime_ready=lambda:self.ready,clock=lambda:1000)

    def publish_and_merge_first(self):
        f=self.fixture
        self.driver.tick(scan=True)
        f.review()
        f.preflight(Role.CODING_SELECT,f.account_b)
        f.call(Role.CODING,"start",protocol=PROTOCOL)
        f.call(Role.CODING,"finish",status="completed",pull_request=f"https://github.com/{f.route.repository}/pull/1")
        self.runs[f.key].update(status="completed",conclusion="success")
        f.github.worker_pull.side_effect=lambda route,receipt,key,url="": (
            {"merged":True,"state":"MERGED","url":f"https://github.com/{f.route.repository}/pull/1"}
            if key==f.view.task_id else None)
        f.github.branch_sha.return_value="c"*40

    def test_sequential_task_uses_preceding_merge_and_dispatches_once(self):
        self.publish_and_merge_first()
        self.driver.tick()
        f=self.fixture
        first=f.controller.inspect(f.view.task_id)
        second=next(v for v in f.controller.tasks() if v.issue_id==self.second_issue["id"])
        self.assertEqual(first.phase,Phase.COMPLETED)
        self.assertFalse(first.active)
        self.assertTrue(second.active)
        self.assertEqual(second.spec.context_sha,"c"*40)
        self.assertEqual(second.spec.runtime_sha,f.spec.runtime_sha)
        self.assertEqual(f.github.dispatch.call_count,2)
        self.driver.tick()
        self.assertEqual(f.github.dispatch.call_count,2)

    def test_runtime_change_holds_second_task_until_required_release_is_ready(self):
        self.publish_and_merge_first()
        f=self.fixture
        self.ready=False
        self.driver.tick()
        self.assertEqual(f.controller.inspect(f.view.task_id).phase,Phase.COMPLETED)
        self.assertEqual(len(f.controller.active()),0)
        self.assertEqual(f.github.dispatch.call_count,1)
        self.assertIn("deployment:required-runtime",f.controller.holds())
        self.ready=True
        self.driver.runtime_sha="f"*40
        self.driver.tick()
        second=next(v for v in f.controller.tasks() if v.issue_id==self.second_issue["id"])
        self.assertEqual(second.spec.runtime_sha,"f"*40)
        self.assertEqual(second.spec.context_sha,"c"*40)
        self.assertEqual(f.github.dispatch.call_count,2)
        self.assertNotIn("deployment:required-runtime",f.controller.holds())

    def test_lost_dispatch_reply_is_discovered_without_another_dispatch(self):
        f=self.fixture
        real_dispatch=f.github.dispatch.side_effect
        def ambiguous(route,inputs):
            real_dispatch(route,inputs)
            raise DispatchError("response lost",uncertain=True)
        f.github.dispatch.side_effect=ambiguous
        self.driver.tick(scan=True)
        self.assertEqual(f.controller.inspect(f.view.task_id).attempt["dispatch_status"],"unknown")
        self.driver.tick()
        self.assertEqual(f.github.dispatch.call_count,1)
        self.assertEqual(f.controller.inspect(f.view.task_id).attempt["run_id"],"123")

    def test_linear_reporting_failure_does_not_change_admission_or_redispatch(self):
        f=self.fixture
        f.linear.status_comment.side_effect=RuntimeError("unavailable")
        with self.assertLogs(level="WARNING"):
            self.driver.tick(scan=True)
            self.driver.tick()
        self.assertEqual(f.github.dispatch.call_count,1)
        self.assertTrue(f.controller.inspect(f.view.task_id).active)
        self.assertTrue(f.controller.effects("report"))
        f.linear.status_comment.side_effect=None
        self.driver.tick()
        self.assertFalse(f.controller.effects("report"))

    def test_one_repair_keeps_original_branch_and_private_wip_evidence(self):
        f=self.fixture
        self.driver.tick(scan=True)
        usage={"observed_at":1000,"windows":[{"name":"primary","used_percent":50,"resets_at":2000,"duration_minutes":300}]}
        def execute_failure():
            f.review()
            f.preflight(Role.CODING_SELECT,f.account_b)
            config=f.call(Role.CODING,"start",protocol=PROTOCOL)
            leased=f.call(Role.CODING,"claim",account_id=f.account_b)
            f.call(Role.CODING,"progress",stage="coding",context_sha=f.spec.context_sha,
                   account_id=f.account_b,usage_before=usage)
            f.call(Role.CODING,"save",account_id=f.account_b,lease=leased["lease"],release=True)
            branch=worker_branch(f.view.identifier,f.view.task_id)
            f.call(Role.CODING,"finish",status="failed",wip_branch=branch,wip_commit_sha="f"*40)
            self.runs[f.key].update(status="completed",conclusion="failure")
            return config
        execute_failure()
        first_key=f.key
        self.driver.tick()
        repair=f.controller.inspect(f.view.task_id)
        self.assertEqual(repair.attempt["attempt_kind"],"repair")
        self.assertNotEqual(first_key,repair.attempt["attempt_id"])
        f.key=repair.attempt["attempt_id"]
        def call(role,action,**fields):
            evidence={**claims(role),"run_id":"124"}
            return f.broker.handle(action,evidence,{"dispatch_key":f.key,**fields})
        f.call=call
        config=execute_failure()
        self.assertEqual(config["repair"]["target_dispatch_key"],f.view.task_id)
        self.assertEqual(config["repair"]["target_branch"],worker_branch(f.view.identifier,f.view.task_id))
        self.assertEqual(config["repair"]["wip_commit_sha"],"f"*40)
        self.driver.tick()
        final=f.controller.inspect(f.view.task_id)
        self.assertEqual(final.phase,Phase.HELD)
        self.assertEqual(final.attempt["retry_kind"],"")
        self.assertEqual(f.github.dispatch.call_count,2)


if __name__=="__main__":
    unittest.main()
