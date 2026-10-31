"""Publication rejects credentials before any push, including recovery paths."""

import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "automation")]
import worker
from common.diagnostics import failure_summary, validate_failure
from publication_guard import PublicationBlocked, PublicationGuard, PublicationScanError

KEY = "a" * 64
BRANCH = "clusterfork/kian-908-aaaaaaaa"
URL = "https://github.com/owner/repo/pull/1"
CHECKOUT_TOKEN = "opaque-checkout-credential"
PUBLISH_TOKEN = "opaque-publication-credential"
AUTH = {"tokens": {"access_token": "opaque-access-credential", "refresh_token": "opaque-refresh-credential",
                   "account_id": "opaque-account-identity", "id_token": "opaque-id-credential"}}


class CredentialTrackingTests(unittest.TestCase):
    def test_probe_quota_and_agent_refreshes_survive_a_later_validation_refresh(self):
        broker = Mock(publication_guard=PublicationGuard({}))
        broker.call.side_effect = lambda action, **kw: {"auth": AUTH, "lease": "fixture"} if action == "claim" else {}
        limits = {"rateLimits": {"primary": {"usedPercent": 10, "resetsAt": time.time() + 1000}}}
        config = {"account_id": "assigned", "model": "gpt-6.1-sol", "effort": "max",
                  "selection": {"min_remaining_percent": 20, "max_snapshot_age_seconds": 60},
                  "issue": {"identifier": "KIAN-908", "title": "Improve worker", "url": "https://linear.app/example"}}
        homes = []
        def refresh(home, stage):
            (home / "auth.json").write_text(json.dumps({"tokens": {
                key: f"{stage}-{key}-credential" for key in AUTH["tokens"]}}))
        def inspect(home, *args):
            refresh(home, "probe" if not homes else "quota")
            homes.append(home)
            return {}, limits
        def agent(broker, config, home, *args, **kwargs):
            refresh(home, "agent")
            return "Updated files."
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(worker, "inspect_account", side_effect=inspect), \
             patch.object(worker, "run_agent", side_effect=agent), \
             patch.object(worker, "read_usage", return_value=limits):
            with worker.run_codex(broker, config, SimpleNamespace(failed=threading.Event()), Path(directory)):
                refresh(homes[-1], "validation")
                broker.publication_guard.refresh_auth()
                for stage in ("probe", "quota", "agent", "validation"):
                    for key in AUTH["tokens"]:
                        with self.subTest(stage=stage, key=key), self.assertRaises(PublicationBlocked):
                            broker.publication_guard.check(f"{stage}-{key}-credential", static=False)

    def test_all_auth_fields_are_retained_across_accounts_refreshes_and_cache_removal(self):
        broker = worker.Broker("https://clusterfork-controller-7604cac329.mexicocentral.cloudapp.azure.com/api/worker", KEY)
        broker.publication_guard = PublicationGuard({})
        broker.call = Mock(side_effect=lambda action, **kw: {"auth": AUTH, "lease": "fixture"})
        for index in range(2):
            with worker.account_lease(broker, str(index)) as (home, _):
                refreshed = {key: f"refreshed-{index}-{key}" for key in AUTH["tokens"]}
                (home / "auth.json").write_text(json.dumps({"tokens": refreshed}))
            self.assertFalse(home.exists())
            for value in refreshed.values():
                with self.subTest(value=value), self.assertRaises(PublicationBlocked):
                    broker.publication_guard.check(value, static=False)
        for value in AUTH["tokens"].values():
            with self.subTest(value=value), self.assertRaises(PublicationBlocked):
                broker.publication_guard.check(value, static=False)

    def test_live_cache_is_reread_before_publication_and_empty_or_non_string_values_are_ignored(self):
        guard = PublicationGuard({})
        guard.remember_auth({"access_token": "", "refresh_token": None, "id_token": 42, "account_id": False})
        guard.check("An ordinary change with token = value", static=False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            with guard.watch_auth(path):
                path.write_text(json.dumps(AUTH))
                guard.refresh_auth()
                path.write_text(json.dumps({"tokens": {"access_token": "latest-opaque-credential"}}))
                guard.refresh_auth()
        guard.refresh_auth()
        for value in [*AUTH["tokens"].values(), "latest-opaque-credential"]:
            with self.subTest(value=value), self.assertRaises(PublicationBlocked):
                guard.check(value, static=False)

    def test_broker_retains_every_oidc_and_github_token_including_refreshes(self):
        environment = {"ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.test/oidc",
                       "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "opaque-oidc-request-credential",
                       "GITHUB_TOKEN": "opaque-job-credential", "GH_TOKEN": "opaque-gh-credential"}
        payload = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 600}).encode()).decode().rstrip("=")
        oidc = [f"eyJhbGciOiJSUzI1NiJ9.{payload}.{suffix}" for suffix in ("first", "second")]
        with patch.dict(os.environ, environment, clear=True):
            broker = worker.Broker("https://clusterfork-controller-7604cac329.mexicocentral.cloudapp.azure.com/api/worker", KEY)
            with patch.object(worker, "json_request", side_effect=[{"value": oidc[0]}, {"token": CHECKOUT_TOKEN},
                                                                  {"value": oidc[1]}, {"token": PUBLISH_TOKEN}]):
                broker.call("checkout")
                broker.expires = 0
                broker.call("github")
        for value in [*oidc, CHECKOUT_TOKEN, PUBLISH_TOKEN, *list(environment.values())[1:]]:
            with self.subTest(value=value), self.assertRaises(PublicationBlocked):
                broker.publication_guard.check(value, static=False)

    def test_unreadable_auth_cache_has_no_sensitive_diagnostic_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "auth.json"
            path.write_text("private-malformed-auth-content")
            guard = PublicationGuard({})
            with guard.watch_auth(path), self.assertRaises(PublicationScanError) as caught:
                guard.refresh_auth()
            self.assertNotIn("private-malformed", str(caught.exception))
            self.assertNotIn(str(path), str(caught.exception))

    def test_only_high_confidence_patterns_block(self):
        guard = PublicationGuard({})
        tokens = ["github_pat_synthetic", "ghp_synthetic", "gho_synthetic", "ghu_synthetic", "ghs_synthetic",
                  "ghr_synthetic", "sk-synthetic-key", "lin_api_synthetic", "eyJsynthetic.payload.signature",
                  "-----BEGIN RSA PRIVATE KEY-----\nsynthetic\n-----END RSA PRIVATE KEY-----"]
        for token in tokens:
            with self.subTest(token=token), self.assertRaises(PublicationBlocked):
                guard.check(token)
        guard.check('token = value\nsecret: example\nauthorization: example\nBearer example\nBasic example\n'
                    'https://user:password@example.test/path')


