"""Adverse schedules cross the same Controller/Driver/Broker seam as production."""

import json
from itertools import product
import sqlite3
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'automation')]
from common.clients import RequestError
from common.context import ContextError
from common.policy import PROTOCOL
from controller.model import InboxQueued, IssueObserved, ProjectionUpdated, Rejected, Role, identity
from context_fixtures import ContextFixture
import test_review_retries as retry_fixtures
import test_controller_driver as driver_fixtures
import worker


def schedules(*actors):
    """All interleavings preserving each actor's own happens-before relation."""
    if not any(actors):
        yield ()
    for index, actor in enumerate(actors):
        if actor:
            tail = [*actors]
            tail[index] = actor[1:]
            for schedule in schedules(*tail):
                yield (actor[0], *schedule)


class SymphonyCausalityTests(unittest.TestCase):
    def fixture(self):
        scenario = retry_fixtures.ReviewDriverRetryTests(methodName='runTest')
        scenario.setUp()
        self.addCleanup(scenario.doCleanups)
        scenario.deny_review()
        return scenario

    @staticmethod
    def move(scenario, target, at, state_type='unstarted'):
        f, driver = scenario.f, scenario.driver
        previous = f.issue['state']['id']
        driver.clock = lambda: at
        f.broker.clock = driver.clock
        scenario.fixture.change_state(f.issue['id'], {'id': target, 'type': state_type}, at=at)
        return {'type': 'Issue', 'id': f.issue['id'], 'webhook_at': at, 'submission': {
            'actor_id': scenario.fixture.histories[f.issue['id']][-1]['actor']['id'],
            'from_state': previous, 'to_state': target, 'changed_at': at}}

    def assert_invariants(self, scenario):
        with scenario.f.database.transaction() as db:
            self.assertFalse(db.execute('SELECT repository FROM grants GROUP BY repository HAVING COUNT(*)>1').fetchall())
        view = scenario.f.controller.inspect(scenario.f.view.task_id)
        self.assertLessEqual(view.review_attempts, 3)
        self.assertLessEqual(view.review_cycle, 2)

    def test_all_resubmission_cleanup_reporting_interleavings_with_restart_at_every_cut(self):
        orders = list(schedules(('move', 'deliver', 'process'), ('cleanup', 'report')))
        self.assertEqual(len(orders), 10)
        for order in orders:
            for cut in range(len(order) + 1):
                with self.subTest(order=order, restart_at=cut):
                    scenario = self.fixture()
                    event = None
                    for index, step in enumerate(order):
                        if index == cut:
                            scenario.reopen()
                        if step == 'move':
                            event = self.move(scenario, scenario.f.route.ready_state_ids[0], 1001)
                        elif step == 'deliver':
                            scenario.driver.apply(InboxQueued(identity(event), event))
                        elif step == 'process':
                            scenario.driver.events()
                        elif step == 'cleanup':
                            scenario.finish_old_review()
                        else:
                            scenario.driver.report()
                        self.assert_invariants(scenario)
                    if cut == len(order):
                        scenario.reopen()
                    scenario.driver.events()
                    scenario.driver.report()
                    view = scenario.f.controller.inspect(scenario.f.view.task_id)
                    self.assertEqual((view.phase, view.review_cycle, view.review_attempts), ('queued', 2, 0))
                    self.assertFalse(view.active)
                    self.assertEqual(scenario.f.issue['state']['id'], scenario.f.route.ready_state_ids[0])
                    scenario.doCleanups()

    def test_newer_withdrawal_wins_over_late_submission_delivery_and_duplicates(self):
        for kind in ('blocked', 'backlog', 'canceled'):
            with self.subTest(kind=kind):
                scenario = self.fixture()
                f = scenario.f
                submission = self.move(scenario, f.route.ready_state_ids[0], 1001)
                state = f.route.linear_states.blocked if kind == 'blocked' else kind
                withdrawal = self.move(scenario, state, 1002, 'canceled' if kind == 'canceled' else 'started')
                for event in (withdrawal, submission, submission):
                    scenario.driver.apply(InboxQueued(identity(event), event))
                scenario.reopen()
                scenario.driver.events()
                scenario.finish_old_review()
                scenario.driver.report()
                view = f.controller.inspect(f.view.task_id)
                self.assertEqual(view.review_cycle, 1)
                self.assertFalse(view.active or view.review_resubmission_pending or view.eligible)
                self.assertEqual(f.issue['state']['id'], state)

    def test_signed_pause_revokes_coding_before_inbox_processing_and_preserves_cleanup(self):
        scenario = self.fixture()
        scenario.finish_old_review()
        scenario.fixture.human_resubmit()
        scenario.driver.tick(scan=True)
        f = scenario.f
        f.key = f.controller.inspect(f.view.task_id).attempt['attempt_id']
        f.review()
        f.preflight(Role.CODING_SELECT, f.account_b)
        f.call(Role.CODING, 'start', protocol=PROTOCOL)
        lease = f.call(Role.CODING, 'claim', account_id=f.account_b)
        f.call(Role.CODING, 'progress', stage='coding', context_sha=f.spec.context_sha)
        scenario.driver.report()
        event = self.move(scenario, f.route.linear_states.blocked, 1003, 'started')
        scenario.driver.apply(InboxQueued(identity(event), event))
        self.assertTrue(f.controller.inbox())
        for action in ('github', 'context', 'instructions', 'heartbeat'):
            with self.assertRaises(Rejected):
                f.controller.authorize(f.view.task_id, f.key, Role.CODING, action, now=1003)
        scenario.driver.report()
        self.assertEqual(f.issue['state']['id'], f.route.linear_states.blocked)
        f.call(Role.CODING, 'save', account_id=f.account_b, lease=lease['lease'], auth=lease['auth'], release=True)
        self.assertEqual(f.pool.get(f.account_b)[0]['lease_until'], 0)

    def test_in_progress_state_webhook_does_not_withdraw_coding_task(self):
        scenario = self.fixture()
        scenario.finish_old_review()
        scenario.fixture.human_resubmit()
        scenario.driver.tick(scan=True)
        f = scenario.f
        f.key = f.controller.inspect(f.view.task_id).attempt['attempt_id']
        f.review()
        f.preflight(Role.CODING_SELECT, f.account_b)
        f.call(Role.CODING, 'start', protocol=PROTOCOL)
        lease = f.call(Role.CODING, 'claim', account_id=f.account_b)
        f.call(Role.CODING, 'progress', stage='coding', context_sha=f.spec.context_sha)
        scenario.driver.report()
        # Linear delivers an update webhook for the in_progress transition
        event = self.move(scenario, f.route.linear_states.in_progress, 1003, 'started')
        scenario.driver.apply(InboxQueued(identity(event), event))
        # Ensure coding authorization is preserved and not withdrawn
        f.controller.authorize(f.view.task_id, f.key, Role.CODING, 'progress', now=1003)
        view = f.controller.inspect(f.view.task_id)
        self.assertTrue(view.eligible)
        self.assertEqual(view.review_attention, '')

    def test_webhook_history_millisecond_timestamp_discrepancy_authorizes_resubmission(self):
        scenario = self.fixture()
        f, driver = scenario.f, scenario.driver
        previous = f.issue['state']['id']
        target = f.route.ready_state_ids[0]
        history_at = 1001.033
        webhook_at = 1001.066
        driver.clock = lambda: 1001.1
        f.broker.clock = driver.clock
        scenario.fixture.change_state(f.issue['id'], {'id': target, 'type': 'unstarted'}, at=history_at)
        event = {'type': 'Issue', 'id': f.issue['id'], 'webhook_at': webhook_at, 'submission': {
            'actor_id': scenario.fixture.histories[f.issue['id']][-1]['actor']['id'],
            'from_state': previous, 'to_state': target, 'changed_at': webhook_at}}
        scenario.driver.apply(InboxQueued(identity(event), event))
        scenario.finish_old_review()
        scenario.driver.events()
        scenario.driver.report()
        view = f.controller.inspect(f.view.task_id)
        self.assertEqual((view.phase, view.review_cycle, view.review_attempts), ('queued', 2, 0))

    def test_same_timestamp_conflict_is_held_until_a_new_unambiguous_human_action(self):
        scenario = self.fixture()
        first = self.move(scenario, scenario.f.route.ready_state_ids[0], 1001)
        second = self.move(scenario, scenario.f.route.linear_states.blocked, 1001, 'started')
        for event in (first, second, first):
            scenario.driver.apply(InboxQueued(identity(event), event))
        scenario.finish_old_review()
        scenario.driver.events()
        view = scenario.f.controller.inspect(scenario.f.view.task_id)
        self.assertEqual(view.review_cycle, 1)
        self.assertFalse(view.eligible)
        fresh = self.move(scenario, scenario.f.route.ready_state_ids[0], 1002)
        scenario.driver.apply(InboxQueued(identity(fresh), fresh))
        scenario.driver.events()
        self.assertEqual(scenario.f.controller.inspect(view.task_id).review_cycle, 2)

    def test_crash_after_remote_state_write_reconciles_without_repeating_it(self):
        scenario = self.fixture()
        # A fresh held cycle gives us a pending state transition, independent of comments.
        scenario.finish_old_review()
        scenario.fixture.human_resubmit()
        scenario.driver.tick(scan=True)
        f = scenario.f
        f.key = f.controller.inspect(f.view.task_id).attempt['attempt_id']
        f.preflight(Role.REVIEW_SELECT, f.account_a)
        config = f.call(Role.REVIEW, 'start', protocol=PROTOCOL)
        from test_readiness import missing_evidence
        f.call(Role.REVIEW, 'review', binding=config['binding'], result=missing_evidence())
        apply = f.controller.apply
        def crash(command, **kwargs):
            if isinstance(command, ProjectionUpdated) and command.outcome == 'confirmed':
                raise RuntimeError('Crash before durable acknowledgement')
            return apply(command, **kwargs)
        with patch.object(f.controller, 'apply', side_effect=crash), self.assertLogs(level='WARNING'):
            scenario.driver.report()
        count = f.linear.set_state.call_count
        scenario.reopen()
        scenario.driver.report()
        self.assertEqual(f.linear.set_state.call_count, count)
        self.assertFalse(f.controller.effects('linear_state'))

    def test_late_in_progress_write_is_reconciled_to_the_newer_human_command(self):
        for target, process_early in product(('blocked', 'backlog', 'canceled'), (False, True)):
            with self.subTest(target=target, process_before_reconcile=process_early):
                fixture = driver_fixtures.ControllerDriverTests(methodName='runTest')
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                f, driver = fixture.fixture, fixture.driver
                fixture.second_issue['state'] = {'id': 'done', 'type': 'completed'}
                driver.tick(scan=True)
                f.review()
                f.preflight(Role.CODING_SELECT, f.account_b)
                f.call(Role.CODING, 'start', protocol=PROTOCOL)
                f.call(Role.CODING, 'claim', account_id=f.account_b)
                f.call(Role.CODING, 'progress', stage='coding', context_sha=f.spec.context_sha)
                write = f.linear.set_state.side_effect
                desired = f.route.linear_states.blocked if target == 'blocked' else target
                def race(key, state):
                    if state == f.route.linear_states.in_progress:
                        previous = f.issue['state']['id']
                        fixture.driver.clock = lambda: 1001
                        fixture.change_state(key, {'id': desired, 'type': 'canceled' if target == 'canceled' else 'started'}, at=1001)
                        entry = fixture.histories[key][-1]
                        event = {'type': 'Issue', 'id': key, 'submission': {
                            'actor_id': entry['actor']['id'], 'changed_at': 1001, 'from_state': previous, 'to_state': desired}}
                        driver.apply(InboxQueued(identity(event), event))
                        write(key, state)
                        raise RuntimeError('Lost response after a late controller write')
                    return write(key, state)
                f.linear.set_state.side_effect = race
                with self.assertLogs(level='WARNING'):
                    driver.report()
                self.assertEqual(f.issue['state']['id'], f.route.linear_states.in_progress)
                f.linear.set_state.side_effect = write
                if process_early:
                    driver.events()
                driver.report()
                self.assertEqual(f.issue['state']['id'], desired)
                self.assertFalse(f.controller.inspect(f.view.task_id).eligible)
                driver.events()
                self.assertFalse(f.controller.inbox())

    def test_version_ten_upgrade_preserves_grants_budget_and_confirmed_attention(self):
        scenario = self.fixture()
        f = scenario.f
        before = f.controller.inspect(f.view.task_id)
        path = f.database.path
        f.database.close()
        with sqlite3.connect(path) as db:
            # Reconstruct the deployed v10 representation of the same held task.
            db.execute("DELETE FROM effects WHERE kind='linear_state'")
            db.execute('DROP TABLE linear_intents')
            db.execute('DROP TABLE linear_workflows')
            db.execute("DELETE FROM metadata WHERE key='linear:intents-v1'")
            db.execute('INSERT INTO comment_signatures VALUES (?,?,?)', (
                f'review-state:{before.task_id}:{before.review_cycle}',
                identity(before.review_attention_at, f.route.linear_states.blocked), 1000))
            db.execute('PRAGMA user_version=10')
        scenario.reopen()
        restored = f.controller.inspect(before.task_id)
        self.assertEqual((restored.active, restored.review_cycle, restored.review_attempts, restored.attempt['attempt_id']),
                         (before.active, before.review_cycle, before.review_attempts, before.attempt['attempt_id']))
        writes = f.linear.set_state.call_count
        scenario.driver.observe_issue(f.issue, known=restored)
        scenario.driver.report()
        self.assertEqual(f.linear.set_state.call_count, writes)
        self.assertFalse(f.controller.effects('linear_state'))
        self.assertEqual(f.database.connection.execute('PRAGMA user_version').fetchone()[0], 11)


