#!/usr/bin/env python3
"""codeview server: stdlib threaded HTTP server for the dashboard UI, JSON
sections, and drop-in module routes.

Usage (normally launched via bin/codeview):
  python3 .../server.py [--repo DIR] [--port N] [--max-commits N]
                        [--reindex] [--no-watch]

Environment honored:
  CODEVIEW_PORT   preferred port (CLI --port wins)
  CODEVIEW_REPO   repo root override (--repo wins; else cwd's git root)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codeview import cachestore, ci, fileview, modules_rt, scan  # noqa: E402

SERVER_PY = Path(__file__).resolve()
UI_DIR = Path(__file__).resolve().parent / "ui"

SCAN_SECTIONS = ("meta", "history", "files", "deps")
WATCH_INTERVAL = 3.0          # s between fingerprint checks
RESTART_QUIET = 5.0           # s the module fingerprint must stay stable
RESTART_MIN_INTERVAL = 30.0   # s minimum gap between self-restarts
CI_REFRESH_INTERVAL = 60.0    # s between GitHub CI state refreshes
LOG_BUFFER_LINES = 5000       # in-memory server log ring size


class AppState:
    """Shared state: cached sections, fingerprints, generation counter."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sections: dict[str, dict] = {}
        self.fingerprints: dict[str, str] = {}
        self.generation = 1
        self.last_rescan_ts: float | None = None
        self.ci_state: str | None = None
        self.logs: deque = deque(maxlen=LOG_BUFFER_LINES)

    def bump(self) -> None:
        with self.lock:
            self.generation += 1

    def get(self, name: str) -> dict | None:
        with self.lock:
            return self.sections.get(name)


def log_line(state: AppState, msg: str) -> None:
    """Append a timestamped lifecycle event to the in-memory log ring."""
    ts = time.strftime("%H:%M:%S")
    with state.lock:
        state.logs.append(f"[{ts}] {msg}")


# ------------------------------------------------------------------ setup --

def default_port(repo_path: Path) -> int:
    """Stable per-repo port (bookmarks survive) derived from the path."""
    digest = hashlib.sha256(str(repo_path).encode()).hexdigest()[:8]
    return 46000 + (int(digest, 16) % 4000)


def pick_port(preferred: int) -> int:
    """First free port at or above preferred."""
    for candidate in range(preferred, preferred + 64):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", candidate))
            return candidate
        except OSError:
            continue
    raise SystemExit(f"codeview: no free port near {preferred}")


def ensure_codeview_dir(repo: Path) -> Path:
    """Create <repo>/.codeview{,/cache} and the cache-only .gitignore once."""
    codeview_dir = repo / scan.CODEVIEW_DIR_NAME
    (codeview_dir / "cache").mkdir(parents=True, exist_ok=True)
    gitignore = codeview_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("cache/\n", encoding="utf-8")
    return codeview_dir


def resolve_repo(explicit: str | None) -> Path | None:
    start = Path(explicit).resolve() if explicit else Path.cwd()
    if not scan.try_git(["rev-parse", "--git-dir"], start):
        return None
    return scan.repo_root(start)


# ----------------------------------------------------------------- scans --

def run_section(section: str, repo: Path,
                shape: scan.RepoShape) -> tuple[dict, str]:
    """Scan one section, write its cache file atomically, return (data, fp)."""
    if section == "meta":
        data = scan.scan_meta(repo, shape)
    elif section == "history":
        data = scan.scan_history(repo, shape)
    elif section == "files":
        data = scan.scan_files(repo, shape)
    elif section == "deps":
        data = scan.scan_deps(repo, shape)
    else:  # pragma: no cover - guarded by SCAN_SECTIONS
        raise ValueError(f"unknown section {section}")
    payload = {"section": section, "scanned_at": data.get("scanned_at_iso"),
               "data": data}
    cachestore.write_json_atomic(
        cachestore.section_path(Path(".codeview"), section), {})
    fp = cachestore.fingerprint([json.dumps(data, sort_keys=True)[:4096]])
    return data, fp


