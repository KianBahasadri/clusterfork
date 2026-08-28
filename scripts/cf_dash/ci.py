"""GitHub Actions CI state for the repo HEAD, via the gh CLI (optional).

Every path returns None ("no CI to report"): gh missing, origin not on
GitHub, no HEAD sha, or no check runs for the sha. Failures are swallowed —
the header simply omits the indicator.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

GH_TIMEOUT = 6.0  # s

_REMOTE_RE = re.compile(
    r"github\.com[:/](?P<owner>[A-Za-z0-9_.-]+)/(?P<name>.+?)(?:\.git)?/?$")

_BAD_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required",
                    "startup_failure"}


def github_slug(repo: Path) -> tuple[str, str] | None:
    """(owner, name) parsed from the origin remote, if it points at GitHub."""
    try:
        url = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5, check=True).stdout.strip()
    except Exception:
        return None
    m = _REMOTE_RE.search(url)
    if not m:
        return None
    return m.group("owner"), m.group("name")


def github_ci_state(repo: Path, head: str) -> str | None:
    """"passing" | "failing" | "running" for the HEAD sha, else None."""
    if not head or shutil.which("gh") is None:
        return None
    slug = github_slug(repo)
    if slug is None:
        return None
    try:
        out = subprocess.run(
            ["gh", "api",
             f"repos/{slug[0]}/{slug[1]}/commits/{head}/check-runs"],
            capture_output=True, text=True, timeout=GH_TIMEOUT,
            check=True).stdout
        runs = json.loads(out).get("check_runs") or []
    except Exception:
        return None
    if not runs:
        return None
    if any(r.get("status") != "completed" for r in runs):
        return "running"
    if any(r.get("conclusion") in _BAD_CONCLUSIONS for r in runs):
        return "failing"
    return "passing"
