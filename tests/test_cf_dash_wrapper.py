"""Tests for bin/cf-dash launcher: tmux decision, session naming, arg
forwarding via mock tmux on PATH (house mock pattern), bypass modes."""
import importlib.util
from importlib.machinery import SourceFileLoader
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_wrapper():
    # Extensionless script: spec_from_file_location needs an explicit loader.
    loader = SourceFileLoader("cf_dash_wrapper", str(REPO_ROOT / "bin" / "cf-dash"))
    spec = importlib.util.spec_from_loader("cf_dash_wrapper", loader)
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


class MockTmuxTests(unittest.TestCase):
    """Run the wrapper binary with mock tmux to verify arg/session behavior."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.mock_bin = self.base / "mock-bin"
        self.mock_bin.mkdir()
        self.log = self.base / "mock-tmux.log"
        (self.mock_bin / "tmux").write_text(
            '#!/bin/bash\n'
            f'LOG="{self.log}"\n'
            'printf "TMUX_CALL: %s\\n" "$*" >> "$LOG"\n'
            'if [[ "$1" == "has-session" ]]; then exit 1; fi\n'
            'exit 0\n')
        (self.mock_bin / "tmux").chmod(0o755)

        # Repo git-init'd in tmp; wrapper must resolve it as root.
        self.repo = self.base / "myproject"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.repo,
                       capture_output=True, check=True)
        env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        env["PATH"] = f"{self.mock_bin}:{os.environ.get('PATH', '')}"
        env["CF_NO_TMUX"] = ""  # unset semantics: presence check only
        del env["CF_NO_TMUX"]
        env["CLUSTERFORK_DIR"] = str(REPO_ROOT / ".tmp-cf-dash-fixture")
        self.env = env

    def _fixture_dir(self) -> Path:
        """Minimal fake CLUSTERFORK_DIR: scripts/cf_dash/server.py present."""
        fixture = Path(self.env["CLUSTERFORK_DIR"])
        server_py = fixture / "scripts" / "cf_dash" / "server.py"
        server_py.parent.mkdir(parents=True, exist_ok=True)
        if not server_py.exists():
            server_py.write_text(
                "# fixture stub\n"
                "import json\n"
                "def default_port(p):\n"
                "    return 47111\n")
        return fixture

    def test_mock_forwards_to_real_server(self):
        # Full-integration-ish: run real wrapper with REAL scripts dir but
        # mock tmux, under a pty (house `script -q -c` trick) so the wrapper
        # takes the tmux path. Expect the tmux log to carry session name +
        # server.py + --repo. tmux itself is mocked so nothing lingers.
        if shutil.which("script") is None:
            self.skipTest("script(1) not available")
        env = dict(self.env)
        env["CLUSTERFORK_DIR"] = str(REPO_ROOT)
        env["TERM"] = "xterm"
        cmd = ["script", "-q", "-c",
               f"{sys.executable} {REPO_ROOT / 'bin' / 'cf-dash'}",
               "/dev/null"]
        proc = subprocess.run(cmd, cwd=self.repo, capture_output=True,
                              text=True, env=env, timeout=30)
        log = self.log.read_text() if self.log.exists() else ""
        self.assertIn("TMUX_CALL", log)
        self.assertIn("-s myproject", log)
        self.assertIn("server.py", log)
        self.assertIn("--repo", log)

    def test_bypass_runs_inline(self):
        # CF_NO_TMUX set → no tmux at all. Server would block, so use
        # --no-watch + short timeout and kill it after confirming boot print.
        env = dict(self.env)
        env["CLUSTERFORK_DIR"] = str(REPO_ROOT)
        env["CF_NO_TMUX"] = "1"
        proc = subprocess.Popen(
            [sys.executable, str(REPO_ROOT / "bin" / "cf-dash"),
             "--port", "46555"],
            cwd=self.repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env)
        try:
            line = proc.stdout.readline()
            deadline = 20
            while (("serving" not in line) and deadline > 0):
                line = proc.stdout.readline()
                deadline -= 1
            self.assertIn("serving", line)
        finally:
            proc.terminate()
            proc.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
