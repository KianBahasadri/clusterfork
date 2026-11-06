"""Accepted, attempt-bound steering must coexist with immutable context reads."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'automation'))
from controller.core import Controller
from controller.instructions import Instructions, rows
from controller.model import Rejected, Role
from context_fixtures import ContextFixture
import json


class SteeringContextTests(unittest.TestCase):
    def setUp(self):
        self.start()

    def start(self, *, cached=True, prerequisite=True):
        self.fx = ContextFixture(self)
        f = self.fx.f
        f.controller = Controller(f.database, autonomous=True)
        f.broker.controller = f.controller
        self.actor = 'a76cf048-2395-425b-ae2b-15779a1de587'
        f.linear.viewer_id = lambda: self.actor
        self.fx.root['project']['labels']['nodes'] = [{'name': f.registry.enrollment_label}]
        if not prerequisite:
            self.fx.dependencies[self.fx.root['id']] = []
        self.instructions = Instructions(f.controller, f.registry, f.linear, clock=f.broker.clock)
        # ContextFixture's read-only transport does not implement mutations.
        # Add acknowledgement content using the actual durable receipt below.
        self.instructions.report = lambda: None
        f.broker.steering = self.instructions
        config = self.fx.start_review()
        if cached:
            self.fx.complete('list')
        self.fx.approve(config)
        self.fx.start_coding()
        self.fx.call(Role.CODING, 'steering_open', thread_id='thread-1', turn_id='turn-1')
        self.comment = {
            'id': '00000000-0000-0000-0000-000000000001',
            'body': '@clusterfork Preserve the existing defaults.',
            'createdAt': datetime.fromtimestamp(1001, timezone.utc).isoformat(),
            'updatedAt': 'version-1', 'user': {'id': f.registry.steering_user_ids[0]},
            'botActor': None, 'parent': None,
        }

    def accept(self):
        fx = self.fx
        if self.comment not in fx.comments[fx.root['id']]:
            fx.comments[fx.root['id']].append(self.comment)
        instruction = fx.call(Role.CODING, 'instructions', thread_id='thread-1', turn_id='turn-1')['instruction']
        self.assertIsNotNone(instruction)
        fx.call(Role.CODING, 'instruction_result', comment_id=instruction['comment_id'],
                delivery=instruction['delivery'], thread_id=instruction['thread_id'],
                turn_id=instruction['turn_id'], status='accepted')
        with fx.f.database.transaction() as db:
            return rows(db)[0]

    def complete(self):
        f = self.fx.f
        command = f.controller.executor_command(f.view.task_id, f.key, now=f.broker.clock())
        return self.fx.call(Role.CODING, 'step_result', step_id=command['step_id'], outcome='completed', result={})

    def test_accepted_steering_and_its_own_ack_do_not_revoke_the_coding_attempt(self):
        fx = self.fx
        before = deepcopy(fx.f.broker.context.sources(fx.f.view))
        row = self.accept()
        ack = {**self.comment, 'id': row['ack_id'], 'body': 'Clusterfork instruction: agent accepted.',
               'user': {'id': self.actor}}
        fx.comments[fx.root['id']].append(ack)
        self.complete()
        current = fx.f.controller.inspect(fx.f.view.task_id)
        self.assertEqual(current.attempt['attempt_id'], fx.f.key)
        self.assertFalse(current.review_attention or current.attempt['retry_kind'])
        self.assertEqual(fx.f.broker.context.sources(current), before)

    def test_context_read_racing_instruction_intake_keeps_its_source_digest(self):
        self.start(cached=False)
        fx = self.fx
        fx.comments[fx.root['id']].append(self.comment)
        fx.complete('list', role=Role.CODING)
        before = fx.f.broker.context.sources(fx.f.view)
        self.accept()
        self.complete()
        after = fx.f.broker.context.sources(fx.f.view)
        for first, second in zip(before, after):
            self.assertEqual({key: first[key] for key in ('content', 'version', 'material')},
                             {key: second[key] for key in ('content', 'version', 'material')})
        self.assertEqual(len(before), len(after))

    def test_editing_or_deleting_a_frozen_instruction_does_not_resend_or_abort_it(self):
        row = self.accept()
        self.comment['body'] = '@clusterfork Edited after acceptance.'
        self.fx.f.broker.validate_context(self.fx.f.controller.inspect(self.fx.f.view.task_id), Role.CODING)
        self.fx.comments[self.fx.root['id']].remove(self.comment)
        self.complete()
        with self.fx.f.database.transaction() as db:
            self.assertEqual(rows(db)[0]['text'], row['text'])
            self.assertEqual(rows(db)[0]['state'], 'accepted')

    def test_an_ordinary_reply_to_accepted_steering_remains_material(self):
        self.accept()
        reply = {**self.comment, 'id': '00000000-0000-0000-0000-000000000002',
                 'body': 'Actually change the deployment contract.', 'parent': {'id': self.comment['id']}}
        self.fx.comments[self.fx.root['id']].append(reply)
        with self.assertRaisesRegex(Rejected, 'material_evidence_changed'):
            self.complete()

    def test_an_addressed_prefix_without_a_durable_receipt_grants_no_exemption(self):
        self.fx.comments[self.fx.root['id']].append(self.comment)
        with self.assertRaisesRegex(Rejected, 'material_evidence_changed'):
            self.complete()

    def test_a_forged_acknowledgement_from_another_author_remains_material(self):
        row = self.accept()
        self.fx.comments[self.fx.root['id']].append({**self.comment, 'id': row['ack_id'],
                                                   'body': 'Clusterfork instruction: agent accepted.'})
        with self.assertRaisesRegex(Rejected, 'material_evidence_changed'):
            self.complete()

    def test_a_receipt_from_another_attempt_does_not_exempt_a_comment(self):
        row = self.accept()
        with self.fx.f.database.transaction() as db:
            value = json.loads(db.execute('SELECT data FROM instructions WHERE comment_id=?',
                                          (row['comment_id'],)).fetchone()[0])
            value['dispatch_key'] = 'f' * 64
            db.execute('UPDATE instructions SET data=? WHERE comment_id=?',
                       (json.dumps(value), row['comment_id']))
        with self.assertRaisesRegex(Rejected, 'material_evidence_changed'):
            self.complete()

    def test_frozen_authorized_references_expand_the_read_only_context_graph(self):
        self.start(prerequisite=False)
        self.assertEqual(self.fx.read('issue', target='KIAN-941', role=Role.CODING,
                                      required=False)['status'], 'denied')
        self.comment['body'] = '@clusterfork Read KIAN-941 for the account contract.'
        self.accept()
        value = self.fx.complete('issue', target='KIAN-941', role=Role.CODING)
        self.assertEqual(value['identifier'], 'KIAN-941')
        self.complete()

    def test_steering_does_not_grant_github_access_outside_enrolled_repositories(self):
        self.comment['body'] = '@clusterfork Read https://github.com/foreign/project/pull/1.'
        self.accept()
        result = self.fx.read('pull', target='https://github.com/foreign/project/pull/1',
                               role=Role.CODING, required=False)
        self.assertEqual(result['status'], 'denied')
        self.complete()


if __name__ == '__main__':
    unittest.main()
