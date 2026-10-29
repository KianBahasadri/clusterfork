"""Linear, GitHub App, and durable Azure receipt adapters for the routing pilot."""

from __future__ import annotations

import json
import hashlib
import time
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from uuid import UUID

from dispatch_core import DispatchError
from worker_reporting import QUEUED_LABEL, SELECTION_LABEL, STAGE_LABELS
from diagnostics import failure, http_operation, operation


class RequestError(RuntimeError):
    def __init__(self, status=None):
        self.status = status
        # Never include response bodies, request headers, or credential values.
        super().__init__(f"remote request failed (HTTP {status or 'unavailable'})")


def request_json(url, *, token, method="GET", data=None):
    headers = {"Authorization": token, "Accept": "application/json",
               "User-Agent": "clusterfork-routing-pilot", "Content-Type": "application/json"}
    if url.startswith("https://api.github.com/"):
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    body = None if data is None else json.dumps(data).encode()
    request = Request(url, data=body, headers=headers, method=method)
    with operation(http_operation(url, data), timeout_seconds=20):
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except (HTTPError, URLError, OSError, ValueError) as exc:
            error = RequestError(exc.code if isinstance(exc, HTTPError) else None)
            error.clusterfork_failure = failure(exc, http_operation(url, data))
            raise error from None


ISSUE_FIELDS = """
id identifier title description url archivedAt createdAt priority
state { id type }
"""
PROJECT_FIELDS = "id archivedAt labels(first: 100) { nodes { name } pageInfo { hasNextPage } }"


