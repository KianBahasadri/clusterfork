"""Task-scoped context snapshots, delivery receipts and approval revalidation."""

import base64
import hashlib
import json
import threading
import time

from common.clients import RequestError, context_read_scope
from common.context import (ContextError, FETCH_SECONDS, MAX_REMOTE_CALLS, MAX_SOURCE_BYTES,
                            MAX_SOURCES, MAX_TASK_BYTES, PAGE_CHARS, digest, pull_ref,
                            references, validate_request)
from common.context_sources import Repository, linear_issue
from common.policy import issue_revision, redact_review_text, redact_review_value
from common.publication import comment_id
from .model import Rejected, dependency_admission_applies


def source_text(content):
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=True, sort_keys=True, indent=2)


def rows(db, attempt):
    return [dict(r) for r in db.execute("SELECT * FROM context_sources WHERE attempt_id=? ORDER BY source_key", (attempt,))]


def revision(db, attempt):
    return digest([[r["source_key"], r["material"], r["status"], r["required"], r["read_until"]] for r in rows(db, attempt)])


def seal(db, attempt, expected):
    sources = rows(db, attempt)
    actual = revision(db, attempt)
    if expected != actual and (sources or expected):
        raise Rejected("Retrieved evidence changed while accepting review")
    for row in sources:
        if row["required"] and (row["status"] != "ok" or row["read_until"] < len(source_text(json.loads(row["content"])))):
            raise Rejected("Required related context is unavailable or incompletely read; review must report missing_evidence")
    manifest = [{"key": r["source_key"], "material": r["material"]} for r in sources]
    db.execute("INSERT INTO context_approvals VALUES (?,?,?) ON CONFLICT(attempt_id) DO UPDATE SET "
               "revision=excluded.revision,manifest=excluded.manifest", (attempt, actual, json.dumps(manifest)))
    db.execute("UPDATE context_sources SET reviewed=1 WHERE attempt_id=?", (attempt,))


