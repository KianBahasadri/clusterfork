"""One SQLite ledger, one worktree at a time per repository."""

import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from .services import ci_runs, ci_workflows, command, git, proposed_plan, stop_process


WORKER_LIMIT = 3
WORKER_SECONDS = 3600
ATTEMPT_LIMIT = 3
NOTES_LINES = 120
CI_DISCOVERY_SECONDS = 480
SESSION_ID = re.compile(r"ses_[A-Za-z0-9]+\Z")

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS tasks (
    issue TEXT PRIMARY KEY, project TEXT NOT NULL, repo TEXT NOT NULL,
    title TEXT NOT NULL, plan TEXT NOT NULL, phase TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, session TEXT, pid INTEGER,
    base TEXT, tree TEXT, audit TEXT, check_exit INTEGER, detail TEXT,
    revision TEXT, workflows TEXT, updated REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS repository_owner ON tasks(repo)
    WHERE phase NOT IN ('approving', 'approved', 'done');
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, issue TEXT NOT NULL, detail TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY, role TEXT NOT NULL, text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def worker_prompt(row, artifacts, instruction):
    return f"""Implement {row['issue']} in this Git worktree. Read and follow the repository's AGENTS.md.
Keep changes scoped to the approved plan below and leave all code changes in the worktree.
The coordinator owns publication. Do not push, change Linear, or open a pull request.
Run the relevant repository checks. Write their shell commands, joined with && where needed,
to {artifacts / 'checks.sh'} so the runner can repeat them and report the exit status.
Keep {artifacts / 'notes.md'} at 120 lines or fewer, updating it in place. Include only
assumptions and judgment calls, blockers or workarounds, and deviations or risks.
Write "None" if there are none. Do not include checks run or open questions.
If blocked, stop and write the reason to {artifacts / 'blocked.txt'}.

Approved plan:
{row['plan']}

Coordinator direction:
{instruction or 'Implement the approved plan.'}
"""


def read_small(path, limit):
    if path.is_symlink():
        raise RuntimeError(f"{path.name} must be a regular file.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError(f"{path.name} is too large.")
    return data.decode()


class Runner:
    def __init__(self, directory, linear):
        self.directory = directory
        self.linear = linear
        self.db = sqlite3.connect(directory / "symphony.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.jobs = set()
        self.cloning = {}
        self.wake = asyncio.Event()

    def setting(self, key, default=""):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def save_setting(self, key, value):
        self.save_settings({key: value})

    def save_settings(self, values):
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)", values.items())

    def chat(self, role, text):
        with self.db:
            self.db.execute("INSERT INTO chat(role,text) VALUES (?,?)", (role, text))

    def rows(self, query, parameters=()):
        return [dict(row) for row in self.db.execute(query, parameters)]

    def task(self, issue):
        rows = self.rows("SELECT * FROM tasks WHERE issue=?", (issue,))
        if not rows:
            raise ValueError(f"No approved plan for {issue}.")
        return rows[0]

    def status(self):
        return self.rows("""SELECT issue, repo, phase, attempts, session, check_exit, detail, revision
                            FROM tasks WHERE phase != 'done' ORDER BY updated DESC""")

    def change(self, issue, event=None, **fields):
        fields["updated"] = time.time()
        assignments = ", ".join(f"{field}=?" for field in fields)
        with self.db:
            self.db.execute(f"UPDATE tasks SET {assignments} WHERE issue=?", (*fields.values(), issue))
            if event:
                self.db.execute("INSERT INTO events(issue,detail) VALUES (?,?)", (issue, event))
                self.db.execute("INSERT INTO chat(role,text) VALUES ('worker',?)", (f"{issue} · {event}",))
        self.wake.set()

    def job(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)

    def block(self, issue, detail):
        detail = detail.strip() or "Interrupted."
        self.change(issue, event=f"Blocked. {detail}", phase="blocked", pid=None, detail=detail)

    def repo_dir(self, repo):
        return self.directory / "repos" / repo

    def worktree(self, issue):
        return self.directory / "worktrees" / issue

    def artifacts(self, issue):
        return self.directory / "runs" / issue

    async def checkout(self, repo, *, refresh=False):
        path = self.repo_dir(repo)
        async with self.cloning.setdefault(repo, asyncio.Lock()):
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                await git(path.parent, "clone", "--branch", "main", "--", f"https://github.com/{repo}.git", str(path))
            elif not (path / ".git").is_dir():
                raise RuntimeError(f"Invalid checkout for {repo}: {path}")
            elif refresh:
                await git(path, "fetch", "origin", "refs/heads/main:refs/remotes/origin/main")
                await git(path, "reset", "--hard", "origin/main")
        return path

    async def approve(self, issue, repo):
        identifier = issue["identifier"]
        plan = proposed_plan(issue["description"])
        if not plan or len(plan) > 24000:
            raise ValueError("The proposed plan must contain between 1 and 24,000 characters.")
        if issue["state"]["type"] in {"completed", "canceled"}:
            raise ValueError(f"{identifier} is already {issue['state']['name']}.")
        with self.db:
            inserted = self.db.execute("""INSERT INTO tasks(issue,project,repo,title,plan,phase,updated)
                                          VALUES (?,?,?,?,?,'approving',?)
                                          ON CONFLICT(issue) DO NOTHING""",
                                       (identifier, issue["project"]["id"], repo, issue["title"], plan, time.time()))
            if not inserted.rowcount:
                raise ValueError(f"{identifier} already has a recorded approval.")
        await self.activate(identifier)

    async def activate(self, issue, *, notify=False):
        await self.linear.set_state(issue, "In Progress", "started")
        event = "Plan approved. Ready for dispatch." if notify else None
        self.change(issue, phase="approved", event=event)

    def dispatch(self, issue, repo, instruction, *, resume=False):
        row = self.task(issue)
        if row["repo"] != repo:
            raise ValueError(f"{issue} is approved for {row['repo']}.")
        allowed = {"review", "blocked"} if resume else {"approved"}
        if row["phase"] not in allowed:
            raise ValueError(f"{issue} is {row['phase']}.")
        if self.setting("paused"):
            raise ValueError(f"Dispatch is paused. {self.setting('paused')}")
        if row["attempts"] >= ATTEMPT_LIMIT:
            raise ValueError(f"{issue} reached its rework limit. Park it and ask the operator to /retry {issue}.")
        if len(self.rows("SELECT 1 FROM tasks WHERE phase='working'")) >= WORKER_LIMIT:
            raise ValueError("The worker limit is reached. Try when a worker finishes.")
        owner = self.rows("""SELECT issue FROM tasks WHERE repo=? AND issue != ?
                             AND phase NOT IN ('approving','approved','done')""", (repo, issue))
        if owner:
            raise ValueError(f"{repo} is reserved by {owner[0]['issue']}.")
        self.change(issue, phase="working", check_exit=None, tree=None, audit=None, detail=None,
                    attempts=row["attempts"] + 1)
        self.job(self.run_worker(issue, instruction))
        return {"issue": issue, "phase": "working"}

    async def require_in_progress(self, row):
        live = await self.linear.issue(row["issue"])
        if ((live["project"] or {}).get("id") != row["project"]
                or live["state"] != {"name": "In Progress", "type": "started"}):
            raise RuntimeError(f"{row['issue']} must still be In Progress in its approved Linear project.")

    async def run_worker(self, issue, instruction):
        row = self.task(issue)
        worktree, artifacts = self.worktree(issue), self.artifacts(issue)
        try:
            await self.require_in_progress(row)
            repo = await self.checkout(row["repo"])
            await git(repo, "fetch", "origin", "refs/heads/main:refs/remotes/origin/main")
            base = await git(repo, "rev-parse", "origin/main")
            if not worktree.exists():
                worktree.parent.mkdir(parents=True, exist_ok=True)
                await git(repo, "worktree", "add", "-b", f"symphony/{issue}", str(worktree), base)
            elif row["revision"] == base and row["base"] != base:
                await git(worktree, "reset", "--mixed", base)
            self.change(issue, base=base, revision=None, workflows=None)
            artifacts.mkdir(parents=True, exist_ok=True)
            for name in ("checks.sh", "blocked.txt"):
                (artifacts / name).unlink(missing_ok=True)
            env = {key: value for key, value in os.environ.items()
                   if key not in {"GITHUB_TOKEN", "GH_TOKEN", "LINEAR_API_KEY", "OPENAI_API_KEY",
                                  "CODEX_HOME", "OPENCODE_GO_API_KEY"}}
            env["XDG_DATA_HOME"] = str(self.directory / "worker-data")
            env["XDG_CONFIG_HOME"] = str(self.directory / "worker-config")
            env["XDG_STATE_HOME"] = str(self.directory / "worker-state")
            env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"permissions": [
                {"action": "shell", "resource": "git push *", "effect": "deny"},
                {"action": "shell", "resource": "gh pr create *", "effect": "deny"},
                {"action": "subagent", "resource": "*", "effect": "deny"},
            ]})
            args = ["opencode", "run", "--standalone", "--auto", "--format", "json",
                    "--model", os.environ.get("SYMPHONY_WORKER_MODEL", "opencode-go/gpt-6-luna")]
            if row["session"]:
                args += ["--session", row["session"]]
            with (artifacts / "worker.log").open("ab") as log:
                log.write(f"\n--- attempt {row['attempts']} ---\n".encode())
                async with asyncio.timeout(WORKER_SECONDS):
                    await self.run_opencode(issue, args + [worker_prompt(row, artifacts, instruction)], worktree, env, log)
                    if (artifacts / "blocked.txt").exists():
                        raise RuntimeError(read_small(artifacts / "blocked.txt", 4000))
                    checks = read_small(artifacts / "checks.sh", 16000).strip()
                    if not checks:
                        raise RuntimeError("The worker supplied no verification command in checks.sh.")
                    log.write(f"\n--- verification ---\n{checks}\n".encode())
                    log.flush()
                    process = await asyncio.create_subprocess_exec(
                        "bash", "-e", "-o", "pipefail", "-c", checks, cwd=worktree, env=env,
                        stdout=log, stderr=log, start_new_session=True,
                    )
                    self.change(issue, pid=process.pid)
                    try:
                        status = await process.wait()
                    finally:
                        await stop_process(process)
            try:
                await git(worktree, "merge-base", "--is-ancestor", base, "HEAD")
            except RuntimeError:
                raise RuntimeError("Rebase the worktree onto origin/main and repeat verification.") from None
            await git(worktree, "add", "-A")
            tree = await git(worktree, "write-tree")
            self.change(issue, event=f"Worker finished. Verification exited {status}. Audit notes.md.",
                        phase="review", pid=None, check_exit=status, tree=tree)
        except TimeoutError:
            self.block(issue, "The worker exceeded its one-hour safety timeout.")
        except (RuntimeError, OSError, ValueError) as error:
            self.block(issue, str(error))

    async def run_opencode(self, issue, args, cwd, env, log):
        process = await asyncio.create_subprocess_exec(
            *args, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True, limit=4 * 1024 * 1024,
        )
        self.change(issue, pid=process.pid)
        error = ""
        try:
            async for line in process.stdout:
                log.write(line)
                if not line.startswith(b"{"):
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                session = event.get("sessionID")
                if isinstance(session, str) and SESSION_ID.fullmatch(session):
                    if session != self.task(issue)["session"]:
                        self.change(issue, session=session)
                if event.get("type") == "error":
                    payload = event.get("error") or {}
                    error = str(payload.get("data", {}).get("message") or payload.get("name") or "OpenCode error")
            status = await process.wait()
        finally:
            await stop_process(process)
        if status or error:
            if re.search("quota|rate.limit|subscription|unauthorized|invalid.api.key", error, re.I):
                self.save_setting("paused", f"OpenCode Go: {error[:1000]}")
            raise RuntimeError((error[:1000] or f"OpenCode exited {status}.") + " See worker.log.")

    def notes(self, issue):
        self.task(issue)
        text = read_small(self.artifacts(issue) / "notes.md", 24000)
        if not text.strip() or len(text.splitlines()) > NOTES_LINES:
            raise ValueError("notes.md must contain between 1 and 120 lines.")
        return text

    def read_notes(self, issue):
        notes = self.notes(issue)
        if self.task(issue)["phase"] == "review":
            self.change(issue, audit=sha256(notes.encode()).hexdigest())
        return notes

    async def read_diff(self, issue):
        row = self.task(issue)
        return (await git(self.worktree(issue), "diff", "--no-ext-diff", row["base"], "--"))[:48000]

    def ship(self, issue):
        row = self.task(issue)
        if row["phase"] != "review" or row["check_exit"] != 0:
            raise ValueError(f"{issue} needs a completed worker with passing verification.")
        if sha256(self.notes(issue).encode()).hexdigest() != row["audit"]:
            raise ValueError("Read the latest notes.md before shipping.")
        self.change(issue, phase="publishing")
        self.job(self.publish(issue))
        return {"issue": issue, "phase": "publishing"}

    async def publish(self, issue):
        row = self.task(issue)
        try:
            await self.require_in_progress(row)
            cwd = self.worktree(issue)
            await git(cwd, "add", "-A")
            tree = await git(cwd, "write-tree")
            if tree != row["tree"]:
                raise RuntimeError("The worktree changed after verification. Repeat checks before shipping.")
            await git(cwd, "fetch", "origin", "refs/heads/main:refs/remotes/origin/main")
            if row["base"] != await git(cwd, "rev-parse", "origin/main"):
                raise RuntimeError("origin/main moved after verification. Rebase and run checks again.")
            if tree == await git(cwd, "rev-parse", f"{row['base']}^{{tree}}"):
                self.change(issue, phase="finishing")
                return await self.finish(issue)
            revision = await git(
                cwd, "-c", "user.name=Symphony", "-c", "user.email=symphony@users.noreply.github.com",
                "commit-tree", tree, "-p", row["base"], "-m", f"{issue} {row['title']}",
            )
            workflows = await ci_workflows(cwd, row["base"], revision)
            self.change(issue, revision=revision, workflows=json.dumps(workflows))
            try:
                await git(cwd, "push", "origin", f"{revision}:refs/heads/main")
            except (RuntimeError, TimeoutError) as push_error:
                try:
                    await git(cwd, "fetch", "origin", "refs/heads/main:refs/remotes/origin/main")
                    await git(cwd, "merge-base", "--is-ancestor", revision, "origin/main")
                except (RuntimeError, TimeoutError):
                    raise RuntimeError("Git push failed. " + (str(push_error) or "Timed out.")) from None
            await self.after_push(issue)
        except (RuntimeError, OSError, ValueError, TimeoutError) as error:
            self.block(issue, str(error) or "GitHub operation timed out. Verify origin/main before retrying.")

    async def after_push(self, issue):
        workflows = json.loads(self.task(issue)["workflows"])
        if workflows:
            self.change(issue, phase="ci", event="Pushed to main. Waiting for GitHub Actions.")
            self.job(self.watch_ci(issue))
        else:
            self.change(issue, phase="finishing")
            await self.finish(issue)

    async def watch_ci(self, issue):
        row = self.task(issue)
        expected = set(json.loads(row["workflows"]))
        while True:
            try:
                runs = await ci_runs(row["repo"], row["revision"])
                by_path = {run["path"].split("@", 1)[0]: run for run in reversed(runs)}
                for path in expected & by_path.keys():
                    current = by_path[path]
                    if current["status"] == "completed" and current["conclusion"] != "success":
                        raise ValueError(f"{path} ended with {current['conclusion']}. {current['html_url']}")
                if all(by_path.get(path, {}).get("conclusion") == "success" for path in expected):
                    self.change(issue, phase="finishing")
                    return await self.finish(issue)
                if expected - by_path.keys() and time.time() - row["updated"] > CI_DISCOVERY_SECONDS:
                    missing = ", ".join(sorted(expected - by_path.keys()))
                    raise ValueError(f"No push CI started for {row['revision']}: {missing}")
            except (RuntimeError, ValueError) as error:
                self.block(issue, str(error))
                return
            await asyncio.sleep(20)

    async def cleanup(self, issue):
        row = self.task(issue)
        if row["phase"] not in {"done", "finishing"}:
            raise ValueError("Clean up only after verified publication.")
        repo = self.repo_dir(row["repo"])
        path = self.worktree(issue)
        if path.exists():
            await git(repo, "worktree", "remove", "--force", str(path))
        branch = f"symphony/{issue}"
        if await git(repo, "branch", "--list", branch):
            await git(repo, "branch", "-D", branch)

    async def finish(self, issue):
        try:
            await self.linear.set_state(issue, "Done", "completed")
        except (RuntimeError, ValueError) as error:
            detail = f"Publication verified. Cannot mark Linear Done: {error}"
            self.change(issue, phase="finish_failed", detail=detail, event=detail)
            return
        detail = "Done."
        try:
            await self.cleanup(issue)
        except (RuntimeError, OSError, TimeoutError) as error:
            detail = f"Done. Worktree cleanup needs attention: {error}"
        self.change(issue, phase="done", detail=detail, event=detail)

    async def recover_publish(self, issue):
        row = self.task(issue)
        try:
            if not row["revision"]:
                raise RuntimeError("Publication stopped before a commit was recorded.")
            cwd = self.worktree(issue)
            await git(cwd, "fetch", "origin", "refs/heads/main:refs/remotes/origin/main")
            await git(cwd, "merge-base", "--is-ancestor", row["revision"], "origin/main")
            await self.after_push(issue)
        except (RuntimeError, OSError, ValueError, TimeoutError) as error:
            self.block(issue, f"Publication interrupted. Verify origin/main: {error}")

    async def recover_approval(self, issue):
        try:
            await self.activate(issue, notify=True)
        except (RuntimeError, ValueError) as error:
            self.change(issue, event=f"Approval saved. Linear activation failed: {error}", detail=str(error))

    def recover(self):
        for row in self.status():
            issue = row["issue"]
            if row["phase"] == "working":
                self.block(issue, "The pod restarted. Resume the saved worker in its existing worktree.")
            elif row["phase"] == "approving":
                self.job(self.recover_approval(issue))
            elif row["phase"] == "publishing":
                self.job(self.recover_publish(issue))
            elif row["phase"] == "ci":
                self.job(self.watch_ci(issue))
            elif row["phase"] == "finishing":
                self.job(self.finish(issue))
