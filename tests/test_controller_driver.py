"""Task sequences cross the same controller/broker/driver seams used in production."""

import copy
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation")]
from controller.driver import Driver
from controller.model import Phase, Role, Hold, InboxQueued, identity, Rejected
from common.routing import DispatchError
from common.policy import PROTOCOL
import test_controller_broker as fixtures
from test_controller_authorization import claims
from common.reporting import worker_branch


class ControllerDriverTests(unittest.TestCase):
    def change_state(self, issue_id, state, *, at=None, actor_id=None, app=False, own=False):
        previous = copy.deepcopy(self.issues[issue_id]['state'])
        self.issues[issue_id]['state'] = copy.deepcopy(state)
        self.histories[issue_id].append({
            'id': str(UUID(int=1000+sum(map(len, self.histories.values())))),
            'changed_at': self.driver.clock() if at is None else at,
            'actor': {'id': actor_id or str(UUID(int=98)), 'app': app, 'isMe': own}, 'botActor': None,
            'fromState': previous, 'toState': copy.deepcopy(state),
            'fromProjectId': None, 'toProjectId': None, 'archived': None, 'trashed': None})

    def human_resubmit(self):
        f=self.fixture
        changed_at=self.driver.clock()+1
        self.driver.clock=lambda:changed_at
        f.broker.clock=self.driver.clock
        previous=f.issue['state']['id']
        self.change_state(f.issue['id'], {'id':f.route.ready_state_ids[0],'type':'unstarted'},
                          at=changed_at, actor_id=str(UUID(int=99)))
        event={'type':'Issue','id':f.issue['id'],'webhook_at':changed_at,
               'submission':{'actor_id':str(UUID(int=99)),'from_state':previous,
                             'to_state':f.route.ready_state_ids[0],'changed_at':changed_at}}
        self.driver.apply(InboxQueued(identity(event),event))
        return event

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
        self.histories={key: [] for key in self.issues}
        self.second_issue=second
        f.linear.issue.side_effect=lambda key:self.issues.get(key)
        f.linear.project_issues.side_effect=lambda project:list(self.issues.values())
        f.linear.issue_dependencies.return_value=[]
        f.linear.issue_history.side_effect=lambda key:copy.deepcopy({**self.issues[key], 'history': self.histories[key]})
        f.linear.set_state.side_effect=lambda key,state:self.change_state(
            key, {'id':state,'type':'started'}, app=True, own=True)
        self.runs={}
        self.run_history=[]
        f.run_ids={}
        self.counter=122
        def dispatch(route,inputs):
            self.counter+=1
            run={"id":self.counter,"run_attempt":1,"status":"in_progress","conclusion":None,
                 'phase': inputs['phase'], 'key': inputs['dispatch_key'],
                 'display_title': f"Clusterfork task · {inputs['linear_identifier']} · {inputs['phase']} · {inputs['dispatch_key']}"}
            self.runs[inputs['dispatch_key']]=run
            self.run_history.append(run)
            f.run_ids[inputs['phase']]=self.counter
        f.github.dispatch.side_effect=dispatch
        f.github.dispatch_runs.side_effect=lambda route,key:[copy.deepcopy(r) for r in self.run_history if r['key']==key and (not route.phase or r['phase']==route.phase)]
        f.github.worker_result.side_effect=lambda repo,owner,**kwargs:copy.deepcopy(next(r for r in self.run_history if str(r["id"])==owner.split(":")[0]))
        f.github.worker_pull.return_value=None
        self.ready=True
        self.driver=Driver(f.controller,f.registry,f.linear,f.github,f.spec.runtime_sha,
                           runtime_ready=lambda:self.ready,clock=lambda:1000)
        review = f.review
        def reviewed():
            if f.key not in self.runs:
                self.driver.dispatch()
            review()
            self.runs[f.key].update(status='completed', conclusion='success')
            self.driver.tick()
        f.review=reviewed

    def publish_and_merge_first(self):
        f=self.fixture
        self.driver.tick(scan=True)
        f.review()
        f.preflight(Role.CODING_SELECT,f.account_b)
        f.call(Role.CODING,"start",protocol=PROTOCOL)
        f.advance('finished', has_changes=True)
        f.call(Role.CODING,"finish",status="completed")
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
        self.assertEqual(f.github.dispatch.call_count,3)
        self.driver.tick()
        self.assertEqual(f.github.dispatch.call_count,3)

    def test_guarded_refresh_keeps_running_coding_and_blocks_queued_admission_on_new_prerequisite(self):
        f = self.fixture
        self.driver.tick(scan=True)
        f.review()
        f.preflight(Role.CODING_SELECT, f.account_b)
        f.call(Role.CODING, "start", protocol=PROTOCOL)
        f.call(Role.CODING, "claim", account_id=f.account_b)
        f.call(Role.CODING, "progress", stage="coding", context_sha=f.spec.context_sha)
        f.linear.issue_dependencies.return_value = [{"id": str(UUID(int=300)), "type": "blocks", "issue": {
            "id": str(UUID(int=301)), "identifier": "KIAN-3", "state": {"type": "unstarted"}, "trashed": False}}]
        f.broker.refresh = self.driver.refresh
        with self.assertRaisesRegex(Rejected, 'not authorized a push'):
            f.call(Role.CODING, "github")
        self.driver.tick()
        current = f.controller.inspect(f.view.task_id)
        self.assertEqual(current.phase, "executing")
        self.assertFalse(current.dependencies_ready or current.review_attention or current.attempt["retry_kind"])
        self.assertEqual(len(f.controller.attempt_history(current.task_id)), 1)
        self.assertEqual(f.github.dispatch.call_count, 2)
        f.advance('testing')
        f.call(Role.CODING, "progress", stage="testing")
        f.advance('publishing')
        f.call(Role.CODING, "progress", stage="publishing")
        f.call(Role.CODING, "github")
        f.advance('finished')
        f.call(Role.CODING, "finish", status="completed")
        self.runs[f.key].update(status="completed", conclusion="success")
        self.driver.tick()
        self.assertEqual(f.controller.inspect(current.task_id).phase, "completed")
        second = next(v for v in f.controller.tasks() if v.issue_id == self.second_issue["id"])
        self.assertEqual(second.phase, "queued")
        self.assertFalse(second.active or second.dependencies_ready)
        self.assertEqual(f.github.dispatch.call_count, 2)
        f.linear.issue_dependencies.return_value = []
        self.driver.tick()
        self.assertTrue(f.controller.inspect(second.task_id).active)
        self.assertEqual(f.github.dispatch.call_count, 3)
        self.driver.tick()
        self.assertEqual(f.github.dispatch.call_count,3)

    def test_runtime_change_holds_second_task_until_required_release_is_ready(self):
        self.publish_and_merge_first()
        f=self.fixture
        self.ready=False
        self.driver.tick()
        self.assertEqual(f.controller.inspect(f.view.task_id).phase,Phase.COMPLETED)
        self.assertEqual(len(f.controller.active()),0)
        self.assertEqual(f.github.dispatch.call_count,2)
        self.assertIn("deployment:required-runtime",f.controller.holds())
        self.ready=True
        self.driver.runtime_sha="f"*40
        self.driver.tick()
        second=next(v for v in f.controller.tasks() if v.issue_id==self.second_issue["id"])
        self.assertEqual(second.spec.runtime_sha,"f"*40)
        self.assertEqual(second.spec.context_sha,"c"*40)
        self.assertEqual(f.github.dispatch.call_count,3)
        self.assertNotIn("deployment:required-runtime",f.controller.holds())

    def test_waiting_status_tracks_the_new_owner_when_the_previous_task_finishes(self):
        third=copy.deepcopy(self.second_issue)
        third.update(id=str(UUID(int=3)),identifier='KIAN-3',createdAt='2026-10-30T00:00:02Z')
        self.issues[third['id']]=third
        self.publish_and_merge_first()
        self.driver.tick()
        comments=[call.args[2] for call in self.fixture.linear.status_comment.call_args_list if call.args[0]==third['id']]
        self.assertIn('Waiting for KIAN-2 to complete.',comments[-1])
        self.assertNotIn('Waiting for KIAN-1',comments[-1])

    def test_removed_mapping_still_observes_the_original_pr_and_stopped_execution(self):
        self.publish_and_merge_first()
        self.driver.registry=replace(self.fixture.registry,routes={})
        with self.assertLogs(level='WARNING'):
            self.driver.tick()
        view=self.fixture.controller.inspect(self.fixture.view.task_id)
        self.assertEqual(view.phase,'completed')
        self.assertFalse(view.active)
        target=self.fixture.github.worker_pull.call_args.args[0]
        self.assertEqual(target.repository,self.fixture.route.repository)
        self.assertEqual(target.ref,self.fixture.route.ref)

    def test_cancellation_after_project_move_waits_for_original_jobs_to_stop(self):
        f=self.fixture
        self.driver.tick(scan=True)
        f.issue['project']={'id':str(UUID(int=999))}
        f.issue['state']={'id':'canceled-state','type':'canceled'}
        self.driver.tick()
        self.assertTrue(f.controller.inspect(f.view.task_id).active)
        self.runs[f.key].update(status='completed',conclusion='cancelled')
        self.driver.tick()
        view=f.controller.inspect(f.view.task_id)
        self.assertEqual(view.phase,'canceled')
        self.assertFalse(view.active)

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

    def test_canceled_definitely_rejected_dispatch_checks_publication_and_releases_the_queue(self):
        f=self.fixture
        f.github.dispatch.side_effect=DispatchError('rejected',uncertain=False)
        self.driver.tick(scan=True)
        f.issue['state']={'id':'canceled-state','type':'canceled'}
        self.driver.tick()
        view=f.controller.inspect(f.view.task_id)
        self.assertEqual(view.phase,'canceled')
        self.assertFalse(view.active)
        self.assertTrue(f.github.worker_pull.called)
        second=next(v for v in f.controller.tasks() if v.issue_id==self.second_issue['id'])
        self.assertTrue(second.active)

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

    def test_scanning_unseen_ineligible_issues_does_not_create_tasks_or_status_comments(self):
        f=self.fixture
        self.second_issue["state"]={"id":"completed-state","type":"completed"}
        self.driver.tick(scan=True)
        self.assertEqual([view.issue_id for view in f.controller.tasks()],[f.issue["id"]])
        self.assertEqual(f.linear.status_comment.call_count,1)

    def test_hold_changes_update_queued_linear_status_without_repeated_comments(self):
        f=self.fixture
        self.driver.tick(scan=True)
        f.linear.status_comment.reset_mock()
        self.driver.apply(Hold('operator:maintenance','Waiting for the operator to resume.'))
        self.driver.report()
        self.assertTrue(any('Waiting for the operator to resume.' in call.args[2]
                            for call in f.linear.status_comment.call_args_list))
        f.linear.status_comment.reset_mock()
        self.driver.apply(Hold('operator:maintenance','Waiting for the operator to resume.'))
        self.driver.report()
        f.linear.status_comment.assert_not_called()

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
        f.linear.set_label.assert_any_call(f.view.issue_id,'clusterfork: auto-repair',True)
        self.assertNotEqual(first_key,repair.attempt["attempt_id"])
        f.key=repair.attempt["attempt_id"]
        config=execute_failure()
        self.assertEqual(config["repair"]["target_dispatch_key"],f.view.task_id)
        self.assertEqual(config["repair"]["target_branch"],worker_branch(f.view.identifier,f.view.task_id))
        self.assertEqual(config["repair"]["wip_commit_sha"],"f"*40)
        self.driver.tick()
        final=f.controller.inspect(f.view.task_id)
        self.assertEqual(final.phase,Phase.HELD)
        self.assertEqual(final.attempt["retry_kind"],"")
        self.assertEqual(f.github.dispatch.call_count,4)
        f.linear.set_label.assert_any_call(f.view.issue_id,'clusterfork: auto-repair',False)
        reports=[call for call in f.linear.status_comment.call_args_list if 'Auto-Repair Report' in call.args[2]]
        self.assertEqual(len(reports),1)
        self.assertIn('operator review is required',reports[0].args[2])


if __name__=="__main__":
    unittest.main()
