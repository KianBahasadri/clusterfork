"""Fixed read operations against trusted Linear and GitHub adapters."""

import base64
import hashlib
import json
import re

from common.context import ContextError, MAX_PAGES, MAX_SOURCE_BYTES, safe_path
from common.routing import DispatchError


def connection_pages(fetch, check):
    rows, cursors, cursor = [], set(), None
    for _ in range(MAX_PAGES):
        check()
        result = fetch(cursor)
        check()
        if not isinstance(result, dict) or not isinstance(result.get("nodes"), list):
            raise ContextError("incomplete")
        page = result.get("pageInfo") or {}
        if type(page.get("hasNextPage")) is not bool:
            raise ContextError("incomplete")
        rows.extend(result["nodes"])
        if len(json.dumps(rows).encode()) > MAX_SOURCE_BYTES:
            raise ContextError("oversized")
        if not page["hasNextPage"]:
            ids = [row["id"] for row in rows]
            if len(ids) != len(set(ids)):
                raise ContextError("incomplete")
            return rows
        cursor = page.get("endCursor")
        if not isinstance(cursor, str) or not cursor or cursor in cursors:
            raise ContextError("incomplete")
        cursors.add(cursor)
    raise ContextError("incomplete")


def linear_issue(linear, target, check):
    check()
    result = linear.query("""query($id: String!) { issue(id: $id) {
      id identifier title description url updatedAt archivedAt trashed
      project { id name } state { id name type }
    } }""", {"id": target})["issue"]
    check()
    if result is None:
        raise ContextError("missing")
    if (not isinstance(result, dict) or not all(result.get(k) for k in ("id", "identifier", "title", "url", "updatedAt"))
            or not (result.get("state") or {}).get("type")):
        raise ContextError("incomplete")

    def comments(cursor):
        return linear.query("""query($id: ID!, $after: String) {
          comments(first: 50, after: $after, filter: {issue: {id: {eq: $id}}}) {
            nodes { id body createdAt updatedAt user { id } botActor { id } parent { id } }
            pageInfo { hasNextPage endCursor }
          }
        }""", {"id": result["id"], "after": cursor})["comments"]

    def related(field, fields, cursor):
        archived = ", includeArchived: true" if field == "inverseRelations" else ""
        issue = linear.query("query($id: String!, $after: String) { issue(id: $id) { "
                             + field + "(first: 50, after: $after" + archived + ") { nodes { " + fields
                             + " } pageInfo { hasNextPage endCursor } } } }",
                             {"id": result["id"], "after": cursor})["issue"]
        if not isinstance(issue, dict):
            raise ContextError("incomplete")
        return issue[field]

    result = {**result,
              "comments": sorted(connection_pages(comments, check), key=lambda c: (c["createdAt"], c["id"])),
              "dependencies": connection_pages(lambda c: related("inverseRelations", "id type issue { id identifier }", c), check),
              "attachments": connection_pages(lambda c: related("attachments", "id title url", c), check)}
    # Detect an issue edit while its paginated discussion was being collected.
    check()
    end = linear.query("query($id: String!) { issue(id: $id) { id updatedAt } }", {"id": result["id"]})["issue"]
    check()
    if not end or end.get("id") != result["id"] or end.get("updatedAt") != result["updatedAt"]:
        raise ContextError("incomplete")
    return result


