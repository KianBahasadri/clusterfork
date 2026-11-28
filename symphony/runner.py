"""One SQLite ledger, sequential per-repo queue, and 2-tier Flash -> Antigravity auto-escalation."""

import asyncio
from contextlib import suppress
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from stat import S_ISREG
import time
from uuid import uuid4

from .execution import redact
from .services import (
    CLOSED_STATES, ISSUE_ID, ci_jobs, ci_runs, ci_workflows, git, proposed_plan, stop_process,
)


WORKER_LIMIT = 3
FLASH_TIMEOUT_SECONDS = 900
ESCALATION_TIMEOUT_SECONDS = 1200
NOTES_LINES = 120
NOTES_BYTES = 24000
WORKER_MODEL = "opencode-go/deepseek-v4.1-flash#max"
ESCALATION_MODEL = "gemini-4-hydrogen-xhigh"
WORKER_CONFIG = {
    "permissions": [
        {"action": "shell", "resource": "git push *", "effect": "deny"},
        {"action": "shell", "resource": "gh pr create *", "effect": "deny"},
        {"action": "subagent", "resource": "*", "effect": "deny"},
        {"action": "websearch", "resource": "*", "effect": "deny"},
    ],
    "mcp": {
        "servers": {
            "context7": {
                "type": "remote",
                "url": "https://mcp.context7.com/mcp",
                "disabled": False,
            },
            "exa": {
                "type": "remote",
                "url": "https://mcp.exa.ai/mcp",
                "disabled": False,
            },
        },
    },
}
CI_DISCOVERY_SECONDS = 480
AGENTS_BYTES = 32000
SHARED_AGENTS = Path(__file__).resolve().parents[1] / "agents" / "AGENTS.md"
ACTIVE_LOCK_PHASES = ("working", "publishing", "ci", "finishing")
TASK_ID_RE = re.compile(r"(?:[A-Z][A-Z0-9]*-[0-9]+|ADHOC-[0-9]+)\Z")


class BusyError(RuntimeError):
    pass


