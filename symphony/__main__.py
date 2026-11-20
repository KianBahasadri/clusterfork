"""Serve Symphony on loopback behind the tailnet HTTPS proxy."""

import asyncio
from collections import deque
from contextlib import suppress
from concurrent.futures import TimeoutError as RequestTimeout
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
from threading import Thread
import time
from urllib.parse import parse_qs, urlsplit

from .codex import Codex, TOOL_SPECS
from .runner import Runner, read_small
from .services import ISSUE_ID, Linear, proposed_plan, with_plan


WEB = Path(__file__).parent / "web"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.png": ("favicon.png", "image/png"),
}
BODY_LIMIT = 32000


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

    async def refresh_projects(self):
        async with self.projects_lock:
            if time.monotonic() - self.fetched_projects >= 60 or not self.fetched_projects:
                self.repos, self.link_problems = await self.linear.projects()
                self.fetched_projects = time.monotonic()
        return self.repos

    async def project_list(self):
        repos = await self.refresh_projects()
        return [{"repo": repo, "label": ", ".join(p["name"] for p in projects)}
                for repo, projects in sorted(repos.items())]

    async def issues(self, repo):
        projects = (await self.refresh_projects()).get(repo)
        if not projects:
            raise ValueError(f"{repo} has no mapped Linear project.")
        return [
            {**{key: row[key] for key in ("identifier", "title", "url", "state")}, "plan": proposed_plan(row["description"])}
            for row in await self.linear.issues(projects)
        ]

    async def live_issue(self, identifier):
        issue = await self.linear.issue(identifier)
        await self.refresh_projects()
        project = (issue["project"] or {}).get("id")
        for repo, projects in self.repos.items():
            if any(item["id"] == project for item in projects):
                return issue, repo
        raise ValueError(self.link_problems.get(project, f"{identifier} is not in a mapped Linear project."))

    async def tool(self, name, args):
        keys = TOOL_SPECS[name][1]
        if not isinstance(args, dict) or set(args) != set(keys) or any(not isinstance(args[key], str) for key in keys):
            raise ValueError("Invalid tool arguments.")
        issue_id = args.get("issue_id")
        if issue_id is not None and not ISSUE_ID.fullmatch(issue_id):
            raise ValueError("Use an uppercase Linear issue identifier.")
        if name == "list_repos":
            return await self.project_list()
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
            return f"Approved {issue_id}. Its plan is locked and the issue is In Progress."
        if name == "worker_status":
            return self.runner.status()
        if name == "spawn_worker":
            return self.runner.dispatch(issue_id, args["repo"].lower(), args["task"])
        if name == "resume_worker":
            return self.runner.dispatch(issue_id, self.runner.task(issue_id)["repo"], args["message"], resume=True)
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

    async def state(self, after):
        login = self.login if not self.coordinator.account and self.login and time.monotonic() < self.login["expires"] else None
        return {"messages": self.runner.rows("SELECT * FROM chat WHERE id > ? ORDER BY id LIMIT 200", (after,)),
                "busy": self.busy, "canInterrupt": bool(self.coordinator.turn),
                "stream": self.coordinator.stream, "tasks": self.runner.status(),
                "signedIn": bool(self.coordinator.account), "login": login,
                "paused": self.runner.setting("paused"), "view": self.runner.setting("view"),
                "codex": self.coordinator.status()}

    async def submit(self, body):
        text = body.get("text")
        repo = body.get("repo") or ""
        if not isinstance(text, str) or not text.strip() or not isinstance(repo, str):
            raise ValueError("Enter a message.")
        text = text.strip()
        if repo and repo not in self.repos:
            raise ValueError("The selected repository is unavailable. Refresh the repo list.")
        previous = self.runner.setting("view")
        if repo != previous:
            self.runner.save_setting("view", repo)
        message = f"[Switched view: {repo or 'All repositories'}]\n{text}" if repo != previous else text
        self.runner.chat("user", message)
        if text == "/interrupt":
            await self.coordinator.interrupt()
            return
        if self.coordinator.turn and self.turn_repo == repo and not text.startswith("/"):
            try:
                await self.coordinator.steer(message)
                return
            except (RuntimeError, ValueError):
                pass  # The turn can complete between the check and the steer.
        self.messages.append((repo, text, message))
        self.runner.wake.set()

    async def context(self, repo, issue=None):
        cwd = await self.runner.checkout(repo, refresh=repo != self.turn_repo) if repo else self.runner.directory
        context = []
        rules = (cwd / "AGENTS.md").resolve()
        if rules.is_relative_to(cwd) and rules.is_file():
            context.append("Active repository AGENTS.md:\n" + read_small(rules, 32000))
        if issue:
            row = self.runner.task(issue)
            context.append(f"Approved plan for {issue}:\n{row['plan']}")
        tasks = self.runner.status()
        if tasks:
            context.append("Current approved work:\n" + json.dumps(tasks, ensure_ascii=False))
        self.turn_repo = repo
        return cwd, "\n\n".join(context)

    async def respond(self, repo, message, issue=None):
        cwd, context = await self.context(repo, issue)
        text = f"{message}\n\n--- Current Symphony context ---\n{context}" if context else message
        return await self.coordinator.respond(text, cwd)

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
                                   row["issue"])
            else:
                raise ValueError(f"{row['issue']} is {row['phase']}.")
        elif command.startswith("/"):
            raise ValueError("Available commands: /login, /compact, /status, /model, /effort, /interrupt, /continue, /retry.")
        else:
            await self.respond(repo, message)

    def event_delivered(self, event_id):
        with self.runner.db:
            self.runner.db.execute("UPDATE events SET delivered=1 WHERE id=?", (event_id,))

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
                if not self.coordinator.account or self.runner.setting("paused"):
                    break
                events = self.runner.rows("SELECT * FROM events WHERE delivered=0 ORDER BY id LIMIT 1")
                if not events:
                    break
                event = events[0]
                row = self.runner.task(event["issue"])
                self.busy = True
                try:
                    status = await self.respond(row["repo"], f"Worker event for {row['issue']}: {event['detail']}",
                                                row["issue"])
                    self.event_delivered(event["id"])
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
        try:
            await self.coordinator.connect()
        except (OSError, RuntimeError, TimeoutError) as error:
            self.runner.chat("system", str(error) or "Codex startup timed out.")
        self.runner.job(self.coordinator.poll_status())
        self.runner.wake.set()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def json_response(self, value, status=200):
        self.respond(json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def respond(self, data, content_type, status=200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def run_async(self, task):
        future = asyncio.run_coroutine_threadsafe(task, self.server.loop)
        try:
            return future.result(timeout=55)
        except RequestTimeout:
            future.cancel()
            raise ValueError("The request timed out.") from None

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
            elif url.path == "/api/projects":
                self.json_response(self.run_async(app.project_list()))
            elif url.path == "/api/issues":
                self.json_response(self.run_async(app.issues(params.get("repo", [""])[0])))
            elif url.path == "/api/state":
                self.json_response(self.run_async(app.state(int(params.get("after", ["0"])[0]))))
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
    with (root / ".symphony.lock").open("w") as lock, ThreadingHTTPServer(("127.0.0.1", 8080), Handler) as http:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        http.loop = loop
        http.app = Symphony(root)
        Thread(target=http.serve_forever, daemon=True).start()
        pump = asyncio.create_task(http.app.pump())
        http.app.runner.job(http.app.start())
        try:
            await exit_event.wait()
        finally:
            await asyncio.to_thread(http.shutdown)
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
