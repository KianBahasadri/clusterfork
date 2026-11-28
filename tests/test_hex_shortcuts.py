"""Exercise portal key sequences and typed requests without a live microphone or Linear writes."""

import base64
from io import BytesIO
import json
from pathlib import Path
from queue import SimpleQueue
from subprocess import CompletedProcess
import sys
from threading import Event
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import UUID

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "hex")]
import linear_shortcut as shortcut


def dialog_output(text, images=()):
    return json.dumps({"text": text, "images": [
        {"mime_type": mime, "data": base64.b64encode(data).decode("ascii")} for mime, data in images
    ]})


class ShortcutTests(unittest.TestCase):
    def run_shortcuts(self, scenario):
        self.handlers, self.timers, self.threads = {}, {}, []
        self.present = Mock()
        self.open_request = Mock(return_value=self.present)
        self.destination = SimpleNamespace(title="Original editor")
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
                "shortcuts": [(shortcut.SHORTCUT, {"trigger_description": "KP_Divide"}),
                              (shortcut.TYPING_SHORTCUT, {"trigger_description": "KP_Page_Up or KP_9"})]}
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
        gio = SimpleNamespace(dbus_address_get_for_bus_sync=Mock(return_value="unix:path=/fixture"),
                              DBusConnection=SimpleNamespace(new_for_address_sync=Mock(return_value=bus)),
                              DBusConnectionFlags=SimpleNamespace(AUTHENTICATION_CLIENT=1, MESSAGE_BUS_CONNECTION=2),
                              BusType=SimpleNamespace(SESSION=0),
                              DBusCallFlags=SimpleNamespace(NONE=0), DBusSignalFlags=SimpleNamespace(NONE=0))
        gi, repository = ModuleType("gi"), ModuleType("gi.repository")
        repository.Gio, repository.GLib = gio, glib
        gi.repository = repository
        self.emit = lambda member, timestamp, name=shortcut.SHORTCUT, **options: self.handlers[member](
            None, None, None, None, member, variant(None, ("/fixture/session", name, timestamp, options)))
        loop.run.side_effect = scenario
        with (patch.dict(sys.modules, {"gi": gi, "gi.repository": repository,
                                       "linear_reply": SimpleNamespace(open_request=self.open_request)}),
              patch.object(shortcut, "Thread", side_effect=thread),
              patch.object(shortcut, "capture_destination", return_value=self.destination)):
            self.assertEqual(shortcut.listen(False), 0)

    def tap(self, timestamp, name=shortcut.SHORTCUT, **options):
        self.emit("Activated", timestamp, name=name, **options)
        self.emit("Deactivated", timestamp + 50, name=name)

    def hold(self):
        source = next(iter(self.timers))
        self.assertFalse(self.timers.pop(source)())

    def calls(self, target):
        return [(args, thread) for called, args, thread in self.threads if called is target]

    def test_single_taps_and_repeated_keydown_do_not_record_or_submit(self):
        def scenario():
            self.emit("Deactivated", 900, name=shortcut.TYPING_SHORTCUT)
            self.emit("Activated", 1000, name=shortcut.TYPING_SHORTCUT)
            self.emit("Activated", 1020, name=shortcut.TYPING_SHORTCUT)
            self.assertEqual(len(self.timers), 1)
            self.emit("Deactivated", 1050, name=shortcut.TYPING_SHORTCUT)
            self.tap(2000)
            self.assertFalse(self.timers)
            self.assertFalse(self.calls(shortcut.capture))
            self.open_request.assert_not_called()
        self.run_shortcuts(scenario)

    def test_double_tap_opens_only_one_window_and_passes_the_activation_token(self):
        def scenario():
            self.tap(1000)
            self.tap(1150, activation_token="fixture-token")
            tab, token, _submit, close = self.open_request.call_args.args
            self.assertEqual((tab, token), ("linear", "fixture-token"))
            self.tap(1300, shortcut.TYPING_SHORTCUT)
            self.present.assert_called_once_with("typing", None)
            self.open_request.assert_called_once()
            close()
            self.tap(1600)
            self.tap(1750)
            self.assertEqual(self.open_request.call_count, 2)
            self.assertFalse(self.calls(shortcut.capture))
            self.assertFalse(self.timers)
        self.run_shortcuts(scenario)

    def test_release_activation_token_takes_precedence(self):
        def scenario():
            self.tap(1000)
            self.emit("Activated", 1150, activation_token="press-token")
            self.emit("Deactivated", 1200, activation_token="release-token")
            self.assertEqual(self.open_request.call_args.args[1], "release-token")
        self.run_shortcuts(scenario)

    def test_typing_tab_opens_before_the_focus_extension_loads(self):
        def scenario():
            message = "Sign out and back in to load Hex Typing Focus."
            with (patch.object(shortcut, "capture_destination", side_effect=shortcut.TypingError(message)),
                  patch.object(shortcut, "focused_title", return_value="Editor")):
                self.tap(1000, shortcut.TYPING_SHORTCUT)
                self.tap(1150, shortcut.TYPING_SHORTCUT)
                tab, _token, submit, close = self.open_request.call_args.args
                self.assertEqual(tab, "typing")
                self.assertEqual(self.open_request.call_args.kwargs["typing_error"], message)
                self.tap(1300)
                self.present.assert_called_once_with("linear", None)
                close()
                submit("linear", "Keep this draft.", ())
                args, _thread = self.calls(shortcut.submit_linear)[0]
                self.assertEqual(args[2:], ("Keep this draft.", "Editor", (), None))
        self.run_shortcuts(scenario)

    def test_taps_must_be_close_together_and_shorter_than_a_hold(self):
        for gap in (350, 351):
            with self.subTest(gap=gap):
                def scenario():
                    self.tap(1000)
                    self.tap(1050 + gap)
                    self.assertEqual(self.open_request.call_count, int(gap <= 350))
                    self.assertFalse(self.calls(shortcut.capture))
                self.run_shortcuts(scenario)

        def delayed_timer():
            self.tap(1000)
            self.emit("Activated", 1150)
            self.emit("Deactivated", 1400)
            self.tap(1500)
            self.open_request.assert_not_called()
            self.assertFalse(self.calls(shortcut.capture))
        self.run_shortcuts(delayed_timer)

    def test_holding_linear_shortcut_does_not_schedule_or_record(self):
        def scenario():
            self.emit("Activated", 1000)
            self.assertFalse(self.timers)
            self.emit("Deactivated", 1300)
            self.tap(1400)
            self.assertFalse(self.calls(shortcut.capture))
            self.open_request.assert_not_called()
        self.run_shortcuts(scenario)

    def test_hold_after_a_tap_records_once_and_release_ends_that_recording(self):
        def scenario():
            self.tap(1000, shortcut.TYPING_SHORTCUT)
            self.emit("Activated", 1150, name=shortcut.TYPING_SHORTCUT)
            self.hold()
            (released, stopping, playback), recording = self.calls(shortcut.capture)[0]
            self.assertIsInstance(playback, shortcut.Playback)
            self.assertFalse(released.is_set())
            self.emit("Activated", 1600, name=shortcut.TYPING_SHORTCUT)
            self.assertEqual(len(self.calls(shortcut.capture)), 1)
            self.emit("Deactivated", 1800, name=shortcut.TYPING_SHORTCUT)
            self.assertTrue(released.is_set())
            self.assertFalse(stopping.is_set())
            self.tap(1900, shortcut.TYPING_SHORTCUT)
            self.open_request.assert_not_called()
            recording.start.assert_called_once()
        self.run_shortcuts(scenario)

    def test_transcription_blocks_another_recording_but_allows_a_text_request(self):
        def scenario():
            self.emit("Activated", 1000, name=shortcut.TYPING_SHORTCUT)
            self.hold()
            self.emit("Deactivated", 1500, name=shortcut.TYPING_SHORTCUT)
            self.emit("Activated", 1700, name=shortcut.TYPING_SHORTCUT)
            self.hold()
            self.emit("Deactivated", 2200, name=shortcut.TYPING_SHORTCUT)
            self.assertEqual(len(self.calls(shortcut.capture)), 1)
            self.assertEqual(len(self.calls(shortcut.notify)), 1)
            self.tap(2400)
            self.tap(2550)
            self.open_request.assert_called_once()
        self.run_shortcuts(scenario)

    def test_shutdown_removes_a_pending_hold_and_releases_an_active_capture(self):
        for pending in (True, False):
            with self.subTest(pending=pending):
                def scenario():
                    self.emit("Activated", 1000, name=shortcut.TYPING_SHORTCUT)
                    if not pending:
                        self.hold()
                    self.stop()
                    self.assertFalse(self.timers)
                    if pending:
                        self.assertFalse(self.calls(shortcut.capture))
                    else:
                        (released, stopping, _playback), _thread = self.calls(shortcut.capture)[0]
                        self.assertTrue(released.is_set())
                        self.assertTrue(stopping.is_set())
                self.run_shortcuts(scenario)

    def test_keypad9_uses_processed_capture_and_ignores_another_shortcuts_release(self):
        def scenario():
            self.emit("Activated", 1000, name=shortcut.TYPING_SHORTCUT)
            self.hold()
            (released, _stopping, typed), _recording = self.calls(shortcut.capture)[0]
            self.assertIsInstance(typed, shortcut.Playback)
            self.emit("Deactivated", 1200)
            self.assertFalse(released.is_set())
            self.emit("Deactivated", 1500, name=shortcut.TYPING_SHORTCUT)
            self.assertTrue(released.is_set())
        self.run_shortcuts(scenario)

    def test_each_double_tap_opens_its_tab_and_submission_uses_the_other_tab(self):
        for binding, initial, final in (
            (shortcut.SHORTCUT, "linear", "typing"), (shortcut.TYPING_SHORTCUT, "typing", "linear")
        ):
            with self.subTest(initial=initial):
                def scenario():
                    other = shortcut.TYPING_SHORTCUT if binding == shortcut.SHORTCUT else shortcut.SHORTCUT
                    self.tap(1000, other)
                    self.tap(1150, binding)
                    self.open_request.assert_not_called()
                    self.tap(1300, binding)
                    start_tab, _token, submit, close = self.open_request.call_args.args
                    self.assertEqual(start_tab, initial)
                    self.tap(1450, other)
                    self.present.assert_called_once_with(final, None)
                    self.open_request.assert_called_once()
                    close()
                    submit(final, "Draft\n", (), "Relay" if final == "linear" else None)
                    if final == "typing":
                        args, _thread = self.calls(shortcut.submit_typing)[0]
                        self.assertEqual(args[2:], ("Draft\n", self.destination))
                    else:
                        args, _thread = self.calls(shortcut.submit_linear)[0]
                        self.assertEqual(args[2:], ("Draft\n", "Original editor", (), "Relay"))
                    self.assertFalse(self.calls(shortcut.capture))
                self.run_shortcuts(scenario)

    def test_num9_stops_replay_and_holding_that_press_does_not_record(self):
        def scenario():
            with patch.object(shortcut.Playback, "cancel", side_effect=[True, False]) as cancel:
                self.emit("Activated", 1000, name=shortcut.TYPING_SHORTCUT)
                self.emit("Activated", 1500, name=shortcut.TYPING_SHORTCUT)
                self.emit("Deactivated", 1600, name=shortcut.TYPING_SHORTCUT)
            cancel.assert_called_once()
            self.assertFalse(self.timers)
            self.assertFalse(self.calls(shortcut.capture))
        self.run_shortcuts(scenario)


