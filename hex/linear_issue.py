"""Send Hex requests to Luna for execution through Linear's MCP tools."""

import argparse
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import date
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory, mkdtemp
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from common.configuration import settings
from common.images import EXTENSIONS
from common.issue_metadata import ISSUE_METADATA_POLICY


WORKSPACE = Path(__file__).resolve().parent
DOTENV = Path.home() / ".config/clusterfork/.env"
MODEL = "opencode-go/gpt-6-luna#max"
AGENT = "linear-issue"
PREVIEW_AGENT = "linear-issue-preview"
IMAGE_QUESTION = "Copy an image for this request, then click Reply."


class FilingError(ValueError):
    """An error message safe to show in a local notification."""


class ReplyNeeded(FilingError):
    def __init__(self, question, *, copy_image=False):
        super().__init__(question)
        self.copy_image = copy_image


@dataclass(frozen=True)
class Dictation:
    transcript: str
    title: str | None
    issue_id: str
    answers: tuple[tuple[str, str], ...] = ()
    image_use: Literal["attach", "reference"] | None = None
    images: tuple[tuple[str, bytes], ...] = ()
    session_id: str | None = None


@dataclass(frozen=True)
class AgentResult:
    text: str
    session_id: str | None
    notifications: tuple[dict, ...]
    reply: ReplyNeeded | None = None
    error: str | None = None


def prompt_for(dictation, *, dry_run=False, image_paths=()):
    lines = [ISSUE_METADATA_POLICY, f"Local date: {date.today().isoformat()}",
             f"Hex request: {dictation.issue_id}", f"Preview only: {str(dry_run).lower()}",
             f"Foreground window: {' '.join((dictation.title or 'unknown').split())}",
             f"Image use: {dictation.image_use or 'none'}"]
    for number, (image_path, (mime, image)) in enumerate(zip(image_paths, dictation.images), 1):
        lines += [f"Image {number} file: {image_path}", f"Image {number} MIME type: {mime}",
                  f"Image {number} byte length: {len(image)}"]
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
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    directory = state / "hex" / "linear" / "runs"
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
            reply = ReplyNeeded(arguments["question"], copy_image=arguments["copy_image"])
        elif linear_url(arguments.get("url")):
            notifications.append(arguments)
        else:
            error = error or "OpenCode supplied an invalid Linear notification URL. See the Hex service journal."
    if final is None or final.get("reason") != "stop":
        error = error or "OpenCode stopped before finishing. Check Linear and the Hex service journal."
    text = "".join(text_parts.get((final or {}).get("messageID"), []))
    return AgentResult(text, session_id, tuple(notifications), reply, error)


def run_opencode(dictation, attempt, *, dry_run=False):
    binary = shutil.which("opencode") or Path.home() / ".opencode/bin/opencode"
    environment = {**os.environ, "PWD": str(WORKSPACE)}
    token = settings(DOTENV).get("LINEAR_API_KEY")
    if token:
        environment["LINEAR_API_KEY"] = token
    command = [str(binary), "run", "--standalone", "--agent", PREVIEW_AGENT if dry_run else AGENT,
               "--model", MODEL, "--format", "json", "--title", "Hex Linear dictation", "--archived"]
    if dictation.session_id:
        command += ["--session", dictation.session_id]
    with ExitStack() as files:
        image_paths = []
        if dictation.images:
            directory = files.enter_context(TemporaryDirectory(prefix="hex-linear-"))
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
        finally:
            if stdout is not None:
                stdout.seek(0)
                output = stdout.read()
    result = parse_response(output, error=failure)
    if result.session_id:
        print(f"Hex Linear dictation {dictation.issue_id} archived OpenCode session {result.session_id}", file=sys.stderr)
    return result


def file_dictation(dictation, *, dry_run=False):
    attempt = start_attempt_log(dictation)
    if dictation.image_use and not dictation.images:
        raise ReplyNeeded(IMAGE_QUESTION, copy_image=True)
    result = run_opencode(dictation, attempt, dry_run=dry_run)
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
