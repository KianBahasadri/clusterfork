"""One fenced repair per dispatch, reusing its branch and repository slot."""

import hashlib
import json
import logging
import re

from diagnostics import failure_summary
from dispatch_core import eligibility
from linear_sync import comment_id, failed_required_checks
from pending_writes import PendingWrites
from readiness_policy import issue_revision, redact_review_text
from selection_reporting import selection_changed
from worker_core import WorkerError
from worker_reporting import AUTO_REPAIR_LABEL, is_auto_repair, worker_branch


RECEIPT_FIELDS = ("is_auto_repair", "repair_parent_key", "repair_parent_issue_id",
                  "target_dispatch_key", "target_identifier", "target_branch", "target_pull_request",
                  "repair_attempted", "repair_issue_id", "repair_key", "repair_context")


def safe_text(value, limit=800):
    return " ".join(redact_review_text(value).split())[:limit].replace("`", "'")


def issue_url(issue):
    url = issue.get("url", "")
    if re.fullmatch(r"https://linear\.app/[\w-]+/issue/[\w-]+(?:/[\w-]+)?", url):
        return url
    return f"https://linear.app/issue/{issue['id']}"


def run_url(repository, owner):
    if re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", owner):
        run, attempt = owner.split(":")
        return f"https://github.com/{repository}/actions/runs/{run}/attempts/{attempt}"
    return ""


def github_url(repository, url):
    # Keep links to this repository only; never propagate credentials or queries.
    return url if re.fullmatch(re.escape(f"https://github.com/{repository}/")
                              + r"(?:pull/[1-9][0-9]*|actions/runs/[1-9][0-9]*(?:/(?:job|attempts)/[1-9][0-9]*)?)", url) else ""


