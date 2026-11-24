"""Serve Symphony on loopback behind the tailnet HTTPS proxy."""

import asyncio
import base64
import binascii
from collections import deque
from contextlib import suppress
from concurrent.futures import TimeoutError as RequestTimeout
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import sqlite3
from threading import Thread
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .antigravity import Antigravity, TOOL_SPECS, is_usage_error
from .execution import card_stage, execution_payload, history_summary
from .issue_details import issue_details, markdown, open_asset
from .metrics import render_metrics
from .runner import BusyError, Runner, rules_state, shared_agents
from .services import (
    CLOSED_STATES, ISSUE_ID, CreatedIssueError, Linear, estimate_scale, proposed_plan, with_plan,
)
from .updates import CHECK_SECONDS as UPDATE_CHECK_SECONDS, UpdateChecker, commit_tag


WEB = Path(__file__).parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/issue.js": ("issue.js", "application/javascript; charset=utf-8"),
    "/panes.js": ("panes.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.ico": ("favicon.ico", "image/vnd.microsoft.icon"),
}
BODY_LIMIT = 14 * 1024 * 1024
DRAIN_BODY_LIMIT = 1024
CONFIG_BODY_LIMIT = 8192
APPROVE_BODY_LIMIT = 1024
TOOL_BODY_LIMIT = 256 * 1024
TEXT_LIMIT = 23000
ISSUE_TITLE_LIMIT = 255
ISSUE_DESCRIPTION_LIMIT = 24000
LABEL_NAME_LIMIT = 80
IMAGE_COUNT = 4
IMAGE_BYTES = 5 * 1024 * 1024
IMAGES_BYTES = 10 * 1024 * 1024
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
IMAGE_MAGIC = {"image/png": b"\x89PNG\r\n\x1a\n", "image/jpeg": b"\xff\xd8\xff"}
IMAGE_CONTENT_TYPES = {extension: mime for mime, extension in IMAGE_TYPES.items()}
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
    "img-src 'self' https: data: blob:; base-uri 'none'; frame-ancestors 'none'"
)
ISSUE_REFRESH_SECONDS = 300
ISSUE_RETRY_SECONDS = 30
REFRESH_COALESCE_SECONDS = 0.25
PROJECT_REFRESH_SECONDS = 60
WEBHOOK_BODY_LIMIT = 256 * 1024
WEBHOOK_READ_SECONDS = 10
WEBHOOK_CLOCK_SKEW_MS = 60_000
WEBHOOK_TYPES = {"Issue", "Project"}
WEBHOOK_ACTIONS = {"create", "update", "remove"}
HISTORY_CONCURRENCY = 3
RECENT_DONE = timedelta(hours=24)
ACTIVE_PHASES = {"working", "publishing", "ci", "finishing"}
DRAINING_PHASES = {"finish_failed"}


def image_matches(mime, data):
    if mime == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return data.startswith(IMAGE_MAGIC[mime])


def decode_chat_images(images):
    if images is None:
        return []
    if not isinstance(images, list):
        raise ValueError("Invalid image attachments.")
    if len(images) > IMAGE_COUNT:
        raise ValueError("Attach at most four images.")
    decoded = []
    total = 0
    for image in images:
        if not isinstance(image, dict) or set(image) != {"type", "data"}:
            raise ValueError("Invalid image attachment.")
        mime, data = image["type"], image["data"]
        if not isinstance(mime, str) or mime not in IMAGE_TYPES:
            raise ValueError("Attach PNG, JPEG, or WebP images.")
        if not isinstance(data, str):
            raise ValueError("Invalid image encoding.")
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Invalid image encoding.") from None
        if len(raw) > IMAGE_BYTES:
            raise ValueError("Each image must be at most 5 MiB.")
        total += len(raw)
        if total > IMAGES_BYTES:
            raise ValueError("Attached images must total at most 10 MiB.")
        if not raw or not image_matches(mime, raw):
            raise ValueError("Image data does not match its type.")
        decoded.append((mime, IMAGE_TYPES[mime], raw))
    return decoded


def project_list(repos):
    return [{"repo": repo, "label": ", ".join(p["name"] for p in projects), "projects": projects}
            for repo, projects in sorted(repos.items())]


def done_cutoff(now=None):
    return (now or datetime.now(timezone.utc)) - RECENT_DONE


def issue_timestamp(value):
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def current_issue(row, cutoff):
    kind = row["state"]["type"]
    if kind == "completed":
        completed = issue_timestamp(row.get("completedAt"))
        return completed is not None and completed >= cutoff
    if kind == "canceled":
        canceled = issue_timestamp(row.get("canceledAt"))
        return canceled is not None and canceled >= cutoff
    return kind not in CLOSED_STATES


