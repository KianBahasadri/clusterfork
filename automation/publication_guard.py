"""In-memory publication checks. Never retain matched text in diagnostics."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading

from common.policy import PRIVATE_KEY_PATTERN, TOKEN_PATTERN, redact_review_text


class PublicationBlocked(RuntimeError):
    def __init__(self):
        super().__init__("Publication blocked by credential leak check")


class PublicationScanError(RuntimeError):
    def __init__(self):
        super().__init__("Publication credential scan could not read an auth cache")


class PublicationGuard:
    def __init__(self, environ=None):
        self._values = set()
        self._auth_paths = set()
        self._lock = threading.RLock()
        environ = os.environ if environ is None else environ
        for key in ("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_RUNTIME_TOKEN",
                    "GITHUB_TOKEN", "GH_TOKEN", "CLUSTERFORK_GIT_TOKEN"):
            self.remember(environ.get(key))

    def remember(self, value):
        if isinstance(value, str) and value:
            with self._lock:
                self._values.add(value)

    def remember_auth(self, auth):
        if isinstance(auth, dict):
            for key, value in auth.items():
                if key in {"access_token", "refresh_token", "account_id", "id_token", "accessToken", "refreshToken"}:
                    self.remember(value)
                self.remember_auth(value)
        elif isinstance(auth, list):
            for item in auth:
                self.remember_auth(item)

    @contextmanager
    def watch_auth(self, path):
        path = Path(path)
        with self._lock:
            self._auth_paths.add(path)
        try:
            yield
        finally:
            with self._lock:
                self._auth_paths.discard(path)

    def capture_auth(self, path):
        try:
            auth = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            raise PublicationScanError() from None
        if not isinstance(auth, dict):
            raise PublicationScanError()
        self.remember_auth(auth)

    def refresh_auth(self):
        with self._lock:
            paths = tuple(self._auth_paths)
        for path in paths:
            self.capture_auth(path)

    def scrub(self, text):
        """Refresh live credentials before retaining private command evidence."""
        self.refresh_auth()
        text = text.replace("\x00", "")
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)
        for value in values:
            text = text.replace(value, "[REDACTED]")
        return redact_review_text(text)

    def check(self, text, *, static=True):
        with self._lock:
            values = tuple(self._values)
        if (any(value in text for value in values)
                or (static and (PRIVATE_KEY_PATTERN.search(text) or TOKEN_PATTERN.search(text)))):
            raise PublicationBlocked()


def changed_lines(diff, prefix="+"):
    """Read added or removed text from one file's unified diff, excluding headers."""
    in_hunk = False
    lines = []
    for line in diff.split("\n"):
        if line.startswith("@@ "):
            in_hunk = True
        elif in_hunk and line.startswith(prefix):
            lines.append(line[1:])
    return "\n".join(lines)
