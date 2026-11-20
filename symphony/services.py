"""Linear discovery, GitHub checks, and the subprocess boundary."""

import asyncio
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import signal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from emoji import emojize, is_emoji
import yaml


ISSUE_ID = re.compile(r"[A-Z][A-Z0-9]*-[0-9]+\Z")
REPO_URL = re.compile(r"https://github\.com/([A-Za-z0-9_-]+/[A-Za-z0-9_.-]+)(?=/?(?:[\s>)\],;]|$))", re.I)
PLAN_HEADING = re.compile(r"^ {0,3}##[ \t]+Proposed Plan[ \t]*#*[ \t]*$")
HEADING = re.compile(r"^ {0,3}#{1,2}[ \t]+\S")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
ISSUE_FIELDS = """
    id identifier title url description priority estimate
    state { name type } team { id } project { id }
"""


def plan_bounds(description):
    offset = 0
    plan = fence = None
    for line in description.splitlines(keepends=True):
        text = line.rstrip("\r\n")
        marker = FENCE.fullmatch(text)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1].startswith(fence) and not marker[2].strip():
                fence = None
        elif fence is None:
            if plan is not None and HEADING.match(text):
                return *plan, offset
            if PLAN_HEADING.fullmatch(text):
                plan = offset, offset + len(line)
        offset += len(line)
    return (*plan, len(description)) if plan else None


def proposed_plan(description):
    bounds = plan_bounds(description or "")
    return description[bounds[1]:bounds[2]].strip() if bounds else ""


def with_plan(description, plan):
    description = description or ""
    bounds = plan_bounds(description)
    section = "## Proposed Plan\n\n" + plan.strip()
    if bounds:
        before, after = description[:bounds[0]], description[bounds[2]:]
        return before + section + ("\n\n" + after if after else "")
    return "\n\n".join(part for part in (description.rstrip(), section) if part)


def repo_links(text):
    return {match.lower().removesuffix(".git") for match in REPO_URL.findall(text or "")
            if match.split("/")[-1] not in {".", ".."}}


def request_json(url, headers, payload=None):
    request = Request(url, headers=headers, data=json.dumps(payload).encode() if payload else None)
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        if payload:
            try:
                errors = json.load(error).get("errors") or []
                detail = "; ".join(str(item["message"]) for item in errors)
            except (ValueError, KeyError, TypeError):
                detail = ""
            if detail:
                raise RuntimeError(f"Linear HTTP {error.code}: {detail[:500]}") from None
        raise RuntimeError(f"{request.host} returned HTTP {error.code}") from None
    except URLError as error:
        raise RuntimeError(f"Cannot reach {request.host}: {error.reason}") from None


class Linear:
    def __init__(self, token):
        self.token = token

    async def query(self, query, **variables):
        response = await asyncio.to_thread(
            request_json,
            "https://api.linear.app/graphql",
            {"Authorization": self.token, "Content-Type": "application/json"},
            {"query": query, "variables": variables},
        )
        if response.get("errors"):
            raise RuntimeError("Linear: " + "; ".join(error["message"] for error in response["errors"]))
        return response["data"]

    async def projects(self):
        repos = {}
        problems = {}
        cursor = None
        while True:
            data = await self.query("""query($after: String) {
                projects(first: 50, after: $after) {
                    nodes { id name icon description archivedAt externalLinks(first: 50) {
                        nodes { url } pageInfo { hasNextPage }
                    } }
                    pageInfo { endCursor hasNextPage }
                }
            }""", after=cursor)
            projects = data["projects"]
            for project in projects["nodes"]:
                if project["archivedAt"]:
                    continue
                links = project["externalLinks"]
                urls = set().union(*(repo_links(link["url"]) for link in links["nodes"]))
                urls = urls or repo_links(project["description"])
                if len(urls) != 1 or links["pageInfo"]["hasNextPage"]:
                    problems[project["id"]] = f"{project['name']} needs one GitHub repository link in Linear."
                    continue
                repo = urls.pop()
                icon = emojize(project["icon"] or "", language="alias")
                repos.setdefault(repo, []).append({
                    "id": project["id"], "name": project["name"],
                    "icon": icon if is_emoji(icon) else "",
                })
            page = projects["pageInfo"]
            if not page["hasNextPage"]:
                return repos, problems
            cursor = page["endCursor"]

    async def issues(self, project_ids):
        ids = list(project_ids)
        if not ids:
            return []
        issues = []
        cursor = None
        while True:
            data = await self.query("""query($ids: [ID!]!, $after: String) {
                issues(first: 50, after: $after, filter: {
                    project: { id: { in: $ids } }
                    state: { type: { nin: ["completed", "canceled"] } }
                }) {
                    nodes {
                        identifier title url description priority priorityLabel estimate dueDate
                        state { name type }
                        project { id name }
                        labels(first: 10) { nodes { name color } }
                    }
                    pageInfo { endCursor hasNextPage }
                }
            }""", ids=ids, after=cursor)
            batch = data["issues"]
            issues.extend(batch["nodes"])
            page = batch["pageInfo"]
            if not page["hasNextPage"]:
                return issues
            cursor = page["endCursor"]

    async def issue(self, identifier):
        if not ISSUE_ID.fullmatch(identifier):
            raise ValueError("Use a Linear issue ID such as KIAN-1077.")
        data = await self.query("query($id: String!) { issue(id: $id) { " + ISSUE_FIELDS + " } }", id=identifier)
        if data["issue"] is None:
            raise ValueError(f"Linear issue {identifier} is unavailable.")
        return data["issue"]

    async def update(self, task_id, **fields):
        data = await self.query("""mutation($id: String!, $input: IssueUpdateInput!) {
            issueUpdate(id: $id, input: $input) { success issue { """ + ISSUE_FIELDS + """ } }
        }""", id=task_id, input=fields)
        result = data["issueUpdate"]
        if not result["success"]:
            raise RuntimeError("Linear rejected the issue update.")
        return result["issue"]

    async def set_state(self, identifier, name, kind):
        issue = await self.issue(identifier)
        if issue["state"] == {"name": name, "type": kind}:
            return
        if issue["state"]["type"] in {"completed", "canceled"}:
            raise ValueError(f"{identifier} is already {issue['state']['name']}.")
        data = await self.query("""query($id: String!) {
            team(id: $id) { states(first: 100) { nodes { id name type } } }
        }""", id=issue["team"]["id"])
        states = [state["id"] for state in data["team"]["states"]["nodes"]
                  if state["name"] == name and state["type"] == kind]
        if len(states) != 1:
            raise RuntimeError(f"The Linear team must have one {name} state.")
        updated = await self.update(issue["id"], stateId=states[0])
        if updated["state"] != {"name": name, "type": kind}:
            raise RuntimeError(f"Linear did not move {identifier} to {name}.")


