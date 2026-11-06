"""A rollout cannot wait for an open PR while forbidding its safe repair."""

from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'automation'))
from controller.core import Controller
from controller.model import Admit, Hold, Rejected, Retry, Role
import test_controller_orchestration as orchestration


class DeploymentRepairTests(unittest.TestCase):
    def setUp(self):
        self.fixture = orchestration.OrchestrationTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.f, self.driver = self.fixture.f, self.fixture.driver
        self.f.controller = Controller(self.f.database, autonomous=True)
        self.f.broker.controller = self.driver.controller = self.f.controller
        self.now = 1000
        self.driver.clock = self.f.broker.clock = lambda: self.now
        original = self.f.github.branch_sha.side_effect
        self.valid_tag = True
        self.f.github.branch_sha.side_effect = lambda route: (
            self.f.spec.runtime_sha if self.valid_tag else 'f' * 40
        ) if route.ref.startswith('clusterfork-runtime-') else original(route)
        self.fixture.start_coding()
        self.f.advance('finished', has_changes=True)
        self.f.call(Role.CODING, 'finish', status='completed')
        self.fixture.fixture.runs[self.f.key].update(status='completed', conclusion='success')
        self.driver.tick()
        self.assertIsNotNone(self.fixture.pull)
        checks = self.fixture.pull['commits']['nodes'][0]['commit']['statusCheckRollup']['contexts']['nodes']
        for check in checks:
            check.update(status='COMPLETED', conclusion='FAILURE')
        self.fixture.fixture.ready = False
        self.driver.apply(Hold('deployment:rollout', 'Waiting for open publications to drain.'))
        self.driver.tick()
        self.parent = self.f.controller.inspect(self.f.view.task_id)
        self.assertEqual(self.parent.attempt['retry_kind'], 'context')
        self.assertEqual(self.parent.attempt['pull_state'], 'open')
        self.deadline = self.parent.attempt['retry_at']
        self.assertEqual(self.f.github.dispatch.call_count, 2)

    def test_open_pr_repairs_on_verified_pinned_runtime_while_new_admissions_wait(self):
        self.fixture.restart()
        self.now = self.deadline
        self.driver.tick()
        repaired = self.f.controller.inspect(self.f.view.task_id)
        self.assertNotEqual(repaired.attempt['attempt_id'], self.parent.attempt['attempt_id'])
        self.assertEqual(repaired.attempt['attempt_kind'], 'repair')
        self.assertEqual(repaired.spec.runtime_sha, self.parent.spec.runtime_sha)
        self.assertEqual(self.f.github.dispatch.call_count, 3)
        route, inputs = self.f.github.dispatch.call_args.args
        self.assertEqual(route.ref, 'clusterfork-runtime-' + self.parent.spec.runtime_sha)
        self.assertEqual(inputs['phase'], 'review')
        second = next(v for v in self.f.controller.tasks() if v.issue_id == self.fixture.fixture.second_issue['id'])
        self.assertFalse(second.active)
        with self.assertRaisesRegex(Rejected, 'admissions are held'):
            self.f.controller.apply(Admit(second.task_id, self.driver.spec(
                self.fixture.fixture.second_issue, self.f.route)), now=self.now)

    def test_operator_and_unknown_deployment_holds_still_stop_repair(self):
        self.now = self.deadline
        for owner in ('operator:maintenance', 'deployment:security-freeze'):
            self.driver.apply(Hold(owner, 'Execution is intentionally held.'))
            self.driver.tick()
            self.assertEqual(self.f.github.dispatch.call_count, 2)
            self.assertEqual(self.f.controller.drain_retry_runtime(self.parent.task_id, now=self.now), '')
            self.driver.apply(Hold(owner, '', release=True))
        self.driver.tick()
        self.assertEqual(self.f.github.dispatch.call_count, 3)

    def test_moved_runtime_tag_cannot_authorize_drain_repair(self):
        self.now, self.valid_tag = self.deadline, False
        self.driver.tick()
        self.assertEqual(self.f.github.dispatch.call_count, 2)
        self.assertEqual(self.f.controller.inspect(self.parent.task_id).attempt['retry_at'], self.deadline)
        self.valid_tag = True
        self.driver.tick()
        self.assertEqual(self.f.github.dispatch.call_count, 3)

    def test_new_runtime_or_policy_cannot_cross_a_rollout_hold(self):
        self.now = self.deadline
        for spec in (replace(self.parent.spec, runtime_sha='f' * 40),
                     replace(self.parent.spec, policy_revision='f' * 64)):
            with self.subTest(spec=spec), self.assertRaisesRegex(Rejected, 'admissions are held'):
                self.f.controller.apply(Retry(self.parent.task_id, spec, 'repair', 'context'), now=self.now)
        self.assertEqual(self.f.github.dispatch.call_count, 2)

    def test_unverified_stop_never_allows_repair_during_rollout(self):
        self.now = self.deadline
        # A later manual rerun of the review phase is still running. Its original
        # completion does not prove all matching GitHub execution has stopped.
        self.fixture.fixture.run_history[0]['status'] = 'in_progress'
        self.driver.tick()
        self.assertEqual(self.f.github.dispatch.call_count, 2)
        self.assertEqual(self.f.controller.drain_retry_runtime(self.parent.task_id, now=self.now), '')


if __name__ == '__main__':
    unittest.main()
