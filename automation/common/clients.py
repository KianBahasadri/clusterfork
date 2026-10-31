"""Linear and GitHub App clients shared by the controller and runners."""

from __future__ import annotations

import json
import hashlib
import logging
import math
import time
from functools import lru_cache
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from common.routing import DispatchError
from common.reporting import EXECUTION_STATUS_LABELS, worker_branch
from common.diagnostics import failure, graphql_failure, http_operation, operation, record


class RequestError(RuntimeError):
    def __init__(self, status=None):
        self.status = status
        # Never include response bodies, request headers, or credential values.
        super().__init__(f"remote request failed (HTTP {status or 'unavailable'})")


def request_json(url, *, token=None, method="GET", data=None, form=False):
    headers = {"Accept": "application/json", "User-Agent": "clusterfork",
               "Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
    if token:
        headers["Authorization"] = token
    if url.startswith("https://api.github.com/"):
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    body = None if data is None else (urlencode(data) if form else json.dumps(data)).encode()
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
id identifier title description url archivedAt createdAt priority sortOrder
state { id type }
team { id }
labels(first: 2, filter: {name: {eq: "clusterfork: auto-repair"}}) { nodes { name } pageInfo { hasNextPage } }
"""
PROJECT_FIELDS = "id archivedAt labels(first: 100) { nodes { name } pageInfo { hasNextPage } }"


class Linear:
    def __init__(self, token=None, request=request_json, *, client_id=None, client_secret=None,
                 clock=time.monotonic):
        self.token = token
        self.request = request
        self.client_id, self.client_secret = client_id, client_secret
        self.clock = clock
        self._access_token, self._refresh_at = None, 0
        self._token_lock = Lock()

    def _authorization(self):
        if not (self.client_id and self.client_secret):
            if not self.token:
                raise RequestError()
            return self.token
        with self._token_lock:
            if not self._access_token or self.clock() >= self._refresh_at:
                self._access_token = None
                issued_at = self.clock()
                result = self.request("https://api.linear.app/oauth/token", method="POST", form=True,
                                      data={"grant_type": "client_credentials", "client_id": self.client_id,
                                            "client_secret": self.client_secret,
                                            "scope": "read,write,comments:create,issues:create"})
                if not isinstance(result, dict):
                    raise RequestError()
                token, expires_in = result.get("access_token"), result.get("expires_in")
                if (not isinstance(token, str) or not token or any(char.isspace() for char in token)
                        or type(expires_in) not in (int, float) or not math.isfinite(expires_in)
                        or expires_in <= 0):
                    raise RequestError()
                self._access_token = token
                # Short-lived tokens still get reused, with half their lifetime reserved.
                self._refresh_at = issued_at + expires_in - min(300, expires_in / 2)
            return f"Bearer {self._access_token}"

    def _clear_token(self, authorization):
        with self._token_lock:
            # Another request may already have replaced the rejected token.
            if authorization == f"Bearer {self._access_token}":
                self._access_token = None

    def query(self, query, variables):
        for attempt in range(2):
            authorization = self._authorization()
            try:
                result = self.request("https://api.linear.app/graphql", token=authorization,
                                      method="POST", data={"query": query, "variables": variables})
            except RequestError as exc:
                if exc.status != 401 or not (self.client_id and self.client_secret):
                    raise
                self._clear_token(authorization)
                if attempt:
                    raise
            else:
                break
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict) or result.get("errors"):
            self._graphql_rejected(query, "errors" if isinstance(result, dict) and result.get("errors")
                                   else "invalid_response", result.get("errors") if isinstance(result, dict) else None)
        if query.lstrip().startswith("mutation") and (not result["data"] or any(
                not isinstance(payload, dict) or payload.get("success") is not True
                for payload in result["data"].values())):
            self._graphql_rejected(query, "unsuccessful_mutation")
        return result["data"]

    @staticmethod
    def _graphql_rejected(query, reason, errors=None):
        error = RequestError()
        error.clusterfork_failure = graphql_failure(
            http_operation("https://api.linear.app/graphql", {"query": query}), reason, errors)
        # HTTP completion alone says nothing about GraphQL success.
        record("linear_graphql_rejected", level=logging.ERROR, **error.clusterfork_failure)
        raise error

    @staticmethod
    def complete(issue):
        if issue and issue.get("labels", {}).get("pageInfo", {}).get("hasNextPage"):
            raise ValueError("Incomplete issue labels; cannot evaluate auto-repair guard")
        if issue and issue.get("project"):
            if issue["project"]["labels"]["pageInfo"]["hasNextPage"]:
                raise ValueError("project has more than 100 labels; cannot evaluate enrollment")
        return issue

    def issue(self, issue_id):
        query = ("query($id: String!) { issue(id: $id) { " + ISSUE_FIELDS
                 + " project { " + PROJECT_FIELDS + " } } }")
        return self.complete(self.query(query, {"id": issue_id})["issue"])

    def download_image(self, url, *, deadline=None):
        from common.images import download_image

        return download_image(url, self._authorization, deadline=deadline)

    def create_issue(self, fields):
        """A caller-supplied UUID makes ambiguous creates recoverable without duplicates."""
        try:
            result = self.query("""mutation($input: IssueCreateInput!) {
              issueCreate(input: $input) { success issue { id } }
            }""", {"input": fields})["issueCreate"]
            if not result["success"] or (result.get("issue") or {}).get("id") != fields["id"]:
                raise RequestError()
        except RequestError:
            # Includes a conflicting create and a successful create whose response
            # was lost. Never generate a replacement ID on a retry.
            existing = self.issue(fields["id"])
            if not existing:
                raise
            return existing
        return self.issue(fields["id"])

    def issue_dependencies(self, issue_id):
        """Read every incoming relation, including sources outside mapped projects.

        For A blocks B, B.inverseRelations contains a `blocks` edge whose
        `issue` is A. Never use B.relations (its outgoing edges) for this gate.
        Return only a complete snapshot; partial/error responses cannot release
        a dependency. Archived sources must not disappear from this check.
        """
        rows, seen, cursor = [], set(), None
        while True:
            issue = self.query("""query($id: String!, $after: String) {
              issue(id: $id) {
                inverseRelations(first: 50, after: $after, includeArchived: true) {
                  nodes { id type issue { id identifier trashed state { type } } }
                  pageInfo { hasNextPage endCursor }
                }
              }
            }""", {"id": issue_id, "after": cursor})["issue"]
            if not isinstance(issue, dict):
                raise ValueError("Cannot verify incoming issue dependencies")
            connection = issue["inverseRelations"]
            nodes, page = connection["nodes"], connection["pageInfo"]
            if not isinstance(nodes, list) or type(page["hasNextPage"]) is not bool:
                raise ValueError("Incomplete incoming issue dependencies")
            rows.extend(nodes)
            if not page["hasNextPage"]:
                return rows
            cursor = page["endCursor"]
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise ValueError("Linear dependency pagination did not advance")
            seen.add(cursor)

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

    def set_execution_status(self, issue_id, name):
        """Replace only owned status indicators, removing conflicts before adding."""
        if name is not None and name not in EXECUTION_STATUS_LABELS:
            raise ValueError("Invalid execution status label")
        issue = self.query("""query($id: String!) {
          issue(id: $id) { team { id } labels(first: 100) {
            nodes { id name } pageInfo { hasNextPage }
          } }
        }""", {"id": issue_id})["issue"]
        if issue["labels"]["pageInfo"]["hasNextPage"]:
            raise ValueError("Incomplete issue labels")
        labels = issue["labels"]["nodes"]
        retained = False
        for label in labels:
            if label["name"] not in EXECUTION_STATUS_LABELS:
                continue
            if label["name"] == name and not retained:
                retained = True
                continue
            result = self.query("""mutation($id: String!, $label: String!) {
              issueRemoveLabel(id: $id, labelId: $label) { success }
            }""", {"id": issue_id, "label": label["id"]})["issueRemoveLabel"]
            if not result["success"]:
                raise RequestError()
        if name and not retained:
            identifier = self.stage_label(issue["team"]["id"], name)
            result = self.query("""mutation($id: String!, $label: String!) {
              issueAddLabel(id: $id, labelId: $label) { success }
            }""", {"id": issue_id, "label": identifier})["issueAddLabel"]
            if not result["success"]:
                raise RequestError()

    def status_comment(self, issue_id, comment_id, body):
        viewer = self.viewer_id()
        # Encode the migration in the ID itself: no process-local pointer, token,
        # random replacement ID, or Azure write after creation is needed for recovery.
        digest = hashlib.sha256(f"clusterfork:comment:{comment_id}:{viewer}".encode()).digest()
        replacement = str(UUID(bytes=digest[:16], version=4))

        def lookup():
            result = self.query("""query($issue: String!, $comments: [ID!]!) {
              issue(id: $issue) { comments(first: 2, filter: {id: {in: $comments}}) {
                nodes { id body user { id } }
              } }
            }""", {"issue": issue_id, "comments": [comment_id, replacement]})
            return {comment["id"]: comment for comment in result["issue"]["comments"]["nodes"]}

        comments = lookup()
        legacy = comments.get(comment_id)
        legacy_author = (legacy.get("user") or {}).get("id") if legacy else None
        migrate = bool(legacy and legacy_author != viewer)
        identifier = replacement if replacement in comments or migrate else comment_id
        recovery = "author_changed" if legacy_author else "author_unavailable"

        def write(identifier):
            nonlocal comments
            comment = comments.get(identifier)
            if (identifier == comment_id and comment
                    and (comment.get("user") or {}).get("id") != viewer):
                return write(replacement)
            if comment and comment["body"] == body:
                return identifier
            if comment:
                self.query("""mutation($id: String!, $body: String!) {
                  commentUpdate(id: $id, input: {body: $body}) { success }
                }""", {"id": identifier, "body": body})
            else:
                try:
                    self.query("""mutation($id: String!, $issue: String!, $body: String!) {
                      commentCreate(input: {id: $id, issueId: $issue, body: $body, doNotSubscribeToIssue: true}) { success }
                    }""", {"id": identifier, "issue": issue_id, "body": body})
                except RequestError as exc:
                    # A competing handler may have committed this deterministic
                    # ID, or our own successful response may have been lost.
                    # Prove existence before updating; never choose another ID.
                    try:
                        observed = lookup()
                    except Exception:
                        raise exc
                    if identifier not in observed:
                        raise
                    comments = observed
                    record("linear_comment_create_recovered", operation="linear/commentCreate",
                           **{k: v for k, v in failure(exc, "linear/commentCreate").items() if k != "operation"})
                    if comments[identifier]["body"] == body:
                        return identifier
                    return write(identifier)
            return identifier

        try:
            identifier = write(identifier)
        except RequestError as exc:
            details = getattr(exc, "clusterfork_failure", {})
            if (identifier != comment_id or comment_id not in comments or not (
                    exc.status == 403 or details.get("graphql_code") == "FORBIDDEN"
                    or details.get("graphql_type") == "forbidden")):
                raise
            # Some integrations omit or share user metadata. A definite update
            # denial can still migrate once; transient failures never create a fork.
            identifier = write(replacement)
            recovery = "update_forbidden"
        if identifier == replacement and replacement not in comments:
            record("linear_comment_identity_recovered", operation="linear/commentCreate", reason=recovery)


class GitHub:
    def __init__(self, app_id, private_key, request=request_json):
        self.app_id, self.private_key, self.request = app_id, private_key, request

    def _installation(self, repository):
        import jwt

        now = int(time.time())
        try:
            signed = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": self.app_id},
                                self.private_key, algorithm="RS256")
            installation = self.request(
                f"https://api.github.com/repos/{repository}/installation", token=f"Bearer {signed}")
            return signed, installation
        except Exception:
            raise DispatchError("GitHub App authentication failed; check its ID, key and installation") from None

    def workflow_changes_allowed(self, repository):
        _, installation = self._installation(repository)
        return installation.get("permissions", {}).get("workflows") == "write"

    def installation_token(self, repository, *, permissions=None):
        try:
            signed, installation = self._installation(repository)
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

    def verify_worker_run(self, repository, claims, dispatch_key, *, workflow="clusterfork-task.yml"):
        from common.accounts import WorkerError
        token = self.installation_token(repository, permissions={"actions": "read"})
        run = self.request(f"https://api.github.com/repos/{repository}/actions/runs/{claims['run_id']}",
                           token=f"Bearer {token}")
        if (run.get("event") != "workflow_dispatch" or run.get("head_sha") != claims["sha"]
                or str(run.get("run_attempt")) != str(claims["run_attempt"])
                or str(run.get("repository", {}).get("id")) != str(claims["repository_id"])
                or run.get("head_repository", {}).get("full_name") != repository
                or run.get("path") != f".github/workflows/{workflow}"
                or dispatch_key not in run.get("display_title", "")
                or run.get("actor", {}).get("login") != "clusterfork[bot]"):
            raise WorkerError("GitHub run does not match the dispatch", 403)



    def worker_result(self, repository, owner, *, latest=False):
        run_id, attempt = owner.split(":")
        if not run_id.isdigit() or not attempt.isdigit():
            raise ValueError("Invalid stored worker identity")
        token = self.installation_token(repository, permissions={"actions": "read"})
        suffix = "" if latest else f"/attempts/{attempt}"
        result = self.request(f"https://api.github.com/repos/{repository}/actions/runs/{run_id}{suffix}",
                              token=f"Bearer {token}")
        if latest and (not isinstance(result, dict) or type(result.get("id")) is not int or result["id"] != int(run_id)
                       or type(result.get("run_attempt")) is not int or result["run_attempt"] < int(attempt)
                       or not isinstance(result.get("status"), str)
                       or result.get("status") not in {"queued", "in_progress", "completed", "waiting", "requested", "pending"}):
            raise RequestError()
        return result

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
        target_key = receipt.get("target_dispatch_key", key)
        branch = receipt.get("target_branch") or worker_branch(receipt["identifier"], key)
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
                    ... on CheckRun { name status conclusion detailsUrl isRequired(pullRequestNumber: $number) }
                    ... on StatusContext { context state targetUrl isRequired(pullRequestNumber: $number) }
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
                or f"<!-- clusterfork-dispatch:{target_key} -->" not in (pull.get("body") or "")):
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
            error = DispatchError(str(exc), uncertain=uncertain)
            error.clusterfork_failure = getattr(exc, 'clusterfork_failure', None) or (
                {'operation': 'github/dispatch', 'kind': 'http_error', 'http_status': exc.status}
                if exc.status is not None else failure(exc, 'github/dispatch'))
            raise error from None
