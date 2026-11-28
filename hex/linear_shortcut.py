"""Double-tap keypad slash for Linear requests, or hold/double-tap keypad 9 to type."""

import argparse
import base64
from dataclasses import replace
from html import escape
import json
import os
from pathlib import Path
from queue import SimpleQueue
import signal
import subprocess
import sys
from tempfile import TemporaryFile
from threading import Event, Thread
import time
from uuid import uuid4

from linear_images import MAX_IMAGES, MAX_TOTAL_BYTES, ImageContextError, validate_image

from linear_issue import Dictation, FilingError, ReplyNeeded, file_dictation, record_history
from typing_replay import Playback, TypingError, capture_destination, on_main_thread, progress_clear, progress_update


APP = "io.github.KianBahasadri.Clusterfork.LinearDictation"
SHORTCUT = "file-issue"
TYPING_SHORTCUT = "type-dictation"
PORTAL = "org.freedesktop.portal.Desktop"
DESKTOP = "/org/freedesktop/portal/desktop"
SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts"
NOTIFY = ["notify-send", "--app-name", "Linear Dictation", "--icon", "accessories-text-editor",
          f"--hint=string:desktop-entry:{APP}"]
HOLD_MS = 250
DOUBLE_TAP_MS = 350


def hex_command(*arguments, timeout=15):
    result = subprocess.run(["hex", *arguments, "--json"], capture_output=True, text=True, timeout=timeout)
    response = json.loads(result.stdout)
    if result.returncode == 4 and response.get("code") == "no-speech":
        return {"status": "transcript", "text": ""}
    if result.returncode or response.get("status") == "error":
        raise FilingError(response.get("message") or "Hex could not record or transcribe this dictation.")
    return response


def focused_title():
    # On GNOME some applications do not publish an accessible window.
    import gi
    from gi.repository import GLib

    try:
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi
    except (ImportError, ValueError):
        return None
    Atspi.set_timeout(100, 100)
    deadline = time.monotonic() + 0.2
    try:
        root = Atspi.get_desktop(0)
        count = root.get_child_count() if root else 0
    except GLib.Error:
        return None
    for index in range(count):
        if time.monotonic() >= deadline:
            break
        try:
            app = root.get_child_at_index(index)
            for child in range(app.get_child_count() if app else 0):
                if time.monotonic() >= deadline:
                    return None
                window = app.get_child_at_index(child)
                if window and window.get_state_set().contains(Atspi.StateType.ACTIVE):
                    return window.get_name()
        except GLib.Error:
            continue
    return None


