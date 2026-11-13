"""Codex command-hook trust hash (matches codex-rs hooks discovery).

Codex records trust in ~/.codex/config.toml as:

    [hooks.state."/home/you/.codex/config.toml:stop:0:0"]
    trusted_hash = "sha256:..."

The hash is SHA-256 of canonical JSON of the normalized hook identity, not
the raw TOML text. Timeout defaults to 600s when omitted, matching Codex's
command-hook normalizer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: canonical_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [canonical_json(item) for item in value]
    return value


def version_for_identity(identity: dict[str, Any]) -> str:
    serialized = json.dumps(
        canonical_json(identity), separators=(",", ":"), ensure_ascii=False
    ).encode()
    return "sha256:" + hashlib.sha256(serialized).hexdigest()


def command_trust_hash(
    event_name: str,
    command: str,
    *,
    runs_async: bool = True,
    timeout_sec: int = 600,
    matcher: str | None = None,
) -> str:
    timeout_sec = max(int(timeout_sec), 1)
    identity = {
        "event_name": event_name,
        "hooks": [
            {
                "async": bool(runs_async),
                "command": command,
                "timeout": timeout_sec,
                "type": "command",
            }
        ],
    }
    if matcher is not None:
        identity["matcher"] = matcher
    return version_for_identity(identity)


def hook_state_key(config_path: str | Path, event_name: str) -> str:
    return f"{config_path}:{event_name}:0:0"


def trust_hash_for_handler(
    event_name: str, handler: dict[str, Any], *, matcher: str | None = None
) -> str:
    timeout = handler.get("timeout")
    if timeout is None:
        timeout = 600
    return command_trust_hash(
        event_name,
        handler["command"],
        runs_async=bool(handler.get("async", False)),
        timeout_sec=timeout,
        matcher=matcher,
    )


def toml_basic_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def format_hooks_state_toml(state: dict[str, dict[str, Any]]) -> str:
    chunks: list[str] = []
    for key in sorted(state):
        entry = state[key]
        lines = [f"[hooks.state.{toml_basic_string(key)}]"]
        if entry.get("enabled") is not None:
            lines.append(f"enabled = {'true' if entry['enabled'] else 'false'}")
        trusted = entry.get("trusted_hash")
        if trusted:
            lines.append(f"trusted_hash = {toml_basic_string(str(trusted))}")
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks) + ("\n" if chunks else "")
