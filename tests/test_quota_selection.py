"""Quota selection behavior with fixed models and multiple independent accounts."""

from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation" / "azure"))
from dispatch_core import Registry  # noqa: E402
from quota_selection import AccountQuota, QuotaWindow, select_account  # noqa: E402

NOW = 1_800_000_000


class QuotaSelectionTests(unittest.TestCase):
    def setUp(self):
        config = Registry.load(ROOT / "automation" / "azure" / "projects.json")
        self.profiles, self.policy = config.agent_profiles, config.agent_selection

    def account(self, account_id, agent="codex", used=(10,), **kwargs):
        return AccountQuota(account_id, agent, self.profiles[agent].model, NOW,
                            tuple(QuotaWindow(value, NOW + 3600) for value in used), **kwargs)

    def choose(self, accounts):
        return select_account(accounts, self.profiles, self.policy, now=NOW)

    def test_selects_least_used_codex_account_with_sol_max(self):
        result = self.choose([self.account("codex-a", used=(50,)),
                              self.account("codex-b", used=(30,)),
                              self.account("codex-c", used=(15,))])
        self.assertEqual(result.account_id, "codex-c")
        self.assertEqual(result.model, "gpt-6.1-sol")
        self.assertEqual(result.effort, "max")

    def test_weekly_limit_constrains_account_with_healthy_short_window(self):
        result = self.choose([self.account("weekly-low", used=(2, 95)),
                              self.account("healthy", used=(35, 45))])
        self.assertEqual(result.account_id, "healthy")
        self.assertEqual(result.remaining_percent, 55)
        self.assertEqual(result.effort, "max")

    def test_busy_and_rate_limited_accounts_are_excluded(self):
        result = self.choose([self.account("busy", used=(0,), in_use=True),
                              self.account("blocked", used=(0,), blocked_until=NOW + 10),
                              self.account("available", used=(40,))])
        self.assertEqual(result.account_id, "available")

    def test_waits_when_every_account_is_near_exhaustion(self):
        self.assertIsNone(self.choose([self.account("low", used=(81,)),
                                      self.account("empty", used=(100,))]))

    def test_missing_stale_or_reset_data_is_not_assumed_to_mean_zero_usage(self):
        base = self.account("test")
        for row in [replace(base, windows=()), replace(base, observed_at=NOW - 61),
                    replace(base, observed_at=NOW + 1),
                    replace(base, windows=(QuotaWindow(10, NOW - 1),)),
                    replace(base, windows=(QuotaWindow(float("nan")),))]:
            with self.subTest(row=row):
                self.assertIsNone(self.choose([row]))

    def test_wrong_model_and_unknown_provider_never_trigger_a_substitute(self):
        base = self.account("test")
        for row in [replace(base, model="gpt-6.1-terra"), replace(base, agent="command-code"),
                    replace(base, agent="antigravity")]:
            with self.subTest(row=row):
                self.assertIsNone(self.choose([row]))


if __name__ == "__main__":
    unittest.main()