class AutoRepair:
    def __init__(self, queue):
        self.queue = queue
        self.writes = PendingWrites("repair_report", queue.clock)
        self.lifecycle_notes = None

    def update(self, table, key, change):
        for _ in range(32):
            row, version = table.get(key)
            change(row)
            try:
                table.replace(row, version)
                return row
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Auto-repair reporting changed concurrently; retry")

    def approval(self, issue, route, row):
        """Only a durable, machine-generated child bypasses interactive readiness."""
        parent, _ = self.queue.table(route.repository).get(row["repair_parent_key"])
        context = json.loads(parent["repair_context"])
        if (not parent.get("repair_attempted") or parent.get("repair_issue_id") != issue["id"]
                or parent.get("repair_key") != row["RowKey"] or row["project_id"] != parent["project_id"]
                or row["repository"] != parent["repository"]
                or row["repair_parent_issue_id"] != parent["issue_id"]
                or parent.get("repair_revision") != issue_revision(issue)
                or any(row.get(name) != context[name] for name in
                       ("target_branch", "target_dispatch_key", "target_identifier", "target_pull_request"))):
            raise WorkerError("Auto-repair context does not match its fenced parent", 403)
        return {"review_key": "auto-repair:" + parent["RowKey"]}

    def mirror(self, row):
        # The queue transaction is authoritative. Mirror before external resources
        # so lifecycle reconciliation also yields ownership of the parent's status.
        for _ in range(32):
            receipt, etag = self.queue.receipts.get(row["RowKey"])
            receipt.update({name: row[name] for name in RECEIPT_FIELDS if name in row})
            try:
                self.queue.receipts.replace(receipt, etag)
                return
            except WorkerError as exc:
                if exc.status != 409:
                    raise
        raise WorkerError("Repair receipt changed concurrently; retry")

    def admit(self, table, state, etag, row, receipt, route, reason):
        q, key = self.queue, row["RowKey"]
        issue = q.linear.issue(row["issue_id"])
        extra = (route.linear_states.in_progress, route.linear_states.blocked) if route.linear_states else ()
        current, _ = eligibility(issue or {}, q.registry, additional_state_ids=extra)
        if (current != route or not route.linear_states or not route.ready_state_ids
                or is_auto_repair(issue, row) or is_auto_repair(issue, receipt)):
            return False
        if not row.get("repair_attempted"):
            if receipt.get("repair_attempted") or receipt.get("repair_issue_id"):
                return False  # A legacy budget without recoverable intent is never reset.
            run, _ = q.optional_run(key)
            runs = q.github.dispatch_runs(route, key)
            if any(run.get("status") != "completed" for run in runs):
                return False
            owner = state["owner"] or (run or {}).get("owner", "")
            if runs:
                latest = max(runs, key=lambda r: (r["id"], r["run_attempt"]))
                owner = f"{latest['id']}:{latest['run_attempt']}"
            pull = q.github.worker_pull(route, receipt, key, (run or {}).get("pull_request", ""))
            checks = failed_required_checks(pull) if pull and pull.get("state") == "OPEN" else []
            trigger = "Required checks failed" if checks else f"Worker execution or validation failed ({route.workflow})"
            if checks:
                details = []
                for name, url in checks:
                    name = safe_text(name, 120).replace("[", "(").replace("]", ")")
                    link = github_url(route.repository, url)
                    details.append(f"[{name}]({link})" if link else name)
                trigger += ": " + ", ".join(sorted(set(details))[:8])
            failed_run = run_url(route.repository, owner)
            if failed_run:
                trigger += f". [Failed worker run]({failed_run})"
            summary = failure_summary(json.loads(run["failure"])) if run and run.get("failure") else safe_text(reason)
            identifier = comment_id(key, "auto-repair-issue")
            repair_key = hashlib.sha256(f"{q.registry.generation}:{route.project_id}:{identifier}".encode()).hexdigest()
            context = {"parent_identifier": issue["identifier"], "parent_url": issue_url(issue),
                       "team_id": issue["team"]["id"], "trigger": trigger, "summary": summary,
                       "target_branch": worker_branch(receipt["identifier"], key),
                       "target_dispatch_key": key, "target_identifier": receipt["identifier"],
                       "target_pull_request": github_url(route.repository, (pull or {}).get("url", "")),
                       "failed_run_url": failed_run,
                       "short_reason": "required checks failed" if checks else "worker failed"}
            latest, version = table.get(key)
            if latest.get("repair_attempted"):
                raise WorkerError("Repair was claimed concurrently; retry")
            latest.update(repair_attempted=True, repair_issue_id=identifier, repair_key=repair_key,
                          repair_context=json.dumps(context))
            # Fence both worker startup and competing observers BEFORE Linear creation.
            table.transaction([(state, etag), (latest, version)])
            row = latest
            slot, etag = table.get("slot")
            if slot["active_key"] != key or slot["owner"] != state["owner"]:
                raise WorkerError("Parent worker changed during repair fencing")
        if not row.get("repair_issue_id") or not row.get("repair_context"):
            return False
        self.mirror(row)
        if self.lifecycle_notes:
            note, _ = q.optional_entry(self.lifecycle_notes, key)
            if note and note.get("lease_until", 0) > q.clock():
                return False  # Drain lifecycle writes that began before receipt handoff.
        context = json.loads(row["repair_context"])
        # An interrupted intent cannot launch alongside a late/manual parent run.
        runs = q.github.dispatch_runs(route, key)
        run, _ = q.optional_run(key)
        known_owner = state["owner"] or (run or {}).get("owner", "")
        if any(run.get("status") != "completed" for run in runs) or (
                known_owner and q.github.worker_result(route.repository, known_owner).get("status") != "completed"):
            return False
        description = (
            f"Repair [{context['parent_identifier']}]({context['parent_url']}) using its existing worker branch "
            f"`{context['target_branch']}` in `{route.repository}` (base `{route.ref}`).\n\n"
            f"Triggered by: {context['trigger']}.\n\nSanitized failure summary: {context['summary']}\n\n"
            + (f"Existing PR: {context['target_pull_request']}\n\n" if context['target_pull_request'] else "")
            + "Investigate the failure, fix the code/tests on this branch, run all configured validation commands, "
            "and update the existing PR or create it if publication failed. The worker publishes the fix. "
            "Read AGENTS.md and the owning repository docs. Respect the broker's workflow-publication capability. "
            "Never bypass merge requirements with --admin, change authentication, or include secrets, credentials, raw logs, "
            "or environment dumps in issues or comments.\n\n"
            "This is the parent's single automatic repair attempt. Do not create another repair task or retry a failed repair."
        )
        label = q.linear.stage_label(context["team_id"], AUTO_REPAIR_LABEL)
        title = f"Auto-repair: {context['parent_identifier']} ({context['short_reason']})"
        repair = q.linear.create_issue({"id": row["repair_issue_id"], "teamId": context["team_id"],
                                        "projectId": route.project_id, "parentId": row["issue_id"],
                                        "stateId": route.ready_state_ids[0], "labelIds": [label], "priority": 2,
                                        "title": title,
                                        "description": description})
        if not repair or repair["id"] != row["repair_issue_id"] or repair["project"]["id"] != route.project_id:
            raise WorkerError("Created repair issue does not match its durable intent")
        # A webhook can already have enqueued this deterministic issue. Adopt it
        # atomically with the parent's slot; never leave the repository free.
        repair_key = row["repair_key"]
        child, version = q.optional_entry(table, repair_key)
        child = child or {"RowKey": repair_key, "sequence": row["sequence"], "queued_at": q.clock()}
        child.update(status="active", priority=2, selected_at=q.clock(), is_auto_repair=True,
                     issue_id=repair["id"], identifier=repair["identifier"], project_id=route.project_id,
                     repository=route.repository, repair_parent_key=key, repair_parent_issue_id=row["issue_id"],
                     **{name: context[name] for name in
                        ("target_branch", "target_dispatch_key", "target_identifier", "target_pull_request")})
        selection_changed(child)
        parent, parent_version = table.get(key)
        parent.update(status="repairing", repair_identifier=repair["identifier"], repair_issue_url=issue_url(repair),
                      repair_revision=issue_revision({"id": row["repair_issue_id"], "project": {"id": route.project_id},
                                                      "title": title, "description": description}))
        selection_changed(parent)
        # Use the pre-observation ETag. A concurrent worker start invalidates this handoff.
        state.update(active_key=repair_key, owner="")
        table.transaction([(state, etag), (parent, parent_version), (child, version)])
        q.selection.sync(table, repair_key)
        return True

    def finish(self, table, state, etag, child, route, action, reason):
        q = self.queue
        parent, parent_version = table.get(child["repair_parent_key"])
        outcome = "completed" if action == "completed" else "blocked"
        if parent.get("repair_outcome") == "completed":
            return True
        if parent.get("repair_outcome") == outcome:
            return
        run, _ = q.optional_run(child["RowKey"])
        pull = q.github.worker_pull(route, child, child["RowKey"], (run or {}).get("pull_request", ""))
        context = json.loads(parent["repair_context"])
        worker_url = run_url(route.repository, (run or {}).get("owner", "") or state["owner"])
        if not worker_url:
            runs = q.github.dispatch_runs(route, child["RowKey"])
            if runs:
                latest = max(runs, key=lambda r: (r["id"], r["run_attempt"]))
                worker_url = run_url(route.repository, f"{latest['id']}:{latest['run_attempt']}")
        if not worker_url:
            return  # Ambiguous dispatch is held for discovery, not a terminal attempt.
        pull_url = github_url(route.repository, (pull or {}).get("url", ""))
        diagnostic = failure_summary(json.loads(run["failure"])) if run and run.get("failure") else ""
        validation = ("All configured validation commands passed." if run and run.get("repair_validation_passed")
                      else "Configured validation was not confirmed by the repair worker.")
        outcome_text = "Fixed & Merged (Done)" if outcome == "completed" else "Repair Failed (Blocked)"
        summary = ("The existing worker branch/PR is resolved. " if outcome == "completed" else "The single repair attempt did not resolve the failure. ")
        if run and run.get("repair_result"):
            result = json.loads(run["repair_result"])
            summary += (f"Prepared commit `{result['commit_sha']}` with edits to {result['changed_files']} file(s) "
                        f"on `{context['target_branch']}`. ")
        summary += safe_text(reason) + " " + validation + (" " + diagnostic if diagnostic else "")
        body = ((q.registry.notify_user_url + "\n\n") if q.registry.notify_user_url else "")
        body += (f"## Auto-Repair Report: {outcome_text}\n\n"
                 f"**Triggered by:** {context['trigger']}. {context['summary']}\n\n"
                 f"**Repair issue & run:** [{parent['repair_identifier']}]({parent['repair_issue_url']}); "
                 f"[repair worker run]({worker_url})"
                 + (f"; [PR]({pull_url})" if pull_url else "; no open PR remains")
                 + f".\n\n**Outcome summary:** {summary}\n\n**Queue status:** "
                 + ("Both issues are Done. The repository queue is unblocked and waiting Todo work can flow again."
                    if outcome == "completed" else
                    "Both issues are Blocked awaiting human review. The repository slot remains held; no further automatic repair or failed-worker retry is allowed."))
        current, version = table.get(child["RowKey"])
        parent.update(status=outcome, repair_outcome=outcome, repair_report_body=body, repair_report_pending=True)
        current.update(status=outcome, reason=safe_text(reason), repair_outcome=outcome)
        if outcome == "completed":
            state.update(active_key="", owner="")
            selection_changed(parent)
            selection_changed(current)
        table.transaction([(state, etag), (parent, parent_version), (current, version)])
        if outcome == "completed":
            # A waiting task can depend on this parent's Linear completed-type
            # state. Project Done before the next admission's dependency read.
            try:
                self.reconcile(table, only_states=True)
            except Exception:
                logging.error("Auto-repair completion state deferred for %s", parent["RowKey"])
        return True

    def reconcile(self, table, *, only_states=False):
        """Retry each terminal projection independently, even after slot release."""
        for parent in table.list():
            if parent.get("status") == "repairing" and not parent.get("repair_outcome"):
                self.progress(table, parent)
                continue
            if not parent.get("repair_report_pending"):
                continue
            route = self.queue.registry.routes.get(parent["project_id"])
            if not route or not route.linear_states:
                continue
            signature = hashlib.sha256(parent["repair_report_body"].encode()).hexdigest()
            target = route.linear_states.done if parent["repair_outcome"] == "completed" else route.linear_states.blocked
            # Older/abandoned writers can still have changed Linear after their
            # last acknowledgement. Reapply all projections while retiring them.
            force = bool(parent.get(self.writes.inflight))
            token = None
            def begin(row):
                nonlocal token
                token = self.writes.begin(row)
            self.update(table, parent["RowKey"], begin)
            errors = False
            for kind, key, issue in (("parent", parent["RowKey"], parent["issue_id"]),
                                     ("child", parent["repair_key"], parent["repair_issue_id"])):
                for projection, write in (
                    ("state", lambda: self.queue.linear.set_state(issue, target)),
                    ("stage", lambda: self.queue.linear.set_stage(issue, None)),
                    ("comment", lambda: self.queue.linear.status_comment(
                        issue, comment_id(key, "auto-repair-report"), parent["repair_report_body"]))):
                    if only_states and projection != "state":
                        continue
                    field = f"repair_report_{kind}_{projection}"
                    try:
                        latest, _ = table.get(parent["RowKey"])
                        if latest.get("repair_report_body") != parent["repair_report_body"]:
                            break
                        if latest.get(field) == signature and not force:
                            continue
                        write()
                        def acknowledge(row):
                            if row.get("repair_report_body") == parent["repair_report_body"]:
                                row[field] = signature
                        self.update(table, parent["RowKey"], acknowledge)
                    except Exception:
                        errors = True
                        logging.error("Auto-repair report deferred for %s", parent["RowKey"])
            def finish(row):
                superseded = row.get("repair_report_body") != parent["repair_report_body"]
                if superseded:
                    self.invalidate_report(row)
                retry = errors or superseded or not all(
                    row.get(f"repair_report_{kind}_{projection}") == signature
                    for kind in ("parent", "child") for projection in ("state", "stage", "comment"))
                self.writes.finish(row, token, retry=retry)
            self.update(table, parent["RowKey"], finish)

    @staticmethod
    def invalidate_report(row):
        for kind in ("parent", "child"):
            for projection in ("state", "stage", "comment"):
                row.pop(f"repair_report_{kind}_{projection}", None)

    def progress(self, table, parent):
        q = self.queue
        route = q.registry.routes.get(parent["project_id"])
        if not route or not route.linear_states:
            return
        run, _ = q.optional_run(parent["repair_key"])
        status = (run or {}).get("status")
        stage = "waiting_for_quota" if status == "waiting_for_quota" else (
            "review" if status == "completed" else (run or {}).get("stage", "initializing"))
        target = route.ready_state_ids[0] if status == "waiting_for_quota" else route.linear_states.in_progress
        signature = json.dumps([target, stage])
        if parent.get("repair_progress_signature") == signature and not parent.get("repair_report_pending"):
            return
        token = None
        try:
            def begin(row):
                nonlocal token
                token = self.writes.begin(row)
            self.update(table, parent["RowKey"], begin)
            for issue_id, state in ((parent["issue_id"], route.linear_states.in_progress),
                                    (parent["repair_issue_id"], target)):
                issue = q.linear.issue(issue_id)
                current, _ = eligibility(issue or {}, q.registry, additional_state_ids=(
                    route.linear_states.in_progress, route.linear_states.blocked))
                if current != route:
                    def stop(row):
                        if row.get("repair_outcome"):
                            self.invalidate_report(row)
                        self.writes.finish(row, token, retry=bool(row.get("repair_outcome")))
                    self.update(table, parent["RowKey"], stop)
                    return
                if issue["state"]["id"] != state:
                    q.linear.set_state(issue_id, state)
            q.linear.set_stage(parent["repair_issue_id"], stage)
            def finish(row):
                if not row.get("repair_outcome"):
                    row["repair_progress_signature"] = signature
                else:
                    self.invalidate_report(row)
                self.writes.finish(row, token, retry=bool(row.get("repair_outcome")))
            self.update(table, parent["RowKey"], finish)
        except Exception:
            if token:
                def defer(row):
                    if row.get("repair_outcome"):
                        self.invalidate_report(row)
                    self.writes.finish(row, token, retry=True)
                self.update(table, parent["RowKey"], defer)
            logging.error("Auto-repair progress deferred for %s", parent["RowKey"])
