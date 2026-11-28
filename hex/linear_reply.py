"""Build the shared Linear and Typing dialog and the Linear reply window."""

import base64
from datetime import datetime
from functools import cache
import json
from pathlib import Path
import sys
from threading import Thread

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from linear_images import MAX_IMAGES, MAX_IMAGE_BYTES, MAX_PIXELS, MAX_SIDE, MAX_TOTAL_BYTES, ImageContextError, validate_image
from linear_issue import fetch_linear_projects, load_cached_projects, load_history
from typing_profiles import PROFILES, load_profiles, save_profiles
from typing_training import open_training


TABBED_SHORTCUTS = (
    ("Ctrl+Enter", "Send request / Type text"),
    ("Enter", "New line"),
    ("Ctrl+V / Ctrl+Shift+V", "Paste text or image (Linear tab)"),
    ("Double-tap numpad /", "Open or switch to Linear tab"),
    ("Hold numpad 9", "Dictate and type"),
    ("Double-tap numpad 9", "Open or switch to Typing tab"),
    ("Escape", "Back / Close"),
)

REPLY_SHORTCUTS = (
    ("Ctrl+Enter", "Send reply"),
    ("Enter", "New line"),
    ("Ctrl+V / Ctrl+Shift+V", "Paste text or image"),
    ("Escape", "Close"),
)


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
        .hex-help-button {
            min-width: 26px;
            min-height: 26px;
            padding: 0;
            border-radius: 999px;
            font-weight: 600;
            color: alpha(@theme_fg_color, 0.72);
            border: 1px solid alpha(@theme_fg_color, 0.22);
        }
        .hex-project-tray {
            padding: 6px;
            border-radius: 8px;
            border: 1px solid alpha(@theme_fg_color, 0.14);
            background: alpha(@theme_fg_color, 0.03);
        }
        .hex-project-chip {
            min-height: 0;
            min-width: 0;
            padding: 3px 10px;
            border-radius: 999px;
            font-size: 0.86em;
            background-image: none;
            box-shadow: none;
            background-color: alpha(@theme_fg_color, 0.06);
            border: 1px solid alpha(@theme_fg_color, 0.14);
            color: alpha(@theme_fg_color, 0.86);
        }
        .hex-project-chip:hover {
            background-color: alpha(@theme_fg_color, 0.12);
        }
        .hex-project-chip:checked {
            background-color: @theme_selected_bg_color;
            border-color: @theme_selected_bg_color;
            color: @theme_selected_fg_color;
            font-weight: 600;
        }
        .hex-history-card {
            padding: 10px;
            border-radius: 8px;
            border: 1px solid alpha(@theme_fg_color, 0.16);
            background: alpha(@theme_fg_color, 0.03);
        }
    """)
    Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def clipboard_has_image(clipboard):
    formats = clipboard.get_formats()
    return any(kind.is_a(Gdk.Texture) for kind in formats.get_gtypes()) or any(
        mime.startswith("image/") for mime in formats.get_mime_types())


def format_timestamp(raw_time):
    if not isinstance(raw_time, str) or not raw_time:
        return ""
    try:
        moment = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        return moment.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw_time


def build_dialog(window, question, submit_text, *, tab=None, closed=lambda: None, typing_error=None):
    style_tabs(window.get_display())
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14,
                      margin_bottom=14, margin_start=14, margin_end=14)
    linear = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    tabs = stack = history_list = project_scroll = None
    selected_project = None
    window_closed = False
    help_window = None

    help_btn = Gtk.Button(label="?", tooltip_text="Keyboard shortcuts", valign=Gtk.Align.CENTER)
    help_btn.add_css_class("flat")
    help_btn.add_css_class("hex-help-button")
    question_label = Gtk.Label(label=question, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                               max_width_chars=48)

    if tab is None:
        header = Gtk.Box(spacing=8)
        question_label.set_hexpand(True)
        help_btn.set_valign(Gtk.Align.START)
        help_btn.set_halign(Gtk.Align.END)
        header.append(question_label)
        header.append(help_btn)
        linear.append(header)
        content.append(linear)
        window.set_child(content)
    else:
        tabs = Gtk.Notebook(show_border=False)
        tabs.add_css_class("hex-tabs")
        tabs.set_action_widget(help_btn, Gtk.PackType.END)
        linear.set_margin_top(10)
        linear.append(question_label)
        project_bar = Adw.WrapBox(child_spacing=6, line_spacing=6)
        project_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                            propagate_natural_height=True, max_content_height=108)
        project_scroll.add_css_class("hex-project-tray")
        project_scroll.set_child(project_bar)
        linear.append(project_scroll)
        tabs.append_page(linear, Gtk.Label(label="Linear"))
        tabs.append_page(typing_options(window, typing_error), Gtk.Label(label="Typing"))
        content.append(tabs)

        stack = Gtk.Stack()
        stack.add_named(content, "compose")

        history_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=14,
                               margin_bottom=14, margin_start=14, margin_end=14)
        history_header = Gtk.Box(spacing=8)
        back_btn = Gtk.Button(label="Back")
        history_title = Gtk.Label(xalign=0, hexpand=True)
        history_title.set_markup("<b>Recent Linear issues</b>")
        history_header.append(back_btn)
        history_header.append(history_title)
        history_pane.append(history_header)
        history_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        history_scroll = Gtk.ScrolledWindow(vexpand=True, min_content_height=340, max_content_height=520,
                                            propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        history_scroll.set_child(history_list)
        history_pane.append(history_scroll)
        stack.add_named(history_pane, "history")
        window.set_child(stack)

    answer = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                          top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
    scroll = Gtk.ScrolledWindow(min_content_height=160 if tabs else 144, max_content_height=420,
                                propagate_natural_height=True, has_frame=True, vexpand=True,
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
    history_btn = None
    if tabs is not None:
        history_btn = Gtk.Button(label="History", halign=Gtk.Align.START)
        footer.append(history_btn)
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

    def select_project(button, name):
        nonlocal selected_project
        if button.get_active():
            selected_project = name

    def populate_projects(items):
        nonlocal selected_project
        while (child := project_bar.get_first_child()) is not None:
            project_bar.remove(child)
        entries = [(None, Gtk.ToggleButton(label="Auto"))]
        auto_btn = entries[0][1]
        for item in items:
            entries.append((item["name"], Gtk.ToggleButton(label=item["name"], group=auto_btn)))
        names = {name for name, _btn in entries if name is not None}
        if selected_project not in names:
            selected_project = None
        for name, btn in entries:
            btn.add_css_class("hex-project-chip")
            if name == selected_project:
                btn.set_active(True)
            btn.connect("toggled", select_project, name)
            project_bar.append(btn)

    def apply_projects(items):
        if not window_closed:
            populate_projects(items)
        return GLib.SOURCE_REMOVE

    def load_projects():
        fetched = fetch_linear_projects()
        GLib.idle_add(apply_projects, fetched)

    if tabs is not None:
        populate_projects(load_cached_projects())
        Thread(target=load_projects, daemon=True).start()

    def refresh_history():
        while (child := history_list.get_first_child()) is not None:
            history_list.remove(child)
        entries = load_history()
        if not entries:
            empty = Gtk.Label(label="No Linear issues created yet.", xalign=0)
            empty.add_css_class("dim-label")
            history_list.append(empty)
            return
        for entry in entries:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.add_css_class("hex-history-card")
            stamp = format_timestamp(entry.get("created_at"))
            if stamp:
                time_label = Gtk.Label(label=stamp, xalign=0)
                time_label.add_css_class("dim-label")
                card.append(time_label)
            transcript = str(entry.get("transcript") or "")
            if transcript:
                card.append(Gtk.Label(label=transcript, selectable=True, xalign=0, wrap=True,
                                      wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=48))
            for item in entry.get("issues") or ():
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "")
                label = str(item.get("title") or url)
                if url.startswith("https://"):
                    card.append(Gtk.LinkButton(uri=url, label=label, halign=Gtk.Align.START))
                elif label:
                    card.append(Gtk.Label(label=label, xalign=0, wrap=True, max_width_chars=48))
            for shot_path in entry.get("screenshots") or ():
                if not isinstance(shot_path, str) or not Path(shot_path).is_file():
                    continue
                try:
                    texture = Gdk.Texture.new_from_filename(shot_path)
                except GLib.Error:
                    continue
                card.append(Gtk.Picture(paintable=texture, height_request=96, hexpand=True,
                                        can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN))
            history_list.append(card)

    def open_shortcuts(_button):
        nonlocal help_window
        if help_window is not None:
            help_window.present()
            return
        dialog = Gtk.Window(title="Keyboard shortcuts", transient_for=window,
                            destroy_with_parent=True, resizable=False)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=16,
                       margin_bottom=16, margin_start=16, margin_end=16)
        grid = Gtk.Grid(column_spacing=16, row_spacing=8)
        rows = TABBED_SHORTCUTS if tabs is not None else REPLY_SHORTCUTS
        for index, (keys_text, description) in enumerate(rows):
            key_label = Gtk.Label(xalign=0)
            key_label.set_markup(f"<b>{GLib.markup_escape_text(keys_text)}</b>")
            grid.attach(key_label, 0, index, 1, 1)
            grid.attach(Gtk.Label(label=description, xalign=0, wrap=True), 1, index, 1, 1)
        body.append(grid)
        close_btn = Gtk.Button(label="Close", halign=Gtk.Align.END)
        close_btn.connect("clicked", lambda _: dialog.close())
        body.append(close_btn)
        dialog.set_child(body)

        def help_key(_controller, keyval, _keycode, _state):
            if keyval == Gdk.KEY_Escape:
                dialog.close()
                return True
            return False

        def help_close(_win):
            nonlocal help_window
            help_window = None
            return False

        help_keys = Gtk.EventControllerKey()
        help_keys.connect("key-pressed", help_key)
        dialog.add_controller(help_keys)
        dialog.connect("close-request", help_close)
        help_window = dialog
        dialog.present()

    def submit(_button):
        nonlocal submitted
        if submitted or not send.get_sensitive():
            return
        submitted = True
        active = "typing" if typing() else "linear"
        draft = text()
        attached = () if typing() else tuple(("image/png", image) for image in images)
        project = None if typing() else selected_project
        window.close()
        submit_text(active, draft, attached, project)

    def update_send(*_args):
        on_typing = typing()
        ready = typing_error is None if on_typing else not reading
        has_text = bool(text())
        can_send = ready and (has_text or (tabs is None and bool(images)))
        send.set_sensitive(can_send)
        draft_hint.set_visible(on_typing and ready and not has_text)

    def tab_changed(*_args):
        send.set_label("Type text" if typing() else "Send request")
        paste.set_visible(not typing())
        if history_btn is not None:
            history_btn.set_visible(not typing())
        if project_scroll is not None:
            project_scroll.set_visible(not typing())
        previews.set_visible(bool(images) and not typing())
        placeholders.set_visible(typing())
        error.set_visible(False)
        update_send()
        window.set_focus(answer)

    def present(name, token=None):
        if stack is not None:
            stack.set_visible_child_name("compose")
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
                update_send()

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
        if clipboard_has_image(view.get_clipboard()):
            view.stop_emission_by_name("paste-clipboard")
            paste_image()

    def key_pressed(_controller, keyval, _keycode, state):
        if stack is not None and stack.get_visible_child_name() == "history":
            if keyval == Gdk.KEY_Escape:
                stack.set_visible_child_name("compose")
                window.set_focus(answer)
                return True
            if keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter} and state & Gdk.ModifierType.CONTROL_MASK:
                return True
            return False
        mods = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.ALT_MASK)
        if keyval in {Gdk.KEY_v, Gdk.KEY_V} and mods == (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
            if not typing() and clipboard_has_image(answer.get_clipboard()):
                paste_image()
            else:
                answer.emit("paste-clipboard")
            return True
        if keyval in {Gdk.KEY_Return, Gdk.KEY_KP_Enter} and state & Gdk.ModifierType.CONTROL_MASK:
            submit(send)
            return True
        if keyval != Gdk.KEY_Escape:
            return False
        window.close()
        return True

    help_btn.connect("clicked", open_shortcuts)
    buffer.connect("changed", update_send)
    answer.connect("paste-clipboard", paste_clipboard)
    paste.connect("clicked", paste_image)
    if history_btn is not None:
        history_btn.connect("clicked", lambda _: (refresh_history(), stack.set_visible_child_name("history")))
        back_btn.connect("clicked", lambda _: (stack.set_visible_child_name("compose"), window.set_focus(answer)))
    send.connect("clicked", submit)
    cancel.connect("clicked", lambda _: window.close())
    keys = Gtk.EventControllerKey()
    keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    keys.connect("key-pressed", key_pressed)
    window.add_controller(keys)

    def close(_window):
        nonlocal window_closed
        window_closed = True
        if help_window is not None:
            help_window.close()
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
    window = Gtk.Window(title="Hex Dictation", default_width=520)
    present = build_dialog(window, "Describe the Linear issues to create or update.", submit,
                           tab=tab, closed=closed, typing_error=typing_error)
    present(tab, token)
    return present


def activate(app, question):
    window = Gtk.ApplicationWindow(application=app, title="Reply to Linear dictation", default_width=328)

    def reply(_tab, text, images, _project=None):
        print(json.dumps({"text": text, "images": [
            {"mime_type": mime, "data": base64.b64encode(image).decode("ascii")} for mime, image in images
        ]}, ensure_ascii=False))

    build_dialog(window, question, reply)(None)

if __name__ == "__main__":
    app = Gtk.Application(flags=Gio.ApplicationFlags.NON_UNIQUE)
    app.connect("activate", activate, sys.stdin.read())
    sys.exit(app.run([sys.argv[0]]))