class Linear:
    def __init__(self, token, request=request_json):
        self.token = token
        self.request = request

    def query(self, query, variables):
        result = self.request("https://api.linear.app/graphql", token=self.token,
                              method="POST", data={"query": query, "variables": variables})
        if not isinstance(result, dict) or result.get("errors") or "data" not in result:
            raise RequestError()
        return result["data"]

    @staticmethod
    def complete(issue):
        if issue and issue.get("project"):
            if issue["project"]["labels"]["pageInfo"]["hasNextPage"]:
                raise ValueError("project has more than 100 labels; cannot evaluate enrollment")
        return issue

    def issue(self, issue_id):
        query = ("query($id: String!) { issue(id: $id) { " + ISSUE_FIELDS
                 + " project { " + PROJECT_FIELDS + " } } }")
        return self.complete(self.query(query, {"id": issue_id})["issue"])

    @lru_cache(maxsize=1)
    def viewer_id(self):
        return self.query("query { viewer { id } }", {})["viewer"]["id"]

    def comment(self, comment_id):
        return self.query("""query($id: String!) {
          comment(id: $id) { issue { id } }
        }""", {"id": comment_id})["comment"]

    def issue_comments(self, issue_id):
        cursor = None
        while True:
            # Query all issue-associated comments, including threaded replies,
            # rather than relying on the issue's top-level discussion view.
            comments = self.query("""query($id: ID!, $after: String) {
              comments(first: 50, after: $after, filter: {issue: {id: {eq: $id}}}) {
                nodes { id body createdAt user { id } botActor { id } parent { id } }
                pageInfo { hasNextPage endCursor }
              }
            }""", {"id": issue_id, "after": cursor})["comments"]
            yield from comments["nodes"]
            if not comments["pageInfo"]["hasNextPage"]:
                return
            next_cursor = comments["pageInfo"]["endCursor"]
            if not next_cursor or next_cursor == cursor:
                raise ValueError("Linear comment pagination did not advance")
            cursor = next_cursor

    def project_issues(self, project_id):
        # Fetch project labels once per page. Nesting them under every issue
        # multiplies Linear's query complexity by both connection page sizes.
        query = """query($id: String!, $after: String) {
          project(id: $id) { """ + PROJECT_FIELDS + """ issues(first: 50, after: $after) {
            nodes { """ + ISSUE_FIELDS + """ }
            pageInfo { hasNextPage endCursor }
          } }
        }"""
        cursor = None
        while True:
            result = self.query(query, {"id": project_id, "after": cursor})["project"]
            if not result:
                return
            issues = result["issues"]
            project = {key: result[key] for key in ("id", "archivedAt", "labels")}
            for issue in issues["nodes"]:
                yield self.complete({**issue, "project": project})
            if not issues["pageInfo"]["hasNextPage"]:
                break
            next_cursor = issues["pageInfo"]["endCursor"]
            if not next_cursor or next_cursor == cursor:
                raise ValueError("Linear pagination did not advance")
            cursor = next_cursor

    def set_state(self, issue_id, state_id):
        result = self.query("""mutation($id: String!, $state: String!) {
          issueUpdate(id: $id, input: {stateId: $state}) { success }
        }""", {"id": issue_id, "state": state_id})
        if not result["issueUpdate"]["success"]:
            raise RequestError()

    def blocked_state(self, issue_id):
        team = self.query("""query($id: String!) {
          issue(id: $id) { team { states(first: 100) {
            nodes { id name type } pageInfo { hasNextPage }
          } } }
        }""", {"id": issue_id})["issue"]["team"]
        if team["states"]["pageInfo"]["hasNextPage"]:
            raise ValueError("Incomplete team workflow states")
        choices = [state for state in team["states"]["nodes"]
                   if state["name"].casefold() == "blocked" and state["type"] in {"unstarted", "started"}]
        if len(choices) != 1:
            raise ValueError("Team needs an unambiguous Blocked workflow state")
        return choices[0]["id"]

    def set_clarification(self, issue_id, enabled):
        from readiness_policy import CLARIFICATION_LABEL

        self.set_label(issue_id, CLARIFICATION_LABEL, enabled)

    def set_selected(self, issue_id, enabled):
        self.set_label(issue_id, SELECTION_LABEL, enabled)

    def set_queued(self, issue_id, enabled):
        self.set_label(issue_id, QUEUED_LABEL, enabled)

    def set_label(self, issue_id, name, enabled):
        issue = self.query("""query($id: String!) {
          issue(id: $id) { team { id } labels(first: 100) {
            nodes { id name } pageInfo { hasNextPage }
          } }
        }""", {"id": issue_id})["issue"]
        if issue["labels"]["pageInfo"]["hasNextPage"]:
            raise ValueError("Incomplete issue labels")
        owned = [label for label in issue["labels"]["nodes"] if label["name"] == name]
        if enabled and not owned:
            identifier = self.stage_label(issue["team"]["id"], name)
            result = self.query("""mutation($id: String!, $label: String!) {
              issueAddLabel(id: $id, labelId: $label) { success }
            }""", {"id": issue_id, "label": identifier})["issueAddLabel"]
            if not result["success"]:
                raise RequestError()
        elif not enabled:
            for label in owned:
                result = self.query("""mutation($id: String!, $label: String!) {
                  issueRemoveLabel(id: $id, labelId: $label) { success }
                }""", {"id": issue_id, "label": label["id"]})["issueRemoveLabel"]
                if not result["success"]:
                    raise RequestError()

    def stage_label(self, team_id, name):
        def lookup():
            result = self.query("""query($team: ID!, $name: String!) {
              issueLabels(first: 2, filter: {team: {id: {eq: $team}}, name: {eq: $name}}) {
                nodes { id } pageInfo { hasNextPage }
              }
            }""", {"team": team_id, "name": name})["issueLabels"]
            if result["pageInfo"]["hasNextPage"] or len(result["nodes"]) > 1:
                raise ValueError("Ambiguous worker stage label")
            return result["nodes"][0]["id"] if result["nodes"] else None

        existing = lookup()
        if existing:
            return existing
        digest = hashlib.sha256(f"clusterfork:{team_id}:{name}".encode()).digest()
        identifier = str(UUID(bytes=digest[:16], version=4))
        try:
            result = self.query("""mutation($id: String!, $team: String!, $name: String!) {
              issueLabelCreate(input: {id: $id, teamId: $team, name: $name,
                description: "Clusterfork repository queue, selection, development stage or readiness", color: "#5E6AD2"}) { success }
            }""", {"id": identifier, "team": team_id, "name": name})["issueLabelCreate"]
            if not result["success"]:
                raise RequestError()
        except RequestError:
            # Another issue in this team can create the same label concurrently,
            # or creation may have succeeded before its response was lost.
            existing = lookup()
            if not existing:
                raise
            return existing
        return identifier

    def set_stage(self, issue_id, stage):
        name = STAGE_LABELS[stage] if stage is not None else None
        issue = self.query("""query($id: String!) {
          issue(id: $id) { team { id } labels(first: 100) {
            nodes { id name } pageInfo { hasNextPage }
          } }
        }""", {"id": issue_id})["issue"]
        if issue["labels"]["pageInfo"]["hasNextPage"]:
            raise ValueError("Incomplete issue labels")
        labels = issue["labels"]["nodes"]
        if name and not any(label["name"] == name for label in labels):
            identifier = self.stage_label(issue["team"]["id"], name)
            result = self.query("""mutation($id: String!, $label: String!) {
              issueAddLabel(id: $id, labelId: $label) { success }
            }""", {"id": issue_id, "label": identifier})["issueAddLabel"]
            if not result["success"]:
                raise RequestError()
        for label in labels:
            if label["name"] in STAGE_LABELS.values() and label["name"] != name:
                result = self.query("""mutation($id: String!, $label: String!) {
                  issueRemoveLabel(id: $id, labelId: $label) { success }
                }""", {"id": issue_id, "label": label["id"]})["issueRemoveLabel"]
                if not result["success"]:
                    raise RequestError()

    def status_comment(self, issue_id, comment_id, body):
        # A deterministic ID recovers creation after an ambiguous API response.
        # Update one owned comment rather than adding a comment on every poll.
        result = self.query("""query($issue: String!, $comment: ID!) {
          issue(id: $issue) { comments(first: 1, filter: {id: {eq: $comment}}) { nodes { id body } } }
        }""", {"issue": issue_id, "comment": comment_id})
        comments = result["issue"]["comments"]["nodes"]
        if comments:
            if comments[0]["body"] == body:
                return
            result = self.query("""mutation($id: String!, $body: String!) {
              commentUpdate(id: $id, input: {body: $body}) { success }
            }""", {"id": comment_id, "body": body})["commentUpdate"]
        else:
            result = self.query("""mutation($id: String!, $issue: String!, $body: String!) {
              commentCreate(input: {id: $id, issueId: $issue, body: $body, doNotSubscribeToIssue: true}) { success }
            }""", {"id": comment_id, "issue": issue_id, "body": body})["commentCreate"]
        if not result["success"]:
            raise RequestError()


