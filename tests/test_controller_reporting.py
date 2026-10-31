"""Live broker results retain existing immutable start and per-account usage reports."""

import copy
from dataclasses import replace
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'automation'),str(ROOT/'automation')]
from controller.model import Initialize,IssueObserved,Role,Finish,ExternalObserved,SessionUpdated,Phase
from controller.reporting import attempt_reports,execution_status
from common.clients import Linear,RequestError
from common.policy import PROTOCOL,CRITERIA
from common.reporting import CLARIFICATION_LABEL,EXECUTION_STATUS_LABELS,QUEUED_LABEL,SELECTION_LABEL,STAGE_LABELS
from call_budget import CallBudget
from label_api import LabelAPI
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
        self.labels=LabelAPI()
        for issue_id in self.fixture.issues:
            self.labels.add_issue(issue_id,['bug'])
        self.label_client=Linear('fixture-token')
        self.label_client.query=Mock(side_effect=self.labels.query)
        self.f.linear.set_execution_status.side_effect=self.label_client.set_execution_status
        self.f.linear.set_label.side_effect=self.label_client.set_label

    def assert_status(self,issue_id,expected):
        statuses=[name for name in self.labels.names(issue_id) if name in EXECUTION_STATUS_LABELS]
        self.assertEqual(statuses,[expected] if expected else [])

    def test_queue_review_and_admission_project_one_status_without_changing_ownership(self):
        f=self.f
        self.driver.tick(scan=True)
        self.assert_status(f.issue['id'],SELECTION_LABEL)
        self.assert_status(self.fixture.second_issue['id'],QUEUED_LABEL)
        f.preflight(Role.REVIEW_SELECT,f.account_a)
        config=f.call(Role.REVIEW,'start',protocol=PROTOCOL)
        self.driver.report()
        self.assert_status(f.issue['id'],SELECTION_LABEL)
        self.assertEqual(f.controller.inspect(f.view.task_id).session('review')['account_id'],f.account_a)
        self.assertTrue(f.controller.inspect(f.view.task_id).active)
        f.call(Role.REVIEW,'review',binding=config['binding'],result=ready())
        self.driver.report()
        self.assert_status(f.issue['id'],SELECTION_LABEL)
        f.preflight(Role.CODING_SELECT,f.account_b)
        f.call(Role.CODING,'start',protocol=PROTOCOL)
        url=f'https://github.com/{f.route.repository}/pull/1'
        f.call(Role.CODING,'finish',status='completed',pull_request=url)
        self.fixture.runs[f.key].update(status='completed',conclusion='success')
        f.github.worker_pull.side_effect=lambda route,receipt,key,url='': (
            {'merged':True,'state':'MERGED','url':f'https://github.com/{f.route.repository}/pull/1'}
            if key==f.view.task_id else None)
        f.github.branch_sha.return_value='c'*40
        self.driver.tick()
        self.assert_status(f.issue['id'],None)
        self.assert_status(self.fixture.second_issue['id'],SELECTION_LABEL)
        second=next(v for v in f.controller.tasks() if v.issue_id==self.fixture.second_issue['id'])
        self.assertTrue(second.active)
        self.assertEqual(second.spec.context_sha,'c'*40)
        self.assertEqual(f.github.dispatch.call_count,2)

    def test_real_coding_results_report_usage_time_and_keep_start_immutable(self):
        f=self.f
        self.driver.tick(scan=True)
        f.review()
        f.preflight(Role.CODING_SELECT,f.account_b)
        f.call(Role.CODING,'start',protocol=PROTOCOL)
        for stage in ('initializing','selecting_account'):
            f.call(Role.CODING,'progress',stage=stage)
            self.driver.report()
            self.assert_status(f.issue['id'],STAGE_LABELS[stage])
            self.assertTrue(f.controller.inspect(f.view.task_id).active)
        leased=f.call(Role.CODING,'claim',account_id=f.account_b)
        before={'observed_at':1000,'windows':[{'name':'primary','used_percent':40,'resets_at':2000,'duration_minutes':300}]}
        f.call(Role.CODING,'progress',stage='coding',context_sha=f.spec.context_sha,
               account_id=f.account_b,usage_before=before,account_email='fixture@example.test',account_plan='pro')
        self.driver.report()
        self.assert_status(f.issue['id'],STAGE_LABELS['coding'])
        starts=[call for call in f.linear.status_comment.call_args_list if 'development started.' in call.args[2]]
        self.assertEqual(len(starts),1)
        self.assertIn('- primary (300-minute): **40% used** / 60% remaining',starts[0].args[2])
        self.assertIn('fixture@example.test',starts[0].args[2])
        f.call(Role.CODING,'heartbeat')
        self.driver.report()
        self.assertEqual(len([call for call in f.linear.status_comment.call_args_list if 'development started.' in call.args[2]]),1)
        after=copy.deepcopy(before)
        after['observed_at']=1123
        after['windows'][0]['used_percent']=43
        f.call(Role.CODING,'progress',stage='testing',usage_after=after,usage_account_id=f.account_b)
        self.driver.report()
        self.assert_status(f.issue['id'],STAGE_LABELS['testing'])
        f.call(Role.CODING,'progress',stage='publishing')
        self.driver.report()
        self.assert_status(f.issue['id'],STAGE_LABELS['publishing'])
        f.call(Role.CODING,'save',account_id=f.account_b,lease=leased['lease'],release=True)
        f.broker.clock=lambda:1123
        f.call(Role.CODING,'finish',status='completed')
        self.driver.report()
        finishes=[call for call in f.linear.status_comment.call_args_list if 'development finished.' in call.args[2]]
        self.assertEqual(len(finishes),1)
        self.assertIn('0h 2m 3s',finishes[0].args[2])
        self.assertIn('- primary (300-minute): 40% used / 60% remaining → **43% used** / 57% remaining',finishes[0].args[2])
        self.assertIn('usage change +3 percentage points',finishes[0].args[2])
        self.assertEqual(len([call for call in f.linear.status_comment.call_args_list if 'development started.' in call.args[2]]),1)
        f.controller.apply(ExternalObserved(f.view.task_id,f.key,'123',1,True,'success',True,True,pull_state='absent'),now=1123)
        self.driver.report()
        self.assert_status(f.issue['id'],None)
        self.assertFalse(f.controller.inspect(f.view.task_id).active)
        self.assertTrue(all(len(set(names)&EXECUTION_STATUS_LABELS)<=1 for _,names in self.labels.snapshots))

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
        self.assertIn('- primary (300-minute): **30% used** / 70% remaining',reports['start'])
        self.assertNotIn('second@example.test',reports['start'])
        self.assertIn('- primary (300-minute): 30% used / 70% remaining → **90% used** / 10% remaining',reports['finish'])
        self.assertIn('- primary (300-minute): 10% used / 90% remaining → **12% used** / 88% remaining',reports['finish'])
        self.assertIn('usage change +60 percentage points',reports['finish'])
        self.assertIn('usage change +2 percentage points',reports['finish'])
        self.assertIn('Account changes: 1',reports['finish'])
        del data['usage_segments'][0]['usage_after']
        missing=dict(attempt_reports(execution,self.f.registry))
        self.assertEqual(missing['start'],reports['start'])
        first,second=missing['finish'].split('Account 2:')
        self.assertIn('final reading unavailable; usage change unavailable.',first)
        self.assertIn('- primary (300-minute): 30% used / 70% remaining',first)
        self.assertNotIn('**',first)
        self.assertIn('10% used / 90% remaining → **12% used** / 88% remaining',second)

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
        self.driver.report()
        settled=f.controller.inspect(view.task_id)
        signature=f.controller.report_signature('task:'+view.task_id)
        self.labels.add_issue(view.issue_id,[*sorted(EXECUTION_STATUS_LABELS),'bug'])
        f.controller.apply(Initialize(),now=1000)
        self.assertTrue(f.controller.effects('report'))
        refreshed=f.controller.inspect(view.task_id)
        self.assertEqual(replace(refreshed,revision=settled.revision),settled)
        self.label_client.query.side_effect=RequestError()
        with self.assertLogs(level='WARNING'):
            self.driver.report()
        self.assertTrue(f.controller.effects('report'))
        self.assertEqual(f.controller.report_signature('task:'+view.task_id),signature)
        self.label_client.query.side_effect=self.labels.query
        self.driver.report()
        self.assert_status(view.issue_id,None)
        self.assertFalse(f.controller.effects('report'))
        f.controller.apply(Initialize(),now=1001)
        self.assertFalse(f.controller.effects('report'))

    def quota_wait(self,role):
        f=self.f
        self.driver.tick(scan=True)
        if role.phase=='coding':
            f.review()
        f.call(role,'start',protocol=PROTOCOL)
        f.call(role,'finish',status='waiting_for_quota',quota_wait={'codex_busy':1})
        self.driver.report()
        self.assert_status(f.issue['id'],STAGE_LABELS['waiting_for_quota'])
        held=f.controller.inspect(f.view.task_id)
        self.assertTrue(held.active)
        self.assertEqual(held.attempt['retry_kind'],'capacity')
        self.fixture.runs[f.key].update(status='completed',conclusion='success')
        self.driver.tick()
        self.assert_status(f.issue['id'],STAGE_LABELS['waiting_for_quota'])
        self.assertEqual(f.github.dispatch.call_count,1)
        self.driver.clock=lambda:1900
        self.driver.tick()
        retried=f.controller.inspect(f.view.task_id)
        self.assertEqual(retried.attempt['ordinal'],2)
        self.assertTrue(retried.active)
        self.assert_status(f.issue['id'],SELECTION_LABEL)
        self.assert_status(self.fixture.second_issue['id'],QUEUED_LABEL)
        self.assertEqual(f.github.dispatch.call_count,2)

    def test_review_quota_wait_replaces_selection_and_preserves_retry_and_grant(self):
        self.quota_wait(Role.REVIEW_SELECT)

    def test_coding_quota_wait_replaces_selection_and_preserves_retry_and_grant(self):
        self.quota_wait(Role.CODING_SELECT)

    def test_pr_checks_keep_one_review_indicator_and_run_links_until_verified_merge(self):
        f=self.f
        self.driver.tick(scan=True)
        f.review()
        f.preflight(Role.CODING_SELECT,f.account_b)
        f.call(Role.CODING,'start',protocol=PROTOCOL)
        url=f'https://github.com/{f.route.repository}/pull/1'
        f.call(Role.CODING,'finish',status='completed',pull_request=url)
        self.driver.report()
        self.assert_status(f.issue['id'],STAGE_LABELS['review'])
        self.fixture.runs[f.key].update(status='completed',conclusion='success')
        pull={'merged':False,'state':'OPEN','url':url}
        f.github.worker_pull.side_effect=lambda route,receipt,key,url='':pull if key==f.view.task_id else None
        self.driver.tick()
        self.assert_status(f.issue['id'],STAGE_LABELS['review'])
        self.assertTrue(f.controller.inspect(f.view.task_id).active)
        comments=[call.args[2] for call in f.linear.status_comment.call_args_list if call.args[0]==f.issue['id']]
        self.assertIn('Waiting for required CI checks to appear.',comments[-1])
        self.assertIn('[Worker run]',comments[-1])
        self.assertIn(f'[Pull request]({url})',comments[-1])
        check={'__typename':'CheckRun','isRequired':True,'name':'tests','status':'IN_PROGRESS','conclusion':None}
        pull.update(commits={'nodes':[{'commit':{'statusCheckRollup':{
            'contexts':{'nodes':[check],'pageInfo':{'hasNextPage':False}}}}}]},
                    autoMergeRequest={'enabledAt':'2026-10-31T00:00:00Z'},mergeable='MERGEABLE',mergeStateStatus='CLEAN')
        self.driver.tick()
        self.assert_status(f.issue['id'],STAGE_LABELS['review'])
        check.update(status='COMPLETED',conclusion='SUCCESS')
        self.driver.tick()
        self.assert_status(f.issue['id'],STAGE_LABELS['review'])
        self.assertTrue(f.controller.inspect(f.view.task_id).active)
        pull.update(merged=True,state='MERGED')
        self.driver.tick()
        self.assert_status(f.issue['id'],None)
        self.assertFalse(f.controller.inspect(f.view.task_id).active)

    def test_canceled_and_skipped_tasks_remove_every_owned_status(self):
        f=self.f
        self.driver.tick(scan=True)
        f.issue['state']={'id':'canceled-state','type':'canceled'}
        self.driver.observe_issue(f.issue,known=f.controller.inspect(f.view.task_id))
        canceled=f.controller.apply(ExternalObserved(f.view.task_id,f.key,'123',1,True,'cancelled',True,True,pull_state='absent'),now=1000)
        second=next(v for v in f.controller.tasks() if v.issue_id==self.fixture.second_issue['id'])
        self.fixture.second_issue['state']={'id':'completed-state','type':'completed'}
        skipped=self.driver.observe_issue(self.fixture.second_issue,known=second)
        self.assertEqual((canceled.phase,skipped.phase),('canceled','skipped'))
        for view in (canceled,skipped):
            self.labels.add_issue(view.issue_id,[*sorted(EXECUTION_STATUS_LABELS),'bug'])
        self.driver.report()
        for view in (canceled,skipped):
            self.assert_status(view.issue_id,None)
            self.assertEqual(self.labels.names(view.issue_id),['bug'])

    def test_failed_stage_replacement_does_not_confirm_report_or_change_execution(self):
        f=self.f
        self.driver.tick(scan=True)
        signature=f.controller.report_signature('task:'+f.view.task_id)
        f.review()
        f.preflight(Role.CODING_SELECT,f.account_b)
        f.call(Role.CODING,'start',protocol=PROTOCOL)
        f.call(Role.CODING,'progress',stage='selecting_account')
        before=f.controller.inspect(f.view.task_id)
        def fail_add(query,variables):
            if 'issueAddLabel(' in query:
                return {'issueAddLabel':{'success':False}}
            return self.labels.query(query,variables)
        self.label_client.query.side_effect=fail_add
        with self.assertLogs(level='WARNING'):
            self.driver.report()
        self.assert_status(f.issue['id'],None)
        self.assertEqual(f.controller.report_signature('task:'+f.view.task_id),signature)
        self.assertEqual(f.controller.inspect(f.view.task_id),before)
        self.assertTrue(f.controller.effects('report'))
        self.label_client.query.side_effect=self.labels.query
        self.driver.report()
        self.assert_status(f.issue['id'],STAGE_LABELS['selecting_account'])
        self.assertFalse(f.controller.effects('report'))
        self.assertEqual(f.github.dispatch.call_count,1)
        self.assertEqual(f.controller.inspect(f.view.task_id).session('coding')['account_id'],f.account_b)

    def test_unchanged_projection_still_cleans_stale_labels_and_retries_failed_cleanup(self):
        f=self.f
        self.driver.tick(scan=True)
        f.preflight(Role.REVIEW_SELECT,f.account_a)
        self.driver.report()
        signature=f.controller.report_signature('task:'+f.view.task_id)
        comment_count=f.linear.status_comment.call_count
        self.labels.add_issue(f.issue['id'],[SELECTION_LABEL,STAGE_LABELS['selecting_account'],'bug'])
        f.controller.apply(SessionUpdated(f.view.task_id,f.key,'review',{'heartbeat_until':1001}),now=1000)
        def fail_remove(query,variables):
            if 'issueRemoveLabel(' in query:
                return {'issueRemoveLabel':{'success':False}}
            return self.labels.query(query,variables)
        self.label_client.query.side_effect=fail_remove
        with CallBudget(self,linear=f.linear,github=f.github,exact={'linear.issue':1,'linear.set_execution_status':1}), \
             self.assertLogs(level='WARNING'):
            self.driver.report()
        self.assertTrue(f.controller.effects('report'))
        self.label_client.query.side_effect=self.labels.query
        with CallBudget(self,linear=f.linear,github=f.github,exact={'linear.issue':1,'linear.set_execution_status':1}):
            self.driver.report()
        self.assert_status(f.issue['id'],SELECTION_LABEL)
        self.assertEqual(f.linear.status_comment.call_count,comment_count)
        self.assertEqual(f.controller.report_signature('task:'+f.view.task_id),signature)
        self.assertFalse(f.controller.effects('report'))
        self.assertEqual(f.github.dispatch.call_count,1)
        with CallBudget(self,linear=f.linear,github=f.github):
            self.driver.report()

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
        self.assert_status(f.issue['id'],CLARIFICATION_LABEL)
        self.assertTrue(f.controller.inspect(f.view.task_id).active)
        self.fixture.runs[f.key].update(status='completed',conclusion='success')
        self.driver.tick()
        self.assert_status(f.issue['id'],CLARIFICATION_LABEL)
        self.assertFalse(f.controller.inspect(f.view.task_id).active)
        f.issue['state']={'id':f.route.ready_state_ids[0],'type':'unstarted'}
        self.driver.tick(scan=True)
        current=f.controller.inspect(f.view.task_id)
        self.assertEqual(current.attempt['ordinal'],2)
        self.assertEqual(current.phase,'reviewing')
        self.assert_status(f.issue['id'],CLARIFICATION_LABEL)
        feedback=[call for call in f.linear.status_comment.call_args_list if 'Who may call the endpoint' in call.args[2]]
        self.assertEqual(len(feedback),1)
        f.key=current.attempt['attempt_id']
        f.call=lambda role,action,**fields:f.broker.handle(action,{**claims(role),'run_id':'124'}, {'dispatch_key':f.key,**fields})
        f.preflight(Role.REVIEW_SELECT,f.account_a)
        config=f.call(Role.REVIEW,'start',protocol=PROTOCOL)
        f.call(Role.REVIEW,'review',binding=config['binding'],result=ready())
        self.driver.report()
        self.assert_status(f.issue['id'],SELECTION_LABEL)
        self.assertEqual(len([call for call in f.linear.status_comment.call_args_list if 'Who may call the endpoint' in call.args[2]]),1)

    def test_temporary_review_retry_does_not_resolve_clarification_feedback(self):
        self.deny_review()
        f=self.f
        self.driver.report()
        self.fixture.runs[f.key].update(status='completed',conclusion='success')
        self.driver.tick()
        f.issue['state']={'id':f.route.ready_state_ids[0],'type':'unstarted'}
        self.driver.tick(scan=True)
        f.key=f.controller.inspect(f.view.task_id).attempt['attempt_id']
        f.call=lambda role,action,**fields:f.broker.handle(action,{**claims(role),'run_id':'124'}, {'dispatch_key':f.key,**fields})
        f.preflight(Role.REVIEW_SELECT,f.account_a)
        config=f.call(Role.REVIEW,'start',protocol=PROTOCOL)
        result=ready()
        result.update(verdict='retry',summary='Repository context is temporarily unavailable.')
        result['criteria'][CRITERIA[0]]={'status':'unknown','reason':'Context unavailable.'}
        f.call(Role.REVIEW,'review',binding=config['binding'],result=result)
        self.driver.report()
        self.assert_status(f.issue['id'],CLARIFICATION_LABEL)
        held=f.controller.inspect(f.view.task_id)
        self.assertEqual(held.attempt['retry_kind'],'review')
        self.assertTrue(held.active)

    def test_status_precedence_uses_history_and_ignores_stale_stages_outside_execution(self):
        view=self.f.controller.inspect(self.f.view.task_id)
        feedback=[{'result':clarification()},{'result':{'verdict':'retry'}}]
        coding=replace(view,phase=Phase.EXECUTING,attempt={**view.attempt,'stage':'coding'})
        self.assertEqual(execution_status(coding,feedback),CLARIFICATION_LABEL)
        self.assertEqual(execution_status(coding,[*feedback,{'result':ready()}]),STAGE_LABELS['coding'])
        self.assertEqual(execution_status(replace(coding,phase=Phase.PUBLISHED),[]),STAGE_LABELS['review'])
        self.assertEqual(execution_status(replace(coding,phase=Phase.HELD),[]),SELECTION_LABEL)
        self.assertIsNone(execution_status(replace(coding,phase=Phase.HELD,active=False),[]))
        for phase in (Phase.COMPLETED,Phase.CANCELED,Phase.SKIPPED):
            with self.subTest(phase=phase):
                self.assertIsNone(execution_status(replace(coding,phase=phase),feedback))

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
