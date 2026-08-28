"""Repository scanners for codeview: git history, file snapshot, dep manifests.

All functions take the resolved repo root and return plain JSON-ready dicts.
Stdlib + git plumbing only.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from codeview import metrics

CODEVIEW_DIR_NAME = ".codeview"

# Excluded from the LOC snapshot by suffix or directory component.
EXCLUDE_SUFFIXES = (
    ".min.js", ".min.css", ".map", ".lock", "-lock.json", ".sum", ".svg",
)
EXCLUDE_DIR_NAMES = {"node_modules", "vendor", "dist", "build", "__pycache__"}

LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript", ".jsx": "JavaScript", ".sh": "Shell",
    ".bash": "Shell", ".rs": "Rust", ".go": "Go", ".c": "C", ".h": "C",
    ".cpp": "C++", ".hpp": "C++", ".cc": "C++", ".md": "Markdown",
    ".json": "JSON", ".toml": "TOML", ".yaml": "YAML", ".yml": "YAML",
    ".html": "HTML", ".css": "CSS", ".sql": "SQL", ".rb": "Ruby",
    ".java": "Java", ".kt": "Kotlin", ".swift": "Swift", ".php": "PHP",
    ".vim": "Vim script", ".lua": "Lua",
}

# Files above this size are byte-counted only; line counts stay None.
MAX_LINE_COUNT_BYTES = 8 * 1024 * 1024

MAX_LOCK_ENTRIES = 500


def git(args: list[str], repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout


def try_git(args: list[str], repo: Path) -> str | None:
    try:
        return git(args, repo)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def repo_root(start_dir: Path) -> Path | None:
    """Resolve via rev-parse so running from subdirs/worktrees works."""
    out = try_git(["rev-parse", "--show-toplevel"], start_dir)
    return None if out is None else Path(out.strip()).resolve()


class RepoShape:
    """Per-repo scan options captured at boot from CLI flags."""

    def __init__(self, max_commits: int = 1000) -> None:
        self.max_commits = max_commits

    def as_dict(self) -> dict:
        return {"max_commits": self.max_commits}


# ------------------------------------------------------------------- meta --

def scan_meta(repo: Path, shape: RepoShape) -> dict:
    head = try_git(["rev-parse", "HEAD"], repo)
    branch = try_git(["rev-parse", "--abbrev-ref", "HEAD"], repo)
    dirty = try_git(["status", "--porcelain=v1"], repo) or ""
    dirty_hash = hashlib.sha256(dirty.encode("utf-8")).hexdigest()[:16]
    return {
        "repo_name": repo.name or str(repo),
        "head": head.strip() if head else None,
        "short_head": head[:8].strip() if head else None,
        # rev-parse prints "HEAD" when detached; empty means no commits yet.
        "branch": (branch or "").strip() or None,
        "detached": (branch or "").strip() == "HEAD",
        "empty_repo": not bool(head),
        "dirty": bool(dirty),
        "dirty_hash": dirty_hash,
        "shape": shape.as_dict(),
        "scanned_at_iso": _now_iso(),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------- history --

def scan_history(repo: Path, shape: RepoShape) -> dict:
    """Cumulative LOC-over-time from `git log --numstat`.

    Per commit: net added-removed delta overall and per top-level dir.
    Cumulative totals are prefix sums the UI derives; binary entries
    (`-\t-\t`) are skipped.
    """
    raw = try_git(
        ["log", "--reverse", "--numstat", "--no-color",
         "--format=%x01%H%x09%cI%x09%s", f"-{shape.max_commits}"],
        repo,
    )
    commits: list[dict] = []
    all_dirs: set[str] = set()
    total = 0
    if raw:
        for chunk in raw.split("\x01"):
            if not chunk.strip():
                continue
            lines = chunk.splitlines()
            head_parts = lines[0].split("\t")
            if len(head_parts) < 3:
                continue
            sha, date, subject = head_parts[0], head_parts[1], head_parts[2]
            delta = 0
            chunk_dirs: dict[str, int] = {}
            for line in lines[1:]:
                parts = line.split("\t")
                if len(parts) != 3:
                    continue
                add_s, del_s, path = parts
                if add_s == "-" or del_s == "-" or not path:
                    continue
                try:
                    d = int(add_s) - int(del_s)
                except ValueError:
                    continue
                delta += d
                top = path.split("/", 1)[0]
                # Root-level files have no dir; bucket them under "(root)".
                if "/" not in path:
                    top = "(root)"
                chunk_dirs[top] = chunk_dirs.get(top, 0) + d
            total += delta
            all_dirs.update(chunk_dirs)
            commits.append({
                "sha": sha[:8], "date": date[:16], "subject": subject[:120],
                "delta": delta, "total": total, "dirs": chunk_dirs,
            })
    return {
        "commits": commits,
        "dirs": sorted(all_dirs),
        "max_commits": shape.max_commits,
        "truncated": len(commits) >= shape.max_commits,
    }


# ----------------------------------------------------------------- files --

def _excluded(path: str) -> bool:
    parts = path.split("/")
    if any(p in EXCLUDE_DIR_NAMES for p in parts[:-1]):
        return True
    name = parts[-1]
    if CODEVIEW_DIR_NAME in parts[:-1]:
        return True
    return name.endswith(EXCLUDE_SUFFIXES)


def _count_lines(path: Path) -> tuple[int | None, bool]:
    """Return (lines|None, is_binary). None means skipped (too large/binary)."""
    text, binary, _ = _read_source(path)
    if text is None:
        return None, binary
    return len(text.splitlines()), False


def _read_source(path: Path) -> tuple[str | None, bool, int | None]:
    """Read an analyzable UTF-8-ish file once, returning text/binary/bytes."""
    try:
        size = path.stat().st_size
    except OSError:
        return None, False, None
    if size > MAX_LINE_COUNT_BYTES:
        return None, False, size
    try:
        data = path.read_bytes()
    except OSError:
        return None, False, size
    if b"\0" in data[:8000]:
        return None, True, size
    return data.decode("utf-8", errors="replace"), False, size


def scan_files(repo: Path, shape: RepoShape) -> dict:  # noqa: ARG001 (uniform signature)
    listing = try_git(["ls-files", "-z"], repo)
    files: list[dict] = []
    langs: dict[str, dict] = {}
    tops: dict[str, dict] = {}
    analyzed_metrics: list[dict] = []
    if listing:
        for relpath in listing.split("\0"):
            if not relpath or _excluded(relpath):
                continue
            abs_path = repo / relpath
            ext = abs_path.suffix.lower()
            lang = LANG_BY_EXT.get(ext, "Other")
            top = relpath.split("/", 1)[0]
            if not abs_path.is_file() or abs_path.is_symlink():
                continue
            source, binary, size = _read_source(abs_path)
            if size is None:
                continue
            if source is None:
                lines = None
                source_metrics = metrics.empty_metrics(
                    "binary" if binary else "too large or unreadable")
            else:
                source_metrics = metrics.analyze_source(source, lang)
                lines = source_metrics["total_lines"]
                analyzed_metrics.append(source_metrics)
            entry = {
                "path": relpath, "top": top, "ext": ext.lstrip("."),
                "lang": lang, "lines": lines, "bytes": size,
                "binary": binary, "metrics": source_metrics,
            }
            files.append(entry)
            if lines is not None:
                for bucket_dict, bucket_key in ((langs, lang), (tops, top)):
                    b = bucket_dict.setdefault(
                        bucket_key, {"files": 0, "lines": 0})
                    b["files"] += 1
                    b["lines"] += lines
    total_lines = sum(b["lines"] for b in langs.values())
    metric_totals = metrics.aggregate(analyzed_metrics)
    return {
        "files": files,
        "langs": langs,
        "tops": tops,
        "total_files": len(files),
        "total_lines": total_lines,
        "metric_totals": metric_totals,
        "total_code_lines": metric_totals["code_lines"],
        "total_blank_lines": metric_totals["blank_lines"],
        "total_comment_lines": metric_totals["comment_lines"],
    }


# ------------------------------------------------------------------ deps --

_TOML_SECTION_RE = re.compile(r"^\[(.+)\]\s*$")
_TOML_KV_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s*=\s*(.+?)\s*$")
_TOML_STR_RE = re.compile(r"^\"([^\"]*)\"$")


def _toml_subset(text: str) -> dict:
    """Flat TOML subset: sections + string scalar values. Enough for manifests."""
    parsed: dict[str, dict] = {}
    current: dict | None = parsed
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        m = _TOML_SECTION_RE.match(line)
        if m:
            current = parsed.setdefault(m.group(1), {})
            continue
        m = _TOML_KV_RE.match(line)
        if m and isinstance(current, dict):
            sm = _TOML_STR_RE.match(m.group(2))
            current[m.group(1)] = sm.group(1) if sm else m.group(2)
    return parsed


def _strip_req(req: str) -> str:
    return req.strip()


def _deps_from_pyproject(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        data = json.loads(json.dumps(_toml_subset(path.read_text(errors="replace"))))
        # deps may use inline arrays; subset parser only keeps scalars, so fall
        # back to a regex sweep over the raw text for PEP 508 requirement lines.
        text = path.read_text(errors="replace")
    except OSError:
        return out
    seen_section = False
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith("[project]"):
            seen_section = True
            continue
        if stripped.startswith("[") and stripped.endswith("]") and seen_section:
            break
        if not seen_section or "=" not in stripped:
            continue
        val = stripped.split("=", 1)[1].strip()
        for item in re.findall(r'"([^"]+)"', val):
            name = re.split(r"[<>=~!\[;\s]", item, 1)[0]
            if name:
                out.append({"name": name, "req": item})
    return out


def _deps_from_go_mod(path: Path) -> list[dict]:
    out: list[dict] = []
    in_require = False
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return out
    for raw_line in text.splitlines():
        line = raw_line.split("//", 1)[0].strip()
        if line.startswith("require ("):
            in_require = True
            continue
        if in_require and line == ")":
            in_require = False
            continue
        if in_require or line.startswith("require "):
            body = line[len("require "):].strip() if line.startswith("require ") else line
            parts = body.split()
            if len(parts) >= 2:
                out.append({"name": parts[0], "req": parts[1]})
    return out


def _locked_from_lock_json(lock_path: Path) -> list[dict]:
    try:
        data = json.loads(lock_path.read_text())
    except (OSError, ValueError):
        return []
    packages = data.get("packages")
    out: list[dict] = []
    if isinstance(packages, dict):
        for key, info in packages.items():
            if not key:  # root entry ""
                continue
            name = info.get("name") or key.rsplit("node_modules/", 1)[-1]
            version = info.get("version")
            if name and version:
                out.append({"name": name, "version": version})
    elif isinstance(data.get("dependencies"), dict):
        for name, info in data["dependencies"].items():
            if isinstance(info, dict) and info.get("version"):
                out.append({"name": name, "version": info["version"]})
    return out


def _locked_from_cargo_lock(path: Path) -> list[dict]:
    out: list[dict] = []
    name = version = None
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return out
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line == "[[package]]":
            if name and version:
                out.append({"name": name, "version": version})
            name = version = None
            continue
        if line.startswith(("name =", "name=")):
            m = _TOML_STR_RE.match(line.split("=", 1)[1].strip())
            if m:
                name = m.group(1)
        elif line.startswith(("version =", "version=")):
            m = _TOML_STR_RE.match(line.split("=", 1)[1].strip())
            if m:
                version = m.group(1)
    if name and version:
        out.append({"name": name, "version": version})
    return out


def scan_deps(repo: Path, shape: RepoShape) -> dict:  # noqa: ARG001
    ecosystems: list[dict] = []

    cargo = repo / "Cargo.toml"
    if cargo.exists():
        declared: list[dict] = []
        try:
            toml = _toml_subset(cargo.read_text(errors="replace"))
        except OSError:
            toml = {}
        for section in ("dependencies", "dev-dependencies"):
            block = toml.get(section) or {}
            for dep_name, req in block.items():
                declared.append({
                    "name": dep_name, "req": _strip_req(str(req)),
                    "kind": "normal" if section == "dependencies" else "dev",
                })
        lock_entries: list[dict] = []
        cargo_lock = repo / "Cargo.lock"
        has_lock = cargo_lock.exists()
        if has_lock:
            lock_entries = _locked_from_cargo_lock(cargo_lock)[:MAX_LOCK_ENTRIES]
        ecosystems.append({
            "name": "cargo", "manifest": "Cargo.toml",
            "lockfile": "Cargo.lock" if has_lock else None,
            "declared": declared, "locked": lock_entries,
        })

    pkg = repo / "package.json"
    if pkg.exists():
        declared = []
        try:
            data = json.loads(pkg.read_text())
        except (OSError, ValueError):
            data = {}
        for kind, section in (("normal", "dependencies"),
                              ("dev", "devDependencies")):
            for dep_name, req in (data.get(section) or {}).items():
                declared.append({"name": dep_name, "req": str(req),
                                 "kind": kind})
        npm_lock = repo / "package-lock.json"
        yarn_lock = repo / "yarn.lock"
        pnpm_lock = repo / "pnpm-lock.yaml"
        lockfile = next((n for n, p in (
            ("package-lock.json", npm_lock), ("pnpm-lock.yaml", pnpm_lock),
            ("yarn.lock", yarn_lock)) if p.exists()), None)
        lock_entries = _locked_from_lock_json(npm_lock)[:MAX_LOCK_ENTRIES]
        ecosystems.append({
            "name": "npm", "manifest": "package.json", "lockfile": lockfile,
            "declared": declared, "locked": lock_entries,
        })

    pyproject = repo / "pyproject.toml"
    if pyproject.exists():
        lock_entries = []
        lockfile = None
        for lock_name in ("uv.lock", "poetry.lock", "requirements.txt"):
            if (repo / lock_name).exists():
                lockfile = lock_name
                break
        ecosystems.append({
            "name": "python", "manifest": "pyproject.toml",
            "lockfile": lockfile,
            "declared": _deps_from_pyproject(pyproject), "locked": [],
        })

    go_mod = repo / "go.mod"
    if go_mod.exists():
        go_sum = repo / "go.sum"
        lock_entries = []
        if go_sum.exists():
            seen: set[tuple[str, str]] = set()
            try:
                for raw_line in go_sum.read_text(errors="replace").splitlines():
                    parts = raw_line.split()
                    if len(parts) >= 2 and parts[0]:
                        key = (parts[0], parts[1])
                        if key not in seen:
                            seen.add(key)
                            lock_entries.append({"name": parts[0],
                                                 "version": parts[1]})
                            if len(lock_entries) >= MAX_LOCK_ENTRIES:
                                break
            except OSError:
                pass
        ecosystems.append({
            "name": "go", "manifest": "go.mod",
            "lockfile": "go.sum" if go_sum.exists() else None,
            "declared": _deps_from_go_mod(go_mod), "locked": lock_entries,
        })

    return {"ecosystems": ecosystems}
