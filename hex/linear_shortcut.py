"""Hold keypad slash to dictate a Linear request, or double-tap to type it."""

import argparse
import base64
from dataclasses import replace
from html import escape
import json
import os
from pathlib import Path
from queue import SimpleQueue
import re
import signal
import subprocess
import sys
from tempfile import TemporaryFile
from threading import Event, Thread
import time
from uuid import uuid4

from linear_images import EXTENSIONS, MAX_IMAGES, MAX_IMAGE_BYTES, MAX_TOTAL_BYTES, ImageContextError, validate_image

from linear_issue import Dictation, FilingError, ReplyNeeded, file_dictation


APP = "io.github.KianBahasadri.Clusterfork.LinearDictation"
SHORTCUT = "file-issue"
PORTAL = "org.freedesktop.portal.Desktop"
DESKTOP = "/org/freedesktop/portal/desktop"
SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts"
NOTIFY = ["notify-send", "--app-name", "Linear Dictation", "--icon", "accessories-text-editor"]
HOLD_MS = 250
DOUBLE_TAP_MS = 350
IMAGE_REQUEST = re.compile(
    r"(?:^|[.!?,;]\s*|\band\s+)(?:(?:can|could|would) you\s+)?(?:please\s+)?"
    r"(?:(?P<attach>attach|include|copy|add|use|embed|paste)|check|read|inspect|look\s+at)\s+"
    r"(?:(?:the|my|this|an?)\s+)?"
    r"(?:clipboard\s+(?:image|screenshot|picture|photo)|(?:current\s+)?(?:image|screenshot|picture|photo)\s+"
    r"(?:(?:that is|that’s|that's)\s+)?(?:currently\s+)?(?:in|on|from|of)\s+(?:(?:my|the)\s+)?clipboard)\b",
    re.IGNORECASE | re.MULTILINE,
)


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
            result = subprocess.run([*NOTIFY, *actions, "--", title, escape(message)], text=True, capture_output=True,
                                    pass_fds=(token_fd,) if url else (), timeout=None if url else 5, check=True)
            if url and result.stdout.strip() in {"default", "open"}:
                token_file.seek(0)
                token = token_file.read(4096).decode().strip()
                environment = {**os.environ, "XDG_ACTIVATION_TOKEN": token} if token else os.environ
                subprocess.run(["xdg-open", url], env=environment, capture_output=True, timeout=15, check=True)
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Linear notification or link failed ({type(error).__name__}).")


def copy_transcript(transcript):
    try:
        expected = transcript.encode("utf-8")
        subprocess.run(["wl-copy", "--type", "text/plain;charset=utf-8"], input=expected,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=5)
        copied = subprocess.run(["wl-paste", "--no-newline", "--type", "text/plain;charset=utf-8"],
                                capture_output=True, check=True, timeout=5).stdout
        if copied != expected:
            raise FilingError("The clipboard did not retain the transcript.")
    except (OSError, subprocess.SubprocessError, FilingError) as error:
        print(f"Hex Linear clipboard copy failed ({type(error).__name__}).")
        notify("Linear dictation", "Could not verify the clipboard copy. Recover the transcript from Hex History.")
        return False
    return True


def clipboard_image():
    try:
        types = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True,
                               check=True, timeout=3).stdout.splitlines()
        mime = next((value for value in EXTENSIONS if value in types), None)
        if mime is None:
            return None
        with TemporaryFile() as file:
            subprocess.run(["wl-paste", "--no-newline", "--type", mime], stdout=file, stderr=subprocess.DEVNULL,
                           check=True, timeout=5)
            file.seek(0)
            data = file.read(MAX_IMAGE_BYTES + 1)
        validate_image(data, mime)
        return mime, data
    except (ImportError, OSError, subprocess.SubprocessError, ImageContextError) as error:
        print(f"Could not read a supported clipboard image ({type(error).__name__}).")
        return None


def reply_image(environment):
    image = clipboard_image()
    while image is None:
        dialog = subprocess.run(["zenity", "--question", "--title=Add a clipboard image", "--width=440",
                                 "--ok-label=Use Clipboard Image", "--text",
                                 "No image could be read from the clipboard. Copy the screenshot, "
                                 "then select Use Clipboard Image."], env=environment, capture_output=True)
        if dialog.returncode == 1:
            return None
        dialog.check_returncode()
        image = clipboard_image()
    return image


