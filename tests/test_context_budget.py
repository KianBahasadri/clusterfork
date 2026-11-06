"""Guard reads cannot consume the agent's durable discovery allowance."""

from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'automation'))
from common.clients import RequestError, context_read_scope, request_json
from common.context import ContextError, MAX_REMOTE_CALLS
from controller.context import Context
from controller.core import Controller
from controller.model import Rejected, Role
from context_fixtures import ContextFixture
from call_budget import CallBudget


class ContextBudgetTests(unittest.TestCase):
    def setUp(self):
        self.fx = ContextFixture(self)
        f = self.fx.f
        f.controller = Controller(f.database, autonomous=True)
        f.broker.controller = f.controller
        self.opened = []

        def upstream(request, *, timeout):
            self.opened.append(request.full_url)
            content = self.fx.linear_request(request.full_url, token=request.get_header('Authorization'),
                                            method=request.get_method(), data=json.loads(request.data))
            return BytesIO(json.dumps(content).encode())

        opener = patch('common.clients.urlopen', side_effect=upstream)
        self.addCleanup(opener.stop)
        opener.start()
        # Use the actual HTTP transport rather than bypassing its before/after
        # callbacks through the fixture's injected request function.
        f.linear.request = request_json
        config = self.fx.start_review()
        self.fx.complete('list')
        self.fx.approve(config)
        self.fx.start_coding()

    def view(self):
        f = self.fx.f
        return f.controller.inspect(f.view.task_id)

    def calls(self):
        with self.fx.f.database.transaction() as db:
            return db.execute('SELECT calls FROM context_budgets WHERE attempt_id=?',
                              (self.fx.f.key,)).fetchone()[0]

    def validate(self):
        return self.fx.f.broker.validate_context(self.view(), Role.CODING)

    def test_repeated_real_validation_reads_preserve_discovery_and_publication_authority(self):
        f = self.fx.f
        before, sources = self.calls(), f.broker.context.sources(self.view())
        with CallBudget(self, linear=f.linear, github=f.github,
                        exact={'linear.query': 650}):
            for _ in range(130):
                self.validate()
        self.assertEqual(self.calls(), before)
        f.advance('publishing')
        self.fx.call(Role.CODING, 'github')
        current = self.view()
        self.assertFalse(current.review_attention or current.attempt['retry_kind'])
        self.assertEqual(current.attempt['attempt_id'], f.key)
        self.assertEqual(f.broker.context.sources(current), sources)

    def test_discovery_counts_each_real_http_attempt_once_and_still_has_a_durable_limit(self):
        f = self.fx.f
        context = f.broker.context
        before, opened = self.calls(), len(self.opened)
        with context_read_scope(lambda: context.check_budget(self.view(), lambda: None), after=lambda: None):
            for _ in range(MAX_REMOTE_CALLS - before):
                request_json('https://api.linear.app/graphql', token='lin_api_CONTROLLER_ONLY', method='POST',
                             data={'query': 'query { viewer { id } }', 'variables': {}})
            with self.assertRaisesRegex(ContextError, 'budget_exceeded'):
                request_json('https://api.linear.app/graphql', token='lin_api_CONTROLLER_ONLY', method='POST',
                             data={'query': 'query { viewer { id } }', 'variables': {}})
        self.assertEqual(len(self.opened) - opened, MAX_REMOTE_CALLS - before)
        self.assertEqual(self.calls(), MAX_REMOTE_CALLS)
        # Starting a new service cannot reset discovery or starve mandatory
        # validation of evidence that was already retrieved and approved.
        f.broker.context = Context(f.database, f.linear, f.github)
        self.validate()
        self.assertEqual(self.calls(), MAX_REMOTE_CALLS)

    def test_validation_retains_contract_revocation_after_many_successful_passes(self):
        for _ in range(70):
            self.validate()
        self.fx.root['description'] += ' An actual contract change.'
        with self.assertRaisesRegex(Rejected, 'material_evidence_changed'):
            self.validate()

    def test_post_read_authority_loss_does_not_return_a_successful_response(self):
        before = self.calls()
        def revoked():
            raise Rejected('Authority revoked during the read')
        with context_read_scope(lambda: self.fx.f.broker.context.check_budget(self.view(), lambda: None), after=revoked):
            with self.assertRaisesRegex(Rejected, 'revoked'):
                request_json('https://api.linear.app/graphql', token='lin_api_CONTROLLER_ONLY', method='POST',
                             data={'query': 'query { viewer { id } }', 'variables': {}})
        self.assertEqual(self.calls(), before + 1)

    def test_a_failed_http_attempt_spends_one_discovery_call(self):
        before = self.calls()
        with patch('common.clients.urlopen', side_effect=HTTPError('https://api.linear.app/graphql', 503, '', {}, None)):
            with context_read_scope(lambda: self.fx.f.broker.context.check_budget(self.view(), lambda: None), after=lambda: None):
                with self.assertRaises(RequestError):
                    request_json('https://api.linear.app/graphql')
        self.assertEqual(self.calls(), before + 1)

    def test_each_validation_pass_still_enforces_its_own_finite_call_limit(self):
        before, opened = self.calls(), len(self.opened)
        with patch('controller.context.MAX_REMOTE_CALLS', 3):
            with self.assertRaisesRegex(Rejected, 'budget is exhausted'):
                self.validate()
        self.assertEqual(len(self.opened) - opened, 3)
        self.assertEqual(self.calls(), before)


if __name__ == '__main__':
    unittest.main()