def current_data_fingerprint(repo: Path, shape: scan.RepoShape) -> str:
    head = scan.try_git(["rev-parse", "HEAD"], repo)
    dirty = scan.try_git(["status", "--porcelain=v1"], repo)
    return cachestore.fingerprint([
        head or "",
        hashlib.sha256((dirty or "").encode()).hexdigest(),
        json.dumps(shape.as_dict(), sort_keys=True),
    ])


def current_module_fingerprint(codeview_dir: Path) -> str:
    entries: list[str] = []
    modules_dir = codeview_dir / "modules"
    if modules_dir.is_dir():
        for p in sorted(modules_dir.glob("*.py")):
            try:
                stat = p.stat()
                entries.append(f"{p.name}:{stat.st_mtime_ns}")
            except OSError:
                continue
    config = codeview_dir / "config.json"
    if config.exists():
        try:
            entries.append(f"config:{config.stat().st_mtime_ns}")
        except OSError:
            pass
    return cachestore.fingerprint(entries or ["none"])


def _init_rescan_ts(state: AppState) -> None:
    """Carry the cached scan time across restarts so staleness display is
    honest (a restart loads cache without rescanning)."""
    meta = state.get("meta") or {}
    iso = meta.get("scanned_at_iso")
    if not iso:
        return
    try:
        from datetime import datetime, timezone
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        with state.lock:
            state.last_rescan_ts = dt.timestamp()
    except ValueError:
        pass


def read_cached_sections(codeview_dir: Path) -> tuple[dict[str, dict],
                                                 dict[str, str]]:
    sections: dict[str, dict] = {}
    fps: dict[str, str] = {}
    stored = cachestore.read_json(
        codeview_dir / "cache" / "fingerprints.json") or {}
    for section in SCAN_SECTIONS:
        data = cachestore.read_json(
            cachestore.section_path(codeview_dir, section))
        if data is None:
            continue
        sections[section] = data.get("data") or {}
        fp = stored.get(section)
        if fp:
            fps[section] = fp
    return sections, fps


def scan_all(repo: Path, shape: scan.RepoShape, codeview_dir: Path,
             state: AppState, force: bool) -> None:
    """Rescan stale sections into memory + disk; keeps old data on failure.

    Staleness = the section's stored data fingerprint (HEAD sha + dirty
    hash + scan options) differing from the current one. meta is cheap and
    always refreshed.
    """
    wanted = current_data_fingerprint(repo, shape)
    fresh: dict[str, tuple[dict, str]] = {}
    errors: list[str] = []
    for section in SCAN_SECTIONS:
        if (not force and section != "meta"
                and state.fingerprints.get(section) == wanted
                and section in state.sections):
            log_line(state, f"scan[{section}]: fresh (fingerprint match), "
                            "skipped")
            continue
        t0 = time.time()
        try:
            data = run_section_now(section, repo, shape)
            fresh[section] = (data, wanted)
            log_line(state, f"scan[{section}]: {_scan_summary(section, data)}"
                     f" ({time.time() - t0:.2f}s)")
        except Exception as exc:  # noqa: BLE001 — keep stale data on failure
            errors.append(f"{section}: {exc.__class__.__name__}: {exc}")
            log_line(state, f"scan[{section}]: FAILED "
                     f"{exc.__class__.__name__}: {exc}")
    with state.lock:
        for section, (data, fp) in fresh.items():
            state.sections[section] = data
            state.fingerprints[section] = fp
        if errors:
            state.sections.setdefault("meta", {})["scan_errors"] = errors
        state.last_rescan_ts = time.time()


def run_section_now(section: str, repo: Path, shape: scan.RepoShape):
    scanners = {
        "meta": scan.scan_meta,
        "history": scan.scan_history,
        "files": scan.scan_files,
        "deps": scan.scan_deps,
    }
    return scanners[section](repo, shape)


