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
from urllib.request import urlopen

import yaml

from symphony.__main__ import IdleHandler, Symphony
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

        self.app.coordinator.turn = "turn_1"
        self.assertFalse(await self.app.idle())
        self.app.coordinator.turn = ""

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
        self.assertEqual(service["spec"]["ports"],
                         [{"name": "webhook", "port": 8082, "targetPort": 8082, "protocol": "TCP"}])

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
