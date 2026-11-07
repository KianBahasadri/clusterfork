#!/usr/bin/env python3
"""Rotate saved accounts for Claude, Codex, Cursor, Grok, OpenCode, and Antigravity.

Shell wrappers (`rotate-claude`, `rotate-codex`, …) call this script. Three
backends share one CLI: copy (Claude), shared-store symlink (Codex / Cursor /
Grok / OpenCode), and GNOME Keyring (Antigravity).
"""

from __future__ import annotations

import hashlib
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from account_sessions import account_identity, login_fingerprints

SUFFIX_RE = re.compile(r"^[A-Za-z0-9._-]+$")
PROFILE_PREFIX = "auth.json."
REMOTE_MANIFEST = "remote-profiles.json"
CLOUD_DIRECTORY = "cloud-reserved"
REMOTE_KEY_PREFIX = "remote:"
CLAUDE_ACTIVE = ".credentials.json"
CLAUDE_PREFIX = ".credentials.json."

KICKOFF_MESSAGE = "hi"
PING_TIMEOUT_SECONDS = 120
KICKOFF_PINGS = {
    # One-shot mode per agent: send one tiny message so the provider starts
    # its usage ticker for that account. Keyed by backend `command`.
    "rotate-claude": ["claude", "-p", KICKOFF_MESSAGE],
    "rotate-codex": [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "-m",
        "gpt-6-luna",
        KICKOFF_MESSAGE,
    ],
    "rotate-cursor-cli": ["cursor-agent", "-p", KICKOFF_MESSAGE],
    "rotate-grok": ["grok", "-p", KICKOFF_MESSAGE],
    "rotate-opencode": ["opencode", "run", KICKOFF_MESSAGE],
    "rotate-antigravity": ["agy", "--print", KICKOFF_MESSAGE],
}


class RotateError(Exception):
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        super().__init__("\n".join(lines))


def fail(*lines: str) -> None:
    raise RotateError(list(lines))


def _init_locale() -> None:
    try:
        locale.setlocale(locale.LC_COLLATE, "")
    except locale.Error:
        pass


_init_locale()


def sort_names(names: Iterable[str]) -> list[str]:
    """Match bash `sort` under the process locale."""
    items = list(names)
    try:
        return sorted(items, key=locale.strxfrm)
    except (TypeError, ValueError, OSError):
        return sorted(items)


def env_path(name: str, default: Path) -> Path:
    # Match bash `${VAR:-default}`: unset or empty uses the default.
    value = os.environ.get(name)
    if value:
        return Path(value)
    return default


def realpath_ms(path: Path) -> Path:
    """Match `realpath -ms`: absolute, collapse `.`/`..`, do not follow symlinks."""
    return Path(os.path.normpath(os.path.abspath(path)))


def atomic_replace(tmp: Path, dest: Path) -> None:
    try:
        os.replace(tmp, dest)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def atomic_copy(src: Path, dest: Path, mode: int = 0o600) -> None:
    tmp = dest.with_name(f".{dest.name}.tmp.{os.getpid()}")
    tmp.unlink(missing_ok=True)
    try:
        shutil.copyfile(src, tmp, follow_symlinks=True)
        os.chmod(tmp, mode)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    atomic_replace(tmp, dest)


def atomic_symlink(target: str, dest: Path) -> None:
    tmp = dest.with_name(f".{dest.name}.tmp.{os.getpid()}")
    tmp.unlink(missing_ok=True)
    try:
        tmp.symlink_to(target)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    atomic_replace(tmp, dest)


def atomic_write_text(dest: Path, text: str, mode: int = 0o600) -> None:
    tmp = dest.with_name(f".{dest.name}.tmp.{os.getpid()}")
    tmp.unlink(missing_ok=True)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.chmod(tmp, mode)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    atomic_replace(tmp, dest)


def atomic_write_bytes(dest: Path, data: bytes, mode: int = 0o600) -> None:
    tmp = dest.with_name(f".{dest.name}.tmp.{os.getpid()}")
    tmp.unlink(missing_ok=True)
    try:
        tmp.write_bytes(data)
        os.chmod(tmp, mode)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    atomic_replace(tmp, dest)


def remote_profile_key(name: str) -> str:
    return REMOTE_KEY_PREFIX + name


def valid_profile_key(name, record) -> bool:
    return bool(SUFFIX_RE.fullmatch(name) or (
        record.get("ownership", "cloud") == "cloud" and record.get("display_name")
        and name == remote_profile_key(record["display_name"])))


def legacy_archive(store_dir, name, record):
    alias = record.get("legacy_alias", name)
    if not SUFFIX_RE.fullmatch(alias):
        if name.startswith(REMOTE_KEY_PREFIX) and "legacy_alias" not in record:
            return None
        raise ValueError("Invalid legacy remote alias")
    return store_dir / CLOUD_DIRECTORY / f"{alias}.json"