def notify(title, message, url=None):
    try:
        with TemporaryFile() as token_file:
            token_fd = token_file.fileno()
            actions = ["--expire-time=0", "--action=default=Open in Linear", "--action=open=Open in Linear",
                       f"--activation-token-fd={token_fd}"] if url else []
            result = subprocess.run([*NOTIFY, *actions, "--", title, escape(message, quote=False)], text=True,
                                    capture_output=True, pass_fds=(token_fd,) if url else (),
                                    timeout=None if url else 5, check=True)
            if url and result.stdout.strip() in {"default", "open"}:
                token_file.seek(0)
                token = token_file.read(4096).decode().strip()
                environment = {**os.environ, "XDG_ACTIVATION_TOKEN": token} if token else os.environ
                subprocess.run(["xdg-open", url], env=environment, capture_output=True, timeout=15, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Linear notification or link failed ({type(error).__name__}).")


def text_entry(question, environment):
    command = [sys.executable, str(Path(__file__).with_name("linear_reply.py"))]
    entry = subprocess.run(command, input=question, env=environment, capture_output=True, text=True, check=True)
    if not entry.stdout.strip():
        return None
    result = json.loads(entry.stdout)
    images = tuple((image["mime_type"], base64.b64decode(image["data"], validate=True)) for image in result["images"])
    if len(images) > MAX_IMAGES or sum(len(data) for _mime, data in images) > MAX_TOTAL_BYTES:
        raise FilingError("Use at most six images and 12 MiB of images per message.")
    try:
        for mime, data in images:
            validate_image(data, mime)
    except ImageContextError as error:
        raise FilingError(str(error)) from None
    return (result["text"], images) if (result["text"].strip() or images) else None


def offer_reply(jobs, dictation, error):
    question = str(error)
    excerpt = " ".join(dictation.transcript.split())
    if len(excerpt) > 140:
        excerpt = excerpt[:137] + "..."
    try:
        with TemporaryFile() as token_file:
            token_fd = token_file.fileno()
            click = subprocess.run([*NOTIFY, "--expire-time=0", "--action=default=Reply", "--action=reply=Reply",
                                    f"--activation-token-fd={token_fd}", "--", "Linear dictation needs a reply",
                                    escape(f"{question}\n\n{excerpt}", quote=False)], capture_output=True, text=True,
                                   pass_fds=(token_fd,), check=True)
            if click.stdout.strip() not in {"default", "reply"}:
                return
            token_file.seek(0)
            token = token_file.read(4096).decode().strip()
        environment = {**os.environ, "XDG_ACTIVATION_TOKEN": token} if token else os.environ
        if entry := text_entry(question, environment):
            answer, images = entry
            answers = (*dictation.answers, (question, answer)) if answer.strip() else dictation.answers
            updated = replace(dictation, answers=answers,
                              images=(*dictation.images, *images), image_use="attach" if (images or dictation.images) else dictation.image_use)
            jobs.put(updated)
            progress_update(dictation.issue_id, 1, 4, progress_excerpt(dictation.transcript, "Resuming"), "active")
    except FilingError as failure:
        notify("Linear dictation", str(failure))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as failure:
        print(f"Could not open a Linear dictation reply ({type(failure).__name__}).")
        notify("Linear dictation", "Could not open the reply. Check that notify-send and GTK 4 are installed.")


def progress_excerpt(text, prefix):
    summary = " ".join(text.split())
    if not summary:
        return prefix
    full = f"{prefix}: {summary}"
    return full if len(full) <= 46 else full[:43].rstrip() + "..."


def request_from_text(transcript, title, request_id, images=(), suggested_project=None):
    image_use = "attach" if images else None
    return Dictation(transcript, title, request_id, image_use=image_use, images=images,
                     suggested_project=suggested_project)


def submit_linear(jobs, stopping, text, title, images, suggested_project=None):
    request_id = str(uuid4())
    try:
        if not stopping.is_set() and text.strip():
            jobs.put(request_from_text(text, title, request_id, images, suggested_project=suggested_project))
            progress_update(request_id, 1, 4, progress_excerpt(text, "Queued"), "active")
            print(f"Hex Linear request {request_id} typed and queued.")
    except FilingError as error:
        notify("Linear request", str(error))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"Could not queue a Linear request ({type(error).__name__}).")
        notify("Linear request", "Could not queue the request.")


def submit_typing(playback, stopping, text, destination, *, restore):
    if stopping.is_set():
        return
    try:
        if isinstance(destination, TypingError):
            raise destination
        playback.type(text, destination, restore=restore)
    except (FilingError, TypingError) as error:
        notify("Hex typing", str(error))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"Hex typing failed ({type(error).__name__}).")
        notify("Hex typing", "Could not replay the text. Check the Hex service journal.")


def capture(released, stopping, playback):
    dictation_id = str(uuid4())
    label = "Typed dictation"
    try:
        status = hex_command("status")
        if status["capture"]["state"] not in {"idle", "transcribing"}:
            raise FilingError("Hex is already recording or is not ready. Release that shortcut and try again.")
        destination = capture_destination()
        hex_command("dictation", "start")
        submitted = False
        try:
            print(f"Hex {label} {dictation_id} started recording.")
            while not released.wait(0.25):
                if stopping.is_set():
                    return
            if stopping.is_set():
                return
            print(f"Hex {label} {dictation_id} requested transcription after release.")
            submitted = True
            result = hex_command("dictation", "stop", "--print", timeout=75)
        finally:
            if not submitted:
                try:
                    hex_command("dictation", "cancel")
                except (OSError, ValueError, subprocess.SubprocessError, FilingError):
                    pass
        transcript = result["text"]
        if transcript:
            submit_typing(playback, stopping, transcript, destination, restore=False)
        print(f"Hex {label} {dictation_id} finished.")
    except (FilingError, TypingError) as error:
        print(f"Hex {label} {dictation_id} failed. {error}")
        notify(label, str(error))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"Hex {label} {dictation_id} capture failed ({type(error).__name__}).")
        notify(label, "Could not capture or transcribe audio. Check that Hex is running.")


def notify_saved_issue(saved, dictation_id):
    title, message, url = saved["title"], saved["message"], saved["url"]
    print(f"Hex Linear dictation {dictation_id} {title}. {url}")
    Thread(target=notify, args=(title, f"{message}\n{url}", url), daemon=True).start()


