"""Linear stage history and recorded execution diagnostics for the issue modal."""

from datetime import datetime, timezone
import json
import os
import re

from .services import ISSUE_ID


STAGES = ("Backlog", "Todo", "In Progress", "Done")
FAILURE_CONCLUSIONS = {
    "action_required", "cancelled", "failure", "skipped", "stale", "startup_failure", "timed_out",
}
SECRET_NAMES = {
    "GITHUB_TOKEN", "GH_TOKEN", "LINEAR_API_KEY", "OPENAI_API_KEY", "OPENCODE_GO_API_KEY",
    "LINEAR_WEBHOOK_SECRET",
}
SECRET_PATTERN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|lin_api_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})\b"
)
LOG_BYTES = 24000
CHECKS_BYTES = 8000
SNAPSHOT_BYTES = 24000


def moment(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def state(value):
    value = value if isinstance(value, dict) else {}
    result = {"name": value.get("name", ""), "type": value.get("type", "")}
    if value.get("id"):
        result["id"] = value["id"]
    return result


def transition(entry):
    if not isinstance(entry, dict):
        return None
    at = moment(entry.get("createdAt"))
    if at is None or not (entry.get("fromState") or entry.get("toState")):
        return None
    return {
        "id": str(entry.get("id") or ""),
        "at": at,
        "from": state(entry.get("fromState")) if entry.get("fromState") else None,
        "to": state(entry.get("toState")) if entry.get("toState") else None,
    }


def history_summary(issue, history, now=None):
    """Turn complete status history into tracked stage totals and the open interval."""
    now = now or datetime.now(timezone.utc)
    history = history or {}
    raw_entries = history.get("entries") or []
    malformed = any(
        isinstance(entry, dict) and (entry.get("fromState") or entry.get("toState"))
        and (moment(entry.get("createdAt")) is None
             or any(entry.get(key) is not None and not isinstance(entry.get(key), dict)
                    for key in ("fromState", "toState")))
        for entry in raw_entries
    )
    entries = sorted((item for item in map(transition, raw_entries) if item),
                     key=lambda item: (item["at"], item["id"]))
    complete = bool(history.get("complete")) and not malformed
    reason = (history.get("error") or "").strip()
    if malformed:
        coverage = "inconsistent"
        reason = "History contains a status transition with an invalid timestamp."
    else:
        coverage = "complete" if complete else ("partial" if entries else "unavailable")
        if not complete and not reason:
            reason = "Recorded history is incomplete."
    current = state(issue.get("state"))
    created = moment(issue.get("createdAt"))
    initial = None
    inconsistent = ""

    if complete and entries:
        first = entries[0]
        if first["from"]:
            if created and first["at"] < created:
                inconsistent = "The first transition is older than the issue creation time."
            else:
                initial = (first["from"], created)
                if created is None:
                    coverage = "partial"
                    reason = "Issue creation time is unavailable, so earlier stages are unknown."
        else:
            coverage = "partial"
            reason = "History does not identify the status before the first transition."
    elif complete:
        if created and current["name"]:
            initial = (current, created)
        else:
            coverage = "partial"
            reason = "Issue creation time is unavailable, so stage totals cannot start."

    totals = {name: 0.0 for name in STAGES}
    visits = {name: 0 for name in STAGES}
    active = None
    active_at = None
    open_name = ""
    current_entry = None

    def enter(value, at):
        nonlocal active, active_at
        active, active_at = value, at
        if at is not None and value and value.get("name") in visits:
            visits[value["name"]] += 1

    def close(at):
        if active and active_at is not None and at >= active_at and active.get("name") in totals:
            totals[active["name"]] += (at - active_at).total_seconds()

    if initial:
        enter(initial[0], initial[1])
        transitions = entries
    elif complete and entries:
        first = entries[0]
        if first["to"]:
            enter(first["to"], first["at"])
        else:
            inconsistent = "The first recorded transition has no destination status."
        transitions = entries[1:]
    else:
        transitions = []

    for item in transitions:
        if active and item["from"] and (
                item["from"]["name"] != active["name"] or item["from"]["type"] != active["type"]):
            inconsistent = (
                f"History goes from {item['from']['name']} but the previous status was {active['name']}."
            )
        if not item["to"]:
            inconsistent = "A recorded status transition has no destination status."
        close(item["at"])
        enter(item["to"], item["at"])

    if complete and not inconsistent:
        if current["name"] and active and (
                current["name"] != active["name"] or current["type"] != active["type"]):
            inconsistent = f"History ends at {active['name']} but Linear reports {current['name']}."
        elif not active:
            inconsistent = "History does not establish the current status."
    elif not complete and active:
        # Partial history can confirm a recent transition, but not the missing period before it.
        active = None
        active_at = None

    if inconsistent:
        coverage = "inconsistent"
        reason = inconsistent
    elif complete and initial is not None and coverage == "complete":
        if active and active_at is not None and now >= active_at:
            close(now)
            open_name = active["name"] if active["name"] in totals else ""
            current_entry = {
                "name": active["name"], "type": active["type"],
                "startedAt": iso(active_at),
                "source": "created" if active_at == created else "history",
                "open": True,
            }
        else:
            current_entry = None
    elif complete and active and active_at is not None and now >= active_at:
        # The exact initial state is unavailable, but the latest transition is still confirmed.
        current_entry = {
            "name": active["name"], "type": active["type"],
            "startedAt": iso(active_at), "source": "history", "open": True,
        }
    else:
        current_entry = None

    numeric = coverage == "complete"
    stages = [{
        "name": name,
        "seconds": totals[name] if numeric else None,
        "visits": visits[name] if numeric else None,
        "open": open_name == name,
    } for name in STAGES]
    return {
        "coverage": coverage,
        "reason": reason,
        "createdAt": iso(created),
        "current": current_entry,
        "stages": stages,
        "transitions": [{
            "id": item["id"], "at": iso(item["at"]),
            "from": item["from"], "to": item["to"],
        } for item in entries],
        "generatedAt": iso(now),
    }


def card_stage(issue, summary):
    current = summary.get("current")
    return {
        "name": (current or {}).get("name") or state(issue.get("state"))["name"],
        "startedAt": (current or {}).get("startedAt"),
        "source": (current or {}).get("source", ""),
        "coverage": summary.get("coverage", "unavailable"),
        "reason": summary.get("reason", ""),
    }


def redact(text):
    text = str(text or "")
    for name in SECRET_NAMES:
        secret = os.environ.get(name, "")
        if len(secret) >= 4:
            text = text.replace(secret, "[redacted]")
    return SECRET_PATTERN.sub("[redacted]", text)


def redact_value(value):
    if isinstance(value, str):
        return redact(value)[:8000]
    if isinstance(value, list):
        return [redact_value(item) for item in value[:200]]
    if isinstance(value, dict):
        return {redact(key): redact_value(item) for key, item in list(value.items())[:200]}
    return value


def bounded_file(path, limit):
    try:
        if path.is_symlink() or not path.is_file():
            return None, False
        size = path.stat().st_size
        with path.open("rb") as stream:
            if size > limit:
                stream.seek(size - limit)
            data = stream.read(limit)
        return data.decode(errors="replace"), size > limit
    except OSError:
        return None, False


def intervals_duration(intervals, now):
    seconds = 0.0
    for start, end in intervals:
        start = moment(start)
        if start is None:
            continue
        end = moment(end) or now
        if end >= start:
            seconds += (end - start).total_seconds()
    return seconds


def union_duration(intervals, now):
    spans = []
    for start, end in intervals:
        start = moment(start)
        if start is None:
            continue
        end = moment(end) or now
        if end < start:
            continue
        spans.append((start, min(end, now) if end > now else end))
    spans.sort()
    total = 0.0
    current_start = current_end = None
    for start, end in spans:
        if current_end is None or start > current_end:
            if current_end is not None:
                total += (current_end - current_start).total_seconds()
            current_start, current_end = start, end
        elif end > current_end:
            current_end = end
    if current_end is not None:
        total += (current_end - current_start).total_seconds()
    return total


def execution_event(row):
    try:
        data = json.loads(row.get("data") or "{}")
    except ValueError:
        data = {}
    return {
        "id": row["id"], "kind": row["kind"],
        "startedAt": iso(moment(row.get("started"))),
        "endedAt": iso(moment(row.get("ended"))),
        "detail": redact(row.get("detail"))[:8000],
        "open": row.get("ended") is None,
        "data": redact_value(data),
    }


def execution_data(row):
    try:
        data = json.loads(row.get("data") or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def snapshot_text(value, limit=SNAPSHOT_BYTES):
    text = redact(value)
    encoded = text.encode()
    if len(encoded) <= limit:
        return text, False
    return encoded[:limit].decode(errors="ignore").rstrip(), True


def attempt_snapshot(row, notes_data):
    notes_data = notes_data if isinstance(notes_data, dict) else {}
    notes_text, notes_bounded = snapshot_text(notes_data.get("text", ""))
    notes_available = bool(notes_data.get("available")) and bool(notes_text.strip())
    if notes_data:
        notes_detail, _ = snapshot_text(notes_data.get("detail", ""), 2000)
    elif row.get("ended") is None:
        notes_detail = "This attempt is still running; notes.md has not been snapshotted yet."
    else:
        notes_detail = "No notes.md snapshot was retained for this attempt."
    return {
        "available": notes_available,
        "text": notes_text if notes_available else "",
        "truncated": bool(notes_data.get("truncated")) or notes_bounded,
        "detail": notes_detail,
    }


def run_interval(run):
    start = moment(run.get("started_at") or run.get("run_started_at"))
    observed = moment(run.get("observed"))
    completed = moment(run.get("completed_at"))
    updated = moment(run.get("updated_at"))
    if run.get("status") == "completed":
        end = completed or updated or observed
        boundary = "completed" if completed else "updated" if updated else "observed" if observed else ""
        known = bool(completed or updated)
    else:
        end = observed
        boundary = "observed" if observed else ""
        known = False
    if start and end and end < start:
        end = start
        known = False
        boundary = "inconsistent"
    return iso(start), iso(end), known, boundary


def execution_payload(identifier, issue, history, runner, now=None):
    if not ISSUE_ID.fullmatch(identifier):
        raise ValueError("Use a Linear issue ID such as KIAN-1077.")
    now = now or datetime.now(timezone.utc)
    stages = redact_value(history_summary(issue, history, now))
    try:
        task = runner.task(identifier)
    except ValueError:
        task = None
    phase = (task or {}).get("phase", "")
    task_attempts = int((task or {}).get("attempts") or 0)

    execution_rows = runner.rows(
        "SELECT * FROM execution WHERE issue=? ORDER BY started, id", (identifier,))
    events = [execution_event(row) for row in execution_rows]
    runs = [dict(row) for row in runner.rows(
        "SELECT * FROM ci_runs WHERE issue=? ORDER BY run_started_at, run_id, attempt", (identifier,))]
    jobs = [dict(row) for row in runner.rows(
        "SELECT * FROM ci_jobs WHERE issue=? ORDER BY started_at, job_id", (identifier,))]
    jobs_by_run = {}
    for job in jobs:
        jobs_by_run.setdefault((job["run_id"], job["attempt"]), []).append(job)

    run_rows = []
    for run in runs:
        started, ended, boundary_known, boundary_source = run_interval(run)
        jobs_for_run = [{
            "jobId": job["job_id"], "name": job["name"], "status": job["status"],
            "conclusion": job["conclusion"], "startedAt": job["started_at"],
            "completedAt": job["completed_at"], "url": job["url"],
            "diagnostic": redact(job["diagnostic"]),
        } for job in jobs_by_run.get((run["run_id"], run["attempt"]), [])]
        run_rows.append({
            "runId": run["run_id"], "attempt": run["attempt"],
            "workflow": run["workflow"], "path": run["path"],
            "revision": run["revision"],
            "status": run["status"], "conclusion": run["conclusion"],
            "url": run["url"],
            "attemptUrl": (f"{run['url']}/attempts/{run['attempt']}"
                           if "/actions/runs/" in (run["url"] or "")
                           and "/attempts/" not in (run["url"] or "") else run["url"]),
            "createdAt": run["created_at"],
            "startedAt": started, "updatedAt": run["updated_at"],
            "completedAt": run["completed_at"], "observedAt": iso(moment(run["observed"])),
            "endedAt": ended, "boundaryKnown": boundary_known,
            "boundarySource": boundary_source, "ongoing": run["status"] != "completed",
            "diagnostic": redact(run["diagnostic"]),
            "jobsFetched": bool(run["jobs_fetched"]), "jobs": jobs_for_run,
        })

    attempt_rows = [row for row in execution_rows if row["kind"] == "attempt"]
    attempt_rows.sort(key=lambda row: (row["started"], row["id"]))
    attempts = []
    for index, row in enumerate(attempt_rows, 1):
        data = execution_data(row)
        notes = attempt_snapshot(row, data.get("notes"))
        attempts.append({
            "id": str(row["id"]),
            "number": index,
            "counter": data.get("attempt", index),
            "startedAt": iso(moment(row.get("started"))),
            "endedAt": iso(moment(row.get("ended"))),
            "open": row.get("ended") is None,
            "notes": notes,
        })
    worker = {
        "attemptCount": len(attempts),
        "coverage": ("recorded" if attempts else
                     "not_started" if task and phase == "queued" and task_attempts == 0
                     else "unavailable"),
        "attempts": attempts,
    }

    timeline = [{**event} for event in events]
    for run in run_rows:
        timeline.append({
            "id": f"github-{run['runId']}-{run['attempt']}", "kind": "ci_run",
            "startedAt": run["startedAt"], "endedAt": run["endedAt"],
            "detail": f"{run['workflow']} attempt {run['attempt']} · {run['status']}"
                      + (f" · {run['conclusion']}" if run["conclusion"] else ""),
            "open": run["ongoing"],
            "data": redact_value({
                key: run[key] for key in (
                    "runId", "attempt", "workflow", "path", "revision", "status", "conclusion",
                    "url", "attemptUrl", "startedAt", "endedAt", "boundaryKnown", "ongoing",
                )
            } | {"url": run["attemptUrl"] or run["url"]}),
        })
    timeline.sort(key=lambda item: (item["startedAt"] or "", item["id"]))

    breakdown = {}
    intervals = []
    gaps = 0
    measured_kinds = set()
    for event in events:
        if event["kind"] in {"attempt", "gap", "completion", "approval", "interruption", "failure"}:
            if event["kind"] == "gap":
                gaps += 1
            continue
        seconds = intervals_duration([(event["startedAt"], event["endedAt"])], now)
        breakdown[event["kind"]] = breakdown.get(event["kind"], 0.0) + seconds
        measured_kinds.add(event["kind"])
        intervals.append((event["startedAt"], event["endedAt"]))
    tracking = bool(measured_kinds & {
        "startup", "worker", "verification", "publication", "ci_wait", "ci_discovery",
    }) or any(event["kind"] == "attempt" for event in events)
    has_ci = bool(task and (task.get("revision") or task.get("workflows")
                            or phase in {"ci", "finishing"}))
    categories = ("startup", "worker", "verification", "coordinator", "publication",
                  "ci_wait", "ci_discovery")
    missing_categories = []
    for category in categories:
        if category in breakdown:
            continue
        value = None
        if category == "coordinator":
            value = 0.0 if tracking else None
        elif category == "startup":
            if task and task_attempts == 0 and phase == "queued":
                value = 0.0
        elif category == "worker":
            if task and ((task_attempts == 0 and phase == "queued")
                         or (tracking and phase == "working")):
                value = 0.0
        elif category == "verification":
            if task and (phase == "queued"
                         or (tracking and phase == "working")):
                value = 0.0
        elif category == "publication":
            if (task and not task.get("revision")
                    and phase not in {"publishing", "ci", "finishing", "done", "finish_failed"}
                    and (tracking or phase == "queued")):
                value = 0.0
        elif category in {"ci_wait", "ci_discovery"}:
            if task and not has_ci and tracking:
                value = 0.0
        breakdown[category] = value
        if value is None:
            missing_categories.append(category)
    ci_intervals = [(run["startedAt"], run["endedAt"]) for run in run_rows
                    if run["startedAt"] and run["endedAt"]]
    ci_seconds = intervals_duration(ci_intervals, now)
    if run_rows:
        breakdown["ci_workflow"] = ci_seconds if ci_intervals else None
        if breakdown["ci_workflow"] is None:
            missing_categories.append("ci_workflow")
    else:
        breakdown["ci_workflow"] = None if has_ci or not tracking else 0.0
        if breakdown["ci_workflow"] is None:
            missing_categories.append("ci_workflow")
    job_intervals = [(job["started_at"], job["completed_at"]) for job in jobs
                     if job["started_at"] and job["completed_at"]]
    job_seconds = intervals_duration(job_intervals, now)
    job_wall = union_duration(job_intervals, now)
    completed_runs = [run for run in run_rows if run["status"] == "completed"]
    jobs_complete = bool(completed_runs) and all(run["jobsFetched"] for run in completed_runs)
    job_value = job_seconds if job_intervals else None
    measured = sum(value for value in breakdown.values() if value is not None)
    wall = union_duration(intervals + ci_intervals, now)
    waiting = breakdown.get("ci_wait")
    discovery = breakdown.get("ci_discovery")
    failures = sum(1 for run in run_rows if run["conclusion"] in FAILURE_CONCLUSIONS)
    incomplete_jobs = sum(
        1 for run in run_rows if run["status"] == "completed" and not run["jobsFetched"])
    boundary_gaps = sum(
        1 for run in run_rows
        if not run["boundaryKnown"] or not run["startedAt"] or not run["endedAt"])
    expected = []
    if task and task.get("workflows"):
        try:
            expected = json.loads(task["workflows"])
        except (TypeError, ValueError):
            expected = []
    current_revision = (task or {}).get("revision")
    current_runs = ([run for run in run_rows if run["revision"] == current_revision]
                    if current_revision else run_rows)
    observed_paths = {run["path"] for run in current_runs}
    missing_paths = sorted(set(expected) - observed_paths)
    ci_coverage = "recorded"
    ci_reason = ""
    if not run_rows:
        ci_coverage = "unavailable"
        ci_reason = ("Push CI was expected but no GitHub run details were recorded."
                     if expected else "No push CI run details were recorded for this issue.")
    elif expected and not current_runs:
        ci_coverage = "unavailable"
        ci_reason = "No GitHub run details were recorded for the current revision."
    else:
        issues = []
        if missing_paths:
            issues.append("No run details for " + ", ".join(missing_paths) + ".")
        if boundary_gaps:
            issues.append("Some run durations use the last recorded observation or have unknown boundaries.")
        if incomplete_jobs:
            issues.append("Some GitHub job details were unavailable.")
        if issues:
            ci_coverage = "partial"
            ci_reason = " ".join(issues)
    execution_coverage = "recorded"
    execution_reason = ""
    if not events and not run_rows:
        if task:
            execution_coverage = "partial"
            execution_reason = "No execution records were captured for earlier activity."
        else:
            execution_coverage = "unavailable"
            execution_reason = "No worker execution has been recorded for this issue."
    elif gaps:
        execution_coverage = "partial"
        execution_reason = "An unobserved restart gap is shown instead of a guessed duration."
    elif missing_categories:
        execution_coverage = "partial"
        execution_reason = "Records predate this timeline for: " + ", ".join(missing_categories) + "."

    artifacts = runner.artifacts(identifier)
    log_text, log_truncated = bounded_file(artifacts / "worker.log", LOG_BYTES)
    checks, checks_truncated = bounded_file(artifacts / "checks.sh", CHECKS_BYTES)
    diagnostics = {
        "workerLog": {
            "available": log_text is not None,
            "text": redact(log_text) if log_text is not None else "",
            "truncated": log_truncated,
            "detail": "" if log_text is not None else "No worker log is available.",
        },
        "checks": {
            "available": checks is not None,
            "command": redact(checks).strip() if checks is not None else "",
            "truncated": checks_truncated,
            "detail": "" if checks is not None else "No verification command is available.",
        },
        "verification": {
            "exit": task.get("check_exit") if task else None,
            "detail": redact(task.get("detail"))[:8000] if task else "",
            "available": bool(task) and task.get("check_exit") is not None,
        },
    }
    project = issue.get("project")
    ci_wall = union_duration(ci_intervals, now)
    ci_overlap = (max(0.0, breakdown["ci_workflow"] - ci_wall)
                  if breakdown["ci_workflow"] is not None else None)
    return {
        "issue": {
            "identifier": identifier, "title": issue.get("title", ""),
            "url": issue.get("url", ""), "state": state(issue.get("state")),
            "project": project.get("name", "") if isinstance(project, dict) else (project or ""),
        },
        "generatedAt": iso(now),
        "stages": stages,
        "execution": {
            "coverage": execution_coverage,
            "reason": execution_reason,
            "events": timeline,
            "breakdown": breakdown,
            "worker": worker,
            "missingCategories": missing_categories,
            "lowerBound": bool(missing_categories or boundary_gaps),
            "measuredSeconds": measured,
            "wallSeconds": wall,
            "overlapSeconds": max(0.0, measured - wall),
            "ci": {
                "coverage": ci_coverage,
                "reason": ci_reason,
                "runs": run_rows,
                "runCount": len({run["runId"] for run in run_rows}),
                "attemptCount": len(run_rows),
                "jobCount": len(jobs),
                "failureCount": failures,
                "expectedWorkflows": expected,
                "missingWorkflows": missing_paths,
                "currentRevision": current_revision,
                "boundaryGaps": boundary_gaps,
                "workflowSeconds": breakdown["ci_workflow"],
                "wallSeconds": ci_wall,
                "jobSeconds": job_value,
                "jobWallSeconds": job_wall if job_intervals else None,
                "jobLowerBound": bool(jobs) and (not jobs_complete or len(job_intervals) < len(jobs)),
                "jobCoverage": ("recorded" if jobs_complete and len(job_intervals) == len(jobs)
                                else "partial" if jobs else "unavailable"),
                "waitingSeconds": waiting,
                "discoverySeconds": discovery,
                "overlapSeconds": ci_overlap,
                "lowerBound": bool(boundary_gaps or incomplete_jobs),
            },
            "diagnostics": diagnostics,
        },
    }
