"""Quota-driven rotation with synthetic credentials and mocked provider requests."""

import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "automation")]
import rotate_auth
import rotate_usage
import account_usage
from common.selection import QuotaWindow, select_account

NOW = 1_800_000_000


def credentials(provider, name):
    payload = base64.urlsafe_b64encode(json.dumps({"sub": name}).encode()).decode().rstrip("=")
    access = f"fixture.{payload}.signature"
    if provider == "codex":
        return {"auth_mode": "chatgpt", "tokens": {"account_id": name, "access_token": access, "refresh_token": name}}
    if provider == "cursor":
        return {"accessToken": access, "refreshToken": name}
    if provider == "claude":
        return {"claudeAiOauth": {"accessToken": access}}
    if provider == "grok":
        return {"grok": {"key": access, "email": name}}
    if provider == "opencode":
        return {"opencode-go": {"type": "api", "key": access}}
    return {"token": {"access_token": access}}


class QuotaRotationTests(unittest.TestCase):
    PROVIDERS = ("claude", "codex", "cursor", "grok", "opencode", "antigravity")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.keyring = {}
        env = {"ROTATE_CLAUDE_DIR": str(self.root / "claude"),
               "ROTATE_ANTIGRAVITY_STATE_DIR": str(self.root / "antigravity")}
        for provider, agent, store in (
            ("codex", "ROTATE_CODEX_CODEX_DIR", "ROTATE_CODEX_AUTH_STORE_DIR"),
            ("cursor", "ROTATE_CURSOR_DIR", "ROTATE_CURSOR_AUTH_STORE_DIR"),
            ("grok", "ROTATE_GROK_DIR", "ROTATE_GROK_AUTH_STORE_DIR"),
            ("opencode", "ROTATE_OPENCODE_DIR", "ROTATE_OPENCODE_AUTH_STORE_DIR"),
        ):
            env[agent], env[store] = str(self.root / provider / "agent"), str(self.root / provider / "store")
        keyring = self.keyring
        for context in (
            patch.dict(os.environ, env), patch("sys.stdout", new=io.StringIO()), patch("sys.stderr", new=io.StringIO()),
            patch.object(rotate_usage.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")),
            patch.object(rotate_auth.AntigravityBackend, "_require_secret_tool"),
            patch.object(rotate_auth.AntigravityBackend, "_lookup", lambda self, service, user: keyring.get((service, user))),
            patch.object(rotate_auth.AntigravityBackend, "_store", lambda self, label, service, user, secret: keyring.__setitem__((service, user), secret)),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.backends = {}
        for provider in self.PROVIDERS:
            backend = self.backends[provider] = rotate_auth.make_backend(provider)
            for name in ("alpha", "beta", "zulu"):
                self.write_profile(provider, name, credentials(provider, name))
            backend.select("alpha")

    def write_profile(self, provider, name, auth):
        backend = self.backends[provider]
        if provider == "antigravity":
            backend._prepare_state()
            backend._add_profile(name)
            self.keyring[(backend.profile_service, name)] = json.dumps(auth).encode()
        else:
            path = (backend.claude_dir / (".credentials.json." + name) if provider == "claude"
                    else backend.store_dir / ("auth.json." + name))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(auth))

    def state(self):
        return ({str(path.relative_to(self.root)): (os.readlink(path) if path.is_symlink() else path.read_bytes())
                 for path in self.root.rglob("*") if path.is_symlink() or path.is_file()}, dict(self.keyring))

    def reader(self, provider, used=None):
        def read(auth, model):
            account = rotate_usage.identity(provider, auth)
            values = {rotate_usage.identity(provider, credentials(provider, name)): value
                      for name, value in (used or {"alpha": 50, "beta": 30, "zulu": 10}).items()}
            # No profile is temporarily activated while another account is inspected.
            self.assertEqual(self.backends[provider].current_suffix(), "alpha")
            return (QuotaWindow(values[account], time.time() + 3600),)
        return Mock(side_effect=read)

    def test_every_bare_rotate_selects_lowest_usage_instead_of_next_name(self):
        for provider, backend in self.backends.items():
            with self.subTest(provider=provider):
                reader = self.reader(provider)
                with patch.dict(account_usage.READERS, {provider: reader}):
                    self.assertEqual(rotate_auth.run([provider]), 0)
                self.assertEqual(backend.current_suffix(), "zulu")
                self.assertEqual(reader.call_count, 3)

    def test_explicit_selection_is_offline_for_every_provider(self):
        with patch.object(account_usage, "request_json", side_effect=AssertionError("unexpected quota request")):
            for provider, backend in self.backends.items():
                with self.subTest(provider=provider):
                    self.assertEqual(rotate_auth.run([provider, "beta"]), 0)
                    self.assertEqual(backend.current_suffix(), "beta")

    def test_no_eligible_account_preserves_all_credentials_links_and_markers(self):
        before = self.state()
        for provider in self.PROVIDERS:
            for values in ({"alpha": 81, "beta": 100, "zulu": None}, {"alpha": None, "beta": None, "zulu": None}):
                with self.subTest(provider=provider, values=values):
                    with patch.dict(account_usage.READERS, {provider: self.reader(provider, values)}):
                        with self.assertRaisesRegex(rotate_auth.RotateError, "no account has fresh quota"):
                            rotate_auth.run([provider])
                    self.assertEqual(self.state(), before)

    def test_current_account_can_remain_selected_when_it_is_best(self):
        for provider, backend in self.backends.items():
            with self.subTest(provider=provider):
                before = self.state()
                with patch.dict(account_usage.READERS, {provider: self.reader(provider, {"alpha": 0, "beta": 20, "zulu": 30})}):
                    backend.select(None)
                self.assertEqual(backend.current_suffix(), "alpha")
                self.assertEqual(self.state(), before)

    def test_aliases_share_one_quota_probe_and_preserve_current_alias(self):
        for provider, backend in self.backends.items():
            with self.subTest(provider=provider):
                self.write_profile(provider, "beta", credentials(provider, "alpha"))
                reader = self.reader(provider, {"alpha": 0, "zulu": 50})
                with patch.dict(account_usage.READERS, {provider: reader}):
                    backend.select(None)
                self.assertEqual(reader.call_count, 2)
                self.assertEqual(backend.current_suffix(), "alpha")

    def test_bad_credentials_do_not_hide_other_healthy_accounts(self):
        for provider, backend in self.backends.items():
            with self.subTest(provider=provider):
                self.write_profile(provider, "beta", {})
                reader = self.reader(provider)
                with patch.dict(account_usage.READERS, {provider: reader}):
                    backend.select(None)
                self.assertEqual(backend.current_suffix(), "zulu")
                self.assertEqual(reader.call_count, 2)

    def test_expired_current_login_can_fall_back_to_another_login_for_the_same_account(self):
        provider = "codex"
        backend = self.backends[provider]
        valid = credentials(provider, "alpha")
        valid["tokens"]["access_token"] = "independent-local-login"
        self.write_profile(provider, "beta", valid)
        def windows(auth, model):
            if auth["tokens"]["access_token"] == credentials(provider, "alpha")["tokens"]["access_token"]:
                raise account_usage.UsageError("saved login expired or was rejected")
            return (QuotaWindow(10 if auth == valid else 50),)
        with patch.dict(account_usage.READERS, {provider: windows}):
            backend.select(None)
        self.assertEqual(backend.current_suffix(), "beta")

    def test_duplicate_alias_of_expired_current_login_is_not_probed_again(self):
        backend = self.backends["codex"]
        self.write_profile("codex", "beta", credentials("codex", "alpha"))
        backend.select("beta")
        reader = Mock(side_effect=account_usage.UsageError("saved login expired or was rejected"))
        with patch.dict(account_usage.READERS, {"codex": reader}):
            with self.assertRaises(rotate_auth.RotateError):
                backend.select(None)
        self.assertEqual(reader.call_count, 2)  # alpha/beta share a login; zulu is separate.
        self.assertEqual(backend.current_suffix(), "beta")

    def test_automatic_selection_uses_only_eligible_local_profiles(self):
        for provider in ("codex", "cursor"):
            backend = self.backends[provider]
            identity = rotate_usage.identity(provider, credentials(provider, "beta"))
            (backend.store_dir / rotate_auth.REMOTE_MANIFEST).write_text(json.dumps({
                "remote:beta": {"account_id": identity, "ownership": "cloud", "display_name": "beta"}}))
            reader = self.reader(provider)
            with self.subTest(provider=provider), patch.dict(account_usage.READERS, {provider: reader}):
                rotate_auth.run([provider, "--local"])
            self.assertEqual(backend.current_suffix(), "zulu")
            self.assertEqual(reader.call_count, 2)

    def test_codex_weekly_ranking_and_seventy_percent_boundary_match_symphony(self):
        backend = self.backends["codex"]
        for short, expected in ((69.999, "alpha"), (70, "beta"), (81, "beta")):
            def windows(auth, model):
                name = auth["tokens"]["account_id"]
                used = {"alpha": (short, 10), "beta": (20, 30), "zulu": (0, 95)}[name]
                return tuple(QuotaWindow(value, time.time() + 3600, duration_minutes=duration)
                             for value, duration in zip(used, (300, 10080)))
            with self.subTest(short=short), patch.dict(account_usage.READERS, {"codex": windows}):
                backend.select(None)
                self.assertEqual(backend.current_suffix(), expected)

    def test_single_saved_profile_can_be_selected(self):
        for provider, backend in self.backends.items():
            with self.subTest(provider=provider):
                with patch.dict(account_usage.READERS, {provider: self.reader(provider)}):
                    chosen = rotate_usage.choose_profile(backend, ["alpha"])
                self.assertEqual(chosen, "alpha")

    def test_kian_account_is_excluded_from_automatic_selection(self):
        backend = self.backends["codex"]
        self.write_profile("codex", "kian", credentials("codex", "kian"))
        reader = self.reader("codex", {"kian": 0, "alpha": 50, "beta": 30, "zulu": 40})
        with patch.dict(account_usage.READERS, {"codex": reader}):
            backend.select(None)
        self.assertEqual(backend.current_suffix(), "beta")
        self.assertEqual(rotate_auth.run(["codex", "kian"]), 0)
        self.assertEqual(backend.current_suffix(), "kian")

    def test_codex_skips_accounts_in_live_panes_and_chooses_best_unused_account(self):
        panes = subprocess.CompletedProcess([], 0, "0\tcodex\tcodex - zulu\n", "")
        reader = self.reader("codex")
        with patch.object(rotate_usage.subprocess, "run", return_value=panes), patch.dict(account_usage.READERS, {"codex": reader}):
            rotate_auth.run(["codex"])
        self.assertEqual(self.backends["codex"].current_suffix(), "beta")
        self.assertEqual(reader.call_count, 2)

    def test_codex_busy_alias_blocks_every_login_for_that_account(self):
        self.write_profile("codex", "beta", credentials("codex", "alpha"))
        panes = subprocess.CompletedProcess([], 0, "0\tcodex\tcodex - beta\n", "")
        reader = self.reader("codex", {"alpha": 0, "zulu": 50})
        with patch.object(rotate_usage.subprocess, "run", return_value=panes), patch.dict(account_usage.READERS, {"codex": reader}):
            rotate_auth.run(["codex"])
        self.assertEqual(self.backends["codex"].current_suffix(), "zulu")
        self.assertEqual(reader.call_count, 1)

    def test_all_codex_accounts_busy_preserves_selection_without_probing(self):
        panes = subprocess.CompletedProcess([], 0, "".join(f"0\tcodex\tcodex - {name}\n" for name in ("alpha", "beta", "zulu")), "")
        before = self.state()
        reader = Mock(side_effect=AssertionError("busy accounts must not be probed"))
        with patch.object(rotate_usage.subprocess, "run", return_value=panes), patch.dict(account_usage.READERS, {"codex": reader}):
            with self.assertRaisesRegex(rotate_auth.RotateError, "already in use"):
                rotate_auth.run(["codex"])
        self.assertEqual(self.state(), before)
        reader.assert_not_called()

    def test_explicit_codex_selection_can_still_choose_busy_account_offline(self):
        with patch.object(rotate_usage.subprocess, "run", side_effect=AssertionError("explicit selection must not inspect tmux")):
            self.assertEqual(rotate_auth.run(["codex", "beta"]), 0)
        self.assertEqual(self.backends["codex"].current_suffix(), "beta")

    def test_busy_codex_detection_does_not_affect_other_providers(self):
        for provider in self.PROVIDERS:
            if provider == "codex":
                continue
            with self.subTest(provider=provider), patch.object(rotate_usage.subprocess, "run", side_effect=AssertionError("only Codex should inspect tmux")), patch.dict(account_usage.READERS, {provider: self.reader(provider)}):
                rotate_auth.run([provider])
            self.assertEqual(self.backends[provider].current_suffix(), "zulu")

    def test_cache_snapshots_are_used_when_fresh(self):
        backend = self.backends["codex"]
        cache_dir = self.root / "conky-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        accounts = []
        for name, used in (("alpha", 40), ("beta", 15), ("zulu", 60)):
            ident = rotate_usage.identity("codex", credentials("codex", name))
            accounts.append({
                "accountId": ident,
                "ok": True,
                "staleCache": False,
                "observedAt": now - 30,
                "windows": [
                    {"label": "5h", "usedPercent": used, "windowSeconds": 18000, "resetAtEpoch": now + 3600},
                    {"label": "weekly", "usedPercent": used, "windowSeconds": 604800, "resetAtEpoch": now + 86400},
                ],
            })
        (cache_dir / "codex-usage.json").write_text(json.dumps({"accounts": accounts}))
        reader = Mock(side_effect=AssertionError("network should not be called when cache is fresh"))
        with patch.dict(os.environ, {"CONKY_CACHE_DIR": str(cache_dir)}), patch.dict(account_usage.READERS, {"codex": reader}):
            backend.select(None)
        self.assertEqual(backend.current_suffix(), "beta")
        self.assertEqual(reader.call_count, 0)

        # Busy filtering must also run before choosing from the fast cache path.
        panes = subprocess.CompletedProcess([], 0, "0\tcodex\tcodex - beta\n", "")
        with patch.dict(os.environ, {"CONKY_CACHE_DIR": str(cache_dir)}), patch.object(rotate_usage.subprocess, "run", return_value=panes), patch.dict(account_usage.READERS, {"codex": reader}):
            backend.select(None)
        self.assertEqual(backend.current_suffix(), "alpha")
        reader.assert_not_called()

    def test_cache_fallback_to_network_when_stale_or_incomplete(self):
        backend = self.backends["codex"]
        cache_dir = self.root / "conky-cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        accounts = [{
            "accountId": rotate_usage.identity("codex", credentials("codex", "alpha")),
            "ok": True,
            "staleCache": False,
            "observedAt": now - 500,
            "windows": [{"label": "5h", "usedPercent": 10, "windowSeconds": 18000, "resetAtEpoch": now + 3600}],
        }]
        (cache_dir / "codex-usage.json").write_text(json.dumps({"accounts": accounts}))
        reader = self.reader("codex", {"alpha": 50, "beta": 20, "zulu": 60})
        with patch.dict(os.environ, {"CONKY_CACHE_DIR": str(cache_dir)}), patch.dict(account_usage.READERS, {"codex": reader}):
            backend.select(None)
        self.assertEqual(backend.current_suffix(), "beta")
        self.assertEqual(reader.call_count, 3)