def process_jobs(jobs, dry_run):
    while True:
        dictation = jobs.get()
        try:
            saved = file_dictation(
                dictation,
                dry_run=dry_run,
                progress=lambda step, total, label: progress_update(
                    dictation.issue_id, step, total, label, "active"
                ),
            )
        except ReplyNeeded as error:
            progress_clear(dictation.issue_id)
            print(f"Hex Linear dictation {dictation.issue_id} needs a reply. See the desktop notification.")
            Thread(target=offer_reply, args=(jobs, dictation, error), daemon=True).start()
        except FilingError as error:
            progress_update(dictation.issue_id, 4, 4, "Linear request failed", "error")
            print(f"Hex Linear dictation {dictation.issue_id} did not proceed. {error}")
            notify("Linear dictation", str(error))
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            progress_update(dictation.issue_id, 4, 4, "Linear request failed", "error")
            print(f"Hex Linear dictation {dictation.issue_id} could not be confirmed ({type(error).__name__}).")
            notify("Linear dictation", "Could not confirm the requested issue changes. Check Linear and the service journal.")
        else:
            if not dry_run and saved.notifications:
                record_history(dictation, saved.notifications)
            if not dry_run:
                for item in saved.notifications:
                    notify_saved_issue(item, dictation.issue_id)
            if saved.error:
                progress_update(dictation.issue_id, 4, 4, "Linear request failed", "error")
                print(f"Hex Linear dictation {dictation.issue_id} could not complete. {saved.error}")
                notify("Linear dictation could not complete", saved.error)
            elif saved.reply:
                progress_clear(dictation.issue_id)
                pending = replace(dictation, session_id=saved.session_id)
                print(f"Hex Linear dictation {dictation.issue_id} needs a reply in {saved.session_id}.")
                Thread(target=offer_reply, args=(jobs, pending, saved.reply), daemon=True).start()
            elif dry_run:
                progress_update(dictation.issue_id, 4, 4, "Preview ready", "done")
                print(saved.text)
                notify("Linear dictation preview", saved.text[:1000])
            else:
                count = len(saved.notifications)
                done_label = saved.notifications[0]["title"] if count == 1 else (
                    f"Saved {count} issues" if count > 1 else "Done"
                )
                progress_update(dictation.issue_id, 4, 4, done_label, "done")


