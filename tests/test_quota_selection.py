"""Quota selection behavior with fixed models and multiple independent accounts."""

from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
from common.routing import Registry  # noqa: E402
from common.selection import AccountQuota, QuotaWindow, select_account  # noqa: E402

NOW = 1_800_000_000


class QuotaSelectionTests(unittest.TestCase):
    def setUp(self):
        config = Registry.load(ROOT / "automation" / "controller" / "projects.json")
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

    def test_provider_precedence_is_stronger_than_quota_and_account_order(self):
        cursor = self.account("cursor-first", "cursor", used=(0,))
        codex = self.account("codex-last", used=(75,))
        for accounts in ([cursor, codex], [codex, cursor]):
            self.assertEqual(self.choose(accounts).account_id, "codex-last")

    def test_eligible_codex_replacements_win_before_cursor_with_weekly_ranking(self):
        cursor = self.account("cursor", "cursor", used=(0,))
        exhausted = self.account("exhausted", used=(100,))
        self.assertEqual(self.choose([cursor, exhausted, self.timed("a", 65, 10),
                                      self.timed("b", 20, 30)]).account_id, "a")

    def test_all_codex_eligibility_guards_allow_cursor_fallback(self):
        base = self.account("codex")
        cursor = self.account("cursor", "cursor", used=(25,))
        for ineligible in (replace(base, in_use=True), replace(base, observed_at=NOW - 61),
                           replace(base, windows=()), replace(base, blocked_until=NOW + 1),
                           replace(base, model="unsupported"), self.account("codex", used=(81,)),
                           self.account("codex", used=(None,))):
            with self.subTest(account=ineligible):
                result = self.choose([ineligible, cursor])
                self.assertEqual((result.agent, result.model, result.effort), ("cursor", "grok-4.7-high", "high"))

    def test_new_decisions_prefer_recovered_codex_and_neither_pool_bypasses_guards(self):
        cursor = self.account("cursor", "cursor", used=(25,))
        codex = self.account("codex", used=(100,))
        self.assertEqual(self.choose([cursor, codex]).agent, "cursor")
        self.assertIsNone(self.choose([replace(cursor, observed_at=NOW - 61), codex]))
        self.assertEqual(self.choose([cursor, self.account("codex", used=(75,))]).agent, "codex")

    def test_cursor_retains_conservative_provider_ranking(self):
        first = replace(self.account("cursor-a", "cursor"), windows=(
            QuotaWindow(65, NOW + 1000, "short", 300), QuotaWindow(10, NOW + 10000, "weekly", 10080)))
        second = self.account("cursor-b", "cursor", used=(30,))
        self.assertEqual(self.choose([first, second]).account_id, "cursor-b")

    def test_weekly_limit_constrains_account_with_healthy_short_window(self):
        result = self.choose([self.account("weekly-low", used=(2, 95)),
                              self.account("healthy", used=(35, 45))])
        self.assertEqual(result.account_id, "healthy")
        self.assertEqual(result.remaining_percent, 55)
        self.assertEqual(result.effort, "max")

    def timed(self, identity, short, weekly):
        return replace(self.account(identity), windows=(
            QuotaWindow(short, NOW + 1000, "codex/primary", 300),
            QuotaWindow(weekly, NOW + 10000, "codex/secondary", 10080)))

    def test_below_seventy_percent_short_usage_ranks_by_weekly_usage(self):
        selected = self.choose([self.timed("a", 65, 10), self.timed("b", 20, 30)])
        self.assertEqual(selected.account_id, "a")
        # Ranking must not overstate the actual minimum headroom.
        self.assertEqual(selected.remaining_percent, 35)

    def test_exactly_seventy_percent_includes_the_short_window(self):
        for short, expected in ((69.999, "a"), (70, "b"), (70.001, "b")):
            with self.subTest(short=short):
                self.assertEqual(self.choose([self.timed("a", short, 10),
                                              self.timed("b", 20, 30)]).account_id, expected)

    def test_weekly_preference_preserves_all_eligibility_guards(self):
        base = self.timed("a", 65, 10)
        other = self.timed("b", 20, 30)
        for account in (replace(base, in_use=True), replace(base, observed_at=NOW - 61),
                        replace(base, blocked_until=NOW + 10), self.timed("a", 65, None),
                        self.timed("a", 65, 90), self.timed("a", 81, 10)):
            with self.subTest(account=account):
                self.assertEqual(self.choose([account, other]).account_id, "b")
        policy = replace(self.policy, min_remaining_percent=40)
        self.assertEqual(select_account([base, other], self.profiles, policy, now=NOW).account_id, "b")

    def test_missing_weekly_window_keeps_the_existing_conservative_ranking(self):
        account = replace(self.timed("a", 65, 10), windows=(QuotaWindow(65, NOW + 1000, "codex/primary", 300),))
        self.assertEqual(self.choose([account, self.timed("b", 20, 30)]).account_id, "b")

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