def persist_state(codeview_dir: Path, state: AppState) -> None:
    with state.lock:
        sections = dict(state.sections)
        fps = dict(state.fingerprints)
    for section in SCAN_SECTIONS:
        if section not in sections:
            continue
        cachestore.write_json_atomic(
            cachestore.section_path(codeview_dir, section),
            {"section": section, "data": sections[section]})
    cachestore.write_json_atomic(
        codeview_dir / "cache" / "fingerprints.json", fps)


def purge_stale_cache_files(codeview_dir: Path) -> None:
    cache_dir = codeview_dir / "cache"
    if not cache_dir.is_dir():
        return
    keep = {f"{s}.json" for s in SCAN_SECTIONS} | {"fingerprints.json"}
    for p in cache_dir.iterdir():
        if p.name.startswith(".") and p.name.endswith(".tmp"):
            p.unlink(missing_ok=True)
        elif p.suffix == ".json" and p.name not in keep:
            p.unlink(missing_ok=True)


# ------------------------------------------------------------------ watch --

def spawn_reloader(repo: Path, port: int, max_commits: int,
                   restart_count: int) -> None:
    """Self-restart via execve(sys.executable) so bash/uv chains survive."""
    env = os.environ.copy()
    env["CODEVIEW_PORT"] = str(port)
    env["CODEVIEW_REPO"] = str(repo)
    env["CODEVIEW_RESTARTS"] = str(restart_count)
    argv = [sys.executable, str(SERVER_PY),
            "--repo", str(repo), "--port", str(port),
            "--max-commits", str(max_commits)]
    sys.stderr.write(f"codeview: restarting (generation {restart_count})\n")
    sys.stderr.flush()
    os.execve(sys.executable, argv, env)


def refresh_ci_state(repo: Path, state: AppState) -> None:
    """Best-effort GitHub Actions state for HEAD; None means 'no CI'."""
    head = (scan.try_git(["rev-parse", "HEAD"], repo) or "").strip()
    try:
        new = ci.github_ci_state(repo, head) if head else None
    except Exception:
        new = None
    changed = new != state.ci_state
    log_line(state, f"ci: {new or 'none'}"
             + (" (changed)" if changed else ""))
    state.ci_state = new


def _scan_summary(section: str, data: dict) -> str:
    if section == "meta":
        return (f"head {data.get('short_head')} branch "
                f"{data.get('branch')} dirty={data.get('dirty')}")
    if section == "history":
        return f"{len(data.get('commits', []))} commits"
    if section == "files":
        return (f"{data.get('total_files')} files, "
                f"{data.get('total_lines')} lines")
    if section == "deps":
        return f"{len(data.get('ecosystems', []))} ecosystems"
    return ""


def watch_loop(ctx) -> None:
    """Background fingerprint watcher: rescan data in place, restart on
    module-set changes after a quiet period."""
    while True:
        time.sleep(WATCH_INTERVAL)
        repo, shape, codeview_dir, state = ctx["repo"], ctx["shape"], \
            ctx["codeview_dir"], ctx["state"]
        now = time.time()
        if now - ctx["ci_ts"] >= CI_REFRESH_INTERVAL:
            ctx["ci_ts"] = now
            refresh_ci_state(repo, state)
        mod_fp = current_module_fingerprint(codeview_dir)
        if mod_fp != ctx["module_fp"]:
            if ctx["mod_seen_at"] is None:
                log_line(state, "watch: module set changed, arming restart "
                                f"({RESTART_QUIET:.0f}s quiet)")
            ctx["mod_seen_at"] = ctx["mod_seen_at"] or time.time()
            if time.time() - ctx["mod_seen_at"] >= RESTART_QUIET:
                now = time.time()
                if now - ctx["last_restart"] >= RESTART_MIN_INTERVAL:
                    log_line(state, "watch: module set stable → self-restart")
                    persist_state(codeview_dir, state)
                    spawn_reloader(repo, ctx["port"],
                                   shape.max_commits,
                                   ctx["restart_count"] + 1)
                    return  # unreachable under execve, kept for clarity
        else:
            ctx["mod_seen_at"] = None
        with state.lock:
            known_meta = state.sections.get("meta")
        dirty_changed = known_meta is not None and (
            known_meta.get("dirty_hash") !=
            _meta_dirty_hash(repo))
        head_changed = known_meta is not None and (
            known_meta.get("head") != _meta_head(repo))
        if head_changed or dirty_changed:
            what = ", ".join(w for w, yes in
                             (("head", head_changed),
                              ("dirty", dirty_changed)) if yes)
            log_line(state, f"watch: data change detected ({what}) → rescan")
            try:
                scan_all(repo, shape, codeview_dir, state, force=False)
                persist_state(codeview_dir, state)
                state.bump()
            except Exception:
                traceback.print_exc()
            if head_changed:
                refresh_ci_state(repo, state)


