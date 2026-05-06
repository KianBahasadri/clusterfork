#!/usr/bin/env python3
"""Convert an OpenCode auth.json file to Codex auth.json format."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert OpenCode auth.json format into Codex auth.json format."
    )
    parser.add_argument("input", type=Path, help="Path to OpenCode auth.json input")
    return parser.parse_args()


def require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"missing or invalid string field: {field}")
    return value


def convert(opencode_auth: dict[str, Any]) -> dict[str, Any]:
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


def main() -> int:
    args = parse_args()

    try:
        with args.input.open("r", encoding="utf-8") as file:
            opencode_auth = json.load(file)
        if not isinstance(opencode_auth, dict):
            raise ValueError("input JSON root must be an object")

        codex_auth = convert(opencode_auth)
        json.dump(codex_auth, sys.stdout, indent=2)
        sys.stdout.write("\n")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
