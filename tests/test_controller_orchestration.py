"""Controller decisions span real SQLite, signed broker roles and remote adapters."""

import copy
import unittest

from controller.core import Controller
from controller.store import Database
from controller.context import Context
from controller.images import Images
from controller.model import Role, Rejected
from common.clients import RequestError
from common.routing import DispatchError
from common.policy import PROTOCOL
from test_controller import ready
import test_controller_driver as fixtures


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.ControllerDriverTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture, self.driver, self.f = fixture, fixture.driver, fixture.fixture
        self.f.broker.refresh = self.driver.refresh
        self.pull = None
        self.f.github.worker_pull.side_effect = lambda route, receipt, key, url='': copy.deepcopy(self.pull) if key == self.f.view.task_id else None
        self.f.github.branch_sha.side_effect = lambda route: self.f.spec.context_sha if route.ref == 'main' else 'c'*40
        self.f.github.create_worker_pull.side_effect = self.create_pull
        self.f.github.merge_worker_pull.side_effect = self.merge_pull

    def create_pull(self, route, plan):
        self.pull = {'url': f'https://github.com/{route.repository}/pull/1', 'state': 'OPEN', 'merged': False,
                     'headRefOid': plan['commit_sha'], 'mergeable': 'MERGEABLE', 'mergeStateStatus': 'CLEAN',
                     'isDraft': False, 'commits': {'nodes': [{'commit': {'statusCheckRollup': {'contexts': {
                         'nodes': [{'__typename': 'CheckRun', 'name': name, 'isRequired': True,
                                    'status': 'IN_PROGRESS', 'conclusion': None} for name in route.required_checks],
                         'pageInfo': {'hasNextPage': False}}}}}]}}
        return {'html_url': self.pull['url']}

    def merge_pull(self, route, url, commit):
        self.assertEqual(commit, self.pull['headRefOid'])
        self.pull.update(merged=True, state='MERGED')
        return {'merged': True}

    def start_coding(self):
        f = self.f
        self.driver.tick(scan=True)
        f.preflight(Role.REVIEW_SELECT, f.account_a)
        config = f.call(Role.REVIEW, 'start', protocol=PROTOCOL)
        f.call(Role.REVIEW, 'review', binding=config['binding'], result=ready())
        self.driver.tick()
        self.assertEqual(f.github.dispatch.call_count, 1)
        with self.assertRaises(Rejected):
            f.call(Role.CODING_SELECT, 'start', protocol=PROTOCOL)
        self.fixture.runs[f.key].update(status='completed', conclusion='success')
        self.driver.tick()
        self.assertEqual(f.github.dispatch.call_count, 2)
        self.assertEqual([c.args[1]['phase'] for c in f.github.dispatch.call_args_list], ['review', 'coding'])
        f.preflight(Role.CODING_SELECT, f.account_b)
        f.call(Role.CODING, 'start', protocol=PROTOCOL)
        f.call(Role.CODING, 'claim', account_id=f.account_b)
        f.call(Role.CODING, 'progress', stage='coding', context_sha=f.spec.context_sha)
        view = f.controller.inspect(f.view.task_id)
        self.assertNotEqual(view.session('review')['run_id'], view.session('coding')['run_id'])

    def restart(self):
        f = self.f
        path = f.database.path
        f.database.close()
        f.database = Database(path)
        self.addCleanup(f.database.close)
        f.controller = Controller(f.database)
        f.broker.controller = self.driver.controller = f.controller
        f.broker.accounts = f.controller.accounts
        f.broker.context = Context(f.database, f.linear, f.github)
        f.broker.images = Images(f.database, f.linear.download_image, clock=f.broker.clock)
        self.driver.images = Images(f.database, f.linear.download_image, clock=self.driver.clock)
        for pool in f.controller.accounts.pools.values():
            pool.clock = f.broker.clock

    def finish_executor(self, *, has_changes=True):
        self.f.advance('finished', has_changes=has_changes)
        self.f.call(Role.CODING, 'finish', status='completed')
        self.fixture.runs[self.f.key].update(status='completed', conclusion='success')

    def passing_checks(self):
        for check in self.pull['commits']['nodes'][0]['commit']['statusCheckRollup']['contexts']['nodes']:
            check.update(status='COMPLETED', conclusion='SUCCESS')

    def test_controller_owns_each_transition_and_merge_after_restart(self):
        self.start_coding()
        f = self.f
        with self.assertRaisesRegex(Rejected, 'controller'):
            f.call(Role.CODING, 'progress', stage='publishing')
        with self.assertRaisesRegex(Rejected, 'authorized a push'):
            f.call(Role.CODING, 'github')
        with self.assertRaisesRegex(Rejected, 'publication evidence'):
            f.call(Role.CODING, 'finish', status='completed')
        step = f.call(Role.CODING, 'command')
        arguments = {'step_id': step['step_id'], 'outcome': 'completed', 'result': {}}
        testing = f.call(Role.CODING, 'step_result', **arguments)
        self.assertEqual(testing['kind'], 'testing')
        self.restart()
        self.assertEqual(f.call(Role.CODING, 'step_result', **arguments), testing)
        with self.assertRaisesRegex(Rejected, 'conflicts'):
            f.call(Role.CODING, 'step_result', **{**arguments, 'result': {'changed': True}})
        self.finish_executor()
        self.assertTrue(f.controller.inspect(f.view.task_id).active)
        f.github.create_worker_pull.assert_not_called()
        self.driver.tick()
        self.assertEqual(f.github.create_worker_pull.call_count, 1)
        self.assertEqual(f.controller.inspect(f.view.task_id).phase, 'published')
        self.driver.tick()
        f.github.merge_worker_pull.assert_not_called()
        self.passing_checks()
        self.restart()
        self.driver.tick()
        self.assertEqual(f.github.merge_worker_pull.call_count, 1)
        self.driver.tick()
        self.assertEqual(f.controller.inspect(f.view.task_id).phase, 'completed')
        self.assertEqual(f.github.dispatch.call_count, 3)  # Next issue's review only.

    def test_lost_create_response_is_discovered_after_restart_without_another_create(self):
        self.start_coding()
        self.finish_executor()
        def uncertain(route, plan):
            self.create_pull(route, plan)
            raise RequestError(503)
        self.f.github.create_worker_pull.side_effect = uncertain
        with self.assertLogs(level='WARNING'):
            self.driver.tick()
        self.restart()
        self.driver.tick()
        self.assertEqual(self.f.github.create_worker_pull.call_count, 1)
        self.assertFalse(self.f.controller.effects('publication'))
        self.assertEqual(self.f.controller.inspect(self.f.view.task_id).phase, 'published')

    def test_no_change_result_needs_stop_proof_and_never_publishes(self):
        self.start_coding()
        self.f.advance('finished')
        self.f.call(Role.CODING, 'finish', status='completed')
        self.driver.tick()
        self.assertTrue(self.f.controller.inspect(self.f.view.task_id).active)
        self.fixture.runs[self.f.key].update(status='completed', conclusion='success')
        self.driver.tick()
        self.assertEqual(self.f.controller.inspect(self.f.view.task_id).phase, 'completed')
        self.f.github.create_worker_pull.assert_not_called()
        self.f.github.merge_worker_pull.assert_not_called()

    def test_failed_required_check_or_changed_head_cannot_merge(self):
        self.start_coding()
        self.finish_executor()
        self.driver.tick()
        self.passing_checks()
        self.pull['headRefOid'] = 'd'*40
        self.driver.tick()
        self.f.github.merge_worker_pull.assert_not_called()
        self.pull['headRefOid'] = 'c'*40
        checks = self.pull['commits']['nodes'][0]['commit']['statusCheckRollup']['contexts']['nodes']
        checks.pop()
        self.driver.tick()
        self.f.github.merge_worker_pull.assert_not_called()

    def test_lost_merge_response_settles_after_restart_without_another_merge(self):
        self.start_coding()
        self.finish_executor()
        self.driver.tick()
        self.passing_checks()
        def uncertain(route, url, commit):
            self.merge_pull(route, url, commit)
            raise RequestError(503)
        self.f.github.merge_worker_pull.side_effect = uncertain
        with self.assertLogs(level='WARNING'):
            self.driver.tick()
        self.restart()
        self.driver.tick()
        self.assertEqual(self.f.github.merge_worker_pull.call_count, 1)
        self.assertEqual(self.f.controller.inspect(self.f.view.task_id).phase, 'completed')
        self.assertFalse(self.f.controller.effects('merge'))

    def test_definite_publication_rejection_retries_without_another_executor(self):
        self.start_coding()
        self.finish_executor()
        self.f.github.create_worker_pull.side_effect = DispatchError('Rejected', uncertain=False)
        with self.assertLogs(level='WARNING'):
            self.driver.tick()
        self.driver.tick()
        self.assertEqual(self.f.github.create_worker_pull.call_count, 1)
        self.restart()
        self.driver.clock = self.f.broker.clock = lambda: 1060
        self.f.github.create_worker_pull.side_effect = self.create_pull
        self.driver.tick()
        self.assertEqual(self.f.github.create_worker_pull.call_count, 2)
        self.assertEqual(self.f.github.dispatch.call_count, 2)

    def test_executor_push_credentials_cannot_create_or_merge_prs(self):
        self.start_coding()
        self.f.advance('publishing')
        self.f.call(Role.CODING, 'github')
        permissions = self.f.github.installation_token.call_args.kwargs['permissions']
        self.assertEqual(permissions, {'contents': 'write', 'workflows': 'write'})
        self.assertNotIn('pull_requests', permissions)
        self.f.github.create_worker_pull.assert_not_called()
        self.f.github.merge_worker_pull.assert_not_called()

    def test_cancel_during_uncertain_create_retains_ownership_until_pr_is_resolved(self):
        self.start_coding()
        self.finish_executor()
        self.f.github.create_worker_pull.side_effect = RequestError(503)
        with self.assertLogs(level='WARNING'):
            self.driver.tick()
        f = self.f
        self.fixture.change_state(f.issue['id'], {'id': 'canceled', 'type': 'canceled'})
        self.driver.tick()
        view = f.controller.inspect(f.view.task_id)
        self.assertTrue(view.active)
        self.assertEqual(f.github.dispatch.call_count, 2)
        self.assertEqual(f.github.create_worker_pull.call_count, 1)
        self.create_pull(f.route, view.session('coding')['data']['publication'])
        self.pull.update(state='CLOSED')
        self.driver.tick()
        self.assertEqual(f.controller.inspect(view.task_id).phase, 'canceled')
        self.assertFalse(f.controller.effects('publication'))

    def test_changed_target_context_blocks_controller_publication(self):
        self.start_coding()
        self.finish_executor()
        self.f.github.branch_sha.side_effect = lambda route: 'd'*40 if route.ref == 'main' else 'c'*40
        self.driver.tick()
        self.f.github.create_worker_pull.assert_not_called()
        self.assertTrue(self.f.controller.inspect(self.f.view.task_id).active)

    def test_deployment_drain_waits_for_pending_publication_and_then_open_pr(self):
        from controller.server import Application
        self.start_coding()
        self.finish_executor()
        view = self.f.controller.inspect(self.f.view.task_id)
        self.driver.observe_execution(view)
        application = Application(self.f.controller, self.f.broker, self.driver, webhook_secret='fixture', metadata={})
        self.assertTrue(self.f.controller.inspect(view.task_id).attempt['stopped'])
        self.assertFalse(application.drained())
        self.driver.tick()
        self.assertEqual(self.f.controller.inspect(view.task_id).attempt['pull_state'], 'open')
        self.assertFalse(application.drained())

    def test_stop_telemetry_retains_both_phase_runs_and_controller_stage_visits(self):
        from controller.telemetry import Telemetry
        from test_controller_telemetry import CONFIG
        from unittest.mock import Mock
        import json
        Telemetry(self.f.database, Mock(), CONFIG, 'a'*40, clock=lambda: 1000, send=Mock())
        self.start_coding()
        self.finish_executor()
        self.driver.tick()
        with self.f.database.transaction() as db:
            events = [json.loads(row[0]) for row in db.execute('SELECT payload FROM telemetry_outbox')]
        stops = {e['Phase']: e['RunId'] for e in events if e['EventType'] == 'github_stopped'}
        self.assertEqual(stops, {'review': '123', 'coding': '124'})
        stages = {e['Stage'] for e in events if e['EventType'] == 'phase_stage'}
        self.assertTrue({'coding', 'testing', 'publishing'}.issubset(stages))


if __name__ == '__main__':
    unittest.main()
