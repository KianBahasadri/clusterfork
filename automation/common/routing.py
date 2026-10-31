"""Linear enrollment and routing rules, independent of Azure and network clients."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from common.selection import SelectionPolicy
from common.timing import timestamps


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
    worker_revision: str = ""
    required_checks: tuple[str, ...] = ()


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
    steering_user_ids: tuple[str, ...] = ()
    notify_user_url: str = ""

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
                       "cursor": {"high"},
                       "codex": {"low", "medium", "high", "xhigh", "max", "ultra"}}
            if agent not in efforts:
                raise ValueError(f"unsupported agent profile: {agent}")
            if not isinstance(row["model"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]+", row["model"]):
                raise ValueError(f"invalid model for {agent}")
            if row["effort"] not in efforts[agent]:
                raise ValueError(f"unsupported effort for {agent}")
            if agent == "cursor" and row["model"] != "grok-4.7-high":
                raise ValueError("Cursor requires the exact grok-4.7-high / high profile")
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
            worker_revision = row.get("worker_revision", "")
            if not isinstance(worker_revision, str) or (worker_revision and not re.fullmatch(r"[0-9a-f]{40}", worker_revision)):
                raise ValueError("worker_revision must be a full commit SHA")
            checks = row.get("required_checks", [])
            if (not isinstance(checks, list) or any(not isinstance(c, str) or not c.strip() or "\n" in c for c in checks)
                    or len(set(checks)) != len(checks)):
                raise ValueError("required_checks must be distinct nonempty check names")
            if (worker_revision or row['workflow']=='clusterfork-task.yml') and (not commands or not checks or not linear_states):
                raise ValueError("shared workers require validation commands, required checks, and lifecycle states")
            routes[project_id] = Route(project_id, row["repository"], row["workflow"],
                                       row["ref"], states, tuple(tuple(c) for c in commands), auto_merge,
                                       linear_states, agent_timeout, worker_revision, tuple(checks))
        steering = data.get("steering", {})
        if not isinstance(steering, dict) or set(steering) - {"trusted_user_ids"}:
            raise ValueError("steering supports only trusted_user_ids")
        users = steering.get("trusted_user_ids", [])
        if not isinstance(users, list):
            raise ValueError("steering.trusted_user_ids must be an array of Linear user UUIDs")
        notify = data.get("notify_user_url", "")
        if not isinstance(notify, str) or (notify and not re.fullmatch(
                r"https://linear\.app/[A-Za-z0-9_-]+/profiles/[A-Za-z0-9_-]+", notify)):
            raise ValueError("notify_user_url must be a Linear profile HTTPS URL")
        return cls(label, generation, profiles, SelectionPolicy.load(data["agent_selection"]), routes,
                   tuple(uuid_string(user) for user in users), notify)


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
        if payload["type"] not in {"Issue", "Project", "Comment", "IssueRelation"}:
            return None
        if payload["action"] not in {"create", "update"} and not (
                payload["action"] == "remove" and payload["type"] in {"Issue", "IssueRelation"}):
            return None
        event = {"type": payload["type"], "id": uuid_string(payload["data"]["id"]),
                 "webhook_at": timestamp / 1000}
        # Only signed, explicit human state transitions can resubmit an assessed
        # issue. Preserve action time, not delivery time, across redeliveries.
        actor = payload.get('actor') or {}
        previous = payload.get('updatedFrom') or {}
        if (payload['type'] == 'Issue' and payload['action'] == 'update'
                and actor.get('type') == 'user' and actor.get('isBot') is not True
                and 'stateId' in previous and previous['stateId'] != payload['data'].get('stateId')):
            try:
                changed_at = datetime.fromisoformat(payload['createdAt'].replace('Z', '+00:00'))
                if changed_at.tzinfo is not None:
                    event['submission'] = {'actor_id': uuid_string(actor['id']),
                                           'from_state': uuid_string(previous['stateId']),
                                           'to_state': uuid_string(payload['data']['stateId']),
                                           'changed_at': changed_at.timestamp()}
            except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
                pass  # Incomplete actor/state evidence is only a wake-up hint.
        if payload["type"] == "IssueRelation":
            # Wake-up hints only. Never decide eligibility from a webhook's
            # relation type or prerequisite state, including removal events.
            event["issue_ids"] = [uuid_string(payload["data"][name])
                                  for name in ("issueId", "relatedIssueId") if payload["data"].get(name)]
        # Diagnostic only: eligibility always comes from a fresh Linear read.
        updated = payload["data"].get("updatedAt")
        if payload["type"] == "Issue" and isinstance(updated, str):
            try:
                parsed = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    event.update(timestamps({"source_updated_at": parsed.timestamp()}))
            except (ValueError, OverflowError):
                pass
        return event
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


def dependency_status(relations):
    """Return prerequisite IDs and a stable hold reason from incoming edges.

    Only an actual completed-type source satisfies a blocks relationship.
    Names, labels, project enrollment, and description text grant no exemption.
    """
    if not isinstance(relations, list):
        raise ValueError("Incomplete dependency relationships")
    ids, waiting = set(), set()
    for relation in relations:
        if not isinstance(relation, dict) or not isinstance(relation.get("type"), str):
            raise ValueError("Unverifiable dependency relationship")
        if relation["type"] != "blocks":
            continue
        source = relation.get("issue")
        if not isinstance(source, dict) or not source.get("id"):
            waiting.add("Unavailable prerequisite (relationship " + uuid_string(relation["id"]) + ")")
            continue
        ids.add(uuid_string(source["id"]))
        identifier = source.get("identifier", "")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*-\d+", identifier):
            identifier = source["id"]
        state = source.get("state") or {}
        if "trashed" not in source or source["trashed"] not in (True, False, None):
            status = "unverifiable"
        elif source["trashed"]:
            status = "deleted"
        elif state.get("type") == "completed":
            continue
        else:
            status = state.get("type") or "unverifiable"
        waiting.add(f"{identifier} ({status})")
    reason = ("Waiting for blocked-by prerequisites: " + "; ".join(sorted(waiting))
              + ". Each prerequisite must have a completed-type workflow status, "
              "or its blocking relationship must be removed.") if waiting else ""
    return json.dumps(sorted(ids)), reason


class DispatchError(RuntimeError):
    """Uncertain means GitHub might have accepted a dispatch before the error."""

    def __init__(self, message, *, uncertain=False):
        super().__init__(message)
        self.uncertain = uncertain
