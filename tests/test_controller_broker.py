"""Worker requests cross real controller transitions and existing fenced accounts."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
from controller.broker import Broker
from controller.core import Controller
from controller.model import Admit, IssueObserved, Rejected, Role, TaskSpec
from controller.store import Database
from dispatch_core import Registry
from dispatch_clients import GitHub, Linear
from readiness_policy import PROTOCOL, issue_revision, policy_version
from issue_images import image_digest
from worker_core import Accounts, ProviderAccounts
from test_remote_worker import MemoryTable, auth
from test_controller import ready
from test_controller_authorization import claims


class ControllerBrokerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Database(Path(directory.name)/"tasks.db")
        self.addCleanup(self.database.close)
        self.controller = Controller(self.database)
        self.registry = Registry.load(ROOT / "automation/azure/projects.json")
        self.route = next(iter(self.registry.routes.values()))
        self.issue = {"id": "66547614-8ed4-4928-834e-aac76f01afd2", "title": "Fix the dry-run flag",
                      "description": "Do not create directories in dry-run mode.", "identifier": "KIAN-1",
                      "project": {"id": self.route.project_id}, "url": "https://linear.app/test/issue/KIAN-1"}
        observed = IssueObserved(self.issue["id"], self.route.project_id, self.route.repository,
                                 self.registry.generation, self.issue["identifier"], True, True,
                                 content_revision=issue_revision(self.issue))
        self.spec = TaskSpec(issue_revision(self.issue), "b"*40, "a"*40, policy_version(self.registry,self.route), image_digest([]))
        self.controller.apply(observed, now=1000)
        self.view = self.controller.apply(Admit(observed.task_id,self.spec),now=1000)
        self.key = self.view.attempt["attempt_id"]
        self.pool = Accounts(MemoryTable(),clock=lambda:1000)
        self.account_a = self.pool.enroll(auth("a"))
        self.account_b = self.pool.enroll(auth("b"))
        self.accounts = ProviderAccounts({"codex":self.pool})
        self.linear = Mock(spec=Linear)
        self.linear.issue.return_value = self.issue
        self.linear.issue_comments.return_value = []
        self.github = Mock(spec=[*dir(GitHub), "request"])
        self.github.branch_sha.return_value = self.spec.context_sha
        self.github.workflow_changes_allowed.return_value = True
        self.github.request.return_value = {"total_count":1,"jobs":[{"name":"review / run","status":"completed","conclusion":"success"}]}
        self.broker = Broker(self.controller,self.registry,self.accounts,self.linear,self.github,clock=lambda:1000)

    def call(self, role, action, **fields):
        return self.broker.handle(action,claims(role),{"dispatch_key":self.key,**fields})

    def preflight(self, role, identity):
        self.call(role,"start",protocol=PROTOCOL)
        leased = self.call(role,"claim",account_id=identity)
        self.call(role,"save",account_id=identity,lease=leased["lease"],auth=leased["auth"],release=True)
        self.call(role,"bind_account",account_id=identity)

    def review(self):
        self.preflight(Role.REVIEW_SELECT,self.account_a)
        config=self.call(Role.REVIEW,"start",protocol=PROTOCOL)
        result=self.call(Role.REVIEW,"review",binding=config["binding"],result=ready())
        self.assertEqual(result,{"status":"passed"})

    def test_probes_save_refresh_and_release_before_binding_without_cross_job_credentials(self):
        self.preflight(Role.REVIEW_SELECT,self.account_a)
        row,_=self.pool.table.get(self.account_a)
        self.assertEqual(row["lease_until"],0)
        config=self.call(Role.REVIEW,"start",protocol=PROTOCOL)
        self.assertEqual(config["account_id"],self.account_a)
        self.assertNotIn("auth",config)
        self.assertEqual(self.call(Role.REVIEW,"accounts"),{"accounts":[self.account_a]})
        with self.assertRaisesRegex(Rejected,"another account"):
            self.call(Role.REVIEW,"claim",account_id=self.account_b)

    def test_same_run_review_and_coding_bind_different_accounts(self):
        self.review()
        self.preflight(Role.CODING_SELECT,self.account_b)
        config=self.call(Role.CODING,"start",protocol=PROTOCOL)
        self.assertEqual(config["account_id"],self.account_b)
        view=self.controller.inspect(self.view.task_id)
        self.assertEqual(view.session("review")["account_id"],self.account_a)
        self.assertEqual(view.session("coding")["account_id"],self.account_b)
        with self.assertRaisesRegex(Rejected,"another account"):
            self.call(Role.CODING,"claim",account_id=self.account_a)

    def test_ambiguous_probe_claim_blocks_other_identities_until_its_release(self):
        self.call(Role.REVIEW_SELECT,"start",protocol=PROTOCOL)
        leased=self.call(Role.REVIEW_SELECT,"claim",account_id=self.account_a)
        with self.assertRaisesRegex(Rejected,"previous quota probe"):
            self.call(Role.REVIEW_SELECT,"claim",account_id=self.account_b)
        self.call(Role.REVIEW_SELECT,"save",account_id=self.account_a,lease=leased["lease"],release=True)
        self.call(Role.REVIEW_SELECT,"claim",account_id=self.account_b)

    def test_coding_does_not_trust_only_a_passing_callback(self):
        self.review()
        self.github.request.return_value["jobs"][0]["conclusion"]="failure"
        with self.assertRaisesRegex(Rejected,"successful review job"):
            self.call(Role.CODING_SELECT,"start",protocol=PROTOCOL)

    def test_readiness_scope_and_protocol_are_checked_before_credentials(self):
        with self.assertRaisesRegex(Rejected,"protocol"):
            self.call(Role.REVIEW_SELECT,"start",protocol="wrong")
        self.preflight(Role.REVIEW_SELECT,self.account_a)
        self.call(Role.REVIEW,"start",protocol=PROTOCOL)
        with self.assertRaises(Rejected):
            self.call(Role.REVIEW,"github")
        self.github.branch_sha.return_value="f"*40
        with self.assertRaisesRegex(Rejected,"context changed"):
            self.call(Role.REVIEW,"claim",account_id=self.account_a)

    def test_fenced_cleanup_is_permitted_after_material_issue_edit(self):
        self.preflight(Role.REVIEW_SELECT,self.account_a)
        self.call(Role.REVIEW,"start",protocol=PROTOCOL)
        leased=self.call(Role.REVIEW,"claim",account_id=self.account_a)
        self.issue["title"]="An edited issue"
        with self.assertRaisesRegex(Rejected,"content changed"):
            self.call(Role.REVIEW,"checkout")
        self.call(Role.REVIEW,"save",account_id=self.account_a,lease=leased["lease"],release=True)
        self.assertEqual(self.pool.table.get(self.account_a)[0]["lease_until"],0)

    def test_coding_failover_requires_release_same_thread_and_codex_only(self):
        self.review()
        self.preflight(Role.CODING_SELECT,self.account_b)
        self.call(Role.CODING,"start",protocol=PROTOCOL)
        leased=self.call(Role.CODING,"claim",account_id=self.account_b)
        usage={"observed_at":1000,"windows":[{"name":"primary","used_percent":50,
               "resets_at":2000,"duration_minutes":300}]}
        self.call(Role.CODING,"progress",stage="coding",context_sha=self.spec.context_sha,
                  account_id=self.account_b,usage_before=usage)
        self.broker.metadata(self.controller.inspect(self.view.task_id),"coding",thread_id="thread-1")
        with self.assertRaisesRegex(Rejected,"Release the exhausted"):
            self.call(Role.CODING,"exhaust_account",account_id=self.account_b,thread_id="thread-1")
        self.call(Role.CODING,"save",account_id=self.account_b,lease=leased["lease"],release=True)
        with self.assertRaisesRegex(Rejected,"same coding thread"):
            self.call(Role.CODING,"exhaust_account",account_id=self.account_b,thread_id="different")
        self.call(Role.CODING,"exhaust_account",account_id=self.account_b,thread_id="thread-1")
        self.assertEqual(self.call(Role.CODING,"accounts"),{"accounts":[self.account_a]})
        with self.assertRaisesRegex(Rejected,"exhausted account"):
            self.call(Role.CODING,"claim",account_id=self.account_b)
        with self.assertRaisesRegex(Rejected,"Cross-provider"):
            self.call(Role.CODING,"claim",account_id="cursor:"+"2"*64)
        replacement=self.call(Role.CODING,"claim",account_id=self.account_a)
        with self.assertRaisesRegex(Rejected,"Release the quota probe"):
            self.call(Role.CODING,"bind_account",account_id=self.account_a)
        self.call(Role.CODING,"save",account_id=self.account_a,lease=replacement["lease"],release=True)
        self.call(Role.CODING,"bind_account",account_id=self.account_a)
        self.assertEqual(self.controller.inspect(self.view.task_id).session("coding")["account_id"],self.account_a)

    def test_coding_requires_a_live_assigned_lease_and_an_explicit_initial_context(self):
        self.review()
        self.preflight(Role.CODING_SELECT,self.account_b)
        self.call(Role.CODING,"start",protocol=PROTOCOL)
        usage={"observed_at":1000,"windows":[{"name":"primary","used_percent":50,
               "resets_at":2000,"duration_minutes":300}]}
        with self.assertRaisesRegex(Rejected,"lease is not active"):
            self.call(Role.CODING,"progress",stage="coding",context_sha=self.spec.context_sha,
                      account_id=self.account_b,usage_before=usage)
        self.call(Role.CODING,"claim",account_id=self.account_b)
        with self.assertRaisesRegex(Rejected,"reviewed context"):
            self.call(Role.CODING,"progress",stage="coding",account_id=self.account_b,usage_before=usage)


if __name__=="__main__":
    unittest.main()