def load_remote_profiles(store_dir: Path, *, migrate=True) -> dict:
    """Read ownership and archive legacy cloud copies without contacting the cloud.

    migrate=False is the consumer path: no recovery, chmod, unlink or writes.
    """
    manifest_path = store_dir / REMOTE_MANIFEST
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text())
    if not isinstance(manifest, dict):
        raise ValueError("Invalid remote account ownership")
    for name, record in manifest.items():
        if (not isinstance(record, dict) or not valid_profile_key(name, record)
                or record.get("ownership", "cloud") not in {"local", "cloud"}
                or not re.fullmatch(r"[0-9a-f]{64}", record.get("account_id", ""))):
            raise ValueError("Invalid remote account ownership")
        if record.get("ownership") == "local" and (
                not re.fullmatch(r"[0-9a-f]{64}", record.get("session_id", ""))
                or not isinstance(record.get("fingerprints"), list) or len(record["fingerprints"]) < 2
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in record["fingerprints"])):
            raise ValueError("Invalid independent local login registration")
        if "display_name" in record and (
                record.get("ownership", "cloud") != "cloud"
                or not isinstance(record["display_name"], str)
                or not SUFFIX_RE.fullmatch(record["display_name"])
                or record["display_name"] in {".", "..", "lock"}
                or len(record["display_name"]) > 120):
            raise ValueError("Invalid remote profile display name")
    if not migrate:
        return manifest
    changed = False
    for name, record in manifest.items():
        if not valid_profile_key(name, record):
            raise ValueError("Invalid remote account ownership")
        backup_name = record.get("local_backup") or record.get("backup_path")
        if not backup_name and record.get("state") != "staged":
            continue
        destination = store_dir / f"{PROFILE_PREFIX}{name}"
        source = destination
        if backup_name:
            source = Path(backup_name).expanduser()
            if not source.is_absolute():
                source = store_dir / source
            if not source.resolve().is_relative_to(store_dir.resolve()):
                raise ValueError("Legacy remote backup is outside its auth store")
        # A completed file restore can precede a failed manifest write. Prefer
        # that destination on retry, but never replace independently changed data.
        destination_present = destination.exists() or destination.is_symlink()
        expected = record.get("source_sha256")
        if destination_present:
            if destination.is_symlink() or not destination.is_file():
                raise ValueError("Legacy remote destination was replaced")
            data = destination.read_bytes()
            if expected is None:
                expected = hashlib.sha256(source.read_bytes()).hexdigest()
        else:
            data = source.read_bytes()
            if expected is None:
                expected = hashlib.sha256(data).hexdigest()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("Legacy remote credentials changed; refusing to overwrite them")
        if not destination_present:
            fd, temporary = tempfile.mkstemp(prefix=".remote-profile-", dir=store_dir)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                # Unlike replace(), link() cannot clobber a concurrent login.
                os.link(temporary, destination)
            finally:
                os.unlink(temporary)
        record["source_sha256"] = expected
        for key in ("backup_path", "local_backup", "staged_for_issue", "staged_for_issue_id"):
            record.pop(key, None)
        if record.get("state") == "staged":
            record.pop("state")
        changed = True
    if changed:
        atomic_write_text(manifest_path, json.dumps(manifest))
    # Old copies belong to the cloud session, including rotated copies of that
    # same chain. Never infer a local login from differing file contents.
    changed = False
    for name, record in manifest.items():
        if record.get("ownership") == "local":
            continue
        if record.get("reclaim"):
            continue
        destination = legacy_archive(store_dir, name, record)
        if destination is None:
            continue
        source = store_dir / f"{PROFILE_PREFIX}{name}"
        expected = record.get("source_sha256")
        if destination.parent.is_symlink() or destination.is_symlink() or source.is_symlink():
            raise ValueError("Legacy remote destination was replaced")
        if source.exists():
            data = source.read_bytes()
            if expected and hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Legacy remote credentials changed; refusing to overwrite them")
            auth = json.loads(data)
            provider = "cursor" if "accessToken" in auth else "codex"
            if account_identity(auth, provider) != record["account_id"]:
                raise ValueError("Legacy remote credentials belong to another account")
            expected = hashlib.sha256(data).hexdigest()
            destination.parent.mkdir(mode=0o700, exist_ok=True)
            if destination.exists():
                if destination.read_bytes() != data:
                    raise ValueError("Legacy remote archive was replaced")
            else:
                # Archive first. A failed manifest write or unlink is repeatable.
                os.link(source, destination)
            source.unlink()
        if destination.exists():
            data = destination.read_bytes()
            if expected and hashlib.sha256(data).hexdigest() != expected:
                raise ValueError("Legacy remote archive changed")
            record["source_sha256"] = hashlib.sha256(data).hexdigest()
            destination.chmod(0o600)
            auth = json.loads(data)
            provider = "cursor" if "accessToken" in auth else "codex"
            if account_identity(auth, provider) != record["account_id"]:
                raise ValueError("Legacy remote credentials belong to another account")
            fingerprints = sorted(set(record.get("fingerprints", [])) | set(login_fingerprints(auth, provider)))
            if fingerprints != record.get("fingerprints"):
                record["fingerprints"] = fingerprints
                changed = True
        if record.get("ownership") != "cloud":
            record["ownership"] = "cloud"
            changed = True
    if changed:
        atomic_write_text(manifest_path, json.dumps(manifest))
    return manifest


def normalize_profile_scopes(store_dir: Path) -> dict:
    """Give remote metadata its own namespace; local names never belong to it."""
    manifest = load_remote_profiles(store_dir)
    normalized = {}
    for alias, record in manifest.items():
        if record.get("ownership", "cloud") == "cloud":
            label = record.get("display_name", alias)
            if (not isinstance(label, str) or not SUFFIX_RE.fullmatch(label)
                    or label in {".", "..", "lock"} or len(label) > 120):
                raise ValueError("Invalid remote profile display name")
            key = remote_profile_key(label)
            value = {**record, "display_name": label}
            archive = legacy_archive(store_dir, alias, record)
            if archive is not None and (archive.exists() or record.get("source_sha256")):
                value["legacy_alias"] = record.get("legacy_alias", alias)
        else:
            key, value = alias, record
        if key in normalized:
            raise ValueError("Conflicting remote profile names")
        normalized[key] = value
    if normalized != manifest:
        atomic_write_text(store_dir / REMOTE_MANIFEST, json.dumps(normalized))
    return normalized


def cloud_enrolled(record):
    return record.get("ownership", "cloud") == "cloud" or record.get("cloud_enrolled", True)


def profile_is_local(store_dir, name, manifest, provider):
    """Availability is independent of selection; unregistered account aliases fail closed."""
    record = manifest.get(name, {})
    if record and record.get("ownership", "cloud") != "local":
        return False
    path = store_dir / f"{PROFILE_PREFIX}{name}"
    if path.is_symlink() or not path.is_file():
        return False
    try:
        auth = json.loads(path.read_text())
        identity = account_identity(auth, provider)
    except (ValueError, TypeError, OSError):
        return not record and not manifest
    enrolled = {value["account_id"] for value in manifest.values() if cloud_enrolled(value)}
    if identity not in enrolled:
        return not record or record.get("ownership") == "local" and record["account_id"] == identity
    if record.get("ownership") != "local" or record["account_id"] != identity:
        return False
    cloud_fingerprints = {fingerprint for value in manifest.values()
                          if value["account_id"] == identity and value.get("ownership", "cloud") == "cloud"
                          for fingerprint in value.get("fingerprints", [])}
    return not cloud_fingerprints.intersection(login_fingerprints(auth, provider))


def prefixed_paths(directory: Path, prefix: str) -> list[Path]:
    if not directory.is_dir():
        return []
    found: list[Path] = []
    for path in directory.iterdir():
        if not path.name.startswith(prefix):
            continue
        suffix = path.name[len(prefix) :]
        if not suffix:
            continue
        if path.exists() or path.is_symlink():
            found.append(path)
    return found


def suffixes_from(directory: Path, prefix: str) -> list[str]:
    return sort_names(path.name[len(prefix) :] for path in prefixed_paths(directory, prefix))


def format_suffixes(label: str, suffixes: list[str]) -> str | None:
    if not suffixes:
        return None
    return f"  {label}: {' '.join(sort_names(set(suffixes)))}"


def choose_by_usage(backend, names: list[str]) -> str:
    if not names:
        fail(f"{backend.command}: no saved profiles",
             f"  Save the active account first: {backend.command} --save NAME")
    from rotate_usage import UsageError, choose_profile
    try:
        return choose_profile(backend, names)
    except UsageError as error:
        fail(f"{backend.command}: {error}",
             f"  Select a profile explicitly: {backend.command} NAME")


