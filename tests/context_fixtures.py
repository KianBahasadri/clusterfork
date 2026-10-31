"""Two projects/repositories and paginated Linear/GitHub read adapters."""

import base64
import copy
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from common.clients import Linear, RequestError
from controller.model import IssueObserved, Role
import test_controller_broker as fixtures
from call_budget import record_api_calls

DEPENDENCY = "11111111-1111-4111-8111-111111111111"
COMMIT, TREE, DOC_TREE = "c" * 40, "d" * 40, "e" * 40
CONTRACT = "Account identity: stable account_id.\nLocal availability: local_available boolean.\nUsage: read-only cached observations.\n"
BLOB = hashlib.sha1(f"blob {len(CONTRACT.encode())}\0".encode() + CONTRACT.encode()).hexdigest()


class ContextFixture:
    def __init__(self, test, *, provider="codex"):
        self.f = fixtures.ControllerBrokerTests(methodName="runTest")
        self.f.setUp()
        test.addCleanup(self.f.doCleanups)
        if provider == "cursor":
            from test_cursor_accounts import auth
            self.f.pool = self.f.accounts.pools[provider]
            self.f.account_a = "cursor:" + self.f.pool.enroll(auth("reviewer"))
            self.f.account_b = "cursor:" + self.f.pool.enroll(auth("coder"))
        self.repo = next(r.repository for r in self.f.registry.routes.values() if r.repository != self.f.route.repository).lower()
        self.pull = f"https://github.com/{self.repo}/pull/55"
        self.root = {**self.f.issue, "updatedAt": "2026-10-31T00:00:00Z", "state": {"id": "todo", "type": "unstarted", "name": "Todo"}}
        self.root["project"] = {**self.root["project"], "labels": {"nodes": [], "pageInfo": {"hasNextPage": False}}}
        self.dependency = {"id": DEPENDENCY, "identifier": "KIAN-941", "title": "Account contract",
                           "description": "Published contract: " + self.pull, "url": "https://linear.app/test/issue/KIAN-941/contract",
                           "project": {"id": "other-project", "name": "Other project"}, "updatedAt": "2026-10-31T01:00:00Z",
                           "state": {"id": "done", "type": "completed", "name": "Done"}, "archivedAt": None, "trashed": False}
        self.comments = {self.root["id"]: [], DEPENDENCY: [
            {"id": f"comment-{i}", "body": "Delivery notes " + str(i), "createdAt": f"2026-10-31T01:{i:02d}:00Z",
             "updatedAt": "version-1", "user": {"id": "human"}, "parent": {"id": "comment-0"} if i else None, "botActor": None}
            for i in range(51)]}
        self.attachments = {self.root["id"]: [], DEPENDENCY: [{"id": "delivery", "title": "Merged contract", "url": self.pull}]}
        self.dependencies = {self.root["id"]: [{"id": "edge", "type": "blocks", "issue": {"id": DEPENDENCY, "identifier": "KIAN-941"}}], DEPENDENCY: []}
        self.pull_data = {"number": 55, "base": {"repo": {"id": 44, "full_name": self.repo}}, "html_url": self.pull,
                          "state": "closed", "merged": True, "merged_at": "2026-10-31T02:00:00Z", "merge_commit_sha": COMMIT,
                          "head": {"sha": "f" * 40}, "title": "Account contract", "body": "See docs/account.md", "changed_files": 101,
                          "updated_at": "version-1"}
        self.files = [{"filename": f"src/file-{i}.py", "status": "modified", "sha": "f" * 40} for i in range(100)]
        self.files.append({"filename": "docs/account.md", "status": "added", "sha": BLOB})
        self.calls, self.gh_calls = [], []
        self.failed = {}
        self.after_query = None
        self.truncate_tree = False
        self.f.linear = Linear("lin_api_CONTROLLER_ONLY", request=self.linear_request)
        record_api_calls(self.f.linear)
        self.f.broker.linear = self.f.broker.context.linear = self.f.linear
        self.f.github.installation_token.return_value = "ghs_CONTROLLER_ONLY"
        self.f.github.request.side_effect = self.github_request
        self.f.broker.context.github = self.f.github

    def call(self, role, action, **data):
        return self.f.call(role, action, **data)

    def start_review(self):
        self.f.preflight(Role.REVIEW_SELECT, self.f.account_a)
        config = self.call(Role.REVIEW, "start", protocol=fixtures.PROTOCOL)
        self.call(Role.REVIEW, "claim", account_id=self.f.account_a)
        return config

    def approve(self, config):
        return self.call(Role.REVIEW, "review", binding=config["binding"], result=fixtures.ready())

    def start_coding(self):
        self.f.preflight(Role.CODING_SELECT, self.f.account_b)
        config = self.call(Role.CODING, "start", protocol=fixtures.PROTOCOL)
        self.call(Role.CODING, "claim", account_id=self.f.account_b)
        self.call(Role.CODING, "progress", stage="coding", context_sha=self.f.spec.context_sha,
                  account_id=self.f.account_b, usage_before={"observed_at": 1000, "windows": [
                      {"name": "primary", "used_percent": 10, "resets_at": 2000, "duration_minutes": 300}]})
        return config

    def add_prerequisite(self):
        self.dependencies[self.root["id"]].append({"id": "new-edge", "type": "blocks", "issue": {
            "id": "22222222-2222-4222-8222-222222222222", "identifier": "KIAN-947"}})
        return self.f.controller.apply(IssueObserved(self.root["id"], self.f.route.project_id,
            self.f.route.repository, self.f.registry.generation, self.root["identifier"], True, False,
            content_revision=self.f.spec.content_revision, reason="Unfinished prerequisite."), now=self.f.broker.clock())

    @staticmethod
    def page(rows, after):
        index = int(after or 0)
        nodes = rows[index:index + 50]
        more = index + 50 < len(rows)
        return {"nodes": copy.deepcopy(nodes), "pageInfo": {"hasNextPage": more, "endCursor": str(index + 50) if more else None}}

    def linear_request(self, url, *, token=None, method="GET", data=None, **kwargs):
        assert url == "https://api.linear.app/graphql" and method == "POST"
        assert token == "lin_api_CONTROLLER_ONLY"
        query, variables = data["query"], data["variables"]
        assert not query.lstrip().startswith("mutation")
        target = variables.get("id")
        self.calls.append((query, copy.deepcopy(variables)))
        if query == "query { viewer { id } }":
            return {"data": {"viewer": {"id": "bot"}}}
        if target in self.failed:
            raise RequestError(self.failed[target])
        issue = self.root if target in {self.root["id"], self.root["identifier"]} else self.dependency if target in {DEPENDENCY, "KIAN-941"} else None
        if "comments(first:" in query:
            result = {"comments": self.page(self.comments.get(target, []), variables.get("after"))}
        elif "inverseRelations(first:" in query:
            result = {"issue": {"inverseRelations": self.page(self.dependencies[issue["id"]], variables.get("after"))}}
        elif "attachments(first:" in query:
            result = {"issue": {"attachments": self.page(self.attachments[issue["id"]], variables.get("after"))}}
        else:
            result = {"issue": copy.deepcopy(issue)}
            if issue and "project { id name }" in query:
                result["issue"]["project"] = {k: issue["project"][k] for k in ("id", "name") if k in issue["project"]}
        if self.after_query:
            self.after_query(query, variables)
        return {"data": result}

    def github_request(self, url, *, token=None, method="GET", data=None):
        assert method == "GET" and data is None
        assert token == "Bearer ghs_CONTROLLER_ONLY"
        self.gh_calls.append(url)
        parsed = urlsplit(url)
        path = parsed.path
        if path in self.failed:
            raise RequestError(self.failed[path])
        if "/actions/runs/" in path:
            return {"total_count": 1, "jobs": [{"name": "review / run", "status": "completed", "conclusion": "success"}]}
        assert path.startswith(f"/repos/{self.repo}/")
        suffix = path.removeprefix(f"/repos/{self.repo}/")
        if suffix == "pulls/55":
            return copy.deepcopy(self.pull_data)
        if suffix == "pulls/55/files":
            page = int(parse_qs(parsed.query)["page"][0])
            return copy.deepcopy(self.files[(page - 1) * 100:page * 100])
        if suffix.startswith("git/commits/"):
            return {"sha": suffix.rsplit("/", 1)[1], "tree": {"sha": TREE}}
        if suffix == "git/trees/" + TREE:
            return {"sha": TREE, "truncated": self.truncate_tree, "tree": [{"path": "docs", "type": "tree", "mode": "040000", "sha": DOC_TREE}]}
        if suffix == "git/trees/" + DOC_TREE:
            return {"sha": DOC_TREE, "truncated": self.truncate_tree,
                    "tree": [{"path": "account.md", "type": "blob", "mode": "100644", "sha": BLOB, "size": len(CONTRACT.encode())}]}
        if suffix == "git/blobs/" + BLOB:
            return {"sha": BLOB, "encoding": "base64", "content": base64.b64encode(CONTRACT.encode()).decode()}
        raise AssertionError("Unexpected fixture read " + suffix)

    def read(self, operation, *, role=Role.REVIEW, **arguments):
        return self.call(role, "context", request={"operation": operation, **arguments})

    def complete(self, operation, *, role=Role.REVIEW, **arguments):
        response = self.read(operation, role=role, **arguments)
        content = response.get("content", "")
        while response.get("next_cursor"):
            response = self.read(operation, role=role, cursor=response["next_cursor"], **arguments)
            content += response.get("content", "")
        if response["status"] != "ok" or not response["complete"]:
            raise AssertionError(response)
        return json.loads(content)

    def contract(self, role=Role.REVIEW):
        related = self.complete("list", role=role)
        assert "KIAN-941" in related["issues"]
        self.complete("issue", target="KIAN-941", role=role)
        self.complete("pull", target=self.pull, role=role)
        return self.complete("file", target=self.pull, path="docs/account.md", role=role)
