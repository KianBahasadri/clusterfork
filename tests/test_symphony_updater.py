"""Tests for Symphony's idle endpoint and idle-aware Guaranine updater CronJob."""

import asyncio
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import types
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

from symphony import updates
from symphony.__main__ import Handler, IdleHandler, Symphony
from symphony.runner import Runner


ROOT = Path(__file__).resolve().parents[1]
K8S_DIR = ROOT / "symphony/kubernetes"
DIGEST_OLD = "sha256:" + "a" * 64
DIGEST_NEW = "sha256:" + "b" * 64


def load_manifests(name):
    return [doc for doc in yaml.safe_load_all((K8S_DIR / name).read_text()) if doc]


def load_updater_module():
    docs = load_manifests("updater.yaml")
    config_map = next(doc for doc in docs if doc["kind"] == "ConfigMap")
    module = types.ModuleType("symphony_updater_from_yaml")
    exec(compile(config_map["data"]["updater.py"], "updater.yaml:updater.py", "exec"), module.__dict__)
    return module


class IdleEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runner = Runner(self.root, None)
        self.addCleanup(self.runner.db.close)
        with patch.dict(os.environ, {"LINEAR_API_KEY": "fixture"}), \
             patch("symphony.__main__.Runner", return_value=self.runner):
            self.app = Symphony(self.root)

    def _insert_task(self, phase):
        with self.runner.db:
            self.runner.db.execute(
                """INSERT INTO tasks(issue,project,repo,title,plan,phase,updated)
                   VALUES ('KIAN-1','proj','owner/repo','Title','Plan',?,0)""",
                (phase,),
            )

    def _set_phase(self, phase):
        with self.runner.db:
            self.runner.db.execute("UPDATE tasks SET phase=? WHERE issue='KIAN-1'", (phase,))

    async def test_idle_checks_coordinator_tasks_and_events(self):
        self.assertTrue(await self.app.idle())

        self.app.busy = True
        self.assertFalse(await self.app.idle())
        self.app.busy = False

        self.app.messages.append(("owner/repo", "hi", "hi"))
        self.assertFalse(await self.app.idle())
        self.app.messages.clear()

        self.app.coordinator.turns["owner/repo"] = "turn_1"
        self.assertFalse(await self.app.idle())
        self.app.coordinator.turns.clear()

        self._insert_task("working")
        for phase in ("approving", "working", "publishing", "ci", "finishing"):
            self._set_phase(phase)
            self.assertFalse(await self.app.idle(), f"{phase} should not be idle")

        for phase in ("approved", "review", "blocked", "finish_failed", "done"):
            self._set_phase(phase)
            self.assertTrue(await self.app.idle(), f"{phase} should be idle")

        with self.runner.db:
            self.runner.db.execute("INSERT INTO events(issue,detail,delivered) VALUES ('KIAN-1','Done.',0)")

        # Signed out: pump will not deliver events, so still idle.
        self.assertTrue(await self.app.idle())

        # Signed in and unpaused: undelivered event means coordinator is about to wake.
        self.app.coordinator.account = "chatgpt"
        self.assertFalse(await self.app.idle())

        # Paused: pump will not deliver events, so updates can still roll out.
        self.runner.save_setting("paused", "quota")
        self.assertTrue(await self.app.idle())

    async def test_draining_holds_unfinished_work_but_not_queued_todo(self):
        self._insert_task("approved")
        self.assertTrue(await self.app.idle())
        self.runner.draining = True
        for phase in ("working", "publishing", "ci", "finishing"):
            self._set_phase(phase)
            self.assertFalse(await self.app.idle(), f"{phase} should never be idle")
        for phase in ("review", "blocked", "finish_failed"):
            self._set_phase(phase)
            self.assertFalse(await self.app.idle(), f"{phase} should hold draining")
        for phase in ("approved", "done"):
            self._set_phase(phase)
            self.assertTrue(await self.app.idle(), f"{phase} should not hold draining")

        self.runner.draining = False
        self._set_phase("blocked")
        self.assertTrue(await self.app.idle())

    async def test_state_uses_cached_update_status_without_registry_requests(self):
        self.app.updates.available = True
        self.app.updates.release = {
            "runningCommit": "1" * 40,
            "availableCommit": "2" * 40,
            "compareUrl": f"https://github.com/KianBahasadri/clusterfork/compare/{'1' * 40}...{'2' * 40}",
            "commitCount": 1,
            "commits": ["KIAN-1132 Add release summary"],
        }
        self.runner.draining = True
        with patch.object(self.app.updates, "check", side_effect=AssertionError("registry request")), \
             patch.object(updates, "urlopen", side_effect=AssertionError("registry request")):
            state = await self.app.state(0)
        self.assertTrue(state["updateAvailable"])
        self.assertTrue(state["draining"])
        self.assertEqual(state["update"], self.app.updates.release)

    async def test_idle_http_handler(self):
        loop = asyncio.get_running_loop()
        server = ThreadingHTTPServer(("127.0.0.1", 0), IdleHandler)
        server.loop = loop
        server.app = self.app
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def fetch(path):
            with urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
                return resp.status, json.loads(resp.read())

        status, body = await asyncio.to_thread(fetch, "/idle")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"idle": True})

        self.app.busy = True
        status, body = await asyncio.to_thread(fetch, "/idle")
        self.assertEqual(status, 200)
        self.assertEqual(body, {"idle": False})

        with self.assertRaises(HTTPError) as ctx:
            await asyncio.to_thread(fetch, "/api/state")
        self.assertEqual(ctx.exception.code, 404)

    async def test_background_update_check_recovers_after_failure(self):
        calls = []

        def check():
            calls.append(1)
            if len(calls) == 1:
                raise OSError("registry unavailable")
            raise asyncio.CancelledError

        with patch.object(self.app.updates, "check", side_effect=check), \
             patch("symphony.__main__.UPDATE_CHECK_SECONDS", 0):
            with self.assertRaises(asyncio.CancelledError):
                await self.app.check_updates()
        self.assertEqual(len(calls), 2)


class DrainHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runner = Runner(self.root, None)
        self.addCleanup(self.runner.db.close)
        with patch.dict(os.environ, {"LINEAR_API_KEY": "fixture"}), \
             patch("symphony.__main__.Runner", return_value=self.runner):
            self.app = Symphony(self.root)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.loop = asyncio.get_running_loop()
        self.server.app = self.app
        thread = Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    async def send(self, payload=None, *, raw=None, headers=None, path="/api/drain"):
        data = json.dumps(payload).encode() if raw is None else raw
        request_headers = {
            "Content-Type": "application/json",
            "X-Symphony": "1",
            "Sec-Fetch-Site": "same-origin",
        }
        if headers is not None:
            request_headers = headers

        def post():
            request = Request(f"http://127.0.0.1:{self.server.server_address[1]}{path}",
                              data=data, method="POST", headers=request_headers)
            try:
                with urlopen(request, timeout=5) as response:
                    return response.status, json.loads(response.read())
            except HTTPError as error:
                with error:
                    return error.code, json.loads(error.read())

        return await asyncio.to_thread(post)

    async def test_valid_drain_requests_confirm_state_and_cancel_wakes_dispatch(self):
        with self.runner.db:
            self.runner.db.execute(
                """INSERT INTO tasks(issue,project,repo,title,plan,phase,updated)
                   VALUES ('KIAN-1','proj','owner/repo','Title','Plan','approved',0)"""
            )
        tasks = self.runner.rows("SELECT * FROM tasks")
        chat = self.runner.rows("SELECT * FROM chat")

        status, body = await self.send({"draining": True})
        self.assertEqual((status, body), (200, {"draining": True}))
        self.assertTrue(self.runner.draining)
        self.assertTrue((await self.app.state(0))["draining"])
        self.assertEqual(self.runner.rows("SELECT * FROM tasks"), tasks)
        self.assertEqual(self.runner.rows("SELECT * FROM chat"), chat)
        self.assertFalse(self.app.busy)

        self.runner.wake.clear()
        status, body = await self.send({"draining": False})
        self.assertEqual((status, body), (200, {"draining": False}))
        self.assertFalse(self.runner.draining)
        self.assertTrue(self.runner.wake.is_set())
        self.assertEqual(self.runner.rows("SELECT * FROM tasks"), tasks)

    async def test_malformed_drain_requests_are_rejected_without_changing_state(self):
        payloads = [
            {}, {"draining": 1}, {"draining": "true"}, {"draining": None},
            {"draining": True, "extra": 1}, [], "true",
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                status, body = await self.send(payload)
                self.assertEqual(status, 400)
                self.assertIn("error", body)
                self.assertFalse(self.runner.draining)
        for raw in (b"not json", b'{"draining":true'):
            with self.subTest(raw=raw):
                status, body = await self.send(raw=raw)
                self.assertEqual(status, 400)
                self.assertFalse(self.runner.draining)
        status, _ = await self.send({"draining": True, "pad": "x" * 2000})
        self.assertEqual(status, 400)
        self.assertFalse(self.runner.draining)

    async def test_drain_request_requires_the_chat_same_origin_policy(self):
        headers = [
            {"Content-Type": "application/json", "Sec-Fetch-Site": "same-origin"},
            {"Content-Type": "text/plain", "X-Symphony": "1", "Sec-Fetch-Site": "same-origin"},
            {"Content-Type": "application/json", "X-Symphony": "1", "Sec-Fetch-Site": "cross-site"},
        ]
        for case in headers:
            with self.subTest(headers=case):
                status, body = await self.send({"draining": True}, headers=case)
                self.assertEqual(status, 403)
                self.assertIn("error", body)
                self.assertFalse(self.runner.draining)
        status, _ = await self.send({"draining": True}, path="/api/other")
        self.assertEqual(status, 404)


class UpdateCheckTests(unittest.TestCase):
    commit = "1" * 40

    def test_missing_or_invalid_build_identity_never_claims_an_update(self):
        for value in ("", "main", "a" * 39, "a" * 41, "g" * 40, "sha256:" + "a" * 64):
            with self.subTest(value=value), \
                 patch.dict(os.environ, {"SYMPHONY_COMMIT": value}), \
                 patch.object(updates, "urlopen") as open_url:
                checker = updates.UpdateChecker()
                self.assertFalse(checker.check())
                self.assertFalse(checker.available)
                open_url.assert_not_called()

    def test_matching_and_differing_digests_cache_the_running_digest(self):
        calls = []

        def digest(token, reference):
            calls.append(reference)
            return DIGEST_OLD if reference == self.commit else DIGEST_NEW

        with patch.dict(os.environ, {"SYMPHONY_COMMIT": self.commit.upper()}), \
             patch.object(updates, "ghcr_token", return_value="token"), \
             patch.object(updates, "ghcr_digest", side_effect=digest), \
             patch.object(updates, "ghcr_commit_for_digest", return_value="") as commit_lookup:
            checker = updates.UpdateChecker()
            self.assertTrue(checker.check())
            self.assertTrue(checker.check())
        self.assertEqual(calls, [self.commit, "main", "main"])
        commit_lookup.assert_called_once_with("token", DIGEST_NEW)

        with patch.dict(os.environ, {"SYMPHONY_COMMIT": self.commit}), \
             patch.object(updates, "ghcr_token", return_value="token"), \
             patch.object(updates, "ghcr_digest", return_value=DIGEST_OLD):
            checker = updates.UpdateChecker()
            self.assertFalse(checker.check())

    def test_initial_digest_failures_do_not_claim_an_update(self):
        checker = updates.UpdateChecker()
        with patch.dict(os.environ, {"SYMPHONY_COMMIT": self.commit}), \
             patch.object(updates, "ghcr_token", return_value="token"), \
             patch.object(updates, "ghcr_digest", side_effect=URLError("down")):
            with self.assertRaises(URLError):
                checker.check()
        self.assertFalse(checker.available)
        self.assertEqual(checker.running_digest, "")

    def test_transient_failures_preserve_the_last_confirmed_result(self):
        checker = updates.UpdateChecker()
        checker.running_digest = DIGEST_OLD
        checker.available = True
        with patch.dict(os.environ, {"SYMPHONY_COMMIT": self.commit}), \
             patch.object(updates, "ghcr_token", return_value="token"), \
             patch.object(updates, "ghcr_digest", side_effect=TimeoutError("slow")):
            with self.assertRaises(TimeoutError):
                checker.check()
        self.assertTrue(checker.available)

        with patch.dict(os.environ, {"SYMPHONY_COMMIT": self.commit}), \
             patch.object(updates, "ghcr_token", side_effect=URLError("down")):
            with self.assertRaises(URLError):
                checker.check()
        self.assertTrue(checker.available)

        with patch.dict(os.environ, {"SYMPHONY_COMMIT": self.commit}), \
             patch.object(updates, "ghcr_token", return_value="token"), \
             patch.object(updates, "ghcr_digest", return_value=DIGEST_OLD):
            self.assertFalse(checker.check())
        self.assertFalse(checker.available)

    def test_malformed_digest_and_empty_token_are_rejected(self):
        class FakeResponse:
            def __init__(self, body=b"", headers=None):
                self.body = body
                self.headers = headers or {}

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with patch.object(updates, "urlopen", return_value=FakeResponse(
                headers={"Docker-Content-Digest": "sha256:not-hex"})):
            with self.assertRaisesRegex(RuntimeError, "Invalid GHCR digest"):
                updates.ghcr_digest("token", "main")
        with patch.object(updates, "urlopen", return_value=FakeResponse(body=b"{}")):
            with self.assertRaisesRegex(RuntimeError, "empty pull token"):
                updates.ghcr_token()
        with patch.object(updates, "urlopen", side_effect=TimeoutError("slow")):
            with self.assertRaises(TimeoutError):
                updates.ghcr_token()

    def test_release_metadata_supports_single_and_multiarch_caches_per_digest_and_degrades(self):
        available = "2" * 40
        child_digest = "sha256:" + "c" * 64
        att_digest = "sha256:" + "d" * 64
        cfg_digest = "sha256:" + "e" * 64

        class FakeResponse:
            def __init__(self, body=b"", headers=None):
                self.body = body
                self.headers = headers or {}

            def read(self):
                return self.body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        # Multi-arch index skips attestation manifest, picks linux/amd64, resolves SYMPHONY_COMMIT, and caches per digest.
        requests = []

        def fake_urlopen(req, timeout=15):
            url = req.full_url if isinstance(req, Request) else req
            requests.append(url)
            if url.endswith("/manifests/" + DIGEST_NEW):
                return FakeResponse(json.dumps({"manifests": [
                    {"digest": att_digest, "annotations": {"vnd.docker.reference.type": "attestation-manifest"}},
                    {"digest": "sha256:" + "f" * 64, "platform": {"os": "linux", "architecture": "arm64"}},
                    {"digest": child_digest, "platform": {"os": "linux", "architecture": "amd64"}},
                ]}).encode())
            if url.endswith("/manifests/" + child_digest):
                return FakeResponse(json.dumps({"config": {"digest": cfg_digest}}).encode())
            if url.endswith("/blobs/" + cfg_digest):
                return FakeResponse(json.dumps({"config": {"Env": [
                    "PATH=/usr/local/bin",
                    f"SYMPHONY_COMMIT={available.upper()}",
                ]}}).encode())
            if f"/compare/{self.commit}...{available}" in url:
                self.assertEqual(req.headers.get("Authorization"), "Bearer gh-tok")
                return FakeResponse(json.dumps({
                    "total_commits": 2,
                    "commits": [
                        {"commit": {"message": "First commit\n\nBody"}},
                        {"commit": {"message": "Second commit\n\nDetails"}},
                    ],
                }).encode())
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.dict(os.environ, {"SYMPHONY_COMMIT": self.commit, "GITHUB_TOKEN": "gh-tok"}), \
             patch.object(updates, "ghcr_token", return_value="token"), \
             patch.object(updates, "ghcr_digest", side_effect=lambda _t, ref: DIGEST_OLD if ref == self.commit else DIGEST_NEW), \
             patch.object(updates, "urlopen", side_effect=fake_urlopen):
            checker = updates.UpdateChecker()
            self.assertTrue(checker.check())
            first_requests = list(requests)
            self.assertTrue(checker.check())
            self.assertEqual(requests, first_requests)

        self.assertEqual(checker.details(), {
            "runningCommit": self.commit,
            "availableCommit": available,
            "compareUrl": f"https://github.com/KianBahasadri/clusterfork/compare/{self.commit}...{available}",
            "commitCount": 2,
            "commits": ["Second commit", "First commit"],
        })

        # Single-arch manifest where GitHub compare fails still exposes compareUrl cleanly.
        with patch.dict(os.environ, {"SYMPHONY_COMMIT": self.commit}), \
             patch.object(updates, "ghcr_json", side_effect=[
                 {"config": {"digest": cfg_digest}},
                 {"config": {"Env": [f"SYMPHONY_COMMIT={available}"]}},
             ]), \
             patch.object(updates, "ghcr_token", return_value="token"), \
             patch.object(updates, "ghcr_digest", side_effect=lambda _t, ref: DIGEST_OLD if ref == self.commit else DIGEST_NEW), \
             patch.object(updates, "github_compare", side_effect=URLError("rate limited")):
            degraded = updates.UpdateChecker()
            self.assertTrue(degraded.check())
        self.assertEqual(degraded.details()["compareUrl"],
                         f"https://github.com/KianBahasadri/clusterfork/compare/{self.commit}...{available}")
        self.assertEqual(degraded.details()["commits"], [])
        self.assertIsNone(degraded.details()["commitCount"])

        # Config lookup failure degrades gracefully while keeping available=True.
        with patch.dict(os.environ, {"SYMPHONY_COMMIT": self.commit}), \
             patch.object(updates, "ghcr_token", return_value="token"), \
             patch.object(updates, "ghcr_digest", side_effect=lambda _t, ref: DIGEST_OLD if ref == self.commit else DIGEST_NEW), \
             patch.object(updates, "ghcr_commit_for_digest", side_effect=URLError("down")):
            no_cfg = updates.UpdateChecker()
            self.assertTrue(no_cfg.check())
        self.assertIsNone(no_cfg.details())


class UpdaterScriptTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_updater_module()
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sa_dir = Path(self.temp.name)
        (self.sa_dir / "namespace").write_text("symphony\n")
        (self.sa_dir / "token").write_text("sa-token\n")
        (self.sa_dir / "ca.crt").write_text("dummy-ca\n")
        self.mod.SA_DIR = self.sa_dir

    def _deployment(self, image, *, replicas=1, ready=1):
        return {
            "spec": {
                "replicas": replicas,
                "template": {
                    "spec": {
                        "containers": [
                            {"name": "symphony", "image": image},
                            {"name": "tailscale", "image": "tailscale/tailscale:v1.104.7"},
                        ],
                    },
                },
            },
            "status": {"readyReplicas": ready},
        }

    def _run(self, deployment, *, remote_digest=DIGEST_NEW, tag_digests=None, idle=True):
        patches = []
        tag_digests = dict(tag_digests or {})
        tag_digests["main"] = remote_digest

        def fake_k8s(method, url, token, context, body=None, content_type=None):
            if method == "GET":
                return deployment
            if method == "PATCH":
                patches.append((url, body, content_type))
                return deployment
            raise AssertionError(f"Unexpected method {method}")

        def fake_digest(token, ref):
            if ref not in tag_digests:
                raise URLError("not found")
            return tag_digests[ref]

        def fake_idle():
            if isinstance(idle, Exception):
                raise idle
            return idle

        with patch.dict(os.environ, {"KUBERNETES_SERVICE_HOST": "10.43.0.1", "KUBERNETES_SERVICE_PORT": "443"}), \
             patch.object(self.mod.ssl, "create_default_context", return_value=object()), \
             patch.object(self.mod, "ghcr_token", return_value="ghcr-tok"), \
             patch.object(self.mod, "ghcr_digest", side_effect=fake_digest), \
             patch.object(self.mod, "k8s_request", side_effect=fake_k8s), \
             patch.object(self.mod, "check_idle", side_effect=fake_idle):
            rc = self.mod.main()
        return rc, patches

    def test_noop_when_digest_already_pinned_and_current(self):
        rc, patches = self._run(self._deployment(f"ghcr.io/kianbahasadri/symphony@{DIGEST_NEW}"))
        self.assertEqual(rc, 0)
        self.assertEqual(patches, [])

    def test_noop_when_commit_sha_tag_matches_main_digest(self):
        sha = "3ae572c12180322034732181ec805ad994d3c795"
        rc, patches = self._run(
            self._deployment(f"ghcr.io/kianbahasadri/symphony:{sha}"),
            tag_digests={sha: DIGEST_NEW},
        )
        self.assertEqual(rc, 0)
        self.assertEqual(patches, [])

    def test_patches_when_digest_changed_and_idle(self):
        rc, patches = self._run(
            self._deployment(f"ghcr.io/kianbahasadri/symphony@{DIGEST_OLD}"),
            idle=True,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(patches), 1)
        url, body, content_type = patches[0]
        self.assertEqual(url, "https://10.43.0.1:443/apis/apps/v1/namespaces/symphony/deployments/symphony")
        self.assertEqual(content_type, "application/strategic-merge-patch+json")
        self.assertEqual(
            body,
            {"spec": {"template": {"spec": {"containers": [
                {"name": "symphony", "image": f"ghcr.io/kianbahasadri/symphony@{DIGEST_NEW}"},
            ]}}}},
        )

    def test_pins_main_tag_to_digest_when_idle(self):
        rc, patches = self._run(self._deployment("ghcr.io/kianbahasadri/symphony:main"), idle=True)
        self.assertEqual(rc, 0)
        self.assertEqual(len(patches), 1)

    def test_defers_when_symphony_is_busy(self):
        rc, patches = self._run(
            self._deployment(f"ghcr.io/kianbahasadri/symphony@{DIGEST_OLD}"),
            idle=False,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(patches, [])

    def test_defers_when_idle_check_fails_and_pod_is_ready(self):
        rc, patches = self._run(
            self._deployment(f"ghcr.io/kianbahasadri/symphony@{DIGEST_OLD}", ready=1),
            idle=URLError("connection refused"),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(patches, [])

    def test_rolls_forward_when_idle_check_fails_and_zero_ready_replicas(self):
        rc, patches = self._run(
            self._deployment(f"ghcr.io/kianbahasadri/symphony@{DIGEST_OLD}", ready=0),
            idle=URLError("connection refused"),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(patches), 1)

    def test_skips_when_deployment_scaled_to_zero(self):
        rc, patches = self._run(
            self._deployment(f"ghcr.io/kianbahasadri/symphony@{DIGEST_OLD}", replicas=0, ready=0),
            idle=URLError("connection refused"),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(patches, [])

    def test_rejects_malformed_ghcr_digest(self):
        class FakeResponse:
            headers = {"Docker-Content-Digest": "sha256:not-hex"}
            def __enter__(self): return self
            def __exit__(self, *args): return False

        with patch.object(self.mod, "urlopen", return_value=FakeResponse()):
            with self.assertRaises(RuntimeError):
                self.mod.ghcr_digest("tok", "main")


class KubernetesManifestTests(unittest.TestCase):
    def test_symphony_pod_remains_without_k8s_credentials_and_api_egress(self):
        deploy = load_manifests("deployment.yaml")[0]
        self.assertIs(deploy["spec"]["template"]["spec"]["automountServiceAccountToken"], False)

        netpols = {d["metadata"]["name"]: d for d in load_manifests("storage-and-network.yaml") if d["kind"] == "NetworkPolicy"}
        self.assertIn("default-deny", netpols)
        sym = netpols["symphony"]
        self.assertEqual(sym["spec"]["podSelector"], {"matchLabels": {"app": "symphony"}})
        self.assertEqual(sym["spec"]["ingress"], [
            {"from": [{"podSelector": {"matchLabels": {"app": "symphony-updater"}}}],
             "ports": [{"port": 8081, "protocol": "TCP"}]},
            {"from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}},
                       "podSelector": {"matchLabels": {"app": "prometheus"}}}],
             "ports": [{"port": 8081, "protocol": "TCP"}]},
            {"from": [{"podSelector": {"matchLabels": {"app": "symphony-webhook"}}}],
             "ports": [{"port": 8082, "protocol": "TCP"}]},
        ])
        webhook = netpols["symphony-webhook"]
        self.assertEqual(webhook["spec"]["podSelector"], {"matchLabels": {"app": "symphony-webhook"}})
        self.assertEqual(webhook["spec"]["policyTypes"], ["Egress"])
        self.assertIn({"to": [{"podSelector": {"matchLabels": {"app": "symphony"}}}],
                       "ports": [{"port": 8082, "protocol": "TCP"}]}, webhook["spec"]["egress"])

        service = next(d for d in load_manifests("storage-and-network.yaml") if d["kind"] == "Service")
        self.assertEqual(service["metadata"]["name"], "symphony")
        self.assertEqual(service["spec"]["selector"], {"app": "symphony"})
        self.assertEqual(service["spec"]["ports"], [
            {"name": "metrics", "port": 8081, "targetPort": 8081, "protocol": "TCP"},
            {"name": "webhook", "port": 8082, "targetPort": 8082, "protocol": "TCP"},
        ])

    def test_webhook_gateway_is_separate_and_exposes_only_the_linear_route(self):
        docs = load_manifests("webhook-gateway.yaml")
        by_kind = {doc["kind"]: doc for doc in docs}

        config = json.loads(by_kind["ConfigMap"]["data"]["serve.json"])
        self.assertTrue(config["AllowFunnel"]["${TS_CERT_DOMAIN}:443"])
        self.assertEqual(config["Web"]["${TS_CERT_DOMAIN}:443"]["Handlers"], {
            "/webhooks/linear": {"Proxy": "http://symphony.symphony.svc.cluster.local:8082"},
        })

        deploy = by_kind["Deployment"]
        self.assertEqual(deploy["metadata"]["name"], "symphony-webhook")
        self.assertEqual(deploy["spec"]["template"]["metadata"]["labels"], {"app": "symphony-webhook"})
        pod = deploy["spec"]["template"]["spec"]
        self.assertIs(pod["automountServiceAccountToken"], False)
        self.assertEqual(pod["securityContext"]["runAsNonRoot"], True)
        self.assertEqual(pod["securityContext"]["seccompProfile"], {"type": "RuntimeDefault"})
        container = pod["containers"][0]
        self.assertIn("@sha256:", container["image"])
        self.assertEqual(container["securityContext"]["allowPrivilegeEscalation"], False)
        self.assertEqual(container["securityContext"]["readOnlyRootFilesystem"], True)
        self.assertEqual(container["securityContext"]["capabilities"], {"drop": ["ALL"]})
        env = {item["name"]: item for item in container["env"]}
        self.assertEqual(env["TS_USERSPACE"]["value"], "true")
        self.assertEqual(env["TS_AUTHKEY"]["valueFrom"]["secretKeyRef"],
                         {"name": "symphony-webhook-tailnet", "key": "authkey"})

        private = load_manifests("storage-and-network.yaml")
        private_serve = json.loads(next(d for d in private if d["kind"] == "ConfigMap")["data"]["serve.json"])
        self.assertFalse(private_serve["AllowFunnel"]["${TS_CERT_DOMAIN}:443"])
        self.assertEqual(private_serve["Web"]["${TS_CERT_DOMAIN}:443"]["Handlers"]["/"]["Proxy"],
                         "http://127.0.0.1:8080")

        symphony = load_manifests("deployment.yaml")[0]
        ports = {port["name"]: port["containerPort"]
                 for port in symphony["spec"]["template"]["spec"]["containers"][0]["ports"]}
        self.assertEqual(ports["webhook"], 8082)

    def test_updater_rbac_and_pod_security_restricted(self):
        docs = load_manifests("updater.yaml")
        by_kind = {doc["kind"]: doc for doc in docs}

        role = by_kind["Role"]
        self.assertEqual(
            role["rules"],
            [{"apiGroups": ["apps"], "resources": ["deployments"], "resourceNames": ["symphony"], "verbs": ["get", "patch"]}],
        )

        cron = by_kind["CronJob"]
        self.assertEqual(cron["spec"]["concurrencyPolicy"], "Forbid")
        pod_spec = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        self.assertEqual(pod_spec["securityContext"]["runAsNonRoot"], True)
        self.assertEqual(pod_spec["securityContext"]["seccompProfile"], {"type": "RuntimeDefault"})
        container = pod_spec["containers"][0]
        self.assertIn("@sha256:", container["image"])
        self.assertEqual(container["securityContext"]["allowPrivilegeEscalation"], False)
        self.assertEqual(container["securityContext"]["readOnlyRootFilesystem"], True)
        self.assertEqual(container["securityContext"]["capabilities"], {"drop": ["ALL"]})


if __name__ == "__main__":
    unittest.main()
