"""Linear enrollment and routing rules, independent of Azure and network clients."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from quota_selection import SelectionPolicy


def uuid_string(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("expected a canonical UUID")
    return value


@dataclass(frozen=True)
class Route:
    project_id: str
    repository: str
    workflow: str
    ref: str
    ready_state_ids: tuple[str, ...]


@dataclass(frozen=True)
class AgentProfile:
    model: str
    effort: str


@dataclass(frozen=True)
class Registry:
    enrollment_label: str
    generation: str
    agent_profiles: dict[str, AgentProfile]
    agent_selection: SelectionPolicy
    routes: dict[str, Route]

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text())
        label, generation = data["enrollment_label"], data["generation"]
        if not isinstance(label, str) or not label.strip():
            raise ValueError("enrollment_label must be nonempty")
        if not isinstance(generation, str) or not re.fullmatch(r"[\w.-]{1,64}", generation):
            raise ValueError("invalid generation")
        profiles = {}
        for agent, row in data["agent_profiles"].items():
            efforts = {"antigravity": {"low", "medium", "high"},
                       "codex": {"low", "medium", "high", "xhigh", "max", "ultra"}}
            if agent not in efforts:
                raise ValueError(f"unsupported agent profile: {agent}")
            if not isinstance(row["model"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]+", row["model"]):
                raise ValueError(f"invalid model for {agent}")
            if row["effort"] not in efforts[agent]:
                raise ValueError(f"unsupported effort for {agent}")
            profiles[agent] = AgentProfile(row["model"], row["effort"])
        if set(profiles) != {"antigravity", "codex"}:
            raise ValueError("configure both Antigravity and Codex profiles explicitly")
        routes = {}
        for row in data["projects"]:
            project_id = uuid_string(row["project_id"])
            if project_id in routes:
                raise ValueError("duplicate project mapping")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", row["repository"]):
                raise ValueError("invalid GitHub owner/repository")
            if not re.fullmatch(r"[A-Za-z0-9_-]+\.ya?ml", row["workflow"]):
                raise ValueError("workflow must be a workflow filename")
            if not isinstance(row["ref"], str) or not re.fullmatch(r"[\w./-]+", row["ref"]):
                raise ValueError("invalid workflow ref")
            states = tuple(uuid_string(s) for s in row["ready_state_ids"])
            if not states:
                raise ValueError("a project needs at least one ready state")
            routes[project_id] = Route(project_id, row["repository"], row["workflow"],
                                       row["ref"], states)
        return cls(label, generation, profiles, SelectionPolicy.load(data["agent_selection"]), routes)


class WebhookError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def webhook_event(body, headers, secret, *, now=None):
    """Authenticate raw bytes and return a minimal queue message, or None to ignore."""
    if not secret:
        raise WebhookError(503, "webhook secret is not configured")
    if len(body) > 1024 * 1024:
        raise WebhookError(413, "webhook body is too large")
    headers = {k.lower(): v for k, v in headers.items()}
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    signature = headers.get("linear-signature", "")
    if (not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature)
            or not hmac.compare_digest(expected, signature)):
        raise WebhookError(401, "invalid webhook signature")
    try:
        payload = json.loads(body)
        timestamp = payload["webhookTimestamp"]
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise ValueError("invalid timestamp")
        clock = time.time() if now is None else now
        if not abs(clock * 1000 - timestamp) <= 300_000:
            raise WebhookError(401, "expired webhook")
        if payload["type"] not in {"Issue", "Project"}:
            return None
        if payload["action"] not in {"create", "update"}:
            return None
        return {"type": payload["type"], "id": uuid_string(payload["data"]["id"])}
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, WebhookError):
            raise
        raise WebhookError(400, "invalid webhook payload") from None


def eligibility(issue, registry):
    """Return (route, reason) using current Linear state, never webhook snapshots."""
    project = issue.get("project")
    if not project:
        return None, "no_project"
    route = registry.routes.get(project["id"])
    if not route:
        return None, "unmapped_project"
    if issue.get("archivedAt") or project.get("archivedAt"):
        return None, "archived"
    labels = project["labels"]["nodes"]
    if not any(label["name"] == registry.enrollment_label for label in labels):
        return None, "project_not_enrolled"
    if issue["state"]["id"] not in route.ready_state_ids:
        return None, "not_ready"
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", issue["identifier"]):
        raise ValueError("invalid Linear issue identifier")
    uuid_string(issue["id"])
    return route, "eligible"


class DispatchError(RuntimeError):
    """Uncertain means GitHub might have accepted a dispatch before the error."""

    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain


class Dispatcher:
    def __init__(self, registry, linear, github, receipts):
        self.registry = registry
        self.linear = linear
        self.github = github
        self.receipts = receipts

    def process(self, event):
        event_type = event.get("type")
        if event_type == "Issue":
            issue = self.linear.issue(uuid_string(event["id"]))
            return [self.route(issue)] if issue else [{"status": "missing_issue"}]
        if event_type == "Project":
            project_id = uuid_string(event["id"])
            if project_id not in self.registry.routes:
                return [{"status": "unmapped_project"}]
            return [self.route(issue) for issue in self.linear.project_issues(project_id)]
        raise ValueError("unsupported queued event")

    def route(self, issue):
        route, reason = eligibility(issue, self.registry)
        if not route:
            return {"status": reason}
        # Pilot: one smoke run per issue and generation. Editing a Todo issue must
        # not launch another run. Production retries require a separate run model.
        key = hashlib.sha256(
            f"{self.registry.generation}:{route.project_id}:{issue['id']}".encode()
        ).hexdigest()
        fields = {"issue_id": issue["id"], "identifier": issue["identifier"],
                  "repository": route.repository, "project_id": route.project_id}
        if not self.receipts.claim(key, fields):
            return {"status": "already_claimed", "dispatch_key": key}
        inputs = {"linear_issue_id": issue["id"], "linear_identifier": issue["identifier"],
                  "linear_project_id": route.project_id, "dispatch_key": key}
        try:
            self.github.dispatch(route, inputs)
        except DispatchError as exc:
            if exc.uncertain:
                # Do not blindly resend after a timeout: the first request may
                # already be queued by GitHub. Keep this receipt for inspection.
                self.receipts.finish(key, "uncertain")
                return {"status": "uncertain", "dispatch_key": key}
            self.receipts.release(key)
            raise
        # A crash here leaves a claim instead of risking a duplicate dispatch.
        self.receipts.finish(key, "dispatched")
        return {"status": "dispatched", "dispatch_key": key, **fields}
