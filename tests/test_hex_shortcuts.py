"""Exercise portal key sequences and typed requests without a live microphone or Linear writes."""

from pathlib import Path
from queue import SimpleQueue
from subprocess import CompletedProcess, CalledProcessError
import sys
from threading import Event
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hex"), str(ROOT / "automation")]
import linear_shortcut as shortcut


class ShortcutTests(unittest.TestCase):
    def run_shortcuts(self, scenario):
        self.handlers, self.timers, self.threads = {}, {}, []
        bus, loop = Mock(), Mock()

        def subscribe(_sender, interface, member, path, _arg, _flags, callback):
            self.handlers[path if interface == "org.freedesktop.portal.Request" else member] = callback
            return len(self.handlers)

        def call(_sender, _path, _interface, method, parameters, *_args):
            if method == "Register":
                return
            options = parameters.unpack()[-1]
            path = f"{shortcut.DESKTOP}/request/fixture/{options['handle_token'].unpack()}"
            result = {"session_handle": "/fixture/session"} if method == "CreateSession" else {
                "shortcuts": [(shortcut.SHORTCUT, {"trigger_description": "KP_Divide"})]}
            self.handlers[path](None, None, None, None, None, variant(None, (0, result)))

        def schedule(_delay, callback):
            source = len(self.timers) + 1
            self.timers[source] = callback
            return source

        def thread(**kwargs):
            item = Mock()
            item.is_alive.return_value = True
            self.threads.append((kwargs["target"], kwargs["args"], item))
            return item

        variant = lambda _signature, value: SimpleNamespace(unpack=lambda: value)
        bus.get_unique_name.return_value = ":fixture"
        bus.signal_subscribe.side_effect = subscribe
        bus.call_sync.side_effect = call
        glib = SimpleNamespace(MainLoop=Mock(side_effect=[loop, Mock(), Mock()]), Variant=variant,
                               timeout_add_seconds=Mock(return_value=999), timeout_add=Mock(side_effect=schedule),
                               source_remove=Mock(side_effect=lambda source: self.timers.pop(source, None)),
                               SOURCE_REMOVE=False, PRIORITY_DEFAULT=0, Error=RuntimeError,
                               unix_signal_add=Mock(side_effect=lambda _priority, _signal, callback: setattr(self, "stop", callback)))
        gio = SimpleNamespace(bus_get_sync=Mock(return_value=bus), BusType=SimpleNamespace(SESSION=0),
                              DBusCallFlags=SimpleNamespace(NONE=0), DBusSignalFlags=SimpleNamespace(NONE=0))
        gi, repository = ModuleType("gi"), ModuleType("gi.repository")
        repository.Gio, repository.GLib = gio, glib
        gi.repository = repository
        self.emit = lambda member, timestamp, **options: self.handlers[member](
            None, None, None, None, member, variant(None, ("/fixture/session", shortcut.SHORTCUT, timestamp, options)))
        loop.run.side_effect = scenario
        with patch.dict(sys.modules, {"gi": gi, "gi.repository": repository}), patch.object(shortcut, "Thread", side_effect=thread):
            self.assertEqual(shortcut.listen(False), 0)

    def tap(self, timestamp, **options):
        self.emit("Activated", timestamp, **options)
        self.emit("Deactivated", timestamp + 50)

    def hold(self):
        source = next(iter(self.timers))
        self.assertFalse(self.timers.pop(source)())

    def calls(self, target):
        return [(args, thread) for called, args, thread in self.threads if called is target]

    def test_single_taps_and_repeated_keydown_do_not_record_or_submit(self):
        def scenario():
            self.emit("Deactivated", 900)
            self.emit("Activated", 1000)
            self.emit("Activated", 1020)
            self.assertEqual(len(self.timers), 1)
            self.emit("Deactivated", 1050)
            self.tap(2000)
            self.assertFalse(self.timers)
            self.assertFalse(self.calls(shortcut.capture))
            self.assertFalse(self.calls(shortcut.compose))
        self.run_shortcuts(scenario)

    def test_double_tap_opens_only_one_window_and_passes_the_activation_token(self):
        def scenario():
            self.tap(1000)
            self.tap(1150, activation_token="fixture-token")
            args, composing = self.calls(shortcut.compose)[0]
            self.assertEqual(args[2], "fixture-token")
            self.tap(1300)
            self.tap(1450)
            self.assertEqual(len(self.calls(shortcut.compose)), 1)
            composing.is_alive.return_value = False
            self.tap(1600)
            self.tap(1750)
            self.assertEqual(len(self.calls(shortcut.compose)), 2)
            self.assertFalse(self.calls(shortcut.capture))
            self.assertFalse(self.timers)
        self.run_shortcuts(scenario)

    def test_release_activation_token_takes_precedence(self):
        def scenario():
            self.tap(1000)
            self.emit("Activated", 1150, activation_token="press-token")
            self.emit("Deactivated", 1200, activation_token="release-token")
            self.assertEqual(self.calls(shortcut.compose)[0][0][2], "release-token")
        self.run_shortcuts(scenario)

    def test_taps_must_be_close_together_and_shorter_than_a_hold(self):
        for gap in (350, 351):
            with self.subTest(gap=gap):
                def scenario():
                    self.tap(1000)
                    self.tap(1050 + gap)
                    self.assertEqual(len(self.calls(shortcut.compose)), int(gap <= 350))
                    self.assertFalse(self.calls(shortcut.capture))
                self.run_shortcuts(scenario)

        def delayed_timer():
            self.tap(1000)
            self.emit("Activated", 1150)
            self.emit("Deactivated", 1400)
            self.tap(1500)
            self.assertFalse(self.calls(shortcut.compose))
            self.assertFalse(self.calls(shortcut.capture))
        self.run_shortcuts(delayed_timer)

    def test_hold_after_a_tap_records_once_and_release_ends_that_recording(self):
        def scenario():
            self.tap(1000)
            self.emit("Activated", 1150)
            self.hold()
            (released, stopping, _jobs), recording = self.calls(shortcut.capture)[0]
            self.assertFalse(released.is_set())
            self.emit("Activated", 1600)
            self.assertEqual(len(self.calls(shortcut.capture)), 1)
            self.emit("Deactivated", 1800)
            self.assertTrue(released.is_set())
            self.assertFalse(stopping.is_set())
            self.tap(1900)
            self.assertFalse(self.calls(shortcut.compose))
            recording.start.assert_called_once()
        self.run_shortcuts(scenario)

    def test_transcription_blocks_another_recording_but_allows_a_text_request(self):
        def scenario():
            self.emit("Activated", 1000)
            self.hold()
            self.emit("Deactivated", 1500)
            self.emit("Activated", 1700)
            self.hold()
            self.emit("Deactivated", 2200)
            self.assertEqual(len(self.calls(shortcut.capture)), 1)
            self.assertEqual(len(self.calls(shortcut.notify)), 1)
            self.tap(2400)
            self.tap(2550)
            self.assertEqual(len(self.calls(shortcut.compose)), 1)
        self.run_shortcuts(scenario)

    def test_shutdown_removes_a_pending_hold_and_releases_an_active_capture(self):
        for pending in (True, False):
            with self.subTest(pending=pending):
                def scenario():
                    self.emit("Activated", 1000)
                    if not pending:
                        self.hold()
                    self.stop()
                    self.assertFalse(self.timers)
                    if pending:
                        self.assertFalse(self.calls(shortcut.capture))
                    else:
                        (released, stopping, _jobs), _thread = self.calls(shortcut.capture)[0]
                        self.assertTrue(released.is_set())
                        self.assertTrue(stopping.is_set())
                self.run_shortcuts(scenario)


