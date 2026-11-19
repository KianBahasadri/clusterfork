"""Collect a multiline Linear request or an answer to a dictation question."""

import argparse
import base64
import json
import sys

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from common.images import MAX_IMAGES, MAX_IMAGE_BYTES, MAX_PIXELS, MAX_SIDE, MAX_TOTAL_BYTES, ImageContextError, validate_image


def activate(app, question, new_request=False):
    window = Gtk.ApplicationWindow(application=app, title="Linear request" if new_request else "Reply to Linear dictation",
                                   default_width=328)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14,
                      margin_bottom=14, margin_start=14, margin_end=14)
    window.set_child(content)
    content.append(Gtk.Label(label=question, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                             max_width_chars=34))
    hint = Gtk.Label(label=("Type your request." if new_request else
                           "Dictate with your regular Hex shortcut or type your answer.") +
                          " Paste images with Ctrl+V. Send with Ctrl+Enter.", xalign=0,
                     wrap=True, max_width_chars=34)
    hint.add_css_class("dim-label")
    content.append(hint)

    answer = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                          top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
    scroll = Gtk.ScrolledWindow(min_content_height=144, has_frame=True, vexpand=True,
                                hscrollbar_policy=Gtk.PolicyType.NEVER)
    scroll.set_child(answer)
    content.append(scroll)

    attachments = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    previews = Gtk.ScrolledWindow(max_content_height=220, propagate_natural_height=True,
                                  hscrollbar_policy=Gtk.PolicyType.NEVER, visible=False)
    previews.set_child(attachments)
    content.append(previews)
    paste = Gtk.Button(label="Paste image", halign=Gtk.Align.START)
    content.append(paste)
    error = Gtk.Label(xalign=0, wrap=True, max_width_chars=34, visible=False)
    error.add_css_class("error")
    content.append(error)

    buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
    cancel = Gtk.Button(label="Cancel")
    send = Gtk.Button(label="Send request" if new_request else "Send reply", sensitive=False)
    send.add_css_class("suggested-action")
    buttons.append(cancel)
    buttons.append(send)
    content.append(buttons)

    buffer = answer.get_buffer()
    images = []
    reading = submitted = False
    cancellable = Gio.Cancellable()

    def text():
        start, end = buffer.get_bounds()
        return buffer.get_text(start, end, False).strip()

    def submit(_button):
        nonlocal submitted
        if submitted or not send.get_sensitive():
            return
        submitted = True
        print(json.dumps({"text": text(), "images": [
            {"mime_type": "image/png", "data": base64.b64encode(image).decode("ascii")} for image in images
        ]}, ensure_ascii=False))
        window.close()

    def update_send(*_args):
        send.set_sensitive(bool(text()) and not reading)

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
            previews.set_visible(True)
        except GLib.Error:
            if not cancellable.is_cancelled():
                show_error("Could not read the clipboard image. Copy a PNG, JPEG, or WebP image and try again.")
        except ImageContextError as failure:
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
    window.connect("close-request", lambda _: cancellable.cancel() or False)
    window.set_focus(answer)
    window.present()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-request", action="store_true", help="Collect a new request instead of a reply")
    args = parser.parse_args()
    app = Gtk.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", activate, sys.stdin.read(), args.new_request)
    sys.exit(app.run([sys.argv[0]]))
