"""Actual Git trees distinguish runtime inputs from fresh target-only changes."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"automation"))
from controller.release import INPUT_ROOTS,RequiredRuntime,local_manifest,package


class ControllerReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.git("init","-q")
        self.git("config","user.email","fixture@example.test")
        self.git("config","user.name","Fixture")
        for name in INPUT_ROOTS:
            (self.root/name).mkdir()
            (self.root/name/"fixture.txt").write_text(name)
        (self.root/"README.md").write_text("Target documentation")
        self.commit()

    def git(self,*args):
        return subprocess.run(["git",*args],cwd=self.root,capture_output=True,text=True,check=True).stdout.strip()

    def commit(self):
        self.git("add",".")
        self.git("commit","-qm","Fixture")
        return self.git("rev-parse","HEAD")

    def test_target_only_merge_changes_checkout_commit_without_requiring_runtime_deploy(self):
        first=local_manifest(self.root,self.git("rev-parse","HEAD"))
        (self.root/"README.md").write_text("Updated target documentation")
        second=local_manifest(self.root,self.commit())
        self.assertNotEqual(first["revision"],second["revision"])
        self.assertEqual(first["digest"],second["digest"])
        (self.root/"automation/new_module.py").write_text("NEW_CAPABILITY=True\n")
        third=local_manifest(self.root,self.commit())
        self.assertNotEqual(second["digest"],third["digest"])

    def test_package_reads_exact_commit_and_excludes_worktree_secrets(self):
        revision=self.git("rev-parse","HEAD")
        (self.root/"automation/.env").write_text("PRIVATE=fixture-private-value")
        (self.root/"automation/fixture.txt").write_text("Uncommitted replacement")
        destination=self.root/"runtime.zip"
        manifest=package(self.root,revision,destination)
        with zipfile.ZipFile(destination) as archive:
            self.assertNotIn("automation/.env",archive.namelist())
            self.assertEqual(archive.read("automation/fixture.txt"),b"automation")
            self.assertEqual(json.loads(archive.read("release.json")),manifest)

    def test_required_runtime_uses_complete_tree_evidence(self):
        revision=self.git("rev-parse","HEAD")
        manifest=local_manifest(self.root,revision)
        entries=[]
        for name in INPUT_ROOTS:
            sha=self.git("rev-parse",f"{revision}:{name}")
            entries.append({"path":name,"type":"tree","sha":sha})
        github=Mock()
        github.request.side_effect=[{"commit":{"tree":{"sha":"a"*40}}},{"tree":entries,"truncated":False}]
        self.assertTrue(RequiredRuntime(github,manifest)())
        github.request.side_effect=[{"commit":{"tree":{"sha":"a"*40}}},{"tree":entries,"truncated":True}]
        with self.assertRaisesRegex(ValueError,"Incomplete"):
            RequiredRuntime(github,manifest)()

    def deployment_fixture(self):
        revision=self.git('rev-parse','HEAD')
        manifest=local_manifest(self.root,revision)
        entries=[{'path':root,'type':'tree','sha':self.git('rev-parse',f'{revision}:{root}')} for root in INPUT_ROOTS]
        source=[{'commit':{'tree':{'sha':'a'*40}}},{'tree':entries,'truncated':False}]
        run={'id':123,'head_sha':revision,'head_branch':'main','path':'.github/workflows/deploy-azure.yml',
             'event':'push','status':'completed','conclusion':'success'}
        return manifest,source,run

    def test_private_source_verification_does_not_deadlock_the_full_deployment_gate(self):
        manifest,source,run=self.deployment_fixture()
        github=Mock()
        required=RequiredRuntime(github,manifest,deployment_run_id='123')
        github.request.side_effect=source
        self.assertTrue(required.source_matches())
        github.request.side_effect=[*source,{**run,'status':'in_progress','conclusion':None}]
        self.assertFalse(required())
        github.request.side_effect=[*source,{**run,'conclusion':'failure'}]
        self.assertFalse(required())
        github.request.side_effect=[*source,run]
        self.assertTrue(required())
        # The exact successful installed version remains verified; target trees
        # are still checked on each admission, including after target-only merges.
        github.request.side_effect=source
        self.assertTrue(required())

    def test_a_successful_other_job_branch_or_revision_cannot_authorize_runtime_admission(self):
        manifest,source,run=self.deployment_fixture()
        for fields in ({'id':124},{'head_sha':'b'*40},{'head_branch':'feature'},
                       {'path':'.github/workflows/test.yml'},{'event':'pull_request'}):
            with self.subTest(fields=fields):
                github=Mock()
                github.request.side_effect=[*source,{**run,**fields}]
                self.assertFalse(RequiredRuntime(github,manifest,deployment_run_id='123')())


if __name__=="__main__":
    unittest.main()