class CodexInUseTests(unittest.TestCase):
    def test_only_live_codex_panes_with_account_labels_count(self):
        panes = "\n".join((
            "0\tcodex\tcodex - alpha",
            "0\tcodex\tcodex - alpha",  # A linked window or another session on the same account.
            "0\tcodex\tcodex - other.name_2-3",
            "1\tcodex\tcodex - dead",
            "0\tbash\tcodex - shell",
            "0\tclaude\tclaude - another-provider",
            "0\tcodex\tmanually renamed",
            "malformed",
        ))
        result = subprocess.CompletedProcess([], 0, panes, "")
        with patch.object(rotate_usage.subprocess, "run", return_value=result) as run:
            self.assertEqual(rotate_usage.codex_profiles_in_use(), {"alpha", "other.name_2-3"})
        # -a includes detached sessions; query reads metadata without capturing pane contents.
        self.assertEqual(run.call_args.args[0][:3], ["tmux", "list-panes", "-a"])

    def test_missing_tmux_no_server_and_timeout_leave_quota_selection_available(self):
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired("tmux", 2)):
            with self.subTest(failure=type(failure).__name__), patch.object(rotate_usage.subprocess, "run", side_effect=failure):
                self.assertEqual(rotate_usage.codex_profiles_in_use(), set())
        with patch.object(rotate_usage.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "no server")):
            self.assertEqual(rotate_usage.codex_profiles_in_use(), set())


