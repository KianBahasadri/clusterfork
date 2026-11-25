"""Build the shared Linear and Typing dialog and the Linear reply window."""

import base64
from functools import cache
import json
import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from linear_images import MAX_IMAGES, MAX_IMAGE_BYTES, MAX_PIXELS, MAX_SIDE, MAX_TOTAL_BYTES, ImageContextError, validate_image
from typing_profiles import PROFILES, load_profiles, save_profiles
from typing_training import open_training


@cache
def style_tabs(display):
    provider = Gtk.CssProvider()
    provider.load_from_string("""
        .hex-tabs > header.top {
            background: alpha(@theme_fg_color, 0.045);
            border: 0;
            border-bottom: 1px solid alpha(@theme_fg_color, 0.20);
            border-radius: 8px 8px 0 0;
            padding: 4px 4px 0;
            box-shadow: none;
        }
        .hex-tabs > header.top > tabs > tab {
            background: transparent;
            border: 1px solid transparent;
            border-radius: 7px 7px 0 0;
            margin: 0 2px -1px;
            min-height: 0;
            min-width: 62px;
            padding: 5px 14px;
            box-shadow: none;
            color: alpha(@theme_fg_color, 0.72);
        }
        .hex-tabs > header.top > tabs > tab:hover {
            background: alpha(@theme_fg_color, 0.07);
        }
        .hex-tabs > header.top > tabs > tab:checked {
            background: @theme_bg_color;
            border-color: alpha(@theme_fg_color, 0.20);
            border-bottom-color: @theme_bg_color;
            color: @theme_fg_color;
        }
    """)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def build_dialog(window, question, submit_text, *, tab=None, closed=lambda: None, typing_error=None):
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14,
                      margin_bottom=14, margin_start=14, margin_end=14)
    window.set_child(content)
    linear = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    tabs = None
    if tab is None:
        content.append(linear)
    else:
        style_tabs(window.get_display())
        tabs = Gtk.Notebook(show_border=False)
        tabs.add_css_class("hex-tabs")
        linear.set_margin_top(10)
        tabs.append_page(linear, Gtk.Label(label="Linear"))
        tabs.append_page(typing_options(window, typing_error), Gtk.Label(label="Typing"))
        content.append(tabs)
    linear.append(Gtk.Label(label=question, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                            max_width_chars=48))
    hint = Gtk.Label(label=("" if tabs else "Dictate or type your answer. ") +
                           "Ctrl+V adds an image · Ctrl+Enter sends", xalign=0,
                     wrap=True, max_width_chars=48)
    hint.add_css_class("dim-label")
    linear.append(hint)

    answer = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                          top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
    scroll = Gtk.ScrolledWindow(min_content_height=160 if tabs else 144, has_frame=True, vexpand=True,
                                hscrollbar_policy=Gtk.PolicyType.NEVER)
    scroll.set_child(answer)
    content.append(scroll)

    placeholders = typing_placeholders() if tabs else None
    if placeholders is not None:
        content.append(placeholders)

    attachments = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    previews = Gtk.ScrolledWindow(max_content_height=220, propagate_natural_height=True,
                                  hscrollbar_policy=Gtk.PolicyType.NEVER, visible=False)
    previews.set_child(attachments)
    content.append(previews)
    error = Gtk.Label(xalign=0, wrap=True, max_width_chars=34, visible=False)
    error.add_css_class("error")
    content.append(error)

    footer = Gtk.Box(spacing=8)
    paste = Gtk.Button(label="Paste image", halign=Gtk.Align.START)
    footer.append(paste)
    draft_hint = Gtk.Label(label="Enter a draft to enable Type text.", visible=False,
                           xalign=0, wrap=True)
    draft_hint.add_css_class("dim-label")
    footer.append(draft_hint)
    buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, hexpand=True)
    cancel = Gtk.Button(label="Cancel")
    send = Gtk.Button(label="Send request" if tabs else "Send reply", sensitive=False)
    send.add_css_class("suggested-action")
    buttons.append(cancel)
    buttons.append(send)
    footer.append(buttons)
    content.append(footer)

    buffer = answer.get_buffer()
    images = []
    reading = submitted = False
    cancellable = Gio.Cancellable()

    def typing():
        return tabs is not None and tabs.get_current_page() == 1

    def text():
        start, end = buffer.get_bounds()
        value = buffer.get_text(start, end, True)
        return value if typing() else value.strip()

    def submit(_button):
        nonlocal submitted
        if submitted or not send.get_sensitive():
            return
        submitted = True
        active = "typing" if typing() else "linear"
        draft = text()
        attached = () if typing() else tuple(("image/png", image) for image in images)
        window.close()
        submit_text(active, draft, attached)

    def update_send(*_args):
        on_typing = typing()
        ready = typing_error is None if on_typing else not reading
        has_text = bool(text())
        send.set_sensitive(ready and has_text)
        draft_hint.set_visible(on_typing and ready and not has_text)

    def tab_changed(*_args):
        send.set_label("Type text" if typing() else "Send request")
        paste.set_visible(not typing())
        previews.set_visible(bool(images) and not typing())
        placeholders.set_visible(typing())
        error.set_visible(False)
        update_send()
        window.set_focus(answer)

    def present(name, token=None):
        if tabs is not None:
            tabs.set_current_page(1 if name == "typing" else 0)
        if token:
            window.set_startup_id(token)
        window.set_focus(answer)
        window.present()

    def show_error(message):
        error.set_label(message)
        error.set_visible(True)

    def image_ready(clipboard, result):
        nonlocal reading
        try:
            texture = clipboard.read_texture_finish(result)
            if texture is None:
                raise ImageContextError("Copy an image or screenshot, then paste it here.")
            width, height = texture.get_width(), texture.get_height()
            if width * height > MAX_PIXELS or max(width, height) > MAX_SIDE:
                raise ImageContextError("The image is too large. Use at most 20 megapixels and 8192 pixels per side.")
            image = texture.save_to_png_bytes().get_data()
            if len(image) > MAX_IMAGE_BYTES or sum(map(len, images)) + len(image) > MAX_TOTAL_BYTES:
                raise ImageContextError("Use at most 5 MiB per image and 12 MiB of images per message.")
            validate_image(image, "image/png")
            if image in images:
                raise ImageContextError("That image is already attached.")
            if len(images) >= MAX_IMAGES:
                raise ImageContextError("You can attach up to six images per message.")
            row = Gtk.Box(spacing=8)
            picture = Gtk.Picture(paintable=texture, height_request=96, hexpand=True,
                                  can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN)
            remove = Gtk.Button(icon_name="edit-delete-symbolic", tooltip_text="Remove image", valign=Gtk.Align.CENTER)

            def remove_image(_button):
                images.remove(image)
                attachments.remove(row)
                previews.set_visible(bool(images))

            remove.connect("clicked", remove_image)
            row.append(picture)
            row.append(remove)
            attachments.append(row)
            images.append(image)
            previews.set_visible(not typing())
        except GLib.Error:
            if not cancellable.is_cancelled() and not typing():
                show_error("Could not read the clipboard image. Copy a PNG, JPEG, or WebP image and try again.")
        except ImageContextError as failure:
            if not typing():
                show_error(str(failure))
        finally:
            reading = False
            paste.set_sensitive(True)
            update_send()

    def paste_image(*_args):
        nonlocal reading
        if reading:
            return
        reading = True
        error.set_visible(False)
        paste.set_sensitive(False)
        update_send()
        answer.get_clipboard().read_texture_async(cancellable, image_ready)

    def paste_clipboard(view):
        if typing():
            return
        formats = view.get_clipboard().get_formats()
        if any(kind.is_a(Gdk.Texture) for kind in formats.get_gtypes()) or any(
                mime.startswith("image/") for mime in formats.get_mime_types()):
            view.stop_emission_by_name("paste-clipboard")
            paste_image()

    def key_pressed(_controller, keyval, _keycode, state):
        if keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter} and state & Gdk.ModifierType.CONTROL_MASK:
            submit(send)
            return True
        if keyval != Gdk.KEY_Escape:
            return False
        window.close()
        return True

    buffer.connect("changed", update_send)
    answer.connect("paste-clipboard", paste_clipboard)
    paste.connect("clicked", paste_image)
    send.connect("clicked", submit)
    cancel.connect("clicked", lambda _: window.close())
    keys = Gtk.EventControllerKey()
    keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    keys.connect("key-pressed", key_pressed)
    window.add_controller(keys)

    def close(_window):
        cancellable.cancel()
        closed()
        return False

    window.connect("close-request", close)
    if tabs is not None:
        tabs.connect_after("switch-page", tab_changed)
        tab_changed()
    return present