def text_entry(question, environment, *, new_request=False):
    command = [sys.executable, str(Path(__file__).with_name("linear_reply.py"))]
    if new_request:
        command.append("--new-request")
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
    return (result["text"], images) if result["text"].strip() else None


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
                                    escape(f"{question}\n\n{excerpt}")], capture_output=True, text=True,
                                   pass_fds=(token_fd,), check=True)
            if click.stdout.strip() not in {"default", "reply"}:
                return
            token_file.seek(0)
            token = token_file.read(4096).decode().strip()
        environment = {**os.environ, "XDG_ACTIVATION_TOKEN": token} if token else os.environ
        if error.copy_image:
            image = reply_image(environment)
            if image is not None:
                jobs.put(replace(dictation, image_use=dictation.image_use or "reference", images=(image,)))
            return
        if entry := text_entry(question, environment):
            answer, images = entry
            jobs.put(replace(dictation, answers=(*dictation.answers, (question, answer)),
                             images=(*dictation.images, *images), image_use="attach" if images else dictation.image_use))
    except FilingError as failure:
        notify("Linear dictation", str(failure))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as failure:
        print(f"Could not open a Linear dictation reply ({type(failure).__name__}).")
        notify("Linear dictation", "Could not open the reply. Check that notify-send, GTK 4 and Zenity are installed.")


def request_from_text(transcript, title, request_id, images=()):
    image_use = "attach" if images else None
    if instruction := IMAGE_REQUEST.search(transcript):
        image_use = "attach" if instruction["attach"] else "reference"
        if not images:
            image = clipboard_image()
            images = (image,) if image else ()
    return Dictation(transcript, title, request_id, image_use=image_use, images=images)


def compose(jobs, stopping, token):
    request_id = str(uuid4())
    try:
        title = focused_title()
        environment = {**os.environ, "XDG_ACTIVATION_TOKEN": token} if token else os.environ
        print(f"Hex Linear request {request_id} opened the text box.")
        entry = text_entry("Describe the Linear issues to create or update.", environment, new_request=True)
        if entry and not stopping.is_set():
            text, images = entry
            jobs.put(request_from_text(text, title, request_id, images))
            print(f"Hex Linear request {request_id} typed and queued.")
    except FilingError as error:
        notify("Linear request", str(error))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"Could not open a Linear request ({type(error).__name__}).")
        notify("Linear request", "Could not open the text box. Check that GTK 4 is installed.")


def capture(released, stopping, jobs):
    dictation_id = str(uuid4())
    try:
        status = hex_command("status")
        if status["capture"]["state"] not in {"idle", "transcribing"}:
            raise FilingError("Hex is already recording or is not ready. Release that shortcut and try again.")
        hex_command("dictation", "start", "--raw")
        submitted = False
        try:
            print(f"Hex Linear dictation {dictation_id} started recording.")
            title = None if released.is_set() else focused_title()
            while not released.wait(0.25):
                if stopping.is_set():
                    return
            if stopping.is_set():
                return
            print(f"Hex Linear dictation {dictation_id} requested transcription after release.")
            submitted = True
            result = hex_command("dictation", "stop", "--print", timeout=75)
        finally:
            if not submitted:
                try:
                    hex_command("dictation", "cancel")
                except (OSError, ValueError, subprocess.SubprocessError, FilingError):
                    pass
        transcript = result["text"]
        if transcript.strip():
            dictation = request_from_text(transcript, title, dictation_id)
            if copy_transcript(transcript.strip()):
                print(f"Hex Linear dictation {dictation_id} clipboard copy verified.")
            print(f"Hex Linear dictation {dictation_id} transcribed and queued.")
            jobs.put(dictation)
        else:
            print(f"Hex Linear dictation {dictation_id} was empty.")
    except FilingError as error:
        print(f"Hex Linear dictation {dictation_id} could not record. {error}")
        notify("Linear dictation", str(error))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"Hex Linear dictation {dictation_id} capture failed ({type(error).__name__}).")
        notify("Linear dictation", "Could not capture or transcribe audio. Check that Hex is running.")


