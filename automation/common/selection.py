"""Choose one pinned agent/account from fresh, provider-normalized quota data."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SelectionPolicy:
    min_remaining_percent: float
    max_snapshot_age_seconds: float

    @classmethod
    def load(cls, data):
        if data.get("strategy") != "quota_priority":
            raise ValueError("agent_selection strategy must be quota_priority")
        if data.get("max_concurrent_tasks_per_account") != 1:
            raise ValueError("only one task may use an account at a time")
        reserve = data.get("min_remaining_percent")
        age = data.get("max_snapshot_age_seconds")
        if not finite_number(reserve) or not 0 < reserve < 100:
            raise ValueError("min_remaining_percent must be between 0 and 100")
        if not finite_number(age) or age <= 0:
            raise ValueError("max_snapshot_age_seconds must be positive")
        return cls(reserve, age)


@dataclass(frozen=True)
class QuotaWindow:
    used_percent: float
    resets_at: float | None = None
    name: str = ""
    duration_minutes: float | None = None


@dataclass(frozen=True)
class AccountQuota:
    # Use provider account identity, not a local profile alias: aliases may
    # represent the same account and share its allowance across independent logins.
    account_id: str
    agent: str
    model: str
    observed_at: float
    windows: tuple[QuotaWindow, ...]
    in_use: bool = False
    blocked_until: float | None = None


@dataclass(frozen=True)
class AccountSelection:
    account_id: str
    agent: str
    model: str
    effort: str
    remaining_percent: float


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _codex_priority(windows, now):
    weekly = tuple(window for window in windows if window.duration_minutes == 10080)
    if not weekly:
        # Missing period metadata falls back to lowest worst-window usage,
        # normalized to a full week so it cannot invent reset urgency.
        return (100 - max(window.used_percent for window in windows)) / 168
    return min(
        (100 - window.used_percent) / max(
            (window.resets_at - now) / 3600 if window.resets_at is not None else 168, 0.5)
        for window in weekly
    )


def select_account(accounts, profiles, policy, *, now):
    """Return the highest-priority usable account, or None.

    All windows supplied must apply to this model. Unknown, stale, reset, busy,
    blocked, and exhausted accounts are ineligible. Codex's reserve is a preference;
    other providers require it. The caller must obtain a durable account lease
    atomically before execution and retry selection if another scheduler won the
    lease. This function does not read credentials, call provider endpoints,
    reserve an account, or start a worker.
    """
    if not finite_number(now):
        raise ValueError("now must be a finite Unix timestamp")
    candidates = []
    for account in accounts:
        profile = profiles.get(account.agent)
        if not profile or account.model != profile.model or not account.account_id:
            continue
        if account.in_use or not account.windows:
            continue
        if not finite_number(account.observed_at) or not 0 <= now - account.observed_at <= policy.max_snapshot_age_seconds:
            continue
        if account.blocked_until is not None:
            if not finite_number(account.blocked_until) or account.blocked_until > now:
                continue
        usable = all(
            finite_number(window.used_percent) and 0 <= window.used_percent <= 100
            and (window.resets_at is None or
                 (finite_number(window.resets_at) and window.resets_at > now))
            for window in account.windows
        )
        if not usable:
            continue
        remaining = 100 - max(window.used_percent for window in account.windows)
        # Weekly Codex limits need capacity, but have no reserve preference.
        # Unknown periods participate in the headroom preference conservatively.
        preferred = tuple(window for window in account.windows
                          if account.agent != "codex" or window.duration_minutes != 10080)
        if not preferred or remaining <= 0:
            continue
        headroom = 100 - max(window.used_percent for window in preferred)
        below_preferred = headroom < policy.min_remaining_percent
        if account.agent != "codex" and below_preferred:
            continue
        selection = AccountSelection(account.account_id, account.agent, profile.model,
                                     profile.effort, remaining)
        score = max(window.used_percent for window in account.windows)
        if account.agent == "codex":
            score = -_codex_priority(account.windows, now)
        rank = ({"codex": 0, "cursor": 1}.get(account.agent, 2), below_preferred, score,
                -headroom if account.agent == "codex" else 0, account.agent, account.account_id)
        candidates.append((rank, selection))
    if not candidates:
        return None
    return min(candidates, key=lambda row: row[0])[1]
