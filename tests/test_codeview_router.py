"""End-to-end tests for codeview.server: boot on a temp repo, hit routes over
real HTTP, verify sections/tabs/module dispatch/staleness handling."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from codeview import server as srv  # noqa: E402
from codeview import cachestore  # noqa: E402


def git(cmd, cwd):
    import subprocess
    return subprocess.run(["git", "-C", str(cwd), *cmd],
                          capture_output=True, text=True, check=True).stdout


def get(url):
    with urllib.request.urlopen(url, timeout=5) as res:
        return res.status, res.read()


class ServerBootTestCase(unittest.TestCase):
    """Boots the real ThreadingHTTPServer once for all route assertions."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        repo = Path(cls.tmp.name).resolve()
        git(["init", "-q"], repo)
        (repo / "app.py").write_text("print('hi')\n")
        git(["add", "-A"], repo)
        git(["-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-qm", "c1"], repo)
        # One good module + one broken module.
        mods_dir = repo / ".codeview" / "modules"
        mods_dir.mkdir(parents=True)
        (mods_dir / "hello.py").write_text(
            'NAME = "hello"\n'
            'def register(reg):\n'
            '    reg.add_route("GET", "",\n'
            '                  lambda r: (200, "<h1>hello tab</h1>", '
            '"text/html"))\n')
        (mods_dir / "broken.py").write_text("def register(reg)\n")

        cls.shape = srv.scan.RepoShape(max_commits=50)
        cls.repo = repo
        cls.codeview_dir = srv.ensure_codeview_dir(repo)
        cls.state = srv.AppState()
        srv.scan_all(repo, cls.shape, cls.codeview_dir, cls.state, force=True)
        srv.persist_state(cls.codeview_dir, cls.state)
        mods = srv.modules_rt.load_modules(mods_dir)

        httpd = srv.bind_with_retry(0)  # ephemeral port
        port = httpd.server_address[1]
        httpd.app = {
            "state": cls.state,
            "modules": mods,
            "module_routes": srv.build_module_table(mods),
            "repo": repo,
            "shape": cls.shape,
            "codeview_dir": cls.codeview_dir,
            "verbose": False,
        }
        httpd._section_re = __import__("re").compile(
            r"^/api/section/(%s)$" % "|".join(srv.SCAN_SECTIONS))
        cls.httpd = httpd
        cls.port = port
        cls.thread = threading.Thread(target=httpd.serve_forever,
                                      kwargs={"poll_interval": 0.1},
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    # ------------------------------------------------------------- tests --

    def test_index_served(self):
        status, body = get(self.url("/"))
        self.assertEqual(status, 200)
        self.assertIn(b"codeview", body)
        self.assertIn(b'data-panel="churn"', body)

    def test_assets_traversal_guard(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            get(self.url("/assets/../scan.py"))
        self.assertEqual(ctx.exception.code, 403)

    def test_assets_served_with_correct_mime(self):
        # Regression: suffix lookup used dotless keys ("css") against
        # dotted ones (".css"), so every asset fell through to
        # application/octet-stream and browsers refused the stylesheet.
        with urllib.request.urlopen(self.url("/assets/app.css"),
                                    timeout=5) as res:
            self.assertEqual(res.status, 200)
            self.assertEqual(res.headers["Content-Type"],
                             "text/css; charset=utf-8")

    def test_summary_endpoint(self):
        status, body = get(self.url("/api/summary"))
        data = json.loads(body)
        self.assertEqual(data["meta"]["repo_name"], self.repo.name)
        self.assertGreater(data["total_lines"], 0)
        self.assertIn("metric_totals", data)
        self.assertGreaterEqual(data["metric_totals"]["code_lines"], 1)
        self.assertEqual(data["commits_count"], 1)
        # Temp repo has no GitHub origin + no CI fetch ran: no CI to report.
        self.assertIsNone(data["ci"])

    def test_section_endpoints(self):
        for section in ("meta", "history", "files", "deps"):
            status, body = get(self.url(f"/api/section/{section}"))
            self.assertEqual(status, 200)
            data = json.loads(body)
            self.assertTrue(data or data == [])
            if section == "history":
                self.assertEqual(data["churn"]["file_count"], 1)
                self.assertEqual(data["churn"]["files"][0]["path"],
                                 "app.py")

    def test_tabs_include_modules_and_broken_flag(self):
        _, body = get(self.url("/api/tabs"))
        tabs = json.loads(body)["tabs"]
        names_kinds = {(t["name"], t["kind"]) for t in tabs}
        self.assertIn(("overview", "core"), names_kinds)
        self.assertIn(("churn", "core"), names_kinds)
        self.assertIn(("hello", "module"), names_kinds)
        self.assertIn(("broken", "broken"), names_kinds)
        self.assertIn(("logs", "core"), names_kinds)
        self.assertEqual(
            [t["name"] for t in tabs if t["kind"] == "core"],
            ["overview", "history", "churn", "files", "deps", "logs"],
        )
        # Server logs is a core tab and sits after all module tabs.
        self.assertEqual(tabs[-1]["name"], "logs")

    def test_logs_endpoint(self):
        status, body = get(self.url("/api/logs"))
        self.assertEqual(status, 200)
        self.assertIsInstance(json.loads(body)["logs"], list)

    def test_file_endpoint_serves_content_and_stats(self):
        status, body = get(self.url("/api/file?path=app.py"))
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertIn("print('hi')", data["content"])
        self.assertEqual(data["lang"], "Python")
        self.assertEqual(data["stats"]["commits"], 1)
        self.assertEqual(data["stats"]["last_commit"]["subject"], "c1")
        self.assertFalse(data["binary"])
        self.assertTrue(data["metrics"]["analyzed"])
        self.assertEqual(data["metrics"]["total_lines"], 1)
        self.assertEqual(data["metrics"]["comment_lines"], 0)

    def test_file_endpoint_404s_untracked(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            get(self.url("/api/file?path=missing.py"))
        self.assertEqual(ctx.exception.code, 404)

    def test_file_endpoint_rejects_traversal(self):
        import urllib.error
        for evil in ("../../etc/passwd", "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
                     "app.py%00extra"):
            with self.assertRaises(urllib.error.HTTPError,
                                   msg=evil) as ctx:
                get(self.url(f"/api/file?path={evil}"))
            self.assertEqual(ctx.exception.code, 404)

    def test_module_route_dispatches(self):
        status, body = get(self.url("/m/hello/"))
        self.assertEqual(status, 200)
        self.assertIn(b"<h1>hello tab</h1>", body)

    def test_broken_module_landing_page_shows_error(self):
        status, body = get(self.url("/m/broken/"))
        self.assertEqual(status, 200)
        self.assertIn(b"broken module", body)
        self.assertIn(b"SyntaxError", body)

    def test_unknown_module_404(self):
        with self.assertRaises(urllib.error.HTTPError):
            get(self.url("/m/nope/"))

    def test_unknown_api_404(self):
        with self.assertRaises(urllib.error.HTTPError):
            get(self.url("/api/nonsense"))

    def test_gen_endpoint_shape(self):
        _, body = get(self.url("/api/gen"))
        data = json.loads(body)
        self.assertIsInstance(data["generation"], int)
        self.assertIn("rescanned_at", data)

    def test_reload_endpoint_forces_rescan_and_bumps_generation(self):
        _, before = get(self.url("/api/gen"))
        gen0 = json.loads(before)["generation"]
        req = urllib.request.Request(self.url("/api/reload"),
                                     data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=60) as res:
            data = json.loads(res.read())
        self.assertTrue(data["ok"])
        self.assertGreater(data["generation"], gen0)
        self.assertIsInstance(data["seconds"], (int, float))
        _, after = get(self.url("/api/gen"))
        self.assertEqual(json.loads(after)["generation"], data["generation"])

    def test_daemon_file_write_and_gated_remove(self):
        daemon = srv.daemon_file(self.repo)
        self.assertFalse(daemon.exists())
        srv.write_daemon_file(self.repo, port=47123, max_commits=50,
                              watch=True)
        data = json.loads(daemon.read_text())
        self.assertEqual(data["port"], 47123)
        self.assertEqual(data["pid"], os.getpid())
        self.assertTrue(data["watch"])
        # A successor pid's entry must survive our cleanup.
        data["pid"] = data["pid"] + 1
        daemon.write_text(json.dumps(data))
        srv.remove_daemon_file(self.repo)
        self.assertTrue(daemon.exists())
        data["pid"] = os.getpid()
        daemon.write_text(json.dumps(data))
        srv.remove_daemon_file(self.repo)
        self.assertFalse(daemon.exists())

    def test_persist_then_reload_roundtrip(self):
        # Cache files exist and reload into a fresh state identically.
        state2 = srv.AppState()
        sections, fps = srv.read_cached_sections(self.codeview_dir)
        state2.sections.update(sections)
        state2.fingerprints.update(fps)
        for section in ("meta", "history", "files", "deps"):
            self.assertIsNotNone(state2.get(section))

    def test_scan_all_rescans_section_with_stale_fingerprint(self):
        # Regression: the old guard skipped any section that had *any*
        # stored fingerprint, freezing non-meta sections forever.
        state2 = srv.AppState()
        state2.sections["files"] = {"files": [], "total_files": 0,
                                    "total_lines": 0}
        state2.fingerprints["files"] = "definitely-stale"
        srv.scan_all(self.repo, self.shape, self.codeview_dir, state2,
                     force=False)
        self.assertGreater(state2.get("files")["total_files"], 0)
        self.assertEqual(state2.fingerprints["files"],
                         srv.current_data_fingerprint(self.repo, self.shape))

    def test_scan_all_skips_section_with_current_fingerprint(self):
        state2 = srv.AppState()
        state2.sections["files"] = {"sentinel": True}
        state2.fingerprints["files"] = srv.current_data_fingerprint(
            self.repo, self.shape)
        srv.scan_all(self.repo, self.shape, self.codeview_dir, state2,
                     force=False)
        self.assertTrue(state2.get("files").get("sentinel"))
        # meta is always refreshed regardless.
        self.assertIsNotNone(state2.get("meta"))

    def test_cache_schema_change_marks_every_loaded_section_stale(self):
        state2 = srv.AppState()
        for section in srv.SCAN_SECTIONS:
            state2.sections[section] = {"sentinel": True}
            state2.fingerprints[section] = "previous-schema"
        wanted = srv.current_data_fingerprint(self.repo, self.shape)
        self.assertEqual(srv.sections_needing_scan(state2, wanted),
                         list(srv.SCAN_SECTIONS))
        state2.fingerprints.update(
            {section: wanted for section in srv.SCAN_SECTIONS})
        self.assertEqual(srv.sections_needing_scan(state2, wanted), [])


if __name__ == "__main__":
    unittest.main()
