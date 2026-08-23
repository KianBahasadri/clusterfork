"""Lean deterministic tests for tmux-wrapped launchers.

Covers: bash -n, sourcing + type checks, mock tmux on PATH for
session-name sanitization and arg/env forwarding. No real tmux
server or flaky pty timing required.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def run_bash(script: str, env=None, cwd=None, timeout=5):
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def has_shellcheck():
    return shutil.which("shellcheck") is not None


class ShellSyntaxTests(unittest.TestCase):
    def test_bash_n(self):
        for p in [REPO_ROOT / "bash_profile.sh"] + sorted(
            (REPO_ROOT / "shell").glob("*.sh")
        ):
            proc = run_bash(f"bash -n {p}")
            self.assertEqual(
                proc.returncode, 0, f"bash -n failed for {p}: {proc.stderr}"
            )

    def test_shellcheck_if_available(self):
        if not has_shellcheck():
            self.skipTest("shellcheck not installed")
        procs = []
        for p in sorted((REPO_ROOT / "shell").glob("*.sh")):
            proc = run_bash(f"shellcheck -s bash -e SC2148 -e SC2163 {p} 2>&1")
            procs.append((p, proc))
        fails = [(p, r.stdout + r.stderr) for p, r in procs if r.returncode != 0]
        self.assertEqual(fails, [], f"shellcheck failed: {fails}")


class SourcingTests(unittest.TestCase):
    def test_launchers_sourceable(self):
        script = (
            "source bash_profile.sh; "
            "type _cf_tmux >/dev/null; "
            'for fn in cl cc ca o oc occ cmd ag; do type "$fn" >/dev/null; done; '
            'echo ok'
        )
        proc = run_bash(script)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok", proc.stdout)

    def test_chrome_not_wrapped(self):
        proc = run_bash("source bash_profile.sh; type chrome")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertNotIn("_cf_tmux", proc.stdout)

    def test_rotate_helpers_exist(self):
        proc = run_bash(
            "source bash_profile.sh; "
            "for fn in rotate-claude rotate-codex rotate-cursor-cli rotate-opencode rotate-antigravity; "
            "do type \"$fn\" >/dev/null; done; echo ok"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok", proc.stdout)


class MockTmuxTests(unittest.TestCase):
    """Mock tmux on PATH to verify forwarding without a real tmux server."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.mock_bin = self.tmp_path / "mock-bin"
        self.fake_bin = self.tmp_path / "fake-bin"
        self.mock_bin.mkdir()
        self.fake_bin.mkdir()
        self.log = self.tmp_path / "mock-tmux.log"

        (self.mock_bin / "tmux").write_text(
            '#!/bin/bash\n'
            f'LOG="${{MOCK_TMUX_LOG:-{self.log}}}"\n'
            'printf "TMUX_CALL: %s\\n" "$*" >> "$LOG"\n'
            'exit 0\n'
        )
        (self.mock_bin / "tmux").chmod(0o755)

        for name in ("claude", "codex", "cursor-agent", "opencode", "agy", "cmd"):
            (self.fake_bin / name).write_text(
                f'#!/bin/bash\necho "FAKE:{name} $*"\n'
            )
            (self.fake_bin / name).chmod(0o755)

        self.base_env = {
            **os.environ,
            "PATH": f"{self.mock_bin}:{self.fake_bin}:{os.environ.get('PATH','')}",
            "MOCK_TMUX_LOG": str(self.log),
        }

    def _script_run(self, inner: str, env=None):
        """Run inner bash via script -q for a pty so [[ -t 0 ]] is true."""
        if shutil.which("script") is None:
            self.skipTest("script(1) not available")
        use_env = env or self.base_env
        use_env = {**use_env, "TERM": "xterm"}
        cmd = ["script", "-q", "-c", f"bash -c {inner!r}", "/dev/null"]
        if inner.startswith("'") and inner.endswith("'"):
            cmd = ["script", "-q", "-c", f"bash -c {inner}", "/dev/null"]
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=use_env,
            timeout=5,
        )
        return proc

    def _log(self):
        if self.log.exists():
            return self.log.read_text()
        return ""

    def test_session_name_sanitization(self):
        cases = {
            "my.project": "my-project",
            "foo:bar": "foo-bar",
            "a.b:c": "a-b-c",
            "-weird": "_-weird",
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                script = (
                    f'base="{raw}"; '
                    '[[ "$base" == "/" || -z "$base" ]] && base="root"; '
                    'name="${base//./-}"; name="${name//:/-}"; '
                    '[[ "$name" == -* ]] && name="_$name"; '
                    '[[ -z "$name" ]] && name="default"; '
                    'echo "$name"'
                )
                proc = run_bash(script)
                self.assertEqual(proc.stdout.strip(), want)

    def test_simple_o_forwards_via_mock(self):
        self.log.unlink(missing_ok=True)
        self._script_run("'source bash_profile.sh; PWD=/tmp/my.project o --help'")
        log = self._log()
        self.assertIn("TMUX_CALL:", log)
        self.assertIn("-s my-project", log)
        self.assertIn("-c /tmp/my.project", log)
        self.assertIn("opencode --help", log)

    def test_oc_continue_forwards(self):
        self.log.unlink(missing_ok=True)
        self._script_run("'source bash_profile.sh; PWD=/tmp/foo oc foo bar'")
        log = self._log()
        self.assertIn("opencode --continue foo bar", log)

    def test_cc_forwarding(self):
        self.log.unlink(missing_ok=True)
        self._script_run("'source bash_profile.sh; PWD=/tmp/proj cc'")
        log = self._log()
        self.assertIn("codex resume --yolo", log)

    def test_cl_env_forwarding(self):
        self.log.unlink(missing_ok=True)
        self._script_run("'source bash_profile.sh; PWD=/tmp/proj cl hello'")
        log = self._log()
        self.assertIn("-e ANTHROPIC_CUSTOM_MODEL_OPTION=claude-opus-4-8", log)
        self.assertIn("-- claude --dangerously-skip-permissions --effort xhigh hello", log)

    def test_occ_env_forwarding(self):
        self.log.unlink(missing_ok=True)
        env = {**self.base_env, "OPENCODE_API_KEY": "test-key-123"}
        self._script_run("'source bash_profile.sh; PWD=/tmp/proj occ --help'", env=env)
        log = self._log()
        self.assertIn("-e ANTHROPIC_BASE_URL=", log)
        self.assertIn("-e ANTHROPIC_API_KEY=test-key-123", log)
        self.assertIn("-- claude --dangerously-skip-permissions", log)

    def test_bypass_cf_no_tmux(self):
        self.log.unlink(missing_ok=True)
        env = {**self.base_env, "CF_NO_TMUX": "1"}
        proc = run_bash("source bash_profile.sh; o --help", env=env)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("FAKE:opencode", proc.stdout)
        self.assertEqual(self._log(), "")

    def test_bypass_tmux_env(self):
        self.log.unlink(missing_ok=True)
        env = {**self.base_env, "TMUX": "1"}
        proc = run_bash("source bash_profile.sh; o --help", env=env)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("FAKE:opencode", proc.stdout)
        self.assertEqual(self._log(), "")

    def test_bypass_non_tty(self):
        self.log.unlink(missing_ok=True)
        proc = run_bash("source bash_profile.sh; echo piped | o --help", env=self.base_env)
        self.assertEqual(self._log(), "")

    def test_bypass_cl_occ_without_pty(self):
        self.log.unlink(missing_ok=True)
        proc = run_bash(
            "source bash_profile.sh; cl --help 2>&1 | head -n 1", env=self.base_env
        )
        self.assertEqual(self._log(), "")
        self.log.unlink(missing_ok=True)
        env = {**self.base_env, "OPENCODE_API_KEY": "test-key-123"}
        proc = run_bash(
            "source bash_profile.sh; occ --help 2>&1 | head -n 1", env=env
        )
        self.assertEqual(self._log(), "")


if __name__ == "__main__":
    unittest.main()
