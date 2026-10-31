"""Render shipped MCP templates using the installed Clusterfork secrets file."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def load_dotenv(path: Path) -> dict[str, str]:
    """Read plain assignments without evaluating shell code or using ambient keys."""
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def json_template(path: Path, values: dict[str, str], *, expand: bool = False):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not values.get("LINEAR_API_KEY", "").strip():
        # Omit the optional header entirely so each client can still use OAuth.
        for root in ("mcpServers", "mcp"):
            server = data.get(root, {}).get("linear", {})
            headers = server.get("headers", {})
            if headers.get("Authorization") in (
                "Bearer ${LINEAR_API_KEY}", "Bearer {env:LINEAR_API_KEY}",
            ):
                del headers["Authorization"]
                if not headers:
                    del server["headers"]

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise SystemExit(f"mcp config: ${{{key}}} not set in installed .env")
        return values[key]

    def walk(obj):
        if isinstance(obj, dict):
            return {key: walk(value) for key, value in obj.items()}
        if isinstance(obj, list):
            return [walk(value) for value in obj]
        if isinstance(obj, str):
            return re.sub(r"\$\{([A-Z_][A-Z0-9_]*)\}", replace, obj)
        return obj

    return walk(data) if expand else data


def toml_template(path: Path, values: dict[str, str]) -> str:
    text = path.read_text(encoding="utf-8")
    if values.get("LINEAR_API_KEY", "").strip():
        return text

    # Preserve TOML formatting and all other settings, including Grok's theme
    # and the Codex tables subsequently used by the installer's checked merge.
    optional = {
        "mcp_servers.linear": 'bearer_token_env_var = "LINEAR_API_KEY"',
        "mcp_servers.linear.headers": 'Authorization = "Bearer ${LINEAR_API_KEY}"',
    }
    section = ""
    lines = []
    for line in text.splitlines(keepends=True):
        match = re.fullmatch(r"\s*\[([^\[\]]+)\]\s*(?:#.*)?", line.strip())
        if match:
            section = match.group(1).strip()
        if line.strip() != optional.get(section):
            lines.append(line)
    return "".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("dotenv", type=Path)
    parser.add_argument("--expand", action="store_true")
    args = parser.parse_args()
    values = load_dotenv(args.dotenv)
    if args.source.suffix == ".toml":
        rendered = toml_template(args.source, values)
    else:
        rendered = json.dumps(json_template(args.source, values, expand=args.expand), indent=2) + "\n"
    if args.expand:
        # Expanded client configs contain secrets, just like the installed .env.
        args.destination.touch(mode=0o600, exist_ok=True)
        args.destination.chmod(0o600)
    args.destination.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
