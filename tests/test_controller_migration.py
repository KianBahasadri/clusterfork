"""A historical task cannot be executed again or inherit old credentials on cutover."""

import hashlib
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from uuid import UUID

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'automation'),str(ROOT/'automation')]
from controller.core import Controller
from controller.migration import collect
from controller.model import Admit,Hold,Initialize,IssueObserved,LegacyImported,Rejected
from controller.store import Database
from test_controller import SPEC


class ControllerMigrationTests(unittest.TestCase):
    def setUp(self):
        self.project,self.issue=str(UUID(int=1)),str(UUID(int=2))
        self.registry=SimpleNamespace(generation='v1',routes={self.project:SimpleNamespace(project_id=self.project,repository='Owner/Repo')})
        self.row={'RowKey':hashlib.sha256(f'v1:{self.project}:{self.issue}'.encode()).hexdigest(),
                  'project_id':self.project,'issue_id':self.issue,'repository':'Owner/Repo','identifier':'KIAN-1',
                  'status':'completed','priority':1,'sort_order':-1,'target_branch':'clusterfork/kian-1-old'}
        self.snapshot={'ClusterforkRepositoryQueue':[self.row,{'RowKey':'slot','active_key':'','owner':''}]}
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path=Path(temporary.name)/'tasks.db'
        self.database=Database(self.path)
        self.addCleanup(self.database.close)
        self.controller=Controller(self.database)
        self.controller.apply(Initialize(),now=1000)

    def command(self):
        history=collect(self.snapshot,self.registry)
        return LegacyImported(history['generation'],tuple(history['records']),history['source_digest'])

    def test_completed_identity_and_branch_survive_import_reopen_and_issue_resubmission(self):
        history=self.command()
        self.controller.apply(history,now=1000)
        self.database.close()
        with Database(self.path) as database:
            controller=Controller(database)
            controller.apply(history,now=1001)
            observation=IssueObserved(self.issue,self.project,'Owner/Repo','v1','KIAN-1',True,True,content_revision=SPEC.content_revision)
            view=controller.apply(observation,now=1002)
            self.assertEqual(view.phase,'completed')
            with self.assertRaises(Rejected):
                controller.apply(Admit(observation.task_id,SPEC),now=1002)
            audit=database.connection.execute('SELECT data FROM legacy_history').fetchone()[0]
            self.assertIn('clusterfork/kian-1-old',audit)
            self.assertEqual(database.connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],0)

    def test_unresolved_work_or_owned_slot_cannot_be_imported_as_idle(self):
        self.row['status']='dispatching'
        with self.assertRaises(Rejected):
            self.command()
        self.row['status']='completed'
        self.snapshot['ClusterforkRepositoryQueue'][1]['active_key']=self.row['RowKey']
        with self.assertRaises(Rejected):
            self.command()
        self.assertEqual(self.controller.tasks(),())

    def test_credential_fields_are_excluded_and_generation_is_proven(self):
        self.row['auth']='private-fixture-credentials'
        history=self.command()
        self.assertNotIn('private-fixture',str(history))
        self.registry.generation='v2'
        with self.assertRaises(Rejected):
            self.command()
        prior=collect(self.snapshot,self.registry,legacy_generations=('v1',))
        self.assertEqual(prior['records'][0]['generation'],'v1')

    def test_an_imported_operator_halt_is_preserved_and_not_recreated_after_explicit_resume(self):
        history=collect(self.snapshot,self.registry,operator_holds=(('operator:legacy-halt','Paused by operator.'),))
        command=LegacyImported(history['generation'],tuple(history['records']),history['source_digest'],history['operator_holds'])
        self.controller.apply(command,now=1000)
        self.assertIn('operator:legacy-halt',self.controller.holds())
        self.controller.apply(Hold('operator:legacy-halt','',release=True),now=1001)
        self.controller.apply(command,now=1002)
        self.assertNotIn('operator:legacy-halt',self.controller.holds())


if __name__=='__main__':
    unittest.main()
