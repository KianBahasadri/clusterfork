"""Project holds cross real SQLite, dispatch and broker boundaries."""

import sqlite3
import unittest

import test_controller_driver as driver_fixtures
import test_controller as core_fixtures
from common.routing import PROJECT_PAUSE_LABEL, eligibility
from common.policy import PROTOCOL
from controller.core import Controller
from controller.model import Admit, Assign, DispatchDeferred, EffectUpdated, Rejected, Review, Role
from controller.reporting import execution_status
from controller.store import Database
from call_budget import CallBudget


class ProjectPauseTests(unittest.TestCase):
    def setUp(self):
        self.harness = driver_fixtures.ControllerDriverTests(methodName='runTest')
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.f = self.harness.fixture
        self.driver = self.harness.driver

    def pause(self, paused=True):
        for issue in self.harness.issues.values():
            if issue['project']['id'] == self.f.route.project_id:
                issue['project']['labels']['nodes'] = [{'name': self.f.registry.enrollment_label}]
                if paused:
                    issue['project']['labels']['nodes'].append({'name': PROJECT_PAUSE_LABEL})

    def test_unseen_paused_issues_never_enter_the_queue(self):
        self.pause()
        with CallBudget(self, linear=self.f.linear, github=self.f.github):
            self.assertIsNone(self.driver.observe_issue(self.harness.second_issue))
        self.assertEqual(len(self.f.controller.tasks()), 1)
        self.assertEqual(eligibility(self.f.issue, self.f.registry), (None, 'project_paused'))

    def test_issue_label_does_not_pause_a_project(self):
        self.f.issue['labels'] = {'nodes': [{'name': PROJECT_PAUSE_LABEL}]}
        self.driver.dispatch()
        self.assertEqual(self.f.github.dispatch.call_count, 1)
        self.assertFalse(self.f.controller.inspect(self.f.view.task_id).project_paused)

    def test_existing_queued_issue_loses_queued_marker_while_paused(self):
        queued = self.driver.observe_issue(self.harness.second_issue)
        self.assertEqual(queued.phase, 'queued')
        self.pause()
        self.driver.scan()
        paused = self.f.controller.inspect(queued.task_id)
        self.assertTrue(paused.project_paused)
        self.assertFalse(paused.active)
        self.assertFalse(self.f.controller.waiting())
        self.assertIsNone(execution_status(paused, []))

    def test_pause_without_enrollment_does_not_enroll_a_project(self):
        self.f.issue['project']['labels']['nodes'] = [{'name': PROJECT_PAUSE_LABEL}]
        self.assertEqual(eligibility(self.f.issue, self.f.registry), (None, 'project_not_enrolled'))

    def test_pause_between_admission_and_dispatch_retires_only_unsent_intent(self):
        self.pause()
        with CallBudget(self, linear=self.f.linear, github=self.f.github,
                        exact={'linear.issue': 1, 'linear.issue_dependencies': 1}):
            self.driver.dispatch()
        self.driver.tick()
        view = self.f.controller.inspect(self.f.view.task_id)
        self.assertTrue(view.project_paused)
        self.assertFalse(view.active)
        self.assertEqual(view.phase, 'queued')
        self.assertEqual(view.review_attempts, 0)
        self.assertEqual(self.f.github.dispatch.call_count, 0)
        self.assertIn('paused', self.driver.render(view))
        self.pause(False)
        self.driver.tick()
        self.assertEqual(self.f.github.dispatch.call_count, 1)
        self.assertEqual(len(self.f.controller.attempt_history(view.task_id)), 2)

    def test_dispatched_phase_cannot_start_after_project_pause(self):
        self.driver.dispatch()
        self.pause()
        self.f.broker.refresh = self.driver.refresh
        with self.assertRaisesRegex(Rejected, 'Project remote development is paused'):
            self.f.call(Role.REVIEW_SELECT, 'start', protocol=PROTOCOL)
        view = self.f.controller.inspect(self.f.view.task_id)
        self.assertFalse(view.sessions)
        for account in (self.f.account_a, self.f.account_b):
            self.assertFalse(self.f.pool.get(account)[0].get('owner'))

    def test_running_review_finishes_without_launching_coding(self):
        self.driver.dispatch()
        self.f.preflight(Role.REVIEW_SELECT, self.f.account_a)
        config = self.f.call(Role.REVIEW, 'start', protocol=PROTOCOL)
        self.pause()
        self.f.broker.refresh = self.driver.refresh
        self.f.call(Role.REVIEW, 'review', binding=config['binding'], result=core_fixtures.ready())
        self.harness.runs[self.f.key].update(status='completed', conclusion='success')
        self.driver.tick()
        view = self.f.controller.inspect(self.f.view.task_id)
        self.assertEqual(view.phase, 'queued')
        self.assertFalse(view.active)
        self.assertEqual(self.f.github.dispatch.call_count, 1)
        self.assertEqual(view.attempt['execution_phase'], 'review')
        self.assertEqual(view.attempt['retry_kind'], '')

    def test_running_coding_can_finish_while_project_is_paused(self):
        self.driver.tick(scan=True)
        self.f.review()
        self.f.preflight(Role.CODING_SELECT, self.f.account_b)
        self.f.call(Role.CODING, 'start', protocol=PROTOCOL)
        self.pause()
        self.f.broker.refresh = self.driver.refresh
        self.f.advance('finished')
        self.f.call(Role.CODING, 'finish', status='completed')
        self.harness.runs[self.f.key].update(status='completed', conclusion='success')
        self.driver.tick()
        self.assertEqual(self.f.controller.inspect(self.f.view.task_id).phase, 'completed')
        self.assertEqual(self.f.github.dispatch.call_count, 2)

    def test_incomplete_project_labels_cannot_dispatch(self):
        self.f.issue['project']['labels']['pageInfo'] = {'hasNextPage': True}
        self.driver.dispatch()
        self.assertEqual(self.f.github.dispatch.call_count, 0)
        self.assertEqual(self.f.controller.effects('dispatch')[0]['state'], 'pending')

    def test_ambiguous_dispatch_remains_owned_during_pause(self):
        from common.routing import DispatchError
        self.f.github.dispatch.side_effect = DispatchError('unknown', uncertain=True)
        self.driver.dispatch()
        self.pause()
        self.driver.tick()
        view = self.f.controller.inspect(self.f.view.task_id)
        self.assertTrue(view.active)
        self.assertEqual(view.attempt['dispatch_status'], 'unknown')
        self.assertEqual(self.f.github.dispatch.call_count, 1)


class ProjectPausePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.f = core_fixtures.ControllerTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_paused_head_does_not_block_another_project_in_same_repository(self):
        first = self.f.issue(project_paused=True, priority=1)
        second = self.f.issue(2, project_id='other-project', priority=4)
        with self.assertRaises(Rejected):
            self.f.apply(Admit(first.task_id, core_fixtures.SPEC))
        admitted = self.f.apply(Admit(second.task_id, core_fixtures.SPEC))
        self.assertTrue(admitted.active)

    def test_claimed_dispatch_cannot_be_declared_unsent_by_pause(self):
        view = self.f.admit()
        effect = self.f.controller.effects('dispatch')[0]
        self.f.apply(EffectUpdated(view.task_id, effect['effect_id'], 'claimed'))
        self.f.issue(project_paused=True)
        with self.assertRaisesRegex(Rejected, 'Only unsent'):
            self.f.apply(DispatchDeferred(view.task_id, view.attempt['attempt_id']))
        retained = self.f.controller.inspect(view.task_id)
        self.assertTrue(retained.active)
        self.assertFalse(retained.attempt['stopped'])

    def test_paused_review_retains_open_publication_until_resume(self):
        view = self.f.admit()
        self.f.start(view, Role.REVIEW_SELECT)
        self.f.apply(Assign(view.task_id, view.attempt['attempt_id'], Role.REVIEW_SELECT, core_fixtures.ACCOUNT_A))
        self.f.start(view, Role.REVIEW)
        reviewed = self.f.apply(Review(view.task_id, view.attempt['attempt_id'], core_fixtures.ready(), core_fixtures.SPEC))
        self.f.issue(project_paused=True)
        paused = self.f.external(reviewed, pull_state='open', pull_request='https://github.com/Owner/Repo/pull/1')
        self.assertTrue(paused.active)
        self.assertEqual(paused.phase, 'held')
        self.assertFalse(paused.review_attention)
        self.assertEqual(paused.attempt['execution_phase'], 'review')
        self.f.issue(project_paused=False)
        resumed = self.f.external(paused, pull_state='open', pull_request=paused.attempt['pull_request'])
        self.assertTrue(resumed.active)
        self.assertEqual(resumed.attempt['execution_phase'], 'coding')

    def test_pause_persists_across_restart_and_preserves_retry_deadline(self):
        self.f.controller = Controller(self.f.database, autonomous=True)
        view = self.f.admit()
        self.f.start(view, Role.REVIEW_SELECT)
        self.f.apply(Assign(view.task_id, view.attempt['attempt_id'], Role.REVIEW_SELECT, core_fixtures.ACCOUNT_A))
        self.f.start(view, Role.REVIEW)
        held = self.f.apply(Review(view.task_id, view.attempt['attempt_id'], {'invalid': True}, core_fixtures.SPEC))
        stopped = self.f.external(held)
        deadline = stopped.attempt['retry_at']
        self.f.issue(project_paused=True)
        self.f.database.close()
        self.f.database = Database(self.f.path)
        self.addCleanup(self.f.database.close)
        self.f.controller = Controller(self.f.database)
        paused = self.f.controller.inspect(view.task_id)
        self.assertTrue(paused.project_paused)
        self.assertEqual(paused.attempt['retry_at'], deadline)
        self.assertFalse(self.f.controller.waiting())
        self.f.now = deadline + 1
        with self.assertRaises(Rejected):
            self.f.apply(Admit(view.task_id, core_fixtures.SPEC))
        self.f.issue(project_paused=False)
        resumed = self.f.apply(Admit(view.task_id, core_fixtures.SPEC))
        self.assertEqual(resumed.attempt['ordinal'], 2)

    def test_version_twelve_upgrade_preserves_active_ownership(self):
        view = self.f.admit()
        self.f.database.close()
        with sqlite3.connect(self.f.path) as db:
            db.execute('ALTER TABLE tasks DROP COLUMN project_paused')
            db.execute('PRAGMA user_version=12')
        self.f.database = Database(self.f.path)
        self.addCleanup(self.f.database.close)
        restored = Controller(self.f.database).inspect(view.task_id)
        self.assertTrue(restored.active)
        self.assertFalse(restored.project_paused)
        self.assertEqual(restored.attempt, view.attempt)
