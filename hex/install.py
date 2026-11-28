"""Install Clusterfork's optional Hex workspace and merge its live settings."""

import argparse
import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


SOURCE = Path(__file__).resolve().parent
LINEAR_DESKTOP = "io.github.KianBahasadri.Clusterfork.LinearDictation.desktop"
LINEAR_UNIT = "hex-linear-issue.service"
FOCUS_EXTENSION = "hex-focus@clusterfork"


def write_changed(destination, content):
    """Publish complete files only, without reloading unchanged Hex configs."""
    if destination.is_file() and destination.read_bytes() == content:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".clusterfork-", dir=destination.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
        temporary.chmod(0o644)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def hex_command(binary, *arguments):
    result = subprocess.run(
        [binary, *arguments, "--json"], check=True,
        capture_output=True, text=True, timeout=10,
    )
    return json.loads(result.stdout)


def apply_settings(binary, home, microphone):
    key = "dictation_processing.default_mode"
    current = hex_command(binary, "settings", "get", key)["settings"][0]["value"]
    wanted = deepcopy(current)
    profile = json.loads((SOURCE / "rewrite.json").read_text())
    profile["prompt"] = (SOURCE / "prompt.md").read_text().rstrip("\n").replace("@HOME@", str(home))
    wanted.setdefault("post_processing", {}).update(profile)
    wanted["transformations"] = ["conversation-context"] + [
        item for item in wanted.get("transformations", []) if item != "conversation-context"
    ]
    if wanted != current:
        hex_command(binary, "settings", "set", key, json.dumps(wanted, ensure_ascii=False))

    entries = hex_command(binary, "vocabulary", "list")["entries"]
    known = {entry["term"].casefold(): entry.get("sounds_like", []) for entry in entries}
    for entry in json.loads((SOURCE / "vocabulary.json").read_text()):
        term = entry["term"]
        aliases = {value.casefold() for value in known.get(term.casefold(), [])}
        missing = [value for value in entry.get("sounds_like", []) if value.casefold() not in aliases]
        if term.casefold() in known and not missing:
            continue
        arguments = ["vocabulary", "add", term]
        if missing:
            arguments.extend(["--sounds-like", ",".join(missing)])
        hex_command(binary, *arguments)

    if microphone:
        current_input = hex_command(binary, "settings", "get", "microphone")["settings"][0]["value"]
        if current_input != "PipeWire Sound Server":
            hex_command(binary, "settings", "set", "microphone", "PipeWire Sound Server")


def install_linear_shortcut(home):
    workspace = home / ".config/hex/linear"
    files = [
        (SOURCE / name, workspace / name)
        for name in ("linear_images.py", "linear_issue.py", "linear_reply.py", "linear_shortcut.py",
                     "linear-emoji.json", "EMOJI-LICENSE",
                     "biocadence.py", "BIOCADENCE-LICENSE", "typing_profiles.py", "typing_replay.py", "typing_training.py")
    ] + [
        (SOURCE / "linear-workspace.json", workspace / "opencode.json"),
        (SOURCE / "linear-agent.md", workspace / ".opencode/agents/linear-issue.md"),
        (SOURCE / "linear-search.js", workspace / ".opencode/plugins/linear-search.js"),
        (SOURCE / LINEAR_DESKTOP, home / ".local/share/applications" / LINEAR_DESKTOP),
        (SOURCE / LINEAR_UNIT, home / ".config/systemd/user" / LINEAR_UNIT),
    ]
    changed = False
    for source, destination in files:
        changed = write_changed(destination, source.read_bytes()) or changed
    preview = (SOURCE / "linear-agent.md").read_text().replace(
        '  - {action: linear_*, resource: "*", effect: allow}',
        '  - {action: linear_list_*, resource: "*", effect: allow}\n'
        '  - {action: linear_get_*, resource: "*", effect: allow}\n'
        '  - {action: linear_extract_images, resource: "*", effect: allow}',
    ).replace('  - {action: hex_upload_clipboard, resource: "*", effect: allow}',
              '  - {action: hex_upload_clipboard, resource: "*", effect: deny}'
    ).replace('  - {action: hex_notify, resource: "*", effect: allow}',
              '  - {action: hex_notify, resource: "*", effect: deny}')
    changed = write_changed(workspace / ".opencode/agents/linear-issue-preview.md", preview.encode()) or changed
    return changed


def install_focus_extension(home):
    source = SOURCE / "gnome" / FOCUS_EXTENSION
    data = os.environ.get("XDG_DATA_HOME") if home == Path.home() else None
    target = Path(data or home / ".local/share") / "gnome-shell/extensions" / FOCUS_EXTENSION
    changed = False
    for name in ("metadata.json", "extension.js"):
        changed = write_changed(target / name, (source / name).read_bytes()) or changed
    return changed


