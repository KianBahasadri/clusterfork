"""Linear, GitHub App, and durable Azure receipt adapters for the routing pilot."""

from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from dispatch_core import DispatchError


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
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except HTTPError as exc:
        raise RequestError(exc.code) from None
    except (URLError, OSError, ValueError):
        raise RequestError() from None


ISSUE_FIELDS = """
id identifier archivedAt
state { id }
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


class GitHub:
    def __init__(self, app_id, private_key, request=request_json):
        self.app_id, self.private_key, self.request = app_id, private_key, request

    def installation_token(self, repository):
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
                      "permissions": {"actions": "write"}})
            return result["token"]
        except Exception:
            # No workflow dispatch has been attempted yet, so retry is safe.
            raise DispatchError("GitHub App authentication failed; check its ID, key and installation") from None

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


class AzureReceipts:
    def __init__(self, connection):
        from azure.data.tables import TableServiceClient

        self.table = TableServiceClient.from_connection_string(connection).create_table_if_not_exists(
            "ClusterforkDispatches")

    def claim(self, key, fields):
        from azure.core.exceptions import ResourceExistsError

        try:
            self.table.create_entity({"PartitionKey": "pilot", "RowKey": key,
                                      "status": "dispatching", "created_at": int(time.time()), **fields})
            return True
        except ResourceExistsError:
            return False

    def finish(self, key, status):
        self.table.update_entity({"PartitionKey": "pilot", "RowKey": key, "status": status})

    def release(self, key):
        self.table.delete_entity("pilot", key)