def _meta_head(repo: Path) -> str:
    out = scan.try_git(["rev-parse", "HEAD"], repo)
    return (out or "").strip()


def _meta_dirty_hash(repo: Path) -> str:
    dirty = scan.try_git(["status", "--porcelain=v1"], repo) or ""
    return hashlib.sha256(dirty.encode()).hexdigest()[:16]


# ----------------------------------------------------------------- http ----

CONTENT_TYPES = {"html": "text/html; charset=utf-8",
                 "js": "application/javascript; charset=utf-8",
                 "css": "text/css; charset=utf-8"}

MIME_BY_SUFFIX = {".html": CONTENT_TYPES["html"],
                  ".js": CONTENT_TYPES["js"], ".css": CONTENT_TYPES["css"],
                  ".json": "application/json; charset=utf-8",
                  ".svg": "image/svg+xml", ".png": "image/png",
                  ".ico": "image/x-icon", ".woff2": "font/woff2"}


class DashHandler(BaseHTTPRequestHandler):
    server_version = "codeview/1.0"

    @property
    def app(self):
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        line = "%s - %s" % (self.address_string(), fmt % args)
        state = self.app.get("state")
        if state is not None:
            log_line(state, line)
        if self.app.get("verbose"):
            sys.stderr.write(line + "\n")

    # -- responses ----------------------------------------------------------

    def send_body(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, default=str).encode()
        self.send_body(status, body, "application/json; charset=utf-8")

    def send_error_page(self, status: int, message: str) -> None:
        self.send_json({"error": message}, status)

    # -- routing ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        self.dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self.dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self.dispatch("POST")

    def dispatch(self, verb: str) -> None:
        try:
            route = self.path.split("?", 1)[0].rstrip("/") or "/"
            if route == "/":
                route = "/"
            handlers = [
                self.h_index,
                self.h_assets,
                self.h_gen,
                self.h_tabs,
                self.h_section_list,
                self.h_section_detail,
                self.h_file,
                self.h_logs,
                self.h_modules_http,
                self.h_ui_fallback,
            ]
            for handler_fn in handlers:
                result = handler_fn(verb, route)
                if result is not None:
                    return
            self.send_error_page(404, f"no route for {route}")
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            try:
                self.send_error_page(500, f"{exc.__class__.__name__}: {exc}")
            except Exception:
                pass

    # -- core endpoints -----------------------------------------------------

    def h_index(self, verb: str, route: str):
        if route != "/":
            return None
        html = (UI_DIR / "index.html")
        if not html.exists():
            self.send_error_page(500, "ui assets missing")
            return True
        self.send_body(200, html.read_bytes(), CONTENT_TYPES["html"])
        return True

    def h_assets(self, verb: str, route: str):
        if not route.startswith("/assets/"):
            return None
        rel = route[len("/assets/"):]
        base = UI_DIR.resolve()
        target = (base / rel).resolve()
        # Real containment (symlink-proof) + strict extension allowlist:
        # nothing outside ui/, nothing executable or dot-filey.
        if not str(target).startswith(str(base) + os.sep):
            self.send_error_page(403, "forbidden")
            return True
        suffix = target.suffix.lower().lstrip(".")
        allowed = {"html", "js", "css", "svg", "png", "ico", "woff2",
                   "json", "map"}
        if suffix not in allowed or target.name.startswith("."):
            self.send_error_page(404, "missing asset")
            return True
        if not target.is_file():
            self.send_error_page(404, "missing asset")
        else:
            mime = MIME_BY_SUFFIX.get("." + suffix,
                                      "application/octet-stream")
            self.send_body(200, target.read_bytes(), mime)
        return True

    def h_gen(self, verb: str, route: str):
        if route != "/api/gen":
            return None
        state: AppState = self.app["state"]
        with state.lock:
            gen = state.generation
            last = state.last_rescan_ts
        self.send_json({"generation": gen, "rescanned_at": last})
        return True

    def h_tabs(self, verb: str, route: str):
        if route != "/api/tabs":
            return None
        mods = self.app["modules"]
        tabs = [{"kind": "core", "name": "overview"},
                {"kind": "core", "name": "history"},
                {"kind": "core", "name": "files"},
                {"kind": "core", "name": "deps"}]
        for m in mods:
            tabs.append({
                "kind": "module" if m["ok"] else "broken",
                "name": m["name"],
                "description": m["description"],
                "href": f"/m/{m['name']}/",
            })
        tabs.append({"kind": "core", "name": "logs"})
        self.send_json({"tabs": tabs})
        return True

    def h_section_list(self, verb: str, route: str):
        if route != "/api/summary":
            return None
        state: AppState = self.app["state"]
        meta = state.get("meta") or {}
        files = state.get("files") or {}
        history = state.get("history") or {}
        deps = state.get("deps") or {}
        commits = history.get("commits") or []
        self.send_json({
            "meta": {
                k: meta.get(k) for k in
                ("repo_name", "short_head", "branch", "detached",
                 "empty_repo", "dirty", "scanned_at_iso")
            },
            "total_files": files.get("total_files"),
            "total_lines": files.get("total_lines"),
            "langs": files.get("langs"),
            "tops": files.get("tops"),
            "commits_count": len(commits),
            "dirs": history.get("dirs"),
            "ecosystems": [
                {k: eco.get(k) for k in ("name", "manifest", "lockfile")}
                for eco in deps.get("ecosystems", [])],
            "modules": [
                {"name": m["name"], "ok": m["ok"]}
                for m in self.app["modules"]],
            "ci": state.ci_state,
        })
        return True

    def h_section_detail(self, verb: str, route: str):
        m = self._SECTION_RE.match(route)
        if not m:
            return None
        name = m.group(1)
        state: AppState = self.app["state"]
        data = state.get(name)
        if data is None:
            self.send_error_page(503, f"section {name!r} not scanned yet")
            return True
        self.send_json(data)
        return True

    _SECTION_RE_TPL = r"^/api/section/({})$".format("|".join(SCAN_SECTIONS))

    @property
    def _SECTION_RE(self):  # noqa: N802 — stdlib handler namespace
        compiled = self.server._section_re  # type: ignore[attr-defined]
        return compiled

    def h_file(self, verb: str, route: str):
        if route != "/api/file":
            return None
        from urllib.parse import parse_qs, urlsplit
        rel = (parse_qs(urlsplit(self.path).query).get("path") or [""])[0]
        # Exact match against the tracked-files cache: traversal-proof by
        # construction — anything not in `git ls-files` has no entry.
        entry = next((f for f in (self.app["state"].get("files")
                                  or {}).get("files", [])
                      if f.get("path") == rel), None)
        if entry is None:
            self.send_error_page(404, "not a tracked file")
            return True
        self.send_json(fileview.file_payload(self.app["repo"], entry))
        return True

    def h_logs(self, verb: str, route: str):
        if route != "/api/logs":
            return None
        state: AppState = self.app["state"]
        with state.lock:
            logs = list(state.logs)
        self.send_json({"logs": logs})
        return True

    # -- module routes --------------------------------------------------------

    def h_modules_http(self, verb: str, route: str):
        table = self.app["module_routes"]
        entry = table.get((verb, route)) or table.get(("GET", route))
        if entry is None:
            return None
        ok, name, fn, error = entry
        if not ok:
            page = ("<pre style='font-family:monospace;padding:16px'>"
                    f"broken module: {name}\n\n{error}</pre>")
            self.send_body(200, page.encode(), CONTENT_TYPES["html"])
            return True
        try:
            status, body, ctype = fn(_make_request(self))
        except Exception as exc:  # noqa: BLE001
            tb = "".join(traceback.format_exception(exc)).strip()
            page = (f"<pre style='font-family:monospace;padding:16px'>"
                    f"module error:\n{tb}</pre>")
            self.send_body(500, page.encode(), CONTENT_TYPES["html"])
            return True
        if isinstance(body, dict):
            self.send_json(body, status)
        elif isinstance(body, bytes):
            self.send_body(status, body, ctype or "application/octet-stream")
        else:
            text = str(body)
            self.send_body(status, text.encode(),
                           ctype or "text/html; charset=utf-8")
        return True

    def h_ui_fallback(self, verb: str, route: str):
        return None