class ProviderQuotaTests(unittest.TestCase):
    def read(self, provider, payload):
        profile, policy = rotate_usage.selection_config(provider)
        with patch.object(account_usage, "request_json", return_value=payload), patch("time.time", return_value=NOW):
            snapshot, error = rotate_usage.measure(provider, credentials(provider, "fixture"), "account", profile.model)
        self.assertIsNone(error)
        return snapshot, select_account([snapshot], {provider: profile}, policy, now=NOW)

    def test_codex_preserves_both_windows_and_durations(self):
        snapshot, selected = self.read("codex", {"rate_limit": {
            "primary_window": {"used_percent": 65, "reset_at": NOW + 1000, "limit_window_seconds": 18000},
            "secondary_window": {"used_percent": 10, "reset_at": NOW + 10000, "limit_window_seconds": 604800}}})
        self.assertEqual([w.duration_minutes for w in snapshot.windows], [300, 10080])
        self.assertEqual(selected.remaining_percent, 35)

    def test_claude_retains_model_specific_weekly_limits(self):
        snapshot, selected = self.read("claude", {"five_hour": {"utilization": 10}, "seven_day": {"utilization": 20},
            "seven_day_opus": {"utilization": 95}, "seven_day_sonnet": None, "extra_usage": {"is_enabled": True}})
        self.assertEqual(len(snapshot.windows), 3)
        self.assertIsNone(selected)

    def test_cursor_uses_applicable_auto_bucket_and_included_cap(self):
        _, selected = self.read("cursor", {"enabled": True, "autoBucketModels": ["grok-4.7-high"],
            "billingCycleStart": (NOW - 1000) * 1000, "billingCycleEnd": (NOW + 10000) * 1000,
            "planUsage": {"autoPercentUsed": 10, "apiPercentUsed": 99, "totalPercentUsed": 40}})
        self.assertEqual(selected.remaining_percent, 60)

    def test_cursor_on_demand_balance_cannot_replace_unknown_included_usage(self):
        _, selected = self.read("cursor", {"enabled": True, "billingCycleStart": (NOW - 1000) * 1000,
            "billingCycleEnd": (NOW + 10000) * 1000, "planUsage": {}, "onDemandUsage": {"limit": 1000}})
        self.assertIsNone(selected)

    def test_grok_uses_measured_credit_usage_and_billing_reset(self):
        for config in ({"used": {"val": 1500}, "monthlyLimit": {"val": 15000}}, {"creditUsagePercent": 10}):
            config["currentPeriod"] = {"start": NOW - 1000, "end": NOW + 10000}
            snapshot, selected = self.read("grok", {"config": config})
            self.assertEqual(selected.remaining_percent, 90)
            self.assertEqual(snapshot.windows[0].resets_at, NOW + 10000)

    def test_opencode_requires_all_three_go_windows(self):
        payload = {"usage": {name: {"status": "ok", "percent": used, "resetsAt": NOW + 10000}
                             for name, used in (("rolling", 10), ("weekly", 20), ("monthly", 35))}}
        snapshot, selected = self.read("opencode", payload)
        self.assertEqual(len(snapshot.windows), 3)
        self.assertEqual(selected.remaining_percent, 65)
        del payload["usage"]["monthly"]
        with patch.object(account_usage, "request_json", return_value=payload):
            snapshot, error = rotate_usage.measure("opencode", credentials("opencode", "fixture"), "a", "")
        self.assertIsNone(snapshot)
        self.assertIn("invalid", error)

    def test_antigravity_preserves_every_bucket_without_averaging_exhaustion_away(self):
        responses = [{"cloudaicompanionProject": "fixture"}, {"groups": [{"buckets": [
            {"bucketId": "gemini", "remainingFraction": 1, "resetTime": NOW + 1000},
            {"bucketId": "argon", "remainingFraction": 0, "resetTime": NOW + 10000}]}]}]
        with patch.object(account_usage, "request_json", side_effect=responses):
            windows = account_usage.antigravity(credentials("antigravity", "fixture"), "")
        self.assertEqual([window.used_percent for window in windows], [0, 100])

    def test_antigravity_falls_back_to_raw_quota_buckets_and_keeps_unknown_usage(self):
        responses = [{"cloudaicompanionProject": "fixture"}, {}, {"buckets": [
            {"modelId": "gemini", "remainingFraction": 0.75, "resetTime": NOW + 1000},
            {"modelId": "argon", "resetTime": NOW + 1000}]}]
        with patch.object(account_usage, "request_json", side_effect=responses):
            windows = account_usage.antigravity(credentials("antigravity", "fixture"), "")
        self.assertEqual([window.used_percent for window in windows], [25, None])

    def test_antigravity_uses_raw_endpoint_when_summary_is_unsupported(self):
        responses = [{"cloudaicompanionProject": "fixture"}, account_usage.UsageError("unsupported", 404),
                     {"buckets": [{"modelId": "gemini", "remainingFraction": 0.75, "resetTime": NOW + 1000}]}]
        with patch.object(account_usage, "request_json", side_effect=responses) as request:
            windows = account_usage.antigravity(credentials("antigravity", "fixture"), "")
        self.assertTrue(request.call_args.args[0].endswith(":retrieveUserQuota"))
        self.assertEqual(windows[0].used_percent, 25)

    def test_authentication_failure_does_not_probe_other_endpoints(self):
        responses = [{"cloudaicompanionProject": "fixture"}, account_usage.UsageError("rejected", 401)]
        with patch.object(account_usage, "request_json", side_effect=responses) as request:
            with self.assertRaises(account_usage.UsageError):
                account_usage.antigravity(credentials("antigravity", "fixture"), "")
        self.assertEqual(request.call_count, 2)

    def test_stale_reset_invalid_and_unknown_readings_are_ineligible(self):
        for value in (None, True, float("nan"), -1, 101):
            with self.subTest(value=value):
                _, selected = self.read("claude", {"five_hour": {"utilization": value}})
                self.assertIsNone(selected)
        _, selected = self.read("claude", {"five_hour": {"utilization": 0, "resets_at": NOW - 1}})
        self.assertIsNone(selected)

    def test_http_failures_do_not_expose_credentials_or_provider_bodies(self):
        error = HTTPError("https://fixture", 401, "private token", {}, io.BytesIO(b"private response"))
        with patch.object(account_usage, "urlopen", side_effect=error):
            with self.assertRaisesRegex(account_usage.UsageError, "expired or was rejected") as raised:
                account_usage.request_json("https://fixture", "private token")
        self.assertNotIn("private", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