def enable_focus_extension(changed):
    command = ["gsettings"]
    key = ["org.gnome.shell", "enabled-extensions"]
    try:
        result = subprocess.run([*command, "get", *key], check=True, capture_output=True, text=True, timeout=5)
        enabled = ast.literal_eval(result.stdout.strip().removeprefix("@as "))
        if FOCUS_EXTENSION not in enabled:
            subprocess.run([*command, "set", *key, repr([*enabled, FOCUS_EXTENSION])],
                           check=True, capture_output=True, timeout=5)
            changed = True
    except (OSError, ValueError, SyntaxError, subprocess.SubprocessError):
        print("Hex: enable Hex Typing Focus in GNOME Extensions after signing in again.", file=sys.stderr)
        return
    if changed:
        print("Hex: GNOME will load the installed Hex Typing Focus extension at the next sign-in.")


def activate_workers(home, changes, extension_changed):
    binary = shutil.which("systemctl")
    if not binary:
        return
    try:
        manager = subprocess.run(
            [binary, "--user", "show-environment"], capture_output=True, text=True, timeout=5,
        )
        # A test HOME or container must never alter the host user's services.
        environment = manager.stdout.splitlines()
        if manager.returncode or f"HOME={home}" not in environment:
            return
        subprocess.run([binary, "--user", "daemon-reload"], check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        print("Hex: use systemctl --user daemon-reload to load the installed workers.", file=sys.stderr)
        return
    gnome = "XDG_SESSION_TYPE=wayland" in environment and any(
        line.startswith("XDG_CURRENT_DESKTOP=") and "GNOME" in line.partition("=")[2].split(":")
        for line in environment
    )
    if gnome:
        enable_focus_extension(extension_changed)
    for unit, changed in changes.items():
        try:
            active = subprocess.run([binary, "--user", "is-active", "--quiet", unit],
                                    capture_output=True, timeout=5).returncode == 0
            subprocess.run(
                [binary, "--user", "enable", "--now", unit],
                check=True, capture_output=True, timeout=10,
            )
            if active and changed:
                subprocess.run([binary, "--user", "restart", unit], check=True, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            print(f"Hex: enable {unit} with systemctl --user enable --now {unit}", file=sys.stderr)


def install(home, microphone=None):
    binary = shutil.which("hex")
    if not binary:
        print("Hex: skipped (hex is not installed).")
        return
    config = home / ".config/hex"
    changed = False
    for name in ("contextual_rewrite.py", "vocabulary_review.py"):
        changed = write_changed(config / name, (SOURCE / name).read_bytes()) or changed
    template = (SOURCE / "hex.config.ts.in").read_text()
    rendered = template.replace("@REWRITE_HELPER@", json.dumps(str(config / "contextual_rewrite.py")))
    config_changed = write_changed(config / "hex.config.ts", rendered.encode())
    changed = config_changed or changed
    documentation = (SOURCE / "CONVERSATION_CONTEXT.md.in").read_text().replace("@CLUSTERFORK@", str(SOURCE.parent))
    write_changed(config / "CONVERSATION_CONTEXT.md", documentation.encode())
    changed = write_changed(
        home / ".config/systemd/user/hex-vocabulary-review.service",
        (SOURCE / "hex-vocabulary-review.service").read_bytes(),
    ) or changed
    linear_changed = install_linear_shortcut(home)
    extension_changed = install_focus_extension(home)
    if microphone:
        source = SOURCE / "microphones" / microphone
        for name, destination in (
            ("60-pcm2902-microphone.conf", home / ".config/wireplumber/wireplumber.conf.d/60-pcm2902-microphone.conf"),
            ("disable-laptop-capture.py", home / ".local/bin/disable-laptop-capture.py"),
            ("60-disable-laptop-capture.conf", home / ".config/systemd/user/wireplumber.service.d/60-disable-laptop-capture.conf"),
        ):
            write_changed(destination, (source / name).read_bytes())
        print("Hex: PCM2902 profile installed; audio rules apply at the next login or WirePlumber restart.")
    subprocess.run([binary, "commands", "check"], check=True, capture_output=True, text=True, timeout=10)
    # The CLI uses a running Hex. Never edit settings.json directly or launch a
    # desktop app as a side effect of installing dotfiles, including in tests.
    if not (home / ".local/share/hex/local-api.json").is_file():
        print("Hex: workspace installed. Start Hex and rerun the installer to apply rewriting and vocabulary.")
        return
    try:
        hex_command(binary, "status")
    except (OSError, subprocess.SubprocessError):
        print("Hex: workspace installed. Start Hex and rerun the installer to apply rewriting and vocabulary.")
        return
    apply_settings(binary, home, microphone)
    activate_workers(home, {"hex-vocabulary-review.service": changed, LINEAR_UNIT: linear_changed}, extension_changed)
    print("Hex: workspace installed; rewrite settings and seed vocabulary merged.")
    if config_changed:
        print("Hex: transformation configuration changed; click Allow in Hex's Output pane to enable its Python helper.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--microphone", choices=["pcm2902"], help="Install this machine's optional USB/laptop microphone rules")
    args = parser.parse_args()
    try:
        install(Path.home(), args.microphone)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        # Do not print command payloads (which contain the personal prompt).
        print(f"Hex installation failed ({type(error).__name__}). Start Hex and rerun hex/install.py.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
