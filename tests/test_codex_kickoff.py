"""Cache-only kickoff decisions and the existing account-preserving ping path."""

import copy
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
loader = importlib.machinery.SourceFileLoader("codex_kickoff", str(ROOT / "bin/clusterfork-codex-kickoff"))
spec = importlib.util.spec_from_loader(loader.name, loader)
kickoff = importlib.util.module_from_spec(spec)
loader.exec_module(kickoff)
import rotate_auth
import rotate_usage

NOW = 1_800_000_000


def window(used=0, reset=0, seconds=18000):
    return {"usedPercent": used, "resetAtEpoch": reset, "windowSeconds": seconds}


def row():
    return {"ok": True, "staleCache": False, "observedAt": NOW,
            "windows": [window(), window(20, NOW + 86400, 604800)]}


class KickoffDecisionTests(unittest.TestCase):
    def reason(self, value):
        return kickoff.skip_reason(value, now=NOW, max_age_seconds=300)

    def test_unused_window_without_pending_reset_is_eligible(self):
        for reset in (0, None, NOW - 1, NOW):
            value = row()
            value["windows"][0]["resetAtEpoch"] = reset
            with self.subTest(reset=reset):
                self.assertIsNone(self.reason(value))

    def test_zero_percent_with_pending_reset_already_started(self):
        for used in (0, 0.1, 50, 99):
            value = row()
            value["windows"][0] = window(used, NOW + 3600)
            with self.subTest(used=used):
                self.assertEqual(self.reason(value), "five-hour window already started")

    def test_moving_full_span_reset_is_unused_even_when_cache_is_minutes_old(self):
        for age in (0, 61, 120, 300):
            for offset in (-1, 0, 1):
                value = row()
                value["observedAt"] = NOW - age
                value["windows"][0] = window(0, NOW - age + 18000 + offset)
                with self.subTest(age=age, offset=offset):
                    self.assertIsNone(self.reason(value))

    def test_provider_full_countdown_handles_observation_before_http_completion(self):
        value = row()
        value["windows"][0] = {**window(0, NOW + 18003), "resetAfterSeconds": 18000}
        self.assertIsNone(self.reason(value))
        # A just-started request can still round down to zero; its countdown
        # takes precedence over the approximate reset minus observation time.
        value["windows"][0].update(resetAtEpoch=NOW + 18000, resetAfterSeconds=17999)
        self.assertEqual(self.reason(value), "five-hour window already started")
        for invalid in (-1, 18001, True, "18000", float("nan")):
            value["windows"][0]["resetAfterSeconds"] = invalid
            with self.subTest(invalid=invalid):
                self.assertIsNotNone(self.reason(value))
        value["windows"][0].update(usedPercent=1, resetAfterSeconds=18000)
        self.assertEqual(self.reason(value), "five-hour window already started")

    def test_shorter_source_freshness_limit_is_honored(self):
        value = {**row(), "observedAt": NOW - 61, "maxAgeSeconds": 60}
        self.assertEqual(self.reason(value), "stale or invalid usage cache")
        for age in (None, 0, -1, True, "300", float("inf")):
            value["maxAgeSeconds"] = age
            with self.subTest(age=age):
                self.assertIsNotNone(self.reason(value))

    def test_iso_reset_is_honored(self):
        value = row()
        value["windows"][0] = {"usedPercent": 0, "windowSeconds": 18000,
                               "resetsAt": "2027-01-15T09:00:00+00:00"}
        self.assertEqual(self.reason(value), "five-hour window already started")
        value["windows"][0]["resetsAt"] = None
        self.assertIsNone(self.reason(value))

    def test_invalid_stale_failed_exhausted_and_incomplete_rows_are_skipped(self):
        cases = [None, {}, {**row(), "ok": False}, {**row(), "staleCache": True},
                 {**row(), "observedAt": NOW - 301}, {**row(), "observedAt": NOW + 1},
                 {**row(), "observedAt": float("nan")}, {**row(), "windows": []},
                 {**row(), "windows": [None]},
                 {**row(), "windows": [window(0, 0, 604800)]},
                 {**row(), "windows": [window(), window()]},
                 {**row(), "windows": [window(), window(100, NOW + 3600, 604800)]},
                 {**row(), "windows": [window(), window(20, NOW - 1, 604800)]}]
        for update in ({"usedPercent": None}, {"usedPercent": True}, {"usedPercent": -1},
                       {"usedPercent": 101}, {"usedPercent": float("nan")},
                       {"resetAtEpoch": "bad"}, {"resetAtEpoch": True},
                       {"resetAtEpoch": -1}, {"resetAtEpoch": float("inf")},
                       {"windowSeconds": None}):
            value = row()
            value["windows"][0].update(update)
            cases.append(value)
        missing_reset = row()
        del missing_reset["windows"][0]["resetAtEpoch"]
        cases.append(missing_reset)
        for value in cases:
            with self.subTest(value=value):
                self.assertIsNotNone(self.reason(value))


class KickoffRunTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.cache = self.home / "conky-linear-HUP/cache"
        self.cache.mkdir(parents=True)
        self.env = {"HOME": str(self.home), "CONKY_CACHE_DIR": "", "CLUSTERFORK_USAGE_CACHE_DIR": "",
                    "ROTATE_CODEX_AUTH_STORE_DIR": "", "ROTATE_CODEX_CODEX_DIR": ""}
        for context in (patch.dict(os.environ, self.env), patch.object(kickoff.time, "time", return_value=NOW),
                        patch("sys.stdout", new=io.StringIO()),
                        patch("account_usage.request_json", side_effect=AssertionError("must not probe quota"))):
            context.start()
            self.addCleanup(context.stop)
        self.backend = rotate_auth.make_backend("codex")
        self.backend.store_dir.mkdir(parents=True)
        self.write_profile("active")
        self.backend.select("active")

    def write_profile(self, name, account=None):
        auth = {"auth_mode": "chatgpt", "tokens": {"account_id": account or name,
                "access_token": "fake-access", "refresh_token": "fake-refresh"}}
        (self.backend.store_dir / f"auth.json.{name}").write_text(json.dumps(auth))
        return rotate_usage.identity("codex", auth)

    def write_cache(self, values):
        accounts = []
        for name, value in values.items():
            value = copy.deepcopy(value)
            auth = json.loads((self.backend.store_dir / f"auth.json.{name}").read_text())
            value["accountId"] = rotate_usage.identity("codex", auth)
            accounts.append(value)
        (self.cache / "codex-usage.json").write_text(json.dumps({"accounts": accounts}))

    def test_only_unstarted_local_accounts_ping_once_and_selection_is_restored(self):
        # Scheduled kickoff includes personal accounts even though bare rotation excludes them.
        for name in ("alias", "used", "rounded", "kian", "cloud", "invalid"):
            self.write_profile(name, "active" if name == "alias" else None)
        (self.backend.store_dir / "auth.json.invalid").write_text("invalid JSON")
        cloud_id = rotate_usage.identity("codex", json.loads(
            (self.backend.store_dir / "auth.json.cloud").read_text()))
        (self.backend.store_dir / rotate_auth.REMOTE_MANIFEST).write_text(json.dumps({
            "remote:cloud": {"ownership": "cloud", "account_id": cloud_id, "display_name": "cloud"}}))
        used, rounded = row(), row()
        used["windows"][0] = window(10, NOW + 100)
        rounded["windows"][0] = window(0, NOW + 100)
        unused_kian = row()
        unused_kian["observedAt"] = NOW - 120
        unused_kian["windows"][0] = {**window(0, NOW - 120 + 18002), "resetAfterSeconds": 18000}
        self.write_cache({"active": row(), "used": used, "rounded": rounded,
                          "kian": unused_kian, "cloud": row()})
        pinged = []

        def ping(command):
            pinged.append((command, self.backend.current_suffix()))
            return True, "ok"

        with patch.object(rotate_auth, "ping_agent", side_effect=ping):
            self.assertEqual(kickoff.run(), 0)
        self.assertEqual(pinged, [("rotate-codex", "active"), ("rotate-codex", "kian")])
        self.assertEqual(self.backend.current_suffix(), "active")
        self.assertTrue(self.backend.auth.is_symlink())

    def test_failed_ping_continues_and_preserves_refreshed_credentials(self):
        self.write_profile("beta")
        self.write_cache({"active": row(), "beta": row()})
        pinged = []

        def ping(command):
            name = self.backend.current_suffix()
            pinged.append(name)
            auth = json.loads(self.backend.auth.read_text())
            auth["tokens"]["refresh_token"] = "refreshed-" + name
            self.backend.auth.unlink()
            self.backend.auth.write_text(json.dumps(auth))
            return name == "beta", "fixture failure"

        with patch.object(rotate_auth, "ping_agent", side_effect=ping):
            self.assertEqual(kickoff.run(), 1)
        self.assertEqual(pinged, ["active", "beta"])
        self.assertEqual(self.backend.current_suffix(), "active")
        self.assertTrue(self.backend.auth.is_symlink())
        for name in pinged:
            auth = json.loads((self.backend.store_dir / f"auth.json.{name}").read_text())
            self.assertEqual(auth["tokens"]["refresh_token"], "refreshed-" + name)

    def test_dry_run_and_missing_stale_or_invalid_cache_do_not_ping(self):
        with patch.object(rotate_auth, "ping_agent") as ping:
            self.assertEqual(kickoff.run(), 0)
            self.write_cache({"active": row()})
            self.assertEqual(kickoff.run(dry_run=True), 0)
            stale = {**row(), "observedAt": NOW - 301}
            self.write_cache({"active": stale})
            self.assertEqual(kickoff.run(), 0)
            (self.cache / "codex-usage.json").write_text("bad JSON")
            self.assertEqual(kickoff.run(), 0)
        ping.assert_not_called()
        self.assertEqual(self.backend.current_suffix(), "active")

    def test_cache_overrides_share_rotation_precedence(self):
        self.write_cache({"active": row()})
        override = self.home / "override"
        override.mkdir()
        (override / "codex-usage.json").write_text('{"accounts": []}')
        for variable in ("CONKY_CACHE_DIR", "CLUSTERFORK_USAGE_CACHE_DIR"):
            with self.subTest(variable=variable), patch.dict(os.environ, {variable: str(override)}), \
                    patch.object(rotate_auth, "ping_agent") as ping:
                self.assertEqual(kickoff.run(), 0)
                ping.assert_not_called()
        with patch.dict(os.environ, {"CONKY_CACHE_DIR": str(override),
                                    "CLUSTERFORK_USAGE_CACHE_DIR": str(self.cache)}), \
                patch.object(rotate_auth, "ping_agent") as ping:
            self.assertEqual(kickoff.run(), 0)
            ping.assert_not_called()


if __name__ == "__main__":
    unittest.main()
