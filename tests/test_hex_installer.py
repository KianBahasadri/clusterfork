"""Hex installation preserves live preferences and private dictation state."""

from copy import deepcopy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hex_install", ROOT / "hex/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class HexInstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name) / 'home with spaces "and quotes"'
        self.config = self.home / ".config/hex"
        self.config.mkdir(parents=True)
        self.data = self.home / ".local/share/hex"
        self.data.mkdir(parents=True)
        (self.data / "local-api.json").write_text("{}")
        self.private = {
            self.data / name: content for name, content in (
                ("history.json", b"not JSON; installation must never read history"),
                ("settings.json", b"owned and saved exclusively by the fake Hex process"),
                ("transform-trust.json", b"original approval state"),
                ("vocabulary-review-state.json", b"original watermark"),
                ("vocabulary-auto-added.jsonl", b"original learning log"),
            )
        }
        self.private.update({self.config / "hex.d.ts": b"Hex-owned types", self.config / "AGENTS.md": b"Hex-owned instructions"})
        for path, content in self.private.items():
            path.write_bytes(content)
        self.mode = {
            "name": "My global mode", "applications": ["Editor"], "browser_hosts": [],
            "vocabulary": ["ProjectName"], "replacements": [{"matched_phrase": "um", "output": ""}],
            "post_processing": {"enabled": True, "model": "old/model", "future_option": "keep"},
            "transformations": ["lowercase", "conversation-context", "no-trailing-punctuation"],
        }
        self.original = deepcopy(self.mode)
        self.vocabulary = [
            {"term": "kObO", "sounds_like": ["my existing alias"]},
            {"term": "gUaRaNiNe", "sounds_like": ["my custom alias", "GURNEY"]},
            {"term": "PrivateLocalName"},
        ]
        self.microphone = "My own USB microphone"
        self.commands = []
        self.processes = []
        self.manager_home = self.home
        for context in (
            patch.object(installer, "hex_command", side_effect=self.hex_command),
            patch.object(installer.subprocess, "run", side_effect=self.run_process),
            patch.object(installer.shutil, "which", side_effect=lambda name: f"/fake/{name}"),
            patch("sys.stdout", new=io.StringIO()),
        ):
            context.start()
            self.addCleanup(context.stop)

    def hex_command(self, binary, *arguments):
        self.commands.append(arguments)
        if arguments == ("status",):
            return {"status": "status"}
        if arguments[:2] == ("settings", "get"):
            value = self.microphone if arguments[2] == "microphone" else self.mode
            return {"settings": [{"value": deepcopy(value)}]}
        if arguments[:2] == ("settings", "set"):
            if arguments[2] == "microphone":
                self.microphone = arguments[3]
            else:
                self.assertEqual(arguments[2], "dictation_processing.default_mode")
                self.mode = json.loads(arguments[3])
            return {"status": "done"}
        if arguments == ("vocabulary", "list"):
            return {"entries": deepcopy(self.vocabulary)}
        if arguments[:2] == ("vocabulary", "add"):
            term = arguments[2]
            existing = next((entry for entry in self.vocabulary if entry["term"].casefold() == term.casefold()), None)
            if existing is None:
                existing = {"term": term}
                self.vocabulary.append(existing)
            if len(arguments) > 3:
                self.assertEqual(arguments[3], "--sounds-like")
                existing.setdefault("sounds_like", []).extend(arguments[4].split(","))
            return {"status": "done"}
        raise AssertionError(f"Unexpected Hex command: {arguments}")

    def run_process(self, arguments, **kwargs):
        self.processes.append(arguments)
        output = f"HOME={self.manager_home}\n" if arguments[1:] == ["--user", "show-environment"] else ""
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    def test_install_merges_owned_keys_and_reinstall_has_no_writes_or_restart(self):
        installer.install(self.home)
        for key in ("name", "applications", "browser_hosts", "vocabulary", "replacements"):
            self.assertEqual(self.mode[key], self.original[key])
        self.assertEqual(self.mode["transformations"], ["conversation-context", "lowercase", "no-trailing-punctuation"])
        profile = self.mode["post_processing"]
        self.assertEqual(profile["model"], "opencode-go/claude-haiku-5-5")
        self.assertEqual(profile["variant"], "none")
        self.assertFalse(profile["enabled"])
        self.assertEqual(profile["future_option"], "keep")
        self.assertIn(str(self.home / "guaranine"), profile["prompt"])
        self.assertEqual(self.microphone, "My own USB microphone")
        self.assertIn({"term": "PrivateLocalName"}, self.vocabulary)
        self.assertIn({"term": "kObO", "sounds_like": ["my existing alias"]}, self.vocabulary)
        self.assertIn({"term": "gUaRaNiNe", "sounds_like": ["my custom alias", "GURNEY", "burning", "grinning"]}, self.vocabulary)
        for path, content in self.private.items():
            self.assertEqual(path.read_bytes(), content)
        # Quoted/space-containing homes are encoded as a literal argv string.
        config = (self.config / "hex.config.ts").read_text()
        argument = config.split('execFile("/usr/bin/python3", [', 1)[1].split("], {", 1)[0]
        self.assertEqual(json.loads(argument), str(self.config / "contextual_rewrite.py"))
        self.assertFalse((self.config / "hex.config.ts").is_symlink())
        workspace = self.config / "linear"
        self.assertTrue((workspace / ".opencode/agents/linear-issue.md").is_file())
        plugin = workspace / ".opencode/plugins/linear-search.js"
        self.assertEqual(plugin.read_bytes(), (ROOT / "hex/linear-search.js").read_bytes())
        self.assertEqual((workspace / "linear_issue.py").read_bytes(), (ROOT / "hex/linear_issue.py").read_bytes())
        preview = (workspace / ".opencode/agents/linear-issue-preview.md").read_text()
        self.assertIn('action: linear_list_*', preview)
        self.assertIn('action: linear_get_*', preview)
        self.assertNotIn('action: linear_*,', preview)
        self.assertIn('action: hex_upload_clipboard, resource: "*", effect: deny', preview)
        self.assertIn('action: hex_notify, resource: "*", effect: deny', preview)
        self.assertTrue((self.home / ".local/share/applications" / installer.LINEAR_DESKTOP).is_file())
        self.assertIn(["/fake/systemctl", "--user", "enable", "--now", installer.LINEAR_UNIT], self.processes)
        files = [*self.config.rglob("*.py"), self.config / "hex.config.ts", plugin,
                 *(self.home / ".config/systemd/user").glob("*.service")]
        timestamps = {path: path.stat().st_mtime_ns for path in files}
        self.commands.clear()
        self.processes.clear()
        installer.install(self.home)
        self.assertEqual(timestamps, {path: path.stat().st_mtime_ns for path in files})
        self.assertFalse(any(command[:2] in [("settings", "set"), ("vocabulary", "add")] for command in self.commands))
        self.assertFalse(any("restart" in command for command in self.processes))

    def test_missing_hex_skips_without_writing_workspace_or_contacting_services(self):
        with patch.object(installer.shutil, "which", return_value=None):
            installer.install(self.home)
        self.assertFalse((self.config / "hex.config.ts").exists())
        self.assertEqual(self.commands, [])
        self.assertEqual(self.processes, [])

    def test_stopped_hex_installs_files_without_settings_or_service_calls(self):
        (self.data / "local-api.json").unlink()
        installer.install(self.home)
        self.assertTrue((self.config / "hex.config.ts").is_file())
        self.assertEqual(self.commands, [])
        self.assertEqual(len(self.processes), 1)
        self.assertEqual(self.processes[0][1:], ["commands", "check"])

    def test_stale_registration_defers_live_changes_without_launching_hex(self):
        with patch.object(installer, "hex_command", side_effect=subprocess.CalledProcessError(1, ["hex", "status"])):
            installer.install(self.home)
        self.assertEqual(self.mode, self.original)
        self.assertEqual(len(self.processes), 1)

    def test_other_home_cannot_activate_host_user_worker(self):
        self.manager_home = Path("/another/home")
        installer.install(self.home)
        self.assertFalse(any("enable" in command or "restart" in command or "daemon-reload" in command for command in self.processes))

    def test_microphone_rules_are_explicit_and_do_not_restart_audio(self):
        installer.install(self.home)
        self.assertFalse((self.home / ".config/wireplumber").exists())
        installer.install(self.home, "pcm2902")
        self.assertEqual(self.microphone, "PipeWire Sound Server")
        self.assertTrue((self.home / ".config/wireplumber/wireplumber.conf.d/60-pcm2902-microphone.conf").is_file())
        self.assertTrue((self.home / ".local/bin/disable-laptop-capture.py").is_file())
        dropin = (self.home / ".config/systemd/user/wireplumber.service.d/60-disable-laptop-capture.conf").read_text()
        self.assertIn('"%h/.local/bin/disable-laptop-capture.py"', dropin)
        self.assertFalse(any("wireplumber" in " ".join(command) for command in self.processes))

    def test_failed_live_update_does_not_activate_worker(self):
        def fail_on_write(binary, *arguments):
            if arguments[:2] == ("settings", "set"):
                raise subprocess.CalledProcessError(1, ["hex", "settings", "set"])
            return self.hex_command(binary, *arguments)
        with patch.object(installer, "hex_command", side_effect=fail_on_write):
            with self.assertRaises(subprocess.CalledProcessError):
                installer.install(self.home)
        self.assertEqual(self.mode, self.original)
        self.assertFalse(any("enable" in command for command in self.processes))


