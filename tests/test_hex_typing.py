"""Check the shared typing engine without a graphical session."""

import os
from pathlib import Path
from random import Random
from statistics import median
import sys
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hex"))
import biocadence
import typing_profiles
import typing_replay


BENCHMARK_TEXT = (
    "Rain fell on the garden in the early morning. The cat watched from the window. "
    "A green chair stood by the door, and a note on the table said to bring a coat. "
    "Before lunch we walked down the path and stopped at the pond to count the ducks."
)


def fixture_model(profile):
    """Fit both profiles to synthetic key transitions with different pause distributions."""
    random = Random(994)
    press = 0
    strokes = []
    pause = {"mid": 0.08, "word": 0.22, "clause": 0.31, "sentence": 0.48, "start": 0}
    if profile == "thoughts":
        pause.update(word=0.32, clause=0.62, sentence=0.92)
    history = ""
    for index, key in enumerate(
        "Flowers grew beside the old house. Below the roof, small birds waited for sunrise. " * 8
    ):
        if index % 31 == 4:
            press += 0.10
            strokes.append(("x", press, press + 0.08))
            press += 0.23
            strokes.append((biocadence.BACKSPACE, press, press + 0.09))
        press += pause[biocadence.boundary(history)] * random.uniform(0.66, 1.34)
        strokes.append((key, press, press + random.uniform(0.045, 0.13)))
        history = (history + key)[-2:]
    return biocadence.fit(strokes)


def rhythm(events):
    bursts = {2: 0, 3: 0}
    boundaries = {context: [] for context in biocadence.CONTEXTS}
    previous, history, run = None, [], 0
    for event in events:
        if not event.pressed:
            continue
        if previous is not None:
            gap = event.time - previous.time
            context = biocadence.boundary(history)
            if context in boundaries:
                boundaries[context].append(gap)
            if previous.key.isalpha() and event.key.isalpha() and gap <= 0.1:
                run += 1
            else:
                if run in bursts:
                    bursts[run] += 1
                run = 1
        else:
            run = 1
        if event.key == biocadence.BACKSPACE:
            history.pop()
        else:
            history.append(event.key)
        previous = event
    if run in bursts:
        bursts[run] += 1
    return bursts, {key: median(gaps) for key, gaps in boundaries.items() if gaps}


class TypingPlanTests(unittest.TestCase):
    def test_corrections_and_rollover_leave_exact_text_with_both_profiles(self):
        text = "  Élan, Already?!\n\tWait... 👋\n" + BENCHMARK_TEXT + "  "
        for profile in typing_profiles.PROFILES:
            with self.subTest(profile=profile):
                model = fixture_model(profile)
                events = biocadence.plan(text, model, Random(5))
                result, pressed, corrections, rollover = [], set(), 0, 0
                for event in events:
                    if not event.pressed:
                        pressed.remove(event.key)
                        continue
                    self.assertNotIn(event.key, pressed)
                    rollover += bool(pressed)
                    pressed.add(event.key)
                    if event.key == biocadence.BACKSPACE:
                        result.pop()
                        corrections += 1
                    else:
                        result.append(event.key)
                self.assertFalse(pressed)
                self.assertEqual("".join(result), text)
                self.assertGreater(corrections, 0)
                self.assertGreater(rollover, 0)

    def test_pairwise_model_replays_two_and_three_letter_bursts_and_boundary_pauses(self):
        sentences = {}
        for profile in typing_profiles.PROFILES:
            with self.subTest(profile=profile):
                events = biocadence.plan(BENCHMARK_TEXT, fixture_model(profile), Random(511))
                bursts, gaps = rhythm(events)
                self.assertGreater(bursts[2], 0)
                self.assertGreater(bursts[3], 0)
                self.assertLess(gaps["mid"], gaps["word"])
                self.assertLess(gaps["word"], gaps["sentence"])
                sentences[profile] = gaps["sentence"]
        self.assertGreater(sentences["thoughts"], sentences["copy"])

    def test_selection_persists_without_overwriting_the_other_profile(self):
        with TemporaryDirectory() as state, patch.dict(os.environ, {"XDG_STATE_HOME": state}):
            self.assertEqual(typing_profiles.load_profiles(), {"selected": "thoughts", "profiles": {}})
            copy = fixture_model("copy")
            typing_profiles.save_profiles({"selected": "copy", "profiles": {"copy": copy}})
            self.assertEqual(typing_profiles.selected_model(), copy)
            loaded = typing_profiles.load_profiles()
            self.assertNotIn("thoughts", loaded["profiles"])
            loaded["profiles"]["thoughts"] = fixture_model("thoughts")
            typing_profiles.save_profiles(loaded)
            self.assertEqual(typing_profiles.load_profiles()["selected"], "copy")
            self.assertEqual(typing_profiles.selected_model(), copy)
            self.assertEqual(typing_profiles.profile_path().stat().st_mode & 0o777, 0o600)


