"""Linear discovery, GitHub checks, and the subprocess boundary."""

import asyncio
from datetime import datetime
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
CLOSED_STATES = {"completed", "canceled", "duplicate"}
REPO_URL = re.compile(r"https://github\.com/([A-Za-z0-9_-]+/[A-Za-z0-9_.-]+)(?=/?(?:[\s>)\],;]|$))", re.I)
PLAN_HEADING = re.compile(r"^ {0,3}##[ \t]+Proposed Plan[ \t]*#*[ \t]*$")
HEADING = re.compile(r"^ {0,3}#{1,2}[ \t]+\S")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
ISSUE_FIELDS = """
    id identifier title url description priority estimate
    state { name type } team { id } project { id }
"""
HISTORY_FIELDS = """
    history(first: 100) {
        nodes {
            id createdAt
            fromState { id name type }
            toState { id name type }
        }
        pageInfo { endCursor hasNextPage }
    }
"""
HISTORY_QUERY = """query($id: String!, $after: String) {
    issue(id: $id) {
        history(first: 100, after: $after) {
            nodes {
                id createdAt
                fromState { id name type }
                toState { id name type }
            }
            pageInfo { endCursor hasNextPage }
        }
    }
}"""
HISTORY_PAGES = 20
ESTIMATE_SCALES = {
    "exponential": ((1, 2, 4, 8, 16), (32, 64)),
    "fibonacci": ((1, 2, 3, 5, 8), (13, 21)),
    "linear": ((1, 2, 3, 4, 5), (6, 7)),
    "tShirt": ((1, 2, 3, 5, 8), (13, 21)),
}
CREATE_ISSUE_MUTATION = """mutation($input: IssueCreateInput!) {
    issueCreate(input: $input) {
        success
        issue {
            id identifier url priority estimate
            state { id name type }
            team { id }
            project { id }
            parent { id identifier }
        }
    }
}"""
BLOCKS_RELATION_MUTATION = """mutation($input: IssueRelationCreateInput!) {
    issueRelationCreate(input: $input) {
        success
        issueRelation { id type issue { id } relatedIssue { id } }
    }
}"""


class CreatedIssueError(RuntimeError):
    """A confirmed issue creation whose returned issue failed validation."""

    def __init__(self, message, issue=None):
        self.issue = issue if isinstance(issue, dict) else {}
        identity = ", ".join(
            f"{key}={self.issue[key]}" for key in ("id", "identifier", "url")
            if isinstance(self.issue.get(key), str) and self.issue[key])
        super().__init__(message + (f" Returned issue: {identity}." if identity else ""))


def estimate_scale(team):
    kind = team.get("issueEstimationType")
    if kind not in ESTIMATE_SCALES:
        raise ValueError(f"The {team.get('name') or 'target'} team does not use a supported estimate scale.")
    base, extended = ESTIMATE_SCALES[kind]
    values = list(base)
    if team.get("issueEstimationExtended") is True:
        values.extend(extended)
    if team.get("issueEstimationAllowZero") is True:
        values.insert(0, 0)
    return values


def valid_history_connection(connection):
    return (isinstance(connection, dict)
            and isinstance(connection.get("nodes"), list)
            and isinstance(connection.get("pageInfo"), dict)
            and isinstance(connection["pageInfo"].get("hasNextPage"), bool))


