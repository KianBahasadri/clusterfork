"""Exercise the guest/host notification boundary without desktop/network access."""

import importlib.machinery
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

from test_clusterfork_notify import NotifyFixture, NOTIFIER


ROOT = Path(__file__).resolve().parent.parent
RELAY = ROOT / "bin/clusterfork-notify-relay"
loader = importlib.machinery.SourceFileLoader("notify_relay", str(RELAY))
spec = importlib.util.spec_from_loader(loader.name, loader)
relay = importlib.util.module_from_spec(spec)
loader.exec_module(relay)


class ProtocolTests(unittest.TestCase):
    def test_only_notification_metadata_is_accepted(self):
        valid = {"agent": "codex", "project": "my-project", "channels": "bell,phone"}
        self.assertEqual(relay.parse_request(json.dumps(valid).encode()),
                         ("codex", "my-project", "bell,phone"))
        for change in (
            {"agent": "bash"}, {"agent": ["codex"]}, {"channels": "all"},
            {"project": "/tmp/file"}, {"project": "$(touch owned)"},
            {"project": "<b>markup</b>"}, {"project": "line\nline"},
            {"project": "x" * 81}, {"command": "id"}, {"url": "http://example.com"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                relay.parse_request(json.dumps({**valid, **change}).encode())
        for payload in (b"[]", b"null", b"bad json", b" " * 513):
            with self.subTest(payload=payload[:20]), self.assertRaises(ValueError):
                relay.parse_request(payload)

    def test_client_sanitizes_only_display_text(self):
        self.assertEqual(relay.project_name("my\n<project>/$(id)"), "myprojectid")
        self.assertEqual(relay.project_name("\x1b\n"), "workspace")

    def test_socket_never_replaces_regular_files_or_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "socket"
            target = Path(directory) / "keep"
            target.write_text("unchanged")
            path.symlink_to(target)
            with self.assertRaises(ValueError):
                relay.bind_socket(path)
            self.assertEqual(target.read_text(), "unchanged")
            path.unlink()
            path.write_text("unchanged")
            with self.assertRaises(ValueError):
                relay.bind_socket(path)
            self.assertEqual(path.read_text(), "unchanged")


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "relay.sock"
        self.log = self.root / "calls"
        shutil.copy2(RELAY, self.root / RELAY.name)
        helper = self.root / "clusterfork-notify"
        helper.write_text("#!/usr/bin/python3 -I\nimport json, sys\n"
                          f"with open({str(self.log)!r}, 'a') as f:\n"
                          "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n")
        helper.chmod(0o755)
        self.server = subprocess.Popen(
            ["python3", "-I", str(self.root / RELAY.name), "serve", str(self.path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        self.addCleanup(self.stop_server)
        deadline = time.monotonic() + 3
        while not self.path.exists():
            if self.server.poll() is not None:
                self.fail(self.server.stderr.read())
            if time.monotonic() >= deadline:
                self.fail("relay did not create its socket")
            time.sleep(0.01)

    def stop_server(self):
        self.server.terminate()
        self.server.wait(timeout=3)
        self.server.stderr.close()

    def request(self, payload):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(self.path))
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            return client.recv(64)

    def test_invalid_requests_do_not_execute_and_valid_requests_are_rate_limited(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.request(b'{"command":"touch owned"}'), b"invalid request\n")
        self.assertEqual(self.request(b"x" * 513), b"invalid request\n")
        self.assertFalse(self.log.exists())
        payload = b'{"agent":"codex","project":"my-project","channels":"bell"}'
        self.assertEqual(self.request(payload), b"accepted\n")
        self.assertIn(b"rate limited", self.request(payload))
        deadline = time.monotonic() + 2
        while not self.log.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.log.read_text().splitlines(),
                         [json.dumps(["--from-devbox", "codex", "my-project", "bell"])])

    def test_stalled_client_does_not_block_later_alerts(self):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stalled:
            stalled.connect(str(self.path))
            stalled.sendall(b"{")
            started = time.monotonic()
            payload = b'{"agent":"claude","project":"project","channels":"bell"}'
            self.assertEqual(self.request(payload), b"accepted\n")
            self.assertLess(time.monotonic() - started, 1.5)

    def test_live_socket_cannot_be_replaced_by_second_server(self):
        with self.assertRaises(ValueError):
            relay.bind_socket(self.path)
        self.assertEqual(self.request(b'{"agent":"codex","project":"project","channels":"bell"}'),
                         b"accepted\n")


class RelayHookTests(NotifyFixture):
    def setUp(self):
        super().setUp()
        self.socket = self.root / "relay.sock"
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.socket))
        self.listener.listen(1)
        self.listener.settimeout(2)
        self.addCleanup(self.listener.close)

    def test_stop_forwards_metadata_only_without_local_side_effects(self):
        self.env_file.write_text(f"CLUSTERFORK_NOTIFY_RELAY_SOCKET={self.socket}\n")
        proc = subprocess.Popen([str(NOTIFIER), "codex"], cwd=ROOT,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=self.env(), text=True)
        try:
            with self.listener.accept()[0] as connection:
                connection.settimeout(1)
                request = json.loads(connection.recv(513))
                connection.sendall(b"accepted\n")
            out, err = proc.communicate("private transcript", timeout=3)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        self.assertEqual((proc.returncode, out, err), (0, "", ""))
        self.assertEqual(request, {"agent": "codex", "project": ROOT.name, "channels": "bell,phone"})
        self.assertFalse(self.mpv_log.exists())
        self.assertFalse(self.curl_log.exists())
        self.assertIn("codex\tbell,phone", self.notify_history.read_text())

    def test_gates_and_preferences_still_apply_before_relay(self):
        dotenv = f"CLUSTERFORK_NOTIFY_RELAY_SOCKET={self.socket}\n"
        for agent, payload in (("antigravity", '{"fullyIdle":false}'),
                               ("grok", '{"reason":"shutdown"}')):
            self.assertEqual(self.run_notifier(agent, dotenv=dotenv, stdin=payload).returncode, 0)
        self.write_prefs("codex=0\n")
        self.assertEqual(self.run_notifier(dotenv=dotenv).returncode, 0)
        self.listener.settimeout(0.05)
        with self.assertRaises(TimeoutError):
            self.listener.accept()
        self.assertFalse(self.notify_history.exists())

    def test_host_preferences_and_channel_subset_are_respected(self):
        desktop = self.root / "desktop"
        self._write_mock("notify-send", f"printf '%s\\n' \"$@\" >'{desktop}'\n")
        self.env_file.write_text("CLUSTERFORK_NTFY_URL=http://127.0.0.1:2586/clusterfork\n")
        self.write_prefs("phone=0\nvolume=60\n")
        result = subprocess.run([str(NOTIFIER), "--from-devbox", "antigravity", "demo", "bell,phone"],
                                env=self.env(), capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--volume=60", self.mpv_log.read_text())
        self.assertFalse(desktop.exists())
        self.assertFalse(self.curl_log.exists())
        self.mpv_log.unlink()
        result = subprocess.run([str(NOTIFIER), "--from-devbox", "codex", "demo", "phone"],
                                env=self.env(), capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertFalse(self.mpv_log.exists())
        self.assertFalse(desktop.exists())

    def test_missing_relay_is_bounded_silent_and_not_logged_as_success(self):
        proc = self.run_notifier(dotenv=f"CLUSTERFORK_NOTIFY_RELAY_SOCKET={self.root}/missing\n")
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))
        self.assertFalse(self.notify_history.exists())

    def test_manual_test_reports_missing_relay_as_failure(self):
        proc = self.run_cli("test", "bell", dotenv=f"CLUSTERFORK_NOTIFY_RELAY_SOCKET={self.root}/missing\n")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("request failed", proc.stderr)
        self.assertFalse(self.mpv_log.exists())
        self.assertFalse(self.notify_history.exists())


if __name__ == "__main__":
    unittest.main()
