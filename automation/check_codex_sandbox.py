#!/usr/bin/env python3
"""Exercise the worker's native Codex sandbox without an account or model call."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile


PROBE = """
import errno
from pathlib import Path
import socket

assert Path('read-probe.txt').read_text() == 'clusterfork sandbox read probe'
for target in (Path('write-probe.txt'), Path('../outside-probe.txt')):
    try:
        target.write_text('must be denied')
    except OSError as exc:
        assert exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS), exc
    else:
        raise SystemExit('Read-only sandbox allowed a filesystem write')
try:
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
except PermissionError:
    pass
else:
    connection.close()
    raise SystemExit('Read-only sandbox allowed a network socket')
print('Codex sandbox verified: reads succeed; filesystem writes and networking are denied.')
"""


def main():
    with tempfile.TemporaryDirectory(prefix="clusterfork-sandbox-") as directory:
        root = Path(directory)
        repository, home = root / "repository", root / "codex"
        repository.mkdir()
        home.mkdir()
        (repository / "read-probe.txt").write_text("clusterfork sandbox read probe")
        outside = root / "outside-probe.txt"
        outside.write_text("unchanged")
        # No account cache or CI credentials are needed to exercise the OS sandbox.
        env = {key: value for key, value in os.environ.items()
               if key in {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR"}}
        env["CODEX_HOME"] = str(home)
        subprocess.run(["codex", "sandbox", "-c", 'sandbox_mode="read-only"',
                        "--", sys.executable, "-c", PROBE],
                       cwd=repository, env=env, check=True, timeout=30)
        if outside.read_text() != "unchanged" or (repository / "write-probe.txt").exists():
            raise RuntimeError("Sandbox probe changed protected files")


if __name__ == "__main__":
    main()
