"""Exercise reusable workflow checkout paths through worker exit and cleanup."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKER = """import json, os, subprocess, sys
from pathlib import Path
root = Path.cwd()
assert (root / 'caller.txt').read_text() == 'caller'
assert Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip()) == root
print(json.dumps({'cwd': str(root), 'args': sys.argv[1:]}))
raise SystemExit(int(os.environ['FIXTURE_WORKER_EXIT']))
"""


def git(root, *args):
    return subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", *args],
                          cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def scalar(block, name, default=""):
    match = re.search(rf"(?m)^\s*{re.escape(name)}: (.+)$", block)
    return match[1] if match else default


class SharedWorkerLayoutTests(unittest.TestCase):
    def test_actions_survive_target_checkout_worker_failure_and_git_cleanup(self):
        for filename in ("clusterfork-select-account.yml", "clusterfork-develop.yml"):
            blocks = re.split(r"(?m)^      - ", (ROOT / ".github/workflows" / filename).read_text())[1:]
            for exit_code in (0, 7):
                with self.subTest(workflow=filename, worker_exit=exit_code), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    workspace, runtime = root / "workspace", root / "runner-temp"
                    workspace.mkdir()
                    runtime.mkdir()
                    engine, caller = root / "engine-source", root / "caller-source"
                    for source in (engine, caller):
                        source.mkdir()
                        git(source, "init", "--quiet")
                    shutil.copytree(ROOT / ".github/actions", engine / ".github/actions")
                    (engine / "automation").mkdir()
                    (engine / "automation/worker.py").write_text(WORKER)
                    (caller / "caller.txt").write_text("caller")
                    # A caller may have its own action definitions and worker;
                    # neither may replace the pinned engine or its cleanup.
                    shutil.copytree(ROOT / ".github/actions", caller / ".github/actions")
                    (caller / "automation").mkdir()
                    (caller / "automation/worker.py").write_text("raise SystemExit(99)\n")
                    for source in (engine, caller):
                        git(source, "add", ".")
                        git(source, "commit", "--quiet", "-m", "fixture")
                    env = {**os.environ, "GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(runtime),
                           "FIXTURE_WORKER_EXIT": str(exit_code)}
                    actions, target, launched = {}, None, False
                    for block in blocks:
                        uses = scalar(block, "uses")
                        if uses.startswith("actions/checkout@"):
                            destination = workspace / scalar(block, "path", ".")
                            source = engine if scalar(block, "repository") else caller
                            # A fresh checkout removes pre-existing contents at
                            # its destination, as actions/checkout does.
                            if destination.exists():
                                shutil.rmtree(destination)
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            git(root, "clone", "--quiet", str(source), str(destination))
                            if source == caller:
                                target = destination.resolve()
                        elif uses.startswith("./"):
                            manifest = workspace / uses / "action.yml"
                            actions[manifest] = manifest.read_bytes()
                        elif scalar(block, "run") and not scalar(block, "name").startswith("Bind the engine"):
                            command = scalar(block, "run")
                            self.assertNotEqual(command, "|", "Handle multiline workflow scripts explicitly")
                            result = subprocess.run(["bash", "-e", "-c", command],
                                                    cwd=workspace / scalar(block, "working-directory", "."),
                                                    env=env, capture_output=True, text=True)
                            if "automation/worker.py" in command:
                                launched = True
                                self.assertEqual(result.returncode, exit_code, result.stderr)
                                observed = json.loads(result.stdout)
                                self.assertEqual(Path(observed["cwd"]), target)
                                self.assertEqual(observed["args"], ["--select-account"]
                                                 if "select-account" in filename else [])
                            else:
                                self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(launched)
                    self.assertTrue(actions)
                    self.assertEqual(git(target, "status", "--porcelain"), "")
                    git(target, "clean", "-ffdx")
                    # GitHub reloads the local composite manifest for its post
                    # steps, including when an earlier step failed.
                    for manifest, original in actions.items():
                        self.assertEqual(manifest.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
