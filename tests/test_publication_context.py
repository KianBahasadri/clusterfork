"""Native publication links must not invalidate their own approved task."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'automation'))
from common.context import ContextError
from controller.context import Context
from controller.core import Controller
from controller.driver import Driver
from controller.model import ExternalObserved, MergeRequested, Role
from controller.publication import reconcile, validate
from context_fixtures import ContextFixture, DEPENDENCY
from call_budget import CallBudget


class PublicationContextTests(unittest.TestCase):
    def setUp(self):
        self.start()

    def start(self, *, cached=True):
        self.fx = ContextFixture(self)
        f = self.fx.f
        f.controller = Controller(f.database, autonomous=True)
        f.broker.controller = f.controller
        self.config = self.fx.start_review()
        if cached:
            self.fx.complete('list')
        self.fx.approve(self.config)
        self.fx.start_coding()
        self.url = f'https://github.com/{f.route.repository}/pull/7'
        self.attachment = {'id': 'own-publication', 'title': 'Implement task', 'url': self.url}
        self.driver = Driver(f.controller, f.registry, f.linear, f.github, f.spec.runtime_sha,
                             runtime_ready=lambda: True, clock=f.broker.clock)
        # Lifecycle history/projection is covered by the driver tests. Keep the
        # native publication validation, context reads and transitions intact.
        self.driver.refresh = lambda view: None
        self.pull = None
        f.github.worker_pull.side_effect = lambda *args: deepcopy(self.pull)
        f.github.create_worker_pull.side_effect = self.create_pull

    def view(self):
        return self.fx.f.controller.inspect(self.fx.f.view.task_id)

    def observe(self, *, state='absent', checked=True):
        f = self.fx.f
        current = self.view()
        return self.driver.apply(ExternalObserved(current.task_id, f.key, current.attempt['run_id'],
            current.attempt['run_attempt'], True, 'success', True, checked,
            self.url if state != 'absent' else '', state))

    def prepare(self):
        f = self.fx.f
        f.advance('finished', has_changes=True)
        self.fx.call(Role.CODING, 'finish', status='completed')
        self.observe()
        f.github.branch_sha.return_value = 'c' * 40

    def create_pull(self, route, plan):
        self.pull = {'url': self.url, 'state': 'OPEN', 'merged': False,
                     'headRefOid': plan['commit_sha'], 'isDraft': False,
                     'mergeable': 'MERGEABLE', 'mergeStateStatus': 'CLEAN',
                     'commits': {'nodes': [{'commit': {'statusCheckRollup': {'contexts': {
                         'nodes': [], 'pageInfo': {'hasNextPage': False}}}}}]}}
        self.fx.attachments[self.fx.root['id']].append(deepcopy(self.attachment))
        return {'html_url': self.url}

    def revalidate(self, *, queries=5):
        f = self.fx.f
        context = Context(f.database, f.linear, f.github)
        with CallBudget(self, linear=f.linear, github=f.github,
                        exact={'linear.query': queries, 'linear.viewer_id': 1}):
            context.revalidate(self.view(), {r.repository.lower() for r in f.registry.routes.values()},
                               lambda: None, approved=True)

    def test_native_create_attachment_ci_and_merge_complete_without_a_new_attempt(self):
        f = self.fx.f
        self.prepare()
        before = f.broker.context.sources(self.view())
        with f.database.transaction() as db:
            approval = tuple(db.execute('SELECT * FROM context_approvals').fetchone())
        reconcile(self.driver)
        self.assertEqual(self.view().phase, 'published')
        self.assertTrue(self.view().attempt['publication_checked'])
        self.assertEqual(self.view().attempt['pull_request'], self.url)
        # Publication proof is durable, rather than a process-local URL cache.
        f.controller = self.driver.controller = Controller(f.database)
        validate(self.driver, self.view())
        self.driver.apply(MergeRequested(self.view().task_id, f.key, 'c' * 40))
        reconcile(self.driver)
        f.github.merge_worker_pull.assert_not_called()
        names = f.route.required_checks or ('test',)
        checks = self.pull['commits']['nodes'][0]['commit']['statusCheckRollup']['contexts']
        checks['nodes'] = [{'__typename': 'CheckRun', 'name': name, 'isRequired': True,
                           'status': 'COMPLETED', 'conclusion': 'SUCCESS'} for name in names]
        reconcile(self.driver)
        f.github.merge_worker_pull.assert_called_once_with(f.route, self.url, 'c' * 40)
        self.observe(state='merged')
        final = self.view()
        self.assertEqual(final.phase, 'completed')
        self.assertFalse(final.active or final.review_attention or final.attempt['retry_kind'])
        self.assertEqual(len(f.controller.attempt_history(final.task_id)), 1)
        self.assertEqual(f.broker.context.sources(final), before)
        with f.database.transaction() as db:
            self.assertEqual(tuple(db.execute('SELECT * FROM context_approvals').fetchone()), approval)

    def test_an_unverified_publication_reference_is_still_material(self):
        self.prepare()
        self.fx.attachments[self.fx.root['id']].append(self.attachment)
        self.observe(state='open', checked=False)
        with self.assertRaisesRegex(ContextError, 'material_evidence_changed'):
            self.revalidate()

    def test_unknown_or_foreign_links_do_not_inherit_our_publication_proof(self):
        for url in (self.url + '0', self.url + '?new=requirement',
                    self.url.replace('github.com', 'github.example.com'),
                    'https://github.com/foreign/repository/pull/7'):
            with self.subTest(url=url):
                self.start()
                self.prepare()
                reconcile(self.driver)
                self.fx.attachments[self.fx.root['id']].append(
                    {**self.attachment, 'id': 'new-contract', 'url': url})
                with self.assertRaisesRegex(ContextError, 'material_evidence_changed'):
                    self.revalidate()

    def test_our_publication_link_on_related_evidence_remains_material(self):
        self.fx.complete('issue', target=DEPENDENCY, role=Role.CODING)
        self.revalidate(queries=11)  # Seal the complete new evidence automatically.
        self.prepare()
        reconcile(self.driver)
        self.fx.attachments[DEPENDENCY].append(self.attachment)
        with self.assertRaisesRegex(ContextError, 'material_evidence_changed'):
            self.revalidate()

    def test_late_publication_proof_normalizes_both_sides_without_rewriting_the_snapshot(self):
        self.start(cached=False)
        f = self.fx.f
        # A source fetched during reconciliation can contain the auto-link
        # before the publication observation is committed.
        self.fx.attachments[self.fx.root['id']].append(self.attachment)
        context = f.broker.context
        self.fx.complete('list', role=Role.CODING)
        self.revalidate()
        before = context.sources(self.view())
        self.prepare()
        self.observe(state='open')
        self.revalidate()
        self.fx.attachments[self.fx.root['id']][0]['title'] = 'Updated display title'
        self.revalidate()
        self.assertEqual(context.sources(self.view()), before)

    def test_real_contract_changes_still_block_a_verified_own_publication(self):
        self.prepare()
        reconcile(self.driver)
        self.fx.root['description'] += ' Change the actual requirement.'
        with self.assertRaisesRegex(ContextError, 'material_evidence_changed'):
            self.revalidate()

    def test_a_changed_pr_head_still_cannot_merge_under_the_attachment_exemption(self):
        f = self.fx.f
        self.prepare()
        reconcile(self.driver)
        self.driver.apply(MergeRequested(self.view().task_id, f.key, 'c' * 40))
        self.pull['headRefOid'] = 'd' * 40
        reconcile(self.driver)
        f.github.merge_worker_pull.assert_not_called()


if __name__ == '__main__':
    unittest.main()
