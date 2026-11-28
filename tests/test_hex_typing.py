"""Check the shared typing engine without a graphical session."""

import os
from pathlib import Path
from random import Random
from statistics import median
import subprocess
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


class FocusAuthorizationTests(unittest.TestCase):
    def test_session_bus_methods_require_the_current_worker_pid(self):
        script = r"""
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

class Variant {
    constructor(_type, value) { this.value = value; }
    deepUnpack() { return this.value; }
}
const timers = new Map();
let nextTimer = 1;
const globalObj = {
    stage: {context: {get_backend: () => ({get_default_seat: () => ({create_virtual_device: () => ({notify_keyval() {}})})})}},
    display: {connect: () => 1, disconnect() {}, focus_window: null, list_all_windows: () => []},
    get_pointer: () => [0, 0, 0],
};
const Clutter = {Orientation: {VERTICAL: 1, HORIZONTAL: 0}, InputDeviceType: {KEYBOARD_DEVICE: 0}, ModifierType: {}, KeyState: {PRESSED: 1, RELEASED: 0}, CURRENT_TIME: 0};
const Gio = {
    DBus: {session: {register_object: () => 1, unregister_object() {}}},
    DBusNodeInfo: {new_for_xml: () => ({interfaces: [{}]})},
    DBusCallFlags: {NO_AUTO_START: 1},
    BusNameWatcherFlags: {NONE: 0},
    bus_watch_name_on_connection: () => 7,
    bus_unwatch_name() {},
};
const GLib = {
    Variant,
    VariantType: class {},
    PRIORITY_DEFAULT: 0,
    SOURCE_REMOVE: false,
    timeout_add: (_prio, _ms, cb) => { const id = nextTimer++; timers.set(id, cb); return id; },
    timeout_add_seconds: (_prio, _sec, cb) => { const id = nextTimer++; timers.set(id, cb); return id; },
    Source: {remove: (id) => timers.delete(id)},
};
class FakeActor {
    constructor(props = {}) {
        Object.assign(this, props);
        this.children = [];
        this.signals = {};
        this.destroyed = false;
    }
    connect(name, cb) { (this.signals[name] ||= []).push(cb); return 1; }
    add_child(child) { this.children.push(child); }
    insert_child_at_index(child, index) { this.children.splice(index, 0, child); }
    destroy_all_children() { this.children = []; }
    get_preferred_size() { return [276, 48 * Math.max(1, this.children.length), 276, 48 * Math.max(1, this.children.length)]; }
    set_position(x, y) { this.pos = [x, y]; }
    destroy() {
        this.destroyed = true;
        if (typeof mainBox !== 'undefined' && mainBox)
            mainBox.children = mainBox.children.filter(item => item !== this);
    }
}
const St = {BoxLayout: FakeActor, Label: FakeActor, Widget: FakeActor};
const chromeCalls = [];
const Main = {
    sessionMode: {isLocked: false, connect: () => 2, disconnect() {}},
    layoutManager: {
        primaryIndex: 0,
        getWorkAreaForMonitor: () => ({x: 0, y: 0, width: 1920, height: 1080}),
        addChrome: (actor, opts) => chromeCalls.push({actor, opts}),
        removeChrome() {},
        connect: () => 3,
        disconnect() {},
    },
};
const source = readFileSync(process.argv[2], 'utf8')
    .replace(/^import .*;\n/gm, '')
    .replace('export default class', 'return class');
const Extension = new Function('Clutter', 'Gio', 'GLib', 'St', 'Extension', 'Main', 'global', source)(
    Clutter, Gio, GLib, St, class {}, Main, globalObj);
const focus = new Extension();
focus._registration = 1;
focus._windowInfo = () => [5, 6, 7, 'Editor'];
let worker = 32;
const connection = {
    call(name, _path, _iface, method, args, _type, _flags, _timeout, _cancel, done) {
        const value = name === 'org.freedesktop.DBus'
            ? (args.value[0] === ':1.32' ? 32 : 41)
            : new Variant('u', worker);
        assert.ok(['GetConnectionUnixProcessID', 'Get'].includes(method));
        queueMicrotask(() => done(this, name === 'org.freedesktop.systemd1' && worker === null ? null : [value]));
    },
    call_finish(result) {
        if (result === null)
            throw new Error('The user manager did not answer');
        return new Variant('tuple', result);
    },
};
async function denied(sender, method, params = null) {
    let error;
    await focus._handleCall(connection, sender, method, params, {
        return_value() { assert.fail('Unexpected authorized response'); },
        return_dbus_error(name) { error = name; },
    });
    assert.equal(error, 'org.freedesktop.DBus.Error.AccessDenied');
}
for (const method of ['Get', 'Activate', 'Ready', 'Key', 'End', 'Progress', 'ProgressClear'])
    await denied(':1.41', method);
let windowInfo;
await focus._handleCall(connection, ':1.32', 'Get', null, {
    return_value(value) { windowInfo = value.value; },
    return_dbus_error() { assert.fail('The worker was denied'); },
});
assert.deepEqual(windowInfo, [5, 6, 7, 'Editor']);
worker = 41;
await denied(':1.32', 'Get');
await denied(':1.32', 'Progress', new Variant('(suuss)', ['id-1', 1, 4, 'Queued', 'active']));
worker = null;
await denied(':1.32', 'Get');

// Authorized progress updates: stack newer above older, sanitize text, fill segments, lock screen, auto-dismiss.
worker = 32;
focus.enable();
let mainBox;
await focus._handleCall(connection, ':1.32', 'Progress',
    new Variant('(suuss)', ['req-1', 1, 4, 'Queued:\n<script>\x07first', 'active']),
    {return_value() {}, return_dbus_error(e) { assert.fail(e); }});
mainBox = focus._toastBox;
assert.equal(chromeCalls[0].opts.affectsInputRegion, false);
assert.equal(mainBox.reactive, false);
assert.equal(mainBox.can_focus, false);
assert.equal(mainBox.children.length, 1);
assert.equal(mainBox.children[0].children[0].text, 'Queued: <script> first');
assert.equal(mainBox.children[0].children[1].children.length, 4);

await focus._handleCall(connection, ':1.32', 'Progress',
    new Variant('(suuss)', ['req-2', 2, 4, 'Connecting', 'active']),
    {return_value() {}, return_dbus_error(e) { assert.fail(e); }});
assert.equal(mainBox.children.length, 2);
// Newer card inserted at index 0 (above req-1).
assert.equal(mainBox.children[0].children[0].text, 'Connecting');
assert.equal(mainBox.children[1].children[0].text, 'Queued: <script> first');
assert.deepEqual(mainBox.pos, [1920 - 276 - 16, 1080 - 96 - 16]);

// Lock screen hides toast container; unlock restores it.
Main.sessionMode.isLocked = true;
focus._syncToastVisibility();
assert.equal(mainBox.visible, false);
Main.sessionMode.isLocked = false;
focus._syncToastVisibility();
assert.equal(mainBox.visible, true);

// Completing req-1 schedules dismiss timer and removes it cleanly when fired.
await focus._handleCall(connection, ':1.32', 'Progress',
    new Variant('(suuss)', ['req-1', 4, 4, 'Created KIAN-1', 'done']),
    {return_value() {}, return_dbus_error(e) { assert.fail(e); }});
const req1Entry = focus._toasts.get('req-1');
assert.ok(req1Entry.dismissId);
const fired = timers.get(req1Entry.dismissId);
timers.delete(req1Entry.dismissId);
fired();
assert.equal(focus._toasts.has('req-1'), false);
assert.equal(mainBox.children.length, 1);

// Cap at MAX_TOASTS (8) evicts oldest and disable() clears everything.
for (let i = 0; i < 10; i++) {
    await focus._handleCall(connection, ':1.32', 'Progress',
        new Variant('(suuss)', [`cap-${i}`, 1, 4, `Req ${i}`, 'active']),
        {return_value() {}, return_dbus_error(e) { assert.fail(e); }});
}
assert.equal(focus._toasts.size, 8);
focus.disable();
assert.equal(focus._toasts.size, 0);
assert.equal(focus._toastBox, null);
assert.equal(timers.size, 0);
"""
        extension = Path(__file__).resolve().parents[1] / "hex/gnome/hex-focus@clusterfork/extension.js"
        result = subprocess.run(["node", "--input-type=module", "-", str(extension)], input=script,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