class GitHub:
    def __init__(self, app_id, private_key, request=request_json):
        self.app_id, self.private_key, self.request = app_id, private_key, request

    def installation_token(self, repository, *, permissions=None):
        import jwt

        now = int(time.time())
        try:
            signed = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id},
                                self.private_key, algorithm="RS256")
            installation = self.request(
                f"https://api.github.com/repos/{repository}/installation", token=f"Bearer {signed}")
            result = self.request(
                f"https://api.github.com/app/installations/{installation['id']}/access_tokens",
                token=f"Bearer {signed}", method="POST",
                data={"repositories": [repository.split("/")[1]],
                      "permissions": permissions if permissions is not None else {"actions": "write"}})
            return result["token"]
        except Exception:
            # No workflow dispatch has been attempted yet, so retry is safe.
            raise DispatchError("GitHub App authentication failed; check its ID, key and installation") from None

    def branch_sha(self, route):
        import re

        token = self.installation_token(route.repository, permissions={"contents": "read"})
        result = self.request(f"https://api.github.com/repos/{route.repository}/commits/{quote(route.ref, safe='')}",
                              token=f"Bearer {token}")
        if not isinstance(result, dict) or not re.fullmatch(r"[0-9a-f]{40}", result.get("sha", "")):
            raise RequestError()
        return result["sha"]

    def verify_worker_run(self, repository, claims, dispatch_key):
        from worker_core import WorkerError
        token = self.installation_token(repository, permissions={"actions": "read"})
        run = self.request(f"https://api.github.com/repos/{repository}/actions/runs/{claims['run_id']}",
                           token=f"Bearer {token}")
        if (run.get("event") != "workflow_dispatch" or run.get("head_sha") != claims["sha"]
                or str(run.get("run_attempt")) != str(claims["run_attempt"])
                or str(run.get("repository", {}).get("id")) != str(claims["repository_id"])
                or run.get("head_repository", {}).get("full_name") != repository
                or run.get("path") != ".github/workflows/clusterfork-worker.yml"
                or dispatch_key not in run.get("display_title", "")
                or run.get("actor", {}).get("login") != "clusterfork[bot]"):
            raise WorkerError("GitHub run does not match the dispatch", 403)

    def worker_result(self, repository, owner):
        run_id, attempt = owner.split(":")
        if not run_id.isdigit() or not attempt.isdigit():
            raise ValueError("Invalid stored worker identity")
        token = self.installation_token(repository, permissions={"actions": "read"})
        return self.request(
            f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/attempts/{attempt}",
            token=f"Bearer {token}")

    def dispatch_runs(self, route, key):
        """List every matching run's latest attempt, including manual reruns.

        Do not filter by creation time/status: an old run can be rerun today.
        Avoid search filters and their 1,000-result cap; paginate the workflow.
        """
        token = self.installation_token(route.repository, permissions={"actions": "read"})
        matches, page, seen = [], 1, 0
        while True:
            result = self.request(
                f"https://api.github.com/repos/{route.repository}/actions/workflows/"
                f"{quote(route.workflow, safe='')}/runs?per_page=100&page={page}", token=f"Bearer {token}")
            runs = result["workflow_runs"]
            for run in runs:
                if key not in run.get("display_title", ""):
                    continue
                if (run.get("event") != "workflow_dispatch"
                        or run.get("path") != f".github/workflows/{route.workflow}"
                        or run.get("actor", {}).get("login") != "clusterfork[bot]"
                        or run.get("repository", {}).get("full_name", "").lower() != route.repository.lower()
                        or not isinstance(run.get("id"), int) or not isinstance(run.get("run_attempt"), int)
                        or run.get("status") not in {"queued", "in_progress", "completed", "waiting", "requested", "pending"}):
                    raise RequestError()
                matches.append(run)
            seen += len(runs)
            if len(runs) < 100:
                if seen < result["total_count"]:
                    raise RequestError()  # A truncated listing cannot prove the worker stopped.
                return matches
            page += 1

    def worker_pull(self, route, receipt, key, url=""):
        import re
        from urllib.parse import urlencode

        repository = route.repository
        token = self.installation_token(repository, permissions={"contents": "read", "pull_requests": "read"})
        branch = f"clusterfork/{receipt['identifier'].lower()}-{key[:8]}"
        number = None
        if url:
            match = re.fullmatch(re.escape(f"https://github.com/{repository}/pull/") + r"([1-9][0-9]*)", url)
            if not match:
                raise ValueError("Invalid stored PR URL")
            number = int(match[1])
        pulls, page = [], 1
        while True:
            query = urlencode({"state": "all", "head": f"{repository.split('/')[0]}:{branch}",
                               "base": route.ref, "per_page": 100, "page": page})
            batch = self.request(f"https://api.github.com/repos/{repository}/pulls?{query}", token=f"Bearer {token}")
            if not isinstance(batch, list):
                raise RequestError()
            pulls.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        # An open PR always wins, even if an older stored URL is already merged
        # or closed. Otherwise retain that URL to handle deleted branch refs.
        opened = next((p for p in pulls if p.get("state") == "open"), None)
        if opened:
            number = opened["number"]
        elif number is None and pulls:
            number = next((p["number"] for p in pulls if p.get("merged_at")), pulls[0]["number"])
        if number is None:
            return None
        result = self.request("https://api.github.com/graphql", token=f"Bearer {token}", method="POST", data={
            "query": """query($owner: String!, $name: String!, $number: Int!) {
              repository(owner: $owner, name: $name) { pullRequest(number: $number) {
                url state merged body baseRefName headRefName headRepository { nameWithOwner }
                author { login __typename } mergeable mergeStateStatus isDraft autoMergeRequest { enabledAt }
                commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) {
                  nodes {
                    __typename
                    ... on CheckRun { name status conclusion isRequired(pullRequestNumber: $number) }
                    ... on StatusContext { context state isRequired(pullRequestNumber: $number) }
                  } pageInfo { hasNextPage }
                } } } } }
              } }
            }""",
            "variables": {"owner": repository.split('/')[0], "name": repository.split('/')[1], "number": number}})
        pull = ((result.get("data") or {}).get("repository") or {}).get("pullRequest")
        if (result.get("errors") or not pull or pull.get("baseRefName") != route.ref
                or pull.get("headRefName") != branch
                or (pull.get("headRepository") or {}).get("nameWithOwner") != repository
                or pull.get("author") != {"login": "clusterfork", "__typename": "Bot"}
                or f"<!-- clusterfork-dispatch:{key} -->" not in (pull.get("body") or "")):
            raise RequestError()
        return pull

    def dispatch(self, route, inputs):
        token = self.installation_token(route.repository)
        try:
            self.request(
                f"https://api.github.com/repos/{route.repository}/actions/workflows/"
                f"{quote(route.workflow, safe='')}/dispatches", token=f"Bearer {token}",
                method="POST", data={"ref": route.ref, "inputs": inputs})
        except RequestError as exc:
            # A definite client rejection cannot have started a run. Treat rate
            # limits as a rejection too; the queue can retry after its visibility delay.
            uncertain = exc.status is None or exc.status >= 500
            raise DispatchError(str(exc), uncertain=uncertain) from None