class HexFullInstallerTests(unittest.TestCase):
    def test_main_installer_includes_hex_and_forwards_flags_in_disposable_home(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            home, repo, binary = directory / "home", directory / "repo", directory / "bin"
            home.mkdir()
            binary.mkdir()
            shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", ".env", ".codeview", "__pycache__", ".notify-relay.sock"))
            (repo / ".env").write_text("CONTEXT7_API_KEY=test\nPIXELLAB_API_KEY=test\n")
            (binary / "hex").write_text('#!/bin/sh\n[ "$*" = "commands check" ] || exit 9\n')
            (binary / "hex").chmod(0o755)
            # Do not touch the real user's systemd manager from this fixture.
            (binary / "systemctl").write_text('#!/bin/sh\nexit 1\n')
            (binary / "systemctl").chmod(0o755)
            environment = {**os.environ, "HOME": str(home), "PATH": str(binary) + os.pathsep + os.environ["PATH"]}
            command = [str(repo / "install-clusterfork.sh")]
            result = subprocess.run(command + ["--hex-microphone", "pcm2902"], cwd=repo, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = home / ".config/hex"
            self.assertTrue((config / "hex.config.ts").is_file())
            self.assertTrue((config / "linear/linear_shortcut.py").is_file())
            self.assertTrue((home / ".config/wireplumber/wireplumber.conf.d/60-pcm2902-microphone.conf").is_file())
            shutil.rmtree(config)
            result = subprocess.run(command + ["--skip-hex"], cwd=repo, env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(config.exists())


if __name__ == "__main__":
    unittest.main()
