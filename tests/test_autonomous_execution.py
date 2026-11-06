"""Automatic execution prevents human-review dead ends without bypassing authority."""

from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'automation'))
from common.policy import AUTONOMOUS_RESULT_SCHEMA, review_prompt, validate_result
from common.clients import RequestError
from controller.core import Controller
from controller.autonomy import RETIRED, retired_states
from controller.linear_state import desired_state, workflow
from controller.model import (Admit, Assign, EvidenceChanged, Review, Role, Retry, Rejected,
                              InboxQueued, ContextObserved, Finish, StepResult, ProjectionUpdated, identity)
from controller.store import Database
from common.routing import Registry
from common.reporting import worker_branch
from context_fixtures import ContextFixture, DEPENDENCY
import test_controller as core_fixtures
from test_controller import SPEC, ACCOUNT_A, ready
from test_readiness import missing_evidence


class AutomaticExecutionTests(unittest.TestCase):
    def setUp(self):
        self.f = core_fixtures.ControllerTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.controller = Controller(self.f.database, autonomous=True)

    def assessment(self, result):
        f = self.f
        view = f.admit()
        f.start(view, Role.REVIEW_SELECT)
        f.apply(Assign(view.task_id, view.attempt['attempt_id'], Role.REVIEW_SELECT, ACCOUNT_A))
        f.start(view, Role.REVIEW)
        return f.apply(Review(view.task_id, view.attempt['attempt_id'], result, SPEC))

    def test_invalid_or_missing_evidence_results_never_require_human_review(self):
        for result in (None, {'bad': 'result'}, missing_evidence()):
            with self.subTest(result=result):
                if self.f.controller.tasks():
                    self.doCleanups()
                    self.setUp()
                held = self.assessment(result)
                self.assertFalse(held.review_attention)
                self.assertEqual(held.attempt['retry_kind'], 'context')
                self.assertIn('no human review', held.reason)
                self.assertTrue(held.active)

    def test_retry_wait_survives_duplicate_events_and_restart_then_releases_queue(self):
        f = self.f
        held = self.assessment({'invalid': True})
        deadline = held.attempt['retry_at']
        f.now += 10
        duplicate = f.apply(EvidenceChanged(held.task_id, held.attempt['attempt_id']))
        self.assertEqual(duplicate.attempt['retry_at'], deadline)
        stopped = f.external(held)
        self.assertFalse(stopped.active)
        self.assertEqual(stopped.phase, 'queued')
        other = f.admit(2)
        self.assertTrue(other.active)  # Cooling work does not monopolize the repo.
        f.database.close()
        f.database = Database(f.path)
        self.addCleanup(f.database.close)
        f.controller = Controller(f.database)
        restored = f.controller.inspect(held.task_id)
        self.assertTrue(f.controller.autonomous)
        self.assertEqual(restored.attempt['retry_at'], deadline)
        self.assertFalse(restored.review_attention)

    def test_more_than_three_attempts_remain_automatic_with_durable_backoff(self):
        f = self.f
        view = self.assessment({'invalid': True})
        delays = []
        for index in range(5):
            delays.append(view.attempt['retry_at'] - f.now)
            f.external(view)
            f.now = view.attempt['retry_at']
            view = f.apply(Admit(view.task_id, SPEC))
            f.start(view, Role.REVIEW_SELECT)
            f.apply(Assign(view.task_id, view.attempt['attempt_id'], Role.REVIEW_SELECT, ACCOUNT_A))
            f.start(view, Role.REVIEW)
            view = f.apply(Review(view.task_id, view.attempt['attempt_id'], {'invalid': True}, SPEC))
            self.assertFalse(view.review_attention)
        self.assertEqual(delays, [60, 120, 240, 480, 960])

    def test_cancellation_cannot_be_resurrected_by_automatic_retry(self):
        f = self.f
        view = self.assessment({'invalid': True})
        f.apply(replace(f.issue(), eligible=False, canceled=True))
        canceled = f.external(view)
        self.assertEqual(canceled.phase, 'canceled')
        f.now += 10000
        with self.assertRaises(Rejected):
            f.apply(Admit(view.task_id, SPEC))

    def test_stale_passing_assessment_cannot_dispatch_coding_after_context_failure(self):
        f = self.f
        reviewed = f.admit()
        f.start(reviewed, Role.REVIEW_SELECT)
        f.apply(Assign(reviewed.task_id, reviewed.attempt['attempt_id'], Role.REVIEW_SELECT, ACCOUNT_A))
        f.start(reviewed, Role.REVIEW)
        f.apply(Review(reviewed.task_id, reviewed.attempt['attempt_id'], ready(), SPEC))
        f.apply(EvidenceChanged(reviewed.task_id, reviewed.attempt['attempt_id']))
        stopped = f.external(reviewed)
        self.assertEqual(stopped.phase, 'queued')
        coding = [effect for effect in f.controller.effects('dispatch')
                  if json.loads(effect['payload']).get('phase') == 'coding']
        self.assertEqual(coding, [])

    def test_context_change_after_coding_schedules_repair_instead_of_publication_deadlock(self):
        f = self.f
        view = f.coding(f.admit())
        held = f.apply(ContextObserved(view.task_id, view.attempt['attempt_id'], replace(SPEC, context_sha='f' * 40)))
        self.assertEqual(held.phase, 'held')
        self.assertEqual(held.attempt['retry_kind'], 'context')
        stopped = f.external(view, conclusion='failure')
        self.assertEqual(stopped.phase, 'queued')
        self.assertFalse(stopped.active)

    def test_pending_reassessment_cannot_publish_or_merge_stale_prepared_work(self):
        f = self.f
        view = f.coding(f.admit())
        for _ in range(3):
            command = f.controller.executor_command(view.task_id, view.attempt['attempt_id'], now=f.now)
            result = ({'has_changes': True, 'branch': worker_branch(view.identifier, view.task_id),
                       'commit_sha': 'c' * 40, 'changed_files': 1, 'title': 'Implement task',
                       'body': f'<!-- clusterfork-dispatch:{view.task_id} -->'}
                      if command['kind'] == 'publishing' else {})
            f.apply(StepResult(view.task_id, view.attempt['attempt_id'], command['step_id'], 'completed', result))
        f.apply(Finish(view.task_id, view.attempt['attempt_id'], 'coding', 'completed'))
        f.external(view, pull_state='open', pull_request='https://github.com/Owner/Repo/pull/1')
        self.assertTrue(f.controller.publication_authority(view.task_id, view.attempt['attempt_id']).active)
        f.apply(EvidenceChanged(view.task_id, view.attempt['attempt_id']))
        waiting = f.external(view, pull_state='open', pull_request='https://github.com/Owner/Repo/pull/1')
        self.assertTrue(waiting.active)
        self.assertEqual(waiting.phase, 'published')
        with self.assertRaises(Rejected):
            f.controller.publication_authority(view.task_id, view.attempt['attempt_id'])

    def test_repeated_repairs_keep_backoff_and_continue_latest_coding_evidence(self):
        f = self.f
        view = f.coding(f.admit())
        parents = []
        for _ in range(3):
            parents.append(view.attempt['attempt_id'])
            failed = f.external(view, conclusion='failure', repairable=True,
                                pull_state='open', pull_request='https://github.com/Owner/Repo/pull/1')
            deadline = failed.attempt['retry_at']
            self.assertGreater(deadline, f.now)
            f.now += 10
            duplicate = f.external(view, conclusion='failure', repairable=True,
                                   pull_state='open', pull_request='https://github.com/Owner/Repo/pull/1')
            self.assertEqual(duplicate.attempt['retry_at'], deadline)
            self.assertTrue(duplicate.active)  # An open PR retains repository ownership.
            f.now = deadline
            retry = f.apply(Retry(view.task_id, SPEC, 'repair automatically', 'context'))
            self.assertEqual(retry.attempt['attempt_kind'], 'repair')
            self.assertEqual(f.controller.repair_parent(view.task_id)['attempt_id'], parents[-1])
            view = f.coding(retry)

    def test_upgrade_adopts_attention_without_resurrecting_canceled_work(self):
        f = self.f
        f.controller = Controller(f.database, autonomous=False)
        held = self.assessment({'invalid': True})
        f.apply(replace(f.issue(), workflow={'state': 'attention', 'type': 'started', 'ready': ['todo'],
                                           'in_progress': 'progress', 'blocked': 'attention', 'done': 'done'}))
        transition = {'actor_id': 'operator', 'from_state': 'todo', 'to_state': 'attention', 'changed_at': f.now}
        event = {'id': held.issue_id, 'type': 'Issue', 'submission': transition}
        f.apply(InboxQueued(identity(event), event))
        self.assertFalse(f.controller.inspect(held.task_id).eligible)
        f.external(held)
        with f.database.transaction() as db:
            db.execute("DELETE FROM metadata WHERE key='execution:adopted-v2'")
        f.controller = Controller(f.database, autonomous=True)
        adopted = f.controller.inspect(held.task_id)
        self.assertFalse(adopted.review_attention)
        self.assertNotIn('explicitly move the issue to Todo', adopted.reason)
        # The new registry has no attention mapping. A durable retired-state
        # record keeps old intent harmless after the issue returns to Todo.
        migrated = f.apply(replace(f.issue(), workflow={'state': 'attention', 'type': 'started', 'ready': ['todo'],
                                                        'in_progress': 'progress', 'blocked': '', 'done': 'done'}))
        self.assertTrue(migrated.eligible)
        with f.database.transaction() as db:
            self.assertEqual(desired_state(db, dict(db.execute('SELECT * FROM tasks WHERE task_id=?',
                                                             (held.task_id,)).fetchone()))[1], 'todo')
        observed = replace(f.issue(), workflow={'state': 'todo', 'type': 'unstarted', 'ready': ['todo'],
                                                'in_progress': 'progress', 'blocked': '', 'done': 'done'})
        self.assertTrue(f.apply(observed).eligible)
        with f.database.transaction() as db:
            self.assertEqual(workflow(db, held.task_id)['blocked'], '')
        f.database.close()
        f.database = Database(f.path)
        self.addCleanup(f.database.close)
        f.controller = Controller(f.database)
        self.assertTrue(f.apply(observed).eligible)

    def test_explicit_human_pause_still_withdraws_execution(self):
        f = self.f
        view = f.admit(workflow={'state': 'todo', 'type': 'unstarted', 'ready': ['todo'],
                                 'in_progress': 'progress', 'blocked': '', 'done': 'done'})
        transition = {'actor_id': 'human', 'from_state': 'todo', 'to_state': 'backlog', 'changed_at': f.now}
        event = {'id': view.issue_id, 'type': 'Issue', 'submission': transition}
        f.apply(InboxQueued(identity(event), event))
        stopped = f.external(view)
        self.assertFalse(stopped.eligible)
        self.assertFalse(stopped.review_attention)
        with self.assertRaises(Rejected):
            f.apply(Retry(view.task_id, SPEC, 'paused', 'context'))

    def test_v1_false_pause_recovers_from_projection_history_without_postponing_retry(self):
        f = self.f
        held = self.assessment({'invalid': True})
        deadline = held.attempt['retry_at']
        f.external(held)
        observation = replace(f.issue(), workflow={'state': 'todo', 'type': 'unstarted', 'ready': ['todo'],
                                                   'in_progress': 'progress', 'blocked': '', 'done': 'done'})
        f.apply(observation)
        # Replay a v1 database after it lost the live blocked mapping. Its own
        # old attention projection is proof; a human restoration is not.
        with f.database.transaction() as db:
            for state, human in (('attention', False), ('backlog', True), ('done', False)):
                db.execute('INSERT INTO effects VALUES (?,?,?,?,?)',
                           (identity(state), held.task_id, 'linear_state',
                            json.dumps({'state': state, 'attention_at': f.now, 'human': human}), 'done'))
            db.execute('DELETE FROM metadata WHERE key=?', (RETIRED,))
            db.execute("DELETE FROM metadata WHERE key='execution:adopted-v2'")
        event = {'id': held.issue_id, 'type': 'Issue', 'submission': {
            'actor_id': 'operator', 'from_state': 'todo', 'to_state': 'attention', 'changed_at': f.now}}
        f.apply(InboxQueued(identity(event), event))
        self.assertEqual(f.controller.inspect(held.task_id).phase, 'held')
        f.controller = Controller(f.database, autonomous=True)
        resumed = f.controller.inspect(held.task_id)
        self.assertEqual(resumed.phase, 'queued')
        self.assertEqual(resumed.attempt['retry_at'], deadline)
        self.assertIn('Automatic diagnosis', resumed.reason)
        self.assertTrue(f.apply(observation).eligible)
        with f.database.transaction() as db:
            self.assertEqual(retired_states(db), {'attention'})
        f.now += 1
        pause = {'id': held.issue_id, 'type': 'Issue', 'submission': {
            'actor_id': 'operator', 'from_state': 'todo', 'to_state': 'backlog', 'changed_at': f.now}}
        f.apply(InboxQueued(identity(pause), pause))
        f.controller = Controller(f.database, autonomous=True)
        self.assertFalse(f.apply(replace(observation, workflow={**observation.workflow,
                                                               'state': 'backlog', 'type': 'backlog'})).eligible)

    def test_retry_projects_todo_again_after_initial_todo_effect_was_confirmed(self):
        f = self.f
        held = self.assessment({'invalid': True})
        observation = replace(f.issue(), workflow={'state': 'todo', 'type': 'unstarted', 'ready': ['todo'],
                                                   'in_progress': 'progress', 'blocked': '', 'done': 'done'})
        f.apply(observation)
        # Recreate the already-completed pre-upgrade Todo projection. Migration
        # must create a fresh effect rather than silently reusing that receipt.
        with f.database.transaction() as db:
            db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       (RETIRED, json.dumps(['attention'])))
            old = identity(held.task_id, 'linear_state', held.review_cycle, held.attempt['attempt_id'], 'todo', 0)
            db.execute('INSERT OR REPLACE INTO effects VALUES (?,?,?,?,?)',
                       (old, held.task_id, 'linear_state',
                        json.dumps({'state': 'todo', 'intent': '', 'outcome': 'confirmed'}), 'done'))
        attention = replace(observation, workflow={**observation.workflow, 'state': 'attention', 'type': 'started'})
        f.apply(attention)
        with f.database.transaction() as db:
            current = dict(db.execute('SELECT * FROM tasks WHERE task_id=?', (held.task_id,)).fetchone())
            key, state = desired_state(db, current)
        self.assertEqual(state, 'todo')
        self.assertNotEqual(key, old)
        self.assertTrue(f.apply(ProjectionUpdated(held.task_id, key, 'claimed',
                         {'structural': True, 'state': 'attention', 'type': 'started'})))
        f.apply(ProjectionUpdated(held.task_id, key, 'confirmed'))
        f.apply(EvidenceChanged(held.task_id, held.attempt['attempt_id']))
        pending = [effect for effect in f.controller.effects('linear_state') if effect['effect_id'] == key]
        self.assertEqual(pending, [])  # Duplicate diagnosis cannot recreate writes.

    def test_production_routes_have_no_attention_state(self):
        registry = Registry.load(ROOT / 'automation/controller/projects.json')
        self.assertTrue(registry.autonomous)
        for route in registry.routes.values():
            self.assertEqual(route.linear_states.blocked, '')

    def test_ready_only_schema_prevents_inconsistent_missing_evidence_outputs(self):
        schema = AUTONOMOUS_RESULT_SCHEMA['properties']
        self.assertEqual(schema['verdict']['enum'], ['ready'])
        self.assertEqual(schema['evidence']['maxItems'], 0)
        self.assertEqual(schema['blockers']['maxItems'], 0)
        for criterion in schema['criteria']['properties'].values():
            self.assertEqual(criterion['properties']['status']['enum'], ['satisfied'])
        self.assertEqual(validate_result(ready())['verdict'], 'ready')
        prompt = review_prompt({}, autonomous=True)
        self.assertIn('without human review', prompt)
        self.assertIn('Do not ask', prompt)


