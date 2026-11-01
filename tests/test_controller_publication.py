"""Real GitHub mutation adapters preserve exact-head merge and error semantics."""

import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from common.clients import GitHub, RequestError
from common.publication import merge_ready
from common.routing import DispatchError


class ControllerPublicationTests(unittest.TestCase):
    def setUp(self):
        self.github = GitHub('app', 'private-key', request=Mock())
        self.github.installation_token = Mock(return_value='scoped-token')
        self.github.workflow_changes_allowed = Mock(return_value=True)
        self.route = SimpleNamespace(repository='Owner/Repo', ref='main')
        self.url = 'https://github.com/Owner/Repo/pull/1'

    def test_controller_merge_is_bound_to_commit_and_approved_workflow_permissions(self):
        self.github.request.return_value = {'merged': True}
        self.github.merge_worker_pull(self.route, self.url, 'c'*40)
        self.github.installation_token.assert_called_once_with('Owner/Repo', permissions={
            'contents': 'write', 'pull_requests': 'write', 'workflows': 'write'})
        self.github.request.assert_called_once_with('https://api.github.com/repos/Owner/Repo/pulls/1/merge',
            token='Bearer scoped-token', method='PUT', data={'sha': 'c'*40, 'merge_method': 'squash'})

    def test_controller_creates_only_the_prepared_pr(self):
        self.github.request.return_value = {'html_url': self.url}
        plan = {'branch': 'clusterfork/task', 'title': 'Implement task', 'body': 'Validated work'}
        self.github.create_worker_pull(self.route, plan)
        self.github.installation_token.assert_called_once_with('Owner/Repo', permissions={'pull_requests': 'write'})
        self.github.request.assert_called_once_with('https://api.github.com/repos/Owner/Repo/pulls',
            token='Bearer scoped-token', method='POST', data={'head': plan['branch'], 'base': 'main',
                'title': plan['title'], 'body': plan['body']})

    def test_remote_rejection_and_uncertainty_remain_distinguishable(self):
        for method in ('publication', 'merge'):
            for status in (None, 403, 409, 422, 429, 500, 503):
                with self.subTest(method=method, status=status):
                    self.github.request.side_effect = RequestError(status)
                    with self.assertRaises(DispatchError) as caught:
                        if method == 'merge':
                            self.github.merge_worker_pull(self.route, self.url, 'c'*40)
                        else:
                            self.github.create_worker_pull(self.route, {'branch': 'work', 'title': 'Task', 'body': 'Body'})
                    self.assertEqual(caught.exception.uncertain, status is None or status >= 500)

    def test_missing_success_or_foreign_reference_cannot_count_as_merge(self):
        self.github.request.return_value = {'merged': False}
        with self.assertRaises(DispatchError) as caught:
            self.github.merge_worker_pull(self.route, self.url, 'c'*40)
        self.assertFalse(caught.exception.uncertain)
        self.github.request.reset_mock()
        with self.assertRaises(ValueError):
            self.github.merge_worker_pull(self.route, 'https://github.com/Other/Repo/pull/1', 'c'*40)
        self.github.request.assert_not_called()

    def test_complete_required_checks_and_mergeability_gate_the_request(self):
        checks = [{'__typename': 'CheckRun', 'name': 'test', 'isRequired': True,
                   'status': 'COMPLETED', 'conclusion': 'SUCCESS'}]
        pull = {'state': 'OPEN', 'merged': False, 'isDraft': False,
                'mergeable': 'MERGEABLE', 'mergeStateStatus': 'CLEAN',
                'commits': {'nodes': [{'commit': {'statusCheckRollup': {
                    'contexts': {'nodes': checks, 'pageInfo': {'hasNextPage': False}}}}}]}}
        self.assertTrue(merge_ready(pull, ['test']))
        self.assertFalse(merge_ready(pull, ['test', 'lint']))
        for changed in ({'isDraft': True}, {'state': 'CLOSED'}, {'mergeable': 'UNKNOWN'},
                        {'mergeStateStatus': 'BLOCKED'}, {'mergeStateStatus': 'BEHIND'}):
            with self.subTest(changed=changed):
                self.assertFalse(merge_ready({**pull, **changed}, ['test']))
        for changed in ({'status': 'IN_PROGRESS'}, {'conclusion': 'FAILURE'}, {'isRequired': False}):
            with self.subTest(check=changed):
                other = copy.deepcopy(pull)
                other['commits']['nodes'][0]['commit']['statusCheckRollup']['contexts']['nodes'][0].update(changed)
                self.assertFalse(merge_ready(other, ['test']))


if __name__ == '__main__':
    unittest.main()
