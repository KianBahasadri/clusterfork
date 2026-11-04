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
                           replace(base, model="unsupported"), self.account("codex", used=(100,)),
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

    def timed(self, identity, short, weekly, *, reset_hours=10000 / 3600):
        return replace(self.account(identity), windows=(
            QuotaWindow(short, NOW + 1000, "codex/primary", 300),
            QuotaWindow(weekly, NOW + reset_hours * 3600, "codex/secondary", 10080)))

    def test_weekly_priority_preserves_actual_minimum_headroom(self):
        selected = self.choose([self.timed("a", 65, 10), self.timed("b", 20, 30)])
        self.assertEqual(selected.account_id, "a")
        # Ranking must not overstate the actual minimum headroom.
        self.assertEqual(selected.remaining_percent, 35)

    def test_codex_five_hour_reserve_allows_low_but_nonzero_weekly_capacity(self):
        for weekly in (80.001, 95, 99.999):
            with self.subTest(weekly=weekly):
                selected = self.choose([self.timed("available", 80, weekly)])
                self.assertEqual(selected.account_id, "available")
                self.assertAlmostEqual(selected.remaining_percent, 100 - weekly)

    def test_codex_rejects_exhausted_or_unknown_capacity_in_either_window(self):
        for short, weekly in ((100, 95), (20, 100), (20, None), (None, 20)):
            with self.subTest(short=short, weekly=weekly):
                self.assertIsNone(self.choose([self.timed("unavailable", short, weekly)]))

    def test_weekly_only_or_reset_snapshots_do_not_establish_codex_capacity(self):
        base = self.timed("account", 20, 95)
        for windows in ((base.windows[1],), (base.windows[0], replace(base.windows[1], resets_at=NOW - 1))):
            with self.subTest(windows=windows):
                self.assertIsNone(self.choose([replace(base, windows=windows)]))

    def test_unknown_codex_durations_use_low_priority_while_cursor_retains_the_reserve(self):
        base = self.timed("account", 20, 95)
        unknown = replace(base, windows=(base.windows[0], replace(base.windows[1], duration_minutes=None)))
        cursor = replace(base, agent="cursor", model=self.profiles["cursor"].model)
        self.assertEqual(self.choose([unknown]).account_id, "account")
        self.assertEqual(self.choose([unknown, self.account("healthy", used=(50,))]).account_id, "healthy")
        self.assertIsNone(self.choose([cursor]))

    def test_reset_priority_prefers_quota_expiring_soon_over_a_fresh_week(self):
        fresh = self.timed("fresh", 0, 10, reset_hours=165)
        expiring = self.timed("expiring", 0, 90, reset_hours=3)
        for accounts in ([fresh, expiring], [expiring, fresh]):
            self.assertEqual(self.choose(accounts).account_id, "expiring")

    def test_reset_priority_considers_remaining_quota_not_just_earliest_reset(self):
        fresh = self.timed("fresh", 0, 10, reset_hours=165)
        nearly_empty = self.timed("nearly-empty", 0, 99.9, reset_hours=3)
        self.assertEqual(self.choose([nearly_empty, fresh]).account_id, "fresh")

    def test_reset_priority_uses_a_thirty_minute_floor(self):
        later = self.timed("later", 0, 85, reset_hours=1)
        # A one-hour floor would incorrectly prefer later's 15% to this 10%.
        soon = self.timed("soon", 0, 90, reset_hours=0.4)
        self.assertEqual(self.choose([later, soon]).account_id, "soon")
        # Sub-half-hour reset times must not inflate a nearly empty account.
        for hours in (1 / 3600, 0.1, 0.499999, 0.5):
            with self.subTest(hours=hours):
                tiny = self.timed("tiny", 0, 99, reset_hours=hours)
                self.assertEqual(self.choose([tiny, later]).account_id, "later")

    def test_twenty_percent_is_a_priority_boundary_not_an_eligibility_cutoff(self):
        other = self.timed("b", 0, 90, reset_hours=165)
        for short, expected in ((79.999, "a"), (80, "a"), (80.001, "b"), (99, "b")):
            with self.subTest(short=short):
                self.assertEqual(self.choose([self.timed("a", short, 0, reset_hours=0.5),
                                              other]).account_id, expected)

    def test_low_five_hour_accounts_remain_available_and_follow_the_same_reset_score(self):
        fresh = self.timed("fresh", 90, 10, reset_hours=165)
        expiring = self.timed("expiring", 99, 90, reset_hours=3)
        selected = self.choose([fresh, expiring])
        self.assertEqual(selected.account_id, "expiring")
        self.assertEqual(selected.remaining_percent, 1)
        self.assertIsNotNone(self.choose([self.timed("last", 99.999, 99.999)]))

    def test_reset_priority_breaks_ties_by_short_headroom_then_stable_identity(self):
        first = self.timed("a", 50, 90, reset_hours=3)
        second = self.timed("b", 20, 80, reset_hours=6)
        for accounts in ([first, second], [second, first]):
            self.assertEqual(self.choose(accounts).account_id, "b")
        identical = replace(second, account_id="a")
        for accounts in ([second, identical], [identical, second]):
            self.assertEqual(self.choose(accounts).account_id, "a")

    def test_missing_weekly_reset_does_not_invent_reset_urgency(self):
        missing = self.timed("missing", 0, 10)
        missing = replace(missing, windows=(missing.windows[0], replace(missing.windows[1], resets_at=None)))
        known = self.timed("known", 0, 10, reset_hours=165)
        self.assertEqual(self.choose([missing, known]).account_id, "known")
        self.assertEqual(self.choose([missing]).account_id, "missing")

    def test_every_applicable_weekly_limit_constrains_reset_priority(self):
        limited = self.timed("limited", 0, 0, reset_hours=0.5)
        limited = replace(limited, windows=limited.windows + (
            QuotaWindow(99, NOW + 165 * 3600, "model/weekly", 10080),))
        other = self.timed("other", 0, 50, reset_hours=165)
        self.assertEqual(self.choose([limited, other]).account_id, "other")

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

    def test_low_codex_capacity_is_preferred_to_cursor_and_exhausted_capacity_is_not(self):
        cursor = self.account("cursor", "cursor", used=(0,))
        low = self.account("low", used=(99,))
        empty = self.account("empty", used=(100,))
        self.assertEqual(self.choose([cursor, low, empty]).account_id, "low")
        self.assertEqual(self.choose([cursor, empty]).account_id, "cursor")
        self.assertIsNone(self.choose([empty]))

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
