"""Per-file pane data: on-disk content + git statistics for one tracked
path. The caller must pass an entry from the tracked-files cache (exact
string match against `git ls-files`), which makes path traversal impossible
by construction: anything else has no entry.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from codeview import metrics, scan

MAX_CONTENT_BYTES = 1_000_000   # cap what we read into the browser
MAX_DISPLAY_LINES = 5000        # cap rendered lines (truncated flag set)
MAX_BLAME_LINES = 3000          # skip blame above this (quadratic cost)

_SEP = "\x1f"  # git --format field separator


def _stats(repo: Path, rel: str, line_count: int | None) -> dict:
    stats: dict = {
        "commits": None, "last_commit": None, "first_commit_date": None,
        "added": None, "deleted": None, "authors": [], "blame": [],
    }

    count = scan.try_git(["rev-list", "--count", "HEAD", "--", rel], repo)
    if count:
        stats["commits"] = int(count.strip())

    last = scan.try_git(
        ["log", "-1", f"--format=%H{_SEP}%an{_SEP}%aI{_SEP}%s", "--", rel],
        repo)
    if last:
        sha, author, date, subject = last.strip().split(_SEP, 3)
        stats["last_commit"] = {"sha": sha[:8], "author": author,
                                "date": date, "subject": subject}

    dates = scan.try_git(["log", "--format=%aI", "--", rel], repo)
    if dates:
        stats["first_commit_date"] = dates.strip().splitlines()[-1]

    numstat = scan.try_git(["log", "--numstat", "--format=", "--", rel], repo)
    if numstat is not None:
        added = deleted = 0
        for line in numstat.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0].isdigit():
                added += int(parts[0])
                deleted += int(parts[1])
        stats["added"], stats["deleted"] = added, deleted

    authors = scan.try_git(["log", "--format=%an", "--", rel], repo)
    if authors:
        stats["authors"] = Counter(
            a for a in (x.strip() for x in authors.splitlines()) if a
        ).most_common(5)

    if line_count and line_count <= MAX_BLAME_LINES:
        blame = scan.try_git(["blame", "--line-porcelain", "--", rel], repo)
        if blame:
            by_author = Counter(
                line[len("author "):] for line in blame.splitlines()
                if line.startswith("author "))
            stats["blame"] = by_author.most_common(5)
    return stats


def file_payload(repo: Path, entry: dict) -> dict:
    """Content + statistics for one tracked-file entry (see scan_files)."""
    rel = entry["path"]
    payload = {
        "path": rel,
        "lang": entry.get("lang"),
        "bytes": entry.get("bytes"),
        "binary": bool(entry.get("binary")),
        "content": None,
        "truncated": False,
        "total_lines": entry.get("lines"),
        "metrics": entry.get("metrics") or metrics.empty_metrics(),
        "stats": _stats(repo, rel, entry.get("lines")),
    }
    if payload["binary"]:
        return payload
    try:
        data = (repo / rel).read_bytes()
    except OSError:
        return payload
    if len(data) <= scan.MAX_LINE_COUNT_BYTES:
        payload["metrics"] = metrics.analyze_source(
            data.decode("utf-8", errors="replace"), payload["lang"] or "Other")
        payload["total_lines"] = payload["metrics"]["total_lines"]
    if len(data) > MAX_CONTENT_BYTES:
        data = data[:MAX_CONTENT_BYTES]
        payload["truncated"] = True
    lines = data.decode("utf-8", errors="replace").split("\n")
    if len(lines) > MAX_DISPLAY_LINES:
        lines = lines[:MAX_DISPLAY_LINES]
        payload["truncated"] = True
    payload["content"] = "\n".join(lines)
    return payload
