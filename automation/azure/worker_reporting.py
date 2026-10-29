"""Safe, durable worker progress and quota summaries for Linear."""

from __future__ import annotations

from datetime import datetime, timezone
import re

from quota_selection import finite_number


STAGES = {
    "initializing": "runner initialization",
    "selecting_account": "account selection",
    "coding": "coding",
    "testing": "final testing and review",
    "publishing": "publication",
    "review": "PR review and CI",
    "waiting_for_quota": "waiting for quota",
}
STAGE_LABELS = {stage: f"clusterfork: {name}" for stage, name in STAGES.items()}


def usage_report(snapshot):
    return {"observed_at": snapshot.observed_at, "windows": [
        {"name": window.name, "used_percent": window.used_percent,
         "resets_at": window.resets_at, "duration_minutes": window.duration_minutes}
        for window in snapshot.windows]}


def validate_usage(data):
    """Allow only quota measurements across the broker/public-comment boundary."""
    if not isinstance(data, dict) or not finite_number(data.get("observed_at")):
        raise ValueError("Invalid usage snapshot")
    windows = data.get("windows")
    if not isinstance(windows, list) or not 1 <= len(windows) <= 32:
        raise ValueError("Invalid usage windows")
    result, names = [], set()
    for window in windows:
        name = window.get("name") if isinstance(window, dict) else None
        if not isinstance(name, str) or not re.fullmatch(r"[\w./-]{1,120}", name) or name in names:
            raise ValueError("Invalid usage window name")
        used, reset, duration = (window.get(key) for key in ("used_percent", "resets_at", "duration_minutes"))
        if not finite_number(used) or not 0 <= used <= 100:
            raise ValueError("Invalid usage percentage")
        if any(value is not None and (not finite_number(value) or value <= 0) for value in (reset, duration)):
            raise ValueError("Invalid usage window metadata")
        names.add(name)
        result.append({"name": name, "used_percent": used, "resets_at": reset, "duration_minutes": duration})
    return {"observed_at": data["observed_at"], "windows": result}


def public_text(value):
    # Provider account labels are plain text, never Markdown or HTML.
    return re.sub(r"([\\`*_{}\[\]<>()|])", r"\\\1", " ".join(str(value).split())[:200])


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def window_name(window):
    minutes = window["duration_minutes"]
    period = "weekly" if minutes == 10080 else (f"{minutes:g}-minute" if minutes else "duration unknown")
    return f"{public_text(window['name'])} ({period})"


def usage_lines(before, after=None):
    lines = []
    current = {window["name"]: window for window in after["windows"]} if after else {}
    for window in before["windows"]:
        used = window["used_percent"]
        prefix = f"- {window_name(window)}: {used:g}% used / {100 - used:g}% remaining"
        if after is not None:
            latest = current.pop(window["name"], None)
            if latest is None:
                prefix += " → unavailable; change unavailable"
            else:
                final = latest["used_percent"]
                prefix += f" → {final:g}% used / {100 - final:g}% remaining"
                if (window["resets_at"] is None or latest["resets_at"] is None
                        or window["resets_at"] != latest["resets_at"]
                        or window["duration_minutes"] != latest["duration_minutes"] or final < used):
                    prefix += "; change unavailable (window reset or changed, or reset time unknown)"
                else:
                    prefix += f"; usage change {final - used:+g} percentage points"
                if latest["resets_at"] is not None:
                    prefix += f"; resets {timestamp(latest['resets_at'])}"
        elif window["resets_at"] is not None:
            prefix += f"; resets {timestamp(window['resets_at'])}"
        lines.append(prefix)
    for window in current.values():
        used = window["used_percent"]
        lines.append(f"- {window_name(window)}: {used:g}% used / {100 - used:g}% remaining; change unavailable (new window)")
    if not any(window["duration_minutes"] == 10080 for window in before["windows"]):
        lines.append("- Weekly usage: unavailable (provider did not identify a weekly window).")
    return "\n".join(lines)


def attempt_comment(run, repository, *, finished=False):
    import json

    account = public_text(run.get("account_email") or run["account_id"])
    plan = public_text(run.get("account_plan") or "unknown")
    before = json.loads(run["usage_before"])
    body = (f"Clusterfork development {'finished' if finished else 'started'}.\n\n"
            f"Account: {account} (ID `{run['account_id']}`, plan: {plan})\n\n"
            f"Agent: Codex · Model: `{run['model']}` · Reasoning effort: `{run['effort']}`\n\n")
    if finished:
        if run.get("finished_at") is not None and run.get("started_at") is not None:
            seconds = max(0, int(run["finished_at"] - run["started_at"]))
            hours, remainder = divmod(seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            body += f"Total worker time: {hours}h {minutes}m {seconds}s. Outcome: {run['status']}.\n\n"
        else:
            body += f"Total worker time: unavailable. Outcome: {run['status']}.\n\n"
        after = json.loads(run["usage_after"]) if run.get("usage_after") else None
        body += f"Usage before ({timestamp(before['observed_at'])}) and after"
        body += f" ({timestamp(after['observed_at'])}):\n\n{usage_lines(before, after)}" if after else (
            f": final reading unavailable; usage change unavailable.\n\nBefore:\n\n{usage_lines(before)}")
        body += "\n\nUsage changes compare account quota readings; provider reporting may lag or include other activity."
    else:
        body += f"Usage before coding ({timestamp(before['observed_at'])}):\n\n{usage_lines(before)}"
    run_id, attempt = run["owner"].split(":")
    return body + f"\n\n[Worker run](https://github.com/{repository}/actions/runs/{run_id}/attempts/{attempt})"
