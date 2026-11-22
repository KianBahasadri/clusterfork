"""Stdlib-only Prometheus text exposition 0.0.4 renderer for Symphony."""

from collections import Counter
import math
import os
import re


KNOWN_PHASES = (
    "approving",
    "approved",
    "working",
    "review",
    "blocked",
    "publishing",
    "ci",
    "finishing",
    "finish_failed",
    "done",
)
EXECUTION_KINDS = (
    "startup",
    "worker",
    "verification",
    "coordinator",
    "publication",
    "ci_wait",
    "ci_discovery",
)
DEPLOY_WORKFLOW_RE = re.compile(r"deploy|publish|release", re.I)


def _escape_label(value):
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_labels(labels):
    if not labels:
        return ""
    pairs = ",".join(f'{key}="{_escape_label(val)}"' for key, val in labels)
    return "{" + pairs + "}"


def _format_value(value):
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    number = float(value)
    if not math.isfinite(number):
        return None
    return format(number, ".12g")


def _emit_family(lines, name, metric_type, help_text, samples):
    emitted = []
    for labels, value in samples:
        formatted = _format_value(value)
        if formatted is not None:
            emitted.append(f"{name}{_format_labels(labels)} {formatted}")
    if not emitted:
        return
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {metric_type}")
    lines.extend(emitted)


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _pvc_usage(directory):
    try:
        stats = os.statvfs(directory)
    except OSError:
        return None
    total = stats.f_blocks * stats.f_frsize
    if total <= 0:
        return None
    available = stats.f_bavail * stats.f_frsize
    used = max(0, (stats.f_blocks - stats.f_bfree) * stats.f_frsize)
    return total, available, used


