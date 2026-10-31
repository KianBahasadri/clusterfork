"""Linear lifecycle behavior across worker, CI, merge, and retry boundaries."""

import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "automation"))
from common.clients import GitHub, Linear, RequestError
from common.routing import Registry
from test_remote_worker import KEY, PROJECT, ISSUE, receipt


def pr():
    return {"url": "https://github.com/KianBahasadri/clusterfork/pull/1", "state": "OPEN", "merged": False,
            "body": f"<!-- clusterfork-dispatch:{KEY} -->", "baseRefName": "main",
            "headRefName": "clusterfork/kian-891-aaaaaaaa",
            "headRepository": {"nameWithOwner": "KianBahasadri/clusterfork"},
            "author": {"login": "clusterfork", "__typename": "Bot"},
            "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN", "isDraft": False,
            "autoMergeRequest": {"enabledAt": "2026-10-28T20:00:00Z"},
            "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {
                "nodes": [{"__typename": "CheckRun", "name": "test", "isRequired": True,
                           "status": "COMPLETED", "conclusion": "SUCCESS"}],
                "pageInfo": {"hasNextPage": False}}}}}]}}


def checks(pull):
    return pull["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"]["nodes"]


class CommentAPI:
    """Stateful Linear fixture: identities can only edit their own comments."""
    old_actor = str(UUID(int=90))
    app_actor = str(UUID(int=91))

    def __init__(self):
        self.actor = self.app_actor
        self.comments, self.writes = {}, []
        self.owners = {}
        self.denied = set()

    def seed(self, identifier, body="Waiting for required CI checks.", actor=None):
        comment = {"id": identifier, "body": body, "user": {"id": actor or self.old_actor},
                   "replies": ["Preserve this discussion."]}
        self.comments[identifier] = comment
        self.owners[identifier] = actor or self.old_actor
        return copy.deepcopy(comment)

    def request(self, url, **kwargs):
        query, values = kwargs["data"]["query"], kwargs["data"]["variables"]
        if "viewer {" in query:
            return {"data": {"viewer": {"id": self.actor}}}
        if "comments(" in query:
            return {"data": {"issue": {"comments": {"nodes": [copy.deepcopy(comment)
                    for identifier, comment in self.comments.items() if identifier in values["comments"]]}}}}
        identifier = values["id"]
        if "commentUpdate(" in query:
            if identifier in self.denied or self.owners[identifier] != self.actor:
                return {"data": None, "errors": [{"message": "Only the comment author may edit. PRIVATE",
                        "path": ["commentUpdate"], "extensions": {"type": "forbidden", "code": "FORBIDDEN"}}]}
            self.comments[identifier]["body"] = values["body"]
            self.writes.append(("update", identifier))
            return {"data": {"commentUpdate": {"success": True}}}
        if "commentCreate(" in query:
            if identifier in self.comments:
                return {"errors": [{"extensions": {"type": "invalid input", "code": "BAD_USER_INPUT"}}]}
            self.comments[identifier] = {"id": identifier, "body": values["body"], "user": {"id": self.actor}}
            self.owners[identifier] = self.actor
            self.writes.append(("create", identifier))
            return {"data": {"commentCreate": {"success": True}}}
        raise AssertionError("Unexpected comment API operation")




