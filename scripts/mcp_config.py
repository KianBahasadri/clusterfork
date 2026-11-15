"""Render every agent's MCP config from the shared roster.

`agents/mcp-servers.json` is the only server list. `apply` writes one agent's
native file. Secret values come from the installed dotenv: expanded clients
receive the value, and other clients receive that client's reference syntax.
An optional bearer is omitted when its dotenv value is missing or blank.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import textwrap
from pathlib import Path


EXPAND_AGENTS = {"cursor", "command-code", "antigravity"}
TEMPLATE_AGENTS = {"opencode", "qwen", "grok", "codex", "claude-settings"}
PRIVATE_AGENTS = EXPAND_AGENTS
AGENTS = (
    "cursor",
    "command-code",
    "antigravity",
    "opencode",
    "qwen",
    "grok",
    "codex",
    "claude-settings",
    "claude-user",
    "claude-plugins",
)
SERVER_KEYS = {
    "name", "enabled", "url", "command", "args", "headers", "bearer", "claudeMarketplace",
}
HEADER_KEYS = {"name", "env", "value", "agents"}
BEARER_KEYS = {"env", "optional"}


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


def _fail(message: str) -> None:
    raise SystemExit(f"mcp config: {message}")


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.isidentifier():
        _fail(f"{label} must be an environment variable name")
    return value


def load_roster(path: Path) -> list[dict]:
    """Return normalized servers in roster order."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"{path} is not valid JSON: {exc}")
    if not isinstance(data, dict) or set(data) != {"servers"} or not isinstance(data["servers"], list):
        _fail(f"{path} must be an object with a servers array")
    servers = []
    names = set()
    for raw in data["servers"]:
        if not isinstance(raw, dict) or set(raw) - SERVER_KEYS:
            _fail(f"server has unknown or missing fields: {raw!r}")
        name = raw.get("name")
        if not isinstance(name, str) or not name or "/" in name or name in names:
            _fail(f"invalid server name: {name!r}")
        names.add(name)
        if not isinstance(raw.get("enabled"), bool):
            _fail(f"{name}: enabled must be a boolean")
        has_url = "url" in raw
        has_command = "command" in raw
        if has_url == has_command:
            _fail(f"{name}: set url or command, not both")
        if has_url and not isinstance(raw["url"], str):
            _fail(f"{name}: url must be a string")
        args = raw.get("args", [])
        if has_command:
            if not isinstance(raw["command"], str) or not raw["command"]:
                _fail(f"{name}: command must be a string")
            if "args" not in raw:
                _fail(f"{name}: command requires args")
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            _fail(f"{name}: args must be an array of strings")
        headers = []
        for header in raw.get("headers", []):
            if not isinstance(header, dict) or set(header) - HEADER_KEYS or "name" not in header:
                _fail(f"{name}: invalid header")
            if ("env" in header) == ("value" in header):
                _fail(f"{name}: header {header.get('name')!r} needs env or value")
            agents = header.get("agents")
            if agents is not None and (
                not isinstance(agents, list) or not all(isinstance(item, str) for item in agents)
            ):
                _fail(f"{name}: header agents must be an array of strings")
            item = {"name": header["name"], "agents": agents}
            if "env" in header:
                item["env"] = _identifier(header["env"], f"{name} header env")
            else:
                if not isinstance(header["value"], str):
                    _fail(f"{name}: header value must be a string")
                item["value"] = header["value"]
            headers.append(item)
        bearer = raw.get("bearer")
        if bearer is not None:
            if not isinstance(bearer, dict) or set(bearer) - BEARER_KEYS or "env" not in bearer:
                _fail(f"{name}: invalid bearer")
            if "optional" in bearer and not isinstance(bearer["optional"], bool):
                _fail(f"{name}: bearer optional must be a boolean")
            bearer = {
                "env": _identifier(bearer["env"], f"{name} bearer env"),
                "optional": bool(bearer.get("optional", False)),
            }
        marketplace = raw.get("claudeMarketplace")
        if marketplace is not None and not isinstance(marketplace, str):
            _fail(f"{name}: claudeMarketplace must be a string")
        servers.append({
            "name": name,
            "enabled": raw["enabled"],
            "url": raw.get("url"),
            "command": raw.get("command"),
            "args": args,
            "headers": headers,
            "bearer": bearer,
            "claudeMarketplace": marketplace,
        })
    return servers


