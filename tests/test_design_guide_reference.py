"""Keep directly runnable references synchronized with their HTML sources."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
BUILDER = REPO_ROOT / "skills" / "design-guide" / "scripts" / "build_reference.py"


class ReferenceBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_builder(self, builder, *args):
        return subprocess.run(
            [sys.executable, str(builder), *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def fixture(self):
        builder = self.root / "skill" / "scripts" / "build_reference.py"
        builder.parent.mkdir(parents=True)
        shutil.copyfile(BUILDER, builder)
        reference = builder.parent.parent / "assets" / "component-reference"
        (reference / "components").mkdir(parents=True)
        (reference / "index.template.html").write_text(
            '<main>\n  <!-- include: components/example.html -->\n</main>\n', encoding="utf-8"
        )
        fragment = reference / "components" / "example.html"
        fragment.write_text("<p>Latency · 42 ms</p>\n", encoding="utf-8")
        return builder, fragment, reference / "index.html"

    def test_checked_in_catalog_matches_sources(self):
        result = self.run_builder(BUILDER, "--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_checked_in_dashboard_matches_sources(self):
        result = self.run_builder(BUILDER, "--reference", "dashboard-reference", "--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dashboard_check_detects_shared_catalog_fragment_edits(self):
        builder, fragment, _ = self.fixture()
        dashboard = fragment.parents[2] / "dashboard-reference"
        dashboard.mkdir()
        (dashboard / "index.template.html").write_text(
            '<main>\n  <!-- include: ../component-reference/components/example.html -->\n</main>\n', encoding="utf-8"
        )
        result = self.run_builder(builder, "--reference", "dashboard-reference")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = dashboard / "index.html"
        self.assertIn("Latency · 42 ms", output.read_text(encoding="utf-8"))
        original = output.read_bytes()

        fragment.write_text("<p>Latency · 18 ms</p>\n", encoding="utf-8")
        result = self.run_builder(builder, "--reference", "dashboard-reference", "--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), original)

        result = self.run_builder(builder, "--reference", "dashboard-reference")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Latency · 18 ms", output.read_text(encoding="utf-8"))

    def test_check_detects_source_edits_without_rewriting_output(self):
        builder, fragment, output = self.fixture()
        result = self.run_builder(builder)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text(encoding="utf-8"), "<main>\n  <p>Latency · 42 ms</p>\n</main>\n")
        original = output.read_bytes()
        modified_time = output.stat().st_mtime_ns

        fragment.write_text("<p>Latency · 18 ms</p>\n", encoding="utf-8")
        result = self.run_builder(builder, "--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(output.stat().st_mtime_ns, modified_time)

        result = self.run_builder(builder)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output.read_text(encoding="utf-8"), "<main>\n  <p>Latency · 18 ms</p>\n</main>\n")
        self.assertEqual(self.run_builder(builder, "--check").returncode, 0)
        modified_time = output.stat().st_mtime_ns
        self.assertEqual(self.run_builder(builder).returncode, 0)
        self.assertEqual(output.stat().st_mtime_ns, modified_time)

    def test_missing_fragment_does_not_replace_working_catalog(self):
        builder, fragment, output = self.fixture()
        result = self.run_builder(builder)
        self.assertEqual(result.returncode, 0, result.stderr)
        original = output.read_bytes()
        fragment.unlink()
        result = self.run_builder(builder)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("example.html", result.stderr)
        self.assertEqual(output.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
