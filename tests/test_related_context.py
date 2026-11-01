"""Related evidence crosses real signed roles, SQLite approval and read adapters."""

import base64
from dataclasses import replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
from common.context import ContextError, MAX_REMOTE_CALLS, digest, validate_request
from common.clients import RequestError, context_read_scope, request_json
from common.publication import comment_id
from controller.context import Context
from controller.model import IssueObserved, Progress, Rejected, Role
from controller_fixtures import update_account
from context_fixtures import ContextFixture, DEPENDENCY, COMMIT, CONTRACT, BLOB
from call_budget import CallBudget
from test_readiness import ready


class RelatedContextTests(unittest.TestCase):
    def setUp(self):
        self.fx = ContextFixture(self)
        self.config = self.fx.start_review()

    def test_cross_project_dependency_pr_published_contract_and_pinned_coding(self):
        result = self.fx.contract()
        self.assertEqual(result["commit"], COMMIT)
        self.assertEqual(result["result"], CONTRACT)
        dependency = self.fx.complete("issue", target=DEPENDENCY)
        self.assertEqual(dependency["state"]["name"], "Done")
        self.assertEqual(len(dependency["comments"]), 51)
        self.assertEqual(dependency["comments"][-1]["parent"], {"id": "comment-0"})
        pull = self.fx.complete("pull", target=self.fx.pull)
        self.assertEqual(len(pull["files"]), 101)
        self.assertTrue(pull["merged"])
        before = self.fx.complete("manifest")
        self.assertTrue(all(row["version"] for row in before))
        self.assertEqual(self.fx.approve(self.config), {"status": "passed"})
        self.fx.start_coding()
        self.fx.f.broker.context = Context(self.fx.f.database, self.fx.f.linear, self.fx.f.github)
        # Recreating the service (as for another VM/job) keeps the same revisions.
        with CallBudget(self, linear=self.fx.f.linear, github=self.fx.f.github):
            self.assertEqual(self.fx.contract(Role.CODING), result)
        after = self.fx.complete("manifest", role=Role.CODING)
        self.assertEqual([(r["source_key"], r["version"]) for r in before], [(r["source_key"], r["version"]) for r in after])
        self.assertFalse(self.fx.read("manifest", role=Role.CODING)["approval_required"])
        calls = self.fx.f.github.installation_token.call_args_list
        context_calls = [c for c in calls if c.args[0].lower() == self.fx.repo]
        self.assertTrue(context_calls)
        self.assertTrue(all(c.kwargs["permissions"] == {"contents": "read", "pull_requests": "read"} for c in context_calls))

    def test_cached_pages_are_complete_and_cannot_be_skipped_or_rebound(self):
        self.fx.complete("list")
        first = self.fx.read("issue", target="KIAN-941")
        self.assertFalse(first["complete"])
        counts = len(self.fx.calls), len(self.fx.gh_calls)
        again = self.fx.read("issue", target="KIAN-941")
        self.assertEqual(first, again)
        self.assertEqual(counts, (len(self.fx.calls), len(self.fx.gh_calls)))
        key, version, _ = base64.urlsafe_b64decode(first["next_cursor"]).decode().split(":")
        bad = base64.urlsafe_b64encode(f"{key}:{version}:999999".encode()).decode()
        self.assertEqual(self.fx.read("issue", target="KIAN-941", cursor=bad)["status"], "invalid_cursor")
        self.assertEqual(self.fx.read("issue", target=self.fx.root["id"], cursor=first["next_cursor"])["status"], "invalid_cursor")
        with self.assertRaisesRegex(Rejected, "incompletely read"):
            self.fx.approve(self.config)
        full = self.fx.complete("issue", target="KIAN-941")
        self.assertEqual(len(full["comments"]), 51)
        self.fx.approve(self.config)

    def test_required_contract_failure_cannot_be_replaced_by_done_or_pr_summary(self):
        self.fx.complete("list")
        self.fx.complete("issue", target="KIAN-941")
        self.fx.complete("pull", target=self.fx.pull)
        self.fx.failed[f"/repos/{self.fx.repo}/git/blobs/{BLOB}"] = 403
        response = self.fx.read("file", target=self.fx.pull, path="docs/account.md")
        self.assertEqual(response["status"], "denied")
        self.assertFalse(response["complete"])
        with self.assertRaisesRegex(Rejected, "fresh readiness"):
            self.fx.approve(self.config)
        self.assertEqual(self.fx.f.controller.inspect(self.fx.f.view.task_id).phase, "held")

    def test_issue_comment_or_workflow_state_changes_revoke_approval(self):
        for change in ("description", "reply", "state"):
            with self.subTest(change=change):
                fx = ContextFixture(self)
                config = fx.start_review()
                fx.contract()
                fx.approve(config)
                if change == "description":
                    fx.dependency["description"] += " New incompatible contract."
                elif change == "reply":
                    fx.comments[DEPENDENCY][-1]["body"] = "The availability flag means something else."
                else:
                    fx.dependency["state"] = {"id": "canceled", "name": "Canceled", "type": "canceled"}
                with self.assertRaisesRegex(Rejected, "fresh readiness"):
                    fx.start_coding()

    def test_pr_identity_and_commit_changes_cannot_authorize_old_contract(self):
        self.fx.contract()
        self.fx.approve(self.config)
        self.fx.pull_data["merge_commit_sha"] = "9" * 40
        with self.assertRaisesRegex(Rejected, "fresh readiness"):
            self.fx.start_coding()
        self.assertEqual(self.fx.f.controller.inspect(self.fx.f.view.task_id).phase, "held")

    def test_material_change_after_coding_starts_blocks_publication(self):
        self.fx.contract()
        self.fx.approve(self.config)
        self.fx.start_coding()
        self.fx.comments[DEPENDENCY][-1]["body"] = "Correction: contract withdrawn."
        count = self.fx.f.github.installation_token.call_count
        with self.assertRaisesRegex(Rejected, "fresh readiness"):
            self.fx.call(Role.CODING, "github")
        # No publication token was requested, even though revalidation can mint read tokens.
        self.assertFalse(any(c.kwargs.get("permissions", {}).get("contents") == "write"
                             for c in self.fx.f.github.installation_token.call_args_list[count:]))

    def test_added_prerequisite_keeps_running_coding_authorized_with_or_without_reviewed_context(self):
        for provider in ("codex", "cursor"):
            for fetched in (False, True):
                with self.subTest(provider=provider, fetched=fetched):
                    fx = ContextFixture(self, provider=provider)
                    config = fx.start_review()
                    if fetched:
                        contract = fx.contract()
                        root = fx.complete("issue", target=fx.root["id"])
                    fx.approve(config)
                    fx.start_coding()
                    sources = fx.f.broker.context.sources(fx.f.view)
                    with fx.f.database.transaction() as db:
                        approval = tuple(db.execute("SELECT * FROM context_approvals").fetchone())
                    view = fx.add_prerequisite()
                    self.assertEqual(view.phase, "executing")
                    self.assertEqual(view.attempt["coding_started"], 1)
                    self.assertFalse(view.dependencies_ready)
                    self.assertFalse(view.review_attention)
                    # Exact KIAN-946 reproduction: publication without any prior
                    # context retrieval must survive the dependency observation.
                    fx.f.advance('publishing')
                    with CallBudget(self, linear=fx.f.linear, github=fx.f.github, exact={
                            "linear.issue": 1, "linear.complete": 1, "linear.query": 12 if fetched else 1,
                            "github.branch_sha": 1, "github.installation_token": 2 if fetched else 1,
                            "github.workflow_changes_allowed": 1, **({"github.request": 4} if fetched else {})}):
                        self.assertEqual(fx.call(Role.CODING, "github"), {"token": "ghs_CONTROLLER_ONLY"})
                    fx.f.broker.context = Context(fx.f.database, fx.f.linear, fx.f.github)
                    with CallBudget(self, linear=fx.f.linear, github=fx.f.github):
                        if fetched:
                            self.assertEqual(fx.complete("issue", target=fx.root["id"], role=Role.CODING), root)
                            self.assertEqual(fx.contract(Role.CODING), contract)
                        else:
                            self.assertEqual(fx.complete("manifest", role=Role.CODING), [])
                    fx.call(Role.CODING, "checkout")
                    fx.call(Role.CODING, "progress", stage="publishing")
                    fx.call(Role.CODING, "github")
                    self.assertEqual(fx.f.broker.context.sources(view), sources)
                    with fx.f.database.transaction() as db:
                        self.assertEqual(tuple(db.execute("SELECT * FROM context_approvals").fetchone()), approval)
                        self.assertEqual(db.execute("SELECT repair_used FROM tasks WHERE task_id=?", (view.task_id,)).fetchone()[0], 0)
                    current = fx.f.controller.inspect(view.task_id)
                    self.assertEqual(current.phase, "executing")
                    self.assertEqual(current.attempt["attempt_id"], view.attempt["attempt_id"])
                    self.assertEqual(len(fx.f.controller.attempt_history(view.task_id)), 1)
                    self.assertFalse(current.review_attention or current.attempt["retry_kind"])
                    self.assertEqual((current.review_cycle, current.review_attempts, current.attempt["retry_at"]), (1, 1, 0))

    def test_added_prerequisite_before_coding_blocks_context_start_and_stage_entry(self):
        for phase in ("review", "ready", "initializing"):
            with self.subTest(phase=phase):
                fx = ContextFixture(self)
                config = fx.start_review()
                fx.contract()
                if phase != "review":
                    fx.approve(config)
                if phase == "initializing":
                    fx.f.preflight(Role.CODING_SELECT, fx.f.account_b)
                    fx.call(Role.CODING, "start", protocol=config["protocol"])
                    fx.call(Role.CODING, "claim", account_id=fx.f.account_b)
                view = fx.add_prerequisite()
                self.assertEqual(view.phase, "held")
                self.assertFalse(view.attempt["coding_started"])
                role = Role.REVIEW if phase == "review" else Role.CODING
                with self.assertRaises(Rejected):
                    fx.complete("manifest", role=role)
                with self.assertRaises(Rejected):
                    if phase == "review":
                        fx.approve(config)
                    elif phase == "ready":
                        fx.start_coding()
                    else:
                        fx.call(Role.CODING, "progress", stage="coding", context_sha=fx.f.spec.context_sha)
                with self.assertRaises(Rejected):
                    fx.call(Role.CODING, "github")
                current = fx.f.controller.inspect(view.task_id)
                self.assertTrue(current.active)
                self.assertFalse(current.review_attention or current.attempt["retry_kind"])

    def test_coding_start_during_revalidation_uses_committed_dependency_phase(self):
        self.fx.contract()
        self.fx.approve(self.config)
        self.fx.f.preflight(Role.CODING_SELECT, self.fx.f.account_b)
        self.fx.call(Role.CODING, "start", protocol=self.config["protocol"])
        self.fx.call(Role.CODING, "claim", account_id=self.fx.f.account_b)
        started = []

        def start_during_fetch(query, variables):
            if not started and variables.get("id") == self.fx.root["id"] and "project { id name }" in query:
                # Another guarded request has finished revalidation and commits
                # its coding transition while this request is fetching evidence.
                started.append(self.fx.f.controller.apply(Progress(self.fx.f.view.task_id, self.fx.f.key,
                    "coding", self.fx.f.spec.context_sha), now=1000))
                self.fx.add_prerequisite()

        self.fx.after_query = start_during_fetch
        self.fx.call(Role.CODING, "checkout")
        self.assertEqual(len(started), 1)
        current = self.fx.f.controller.inspect(self.fx.f.view.task_id)
        self.assertEqual(current.phase, "executing")
        self.assertFalse(current.dependencies_ready or current.review_attention or current.attempt["retry_kind"])

    def test_added_prerequisite_does_not_exempt_material_evidence_changes(self):
        for change in ("root_reply", "old_edge_removed", "old_edge_changed", "new_related_edge",
                       "upstream_edge", "contract_withdrawn", "pr_commit"):
            with self.subTest(change=change):
                fx = ContextFixture(self)
                config = fx.start_review()
                fx.contract()
                fx.approve(config)
                fx.start_coding()
                view = fx.add_prerequisite()
                if change == "root_reply":
                    fx.comments[fx.root["id"]].append({"id": "decision", "body": "Use a different contract.",
                        "createdAt": "later", "updatedAt": "later", "user": {"id": "human"}, "parent": None})
                elif change == "old_edge_removed":
                    fx.dependencies[fx.root["id"]].pop(0)
                elif change == "old_edge_changed":
                    fx.dependencies[fx.root["id"]][0]["issue"]["identifier"] = "KIAN-999"
                elif change == "new_related_edge":
                    fx.dependencies[fx.root["id"]][-1]["type"] = "related"
                elif change == "upstream_edge":
                    fx.dependencies[DEPENDENCY] = fx.dependencies[fx.root["id"]][-1:]
                elif change == "contract_withdrawn":
                    fx.comments[DEPENDENCY][-1]["body"] = "Correction: contract withdrawn."
                else:
                    fx.pull_data["merge_commit_sha"] = "9" * 40
                count = fx.f.github.installation_token.call_count
                with self.assertRaisesRegex(Rejected, "fresh readiness"):
                    fx.call(Role.CODING, "github")
                self.assertFalse(any(c.kwargs.get("permissions", {}).get("contents") == "write"
                                     for c in fx.f.github.installation_token.call_args_list[count:]))
                current = fx.f.controller.inspect(view.task_id)
                self.assertTrue(current.review_attention)
                self.assertFalse(current.attempt["retry_kind"])

    def test_added_prerequisite_does_not_exempt_authorization_loss(self):
        for loss in ("cancellation", "lease", "lease_after_reclaim", "lease_owner", "deadline", "mapping", "installation"):
            with self.subTest(loss=loss):
                fx = ContextFixture(self)
                config = fx.start_review()
                fx.contract()
                fx.approve(config)
                fx.start_coding()
                view = fx.add_prerequisite()
                if loss == "cancellation":
                    fx.f.controller.apply(IssueObserved(view.issue_id, view.project_id, view.repository,
                        view.generation, view.identifier, False, False, content_revision=view.spec.content_revision,
                        canceled=True), now=1001)
                elif loss in {"lease", "lease_after_reclaim"}:
                    if loss == "lease_after_reclaim":
                        account, _ = fx.f.accounts.get(fx.f.account_b)
                        fx.call(Role.CODING, "save", account_id=fx.f.account_b, lease=account["fence"], release=True)
                        fx.call(Role.CODING, "claim", account_id=fx.f.account_b)
                    update_account(fx.f.pool, fx.f.account_b, lease_until=1000)
                elif loss == "lease_owner":
                    update_account(fx.f.pool, fx.f.account_b, owner="another-attempt")
                elif loss == "deadline":
                    fx.f.broker.clock = lambda: view.session("coding")["deadline"]
                elif loss == "mapping":
                    fx.f.broker.registry = replace(fx.f.registry, routes={key: route for key, route in
                        fx.f.registry.routes.items() if route.repository.lower() != fx.repo})
                else:
                    fx.f.github.installation_token.side_effect = RequestError(403)
                count = fx.f.github.installation_token.call_count
                if loss != "installation":
                    with self.assertRaises(Rejected):
                        fx.complete("manifest", role=Role.CODING)
                with self.assertRaises(Rejected):
                    fx.call(Role.CODING, "github")
                self.assertFalse(any(c.kwargs.get("permissions", {}).get("contents") == "write"
                                     for c in fx.f.github.installation_token.call_args_list[count:]))

    def test_new_evidence_after_prerequisite_added_still_requires_review(self):
        self.fx.complete("list")
        self.fx.approve(self.config)
        self.fx.start_coding()
        self.fx.add_prerequisite()
        self.fx.complete("issue", target="KIAN-941", role=Role.CODING)
        self.assertTrue(self.fx.read("manifest", role=Role.CODING)["approval_required"])
        with self.assertRaisesRegex(Rejected, "fresh readiness"):
            self.fx.call(Role.CODING, "github")

    def test_fenced_release_after_added_prerequisite_preserves_validated_worker_cleanup(self):
        self.fx.contract()
        self.fx.approve(self.config)
        self.fx.start_coding()
        self.fx.f.advance('publishing')
        self.fx.add_prerequisite()
        account, _ = self.fx.f.accounts.get(self.fx.f.account_b)
        self.fx.call(Role.CODING, "save", account_id=self.fx.f.account_b, lease=account["fence"], release=True)
        with self.assertRaisesRegex(Rejected, "active execution account lease"):
            self.fx.complete("manifest", role=Role.CODING)
        self.fx.call(Role.CODING, "checkout")
        self.fx.call(Role.CODING, "github")
        self.fx.comments[DEPENDENCY][-1]["body"] = "Correction: contract withdrawn."
        with self.assertRaisesRegex(Rejected, "fresh readiness"):
            self.fx.call(Role.CODING, "github")

    def test_reporting_labels_timestamps_and_unrelated_branch_activity_do_not_loop(self):
        self.fx.contract()
        self.fx.approve(self.config)
        self.fx.root["updatedAt"] = self.fx.dependency["updatedAt"] = "later"
        self.fx.root["state"] = {"id": "progress", "name": "In Progress", "type": "started"}
        self.fx.root["labels"] = {"nodes": [{"name": "clusterfork: coding"}]}
        self.fx.comments[self.fx.root["id"]].append({"id": comment_id(self.fx.f.view.task_id, "controller-status"),
              "body": "Status updated", "createdAt": "later", "updatedAt": "later", "user": {"id": "bot"}, "parent": None})
        self.fx.comments[DEPENDENCY][0]["updatedAt"] = "later"  # No content change.
        self.fx.pull_data["updated_at"] = "later"
        self.fx.start_coding()
        self.fx.f.advance('publishing')
        self.fx.call(Role.CODING, "github")
        self.assertEqual(self.fx.f.controller.inspect(self.fx.f.view.task_id).phase, "executing")

    def test_new_coding_evidence_is_readable_but_requires_review_before_publication(self):
        self.fx.complete("list")
        self.fx.approve(self.config)
        self.fx.start_coding()
        response = self.fx.read("issue", target="KIAN-941", role=Role.CODING)
        self.assertTrue(response["approval_required"])
        self.fx.complete("issue", target="KIAN-941", role=Role.CODING)
        with self.assertRaisesRegex(Rejected, "fresh readiness"):
            self.fx.call(Role.CODING, "github")

    def test_coding_can_browse_and_search_more_files_at_reviewed_commit(self):
        self.fx.contract()
        self.fx.approve(self.config)
        self.fx.start_coding()
        tree = self.fx.complete("tree", target=self.fx.pull, path="docs", role=Role.CODING)
        self.assertEqual(tree["commit"], COMMIT)
        self.assertEqual(tree["result"]["entries"][0]["path"], "account.md")
        search = self.fx.complete("search", target=self.fx.pull, path="docs", query="account_id", role=Role.CODING)
        self.assertEqual(search["result"]["matches"][0]["line"], 1)
        self.assertFalse(self.fx.read("manifest", role=Role.CODING)["approval_required"])
        self.fx.f.advance('publishing')
        self.fx.call(Role.CODING, "github")

    def test_mutations_unrelated_issues_repositories_paths_and_credentials_are_denied(self):
        self.fx.complete("list")
        for request in ({"operation": "delete", "target": "KIAN-941"},
                        {"operation": "issue", "target": "KIAN-9999"},
                        {"operation": "pull", "target": f"https://github.com/{self.fx.repo}/pull/777"},
                        {"operation": "pull", "target": "https://github.com/stranger/private/pull/1"},
                        {"operation": "file", "target": self.fx.pull, "path": "../auth.json"},
                        {"operation": "file", "target": self.fx.pull, "path": ".codex/auth.json"},
                        {"operation": "issue", "target": "KIAN-941", "method": "POST"}):
            with self.subTest(request=request):
                before = len(self.fx.calls), len(self.fx.gh_calls)
                result = self.fx.call(Role.REVIEW, "context", request=request)
                self.assertFalse(result["complete"])
                self.assertIn(result["status"], {"invalid_request", "denied"})
                self.assertEqual(before, (len(self.fx.calls), len(self.fx.gh_calls)))
        with self.assertRaises(Rejected):
            self.fx.call(Role.REVIEW, "github")
        self.fx.complete("issue", target="KIAN-941")
        with self.fx.f.database.transaction() as db:
            stored = " ".join(r[0] for r in db.execute("SELECT content FROM context_sources"))
        self.assertNotIn("CONTROLLER_ONLY", stored)

    def test_mapped_repository_revocation_and_installation_denial_stop_reads(self):
        self.fx.complete("issue", target="KIAN-941")
        self.fx.f.github.installation_token.side_effect = RequestError(403)
        self.assertEqual(self.fx.read("pull", target=self.fx.pull)["status"], "denied")
        routes = {key: value for key, value in self.fx.f.registry.routes.items() if value.repository.lower() != self.fx.repo}
        self.fx.f.broker.registry = replace(self.fx.f.registry, routes=routes)
        before = self.fx.f.github.installation_token.call_count
        self.assertEqual(self.fx.read("pull", target=self.fx.pull)["status"], "denied")
        self.assertEqual(before, self.fx.f.github.installation_token.call_count)

    def test_pagination_cycles_missing_metadata_and_truncated_trees_are_explicit(self):
        self.fx.complete("list")
        original = self.fx.page
        self.fx.page = lambda rows, after: {"nodes": rows[:1], "pageInfo": {"hasNextPage": True, "endCursor": "0"}}
        self.assertEqual(self.fx.read("issue", target="KIAN-941")["status"], "incomplete")
        self.fx.page = original
        # A new task avoids retrying a deliberately cached failed snapshot.
        other = ContextFixture(self)
        other.start_review()
        other.complete("issue", target="KIAN-941")
        other.complete("pull", target=other.pull)
        other.truncate_tree = True
        self.assertEqual(other.read("tree", target=other.pull)["status"], "incomplete")

    def test_missing_oversized_and_budget_exhausted_sources_are_explicit(self):
        self.fx.complete("list")
        self.fx.failed["KIAN-941"] = 404
        self.assertEqual(self.fx.read("issue", target="KIAN-941")["status"], "missing")
        # Explicit references to another issue do not permit arbitrary browsing.
        other = ContextFixture(self)
        other.start_review()
        other.dependency["description"] = "x" * 1_000_001
        self.assertEqual(other.read("issue", target="KIAN-941")["status"], "oversized")
        with other.f.database.transaction() as db:
            db.execute("INSERT OR REPLACE INTO context_budgets VALUES (?,?)", (other.f.key, MAX_REMOTE_CALLS))
        # Exhaustion applies to HTTP transport even before a response is read.
        with context_read_scope(lambda: other.f.broker.context.check_budget(other.f.view, lambda: None)), \
             self.assertRaisesRegex(ContextError, "budget_exceeded"):
            request_json("https://api.github.com/repos/test/test")

    def test_cancellation_during_retrieval_returns_no_source_and_no_cached_success(self):
        self.fx.complete("list")
        def cancel(query, variables):
            if variables.get("id") == "KIAN-941":
                self.fx.f.controller.apply(IssueObserved(self.fx.root["id"], self.fx.f.route.project_id,
                    self.fx.f.route.repository, self.fx.f.registry.generation, self.fx.root["identifier"],
                    False, True, canceled=True), now=1001)
        self.fx.after_query = cancel
        with self.assertRaises(Rejected):
            self.fx.read("issue", target="KIAN-941")
        with self.fx.f.database.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM context_sources WHERE request LIKE '%KIAN-941%'").fetchone()[0], 0)

    def test_timeout_or_source_budget_exhaustion_cannot_leave_a_passable_partial_snapshot(self):
        self.fx.complete("list")
        clock = [0]
        self.fx.f.broker.context.monotonic = lambda: clock[0]
        self.fx.after_query = lambda query, variables: clock.__setitem__(0, 13) if variables.get("id") == "KIAN-941" else None
        response = self.fx.read("issue", target="KIAN-941")
        self.assertEqual(response["status"], "timeout")
        self.fx.after_query = None
        with self.assertRaisesRegex(Rejected, "fresh readiness"):
            self.fx.approve(self.config)
        other = ContextFixture(self)
        config = other.start_review()
        other.complete("list")
        with patch("controller.context.MAX_SOURCES", 1):
            self.assertEqual(other.read("issue", target="KIAN-941")["status"], "budget_exceeded")
        with self.assertRaisesRegex(Rejected, "fresh readiness"):
            other.approve(config)

    def test_selectors_expired_leases_and_forged_review_bindings_cannot_use_context(self):
        other = ContextFixture(self)
        with self.assertRaises(Rejected):
            other.call(Role.REVIEW_SELECT, "context", request={"operation": "list"})
        self.fx.contract()
        from controller.model import Review
        with self.assertRaisesRegex(Rejected, "evidence changed"):
            self.fx.f.controller.apply(Review(self.fx.f.view.task_id, self.fx.f.key,
                ready(), self.fx.f.spec, "forged"), now=1000)
        update_account(self.fx.f.pool, self.fx.f.account_a, lease_until=0)
        before = len(self.fx.calls)
        with self.assertRaisesRegex(Rejected, "active execution account lease"):
            self.fx.read("file", target=self.fx.pull, path="docs/account.md")
        self.assertEqual(len(self.fx.calls), before)

    def test_opaque_transport_secrets_are_scrubbed_and_optional_missing_candidates_do_not_block(self):
        self.fx.f.linear._access_token = "opaque-controller-oauth-secret"
        self.fx.dependency["description"] += " opaque-controller-oauth-secret"
        self.fx.contract()
        dependency = self.fx.complete("issue", target="KIAN-941")
        self.assertNotIn("opaque-controller-oauth-secret", json.dumps(dependency))
        self.assertIn("[REDACTED]", dependency["description"])
        response = self.fx.read("file", target=self.fx.pull, path="docs/does-not-exist.md", required=False)
        self.assertEqual(response["status"], "missing")
        self.fx.approve(self.config)

    def test_context_http_reads_are_bounded_without_returning_response_bodies(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"x" * 2_000_001
        with patch("common.clients.urlopen", return_value=response) as opened, context_read_scope(lambda: None), \
             self.assertRaisesRegex(ContextError, "oversized"):
            request_json("https://api.github.com/repos/allowed/repo", token="Bearer private")
        self.assertEqual(opened.call_args.kwargs["timeout"], 5)
        response.read.assert_called_once_with(2_000_001)


if __name__ == "__main__":
    unittest.main()
