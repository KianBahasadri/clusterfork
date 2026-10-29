"""Deployment pauses, tested revisions, draining, and recovery boundaries."""

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
import deploy
import pilot
from deployment_control import DeploymentControl
from worker_core import WorkerError
from test_remote_worker import MemoryTable

REVISION = "a" * 40
OWNER = "github:123:1"


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.control = DeploymentControl(MemoryTable())

    def test_pause_is_durable_and_only_its_current_owner_can_resume(self):
        self.assertFalse(self.control.is_paused())
        self.control.pause(OWNER, REVISION)
        self.assertTrue(DeploymentControl(self.control.table).is_paused())
        self.control.pause("github:124:1", "b" * 40)
        with self.assertRaises(WorkerError):
            self.control.resume(OWNER, REVISION)
        self.assertTrue(self.control.is_paused())
        self.control.resume("github:124:1", "b" * 40)
        self.assertFalse(self.control.is_paused())

    def test_package_stamps_only_source_digest_and_public_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deployment.zip"
            pilot.package(path, revision=REVISION)
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(set(archive.namelist()), set(pilot.PACKAGE_FILES) | {"deployment.json"})
                self.assertEqual(json.loads(archive.read("deployment.json")),
                                 {"revision": REVISION, "digest": pilot.source_digest()})
                self.assertEqual((archive.getinfo("deployment.json").external_attr >> 16) & 0o777, 0o644)

    def test_deployment_waits_for_both_main_ci_and_existing_workers(self):
        with patch.object(deploy, "control", return_value=self.control), \
             patch.object(deploy, "github", return_value={"sha": REVISION}), \
             patch.object(deploy, "ci_state", side_effect=["pending", "success", "success"]), \
             patch.object(deploy, "active_workers", side_effect=[[1], [1], []]), \
             patch.object(deploy.time, "sleep"), \
             patch.object(deploy.pilot, "package") as package, \
             patch.object(deploy, "output") as output:
            deploy.prepare(REVISION, OWNER)
        self.assertTrue(self.control.is_paused())
        package.assert_called_once_with(Path("/tmp/clusterfork-function.zip"), revision=REVISION)
        output.assert_called_once_with("proceed", True)

    def test_failed_ci_never_publishes_and_keeps_dispatch_paused(self):
        with patch.object(deploy, "control", return_value=self.control), \
             patch.object(deploy, "github", return_value={"sha": REVISION}), \
             patch.object(deploy, "ci_state", return_value="failure"), \
             patch.object(deploy.pilot, "package") as package:
            with self.assertRaisesRegex(RuntimeError, "CI"):
                deploy.prepare(REVISION, OWNER)
        package.assert_not_called()
        self.assertTrue(self.control.is_paused())

    def test_superseded_revision_holds_pause_for_next_deployment(self):
        with patch.object(deploy, "control", return_value=self.control), \
             patch.object(deploy, "github", side_effect=[{"sha": REVISION}, {"sha": "b" * 40}]), \
             patch.object(deploy, "output") as output:
            deploy.prepare(REVISION, OWNER)
        self.assertTrue(self.control.is_paused())
        output.assert_called_once_with("proceed", False)

    def test_only_exact_healthy_package_resumes_dispatch(self):
        self.control.pause(OWNER, REVISION)
        with patch.object(deploy, "control", return_value=self.control), \
             patch.object(deploy, "github", return_value={"sha": REVISION}), \
             patch.object(deploy, "health", return_value={
                 "deployment": {"revision": REVISION, "digest": pilot.source_digest()},
                 "serial_admission": True, "missing": []}):
            deploy.verify(REVISION, OWNER)
        self.assertFalse(self.control.is_paused())

    def test_stale_or_unhealthy_cloud_does_not_resume_dispatch(self):
        self.control.pause(OWNER, REVISION)
        with patch.object(deploy, "control", return_value=self.control), \
             patch.object(deploy, "health", return_value={"deployment": {"revision": "b" * 40}}), \
             patch.object(deploy.time, "monotonic", side_effect=[0, 1, 241]), \
             patch.object(deploy.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "expected healthy revision"):
                deploy.verify(REVISION, OWNER)
        self.assertTrue(self.control.is_paused())

    def test_drain_finds_active_runs_after_the_first_page(self):
        first = [{"id": n, "status": "completed"} for n in range(100)]
        with patch.object(deploy, "github", side_effect=[{"workflow_runs": first},
                      {"workflow_runs": [{"id": 101, "status": "queued"}]}]):
            self.assertEqual(deploy.active_workers(), [101])

    def test_unhealthy_response_exposes_only_safe_error_class(self):
        error = HTTPError(deploy.HEALTH, 503, "unavailable", {},
                          io.BytesIO(json.dumps({"runtime_error": "PermissionError",
                                                 "private": "do not log this"}).encode()))
        with patch.object(deploy, "urlopen", side_effect=error):
            self.assertEqual(deploy.health(), {"http_status": 503, "runtime_error": "PermissionError"})

    def test_ci_requires_the_same_main_push_revision(self):
        with patch.object(deploy, "github", return_value={"workflow_runs": [
            {"id": 1, "run_attempt": 1, "head_sha": REVISION, "head_branch": "main",
             "event": "push", "status": "completed", "conclusion": "failure"},
            {"id": 2, "run_attempt": 1, "head_sha": REVISION, "head_branch": "other",
             "event": "pull_request", "status": "completed", "conclusion": "success"}]}):
            self.assertEqual(deploy.ci_state(REVISION), "failure")
