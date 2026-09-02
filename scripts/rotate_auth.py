#!/usr/bin/env python3
"""Rotate saved accounts for Claude, Codex, Cursor, Grok, OpenCode, and Antigravity.

Shell wrappers (`rotate-claude`, `rotate-codex`, …) call this script. Three
backends share one CLI: copy (Claude), shared-store symlink (Codex / Cursor /
Grok / OpenCode), and GNOME Keyring (Antigravity).
"""

from __future__ import annotations

import json
import locale
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

SUFFIX_RE = re.compile(r"^[A-Za-z0-9._-]+$")
PROFILE_PREFIX = "auth.json."
CLAUDE_ACTIVE = ".credentials.json"
CLAUDE_PREFIX = ".credentials.json."

KICKOFF_MESSAGE = "hi"
PING_TIMEOUT_SECONDS = 120
KICKOFF_PINGS = {
    # One-shot mode per agent: send one tiny message so the provider starts
    # its usage ticker for that account. Keyed by backend `command`.
    "rotate-claude": ["claude", "-p", KICKOFF_MESSAGE],
    "rotate-codex": ["codex", "exec", "--skip-git-repo-check", KICKOFF_MESSAGE],
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


def next_in_order(suffixes: list[str], current: str | None) -> str:
    if current and current in suffixes:
        return suffixes[(suffixes.index(current) + 1) % len(suffixes)]
    return suffixes[0]


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


def print_profile_list(command: str, suffixes: list[str], current: str | None) -> int:
    if not suffixes:
        print(f"{command}: no saved profiles")
        return 0
    print(f"{command}: saved profiles")
    for name in suffixes:
        mark = "*" if name == current else " "
        print(f"  {mark} {name}")
    return 0


def usage(command: str) -> list[str]:
    return [
        f"{command}: usage:",
        f"  {command} [name]",
        f"  {command} --save name",
        f"  {command} --unhook",
        f"  {command} --list",
        f"  {command} --kickoff [names]",
    ]


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
        return "save", args[1]
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
        if len(names) < 2:
            extra = format_suffixes("suffixes", names)
            lines = [f"{self.command}: need at least two {CLAUDE_PREFIX}* files"]
            if extra:
                lines.append(extra)
            lines.append(f"  Save the active account first: {self.command} --save NAME")
            fail(*lines)
        return next_in_order(names, self.current_suffix())


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

    def current_suffix(self) -> str | None:
        if not self.current.is_symlink():
            return None
        base = Path(readlink_text(self.current)).name
        if not base.startswith(PROFILE_PREFIX):
            return None
        suffix = base[len(PROFILE_PREFIX) :]
        return suffix or None

    def list_profiles(self) -> int:
        current = self.current_suffix() if self.current.is_symlink() else None
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
        names = self.suffixes()
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

    def save(self, name: str) -> int:
        validate_suffix(self.command, name)
        if not self.auth.is_file():
            fail(f"{self.command}: missing active credentials: {self.auth}")

        self.store_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.store_dir, 0o700)
        except OSError:
            pass

        dest = self.store_dir / f"{PROFILE_PREFIX}{name}"
        if dest.is_dir():
            fail(f"{self.command}: {dest} exists and is a directory")
        atomic_copy(self.auth, dest)

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
                extra = format_suffixes("suffixes", names)
                lines = [f"{self.command}: no matching {PROFILE_PREFIX}{requested} file"]
                if extra:
                    lines.append(extra)
                fail(*lines)
            return requested
        if len(names) < 2:
            extra = format_suffixes("suffixes", names)
            lines = [f"{self.command}: need at least two {PROFILE_PREFIX}* files"]
            if extra:
                lines.append(extra)
            lines.append(f"  Save the active account first: {self.command} --save NAME")
            fail(*lines)
        return next_in_order(names, self.current_suffix())

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
        elif len(names) < 2:
            lines = [f"{self.command}: need at least two saved profiles"]
            if extra:
                lines.append(extra)
            lines.append(f"  Save the active account first: {self.command} --save NAME")
            fail(*lines)
        else:
            next_suffix = None

        current = self.current_suffix()
        if requested is None:
            if not current or current not in lookup:
                fail(
                    f"{self.command}: no current profile marker",
                    f"  Save the active account first: {self.command} --save NAME",
                )
            next_suffix = next_in_order(names, current)
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
            "[name | --save name | --unhook | --list | --kickoff [names]]",
            file=sys.stderr,
        )
        return 0 if argv else 1

    backend = make_backend(argv[0])
    action, name = parse_action(backend.command, argv[1:])
    if action == "help":
        print("\n".join(usage(backend.command)))
        return 0
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
    sys.exit(main())
