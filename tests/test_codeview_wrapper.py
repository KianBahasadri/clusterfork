"""Tests for bin/codeview control CLI: tmux decision, session naming,
daemon lifecycle over real HTTP (background mode), and the tmux-wrapped
start path via mock tmux on PATH (house mock pattern)."""
import importlib.util
import json
from importlib.machinery import SourceFileLoader
import os
import shutil
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


class TmuxRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = load_wrapper()

    def test_sanitize_matches_house_rules(self):
        cases = {
            "my.project": "my-project",
            "foo:bar": "foo-bar",
            "-weird": "_-weird",
            "plain": "plain",
        }
        for raw, want in cases.items():
            self.assertEqual(self.w.sanitize_session_name(raw), want)

    def test_bypass_env_flags(self):
        self.assertFalse(self.w.should_wrap_in_tmux({"CF_NO_TMUX": "1"}))
        self.assertFalse(self.w.should_wrap_in_tmux({"TMUX": "/tmp/x"}))

    def test_wrap_when_tty_and_unset(self):
        # can't force a tty in CI; assert the helper consults stdin only.
        self.assertIn("isatty", _src("should_wrap_in_tmux"))


def _src(fn_name):
    import inspect
    w = load_wrapper()
    return inspect.getsource(getattr(w, fn_name))


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


class MockTmuxTests(unittest.TestCase):
    """Run the wrapper with mock tmux under a pty so `start` takes the
    tmux-detach path; the mock actually launches the real server so the
    wrapper's wait-for-port loop succeeds."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.mock_bin = self.base / "mock-bin"
        self.mock_bin.mkdir()
        self.log = self.base / "mock-tmux.log"
        self.marker = self.base / "mock-tmux.pid"
        (self.mock_bin / "tmux").write_text(
            '#!/bin/bash\n'
            f'LOG="{self.log}"\n'
            f'MARKER="{self.marker}"\n'
            'printf "TMUX_CALL: %s\\n" "$*" >> "$LOG"\n'
            'if [[ "$1" == "has-session" ]]; then\n'
            '  if [[ -f "$MARKER" ]] && kill -0 "$(cat "$MARKER")" '
            '2>/dev/null; then exit 0; fi\n'
            '  exit 1\n'
            'fi\n'
            'if [[ "$1" == "new-session" ]]; then\n'
            '  after=(); seen=0\n'
            '  for a in "$@"; do\n'
            '    [[ $seen == 1 ]] && after+=("$a")\n'
            '    [[ "$a" == "--" ]] && seen=1\n'
            '  done\n'
            '  nohup "${after[@]}" >/dev/null 2>&1 &\n'
            '  echo $! > "$MARKER"\n'
            '  exit 0\n'
            'fi\n'
            'exit 0\n')
        (self.mock_bin / "tmux").chmod(0o755)
        self.addCleanup(self._kill_mock_server)

        # Repo git-init'd in tmp; wrapper must resolve it as root.
        self.repo = self.base / "myproject"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo,
                       capture_output=True, check=True)

    def _kill_mock_server(self):
        pid = None
        daemon = self.repo / ".codeview" / "daemon.json"
        if daemon.exists():
            try:
                pid = int(json.loads(daemon.read_text())["pid"])
            except (ValueError, KeyError, OSError):
                pass
        if pid is None and self.marker.exists():
            try:
                pid = int(self.marker.read_text().strip())
            except (ValueError, OSError):
                pass
        if pid is not None:
            try:
                os.kill(pid, 15)
            except OSError:
                pass

    def _env(self):
        env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        env["PATH"] = f"{self.mock_bin}:{os.environ.get('PATH', '')}"
        env.pop("CF_NO_TMUX", None)
        env["TERM"] = "xterm"
        env["CLUSTERFORK_DIR"] = str(REPO_ROOT)
        return env

    def test_start_wraps_in_detached_tmux_session(self):
        # Full-integration-ish: real wrapper, real server, mock tmux, under
        # a pty (house `script -q -c` trick) so the wrapper takes the tmux
        # path. The mock launches the real server, so the wrapper's wait
        # loop should observe the port and report success.
        if shutil.which("script") is None:
            self.skipTest("script(1) not available")
        cmd = ["script", "-q", "-c",
               f"{sys.executable} {REPO_ROOT / 'bin' / 'codeview'} "
               f"start --no-open",
               "/dev/null"]
        proc = subprocess.run(cmd, cwd=self.repo, capture_output=True,
                              text=True, env=self._env(), timeout=60)
        log = self.log.read_text() if self.log.exists() else ""
        self.assertIn("TMUX_CALL", log)
        self.assertIn("new-session -d -s myproject", log)
        self.assertIn("server.py", log)
        self.assertIn("--repo", log)
        self.assertIn("✓  started codeview", proc.stdout)
        self.assertIn("tmux session: myproject", proc.stdout)
        # Server published its bookkeeping, including the session name.
        info = json.loads(
            (self.repo / ".codeview" / "daemon.json").read_text())
        self.assertEqual(info["session"], "myproject")
        self.assertIsInstance(info["port"], int)
        self.assertTrue(46000 <= info["port"] < 50000)


if __name__ == "__main__":
    unittest.main()
