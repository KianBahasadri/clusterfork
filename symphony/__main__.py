"""Serve Symphony on loopback behind the tailnet HTTPS proxy."""

import asyncio
from collections import deque
from contextlib import suppress
from concurrent.futures import TimeoutError as RequestTimeout
from datetime import datetime, timedelta, timezone
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import signal
from threading import Thread
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .codex import Codex, TOOL_SPECS
from .execution import card_stage, execution_payload, history_summary
from .issue_details import issue_details, markdown, open_asset
from .runner import Runner, read_small
from .services import CLOSED_STATES, ISSUE_ID, Linear, proposed_plan, with_plan


WEB = Path(__file__).parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/issue.js": ("issue.js", "application/javascript; charset=utf-8"),
    "/panes.js": ("panes.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.ico": ("favicon.ico", "image/vnd.microsoft.icon"),
}
BODY_LIMIT = 32000
ISSUE_REFRESH_SECONDS = 30
HISTORY_CONCURRENCY = 3
RECENT_DONE = timedelta(hours=24)
ACTIVE_PHASES = {"approving", "working", "publishing", "ci", "finishing"}


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
    return kind not in CLOSED_STATES


def issue_summary(row, stage=None):
    plan = proposed_plan(row["description"])
    summary = {**{key: row.get(key) for key in ("identifier", "title", "url", "state", "priority",
                                                "priorityLabel", "estimate", "dueDate", "updatedAt",
                                                "createdAt", "completedAt")},
               "project": (row["project"] or {}).get("name", ""),
               "projectId": (row["project"] or {}).get("id", ""),
               "labels": row["labels"]["nodes"],
               "plan": plan, "planHtml": markdown(plan)}
    if stage is not None:
        summary["stage"] = stage
    return summary


