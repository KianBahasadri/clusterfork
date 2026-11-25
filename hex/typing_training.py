"""Collect training samples from the dedicated GTK text field."""

from functools import cache
from html import escape
from itertools import cycle, groupby
from time import monotonic

from gi.repository import Gdk, Gtk, Pango

from biocadence import BACKSPACE, fit
from typing_profiles import PROFILES


MIN_KEYS = 40
PASSAGE = (
    "On Friday morning, the library opened a little early. Light fell across the wooden tables, "
    "and the first visitors settled in with books and coffee. Outside, a cyclist rang a bell twice. "
    "By noon, the quiet room had filled with the sound of turning pages."
)
THINKING_PROMPTS = (
    "Write about your favorite food and what makes it special.",
    "Describe a place you know well.",
    "Write about one of your best friends and a memory you share.",
    "Describe how you would spend a free afternoon.",
    "Write about a song you keep coming back to.",
    "Tell the story of a small thing that made you laugh.",
    "Describe a hobby you would like to learn.",
    "Write about a book, film, or game you would recommend.",
    "Describe a trip you would like to take.",
    "Write about a useful thing someone taught you.",
    "Describe a tradition you look forward to.",
    "Write about a project you would enjoy starting.",
    "Describe your ideal rainy day.",
    "Write about an ordinary object that has a story.",
    "Describe a childhood memory you can picture clearly.",
)
thinking_prompts = cycle(THINKING_PROMPTS)


@cache
def style_training(display):
    provider = Gtk.CssProvider()
    provider.load_from_string("""
        window.hex-training, .hex-training headerbar {
            background: #323437;
            color: #d1d0c5;
        }
        .hex-training .training-caption {
            color: #a5a6a2;
        }
        .hex-training .training-progress {
            color: #e2b714;
            font-family: monospace;
        }
        .hex-training .copy-passage {
            font-family: "Roboto Mono", monospace;
            font-size: 22px;
        }
        .hex-training .thinking-prompt {
            font-size: 18px;
        }
        .hex-training textview, .hex-training textview text {
            background: #2c2e31;
            color: #d1d0c5;
            caret-color: #e2b714;
        }
        .hex-training textview {
            font-family: "Roboto Mono", monospace;
            font-size: 15px;
        }
        .hex-training button.suggested-action {
            background: #e2b714;
            color: #222326;
        }
        .hex-training button.suggested-action:disabled {
            background: #404247;
            color: #a5a6a2;
        }
    """)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def copy_exercise():
    passage = Gtk.Label(xalign=0, yalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                        max_width_chars=60, can_target=False)
    passage.add_css_class("copy-passage")
    field = Gtk.Text(max_length=len(PASSAGE), opacity=0)
    field.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
                          ["Copy typing input", PASSAGE])
    overlay = Gtk.Overlay(margin_top=10, margin_bottom=12)
    overlay.set_child(passage)
    overlay.add_overlay(field)
    field.connect("paste-clipboard", lambda field: field.stop_emission_by_name("paste-clipboard"))

    def render(*_args):
        typed = field.get_text()
        cursor = field.get_position()

        def style(item):
            index, char = item
            color = "#a5a6a2"
            if index < len(typed):
                color = "#d1d0c5" if char == typed[index] else "#fa7b88"
            caret = ' underline="single" underline_color="#e2b714"' if index == cursor else ""
            return f'foreground="{color}"{caret}'

        # Keep the reference text in place so mistakes do not shift line breaks.
        passage.set_markup("".join(
            f"<span {attributes}>{escape(''.join(char for _, char in chars))}</span>"
            for attributes, chars in groupby(enumerate(PASSAGE), key=style)
        ))

    field.connect("changed", render)
    field.connect("notify::cursor-position", render)
    render()
    return overlay, field


def open_training(parent, profile, save):
    copying = profile == "copy"
    window = Gtk.Window(title=PROFILES[profile], transient_for=parent, modal=True,
                        destroy_with_parent=True, default_width=680 if copying else 530)
    window.add_css_class("hex-training")
    style_training(window.get_display())
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=20,
                      margin_bottom=20, margin_start=24, margin_end=24)
    window.set_child(content)
    header = Gtk.Box(spacing=12)
    caption = Gtk.Label(label="Type the passage" if copying else "Write in your own words",
                        hexpand=True, xalign=0)
    caption.add_css_class("training-caption")
    progress = Gtk.Label(label=f"0 / {MIN_KEYS} typed")
    progress.add_css_class("training-progress")
    header.append(caption)
    header.append(progress)
    content.append(header)

    if copying:
        exercise, field = copy_exercise()
        content.append(exercise)
    else:
        prompt = Gtk.Label(label=next(thinking_prompts), xalign=0, wrap=True)
        prompt.add_css_class("thinking-prompt")
        content.append(prompt)
        field = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, top_margin=10,
                             bottom_margin=10, left_margin=10, right_margin=10)
        scroll = Gtk.ScrolledWindow(min_content_height=168, has_frame=True, vexpand=True)
        scroll.set_child(field)
        content.append(scroll)

    status = Gtk.Label(xalign=0, wrap=True, visible=False)
    status.add_css_class("error")
    content.append(status)
    controls = Gtk.Box(spacing=8, halign=Gtk.Align.END)
    cancel = Gtk.Button(label="Cancel")
    cancel.add_css_class("flat")
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
            if copying:
                return False
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
                progress.set_label(f"{count} / {MIN_KEYS} typed" if count < MIN_KEYS else f"{count} typed")
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
    focus = Gtk.EventControllerFocus()
    focus.connect("leave", lambda _: pending.clear())
    field.add_controller(focus)
    finish.connect("clicked", submit)
    cancel.connect("clicked", lambda _: window.close())
    window.connect("close-request", close)
    window.set_focus(field)
    window.present()
    return window
