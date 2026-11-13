"""Offline sync fixtures cover immutable downloads, adapters, and failure behavior."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import URLError
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sync_upstream_skills as upstream  # noqa: E402


AGENT = b"""---
name: code-simplifier
description: >
  Refine the changed code while preserving its behavior.
model: opus
---

Keep the existing behavior and the project's conventions.

    model: this is example text, not metadata
"""
SKILL = b"""---
name: deslop
description: Clean unnecessary generated code from the branch.
---

Inspect the branch's changes and make focused edits.
"""


class UpstreamSkillsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "scripts").mkdir()
        manifest = (ROOT / "scripts/upstream_skills.json").read_bytes()
        (self.root / "scripts/upstream_skills.json").write_bytes(manifest)
        self.sources = upstream.load_sources(self.root)
        self.commits = {source.repository: "a" * 40 for source in self.sources}
        self.prompts = {"code-simplifier": AGENT, "deslop": SKILL}
        self.licenses = {source.name: b"Fixture license terms\n" for source in self.sources}
        self.requests = []

    def fetch(self, url):
        self.requests.append(url)
        for source in self.sources:
            commit = self.commits[source.repository]
            if url == f"https://api.github.com/repos/{source.repository}/commits/{source.branch}":
                return json.dumps({"sha": commit}).encode()
            base = f"https://raw.githubusercontent.com/{source.repository}/{commit}/"
            if url == base + source.prompt_path:
                return self.prompts[source.name]
            if url == base + source.license_path:
                return self.licenses[source.name]
        raise AssertionError(f"Unexpected network request: {url}")

    def sync(self):
        return upstream.sync(self.root, self.sources, fetch=self.fetch)

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def test_import_preserves_cursor_bytes_and_anthropic_body(self):
        self.assertTrue(self.sync())
        self.assertEqual((self.root / "skills/deslop/SKILL.md").read_bytes(), SKILL)
        adapted = (self.root / "skills/code-simplifier/SKILL.md").read_bytes()
        self.assertNotIn(b"\nmodel: opus\n", adapted)
        self.assertEqual(adapted.split(b"\n---\n", 1)[1], AGENT.split(b"\n---\n", 1)[1])
        self.assertEqual((self.root / "skills/code-simplifier/UPSTREAM.md").read_bytes(), AGENT)
        upstream.check(self.root, self.sources)

    def test_prompt_and_license_downloads_use_the_same_immutable_commit(self):
        self.sync()
        self.assertEqual(len(self.requests), 6)
        for source in self.sources:
            base = f"https://raw.githubusercontent.com/{source.repository}/{'a' * 40}/"
            self.assertIn(base + source.prompt_path, self.requests)
            self.assertIn(base + source.license_path, self.requests)
        self.assertFalse(any("/main/" in url for url in self.requests))

    def test_repeated_sync_and_unrelated_upstream_commits_make_no_changes(self):
        self.sync()
        before = self.snapshot()
        self.assertEqual(self.sync(), [])
        self.commits = {repository: "b" * 40 for repository in self.commits}
        self.assertEqual(self.sync(), [])
        self.assertEqual(before, self.snapshot())

    def test_prompt_and_license_changes_refresh_the_snapshot(self):
        self.sync()
        self.commits["cursor/plugins"] = "b" * 40
        self.prompts["deslop"] += b"Keep the change small.\n"
        self.licenses["deslop"] += b"Additional notice.\n"
        self.assertTrue(self.sync())
        metadata = json.loads((self.root / "skills/deslop/UPSTREAM.json").read_bytes())
        self.assertEqual(metadata["commit"], "b" * 40)
        self.assertEqual((self.root / "skills/deslop/SKILL.md").read_bytes(), self.prompts["deslop"])
        self.assertEqual((self.root / "skills/deslop/LICENSE").read_bytes(), self.licenses["deslop"])
        upstream.check(self.root, self.sources)

    def test_network_failure_on_second_source_leaves_all_files_unchanged(self):
        self.sync()
        before = self.snapshot()
        self.prompts["code-simplifier"] += b"An upstream improvement.\n"

        def failing_fetch(url):
            if "cursor/plugins" in url:
                raise URLError("fixture outage")
            return self.fetch(url)

        with self.assertRaises(URLError):
            upstream.sync(self.root, self.sources, fetch=failing_fetch)
        self.assertEqual(before, self.snapshot())

    def test_malformed_second_prompt_leaves_all_files_unchanged(self):
        self.sync()
        before = self.snapshot()
        self.prompts["code-simplifier"] += b"An upstream improvement.\n"
        self.prompts["deslop"] = b"<html>Not a skill</html>"
        with self.assertRaises(ValueError):
            self.sync()
        self.assertEqual(before, self.snapshot())

    def test_new_claude_agent_tool_metadata_requires_an_adapter_change(self):
        self.prompts["code-simplifier"] = AGENT.replace(b"model: opus", b"model: opus\ntools: Bash")
        with self.assertRaisesRegex(ValueError, "Unsupported Claude agent metadata"):
            self.sync()
        self.assertFalse((self.root / "skills").exists())

    def test_disabled_skill_is_not_fetched_or_reenabled(self):
        disabled = self.root / "disabled-skills/deslop"
        disabled.mkdir(parents=True)
        (disabled / "SKILL.md").write_bytes(SKILL)
        self.sync()
        upstream.check(self.root, self.sources)
        self.assertFalse((self.root / "skills/deslop").exists())
        self.assertFalse(any("cursor/plugins" in url for url in self.requests))
        self.assertEqual((disabled / "SKILL.md").read_bytes(), SKILL)

    def test_offline_check_catches_prompt_license_and_provenance_drift(self):
        self.sync()
        for relative in ("code-simplifier/SKILL.md", "code-simplifier/UPSTREAM.md",
                         "deslop/SKILL.md", "deslop/LICENSE", "deslop/UPSTREAM.json"):
            path = self.root / "skills" / relative
            original = path.read_bytes()
            with self.subTest(path=relative):
                path.write_bytes(original.replace(b"main", b"other") if path.suffix == ".json"
                                 else original + b"Unexpected local edit.\n")
                with self.assertRaises(ValueError):
                    upstream.check(self.root, self.sources)
                path.write_bytes(original)

    def test_manifest_cannot_write_outside_skill_directories(self):
        path = self.root / "scripts/upstream_skills.json"
        manifest = json.loads(path.read_bytes())
        manifest["skills"][0]["name"] = "../outside"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "Invalid skill name"):
            upstream.load_sources(self.root)

    def test_symlink_destination_is_not_overwritten(self):
        (self.root / "skills").mkdir()
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / "skills/code-simplifier").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "destination escapes"):
            self.sync()
        self.assertEqual(list(outside.iterdir()), [])

    def test_wrong_name_and_empty_license_fail_before_writing(self):
        for prompt, license_text in ((SKILL.replace(b"name: deslop", b"name: unexpected"), b"License"),
                                     (SKILL, b"\n")):
            with self.subTest(prompt=prompt, license=license_text):
                self.prompts["deslop"] = prompt
                self.licenses["deslop"] = license_text
                with self.assertRaises(ValueError):
                    self.sync()
                self.assertFalse((self.root / "skills").exists())

    def test_api_token_is_never_sent_to_the_raw_file_host(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, limit):
                return b"fixture"

        with patch.dict("os.environ", {"GH_TOKEN": "fixture-token"}), \
                patch.object(upstream, "urlopen", return_value=Response()) as request:
            upstream.fetch_url("https://api.github.com/repos/cursor/plugins/commits/main")
            self.assertEqual(request.call_args.args[0].get_header("Authorization"), "Bearer fixture-token")
            upstream.fetch_url("https://raw.githubusercontent.com/cursor/plugins/fixture")
            self.assertIsNone(request.call_args.args[0].get_header("Authorization"))


class CheckedInSnapshotTests(unittest.TestCase):
    def test_actual_vendored_prompts_and_licenses_match_their_provenance(self):
        sources = upstream.load_sources(ROOT)
        self.assertEqual({source.name for source in sources}, {"code-simplifier", "deslop"})
        upstream.check(ROOT, sources)


if __name__ == "__main__":
    unittest.main()
