"""Characterization tests for scripts/rotate_auth.py.

Only the pure logic and env-overridable CLI paths are tested. The Antigravity
backend needs `secret-tool` and a real keyring; that is deliberately not
covered here.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

    def test_flags_reject_extra_args(self):
        for flag in ("-h", "--help", "--list", "--unhook"):
            with self.assertRaises(rotate_auth.RotateError):
                rotate_auth.parse_action("cmd", [flag, "extra"])

    def test_start_and_kickoff_are_aliases(self):
        self.assertEqual(rotate_auth.parse_action("cmd", ["--start"]), ("start", None))
        self.assertEqual(
            rotate_auth.parse_action("cmd", ["--kickoff"]), ("start", None)
        )

    def test_unknown_flag_fails(self):
        with self.assertRaises(rotate_auth.RotateError):
            rotate_auth.parse_action("cmd", ["--bogus"])

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

    def test_start_pings_each_profile_and_restores_current(self):
        self._write_profile("alpha", '{"t": 1}')
        self._write_profile("beta", '{"t": 2}')
        backend = self._backend()
        self.assertEqual(backend.select("beta"), 0)
        orig_current = rotate_auth.readlink_text(self.store_dir / "current")
        self.assertEqual(orig_current, "auth.json.beta")

        log = self._patch_ping(exit_code=0)
        self.assertEqual(backend.start(), 0)

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

    def test_start_counts_failures_and_still_restores_current(self):
        self._write_profile("alpha", '{"t": 1}')
        self._write_profile("beta", '{"t": 2}')
        backend = self._backend()
        self.assertEqual(backend.select("alpha"), 0)

        self._patch_ping(exit_code=3)
        self.assertEqual(backend.start(), 1)
        self.assertEqual(
            rotate_auth.readlink_text(self.store_dir / "current"), "auth.json.alpha"
        )

    def test_start_when_unhooked_removes_auth_link_afterwards(self):
        self._write_profile("alpha", '{"t": 1}')
        backend = self._backend()

        log = self._patch_ping(exit_code=0)
        self.assertEqual(backend.start(), 0)
        self.assertEqual(log.read_text(encoding="utf-8").count("called"), 1)
        self.assertFalse((self.agent_dir / "auth.json").exists())
        self.assertTrue((self.store_dir / "current").is_symlink())

    def test_start_refuses_regular_auth_file(self):
        self._write_profile("alpha", "{}")
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        (self.agent_dir / "auth.json").write_text("{}", encoding="utf-8")
        backend = self._backend()
        with self.assertRaises(rotate_auth.RotateError):
            backend.start()

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


if __name__ == "__main__":
    unittest.main()
