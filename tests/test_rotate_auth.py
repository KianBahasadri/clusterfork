"""Tests for scripts/rotate_auth.py.

Filesystem paths use temporary directories. Antigravity deletion uses a fake
secret-tool service so tests never touch the real keyring.
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    # The script lives outside a package; load it directly by path.
    spec = importlib.util.spec_from_file_location(
        "rotate_auth", REPO_ROOT / "scripts" / "rotate_auth.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rotate_auth = _load_module()


class ParseActionTests(unittest.TestCase):
    def test_no_args_is_bare_rotate(self):
        self.assertEqual(rotate_auth.parse_action("cmd", []), ("rotate", None))

    def test_name_selects_profile(self):
        self.assertEqual(rotate_auth.parse_action("cmd", ["work"]), ("select", "work"))

    def test_empty_string_is_bare_rotate(self):
        # Old bash treated `rotate-claude ""` as a bare rotate.
        self.assertEqual(rotate_auth.parse_action("cmd", [""]), ("rotate", None))

    def test_save_takes_exactly_one_arg(self):
        self.assertEqual(rotate_auth.parse_action("cmd", ["--save", "x"]), ("save", "x"))
        with self.assertRaises(rotate_auth.RotateError):
            rotate_auth.parse_action("cmd", ["--save"])
        with self.assertRaises(rotate_auth.RotateError):
            rotate_auth.parse_action("cmd", ["--save", "a", "b"])

    def test_delete_requires_exactly_one_valid_name(self):
        self.assertEqual(
            rotate_auth.parse_action("cmd", ["--delete", "work"]), ("delete", "work")
        )
        for args in (
            ["--delete"], ["--delete", "a", "b"], ["--delete", ""],
            ["--delete", "../work"], ["--delete", "has space"],
        ):
            with self.subTest(args=args), self.assertRaises(rotate_auth.RotateError):
                rotate_auth.parse_action("cmd", args)

    def test_flags_reject_extra_args(self):
        for flag in ("-h", "--help", "--list", "--unhook"):
            with self.assertRaises(rotate_auth.RotateError):
                rotate_auth.parse_action("cmd", [flag, "extra"])

    def test_kickoff_parses_action(self):
        self.assertEqual(
            rotate_auth.parse_action("cmd", ["--kickoff"]), ("kickoff", None)
        )
        self.assertEqual(
            rotate_auth.parse_action("cmd", ["--kickoff", "a", "b"]),
            ("kickoff", ["a", "b"]),
        )

    def test_unknown_flag_fails(self):
        for flag in ("--bogus", "--start"):
            with self.assertRaises(rotate_auth.RotateError):
                rotate_auth.parse_action("cmd", [flag])

    def test_too_many_positional_args_fail(self):
        with self.assertRaises(rotate_auth.RotateError):
            rotate_auth.parse_action("cmd", ["a", "b"])


class ValidateSuffixTests(unittest.TestCase):
    def test_valid_names_pass(self):
        for name in ("work", "a.b_c-d", "123"):
            rotate_auth.validate_suffix("cmd", name)

    def test_invalid_names_fail(self):
        for name in ("has space", "slash/name", "", "unié"):
            with self.assertRaises(rotate_auth.RotateError):
                rotate_auth.validate_suffix("cmd", name)


class NextInOrderTests(unittest.TestCase):
    def test_advances_within_list(self):
        self.assertEqual(
            rotate_auth.next_in_order(["a", "b", "c"], "a"), "b"
        )

    def test_wraps_around(self):
        self.assertEqual(
            rotate_auth.next_in_order(["a", "b", "c"], "c"), "a"
        )

    def test_current_not_in_list_starts_at_first(self):
        self.assertEqual(
            rotate_auth.next_in_order(["a", "b"], None), "a"
        )
        self.assertEqual(
            rotate_auth.next_in_order(["a", "b"], "zzz"), "a"
        )


class SortNamesTests(unittest.TestCase):
    def test_sorts_and_dedups_via_caller(self):
        self.assertEqual(rotate_auth.sort_names(["b", "a", "b"]), ["a", "b", "b"])

    def test_empty_input(self):
        self.assertEqual(rotate_auth.sort_names([]), [])


class EnvPathTests(unittest.TestCase):
    def test_unset_uses_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ.pop("ROTATE_TEST_VAR", None)
            self.assertEqual(
                rotate_auth.env_path("ROTATE_TEST_VAR", Path(tmp)), Path(tmp)
            )

    def test_empty_value_uses_default(self):
        # Matches bash `${VAR:-default}` semantics.
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["ROTATE_TEST_VAR"] = ""
            try:
                self.assertEqual(
                    rotate_auth.env_path("ROTATE_TEST_VAR", Path(tmp)), Path(tmp)
                )
            finally:
                del os.environ["ROTATE_TEST_VAR"]

    def test_set_value_wins(self):
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            os.environ["ROTATE_TEST_VAR"] = tmp_b
            try:
                self.assertEqual(
                    rotate_auth.env_path("ROTATE_TEST_VAR", Path(tmp_a)),
                    Path(tmp_b),
                )
            finally:
                del os.environ["ROTATE_TEST_VAR"]


class RealpathMsTests(unittest.TestCase):
    def test_absolute_path_stays_absolute(self):
        out = rotate_auth.realpath_ms(Path("relative/../thing"))
        self.assertTrue(out.is_absolute())
        self.assertEqual(out.name, "thing")

    def test_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            real_dir = Path(tmp) / "real"
            link = Path(tmp) / "link"
            real_dir.mkdir()
            link.symlink_to(real_dir)
            self.assertEqual(
                rotate_auth.realpath_ms(link / "file.txt"),
                Path(tmp) / "link" / "file.txt",
            )


class DeleteProfileTests(unittest.TestCase):
    AGENTS = ("claude", "codex", "cursor", "grok", "opencode", "antigravity")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = {
            "ROTATE_CLAUDE_DIR": str(self.root / "claude"),
            "ROTATE_ANTIGRAVITY_STATE_DIR": str(self.root / "antigravity"),
        }
        for agent, agent_env, store_env in (
            ("codex", "ROTATE_CODEX_CODEX_DIR", "ROTATE_CODEX_AUTH_STORE_DIR"),
            ("cursor", "ROTATE_CURSOR_DIR", "ROTATE_CURSOR_AUTH_STORE_DIR"),
            ("grok", "ROTATE_GROK_DIR", "ROTATE_GROK_AUTH_STORE_DIR"),
            ("opencode", "ROTATE_OPENCODE_DIR", "ROTATE_OPENCODE_AUTH_STORE_DIR"),
        ):
            env[agent_env] = str(self.root / agent / "agent")
            env[store_env] = str(self.root / agent / "store")
        env_patch = patch.dict(os.environ, env)
        env_patch.start()
        self.addCleanup(env_patch.stop)
        output_patch = patch("sys.stdout", new=io.StringIO())
        self.output = output_patch.start()
        self.addCleanup(output_patch.stop)
        input_patch = patch("builtins.input", return_value="yes")
        self.answer = input_patch.start()
        self.addCleanup(input_patch.stop)
        self.keyring = {}
        for fake in (
            patch.object(rotate_auth.AntigravityBackend, "_require_secret_tool"),
            patch.object(rotate_auth.subprocess, "run", side_effect=self._secret_tool),
        ):
            fake.start()
            self.addCleanup(fake.stop)

        self.backends = {}
        for agent in self.AGENTS:
            backend = rotate_auth.make_backend(agent)
            self.backends[agent] = backend
            if agent == "antigravity":
                backend.state_dir.mkdir()
                backend.profiles_file.write_text("alpha\nbeta\n", encoding="utf-8")
                backend.current_file.write_text("alpha\n", encoding="utf-8")
                for name in ("alpha", "beta"):
                    self.keyring[(backend.profile_service, name)] = name.encode()
                self.keyring[(backend.active_service, backend.active_user)] = b"alpha"
            else:
                for name in ("alpha", "beta"):
                    profile = self._profile_path(backend, name)
                    profile.parent.mkdir(parents=True, exist_ok=True)
                    profile.write_text(
                        json.dumps({"claudeAiOauth": {"accessToken": name}}),
                        encoding="utf-8",
                    )
                backend.select("alpha")

    def _profile_path(self, backend, name):
        if isinstance(backend, rotate_auth.ClaudeBackend):
            return backend.claude_dir / f".credentials.json.{name}"
        return backend.store_dir / f"auth.json.{name}"

    def _secret_tool(self, args, **kwargs):
        self.assertEqual(args[0], "secret-tool")
        key = (args[args.index("service") + 1], args[args.index("username") + 1])
        if args[1] == "lookup":
            secret = self.keyring.get(key)
            return subprocess.CompletedProcess(
                args, 0 if secret is not None else 1, stdout=secret or b"", stderr=b""
            )
        if args[1] == "clear":
            secret = self.keyring.pop(key, None)
            return subprocess.CompletedProcess(
                args, 0 if secret is not None else 1, stdout=b"", stderr=b""
            )
        self.assertEqual(args[1], "store")
        self.keyring[key] = kwargs["input"]
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    def _snapshot(self):
        files = {}
        for path in self.root.rglob("*"):
            if path.is_symlink():
                files[str(path)] = ("link", os.readlink(path))
            elif path.is_file():
                files[str(path)] = ("file", path.read_bytes())
        return files, dict(self.keyring)

    def test_delete_inactive_profile_preserves_active_credentials(self):
        for agent, backend in self.backends.items():
            with self.subTest(agent=agent):
                before_files, before_keys = self._snapshot()

                def confirm(prompt):
                    self.assertIn(backend.command, prompt)
                    self.assertIn("'beta'", prompt)
                    self.assertIn("[y/N]", prompt)
                    self.assertEqual(self._snapshot(), (before_files, before_keys))
                    return "yes"

                self.answer.reset_mock()
                self.answer.side_effect = confirm
                self.assertEqual(rotate_auth.run([agent, "--delete", "beta"]), 0)
                self.answer.assert_called_once()
                self.assertEqual(backend.suffixes(), ["alpha"])
                self.assertEqual(backend.current_suffix(), "alpha")
                if agent == "antigravity":
                    before_keys.pop((backend.profile_service, "beta"))
                    before_files[str(backend.profiles_file)] = ("file", b"alpha\n")
                else:
                    before_files.pop(str(self._profile_path(backend, "beta")))
                self.assertEqual(self._snapshot(), (before_files, before_keys))
                self.assertIn(f"{backend.command}: deleted profile beta", self.output.getvalue())

    def test_declining_or_empty_confirmation_preserves_all_state(self):
        before = self._snapshot()
        for agent, backend in self.backends.items():
            for answer in ("", "n", "no", "anything else"):
                with self.subTest(agent=agent, answer=answer):
                    self.answer.return_value = answer
                    self.assertEqual(rotate_auth.run([agent, "--delete", "beta"]), 1)
                    self.assertEqual(self._snapshot(), before)
                    self.assertIn(
                        f"{backend.command}: deletion cancelled", self.output.getvalue()
                    )

    def test_eof_or_interrupt_preserves_all_state(self):
        before = self._snapshot()
        for agent in self.AGENTS:
            for interruption in (EOFError, KeyboardInterrupt):
                with self.subTest(agent=agent, interruption=interruption):
                    self.answer.side_effect = interruption
                    self.assertEqual(rotate_auth.run([agent, "--delete", "beta"]), 1)
                    self.assertEqual(self._snapshot(), before)

    def test_confirmation_accepts_y_or_yes_case_insensitively(self):
        backend = self.backends["codex"]
        profile = self._profile_path(backend, "beta")
        contents = profile.read_bytes()
        for answer in ("y", "Y", "yes", "YES", " Yes "):
            with self.subTest(answer=answer):
                self.answer.return_value = answer
                profile.write_bytes(contents)
                self.assertEqual(rotate_auth.run(["codex", "--delete", "beta"]), 0)
                self.assertFalse(profile.exists())

    def test_cancelling_detached_profile_delete_preserves_selection(self):
        for agent, backend in self.backends.items():
            with self.subTest(agent=agent):
                self.assertEqual(rotate_auth.run([agent, "--unhook"]), 0)
                before = self._snapshot()
                self.answer.return_value = "n"
                self.assertEqual(rotate_auth.run([agent, "--delete", "alpha"]), 1)
                self.assertEqual(self._snapshot(), before)

    def test_delete_active_profile_refuses_without_changing_state(self):
        before = self._snapshot()
        for agent in self.AGENTS:
            with self.subTest(agent=agent), self.assertRaises(rotate_auth.RotateError) as error:
                rotate_auth.run([agent, "--delete", "alpha"])
            self.assertIn("cannot delete active profile", str(error.exception))
            self.assertIn("--unhook", str(error.exception))
            self.assertEqual(self._snapshot(), before)
        self.answer.assert_not_called()

    def test_unhook_then_delete_last_profile_clears_selection(self):
        for agent, backend in self.backends.items():
            with self.subTest(agent=agent):
                rotate_auth.run([agent, "--delete", "beta"])
                self.assertEqual(rotate_auth.run([agent, "--unhook"]), 0)
                self.assertEqual(rotate_auth.run([agent, "--delete", "alpha"]), 0)
                self.assertEqual(backend.suffixes(), [])
                self.assertIsNone(backend.current_suffix())
                if agent == "antigravity":
                    self.assertFalse(backend.current_file.exists())
                    self.assertEqual(self.keyring, {})
                elif agent == "claude":
                    self.assertFalse(backend.active.exists())
                else:
                    self.assertFalse(backend.auth.is_symlink())
                    self.assertFalse(backend.current.is_symlink())

    def test_unknown_or_invalid_name_never_changes_state(self):
        before = self._snapshot()
        for agent in self.AGENTS:
            for name in ("missing", "", "../alpha", "has space"):
                with self.subTest(agent=agent, name=name):
                    with self.assertRaises(rotate_auth.RotateError):
                        rotate_auth.run([agent, "--delete", name])
                    self.assertEqual(self._snapshot(), before)
        self.answer.assert_not_called()

    def test_shared_store_preserves_new_regular_file_login(self):
        for agent in ("codex", "cursor", "grok", "opencode"):
            with self.subTest(agent=agent):
                backend = self.backends[agent]
                backend.unhook()
                backend.auth.write_bytes(b"new unsaved login")
                self.assertEqual(rotate_auth.run([agent, "--delete", "alpha"]), 0)
                self.assertEqual(backend.auth.read_bytes(), b"new unsaved login")
                self.assertFalse(backend.auth.is_symlink())
                self.assertFalse(backend.current.is_symlink())
                self.assertEqual(backend.suffixes(), ["beta"])

    def test_shared_store_protects_direct_auth_link(self):
        backend = self.backends["codex"]
        backend.auth.unlink()
        backend.auth.symlink_to(self._profile_path(backend, "beta"))
        before = self._snapshot()
        with self.assertRaises(rotate_auth.RotateError):
            backend.delete("beta")
        self.assertEqual(self._snapshot(), before)

    def test_claude_protects_duplicate_token_profiles(self):
        backend = self.backends["claude"]
        self._profile_path(backend, "beta").write_bytes(backend.active.read_bytes())
        before = self._snapshot()
        with self.assertRaises(rotate_auth.RotateError):
            backend.delete("beta")
        self.assertEqual(self._snapshot(), before)

    def test_file_profile_symlink_is_removed_without_deleting_target(self):
        for agent in ("claude", "codex", "cursor", "grok", "opencode"):
            with self.subTest(agent=agent):
                backend = self.backends[agent]
                profile = self._profile_path(backend, "beta")
                target = profile.with_name("external-credentials")
                profile.rename(target)
                profile.symlink_to(target)
                original = target.read_bytes()
                self.assertEqual(backend.delete("beta"), 0)
                self.assertFalse(profile.is_symlink())
                self.assertEqual(target.read_bytes(), original)

    def test_antigravity_keyring_failure_preserves_profile_metadata(self):
        backend = self.backends["antigravity"]
        before = self._snapshot()
        failed = subprocess.CompletedProcess([], 1, stdout=b"", stderr=b"keyring locked")
        with patch.object(rotate_auth.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(rotate_auth.RotateError, "keyring locked"):
                backend.delete("beta")
        self.assertEqual(self._snapshot(), before)

    def test_antigravity_missing_keyring_entry_preserves_profile_metadata(self):
        backend = self.backends["antigravity"]
        del self.keyring[(backend.profile_service, "beta")]
        before = self._snapshot()
        with self.assertRaisesRegex(rotate_auth.RotateError, "secret-tool clear failed"):
            backend.delete("beta")
        self.assertEqual(self._snapshot(), before)

    def test_file_profile_directory_is_never_deleted(self):
        for agent in ("claude", "codex", "cursor", "grok", "opencode"):
            with self.subTest(agent=agent):
                backend = self.backends[agent]
                profile = self._profile_path(backend, "beta")
                profile.unlink()
                profile.mkdir()
                (profile / "keep").write_bytes(b"unrelated file")
                before = self._snapshot()
                with self.assertRaises(OSError):
                    backend.delete("beta")
                self.assertTrue(profile.is_dir())
                self.assertEqual(self._snapshot(), before)

    def test_help_advertises_delete_for_every_agent(self):
        for agent in self.AGENTS:
            with self.subTest(agent=agent):
                self.output.seek(0)
                self.output.truncate()
                self.assertEqual(rotate_auth.run([agent, "--help"]), 0)
                self.assertIn("--delete name", self.output.getvalue())


class SharedStoreBackendTests(unittest.TestCase):
    """Exercise SharedStoreBackend through its env-var overrides."""

    AGENT_ENV = "ROTATE_OPENCODE_DIR"
    STORE_ENV = "ROTATE_OPENCODE_AUTH_STORE_DIR"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        home = Path(self._tmp.name)
        self.agent_dir = home / ".local/share/opencode"
        self.store_dir = home / ".local/share/clusterfork-auth/opencode"
        os.environ[self.AGENT_ENV] = str(self.agent_dir)
        os.environ[self.STORE_ENV] = str(self.store_dir)

    def tearDown(self):
        os.environ.pop(self.AGENT_ENV, None)
        os.environ.pop(self.STORE_ENV, None)

    def _backend(self) -> rotate_auth.SharedStoreBackend:
        return rotate_auth.SharedStoreBackend(
            command="rotate-opencode",
            label="OpenCode",
            agent_dir_env=self.AGENT_ENV,
            store_dir_env=self.STORE_ENV,
            default_agent=Path(".local/share/opencode"),
            default_store=Path(".local/share/clusterfork-auth/opencode"),
        )

    def _write_profile(self, name: str, token: str) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        (self.store_dir / f"auth.json.{name}").write_text(token, encoding="utf-8")

    def _fake_ping(self, exit_code: int = 0) -> str:
        """Put a fake `opencode` first on PATH; returns its bin dir."""
        bin_dir = Path(self._tmp.name) / "fakebin"
        bin_dir.mkdir(exist_ok=True)
        script = bin_dir / "opencode"
        script.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$@" >> "$FAKE_PING_LOG"\n'
            'echo "called" >> "$FAKE_PING_LOG"\n'
            'exit "$FAKE_PING_EXIT"\n',
            encoding="utf-8",
        )
        script.chmod(0o755)
        return str(bin_dir)

    def _patch_ping(self, exit_code: int = 0):
        log = Path(self._tmp.name) / "ping.log"
        os.environ["FAKE_PING_LOG"] = str(log)
        os.environ["FAKE_PING_EXIT"] = str(exit_code)
        saved_path = os.environ["PATH"]
        os.environ["PATH"] = f"{self._fake_ping(exit_code)}{os.pathsep}{saved_path}"
        self.addCleanup(os.environ.pop, "FAKE_PING_LOG", None)
        self.addCleanup(os.environ.pop, "FAKE_PING_EXIT", None)
        self.addCleanup(os.environ.__setitem__, "PATH", saved_path)
        return log

    def test_save_creates_profile_and_link_chain(self):
        self.agent_dir.mkdir(parents=True)
        (self.agent_dir / "auth.json").write_text('{"k": 1}', encoding="utf-8")
        backend = rotate_auth.SharedStoreBackend(
            command="rotate-opencode",
            label="OpenCode",
            agent_dir_env=self.AGENT_ENV,
            store_dir_env=self.STORE_ENV,
            default_agent=Path(".local/share/opencode"),
            default_store=Path(".local/share/clusterfork-auth/opencode"),
        )
        rc = backend.save("work")
        self.assertEqual(rc, 0)
        profile = self.store_dir / "auth.json.work"
        self.assertTrue(profile.is_file())
        current = self.store_dir / "current"
        self.assertTrue(current.is_symlink())
        auth = self.agent_dir / "auth.json"
        self.assertTrue(auth.is_symlink())
        self.assertEqual(auth.resolve(), profile.resolve())

    def test_select_cycles_through_profiles(self):
        self._write_profile("alpha", '{"t": 1}')
        self._write_profile("beta", '{"t": 2}')
        backend = rotate_auth.SharedStoreBackend(
            command="rotate-opencode",
            label="OpenCode",
            agent_dir_env=self.AGENT_ENV,
            store_dir_env=self.STORE_ENV,
            default_agent=Path(".local/share/opencode"),
            default_store=Path(".local/share/clusterfork-auth/opencode"),
        )
        self.assertEqual(backend.select(None), 0)
        first = backend.current_suffix()
        self.assertIn(first, {"alpha", "beta"})
        self.assertEqual(backend.select(None), 0)
        second = backend.current_suffix()
        self.assertNotEqual(first, second)

    def test_select_unknown_profile_fails(self):
        self._write_profile("alpha", "{}")
        backend = rotate_auth.SharedStoreBackend(
            command="rotate-opencode",
            label="OpenCode",
            agent_dir_env=self.AGENT_ENV,
            store_dir_env=self.STORE_ENV,
            default_agent=Path(".local/share/opencode"),
            default_store=Path(".local/share/clusterfork-auth/opencode"),
        )
        with self.assertRaises(rotate_auth.RotateError):
            backend.select("nope")

    def test_kickoff_pings_each_profile_and_restores_current(self):
        self._write_profile("alpha", '{"t": 1}')
        self._write_profile("beta", '{"t": 2}')
        backend = self._backend()
        self.assertEqual(backend.select("beta"), 0)
        orig_current = rotate_auth.readlink_text(self.store_dir / "current")
        self.assertEqual(orig_current, "auth.json.beta")

        log = self._patch_ping(exit_code=0)
        self.assertEqual(backend.kickoff(), 0)

        lines = log.read_text(encoding="utf-8").splitlines()
        # Each blocking call logs the argv ("run", "hi") plus a "called" marker.
        self.assertEqual(lines.count("called"), 2)
        self.assertEqual(lines.count("run"), 2)
        self.assertIn("hi", lines)
        self.assertEqual(
            rotate_auth.readlink_text(self.store_dir / "current"), "auth.json.beta"
        )
        auth = self.agent_dir / "auth.json"
        self.assertTrue(auth.is_symlink())

    def test_kickoff_counts_failures_and_still_restores_current(self):
        self._write_profile("alpha", '{"t": 1}')
        self._write_profile("beta", '{"t": 2}')
        backend = self._backend()
        self.assertEqual(backend.select("alpha"), 0)

        self._patch_ping(exit_code=3)
        self.assertEqual(backend.kickoff(), 1)
        self.assertEqual(
            rotate_auth.readlink_text(self.store_dir / "current"), "auth.json.alpha"
        )

    def test_kickoff_when_unhooked_removes_auth_link_afterwards(self):
        self._write_profile("alpha", '{"t": 1}')
        backend = self._backend()

        log = self._patch_ping(exit_code=0)
        self.assertEqual(backend.kickoff(), 0)
        self.assertEqual(log.read_text(encoding="utf-8").count("called"), 1)
        self.assertFalse((self.agent_dir / "auth.json").exists())
        self.assertTrue((self.store_dir / "current").is_symlink())

    def test_kickoff_refuses_regular_auth_file(self):
        self._write_profile("alpha", "{}")
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        (self.agent_dir / "auth.json").write_text("{}", encoding="utf-8")
        backend = self._backend()
        with self.assertRaises(rotate_auth.RotateError):
            backend.kickoff()

    def test_unhook_without_symlink_reports_already(self):
        self._write_profile("alpha", "{}")
        backend = rotate_auth.SharedStoreBackend(
            command="rotate-opencode",
            label="OpenCode",
            agent_dir_env=self.AGENT_ENV,
            store_dir_env=self.STORE_ENV,
            default_agent=Path(".local/share/opencode"),
            default_store=Path(".local/share/clusterfork-auth/opencode"),
        )
        self.assertEqual(backend.unhook(), 0)

    def test_run_dispatches_list_for_opencode(self):
        self._write_profile("alpha", "{}")
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "rotate_auth.py"), "opencode", "--list"],
            capture_output=True,
            text=True,
            env={**os.environ},
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("saved profiles", proc.stdout)
        self.assertIn("alpha", proc.stdout)

    def test_make_backend_grok(self):
        backend = rotate_auth.make_backend("grok")
        self.assertIsInstance(backend, rotate_auth.SharedStoreBackend)
        self.assertEqual(backend.command, "rotate-grok")
        self.assertEqual(backend.label, "Grok")
        self.assertEqual(backend.agent_dir, Path.home() / ".grok")
        self.assertEqual(
            backend.store_dir, Path.home() / ".local/share/clusterfork-auth/grok"
        )

    def test_kickoff_codex_ping_uses_luna_model(self):
        cmd = rotate_auth.KICKOFF_PINGS["rotate-codex"]
        self.assertIn("-m", cmd)
        self.assertEqual(cmd[cmd.index("-m") + 1], "gpt-6-luna")



if __name__ == "__main__":
    unittest.main()