def notify_saved_issue(saved, dictation_id):
    title, message, url = saved["title"], saved["message"], saved["url"]
    print(f"Hex Linear dictation {dictation_id} {title}. {url}")
    Thread(target=notify, args=(title, f"{message}\n{url}", url), daemon=True).start()


def process_jobs(jobs, dry_run):
    while True:
        dictation = jobs.get()
        try:
            saved = file_dictation(dictation, dry_run=dry_run)
        except ReplyNeeded as error:
            print(f"Hex Linear dictation {dictation.issue_id} needs a reply. See the desktop notification.")
            Thread(target=offer_reply, args=(jobs, dictation, error), daemon=True).start()
        except FilingError as error:
            print(f"Hex Linear dictation {dictation.issue_id} did not proceed. {error}")
            notify("Linear dictation", str(error))
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
            print(f"Hex Linear dictation {dictation.issue_id} could not be confirmed ({type(error).__name__}).")
            notify("Linear dictation", "Could not confirm the requested issue changes. Check Linear and the service journal.")
        else:
            if not dry_run:
                for item in saved.notifications:
                    notify_saved_issue(item, dictation.issue_id)
            if saved.error:
                print(f"Hex Linear dictation {dictation.issue_id} could not complete. {saved.error}")
                notify("Linear dictation could not complete", saved.error)
            elif saved.reply:
                pending = replace(dictation, session_id=saved.session_id)
                print(f"Hex Linear dictation {dictation.issue_id} needs a reply in {saved.session_id}.")
                Thread(target=offer_reply, args=(jobs, pending, saved.reply), daemon=True).start()
            elif dry_run:
                print(saved.text)
                notify("Linear dictation preview", saved.text[:1000])


def listen(dry_run):
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    loop = GLib.MainLoop()
    jobs = SimpleQueue()
    stopping = Event()
    released = None
    recording = None
    composing = None
    hold_timer = None
    pressed_at = last_tap = activation_token = None
    closed = False

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
            Thread(target=notify, args=("Linear dictation", "Hex is still transcribing the previous recording."),
                   daemon=True).start()
        else:
            recording = Thread(target=capture, args=(released, stopping, jobs), daemon=True)
            recording.start()
        return GLib.SOURCE_REMOVE

    def shortcut(_bus, _sender, _path, _interface, member, parameters):
        nonlocal released, composing, hold_timer, pressed_at, last_tap, activation_token
        handle, name, timestamp, options = parameters.unpack()
        if handle != session or name != SHORTCUT:
            return
        if member == "Activated" and released is None:
            released, pressed_at = Event(), timestamp
            activation_token = options.get("activation_token")
            hold_timer = GLib.timeout_add(HOLD_MS, begin_capture)
        if member == "Deactivated" and released is not None:
            released.set()
            released = None
            if hold_timer is None:
                return
            GLib.source_remove(hold_timer)
            hold_timer = None
            if timestamp - pressed_at >= HOLD_MS:
                last_tap = None
            elif last_tap is not None and pressed_at - last_tap <= DOUBLE_TAP_MS:
                last_tap = None
                if composing is None or not composing.is_alive():
                    token = options.get("activation_token") or activation_token
                    composing = Thread(target=compose, args=(jobs, stopping, token), daemon=True)
                    composing.start()
            else:
                last_tap = timestamp

    def stop(*_args):
        nonlocal hold_timer
        stopping.set()
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
        binding = [(SHORTCUT, {"description": GLib.Variant("s", "Hold to dictate a Linear request; double-tap to type"),
                               "preferred_trigger": GLib.Variant("s", "KP_Divide")})]
        bound = request("BindShortcuts", "(oa(sa{sv})sa{sv})", (session, binding, ""), {}, 300)
        if not bound or not any(name == SHORTCUT and details.get("trigger_description")
                                for name, details in bound.get("shortcuts", [])):
            print("Linear dictation has no shortcut. Set it in the desktop's global shortcut settings and restart the service.")
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
        trigger = next(details["trigger_description"] for name, details in bound["shortcuts"] if name == SHORTCUT)
        print(f"Linear dictation shortcut ready: {trigger}.")
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
