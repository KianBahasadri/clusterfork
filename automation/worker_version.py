"""Read a pinned worker's contract without changing the running broker imports."""

from pathlib import Path
import re
import subprocess
import sys
import tempfile

CONTRACT_FILES = ("readiness_policy.py", "worker_reporting.py", "quota_selection.py", "issue_images.py")


def protocol_at_revision(revision, read_file):
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("worker revision must be a full commit SHA")
    with tempfile.TemporaryDirectory() as directory:
        for name in CONTRACT_FILES:
            (Path(directory) / name).write_text(read_file(revision, name))
        result = subprocess.run([sys.executable, "-I", "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); from readiness_policy import PROTOCOL; print(PROTOCOL)", directory],
            capture_output=True, text=True, timeout=20)
        if result.returncode or not re.fullmatch(r"readiness-v1:[0-9a-f]{64}\n?", result.stdout):
            raise ValueError("pinned worker contract could not be verified")
        return result.stdout.strip()
