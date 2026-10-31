"""Atomic account authority, durable projections and cached visual context."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'automation'))
from controller.core import Controller
from controller.images import Images
from controller.model import ReportConfirmed,Rejected,Role
from controller.store import Database
from common.images import image_digest
from common.policy import PROTOCOL
from common.accounts import WorkerError
import test_controller_broker as fixtures


class AtomicAccountTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.ControllerBrokerTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def test_failed_assignment_rolls_back_its_account_lease(self):
        f=self.f
        f.call(Role.REVIEW,'start',protocol=PROTOCOL)
        with patch.object(f.controller,'_assign',side_effect=Rejected('injected transition failure')):
            with self.assertRaises(Rejected):
                f.call(Role.REVIEW,'bind_account',account_id=f.account_a)
        self.assertEqual(f.pool.get(f.account_a)[0]['lease_until'],0)
        self.assertEqual(f.controller.inspect(f.view.task_id).session('review')['account_id'],'')

    def test_reserved_selection_cannot_be_taken_by_another_worker_during_checkout(self):
        f=self.f
        f.preflight(Role.REVIEW_SELECT,f.account_a)
        initial=f.pool.get(f.account_a)[0]
        with self.assertRaises(WorkerError):
            f.pool.claim(f.account_a,'another-worker')
        f.broker.clock=lambda:1050
        f.call(Role.REVIEW,'heartbeat')
        self.assertEqual(f.pool.get(f.account_a)[0]['fence'],initial['fence'])
        f.call(Role.REVIEW,'start',protocol=PROTOCOL)
        claimed=f.call(Role.REVIEW,'claim',account_id=f.account_a)
        self.assertEqual(claimed['lease'],initial['fence'])

    def test_initial_pool_access_closes_before_target_checkout(self):
        f=self.f
        f.call(Role.REVIEW,'start',protocol=PROTOCOL)
        with self.assertRaises(Rejected):
            f.call(Role.REVIEW,'checkout')
        f.call(Role.REVIEW,'bind_account',account_id=f.account_a)
        with self.assertRaises(Rejected):
            f.call(Role.REVIEW,'claim',account_id=f.account_b)
        with self.assertRaises(Rejected):
            f.call(Role.REVIEW,'checkout')
        f.call(Role.REVIEW,'start',protocol=PROTOCOL)
        f.call(Role.REVIEW,'checkout')
        f.github.installation_token.assert_called()

    def test_lost_claim_reply_can_be_discovered_without_opening_another_account(self):
        f=self.f
        f.call(Role.REVIEW,'start',protocol=PROTOCOL)
        first=f.call(Role.REVIEW,'claim',account_id=f.account_a)
        repeated=f.call(Role.REVIEW,'claim',account_id=f.account_a)
        self.assertEqual(first['lease'],repeated['lease'])
        with self.assertRaises(Rejected):
            f.call(Role.REVIEW,'claim',account_id=f.account_b)

    def test_old_fence_cannot_save_after_reassignment(self):
        f=self.f
        first=f.pool.claim(f.account_a,'worker-a')
        f.broker.clock=lambda:1121
        second=f.pool.claim(f.account_a,'worker-b')
        with self.assertRaises(WorkerError):
            f.pool.save(f.account_a,'worker-a',first['lease'],first['auth'])
        self.assertEqual(f.pool.get(f.account_a)[0]['fence'],second['lease'])

    def test_simultaneous_claims_grant_only_one_owner(self):
        def claim(owner):
            try:
                self.f.pool.claim(self.f.account_a,owner)
                return owner
            except WorkerError:
                return None
        with ThreadPoolExecutor(max_workers=2) as workers:
            results=list(workers.map(claim,('worker-a','worker-b')))
        winners=[owner for owner in results if owner]
        self.assertEqual(len(winners),1)
        self.assertEqual(self.f.pool.get(self.f.account_a)[0]['owner'],winners[0])


class DurableContextTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path=Path(directory.name)/'tasks.db'
        self.database=Database(self.path)
        self.addCleanup(self.database.close)

    def test_confirmed_report_survives_database_and_controller_restart(self):
        signature=hashlib.sha256(b'confirmed remote report').hexdigest()
        Controller(self.database).apply(ReportConfirmed('comment:fixture',signature),now=1000)
        self.database.close()
        reopened=Database(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(Controller(reopened).report_signature('comment:fixture'),signature)

    def test_image_cache_survives_restart_and_changed_bytes_require_revalidation(self):
        current=(ROOT/'tests/fixtures/issue-images/current.png').read_bytes()
        target=(ROOT/'tests/fixtures/issue-images/target.png').read_bytes()
        download=Mock(return_value=(current,'image/png'))
        issue={'id':'fixture','title':'A visual issue','description':'![Requirement](https://example.com/input.png)'}
        first=Images(self.database,download).snapshot(issue)
        self.database.close()
        reopened=Database(self.path)
        self.addCleanup(reopened.close)
        cache=Images(reopened,download)
        download.return_value=(target,'image/png')
        self.assertEqual(cache.snapshot(issue),first)
        self.assertEqual(download.call_count,1)
        changed=cache.snapshot(issue,revalidate=True)
        self.assertNotEqual(image_digest(changed),image_digest(first))
        self.assertEqual(download.call_count,2)
        cache.prune(set())
        self.assertEqual(reopened.connection.execute('SELECT count(*) FROM image_snapshots').fetchone()[0],0)
