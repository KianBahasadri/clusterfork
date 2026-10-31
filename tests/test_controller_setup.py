"""Enrollment evidence identifies the central release without exposing credentials."""

import base64
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'automation'),str(ROOT/'automation')]
from controller.setup import evidence
from controller.workflows import ENGINE,WORKFLOW,render
from common.routing import Registry


class ControllerSetupTests(unittest.TestCase):
    def setUp(self):
        self.registry=Registry.load(ROOT/'automation/controller/projects.json')
        self.route=next(iter(self.registry.routes.values()))
        self.manifest={'revision':'a'*40}
        self.github=Mock()
        self.github._installation.return_value=('private-jwt',{'permissions':{'contents':'write','pull_requests':'write','actions':'write','workflows':'write'}})
        self.github.installation_token.return_value='private-installation-token'
        self.github.branch_sha.return_value='b'*40

    def responses(self,*,sha='a'*40,source=None):
        self.github.request.side_effect=[
            {'ref':'refs/tags/clusterfork-runtime-'+'a'*40,'object':{'sha':sha,'type':'commit'}},
            {'state':'active'},
            {'content':base64.b64encode(source or render()[WORKFLOW].encode()).decode()}]

    def test_evidence_uses_central_workflow_and_bound_target_without_credentials(self):
        self.responses()
        result=evidence(self.registry,self.github,self.manifest,self.route.project_id)
        self.assertTrue(result['app_access'])
        self.assertTrue(result['runtime_tag_verified'])
        self.assertTrue(result['workflow_verified'])
        self.assertEqual(result['workflow_repository'],ENGINE)
        self.assertEqual(result['branch_sha'],'b'*40)
        self.assertNotIn('private-',json.dumps(result))
        self.github.installation_token.assert_called_once_with(ENGINE,permissions={'contents':'read','actions':'read'})

    def test_another_tag_target_or_unexpected_graph_cannot_pass_setup(self):
        self.responses(sha='c'*40)
        self.assertFalse(evidence(self.registry,self.github,self.manifest,self.route.project_id)['runtime_tag_verified'])
        self.responses(source=b'unexpected workflow')
        self.assertFalse(evidence(self.registry,self.github,self.manifest,self.route.project_id)['workflow_verified'])