class SymphonyAuthorityTests(unittest.TestCase):
    def test_queries_are_read_only_and_cannot_authorize_a_stale_view_or_invalid_clock(self):
        fx = ContextFixture(self)
        cfg = fx.start_review()
        fx.approve(cfg)
        fx.start_coding()
        f = fx.f
        stale = f.controller.inspect(f.view.task_id)
        with f.database.transaction() as db:
            before = [tuple(row) for row in db.execute('SELECT * FROM effects ORDER BY effect_id')]
        for action in ('context', 'github', 'heartbeat', 'instructions', 'progress'):
            f.controller.authorize(stale.task_id, f.key, Role.CODING, action, now=1001)
        with f.database.transaction() as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM effects ORDER BY effect_id')], before)
        for now in (float('nan'), float('inf'), True):
            with self.assertRaises(ValueError):
                f.controller.authorize(stale.task_id, f.key, Role.CODING, 'github', now=now)
        f.controller.apply(IssueObserved(f.issue['id'], f.route.project_id, f.route.repository,
            f.registry.generation, f.issue['identifier'], False, True, canceled=True,
            content_revision=f.spec.content_revision), now=1001)
        self.assertFalse(f.controller.accepts_instructions(stale, now=1001))
        with self.assertRaises(Rejected):
            f.controller.authorize(stale.task_id, f.key, Role.CODING, 'github', now=1001)

    def test_start_queries_allow_session_creation_but_require_readiness_before_coding(self):
        fx = ContextFixture(self)
        f = fx.f
        f.controller.authorize(f.view.task_id, f.key, Role.REVIEW_SELECT, 'start', now=1000)
        with self.assertRaises(Rejected):
            f.controller.authorize(f.view.task_id, f.key, Role.CODING_SELECT, 'start', now=1000)
        cfg = fx.start_review()
        fx.approve(cfg)
        self.assertIsNone(f.controller.inspect(f.view.task_id).session('coding'))
        f.controller.authorize(f.view.task_id, f.key, Role.CODING_SELECT, 'start', now=1000)

    def test_running_dependency_change_preserves_every_operation_and_approval(self):
        for context in ('none', 'list', 'contract'):
            with self.subTest(context=context):
                fx = ContextFixture(self)
                cfg = fx.start_review()
                if context == 'list':
                    fx.complete('list')
                elif context == 'contract':
                    fx.contract()
                fx.approve(cfg)
                fx.start_coding()
                f = fx.f
                with f.database.transaction() as db:
                    seal = tuple(db.execute('SELECT * FROM context_approvals').fetchone())
                f.controller.apply(IssueObserved(f.issue['id'], f.route.project_id, f.route.repository,
                    f.registry.generation, f.issue['identifier'], True, False, content_revision=f.spec.content_revision), now=1001)
                fx.dependencies[fx.root['id']].append({'id': 'new-edge', 'type': 'blocks',
                    'issue': {'id': 'new-issue', 'identifier': 'KIAN-999'}})
                for action in ('context', 'github', 'heartbeat', 'instructions', 'progress'):
                    f.controller.authorize(f.view.task_id, f.key, Role.CODING, action, now=1001)
                fx.complete('manifest', role=Role.CODING)
                fx.call(Role.CODING, 'heartbeat')
                fx.call(Role.CODING, 'progress', stage='publishing')
                fx.call(Role.CODING, 'github')
                with f.database.transaction() as db:
                    self.assertEqual(tuple(db.execute('SELECT * FROM context_approvals').fetchone()), seal)
                self.assertFalse(f.controller.inspect(f.view.task_id).review_attention)

    def test_transient_revalidation_preserves_approval_and_budget_then_recovers(self):
        for status in (408, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                fx = ContextFixture(self)
                cfg = fx.start_review()
                fx.contract()
                fx.approve(cfg)
                fx.start_coding()
                before = fx.f.controller.inspect(fx.f.view.task_id)
                fx.failed[f'/repos/{fx.repo}/pulls/55'] = status
                with self.assertRaises(RequestError):
                    fx.call(Role.CODING, 'github')
                after = fx.f.controller.inspect(before.task_id)
                self.assertEqual((after.phase, after.review_attempts, after.review_attention, after.attempt['retry_kind']),
                                 (before.phase, before.review_attempts, before.review_attention, before.attempt['retry_kind']))
                fx.failed.clear()
                self.assertIn('token', fx.call(Role.CODING, 'github'))

    def test_temporary_fetch_is_not_cached_as_missing_evidence(self):
        fx = ContextFixture(self)
        cfg = fx.start_review()
        fx.failed['KIAN-941'] = 503
        with self.assertRaises(RequestError):
            fx.read('issue', target='KIAN-941')
        source = next(row for row in fx.f.broker.context.sources(fx.f.controller.inspect(fx.f.view.task_id))
                      if json.loads(row['request']).get('target') == 'KIAN-941')
        self.assertEqual((source['status'], source['required']), ('temporary_unavailable', 1))
        with self.assertRaises(RequestError):
            fx.approve(cfg)
        self.assertFalse(fx.f.controller.inspect(fx.f.view.task_id).review_attention)
        fx.failed.clear()
        self.assertEqual(fx.complete('issue', target='KIAN-941')['identifier'], 'KIAN-941')
        self.assertEqual(fx.approve(cfg), {'status': 'passed'})

    def test_timeout_and_invalid_response_do_not_claim_evidence_changed(self):
        fx = ContextFixture(self)
        cfg = fx.start_review()
        fx.approve(cfg)
        fx.start_coding()
        for error, expected in ((ContextError('timeout'), RequestError), (ContextError('incomplete'), RequestError),
                                (ValueError('bad response'), ValueError)):
            with patch.object(fx.f.broker.context, 'revalidate', side_effect=error), self.assertRaises(expected):
                fx.call(Role.CODING, 'github')
            self.assertFalse(fx.f.controller.inspect(fx.f.view.task_id).review_attention)

    def test_permanent_source_access_loss_still_holds_without_a_retry(self):
        fx = ContextFixture(self)
        cfg = fx.start_review()
        fx.contract()
        fx.approve(cfg)
        fx.start_coding()
        fx.failed[f'/repos/{fx.repo}/pulls/55'] = 403
        with self.assertRaises(Rejected):
            fx.call(Role.CODING, 'github')
        view = fx.f.controller.inspect(fx.f.view.task_id)
        self.assertEqual(view.phase, 'held')
        self.assertTrue(view.review_attention)
        self.assertEqual(view.attempt['retry_kind'], '')


class SymphonyBrokerRetryTests(unittest.TestCase):
    def test_only_repeatable_operations_retry_temporary_failures_with_a_stable_request_identity(self):
        for action in ('start', 'checkout', 'github', 'context', 'progress'):
            with self.subTest(action=action):
                broker = worker.Broker('https://clusterfork-controller-7604cac329.mexicocentral.cloudapp.azure.com/api/worker',
                                       'a' * 64, sleep=Mock(), retry_delays=(0, 0))
                broker.token, broker.expires = 'private-test-token', time.time() + 3600
                with patch.object(worker, 'json_request', side_effect=[HTTPError('https://broker', 503, 'temporary', {}, None),
                                                                      {'token': 'publication-test-token'}]) as request:
                    broker.call(action)
                self.assertEqual(request.call_count, 2)
                self.assertEqual(request.call_args_list[0].args[2]['request_id'], request.call_args_list[1].args[2]['request_id'])
                broker.sleep.assert_called_once_with(0)

    def test_ambiguous_mutations_and_authorization_denials_never_retry(self):
        for action, status in (('claim', 503), ('bind_account', 503), ('review', 503), ('save', 503), ('github', 403)):
            with self.subTest(action=action, status=status):
                broker = worker.Broker('https://clusterfork-controller-7604cac329.mexicocentral.cloudapp.azure.com/api/worker',
                                       'a' * 64, sleep=Mock(), retry_delays=(0, 0))
                broker.token, broker.expires = 'private-test-token', time.time() + 3600
                with patch.object(worker, 'json_request', side_effect=HTTPError('https://broker', status, 'failed', {}, None)) as request:
                    with self.assertRaises(HTTPError):
                        broker.call(action)
                self.assertEqual(request.call_count, 1)
                broker.sleep.assert_not_called()

    def test_temporary_outage_exhausts_the_finite_call_budget(self):
        broker = worker.Broker('https://clusterfork-controller-7604cac329.mexicocentral.cloudapp.azure.com/api/worker',
                               'a' * 64, sleep=Mock(), retry_delays=(0, 0))
        broker.token, broker.expires = 'private-test-token', time.time() + 3600
        with patch.object(worker, 'json_request', side_effect=URLError('unavailable')) as request:
            with self.assertRaises(URLError):
                broker.call('github')
        self.assertEqual(request.call_count, 3)


if __name__ == '__main__':
    unittest.main()