def ping_agent(command_key: str) -> tuple[bool, str]:
    """Run the one-shot ping for an agent backend. Returns (ok, detail)."""
    argv = KICKOFF_PINGS.get(command_key)
    if argv is None:
        return False, f"no ping command registered for {command_key}"
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=PING_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return False, f"{argv[0]} not found on PATH"
    except subprocess.TimeoutExpired:
        return False, f"timed out after {PING_TIMEOUT_SECONDS}s"
    if proc.returncode == 0:
        return True, "ok"
    output = (proc.stderr or "").strip() or (proc.stdout or "").strip()
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    detail = lines[-1] if lines else ""
    return False, (f"exit {proc.returncode}: {detail}"[:160] if detail else f"exit {proc.returncode}")


def validate_suffix(command: str, suffix: str) -> None:
    if not SUFFIX_RE.fullmatch(suffix):
        fail(
            f"{command}: profile names may only contain letters, numbers, "
            "dots, underscores, and dashes"
        )


def refuse_active_delete(command: str, name: str) -> None:
    fail(
        f"{command}: cannot delete active profile: {name}",
        f"  Switch to another profile or detach first: {command} --unhook",
    )


def confirm_delete(command: str, name: str, *, kind: str = "saved profile") -> bool:
    try:
        answer = input(f"{command}: delete {kind} {name!r}? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        answer = ""
    if answer.strip().lower() in ("y", "yes"):
        return True
    print(f"{command}: deletion cancelled")
    return False


def is_interactive() -> bool:
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
        import termios
        termios.tcgetattr(sys.stdin.fileno())
        return True
    except (OSError, AttributeError, ValueError):
        return False


def _read_menu_action(fd):
    """Read one menu key: up, down, enter, cancel, space, all, or None."""
    import select

    ch = os.read(fd, 1)
    if not ch:
        raise EOFError
    if ch == b"\x1b":
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            return "cancel"
        follow = os.read(fd, 1)
        if follow not in (b"[", b"O"):
            return None
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            return None
        arrow = os.read(fd, 1)
        if arrow == b"A":
            return "up"
        if arrow == b"B":
            return "down"
        return None
    if ch in (b"\r", b"\n"):
        return "enter"
    if ch in (b"\x03", b"\x04", b"q", b"Q"):
        return "cancel"
    if ch in (b"k", b"K"):
        return "up"
    if ch in (b"j", b"J"):
        return "down"
    if ch == b" ":
        return "space"
    if ch in (b"a", b"A"):
        return "all"
    return None


def _run_menu(header: str, render_rows, on_action) -> int:
    """Draw a menu until ``on_action`` finishes. Returns the row count still on screen."""
    import termios
    import tty

    rows = render_rows()
    fd = sys.stdin.fileno()
    sys.stdout.write(header)
    for line in rows:
        sys.stdout.write(f"{line}\n")
    sys.stdout.flush()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()
        while True:
            action = _read_menu_action(fd)
            if on_action(action):
                break
            rows = render_rows()
            sys.stdout.write(f"\x1b[{len(rows)}A")
            for line in rows:
                sys.stdout.write(f"\r\x1b[K{line}\n")
            sys.stdout.flush()
    finally:
        try:
            sys.stdout.write("\x1b[?25h")
            sys.stdout.flush()
        except OSError:
            pass
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except (termios.error, OSError):
            pass
    return len(rows)


def _finish_menu(row_count: int, confirmation: str) -> None:
    sys.stdout.write(f"\x1b[{row_count + 1}A\r\x1b[J{confirmation}")
    sys.stdout.flush()


def prompt_choice(header: str, options: list[str], confirmation: str) -> str:
    """Arrow-key menu. ``confirmation`` is a format string with ``{choice}``."""
    if not options:
        raise ValueError("Menu options are required")
    selected = 0

    def render_rows():
        return [f"{' > ' if i == selected else '   '}{opt}" for i, opt in enumerate(options)]

    def on_action(action):
        nonlocal selected
        if action == "up":
            selected = max(0, selected - 1)
        elif action == "down":
            selected = min(len(options) - 1, selected + 1)
        elif action == "enter":
            return True
        elif action == "cancel":
            raise KeyboardInterrupt
        return False

    row_count = _run_menu(header, render_rows, on_action)
    choice = options[selected]
    _finish_menu(row_count, confirmation.format(choice=choice))
    return choice


def prompt_profile_selection(command: str, names: list[str]) -> list[str]:
    """Checkbox list. Space toggles, ``a`` selects all, Enter migrates the checks."""
    if not names:
        return []
    checked: set[str] = set()
    cursor = 0
    rows = ["all", *names]

    def all_checked() -> bool:
        return checked == set(names)

    def render_rows():
        lines = []
        for index, name in enumerate(rows):
            mark = "x" if (index == 0 and all_checked()) or (index > 0 and name in checked) else " "
            pointer = ">" if index == cursor else " "
            lines.append(f" {pointer} [{mark}] {name}")
        return lines

    def toggle_all():
        if all_checked():
            checked.clear()
        else:
            checked.update(names)

    def on_action(action):
        nonlocal cursor
        if action == "up":
            cursor = max(0, cursor - 1)
        elif action == "down":
            cursor = min(len(rows) - 1, cursor + 1)
        elif action == "space":
            if cursor == 0:
                toggle_all()
            else:
                name = rows[cursor]
                checked.symmetric_difference_update({name})
        elif action == "all":
            toggle_all()
        elif action == "enter":
            return True
        elif action == "cancel":
            raise KeyboardInterrupt
        return False

    row_count = _run_menu(
        f"{command}: select profiles to migrate (space toggles, a selects all, Enter migrates):\n",
        render_rows,
        on_action,
    )
    selected = [name for name in names if name in checked]
    if selected:
        shown = "all profiles" if len(selected) == len(names) else ", ".join(selected)
        _finish_menu(row_count, f"{command}: migrating {shown}\n")
    else:
        _finish_menu(row_count, "")
    return selected


def prompt_scope(command: str, name: str) -> str:
    """Prompt user to choose between 'local' and 'remote' scope using arrow keys."""
    return prompt_choice(
        f"{command}: select scope for {name!r} (use arrow keys, Enter to confirm):\n",
        ["local", "remote"],
        f"{command}: selected scope '{{choice}}' for {name!r}\n",
    )


def prompt_migration(command: str) -> str:
    """Prompt for the single-login migration direction. Returns the action name."""
    choice = prompt_choice(
        f"{command}: select migration direction (use arrow keys, Enter to confirm):\n",
        ["local to remote", "remote to local"],
        f"{command}: selected migration '{{choice}}'\n",
    )
    return {
        "local to remote": "migrate-from-local-to-remote",
        "remote to local": "migrate-from-remote-to-local",
    }[choice]


def print_profile_list(command: str, suffixes: list[str], current: str | None) -> int:
    if not suffixes:
        print(f"{command}: no saved profiles")
        return 0
    print(f"{command}: saved profiles")
    for name in suffixes:
        mark = "*" if name == current else " "
        print(f"  {mark} {name}")
    return 0


def print_scoped_profile_list(
    command: str,
    rows: list[tuple[str, bool, bool, bool]],
) -> int:
    """Print local/remote profiles with flush name and tag columns.

    Each row is ``(name, is_current, has_local, has_remote)``.
    Tag columns stay aligned across mixed local-only and remote-only rows.
    """
    if not rows:
        print(f"{command}: no saved profiles")
        return 0
    name_width = max(len(name) for name, *_ in rows)
    local_width = len("[local]")
    print(f"{command}: saved profiles")
    for name, is_current, has_local, has_remote in rows:
        mark = "*" if is_current else " "
        local_tag = "[local]" if has_local else " " * local_width
        remote_tag = "[remote]" if has_remote else ""
        line = f"  {mark} {name:<{name_width}}  {local_tag} {remote_tag}"
        print(line.rstrip())
    return 0


def usage(command: str) -> list[str]:
    lines = [
        f"{command}: usage:",
        f"  {command} [name]",
        f"  {command} --save name",
        f"  {command} --delete name",
        f"  {command} --unhook",
        f"  {command} --list",
        f"  {command} --kickoff [names]",
        "  Without a name, select an account using Symphony's quota policy.",
    ]
    if command in {"rotate-codex", "rotate-cursor-cli"}:
        lines.extend([
            f"  {command} [--local | --remote] --save name",
            f"  {command} --local --list",
            f"  {command} --remote --list",
            f"  {command} --remote --delete name",
            f"  {command} --migrate",
            f"  {command} --migrate-from-local-to-remote",
            f"  {command} --migrate-from-remote-to-local",
            "  --save selects local or remote scope interactively, or via --local/--remote.",
            "  --migrate prompts for a direction, then which profiles to move.",
            "  --migrate-from-local-to-remote moves existing single-login profiles to remote.",
            "  --migrate-from-remote-to-local moves this host's remote-only profiles to local.",
        ])
    return lines


def parse_scope(command: str, args: list[str]) -> tuple[str | None, list[str]]:
    scopes = [value for value in args if value in {"--local", "--remote"}]
    if len(scopes) > 1:
        fail(*usage(command))
    if scopes and command not in {"rotate-codex", "rotate-cursor-cli"}:
        fail(f"{command}: local/remote account management is supported for Codex and Cursor")
    return (scopes[0][2:] if scopes else None,
            [value for value in args if value not in {"--local", "--remote"}])


def parse_action(command: str, args: list[str]) -> tuple[str, str | list[str] | None]:
    if not args:
        return "rotate", None
    first = args[0]
    if first in ("-h", "--help"):
        if len(args) != 1:
            fail(*usage(command))
        return "help", None
    if first == "--list":
        if len(args) != 1:
            fail(*usage(command))
        return "list", None
    if first == "--unhook":
        if len(args) != 1:
            fail(*usage(command))
        return "unhook", None
    migration = {
        "--migrate": "migrate",
        "--migrate-from-local-to-remote": "migrate-from-local-to-remote",
        "--migrate-from-remote-to-local": "migrate-from-remote-to-local",
    }.get(first)
    if migration:
        if len(args) != 1:
            fail(*usage(command))
        return migration, None
    if first == "--kickoff":
        profiles = args[1:]
        if any(n.startswith("-") or not n for n in profiles):
            fail(*usage(command))
        if len(profiles) != len(set(profiles)):
            fail(*usage(command))
        if profiles:
            for name in profiles:
                validate_suffix(command, name)
        return "kickoff", profiles or None  # type: ignore[return-value]
    if first == "--save":
        if len(args) != 2:
            fail(*usage(command))
        return first[2:], args[1]
    if first == "--delete":
        if len(args) != 2:
            fail(*usage(command))
        validate_suffix(command, args[1])
        return "delete", args[1]
    if first.startswith("--"):
        fail(*usage(command))
    if len(args) != 1:
        fail(*usage(command))
    # Old bash treated `rotate-claude ""` as a bare rotate (`${1:-}`).
    if not first:
        return "rotate", None
    return "select", first


def resolve_link(path: Path) -> Path:
    target = os.readlink(path)
    if target.startswith("/"):
        return Path(target)
    return path.parent / target


def readlink_text(path: Path) -> str:
    return os.readlink(path)


class ClaudeBackend:
    command = "rotate-claude"

    def __init__(self) -> None:
        self.claude_dir = env_path("ROTATE_CLAUDE_DIR", Path.home() / ".claude")
        self.active = self.claude_dir / CLAUDE_ACTIVE

    def suffixes(self) -> list[str]:
        return suffixes_from(self.claude_dir, CLAUDE_PREFIX)

    def current_suffix(self) -> str | None:
        token = self._read_token(self.active)
        if not token:
            return None
        for name in self.suffixes():
            path = self.claude_dir / f"{CLAUDE_PREFIX}{name}"
            if not path.is_file():
                continue
            if self._read_token(path) == token:
                return name
        return None

    def _read_token(self, path: Path) -> str | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        oauth = data.get("claudeAiOauth")
        if not isinstance(oauth, dict):
            return None
        token = oauth.get("accessToken")
        if isinstance(token, str) and token:
            return token
        return None

    def list_profiles(self) -> int:
        return print_profile_list(self.command, self.suffixes(), self.current_suffix())

    def select(self, requested: str | None) -> int:
        if not self.claude_dir.is_dir():
            fail(f"{self.command}: missing Claude auth directory: {self.claude_dir}")

        names = self.suffixes()
        next_suffix = self._choose(names, requested)
        source = self.claude_dir / f"{CLAUDE_PREFIX}{next_suffix}"
        atomic_copy(source, self.active)
        print(f"{self.command}: selected {CLAUDE_PREFIX}{next_suffix}")
        print(f"  Claude: {self.active} is now a copy of {CLAUDE_PREFIX}{next_suffix}")
        return 0

    def save(self, name: str) -> int:
        validate_suffix(self.command, name)
        if not self.claude_dir.is_dir():
            fail(f"{self.command}: missing Claude auth directory: {self.claude_dir}")
        if not self.active.is_file():
            fail(f"{self.command}: missing active credentials: {self.active}")

        dest = self.claude_dir / f"{CLAUDE_PREFIX}{name}"
        if dest.is_dir():
            fail(f"{self.command}: {dest} exists and is a directory")
        atomic_copy(self.active, dest)
        print(f"{self.command}: saved active credentials as {name}")
        print(f"  Claude: {dest} is now a copy of {self.active}")
        return 0

    def delete(self, name: str) -> int:
        validate_suffix(self.command, name)
        self._choose(self.suffixes(), name)
        profile = self.claude_dir / f"{CLAUDE_PREFIX}{name}"
        token = self._read_token(profile)
        linked = self.active.is_symlink() and (
            realpath_ms(resolve_link(self.active)) == realpath_ms(profile)
        )
        if linked or (token is not None and self._read_token(self.active) == token):
            refuse_active_delete(self.command, name)
        if not confirm_delete(self.command, name):
            return 1
        profile.unlink()
        print(f"{self.command}: deleted profile {name}")
        return 0

    def unhook(self) -> int:
        if not self.active.exists() and not self.active.is_symlink():
            print(f"{self.command}: already unhooked (no active credentials)")
            return 0
        if self.active.is_dir() and not self.active.is_symlink():
            fail(f"{self.command}: {self.active} exists and is a directory")
        if not self.active.is_symlink() and not self.current_suffix():
            fail(
                f"{self.command}: active credentials are not saved as a profile",
                f"  Save them first: {self.command} --save NAME",
            )
        self.active.unlink()
        print(f"{self.command}: unhooked active credentials")
        print(f"  Claude: removed {self.active}")
        print(f"  Log in, then: {self.command} --save NAME")
        return 0

    def kickoff(self, selected: list[str] | None = None) -> int:  # noqa: C901
        names = self.suffixes()
        if not names:
            fail(
                f"{self.command}: no saved profiles",
                f"  Save the active account first: {self.command} --save NAME",
            )
        if selected is not None:
            missing = [n for n in selected if n not in set(names)]
            if missing:
                extra = format_suffixes("suffixes", names)
                lines = [f"{self.command}: no matching {CLAUDE_PREFIX}{missing[0]} file"]
                if extra:
                    lines.append(extra)
                fail(*lines)
            # Preserve the on-disk sorted order for determinism.
            names = [n for n in names if n in set(selected)]

        active_bytes: bytes | None = None
        if self.active.is_file():
            active_bytes = self.active.read_bytes()

        print(f"{self.command}: kickoff — pinging each saved profile with {KICKOFF_MESSAGE!r}")
        failures = 0
        for name in names:
            atomic_copy(self.claude_dir / f"{CLAUDE_PREFIX}{name}", self.active)
            ok, detail = ping_agent(self.command)
            mark = "ok" if ok else "FAIL"
            print(f"  {name}: {mark}" + (f" ({detail})" if not ok else ""))
            if not ok:
                failures += 1

        if active_bytes is not None:
            atomic_write_bytes(self.active, active_bytes)
        return 1 if failures else 0

    def _choose(self, names: list[str], requested: str | None) -> str:
        if requested is not None:
            if requested not in names:
                extra = format_suffixes("suffixes", names)
                lines = [f"{self.command}: no matching {CLAUDE_PREFIX}{requested} file"]
                if extra:
                    lines.append(extra)
                fail(*lines)
            return requested
        return choose_by_usage(self, names)


class SharedStoreBackend:
    def __init__(
        self,
        *,
        command: str,
        label: str,
        agent_dir_env: str,
        store_dir_env: str,
        default_agent: Path,
        default_store: Path,
    ) -> None:
        home = Path.home()
        self.command = command
        self.label = label
        self.agent_dir = env_path(agent_dir_env, home / default_agent)
        self.store_dir = env_path(store_dir_env, home / default_store)
        self.auth = self.agent_dir / "auth.json"
        self.current = self.store_dir / "current"

    def suffixes(self) -> list[str]:
        return suffixes_from(self.store_dir, PROFILE_PREFIX)

    def remote_profiles(self) -> set[str]:
        if self.command not in {"rotate-codex", "rotate-cursor-cli"}:
            return set()
        try:
            manifest = normalize_profile_scopes(self.store_dir)
            if not manifest:
                return set()
            return {name for name, record in manifest.items() if record.get("ownership", "cloud") == "cloud"}
        except (OSError, ValueError, TypeError):
            fail(f"{self.command}: cannot read remote account ownership; refusing local rotation")

    def local_profiles(self, requested: str | None = None) -> list[str]:
        remote = self.remote_profiles()
        names = self.suffixes()
        if self.command in {"rotate-codex", "rotate-cursor-cli"}:
            manifest = load_remote_profiles(self.store_dir)
            provider = "cursor" if self.command == "rotate-cursor-cli" else "codex"
            eligible = ([name for name in names if profile_is_local(self.store_dir, name, manifest, provider)]
                        if manifest else names)
        else:
            eligible = names
        if requested in remote or requested in names and requested not in eligible:
            fail(f"{self.command}: credentials belong to a remote login",
                 f"  Sign in separately, then: {self.command} --local --save NAME")
        return eligible

    def current_suffix(self) -> str | None:
        if not self.current.is_symlink():
            return None
        base = Path(readlink_text(self.current)).name
        if not base.startswith(PROFILE_PREFIX):
            return None
        suffix = base[len(PROFILE_PREFIX) :]
        return suffix or None

    def list_profiles(self) -> int:
        remote = self.remote_profiles()
        current = self.current_suffix() if self.current.is_symlink() else None
        if remote:
            manifest = load_remote_profiles(self.store_dir)
            remote_names = {
                manifest.get(k, {}).get("display_name", k.removeprefix("remote:"))
                for k in remote
            }
            local_names = set(self.suffixes())
            rows = [
                (
                    name,
                    name in local_names and name == current,
                    name in local_names,
                    name in remote_names,
                )
                for name in sort_names(local_names | remote_names)
            ]
            return print_scoped_profile_list(self.command, rows)
        return print_profile_list(self.command, self.suffixes(), current)

    def select(self, requested: str | None) -> int:
        if self.auth.exists() and not self.auth.is_symlink():
            fail(
                f"{self.command}: {self.auth} exists but is not a symlink",
                f"  Save it as a named profile first: {self.command} --save NAME",
            )
        if not self.store_dir.is_dir():
            fail(
                f"{self.command}: missing shared auth directory: {self.store_dir}",
                "  Run install-clusterfork.sh to migrate existing profiles.",
            )
        names = self.local_profiles(requested)
        next_suffix = self._choose(names, requested)
        atomic_symlink(f"{PROFILE_PREFIX}{next_suffix}", self.current)
        if not self._auth_links_to_current():
            self._restore_auth_link()
        print(f"{self.command}: selected {PROFILE_PREFIX}{next_suffix}")
        print(
            f"  {self.label}: {self.auth} -> {readlink_text(self.auth)} -> "
            f"{readlink_text(self.current)}"
        )
        return 0

    def delete(self, name: str) -> int:
        validate_suffix(self.command, name)
        self.local_profiles(name)
        self._choose(self.suffixes(), name)
        profile = self.store_dir / f"{PROFILE_PREFIX}{name}"
        selected = self.current.is_symlink() and (
            realpath_ms(resolve_link(self.current)) == realpath_ms(profile)
        )
        directly_linked = self.auth.is_symlink() and (
            realpath_ms(resolve_link(self.auth)) == realpath_ms(profile)
        )
        if directly_linked or (selected and self._auth_links_to_current()):
            refuse_active_delete(self.command, name)
        if not confirm_delete(self.command, name):
            return 1
        profile.unlink()
        if self.command in {"rotate-codex", "rotate-cursor-cli"}:
            manifest = load_remote_profiles(self.store_dir)
            if manifest.get(name, {}).get("ownership") == "local":
                del manifest[name]
                atomic_write_text(self.store_dir / REMOTE_MANIFEST, json.dumps(manifest))
        if selected:
            self.current.unlink()
        print(f"{self.command}: deleted profile {name}")
        return 0

    def save(self, name: str) -> int:
        validate_suffix(self.command, name)
        self.local_profiles(name)
        if not self.auth.is_file():
            fail(f"{self.command}: missing active credentials: {self.auth}")

        local_record = None
        if self.command in {"rotate-codex", "rotate-cursor-cli"}:
            manifest = load_remote_profiles(self.store_dir)
            if manifest:
                provider = "cursor" if self.command == "rotate-cursor-cli" else "codex"
                try:
                    auth = json.loads(self.auth.read_text())
                    identity = account_identity(auth, provider)
                    fingerprints = login_fingerprints(auth, provider)
                except (OSError, ValueError, TypeError):
                    fail(f"{self.command}: cannot establish active login ownership")
                managed = any(record["account_id"] == identity and cloud_enrolled(record) for record in manifest.values())
                source = next((record for alias, record in manifest.items()
                                   if record.get("ownership") == "local" and record["account_id"] == identity
                                   and (self.auth.resolve() == (self.store_dir / f"{PROFILE_PREFIX}{alias}").resolve()
                                        or set(fingerprints).intersection(record.get("fingerprints", [])))
                                   and profile_is_local(self.store_dir, alias, manifest, provider)), None)
                if manifest.get(name, {}).get("ownership") == "local" and source is None:
                    fail(f"{self.command}: refusing to overwrite an independent login")
                if managed or source:
                    if source is None:
                        fail(f"{self.command}: current credentials are not a saved local login",
                             f"  Sign in separately, then: {self.command} --local --save NAME")
                    existing = manifest.get(name)
                    if existing and existing.get("session_id") != source["session_id"]:
                        fail(f"{self.command}: refusing to overwrite an independent login")
                    local_record = {**source, "fingerprints": sorted(set(source.get("fingerprints", [])) | set(fingerprints))}

        self.store_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.store_dir, 0o700)
        except OSError:
            pass

        dest = self.store_dir / f"{PROFILE_PREFIX}{name}"
        if dest.is_dir():
            fail(f"{self.command}: {dest} exists and is a directory")
        atomic_copy(self.auth, dest)
        if local_record:
            manifest[name] = local_record
            atomic_write_text(self.store_dir / REMOTE_MANIFEST, json.dumps(manifest))

        atomic_symlink(f"{PROFILE_PREFIX}{name}", self.current)
        if not self._auth_links_to_current():
            self._restore_auth_link()

        print(f"{self.command}: saved active credentials as {name}")
        print(
            f"  {self.label}: {self.auth} -> {readlink_text(self.auth)} -> "
            f"{readlink_text(self.current)}"
        )
        return 0

    def kickoff(self, selected: list[str] | None = None) -> int:
        if self.auth.exists() and not self.auth.is_symlink():
            fail(
                f"{self.command}: {self.auth} exists but is not a symlink",
                f"  Save it as a named profile first: {self.command} --save NAME",
            )
        if not self.store_dir.is_dir():
            fail(
                f"{self.command}: missing shared auth directory: {self.store_dir}",
                "  Run install-clusterfork.sh to migrate existing profiles.",
            )
        names = self.local_profiles()
        if not names:
            fail(
                f"{self.command}: no saved profiles",
                f"  Save the active account first: {self.command} --save NAME",
            )
        if selected is not None:
            for name in selected:
                self.local_profiles(name)
            missing = [n for n in selected if n not in set(names)]
            if missing:
                extra = format_suffixes("suffixes", names)
                lines = [f"{self.command}: no matching {PROFILE_PREFIX}{missing[0]} file"]
                if extra:
                    lines.append(extra)
                fail(*lines)
            names = [n for n in names if n in set(selected)]

        orig_current = readlink_text(self.current) if self.current.is_symlink() else None
        orig_auth_hooked = self.auth.is_symlink()

        print(f"{self.command}: kickoff — pinging each saved profile with {KICKOFF_MESSAGE!r}")
        failures = 0
        for name in names:
            atomic_symlink(f"{PROFILE_PREFIX}{name}", self.current)
            if not self._auth_links_to_current():
                self._restore_auth_link()
            ok, detail = ping_agent(self.command)
            mark = "ok" if ok else "FAIL"
            print(f"  {name}: {mark}" + (f" ({detail})" if not ok else ""))
            if not ok:
                failures += 1

        if orig_current is not None:
            atomic_symlink(orig_current, self.current)
        else:
            print(
                f"{self.command}: warning: store had no previous current link; "
                f"left it at {readlink_text(self.current)}",
                file=sys.stderr,
            )
        if not orig_auth_hooked and self.auth.is_symlink():
            self.auth.unlink()
        return 1 if failures else 0

    def unhook(self) -> int:
        if self.auth.exists() and not self.auth.is_symlink():
            fail(
                f"{self.command}: {self.auth} exists but is not a symlink",
                f"  Save it as a named profile first: {self.command} --save NAME",
            )
        if not self.auth.is_symlink():
            print(f"{self.command}: already unhooked (no active credentials)")
            return 0
        self.auth.unlink()
        print(f"{self.command}: unhooked active credentials")
        print(f"  {self.label}: removed {self.auth}")
        print(f"  Log in, then: {self.command} --save NAME")
        return 0

    def _choose(self, names: list[str], requested: str | None) -> str:
        if requested is not None:
            if requested not in names:
                if self.command in {"rotate-codex", "rotate-cursor-cli"} and any(
                        record.get("ownership", "cloud") == "cloud" and record.get("display_name", alias) == requested
                        for alias, record in load_remote_profiles(self.store_dir).items()):
                    fail(f"{self.command}: no local profile named {requested!r}",
                         f"  Sign in, then: {self.command} --local --save {requested}")
                extra = format_suffixes("suffixes", names)
                lines = [f"{self.command}: no matching {PROFILE_PREFIX}{requested} file"]
                if extra:
                    lines.append(extra)
                fail(*lines)
            return requested
        return choose_by_usage(self, names)

    def _restore_auth_link(self) -> None:
        relative = os.path.relpath(
            str(realpath_ms(self.current)),
            start=str(realpath_ms(self.agent_dir)),
        )
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        atomic_symlink(relative, self.auth)

    def _auth_links_to_current(self) -> bool:
        if not self.auth.is_symlink() or not self.current.is_symlink():
            return False
        return realpath_ms(resolve_link(self.auth)) == realpath_ms(self.current)


