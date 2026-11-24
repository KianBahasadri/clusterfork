import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hex"))

import contextual_rewrite as rewrite
import vocabulary_review as review


class BackgroundVocabularyTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name) / "review-state.json"
        self.settings = Path(directory.name) / "settings.json"
        self.settings.write_text(json.dumps({
            "dictation_processing": {"default_mode": {"post_processing": {
                "model": "opencode-go/deepseek-v4.1-flash", "variant": "low", "deadline_seconds": 30,
            }}},
            "vocabulary": ["Kobo"],
        }))
        for module, name, value in [(review, "STATE_PATH", self.state), (rewrite, "SETTINGS_PATH", self.settings)]:
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def entry(self, identifier, raw="Use Syncthing.", output=None, kind="dictation", timestamp=None):
        return {
            "id": identifier, "timestamp_ms": timestamp if timestamp is not None else identifier * 1000,
            "raw_text": raw, "final_text": raw if output is None else output, "kind": kind,
        }

    def test_initial_start_skips_existing_history_and_checkpoint_survives_restart(self):
        entries = [self.entry(1), self.entry(2)]
        with patch.object(review, "review_entry") as run:
            cursor = review.load_cursor(entries)
            self.assertEqual(review.process_pending(entries, cursor), (2000, 2))
            run.assert_not_called()
            entries.append(self.entry(3))
            self.assertEqual(review.process_pending(entries, cursor), (3000, 3))
            run.assert_called_once_with(entries[-1], entries)
            self.assertEqual(review.load_cursor(entries), (3000, 3))
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(set(json.loads(self.state.read_text())), {"version", "last_entry"})

    def test_review_failure_does_not_block_later_deliveries_or_replay_failed_entry(self):
        entries = [self.entry(1), self.entry(2)]
        with patch.object(review, "review_entry", side_effect=[TimeoutError(), 0]) as run:
            cursor = review.process_pending(entries, (0, 0))
            self.assertEqual(run.call_count, 2)
            self.assertEqual(review.load_cursor(entries), (2000, 2))
            review.process_pending(entries, cursor)
            self.assertEqual(run.call_count, 2)

    def test_history_clear_with_reset_ids_does_not_disable_new_reviews(self):
        entry = self.entry(1, timestamp=4000)
        with patch.object(review, "review_entry") as run:
            self.assertEqual(review.process_pending([entry], (3000, 20)), (4000, 1))
            run.assert_called_once()

    def test_original_evidence_excludes_current_future_and_non_dictated_entries(self):
        current = self.entry(5, raw="Use Syncthing.")
        entries = [
            self.entry(1, raw="Use sync thing.", output="Use Syncthing."),
            self.entry(2, kind="typed-text"), self.entry(3, kind="printed"),
            {"id": 4, "timestamp_ms": 4000, "final_text": "Syncthing"},
            current, self.entry(6),
        ]
        previous = review.earlier_originals(entries, current)
        self.assertEqual(previous, ["Use sync thing."])
        candidate = {"term": "Syncthing", "category": "technical", "confidence": "high"}
        self.assertEqual(rewrite.eligible_terms([candidate], current["raw_text"], current["final_text"], [], previous), [])

    def test_first_mention_repeated_within_current_entry_does_not_start_a_review(self):
        current = self.entry(1, raw="Syncthing, Syncthing, Syncthing.")
        with patch.object(rewrite, "generate") as generate:
            self.assertEqual(review.review_entry(current, [current]), 0)
            generate.assert_not_called()

    def test_non_dictations_do_not_start_model_or_vocabulary_calls(self):
        with patch.object(rewrite, "generate") as generate, patch.object(rewrite, "remember_terms") as remember:
            for kind in ["typed-text", "printed", "raw"]:
                current = self.entry(2, kind=kind)
                self.assertEqual(review.review_entry(current, [self.entry(1), current]), 0)
            generate.assert_not_called()
            remember.assert_not_called()

    def test_separate_review_contains_raw_evidence_and_preserves_live_vocabulary(self):
        current = self.entry(2, raw="Restart syncthing.", output="Restart Syncthing.")
        entries = [self.entry(1, raw="I installed Syncthing."), current]
        candidate = {"term": "Syncthing", "category": "technical", "confidence": "high"}

        def generate(prompt, profile, deadline):
            reference = json.loads(prompt.split("JSON data to review:\n", 1)[1])
            self.assertEqual(reference["verified_repeated_phrases"], ["syncthing"])
            self.assertEqual(reference["current_transcript"], "Restart syncthing.")
            self.assertEqual(reference["pasted_text"], "Restart Syncthing.")
            self.assertEqual(reference["vocabulary"], ["Kobo"])
            self.assertEqual(profile["model"], "opencode-go/deepseek-v4.1-flash")
            self.assertEqual(profile["variant"], "low")
            settings = json.loads(self.settings.read_text())
            settings["vocabulary"].append("Syncthing")
            self.settings.write_text(json.dumps(settings))
            return json.dumps({"vocabulary_review": [candidate]})

        with patch.object(rewrite, "generate", side_effect=generate), patch.object(rewrite, "remember_terms", return_value=[]) as remember:
            self.assertEqual(review.review_entry(current, entries), 0)
            self.assertEqual(remember.call_args.args[3], ["Kobo", "Syncthing"])
            self.assertEqual(remember.call_args.args[-1], ["I installed Syncthing."])

    def test_malformed_review_never_updates_vocabulary(self):
        entries = [self.entry(1), self.entry(2)]
        with patch.object(rewrite, "generate", return_value='{"text":"Use Syncthing."}'), patch.object(rewrite, "remember_terms") as remember:
            with self.assertRaises(ValueError):
                review.review_entry(entries[-1], entries)
            remember.assert_not_called()


if __name__ == "__main__":
    unittest.main()
