"""Literal local configuration and canonical controller registry paths."""

import os
from pathlib import Path
import re
import shlex

ROOT=Path(__file__).resolve().parents[2]
REGISTRY=ROOT/"automation/controller/projects.json"


CONFIG_NAMES = ("LINEAR_API_KEY", "LINEAR_OAUTH_CLIENT_ID", "LINEAR_OAUTH_CLIENT_SECRET",
                "LINEAR_WEBHOOK_SECRET", "GITHUB_APP_ID",
                "GITHUB_APP_PRIVATE_KEY_FILE", "COMMAND_CODE_API_KEY")


def settings(path):
    """Read only these literal dotenv entries. Never source a user's shell file."""
    values = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
            if match and match[1] in CONFIG_NAMES:
                parts = shlex.split(match[2], comments=True)
                if len(parts) > 1:
                    raise ValueError(f"quote the value of {match[1]} in {path}")
                values[match[1]] = parts[0] if parts else ""
    for name in CONFIG_NAMES:
        if name in os.environ:
            values[name] = os.environ[name]
    return values


def private_key_path(values):
    value = values.get("GITHUB_APP_PRIVATE_KEY_FILE")
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def check(values):
    missing = [name for name in ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY_FILE")
               if not values.get(name)]
    if not (values.get("LINEAR_API_KEY") or (values.get("LINEAR_OAUTH_CLIENT_ID")
                                            and values.get("LINEAR_OAUTH_CLIENT_SECRET"))):
        missing.insert(0, "LINEAR_API_KEY or (LINEAR_OAUTH_CLIENT_ID and LINEAR_OAUTH_CLIENT_SECRET)")
    key = private_key_path(values)
    if key and not key.is_file():
        missing.append("GITHUB_APP_PRIVATE_KEY_FILE (file not found)")
    if values.get("GITHUB_APP_ID") and not values["GITHUB_APP_ID"].isdigit():
        missing.append("GITHUB_APP_ID (use the numeric App ID)")
    return missing
