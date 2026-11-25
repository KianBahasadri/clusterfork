"""Collect training samples from the dedicated GTK text field."""

from time import monotonic

from gi.repository import Gdk, Gtk

from biocadence import BACKSPACE, fit
from typing_profiles import PROFILES


MIN_KEYS = 40
PASSAGE = (
    "On Friday morning, the library opened a little early. Light fell across the wooden tables, "
    "and the first visitors settled in with books and coffee. Outside, a cyclist rang a bell twice. "
    "By noon, the quiet room had filled with the sound of turning pages."
)


def open_training(parent, profile, save):
    window = Gtk.Window(title=f"Train {PROFILES[profile]}", transient_for=parent,
                        modal=True, destroy_with_parent=True, default_width=480)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14,
                      margin_bottom=14, margin_start=14, margin_end=14)
    window.set_child(content)
    prompt = PASSAGE if profile == "copy" else "Write a few sentences about a place you know well."
    content.append(Gtk.Label(label=prompt, xalign=0, wrap=True, max_width_chars=58))
    content.append(Gtk.Label(label="Hex saves timing statistics from this field. Type at least 40 characters.",
                             xalign=0, wrap=True, max_width_chars=58))
    field = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8,
                         left_margin=8, right_margin=8)
    scroll = Gtk.ScrolledWindow(min_content_height=160, has_frame=True, vexpand=True)
    scroll.set_child(field)
    content.append(scroll)
    status = Gtk.Label(xalign=0, wrap=True, visible=False)
    status.add_css_class("error")
    content.append(status)
    controls = Gtk.Box(spacing=8, halign=Gtk.Align.END)
    cancel = Gtk.Button(label="Cancel")
    finish = Gtk.Button(label="Save profile", sensitive=False)
    finish.add_css_class("suggested-action")
    controls.append(cancel)
    controls.append(finish)
    content.append(controls)

    pending, strokes = {}, []
    count = 0

    def press(_keys, keyval, keycode, modifiers):
        if modifiers & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        value = Gdk.keyval_to_unicode(keyval)
        if keyval == Gdk.KEY_BackSpace:
            key = BACKSPACE
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            key = "\n"
        elif value and (chr(value).isprintable() or value == 9):
            key = chr(value)
        else:
            return False
        pending.setdefault(keycode, (key, monotonic()))
        return False

    def release(_keys, _keyval, keycode, _modifiers):
        nonlocal count
        if keycode in pending:
            key, started = pending.pop(keycode)
            strokes.append((key, started, monotonic()))
            if key != BACKSPACE:
                count += 1
                finish.set_sensitive(count >= MIN_KEYS)

    def submit(_button):
        try:
            save(fit(sorted(strokes, key=lambda stroke: stroke[1])))
        except (OSError, ValueError) as error:
            status.set_label(f"Could not save the profile ({type(error).__name__}).")
            status.set_visible(True)
            return
        window.close()

    def close(_window):
        pending.clear()
        strokes.clear()
        return False

    keys = Gtk.EventControllerKey()
    keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    keys.connect("key-pressed", press)
    keys.connect("key-released", release)
    field.add_controller(keys)
    field.connect("notify::has-focus", lambda *_: pending.clear())
    finish.connect("clicked", submit)
    cancel.connect("clicked", lambda _: window.close())
    window.connect("close-request", close)
    window.set_focus(field)
    window.present()
    return window
