"""Replay a BioCadence plan in the original GNOME window and field."""

from collections import deque
from dataclasses import dataclass
from queue import Empty, SimpleQueue
from threading import Event, Lock, current_thread, main_thread
from time import monotonic

from biocadence import BACKSPACE, plan, unicode_entry
from typing_profiles import selected_model


INTERFACE = "io.github.KianBahasadri.Clusterfork.HexFocus"
PATH = "/io/github/KianBahasadri/Clusterfork/HexFocus"
FOCUS_LOST = "The original typing destination lost focus."
SHIFT, CONTROL, RETURN, TAB = 0xFFE1, 0xFFE3, 0xFF0D, 0xFF09


class TypingError(ValueError):
    pass


def on_main_thread(function, *args):
    """Keep AT-SPI's cache access on the GLib thread that dispatches its events."""
    if current_thread() is main_thread():
        return function(*args)
    from gi.repository import GLib

    results = SimpleQueue()

    def invoke():
        try:
            results.put((function(*args), None))
        except Exception as error:
            results.put((None, error))
        return GLib.SOURCE_REMOVE

    source = GLib.idle_add(invoke)
    try:
        value, error = results.get(timeout=0.75)
    except Empty as error:
        GLib.source_remove(source)
        raise TypingError("Hex could not check the destination's focus.") from error
    if error is not None:
        raise error
    return value


def bridge(method, signature=None, *args):
    from gi.repository import Gio, GLib

    parameters = GLib.Variant(signature, args) if signature else None
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return bus.call_sync("org.gnome.Shell", PATH, INTERFACE, method, parameters,
                             None, Gio.DBusCallFlags.NO_AUTO_START, 500, None).unpack()
    except GLib.Error as error:
        raise TypingError("Hex Typing Focus is unavailable. Sign out and back in to load it.") from error


def accessibility():
    import gi

    try:
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError):
        return None
    Atspi.set_timeout(65, 65)
    return Atspi


def focused_field(pid):
    """Look up the focused editor if its application exposes one over AT-SPI."""
    from gi.repository import GLib

    atspi = accessibility()
    if atspi is None or pid <= 0:
        return None
    deadline = monotonic() + 0.15
    try:
        desktop = atspi.get_desktop(0)
        if desktop is None:
            return None
        for index in range(desktop.get_child_count()):
            if monotonic() >= deadline:
                break
            app = desktop.get_child_at_index(index)
            if app is None or app.get_process_id() != pid:
                continue
            pending = deque([app])
            while pending and monotonic() < deadline:
                child = pending.popleft()
                state = child.get_state_set()
                if state.contains(atspi.StateType.FOCUSED) and state.contains(atspi.StateType.EDITABLE):
                    return child
                for item in range(child.get_child_count()):
                    if monotonic() >= deadline:
                        break
                    descendant = child.get_child_at_index(item)
                    if descendant is not None:
                        pending.append(descendant)
    except GLib.Error:
        pass
    return None


def field_focused(field):
    if field is None:
        return True
    from gi.repository import Atspi, GLib

    try:
        field.clear_cache_single()
        state = field.get_state_set()
    except GLib.Error:
        return False
    return state is not None and state.contains(Atspi.StateType.FOCUSED) and not state.contains(Atspi.StateType.DEFUNCT)


@dataclass(frozen=True)
class Destination:
    window: int
    revision: int
    title: str
    field: object = None


def capture_destination():
    window, revision, pid, title = bridge("Get")
    if not window:
        raise TypingError("Focus a destination before opening Hex typing.")
    return Destination(window, revision, title, on_main_thread(focused_field, pid))


