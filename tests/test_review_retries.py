"""Readiness cycles are bounded across workers, observations and SQLite restarts."""

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'automation'))
from controller.core import Controller
from controller.model import (Admit, Assign, ContextObserved, DispatchObserved, ExternalObserved,
                              Finish, Hold, InboxQueued, Rejected, Resubmit, Retry, Review, Role, Start, identity)
from controller.store import Database
from common.policy import PROTOCOL
from call_budget import CallBudget
from test_readiness import missing_evidence, ready
import test_controller as fixtures
import test_controller_driver as driver_fixtures

TIMEOUT = {'operation': 'worker/readiness', 'kind': 'timeout'}


class ReviewRetryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ControllerTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def begin(self, view, *, assess=True):
        f = self.f
        run = str(122 + view.attempt['ordinal'])
        f.apply(Start(view.task_id, view.attempt['attempt_id'], run, 1, Role.REVIEW_SELECT))
        if assess:
            f.apply(Assign(view.task_id, view.attempt['attempt_id'], Role.REVIEW_SELECT, fixtures.ACCOUNT_A))
            f.apply(Start(view.task_id, view.attempt['attempt_id'], run, 1, Role.REVIEW))

    def stop(self, view, **fields):
        return self.f.apply(ExternalObserved(view.task_id, view.attempt['attempt_id'],
                            str(122 + view.attempt['ordinal']), 1, **{
                                'stopped': True, 'conclusion': 'failure', 'all_attempts_stopped': True,
                                'publication_checked': True, 'pull_state': 'absent', **fields}))

    def retry(self, view):
        self.f.now = view.attempt['retry_at']
        return self.f.apply(Retry(view.task_id, fixtures.SPEC, 'Another observation', view.attempt['retry_kind']))

    def reopen(self):
        f = self.f
        f.database.close()
        f.database = Database(f.path)
        self.addCleanup(f.database.close)
        f.controller = Controller(f.database)

    def test_verified_failures_use_two_delays_and_exhaust_across_restarts_and_duplicate_callbacks(self):
        f = self.f
        view = f.admit()
        failures = [TIMEOUT, {'operation': 'http/other', 'kind': 'http_error', 'http_status': 503},
                    {'operation': 'worker/agent', 'kind': 'transport_error', 'error_type': 'ConnectionError'}]
        for index, diagnostic in enumerate(failures):
            self.begin(view)
            finish = Finish(view.task_id, view.attempt['attempt_id'], 'review', 'failed', failure=diagnostic)
            failed = f.apply(finish)
            self.assertEqual(failed.review_attempts, index+1)
            self.assertTrue(failed.active)  # A callback does not prove stop.
            if index < 2:
                self.assertEqual(failed.attempt['retry_at'], f.now + (300, 900)[index])
                self.assertIn(f'{2-index} attempt(s) remaining', failed.reason)
                self.assertEqual(failed.attempt['retry_decision'], 'retry')
                with self.assertRaises(Rejected):
                    self.retry(failed)
                # Retry authorization, not callback wording, owns the clock.
                f.now -= 10
                self.assertEqual(f.apply(replace(finish, reason='a different error')).attempt['retry_at'], failed.attempt['retry_at'])
            else:
                self.assertIn('exhausted all 3', failed.reason)
                self.assertEqual(failed.attempt['retry_kind'], '')
                self.assertEqual(failed.attempt['retry_at'], 0)
            stopped = self.stop(view)
            self.reopen()
            restored = f.controller.inspect(view.task_id)
            self.assertEqual(restored.review_attempts, index+1)
            self.assertEqual(restored.attempt['retry_decision'], stopped.attempt['retry_decision'])
            if index < 2:
                view = self.retry(restored)
        self.assertFalse(restored.active)
        self.assertEqual(len(f.controller.attempt_history(view.task_id)), 3)
        with self.assertRaises(Rejected):
            f.apply(Retry(view.task_id, fixtures.SPEC, 'try again', 'review'))

    def test_timeout_before_worker_startup_cannot_bypass_the_same_budget(self):
        view = self.f.admit()
        for index in range(3):
            failed = self.stop(view, conclusion='timed_out')  # No session or model result.
            self.assertEqual(failed.review_attempts, index+1)
            self.assertFalse(failed.sessions)
            if index < 2:
                view = self.retry(failed)
        self.assertTrue(failed.review_attention)
        self.assertFalse(failed.active)
        self.assertEqual(failed.attempt['retry_kind'], '')

    def test_missing_contract_and_legacy_model_retry_are_terminal_without_a_product_question(self):
        for index, result in enumerate((missing_evidence(), {**missing_evidence(), 'verdict': 'retry'}), 1):
            with self.subTest(verdict=result['verdict']):
                view = self.f.admit(index, repository=f'Owner/Repo{index}')
                self.begin(view)
                denied = self.f.apply(Review(view.task_id, view.attempt['attempt_id'], result, fixtures.SPEC))
                self.assertTrue(denied.review_attention)
                self.assertEqual(denied.attempt['retry_kind'], '')
                self.assertEqual(denied.attempt['retry_decision'], 'attention')
                if index == 1:
                    self.assertIn('Attach the merged contract', denied.reason)
                    self.assertNotIn('Question:', denied.reason)
                self.assertFalse(self.stop(view, conclusion='success').active)
                self.reopen()
                # Content edits, eligibility churn and observations cannot resubmit.
                issue = self.f.issue(index, repository=f'Owner/Repo{index}', content_revision='f'*64)
                self.f.apply(replace(issue, eligible=False))
                held = self.f.apply(issue)
                self.assertEqual(held.phase, 'held')
                self.assertEqual(held.review_attempts, 1)
                self.assertFalse(self.f.controller.waiting(held.repository))

    def test_unclassified_failed_job_and_unsupported_access_are_terminal(self):
        for index, diagnostic in enumerate(({}, {'operation': 'worker/agent', 'kind': 'worker_error',
                                               'reason_code': 'cursor_readiness_tool_denied'},
                                              {'operation': 'http/other', 'kind': 'http_error', 'http_status': 403}), 1):
            view = self.f.admit(index, repository=f'Owner/Repo{index}')
            self.begin(view, assess=False)
            failed = self.f.apply(Finish(view.task_id, view.attempt['attempt_id'], 'review', 'failed', failure=diagnostic))
            self.assertTrue(failed.review_attention)
            self.assertEqual(failed.attempt['retry_kind'], '')
            self.assertFalse(self.stop(view).active)
        view = self.f.admit(4, repository='Owner/Repo4')
        failed = self.stop(view)  # Generic GitHub failure is insufficient too.
        self.assertTrue(failed.review_attention)
        self.assertFalse(failed.active)

    def test_quota_waits_do_not_spend_assessment_failure_budget(self):
        view = self.f.admit()
        for _ in range(5):
            self.begin(view, assess=False)
            waiting = self.f.apply(Finish(view.task_id, view.attempt['attempt_id'], 'review', 'waiting_for_quota'))
            self.assertEqual(waiting.review_attempts, 0)
            self.assertEqual(waiting.attempt['retry_kind'], 'capacity')
            view = self.retry(self.stop(view, conclusion='success'))
        self.begin(view)
        failed = self.f.apply(Finish(view.task_id, view.attempt['attempt_id'], 'review', 'failed', failure=TIMEOUT))
        self.assertEqual(failed.review_attempts, 1)
        self.assertEqual(failed.attempt['retry_at'], self.f.now+300)

    def test_context_churn_and_invalidated_approval_never_reset_or_authorize_retries(self):
        view = self.f.admit()
        self.begin(view)
        self.f.apply(Finish(view.task_id, view.attempt['attempt_id'], 'review', 'failed', failure=TIMEOUT))
        changed = replace(fixtures.SPEC, runtime_sha='f'*40, context_sha='d'*40)
        observed = self.f.apply(ContextObserved(view.task_id, view.attempt['attempt_id'], changed))
        self.assertEqual(observed.review_attempts, 1)
        retry_at = observed.attempt['retry_at']
        self.assertEqual(self.f.apply(ContextObserved(view.task_id, view.attempt['attempt_id'], changed)).attempt['retry_at'], retry_at)
        view = self.retry(self.stop(view))
        self.begin(view)
        self.f.apply(Review(view.task_id, view.attempt['attempt_id'], ready(), fixtures.SPEC))
        invalidated = self.f.apply(ContextObserved(view.task_id, view.attempt['attempt_id'], changed))
        self.assertEqual(invalidated.review_attempts, 2)
        self.assertTrue(invalidated.review_attention)
        self.assertEqual(invalidated.attempt['retry_kind'], '')

    def test_deployment_holds_preserve_the_due_time_and_budget_with_a_new_runtime(self):
        f = self.f
        view = f.admit()
        failed = self.stop(view, conclusion='timed_out')
        f.apply(Hold('deployment:runtime', 'Deployment is draining.'))
        f.now = failed.attempt['retry_at']
        with self.assertRaises(Rejected):
            self.retry(failed)
        self.reopen()
        restored = f.controller.inspect(view.task_id)
        self.assertEqual(restored.review_attempts, 1)
        self.assertEqual(restored.attempt['retry_at'], failed.attempt['retry_at'])
        f.apply(Hold('deployment:runtime', '', release=True))
        changed = replace(fixtures.SPEC, runtime_sha='f'*40, context_sha='d'*40)
        second = f.apply(Retry(view.task_id, changed, 'Deployed another runtime', 'review'))
        self.assertEqual(second.review_attempts, 2)
        timed_out = self.stop(second, conclusion='timed_out')
        self.assertEqual(timed_out.attempt['retry_at'], f.now+900)

    def test_a_new_task_identity_cannot_replenish_an_unresolved_submission(self):
        issue = self.f.issue()
        self.f.apply(Admit(issue.task_id, fixtures.SPEC))
        with self.assertRaisesRegex(Rejected, 'unresolved submission'):
            self.f.apply(replace(issue, generation='another-deployment'))
        self.assertEqual(len(self.f.controller.tasks()), 1)

    def test_dependency_wait_releases_the_slot_without_resetting_or_shortening_a_retry(self):
        f = self.f
        view = f.admit(priority=1)
        self.begin(view)
        f.apply(Finish(view.task_id, view.attempt['attempt_id'], 'review', 'failed', failure=TIMEOUT))
        issue = f.issue(priority=1, dependencies_ready=False)
        queued = self.stop(view)
        self.assertFalse(queued.active)
        self.assertEqual(queued.review_attempts, 1)
        f.apply(replace(issue, dependencies_ready=True))
        with self.assertRaisesRegex(Rejected, 'delay'):
            f.apply(Admit(view.task_id, fixtures.SPEC))
        second = f.issue(2, priority=4)
        other = f.apply(Admit(second.task_id, fixtures.SPEC))
        self.assertTrue(other.active)
        self.stop(other)
        f.now = queued.attempt['retry_at']
        retried = f.apply(Admit(view.task_id, fixtures.SPEC))
        self.assertEqual(retried.review_attempts, 2)

    def test_explicit_resubmission_waits_for_safe_release_then_opens_exactly_one_cycle(self):
        f = self.f
        view = f.admit()
        self.begin(view)
        f.apply(Review(view.task_id, view.attempt['attempt_id'], missing_evidence(), fixtures.SPEC))
        f.now += 1
        resubmit = Resubmit(view.task_id, identity('human-state-change'), f.now)
        pending = f.apply(resubmit)
        self.assertTrue(pending.review_resubmission_pending)
        self.assertEqual(pending.review_cycle, 1)
        self.assertTrue(self.stop(view, all_attempts_stopped=False).active)
        queued = self.stop(view, conclusion='success')
        self.assertEqual(queued.phase, 'queued')
        self.assertEqual(queued.review_cycle, 2)
        self.assertEqual(queued.review_attempts, 0)
        self.assertEqual(f.apply(resubmit).review_cycle, 2)
        fresh = f.apply(Admit(view.task_id, fixtures.SPEC))
        self.assertEqual(fresh.review_attempts, 1)
        self.assertNotEqual(fresh.attempt['attempt_id'], view.attempt['attempt_id'])

    def test_definite_dispatch_timeout_is_bounded_and_unknown_dispatch_is_never_resent(self):
        view = self.f.admit()
        unknown = self.f.apply(DispatchObserved(view.task_id, view.attempt['attempt_id'], 'unknown', TIMEOUT))
        self.assertEqual(unknown.attempt['retry_kind'], '')
        self.assertTrue(unknown.active)
        with self.assertRaises(Rejected):
            self.f.apply(Retry(view.task_id, fixtures.SPEC, 'try again', 'review'))
        rejected = self.f.apply(DispatchObserved(view.task_id, view.attempt['attempt_id'], 'rejected', TIMEOUT))
        again = self.f.apply(DispatchObserved(view.task_id, view.attempt['attempt_id'], 'rejected', TIMEOUT))
        self.assertEqual(again.attempt['retry_at'], rejected.attempt['retry_at'])
        self.assertEqual(rejected.review_attempts, 1)

    def test_version_seven_upgrade_charges_old_loop_history_and_requires_human_resubmission(self):
        f = self.f
        view = f.admit()
        legacy_result = {**missing_evidence(), 'verdict': 'retry'}
        with f.database.transaction() as db:
            db.execute("DELETE FROM metadata WHERE key='readiness:bounded-v2'")
            for ordinal in (2, 3, 4):
                key = identity(view.task_id, ordinal)
                db.execute("INSERT INTO attempts(attempt_id,task_id,ordinal,dispatch_status,started_at,review_result,retry_kind,retry_at) "
                           "VALUES(?,?,?,'accepted',?,?, 'review',1900)",
                           (key, view.task_id, ordinal, f.now, json.dumps(legacy_result)))
            db.execute("UPDATE tasks SET current_attempt=?,phase='held' WHERE task_id=?", (key, view.task_id))
        f.database.close()
        with sqlite3.connect(f.path) as db:
            for table, columns in {'tasks': ('review_cycle', 'review_attempts', 'review_attention', 'review_attention_at',
                                            'review_submitted_at', 'pending_submission_at'),
                                   'attempts': ('review_cycle', 'review_charged', 'failure_class', 'retry_decision', 'review_detail')}.items():
                for column in columns:
                    db.execute(f'ALTER TABLE {table} DROP COLUMN {column}')
            db.execute('PRAGMA user_version=7')
        self.reopen()
        upgraded = f.controller.inspect(view.task_id)
        self.assertEqual(upgraded.review_attempts, 4)
        self.assertTrue(upgraded.review_attention)
        self.assertEqual(upgraded.attempt['retry_kind'], '')
        self.assertIn('published contract is unavailable', upgraded.reason)
        self.assertTrue(upgraded.active)  # Upgrade is not proof of stop.
        self.reopen()
        self.assertEqual(f.controller.inspect(view.task_id).review_attempts, 4)

    def test_imported_history_without_native_attempts_cannot_open_a_fresh_unattended_cycle(self):
        issue = self.f.issue()
        with self.f.database.transaction() as db:
            db.execute("DELETE FROM metadata WHERE key='readiness:bounded-v2'")
            db.execute('INSERT INTO legacy_history VALUES (?,?,?)', ('old-review', issue.task_id, '{}'))
        self.reopen()
        upgraded = self.f.controller.inspect(issue.task_id)
        self.assertEqual(upgraded.phase, 'held')
        self.assertEqual(upgraded.review_attempts, 3)
        self.assertTrue(upgraded.review_attention)
        self.assertFalse(self.f.controller.waiting())


class ReviewDriverRetryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = driver_fixtures.ControllerDriverTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f, self.driver = self.fixture.fixture, self.fixture.driver

    def test_missing_contract_reporting_failures_do_not_retry_and_safe_release_advances_queue(self):
        f = self.f
        self.driver.tick(scan=True)
        f.preflight(Role.REVIEW_SELECT, f.account_a)
        config = f.call(Role.REVIEW, 'start', protocol=PROTOCOL)
        lease = f.call(Role.REVIEW, 'claim', account_id=f.account_a)
        # Simulate a lost credential-cleanup callback; only external stop can reclaim it.
        f.call(Role.REVIEW, 'review', binding=config['binding'], result=missing_evidence())
        f.linear.set_state.side_effect = RuntimeError('temporary reporting outage')
        with self.assertLogs(level='WARNING'):
            self.driver.tick(scan=True)
        self.assertTrue(f.controller.inspect(f.view.task_id).active)
        self.assertGreater(f.pool.get(f.account_a)[0]['lease_until'], 1000)
        self.fixture.runs[f.key].update(status='completed', conclusion='success')
        with self.assertLogs(level='WARNING'):
            self.driver.tick(scan=True)
            self.driver.tick(scan=True)
        denied = f.controller.inspect(f.view.task_id)
        self.assertFalse(denied.active)
        self.assertEqual(denied.phase, 'held')
        self.assertEqual(denied.review_attempts, 1)
        self.assertEqual(denied.attempt['retry_kind'], '')
        self.assertEqual(f.pool.get(f.account_a)[0]['owner'], '')
        self.assertEqual(f.github.dispatch.call_count, 2)  # Unrelated task advances.
        self.assertIn('KIAN-941', self.driver.render(denied))
        self.assertIn('/runs/123/attempts/1', self.driver.render(denied))
        with self.assertRaises(Exception):
            f.call(Role.REVIEW, 'save', account_id=f.account_a, lease=lease['lease'], release=True)
        f.linear.set_state.side_effect = lambda key, state: self.fixture.issues[key].update(state={'id': state, 'type': 'started'})
        self.driver.report()
        self.assertEqual(f.issue['state']['id'], f.route.linear_states.blocked)
        feedback = [call.args[2] for call in f.linear.status_comment.call_args_list if 'Evidence:' in call.args[2]]
        self.assertTrue(any(f.registry.notify_user_url in body for body in feedback))

    def test_open_pr_keeps_admission_after_terminal_review_failure(self):
        f = self.f
        self.driver.tick(scan=True)
        f.preflight(Role.REVIEW_SELECT, f.account_a)
        config = f.call(Role.REVIEW, 'start', protocol=PROTOCOL)
        f.call(Role.REVIEW, 'review', binding=config['binding'], result=missing_evidence())
        self.fixture.runs[f.key].update(status='completed', conclusion='success')
        f.github.worker_pull.return_value = {'url': f'https://github.com/{f.route.repository}/pull/1',
                                           'state': 'OPEN', 'merged': False}
        self.driver.tick()
        self.assertTrue(f.controller.inspect(f.view.task_id).active)
        self.assertEqual(f.github.dispatch.call_count, 1)
        f.github.worker_pull.return_value['state'] = 'CLOSED'
        self.driver.tick()
        self.assertFalse(f.controller.inspect(f.view.task_id).active)

    def test_controller_dispatches_exactly_three_jobs_for_verified_startup_timeouts(self):
        f = self.f
        self.fixture.second_issue['state'] = {'id': 'done', 'type': 'completed'}
        self.driver.tick(scan=True)
        for index in range(3):
            current = f.controller.inspect(f.view.task_id)
            self.fixture.runs[current.attempt['attempt_id']].update(status='completed', conclusion='timed_out')
            self.driver.tick()
            failed = f.controller.inspect(f.view.task_id)
            self.assertEqual(failed.review_attempts, index+1)
            if index < 2:
                due = failed.attempt['retry_at']
                self.driver.clock = lambda: due-1
                self.driver.tick(scan=True)
                self.assertEqual(f.github.dispatch.call_count, index+1)
                self.driver.clock = lambda: due
                self.driver.tick(scan=True)
                self.assertEqual(f.github.dispatch.call_count, index+2)
        self.driver.tick()  # Settle the acknowledged Needs attention projection.
        with CallBudget(self, linear=f.linear, github=f.github, exact={
                'linear.project_issues': len(f.registry.routes),
                'linear.issue_dependencies': len(f.registry.routes)}):
            self.driver.tick(scan=True)
        self.assertEqual(f.github.dispatch.call_count, 3)
        self.assertFalse(failed.active)
        self.assertTrue(failed.review_attention)
        self.assertEqual(f.issue['state']['id'], f.route.linear_states.blocked)
        rendered = self.driver.render(failed)
        self.assertIn('exhausted all 3', rendered)
        for run_id in ('123', '124', '125'):
            self.assertIn('/runs/'+run_id+'/attempts/1', rendered)

    def test_generation_changes_keep_the_unresolved_task_and_retry_budget(self):
        f = self.f
        self.fixture.second_issue['state'] = {'id': 'done', 'type': 'completed'}
        self.driver.tick(scan=True)
        self.fixture.runs[f.key].update(status='completed', conclusion='timed_out')
        self.driver.tick()
        self.driver.registry = replace(f.registry, generation='another-deployment')
        self.driver.clock = lambda: 1300
        self.driver.tick(scan=True)
        current = f.controller.inspect(f.view.task_id)
        self.assertEqual(current.review_attempts, 2)
        self.assertEqual(current.task_id, f.view.task_id)
        self.assertEqual(len(f.controller.tasks()), 1)

    def test_human_resubmission_survives_reporting_while_waiting_and_duplicate_events(self):
        f = self.f
        self.fixture.second_issue['state'] = {'id': 'done', 'type': 'completed'}
        self.driver.tick(scan=True)
        f.preflight(Role.REVIEW_SELECT, f.account_a)
        config = f.call(Role.REVIEW, 'start', protocol=PROTOCOL)
        f.call(Role.REVIEW, 'review', binding=config['binding'], result=missing_evidence())
        self.driver.report()
        f.issue['state'] = {'id': f.route.ready_state_ids[0], 'type': 'unstarted'}
        self.driver.tick(scan=True)  # A scan or bot's Todo write is insufficient.
        self.assertEqual(f.controller.inspect(f.view.task_id).review_cycle, 1)
        f.issue['state']['id'] = f.route.linear_states.blocked
        self.fixture.human_resubmit()
        self.driver.tick()
        pending = f.controller.inspect(f.view.task_id)
        self.assertTrue(pending.review_resubmission_pending)
        self.assertEqual(f.issue['state']['id'], f.route.ready_state_ids[0])
        self.fixture.runs[f.key].update(status='completed', conclusion='success')
        self.driver.tick()
        fresh = f.controller.inspect(f.view.task_id)
        self.assertEqual(fresh.review_cycle, 2)
        self.assertEqual(fresh.review_attempts, 1)
        self.assertEqual(f.github.dispatch.call_count, 2)
        event = {'type': 'Issue', 'id': f.issue['id'], 'webhook_at': self.driver.clock()+10,
                 'submission': {'actor_id': str(UUID(int=99)),
                                'from_state': f.route.linear_states.blocked, 'to_state': f.route.ready_state_ids[0],
                                'changed_at': self.driver.clock()}}
        self.driver.apply(InboxQueued(identity(event), event))
        with CallBudget(self, linear=f.linear, github=f.github,
                        exact={'linear.issue': 1, 'linear.issue_dependencies': 1}):
            self.driver.events()
        self.driver.tick(scan=True)
        self.assertEqual(f.controller.inspect(f.view.task_id).review_cycle, 2)
        self.assertEqual(f.github.dispatch.call_count, 2)

    def test_an_older_run_rerun_blocks_release_and_retry_until_it_stops(self):
        f = self.f
        self.fixture.second_issue['state'] = {'id': 'done', 'type': 'completed'}
        self.driver.tick(scan=True)
        old_key = f.key
        self.fixture.runs[old_key].update(status='completed', conclusion='timed_out')
        self.driver.tick()
        self.driver.clock = lambda: 1300
        self.driver.tick()
        current = f.controller.inspect(f.view.task_id)
        self.assertEqual(current.review_attempts, 2)
        self.fixture.runs[old_key].update(status='in_progress', run_attempt=2)
        self.fixture.runs[current.attempt['attempt_id']].update(status='completed', conclusion='failure')
        self.driver.tick()
        held = f.controller.inspect(f.view.task_id)
        self.assertTrue(held.active)
        self.assertFalse(held.attempt['all_stopped'])
        self.assertEqual(f.github.dispatch.call_count, 2)
        self.fixture.runs[old_key].update(status='completed')
        self.driver.tick()
        self.assertFalse(f.controller.inspect(f.view.task_id).active)


if __name__ == '__main__':
    unittest.main()