def valid_webhook(payload, now_ms=None):
    if not isinstance(payload, dict):
        return False
    kind = payload.get("type")
    action = payload.get("action")
    if not isinstance(kind, str) or not isinstance(action, str):
        return False
    if kind not in WEBHOOK_TYPES or action not in WEBHOOK_ACTIONS:
        return False
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"].strip():
        return False
    timestamp = payload.get("webhookTimestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        return False
    if isinstance(timestamp, float) and not math.isfinite(timestamp):
        return False
    now = time.time() * 1000 if now_ms is None else now_ms
    return now - WEBHOOK_CLOCK_SKEW_MS <= timestamp <= now + WEBHOOK_CLOCK_SKEW_MS


def chat_message(row):
    message = dict(row)
    try:
        images = json.loads(message.pop("attachments") or "[]")
    except (TypeError, ValueError):
        images = []
    message["images"] = images if isinstance(images, list) else []
    try:
        meta = json.loads(message.pop("meta") or "{}")
    except (TypeError, ValueError):
        meta = {}
    if isinstance(meta, dict) and meta:
        message["meta"] = meta
    if message["role"] == "assistant":
        message["html"] = markdown(message["text"])
    return message


def issue_summary(row, stage=None):
    plan = proposed_plan(row["description"])
    summary = {**{key: row.get(key) for key in ("identifier", "title", "url", "state", "priority",
                                                "priorityLabel", "estimate", "dueDate", "updatedAt",
                                                "createdAt", "completedAt", "canceledAt")},
               "project": (row["project"] or {}).get("name", ""),
               "projectId": (row["project"] or {}).get("id", ""),
               "labels": row["labels"]["nodes"],
               "plan": plan, "planHash": hashlib.sha256(plan.encode()).hexdigest() if plan else "",
               "planHtml": markdown(plan)}
    if stage is not None:
        summary["stage"] = stage
    return summary


def web_version():
    tag = commit_tag()
    if tag:
        return tag
    hasher = hashlib.sha256()
    for name in sorted({*(name for name, _ in STATIC.values()), "mermaid.min.js"}):
        path = WEB / name
        if path.is_file():
            hasher.update(name.encode())
            hasher.update(b"\0")
            hasher.update(path.read_bytes())
    return "web-" + hasher.hexdigest()[:16]


class Symphony:
    def __init__(self, root):
        self.linear = Linear(os.environ["LINEAR_API_KEY"])
        self.internal_token = secrets.token_hex(32)
        self.runner = Runner(root, self.linear)
        self.coordinator = Antigravity(
            self.runner,
            self.tool,
            alert_repo=self.resolve_repo,
            internal_token=self.internal_token,
        )
        self.runner.escalator = self.coordinator
        self.messages = deque()
        self.busy = False
        self.turn_repo = ""
        self.compacting_repo = ""
        self.pending_rules = {}
        self.auth_config_lock = asyncio.Lock()
        self.login = None
        self.repos = {}
        self.link_problems = {}
        self.fetched_projects = 0
        self.projects_lock = asyncio.Lock()
        self.issue_snapshot = None
        self.issue_revision = ""
        self.issue_error = ""
        self.history_cache = {}
        self.stream_cache = ("", "", "")
        self.webhook_secret = os.environ.get("LINEAR_WEBHOOK_SECRET", "")
        self.updates = UpdateChecker()
        self.version = web_version()

    async def refresh_projects(self):
        async with self.projects_lock:
            if self.runner.projects_changed:
                self.runner.projects_changed = False
                self.fetched_projects = 0
            if time.monotonic() - self.fetched_projects >= PROJECT_REFRESH_SECONDS or not self.fetched_projects:
                self.repos, self.link_problems = await self.linear.projects()
                self.fetched_projects = time.monotonic()
        return self.repos

    @staticmethod
    def history_key(row):
        state = row.get("state") or {}
        return (row["identifier"], row.get("updatedAt"), row.get("completedAt"),
                row.get("canceledAt"), state.get("name"), state.get("type"))

    async def history_for(self, row, *, fetch_missing=False):
        key = self.history_key(row)
        cached = self.history_cache.get(key)
        if cached is not None:
            return cached
        connection = row.get("history")
        if connection is None and not fetch_missing:
            result = {"entries": [], "complete": False,
                      "error": "Linear did not return issue history."}
        else:
            try:
                result = await self.linear.issue_history(
                    row.get("id") or row["identifier"], connection)
            except (OSError, RuntimeError, ValueError, TimeoutError) as error:
                nodes = connection.get("nodes") if isinstance(connection, dict) else None
                result = {"entries": nodes if isinstance(nodes, list) else [], "complete": False,
                          "error": str(error) or "Linear issue history is unavailable."}
        if result.get("complete"):
            self.history_cache[key] = result
        return result

    async def refresh_issue_snapshot(self):
        repos = await self.refresh_projects()
        repo_by_project = {project["id"]: repo for repo, projects in repos.items() for project in projects}
        cutoff = done_cutoff()
        rows_by_project = {project_id: [] for project_id in repo_by_project}
        done_since = cutoff.isoformat().replace("+00:00", "Z")
        accepted = []
        for row in await self.linear.issues(repo_by_project, done_since=done_since):
            project = (row["project"] or {}).get("id")
            if project in rows_by_project and current_issue(row, cutoff):
                accepted.append((project, row))
        semaphore = asyncio.Semaphore(HISTORY_CONCURRENCY)

        async def history(row):
            async with semaphore:
                return await self.history_for(row)

        histories = await asyncio.gather(*(history(row) for _, row in accepted))
        now = datetime.now(timezone.utc)
        keep = set()
        for (project, row), record in zip(accepted, histories):
            key = self.history_key(row)
            keep.add(key)
            stage = card_stage(row, history_summary(row, record, now))
            rows_by_project[project].append(issue_summary(row, stage))
        self.history_cache = {
            key: value for key, value in self.history_cache.items() if key in keep
        }
        snapshot = [{**project, "repo": repo, "issues": rows_by_project[project["id"]]}
                    for repo, projects in sorted(repos.items()) for project in projects]
        if snapshot != self.issue_snapshot:
            self.issue_snapshot = snapshot
            self.issue_revision = uuid4().hex
        self.runner.sync_todo_issues(snapshot)
        if self.runner.rows("SELECT 1 FROM tasks WHERE phase='queued' LIMIT 1"):
            self.runner.wake.set()

    async def poll_issues(self):
        loop = asyncio.get_running_loop()
        delay = 0
        retry = ISSUE_RETRY_SECONDS
        not_before = 0.0
        while True:
            if not_before:
                remaining = not_before - loop.time()
                if remaining > 0:
                    try:
                        await asyncio.wait_for(self.runner.issue_refresh.wait(), timeout=remaining)
                    except TimeoutError:
                        pass
                    remaining = not_before - loop.time()
                    if remaining > 0:
                        await asyncio.sleep(remaining)
            elif delay:
                try:
                    await asyncio.wait_for(self.runner.issue_refresh.wait(), timeout=delay)
                except TimeoutError:
                    pass
            else:
                self.runner.issue_refresh.set()
            try:
                await asyncio.sleep(REFRESH_COALESCE_SECONDS)
                self.runner.issue_refresh.clear()
                await self.refresh_issue_snapshot()
            except (OSError, RuntimeError, ValueError) as error:
                self.issue_error = str(error) or "Linear issue refresh timed out."
                delay = retry
                retry = min(retry * 2, ISSUE_REFRESH_SECONDS)
                not_before = loop.time() + delay
            else:
                self.issue_error = ""
                delay = ISSUE_REFRESH_SECONDS
                retry = ISSUE_RETRY_SECONDS
                not_before = 0.0

    def request_issue_refresh(self, projects=False):
        self.runner.request_issue_refresh(projects=projects)

    async def issues(self, repo):
        projects = (await self.refresh_projects()).get(repo)
        if not projects:
            raise ValueError(f"{repo} has no mapped Linear project.")
        return [issue_summary(row) for row in await self.linear.issues(project["id"] for project in projects)]

    async def execution(self, identifier):
        if not ISSUE_ID.fullmatch(identifier):
            raise ValueError("Use a Linear issue ID such as KIAN-1077.")
        row = next((issue for project in self.issue_snapshot or []
                    for issue in project["issues"] if issue["identifier"] == identifier), None)
        if row is None:
            raise ValueError(f"{identifier} is not in the current issue list.")
        history = await self.history_for(row, fetch_missing=True)
        return execution_payload(identifier, row, history, self.runner)

    def mapped_project(self, project_id):
        for repo, projects in self.repos.items():
            for project in projects:
                if project["id"] == project_id:
                    return repo, project
        return None

    async def live_issue(self, identifier):
        issue = await self.linear.issue(identifier)
        await self.refresh_projects()
        project = (issue["project"] or {}).get("id")
        found = self.mapped_project(project)
        if found:
            return issue, found[0]
        raise ValueError(self.link_problems.get(project, f"{identifier} is not in a mapped Linear project."))

    def save_chat_images(self, images):
        saved = []
        try:
            for mime, extension, data in images:
                name = self.runner.save_chat_image(extension, data)
                saved.append({"name": name, "type": mime})
        except (OSError, ValueError):
            self.remove_chat_images(saved)
            raise
        return saved

    def remove_chat_images(self, images):
        for image in images:
            with suppress(OSError, ValueError):
                self.runner.chat_image_path(image["name"]).unlink(missing_ok=True)

    def chat_image(self, name):
        path = self.runner.chat_image_path(name)
        content_type = IMAGE_CONTENT_TYPES.get(path.suffix.lstrip("."))
        if not content_type or not path.is_file():
            raise ValueError("Image not found.")
        return path.read_bytes(), content_type

    async def create_issue(self, args):
        required = {"repo", "title", "description", "priority", "estimate"}
        optional = {"project_id", "parent_id", "blocked_by", "labels"}
        if not isinstance(args, dict) or not required <= set(args) <= required | optional:
            raise ValueError("Invalid create_issue arguments.")
        for key in ("repo", "title", "description"):
            if not isinstance(args[key], str):
                raise ValueError(f"{key} must be a string.")
        repo = args["repo"].strip().lower().removesuffix(".git")
        title = args["title"].strip()
        description = args["description"].strip()
        if not repo:
            raise ValueError("repo must name an enrolled GitHub repository.")
        if not title or len(title) > ISSUE_TITLE_LIMIT:
            raise ValueError(f"The title must contain between 1 and {ISSUE_TITLE_LIMIT} characters.")
        if not description or len(description) > ISSUE_DESCRIPTION_LIMIT:
            raise ValueError("The description must contain between 1 and "
                             f"{ISSUE_DESCRIPTION_LIMIT:,} characters.")
        priority = args["priority"]
        if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 4:
            raise ValueError("The priority must be an integer from 1 to 4.")
        estimate = args["estimate"]
        if isinstance(estimate, bool) or not isinstance(estimate, int):
            raise ValueError("The estimate must be an integer from the target team's scale.")
        project_id = args.get("project_id")
        if project_id is not None:
            if not isinstance(project_id, str) or not project_id.strip():
                raise ValueError("project_id must be a nonempty Linear project ID.")
            project_id = project_id.strip()
        parent_id = args.get("parent_id")
        if parent_id is not None:
            if not isinstance(parent_id, str):
                raise ValueError("Use an uppercase Linear issue identifier.")
            parent_id = parent_id.strip()
            if not ISSUE_ID.fullmatch(parent_id):
                raise ValueError("Use an uppercase Linear issue identifier.")
        blocked_by = args.get("blocked_by", [])
        if not isinstance(blocked_by, list) or any(not isinstance(item, str) for item in blocked_by):
            raise ValueError("blocked_by must be a list of uppercase Linear issue identifiers.")
        blocked_by = [item.strip() for item in blocked_by]
        if any(not ISSUE_ID.fullmatch(item) for item in blocked_by):
            raise ValueError("Use uppercase Linear issue identifiers.")
        blocked_by = list(dict.fromkeys(blocked_by))
        labels = args.get("labels", [])
        if not isinstance(labels, list) or any(not isinstance(item, str) for item in labels):
            raise ValueError("labels must be a list of existing label names.")
        labels = [item.strip() for item in labels]
        if any(not item or len(item) > LABEL_NAME_LIMIT for item in labels):
            raise ValueError(f"Label names must contain between 1 and {LABEL_NAME_LIMIT} characters.")
        labels = list(dict.fromkeys(labels))

        projects = (await self.refresh_projects()).get(repo)
        if not projects:
            raise ValueError(f"{repo} has no mapped Linear project.")
        if project_id is not None:
            project = next((item for item in projects if item["id"] == project_id), None)
            if not project:
                raise ValueError(f"Linear project {project_id} is not mapped to {repo}.")
        elif len(projects) == 1:
            project = projects[0]
        else:
            raise ValueError(f"{repo} maps to several Linear projects. Pass project_id.")

        context = await self.linear.project_context(project["id"])
        if context.get("id") != project["id"]:
            raise RuntimeError("Linear returned the wrong project.")
        if context.get("archivedAt"):
            raise ValueError(f"Linear project {context.get('name') or project['name']} is archived.")
        teams = [team for team in context.get("teams") or [] if not team.get("archivedAt")]
        if len(teams) != 1:
            raise ValueError(f"{project['name']} must have exactly one active owning Linear team.")
        team = await self.linear.team_context(teams[0]["id"])
        if team.get("id") != teams[0]["id"]:
            raise RuntimeError("Linear returned the wrong team.")
        if team.get("archivedAt"):
            raise ValueError(f"Linear team {team.get('name') or teams[0]['name']} is archived.")

        scale = estimate_scale(team)
        if estimate not in scale:
            options = ", ".join(str(value) for value in scale)
            raise ValueError(f"The estimate must be one of {options} for the "
                             f"{team.get('name') or 'target'} team.")
        states = [state for state in team.get("states") or []
                  if not state.get("archivedAt") and state.get("name") == "Backlog"
                  and state.get("type") == "backlog"]
        if len(states) != 1:
            raise RuntimeError("The Linear team must have one Backlog state.")
        state = states[0]

        label_ids = []
        if labels:
            available = await self.linear.team_labels(team["id"])
            for name in labels:
                matches = [label for label in available if label.get("name") == name]
                if not matches:
                    raise ValueError(f"Unknown Linear label: {name}.")
                if len(matches) != 1:
                    raise ValueError(f"Linear label {name} is ambiguous.")
                if matches[0].get("id") not in label_ids:
                    label_ids.append(matches[0]["id"])

        parent_issue = None
        if parent_id:
            parent_issue, _ = await self.live_issue(parent_id)
        blockers = []
        for identifier in blocked_by:
            issue, _ = await self.live_issue(identifier)
            blockers.append((identifier, issue))

        try:
            issue = await self.linear.create_issue(
                project["id"], team["id"], state["id"], title, description, priority, estimate,
                parent_id=parent_issue["id"] if parent_issue else None, label_ids=label_ids,
            )
        except CreatedIssueError:
            self.request_issue_refresh()
            raise
        links = {"succeeded": [], "failed": []}
        for identifier, blocker in blockers:
            try:
                await self.linear.create_blocks_relation(blocker["id"], issue["id"])
            except (OSError, RuntimeError, TimeoutError, ValueError) as error:
                links["failed"].append({"identifier": identifier,
                                        "error": str(error) or "Blocking link failed."})
            else:
                links["succeeded"].append(identifier)
        self.request_issue_refresh()
        message = f"Created {issue['identifier']} in Backlog for {repo}."
        if links["failed"]:
            names = ", ".join(item["identifier"] for item in links["failed"])
            message += (f" Blocking links failed for {names}; {issue['identifier']} was not recreated."
                        " Reconcile those links in Linear.")
        return {"id": issue["id"], "identifier": issue["identifier"], "url": issue["url"],
                "project": context.get("name") or project["name"], "team": team.get("name") or "",
                "state": "Backlog", "priority": priority, "estimate": estimate,
                "blockedBy": links, "message": message}

    async def tool(self, name, args):
        if name not in TOOL_SPECS:
            raise ValueError("Unknown tool.")
        if name == "create_issue":
            return await self.create_issue(args)
        if name == "enqueue_task":
            if not isinstance(args, dict) or not {"repo", "prompt"} <= set(args) <= {"repo", "prompt", "title"}:
                raise ValueError("Invalid enqueue_task arguments.")
            repo = str(args["repo"]).strip().lower().removesuffix(".git")
            if repo not in await self.refresh_projects():
                raise ValueError(f"Unknown repository {repo}.")
            return self.runner.enqueue_adhoc(repo, str(args["prompt"]), str(args.get("title") or ""))
        keys = TOOL_SPECS[name][1]
        if not isinstance(args, dict) or set(args) != set(keys) or any(not isinstance(args[key], str) for key in keys):
            raise ValueError("Invalid tool arguments.")
        issue_id = args.get("issue_id")
        if issue_id is not None and not ISSUE_ID.fullmatch(issue_id):
            raise ValueError("Use an uppercase Linear issue identifier.")
        if name == "list_repos":
            return project_list(await self.refresh_projects())
        if name == "list_issues":
            return await self.issues(args["repo"])
        if name == "get_issue":
            issue, repo = await self.live_issue(issue_id)
            return {**issue, "repo": repo}
        if name == "set_plan":
            issue, repo = await self.live_issue(issue_id)
            plan = args["plan"].strip()
            if not plan or len(plan) > 24000:
                raise ValueError("The plan must contain between 1 and 24,000 characters.")
            description = with_plan(issue["description"], plan)
            if proposed_plan(description) != plan:
                raise ValueError("Use level-three headings within the Proposed Plan.")
            await self.linear.update(issue["id"], description=description)
            self.request_issue_refresh()
            title = issue.get("title") or ""
            plan_hash = hashlib.sha256(plan.encode()).hexdigest()
            label = f": {title}" if title else ""
            self.runner.chat(
                "system",
                f"Proposed plan for {issue_id}{label}",
                repo=repo,
                meta={"kind": "plan_proposal", "issue": issue_id, "title": title, "planHash": plan_hash},
            )
            return f"Saved the Proposed Plan section for {issue_id}."
        if name == "worker_status":
            return {"tasks": self.runner.status(), "stats": self.runner.stats()}
        if name == "retry_task":
            return self.runner.retry(args["task_id"])
        if name in {"list_repo_buttons", "set_repo_button", "delete_repo_button", "render_diagram"}:
            target_repo = args["repo"].strip().lower()
            if target_repo not in await self.refresh_projects():
                raise ValueError(f"Unknown repository {target_repo}.")
            if name == "list_repo_buttons":
                return self.runner.repo_buttons(target_repo)
            if name == "set_repo_button":
                return self.runner.set_repo_button(target_repo, args["label"], args["prompt"], args["mode"])
            if name == "render_diagram":
                saved = self.runner.save_diagram(target_repo, args["title"], args["mermaid"])
                return f"Rendered diagram {saved['title']} for {target_repo}."
            return self.runner.delete_repo_button(target_repo, args["label"])
        raise ValueError("Unknown tool.")

    async def check_updates(self):
        while True:
            try:
                await asyncio.to_thread(self.updates.check)
            except (OSError, RuntimeError, TimeoutError, ValueError):
                pass
            await asyncio.sleep(UPDATE_CHECK_SECONDS)

    async def set_draining(self, draining):
        self.runner.draining = draining
        if not draining:
            self.runner.wake.set()
        return {"draining": self.runner.draining}

    async def approve_plan(self, body):
        """Move a planned Backlog issue straight to Todo so the Flash queue runs it."""
        if (not isinstance(body, dict) or not {"issue"} <= set(body) <= {"issue", "planHash"}
                or not isinstance(body["issue"], str) or not ISSUE_ID.fullmatch(body["issue"])):
            raise ValueError("Expected an issue identifier.")
        issue_id = body["issue"]
        issue, repo = await self.live_issue(issue_id)
        if issue["state"]["type"] in CLOSED_STATES:
            raise BusyError(f"{issue_id} is already {issue['state']['name']}.")
        if body.get("planHash"):
            plan = proposed_plan(issue.get("description") or "")
            if not plan:
                raise BusyError(f"{issue_id} no longer has a proposed plan.")
            live_hash = hashlib.sha256(plan.encode()).hexdigest()
            if not hmac.compare_digest(live_hash, str(body["planHash"])):
                raise BusyError(f"The proposed plan for {issue_id} changed since it was proposed.")
        await self.linear.set_state(issue_id, "Todo", "unstarted")
        self.request_issue_refresh()
        self.runner.chat("system", f"Moved {issue_id} to Todo for automatic Flash dispatch.", repo=repo)
        return {"approved": issue_id, "repo": repo}

    def resolve_repo(self, project="", repo=""):
        if repo and repo in self.repos:
            return repo
        target = project or self.runner.setting("view")
        found = self.mapped_project(target) if target else None
        if found:
            return found[0]
        if self.issue_snapshot:
            return self.issue_snapshot[0].get("repo", "")
        return ""

    def active_login(self):
        if (not self.login or self.coordinator.account
                or not self.coordinator.login_pending
                or (self.coordinator.login_process and self.coordinator.login_process.returncode is not None)
                or time.monotonic() >= self.login["expires"]):
            self.login = None
            return None
        return self.login

    def coordinator_busy(self):
        return bool(self.busy or self.coordinator.any_turn() or self.messages)

    async def start_antigravity_login(self):
        current = self.active_login()
        if current:
            return current
        result = await self.coordinator.login()
        self.login = {
            "url": result["url"],
            "expires": time.monotonic() + 58,
        }
        return self.login

    async def config_state(self):
        login = self.active_login()
        return {
            "opencodeGo": {**self.runner.opencode_go_status(), "busy": self.runner.opencode_go_busy()},
            "antigravity": {
                "signedIn": bool(self.coordinator.account),
                "account": dict(self.coordinator.account_info),
                "login": {"url": login["url"]} if login else None,
                "busy": self.coordinator_busy(),
            },
        }

    async def update_config(self, body):
        if not isinstance(body, dict) or not isinstance(body.get("action"), str):
            raise ValueError("Unsupported config action.")
        action = body["action"]
        if action == "set_opencode_key":
            if set(body) != {"action", "apiKey"}:
                raise ValueError("Unsupported config fields.")
            await self.runner.set_opencode_go_key(body["apiKey"])
            self.runner.wake.set()
        elif action == "clear_opencode_key":
            if set(body) != {"action"}:
                raise ValueError("Unsupported config fields.")
            await self.runner.clear_opencode_go_key()
        elif action == "antigravity_login":
            if set(body) != {"action"}:
                raise ValueError("Unsupported config fields.")
            async with self.auth_config_lock:
                if self.coordinator_busy():
                    raise BusyError("Cannot start Antigravity sign-in while a coordinator turn is active.")
                await self.start_antigravity_login()
        elif action == "antigravity_complete_login":
            if set(body) != {"action", "code"}:
                raise ValueError("Unsupported config fields.")
            async with self.auth_config_lock:
                if self.coordinator_busy():
                    raise BusyError("Cannot complete Antigravity sign-in while a coordinator turn is active.")
                await self.coordinator.complete_login(body["code"])
                self.login = None
        elif action == "antigravity_cancel_login":
            if set(body) != {"action"}:
                raise ValueError("Unsupported config fields.")
            async with self.auth_config_lock:
                if self.coordinator_busy():
                    raise BusyError("Cannot cancel Antigravity sign-in while a coordinator turn is active.")
                await self.coordinator.cancel_login()
                self.login = None
        elif action == "antigravity_logout":
            if set(body) != {"action"}:
                raise ValueError("Unsupported config fields.")
            async with self.auth_config_lock:
                if self.coordinator_busy():
                    raise BusyError("Cannot sign out of Antigravity while a coordinator turn is active.")
                await self.coordinator.logout()
                self.login = None
        else:
            raise ValueError("Unsupported config action.")
        return await self.config_state()

    async def state(self, after, issue_revision="", project="", repo=""):
        active_login = self.active_login()
        login = {"url": active_login["url"]} if active_login else None
        active_repo = self.resolve_repo(project, repo)
        rows = self.runner.rows(
            "SELECT * FROM chat WHERE id > ? AND repo = ? ORDER BY id LIMIT 200",
            (after, active_repo),
        )
        messages = [chat_message(row) for row in rows]
        text = self.coordinator.stream_for(active_repo)
        if self.stream_cache[:2] != (active_repo, text):
            self.stream_cache = (active_repo, text, markdown(text) if text else "")
        raw_buttons = self.runner.repo_buttons(active_repo) if active_repo else []
        raw_diagram = self.runner.diagram(active_repo) if active_repo else None
        state = {
            "messages": messages,
            "busy": self.busy,
            "canInterrupt": bool(self.coordinator.turn_for(active_repo)),
            "stream": text,
            "streamHtml": self.stream_cache[2],
            "tasks": self.runner.status(),
            "signedIn": bool(self.coordinator.account),
            "login": login,
            "paused": self.runner.setting("paused"),
            "view": self.runner.setting("view"),
            "repo": active_repo,
            "repoActivity": self.runner.repo_chat_latest(),
            "buttons": raw_buttons if isinstance(raw_buttons, list) else [],
            "diagram": raw_diagram if isinstance(raw_diagram, dict) else None,
            "coordinator": self.coordinator.status(active_repo),
            "issueError": self.issue_error,
            "compacting": (
                "running" if self.compacting_repo == active_repo else
                "queued" if any(entry[0] == active_repo and entry[1] == "/compact"
                                for entry in self.messages) else ""
            ),
            "updateAvailable": self.updates.available,
            "update": self.updates.details(),
            "draining": self.runner.draining,
            "version": self.version,
            "usageAlert": self.coordinator.usage_alert(),
        }
        if self.issue_snapshot is not None and issue_revision != self.issue_revision:
            state.update(projects=self.issue_snapshot, issueRevision=self.issue_revision)
        return state

    async def idle(self):
        if self.busy or self.messages or self.coordinator.any_turn():
            return False
        tasks = self.runner.status()
        if any(task["phase"] in ACTIVE_PHASES for task in tasks):
            return False
        if self.runner.draining and any(task["phase"] in DRAINING_PHASES for task in tasks):
            return False
        return True

    async def metrics(self):
        return render_metrics(self)

    def saved_message(self, message_id):
        row = self.runner.rows("SELECT * FROM chat WHERE id=?", (message_id,))[0]
        return chat_message(row)

    async def submit(self, body):
        text = body.get("text", "")
        project = body.get("project") or ""
        if not isinstance(text, str) or not isinstance(project, str):
            raise ValueError("Enter a message.")
        text = text.strip()
        if len(text) > TEXT_LIMIT:
            raise ValueError(f"Messages must be at most {TEXT_LIMIT:,} characters.")
        images = decode_chat_images(body.get("images"))
        if not text and not images:
            raise ValueError("Enter a message.")
        if images and text.startswith("/"):
            raise ValueError("Images cannot be sent with slash commands.")
        found = self.mapped_project(project) if project else None
        if text == "/interrupt":
            repo = found[0] if found else self.coordinator.active_repo()
            await self.coordinator.interrupt(repo)
            message_id = self.runner.chat("user", text, repo=repo)
            return {"message": self.saved_message(message_id)}
        if project and not found:
            raise ValueError("The selected project is unavailable. Refresh the project list.")
        repo = found[0] if found else ""
        previous = self.runner.setting("view")
        if project != previous:
            self.runner.save_setting("view", project)
        message = text
        attachments = self.save_chat_images(images)
        try:
            message_id = self.runner.chat("user", message, attachments, repo=repo)
        except Exception:
            self.remove_chat_images(attachments)
            raise
        queued_compact = text == "/compact" and bool(
            self.busy or self.coordinator.turn_for(repo) or self.messages
        )
        self.messages.append((repo, text, message, attachments))
        if queued_compact:
            self.runner.chat("system", "Compaction queued after the current turn.", repo=repo)
        self.runner.wake.set()
        return {"message": self.saved_message(message_id)}

    async def checkout_cwd(self, repo):
        cwd = await self.runner.checkout(repo, refresh=repo != self.turn_repo) if repo else self.runner.directory
        self.turn_repo = repo
        return cwd

    async def context(self, repo):
        cwd = await self.checkout_cwd(repo)
        defaults, repo_rules, rules_hash = rules_state(cwd)
        context = []
        compacted = repo in self.coordinator.compacted_repos
        if self.coordinator.injected_rules.get(repo) != rules_hash:
            if defaults.strip():
                context.append("Global AGENTS.md defaults:\n" + defaults)
            if repo_rules:
                context.append("Active repository AGENTS.md:\n" + repo_rules)
        if compacted:
            context.append("Context was compacted. Call worker_status for live task state.")
        self.pending_rules[repo] = rules_hash
        return cwd, "\n\n".join(context)

    async def respond(self, repo, message, issues=(), attachments=()):
        self.pending_rules.pop(repo, None)
        cwd, context = await self.context(repo)
        rules_hash = self.pending_rules.pop(repo, None)
        text = f"{message}\n\n--- Current Symphony context ---\n{context}" if context else message
        attributed = list(dict.fromkeys(issues))
        started = time.time()
        open_turns = [(issue, self.runner.record_execution(
            issue, "coordinator", started=started, detail="Coordinator turn started.",
            data={"issues": attributed},
        )) for issue in attributed]
        detail = "Coordinator turn finished."
        status = "failed"
        try:
            outcome = await self.coordinator.respond(repo, text, cwd, attachments)
            status = outcome if isinstance(outcome, str) and outcome else "completed"
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            detail = f"Coordinator turn failed: {error}"
            raise
        except asyncio.CancelledError:
            status = "cancelled"
            detail = "Coordinator turn cancelled."
            raise
        else:
            if rules_hash is not None:
                self.coordinator.injected_rules[repo] = rules_hash
            self.coordinator.compacted_repos.discard(repo)
            return outcome
        finally:
            ended = time.time()
            model = self.coordinator.current_model() or "unknown"
            self.runner.record_coordinator_turn(repo, model, started=started, ended=ended, status=status)
            tokens = self.coordinator.consume_turn_tokens(repo)
            if tokens:
                self.runner.record_token_usage(
                    "coordinator", repo, model,
                    input_tokens=tokens.get("input_tokens"),
                    output_tokens=tokens.get("output_tokens"),
                    total_tokens=tokens.get("total_tokens"),
                    cost_usd=None,
                    recorded=ended,
                )
            for _, execution_id in open_turns:
                self.runner.close_execution(execution_id, ended=ended, detail=detail)

    async def handle(self, repo, text, message, attachments=()):
        command, _, argument = text.partition(" ")
        if attachments and command.startswith("/"):
            raise ValueError("Images cannot be sent with slash commands.")
        if command == "/login":
            await self.start_antigravity_login()
        elif command == "/run":
            if not repo:
                raise ValueError("Select a project before running an ad-hoc task.")
            if not argument.strip():
                raise ValueError("Enter a task after /run.")
            self.runner.enqueue_adhoc(repo, argument.strip())
        elif command == "/stats":
            self.runner.chat("system", self.runner.stats(), repo=repo)
        elif command == "/new":
            await self.coordinator.new_session(repo)
            self.runner.chat("system", "Started a new coordinator session.", repo=repo)
        elif command == "/compact":
            self.compacting_repo = repo
            try:
                cwd = await self.checkout_cwd(repo)
                await self.coordinator.compact(repo, cwd)
            finally:
                self.compacting_repo = ""
        elif command == "/status":
            status = self.coordinator.status(repo)
            usage = [status["model"]]
            usage.extend(f"{label} {status['rateLimits'][label]['left']}% left"
                         for label in ("5h", "weekly") if label in status["rateLimits"])
            summary = [
                f"{task['issue']} · {task['phase']}" + (f" ({task['tier']})" if task.get("tier") else "")
                for task in self.runner.status()
            ]
            self.runner.chat("system", "\n".join(filter(None, [
                " · ".join(filter(None, usage)), "\n".join(summary) or "No active or queued work.",
            ])), repo=repo)
        elif command == "/mcp":
            sections = ["Symphony tools (via MCP):"]
            for tool_name, (description, params) in TOOL_SPECS.items():
                sections.append(f"- {tool_name}({', '.join(params)}) — {description}")
            servers = await self.coordinator.mcp_servers()
            if servers:
                sections.append("\nConfigured MCP servers:")
                for server in servers:
                    sections.append(f"- {server['name']} [{server['authStatus']}]")
            self.runner.chat("system", "\n".join(sections), repo=repo)
        elif command == "/model":
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", argument):
                raise ValueError("Enter a model ID after /model.")
            self.runner.save_setting("model", argument)
            self.runner.chat("system", f"Model set to {argument}.", repo=repo)
        elif command == "/continue":
            self.runner.save_setting("paused", "")
            self.runner.chat("system", "Automatic dispatch resumed.", repo=repo)
            self.runner.wake.set()
        elif command == "/retry":
            self.runner.retry(argument)
        elif command.startswith("/"):
            raise ValueError(
                "Available commands: /run, /stats, /login, /new, /compact, /status, /mcp, /model, /interrupt, /continue, /retry."
            )
        else:
            await self.respond(repo, message, (), attachments)

    async def pump(self):
        while True:
            await self.runner.wake.wait()
            self.runner.wake.clear()
            while True:
                if self.messages:
                    self.busy = True
                    entry = self.messages.popleft()
                    try:
                        await self.handle(*entry)
                    except (ValueError, RuntimeError, OSError, TimeoutError) as error:
                        if is_usage_error(error):
                            posted = self.coordinator.record_usage_error(str(error), repo=entry[0])
                            if not posted:
                                self.runner.chat("system", str(error) or "Antigravity usage limit reached.", repo=entry[0])
                        else:
                            self.runner.chat("system", str(error) or "The request timed out.", repo=entry[0])
                    finally:
                        self.busy = False
                    continue
                if self.runner.setting("paused"):
                    break
                if await self.runner.dispatch_queued():
                    continue
                break

    async def start(self):
        self.runner.recover()
        self.runner.job(self.poll_issues())
        self.runner.job(self.check_updates())
        try:
            await self.coordinator.connect()
        except (OSError, RuntimeError, TimeoutError) as error:
            self.runner.chat("system", str(error) or "Antigravity initialization failed.")
        self.runner.job(self.coordinator.poll_status())
        self.runner.wake.set()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def json_response(self, value, status=200, *, cache_control="no-store"):
        self.respond(json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8", status,
                     cache_control=cache_control)

    def respond(self, data, content_type, status=200, *, cache_control="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        with suppress(BrokenPipeError, ConnectionResetError):
            self.end_headers()
            self.wfile.write(data)

    def run_async(self, task):
        future = asyncio.run_coroutine_threadsafe(task, self.server.loop)
        try:
            return future.result(timeout=55)
        except RequestTimeout:
            future.cancel()
            raise ValueError("The request timed out.") from None

    def linear_asset(self, url):
        with open_asset(self.server.app.linear.token, url, self.headers.get("Range")) as response:
            self.send_response(response.status)
            for name in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Content-Disposition"):
                if value := response.headers.get(name):
                    self.send_header(name, value)
            self.send_header("Cache-Control", "private, max-age=300")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "sandbox; default-src 'none'; style-src 'unsafe-inline'")
            try:
                self.end_headers()
                shutil.copyfileobj(response, self.wfile)
            except OSError:
                self.close_connection = True

    def do_GET(self):
        url = urlsplit(self.path)
        params = parse_qs(url.query)
        app = self.server.app
        try:
            if url.path in STATIC:
                name, content_type = STATIC[url.path]
                self.respond((WEB / name).read_bytes(), content_type)
                return
            if url.path == "/mermaid.min.js":
                mermaid = WEB / "mermaid.min.js"
                if mermaid.is_file():
                    self.respond(mermaid.read_bytes(), "application/javascript; charset=utf-8")
                else:
                    self.json_response({"error": "Mermaid library is unavailable."}, 404)
                return
            if url.path == "/healthz":
                self.json_response({"status": "ok"})
            elif url.path == "/api/state":
                after = int(params.get("after", ["0"])[0])
                self.json_response(self.run_async(app.state(
                    after,
                    params.get("issues", [""])[0],
                    project=params.get("project", [""])[0],
                    repo=params.get("repo", [""])[0],
                )))
            elif url.path == "/api/issue":
                issue = self.run_async(issue_details(app.linear, params.get("id", [""])[0]))
                cache_control = "private, max-age=60"
                if params.get("updated") == [issue["updatedAt"]]:
                    cache_control = "private, max-age=31536000, immutable"
                self.json_response(issue, cache_control=cache_control)
            elif url.path == "/api/execution":
                self.json_response(self.run_async(app.execution(params.get("id", [""])[0])))
            elif url.path == "/api/agents":
                content = shared_agents()
                self.json_response({"content": content, "html": markdown(content)})
            elif url.path == "/api/config":
                self.json_response(self.run_async(app.config_state()))
            elif url.path == "/api/linear-asset":
                self.linear_asset(params.get("url", [""])[0])
            elif url.path == "/api/chat-image":
                try:
                    data, content_type = app.chat_image(params.get("name", [""])[0])
                except (ValueError, OSError):
                    self.json_response({"error": "Image not found."}, 404)
                    return
                self.respond(data, content_type, cache_control="private, max-age=31536000, immutable")
            else:
                self.json_response({"error": "Page not found."}, 404)
        except (ValueError, RuntimeError, OSError) as error:
            self.json_response({"error": str(error)}, 503)

    def valid_origin(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        parsed = urlsplit(origin)
        host = self.headers.get("Host") or ""
        return parsed.scheme in {"http", "https"} and bool(host) and parsed.netloc == host and parsed.path in {"", "/"}

    def _handle_internal_tool(self):
        auth = self.headers.get("Authorization") or ""
        expected = f"Bearer {self.server.app.internal_token}"
        if not self.server.app.internal_token or not hmac.compare_digest(auth, expected):
            self.json_response({"error": "Unauthorized."}, 401)
            return
        try:
            length = int(self.headers["Content-Length"])
            if not 0 < length <= TOOL_BODY_LIMIT:
                raise ValueError("Tool request is too large.")
            raw = self.rfile.read(length)
            body = json.loads(raw)
            name = body.get("name")
            arguments = body.get("arguments") or {}
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise ValueError("Expected tool name and arguments object.")
            result = self.run_async(self.server.app.tool(name, arguments))
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            self.json_response({"ok": True, "text": text})
        except (KeyError, TypeError, ValueError, RuntimeError, OSError) as error:
            self.json_response({"ok": False, "text": str(error) or "Tool call failed."})

    def do_POST(self):
        if self.path == "/internal/tool":
            self._handle_internal_tool()
            return
        if self.path not in {"/api/chat", "/api/drain", "/api/config", "/api/approve"}:
            self.json_response({"error": "Page not found."}, 404)
            return
        if (self.headers.get("X-Symphony") != "1" or self.headers.get_content_type() != "application/json"
                or self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin"
                or not self.valid_origin()):
            self.json_response({"error": "Same-origin requests only."}, 403)
            return
        try:
            length = int(self.headers["Content-Length"])
            limit = (APPROVE_BODY_LIMIT if self.path == "/api/approve"
                     else CONFIG_BODY_LIMIT if self.path == "/api/config"
                     else DRAIN_BODY_LIMIT if self.path == "/api/drain" else BODY_LIMIT)
            if not 0 < length <= limit:
                raise ValueError("Request is too large." if self.path != "/api/chat"
                                 else "Message is too large.")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Request body is incomplete.")
            try:
                body = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ValueError("Invalid JSON request.") from None
            if self.path == "/api/drain":
                if (not isinstance(body, dict) or set(body) != {"draining"}
                        or not isinstance(body["draining"], bool)):
                    raise ValueError("Expected a draining boolean.")
                self.json_response(self.run_async(self.server.app.set_draining(body["draining"])))
                return
            if self.path == "/api/config":
                self.json_response(self.run_async(self.server.app.update_config(body)))
                return
            if self.path == "/api/approve":
                self.json_response(self.run_async(self.server.app.approve_plan(body)))
                return
            if not isinstance(body, dict):
                raise ValueError("Expected a chat message.")
            self.json_response(self.run_async(self.server.app.submit(body)), 202)
        except BusyError as error:
            self.json_response({"error": str(error)}, 409)
        except (TypeError, ValueError, RuntimeError, OSError) as error:
            self.json_response({"error": str(error)}, 400)


class IdleHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        content_type = "application/json; charset=utf-8"
        if path == "/idle":
            future = asyncio.run_coroutine_threadsafe(self.server.app.idle(), self.server.loop)
            try:
                idle = future.result(timeout=5)
                body, status = json.dumps({"idle": idle}).encode(), 200
            except RequestTimeout:
                future.cancel()
                body, status = b'{"error":"Unavailable."}', 503
            except (RuntimeError, OSError):
                body, status = b'{"error":"Unavailable."}', 503
        elif path == "/metrics":
            future = asyncio.run_coroutine_threadsafe(self.server.app.metrics(), self.server.loop)
            try:
                text = future.result(timeout=5)
                body, content_type, status = text.encode("utf-8"), "text/plain; version=0.0.4; charset=utf-8", 200
            except RequestTimeout:
                future.cancel()
                body, status = b'{"error":"Unavailable."}', 503
            except (RuntimeError, OSError, sqlite3.Error, ValueError, TypeError):
                body, status = b'{"error":"Unavailable."}', 503
        else:
            body, status = b'{"error":"Not found."}', 404
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        with suppress(BrokenPipeError, ConnectionResetError):
            self.end_headers()
            self.wfile.write(body)


class WebhookHandler(BaseHTTPRequestHandler):
    timeout = WEBHOOK_READ_SECONDS

    def log_message(self, *_):
        pass

    def reply(self, status, message):
        body = json.dumps({("status" if status < 400 else "error"): message}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        with suppress(BrokenPipeError, ConnectionResetError):
            self.end_headers()
            self.wfile.write(body)

    def unsupported(self):
        self.reply(405, "Method not allowed.")

    do_GET = do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = unsupported
    do_TRACE = do_CONNECT = unsupported

    def do_POST(self):
        if urlsplit(self.path).path != "/webhooks/linear":
            self.reply(404, "Not found.")
            return
        app = self.server.app
        if not app.webhook_secret:
            self.reply(503, "Webhook signing is not configured.")
            return
        try:
            length = int(self.headers["Content-Length"])
        except (KeyError, TypeError, ValueError):
            self.reply(411, "Content-Length is required.")
            return
        if length <= 0:
            self.reply(400, "A webhook body is required.")
            return
        if length > WEBHOOK_BODY_LIMIT:
            self.reply(413, "Webhook body is too large.")
            return
        try:
            body = self.rfile.read(length)
        except (TimeoutError, OSError):
            self.reply(408, "Webhook body timed out.")
            return
        if len(body) != length:
            self.reply(400, "Webhook body is incomplete.")
            return
        signature = (self.headers.get("Linear-Signature") or "").strip()
        expected = hmac.new(app.webhook_secret.encode(), body, hashlib.sha256).digest()
        try:
            provided = bytes.fromhex(signature)
        except ValueError:
            provided = b""
        if not provided or not hmac.compare_digest(provided, expected):
            self.reply(401, "Invalid webhook signature.")
            return
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, ValueError):
            self.reply(400, "Invalid webhook payload.")
            return
        if not valid_webhook(payload):
            self.reply(400, "Unsupported webhook payload.")
            return
        self.server.loop.call_soon_threadsafe(
            app.request_issue_refresh, payload["type"] == "Project")
        self.reply(200, "ok")


async def serve():
    os.umask(0o077)
    root = Path(os.environ.get("SYMPHONY_DATA", "/data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    Path.home().mkdir(parents=True, exist_ok=True)
    exit_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, exit_event.set)
    with ((root / ".symphony.lock").open("w") as lock,
          ThreadingHTTPServer(("127.0.0.1", 8080), Handler) as http,
          ThreadingHTTPServer(("0.0.0.0", 8081), IdleHandler) as idle_http,
          ThreadingHTTPServer(("0.0.0.0", 8082), WebhookHandler) as webhook_http):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        http.loop = idle_http.loop = webhook_http.loop = loop
        http.app = idle_http.app = webhook_http.app = Symphony(root)
        Thread(target=http.serve_forever, daemon=True).start()
        Thread(target=idle_http.serve_forever, daemon=True).start()
        Thread(target=webhook_http.serve_forever, daemon=True).start()
        pump = asyncio.create_task(http.app.pump())
        http.app.runner.job(http.app.start())
        try:
            await exit_event.wait()
        finally:
            await asyncio.to_thread(http.shutdown)
            await asyncio.to_thread(idle_http.shutdown)
            await asyncio.to_thread(webhook_http.shutdown)
            pump.cancel()
            with suppress(asyncio.CancelledError):
                await pump
            tasks = list(http.app.runner.jobs)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await http.app.coordinator.close()
            http.app.runner.db.close()


if __name__ == "__main__":
    asyncio.run(serve())
