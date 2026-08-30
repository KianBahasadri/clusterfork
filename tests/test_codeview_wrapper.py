"""Tests for bin/codeview control CLI: daemon lifecycle over real HTTP
(background mode)."""
import importlib.util
import json
from importlib.machinery import SourceFileLoader
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_wrapper():
    # Extensionless script: spec_from_file_location needs an explicit loader.
    loader = SourceFileLoader("codeview_wrapper", str(REPO_ROOT / "bin" / "codeview"))
    spec = importlib.util.spec_from_loader("codeview_wrapper", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class DaemonCLITests(unittest.TestCase):
    """Full lifecycle in bypass mode: start/status/reload/stop over real
    HTTP against a throwaway repo. CF_NO_TMUX → detached background spawn."""

    PORT = 46599

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        repo = Path(cls.tmp.name) / "myproject"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo,
                       capture_output=True, check=True)
        (repo / "app.py").write_text("print('hi')\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True,
                       capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "c1"], cwd=repo, capture_output=True,
            check=True)
        cls.repo = repo
        cls.env = {
            k: v for k, v in os.environ.items() if k != "TMUX"
        }
        cls.env["CF_NO_TMUX"] = "1"
        cls.env["CLUSTERFORK_DIR"] = str(REPO_ROOT)
        cls.wrapper = str(REPO_ROOT / "bin" / "codeview")

    @classmethod
    def tearDownClass(cls):
        # Safety net: make sure no daemon outlives the test.
        subprocess.run(
            [sys.executable, cls.wrapper, "stop"], cwd=cls.repo,
            env=cls.env, capture_output=True, timeout=30)

    def run_cli(self, *args, timeout=60):
        return subprocess.run(
            [sys.executable, self.wrapper, *args], cwd=self.repo,
            capture_output=True, text=True, env=self.env, timeout=timeout)

    def _daemon(self):
        path = self.repo / ".codeview" / "daemon.json"
        for _ in range(60):
            if path.exists():
                try:
                    return json.loads(path.read_text())
                except ValueError:
                    pass
            time.sleep(0.25)
        self.fail("daemon.json never appeared")

    def test_full_lifecycle(self):
        # start (detached, no browser)
        proc = self.run_cli("start", "--port", str(self.PORT), "--no-open")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("✓  started codeview", proc.stdout)
        self.assertIn("(background)", proc.stdout)

        info = self._daemon()
        self.assertEqual(info["port"], self.PORT)
        self.assertIsNone(info.get("session"))
        self.assertTrue(info.get("watch"))

        # status → running, exit 0
        proc = self.run_cli("status")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("codeview is running", proc.stdout)
        self.assertIn(f"http://127.0.0.1:{self.PORT}/", proc.stdout)
        self.assertIn("· clean", proc.stdout)
        self.assertIn("modules", proc.stdout)

        # start again → idempotent, no second daemon
        proc = self.run_cli("start", "--port", str(self.PORT), "--no-open")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("already running", proc.stdout)
        self.assertEqual(json.loads(
            (self.repo / ".codeview" / "daemon.json").read_text())["pid"],
            info["pid"])

        # reload → forced rescan, generation bumps
        gen0 = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{self.PORT}/api/gen", timeout=5).read()
        )["generation"]
        proc = self.run_cli("reload")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("rescan complete", proc.stdout)
        gen1 = json.loads(urllib.request.urlopen(
            f"http://127.0.0.1:{self.PORT}/api/gen", timeout=5).read()
        )["generation"]
        self.assertGreater(gen1, gen0)

        # reload with start flags is a parse error, not a rescan
        proc = self.run_cli("reload", "--port", "1234")
        self.assertNotEqual(proc.returncode, 0)

        # stop → port freed, bookkeeping removed
        proc = self.run_cli("stop")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("stopped", proc.stdout)
        self.assertFalse(
            (self.repo / ".codeview" / "daemon.json").exists())
        time.sleep(0.3)
        self.assertFalse(self._port_alive(self.PORT))

        # status after stop → exit 1, stop again → idempotent success
        proc = self.run_cli("status")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("not running", proc.stdout)
        proc = self.run_cli("stop")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("not running", proc.stdout)

    @staticmethod
    def _port_alive(port):
        import socket
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return True
        except OSError:
            return False

    def test_reload_when_stopped_fails(self):
        proc = self.run_cli("reload")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not running", proc.stderr)


class DefaultCommandTests(unittest.TestCase):
    def test_no_args_defaults_to_start(self):
        w = load_wrapper()
        _, args = w.resolve_args([])
        self.assertEqual(args.command, "start")
        self.assertFalse(args.no_open)
        self.assertFalse(args.no_watch)
        self.assertFalse(args.reindex)
        self.assertEqual(args.max_commits, 1000)
        self.assertIsNone(args.port)

    def test_explicit_status_is_still_status(self):
        w = load_wrapper()
        _, args = w.resolve_args(["status"])
        self.assertEqual(args.command, "status")


if __name__ == "__main__":
    unittest.main()
