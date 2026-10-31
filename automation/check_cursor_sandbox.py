#!/usr/bin/env python3
"""Verify Cursor readiness's filesystem boundary without provider credentials."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parent))
from cursor_runtime import environment, read_only_command


def main():
    with tempfile.TemporaryDirectory(prefix="clusterfork-cursor-sandbox-") as directory:
        root = Path(directory)
        home, workspace = root / "home", root / "workspace"
        home.mkdir()
        workspace.mkdir()
        target = workspace / "read-only.txt"
        target.write_text("original")
        (workspace / ".cursor").mkdir()
        for name in ("hooks.json", "mcp.json", "cli.json"):
            (workspace / ".cursor" / name).write_text('{"must_not_run": true}')
        check = """import errno, pathlib, sys
home, target = map(pathlib.Path, sys.argv[1:])
assert target.read_text() == 'original'
for name in ('hooks.json', 'mcp.json', 'cli.json'):
    assert (target.parent / '.cursor' / name).read_text() == '{}'
for path in (target, target.parent / 'new.txt'):
    try:
        path.write_text('forbidden')
    except OSError as exc:
        assert exc.errno in (errno.EROFS, errno.EACCES), exc.errno
    else:
        raise SystemExit('Read-only filesystem enforcement failed')
(home / 'private-scratch.txt').write_text('allowed')
"""
        subprocess.run(read_only_command(home, workspace, [sys.executable, "-c", check, str(home), str(target)]),
                       cwd=workspace, env=environment(home, os.environ), check=True, timeout=30)
        assert target.read_text() == "original"
    print("Cursor readiness filesystem boundary verified without credentials")


if __name__ == "__main__":
    main()