class Repository:
    """Only a referenced PR may mint an immutable repository browsing scope."""
    def __init__(self, github, repository, check):
        self.github, self.repository, self.check = github, repository, check
        check()
        try:
            self.token = github.installation_token(repository, permissions={"contents": "read", "pull_requests": "read"})
        except DispatchError:
            check()
            raise ContextError("inaccessible") from None
        check()

    def get(self, path):
        self.check()
        result = self.github.request(f"https://api.github.com/repos/{self.repository}/{path}", token=f"Bearer {self.token}")
        self.check()
        return result

    def pull(self, number):
        pull = self.get(f"pulls/{number}")
        if not isinstance(pull, dict):
            raise ContextError("missing")
        base = (pull.get("base") or {}).get("repo") or {}
        expected = f"https://github.com/{self.repository}/pull/{number}"
        if (pull.get("number") != number or base.get("full_name", "").lower() != self.repository
                or pull.get("html_url", "").lower() != expected or not base.get("id")
                or pull.get("state") not in {"open", "closed"} or type(pull.get("merged")) is not bool
                or bool(pull.get("merged_at")) != pull["merged"]):
            raise ContextError("identity_mismatch")
        commit = pull.get("merge_commit_sha") if pull["merged"] else (pull.get("head") or {}).get("sha")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ContextError("incomplete")
        committed = self.get(f"git/commits/{commit}")
        if not isinstance(committed, dict):
            raise ContextError("incomplete")
        tree = (committed.get("tree") or {}).get("sha", "")
        if committed.get("sha") != commit or not re.fullmatch(r"[0-9a-f]{40}", tree):
            raise ContextError("identity_mismatch")
        files = []
        for page in range(1, MAX_PAGES + 1):
            rows = self.get(f"pulls/{number}/files?per_page=100&page={page}")
            if not isinstance(rows, list):
                raise ContextError("incomplete")
            for row in rows:
                files.append({k: row.get(k) for k in ("filename", "previous_filename", "status", "sha")})
            if len(rows) < 100:
                break
        else:
            raise ContextError("incomplete")
        if type(pull.get("changed_files")) is not int or len(files) != pull["changed_files"]:
            raise ContextError("incomplete")
        if len({f["filename"] for f in files}) != len(files) or any(not f["filename"] for f in files):
            raise ContextError("incomplete")
        return {"repository": self.repository, "repository_id": base["id"], "number": number, "url": expected,
                "state": pull["state"], "merged": pull["merged"], "merged_at": pull["merged_at"],
                "title": pull.get("title"), "body": pull.get("body"), "updated_at": pull.get("updated_at"),
                "head_sha": (pull.get("head") or {}).get("sha"), "commit": commit, "tree": tree, "files": files}

    def entries(self, tree, recursive=False):
        result = self.get(f"git/trees/{tree}" + ("?recursive=1" if recursive else ""))
        if (not isinstance(result, dict) or result.get("sha") != tree or result.get("truncated") is not False
                or not isinstance(result.get("tree"), list)):
            raise ContextError("incomplete")
        for entry in result["tree"]:
            if (not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or not entry["path"]
                    or not re.fullmatch(r"[0-9a-f]{40}", entry.get("sha", ""))
                    or entry.get("type") not in {"tree", "blob", "commit"}):
                raise ContextError("incomplete")
        return result["tree"]

    def locate(self, tree, path):
        entry = {"type": "tree", "sha": tree}
        for component in path.split("/") if path else []:
            if entry["type"] != "tree":
                raise ContextError("missing")
            entry = next((row for row in self.entries(entry["sha"]) if row.get("path") == component), None)
            if entry is None:
                raise ContextError("missing")
            if entry.get("mode") in {"120000", "160000"}:
                raise ContextError("unsupported")
        return entry

    def blob(self, entry):
        if entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}:
            raise ContextError("unsupported")
        if type(entry.get("size")) is not int or not 0 <= entry["size"] <= MAX_SOURCE_BYTES:
            raise ContextError("oversized")
        result = self.get(f"git/blobs/{entry['sha']}")
        if not isinstance(result, dict) or result.get("encoding") != "base64" or result.get("sha") != entry["sha"]:
            raise ContextError("incomplete")
        raw = base64.b64decode("".join(result["content"].split()), validate=True)
        if len(raw) != entry["size"] or len(raw) > MAX_SOURCE_BYTES:
            raise ContextError("incomplete")
        if hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() != entry["sha"]:
            raise ContextError("identity_mismatch")
        try:
            text = raw.decode("utf-8")
        except UnicodeError:
            raise ContextError("unsupported") from None
        if "\0" in text:
            raise ContextError("unsupported")
        return text

    def read(self, pull, operation, path, query=""):
        entry = self.locate(pull["tree"], path)
        if operation == "file":
            return self.blob(entry)
        if operation == "tree":
            if entry["type"] != "tree":
                raise ContextError("unsupported")
            return {"path": path, "entries": self.entries(entry["sha"])}
        entries = self.entries(entry["sha"], recursive=True) if entry["type"] == "tree" else [{**entry, "path": ""}]
        blobs = [e for e in entries if e.get("type") == "blob"]
        if len(blobs) > 50 or sum(e.get("size", MAX_SOURCE_BYTES) for e in blobs) > MAX_SOURCE_BYTES:
            raise ContextError("oversized")
        matches = []
        excluded = [{"path": "/".join(p for p in (path, e["path"]) if p), "reason": "submodule"}
                    for e in entries if e.get("type") == "commit"]
        for blob in blobs:
            name = "/".join(p for p in (path, blob["path"]) if p)
            try:
                safe_path(name)
                text = self.blob(blob)
            except ContextError as exc:
                if exc.status not in {"unsupported", "denied"}:
                    raise
                excluded.append({"path": name, "reason": exc.status})
                continue
            matches.extend({"path": name, "line": i, "text": line} for i, line in enumerate(text.splitlines(), 1) if query in line)
        return {"matches": matches, "excluded": excluded, "scope": "UTF-8 regular files only"}