SESSION_ID = re.compile(r"ses_[A-Za-z0-9]+\Z")
CHAT_IMAGE_NAME = re.compile(r"[0-9a-f]{32}\.(?:png|jpg|webp)\Z")

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS tasks (
    issue TEXT PRIMARY KEY, project TEXT NOT NULL, repo TEXT NOT NULL,
    title TEXT NOT NULL, plan TEXT NOT NULL, phase TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, session TEXT, pid INTEGER,
    base TEXT, tree TEXT, audit TEXT, check_exit INTEGER, detail TEXT,
    revision TEXT, workflows TEXT, updated REAL NOT NULL,
    tier TEXT NOT NULL DEFAULT '', escalated INTEGER NOT NULL DEFAULT 0,
    trigger TEXT NOT NULL DEFAULT '', branch TEXT NOT NULL DEFAULT '',
    priority INTEGER NOT NULL DEFAULT 2
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, issue TEXT NOT NULL, detail TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0, phase TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chat (
    id INTEGER PRIMARY KEY, role TEXT NOT NULL, text TEXT NOT NULL,
    attachments TEXT NOT NULL DEFAULT '[]',
    repo TEXT NOT NULL DEFAULT '',
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS threads (
    repo TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
    tools_hash TEXT NOT NULL, context_used INTEGER
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS repo_buttons (
    repo TEXT NOT NULL,
    label TEXT NOT NULL,
    prompt TEXT NOT NULL,
    mode TEXT NOT NULL,
    updated REAL NOT NULL,
    PRIMARY KEY (repo, label)
);
CREATE TABLE IF NOT EXISTS diagrams (
    repo TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS execution (
    id INTEGER PRIMARY KEY, issue TEXT NOT NULL, kind TEXT NOT NULL,
    started REAL NOT NULL, ended REAL, detail TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS execution_issue ON execution(issue, started);
CREATE TABLE IF NOT EXISTS ci_runs (
    run_id INTEGER NOT NULL, attempt INTEGER NOT NULL, issue TEXT NOT NULL,
    repo TEXT NOT NULL, revision TEXT NOT NULL, workflow TEXT NOT NULL,
    path TEXT NOT NULL, status TEXT NOT NULL, conclusion TEXT,
    url TEXT, created_at TEXT, run_started_at TEXT, updated_at TEXT,
    completed_at TEXT, diagnostic TEXT NOT NULL DEFAULT '',
    jobs_fetched INTEGER NOT NULL DEFAULT 0, observed REAL NOT NULL,
    PRIMARY KEY (run_id, attempt)
);
CREATE INDEX IF NOT EXISTS ci_runs_issue ON ci_runs(issue, run_started_at);
CREATE TABLE IF NOT EXISTS ci_jobs (
    run_id INTEGER NOT NULL, attempt INTEGER NOT NULL, job_id INTEGER NOT NULL,
    issue TEXT NOT NULL, name TEXT NOT NULL, status TEXT NOT NULL,
    conclusion TEXT, started_at TEXT, completed_at TEXT, url TEXT,
    diagnostic TEXT NOT NULL DEFAULT '', observed REAL NOT NULL,
    PRIMARY KEY (run_id, attempt, job_id)
);
CREATE TABLE IF NOT EXISTS coordinator_turns (
    id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    model TEXT NOT NULL,
    started REAL NOT NULL,
    ended REAL NOT NULL,
    duration REAL NOT NULL,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS coordinator_turns_repo_model ON coordinator_turns(repo, model, status);
CREATE TABLE IF NOT EXISTS token_usage (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    repo TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    cost_usd REAL,
    recorded REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS token_usage_breakdown ON token_usage(source, repo, model);
"""


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _optional_int(value):
    return int(value) if _finite_number(value) and value >= 0 else None


def _optional_float(value):
    return float(value) if _finite_number(value) and value >= 0 else None


def _opencode_step_usage(part):
    if not isinstance(part, dict):
        return None, None, None, None
    tokens = part.get("tokens")
    tokens = tokens if isinstance(tokens, dict) else {}
    input_tokens = _optional_int(tokens.get("input", tokens.get("inputTokens")))
    output_tokens = _optional_int(tokens.get("output", tokens.get("outputTokens")))
    total_tokens = _optional_int(tokens.get("total", tokens.get("totalTokens")))
    cost_usd = _optional_float(part.get("cost"))
    return input_tokens, output_tokens, total_tokens, cost_usd


def _cli_model(args, default=""):
    for index, item in enumerate(args):
        if item == "--model" and index + 1 < len(args):
            return str(args[index + 1])
    return default


def worker_prompt(row, artifacts, instruction=""):
    extra = f"\n\nAdditional direction:\n{instruction}" if instruction else ""
    return f"""Implement {row['issue']} ({row['title']}) in this Git worktree. Read and follow the repository's AGENTS.md.
Keep changes scoped, clean, and leave all code changes in the worktree.
Symphony owns publication. Do not push, change Linear, or open a pull request.
If the repository has tests or linters covering what you changed, run narrowly targeted checks for the changed area (targeting under 30 seconds—never repo-wide suites, which CI owns) and write that exact shell command to {artifacts / 'checks.sh'} (joined with && where needed). If no local checks apply, you may omit checks.sh.
Write {artifacts / 'notes.md'} (at most 120 lines) only if there are non-obvious assumptions, workarounds, or risks; omit it otherwise.
If blocked by missing dependencies, build environments, tools, credentials, or anything outside the repository code, stop immediately and write the concrete blocker to {artifacts / 'blocked.txt'}—never try to install missing system tooling.
Use the configured Context7 and Exa MCP tools for library docs and web search.
For browser checks, use the preinstalled Playwright package from `/opt/symphony/bin/python` with `--no-sandbox`, `--disable-dev-shm-usage`, and `--disable-gpu`.

Task:
{row['plan']}{extra}
"""


def escalation_prompt(row, artifacts, receipt):
    return f"""Implement {row['issue']} ({row['title']}) in this clean Git worktree reset to origin/main.
Read and follow the repository's AGENTS.md. Do not push, change Linear, or open a pull request.
If the repository has targeted checks/tests for the changed area (under 30 seconds), run them and save the command to {artifacts / 'checks.sh'}.
If genuinely blocked by missing system tooling or credentials outside the repo, write the blocker to {artifacts / 'blocked.txt'} and stop.

Task:
{row['plan']}

Prior Tier-1 (DeepSeek-V4.1-Flash) failure receipt (worktree has been reset cleanly to origin/main):
{receipt}
"""


def read_small(path, limit):
    if path.is_symlink():
        raise RuntimeError(f"{path.name} must be a regular file.")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise RuntimeError(f"{path.name} is too large.")
    return data.decode()


def shared_agents():
    path = SHARED_AGENTS
    try:
        if path.is_symlink():
            raise RuntimeError(f"{path.name} must be a regular file.")
        if not S_ISREG(path.stat().st_mode):
            raise RuntimeError(f"{path.name} must be a regular file.")
        return read_small(path, AGENTS_BYTES)
    except FileNotFoundError:
        raise RuntimeError(f"Global AGENTS.md is missing at {path}.") from None
    except RuntimeError as error:
        raise RuntimeError(f"Global AGENTS.md is unavailable: {error}") from None
    except (OSError, UnicodeError) as error:
        raise RuntimeError(f"Global AGENTS.md could not be read: {error}") from None


def rules_state(cwd):
    defaults = shared_agents()
    repo_rules = ""
    if cwd:
        rules = (cwd / "AGENTS.md").resolve()
        if rules.is_relative_to(cwd) and rules.is_file():
            repo_rules = read_small(rules, 32000)
    hashes = (sha256(defaults.encode()).hexdigest(), sha256(repo_rules.encode()).hexdigest())
    return defaults, repo_rules, hashes


def install_agent_instructions(directory):
    source = SHARED_AGENTS.resolve()
    shared_agents()
    relative = Path("worker-config") / "opencode" / "AGENTS.md"
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        if path.resolve() == source:
            return
        path.unlink()
    elif path.exists():
        if not path.is_file():
            raise RuntimeError(f"{relative} must be a regular file or symlink.")
        path.unlink()
    try:
        path.symlink_to(source)
    except OSError as error:
        raise RuntimeError(f"Cannot link global AGENTS.md at {relative}: {error}") from None


def bounded_snapshot(text, limit):
    text = redact(text)
    encoded = text.encode()
    truncated = len(encoded) > limit
    if truncated:
        text = encoded[:limit].decode(errors="ignore").rstrip()
    return text, truncated


def is_linear_task(row):
    return bool(row.get("project")) and bool(ISSUE_ID.fullmatch(row.get("issue") or ""))


class Runner:
    def __init__(self, directory, linear, *, escalator=None):
        self.directory = directory
        self.linear = linear
        self.escalator = escalator
        install_agent_instructions(directory)
        self.db = sqlite3.connect(directory / "symphony.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate_schema()
        self.jobs = set()
        self.cloning = {}
        self.config_lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.issue_refresh = asyncio.Event()
        self.projects_changed = False
        self.draining = False

    def _migrate_schema(self):
        with self.db:
            self.db.execute("DROP INDEX IF EXISTS repository_owner")
            self.db.execute(
                """CREATE UNIQUE INDEX IF NOT EXISTS repo_active_lock ON tasks(repo)
                   WHERE phase IN ('working', 'publishing', 'ci', 'finishing')"""
            )
            task_cols = {row["name"] for row in self.db.execute("PRAGMA table_info(tasks)")}
            for col, ddl in (
                ("tier", "TEXT NOT NULL DEFAULT ''"),
                ("escalated", "INTEGER NOT NULL DEFAULT 0"),
                ("trigger", "TEXT NOT NULL DEFAULT ''"),
                ("branch", "TEXT NOT NULL DEFAULT ''"),
                ("priority", "INTEGER NOT NULL DEFAULT 2"),
            ):
                if col not in task_cols:
                    self.db.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ddl}")
            if "phase" not in {row["name"] for row in self.db.execute("PRAGMA table_info(events)")}:
                self.db.execute("ALTER TABLE events ADD COLUMN phase TEXT NOT NULL DEFAULT ''")
            chat_cols = {row["name"] for row in self.db.execute("PRAGMA table_info(chat)")}
            if "attachments" not in chat_cols:
                self.db.execute("ALTER TABLE chat ADD COLUMN attachments TEXT NOT NULL DEFAULT '[]'")
            if "repo" not in chat_cols:
                self.db.execute("ALTER TABLE chat ADD COLUMN repo TEXT NOT NULL DEFAULT ''")
            if "meta" not in chat_cols:
                self.db.execute("ALTER TABLE chat ADD COLUMN meta TEXT NOT NULL DEFAULT '{}'")
            # Cleanly migrate legacy phases and stale Codex settings from previous PVC databases.
            self.db.execute("UPDATE tasks SET phase='queued' WHERE phase IN ('approving', 'approved')")
            self.db.execute("UPDATE tasks SET phase='parked' WHERE phase IN ('review', 'blocked')")
            self.db.execute("DELETE FROM settings WHERE key IN ('effort', 'thread', 'tools_hash')")
            self.db.execute(
                "DELETE FROM settings WHERE key='model' AND value NOT LIKE 'gemini-%' AND value NOT LIKE 'claude-%'"
            )

    def request_issue_refresh(self, projects=False):
        if projects:
            self.projects_changed = True
        self.issue_refresh.set()

    def setting(self, key, default=""):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def save_setting(self, key, value):
        self.save_settings({key: value})

    def save_settings(self, values):
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)", values.items())

    def repo_buttons(self, repo):
        return self.rows(
            "SELECT repo, label, prompt, mode FROM repo_buttons WHERE repo=? ORDER BY rowid",
            (repo,),
        )

    def set_repo_button(self, repo, label, prompt, mode="attach"):
        clean_repo = (repo or "").strip().lower()
        clean_label = (label or "").strip()
        clean_prompt = (prompt or "").strip()
        clean_mode = (mode or "attach").strip().lower()
        if not clean_repo:
            raise ValueError("Repository is required.")
        if not clean_label or len(clean_label) > 40:
            raise ValueError("Button label must contain between 1 and 40 characters.")
        if not clean_prompt or len(clean_prompt) > 4000:
            raise ValueError("Button prompt must contain between 1 and 4,000 characters.")
        if clean_mode not in {"attach", "send"}:
            raise ValueError("Button mode must be 'attach' or 'send'.")
        with self.db:
            self.db.execute(
                """INSERT INTO repo_buttons(repo, label, prompt, mode, updated)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(repo, label) DO UPDATE SET
                       prompt=excluded.prompt, mode=excluded.mode, updated=excluded.updated""",
                (clean_repo, clean_label, clean_prompt, clean_mode, time.time()),
            )
        return {"repo": clean_repo, "label": clean_label, "prompt": clean_prompt, "mode": clean_mode}

    def delete_repo_button(self, repo, label):
        clean_repo = (repo or "").strip().lower()
        clean_label = (label or "").strip()
        with self.db:
            cursor = self.db.execute(
                "DELETE FROM repo_buttons WHERE repo=? AND label=?",
                (clean_repo, clean_label),
            )
        if not cursor.rowcount:
            raise ValueError(f"No button named {clean_label} for {clean_repo}.")
        return {"deleted": True, "repo": clean_repo, "label": clean_label}

    def diagram(self, repo):
        rows = self.rows(
            "SELECT repo, title, source, updated FROM diagrams WHERE repo=?",
            (repo,),
        )
        return rows[0] if rows else None

    def save_diagram(self, repo, title, source):
        clean_repo = (repo or "").strip().lower()
        clean_title = (title or "").strip()
        clean_source = (source or "").strip()
        if not clean_repo:
            raise ValueError("Repository is required.")
        if not clean_title or len(clean_title) > 120:
            raise ValueError("Diagram title must contain between 1 and 120 characters.")
        if not clean_source or len(clean_source) > 32000:
            raise ValueError("Mermaid diagram source must contain between 1 and 32,000 characters.")
        updated = time.time()
        with self.db:
            self.db.execute(
                """INSERT INTO diagrams(repo, title, source, updated)
                   VALUES (?,?,?,?)
                   ON CONFLICT(repo) DO UPDATE SET
                       title=excluded.title, source=excluded.source, updated=excluded.updated""",
                (clean_repo, clean_title, clean_source, updated),
            )
        return {"repo": clean_repo, "title": clean_title, "source": clean_source, "updated": updated}

    def opencode_db_path(self):
        return self.directory / "worker-data" / "opencode" / "opencode.db"

    def opencode_auth_path(self):
        return self.directory / "worker-data" / "opencode" / "auth.json"

    def opencode_auth_env(self):
        env = {key: value for key, value in os.environ.items()
               if key not in {"GITHUB_TOKEN", "GH_TOKEN", "LINEAR_API_KEY", "OPENAI_API_KEY",
                              "CODEX_HOME", "OPENCODE_GO_API_KEY", "LINEAR_WEBHOOK_SECRET",
                              "LINEAR_WEBHOOK_PATH_TOKEN", "SYMPHONY_INTERNAL_TOKEN",
                              "SYMPHONY_INTERNAL_URL"}}
        env["XDG_DATA_HOME"] = str(self.directory / "worker-data")
        env["XDG_CONFIG_HOME"] = str(self.directory / "worker-config")
        env["XDG_STATE_HOME"] = str(self.directory / "worker-state")
        env["XDG_CACHE_HOME"] = str(self.directory / "worker-cache")
        return env

    def _read_opencode_auth_dict(self):
        path = self.opencode_auth_path()
        if path.is_symlink():
            return True, None, True
        if not path.exists():
            return False, None, False
        try:
            if not S_ISREG(path.stat().st_mode):
                return True, None, True
            with path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                return True, None, True
            data = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            return True, None, True
        if not isinstance(data, dict):
            return True, None, True
        return True, data, False

    def opencode_go_status(self):
        db_path = self.opencode_db_path()
        if db_path.is_symlink():
            return {"configured": False, "malformed": True}
        if db_path.exists():
            try:
                if not S_ISREG(db_path.stat().st_mode):
                    return {"configured": False, "malformed": True}
                con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                try:
                    rows = con.execute(
                        "SELECT value FROM credential WHERE integration_id = 'opencode-go'"
                    ).fetchall()
                finally:
                    con.close()
                configured = False
                for (raw_value,) in rows:
                    entry = json.loads(raw_value)
                    if (isinstance(entry, dict) and isinstance(entry.get("key"), str)
                            and entry["key"].strip()):
                        configured = True
                        break
                return {"configured": configured, "malformed": False}
            except (OSError, sqlite3.Error, UnicodeDecodeError, ValueError):
                return {"configured": False, "malformed": True}
        _, data, malformed = self._read_opencode_auth_dict()
        if malformed:
            return {"configured": False, "malformed": True}
        entry = data.get("opencode-go") if data else None
        configured = (
            isinstance(entry, dict)
            and isinstance(entry.get("key"), str)
            and bool(entry["key"].strip())
        )
        return {"configured": configured, "malformed": False}

    def has_opencode_go_key(self):
        return self.opencode_go_status()["configured"]

    def opencode_go_busy(self):
        return bool(self.rows("SELECT 1 FROM tasks WHERE phase='working' LIMIT 1"))

    def _ensure_opencode_dirs(self):
        db_dir = self.opencode_db_path().parent
        if db_dir.exists():
            return
        worker_data = self.directory / "worker-data"
        worker_data.mkdir(mode=0o700, exist_ok=True)
        db_dir.mkdir(mode=0o700, exist_ok=True)

    async def _run_opencode_auth(self, args, *, stdin_text=None):
        try:
            process = await asyncio.create_subprocess_exec(
                "opencode", "auth", *args,
                cwd=self.directory, env=self.opencode_auth_env(),
                stdin=(asyncio.subprocess.PIPE if stdin_text is not None
                       else asyncio.subprocess.DEVNULL),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError:
            raise RuntimeError("Could not update OpenCode Go credentials.") from None
        try:
            async with asyncio.timeout(30):
                stdout, _ = await process.communicate(
                    stdin_text.encode("utf-8") if stdin_text is not None else None
                )
        except (OSError, TimeoutError):
            raise RuntimeError("Could not update OpenCode Go credentials.") from None
        finally:
            await stop_process(process)
        if process.returncode:
            raise RuntimeError("Could not update OpenCode Go credentials.")
        return stdout.decode("utf-8", errors="replace")

    async def _opencode_go_ids(self):
        raw = await self._run_opencode_auth(["export", "--standalone", "opencode-go"])
        try:
            items = json.loads(raw or "[]")
        except ValueError:
            raise RuntimeError("Could not update OpenCode Go credentials.") from None
        if not isinstance(items, list):
            raise RuntimeError("Could not update OpenCode Go credentials.")
        return [item["id"] for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)]

    def _chmod_opencode_db(self):
        db_path = self.opencode_db_path()
        try:
            if db_path.exists() and not db_path.is_symlink() and S_ISREG(db_path.stat().st_mode):
                os.chmod(db_path, 0o600)
        except OSError:
            raise RuntimeError("Could not update OpenCode Go credentials.") from None

    async def set_opencode_go_key(self, key):
        if not isinstance(key, str):
            raise ValueError("Enter a valid OpenCode Go API key.")
        cleaned = key.strip()
        if not cleaned or len(cleaned) > 4096 or any(ch in cleaned for ch in "\r\n\x00"):
            raise ValueError("Enter a valid OpenCode Go API key.")
        async with self.config_lock:
            if self.opencode_go_busy():
                raise BusyError("Cannot change OpenCode Go credentials while a worker is running.")
            try:
                self._ensure_opencode_dirs()
            except OSError:
                raise RuntimeError("Could not update OpenCode Go credentials.") from None
            if self.opencode_go_status()["malformed"]:
                raise RuntimeError("Existing OpenCode auth store is invalid and cannot be updated safely.")
            old_ids = await self._opencode_go_ids()
            payload = json.dumps([{
                "id": f"cred_{uuid4().hex}",
                "integrationID": "opencode-go",
                "label": "API key",
                "active": True,
                "value": {"type": "key", "key": cleaned},
            }])
            await self._run_opencode_auth(["import", "--standalone"], stdin_text=payload)
            for old_id in old_ids:
                await self._run_opencode_auth(["logout", "--standalone", "opencode-go", old_id])
            self._chmod_opencode_db()

    async def clear_opencode_go_key(self):
        async with self.config_lock:
            if self.opencode_go_busy():
                raise BusyError("Cannot change OpenCode Go credentials while a worker is running.")
            db_path = self.opencode_db_path()
            auth_path = self.opencode_auth_path()
            if (not db_path.exists() and not db_path.is_symlink()
                    and not auth_path.exists() and not auth_path.is_symlink()):
                return
            if self.opencode_go_status()["malformed"]:
                raise RuntimeError("Existing OpenCode auth store is invalid and cannot be updated safely.")
            old_ids = await self._opencode_go_ids()
            for old_id in old_ids:
                await self._run_opencode_auth(["logout", "--standalone", "opencode-go", old_id])
            self._chmod_opencode_db()

    def chat(self, role, text, attachments=(), *, repo="", meta=None):
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO chat(role,text,attachments,repo,meta) VALUES (?,?,?,?,?)",
                (role, text, json.dumps(list(attachments), ensure_ascii=False), repo,
                 json.dumps(meta or {}, ensure_ascii=False)),
            )
        return cursor.lastrowid

    def thread(self, repo):
        row = self.db.execute(
            "SELECT repo, thread_id, tools_hash, context_used FROM threads WHERE repo=?",
            (repo,),
        ).fetchone()
        return dict(row) if row else None

    def thread_repo(self, thread_id):
        row = self.db.execute(
            "SELECT repo FROM threads WHERE thread_id=?",
            (thread_id,),
        ).fetchone()
        return row["repo"] if row else None

    def save_thread(self, repo, thread_id, tools_hash, context_used=None):
        with self.db:
            self.db.execute(
                """INSERT OR REPLACE INTO threads(repo, thread_id, tools_hash, context_used)
                   VALUES (?,?,?,?)""",
                (repo, thread_id, tools_hash, context_used),
            )

    def save_thread_context(self, repo, context_used):
        with self.db:
            self.db.execute(
                "UPDATE threads SET context_used=? WHERE repo=?",
                (context_used, repo),
            )

    def delete_thread(self, repo):
        with self.db:
            self.db.execute("DELETE FROM threads WHERE repo=?", (repo,))

    def repo_chat_latest(self):
        return {
            row["repo"]: row["latest"]
            for row in self.rows(
                "SELECT repo, MAX(id) AS latest FROM chat WHERE repo != '' GROUP BY repo"
            )
        }

    def chat_image_dir(self):
        return self.directory / "chat-images"

    def chat_image_path(self, name):
        if not isinstance(name, str) or not CHAT_IMAGE_NAME.fullmatch(name):
            raise ValueError("Image not found.")
        directory = self.chat_image_dir()
        path = (directory / name).resolve()
        if path.parent != directory.resolve():
            raise ValueError("Image not found.")
        return path

    def save_chat_image(self, extension, data):
        directory = self.chat_image_dir()
        directory.mkdir(parents=True, exist_ok=True)
        name = f"{uuid4().hex}.{extension}"
        path = directory / name
        try:
            with path.open("xb") as stream:
                stream.write(data)
            path.chmod(0o600)
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return name

    def rows(self, query, parameters=()):
        return [dict(row) for row in self.db.execute(query, parameters)]

    def task(self, issue):
        rows = self.rows("SELECT * FROM tasks WHERE issue=?", (issue,))
        if not rows:
            raise ValueError(f"Unknown task {issue}.")
        return rows[0]

    def record_execution(self, issue, kind, *, started=None, ended=None, detail="", data=None):
        started = time.time() if started is None else started
        with self.db:
            cursor = self.db.execute(
                """INSERT INTO execution(issue,kind,started,ended,detail,data)
                   VALUES (?,?,?,?,?,?)""",
                (issue, kind, started, ended, detail, json.dumps(data or {}, ensure_ascii=False)),
            )
        return cursor.lastrowid

    def open_execution(self, issue, kind, *, detail="", data=None, started=None):
        started = time.time() if started is None else started
        for row in self.rows("""SELECT id FROM execution WHERE issue=? AND kind=? AND ended IS NULL""",
                             (issue, kind)):
            self.close_execution(row["id"], ended=started,
                                 detail=f"Superseded by a new {kind} interval.")
        return self.record_execution(issue, kind, started=started, detail=detail, data=data)

    def close_execution(self, execution_id, *, ended=None, detail=None):
        fields = {"ended": time.time() if ended is None else ended}
        if detail is not None:
            fields["detail"] = detail
        assignments = ", ".join(f"{field}=?" for field in fields)
        with self.db:
            self.db.execute(f"UPDATE execution SET {assignments} WHERE id=? AND ended IS NULL",
                            (*fields.values(), execution_id))

    def close_execution_kind(self, issue, kind, *, ended=None, detail=None):
        for row in self.rows("""SELECT id FROM execution WHERE issue=? AND kind=? AND ended IS NULL""",
                             (issue, kind)):
            self.close_execution(row["id"], ended=ended, detail=detail)

    def close_open_execution(self, issue, *, ended=None, detail=None):
        for row in self.rows("SELECT id FROM execution WHERE issue=? AND ended IS NULL", (issue,)):
            self.close_execution(row["id"], ended=ended, detail=detail)

    def update_execution_data(self, execution_id, data, *, detail=None):
        fields = ["data=?"]
        values = [json.dumps(data, ensure_ascii=False)]
        if detail is not None:
            fields.append("detail=?")
            values.append(detail)
        values.append(execution_id)
        with self.db:
            self.db.execute(f"UPDATE execution SET {', '.join(fields)} WHERE id=?", values)

    def _issue_repo(self, issue):
        rows = self.rows("SELECT repo FROM tasks WHERE issue=?", (issue,))
        return rows[0]["repo"] if rows else ""

    def record_coordinator_turn(self, repo, model, *, started, ended, status):
        duration = max(0.0, float(ended) - float(started))
        with self.db:
            self.db.execute(
                """INSERT INTO coordinator_turns(repo, model, started, ended, duration, status)
                   VALUES (?,?,?,?,?,?)""",
                (repo or "", model or "unknown", float(started), float(ended), duration, status or "unknown"),
            )

    def record_token_usage(self, source, repo, model, *, input_tokens=None, output_tokens=None,
                           total_tokens=None, cost_usd=None, recorded=None):
        inp = _optional_int(input_tokens)
        out = _optional_int(output_tokens)
        total = _optional_int(total_tokens)
        cost = _optional_float(cost_usd)
        if inp is None and out is None and total is None and cost is None:
            return False
        with self.db:
            self.db.execute(
                """INSERT INTO token_usage(source, repo, model, input_tokens, output_tokens,
                                           total_tokens, cost_usd, recorded)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (source, repo or "", model or "unknown", inp, out, total, cost,
                 time.time() if recorded is None else float(recorded)),
            )
        return True

    def notes_snapshot(self, issue):
        path = self.artifacts(issue) / "notes.md"
        try:
            if path.is_symlink() or not path.is_file():
                return {"available": False, "text": "", "truncated": False,
                        "detail": "No notes.md was produced for this attempt."}
            with path.open("rb") as stream:
                raw = stream.read(NOTES_BYTES + 1)
        except OSError:
            return {"available": False, "text": "", "truncated": False,
                    "detail": "notes.md could not be read for this attempt."}
        if len(raw) > NOTES_BYTES:
            return {"available": False, "text": "", "truncated": False,
                    "detail": "notes.md exceeded the retained size limit and was not snapshotted."}
        if not raw.strip():
            return {"available": False, "text": "", "truncated": False,
                    "detail": "notes.md was empty when this attempt completed."}
        text = redact(raw.decode(errors="replace"))
        lines = text.splitlines()
        truncated = len(lines) > NOTES_LINES
        text = "\n".join(lines[:NOTES_LINES])
        text, bounded = bounded_snapshot(text, NOTES_BYTES)
        return {"available": bool(text.strip()), "text": text,
                "truncated": truncated or bounded,
                "detail": "" if text.strip() else "No notes.md content was retained for this attempt."}

    def mark_attempt_notes_ready(self, issue, attempt_id):
        if attempt_id is None:
            return
        rows = self.rows(
            "SELECT data FROM execution WHERE id=? AND issue=? AND kind='attempt'",
            (attempt_id, issue),
        )
        if not rows:
            return
        try:
            data = json.loads(rows[0]["data"] or "{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["notesReady"] = True
        self.update_execution_data(attempt_id, data)

    def finish_attempt(self, issue, attempt_id, *, detail=None, ended=None):
        if attempt_id is None:
            return
        rows = self.rows(
            "SELECT * FROM execution WHERE id=? AND issue=? AND kind='attempt'",
            (attempt_id, issue),
        )
        if not rows:
            return
        row = rows[0]
        try:
            data = json.loads(row["data"] or "{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if "notes" not in data:
            if data.get("notesReady") is True:
                data["notes"] = self.notes_snapshot(issue)
            else:
                data["notes"] = {
                    "available": False, "text": "", "truncated": False,
                    "detail": "This attempt did not initialize notes.md, so no notes snapshot was retained.",
                }
        self.update_execution_data(attempt_id, data)
        self.close_execution(attempt_id, ended=ended, detail=detail)

    def finish_open_attempt(self, issue, *, ended=None):
        rows = self.rows("""SELECT id FROM execution
                            WHERE issue=? AND kind='attempt' AND ended IS NULL
                            ORDER BY id DESC LIMIT 1""", (issue,))
        if rows:
            self.finish_attempt(issue, rows[0]["id"], ended=ended)

    def status(self):
        return self.rows(
            """SELECT issue, repo, title, phase, tier, escalated, trigger, branch,
                      attempts, session, check_exit, detail, revision
               FROM tasks WHERE phase != 'done' ORDER BY updated DESC"""
        )

    def stats(self, limit=30):
        rows = self.rows(
            """SELECT issue, phase, tier, escalated, trigger
               FROM tasks WHERE phase IN ('done', 'parked')
               ORDER BY updated DESC LIMIT ?""",
            (limit,),
        )
        if not rows:
            return "No completed or parked tasks recorded yet."
        flash_pass = sum(1 for r in rows if r["phase"] == "done" and not r["escalated"])
        agy_pass = sum(1 for r in rows if r["phase"] == "done" and r["escalated"])
        parked = sum(1 for r in rows if r["phase"] == "parked")
        triggers = {}
        for r in rows:
            if r["trigger"]:
                triggers[r["trigger"]] = triggers.get(r["trigger"], 0) + 1
        trigger_text = ", ".join(f"{k}: {v}" for k, v in sorted(triggers.items()))
        total = len(rows)
        lines = [
            f"Last {total} finished tasks: {flash_pass} passed on Flash ({int(100 * flash_pass / total)}%), "
            f"{agy_pass} auto-escalated & passed on Hydrogen ({int(100 * agy_pass / total)}%), "
            f"{parked} parked ({int(100 * parked / total)}%)."
        ]
        if trigger_text:
            lines.append(f"Escalation triggers: {trigger_text}.")
        return "\n".join(lines)

    def change(self, issue, event=None, *, supersede=False, notify=False, **fields):
        fields["updated"] = time.time()
        assignments = ", ".join(f"{field}=?" for field in fields)
        with self.db:
            self.db.execute(f"UPDATE tasks SET {assignments} WHERE issue=?", (*fields.values(), issue))
            if supersede:
                self.db.execute("UPDATE events SET delivered=1 WHERE issue=? AND delivered=0", (issue,))
            if event:
                row = self.db.execute(
                    "SELECT phase, repo FROM tasks WHERE issue=?", (issue,)
                ).fetchone()
                if notify:
                    phase = fields.get("phase")
                    if phase is None:
                        phase = row["phase"]
                    self.db.execute("INSERT INTO events(issue,detail,phase) VALUES (?,?,?)", (issue, event, phase))
                self.db.execute(
                    "INSERT INTO chat(role,text,repo) VALUES ('worker',?,?)",
                    (f"{issue} · {event}", row["repo"]),
                )
        self.wake.set()

    def acknowledge_events(self, issue):
        with self.db:
            self.db.execute("UPDATE events SET delivered=1 WHERE issue=? AND delivered=0", (issue,))

    def discard_stale_events(self, issue=None):
        query = """UPDATE events SET delivered=1
                   WHERE delivered=0
                     AND phase != (SELECT phase FROM tasks WHERE tasks.issue=events.issue)"""
        parameters = ()
        if issue is not None:
            query += " AND issue=?"
            parameters = (issue,)
        with self.db:
            self.db.execute(query, parameters)

    def job(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)

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

    def enqueue_adhoc(self, repo, prompt, title=""):
        clean_repo = (repo or "").strip().lower()
        clean_prompt = (prompt or "").strip()
        if not clean_repo:
            raise ValueError("Repository is required.")
        if not clean_prompt or len(clean_prompt) > 24000:
            raise ValueError("Task prompt must contain between 1 and 24,000 characters.")
        clean_title = (title or clean_prompt.splitlines()[0]).strip()[:120]
        with self.db:
            existing = [
                int(row["issue"].split("-", 1)[1])
                for row in self.db.execute("SELECT issue FROM tasks WHERE issue LIKE 'ADHOC-%'")
                if row["issue"].split("-", 1)[1].isdigit()
            ]
            task_id = f"ADHOC-{(max(existing) + 1) if existing else 1}"
            self.db.execute(
                """INSERT INTO tasks(issue, project, repo, title, plan, phase, priority, updated)
                   VALUES (?,?,?,?,?,'queued',2,?)""",
                (task_id, "", clean_repo, clean_title, clean_prompt, time.time()),
            )
            self.db.execute(
                "INSERT INTO chat(role,text,repo) VALUES ('worker',?,?)",
                (f"{task_id} · Queued for Flash worker: {clean_title}", clean_repo),
            )
        self.wake.set()
        return {"issue": task_id, "repo": clean_repo, "title": clean_title, "phase": "queued"}

    def sync_todo_issues(self, snapshot):
        """Auto-queue every Linear Todo issue directly—zero approval ceremony."""
        if not snapshot:
            return []
        live_todos = {}
        for project in snapshot:
            repo = project.get("repo") or ""
            project_id = project.get("id") or ""
            if not repo or not project_id:
                continue
            for issue in project.get("issues") or []:
                state = issue.get("state") or {}
                if state.get("name") == "Todo" and state.get("type") == "unstarted":
                    identifier = issue["identifier"]
                    title = (issue.get("title") or identifier).strip()
                    plan = (issue.get("plan") or "").strip()
                    if not plan:
                        plan = title
                    priority = issue.get("priority") or 4
                    live_todos[identifier] = (project_id, repo, title, plan, priority)
        queued_new = []
        now = time.time()
        with self.db:
            existing = {
                row["issue"]: row
                for row in self.db.execute("SELECT issue, project, phase FROM tasks")
            }
            for identifier, (project_id, repo, title, plan, priority) in live_todos.items():
                current = existing.get(identifier)
                if current is None:
                    self.db.execute(
                        """INSERT INTO tasks(issue, project, repo, title, plan, phase, priority, updated)
                           VALUES (?,?,?,?,?,'queued',?,?)""",
                        (identifier, project_id, repo, title, plan, priority, now),
                    )
                    queued_new.append(identifier)
                elif current["phase"] == "done":
                    # Reopened in Linear -> re-queue cleanly.
                    self.db.execute(
                        """UPDATE tasks
                           SET project=?, repo=?, title=?, plan=?, phase='queued',
                               tier='', escalated=0, trigger='', branch='', detail=NULL,
                               priority=?, updated=?
                           WHERE issue=?""",
                        (project_id, repo, title, plan, priority, now, identifier),
                    )
                    queued_new.append(identifier)
                elif current["phase"] == "queued":
                    self.db.execute(
                        "UPDATE tasks SET project=?, repo=?, title=?, priority=? WHERE issue=?",
                        (project_id, repo, title, priority, identifier),
                    )
            # Remove queued Linear issues that moved out of Todo before starting.
            for identifier, row in existing.items():
                if row["phase"] == "queued" and row["project"] and identifier not in live_todos:
                    self.db.execute("DELETE FROM tasks WHERE issue=?", (identifier,))
        if queued_new:
            self.wake.set()
        return queued_new

    def retry(self, issue):
        task_id = (issue or "").strip().upper()
        if not TASK_ID_RE.fullmatch(task_id):
            raise ValueError("Enter a valid task ID (e.g. KIAN-123 or ADHOC-1).")
        row = self.task(task_id)
        if row["phase"] == "finish_failed":
            self.change(task_id, phase="finishing", detail=None)
            self.job(self.finish(task_id))
            return {"issue": task_id, "phase": "finishing"}
        if row["phase"] not in {"parked", "blocked"}:
            raise ValueError(f"{task_id} is {row['phase']}, not parked.")
        self.change(
            task_id,
            event="Re-queued for Flash worker.",
            phase="queued",
            tier="",
            escalated=0,
            trigger="",
            branch="",
            detail=None,
            check_exit=None,
            supersede=True,
        )
        return {"issue": task_id, "phase": "queued"}

    def _repo_busy(self, repo):
        placeholders = ", ".join("?" for _ in ACTIVE_LOCK_PHASES)
        return bool(self.rows(
            f"SELECT 1 FROM tasks WHERE repo=? AND phase IN ({placeholders}) LIMIT 1",
            (repo, *ACTIVE_LOCK_PHASES),
        ))

    def _workers_running(self):
        return len(self.rows("SELECT 1 FROM tasks WHERE phase='working'"))

    async def dispatch_queued(self):
        if self.setting("paused") or self.draining or not self.has_opencode_go_key():
            return []
        candidates = self.rows(
            "SELECT * FROM tasks WHERE phase='queued' ORDER BY priority ASC, updated ASC, issue ASC"
        )
        dispatched = []
        for row in candidates:
            issue = row["issue"]
            repo = row["repo"]
            if self._workers_running() >= WORKER_LIMIT:
                break
            if self._repo_busy(repo):
                continue
            full_plan = row["plan"]
            if is_linear_task(row):
                try:
                    live = await self.linear.issue(issue)
                except (OSError, RuntimeError, TimeoutError, ValueError) as error:
                    self.change(issue, detail=f"Could not read Linear: {error}")
                    continue
                live_state = live.get("state") or {}
                if ((live.get("project") or {}).get("id") != row["project"]
                        or live_state.get("name") != "Todo"
                        or live_state.get("type") != "unstarted"):
                    # No longer Todo -> drop from queue cleanly.
                    with self.db:
                        self.db.execute("DELETE FROM tasks WHERE issue=? AND phase='queued'", (issue,))
                    continue
                desc = (live.get("description") or "").strip()
                plan_section = proposed_plan(desc)
                full_plan = plan_section or (f"{live.get('title') or row['title']}\n\n{desc}".strip())
                try:
                    await self.linear.set_state(issue, "In Progress", "started")
                    self.request_issue_refresh()
                except (OSError, RuntimeError, TimeoutError, ValueError) as error:
                    self.change(issue, detail=f"Could not move {issue} to In Progress: {error}")
                    continue
            async with self.config_lock:
                if self._workers_running() >= WORKER_LIMIT or self._repo_busy(repo):
                    continue
                current = self.task(issue)
                if current["phase"] != "queued":
                    continue
                attempt = current["attempts"] + 1
                self.change(
                    issue,
                    event="Started Tier-1 worker (deepseek-v4.1-flash).",
                    phase="working",
                    tier="flash",
                    escalated=0,
                    trigger="",
                    branch="",
                    plan=full_plan,
                    attempts=attempt,
                    check_exit=None,
                    tree=None,
                    detail=None,
                    supersede=True,
                )
            self.job(self.run_worker(issue))
            dispatched.append(issue)
        return dispatched

    async def _run_optional_checks(self, issue, worktree, artifacts, env, log, attempt_id=None):
        checks_path = artifacts / "checks.sh"
        if not checks_path.exists():
            return 0
        checks = read_small(checks_path, 16000).strip()
        if not checks:
            return 0
        log.write(f"\n--- verification ---\n{checks}\n".encode())
        log.flush()
        data = {"command": checks[:4000]}
        if attempt_id is not None:
            data["attemptId"] = attempt_id
        self.open_execution(issue, "verification", detail="Verification started.", data=data)
        process = await asyncio.create_subprocess_exec(
            "bash", "-e", "-o", "pipefail", "-c", checks, cwd=worktree, env=env,
            stdout=log, stderr=log, start_new_session=True,
        )
        self.change(issue, pid=process.pid)
        try:
            status = await process.wait()
        finally:
            await stop_process(process)
        self.close_execution_kind(issue, "verification", detail=f"Verification exited {status}.")
        return status

    async def _failure_receipt(self, worktree, base, log_path, trigger, reason):
        diff_stat = ""
        with suppress(RuntimeError, OSError):
            await git(worktree, "add", "-A")
            diff_stat = (await git(worktree, "diff", "--cached", "--stat", base)).strip()
        log_tail = ""
        if log_path.is_file():
            with suppress(OSError):
                raw = log_path.read_bytes()[-6000:]
                log_tail = redact(raw.decode("utf-8", errors="replace")).strip()[-3500:]
        parts = [f"Trigger: {trigger}", f"Reason: {reason}"]
        if diff_stat:
            parts.append(f"Files touched by Flash:\n{diff_stat}")
        if log_tail:
            parts.append(f"Tail of worker.log:\n{log_tail}")
        return "\n\n".join(parts)

    async def _reset_worktree_clean(self, repo_path, worktree, issue):
        await git(repo_path, "fetch", "origin", "refs/heads/main:refs/remotes/origin/main")
        base = await git(repo_path, "rev-parse", "origin/main")
        if not worktree.exists():
            worktree.parent.mkdir(parents=True, exist_ok=True)
            branch = f"symphony/{issue}"
            if await git(repo_path, "branch", "--list", branch):
                await git(repo_path, "branch", "-D", branch)
            await git(repo_path, "worktree", "add", "-b", branch, str(worktree), base)
        else:
            await git(worktree, "reset", "--hard", base)
            await git(worktree, "clean", "-fdx")
        return base

    async def park(self, issue, detail, *, trigger=None):
        """Push any WIP diff to a parked branch, mark parked, and release the repo queue."""
        detail = (detail or "Stopped.").strip()
        row = self.task(issue)
        worktree = self.worktree(issue)
        branch = ""
        pushed_branch = False
        if worktree.is_dir() and row.get("base") and not row.get("revision"):
            with suppress(RuntimeError, OSError, TimeoutError):
                await git(worktree, "add", "-A")
                tree = await git(worktree, "write-tree")
                base_tree = await git(worktree, "rev-parse", f"{row['base']}^{{tree}}")
                if tree != base_tree:
                    wip_rev = await git(
                        worktree,
                        "-c", "user.name=Symphony",
                        "-c", "user.email=symphony@users.noreply.github.com",
                        "commit-tree", tree, "-p", row["base"],
                        "-m", f"WIP {issue} {row['title']}",
                    )
                    branch = f"symphony/parked-{issue}"
                    await git(worktree, "push", "-f", "origin", f"{wip_rev}:refs/heads/{branch}")
                    pushed_branch = True
        if pushed_branch and worktree.exists():
            with suppress(RuntimeError, OSError):
                repo = self.repo_dir(row["repo"])
                await git(repo, "worktree", "remove", "--force", str(worktree))
                local_branch = f"symphony/{issue}"
                if await git(repo, "branch", "--list", local_branch):
                    await git(repo, "branch", "-D", local_branch)
        self.finish_open_attempt(issue)
        self.close_open_execution(issue)
        self.record_execution(issue, "failure", started=time.time(), ended=time.time(), detail=detail)
        suffix = f" WIP pushed to branch {branch}." if pushed_branch else ""
        fields = {"phase": "parked", "pid": None, "detail": detail, "branch": branch}
        if trigger is not None:
            fields["trigger"] = trigger
        self.change(issue, event=f"Parked. {detail}{suffix}", supersede=True, **fields)

    async def run_worker(self, issue):
        row = self.task(issue)
        worktree, artifacts = self.worktree(issue), self.artifacts(issue)
        log_path = artifacts / "worker.log"
        attempt_id = self.record_execution(
            issue, "attempt", detail="Tier-1 Flash attempt started.",
            data={"attempt": row["attempts"], "tier": "flash", "notesReady": False},
        )
        self.open_execution(issue, "startup", detail="Setting up worktree.",
                            data={"attempt": row["attempts"], "attemptId": attempt_id})
        try:
            artifacts.mkdir(parents=True, exist_ok=True)
            for name in ("checks.sh", "blocked.txt", "notes.md"):
                (artifacts / name).unlink(missing_ok=True)
            self.mark_attempt_notes_ready(issue, attempt_id)
            repo_path = await self.checkout(row["repo"])
            base = await self._reset_worktree_clean(repo_path, worktree, issue)
            self.change(issue, base=base, revision=None, workflows=None)
        except (RuntimeError, OSError, ValueError, TimeoutError) as error:
            await self.park(issue, str(error))
            return

        # --- Tier 1: DeepSeek-V4.1-Flash (OpenCode Go, 15m cap) ---
        env = self.opencode_auth_env()
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(WORKER_CONFIG)
        flash_model = os.environ.get("SYMPHONY_WORKER_MODEL", WORKER_MODEL)
        args = ["opencode", "run", "--standalone", "--auto", "--format", "json", "--model", flash_model]
        self.close_execution_kind(issue, "startup", detail="Flash worker started.")
        self.open_execution(issue, "worker", detail="Tier-1 Flash worker started.",
                            data={"attempt": row["attempts"], "attemptId": attempt_id, "tier": "flash"})
        trigger = ""
        reason = ""
        provider_paused = False
        try:
            with log_path.open("ab") as log:
                log.write(f"\n--- tier 1 flash (attempt {row['attempts']}) ---\n".encode())
                async with asyncio.timeout(FLASH_TIMEOUT_SECONDS):
                    await self.run_opencode(issue, args + [worker_prompt(row, artifacts)], worktree, env, log)
                    self.close_execution_kind(issue, "worker", detail="Tier-1 Flash worker exited.")
                    if (artifacts / "blocked.txt").exists():
                        trigger = "blocked"
                        reason = read_small(artifacts / "blocked.txt", 4000).strip() or "Worker reported a blocker."
                    else:
                        status = await self._run_optional_checks(issue, worktree, artifacts, env, log, attempt_id)
                        self.change(issue, check_exit=status)
                        if status != 0:
                            trigger = "check_failed"
                            reason = f"Verification exited {status}."
        except TimeoutError:
            self.close_execution_kind(issue, "worker", detail="Tier-1 Flash timed out.")
            trigger = "timeout"
            reason = f"Flash worker exceeded its {FLASH_TIMEOUT_SECONDS // 60}-minute timeout."
        except (RuntimeError, OSError, ValueError) as error:
            self.close_execution_kind(issue, "worker", detail=str(error))
            provider_paused = bool(self.setting("paused"))
            trigger = "worker_error"
            reason = str(error)

        self.finish_attempt(
            issue, attempt_id,
            detail=("Tier-1 Flash passed." if not trigger else f"Tier-1 Flash failed ({trigger}): {reason}"),
        )

        if not trigger:
            await self.publish(issue)
            return

        if provider_paused:
            await self.park(issue, f"Paused on OpenCode Go provider error: {reason}", trigger=trigger)
            return

        # --- Tier 2: Auto-Escalation to Antigravity (gemini-4-hydrogen-xhigh, 20m cap) ---
        if self.escalator is None:
            await self.park(issue, f"Flash failed ({trigger}): {reason}", trigger=trigger)
            return

        allowed, quota_reason = self.escalator.can_auto_escalate()
        if not allowed:
            await self.park(
                issue,
                f"Flash failed ({trigger}: {reason}). Auto-escalation skipped: {quota_reason}",
                trigger=trigger,
            )
            return

        receipt = await self._failure_receipt(worktree, base, log_path, trigger, reason)
        esc_attempt = self.task(issue)["attempts"] + 1
        esc_attempt_id = self.record_execution(
            issue, "attempt", detail=f"Tier-2 Antigravity escalation started ({trigger}).",
            data={"attempt": esc_attempt, "tier": "agy", "trigger": trigger, "notesReady": False},
        )
        self.change(
            issue,
            event=f"Flash failed ({trigger}). Auto-escalating to {ESCALATION_MODEL}.",
            tier="agy",
            escalated=1,
            trigger=trigger,
            attempts=esc_attempt,
            check_exit=None,
        )
        try:
            for name in ("checks.sh", "blocked.txt", "notes.md"):
                (artifacts / name).unlink(missing_ok=True)
            self.mark_attempt_notes_ready(issue, esc_attempt_id)
            repo_path = await self.checkout(row["repo"])
            base = await self._reset_worktree_clean(repo_path, worktree, issue)
            self.change(issue, base=base)
            self.open_execution(issue, "worker", detail=f"Tier-2 escalation ({ESCALATION_MODEL}) started.",
                                data={"attempt": esc_attempt, "attemptId": esc_attempt_id, "tier": "agy"})
            with log_path.open("ab") as log:
                log.write(f"\n--- tier 2 escalation ({ESCALATION_MODEL}) ---\n".encode())
                await self.escalator.run_escalation_worker(
                    issue, row["repo"],
                    escalation_prompt(self.task(issue), artifacts, receipt),
                    worktree, log, timeout=ESCALATION_TIMEOUT_SECONDS,
                )
                self.close_execution_kind(issue, "worker", detail="Tier-2 escalation exited.")
                if (artifacts / "blocked.txt").exists():
                    raise RuntimeError(read_small(artifacts / "blocked.txt", 4000).strip() or "Escalation reported a blocker.")
                status = await self._run_optional_checks(issue, worktree, artifacts, env, log, esc_attempt_id)
                self.change(issue, check_exit=status)
                if status != 0:
                    raise RuntimeError(f"Verification exited {status} after escalation.")
        except TimeoutError:
            self.close_execution_kind(issue, "worker", detail="Tier-2 escalation timed out.")
            self.finish_attempt(issue, esc_attempt_id, detail="Tier-2 escalation timed out.")
            await self.park(
                issue,
                f"Escalation ({ESCALATION_MODEL}) exceeded its {ESCALATION_TIMEOUT_SECONDS // 60}-minute timeout.",
                trigger=trigger,
            )
            return
        except (RuntimeError, OSError, ValueError) as error:
            self.close_execution_kind(issue, "worker", detail=str(error))
            self.finish_attempt(issue, esc_attempt_id, detail=str(error))
            await self.park(issue, f"Escalation failed: {error}", trigger=trigger)
            return

        self.finish_attempt(issue, esc_attempt_id, detail="Tier-2 escalation passed.")
        await self.publish(issue)

    async def run_opencode(self, issue, args, cwd, env, log):
        repo = self._issue_repo(issue)
        model = _cli_model(args, os.environ.get("SYMPHONY_WORKER_MODEL", WORKER_MODEL))
        process = await asyncio.create_subprocess_exec(
            *args, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, start_new_session=True, limit=4 * 1024 * 1024,
        )
        self.change(issue, pid=process.pid)
        error = ""
        error_status = None
        try:
            async for line in process.stdout:
                log.write(line)
                if not line.startswith(b"{"):
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                session = event.get("sessionID")
                if isinstance(session, str) and SESSION_ID.fullmatch(session):
                    if session != self.task(issue)["session"]:
                        self.change(issue, session=session)
                if event.get("type") == "error":
                    payload = event.get("error")
                    if not isinstance(payload, dict):
                        payload = {"message": payload}
                    error = str(payload.get("message") or payload.get("type") or "OpenCode error")
                    error_status = payload.get("status")
                elif event.get("type") == "step_finish":
                    inp, out, total, cost = _opencode_step_usage(event.get("part"))
                    self.record_token_usage(
                        "worker", repo, model,
                        input_tokens=inp, output_tokens=out, total_tokens=total, cost_usd=cost,
                    )
            status = await process.wait()
        finally:
            await stop_process(process)
        if status or error:
            if error_status in (401, 429) or re.search(
                "quota|rate.limit|subscription|unauthorized|invalid.api.key", error, re.I
            ):
                self.save_setting("paused", f"OpenCode Go: {error[:1000]}")
            raise RuntimeError((error[:1000] or f"OpenCode exited {status}.") + " See worker.log.")

    async def publish(self, issue):
        row = self.task(issue)
        self.change(issue, phase="publishing", pid=None, supersede=True)
        publication = self.open_execution(issue, "publication", detail="Publication started.")
        try:
            cwd = self.worktree(issue)
            artifacts = self.artifacts(issue)
            base = row["base"]
            await git(cwd, "add", "-A")
            tree = await git(cwd, "write-tree")
            if tree == await git(cwd, "rev-parse", f"{base}^{{tree}}"):
                self.close_execution(publication, detail="No repository changes to publish.")
                self.change(issue, phase="finishing")
                return await self.finish(issue)

            await git(cwd, "fetch", "origin", "refs/heads/main:refs/remotes/origin/main")
            remote_main = await git(cwd, "rev-parse", "origin/main")
            if remote_main != base:
                # Auto-rebase cleanly onto updated origin/main.
                await git(
                    cwd,
                    "-c", "user.name=Symphony",
                    "-c", "user.email=symphony@users.noreply.github.com",
                    "commit", "-m", f"WIP {issue}",
                )
                try:
                    await git(cwd, "rebase", remote_main)
                except RuntimeError:
                    with suppress(RuntimeError):
                        await git(cwd, "rebase", "--abort")
                    with suppress(RuntimeError):
                        await git(cwd, "reset", "--mixed", "HEAD~1")
                    raise RuntimeError("Auto-rebase onto origin/main hit a merge conflict.") from None
                base = remote_main
                self.change(issue, base=base)
                with (artifacts / "worker.log").open("ab") as log:
                    status = await self._run_optional_checks(issue, cwd, artifacts, self.opencode_auth_env(), log)
                if status != 0:
                    raise RuntimeError(f"Checks exited {status} after auto-rebasing onto origin/main.")
                await git(cwd, "add", "-A")
                tree = await git(cwd, "write-tree")

            revision = await git(
                cwd, "-c", "user.name=Symphony", "-c", "user.email=symphony@users.noreply.github.com",
                "commit-tree", tree, "-p", base, "-m", f"{issue} {row['title']}",
            )
            workflows = await ci_workflows(cwd, base, revision)
            self.change(issue, revision=revision, tree=tree, workflows=json.dumps(workflows))
            try:
                await git(cwd, "push", "origin", f"{revision}:refs/heads/main")
            except (RuntimeError, TimeoutError) as push_error:
                try:
                    await git(cwd, "fetch", "origin", "refs/heads/main:refs/remotes/origin/main")
                    await git(cwd, "merge-base", "--is-ancestor", revision, "origin/main")
                except (RuntimeError, TimeoutError):
                    raise RuntimeError("Git push failed. " + (str(push_error) or "Timed out.")) from None
            self.close_execution(publication, detail=f"Pushed {revision} to main.")
            await self.after_push(issue)
        except (RuntimeError, OSError, ValueError, TimeoutError) as error:
            await self.park(issue, str(error) or "GitHub operation timed out.")

    async def after_push(self, issue):
        workflows = json.loads(self.task(issue)["workflows"] or "[]")
        if workflows:
            self.change(issue, phase="ci", event="Pushed to main. Waiting for GitHub Actions.",
                        supersede=True, notify=False)
            self.job(self.watch_ci(issue))
        else:
            self.change(issue, phase="finishing")
            await self.finish(issue)

    async def watch_ci(self, issue):
        row = self.task(issue)
        expected = set(json.loads(row["workflows"] or "[]"))
        self.open_execution(issue, "ci_wait", detail="Waiting for GitHub Actions.")
        self.open_execution(issue, "ci_discovery", detail="Waiting for push CI to appear.")
        discovered = False
        while True:
            try:
                runs = await ci_runs(row["repo"], row["revision"])
                try:
                    await self.observe_ci(issue, row, runs)
                except (OSError, RuntimeError, ValueError, TimeoutError) as error:
                    self.record_execution(issue, "ci_error", started=time.time(), ended=time.time(),
                                          detail=f"CI details are unavailable: {error}")
                by_path = {run["path"].split("@", 1)[0]: run for run in reversed(runs)}
                if not discovered and expected & by_path.keys():
                    self.close_execution_kind(issue, "ci_discovery", detail="GitHub Actions appeared.")
                    discovered = True
                for path in expected & by_path.keys():
                    current = by_path[path]
                    if current["status"] == "completed" and current["conclusion"] != "success":
                        raise ValueError(f"{path} ended with {current['conclusion']}. {current['html_url']}")
                if all(by_path.get(path, {}).get("conclusion") == "success" for path in expected):
                    self.close_execution_kind(issue, "ci_wait", detail="GitHub Actions succeeded.")
                    self.close_execution_kind(issue, "ci_discovery", detail="GitHub Actions succeeded.")
                    self.change(issue, phase="finishing")
                    return await self.finish(issue)
                if expected - by_path.keys() and time.time() - row["updated"] > CI_DISCOVERY_SECONDS:
                    missing = ", ".join(sorted(expected - by_path.keys()))
                    raise ValueError(f"No push CI started for {row['revision']}: {missing}")
            except (RuntimeError, ValueError) as error:
                await self.park(issue, str(error))
                return
            await asyncio.sleep(20)

    async def observe_ci(self, issue, row, runs):
        expected = set(json.loads(row["workflows"] or "[]"))
        for run in runs:
            path = (run.get("path") or "").split("@", 1)[0]
            run_id = run.get("id")
            if path not in expected or not run_id:
                continue
            attempt = int(run.get("run_attempt") or 1)
            conclusion = run.get("conclusion")
            diagnostic = ("" if conclusion in (None, "success")
                          else f"{run.get('name') or path} ended with {conclusion}.")
            with self.db:
                self.db.execute(
                    """INSERT INTO ci_runs(run_id,attempt,issue,repo,revision,workflow,path,
                                           status,conclusion,url,created_at,run_started_at,
                                           updated_at,completed_at,diagnostic,observed)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(run_id,attempt) DO UPDATE SET
                           status=excluded.status, conclusion=excluded.conclusion,
                           url=excluded.url, created_at=excluded.created_at,
                           run_started_at=excluded.run_started_at,
                           updated_at=excluded.updated_at, completed_at=excluded.completed_at,
                           diagnostic=CASE WHEN excluded.diagnostic='' THEN ci_runs.diagnostic
                                           ELSE excluded.diagnostic END,
                           observed=excluded.observed""",
                    (run_id, attempt, issue, row["repo"], row["revision"],
                     run.get("name") or path, path, run.get("status") or "", conclusion,
                     run.get("html_url") or "", run.get("created_at"),
                     run.get("run_started_at"), run.get("updated_at"),
                     run.get("completed_at"), diagnostic, time.time()),
                )
            if run.get("status") != "completed":
                continue
            current = self.rows("SELECT jobs_fetched FROM ci_runs WHERE run_id=? AND attempt=?",
                                (run_id, attempt))[0]
            if current["jobs_fetched"]:
                continue
            try:
                jobs = await ci_jobs(row["repo"], run_id, attempt)
            except (OSError, RuntimeError, ValueError, TimeoutError) as error:
                with self.db:
                    self.db.execute("UPDATE ci_runs SET diagnostic=? WHERE run_id=? AND attempt=?",
                                    (f"GitHub job details are unavailable: {error}", run_id, attempt))
                continue
            details = []
            with self.db:
                self.db.execute("DELETE FROM ci_jobs WHERE run_id=? AND attempt=?", (run_id, attempt))
                for job in jobs:
                    job_conclusion = job.get("conclusion")
                    failed_steps = [
                        step.get("name") for step in job.get("steps") or []
                        if step.get("conclusion") in {"failure", "timed_out", "cancelled", "action_required"}
                    ]
                    job_diagnostic = ""
                    if job_conclusion not in (None, "success"):
                        job_diagnostic = f"{job.get('name') or 'Job'} ended with {job_conclusion}."
                        if failed_steps:
                            job_diagnostic += " Failed steps: " + ", ".join(failed_steps[:5]) + "."
                        details.append(job_diagnostic)
                    self.db.execute(
                        """INSERT OR REPLACE INTO ci_jobs(run_id,attempt,job_id,issue,name,status,
                                                          conclusion,started_at,completed_at,url,
                                                          diagnostic,observed)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (run_id, attempt, job.get("id") or 0, issue,
                         job.get("name") or "Job", job.get("status") or "",
                         job_conclusion, job.get("started_at"), job.get("completed_at"),
                         job.get("html_url") or "", job_diagnostic, time.time()),
                    )
                joined = " ".join(details[:8])
                combined = " ".join(part for part in (diagnostic, joined) if part)
                self.db.execute(
                    """UPDATE ci_runs SET jobs_fetched=1, diagnostic=? WHERE run_id=? AND attempt=?""",
                    (combined, run_id, attempt),
                )

    async def cleanup(self, issue):
        row = self.task(issue)
        self.discard_stale_events(issue)
        repo = self.repo_dir(row["repo"])
        path = self.worktree(issue)
        if path.exists():
            await git(repo, "worktree", "remove", "--force", str(path))
        branch = f"symphony/{issue}"
        if await git(repo, "branch", "--list", branch):
            await git(repo, "branch", "-D", branch)

    async def finish(self, issue):
        row = self.task(issue)
        if is_linear_task(row):
            try:
                await self.linear.set_state(issue, "Done", "completed")
                self.request_issue_refresh()
            except (RuntimeError, ValueError) as error:
                detail = f"Publication verified. Cannot mark Linear Done: {error}"
                self.close_open_execution(issue)
                self.record_execution(issue, "failure", started=time.time(), ended=time.time(), detail=detail)
                self.change(issue, phase="finish_failed", detail=detail, event=detail, supersede=True)
                return
        tier_label = "Hydrogen" if row.get("escalated") else "Flash"
        detail = f"Done ({tier_label})."
        try:
            await self.cleanup(issue)
        except (RuntimeError, OSError, TimeoutError) as error:
            detail = f"Done ({tier_label}). Worktree cleanup needs attention: {error}"
        self.close_open_execution(issue)
        if not self.rows("SELECT 1 FROM execution WHERE issue=? AND kind='completion'", (issue,)):
            self.record_execution(issue, "completion", started=time.time(), ended=time.time(), detail=detail)
        self.change(issue, phase="done", detail=detail, event=detail, supersede=True, notify=False)

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
            await self.park(issue, f"Publication interrupted. Verify origin/main: {error}")

    def recover(self):
        now = time.time()
        for row in self.status():
            issue = row["issue"]
            opened = self.rows("SELECT started FROM execution WHERE issue=? AND ended IS NULL", (issue,))
            if opened:
                observed = [self.task(issue)["updated"], *(item["started"] for item in opened)]
                for query in (
                    "SELECT MAX(observed) AS value FROM ci_runs WHERE issue=?",
                    "SELECT MAX(observed) AS value FROM ci_jobs WHERE issue=?",
                    "SELECT MAX(COALESCE(ended, started)) AS value FROM execution WHERE issue=?",
                ):
                    value = self.rows(query, (issue,))[0]["value"]
                    if value is not None:
                        observed.append(value)
                last_observed = min(now, max(observed))
                self.finish_open_attempt(issue, ended=last_observed)
                self.close_open_execution(issue, ended=last_observed)
                if now > last_observed:
                    self.record_execution(
                        issue, "gap", started=last_observed, ended=now,
                        detail="Unobserved outage after the last recorded activity; duration is unknown.",
                        data={"unknown": True},
                    )
                self.record_execution(
                    issue, "interruption", started=now, ended=now,
                    detail="Recovered after a restart. Open intervals ended at the last recorded activity.",
                )
            if row["phase"] == "working":
                self.change(
                    issue,
                    event="Pod restarted mid-run. Parked; use /retry to re-queue.",
                    phase="parked",
                    pid=None,
                    detail="Pod restarted while worker was running.",
                    supersede=True,
                )
            elif row["phase"] == "publishing":
                self.job(self.recover_publish(issue))
            elif row["phase"] == "ci":
                self.job(self.watch_ci(issue))
            elif row["phase"] == "finishing":
                self.job(self.finish(issue))