class TypedRequestTests(unittest.TestCase):
    def test_typed_text_uses_the_same_queue_without_touching_the_microphone_or_clipboard(self):
        jobs = SimpleQueue()
        text = "Create two independent issues.\nKeep the café details.\n"
        with (patch.object(shortcut.subprocess, "run", return_value=CompletedProcess("dialog", 0, stdout=text)) as run,
              patch.object(shortcut, "focused_title", return_value="Project context"),
              patch.object(shortcut, "hex_command") as hex_command,
              patch.object(shortcut, "clipboard_image") as image,
              patch.object(shortcut, "copy_transcript") as copy):
            shortcut.compose(jobs, Event(), "fixture-token")
        request = jobs.get_nowait()
        self.assertEqual(request.transcript, text)
        self.assertEqual(request.title, "Project context")
        self.assertEqual(UUID(request.issue_id).version, 4)
        self.assertIsNone(request.session_id)
        self.assertEqual(run.call_args.args[0][-1], "--new-request")
        self.assertEqual(run.call_args.kwargs["env"]["XDG_ACTIVATION_TOKEN"], "fixture-token")
        hex_command.assert_not_called()
        image.assert_not_called()
        copy.assert_not_called()
        self.assertTrue(jobs.empty())

    def test_a_typed_image_request_captures_the_image_and_uses_the_same_agent_input(self):
        jobs, image = SimpleQueue(), ("image/png", b"clipboard image")
        text = "Make an issue. Attach the image in my clipboard."
        with (patch.object(shortcut.subprocess, "run", return_value=CompletedProcess("dialog", 0, stdout=text)),
              patch.object(shortcut, "focused_title", return_value=None),
              patch.object(shortcut, "clipboard_image", return_value=image)):
            shortcut.compose(jobs, Event(), None)
        request = jobs.get_nowait()
        self.assertEqual(request.image_use, "attach")
        self.assertEqual(request.image, image)
        self.assertEqual(request.transcript, text)

    def test_cancelling_empty_text_or_shutdown_never_queues_a_request(self):
        for text, stopped in (("", False), ("  \n", False), ("Do not send after shutdown", True)):
            with self.subTest(text=text, stopped=stopped):
                jobs, stopping = SimpleQueue(), Event()
                if stopped:
                    stopping.set()
                with (patch.object(shortcut.subprocess, "run", return_value=CompletedProcess("dialog", 0, stdout=text)),
                      patch.object(shortcut, "focused_title"), patch.object(shortcut, "request_from_text") as prepare):
                    shortcut.compose(jobs, stopping, None)
                self.assertTrue(jobs.empty())
                prepare.assert_not_called()

    def test_dialog_failure_notifies_without_submitting_anything(self):
        jobs = SimpleQueue()
        with (patch.object(shortcut.subprocess, "run", side_effect=CalledProcessError(1, "dialog")),
              patch.object(shortcut, "focused_title"), patch.object(shortcut, "notify") as notify):
            shortcut.compose(jobs, Event(), None)
        self.assertTrue(jobs.empty())
        notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