class AutomaticContextTests(unittest.TestCase):
    def setUp(self):
        self.fx = ContextFixture(self)
        f = self.fx.f
        f.controller = Controller(f.database, autonomous=True)
        f.broker.controller = f.controller

    def complete_coding(self):
        f = self.fx.f
        command = f.controller.executor_command(f.view.task_id, f.key, now=f.broker.clock())
        return self.fx.call(Role.CODING, 'step_result', step_id=command['step_id'],
                            outcome='completed', result={})

    def test_nonblocking_relation_changes_do_not_abort_active_coding(self):
        fx = self.fx
        config = fx.start_review()
        fx.complete('list')
        fx.approve(config)
        fx.start_coding()
        fx.dependencies[fx.root['id']].append({'id': 'harmless', 'type': 'related',
                                             'issue': {'id': 'new', 'identifier': 'KIAN-99'}})
        self.complete_coding()
        self.assertFalse(fx.f.controller.inspect(fx.f.view.task_id).review_attention)

    def test_converting_new_prerequisite_to_reference_does_not_revoke_running_authority(self):
        fx = self.fx
        config = fx.start_review()
        fx.complete('list')
        fx.approve(config)
        fx.start_coding()
        fx.add_prerequisite()
        fx.dependencies[fx.root['id']][-1]['type'] = 'related'
        self.complete_coding()
        self.assertFalse(fx.f.controller.inspect(fx.f.view.task_id).review_attention)

    def test_related_issue_workflow_status_is_not_a_prerequisite_contract(self):
        fx = self.fx
        fx.dependencies[fx.root['id']][0]['type'] = 'related'
        fx.comments[fx.root['id']].append({**fx.comments[DEPENDENCY][0], 'id': 'reference',
                                         'body': 'Reference: KIAN-941'})
        config = fx.start_review()
        fx.complete('list')
        fx.complete('issue', target='KIAN-941')
        fx.approve(config)
        fx.start_coding()
        fx.dependency['state'] = {'id': 'progress', 'type': 'started', 'name': 'In Progress'}
        self.complete_coding()
        self.assertFalse(fx.f.controller.inspect(fx.f.view.task_id).review_attention)

    def test_complete_new_evidence_during_coding_is_validated_and_sealed(self):
        fx = self.fx
        config = fx.start_review()
        fx.approve(config)
        fx.start_coding()
        fx.complete('list', role=Role.CODING)
        fx.complete('issue', target=DEPENDENCY, role=Role.CODING)
        self.complete_coding()
        self.assertFalse(fx.read('manifest', role=Role.CODING)['approval_required'])
        with fx.f.database.transaction() as db:
            self.assertFalse(db.execute('SELECT 1 FROM context_sources WHERE reviewed=0').fetchone())

    def test_changed_reviewed_contract_still_stops_old_authority_and_reassesses(self):
        fx = self.fx
        config = fx.start_review()
        fx.contract()
        fx.approve(config)
        fx.start_coding()
        fx.dependency['description'] += ' Incompatible new contract: private source contents.'
        with self.assertRaisesRegex(Rejected, 'material_evidence_changed') as caught:
            self.complete_coding()
        held = fx.f.controller.inspect(fx.f.view.task_id)
        self.assertEqual(held.phase, 'held')
        self.assertFalse(held.review_attention)
        self.assertEqual(held.attempt['retry_kind'], 'context')
        self.assertIn('issue KIAN-941', held.reason)
        self.assertIn('[source ', held.reason)
        self.assertNotIn('private source contents', held.reason)
        self.assertNotIn('private source contents', str(caught.exception))

    def test_revoked_access_reports_source_and_http_category_without_raw_response(self):
        fx = self.fx
        config = fx.start_review()
        fx.contract()
        fx.approve(config)
        fx.start_coding()
        fx.failed['KIAN-941'] = 403
        with self.assertRaisesRegex(Rejected, 'http_403.*issue KIAN-941'):
            self.complete_coding()
        held = fx.f.controller.inspect(fx.f.view.task_id)
        self.assertIn('http_403', held.reason)
        self.assertIn('issue KIAN-941', held.reason)
        self.assertFalse(held.review_attention)

    def test_incomplete_new_evidence_names_source_instead_of_an_opaque_403(self):
        fx = self.fx
        config = fx.start_review()
        fx.approve(config)
        fx.start_coding()
        fx.complete('list', role=Role.CODING)
        response = fx.read('issue', target=DEPENDENCY, role=Role.CODING)
        self.assertFalse(response['complete'])
        with self.assertRaisesRegex(Rejected, 'required_source_incomplete'):
            self.complete_coding()
        held = fx.f.controller.inspect(fx.f.view.task_id)
        self.assertIn(DEPENDENCY, held.reason)
        self.assertIn(response['source_key'], held.reason)
        self.assertFalse(held.review_attention)

    def test_temporary_read_outage_does_not_falsely_revoke_the_original_attempt(self):
        fx = self.fx
        config = fx.start_review()
        fx.contract()
        fx.approve(config)
        fx.start_coding()
        fx.failed['KIAN-941'] = 503
        with self.assertRaises(RequestError):
            self.complete_coding()
        current = fx.f.controller.inspect(fx.f.view.task_id)
        self.assertEqual(current.phase, 'executing')
        self.assertFalse(current.review_attention or current.attempt['retry_kind'])
        del fx.failed['KIAN-941']
        self.complete_coding()
        self.assertEqual(fx.f.controller.inspect(fx.f.view.task_id).attempt['attempt_id'], fx.f.key)


if __name__ == '__main__':
    unittest.main()