class AntigravityBackend:
    command = "rotate-antigravity"
    active_service = "gemini"
    active_user = "antigravity"
    profile_service = "rotate-antigravity"

    def __init__(self) -> None:
        default = Path.home() / ".gemini" / "antigravity-cli" / "rotate-auth"
        self.state_dir = env_path("ROTATE_ANTIGRAVITY_STATE_DIR", default)
        self.profiles_file = self.state_dir / "profiles"
        self.current_file = self.state_dir / "current"

    def suffixes(self) -> list[str]:
        if not self.profiles_file.is_file():
            return []
        names: set[str] = set()
        for line in self.profiles_file.read_text(encoding="utf-8").splitlines():
            # Match the old bash reader: do not strip; invalid lines are skipped.
            if line and SUFFIX_RE.fullmatch(line):
                names.add(line)
        return sort_names(names)

    def current_suffix(self) -> str | None:
        if not self.current_file.is_file():
            return None
        raw = self.current_file.read_text(encoding="utf-8").splitlines()
        name = raw[0] if raw else ""
        if SUFFIX_RE.fullmatch(name):
            return name
        return None

    def list_profiles(self) -> int:
        return print_profile_list(self.command, self.suffixes(), self.current_suffix())

    def select(self, requested: str | None) -> int:
        if requested is not None:
            validate_suffix(self.command, requested)
        self._require_secret_tool()
        self._prepare_state()
        names = self.suffixes()
        lookup = set(names)
        extra = format_suffixes("profiles", names)

        if requested is not None:
            if requested not in lookup:
                lines = [f"{self.command}: no matching profile: {requested}"]
                if extra:
                    lines.append(extra)
                fail(*lines)
            next_suffix = requested
        else:
            next_suffix = None

        current = self.current_suffix()
        if requested is None:
            if current not in lookup and self._lookup(self.active_service, self.active_user) is not None:
                fail(
                    f"{self.command}: active keyring item is not saved as a profile",
                    f"  Save the active account first: {self.command} --save NAME",
                )
            next_suffix = choose_by_usage(self, names)
            if current not in lookup:
                current = None
        elif current and current not in lookup:
            print(
                f"{self.command}: warning: current profile marker is unknown; "
                "active item was not backed up before switching",
                file=sys.stderr,
            )
            current = None

        assert next_suffix is not None
        if current:
            self._save_active_to_profile(current, required=False)
        self._install_profile(next_suffix)
        self._write_current(next_suffix)
        print(f"{self.command}: selected profile {next_suffix}")
        print(f"  Active: service={self.active_service} username={self.active_user}")
        return 0

    def kickoff(self, selected: list[str] | None = None) -> int:
        self._require_secret_tool()
        self._prepare_state()
        names = self.suffixes()
        if selected is not None:
            missing = [n for n in selected if n not in set(names)]
            if missing:
                extra = format_suffixes("profiles", names)
                lines = [f"{self.command}: no matching profile: {missing[0]}"]
                if extra:
                    lines.append(extra)
                fail(*lines)
            names = [n for n in names if n in set(selected)]
        if not names:
            fail(
                f"{self.command}: no saved profiles",
                f"  Save the active account first: {self.command} --save NAME",
            )
        current = self.current_suffix()
        if current and current not in set(names):
            print(
                f"{self.command}: warning: current profile marker is unknown; "
                "active keyring item will not be restored afterwards",
                file=sys.stderr,
            )
            current = None

        was_unhooked = False
        if current is None:
            if self._lookup(self.active_service, self.active_user) is not None:
                fail(
                    f"{self.command}: active keyring item is not saved as a profile",
                    f"  Save it first: {self.command} --save NAME",
                )
            was_unhooked = True

        print(f"{self.command}: kickoff — pinging each saved profile with {KICKOFF_MESSAGE!r}")
        failures = 0
        for name in names:
            self._install_profile(name)
            ok, detail = ping_agent(self.command)
            mark = "ok" if ok else "FAIL"
            print(f"  {name}: {mark}" + (f" ({detail})" if not ok else ""))
            if not ok:
                failures += 1

        if current is not None:
            self._install_profile(current)
            self._write_current(current)
        elif was_unhooked:
            self._clear(self.active_service, self.active_user)
        return 1 if failures else 0

    def save(self, name: str) -> int:
        validate_suffix(self.command, name)
        self._require_secret_tool()
        self._prepare_state()
        self._save_active_to_profile(name, required=True)
        self._add_profile(name)
        self._write_current(name)
        print(f"{self.command}: saved active keyring item as {name}")
        print(f"  Active: service={self.active_service} username={self.active_user}")
        print(f"  Profile: service={self.profile_service} username={name}")
        return 0

    def delete(self, name: str) -> int:
        validate_suffix(self.command, name)
        self._require_secret_tool()
        names = self.suffixes()
        if name not in names:
            lines = [f"{self.command}: no matching profile: {name}"]
            extra = format_suffixes("profiles", names)
            if extra:
                lines.append(extra)
            fail(*lines)
        selected = self.current_suffix() == name
        if selected and self._lookup(self.active_service, self.active_user) is not None:
            refuse_active_delete(self.command, name)
        if not confirm_delete(self.command, name):
            return 1
        # Lookup unlocks a saved item if needed. Always check clear's result:
        # lookup also returns None when the keyring is unavailable.
        self._lookup(self.profile_service, name)
        self._clear(self.profile_service, name)
        atomic_write_text(
            self.profiles_file, "".join(f"{item}\n" for item in names if item != name)
        )
        if selected:
            self.current_file.unlink()
        print(f"{self.command}: deleted profile {name}")
        return 0

    def unhook(self) -> int:
        self._require_secret_tool()
        secret = self._lookup(self.active_service, self.active_user)
        if secret is None:
            print(f"{self.command}: already unhooked (no active keyring item)")
            return 0
        current = self.current_suffix()
        if not current or current not in set(self.suffixes()):
            fail(
                f"{self.command}: active keyring item is not saved as a profile",
                f"  Save it first: {self.command} --save NAME",
            )
        self._prepare_state()
        self._save_active_to_profile(current, required=True)
        self._clear(self.active_service, self.active_user)
        print(f"{self.command}: unhooked active credentials")
        print(f"  Cleared: service={self.active_service} username={self.active_user}")
        print(f"  Log in, then: {self.command} --save NAME")
        return 0

    def _require_secret_tool(self) -> None:
        if shutil.which("secret-tool") is None:
            fail(f"{self.command}: secret-tool is required")

    def _prepare_state(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.state_dir, 0o700)
        except OSError:
            pass
        if not self.profiles_file.exists():
            self.profiles_file.write_text("", encoding="utf-8")
            try:
                os.chmod(self.profiles_file, 0o600)
            except OSError:
                pass

    def _add_profile(self, name: str) -> None:
        names = set(self.suffixes())
        names.add(name)
        atomic_write_text(
            self.profiles_file, "".join(f"{item}\n" for item in sort_names(names))
        )

    def _write_current(self, name: str) -> None:
        atomic_write_text(self.current_file, f"{name}\n")

    def _lookup(self, service: str, username: str) -> bytes | None:
        result = subprocess.run(
            ["secret-tool", "lookup", "service", service, "username", username],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout

    def _store(self, label: str, service: str, username: str, secret: bytes) -> None:
        result = subprocess.run(
            [
                "secret-tool",
                "store",
                f"--label={label}",
                "service",
                service,
                "username",
                username,
            ],
            input=secret,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace").strip()
            fail(f"{self.command}: secret-tool store failed" + (f": {err}" if err else ""))

    def _clear(self, service: str, username: str) -> None:
        result = subprocess.run(
            ["secret-tool", "clear", "service", service, "username", username],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace").strip()
            fail(f"{self.command}: secret-tool clear failed" + (f": {err}" if err else ""))

    def _save_active_to_profile(self, name: str, *, required: bool) -> None:
        secret = self._lookup(self.active_service, self.active_user)
        if secret is None:
            if required:
                fail(
                    f"{self.command}: missing active keyring item: "
                    f"service={self.active_service} username={self.active_user}"
                )
            print(
                f"{self.command}: warning: active keyring item was not backed up before switching",
                file=sys.stderr,
            )
            return
        self._store(
            f"antigravity on gemini ({name})",
            self.profile_service,
            name,
            secret,
        )

    def _install_profile(self, name: str) -> None:
        secret = self._lookup(self.profile_service, name)
        if secret is None:
            fail(
                f"{self.command}: missing saved keyring item: "
                f"service={self.profile_service} username={name}"
            )
        self._store("antigravity on gemini", self.active_service, self.active_user, secret)


def make_backend(agent: str) -> ClaudeBackend | SharedStoreBackend | AntigravityBackend:
    shared = {
        "codex": dict(
            command="rotate-codex",
            label="Codex",
            agent_dir_env="ROTATE_CODEX_CODEX_DIR",
            store_dir_env="ROTATE_CODEX_AUTH_STORE_DIR",
            default_agent=Path(".codex"),
            default_store=Path(".local/share/clusterfork-auth/codex"),
        ),
        "cursor": dict(
            command="rotate-cursor-cli",
            label="Cursor",
            agent_dir_env="ROTATE_CURSOR_DIR",
            store_dir_env="ROTATE_CURSOR_AUTH_STORE_DIR",
            default_agent=Path(".config/cursor"),
            default_store=Path(".local/share/clusterfork-auth/cursor"),
        ),
        "grok": dict(
            command="rotate-grok",
            label="Grok",
            agent_dir_env="ROTATE_GROK_DIR",
            store_dir_env="ROTATE_GROK_AUTH_STORE_DIR",
            default_agent=Path(".grok"),
            default_store=Path(".local/share/clusterfork-auth/grok"),
        ),
        "opencode": dict(
            command="rotate-opencode",
            label="OpenCode",
            agent_dir_env="ROTATE_OPENCODE_DIR",
            store_dir_env="ROTATE_OPENCODE_AUTH_STORE_DIR",
            default_agent=Path(".local/share/opencode"),
            default_store=Path(".local/share/clusterfork-auth/opencode"),
        ),
    }
    if agent == "claude":
        return ClaudeBackend()
    if agent == "antigravity":
        return AntigravityBackend()
    if agent in shared:
        return SharedStoreBackend(**shared[agent])
    fail(
        f"rotate-auth: unknown agent: {agent}",
        "  agents: claude, codex, cursor, grok, opencode, antigravity",
    )
    raise AssertionError


def run(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(
            "usage: rotate_auth.py <claude|codex|cursor|grok|opencode|antigravity> "
            "[name | --save name | --delete name | --unhook | --list | --kickoff [names]]",
            file=sys.stderr,
        )
        return 0 if argv else 1

    backend = make_backend(argv[0])
    scope, args = parse_scope(backend.command, argv[1:])
    action, name = parse_action(backend.command, args)
    if action == "help":
        print("\n".join(usage(backend.command)))
        return 0
    if action in {"migrate", "migrate-from-local-to-remote", "migrate-from-remote-to-local"}:
        if backend.command not in {"rotate-codex", "rotate-cursor-cli"}:
            fail(f"{backend.command}: local/remote account management is supported for Codex and Cursor")
        if scope is not None:
            fail(*usage(backend.command))
        direction = action
        if action == "migrate":
            if not is_interactive():
                fail(*usage(backend.command))
            try:
                direction = prompt_migration(backend.command)
            except (KeyboardInterrupt, EOFError):
                fail(f"{backend.command}: migration cancelled")
        from rotate_accounts import manage
        return manage(backend, "local", direction, None, choose=action == "migrate")
    if (scope == "remote" or action in {"save", "delete"}
            and backend.command in {"rotate-codex", "rotate-cursor-cli"}):
        if scope == "remote" and action not in {"save", "list", "delete"}:
            fail(f"{backend.command}: use --remote with --save, --list or --delete",
                 "  Remote jobs select their own credentials.")
        if action == "save" and scope is None:
            if not is_interactive():
                fail(*usage(backend.command))
            try:
                assert isinstance(name, str)
                scope = prompt_scope(backend.command, name)
            except (KeyboardInterrupt, EOFError):
                fail(f"{backend.command}: save cancelled")
        from rotate_accounts import manage
        return manage(backend, scope or "local", action, name)
    if scope == "local" and action == "list":
        assert isinstance(backend, SharedStoreBackend)
        return print_profile_list(backend.command, backend.local_profiles(), backend.current_suffix())
    if action == "list":
        return backend.list_profiles()
    if action == "unhook":
        return backend.unhook()
    if action == "kickoff":
        selected = name if isinstance(name, list) else None
        return backend.kickoff(selected)
    if action == "save":
        assert name is not None and isinstance(name, str)
        return backend.save(name)
    if action == "delete":
        assert name is not None and isinstance(name, str)
        return backend.delete(name)
    assert name is None or isinstance(name, str)
    return backend.select(name)


def main() -> int:
    try:
        return run(sys.argv[1:])
    except RotateError as exc:
        print("\n".join(exc.lines), file=sys.stderr)
        return 1
    except (OSError, UnicodeError) as exc:
        print(f"rotate-auth: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # The scoped helper imports this module for the shared ownership rules and
    # error type. Reuse the running CLI instead of loading a second copy.
    sys.modules["rotate_auth"] = sys.modules[__name__]
    sys.exit(main())
