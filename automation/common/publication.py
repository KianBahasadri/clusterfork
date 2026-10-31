"""Project-scoped Linear lifecycle updates from durable workers and actual PRs."""

from __future__ import annotations

import hashlib
from uuid import UUID

from common.policy import redact_review_text


def comment_id(key, kind):
    # Linear accepts client-supplied UUIDv4 IDs. Stable IDs recover ambiguous writes.
    digest = hashlib.sha256(f"clusterfork:{key}:{kind}".encode()).digest()
    return str(UUID(bytes=digest[:16], version=4))


def failed_required_checks(pull):
    """Only completed, failing required checks qualify for automatic repair."""
    commits = pull.get("commits", {}).get("nodes", [])
    rollup = commits[-1]["commit"].get("statusCheckRollup") if commits else None
    if not rollup:
        return []
    contexts = rollup["contexts"]
    if contexts["pageInfo"]["hasNextPage"]:
        raise ValueError("Incomplete PR check results")
    required = [check for check in contexts["nodes"] if check["isRequired"]]
    failures = []
    for check in required:
        if check["__typename"] == "CheckRun":
            if check["status"] != "COMPLETED":
                return []
            if check["conclusion"] in {"FAILURE", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}:
                failures.append((check["name"], check.get("detailsUrl", "")))
        elif check["__typename"] == "StatusContext":
            if check["state"] in {"PENDING", "EXPECTED"}:
                return []
            if check["state"] in {"FAILURE", "ERROR"}:
                failures.append((check["context"], check.get("targetUrl", "")))
        else:
            raise ValueError("Unknown PR check type")
    return failures


def pull_outcome(pull, auto_merge):
    """Return a lifecycle state and explanation; pending CI is normal progress."""
    if pull.get("merged"):
        return "done", "Pull request merged."
    if pull.get("state") == "CLOSED":
        return "blocked", "Pull request was closed without merging."
    if pull.get("state") != "OPEN":
        raise ValueError("Unknown pull request state")
    if pull.get("isDraft"):
        return "blocked", "Pull request is a draft and cannot merge yet."
    if pull.get("mergeable") == "CONFLICTING" or pull.get("mergeStateStatus") == "DIRTY":
        return "blocked", "Pull request has merge conflicts."
    commits = pull.get("commits", {}).get("nodes", [])
    rollup = commits[-1]["commit"].get("statusCheckRollup") if commits else None
    if not rollup:
        return "in_progress", "Waiting for required CI checks to appear."
    contexts = rollup["contexts"]
    if contexts["pageInfo"]["hasNextPage"]:
        raise ValueError("Incomplete PR check results")
    required = [check for check in contexts["nodes"] if check["isRequired"]]
    if not required:
        return "in_progress", "Waiting for required CI checks to appear."
    pending, failures = False, []
    for check in required:
        if check["__typename"] == "CheckRun":
            if check["status"] != "COMPLETED":
                pending = True
            elif check["conclusion"] not in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
                failures.append(check["name"])
        elif check["__typename"] == "StatusContext":
            if check["state"] in {"PENDING", "EXPECTED"}:
                pending = True
            elif check["state"] in {"FAILURE", "ERROR"}:
                failures.append(check["context"])
            elif check["state"] != "SUCCESS":
                raise ValueError("Unknown commit status")
        else:
            raise ValueError("Unknown PR check type")
    if pending:
        return "in_progress", "Waiting for required CI checks."
    if failures:
        names = ", ".join(sorted(set(" ".join(redact_review_text(name).split())[:120] for name in failures)))
        return "blocked", f"Required CI checks failed or were canceled: {names[:600]}."
    if auto_merge:
        if pull.get("mergeStateStatus") == "BEHIND":
            return "blocked", "GitHub requires the PR branch to be updated before merging."
        if pull.get("mergeable") == "UNKNOWN" or pull.get("mergeStateStatus") == "UNKNOWN":
            return "in_progress", "GitHub is calculating mergeability."
        if not pull.get("autoMergeRequest"):
            return "blocked", "Automatic merge is not enabled on this PR."
        if pull.get("mergeStateStatus") == "BLOCKED":
            return "blocked", "GitHub branch requirements are preventing the merge."
        return "in_progress", "Required CI passed; waiting for GitHub to merge the PR."
    return "in_progress", "Required CI passed; the project is configured for manual merging."
