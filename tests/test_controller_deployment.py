"""Deployment needs stopped jobs, resolved publication, and released credentials."""

from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'automation'),str(ROOT/'automation')]
from controller.deployment import verify_release
from controller.model import Rejected
from controller.server import Application
from common.clients import RequestError
import test_controller as fixtures


class ControllerDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ControllerTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.resources_idle=Mock(return_value=True)
        self.broker,self.driver=Mock(),Mock()
        self.driver.runtime_source_ready=self.driver.runtime_ready
        self.app=Application(self.fixture.controller,self.broker,self.driver,webhook_secret='fixture',
                             metadata={'revision':'a'*40},resources_idle=self.resources_idle)

    def test_unconfirmed_dispatch_cannot_be_treated_as_drained(self):
        self.fixture.admit()
        self.assertFalse(self.app.drained())
        self.resources_idle.assert_not_called()

    def test_a_stopped_retry_can_deploy_but_a_live_credential_lease_prevents_it(self):
        view=self.fixture.admit()
        self.fixture.external(view,conclusion='timed_out')
        self.assertTrue(self.app.drained())
        self.assertTrue(self.fixture.controller.active())
        self.resources_idle.return_value=False
        self.assertFalse(self.app.drained())

    def test_a_pull_request_keeps_deployment_waiting_after_jobs_stop(self):
        view=self.fixture.coding(self.fixture.admit())
        self.fixture.external(view,pull_state='open',pull_request='https://github.com/Owner/Repo/pull/1')
        self.assertFalse(self.app.drained())

    def test_release_verification_rejects_a_tag_pointing_to_other_code(self):
        self.driver.runtime_ready.return_value=True
        self.broker.github.request.return_value={'ref':'refs/tags/clusterfork-runtime-'+'a'*40,
                                                 'object':{'sha':'b'*40,'type':'commit'}}
        with self.assertRaisesRegex(Rejected,'reference'):
            verify_release(self.app,'a'*40)
        self.driver.runtime_ready.return_value=False
        self.broker.github.reset_mock()
        with self.assertRaises(Rejected):
            verify_release(self.app,'a'*40)
        self.broker.github.installation_token.assert_not_called()

    def test_lost_tag_creation_reply_is_verified_without_a_second_creation(self):
        self.driver.runtime_ready.return_value=True
        ref='refs/tags/clusterfork-runtime-'+'a'*40
        self.broker.github.request.side_effect=[RequestError(404),RequestError(),
                                                {'ref':ref,'object':{'sha':'a'*40,'type':'commit'}}]
        self.assertTrue(verify_release(self.app,'a'*40)['verified'])
        posts=[call for call in self.broker.github.request.call_args_list if call.kwargs.get('method')=='POST']
        self.assertEqual(len(posts),1)


if __name__=='__main__':
    unittest.main()
