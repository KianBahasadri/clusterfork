"""Live broker results retain existing immutable start and per-account usage reports."""

import copy
from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'automation'),str(ROOT/'automation/azure')]
from controller.model import Initialize,IssueObserved,Role,Finish,EffectUpdated,ExternalObserved
from controller.reporting import attempt_reports
from readiness_policy import PROTOCOL
from test_readiness import clarification,ready
from test_controller_authorization import claims
import test_controller_driver as fixtures


class ControllerReportingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ControllerDriverTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.driver=self.fixture.driver
        self.f=self.fixture.fixture

    def test_real_coding_results_report_usage_time_and_keep_start_immutable(self):
        f=self.f
        self.driver.tick(scan=True)
        f.review()
        f.preflight(Role.CODING_SELECT,f.account_b)
        f.call(Role.CODING,'start',protocol=PROTOCOL)
        leased=f.call(Role.CODING,'claim',account_id=f.account_b)
        before={'observed_at':1000,'windows':[{'name':'primary','used_percent':40,'resets_at':2000,'duration_minutes':300}]}
        f.call(Role.CODING,'progress',stage='coding',context_sha=f.spec.context_sha,
               account_id=f.account_b,usage_before=before,account_email='fixture@example.test',account_plan='pro')
        self.driver.report()
        starts=[call for call in f.linear.status_comment.call_args_list if 'development started.' in call.args[2]]
        self.assertEqual(len(starts),1)
        self.assertIn('40% used',starts[0].args[2])
        self.assertIn('fixture@example.test',starts[0].args[2])
        f.call(Role.CODING,'heartbeat')
        self.driver.report()
        self.assertEqual(len([call for call in f.linear.status_comment.call_args_list if 'development started.' in call.args[2]]),1)
        after=copy.deepcopy(before)
        after['observed_at']=1123
        after['windows'][0]['used_percent']=43
        f.call(Role.CODING,'progress',stage='testing',usage_after=after,usage_account_id=f.account_b)
        f.call(Role.CODING,'save',account_id=f.account_b,lease=leased['lease'],release=True)
        f.broker.clock=lambda:1123
        f.call(Role.CODING,'finish',status='completed')
        self.driver.report()
        finishes=[call for call in f.linear.status_comment.call_args_list if 'development finished.' in call.args[2]]
        self.assertEqual(len(finishes),1)
        self.assertIn('0h 2m 3s',finishes[0].args[2])
        self.assertIn('usage change +3 percentage points',finishes[0].args[2])
        self.assertEqual(len([call for call in f.linear.status_comment.call_args_list if 'development started.' in call.args[2]]),1)

    def test_failover_reports_each_account_separately_and_keeps_original_start(self):
        before=lambda used:{'observed_at':1000,'windows':[{'name':'primary','used_percent':used,'resets_at':2000,'duration_minutes':300}]}
        data={'usage_before':before(30),'original_account_id':'1'*64,'provider':'codex','model':'fixture-model','effort':'high',
              'agent_started_at':1000,'finished_at':1123,'usage_segments':[
                  {'account_id':'1'*64,'account_email':'first@example.test','usage_before':before(30),'usage_after':before(90)},
                  {'account_id':'2'*64,'account_email':'second@example.test','usage_before':before(10),'usage_after':before(12)}]}
        execution={'attempt':{'run_id':'123','run_attempt':1,'agent_outcome':'completed','stopped':1},
                   'session':{'finished':1,'data':data}}
        reports=dict(attempt_reports(execution,self.f.registry))
        self.assertIn('first@example.test',reports['start'])
        self.assertNotIn('second@example.test',reports['start'])
        self.assertIn('usage change +60 percentage points',reports['finish'])
        self.assertIn('usage change +2 percentage points',reports['finish'])
        self.assertIn('Account changes: 1',reports['finish'])

    def test_old_generation_completed_issue_is_ignored_until_it_becomes_eligible(self):
        f=self.f
        issue=copy.deepcopy(self.fixture.second_issue)
        issue['state']={'id':'completed-state','type':'completed'}
        prior=f.controller.apply(IssueObserved(issue['id'],f.route.project_id,f.route.repository,'old-generation',
                                               issue['identifier'],False,True),now=1000)
        prior=replace(prior,phase='completed')
        self.assertIs(self.driver.observe_issue(issue,known=prior),prior)
        self.assertEqual(len(f.controller.tasks()),2)

    def test_reporting_upgrade_requeues_execution_history_once_without_resetting_completion(self):
        f=self.f
        view=f.controller.inspect(f.view.task_id)
        f.review()
        f.preflight(Role.CODING_SELECT,f.account_b)
        f.call(Role.CODING,'start',protocol=PROTOCOL)
        f.controller.apply(Finish(view.task_id,f.key,'coding','completed'),now=1000)
        f.controller.apply(ExternalObserved(view.task_id,f.key,'123',1,True,'success',True,True,pull_state='absent'),now=1000)
        for effect in f.controller.effects('report'):
            f.controller.apply(EffectUpdated(view.task_id,effect['effect_id'],'done'),now=1000)
        f.controller.apply(Initialize(),now=1000)
        self.assertTrue(f.controller.effects('report'))
        self.assertEqual(f.controller.inspect(view.task_id).phase,'completed')
        for effect in f.controller.effects('report'):
            f.controller.apply(EffectUpdated(view.task_id,effect['effect_id'],'done'),now=1000)
        f.controller.apply(Initialize(),now=1001)
        self.assertFalse(f.controller.effects('report'))

    def deny_review(self):
        self.fixture.second_issue['state']={'id':'completed-state','type':'completed'}
        self.driver.tick(scan=True)
        f=self.f
        f.preflight(Role.REVIEW_SELECT,f.account_a)
        config=f.call(Role.REVIEW,'start',protocol=PROTOCOL)
        f.call(Role.REVIEW,'review',binding=config['binding'],result=clarification())

    def test_immediate_resubmission_keeps_review_discussion_and_clarification_until_a_pass(self):
        self.deny_review()
        f=self.f
        self.driver.report()
        self.fixture.runs[f.key].update(status='completed',conclusion='success')
        self.driver.tick()
        f.issue['state']={'id':f.route.ready_state_ids[0],'type':'unstarted'}
        self.driver.tick(scan=True)
        current=f.controller.inspect(f.view.task_id)
        self.assertEqual(current.attempt['ordinal'],2)
        self.assertEqual(current.phase,'reviewing')
        f.linear.set_clarification.assert_called_with(f.view.issue_id,True)
        feedback=[call for call in f.linear.status_comment.call_args_list if 'Who may call the endpoint' in call.args[2]]
        self.assertEqual(len(feedback),1)
        f.key=current.attempt['attempt_id']
        f.call=lambda role,action,**fields:f.broker.handle(action,{**claims(role),'run_id':'124'}, {'dispatch_key':f.key,**fields})
        f.preflight(Role.REVIEW_SELECT,f.account_a)
        config=f.call(Role.REVIEW,'start',protocol=PROTOCOL)
        f.call(Role.REVIEW,'review',binding=config['binding'],result=ready())
        self.driver.report()
        f.linear.set_clarification.assert_called_with(f.view.issue_id,False)
        self.assertEqual(len([call for call in f.linear.status_comment.call_args_list if 'Who may call the endpoint' in call.args[2]]),1)

    def test_failed_rejection_reporting_cannot_create_an_automatic_review_loop(self):
        self.deny_review()
        f=self.f
        f.linear.set_state.side_effect=RuntimeError('unavailable')
        self.fixture.runs[f.key].update(status='completed',conclusion='success')
        with self.assertLogs(level='WARNING'):
            self.driver.tick()
            self.driver.tick(scan=True)
        self.assertEqual(f.github.dispatch.call_count,1)
        self.assertEqual(f.controller.inspect(f.view.task_id).phase,'held')

    def test_archived_issue_keeps_reporting_intent_without_changing_tracker_state(self):
        f=self.f
        f.issue['archivedAt']='2026-10-30T00:00:00Z'
        self.driver.observe_issue(f.issue,known=f.controller.inspect(f.view.task_id))
        f.linear.reset_mock()
        self.driver.report()
        f.linear.status_comment.assert_not_called()
        f.linear.set_state.assert_not_called()
        self.assertTrue(f.controller.effects('report'))