class ModuleRequest:
    """Minimal request facade handed to module handlers."""

    def __init__(self, inner: "DashHandler", query: dict[str, str]) -> None:
        self.method = inner.command
        self.path = inner.path
        self.query = query
        self.headers = inner.headers
        self._inner = inner

    def json(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self._inner.rfile.read(length) if length > 0 else b""
        return json.loads(raw.decode()) if raw else {}

    def text(self) -> str:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self._inner.rfile.read(length) if length > 0 else b""
        return raw.decode("utf-8", "replace")


def _make_request(inner: DashHandler) -> ModuleRequest:
    from urllib.parse import parse_qs, urlsplit
    parts = urlsplit(inner.path)
    q = {k: v[-1] for k, v in parse_qs(parts.query).items()}
    return ModuleRequest(inner, q)


def build_module_table(mods: list[dict]) -> dict:
    """{(verb, path): (ok, name, fn|None, error)} for dispatch.

    Keys use trailing-slash-stripped form matching dispatch()'s
    normalization ('' → '/')."""
    table: dict = {}
    for m in mods:
        if not m["ok"]:
            # Broken tab still needs a landing page.
            table[("GET", f"/m/{m['name']}")] = \
                (False, m["name"], None, m["error"])
            continue
        for (verb, route), fn in m["routes"].items():
            # Module registry emits stripped canonical paths; defensive strip
            # so direct dict users can't reintroduce slash-form drift.
            key_route = route.rstrip("/") or "/"
            table[(verb, key_route)] = (True, m["name"], fn, None)
    return table


# ------------------------------------------------------------------ main ---

def parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="codeview server")
    parser.add_argument("--repo", help="repo root (default: cwd's git root)")
    parser.add_argument("--port", type=int,
                        help="port to bind (default: stable per-repo hash)")
    parser.add_argument("--max-commits", type=int, default=1000)
    parser.add_argument("--reindex", action="store_true",
                        help="force full rescan on boot")
    parser.add_argument("--no-watch", action="store_true",
                        help="disable background rescans/restarts")
    return parser.parse_args(argv)