def _present(values: dict[str, str], env_name: str) -> bool:
    return bool(values.get(env_name, "").strip())


def _reference(agent: str, env_name: str) -> str:
    if agent == "opencode":
        return "{env:" + env_name + "}"
    if agent == "codex":
        return env_name
    return "${" + env_name + "}"


def _secret(agent: str, values: dict[str, str], env_name: str, *, optional: bool) -> str | None:
    """Return the text to embed, or None when an optional secret is absent."""
    if not _present(values, env_name):
        if optional:
            return None
        if agent in EXPAND_AGENTS:
            _fail(f"${{{env_name}}} not set in installed .env")
        return _reference(agent, env_name)
    if agent in EXPAND_AGENTS:
        return values[env_name]
    return _reference(agent, env_name)


def _header_visible(header: dict, agent: str) -> bool:
    agents = header["agents"]
    return agents is None or agent in agents


def _headers(agent: str, server: dict, values: dict[str, str]) -> list[tuple[str, str]]:
    items = []
    for header in server["headers"]:
        if not _header_visible(header, agent) or agent == "codex":
            continue
        if "env" in header:
            items.append((header["name"], _secret(agent, values, header["env"], optional=False)))
        else:
            items.append((header["name"], header["value"]))
    bearer = server["bearer"]
    if bearer and agent != "codex":
        token = _secret(agent, values, bearer["env"], optional=bearer["optional"])
        if token is not None:
            items.append(("Authorization", "Bearer " + token))
    return items


def _stdio(server: dict, *, command_list: bool = False) -> dict:
    if command_list:
        return {"command": [server["command"], *server["args"]]}
    return {"command": server["command"], "args": server["args"]}


def _with_headers(entry: dict, headers: list[tuple[str, str]]) -> dict:
    if headers:
        entry["headers"] = dict(headers)
    return entry


def _json_entry(agent: str, server: dict, values: dict[str, str]) -> dict:
    headers = _headers(agent, server, values)
    remote = server["url"] is not None
    if agent == "cursor":
        entry = {"url": server["url"]} if remote else _stdio(server)
        return _with_headers(entry, headers)
    if agent == "command-code":
        entry = {"transport": "http" if remote else "stdio", "enabled": server["enabled"]}
        if remote:
            entry["url"] = server["url"]
        else:
            entry.update(_stdio(server))
        return _with_headers(entry, headers)
    if agent == "antigravity":
        entry = {"serverUrl": server["url"]} if remote else _stdio(server)
        entry = _with_headers(entry, headers)
        if not server["enabled"]:
            entry["disabled"] = True
        return entry
    if agent == "opencode":
        if remote:
            entry = {"type": "remote", "url": server["url"]}
        else:
            entry = {"type": "local", **_stdio(server, command_list=True)}
        entry = _with_headers(entry, headers)
        entry["enabled"] = server["enabled"]
        return entry
    if agent == "qwen":
        entry = {"httpUrl": server["url"]} if remote else _stdio(server)
        return _with_headers(entry, headers)
    if agent in ("claude-user", "claude-plugins"):
        if remote:
            entry = {"type": "http", "url": server["url"]}
        else:
            entry = _stdio(server)
            if agent == "claude-plugins":
                entry = {"type": "stdio", **entry}
        return _with_headers(entry, headers)
    raise SystemExit(f"mcp config: {agent} has no JSON server entry")


def _toml_quote(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(item) for item in value) + "]"
    if isinstance(value, str):
        return json.dumps(value)
    raise SystemExit(f"mcp config: cannot encode TOML value {value!r}")