class ReplayTests(unittest.TestCase):
    def test_waits_for_submit_modifiers_before_sending_keys(self):
        keys = [biocadence.KeyEvent(0, "a", pressed) for pressed in (True, False)]
        responses = [(False,), (13, 21, 0, ""), (True,), (True,), (True,), ()]
        with (patch.object(typing_replay, "plan", return_value=keys),
              patch.object(typing_replay, "bridge", side_effect=responses) as bridge):
            typing_replay.play("a", {}, typing_replay.Destination(13, 21, ""), Event())
        self.assertEqual([call.args[0] for call in bridge.call_args_list],
                         ["Ready", "Get", "Ready", "Key", "Key", "End"])

    def test_expands_unicode_and_newlines_to_key_transitions_only(self):
        keys = [biocadence.KeyEvent(0, char, pressed) for char in "Aé\n\t" for pressed in (True, False)]
        calls = []

        def call(method, _signature=None, *args):
            calls.append((method, args))
            return (True,)

        with (patch.object(typing_replay, "plan", return_value=keys),
              patch.object(typing_replay, "bridge", side_effect=call)):
            typing_replay.play("Aé\n\t", {}, typing_replay.Destination(13, 21, ""), Event())
        self.assertEqual({method for method, _ in calls}, {"Ready", "Key", "End"})
        self.assertTrue(all(args[:2] == (13, 21) for method, args in calls if method == "Key"))
        down = [args[2] for method, args in calls if method == "Key" and args[3]]
        self.assertEqual(down, [ord("A"), typing_replay.CONTROL, typing_replay.SHIFT, ord("u"),
                                ord("e"), ord("9"), ord(" "), typing_replay.SHIFT,
                                typing_replay.RETURN, typing_replay.TAB])

    def test_releases_keys_on_cancellation_and_ignores_future_events(self):
        cancelled = Event()
        keys = [biocadence.KeyEvent(0, "A", True), biocadence.KeyEvent(10, "A", False)]

        def call(method, *_args):
            if method == "Key":
                cancelled.set()
            return (True,)

        with (patch.object(typing_replay, "plan", return_value=keys),
              patch.object(typing_replay, "bridge", side_effect=call) as bridge):
            typing_replay.play("A", {}, typing_replay.Destination(13, 21, ""), cancelled)
        self.assertEqual([call.args[0] for call in bridge.call_args_list], ["Ready", "Key", "End"])

    def test_focus_guard_refuses_all_remaining_keys(self):
        keys = [biocadence.KeyEvent(0, "a", True), biocadence.KeyEvent(0, "a", False)]
        with (patch.object(typing_replay, "plan", return_value=keys),
              patch.object(typing_replay, "bridge", side_effect=[(True,), (False,), ()]) as bridge,
              self.assertRaisesRegex(typing_replay.TypingError, "lost focus")):
            typing_replay.play("a", {}, typing_replay.Destination(13, 21, ""), Event())
        self.assertEqual([call.args[0] for call in bridge.call_args_list], ["Ready", "Key", "End"])
