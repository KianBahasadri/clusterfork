"""Opt-in native Codex vision smoke test; no Linear/GitHub calls or publication.

CLUSTERFORK_LIVE_IMAGES=1 python3 -m unittest discover -s tests -p test_issue_images_live.py -v
Uses a private temporary copy of the current Codex auth cache; requires the pinned CLI/model.
"""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation"), str(ROOT / "automation/azure")]
import worker
from agent_session import run_agent
from dispatch_core import Registry
from issue_images import capture_images, image_digest, image_prompt, materialize_images
from test_issue_images import DESCRIPTION, screenshots


@unittest.skipUnless(os.environ.get("CLUSTERFORK_LIVE_IMAGES") == "1", "opt-in native Codex vision smoke test")
class NativeVisionTests(unittest.TestCase):
    def test_readiness_and_coding_interpret_details_available_only_in_screenshots(self):
        source = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        self.assertTrue((source / "auth.json").is_file(), "A current Codex auth cache is required")
        self.assertIn("0.165.0", subprocess.check_output(["codex", "--version"], text=True))
        profile = Registry.load(ROOT / "automation/azure/projects.json").agent_profiles["codex"]
        images = capture_images(DESCRIPTION, screenshots().download_image)
        issue = {"id": "fixture", "identifier": "VISUAL-1", "title": "Match the target screenshot",
                 "description": DESCRIPTION + "\nDescribe the visual differences in your summary.",
                 "url": "https://linear.app/issue/fixture", "comments": []}
        self.assertNotIn("Launch preview", json.dumps(issue) + image_prompt(images))
        config = {"model": profile.model, "effort": profile.effort, "ref": "main", "images": images,
                  "issue": issue, "binding": {"context_sha": "b" * 40, "images_sha": image_digest(images)},
                  "review_timeout_seconds": 600}
        signal = SimpleNamespace(failed=threading.Event())
        selection = SimpleNamespace(account_id="fixture", model=profile.model, effort=profile.effort)
        results = {}

        class Broker:
            def call(self, action, **data):
                if action == "checkout":
                    return {"token": "fixture-read-token"}
                if action == "review":
                    results.update(data)
                    return {"status": "passed"}
                if action == "steering_open":
                    return {"accepting": True}
                return {}

        with tempfile.TemporaryDirectory(prefix="clusterfork-vision-") as directory:
            root = Path(directory)
            home, workspace, scratch = (root / name for name in ("home", "workspace", "scratch"))
            for path in (home, workspace, scratch):
                path.mkdir(mode=0o700)
            shutil.copyfile(source / "auth.json", home / "auth.json")
            (home / "auth.json").chmod(0o600)
            (workspace / "docs").mkdir()
            (workspace / "AGENTS.md").write_text("Read docs/README.md. This is a local static page.\n")
            (workspace / "docs/README.md").write_text(
                "The button is in index.html. Match screenshot text and colors. Keep the heading. "
                "No network, credentials, spending, external actions, or publication are needed.\n")
            (workspace / "index.html").write_text(
                '<h1>Release dashboard</h1><button style="background:#3347b0">Open preview</button>\n')
            subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)

            @contextmanager
            def lease(*args):
                yield home, signal

            native_command = worker.command

            def command(args, **kwargs):
                if args[0] == "git":
                    return "b" * 40 if args[:2] == ["git", "rev-parse"] else ""
                return native_command(args + ["--cd", str(workspace)], **kwargs)

            with patch.object(worker, "choose_account", return_value=([selection], {}, None)), \
                 patch.object(worker, "select_account", return_value=selection), \
                 patch.object(worker, "account_lease", lease), \
                 patch.object(worker, "inspect_account", return_value=({}, {})), \
                 patch.object(worker, "quota_snapshot"), patch.object(worker, "command", command):
                self.assertEqual(worker.review_readiness(Broker(), config, signal, scratch), "passed")
            self.assertEqual(results["result"]["verdict"], "ready")
            self.assertIn("Launch preview", json.dumps(results["result"]))
            self.assertIn("Open preview", (workspace / "index.html").read_text())
            print("Readiness: identified target button text from the screenshots in read-only review.", flush=True)
            shutil.rmtree(scratch)

            # A separate scratch directory mirrors the separate coding runner.
            coding = root / "coding"
            coding.mkdir(mode=0o700)
            paths = materialize_images(config, coding)
            summary = run_agent(Broker(), config, home, workspace,
                                "Implement this local static page issue:\n" + json.dumps(issue) + image_prompt(images),
                                worker.clean_environment(), timeout=600, image_paths=paths)
            self.assertIn("Launch preview", (workspace / "index.html").read_text())
            self.assertIn("Release dashboard", (workspace / "index.html").read_text())
            self.assertTrue(summary)
            print("Coding: implemented target button text visible only in the screenshot.", flush=True)


if __name__ == "__main__":
    unittest.main()