def render_toml(agent: str, roster: list[dict], values: dict[str, str]) -> str:
    """Return the mcp_servers tables for Codex or Grok."""
    blocks = []
    for server in roster:
        lines = [f"[mcp_servers.{server['name']}]"]
        if server["url"] is not None:
            lines.append(f"url = {_toml_quote(server['url'])}")
        else:
            lines.append(f"command = {_toml_quote(server['command'])}")
            lines.append(f"args = {_toml_quote(server['args'])}")
        if agent == "codex" and server["bearer"]:
            token = _secret(agent, values, server["bearer"]["env"], optional=server["bearer"]["optional"])
            if token is not None:
                lines.append(f"bearer_token_env_var = {_toml_quote(server['bearer']['env'])}")
        lines.append(f"enabled = {_toml_quote(server['enabled'])}")
        blocks.append("\n".join(lines))
        if agent == "codex":
            env_headers = [
                (header["name"], header["env"])
                for header in server["headers"]
                if "env" in header and _header_visible(header, agent)
            ]
            static_headers = [
                (header["name"], header["value"])
                for header in server["headers"]
                if "value" in header and _header_visible(header, agent)
            ]
            if env_headers:
                rows = [f"[mcp_servers.{server['name']}.env_http_headers]"]
                rows.extend(f"{name} = {_toml_quote(env_name)}" for name, env_name in env_headers)
                blocks.append("\n".join(rows))
            if static_headers:
                rows = [f"[mcp_servers.{server['name']}.headers]"]
                rows.extend(f"{name} = {_toml_quote(value)}" for name, value in static_headers)
                blocks.append("\n".join(rows))
        else:
            headers = _headers(agent, server, values)
            if headers:
                rows = [f"[mcp_servers.{server['name']}.headers]"]
                rows.extend(f"{name} = {_toml_quote(value)}" for name, value in headers)
                blocks.append("\n".join(rows))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _table_name(line: str) -> str | None:
    text = line.strip()
    if not text.startswith("["):
        return None
    text = text.lstrip("[").strip()
    if text.startswith("["):
        text = text[1:].strip()
    end = text.find("]")
    if end == -1:
        return None
    return text[:end].strip()


def strip_toml_root(text: str, root: str) -> str:
    """Drop tables under root, keeping comments that precede the next kept table."""
    out = []
    pending = []
    dropping = False
    for line in text.splitlines(keepends=True):
        name = _table_name(line)
        if name is not None:
            dropping = name == root or name.startswith(root + ".")
            if dropping:
                pending = []
                continue
            out.extend(pending)
            pending = []
            out.append(line)
            continue
        if dropping:
            if not line.strip() or line.lstrip().startswith("#"):
                pending.append(line)
            else:
                pending = []
            continue
        out.append(line)
    out.extend(pending)
    return "".join(out)


def inject_toml(template: str, block: str) -> str:
    base = strip_toml_root(template, "mcp_servers").rstrip() + "\n"
    if not block.strip():
        return base
    return base + "\n" + block.rstrip() + "\n"


def render_codex_source(template: str, roster: list[dict], values: dict[str, str]) -> str:
    return inject_toml(template, render_toml("codex", roster, values))


def _claude_plugins(roster: list[dict]) -> dict[str, bool]:
    plugins = {}
    for server in roster:
        if server["claudeMarketplace"]:
            plugins[server["claudeMarketplace"]] = server["enabled"]
    for server in roster:
        if server["claudeMarketplace"] or server["enabled"]:
            continue
        plugins[f"{server['name']}@skills-dir"] = False
    return plugins


def _dump(data: object) -> str:
    return json.dumps(data, indent=2) + "\n"