def typing_options(window, typing_error):
    pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=10)
    row = Gtk.Box(spacing=8)
    pane.append(row)
    toggle = Gtk.Box(valign=Gtk.Align.CENTER)
    toggle.add_css_class("linked")
    thinking = Gtk.ToggleButton(label=PROFILES["thoughts"])
    copying = Gtk.ToggleButton(label=PROFILES["copy"], group=thinking)
    toggle.append(thinking)
    toggle.append(copying)
    choices = {"thoughts": thinking, "copy": copying}
    train = Gtk.Button(label="Train BioCadence", valign=Gtk.Align.CENTER, halign=Gtk.Align.END, hexpand=True)
    row.append(toggle)
    row.append(train)
    warning = Gtk.Label(xalign=0, wrap=True)
    warning.add_css_class("warning")
    pane.append(warning)
    if typing_error:
        notice = Gtk.Label(label=typing_error, xalign=0, wrap=True, max_width_chars=48)
        notice.add_css_class("warning")
        pane.append(notice)

    try:
        state = load_profiles()
    except (OSError, KeyError, TypeError, ValueError):
        toggle.set_sensitive(False)
        train.set_sensitive(False)
        warning.set_label("Hex could not read the saved profiles.")
        return pane

    def update_warning():
        selected = state["selected"]
        trained = selected in state["profiles"]
        warning.set_label(f"{PROFILES[selected]} is untrained. Using default timing.")
        warning.set_visible(not trained)

    def select(button, selected):
        if not button.get_active() or selected == state["selected"]:
            return
        try:
            save_profiles({**state, "selected": selected})
        except OSError:
            choices[state["selected"]].set_active(True)
            warning.set_label("Hex could not save the selected profile.")
            warning.set_visible(True)
            return
        state["selected"] = selected
        update_warning()

    def start_training(_button):
        selected = state["selected"]

        def save(model):
            trained = {**state["profiles"], selected: model}
            save_profiles({**state, "profiles": trained})
            state["profiles"] = trained
            update_warning()

        open_training(window, selected, save)

    choices[state["selected"]].set_active(True)
    for profile, button in choices.items():
        button.connect("toggled", select, profile)
    train.connect("clicked", start_training)
    update_warning()
    return pane