def render_metrics(app):
    """Render Symphony's runtime, SQLite, rate-limit, and PVC metrics in Prometheus 0.0.4 text."""
    runner = app.runner
    coordinator = app.coordinator
    lines = []

    _emit_family(lines, "symphony_up", "gauge", "Symphony process is up and serving metrics.", [((), 1)])
    _emit_family(lines, "symphony_dispatch_paused", "gauge",
                 "Whether automatic worker dispatch is paused (1) or enabled (0).",
                 [((), int(bool(runner.setting("paused"))))])
    _emit_family(lines, "symphony_draining", "gauge",
                 "Whether Symphony is draining for a rollout (1) or accepting new dispatches (0).",
                 [((), int(bool(runner.draining)))])
    _emit_family(lines, "symphony_coordinator_signed_in", "gauge",
                 "Whether the Codex coordinator is currently signed in (1) or signed out (0).",
                 [((), int(bool(getattr(coordinator, "account", ""))))])
    busy = bool(app.busy or app.messages or (coordinator.any_turn() if hasattr(coordinator, "any_turn") else False))
    _emit_family(lines, "symphony_coordinator_busy", "gauge",
                 "Whether a coordinator turn or queued coordinator message is active (1) or idle (0).",
                 [((), int(busy))])

    pending_events = runner.rows("SELECT COUNT(*) AS cnt FROM events WHERE delivered=0")[0]["cnt"]
    _emit_family(lines, "symphony_pending_events", "gauge",
                 "Number of undelivered worker events awaiting coordinator delivery.",
                 [((), pending_events)])

    phase_counts = {phase: 0 for phase in KNOWN_PHASES}
    for row in runner.rows("SELECT phase, COUNT(*) AS cnt FROM tasks GROUP BY phase"):
        phase_counts[row["phase"]] = row["cnt"]
    _emit_family(lines, "symphony_tasks_by_phase", "gauge",
                 "Current recorded tasks grouped by Symphony lifecycle phase.",
                 [((("phase", phase),), count) for phase, count in phase_counts.items()])

    repo_phase_rows = runner.rows(
        "SELECT repo, phase, COUNT(*) AS cnt FROM tasks GROUP BY repo, phase ORDER BY repo, phase"
    )
    _emit_family(lines, "symphony_tasks", "gauge",
                 "Current recorded tasks grouped by repository and lifecycle phase.",
                 [((("repo", row["repo"]), ("phase", row["phase"])), row["cnt"]) for row in repo_phase_rows])

    task_stats = runner.rows(
        """SELECT tasks.repo AS repo,
                  COUNT(*) AS tasks_total,
                  SUM(CASE WHEN MAX(COALESCE(att.cnt, 0), tasks.attempts) > 1 THEN 1 ELSE 0 END) AS tasks_reworked,
                  SUM(CASE WHEN MAX(COALESCE(att.cnt, 0), tasks.attempts) > 1
                           THEN MAX(COALESCE(att.cnt, 0), tasks.attempts) - 1 ELSE 0 END) AS rework_attempts,
                  SUM(MAX(COALESCE(att.cnt, 0), tasks.attempts)) AS worker_attempts
           FROM tasks
           LEFT JOIN (
               SELECT issue, COUNT(*) AS cnt
               FROM execution
               WHERE kind = 'attempt'
               GROUP BY issue
           ) att ON att.issue = tasks.issue
           GROUP BY tasks.repo
           ORDER BY tasks.repo"""
    )
    _emit_family(lines, "symphony_tasks_total", "counter",
                 "Total recorded tasks in the ledger by repository.",
                 [((("repo", row["repo"]),), row["tasks_total"]) for row in task_stats])
    _emit_family(lines, "symphony_tasks_reworked_total", "counter",
                 "Tasks requiring more than one worker attempt by repository.",
                 [((("repo", row["repo"]),), row["tasks_reworked"] or 0) for row in task_stats])
    _emit_family(lines, "symphony_rework_attempts_total", "counter",
                 "Additional worker attempts beyond the initial attempt by repository.",
                 [((("repo", row["repo"]),), row["rework_attempts"] or 0) for row in task_stats])
    _emit_family(lines, "symphony_worker_attempts_total", "counter",
                 "Total worker attempts started across issues by repository.",
                 [((("repo", row["repo"]),), row["worker_attempts"] or 0) for row in task_stats])

    ci_completed = runner.rows(
        """SELECT repo, COALESCE(NULLIF(conclusion, ''), 'unknown') AS conclusion, COUNT(*) AS cnt
           FROM ci_runs
           WHERE status = 'completed'
           GROUP BY repo, COALESCE(NULLIF(conclusion, ''), 'unknown')
           ORDER BY repo, conclusion"""
    )
    _emit_family(lines, "symphony_ci_runs_total", "counter",
                 "Completed GitHub Actions runs observed by repository and conclusion.",
                 [((("repo", row["repo"]), ("conclusion", row["conclusion"])), row["cnt"]) for row in ci_completed])

    ci_active = runner.rows(
        "SELECT repo, COUNT(*) AS cnt FROM ci_runs WHERE status != 'completed' GROUP BY repo ORDER BY repo"
    )
    _emit_family(lines, "symphony_ci_runs_active", "gauge",
                 "In-flight non-completed GitHub Actions runs currently observed by repository.",
                 [((("repo", row["repo"]),), row["cnt"]) for row in ci_active])

    placeholders = ", ".join("?" for _ in EXECUTION_KINDS)
    exec_rows = runner.rows(
        f"""SELECT tasks.repo AS repo, execution.kind AS kind,
                   SUM(execution.ended - execution.started) AS total_seconds,
                   COUNT(*) AS cnt
            FROM execution
            JOIN tasks ON tasks.issue = execution.issue
            WHERE execution.ended IS NOT NULL
              AND execution.ended >= execution.started
              AND execution.kind IN ({placeholders})
            GROUP BY tasks.repo, execution.kind
            ORDER BY tasks.repo, execution.kind""",
        EXECUTION_KINDS,
    )
    _emit_family(lines, "symphony_execution_duration_seconds_sum", "counter",
                 "Total closed execution stage duration in seconds by repository and stage.",
                 [((("repo", row["repo"]), ("kind", row["kind"])), row["total_seconds"]) for row in exec_rows])
    _emit_family(lines, "symphony_execution_duration_seconds_count", "counter",
                 "Total count of closed execution stage intervals by repository and stage.",
                 [((("repo", row["repo"]), ("kind", row["kind"])), row["cnt"]) for row in exec_rows])

    turn_rows = runner.rows(
        """SELECT repo, model, status, SUM(duration) AS total_seconds, COUNT(*) AS cnt
           FROM coordinator_turns
           WHERE duration >= 0
           GROUP BY repo, model, status
           ORDER BY repo, model, status"""
    )
    _emit_family(lines, "symphony_coordinator_turn_duration_seconds_sum", "counter",
                 "Total coordinator turn duration in seconds by repository, model, and status.",
                 [((("repo", row["repo"]), ("model", row["model"]), ("status", row["status"])),
                   row["total_seconds"]) for row in turn_rows])
    _emit_family(lines, "symphony_coordinator_turn_duration_seconds_count", "counter",
                 "Total count of coordinator turns by repository, model, and status.",
                 [((("repo", row["repo"]), ("model", row["model"]), ("status", row["status"])),
                   row["cnt"]) for row in turn_rows])

    token_rows = runner.rows(
        """SELECT source, repo, model,
                  SUM(input_tokens) AS inp, COUNT(input_tokens) AS inp_n,
                  SUM(output_tokens) AS out, COUNT(output_tokens) AS out_n,
                  SUM(total_tokens) AS tot, COUNT(total_tokens) AS tot_n
           FROM token_usage
           GROUP BY source, repo, model
           ORDER BY source, repo, model"""
    )
    token_samples = []
    for row in token_rows:
        for direction, sum_key, count_key in (
            ("input", "inp", "inp_n"),
            ("output", "out", "out_n"),
            ("total", "tot", "tot_n"),
        ):
            if row[count_key]:
                token_samples.append((
                    (("source", row["source"]), ("repo", row["repo"]),
                     ("model", row["model"]), ("direction", direction)),
                    row[sum_key],
                ))
    _emit_family(lines, "symphony_tokens_total", "counter",
                 "Observed token counts by source (coordinator/worker), repository, model, and direction.",
                 token_samples)

    cost_rows = runner.rows(
        """SELECT source, repo, model, SUM(cost_usd) AS cost_usd
           FROM token_usage
           WHERE cost_usd IS NOT NULL
           GROUP BY source, repo, model
           ORDER BY source, repo, model"""
    )
    _emit_family(lines, "symphony_estimated_spend_usd_total", "counter",
                 "Observed USD spend reported by model providers, omitting subscription usage without dollar billing.",
                 [((("source", row["source"]), ("repo", row["repo"]), ("model", row["model"])),
                   row["cost_usd"]) for row in cost_rows])

    rate_limits = getattr(coordinator, "rate_limits", None) or {}
    limit_samples = []
    for window in ("5h", "weekly"):
        entry = rate_limits.get(window)
        if isinstance(entry, dict) and _finite_number(entry.get("left")):
            limit_samples.append(((("window", window),), entry["left"]))
    _emit_family(lines, "symphony_codex_rate_limit_remaining_percent", "gauge",
                 "Remaining Codex ChatGPT rate-limit percentage for reported quota windows.",
                 limit_samples)

    push_rows = runner.rows(
        """SELECT tasks.repo AS repo, COUNT(*) AS cnt
           FROM execution
           JOIN tasks ON tasks.issue = execution.issue
           WHERE execution.kind = 'publication'
             AND execution.detail LIKE 'Pushed % to main.'
           GROUP BY tasks.repo
           ORDER BY tasks.repo"""
    )
    _emit_family(lines, "symphony_pushes_total", "counter",
                 "Total Symphony pushes to main by repository.",
                 [((("repo", row["repo"]),), row["cnt"]) for row in push_rows])

    deploy_counts = Counter()
    for row in runner.rows(
        """SELECT repo, workflow, path, COALESCE(NULLIF(conclusion, ''), 'unknown') AS conclusion
           FROM ci_runs
           WHERE status = 'completed'"""
    ):
        workflow_name = row["workflow"] or row["path"] or ""
        if DEPLOY_WORKFLOW_RE.search(row["workflow"] or "") or DEPLOY_WORKFLOW_RE.search(row["path"] or ""):
            deploy_counts[(row["repo"], workflow_name, row["conclusion"])] += 1
    _emit_family(lines, "symphony_deployments_total", "counter",
                 "Completed deploy/publish/release GitHub Actions runs by repository, workflow, and conclusion.",
                 [((("repo", repo), ("workflow", wf), ("conclusion", concl)), count)
                  for (repo, wf, concl), count in sorted(deploy_counts.items())])

    pvc = _pvc_usage(runner.directory)
    if pvc is not None:
        total_bytes, avail_bytes, used_bytes = pvc
        _emit_family(lines, "symphony_pvc_bytes_total", "gauge",
                     "Total size in bytes of the Symphony PVC filesystem.", [((), total_bytes)])
        _emit_family(lines, "symphony_pvc_bytes_available", "gauge",
                     "Available bytes to non-privileged processes on the Symphony PVC filesystem.", [((), avail_bytes)])
        _emit_family(lines, "symphony_pvc_bytes_used", "gauge",
                     "Used bytes on the Symphony PVC filesystem.", [((), used_bytes)])

    availability = {
        "coordinator_tokens": bool(runner.rows(
            """SELECT 1 FROM token_usage
               WHERE source = 'coordinator'
                 AND (input_tokens IS NOT NULL OR output_tokens IS NOT NULL OR total_tokens IS NOT NULL)
               LIMIT 1"""
        )),
        "coordinator_cost_usd": bool(runner.rows(
            "SELECT 1 FROM token_usage WHERE source = 'coordinator' AND cost_usd IS NOT NULL LIMIT 1"
        )),
        "worker_tokens": bool(runner.rows(
            """SELECT 1 FROM token_usage
               WHERE source = 'worker'
                 AND (input_tokens IS NOT NULL OR output_tokens IS NOT NULL OR total_tokens IS NOT NULL)
               LIMIT 1"""
        )),
        "worker_cost_usd": bool(runner.rows(
            "SELECT 1 FROM token_usage WHERE source = 'worker' AND cost_usd IS NOT NULL LIMIT 1"
        )),
        "codex_rate_limits": bool(limit_samples),
        "deployments": bool(deploy_counts),
        "pvc_disk": pvc is not None,
    }
    _emit_family(lines, "symphony_metric_available", "gauge",
                 "Explicit collection availability flag (1 = real observations recorded, 0 = unavailable/uncollected).",
                 [((("metric", key),), int(val)) for key, val in availability.items()])

    return "\n".join(lines) + "\n"
