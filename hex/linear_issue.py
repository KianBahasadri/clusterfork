"""Send Hex requests to the Linear assistant for execution through Linear's MCP tools."""

import argparse
import base64
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
from tempfile import NamedTemporaryFile, TemporaryDirectory, mkdtemp
import time
from typing import Literal
import urllib.error
from urllib.parse import urlsplit
import urllib.request
from uuid import uuid4

from linear_images import EXTENSIONS

WORKSPACE = Path(__file__).resolve().parent
DOTENV = Path.home() / ".config/clusterfork/.env"
ISSUE_METADATA_POLICY = """Every Symphony Linear issue, including agent-created and follow-up issues,
must have an explicit priority (1 Urgent, 2 High, 3 Normal, or 4 Low) and an
estimate from its team's configured estimate scale. No priority (0) and a
missing estimate are incomplete. Choose values from the issue's urgency, scope,
complexity and verification work; preserve priorities and estimates already
supplied by the user. An explicit zero estimate is valid only when the team
allows it and the scope justifies it. Fill missing fields autonomously before
implementation; missing metadata alone never requires human review.
"""

MODEL = "opencode-go/deepseek-v4.1-flash#max"
AGENT = "linear-issue"
PREVIEW_AGENT = "linear-issue-preview"
MCP_SERVER = "linear"
MCP_STARTUP_SECONDS = 45
MCP_POLL_SECONDS = 0.25
SERVER_STOP_SECONDS = 5
HISTORY_LIMIT = 50
RECENT_CONTEXT_COUNT = 5
RECENT_CONTEXT_AGE_SECONDS = 3600
RECENT_CONTEXT_MAX_CHARS = 1500
PROJECTS_QUERY = """query($after: String) {
    projects(first: 50, after: $after) {
        nodes { id name archivedAt status { name type } }
        pageInfo { endCursor hasNextPage }
    }
}"""


class FilingError(ValueError):
    """An error message safe to show in a local notification."""


class ReplyNeeded(FilingError):
    pass


@dataclass(frozen=True)
class Dictation:
    transcript: str
    title: str | None
    issue_id: str
    answers: tuple[tuple[str, str], ...] = ()
    image_use: Literal["attach", "reference"] | None = None
    images: tuple[tuple[str, bytes], ...] = ()
    session_id: str | None = None
    suggested_project: str | None = None


@dataclass(frozen=True)
class AgentResult:
    text: str
    session_id: str | None
    notifications: tuple[dict, ...]
    reply: ReplyNeeded | None = None
    error: str | None = None


@dataclass(frozen=True)
class OpenCodeServer:
    url: str
    password: str
    process: subprocess.Popen


def state_dir():
    base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return base / "hex" / "linear"


def _write_private_json(path, payload):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", prefix=f".{path.name}-",
                             dir=path.parent, delete=False) as temp:
        temp_path = Path(temp.name)
        try:
            os.chmod(temp_path, 0o600)
            json.dump(payload, temp, ensure_ascii=False, indent=2)
            temp.write("\n")
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
    temp_path.replace(path)


def _normalize_projects(items):
    cleaned = []
    seen = set()
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict):
            continue
        pid, name = item.get("id"), item.get("name")
        if not isinstance(pid, str) or not pid or not isinstance(name, str) or not name.strip():
            continue
        if pid in seen:
            continue
        seen.add(pid)
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        cleaned.append({
            "id": pid,
            "name": name.strip(),
            "status": str(status.get("name") or item.get("status") or ""),
            "type": str(status.get("type") or item.get("type") or ""),
        })
    return cleaned