def render_json(agent: str, roster: list[dict], values: dict[str, str], template: str | None = None) -> str:
    if agent == "cursor":
        servers = {
            server["name"]: _json_entry(agent, server, values)
            for server in roster if server["enabled"]
        }
        return _dump({"mcpServers": servers})
    if agent in ("command-code", "antigravity"):
        servers = {
            server["name"]: _json_entry(agent, server, values) for server in roster
        }
        return _dump({"mcpServers": servers})
    if template is None:
        _fail(f"{agent} requires a template")
    try:
        data = json.loads(template)
    except json.JSONDecodeError as exc:
        _fail(f"template is not valid JSON: {exc}")
    if not isinstance(data, dict):
        _fail("template must be a JSON object")
    if agent == "opencode":
        data["mcp"] = {
            server["name"]: _json_entry(agent, server, values) for server in roster
        }
    elif agent == "qwen":
        data["mcp"] = {"excluded": [server["name"] for server in roster if not server["enabled"]]}
        data["mcpServers"] = {
            server["name"]: _json_entry(agent, server, values) for server in roster
        }
    elif agent == "claude-settings":
        existing = data.get("enabledPlugins") or {}
        if not isinstance(existing, dict):
            _fail("enabledPlugins must be an object")
        generated = _claude_plugins(roster)
        managed = set(generated)
        managed.update(f"{server['name']}@skills-dir" for server in roster)
        managed.update(
            server["claudeMarketplace"] for server in roster if server["claudeMarketplace"]
        )
        data["enabledPlugins"] = {
            **{key: value for key, value in existing.items() if key not in managed},
            **generated,
        }
    else:
        _fail(f"{agent} is not a JSON template agent")
    return _dump(data)


def render_claude_user(existing_text: str | None, roster: list[dict], values: dict[str, str]) -> str:
    if existing_text:
        try:
            data = json.loads(existing_text)
        except json.JSONDecodeError as exc:
            _fail(f"~/.claude.json is not valid JSON: {exc}")
        if not isinstance(data, dict):
            _fail("~/.claude.json must be a JSON object")
    else:
        data = {}
    current = data.get("mcpServers")
    if current is None:
        current = {}
    elif not isinstance(current, dict):
        _fail("mcpServers in ~/.claude.json must be an object")
    servers = dict(current)
    for server in roster:
        if server["claudeMarketplace"] or not server["enabled"]:
            servers.pop(server["name"], None)
        else:
            servers[server["name"]] = _json_entry("claude-user", server, values)
    updated = dict(data)
    updated["mcpServers"] = servers
    if updated == data and existing_text:
        return existing_text if existing_text.endswith("\n") else existing_text + "\n"
    return _dump(updated)


def claude_plugin_files(roster: list[dict], values: dict[str, str]) -> dict[str, str]:
    """Relative path to file text for disabled non-marketplace servers."""
    files = {}
    for server in roster:
        if server["enabled"] or server["claudeMarketplace"]:
            continue
        name = server["name"]
        plugin = {
            "name": name,
            "description": f"{name} MCP server, shipped disabled by clusterfork",
            "version": "1.0.0",
        }
        files[f"{name}/.claude-plugin/plugin.json"] = _dump(plugin)
        files[f"{name}/.mcp.json"] = _dump({
            "mcpServers": {name: _json_entry("claude-plugins", server, values)},
        })
    return files


_BIN_LAUNCHER = re.compile(r"bin/[\w.-]+")


def server_reach(server: dict) -> str:
    """Return the URL or command shown for a server in the install summary."""
    if server["url"]:
        return server["url"]
    command = server["command"]
    args = server["args"]
    if command == "bash" and len(args) >= 2 and args[0] == "-c":
        match = _BIN_LAUNCHER.search(args[1])
        if match:
            return match.group(0)
    return " ".join([command, *args])