class PublicationIntegrationTests(unittest.TestCase):
    def exercise(self, files=None, *, prior_files=None, seed=None, title="Improve worker", summary="Updated files.",
                 issue_url="https://linear.app/example", existing=False, repair=False, pr_title="Prior work", pr_body="",
                 expected_reason=None, validation=None, guard=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote, checkout = root / "remote.git", root / "checkout"
            def git(*args):
                return subprocess.check_output(["git", "-C", str(checkout), *args], text=True,
                                               stderr=subprocess.DEVNULL).strip()
            def write(files):
                for name, content in (files or {}).items():
                    path = checkout / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if content is None:
                        path.unlink()
                    elif isinstance(content, bytes):
                        path.write_bytes(content)
                    else:
                        path.write_text(content)
            subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)
            subprocess.run(["git", "clone", str(remote), str(checkout)], check=True, capture_output=True)
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            write({"file.txt": "original\n", **(seed or {})})
            git("add", ".")
            git("commit", "-m", "initial")
            git("push", "origin", "main")
            main = git("rev-parse", "HEAD")
            if prior_files:
                git("checkout", "-b", BRANCH)
                write(prior_files)
                git("add", ".")
                git("commit", "-m", "previous attempt")
                git("push", "origin", BRANCH)
                git("checkout", "main")
                git("branch", "-D", BRANCH)
            before = git("ls-remote", "origin", "refs/heads/" + BRANCH)
            broker = Mock(key=KEY, publication_guard=guard or PublicationGuard({}))
            broker.publication_guard.remember_auth(AUTH)
            broker.call.side_effect = lambda action, **kw: {"token": PUBLISH_TOKEN if action == "github" else CHECKOUT_TOKEN}
            config = {"repository": "owner/repo", "ref": "main", "auto_merge": True,
                      "issue": {"identifier": "KIAN-908", "title": title, "url": issue_url},
                      "validation_commands": [[sys.executable, "-c", validation or "pass"]]}
            if repair:
                config["repair"] = {"target_branch": BRANCH, "target_identifier": "KIAN-908", "target_dispatch_key": KEY}
            pull = {"html_url": URL, "title": pr_title, "body": pr_body + f"\n<!-- clusterfork-dispatch:{KEY} -->",
                    "state": "open", "user": {"login": "clusterfork[bot]"}, "base": {"ref": "main"},
                    "head": {"repo": {"full_name": "owner/repo"}, "ref": BRANCH,
                             "sha": before.split()[0] if before else main}}
            @contextmanager
            def implement(*args, validate):
                write(files)
                from test_remote_worker import validate_fake_turns
                validate_fake_turns(validate, summary)
                yield summary
            previous = Path.cwd()
            try:
                os.chdir(checkout)
                with patch.object(worker, "run_codex", side_effect=implement) as agent, \
                     patch.object(worker, "find_pr", return_value=URL if existing else None), \
                     patch.object(worker, "repair_check_output", return_value=""), \
                     patch.object(worker, "json_request", return_value=pull) as request, \
                     patch.object(worker, "request_auto_merge") as merge, \
                     patch.object(worker, "command", wraps=worker.command) as command:
                    if expected_reason:
                        with self.assertLogs(level="ERROR") as logged, self.assertRaises(worker.WorkerFailure) as caught:
                            worker.execute(broker, config, SimpleNamespace(failed=threading.Event()), root)
                        diagnostic = validate_failure(caught.exception.clusterfork_failure)
                        self.assertEqual(diagnostic["reason_code"], expected_reason)
                        self.assertEqual(diagnostic["operation"], "worker/publication_scan")
                        self.assertIn("publication blocked", failure_summary(diagnostic))
                        details = str(caught.exception) + json.dumps(diagnostic) + "\n".join(logged.output)
                        for value in [*AUTH["tokens"].values(), CHECKOUT_TOKEN, PUBLISH_TOKEN, "ghp_synthetic"]:
                            self.assertNotIn(value, details)
                        self.assertEqual(git("ls-remote", "origin", "refs/heads/" + BRANCH), before)
                        self.assertFalse(any(call.args[0][:2] == ["git", "push"] for call in command.call_args_list))
                        self.assertFalse(any(len(call.args) > 2 for call in request.call_args_list))
                        merge.assert_not_called()
                    else:
                        self.assertEqual(worker.execute(broker, config, SimpleNamespace(failed=threading.Event()), root),
                                         ("completed", URL))
                        merge.assert_called_once()
                        if not existing:
                            self.assertEqual(request.call_args.args[2]["title"], "KIAN-908: " + title)
                            self.assertIn(summary, request.call_args.args[2]["body"])
                    if existing and not repair:
                        agent.assert_not_called()
                self.assertEqual(git("ls-remote", "origin", "refs/heads/main").split()[0], main)
            finally:
                os.chdir(previous)

    def test_added_static_tokens_block_tracked_untracked_and_recovered_changes(self):
        for path in ("file.txt", "new-config.txt", "tests-copy/fixture.txt", "config\nquoted.txt"):
            for recovered in (False, True):
                with self.subTest(path=path, recovered=recovered):
                    self.exercise({} if recovered else {path: "ghp_synthetic\n"},
                                  prior_files={path: "ghp_synthetic\n"} if recovered else None,
                                  expected_reason="publication_credentials_detected")

    def test_runtime_values_block_in_tests_binary_files_and_removed_lines(self):
        for path in ("tests/fixture.py", "image.bin"):
            with self.subTest(path=path):
                content = b"\xff\x00" + AUTH["tokens"]["access_token"].encode() + b"\x00"
                self.exercise({path: content}, expected_reason="publication_credentials_detected")
        self.exercise({"file.txt": "removed live credential\n"},
                      seed={"file.txt": AUTH["tokens"]["account_id"] + "\n"},
                      expected_reason="publication_credentials_detected")

    def test_exact_values_with_newlines_block_in_test_fixtures(self):
        for removed in (False, True):
            with self.subTest(removed=removed):
                guard = PublicationGuard({})
                guard.remember("opaque-first-line\r\nopaque-second-line")
                self.exercise({"tests/fixture.txt": "ordinary\n" if removed else "opaque-first-line\r\nopaque-second-line\n"},
                              seed={"tests/fixture.txt": "opaque-first-line\r\nopaque-second-line\n"} if removed else None,
                              guard=guard, expected_reason="publication_credentials_detected")

    def test_test_fixtures_removed_static_tokens_and_generic_assignments_publish(self):
        self.exercise({"tests/nested/fixture.py": "ghp_synthetic\nlin_api_synthetic\n",
                       "tests/quoted\nfixture.py": "sk-synthetic-key\n",
                       "tests/key.pem": "-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----\n",
                       "file.txt": "token = value\nsecret: example\nBearer example\nBasic example\n"
                                   "https://user:password@example.test/path\n",
                       "image.bin": b"\xff\x00ordinary binary data\x00"},
                      seed={"file.txt": "ghp_old_fixture\n"})

    def test_multiline_private_key_in_added_lines_blocks(self):
        self.exercise({"key.pem": "-----BEGIN PRIVATE KEY-----\nsynthetic\n-----END PRIVATE KEY-----\n"},
                      expected_reason="publication_credentials_detected")

    def test_summary_title_and_generated_body_are_checked_before_push_without_truncation(self):
        for kwargs in ({"summary": "ghp_synthetic"}, {"title": "ghp_synthetic"},
                       {"summary": "s" * 40_001 + AUTH["tokens"]["refresh_token"]},
                       {"title": "t" * 260 + AUTH["tokens"]["id_token"]},
                       {"issue_url": "https://linear.app/" + CHECKOUT_TOKEN},
                       {"summary": PUBLISH_TOKEN}):
            with self.subTest(fields=list(kwargs)):
                self.exercise({"file.txt": "updated\n"}, expected_reason="publication_credentials_detected", **kwargs)

    def test_existing_pr_recovery_and_repair_check_metadata_and_committed_diff(self):
        for repair in (False, True):
            for kwargs in ({"pr_body": "ghp_synthetic"}, {"pr_title": PUBLISH_TOKEN},
                           {"prior_files": {"config.txt": AUTH["tokens"]["account_id"]}}):
                with self.subTest(repair=repair, fields=list(kwargs)):
                    self.exercise({"file.txt": "fixed\n"}, existing=True, repair=repair,
                                  expected_reason="publication_credentials_detected", **kwargs)

    def test_validation_changes_are_scanned_and_scan_errors_fail_closed(self):
        self.exercise({"file.txt": "updated\n"},
                      validation="from pathlib import Path; Path('generated.txt').write_text('ghp_synthetic')",
                      expected_reason="publication_credentials_detected")
        with tempfile.TemporaryDirectory() as directory:
            guard = PublicationGuard({})
            path = Path(directory) / "auth.json"
            path.write_text("malformed private cache")
            with guard.watch_auth(path):
                self.exercise({"file.txt": "updated\n"}, guard=guard, expected_reason="publication_scan_failed")


if __name__ == "__main__":
    unittest.main()