async def stop_process(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def command(*args, cwd=None, env=None, timeout=120):
    process = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, env=env, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, start_new_session=True,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout)
    except (asyncio.CancelledError, TimeoutError):
        await stop_process(process)
        raise
    if process.returncode:
        message = err.decode(errors="replace").strip() or out.decode(errors="replace").strip()
        raise RuntimeError(message[-3500:] or f"{args[0]} exited {process.returncode}.")
    return out.decode(errors="replace").rstrip("\n")


def github_env():
    return {**os.environ, "GH_TOKEN": os.environ["GITHUB_TOKEN"], "GIT_TERMINAL_PROMPT": "0"}


async def git(path, *args):
    return await command("git", "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
                         *args, cwd=path, env=github_env())


FILTER_TOKENS = re.compile(r"\\.|(?:^|(?<=/))\*\*/|\*\*|\[[a-zA-Z0-9-]+\]|.", re.DOTALL)
FILTER_WILDCARDS = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*"}


@lru_cache(maxsize=128)
def workflow_pattern(pattern):
    parts = []
    for token in FILTER_TOKENS.findall(pattern):
        if token in FILTER_WILDCARDS:
            parts.append(FILTER_WILDCARDS[token])
        elif token in {"?", "+"} and parts:
            parts[-1] = f"(?:{parts[-1]}){token}"
        elif token.startswith("[") and len(token) > 1:
            parts.append(token)
        else:
            parts.append(re.escape(token[1:] if token.startswith("\\") and len(token) > 1 else token))
    return re.compile("".join(parts), re.DOTALL)


def matches_filter(value, patterns):
    for pattern in reversed(patterns):
        if workflow_pattern(pattern.removeprefix("!")).fullmatch(value):
            return not pattern.startswith("!")
    return False


async def ci_workflows(repo, base, commit):
    files = (await git(repo, "ls-tree", "-r", "--name-only", commit, ".github/workflows/")).splitlines()
    files = [name for name in files if Path(name).suffix in {".yml", ".yaml"}]
    changed = [path for path in (await git(
        repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", base, commit,
    )).split("\0") if path]
    workflows = []
    for name in files:
        config = yaml.load(await git(repo, "show", f"{commit}:{name}"), Loader=yaml.BaseLoader)
        triggers = (config or {}).get("on", {})
        if isinstance(triggers, str):
            triggers = [triggers]
        if "push" not in triggers:
            continue
        push = (triggers.get("push") or {}) if isinstance(triggers, dict) else {}
        if not isinstance(push, dict):
            push = {}
        if "branches" in push and not matches_filter("main", push["branches"]):
            continue
        if matches_filter("main", push.get("branches-ignore", [])):
            continue
        if {"tags", "tags-ignore"} & push.keys() and not {"branches", "branches-ignore"} & push.keys():
            continue
        if "paths" in push and not any(matches_filter(path, push["paths"]) for path in changed):
            continue
        if "paths-ignore" in push and all(matches_filter(path, push["paths-ignore"]) for path in changed):
            continue
        workflows.append(name)
    return workflows


async def ci_runs(repo, commit):
    query = urlencode({"head_sha": commit, "branch": "main", "event": "push", "per_page": 100})
    response = await asyncio.to_thread(
        request_json, f"https://api.github.com/repos/{repo}/actions/runs?{query}",
        {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"},
    )
    return response["workflow_runs"]
