"""Cache storage for codeview: per-section JSON files, fingerprints, atomic IO.

Layout under <repo>/.codeview/:
  cache/meta.json        scan config + freshness banner data
  cache/history.json     cumulative LOC history aggregates
  cache/files.json       HEAD snapshot file index
  cache/deps.json        dependency manifest summary
  cache/fingerprints.json per-section input fingerprints
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


def fingerprint(inputs: list[str]) -> str:
    h = hashlib.sha256()
    for s in inputs:
        h.update(s.encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def section_path(codeview_dir: Path, section: str) -> Path:
    return codeview_dir / "cache" / f"{section}.json"


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
        # Corrupt/partially-written cache should degrade to empty, not crash boot.
    except (OSError, ValueError):
        return None


def write_json_atomic(path: Path, payload: dict, mode: int = 0o600) -> None:
    """Pid-suffixed tmp + rename, matching scripts/rotate_auth.py conventions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent),
        prefix=f".{path.name}.tmp.", delete=False,
    )
    tmp_name = fd.name
    try:
        with fd:
            json.dump(payload, fd, ensure_ascii=False, sort_keys=True)
            fd.flush()
            os.fsync(fd.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        # os.replace consumed the tmp; only failure paths leave one behind.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