def listen(dry_run):
    from gi.repository import Gio, GLib

    # Keep the host app registration on its own connection. GTK may use the
    # process-wide session connection for unrelated portal calls.
    address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
    flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    bus = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
    loop = GLib.MainLoop()
    jobs = SimpleQueue()
    stopping = Event()
    playback = Playback()
    released = None
    active_shortcut = None
    recording = None
    composing = None
    hold_timer = None
    pressed_at = last_tap = activation_token = None
    closed = False

    def show_dialog(tab, token):
        nonlocal composing
        try:
            from linear_reply import open_request

            typing_error = None
            try:
                destination = capture_destination()
                title = destination.title
            except TypingError as error:
                destination = error
                title = focused_title()
                typing_error = str(error)

            def finished():
                nonlocal composing
                composing = None

            def submit(active, text, images, suggested_project=None):
                if active == "typing":
                    Thread(target=submit_typing, args=(playback, stopping, text, destination),
                           kwargs={"restore": True}, daemon=True).start()
                else:
                    Thread(target=submit_linear, args=(jobs, stopping, text, title, images, suggested_project),
                           daemon=True).start()

            composing = open_request(tab, token, submit, finished, typing_error=typing_error)
        except (ImportError, GLib.Error, RuntimeError, ValueError) as error:
            print(f"Could not open the Hex dictation dialog ({type(error).__name__}).")
            Thread(target=notify, args=("Hex dictation", "Could not open the dialog. Check that GTK 4 is installed."),
                   daemon=True).start()

    def call(interface, method, parameters, path=DESKTOP):
        return bus.call_sync(PORTAL, path, interface, method, parameters, None, Gio.DBusCallFlags.NONE, 5000, None)

    def request(method, signature, args, options, timeout):
        token = uuid4().hex
        sender = bus.get_unique_name().lstrip(":").replace(".", "_")
        path = f"{DESKTOP}/request/{sender}/{token}"
        response = []
        waiting = GLib.MainLoop()

        def answer(_bus, _sender, _path, _interface, _signal, parameters):
            response.extend(parameters.unpack())
            waiting.quit()

        subscription = bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Request", "Response", path,
                                            None, Gio.DBusSignalFlags.NONE, answer)
        timer = GLib.timeout_add_seconds(timeout, lambda: waiting.quit() or True)
        try:
            payload = GLib.Variant(signature, (*args, {**options, "handle_token": GLib.Variant("s", token)}))
            call(SHORTCUTS, method, payload)
            waiting.run()
            if not response:
                raise FilingError("The desktop portal did not answer the shortcut request.")
            code, result = response
            if code == 1:
                return None
            if code != 0:
                raise FilingError(f"The desktop portal rejected {method} (response {code}).")
            return result
        finally:
            GLib.source_remove(timer)
            bus.signal_unsubscribe(subscription)

    def begin_capture():
        nonlocal hold_timer, recording, last_tap
        hold_timer = last_tap = None
        if released is None or stopping.is_set():
            return GLib.SOURCE_REMOVE
        if recording is not None and recording.is_alive():
            Thread(target=notify, args=("Typed dictation", "Hex is still processing the previous recording."),
                   daemon=True).start()
        else:
            recording = Thread(target=capture, args=(released, stopping, playback), daemon=True)
            recording.start()
        return GLib.SOURCE_REMOVE

    def shortcut(_bus, _sender, _path, _interface, member, parameters):
        nonlocal released, active_shortcut, hold_timer, pressed_at, last_tap, activation_token
        handle, name, timestamp, options = parameters.unpack()
        if handle != session or name not in (SHORTCUT, TYPING_SHORTCUT):
            return
        if member == "Activated" and released is None:
            released, pressed_at = Event(), timestamp
            active_shortcut = name
            activation_token = options.get("activation_token")
            if name == TYPING_SHORTCUT and playback.cancel():
                last_tap = None
                return
            if composing is not None:
                composing("typing" if name == TYPING_SHORTCUT else "linear", activation_token)
                last_tap = None
                return
            if name == TYPING_SHORTCUT:
                hold_timer = GLib.timeout_add(HOLD_MS, begin_capture)
        if member == "Deactivated" and released is not None and name == active_shortcut:
            released.set()
            released = None
            active_shortcut = None
            if hold_timer is not None:
                GLib.source_remove(hold_timer)
                hold_timer = None
            elif name == TYPING_SHORTCUT:
                return
            if timestamp - pressed_at >= HOLD_MS:
                last_tap = None
            elif last_tap is not None and last_tap[0] == name and pressed_at - last_tap[1] <= DOUBLE_TAP_MS:
                last_tap = None
                token = options.get("activation_token") or activation_token
                show_dialog("typing" if name == TYPING_SHORTCUT else "linear", token)
            else:
                last_tap = name, timestamp

    def stop(*_args):
        nonlocal hold_timer
        stopping.set()
        playback.cancel()
        progress_clear("")
        if hold_timer is not None:
            GLib.source_remove(hold_timer)
            hold_timer = None
        if released is not None:
            released.set()
        loop.quit()
        return GLib.SOURCE_REMOVE

    def portal_closed(*_args):
        nonlocal closed
        closed = True
        stop()

    try:
        call("org.freedesktop.host.portal.Registry", "Register", GLib.Variant("(sa{sv})", (APP, {})))
        created = request("CreateSession", "(a{sv})", (), {"session_handle_token": GLib.Variant("s", uuid4().hex)}, 10)
        if created is None:
            return 0
        session = created["session_handle"]
        binding = [(SHORTCUT, {"description": GLib.Variant("s", "Double-tap to open the Linear request window"),
                               "preferred_trigger": GLib.Variant("s", "KP_Divide")}),
                   (TYPING_SHORTCUT, {"description": GLib.Variant("s", "Hold to dictate and type; double-tap for Typing"),
                                      "preferred_trigger": GLib.Variant("s", "KP_Prior")})]
        bound = request("BindShortcuts", "(oa(sa{sv})sa{sv})", (session, binding, ""), {}, 300)
        triggers = {name: details.get("trigger_description") for name, details in (bound or {}).get("shortcuts", [])}
        if not any(triggers.values()):
            print("Hex dictation has no shortcuts. Set them in the desktop's global shortcut settings and restart the service.")
            return 0
        for member in ("Activated", "Deactivated"):
            bus.signal_subscribe(PORTAL, SHORTCUTS, member, DESKTOP, None, Gio.DBusSignalFlags.NONE, shortcut)
        bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Session", "Closed", session, None,
                             Gio.DBusSignalFlags.NONE, portal_closed)
        bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged", "/org/freedesktop/DBus",
                             PORTAL, Gio.DBusSignalFlags.NONE, portal_closed)
        for signum in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, stop)
        Thread(target=process_jobs, args=(jobs, dry_run), daemon=True).start()
        for name, label in ((SHORTCUT, "Linear dictation"), (TYPING_SHORTCUT, "Typed dictation")):
            print(f"{label} shortcut ready: {triggers.get(name) or 'unbound'}.")
        loop.run()
        if recording is not None and stopping.is_set():
            recording.join(timeout=5)
        return int(closed)
    except (GLib.Error, KeyError, FilingError) as error:
        print(f"Linear shortcut setup failed: {error}", file=sys.stderr)
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate captures without writing to Linear")
    return listen(parser.parse_args().dry_run)


if __name__ == "__main__":
    sys.exit(main())
