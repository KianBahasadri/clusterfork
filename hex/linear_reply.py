"""Collect a multiline Linear request or an answer to a dictation question."""

import argparse
import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, Gtk, Pango


def activate(app, question, new_request=False):
    window = Gtk.ApplicationWindow(application=app, title="Linear request" if new_request else "Reply to Linear dictation",
                                   default_width=328)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14,
                      margin_bottom=14, margin_start=14, margin_end=14)
    window.set_child(content)
    content.append(Gtk.Label(label=question, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                             max_width_chars=34))
    hint = Gtk.Label(label="Type your request, then select Send request." if new_request else
                          "Dictate with your regular Hex shortcut or type your answer.", xalign=0,
                     wrap=True, max_width_chars=34)
    hint.add_css_class("dim-label")
    content.append(hint)

    answer = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                          top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
    scroll = Gtk.ScrolledWindow(min_content_height=144, has_frame=True, vexpand=True,
                                hscrollbar_policy=Gtk.PolicyType.NEVER)
    scroll.set_child(answer)
    content.append(scroll)

    buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
    cancel = Gtk.Button(label="Cancel")
    send = Gtk.Button(label="Send request" if new_request else "Send reply", sensitive=False)
    send.add_css_class("suggested-action")
    buttons.append(cancel)
    buttons.append(send)
    content.append(buttons)

    buffer = answer.get_buffer()

    def text():
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, False).strip()

    def submit(_button):
        print(text())
        window.close()

    def escape(_controller, keyval, _keycode, _state):
        if keyval != Gdk.KEY_Escape:
            return False
        window.close()
        return True

    buffer.connect("changed", lambda _: send.set_sensitive(bool(text())))
    send.connect("clicked", submit)
    cancel.connect("clicked", lambda _: window.close())
    keys = Gtk.EventControllerKey()
    keys.connect("key-pressed", escape)
    window.add_controller(keys)
    window.set_focus(answer)
    window.present()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-request", action="store_true", help="Collect a new request instead of a reply")
    args = parser.parse_args()
    app = Gtk.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", activate, sys.stdin.read(), args.new_request)
    sys.exit(app.run([sys.argv[0]]))