class Context:
    def __init__(self, database, linear, github, *, monotonic=time.monotonic):
        self.database, self.linear, self.github = database, linear, github
        self.monotonic = monotonic
        self._reporting_actor = None
        # Serializing retrieval and sealing prevents a concurrent read escaping
        # the approved manifest. No network operation holds a SQLite transaction.
        self._locks, self._locks_guard = {}, threading.Lock()

    def lock_for(self, view):
        with self._locks_guard:
            return self._locks.setdefault(view.attempt["attempt_id"], threading.RLock())

    def sources(self, view):
        with self.database.transaction() as db:
            return rows(db, view.attempt["attempt_id"])

    def check_budget(self, view, check):
        check()
        attempt = view.attempt["attempt_id"]
        with self.database.transaction() as db:
            db.execute("INSERT OR IGNORE INTO context_budgets(attempt_id) VALUES (?)", (attempt,))
            count = db.execute("SELECT calls FROM context_budgets WHERE attempt_id=?", (attempt,)).fetchone()[0]
            if count >= MAX_REMOTE_CALLS:
                raise ContextError("budget_exceeded")
            db.execute("UPDATE context_budgets SET calls=calls+1 WHERE attempt_id=?", (attempt,))

    def bounded(self, check):
        deadline = self.monotonic() + FETCH_SECONDS

        def bounded():
            check()
            if self.monotonic() >= deadline:
                raise ContextError("timeout")
        return bounded

    def reporting_ids(self, issue):
        """Exclude our own deterministic reports, including author-migrated IDs.

        Human decisions/replies remain material even when replying to a report.
        Never trust a prefix in arbitrary source text to exempt a comment.
        """
        ids = set()
        with self.database.transaction() as db:
            tasks = db.execute("SELECT task_id FROM tasks WHERE issue_id=?", (issue["id"],)).fetchall()
            for task in tasks:
                ids.add(comment_id(task[0], "controller-status"))
                ids.add(comment_id(task[0], "auto-repair-report"))
                for attempt in db.execute("SELECT * FROM attempts WHERE task_id=?", (task[0],)):
                    ids.add(comment_id(attempt["attempt_id"], "controller-review"))
                    for kind in ("start", "finish"):
                        ids.add(comment_id(attempt["attempt_id"], f"{attempt['run_id']}:{attempt['run_attempt']}:{kind}"))
        from uuid import UUID
        migrated = set()
        for comment in issue.get("comments", []):
            user = (comment.get("user") or {}).get("id")
            if user and user == self._reporting_actor:
                for original in ids:
                    raw = hashlib.sha256(f"clusterfork:comment:{original}:{user}".encode()).digest()
                    migrated.add(str(UUID(bytes=raw[:16], version=4)))
        owned = {c["id"] for c in issue.get("comments", []) if (c.get("user") or {}).get("id") == self._reporting_actor}
        return (ids | migrated) & owned

    def material(self, view, request, content, *, original=None):
        if request["operation"] == "issue":
            ignore = self.reporting_ids(content)
            value = {k: content.get(k) for k in ("id", "identifier", "title", "description", "project", "archivedAt", "trashed")}
            if content["id"] != view.issue_id:
                value["state"] = content["state"]
            value["comments"] = [{k: c.get(k) for k in ("id", "body", "parent", "user", "botActor")}
                                 for c in content["comments"] if c["id"] not in ignore]
            dependencies = content["dependencies"]
            if original is not None and content["id"] == view.issue_id:
                # Coding can start while the source is fetched; use its committed
                # phase, as the broker's authority checks do.
                with self.database.transaction() as db:
                    attempt = db.execute("SELECT coding_started FROM attempts WHERE attempt_id=?",
                                         (view.attempt["attempt_id"],)).fetchone()
                if not dependency_admission_applies(attempt):
                    # Added root blockers only govern the next admission. Keep
                    # checking original edges and every retrieved contract without
                    # rewriting the immutable snapshot or its approved digest.
                    known = {r["id"] for r in original["dependencies"]}
                    dependencies = [r for r in dependencies if r["type"] != "blocks" or r["id"] in known]
            value["dependencies"] = sorted(dependencies, key=lambda r: r["id"])
            value["attachments"] = sorted(content["attachments"], key=lambda r: r["id"])
        elif request["operation"] == "pull":
            value = {k: v for k, v in content.items() if k != "updated_at"}
        else:
            value = content
        return digest(value)

    def sanitize(self, content, secrets=()):
        value = redact_review_text(content) if isinstance(content, str) else redact_review_value(content)
        private = [*secrets, *[getattr(self.linear, name, None) for name in ("token", "_access_token", "client_secret")]]

        def scrub(item):
            if isinstance(item, str):
                for secret in private:
                    if isinstance(secret, str) and len(secret) >= 8:
                        item = item.replace(secret, "[REDACTED]")
            elif isinstance(item, dict):
                return {scrub(k): scrub(v) for k, v in item.items()}
            elif isinstance(item, list):
                return [scrub(v) for v in item]
            return item
        return scrub(value)

    def store(self, view, request, content, status="ok", *, required=False, secrets=()):
        # Redact before persistence and before returning content to either agent.
        content = self.sanitize(content, secrets)
        encoded = json.dumps(content, ensure_ascii=True)
        if len(encoded.encode()) > MAX_SOURCE_BYTES:
            content, encoded, status = {"error": "oversized"}, '{"error":"oversized"}', "oversized"
        key = digest(request)
        with self.database.transaction() as db:
            current = rows(db, view.attempt["attempt_id"])
            previous = next((row for row in current if row['source_key'] == key), None)
            if previous and previous['status'] != 'temporary_unavailable':
                raise Rejected('An immutable context snapshot cannot be replaced')
            size = sum(len(r['content'].encode()) for r in current) - (len(previous['content'].encode()) if previous else 0)
            if (not previous and len(current) >= MAX_SOURCES) or size + len(encoded) > MAX_TASK_BYTES:
                raise ContextError("budget_exceeded")
            material = self.material(view, request, content) if status == "ok" else digest([status])
            # Version records the snapshot actually read, including comment
            # versions and source updatedAt; material excludes reporting noise.
            version = digest(content)
            db.execute("INSERT INTO context_sources(attempt_id,source_key,request,content,status,version,material,required) "
                       "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(attempt_id,source_key) DO UPDATE SET "
                       "content=excluded.content,status=excluded.status,version=excluded.version,material=excluded.material,"
                       "required=MAX(context_sources.required,excluded.required),read_until=0,reviewed=0",
                       (view.attempt["attempt_id"], key, json.dumps(request), encoded, status, version, material, required))
        return self.lookup(view, request)

    def lookup(self, view, request):
        with self.database.transaction() as db:
            row = db.execute("SELECT * FROM context_sources WHERE attempt_id=? AND source_key=?",
                             (view.attempt["attempt_id"], digest(request))).fetchone()
        return dict(row) if row else None

    def root(self, view, check):
        request = {"operation": "issue", "target": view.issue_id}
        row = self.lookup(view, request)
        if row and row['status'] != 'temporary_unavailable':
            return row
        return self.fetch(view, request, check, required=True)

    def graph(self, view):
        issues, pulls = {view.issue_id, view.identifier}, set()
        for row in self.sources(view):
            if json.loads(row["request"])["operation"] == "issue" and row["status"] == "ok":
                related, delivery = references(json.loads(row["content"]))
                issues.update(related)
                pulls.update(delivery)
        return issues, pulls

    def repository(self, view, target, repositories, check):
        repo, number, url = pull_ref(target)
        if repo not in repositories or url not in self.graph(view)[1]:
            raise ContextError("denied")
        return Repository(self.github, repo, check), number

    def fetch(self, view, request, check, repositories=(), *, required=False):
        try:
            op = request["operation"]
            secrets = ()
            if op == "issue":
                content = linear_issue(self.linear, request["target"], check)
                if self._reporting_actor is None:
                    self._reporting_actor = self.linear.viewer_id()
                if request["target"] not in {content["id"], content["identifier"]}:
                    raise ContextError("identity_mismatch")
                if content["id"] == view.issue_id and issue_revision(content) != view.spec.content_revision:
                    raise ContextError("changed")
            else:
                repo, number = self.repository(view, request["target"], repositories, check)
                secrets = (repo.token,)
                if op == "pull":
                    content = repo.pull(number)
                else:
                    pin = self.lookup(view, {"operation": "pull", "target": request["target"]})
                    if not pin or pin["status"] != "ok":
                        raise ContextError("incomplete")
                    pull = json.loads(pin["content"])
                    content = {"repository": pull["repository"], "commit": pull["commit"],
                               "path": request.get("path", ""),
                               "result": repo.read(pull, op, request.get("path", ""), request.get("query", ""))}
            check()
            return self.store(view, request, content, secrets=secrets)
        except (RequestError, ContextError, KeyError, TypeError, ValueError, AttributeError) as exc:
            if isinstance(exc, RequestError) and exc.retryable:
                check()
                # Preserve the required-read obligation while permitting the
                # same operation to recover. Approval cannot ignore the outage.
                self.store(view, request, {'error': 'temporary_unavailable'}, 'temporary_unavailable', required=required)
                raise
            # No exception message, request headers, tokens or raw responses.
            status = exc.status if isinstance(exc, ContextError) else (
                "missing" if getattr(exc, "status", None) == 404 else
                "denied" if getattr(exc, "status", None) in {401, 403} else
                "unavailable" if isinstance(exc, RequestError) else "incomplete")
            check()
            return self.store(view, request, {"error": status}, status)

    def manifest(self, view):
        return [{"source": json.loads(r["request"]), "source_key": r["source_key"], "version": r["version"],
                 "material_revision": r["material"], "status": r["status"], "required": bool(r["required"]),
                 "complete": r["status"] == "ok" and r["read_until"] >= len(source_text(json.loads(r["content"]))),
                 "reviewed": bool(r["reviewed"]),
                 "commit": json.loads(r["content"]).get("commit") if isinstance(json.loads(r["content"]), dict) else None}
                for r in self.sources(view)]

    def approval_required(self, view):
        sources = self.sources(view)
        pins = {json.loads(r["request"]).get("target") for r in sources
                if r["reviewed"] and r["status"] == "ok" and json.loads(r["request"])["operation"] == "pull"}
        return any(not r["reviewed"] and not (json.loads(r["request"])["operation"] in {"file", "tree", "search"}
                                             and json.loads(r["request"])["target"] in pins) for r in sources)

    def read(self, view, value, repositories, check):
        try:
            return self._read(view, value, repositories, check)
        except ContextError as exc:
            if exc.status in {"timeout", "budget_exceeded"}:
                # A timed-out required fetch must remain evidence of absence,
                # even when the deadline prevented storing its partial payload.
                check()
                request = validate_request(value)
                if value.get("required", True):
                    # One reserved, bounded receipt also works when the source
                    # count/byte budget is full. It contains no partial content.
                    with self.database.transaction() as db:
                        db.execute("INSERT OR IGNORE INTO context_sources(attempt_id,source_key,request,content,status,version,material,required) "
                                   "VALUES (?,?,?,?,?,?,?,1)", (view.attempt["attempt_id"], "retrieval-failure", json.dumps(request),
                                   json.dumps({"error": exc.status}), exc.status, digest(exc.status), digest(exc.status)))
            raise

    def _read(self, view, value, repositories, check):
        request = validate_request(value)
        with self.lock_for(view):
            check = self.bounded(check)
            check()
            with context_read_scope(lambda: self.check_budget(view, check)):
                op = request["operation"]
                if op != "manifest":
                    root = self.root(view, check)
                    if root["status"] != "ok":
                        self.require(view, root)
                        return {"status": root["status"], "complete": False, "error": "Current task context is unavailable"}
                if op == "manifest":
                    content = self.manifest(view)
                elif op == "list":
                    issues, pulls = self.graph(view)
                    content = {"issues": sorted(issues), "pull_requests": sorted(pulls)}
                else:
                    if op == "issue":
                        if request["target"] not in self.graph(view)[0]:
                            raise ContextError("denied")
                        # Canonicalize identifiers already fetched by ID (and vice versa).
                        for row in self.sources(view):
                            data = json.loads(row["content"])
                            if (json.loads(row["request"])["operation"] == "issue" and row["status"] == "ok"
                                    and request["target"] in {data["id"], data["identifier"]}):
                                request = json.loads(row["request"])
                                break
                    else:
                        repo, _, url = pull_ref(request["target"])
                        if repo not in repositories or url not in self.graph(view)[1]:
                            raise ContextError("denied")
                        if op != "pull":
                            pin_request = {"operation": "pull", "target": url}
                            pin = self.lookup(view, pin_request)
                            if not pin or pin['status'] == 'temporary_unavailable':
                                self.fetch(view, pin_request, check, repositories, required=value.get('required', True))
                    row = self.lookup(view, request)
                    if not row or row['status'] == 'temporary_unavailable':
                        row = self.fetch(view, request, check, repositories, required=value.get('required', True))
                    if row["status"] != "ok":
                        if value.get("required", True):
                            self.require(view, row)
                        return {"status": row["status"], "source_key": row["source_key"], "version": row["version"], "complete": False}
                    content = json.loads(row["content"])
                text = source_text(content)
                key, version = digest(request), digest(content)
                offset = 0
                if value.get("cursor"):
                    try:
                        raw = base64.urlsafe_b64decode(value["cursor"]).decode().split(":")
                        if len(raw) != 3 or raw[:2] != [key, version]:
                            raise ValueError()
                        offset = int(raw[2])
                    except (ValueError, UnicodeError):
                        raise ContextError("invalid_cursor") from None
                if offset < 0 or offset > len(text) or (op not in {"list", "manifest"} and offset > row["read_until"]):
                    raise ContextError("invalid_cursor")
                end = min(offset + PAGE_CHARS, len(text))
                if op not in {"list", "manifest"}:
                    with self.database.transaction() as db:
                        db.execute("UPDATE context_sources SET read_until=MAX(read_until,?),required=MAX(required,?) "
                                   "WHERE attempt_id=? AND source_key=?", (end, value.get("required", True), view.attempt["attempt_id"], key))
                check()
                return {"status": "ok", "source_key": key, "version": version, "complete": end == len(text),
                        "next_cursor": base64.urlsafe_b64encode(f"{key}:{version}:{end}".encode()).decode() if end < len(text) else None,
                        "content": text[offset:end], "approval_required": view.phase != "reviewing" and self.approval_required(view)}

    def require(self, view, row):
        with self.database.transaction() as db:
            db.execute("UPDATE context_sources SET required=1 WHERE attempt_id=? AND source_key=?", (view.attempt["attempt_id"], row["source_key"]))

    def revalidate(self, view, repositories, check, *, approved=False):
        """Compare material evidence; keep the original revisions for coding."""
        with self.lock_for(view):
            check = self.bounded(check)
            with context_read_scope(lambda: self.check_budget(view, check)):
                for row in self.sources(view):
                    check()
                    request = json.loads(row["request"])
                    if row["status"] != "ok":
                        if row["required"]:
                            if row['status'] == 'temporary_unavailable':
                                raise RequestError(503)
                            raise ContextError("required_source_unavailable")
                        continue
                    if request["operation"] == "issue":
                        content = linear_issue(self.linear, request["target"], check)
                        if self._reporting_actor is None:
                            self._reporting_actor = self.linear.viewer_id()
                    elif request["operation"] == "pull":
                        repo, number = self.repository(view, request["target"], repositories, check)
                        content = repo.pull(number)
                    else:
                        # Immutable blobs/trees stay in the durable cache. The
                        # owning PR recheck proves identity and continued access.
                        continue
                    if self.material(view, request, self.sanitize(content), original=json.loads(row["content"])) != row["material"]:
                        raise ContextError("material_evidence_changed")
                if approved and self.approval_required(view):
                    raise ContextError("new_evidence_requires_review")
                if approved:
                    with self.database.transaction() as db:
                        approval = db.execute("SELECT manifest FROM context_approvals WHERE attempt_id=?", (view.attempt["attempt_id"],)).fetchone()
                    if not approval:
                        raise ContextError("missing_approval")
                    sealed = {r["key"]: r["material"] for r in json.loads(approval[0])}
                    if any(r["reviewed"] and sealed.get(r["source_key"]) != r["material"] for r in self.sources(view)):
                        raise ContextError("material_evidence_changed")
                    for row in self.sources(view):
                        if row["required"] and row["read_until"] < len(source_text(json.loads(row["content"]))):
                            raise ContextError("required_source_incomplete")
            check()
            with self.database.transaction() as db:
                return revision(db, view.attempt["attempt_id"])
