"""Cache notifications by their exact text and current synthesis settings."""

from contextlib import closing
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "clusterfork-notify-speech"
loader = SourceFileLoader("notify_speech", str(SCRIPT))
spec = spec_from_loader(loader.name, loader)
speech = module_from_spec(spec)
loader.exec_module(speech)


class SpeechCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))
        self.model = self.directory / f"{speech.VOICE}.onnx"
        self.model.write_bytes(b"test model")
        self.config = self.directory / f"{speech.VOICE}.onnx.json"
        self.config.write_text('{"inference": {"length_scale": 1.0}}', encoding="utf-8")
        self.enterContext(patch.object(speech, "version", return_value="test-version"))
        self.synthesize = self.enterContext(patch.object(speech, "synthesize"))

    def rows(self):
        with closing(sqlite3.connect(self.directory / "speech.sqlite3")) as cache:
            return cache.execute("SELECT text, settings, wav FROM clips ORDER BY text").fetchall()

    def test_exact_text_has_its_own_recording(self):
        labels = ("clusterfork", "clusterfork-1", "clusterfork 1")
        self.synthesize.side_effect = [name.encode() for name in labels]
        for name in labels + labels:
            self.assertEqual(speech.cached_audio(self.directory, name), name.encode())
        self.assertEqual(self.synthesize.call_count, len(labels))
        self.assertEqual({text for text, _, _ in self.rows()}, set(labels))

    def test_settings_or_model_change_replaces_the_recording(self):
        self.synthesize.side_effect = [b"first clip", b"new settings", b"new model"]
        self.assertEqual(speech.cached_audio(self.directory, "clusterfork"), b"first clip")
        with patch.dict(speech.SYNTHESIS_OPTIONS, volume=0.5):
            self.assertEqual(speech.cached_audio(self.directory, "clusterfork"), b"new settings")
            self.assertEqual(speech.cached_audio(self.directory, "clusterfork"), b"new settings")
            self.model.write_bytes(b"updated model")
            self.assertEqual(speech.cached_audio(self.directory, "clusterfork"), b"new model")
            self.assertEqual(speech.cached_audio(self.directory, "clusterfork"), b"new model")
        [(text, settings, wav)] = self.rows()
        self.assertEqual((text, wav), ("clusterfork", b"new model"))
        self.assertEqual(json.loads(settings)["synthesis"]["volume"], 0.5)
        self.assertEqual(self.synthesize.call_count, 3)

    def test_failed_regeneration_never_returns_the_old_recording(self):
        self.synthesize.side_effect = [b"old clip", ValueError("synthesis failed"), b"new clip"]
        self.assertEqual(speech.cached_audio(self.directory, "clusterfork"), b"old clip")
        self.config.write_text('{"inference": {"length_scale": 1.2}}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "synthesis failed"):
            speech.cached_audio(self.directory, "clusterfork")
        self.assertEqual(speech.cached_audio(self.directory, "clusterfork"), b"new clip")
        [(text, settings, wav)] = self.rows()
        self.assertEqual((text, wav), ("clusterfork", b"new clip"))
        self.assertEqual(json.loads(settings)["voice_config"]["inference"]["length_scale"], 1.2)