def format_summary(roster: list[dict], *, columns: int) -> str:
    """Return the installer's MCP section, aligned like its skills section."""
    ordered = [server for server in roster if server["enabled"]]
    ordered += [server for server in roster if not server["enabled"]]
    if not ordered:
        return ""
    name_width = min(32, max(len(server["name"]) for server in ordered))
    tag_width = len("[disabled]")
    description_width = max(24, columns - name_width - tag_width - 7)
    lines = ["", "  MCP"]
    previous = None
    for server in ordered:
        status = "enabled" if server["enabled"] else "disabled"
        if previous is not None and status != previous:
            lines.append("")
        previous = status
        summary = textwrap.shorten(server_reach(server), width=description_width, placeholder="…")
        name = server["name"]
        tag = f"[{status}]"
        if len(name) > name_width:
            lines.append(f"    {tag:<{tag_width}} {name}")
            tag, name = "", ""
        lines.append(f"    {tag:<{tag_width}} {name:<{name_width}}  {summary}")
    return "\n".join(lines)


def selected_names(agent: str, roster: list[dict]) -> list[str]:
    if agent == "cursor":
        return [server["name"] for server in roster if server["enabled"]]
    if agent == "claude-plugins":
        return [
            server["name"] for server in roster
            if not server["enabled"] and not server["claudeMarketplace"]
        ]
    if agent == "claude-user":
        return [
            server["name"] for server in roster
            if server["enabled"] and not server["claudeMarketplace"]
        ]
    if agent == "claude-settings":
        return list(_claude_plugins(roster))
    if agent not in AGENTS:
        _fail(f"unknown agent {agent}")
    return [server["name"] for server in roster]


def _write(path: Path, text: str, *, private: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if private:
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
    path.write_text(text, encoding="utf-8")
    if private:
        path.chmod(0o600)


def apply(agent: str, roster: list[dict], values: dict[str, str], dest: Path, template: Path | None) -> None:
    if agent in TEMPLATE_AGENTS and template is None:
        _fail(f"{agent} requires --template")
    if agent not in TEMPLATE_AGENTS and template is not None:
        _fail(f"{agent} does not take --template")
    if agent in ("grok", "codex"):
        text = inject_toml(template.read_text(encoding="utf-8"), render_toml(agent, roster, values))
        _write(dest, text, private=False)
        return
    if agent == "claude-user":
        current = dest.read_text(encoding="utf-8") if dest.is_file() else None
        text = render_claude_user(current, roster, values)
        if current != text:
            _write(dest, text, private=False)
        return
    if agent == "claude-plugins":
        dest.mkdir(parents=True, exist_ok=True)
        files = claude_plugin_files(roster, values)
        for server in roster:
            plugin_dir = dest / server["name"]
            installing = f"{server['name']}/.mcp.json" in files
            stale_plugin = (plugin_dir / ".mcp.json").is_file()
            if plugin_dir.exists() and (installing or stale_plugin):
                shutil.rmtree(plugin_dir)
        for relative, text in files.items():
            _write(dest / relative, text, private=False)
        return
    template_text = template.read_text(encoding="utf-8") if template else None
    _write(dest, render_json(agent, roster, values, template_text), private=agent in PRIVATE_AGENTS)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    apply_parser = commands.add_parser("apply", help="write one agent's MCP config")
    apply_parser.add_argument("--roster", type=Path, required=True)
    apply_parser.add_argument("--dotenv", type=Path, required=True)
    apply_parser.add_argument("--agent", choices=AGENTS, required=True)
    apply_parser.add_argument("--dest", type=Path, required=True)
    apply_parser.add_argument("--template", type=Path)
    names_parser = commands.add_parser("names", help="list servers installed for one agent")
    names_parser.add_argument("--roster", type=Path, required=True)
    names_parser.add_argument("--agent", choices=AGENTS, required=True)
    summary_parser = commands.add_parser("summary", help="print the installer's MCP section")
    summary_parser.add_argument("--roster", type=Path, required=True)
    args = parser.parse_args(argv)
    roster = load_roster(args.roster)
    if args.command == "names":
        print(", ".join(selected_names(args.agent, roster)))
        return
    if args.command == "summary":
        text = format_summary(roster, columns=shutil.get_terminal_size(fallback=(120, 24)).columns)
        if text:
            print(text)
        return
    apply(args.agent, roster, load_dotenv(args.dotenv), args.dest, args.template)


if __name__ == "__main__":
    main()