class TypedDictationTests(unittest.TestCase):
    def test_capture_types_the_processed_text_without_copying_or_filing_it(self):
        released, stopping = Event(), Event()
        released.set()
        text = "Processed café text.\nAnother line."
        playback, destination = Mock(), object()
        with (patch.object(shortcut, "hex_command", side_effect=[
                {"capture": {"state": "idle"}}, {}, {"text": text}]) as command,
              patch.object(shortcut, "capture_destination", return_value=destination)):
            shortcut.capture(released, stopping, playback)
        self.assertEqual([call.args for call in command.call_args_list],
                         [("status",), ("dictation", "start"), ("dictation", "stop", "--print")])
        playback.type.assert_called_once_with(text, destination, restore=False)

    def test_empty_dictation_does_not_open_a_typing_session(self):
        released = Event()
        released.set()
        playback = Mock()
        with (patch.object(shortcut, "hex_command", side_effect=[
                {"capture": {"state": "idle"}}, {}, {"text": ""}]),
              patch.object(shortcut, "capture_destination")):
            shortcut.capture(released, Event(), playback)
        playback.type.assert_not_called()


class TypedRequestTests(unittest.TestCase):
    def test_typed_text_uses_the_same_queue_without_touching_the_microphone(self):
        jobs = SimpleQueue()
        text = "Create two independent issues.\nKeep the café details.\n"
        with (patch.object(shortcut, "hex_command") as hex_command,
              patch.object(shortcut, "progress_update") as progress):
            shortcut.submit_linear(jobs, Event(), text, "Project context", (), suggested_project="Clusterfork")
        request = jobs.get_nowait()
        self.assertEqual(request.transcript, text)
        self.assertEqual(request.title, "Project context")
        self.assertEqual(request.suggested_project, "Clusterfork")
        self.assertEqual(UUID(request.issue_id).version, 4)
        self.assertIsNone(request.session_id)
        progress.assert_called_once_with(request.issue_id, 1, 4, "Queued: Create two independent issues. Keep...", "active")
        hex_command.assert_not_called()
        self.assertTrue(jobs.empty())

    def test_cancelling_empty_text_or_shutdown_never_queues_a_request(self):
        for text, stopped in (("", False), ("  \n", False), ("Do not send after shutdown", True)):
            with self.subTest(text=text, stopped=stopped):
                jobs, stopping = SimpleQueue(), Event()
                if stopped:
                    stopping.set()
                with patch.object(shortcut, "request_from_text") as prepare:
                    shortcut.submit_linear(jobs, stopping, text, None, ())
                self.assertTrue(jobs.empty())
                prepare.assert_not_called()

    def test_pasted_images_reach_the_queue_as_attach_requests(self):
        jobs = SimpleQueue()
        images = []
        for color in ("red", "blue"):
            data = BytesIO()
            Image.new("RGB", (2, 2), color).save(data, format="PNG")
            images.append(("image/png", data.getvalue()))
        text = "Create an issue with these two screenshots."
        shortcut.submit_linear(jobs, Event(), text, None, tuple(images))
        request = jobs.get_nowait()
        self.assertEqual(request.transcript, text)
        self.assertEqual(request.image_use, "attach")
        self.assertEqual(request.images, tuple(images))

    def test_corrupt_or_excessive_dialog_images_are_not_submitted(self):
        for images in ([('image/png', b'corrupt')], [('image/png', b'x')] * 7):
            with (self.subTest(images=len(images)), patch.object(shortcut.subprocess, "run",
                    return_value=CompletedProcess("dialog", 0, stdout=dialog_output("Use these images", images)))):
                with self.assertRaises(shortcut.FilingError):
                    shortcut.text_entry("Question", {})

    def test_pasting_an_image_into_a_reply_preserves_the_request_and_prior_context(self):
        jobs = SimpleQueue()
        data = BytesIO()
        Image.new("RGB", (2, 2), "red").save(data, format="PNG")
        image = ("image/png", data.getvalue())
        request = shortcut.Dictation("Original request", "Project", "fixture-request",
                                     answers=(("Which project?", "Clusterfork"),),
                                     images=(("image/png", b"original"),), session_id="ses_fixture")
        with patch.object(shortcut.subprocess, "run", side_effect=[
                CompletedProcess("notify-send", 0, stdout="reply\n"),
                CompletedProcess("dialog", 0, stdout=dialog_output("Use this screenshot too.", (image,))),
        ]):
            shortcut.offer_reply(jobs, request, shortcut.ReplyNeeded("What should it look like?"))
        result = jobs.get_nowait()
        self.assertEqual(result.issue_id, request.issue_id)
        self.assertEqual(result.session_id, request.session_id)
        self.assertEqual(result.transcript, request.transcript)
        self.assertEqual(result.answers, (*request.answers, ("What should it look like?", "Use this screenshot too.")))
        self.assertEqual(result.images, (*request.images, image))
        self.assertEqual(result.image_use, "attach")

    def test_image_only_reply_submits_images_without_empty_answer(self):
        jobs = SimpleQueue()
        data = BytesIO()
        Image.new("RGB", (2, 2), "green").save(data, format="PNG")
        image = ("image/png", data.getvalue())
        request = shortcut.Dictation("Original request", "Project", "fixture-request",
                                     answers=(("Which project?", "Clusterfork"),),
                                     session_id="ses_fixture")
        with patch.object(shortcut.subprocess, "run", side_effect=[
                CompletedProcess("notify-send", 0, stdout="reply\n"),
                CompletedProcess("dialog", 0, stdout=dialog_output("  \n", (image,))),
        ]):
            shortcut.offer_reply(jobs, request, shortcut.ReplyNeeded("Attach screenshot"))
        result = jobs.get_nowait()
        self.assertEqual(result.answers, request.answers)
        self.assertEqual(result.images, (image,))
        self.assertEqual(result.image_use, "attach")

    def test_notifications_group_by_desktop_entry_and_keep_quotes_literal(self):
        with patch.object(shortcut.subprocess, "run",
                          return_value=CompletedProcess("notify-send", 0, stdout="")) as run:
            shortcut.notify("Linear dictation", "Hex's \"quote\" & <b>bold</b>")
        command = run.call_args.args[0]
        self.assertIn(f"--hint=string:desktop-entry:{shortcut.APP}", command)
        self.assertEqual(command[-1], "Hex's \"quote\" &amp; &lt;b&gt;bold&lt;/b&gt;")

        jobs = SimpleQueue()
        request = shortcut.Dictation("It's broken", "Window", "fixture-request")
        with patch.object(shortcut.subprocess, "run",
                          return_value=CompletedProcess("notify-send", 0, stdout="")) as run:
            shortcut.offer_reply(jobs, request, shortcut.ReplyNeeded("What's next <tag>?"))
        reply_cmd = run.call_args.args[0]
        self.assertIn(f"--hint=string:desktop-entry:{shortcut.APP}", reply_cmd)
        self.assertEqual(reply_cmd[-1], "What's next &lt;tag&gt;?\n\nIt's broken")

    def test_cannot_queue_after_request_preparation_fails(self):
        jobs = SimpleQueue()
        with (patch.object(shortcut, "request_from_text", side_effect=ValueError),
              patch.object(shortcut, "notify") as notify):
            shortcut.submit_linear(jobs, Event(), "Valid draft", None, ())
        self.assertTrue(jobs.empty())
        notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
