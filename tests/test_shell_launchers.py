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
            'for fn in cl cc ca oc occ cmd ag gk; do type "$fn" >/dev/null; done; '
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
            "for fn in rotate-claude rotate-codex rotate-cursor-cli rotate-grok rotate-opencode rotate-antigravity; "
            "do type \"$fn\" >/dev/null; done; echo ok"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok", proc.stdout)

    def test_no_launcher_uses_command_builtin(self):
        for p in sorted((REPO_ROOT / "shell").glob("*.sh")):
            text = p.read_text()
            self.assertNotIn(
                "command cmd",
                text,
                f"{p.name} must not use shell builtin 'command cmd' — tmux execvp cannot run builtins (regression for cmd)",
            )
            self.assertNotIn(
                "_cf_tmux command",
                text,
                f"{p.name} must not pass shell builtin 'command' to _cf_tmux/tmux",
            )

    def test_cmd_uses_resolved_binary(self):
        text = (REPO_ROOT / "shell" / "cmd.sh").read_text()
        self.assertIn("type -P cmd", text)
        self.assertNotIn("_cf_tmux command", text)


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
            'if [[ "$1" == "has-session" ]]; then\n'
            '  target=""\n'
            '  args=("$@")\n'
            '  for i in "${!args[@]}"; do\n'
            '    if [[ "${args[i]}" == "-t" ]]; then target="${args[i+1]:-}"; break; fi\n'
            '  done\n'
            '  if [[ -n "${MOCK_TMUX_EXISTING_SESSIONS:-}" ]]; then\n'
            '    for s in $MOCK_TMUX_EXISTING_SESSIONS; do\n'
            '      if [[ "$s" == "$target" ]]; then exit 0; fi\n'
            '    done\n'
            '  fi\n'
            '  exit 1\n'
            'fi\n'
            'if [[ -n "${MOCK_TMUX_FAIL:-}" ]]; then\n'
            '  printf "MOCK_TMUX_ERROR: forced failure\\n" >> "$LOG"\n'
            '  exit "${MOCK_TMUX_EXIT_CODE:-1}"\n'
            'fi\n'
            '# Validate the binary tmux would exec is real (catches shell builtins like `command`).\n'
            'args=("$@")\n'
            'bin=""\n'
            'for i in "${!args[@]}"; do\n'
            '  if [[ "${args[i]}" == "--" ]]; then bin="${args[i+1]:-}"; break; fi\n'
            'done\n'
            'if [[ -z "$bin" ]]; then\n'
            '  for i in "${!args[@]}"; do\n'
            '    if [[ "${args[i]}" == "-c" ]]; then bin="${args[i+2]:-}"; break; fi\n'
            '  done\n'
            'fi\n'
            'if [[ "$bin" == "command" ]]; then\n'
            '  printf "MOCK_TMUX_ERROR: shell builtin \\`command\\` cannot be execed via tmux\\n" >> "$LOG"\n'
            '  exit 127\n'
            'fi\n'
            'if [[ -n "$bin" ]]; then\n'
            '  if [[ "$bin" == */* ]]; then\n'
            '    if [[ ! -x "$bin" ]]; then printf "MOCK_TMUX_ERROR: binary \\`%s\\` not executable\\n" "$bin" >> "$LOG"; exit 127; fi\n'
            '  else\n'
            '    if ! type -P "$bin" >/dev/null 2>&1; then printf "MOCK_TMUX_ERROR: binary \\`%s\\` not found on PATH\\n" "$bin" >> "$LOG"; exit 127; fi\n'
            '  fi\n'
            'fi\n'
            'exit 0\n'
        )
        (self.mock_bin / "tmux").chmod(0o755)

        for name in ("claude", "codex", "cursor-agent", "opencode", "agy", "cmd", "grok"):
            (self.fake_bin / name).write_text(
                f'#!/bin/bash\necho "FAKE:{name} $*"\n'
            )
            (self.fake_bin / name).chmod(0o755)

        env_no_tmux = {k: v for k, v in os.environ.items() if k != "TMUX"}
        self.base_env = {
            **env_no_tmux,
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

    def test_oc_forwards_via_mock(self):
        self.log.unlink(missing_ok=True)
        self._script_run("'source bash_profile.sh; PWD=/tmp/my.project oc --help'")
        log = self._log()
        self.assertIn("TMUX_CALL:", log)
        self.assertIn("-s my-project", log)
        self.assertIn("-c /tmp/my.project", log)
        self.assertIn("opencode --help", log)
        self.assertNotIn("--continue", log)
        self.assertNotIn("MOCK_TMUX_ERROR", log)

    def test_cc_forwarding(self):
        self.log.unlink(missing_ok=True)
        self._script_run("'source bash_profile.sh; PWD=/tmp/proj cc'")
        log = self._log()
        self.assertIn("codex resume --yolo", log)
        self.assertNotIn("model=", log)
        self.assertNotIn("model_reasoning_effort=", log)
        self.assertNotIn("MOCK_TMUX_ERROR", log)

    def test_cc_forwards_user_args(self):
        self.log.unlink(missing_ok=True)
        self._script_run(
            "'source bash_profile.sh; PWD=/tmp/proj "
            "cc --model gpt-5.6-terra --config model_reasoning_effort=max'"
        )
        log = self._log()
        self.assertIn(
            "codex resume --yolo --model gpt-5.6-terra --config model_reasoning_effort=max",
            log,
        )
        self.assertNotIn("MOCK_TMUX_ERROR", log)

    def test_cl_env_forwarding(self):
        self.log.unlink(missing_ok=True)
        self._script_run("'source bash_profile.sh; PWD=/tmp/proj cl hello'")
        log = self._log()
        self.assertIn("-e ANTHROPIC_CUSTOM_MODEL_OPTION=claude-opus-4-8", log)
        self.assertIn("-- claude --dangerously-skip-permissions --effort max --continue hello", log)
        self.assertNotIn("MOCK_TMUX_ERROR", log)

    def test_cl_bare_resumes_via_mock(self):
        self.log.unlink(missing_ok=True)
        self._script_run("'source bash_profile.sh; PWD=/tmp/proj cl'")
        log = self._log()
        self.assertIn("TMUX_CALL:", log)
        self.assertIn("-- claude --dangerously-skip-permissions --effort max --continue", log)
        self.assertNotIn("MOCK_TMUX_ERROR", log)

    def test_cl_flags_forward_unchanged(self):
        self.log.unlink(missing_ok=True)
        self._script_run("'source bash_profile.sh; PWD=/tmp/proj cl --model claude-opus-4-8'")
        log = self._log()
        self.assertIn("-- claude --dangerously-skip-permissions --effort max --model claude-opus-4-8", log)
        self.assertNotIn("--continue", log)
        self.assertNotIn("MOCK_TMUX_ERROR", log)

    def test_occ_env_forwarding(self):
        self.log.unlink(missing_ok=True)
        env = {**self.base_env, "OPENCODE_API_KEY": "test-key-123"}
        self._script_run("'source bash_profile.sh; PWD=/tmp/proj occ --help'", env=env)
        log = self._log()
        self.assertIn("-e ANTHROPIC_BASE_URL=", log)
        self.assertIn("-e ANTHROPIC_API_KEY=test-key-123", log)
        self.assertIn("-- claude --dangerously-skip-permissions", log)
        self.assertNotIn("MOCK_TMUX_ERROR", log)

    def test_gk_forwards_via_mock(self):
        self.log.unlink(missing_ok=True)
        self._script_run("'source bash_profile.sh; PWD=/tmp/my.project gk --help'")
        log = self._log()
        self.assertIn("TMUX_CALL:", log)
        self.assertIn("-s my-project", log)
        self.assertIn("grok --help", log)
        self.assertNotIn("MOCK_TMUX_ERROR", log)

    def test_cmd_default_resume_via_mock(self):
        self.log.unlink(missing_ok=True)
        self._script_run("'source bash_profile.sh; PWD=/tmp/proj cmd'")
        log = self._log()
        self.assertIn("TMUX_CALL:", log)
        self.assertNotIn("MOCK_TMUX_ERROR", log)
        self.assertNotIn("command cmd", log)
        self.assertIn("--resume --yolo", log)
        self.assertIn("cmd", log)

    def test_cmd_yolo_passthrough_via_mock(self):
        self.log.unlink(missing_ok=True)
        self._script_run("'source bash_profile.sh; PWD=/tmp/proj cmd --yolo hello'")
        log = self._log()
        self.assertIn("TMUX_CALL:", log)
        self.assertNotIn("MOCK_TMUX_ERROR", log)
        self.assertNotIn("--resume", log)
        self.assertIn("--yolo hello", log)

    def test_cmd_dangerously_skip_passthrough(self):
        self.log.unlink(missing_ok=True)
        self._script_run(
            "'source bash_profile.sh; PWD=/tmp/proj cmd --dangerously-skip-permissions hello'"
        )
        log = self._log()
        self.assertNotIn("MOCK_TMUX_ERROR", log)
        self.assertNotIn("--resume", log)
        self.assertIn("--dangerously-skip-permissions hello", log)

    def test_tmux_failure_propagates(self):
        self.log.unlink(missing_ok=True)
        env = {**self.base_env, "MOCK_TMUX_FAIL": "1", "MOCK_TMUX_EXIT_CODE": "42"}
        proc = self._script_run("'source bash_profile.sh; PWD=/tmp/proj cmd --help; echo EXIT:$?'", env=env)
        log = self._log()
        self.assertIn("MOCK_TMUX_ERROR: forced failure", log)
        self.assertIn("EXIT:42", proc.stdout + proc.stderr)

    def test_all_launchers_binary_resolvable(self):
        cases = [
            ("oc --help", "opencode"),
            ("cc --help", "codex"),
            ("ca --help", "cursor-agent"),
            ("ag --help", "agy"),
            ("cl --help", "claude"),
            ("cmd --help", "cmd"),
            ("gk --help", "grok"),
        ]
        for invocation, _ in cases:
            with self.subTest(invocation=invocation):
                self.log.unlink(missing_ok=True)
                env = self.base_env
                if invocation.startswith("occ") or invocation.startswith("cl "):
                    pass
                if "occ" in invocation:
                    env = {**self.base_env, "OPENCODE_API_KEY": "test-key-123"}
                    invocation = "occ --help"
                self._script_run(f"'source bash_profile.sh; PWD=/tmp/proj {invocation}'", env=env)
                log = self._log()
                self.assertIn("TMUX_CALL:", log, invocation)
                self.assertNotIn("MOCK_TMUX_ERROR", log, f"{invocation}: {log}")

    def test_bypass_cf_no_tmux(self):
        self.log.unlink(missing_ok=True)
        env = {**self.base_env, "CF_NO_TMUX": "1"}
        proc = run_bash("source bash_profile.sh; oc --help", env=env)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("FAKE:opencode", proc.stdout)
        self.assertEqual(self._log(), "")

    def test_bypass_tmux_env(self):
        self.log.unlink(missing_ok=True)
        env = {**self.base_env, "TMUX": "1"}
        proc = run_bash("source bash_profile.sh; oc --help", env=env)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("FAKE:opencode", proc.stdout)
        self.assertEqual(self._log(), "")

    def test_bypass_non_tty(self):
        self.log.unlink(missing_ok=True)
        proc = run_bash("source bash_profile.sh; echo piped | oc --help", env=self.base_env)
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

    def test_cmd_bypass_runs_without_crash(self):
        proc = run_bash("source bash_profile.sh; CF_NO_TMUX=1 cmd --help 2>&1 | head -n 1", env=self.base_env)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("FAKE:cmd", proc.stdout)

    def test_always_new_session_not_attach(self):
        for p in sorted((REPO_ROOT / "shell").glob("*.sh")):
            if p.name == "chrome.sh":
                continue
            text = p.read_text()
            if "new-session" in text:
                self.assertNotIn("new-session -A", text, f"{p.name} must not use -A (attach) — always create a new session")

    def test_second_launch_gets_suffixed_name(self):
        self.log.unlink(missing_ok=True)
        env = {**self.base_env, "MOCK_TMUX_EXISTING_SESSIONS": "my-project"}
        self._script_run("'source bash_profile.sh; PWD=/tmp/my.project oc --help'", env=env)
        log = self._log()
        self.assertIn("-s my-project-1", log)
        self.assertNotIn("-A", log)

    def test_third_launch_suffix_increments(self):
        self.log.unlink(missing_ok=True)
        env = {**self.base_env, "MOCK_TMUX_EXISTING_SESSIONS": "my-project my-project-1"}
        self._script_run("'source bash_profile.sh; PWD=/tmp/my.project oc --help'", env=env)
        log = self._log()
        self.assertIn("-s my-project-2", log)
        self.assertNotIn("-A", log)

    def test_cl_second_launch_suffixed(self):
        self.log.unlink(missing_ok=True)
        env = {**self.base_env, "MOCK_TMUX_EXISTING_SESSIONS": "proj"}
        self._script_run("'source bash_profile.sh; PWD=/tmp/proj cl hello'", env=env)
        log = self._log()
        self.assertIn("-s proj-1", log)
        self.assertNotIn("-A", log)

    def test_occ_second_launch_suffixed(self):
        self.log.unlink(missing_ok=True)
        env = {**self.base_env, "OPENCODE_API_KEY": "test-key-123", "MOCK_TMUX_EXISTING_SESSIONS": "proj"}
        self._script_run("'source bash_profile.sh; PWD=/tmp/proj occ --help'", env=env)
        log = self._log()
        self.assertIn("-s proj-1", log)
        self.assertNotIn("-A", log)


if __name__ == "__main__":
    unittest.main()