def bind_with_retry(port: int, attempts: int = 10, delay: float = 0.5):
    """Retry-bind to ride out the CLOEXEC gap across self-restarts."""
    last_exc: OSError | None = None
    for _ in range(attempts):
        try:
            return ThreadingHTTPServer(("127.0.0.1", port), DashHandler)
        except OSError as exc:
            last_exc = exc
            time.sleep(delay)
    raise SystemExit(f"codeview: could not bind port {port}: {last_exc}")


def main(argv=None) -> int:
    args = parse_args(argv)
    preferred = args.port or int(os.environ.get("CODEVIEW_PORT")
                                 or default_port(Path.cwd()))
    # On self-restart (CODEVIEW_RESTARTS set) the preferred port MUST hold:
    # the wrapper, browser polls, and bookmarks all point at it. TIME_WAIT
    # sockets are ridden out by bind_with_retry + SO_REUSEADDR instead of
    # drifting to preferred+1 (which silently strands every client).
    strict_port = bool(os.environ.get("CODEVIEW_RESTARTS"))
    port = preferred if strict_port else pick_port(preferred)
    repo = resolve_repo(args.repo or os.environ.get("CODEVIEW_REPO"))
    if repo is None:
        print("codeview: not inside a git repository", file=sys.stderr)
        return 1
    shape = scan.RepoShape(max_commits=args.max_commits)
    codeview_dir = ensure_codeview_dir(repo)
    purge_stale_cache_files(codeview_dir)

    state = AppState()
    sections, fps = {}, {}
    if not args.reindex:
        sections, fps = read_cached_sections(codeview_dir)
    state.sections.update({k: v for k, v in sections.items()})
    state.fingerprints.update(fps)
    _init_rescan_ts(state)
    log_line(state, f"boot: loaded cached sections "
             f"{sorted(sections) or '[]'} from {codeview_dir.name}/cache")
    missing = [s for s in SCAN_SECTIONS if s not in state.sections]
    if missing:
        log_line(state, f"boot: cache miss for {missing} → scanning")
        scan_all(repo, shape, codeview_dir, state, force=args.reindex)
    persist_state(codeview_dir, state)

    mods = modules_rt.load_modules(codeview_dir / "modules")
    log_line(state, f"boot: modules {sum(1 for m in mods if m['ok'])}/"
             f"{len(mods)} ok")
    httpd = bind_with_retry(port)

    watchdog_ctx = {
        "repo": repo, "shape": shape, "codeview_dir": codeview_dir,
        "state": state,
        "port": port,
        "module_fp": current_module_fingerprint(codeview_dir),
        "mod_seen_at": None,
        "last_restart": 0.0,
        "restart_count": int(os.environ.get("CODEVIEW_RESTARTS") or 0),
        "ci_ts": time.time(),
    }
    httpd.app = {  # type: ignore[attr-defined]
        "state": state, "modules": mods,
        "module_routes": build_module_table(mods),
        "repo": repo,
        "verbose": bool(os.environ.get("CODEVIEW_VERBOSE")),
        "_ctx": watchdog_ctx,
    }
    httpd._section_re = __import__("re").compile(  # type: ignore[attr-defined]
        r"^/api/section/(%s)$" % "|".join(SCAN_SECTIONS))

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    if not args.no_watch:
        threading.Thread(target=watch_loop, args=(watchdog_ctx,),
                         daemon=True, name="codeview-watch").start()
        log_line(state, f"boot: watch loop armed "
                        f"({WATCH_INTERVAL:.0f}s interval)")
    # CI indicator: one eager fetch so the header is populated fast, then
    # the watch loop keeps it fresh (or not, with --no-watch).
    threading.Thread(target=refresh_ci_state, args=(repo, state),
                     daemon=True, name="codeview-ci").start()

    print(f"codeview: serving {repo.name} at http://127.0.0.1:{port}/ "
          f"(modules: {sum(1 for m in mods if m['ok'])}/{len(mods)} ok)",
          flush=True)
    log_line(state, f"serving {repo.name} at http://127.0.0.1:{port}/")
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        persist_state(codeview_dir, state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
