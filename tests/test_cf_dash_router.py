"""End-to-end tests for cf_dash.server: boot on a temp repo, hit routes over
real HTTP, verify sections/tabs/module dispatch/staleness handling."""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cf_dash import server as srv  # noqa: E402
from cf_dash import cachestore  # noqa: E402


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
        mods_dir = repo / ".cf-dash" / "modules"
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
        cls.cf_dir = srv.ensure_cf_dir(repo)
        cls.state = srv.AppState()
        srv.scan_all(repo, cls.shape, cls.cf_dir, cls.state, force=True)
        srv.persist_state(cls.cf_dir, cls.state)
        mods = srv.modules_rt.load_modules(mods_dir)

        httpd = srv.bind_with_retry(0)  # ephemeral port
        port = httpd.server_address[1]
        httpd.app = {
            "state": cls.state,
            "modules": mods,
            "module_routes": srv.build_module_table(mods),
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
        self.assertIn(b"cf-dash", body)

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
        self.assertEqual(data["commits_count"], 1)

    def test_section_endpoints(self):
        for section in ("meta", "history", "files", "deps"):
            status, body = get(self.url(f"/api/section/{section}"))
            self.assertEqual(status, 200)
            data = json.loads(body)
            self.assertTrue(data or data == [])

    def test_tabs_include_modules_and_broken_flag(self):
        _, body = get(self.url("/api/tabs"))
        tabs = json.loads(body)["tabs"]
        names_kinds = {(t["name"], t["kind"]) for t in tabs}
        self.assertIn(("overview", "core"), names_kinds)
        self.assertIn(("hello", "module"), names_kinds)
        self.assertIn(("broken", "broken"), names_kinds)

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

    def test_persist_then_reload_roundtrip(self):
        # Cache files exist and reload into a fresh state identically.
        state2 = srv.AppState()
        sections, fps = srv.read_cached_sections(self.cf_dir)
        state2.sections.update(sections)
        state2.fingerprints.update(fps)
        for section in ("meta", "history", "files", "deps"):
            self.assertIsNotNone(state2.get(section))


if __name__ == "__main__":
    unittest.main()
