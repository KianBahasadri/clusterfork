"""Bind verified OIDC claims to an immutable dispatch and one signed job role."""

import re

from .model import Rejected, Role
from .workflows import ENGINE, ROLE_FILES, WORKFLOW

SELECTOR_ACTIONS = {"start", "heartbeat", "accounts", "claim", "save", "bind_account", "finish"}
REVIEW_ACTIONS = {"start", "heartbeat", "accounts", "claim", "save", "checkout", "review", "finish"}
CODING_ACTIONS = {"start", "heartbeat", "accounts", "claim", "save", "bind_account", "exhaust_account",
                  "checkout", "github", "progress", "finish", "steering_open", "instructions",
                  "instruction_result", "steering_close"}


def release_ref(revision):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise Rejected("Invalid runtime revision")
    return "refs/tags/clusterfork-runtime-" + revision


def authorize_job(claims, view, route, action):
    """The HTTP adapter must verify JWT signature/audience/expiry before this call."""
    if (not view.spec or route.project_id != view.project_id or route.repository.lower() != view.repository):
        raise Rejected("Task target is no longer mapped")
    revision = view.spec.runtime_sha
    ref = release_ref(revision)
    expected = {
        "repository": ENGINE, "ref": ref, "sha": revision,
        "event_name": "workflow_dispatch", "runner_environment": "github-hosted",
        "workflow_ref": f"{ENGINE}/.github/workflows/{WORKFLOW}@{ref}",
        "job_workflow_sha": revision,
    }
    if any(claims.get(key) != value for key, value in expected.items()):
        raise Rejected("Signed workflow identity does not match the admitted release")
    for name in ("run_id", "run_attempt", "repository_id"):
        if not re.fullmatch(r"[1-9][0-9]*", str(claims.get(name, ""))):
            raise Rejected("Invalid authenticated run identity")
    role = next((Role(name) for name, path in ROLE_FILES.items() if claims.get("job_workflow_ref") in {
        f"{ENGINE}/.github/workflows/{path}@{ref}", f"{ENGINE}/.github/workflows/{path}@{revision}"}), None)
    if role is None:
        raise Rejected("No signed phase role was granted")
    allowed = SELECTOR_ACTIONS if role.selector else REVIEW_ACTIONS if role == Role.REVIEW else CODING_ACTIONS
    if action not in allowed:
        raise Rejected("This signed job role cannot perform that operation")
    return role


def verify_review_job(github, run_id, run_attempt):
    """Use the exact attempt's actual jobs; a workflow output cannot grant coding."""
    if any(not re.fullmatch(r"[1-9][0-9]*", str(n)) for n in (run_id, run_attempt)):
        raise Rejected("Invalid run attempt")
    token = github.installation_token(ENGINE, permissions={"actions": "read"})
    page, seen, matches = 1, 0, []
    while True:
        result = github.request(f"https://api.github.com/repos/{ENGINE}/actions/runs/{run_id}/attempts/{run_attempt}/jobs?per_page=100&page={page}",
                                token=f"Bearer {token}")
        jobs = result["jobs"]
        if not isinstance(jobs, list):
            raise Rejected("Incomplete review-job evidence")
        matches.extend(job for job in jobs if job.get("name") == "review / run")
        seen += len(jobs)
        if len(jobs) < 100:
            if seen < result["total_count"]:
                raise Rejected("Incomplete review-job evidence")
            break
        page += 1
    return bool(len(matches) == 1 and matches[0].get("status") == "completed" and matches[0].get("conclusion") == "success")