def restore_destination(destination, cancelled):
    from gi.repository import GLib

    if not bridge("Activate", "(u)", destination.window)[0]:
        raise TypingError("The original typing window is no longer available.")
    requested_field = False
    deadline = monotonic() + 0.7
    while not cancelled.is_set() and monotonic() < deadline:
        window, revision, *_ = bridge("Get")
        if window == destination.window:
            if destination.field is None or on_main_thread(field_focused, destination.field):
                return revision
            if not requested_field:
                requested_field = True
                try:
                    def focus_field():
                        component = destination.field.get_component_iface()
                        if component is not None:
                            component.grab_focus()
                    on_main_thread(focus_field)
                except GLib.Error as error:
                    raise TypingError(FOCUS_LOST) from error
        cancelled.wait(0.02)
    if not cancelled.is_set():
        raise TypingError(FOCUS_LOST)
    return None


def play(text, model, destination, cancelled, *, restore=False):
    if "\x00" in text:
        raise TypingError("Hex cannot type text containing a NUL character.")
    if not text or cancelled.is_set():
        return
    events = plan(text, model)
    if cancelled.is_set():
        return
    revision = restore_destination(destination, cancelled) if restore else destination.revision

    def check_destination():
        window, current_revision, *_ = bridge("Get")
        if (window, current_revision) != (destination.window, revision) or not matches_field():
            raise TypingError(FOCUS_LOST)

    def matches_field():
        return destination.field is None or on_main_thread(field_focused, destination.field)

    # Ctrl+Enter closes the dialog while Ctrl is still down. Wait for release
    # before the first key can be interpreted as a shortcut in the target app.
    deadline = monotonic() + 2
    while not cancelled.is_set() and not bridge("Ready")[0]:
        check_destination()
        if monotonic() >= deadline:
            raise TypingError("Release the held modifier keys before typing.")
        cancelled.wait(0.02)
    started = monotonic()

    def send(keysym, pressed):
        if cancelled.is_set():
            return
        if not matches_field():
            raise TypingError(FOCUS_LOST)
        if not cancelled.is_set() and not bridge(
            "Key", "(uuub)", destination.window, revision, keysym, pressed
        )[0]:
            raise TypingError(FOCUS_LOST)

    def unicode_keys(character):
        # IBus and GTK's simple input context accept Ctrl+Shift+U, hex digits and Space.
        sequence = [(CONTROL, True), (SHIFT, True), (ord("u"), True), (ord("u"), False),
                    (SHIFT, False), (CONTROL, False)]
        for digit in f"{ord(character):x} ":
            sequence.extend(((ord(digit), True), (ord(digit), False)))
        for keysym, pressed in sequence:
            if cancelled.is_set():
                return
            send(keysym, pressed)
            cancelled.wait(0.005)

    try:
        for event in events:
            remaining = started + event.time - monotonic()
            while remaining > 0.04:
                if cancelled.wait(0.04):
                    return
                check_destination()
                remaining = started + event.time - monotonic()
            if cancelled.wait(max(0, remaining)):
                return
            if unicode_entry(event.key):
                if event.pressed:
                    unicode_keys(event.key)
            elif event.key == "\n":
                if event.pressed:
                    send(SHIFT, True)
                send(RETURN, event.pressed)
                if not event.pressed:
                    send(SHIFT, False)
            elif event.key == BACKSPACE:
                send(0xFF08, event.pressed)
            elif event.key == "\t":
                send(TAB, event.pressed)
            else:
                send(ord(event.key), event.pressed)
    finally:
        try:
            bridge("End")
        except TypingError:
            pass


class Playback:
    """Allow one replay at a time and let the shortcut stop its wait immediately."""

    def __init__(self):
        self._lock = Lock()
        self._cancelled = None

    def cancel(self):
        with self._lock:
            if self._cancelled is None:
                return False
            self._cancelled.set()
            return True

    def type(self, text, destination, *, restore=False):
        with self._lock:
            if self._cancelled is not None:
                raise TypingError("Hex is already typing. Press numpad 9 to stop it.")
            cancelled = self._cancelled = Event()
        try:
            play(text, selected_model(), destination, cancelled, restore=restore)
        finally:
            with self._lock:
                self._cancelled = None
