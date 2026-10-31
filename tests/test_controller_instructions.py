"""Existing steering semantics consume current read-only controller views."""

from datetime import datetime,timezone
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"automation")]
from controller.instructions import Instructions
from controller.model import Rejected, Role
from common.policy import PROTOCOL
import test_controller_broker as fixtures


class ControllerInstructionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.ControllerBrokerTests(methodName="runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f=self.f
        f.issue.update(state={"id":f.route.ready_state_ids[0],"type":"unstarted"})
        f.issue["project"].update(labels={"nodes":[{"name":f.registry.enrollment_label}]})
        self.instructions=Instructions(f.controller,f.registry,f.linear,clock=lambda:1000)
        f.broker.steering=self.instructions
        f.linear.viewer_id.return_value="a76cf048-2395-425b-ae2b-15779a1de587"
        f.review()
        f.preflight(Role.CODING_SELECT,f.account_b)
        f.call(Role.CODING,"start",protocol=PROTOCOL)
        f.call(Role.CODING,"claim",account_id=f.account_b)
        usage={"observed_at":1000,"windows":[{"name":"primary","used_percent":50,"resets_at":2000,"duration_minutes":300}]}
        f.call(Role.CODING,"progress",stage="coding",context_sha=f.spec.context_sha,
               account_id=f.account_b,usage_before=usage)
        f.call(Role.CODING,"steering_open",thread_id="thread-1",turn_id="turn-1")
        self.comment={"id":"00000000-0000-0000-0000-000000000001","body":"@clusterfork Preserve the config defaults.",
                      "createdAt":datetime.fromtimestamp(1001,timezone.utc).isoformat(),
                      "user":{"id":f.registry.steering_user_ids[0]},"botActor":None,"parent":None}
        f.linear.issue_comments.return_value=[self.comment]

    def call(self,action,**fields):
        return self.f.call(Role.CODING,action,thread_id="thread-1",turn_id="turn-1",**fields)

    def test_instruction_is_attempt_bound_delivered_once_and_acknowledged(self):
        result=self.call("instructions")
        instruction=result["instruction"]
        self.assertEqual(instruction["text"],"Preserve the config defaults.")
        self.assertEqual(instruction["dispatch_key"],self.f.key)
        self.assertIsNone(self.call("instructions")["instruction"])
        response=self.call("instruction_result",comment_id=instruction["comment_id"],delivery=instruction["delivery"],status="accepted")
        self.assertEqual(response,{"status":"accepted"})
        self.assertIsNone(self.call("instructions")["instruction"])

    def test_untrusted_author_cannot_instruct_or_replace_a_thread(self):
        self.comment["user"]["id"]="00000000-0000-0000-0000-000000000002"
        self.assertIsNone(self.call("instructions")["instruction"])
        with self.assertRaisesRegex(Rejected,"cannot be replaced"):
            self.f.call(Role.CODING,"steering_open",thread_id="another-thread",turn_id="turn-2")

    def test_delivery_timeout_is_uncertain_and_never_replayed(self):
        instruction=self.call("instructions")["instruction"]
        self.instructions.clock=lambda:1091
        self.assertIsNone(self.call("instructions")["instruction"])
        from controller.instructions import rows
        with self.f.database.transaction() as db:
            self.assertEqual(rows(db)[0]['state'],'uncertain')
        # A matching late acknowledgement may settle the original delivery.
        self.assertEqual(self.call("instruction_result",comment_id=instruction['comment_id'],
                                  delivery=instruction['delivery'],status='accepted'),{'status':'accepted'})

    def test_first_observed_text_is_frozen_and_comments_are_ordered(self):
        second=deepcopy(self.comment)
        second.update(id='00000000-0000-0000-0000-000000000002',body='@clusterfork Second instruction')
        self.f.linear.issue_comments.return_value=[second,self.comment]
        self.instructions.observe(self.f.issue['id'])
        self.comment['body']='@clusterfork Edited after observation'
        first=self.call('instructions')['instruction']
        self.assertEqual(first['text'],'Preserve the config defaults.')
        self.call('instruction_result',comment_id=first['comment_id'],delivery=first['delivery'],status='accepted')
        self.assertEqual(self.call('instructions')['instruction']['text'],'Second instruction')

    def test_database_restart_retains_in_flight_delivery_and_report_signatures(self):
        instruction=self.call('instructions')['instruction']
        writes=self.f.linear.status_comment.call_count
        from controller.core import Controller
        from controller.store import Database
        path=self.f.database.path
        self.f.database.close()
        reopened=Database(path)
        self.addCleanup(reopened.close)
        self.f.database=reopened
        self.f.controller=Controller(reopened)
        self.f.broker.controller=self.f.controller
        self.f.broker.accounts=self.f.controller.accounts
        self.instructions.controller=self.f.controller
        self.assertIsNone(self.call('instructions')['instruction'])
        self.assertEqual(self.f.linear.status_comment.call_count,writes)
        with self.assertRaises(Rejected):
            self.call('instruction_result',comment_id=instruction['comment_id'],delivery='wrong',status='accepted')

    def test_stopped_session_never_transfers_a_queued_instruction_to_another_attempt(self):
        self.instructions.observe(self.f.issue['id'])
        self.f.call(Role.CODING,'finish',status='failed')
        self.instructions.reconcile()
        from controller.instructions import rows
        with self.f.database.transaction() as db:
            self.assertEqual(rows(db)[0]['state'],'failed')


if __name__=="__main__":
    unittest.main()
