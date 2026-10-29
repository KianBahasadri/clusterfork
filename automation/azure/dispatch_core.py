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
class LinearStates:
    in_progress: str
    blocked: str
    done: str


@dataclass(frozen=True)
class Route:
    project_id: str
    repository: str
    workflow: str
    ref: str
    ready_state_ids: tuple[str, ...]
    validation_commands: tuple[tuple[str, ...], ...] = ()
    auto_merge: bool = True
    linear_states: LinearStates | None = None
    agent_timeout_minutes: int = 300


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
        if "codex" not in profiles:
            raise ValueError("configure the Codex profile explicitly")
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
            auto_merge = row.get("auto_merge", True)
            if not isinstance(auto_merge, bool):
                raise ValueError("auto_merge must be a boolean")
            agent_timeout = row.get("agent_timeout_minutes", 300)
            if isinstance(agent_timeout, bool) or not isinstance(agent_timeout, int) or not 1 <= agent_timeout <= 330:
                raise ValueError("agent_timeout_minutes must be an integer from 1 to 330")
            linear_states = None
            if "linear_states" in row:
                configured = row["linear_states"]
                if not isinstance(configured, dict) or set(configured) != {"in_progress", "blocked", "done"}:
                    raise ValueError("linear_states needs in_progress, blocked, and done")
                linear_states = LinearStates(**{name: uuid_string(value) for name, value in configured.items()})
                values = (linear_states.in_progress, linear_states.blocked, linear_states.done)
                if len(set(values)) != 3 or set(values).intersection(states):
                    raise ValueError("Linear lifecycle states must be distinct from each other and ready states")
            commands = row.get("validation_commands", [])
            if not isinstance(commands, list) or any(
                not isinstance(command, list) or not command
                or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command)
                for command in commands
            ):
                raise ValueError("validation_commands must be arrays of command arguments")
            routes[project_id] = Route(project_id, row["repository"], row["workflow"],
                                       row["ref"], states, tuple(tuple(c) for c in commands), auto_merge,
                                       linear_states, agent_timeout)
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


def eligibility(issue, registry, *, additional_state_ids=()):
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
    if issue["state"].get("type") == "canceled":
        return None, "canceled"
    if issue["state"]["id"] not in (*route.ready_state_ids, *additional_state_ids):
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
    def __init__(self, registry, linear, queue):
        self.registry = registry
        self.linear = linear
        self.queue = queue

    def process(self, event):
        event_type = event.get("type")
        if event_type == "Issue":
            issue = self.linear.issue(uuid_string(event["id"]))
            return [self.route(issue)] if issue else [{"status": "missing_issue"}]
        if event_type == "Project":
            project_id = uuid_string(event["id"])
            if project_id not in self.registry.routes:
                return [{"status": "unmapped_project"}]
            # Persist the complete scan before dispatching its head. Repeated
            # scans retain their original sequence; batch ties are deterministic.
            issues = sorted(self.linear.project_issues(project_id),
                            key=lambda i: (i.get("createdAt", ""), i["id"]))
            return self.route_issues(issues)
        raise ValueError("unsupported queued event")

    def route(self, issue):
        return self.route_issues([issue])[0]

    def route_issues(self, issues):
        results, repositories = [], set()
        for issue in issues:
            route, reason = eligibility(issue, self.registry)
            if route:
                key = self.queue.enqueue(issue, route)
                results.append({"status": "already_claimed", "dispatch_key": key})
            else:
                results.append({"status": reason})
            mapped = self.registry.routes.get((issue.get("project") or {}).get("id"))
            if mapped:
                repositories.add(mapped.repository.lower())
        advanced = {}
        for repository in sorted(repositories):
            advanced.update(self.queue.advance(repository))
        return [advanced.get(result.get("dispatch_key"), result) for result in results]
