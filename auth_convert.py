#!/usr/bin/env python3
"""Convert auth.json files between OpenCode and Codex formats."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert auth.json files between OpenCode and Codex formats."
    )
    parser.add_argument(
        "--from",
        dest="input_format",
        choices=("opencode", "codex", "auto"),
        required=True,
        help="Input format. Use 'auto' to detect OpenCode or Codex from the JSON shape.",
    )
    parser.add_argument("input", type=Path, help="Path to auth.json input")
    return parser.parse_args()


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing or invalid string field: {field}")
    return value


def detect_format(auth: dict[str, Any]) -> str:
    openai = auth.get("openai")
    if isinstance(openai, dict) and all(
        isinstance(openai.get(field), str)
        for field in ("access", "refresh", "accountId")
    ):
        return "opencode"

    tokens = auth.get("tokens")
    if auth.get("auth_mode") == "chatgpt" and isinstance(tokens, dict) and all(
        isinstance(tokens.get(field), str)
        for field in ("access_token", "refresh_token", "account_id")
    ):
        return "codex"

    raise ValueError("could not detect input format; pass --from opencode or --from codex")


def opencode_to_codex(opencode_auth: dict[str, Any]) -> dict[str, Any]:
    openai = opencode_auth.get("openai")
    if not isinstance(openai, dict):
        raise ValueError("missing or invalid object field: openai")

    access = require_string(openai.get("access"), "openai.access")
    refresh = require_string(openai.get("refresh"), "openai.refresh")
    account_id = require_string(openai.get("accountId"), "openai.accountId")

    return {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            # Codex expects an id_token-shaped JWT here. OpenCode does not store a
            # separate id token, but Codex accepts the OpenCode access JWT.
            "id_token": access,
            "access_token": access,
            "refresh_token": refresh,
            "account_id": account_id,
        },
        "last_refresh": datetime.now(timezone.utc).isoformat(),
    }


def codex_to_opencode(codex_auth: dict[str, Any]) -> dict[str, Any]:
    tokens = codex_auth.get("tokens")
    if not isinstance(tokens, dict):
        raise ValueError("missing or invalid object field: tokens")

    access = require_string(tokens.get("access_token"), "tokens.access_token")
    refresh = require_string(tokens.get("refresh_token"), "tokens.refresh_token")
    account_id = require_string(tokens.get("account_id"), "tokens.account_id")

    return {
        "openai": {
            "access": access,
            "refresh": refresh,
            "accountId": account_id,
        }
    }


def convert(auth: dict[str, Any], input_format: str) -> dict[str, Any]:
    if input_format == "auto":
        input_format = detect_format(auth)

    if input_format == "opencode":
        return opencode_to_codex(auth)
    if input_format == "codex":
        return codex_to_opencode(auth)

    raise ValueError(f"unsupported input format: {input_format}")


def main() -> int:
    args = parse_args()

    try:
        with args.input.open("r", encoding="utf-8") as file:
            auth = json.load(file)
        if not isinstance(auth, dict):
            raise ValueError("input JSON root must be an object")

        converted_auth = convert(auth, args.input_format)
        json.dump(converted_auth, sys.stdout, indent=2)
        sys.stdout.write("\n")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