def typing_placeholders():
    pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, sensitive=False)
    options = Gtk.Box(spacing=12)
    estimate = Gtk.Button(label="AI-writing estimate")
    estimate.add_css_class("flat")
    options.append(estimate)
    options.append(Gtk.CheckButton(label="Periodic focus breaks"))
    pane.append(options)
    speed_row = Gtk.Box(spacing=8)
    speed_row.append(Gtk.Label(label="Speed"))
    speed = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, -2, 2, 1)
    speed.set_hexpand(True)
    speed.set_value(0)
    speed_row.append(speed)
    speed_row.append(Gtk.Label(label="1×"))
    speed_row.append(Gtk.Label(label="Duration —"))
    pane.append(speed_row)
    return pane


def open_request(tab, token, submit, closed, *, typing_error=None):
    Gtk.init()
    window = Gtk.Window(title="Hex Dictation", default_width=480)
    present = build_dialog(window, "Describe the Linear issues to create or update.", submit,
                           tab=tab, closed=closed, typing_error=typing_error)
    present(tab, token)
    return present


def activate(app, question):
    window = Gtk.ApplicationWindow(application=app, title="Reply to Linear dictation", default_width=328)

    def reply(_tab, text, images):
        print(json.dumps({"text": text, "images": [
            {"mime_type": mime, "data": base64.b64encode(image).decode("ascii")} for mime, image in images
        ]}, ensure_ascii=False))

    build_dialog(window, question, reply)(None)

if __name__ == "__main__":
    app = Gtk.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", activate, sys.stdin.read())
    sys.exit(app.run([sys.argv[0]]))
