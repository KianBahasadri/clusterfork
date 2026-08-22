"""Characterization tests for scripts/auth_convert.py."""

import sys
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import auth_convert


def opencode_auth():
    return {
        "openai": {
            "access": "acc",
            "refresh": "ref",
            "accountId": "acct-1",
        }
    }


def codex_auth():
    return {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "id_token": "acc",
            "access_token": "acc",
            "refresh_token": "ref",
            "account_id": "acct-1",
        },
        "last_refresh": "2026-01-01T00:00:00+00:00",
    }


class DetectFormatTests(unittest.TestCase):
    def test_detects_opencode_shape(self):
        self.assertEqual(auth_convert.detect_format(opencode_auth()), "opencode")

    def test_detects_codex_shape(self):
        self.assertEqual(auth_convert.detect_format(codex_auth()), "codex")

    def test_unknown_shape_raises(self):
        with self.assertRaises(ValueError):
            auth_convert.detect_format({"something": "else"})


class OpencodeToCodexTests(unittest.TestCase):
    def test_maps_fields_into_codex_shape(self):
        out = auth_convert.opencode_to_codex(opencode_auth())
        self.assertEqual(out["auth_mode"], "chatgpt")
        self.assertIsNone(out["OPENAI_API_KEY"])
        self.assertEqual(
            out["tokens"],
            {
                "id_token": "acc",
                "access_token": "acc",
                "refresh_token": "ref",
                "account_id": "acct-1",
            },
        )

    def test_last_refresh_is_parseable_utc_iso(self):
        # Value is wall-clock at conversion time, so pin the shape, not the value.
        out = auth_convert.opencode_to_codex(opencode_auth())
        parsed = datetime.fromisoformat(out["last_refresh"])
        self.assertEqual(parsed.utcoffset().total_seconds(), 0)

    def test_missing_refresh_raises(self):
        auth = opencode_auth()
        del auth["openai"]["refresh"]
        with self.assertRaises(ValueError):
            auth_convert.opencode_to_codex(auth)


class CodexToOpencodeTests(unittest.TestCase):
    def test_maps_fields_into_opencode_shape(self):
        out = auth_convert.codex_to_opencode(codex_auth())
        self.assertEqual(
            out,
            {"openai": {"access": "acc", "refresh": "ref", "accountId": "acct-1"}},
        )

    def test_missing_tokens_raises(self):
        with self.assertRaises(ValueError):
            auth_convert.codex_to_opencode({"auth_mode": "chatgpt"})


class ConvertDispatchTests(unittest.TestCase):
    def test_explicit_opencode_input(self):
        out = auth_convert.convert(opencode_auth(), "opencode")
        self.assertEqual(out["tokens"]["account_id"], "acct-1")

    def test_explicit_codex_input(self):
        out = auth_convert.convert(codex_auth(), "codex")
        self.assertEqual(out["openai"]["accountId"], "acct-1")

    def test_auto_detects_both_directions(self):
        self.assertIn("tokens", auth_convert.convert(opencode_auth(), "auto"))
        self.assertIn("openai", auth_convert.convert(codex_auth(), "auto"))

    def test_unsupported_format_raises(self):
        with self.assertRaises(ValueError):
            auth_convert.convert({}, "yaml")


if __name__ == "__main__":
    unittest.main()