def valid_history_node(node):
    if not isinstance(node, dict):
        return False
    if not (node.get("fromState") or node.get("toState")):
        return True
    if any(node.get(key) is not None and not isinstance(node.get(key), dict)
           for key in ("fromState", "toState")):
        return False
    try:
        datetime.fromisoformat(str(node.get("createdAt") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


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

    async def issues(self, project_ids, done_since=None):
        ids = list(project_ids)
        if not ids:
            return []
        issues = []
        cursor = None
        where = """project: { id: { in: $ids } }
                    state: { type: { nin: $closed } }"""
        declaration = ""
        variables = {}
        if done_since:
            where = """project: { id: { in: $ids } }
                    or: [
                        { state: { type: { nin: $closed } } }
                        { state: { type: { eq: "completed" } }, completedAt: { gte: $done } }
                    ]"""
            declaration = ", $done: DateTimeOrDuration!"
            variables["done"] = done_since
        while True:
            data = await self.query("""query($ids: [ID!]!, $after: String, $closed: [String!]!""" + declaration + """) {
                issues(first: 50, after: $after, filter: {
                    """ + where + """
                }) {
                    nodes {
                        id createdAt
                        identifier title url description priority priorityLabel estimate dueDate
                        updatedAt completedAt
                        state { name type color position }
                        project { id name }
                        labels(first: 10) { nodes { name color } }
                        """ + HISTORY_FIELDS + """
                    }
                    pageInfo { endCursor hasNextPage }
                }
            }""", ids=ids, after=cursor, closed=sorted(CLOSED_STATES), **variables)
            batch = data["issues"]
            issues.extend(batch["nodes"])
            page = batch["pageInfo"]
            if not page["hasNextPage"]:
                return issues
            cursor = page["endCursor"]

    async def issue_history(self, issue_id, connection=None):
        if connection is None:
            data = await self.query(HISTORY_QUERY, id=issue_id, after=None)
            issue = data.get("issue") if isinstance(data, dict) else None
            if not isinstance(issue, dict):
                raise RuntimeError(f"Linear issue {issue_id} history is unavailable.")
            connection = issue.get("history")
        entries = []
        seen = set()
        pages = 0
        while True:
            if not valid_history_connection(connection):
                raise RuntimeError("Linear returned malformed issue history.")
            nodes = connection["nodes"]
            if not all(valid_history_node(node) for node in nodes):
                raise RuntimeError("Linear issue history contains a malformed status transition.")
            entries.extend(nodes)
            page = connection["pageInfo"]
            if not page["hasNextPage"]:
                return {"entries": entries, "complete": True, "error": ""}
            cursor = page.get("endCursor")
            if not cursor or cursor in seen:
                raise RuntimeError("Linear issue history pagination did not advance.")
            seen.add(cursor)
            if pages >= HISTORY_PAGES:
                return {"entries": entries, "complete": False,
                        "error": f"History exceeds {HISTORY_PAGES} pages."}
            pages += 1
            data = await self.query(HISTORY_QUERY, id=issue_id, after=cursor)
            issue = data.get("issue") if isinstance(data, dict) else None
            if not isinstance(issue, dict):
                raise RuntimeError(f"Linear issue {issue_id} history page is unavailable.")
            connection = issue.get("history")

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

    async def project_context(self, project_id):
        teams = []
        cursor = None
        while True:
            data = await self.query("""query($id: String!, $after: String) {
                project(id: $id) {
                    id name archivedAt
                    teams(first: 50, after: $after) {
                        nodes { id name key archivedAt }
                        pageInfo { endCursor hasNextPage }
                    }
                }
            }""", id=project_id, after=cursor)
            project = data.get("project") if isinstance(data, dict) else None
            if not isinstance(project, dict):
                raise ValueError(f"Linear project {project_id} is unavailable.")
            connection = project.get("teams")
            if (not isinstance(connection, dict) or not isinstance(connection.get("nodes"), list)
                    or not all(isinstance(team, dict) for team in connection["nodes"])):
                raise RuntimeError("Linear returned malformed project teams.")
            teams.extend(connection["nodes"])
            page = connection.get("pageInfo")
            if not isinstance(page, dict) or not isinstance(page.get("hasNextPage"), bool):
                raise RuntimeError("Linear returned malformed project team pagination.")
            if not page["hasNextPage"]:
                return {"id": project.get("id"), "name": project.get("name"),
                        "archivedAt": project.get("archivedAt"), "teams": teams}
            cursor = page.get("endCursor")
            if not cursor:
                raise RuntimeError("Linear project team pagination did not advance.")

    async def team_context(self, team_id):
        states = []
        cursor = None
        while True:
            data = await self.query("""query($id: String!, $after: String) {
                team(id: $id) {
                    id name key archivedAt
                    issueEstimationType issueEstimationExtended issueEstimationAllowZero
                    states(first: 100, after: $after) {
                        nodes { id name type archivedAt }
                        pageInfo { endCursor hasNextPage }
                    }
                }
            }""", id=team_id, after=cursor)
            team = data.get("team") if isinstance(data, dict) else None
            if not isinstance(team, dict):
                raise ValueError(f"Linear team {team_id} is unavailable.")
            connection = team.get("states")
            if (not isinstance(connection, dict) or not isinstance(connection.get("nodes"), list)
                    or not all(isinstance(state, dict) for state in connection["nodes"])):
                raise RuntimeError("Linear returned malformed team states.")
            states.extend(connection["nodes"])
            page = connection.get("pageInfo")
            if not isinstance(page, dict) or not isinstance(page.get("hasNextPage"), bool):
                raise RuntimeError("Linear returned malformed team state pagination.")
            if not page["hasNextPage"]:
                return {**team, "states": states}
            cursor = page.get("endCursor")
            if not cursor:
                raise RuntimeError("Linear team state pagination did not advance.")

    async def team_labels(self, team_id):
        labels = []
        cursor = None
        seen = set()
        pages = 0
        while True:
            data = await self.query("""query($team: ID!, $after: String) {
                issueLabels(first: 100, after: $after, filter: {
                    or: [
                        { team: { id: { eq: $team } } }
                        { team: { null: true } }
                    ]
                }) {
                    nodes { id name archivedAt }
                    pageInfo { endCursor hasNextPage }
                }
            }""", team=team_id, after=cursor)
            connection = data.get("issueLabels") if isinstance(data, dict) else None
            if (not isinstance(connection, dict) or not isinstance(connection.get("nodes"), list)
                    or not all(isinstance(node, dict) for node in connection["nodes"])):
                raise RuntimeError("Linear returned malformed labels.")
            for node in connection["nodes"]:
                if node.get("archivedAt"):
                    continue
                if not isinstance(node.get("id"), str) or not node["id"] or not isinstance(node.get("name"), str):
                    raise RuntimeError("Linear returned malformed labels.")
                labels.append(node)
            page = connection.get("pageInfo")
            if not isinstance(page, dict) or not isinstance(page.get("hasNextPage"), bool):
                raise RuntimeError("Linear returned malformed label pagination.")
            if not page["hasNextPage"]:
                return labels
            cursor = page.get("endCursor")
            if not cursor or cursor in seen:
                raise RuntimeError("Linear label pagination did not advance.")
            seen.add(cursor)
            pages += 1
            if pages >= 20:
                raise RuntimeError("Linear labels exceed 20 pages.")

    async def create_issue(self, project_id, team_id, state_id, title, description, priority,
                           estimate, parent_id=None, label_ids=()):
        fields = {"teamId": team_id, "projectId": project_id, "stateId": state_id,
                  "title": title, "description": description, "priority": priority,
                  "estimate": estimate, "useDefaultTemplate": False}
        if parent_id:
            fields["parentId"] = parent_id
        if label_ids:
            fields["labelIds"] = list(label_ids)
        try:
            data = await self.query(CREATE_ISSUE_MUTATION, input=fields)
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            raise RuntimeError(
                "The Linear issue creation outcome is uncertain after an error. "
                "Check existing issues in Linear and reconcile before creating another. "
                + (str(error) or type(error).__name__)) from None
        result = data.get("issueCreate") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            raise RuntimeError("Linear returned no issue creation result. "
                               "Check existing issues in Linear and reconcile before creating another.")
        if result.get("success") is not True:
            raise RuntimeError("Linear rejected the issue creation.")
        issue = result.get("issue")
        if not isinstance(issue, dict):
            raise CreatedIssueError("Linear reported a successful creation without returning the issue. "
                                    "Check existing issues in Linear and reconcile before creating another.")
        identifier = issue.get("identifier")
        if (not isinstance(identifier, str) or not ISSUE_ID.fullmatch(identifier)
                or not isinstance(issue.get("id"), str) or not issue["id"]
                or not isinstance(issue.get("url"), str) or not issue["url"]):
            raise CreatedIssueError("Linear created an issue but returned an incomplete identity. "
                                    "Check Linear before creating another.", issue)
        if (issue.get("project") or {}).get("id") != project_id:
            raise CreatedIssueError(f"Linear created {identifier} in the wrong project. "
                                    "Check Linear before creating another.", issue)
        if (issue.get("team") or {}).get("id") != team_id:
            raise CreatedIssueError(f"Linear created {identifier} in the wrong team. "
                                    "Check Linear before creating another.", issue)
        state = issue.get("state") or {}
        if state.get("name") != "Backlog" or state.get("type") != "backlog":
            raise CreatedIssueError(f"Linear created {identifier} outside Backlog. "
                                    "Check Linear before creating another.", issue)
        if issue.get("priority") != priority or issue.get("estimate") != estimate:
            raise CreatedIssueError(f"Linear created {identifier} without the requested priority and estimate. "
                                    "Check Linear before creating another.", issue)
        if parent_id and (issue.get("parent") or {}).get("id") != parent_id:
            raise CreatedIssueError(f"Linear created {identifier} without the requested parent. "
                                    "Check Linear before creating another.", issue)
        return issue

    async def create_blocks_relation(self, issue_id, related_issue_id):
        try:
            data = await self.query(BLOCKS_RELATION_MUTATION, input={
                "issueId": issue_id, "relatedIssueId": related_issue_id, "type": "blocks"})
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            raise RuntimeError("The blocking link outcome is uncertain after an error. "
                               + (str(error) or type(error).__name__)) from None
        result = data.get("issueRelationCreate") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            raise RuntimeError("Linear returned no issue relation result.")
        if result.get("success") is not True:
            raise RuntimeError("Linear rejected the blocking link.")
        relation = result.get("issueRelation")
        if (not isinstance(relation, dict) or relation.get("type") != "blocks"
                or (relation.get("issue") or {}).get("id") != issue_id
                or (relation.get("relatedIssue") or {}).get("id") != related_issue_id):
            raise RuntimeError("Linear returned a mismatched blocking link.")
        return relation

    async def set_state(self, identifier, name, kind):
        issue = await self.issue(identifier)
        if issue["state"] == {"name": name, "type": kind}:
            return
        if issue["state"]["type"] in CLOSED_STATES:
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
    env = {key: value for key, value in os.environ.items() if key != "LINEAR_WEBHOOK_SECRET"}
    return {**env, "GH_TOKEN": os.environ["GITHUB_TOKEN"], "GIT_TERMINAL_PROMPT": "0"}


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


async def ci_jobs(repo, run_id, attempt):
    jobs = []
    for page in range(1, 6):
        query = urlencode({"per_page": 100, "page": page})
        response = await asyncio.to_thread(
            request_json,
            f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?{query}",
            {"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"},
        )
        batch = response.get("jobs")
        if not isinstance(batch, list):
            raise RuntimeError("GitHub returned a malformed job list.")
        jobs.extend(batch)
        total = response.get("total_count")
        if isinstance(total, int) and len(jobs) >= total:
            return jobs
        if not isinstance(total, int) and len(batch) < 100:
            return jobs
        if page == 5:
            raise RuntimeError("GitHub job list exceeds the 500-job recording limit.")
    return jobs