def load_cached_projects():
    path = state_dir() / "projects_cache.json"
    try:
        return _normalize_projects(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def fetch_linear_projects(timeout=5):
    try:
        token = linear_api_key()
    except ValueError:
        return load_cached_projects()
    if not token:
        return load_cached_projects()
    projects = []
    cursor = None
    try:
        while True:
            request = urllib.request.Request(
                "https://api.linear.app/graphql",
                headers={"Authorization": token, "Content-Type": "application/json"},
                data=json.dumps({"query": PROJECTS_QUERY, "variables": {"after": cursor}}).encode("utf-8"),
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            if payload.get("errors"):
                return load_cached_projects()
            connection = ((payload.get("data") or {}).get("projects") or {})
            for node in connection.get("nodes") or ():
                if not isinstance(node, dict) or node.get("archivedAt"):
                    continue
                status = node.get("status") if isinstance(node.get("status"), dict) else {}
                stype = str(status.get("type") or "").lower()
                if stype in {"completed", "canceled"}:
                    continue
                projects.append({
                    "id": str(node.get("id") or ""),
                    "name": str(node.get("name") or "").strip(),
                    "status": str(status.get("name") or ""),
                    "type": stype,
                })
            page = connection.get("pageInfo") or {}
            if not page.get("hasNextPage") or not page.get("endCursor"):
                break
            cursor = page["endCursor"]
    except (OSError, ValueError, urllib.error.URLError):
        return load_cached_projects()
    normalized = _normalize_projects(projects)
    normalized.sort(key=lambda item: (0 if item["type"] == "started" else 1, item["name"].casefold()))
    try:
        _write_private_json(state_dir() / "projects_cache.json", normalized)
    except OSError:
        pass
    return normalized


def load_history(limit=HISTORY_LIMIT):
    path = state_dir() / "history.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [entry for entry in data[:limit] if isinstance(entry, dict)]


def record_history(dictation, notifications):
    root = state_dir()
    images_dir = root / "history-images"
    saved_screenshots = []
    if dictation.images:
        try:
            images_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            for number, (mime, image) in enumerate(dictation.images, 1):
                ext = EXTENSIONS.get(mime)
                if not ext:
                    continue
                target = images_dir / f"{dictation.issue_id}-{number}.{ext}"
                if not target.exists():
                    with private_file(target) as stream:
                        stream.write(image)
                saved_screenshots.append(str(target))
        except OSError as error:
            print(f"Hex Linear dictation {dictation.issue_id} could not save history screenshot "
                  f"({type(error).__name__}).", file=sys.stderr)
    issues = [
        {"title": str(item.get("title") or ""), "message": str(item.get("message") or ""), "url": str(item.get("url") or "")}
        for item in notifications if isinstance(item, dict)
    ]
    entry = {
        "id": dictation.issue_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "transcript": dictation.transcript,
        "suggested_project": dictation.suggested_project,
        "issues": issues,
        "screenshots": saved_screenshots,
    }
    existing = [item for item in load_history(HISTORY_LIMIT) if item.get("id") != dictation.issue_id]
    updated = [entry, *existing]
    kept, dropped = updated[:HISTORY_LIMIT], updated[HISTORY_LIMIT:]
    retained_paths = {path for item in kept for path in (item.get("screenshots") or ())}
    for item in dropped:
        for path_str in item.get("screenshots") or ():
            if path_str in retained_paths:
                continue
            try:
                candidate = Path(path_str).resolve()
                if candidate.is_relative_to(images_dir.resolve()):
                    candidate.unlink(missing_ok=True)
            except OSError:
                pass
    try:
        _write_private_json(root / "history.json", kept)
    except OSError as error:
        print(f"Hex Linear dictation {dictation.issue_id} could not write history ({type(error).__name__}).",
              file=sys.stderr)


def recent_context(exclude_id="", now=None):
    current = now or datetime.now(timezone.utc)
    selected = []
    for entry in load_history(HISTORY_LIMIT):
        if exclude_id and entry.get("id") == exclude_id:
            continue
        raw_time = entry.get("created_at")
        if not isinstance(raw_time, str):
            continue
        try:
            moment = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        age = (current - moment).total_seconds()
        if age < -60 or age > RECENT_CONTEXT_AGE_SECONDS:
            continue
        excerpt = " ".join(str(entry.get("transcript") or "").split())
        if not excerpt:
            continue
        if len(excerpt) > 220:
            excerpt = excerpt[:217] + "..."
        issues = [
            f"{item.get('title') or ''} ({item.get('url') or ''})".strip()
            for item in (entry.get("issues") or ())
            if isinstance(item, dict) and (item.get("title") or item.get("url"))
        ]
        line = f"- {excerpt}" + (f" [-> {', '.join(issues)}]" if issues else "")
        selected.append(line)
        if len(selected) >= RECENT_CONTEXT_COUNT:
            break
    lines = []
    total = 0
    for line in reversed(selected):
        if total + len(line) + 1 > RECENT_CONTEXT_MAX_CHARS:
            continue
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def prompt_for(dictation, *, dry_run=False, image_paths=(), recent=None):
    lines = [ISSUE_METADATA_POLICY, f"Local date: {date.today().isoformat()}",
             f"Hex request: {dictation.issue_id}", f"Preview only: {str(dry_run).lower()}",
             f"Foreground window: {' '.join((dictation.title or 'unknown').split())}",
             f"Image use: {dictation.image_use or 'none'}"]
    if dictation.suggested_project:
        lines.append(f"Suggested project (non-binding hint): {dictation.suggested_project}")
    for number, (image_path, (mime, image)) in enumerate(zip(image_paths, dictation.images), 1):
        lines += [f"Image {number} file: {image_path}", f"Image {number} MIME type: {mime}",
                  f"Image {number} byte length: {len(image)}"]
    recent_lines = recent_context(exclude_id=dictation.issue_id) if recent is None else recent
    if recent_lines:
        lines += [
            "\nRecent prior Hex requests (background reference context only; "
            "do not re-create or re-edit them unless explicitly asked):",
            recent_lines,
        ]
    lines += ["\nLoad the current projects, their summaries and descriptions through Linear MCP before choosing a project.",
              "Use the project catalog already in this session when continuing a reply, refreshing it if needed.",
              "\nVerbatim transcript", dictation.transcript]
    for question, answer in dictation.answers:
        lines += ["\nFollow-up question", question, "Follow-up answer", answer]
    lines += ["\nFor an essential missing detail, call hex_reply and wait for the answer in this session."]
    if dry_run:
        lines += ["Preview only. Read the relevant Linear context and describe the proposed changes in your final reply.",
                  "The preview agent permits reads and clarification requests. Finish with the proposed plan."]
    else:
        lines += ["When finished, call hex_notify for each affected issue or project with its verified Linear URL."]
    return "\n".join(lines)


def private_file(path):
    return os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600), "w+b")