class Symphony:
    def __init__(self, root):
        self.linear = Linear(os.environ["LINEAR_API_KEY"])
        self.runner = Runner(root, self.linear)
        self.coordinator = Codex(self.runner, self.tool)
        self.messages = deque()
        self.busy = False
        self.turn_repo = ""
        self.login = None
        self.repos = {}
        self.link_problems = {}
        self.fetched_projects = 0
        self.projects_lock = asyncio.Lock()
        self.issue_snapshot = None
        self.issue_revision = ""
        self.issue_error = ""
        self.history_cache = {}

    async def refresh_projects(self):
        async with self.projects_lock:
            if time.monotonic() - self.fetched_projects >= 60 or not self.fetched_projects:
                self.repos, self.link_problems = await self.linear.projects()
                self.fetched_projects = time.monotonic()
        return self.repos

    @staticmethod
    def history_key(row):
        state = row.get("state") or {}
        return (row["identifier"], row.get("updatedAt"), row.get("completedAt"),
                state.get("name"), state.get("type"))

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
        if self.runner.rows("SELECT 1 FROM tasks WHERE phase='approved' LIMIT 1"):
            self.runner.wake.set()

    async def poll_issues(self):
        delay = ISSUE_REFRESH_SECONDS
        while True:
            try:
                await self.refresh_issue_snapshot()
            except (OSError, RuntimeError, ValueError) as error:
                self.issue_error = str(error) or "Linear issue refresh timed out."
                delay = min(delay * 2, 300)
            else:
                self.issue_error = ""
                delay = ISSUE_REFRESH_SECONDS
            await asyncio.sleep(delay)

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

    async def tool(self, name, args):
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
            issue, _ = await self.live_issue(issue_id)
            if self.runner.rows("SELECT 1 FROM tasks WHERE issue=?", (issue_id,)):
                raise ValueError("The proposed plan is locked by approval.")
            plan = args["plan"].strip()
            if not plan or len(plan) > 24000:
                raise ValueError("The plan must contain between 1 and 24,000 characters.")
            description = with_plan(issue["description"], plan)
            if proposed_plan(description) != plan:
                raise ValueError("Use level-three headings within the Proposed Plan.")
            await self.linear.update(issue["id"], description=description)
            return f"Saved the Proposed Plan section for {issue_id}."
        if name == "approve_issue":
            issue, repo = await self.live_issue(issue_id)
            await self.runner.approve(issue, repo)
            return f"Approved {issue_id}. Its plan is locked and the issue is queued in Todo."
        if name == "worker_status":
            return self.runner.status()
        if name == "spawn_worker":
            return await self.runner.dispatch(issue_id, args["repo"].lower(), args["task"])
        if name == "resume_worker":
            return await self.runner.dispatch(issue_id, self.runner.task(issue_id)["repo"],
                                              args["message"], resume=True)
        if name == "read_notes":
            return self.runner.read_notes(issue_id)
        if name == "read_diff":
            return await self.runner.read_diff(issue_id)
        if name == "ship_worker":
            return self.runner.ship(issue_id)
        if name == "park_worker":
            if self.runner.task(issue_id)["phase"] not in {"review", "blocked"}:
                raise ValueError("Wait until the current worker or publication has stopped.")
            self.runner.block(issue_id, args["reason"][:2000])
            return "Worktree and worker session preserved."
        if name == "cleanup_worker":
            await self.runner.cleanup(issue_id)
            return "Worktree cleaned up."
        raise ValueError("Unknown tool.")

    async def state(self, after, issue_revision=""):
        login = self.login if not self.coordinator.account and self.login and time.monotonic() < self.login["expires"] else None
        messages = self.runner.rows("SELECT * FROM chat WHERE id > ? ORDER BY id LIMIT 200", (after,))
        for message in messages:
            if message["role"] == "assistant":
                message["html"] = markdown(message["text"])
        state = {"messages": messages,
                 "busy": self.busy, "canInterrupt": bool(self.coordinator.turn),
                 "stream": self.coordinator.stream,
                 "streamHtml": markdown(self.coordinator.stream) if self.coordinator.stream else "",
                 "tasks": self.runner.status(),
                 "signedIn": bool(self.coordinator.account), "login": login,
                 "paused": self.runner.setting("paused"), "view": self.runner.setting("view"),
                 "codex": self.coordinator.status(), "issueError": self.issue_error}
        if self.issue_snapshot is not None and issue_revision != self.issue_revision:
            state.update(projects=self.issue_snapshot, issueRevision=self.issue_revision)
        return state

    async def idle(self):
        if self.busy or self.messages or self.coordinator.turn:
            return False
        if any(task["phase"] in ACTIVE_PHASES for task in self.runner.status()):
            return False
        if self.coordinator.account and not self.runner.setting("paused"):
            if self.runner.rows("SELECT 1 FROM events WHERE delivered=0 LIMIT 1"):
                return False
        return True

    async def submit(self, body):
        text = body.get("text")
        project = body.get("project") or ""
        if not isinstance(text, str) or not text.strip() or not isinstance(project, str):
            raise ValueError("Enter a message.")
        text = text.strip()
        if text == "/interrupt":
            await self.coordinator.interrupt()
            self.runner.chat("user", text)
            return
        found = self.mapped_project(project) if project else None
        if project and not found:
            raise ValueError("The selected project is unavailable. Refresh the project list.")
        repo = found[0] if found else ""
        previous = self.runner.setting("view")
        if project != previous:
            self.runner.save_setting("view", project)
        label = found[1]["name"] if found else "All projects"
        message = f"[Switched view: {label}]\n{text}" if project != previous else text
        self.runner.chat("user", message)
        if self.coordinator.turn and self.turn_repo == repo and not text.startswith("/"):
            try:
                await self.coordinator.steer(message)
                return
            except (OSError, RuntimeError, ValueError):
                pass  # The turn or the server can exit before the steer is accepted.
        self.messages.append((repo, text, message))
        self.runner.wake.set()

    async def context(self, repo, issues=()):
        cwd = await self.runner.checkout(repo, refresh=repo != self.turn_repo) if repo else self.runner.directory
        context = []
        rules = (cwd / "AGENTS.md").resolve()
        if rules.is_relative_to(cwd) and rules.is_file():
            context.append("Active repository AGENTS.md:\n" + read_small(rules, 32000))
        for issue in dict.fromkeys(issues):
            row = self.runner.task(issue)
            context.append(f"Approved plan for {issue}:\n{row['plan']}")
        tasks = self.runner.status()
        if tasks:
            context.append("Current approved work:\n" + json.dumps(tasks, ensure_ascii=False))
        self.turn_repo = repo
        return cwd, "\n\n".join(context)

    async def respond(self, repo, message, issues=()):
        cwd, context = await self.context(repo, issues)
        text = f"{message}\n\n--- Current Symphony context ---\n{context}" if context else message
        attributed = list(dict.fromkeys(issues))
        started = time.time()
        open_turns = [(issue, self.runner.record_execution(
            issue, "coordinator", started=started, detail="Coordinator turn started.",
            data={"issues": attributed},
        )) for issue in attributed]
        detail = "Coordinator handled worker events."
        try:
            return await self.coordinator.respond(text, cwd)
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            detail = f"Coordinator turn failed: {error}"
            raise
        finally:
            ended = time.time()
            for _, execution_id in open_turns:
                self.runner.close_execution(execution_id, ended=ended, detail=detail)

    async def handle(self, repo, text, message):
        command, _, argument = text.partition(" ")
        if command == "/login":
            result = await self.coordinator.login()
            self.login = {"url": result["verificationUrl"], "code": result["userCode"],
                          "expires": time.monotonic() + 900}
        elif command == "/compact":
            cwd, _ = await self.context(repo)
            await self.coordinator.compact(cwd)
        elif command == "/status":
            status = self.coordinator.status()
            usage = [f"{status['model']} {status['effort']}".strip()]
            if status["contextUsed"] is not None:
                usage.append(f"Context {status['contextUsed']}% used")
            usage.extend(f"{label} {status['rateLimits'][label]['left']}% left"
                         for label in ("5h", "weekly") if label in status["rateLimits"])
            summary = [f"{task['issue']} · {task['phase']}" for task in self.runner.status()]
            self.runner.chat("system", "\n".join(filter(None, [
                " · ".join(filter(None, usage)), "\n".join(summary) or "No approved work in progress.",
            ])))
        elif command in {"/model", "/effort"}:
            if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", argument):
                raise ValueError(f"Enter a value after {command}.")
            self.runner.save_setting(command[1:], argument)
            self.runner.chat("system", f"{command[1:].capitalize()} set to {argument}.")
        elif command == "/continue":
            self.runner.save_setting("paused", "")
            self.runner.chat("system", "Automatic dispatch resumed.")
            self.runner.wake.set()
        elif command == "/retry":
            row = self.runner.task(argument.upper())
            if row["phase"] == "approving":
                await self.runner.activate(row["issue"], notify=True)
            elif row["phase"] == "finish_failed":
                self.runner.change(row["issue"], phase="finishing")
                self.runner.job(self.runner.finish(row["issue"]))
            elif row["phase"] == "blocked":
                self.runner.change(row["issue"], attempts=0)
                await self.respond(row["repo"], f"The operator requested another attempt at {row['issue']}.",
                                   (row["issue"],))
            else:
                raise ValueError(f"{row['issue']} is {row['phase']}.")
        elif command.startswith("/"):
            raise ValueError("Available commands: /login, /compact, /status, /model, /effort, /interrupt, /continue, /retry.")
        else:
            await self.respond(repo, message)

    def events_delivered(self, event_ids):
        with self.runner.db:
            self.runner.db.executemany("UPDATE events SET delivered=1 WHERE id=?", [(eid,) for eid in event_ids])

    async def pump(self):
        while True:
            await self.runner.wake.wait()
            self.runner.wake.clear()
            while True:
                if self.messages:
                    self.busy = True
                    try:
                        await self.handle(*self.messages.popleft())
                    except (ValueError, RuntimeError, OSError, TimeoutError) as error:
                        self.runner.chat("system", str(error) or "The request timed out.")
                    finally:
                        self.busy = False
                    continue
                if self.runner.setting("paused"):
                    break
                if await self.runner.dispatch_approved():
                    continue
                if not self.coordinator.account:
                    break
                self.runner.discard_stale_events()
                events = self.runner.rows("SELECT * FROM events WHERE delivered=0 ORDER BY id")
                if not events:
                    break
                rows = {issue: self.runner.task(issue) for issue in dict.fromkeys(event["issue"] for event in events)}
                repos = list(dict.fromkeys(row["repo"] for row in rows.values()))
                repo = repos[0] if len(repos) == 1 else ""
                if len(events) == 1:
                    prompt = f"Worker event for {events[0]['issue']}: {events[0]['detail']}"
                else:
                    lines = "\n".join(f"- {event['issue']}: {event['detail']}" for event in events)
                    prompt = f"Worker events ({len(events)}):\n{lines}"
                self.busy = True
                try:
                    status = await self.respond(repo, prompt, tuple(rows))
                    self.events_delivered([event["id"] for event in events])
                    if status == "interrupted":
                        break
                except (ValueError, RuntimeError, OSError, TimeoutError) as error:
                    self.runner.save_setting("paused", str(error) or "Coordinator timed out.")
                    self.runner.chat("system", f"Automatic dispatch paused. {self.runner.setting('paused')}")
                    break
                finally:
                    self.busy = False

    async def start(self):
        self.runner.recover()
        self.runner.job(self.poll_issues())
        try:
            await self.coordinator.connect()
        except (OSError, RuntimeError, TimeoutError) as error:
            self.runner.chat("system", str(error) or "Codex startup timed out.")
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
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self' https: data:; base-uri 'none'; frame-ancestors 'none'")
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
            if url.path == "/healthz":
                self.json_response({"status": "ok"})
            elif url.path == "/api/state":
                after = int(params.get("after", ["0"])[0])
                self.json_response(self.run_async(app.state(after, params.get("issues", [""])[0])))
            elif url.path == "/api/issue":
                issue = self.run_async(issue_details(app.linear, params.get("id", [""])[0]))
                cache_control = "private, max-age=60"
                if params.get("updated") == [issue["updatedAt"]]:
                    cache_control = "private, max-age=31536000, immutable"
                self.json_response(issue, cache_control=cache_control)
            elif url.path == "/api/execution":
                self.json_response(self.run_async(app.execution(params.get("id", [""])[0])))
            elif url.path == "/api/linear-asset":
                self.linear_asset(params.get("url", [""])[0])
            else:
                self.json_response({"error": "Page not found."}, 404)
        except (ValueError, RuntimeError, OSError) as error:
            self.json_response({"error": str(error)}, 503)

    def do_POST(self):
        if self.path != "/api/chat":
            self.json_response({"error": "Page not found."}, 404)
            return
        if (self.headers.get("X-Symphony") != "1" or self.headers.get_content_type() != "application/json"
                or self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin"):
            self.json_response({"error": "Same-origin requests only."}, 403)
            return
        try:
            length = int(self.headers["Content-Length"])
            if not 0 < length <= BODY_LIMIT:
                raise ValueError("Message is too large.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("Expected a chat message.")
            self.json_response(self.run_async(self.server.app.submit(body)), 202)
        except (TypeError, ValueError, RuntimeError, OSError) as error:
            self.json_response({"error": str(error)}, 400)


class IdleHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if urlsplit(self.path).path != "/idle":
            body, status = b'{"error":"Not found."}', 404
        else:
            future = asyncio.run_coroutine_threadsafe(self.server.app.idle(), self.server.loop)
            try:
                idle = future.result(timeout=5)
                body, status = json.dumps({"idle": idle}).encode(), 200
            except RequestTimeout:
                future.cancel()
                body, status = b'{"error":"Unavailable."}', 503
            except (RuntimeError, OSError):
                body, status = b'{"error":"Unavailable."}', 503
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        with suppress(BrokenPipeError, ConnectionResetError):
            self.end_headers()
            self.wfile.write(body)


async def serve():
    os.umask(0o077)
    root = Path(os.environ.get("SYMPHONY_DATA", "/data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    Path.home().mkdir(parents=True, exist_ok=True)
    if key := os.environ.get("OPENCODE_GO_API_KEY"):
        auth = root / "worker-data/opencode/auth.json"
        auth.parent.mkdir(parents=True, exist_ok=True)
        temp = auth.with_suffix(".new")
        temp.write_text(json.dumps({"opencode-go": {"type": "api", "key": key}}))
        temp.replace(auth)
    exit_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, exit_event.set)
    with ((root / ".symphony.lock").open("w") as lock,
          ThreadingHTTPServer(("127.0.0.1", 8080), Handler) as http,
          ThreadingHTTPServer(("0.0.0.0", 8081), IdleHandler) as idle_http):
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        http.loop = idle_http.loop = loop
        http.app = idle_http.app = Symphony(root)
        Thread(target=http.serve_forever, daemon=True).start()
        Thread(target=idle_http.serve_forever, daemon=True).start()
        pump = asyncio.create_task(http.app.pump())
        http.app.runner.job(http.app.start())
        try:
            await exit_event.wait()
        finally:
            await asyncio.to_thread(http.shutdown)
            await asyncio.to_thread(idle_http.shutdown)
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
