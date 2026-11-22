"""Tests for Symphony Prometheus /metrics exposition and telemetry capture."""

import asyncio
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from threading import Thread
import sys
import unittest
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError
from urllib.request import urlopen

from symphony.__main__ import IdleHandler, Symphony
from symphony.metrics import render_metrics
from symphony.runner import Runner


REPO = "kianbahasadri/clusterfork"


class MetricsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runner = Runner(self.root, None)
        self.addCleanup(self.runner.db.close)
        with patch.dict(os.environ, {"LINEAR_API_KEY": "fixture"}), \
             patch("symphony.__main__.Runner", return_value=self.runner):
            self.app = Symphony(self.root)

    def _insert_task(self, issue, *, repo=REPO, phase="done", attempts=1):
        with self.runner.db:
            self.runner.db.execute(
                """INSERT INTO tasks(issue, project, repo, title, plan, phase, attempts, updated)
                   VALUES (?, 'proj', ?, 'Title', 'Plan', ?, ?, 100.0)""",
                (issue, repo, phase, attempts),
            )

    def test_empty_ledger_surfaces_unavailable_metrics_without_fabricating_zeros(self):
        fake_stat = SimpleNamespace(f_blocks=1000, f_frsize=4096, f_bavail=750, f_bfree=750)
        with patch("symphony.metrics.os.statvfs", return_value=fake_stat):
            text = render_metrics(self.app)

        self.assertTrue(text.endswith("\n"))
        self.assertIn("# TYPE symphony_up gauge\nsymphony_up 1\n", text)
        self.assertIn('symphony_tasks_by_phase{phase="working"} 0\n', text)
        self.assertIn('symphony_metric_available{metric="coordinator_tokens"} 0\n', text)
        self.assertIn('symphony_metric_available{metric="coordinator_cost_usd"} 0\n', text)
        self.assertIn('symphony_metric_available{metric="worker_tokens"} 0\n', text)
        self.assertIn('symphony_metric_available{metric="worker_cost_usd"} 0\n', text)
        self.assertIn('symphony_metric_available{metric="codex_rate_limits"} 0\n', text)
        self.assertIn('symphony_metric_available{metric="deployments"} 0\n', text)
        self.assertIn('symphony_metric_available{metric="pvc_disk"} 1\n', text)
        self.assertIn("symphony_pvc_bytes_total 4096000\n", text)
        self.assertIn("symphony_pvc_bytes_available 3072000\n", text)
        self.assertIn("symphony_pvc_bytes_used 1024000\n", text)
        self.assertNotIn("symphony_tokens_total", text)
        self.assertNotIn("symphony_estimated_spend_usd_total", text)
        self.assertNotIn("symphony_codex_rate_limit_remaining_percent", text)

        with patch("symphony.metrics.os.statvfs", side_effect=OSError("unavailable")):
            no_disk = render_metrics(self.app)
        self.assertIn('symphony_metric_available{metric="pvc_disk"} 0\n', no_disk)
        self.assertNotIn("symphony_pvc_bytes_total", no_disk)

    async def test_opencode_worker_records_step_finish_tokens_and_cost(self):
        self._insert_task("KIAN-1", phase="working", attempts=1)
        artifacts = self.runner.artifacts("KIAN-1")
        artifacts.mkdir(parents=True)

        worker_script = (
            "import json; "
            "print(json.dumps({'type':'step_finish','sessionID':'ses_w1',"
            "'part':{'messageID':'m1','reason':'tool-calls',"
            "'tokens':{'input':120,'output':30,'total':150},'cost':0.0025}})); "
            "print(json.dumps({'type':'step_finish','sessionID':'ses_w1',"
            "'part':{'messageID':'m2','reason':'stop',"
            "'tokens':{'input':80,'output':20}}}))"
        )
        with (artifacts / "worker.log").open("wb") as log:
            await self.runner.run_opencode(
                "KIAN-1",
                [sys.executable, "-c", worker_script, "--model", "opencode-go/deepseek-v4.1-flash#max"],
                self.root,
                os.environ,
                log,
            )

        rows = self.runner.rows("SELECT * FROM token_usage ORDER BY id")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source"], "worker")
        self.assertEqual((rows[0]["input_tokens"], rows[0]["output_tokens"], rows[0]["total_tokens"]),
                         (120, 30, 150))
        self.assertAlmostEqual(rows[0]["cost_usd"], 0.0025)
        self.assertIsNone(rows[1]["total_tokens"])
        self.assertIsNone(rows[1]["cost_usd"])

        text = render_metrics(self.app)
        self.assertIn(
            f'symphony_tokens_total{{source="worker",repo="{REPO}",model="opencode-go/deepseek-v4.1-flash#max",direction="input"}} 200\n',
            text,
        )
        self.assertIn(
            f'symphony_tokens_total{{source="worker",repo="{REPO}",model="opencode-go/deepseek-v4.1-flash#max",direction="output"}} 50\n',
            text,
        )
        self.assertIn(
            f'symphony_estimated_spend_usd_total{{source="worker",repo="{REPO}",model="opencode-go/deepseek-v4.1-flash#max"}} 0.0025\n',
            text,
        )
        self.assertIn('symphony_metric_available{metric="worker_tokens"} 1\n', text)
        self.assertIn('symphony_metric_available{metric="worker_cost_usd"} 1\n', text)

    async def test_coordinator_respond_records_latency_status_and_tokens_without_cost(self):
        self.app.coordinator.set_thread(REPO, "thr_1")
        self.app.coordinator.model = "gpt-5.4"

        async def fake_respond(repo, text, cwd):
            self.app.coordinator.notification("turn/started", {
                "threadId": "thr_1", "turn": {"id": "t1", "startedAt": 100},
            })
            self.app.coordinator.notification("thread/tokenUsage/updated", {
                "threadId": "thr_1",
                "tokenUsage": {
                    "modelContextWindow": 112000,
                    "last": {"inputTokens": 400, "outputTokens": 75, "totalTokens": 475},
                },
            })
            self.app.coordinator.notification("turn/completed", {
                "threadId": "thr_1", "turn": {"id": "t1", "status": "completed"},
            })
            return "completed"

        with patch.object(self.app, "context", AsyncMock(return_value=(self.root, ""))), \
             patch.object(self.app.coordinator, "respond", side_effect=fake_respond):
            outcome = await self.app.respond(REPO, "hello")
        self.assertEqual(outcome, "completed")

        with patch.object(self.app, "context", AsyncMock(return_value=(self.root, ""))), \
             patch.object(self.app.coordinator, "respond", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                await self.app.respond(REPO, "fail")

        turns = self.runner.rows("SELECT repo, model, status FROM coordinator_turns ORDER BY id")
        self.assertEqual(turns, [
            {"repo": REPO, "model": "gpt-5.4", "status": "completed"},
            {"repo": REPO, "model": "gpt-5.4", "status": "failed"},
        ])
        usage = self.runner.rows("SELECT * FROM token_usage")
        self.assertEqual(len(usage), 1)
        self.assertEqual(usage[0]["source"], "coordinator")
        self.assertIsNone(usage[0]["cost_usd"])

        text = render_metrics(self.app)
        self.assertIn(
            f'symphony_coordinator_turn_duration_seconds_count{{repo="{REPO}",model="gpt-5.4",status="completed"}} 1\n',
            text,
        )
        self.assertIn(
            f'symphony_coordinator_turn_duration_seconds_count{{repo="{REPO}",model="gpt-5.4",status="failed"}} 1\n',
            text,
        )
        self.assertIn(
            f'symphony_tokens_total{{source="coordinator",repo="{REPO}",model="gpt-5.4",direction="input"}} 400\n',
            text,
        )
        self.assertNotIn('source="coordinator",repo="' + REPO + '",model="gpt-5.4"}\n', text)
        self.assertIn('symphony_metric_available{metric="coordinator_tokens"} 1\n', text)
        self.assertIn('symphony_metric_available{metric="coordinator_cost_usd"} 0\n', text)

    def test_renders_rework_ci_deployments_pushes_rate_limits_and_escaped_labels(self):
        self._insert_task("KIAN-1", repo=REPO, phase="done", attempts=2)
        self._insert_task("KIAN-2", repo=REPO, phase="working", attempts=1)
        self.runner.record_execution("KIAN-1", "attempt", started=1, ended=2)
        self.runner.record_execution("KIAN-1", "attempt", started=3, ended=4)
        self.runner.record_execution("KIAN-1", "worker", started=10.0, ended=25.5, detail="ok")
        self.runner.record_execution(
            "KIAN-1", "publication", started=30.0, ended=32.0, detail="Pushed deadbeef to main.",
        )
        with self.runner.db:
            self.runner.db.execute(
                """INSERT INTO ci_runs(run_id, attempt, issue, repo, revision, workflow, path,
                                       status, conclusion, observed)
                   VALUES (1, 1, 'KIAN-1', ?, 'deadbeef', 'Deploy "prod"\\main\nv2', '.github/workflows/deploy.yml',
                           'completed', 'success', 50.0)""",
                (REPO,),
            )
            self.runner.db.execute(
                """INSERT INTO ci_runs(run_id, attempt, issue, repo, revision, workflow, path,
                                       status, conclusion, observed)
                   VALUES (2, 1, 'KIAN-2', ?, 'feedface', 'Tests', '.github/workflows/test.yml',
                           'in_progress', NULL, 51.0)""",
                (REPO,),
            )
        self.app.coordinator.rate_limits = {"5h": {"left": 88, "resetsAt": 999999}}

        text = render_metrics(self.app)
        self.assertIn(f'symphony_tasks_total{{repo="{REPO}"}} 2\n', text)
        self.assertIn(f'symphony_tasks_reworked_total{{repo="{REPO}"}} 1\n', text)
        self.assertIn(f'symphony_rework_attempts_total{{repo="{REPO}"}} 1\n', text)
        self.assertIn(f'symphony_worker_attempts_total{{repo="{REPO}"}} 3\n', text)
        self.assertIn(f'symphony_execution_duration_seconds_sum{{repo="{REPO}",kind="worker"}} 15.5\n', text)
        self.assertIn(f'symphony_execution_duration_seconds_count{{repo="{REPO}",kind="worker"}} 1\n', text)
        self.assertIn(f'symphony_pushes_total{{repo="{REPO}"}} 1\n', text)
        self.assertIn(f'symphony_ci_runs_total{{repo="{REPO}",conclusion="success"}} 1\n', text)
        self.assertIn(f'symphony_ci_runs_active{{repo="{REPO}"}} 1\n', text)
        self.assertIn(
            f'symphony_deployments_total{{repo="{REPO}",workflow="Deploy \\"prod\\"\\\\main\\nv2",conclusion="success"}} 1\n',
            text,
        )
        self.assertIn('symphony_codex_rate_limit_remaining_percent{window="5h"} 88\n', text)
        self.assertIn('symphony_metric_available{metric="deployments"} 1\n', text)
        self.assertIn('symphony_metric_available{metric="codex_rate_limits"} 1\n', text)

    async def test_idle_http_handler_serves_prometheus_metrics_on_loop_thread(self):
        loop = asyncio.get_running_loop()
        server = ThreadingHTTPServer(("127.0.0.1", 0), IdleHandler)
        server.loop = loop
        server.app = self.app
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        port = server.server_address[1]

        def fetch():
            with urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8")

        status, headers, body = await asyncio.to_thread(fetch)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "text/plain; version=0.0.4; charset=utf-8")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertIn("symphony_up 1\n", body)

        with patch.object(self.app, "metrics", side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaises(HTTPError) as err:
                await asyncio.to_thread(fetch)
        self.assertEqual(err.exception.code, 503)


if __name__ == "__main__":
    unittest.main()