def start_attempt_log(dictation):
    directory = state_dir() / "runs"
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        attempt = Path(mkdtemp(prefix=f"{dictation.issue_id}-", dir=directory))
        with private_file(attempt / "transcript.txt") as transcript:
            transcript.write(dictation.transcript.encode("utf-8"))
    except OSError as error:
        print(f"Hex Linear dictation {dictation.issue_id} could not start an attempt log ({type(error).__name__}).",
              file=sys.stderr)
        return None
    print(f"Hex Linear dictation {dictation.issue_id} attempt={attempt}", file=sys.stderr, flush=True)
    return attempt


def linear_url(value):
    if not isinstance(value, str) or any(ord(char) <= 32 for char in value):
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.netloc == "linear.app" and bool(
        re.fullmatch(r"/[^/]+/(?:issue|project)/[^/]+(?:/[^?#]*)?", parsed.path))


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def stop_server(process):
    try:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=SERVER_STOP_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=SERVER_STOP_SECONDS)
            except subprocess.TimeoutExpired:
                pass
    except OSError:
        pass


@contextmanager
def private_server(binary, environment):
    port = free_port()
    password = secrets.token_urlsafe(32)
    try:
        process = subprocess.Popen(
            [str(binary), "serve", "--hostname", "127.0.0.1", "--port", str(port)],
            cwd=WORKSPACE, env={**environment, "OPENCODE_PASSWORD": password},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise FilingError("OpenCode could not start the Linear MCP server. Check the Hex service journal.") from error
    try:
        yield OpenCodeServer(f"http://127.0.0.1:{port}", password, process)
    finally:
        stop_server(process)


def mcp_servers(server, timeout):
    authorization = base64.b64encode(f"opencode:{server.password}".encode()).decode("ascii")
    request = urllib.request.Request(
        f"{server.url}/api/mcp", headers={"Authorization": f"Basic {authorization}"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    servers = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(servers, list):
        raise ValueError("Malformed MCP status response")
    return servers


def redact_diagnostic(value, secret):
    if isinstance(value, dict):
        return {key: redact_diagnostic(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_diagnostic(item, secret) for item in value]
    if isinstance(value, str):
        text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", value)
        return text.replace(secret, "[redacted]") if secret else text
    return value


def save_mcp_diagnostic(attempt, *, outcome, servers, process=None, secret=""):
    if attempt is None:
        return
    diagnostic = {"outcome": outcome, "timeout_seconds": MCP_STARTUP_SECONDS, "servers": servers}
    if process is not None:
        diagnostic["server_exit"] = process.poll()
    try:
        with private_file(attempt / "mcp.json") as file:
            text = json.dumps(redact_diagnostic(diagnostic, secret), ensure_ascii=False, indent=2)
            file.write(text.encode("utf-8"))
            file.write(b"\n")
    except OSError as error:
        print(f"Hex Linear dictation {attempt.name} could not save MCP startup diagnostics "
              f"({type(error).__name__}).", file=sys.stderr)


def linear_status(servers):
    for item in servers:
        if isinstance(item, dict) and item.get("name") == MCP_SERVER:
            status = item.get("status")
            return status if isinstance(status, dict) else {}
    return None


def wait_for_linear_mcp(server, attempt, secret=""):
    deadline = time.monotonic() + MCP_STARTUP_SECONDS
    servers = []
    responded = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            save_mcp_diagnostic(attempt, outcome="timeout", servers=servers, process=server.process, secret=secret)
            if responded and linear_status(servers) is None:
                return ("Linear MCP is not configured in the Hex workspace. "
                        "Reinstall Hex and check the Hex service journal.")
            return (f"Linear MCP did not connect within {MCP_STARTUP_SECONDS} seconds. "
                    "Check the Linear credential and the Hex service journal.")
        if server.process.poll() is not None:
            save_mcp_diagnostic(attempt, outcome="server-exit", servers=servers, process=server.process,
                                secret=secret)
            return ("OpenCode exited before Linear MCP was ready. "
                    "Check the Hex service journal.")
        try:
            servers = mcp_servers(server, timeout=max(1, min(remaining, 5)))
        except (OSError, ValueError, urllib.error.URLError):
            pass
        else:
            responded = True
            status = linear_status(servers)
            state = status.get("status") if status else None
            if state == "connected":
                save_mcp_diagnostic(attempt, outcome="connected", servers=servers, process=server.process,
                                    secret=secret)
                return None
            if state == "needs_auth":
                save_mcp_diagnostic(attempt, outcome="needs_auth", servers=servers, process=server.process,
                                    secret=secret)
                return ("Linear MCP needs authentication. Check LINEAR_API_KEY in "
                        "~/.config/clusterfork/.env and the Hex service journal.")
            if state == "failed":
                save_mcp_diagnostic(attempt, outcome="failed", servers=servers, process=server.process,
                                    secret=secret)
                return "Linear MCP failed to start. Check the Linear credential and the Hex service journal."
            if state == "disabled":
                save_mcp_diagnostic(attempt, outcome="disabled", servers=servers, process=server.process,
                                    secret=secret)
                return ("Linear MCP is disabled in the Hex OpenCode workspace. "
                        "Reinstall Hex and check the Hex service journal.")
        time.sleep(max(0, min(MCP_POLL_SECONDS, deadline - time.monotonic())))


def parse_response(output, *, error=None):
    text_parts = {}
    final = session_id = reply = None
    notifications, calls = [], set()
    for line in (output or b"").splitlines():
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(event, dict):
            continue
        session = event.get("sessionID")
        if isinstance(session, str) and re.fullmatch(r"ses_[A-Za-z0-9]+", session):
            session_id = session
        part = event.get("part") or {}
        if event.get("type") == "error":
            error = error or "OpenCode reported an error. Check Linear and the Hex service journal."
        if event.get("type") == "text":
            text_parts.setdefault(part["messageID"], []).append(part["text"])
        if event.get("type") == "step_finish":
            final = part
        if event.get("type") != "tool_use" or part.get("tool") not in {"hex_notify", "hex_reply"}:
            continue
        state = part.get("state") or {}
        call_id = part["id"]
        if state.get("status") != "completed" or call_id in calls:
            continue
        calls.add(call_id)
        arguments = state["input"]
        if part["tool"] == "hex_reply":
            reply = ReplyNeeded(arguments["question"])
        elif linear_url(arguments.get("url")):
            notifications.append(arguments)
        else:
            error = error or "OpenCode supplied an invalid Linear notification URL. See the Hex service journal."
    if final is None or final.get("reason") != "stop":
        error = error or "OpenCode stopped before finishing. Check Linear and the Hex service journal."
    text = "".join(text_parts.get((final or {}).get("messageID"), []))
    return AgentResult(text, session_id, tuple(notifications), reply, error)


def linear_api_key():
    if "LINEAR_API_KEY" in os.environ:
        return os.environ["LINEAR_API_KEY"]
    token = ""
    if DOTENV.is_file():
        for line in DOTENV.read_text().splitlines():
            match = re.match(r"^\s*(?:export\s+)?LINEAR_API_KEY\s*=(.*)$", line)
            if match:
                parts = shlex.split(match[1], comments=True)
                if len(parts) > 1:
                    raise ValueError(f"quote the value of LINEAR_API_KEY in {DOTENV}")
                token = parts[0] if parts else ""
    return token


def run_opencode(dictation, attempt, *, dry_run=False, progress=None):
    binary = shutil.which("opencode") or Path.home() / ".opencode/bin/opencode"
    environment = {**os.environ, "PWD": str(WORKSPACE)}
    environment.pop("HEX_LINEAR_ATTACHMENT_DIR", None)
    token = linear_api_key()
    if token:
        environment["LINEAR_API_KEY"] = token
    command = [str(binary), "run", "--agent", PREVIEW_AGENT if dry_run else AGENT,
               "--model", MODEL, "--format", "json", "--title", "Hex Linear dictation", "--archived"]
    if dictation.session_id:
        command += ["--session", dictation.session_id]
    with ExitStack() as files:
        image_paths = []
        if dictation.images:
            directory = files.enter_context(TemporaryDirectory(prefix="hex-linear-"))
            if dictation.image_use == "attach" and not dry_run:
                environment["HEX_LINEAR_ATTACHMENT_DIR"] = directory
            for number, (mime, image) in enumerate(dictation.images, 1):
                image_path = Path(directory) / f"clipboard-{number}.{EXTENSIONS[mime]}"
                with private_file(image_path) as file:
                    file.write(image)
                image_paths.append(image_path)
                command += ["--file", str(image_path)]
        prompt = prompt_for(dictation, dry_run=dry_run, image_paths=image_paths).encode("utf-8")
        stdout = stderr = None
        if attempt is not None:
            try:
                with private_file(attempt / "prompt.txt") as saved_prompt:
                    saved_prompt.write(prompt)
                response = files.enter_context(private_file(attempt / "stdout.jsonl"))
                errors = files.enter_context(private_file(attempt / "stderr.txt"))
            except OSError as error:
                print(f"Hex Linear dictation {dictation.issue_id} could not save the OpenCode trace ({type(error).__name__}).",
                      file=sys.stderr)
            else:
                stdout, stderr = response, errors
        output = failure = None
        try:
            # The server defaults to WORKSPACE because it starts with that cwd;
            # run_opencode uses the same directory and server for the model turn.
            with private_server(binary, environment) as server:
                if progress:
                    progress(2, 4, "Connecting to Linear")
                startup_error = wait_for_linear_mcp(server, attempt, token)
                if startup_error:
                    return AgentResult("", None, (), error=startup_error)
                if progress:
                    progress(3, 4, "Creating Linear issue")
                environment["OPENCODE_PASSWORD"] = server.password
                command = command[:2] + ["--server", server.url] + command[2:]
                result = subprocess.run(command, input=prompt, cwd=WORKSPACE, env=environment,
                                        stdout=stdout or subprocess.PIPE, stderr=stderr or subprocess.PIPE, timeout=1200)
                output = result.stdout
                if result.returncode:
                    print(f"Hex Linear dictation {dictation.issue_id} OpenCode exited with status {result.returncode}.",
                          file=sys.stderr)
                    failure = "OpenCode failed. Check Linear and the Hex service journal."
        except subprocess.TimeoutExpired as error:
            output = error.stdout
            failure = "OpenCode exceeded its time limit. Check Linear and the Hex service journal."
        except FilingError as error:
            save_mcp_diagnostic(attempt, outcome="server-start-failed", servers=[])
            return AgentResult("", None, (), error=str(error))
        finally:
            if stdout is not None:
                stdout.seek(0)
                output = stdout.read()
    result = parse_response(output, error=failure)
    if result.session_id:
        print(f"Hex Linear dictation {dictation.issue_id} archived OpenCode session {result.session_id}", file=sys.stderr)
    return result


def file_dictation(dictation, *, dry_run=False, progress=None):
    attempt = start_attempt_log(dictation)
    result = run_opencode(dictation, attempt, dry_run=dry_run, progress=progress)
    if not dry_run and not (result.error or result.reply or result.notifications):
        result = replace(result, error="OpenCode finished without confirming a result. See the Hex service journal.")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", required=True, help="Preview a request using read-only MCP tools")
    args = parser.parse_args()
    transcript = sys.stdin.read()
    if not transcript.strip():
        parser.error("Pass a dictation transcript on stdin")
    try:
        result = file_dictation(Dictation(transcript, None, str(uuid4())), dry_run=args.dry_run)
        print(result.text)
        if result.reply:
            print(result.reply, file=sys.stderr)
        if result.error:
            raise FilingError(result.error)
    except FilingError as error:
        print(error, file=sys.stderr)
        return 1
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"Issue preview failed ({type(error).__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
