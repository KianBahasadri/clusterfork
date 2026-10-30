"""Existing steering semantics consume current read-only controller views."""

from datetime import datetime,timezone
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"automation"),str(ROOT/"automation/azure")]
from controller.instructions import Instructions
from controller.model import Rejected, Role
from readiness_policy import PROTOCOL
from test_remote_worker import MemoryTable
import test_controller_broker as fixtures


class ControllerInstructionTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.ControllerBrokerTests(methodName="runTest")
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f=self.f
        f.issue.update(state={"id":f.route.ready_state_ids[0],"type":"unstarted"})
        f.issue["project"].update(labels={"nodes":[{"name":f.registry.enrollment_label}]})
        self.table=MemoryTable()
        self.instructions=Instructions(f.controller,f.registry,self.table,f.linear,clock=lambda:1000)
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


if __name__=="__main__":
    unittest.main()