class LifecycleAdapterTests(unittest.TestCase):
    def test_deployment_recovery_queries_current_github_attempt_and_holds_incomplete_results(self):
        client = GitHub("app", "private-test-key", request=Mock())
        with patch.object(client, "installation_token", return_value="test-token"):
            client.request.return_value = {"id": 123, "run_attempt": 2, "status": "in_progress"}
            self.assertEqual(client.worker_result("owner/repo", "123:1", latest=True)["run_attempt"], 2)
            self.assertEqual(client.request.call_args.args[0], "https://api.github.com/repos/owner/repo/actions/runs/123")
            for invalid in ({"status": "completed"}, {"id": 999, "run_attempt": 1, "status": "completed"},
                            {"id": 123, "run_attempt": 0, "status": "completed"},
                            {"id": 123, "run_attempt": 2, "status": "unknown"}):
                with self.subTest(invalid=invalid), self.assertRaises(RequestError):
                    client.request.return_value = invalid
                    client.worker_result("owner/repo", "123:1", latest=True)

    def test_comment_creation_and_updates_use_the_same_id_and_skip_identical_text(self):
        api = CommentAPI()
        request = Mock(side_effect=api.request)
        client = Linear("private-test-token", request)
        for body in ("old", "new", "new"):
            client.status_comment(ISSUE, ISSUE, body)
        self.assertEqual(request.call_count, 6)  # One cached viewer lookup.
        self.assertEqual(api.writes, [("create", ISSUE), ("update", ISSUE)])

    def test_old_identity_update_is_rejected_but_recovery_preserves_thread_and_reuses_replacement(self):
        api = CommentAPI()
        original = api.seed(ISSUE)
        client = Linear("test", api.request)
        # Reproduce a successful HTTP response carrying a rejected mutation.
        with self.assertLogs(level="ERROR") as logs, self.assertRaises(RequestError) as caught:
            client.query("""mutation($id: String!, $body: String!) {
              commentUpdate(id: $id, input: {body: $body}) { success }
            }""", {"id": ISSUE, "body": "new"})
        self.assertEqual(caught.exception.clusterfork_failure["graphql_type"], "forbidden")
        self.assertNotIn("PRIVATE", "\n".join(logs.output))
        for body in ("new", "new", "merged", "merged"):
            # A new client models process restarts and token renewal.
            Linear("test", api.request).status_comment(ISSUE, ISSUE, body)
        self.assertEqual(api.comments[ISSUE], original)
        self.assertEqual(len(api.comments), 2)
        replacement = next(identifier for identifier in api.comments if identifier != ISSUE)
        self.assertEqual(UUID(replacement).version, 4)
        self.assertEqual(api.comments[replacement]["body"], "merged")
        self.assertEqual(api.writes, [("create", replacement), ("update", replacement)])

    def test_ambiguous_replacement_creation_never_creates_another_comment(self):
        for response in ("transport", "graphql", "unsuccessful"):
            with self.subTest(response=response):
                api = CommentAPI()
                original = api.seed(ISSUE)

                def request(url, **kwargs):
                    result = api.request(url, **kwargs)
                    if "commentCreate(" in kwargs["data"]["query"]:
                        if response == "transport":
                            raise RequestError(503)
                        if response == "graphql":
                            return {"data": None, "errors": [{"extensions": {"type": "internal error"}}]}
                        return {"data": {"commentCreate": {"success": False}}}
                    return result

                with self.assertLogs(level="INFO") as logs:
                    Linear("test", request).status_comment(ISSUE, ISSUE, "merged")
                self.assertIn("linear_comment_create_recovered", "\n".join(logs.output))
                for _ in range(3):
                    Linear("test", api.request).status_comment(ISSUE, ISSUE, "merged")
                self.assertEqual(api.comments[ISSUE], original)
                self.assertEqual(len(api.comments), 2)
                self.assertEqual(len(api.writes), 1)

    def test_concurrent_deterministic_comment_creates_recover_without_forking_discussion(self):
        for migrate in (False, True):
            with self.subTest(migrate=migrate):
                api = CommentAPI()
                original = api.seed(ISSUE) if migrate else None
                barrier, local, lock = threading.Barrier(2), threading.local(), threading.RLock()

                def concurrent(url, **kwargs):
                    with lock:
                        result = api.request(url, **kwargs)
                    if "comments(" in kwargs["data"]["query"] and not getattr(local, "read", False):
                        local.read = True
                        barrier.wait(timeout=5)  # Both handlers first observe no target.
                    return result

                with self.assertLogs(level="INFO") as logs, ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(lambda _: Linear("test", concurrent).status_comment(ISSUE, ISSUE, "queued"), range(2)))
                self.assertIn("linear_comment_create_recovered", "\n".join(logs.output))
                self.assertEqual(len(api.comments), 2 if migrate else 1)
                self.assertEqual(len(api.writes), 1)
                target = api.writes[0][1]
                api.comments[target]["replies"] = ["Keep this human decision."]
                Linear("test", api.request).status_comment(ISSUE, ISSUE, "review dispatched")
                self.assertEqual(api.comments[target]["replies"], ["Keep this human decision."])
                self.assertEqual(api.comments[target]["body"], "review dispatched")
                if migrate:
                    self.assertEqual(api.comments[ISSUE], original)

    def test_ambiguous_initial_create_with_missing_author_metadata_recovers_the_same_body(self):
        api = CommentAPI()
        def lost_response(url, **kwargs):
            result = api.request(url, **kwargs)
            if "commentCreate(" in kwargs["data"]["query"]:
                identifier = kwargs["data"]["variables"]["id"]
                api.comments[identifier]["user"] = None
                api.comments[identifier]["replies"] = ["Keep this decision."]
                raise RequestError(503)
            return result
        Linear("test", lost_response).status_comment(ISSUE, ISSUE, "review dispatched")
        self.assertEqual(list(api.comments), [ISSUE])
        self.assertEqual(api.writes, [("create", ISSUE)])
        self.assertEqual(api.comments[ISSUE]["replies"], ["Keep this decision."])

    def test_failed_create_without_confirmed_comment_preserves_classification_and_id(self):
        api = CommentAPI()
        attempts = []

        def forbidden(url, **kwargs):
            if "commentCreate(" in kwargs["data"]["query"]:
                attempts.append(kwargs["data"]["variables"]["id"])
                return {"data": None, "errors": [{"message": "SECRET-authentication-data",
                        "path": ["commentCreate"], "extensions": {"code": "FORBIDDEN", "type": "forbidden"}}]}
            return api.request(url, **kwargs)

        for _ in range(2):
            with self.assertLogs(level="ERROR") as logs, self.assertRaises(RequestError) as caught:
                Linear("test", forbidden).status_comment(ISSUE, ISSUE, "queued")
            self.assertEqual(caught.exception.clusterfork_failure["graphql_code"], "FORBIDDEN")
            self.assertNotIn("SECRET", "\n".join(logs.output))
        self.assertEqual(attempts, [ISSUE, ISSUE])
        self.assertFalse(api.comments)

    def test_explicit_update_denial_with_matching_author_migrates_once(self):
        api = CommentAPI()
        original = api.seed(ISSUE, actor=api.app_actor)
        api.denied.add(ISSUE)
        with self.assertLogs(level="ERROR"):
            Linear("test", api.request).status_comment(ISSUE, ISSUE, "merged")
        for _ in range(3):
            Linear("test", api.request).status_comment(ISSUE, ISSUE, "merged")
        self.assertEqual(api.comments[ISSUE], original)
        self.assertEqual(len(api.comments), 2)
        self.assertEqual(len(api.writes), 1)

    def test_unavailable_author_metadata_keeps_one_replacement_even_for_bot_comments(self):
        api = CommentAPI()
        api.seed(ISSUE)
        api.comments[ISSUE]["user"] = None
        original = copy.deepcopy(api.comments[ISSUE])
        Linear("test", api.request).status_comment(ISSUE, ISSUE, "new")
        replacement = api.writes[0][1]
        api.comments[replacement]["user"] = None
        for body in ("new", "merged", "merged"):
            Linear("test", api.request).status_comment(ISSUE, ISSUE, body)
        self.assertEqual(api.comments[ISSUE], original)
        self.assertEqual(len(api.comments), 2)
        self.assertEqual(api.writes, [("create", replacement), ("update", replacement)])

    def test_ambiguous_replacement_update_reuses_the_committed_body(self):
        api = CommentAPI()
        original = api.seed(ISSUE)
        Linear("test", api.request).status_comment(ISSUE, ISSUE, "new")
        replacement = api.writes[0][1]

        def response_lost(url, **kwargs):
            result = api.request(url, **kwargs)
            if "commentUpdate(" in kwargs["data"]["query"]:
                raise RequestError(503)
            return result

        with self.assertRaises(RequestError):
            Linear("test", response_lost).status_comment(ISSUE, ISSUE, "merged")
        for _ in range(3):
            Linear("test", api.request).status_comment(ISSUE, ISSUE, "merged")
        self.assertEqual(api.comments[ISSUE], original)
        self.assertEqual(len(api.comments), 2)
        self.assertEqual(api.writes, [("create", replacement), ("update", replacement)])

    def test_transient_update_failure_does_not_create_a_replacement(self):
        api = CommentAPI()
        original = api.seed(ISSUE, actor=api.app_actor)

        def unavailable(url, **kwargs):
            if "commentUpdate(" in kwargs["data"]["query"]:
                raise RequestError(503)
            return api.request(url, **kwargs)

        with self.assertRaises(RequestError):
            Linear("test", unavailable).status_comment(ISSUE, ISSUE, "merged")
        self.assertEqual(api.comments, {ISSUE: original})
        Linear("test", api.request).status_comment(ISSUE, ISSUE, "merged")
        self.assertEqual(api.writes, [("update", ISSUE)])

    def test_pr_lookup_requires_the_exact_dispatch_branch_author_and_marker(self):
        route = Registry.load(ROOT / "automation/controller/projects.json").routes[PROJECT]
        for change in ({}, {"author": {"login": "someone", "__typename": "User"}},
                       {"body": "unrelated"}, {"headRefName": "another-branch"},
                       {"headRepository": {"nameWithOwner": "attacker/fork"}}):
            with self.subTest(change=change):
                client = GitHub("1", "unused", lambda url, **kw: [] if "/pulls?" in url else
                                {"data": {"repository": {"pullRequest": {**pr(), **change}}}})
                with patch.object(client, "installation_token", return_value="test-token") as token:
                    if change:
                        with self.assertRaises(RequestError):
                            client.worker_pull(route, receipt(), KEY, pr()["url"])
                    else:
                        self.assertEqual(client.worker_pull(route, receipt(), KEY, pr()["url"])["url"], pr()["url"])
                token.assert_called_once_with(route.repository, permissions={"contents": "read", "pull_requests": "read"})

    def test_pr_lookup_checks_all_pages_and_prefers_open_pr_over_stored_merged_url(self):
        route = Registry.load(ROOT / "automation/controller/projects.json").routes[PROJECT]
        pull = {**pr(), "url": "https://github.com/KianBahasadri/clusterfork/pull/999"}
        request = Mock(side_effect=[
            [{"number": n, "state": "closed", "merged_at": "earlier"} for n in range(1, 101)],
            [{"number": 999, "state": "open", "merged_at": None}],
            {"data": {"repository": {"pullRequest": pull}}}])
        client = GitHub("1", "unused", request)
        with patch.object(client, "installation_token", return_value="test-token"):
            self.assertEqual(client.worker_pull(route, receipt(), KEY, pr()["url"]), pull)
        self.assertEqual(request.call_args.kwargs["data"]["variables"]["number"], 999)

    def test_repair_pr_lookup_uses_parent_branch_and_parent_dispatch_marker(self):
        route = Registry.load(ROOT / "automation/controller/projects.json").routes[PROJECT]
        child_key = "d" * 64
        fields = {**receipt(), "identifier": "KIAN-1000", "is_auto_repair": True,
                  "target_dispatch_key": KEY, "target_branch": pr()["headRefName"]}
        request = Mock(side_effect=[[{"number": 1, "state": "open"}],
                                    {"data": {"repository": {"pullRequest": pr()}}}])
        client = GitHub("1", "unused", request)
        with patch.object(client, "installation_token", return_value="test-token"):
            self.assertEqual(client.worker_pull(route, fields, child_key), pr())
        self.assertIn("kian-891-aaaaaaaa", request.call_args_list[0].args[0])
        query = request.call_args.kwargs["data"]["query"]
        self.assertIn("detailsUrl", query)
        self.assertIn("targetUrl", query)

    def test_lifecycle_configuration_is_required_complete_and_distinct(self):
        data = json.loads((ROOT / "automation/controller/projects.json").read_text())
        original = data["projects"][0].pop("linear_states")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "projects.json"
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                Registry.load(path)
            for invalid in ({"blocked": original["blocked"]}, {**original, "done": original["blocked"]},
                            {**original, "done": data["projects"][0]["ready_state_ids"][0]},
                            {**original, "blocked": "not-a-uuid"}):
                data["projects"][0]["linear_states"] = invalid
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    Registry.load(path)


if __name__ == "__main__":
    unittest.main()
