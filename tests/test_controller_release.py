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


if __name__=="__main__":
    unittest.main()
