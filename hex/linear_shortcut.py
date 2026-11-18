"""Hold keypad slash to record a Hex dictation for Linear."""

import argparse
from html import escape
from http.client import HTTPConnection, HTTPException
import json
import os
from pathlib import Path
from queue import SimpleQueue
import signal
import subprocess
import sys
from threading import Event, Thread
import time
from uuid import uuid4

from common.clients import RequestError

from linear_issue import FilingError, file_dictation


APP = "io.github.KianBahasadri.Clusterfork.LinearDictation"
SHORTCUT = "file-issue"
PORTAL = "org.freedesktop.portal.Desktop"
DESKTOP = "/org/freedesktop/portal/desktop"
SHORTCUTS = "org.freedesktop.portal.GlobalShortcuts"
HEX_REGISTRATION = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "hex/local-api.json"


def hex_request(registration, path, *, owner=None, data=None, timeout=4):
    headers = {"Authorization": "Bearer " + registration["token"]}
    if owner:
        headers["X-Hex-Dictation-Token"] = owner
    if data:
        headers["Content-Type"] = "application/json"
    connection = HTTPConnection("127.0.0.1", registration["port"], timeout=timeout)
    try:
        connection.request("POST", path, json.dumps(data) if data else None, headers)
        response = connection.getresponse()
        if response.status == 204:
            return None
        body = json.loads(response.read(262_145))
        if response.status in (200, 201):
            return body
        code = body.get("error") or body.get("code")
        if code == "capture-discarded":
            return {"transcript": ""}
        if code == "dictation-busy":
            raise FilingError("Hex is already recording. Release that shortcut and try again.")
        raise FilingError(f"Hex could not record or transcribe this dictation (HTTP {response.status}).")
    finally:
        connection.close()


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
    command = ["notify-send", "--app-name", "Linear Dictation", "--icon", "accessories-text-editor"]
    if url:
        command += ["--action=open=Open in Linear"]
    try:
        result = subprocess.run([*command, "--", title, escape(message)], text=True, capture_output=True,
                                timeout=120 if url else 5, check=True)
        if url and result.stdout.strip() == "open":
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        pass
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Could not show Linear notification ({type(error).__name__}).")


def capture(released, stopping, jobs):
    try:
        registration = json.loads(HEX_REGISTRATION.read_text())
        record = hex_request(registration, "/dictations", data={"source": "clusterfork-linear"})
        owner, path = record["ownerToken"], f"/dictations/{int(record['id'])}"
        result = None
        try:
            print("Hex Linear recording started.")
            title = None if released.is_set() else focused_title()
            while not released.wait(3):
                hex_request(registration, path + "/heartbeat", owner=owner)
            if stopping.is_set():
                return
            result = hex_request(registration, path + "/finish", owner=owner, timeout=125)
        finally:
            # Finishing consumes the recording. Cancel only when capture or finish fails.
            if result is None:
                try:
                    hex_request(registration, path + "/cancel", owner=owner)
                except (OSError, ValueError, HTTPException):
                    pass  # Hex also cancels the capture when its lease expires.
        transcript = result["transcript"].strip()
        if transcript:
            jobs.put((transcript, title, str(uuid4())))
            print("Hex Linear dictation queued.")
        else:
            print("Hex Linear dictation was empty.")
    except FilingError as error:
        notify("Linear dictation", str(error))
    except (OSError, ValueError, KeyError, TypeError, HTTPException) as error:
        print(f"Hex capture failed ({type(error).__name__}).")
        notify("Linear dictation", "Could not capture or transcribe audio. Check that Hex is running.")


def process_jobs(jobs, dry_run):
    while True:
        transcript, title, issue_id = jobs.get()
        try:
            saved = file_dictation(transcript, title, issue_id, dry_run=dry_run)
        except FilingError as error:
            print("Hex Linear draft did not proceed. See the desktop notification.")
            notify("Linear dictation", str(error))
        except (OSError, ValueError, KeyError, TypeError, RequestError, subprocess.SubprocessError) as error:
            print(f"Hex Linear issue could not be confirmed ({type(error).__name__}).")
            notify("Linear dictation", "Could not confirm issue filing. Check Linear and the service journal.")
        else:
            if dry_run:
                print("Hex Linear dry run validated the issue and dependencies.")
                notify("Linear dictation dry run", "Draft and dependencies validated.")
                continue
            identifier, url = saved["identifier"], saved["url"]
            pending = saved["unverified"]
            lines = []
            for key, label in (("blocked_by", "Blocked by"), ("blocks", "Blocks")):
                verified = [ref for ref in saved[key] if ref not in pending]
                if verified:
                    lines.append(f"{label} {', '.join(verified)}")
            if pending:
                lines.append(f"Could not confirm links to {', '.join(pending)}. Open the issue to check them.")
            lines.append(url)
            print(f"Filed Linear issue {identifier}." + (" Some dependency links are unverified." if pending else ""))
            Thread(target=notify, args=(f"Created {identifier}", "\n".join(lines), url), daemon=True).start()


def listen(dry_run):
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    loop = GLib.MainLoop()
    jobs = SimpleQueue()
    stopping = Event()
    released = None
    recording = None
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

    def shortcut(_bus, _sender, _path, _interface, member, parameters):
        nonlocal released, recording
        handle, name, _timestamp, _options = parameters.unpack()
        if handle != session or name != SHORTCUT:
            return
        if member == "Activated" and released is None:
            released = Event()
            if recording is not None and recording.is_alive():
                Thread(target=notify, args=("Linear dictation", "Hex is still transcribing the previous recording."),
                       daemon=True).start()
                return
            recording = Thread(target=capture, args=(released, stopping, jobs), daemon=True)
            recording.start()
        if member == "Deactivated" and released is not None:
            released.set()
            released = None

    def stop(*_args):
        stopping.set()
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
        binding = [(SHORTCUT, {"description": GLib.Variant("s", "Hold to dictate a Linear issue"),
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
