"""Local history repair preserves messages, creator identities and file offsets."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
import json
import os
import stat
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("history_sessions", REPO_ROOT / "scripts/account_sessions.py")
SESSIONS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SESSIONS)


class CodexHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)

    def rollout(self, directory="sessions/2026/11/13", payload=None):
        path = self.home / directory / "rollout-test.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if payload is None:
            payload = {"account_scoped": True, "creator_user_id": "original-user",
                       "creator_account_id": "original-account", "id": "thread-id",
                       "history_mode": "paginated", "base_instructions": {"text": 'Keep "account_scoped":true in this text.'}}
        header = json.dumps({"type": "session_meta", "payload": payload}, separators=(",", ":")).encode() + b"\n"
        body = b'{"type":"response_item","payload":{"text":"unchanged message"}}\n'
        path.write_bytes(header + body)
        return path, header, body

    def test_preserves_creator_metadata_messages_offsets_and_open_file_inode(self):
        path, header, body = self.rollout()
        before = path.stat()
        with path.open("ab") as writer:
            self.assertEqual(SESSIONS.share_codex_history(self.home), 1)
            writer.write(b'{"type":"event_msg","payload":{"type":"appended"}}\n')
        updated = path.read_bytes()
        expected = json.loads(header)
        del expected["payload"]["account_scoped"]
        self.assertEqual(json.loads(updated.splitlines()[0]), expected)
        self.assertEqual(len(updated.splitlines(keepends=True)[0]), len(header))
        self.assertEqual(updated[len(header):len(header) + len(body)], body)
        self.assertTrue(updated.endswith(b'{"type":"event_msg","payload":{"type":"appended"}}\n'))
        self.assertEqual(path.stat().st_ino, before.st_ino)

    def test_marker_backup_is_private_and_can_restore_exact_original_bytes(self):
        path, header, body = self.rollout()
        SESSIONS.share_codex_history(self.home)
        backup = self.home / "clusterfork-history-scopes.jsonl"
        self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        record = json.loads(backup.read_text())
        self.assertEqual(self.home / record["path"], path)
        with path.open("r+b") as stream:
            os.pwrite(stream.fileno(), record["original"].encode(), record["offset"])
        self.assertEqual(path.read_bytes(), header + body)

    def test_idle_session_modification_time_is_preserved(self):
        path, _, _ = self.rollout()
        timestamp = 1_700_000_000_000_000_000
        os.utime(path, ns=(timestamp, timestamp))
        self.assertEqual(SESSIONS.share_codex_history(self.home), 1)
        self.assertEqual(path.stat().st_mtime_ns, timestamp)
        record = json.loads((self.home / "clusterfork-history-scopes.jsonl").read_text())
        self.assertEqual(record["mtime_ns"], timestamp)

    def test_repeated_runs_are_noops_and_do_not_duplicate_backups(self):
        path, _, _ = self.rollout()
        self.assertEqual(SESSIONS.share_codex_history(self.home), 1)
        state = path.read_bytes()
        self.assertEqual(SESSIONS.share_codex_history(self.home), 0)
        self.assertEqual(path.read_bytes(), state)
        self.assertEqual(len((self.home / "clusterfork-history-scopes.jsonl").read_text().splitlines()), 1)

    def test_concurrent_launches_repair_and_back_up_a_session_once(self):
        self.rollout()
        with ThreadPoolExecutor(max_workers=2) as pool:
            counts = list(pool.map(SESSIONS.share_codex_history, [self.home, self.home]))
        self.assertEqual(sorted(counts), [0, 1])
        self.assertEqual(len((self.home / "clusterfork-history-scopes.jsonl").read_text().splitlines()), 1)

    def test_failed_backup_does_not_change_session(self):
        path, header, body = self.rollout()
        (self.home / "clusterfork-history-scopes.jsonl").mkdir()
        with self.assertRaises(OSError):
            SESSIONS.share_codex_history(self.home)
        self.assertEqual(path.read_bytes(), header + body)

    def test_scoped_archived_and_reverted_paths_are_also_shared(self):
        original, _, _ = self.rollout()
        archived, _, _ = self.rollout("archived_sessions")
        reverted = original.with_name("rollout-reverted.jsonl")
        reverted.write_bytes(original.read_bytes())
        self.assertEqual(SESSIONS.share_codex_history(self.home), 3)
        for path in (original, archived, reverted):
            self.assertNotIn("account_scoped", json.loads(path.read_bytes().splitlines()[0])["payload"])

    def test_marker_at_end_and_whitespace_are_supported(self):
        path, _, body = self.rollout(payload={"id": "thread-id", "account_scoped": True})
        header = b'{ "type": "session_meta", "payload": {"id": "thread-id", "account_scoped" : true } }\n'
        path.write_bytes(header + body)
        self.assertEqual(SESSIONS.share_codex_history(self.home), 1)
        self.assertEqual(path.read_bytes()[len(header):], body)
        self.assertEqual(json.loads(path.read_bytes().splitlines()[0])["payload"], {"id": "thread-id"})

    def test_legacy_and_explicitly_unscoped_sessions_are_untouched(self):
        for payload in ({"id": "thread-id"}, {"id": "thread-id", "account_scoped": False}):
            with self.subTest(payload=payload):
                path, header, body = self.rollout(payload=payload)
                self.assertEqual(SESSIONS.share_codex_history(self.home), 0)
                self.assertEqual(path.read_bytes(), header + body)
        self.assertFalse((self.home / "clusterfork-history-scopes.jsonl").exists())

    def test_missing_home_is_a_noop(self):
        self.assertEqual(SESSIONS.share_codex_history(self.home / "missing"), 0)
        self.assertFalse((self.home / "missing").exists())

    def test_ambiguous_header_is_rejected_before_any_write(self):
        path, _, body = self.rollout()
        header = b'{"type":"session_meta","payload":{"account_scoped":true,"account_scoped":true,"id":"thread-id"}}\n'
        path.write_bytes(header + body)
        with self.assertRaisesRegex(ValueError, "Cannot preserve"):
            SESSIONS.share_codex_history(self.home)
        self.assertEqual(path.read_bytes(), header + body)
        self.assertFalse((self.home / "clusterfork-history-scopes.jsonl").exists())

    def test_clean_index_skips_reopening_unchanged_and_invalidates_on_ctime_or_size(self):
        scoped, header, body = self.rollout(payload={"id": "s1", "account_scoped": True})
        clean, clean_header, clean_body = self.rollout("sessions/2026/11/14", payload={"id": "s2"})

        self.assertEqual(SESSIONS.share_codex_history(self.home), 1)
        index_path = self.home / SESSIONS.HISTORY_INDEX_NAME
        self.assertTrue(index_path.is_file())
        self.assertEqual(stat.S_IMODE(index_path.stat().st_mode), 0o600)

        # Second scan skips opening already-clean indexed rollouts entirely.
        with patch.object(os, "open", wraps=os.open) as mock_open:
            self.assertEqual(SESSIONS.share_codex_history(self.home), 0)
        opened_rollouts = [c.args[0] for c in mock_open.call_args_list if str(c.args[0]).endswith(".jsonl")]
        self.assertEqual(opened_rollouts, [])

        # Even if a write restores original mtime, ctime_ns invalidates it.
        saved_stat = clean.stat()
        restored = clean_header.replace(b'"id":"s2"', b'"account_scoped":true,"id":"s2"') + clean_body
        clean.write_bytes(restored)
        os.utime(clean, ns=(saved_stat.st_atime_ns, saved_stat.st_mtime_ns))
        self.assertEqual(SESSIONS.share_codex_history(self.home), 1)
        self.assertNotIn("account_scoped", json.loads(clean.read_bytes().splitlines()[0])["payload"])
        self.assertNotIn("account_scoped", json.loads(scoped.read_bytes().splitlines()[0])["payload"])

    def test_symlinks_in_session_dirs_are_skipped(self):
        outside = self.home / "outside.jsonl"
        payload = {"account_scoped": True, "id": "outside-id"}
        raw = json.dumps({"type": "session_meta", "payload": payload}).encode() + b"\n"
        outside.write_bytes(raw)
        sessions_dir = self.home / "sessions" / "2026" / "11" / "13"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / "rollout-link.jsonl").symlink_to(outside)

        self.assertEqual(SESSIONS.share_codex_history(self.home), 0)
        self.assertEqual(outside.read_bytes(), raw)
