"""Provider identity, login comparisons and shared local Codex history."""

import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys


def share_codex_history(home):
    """Remove discovery scope in place, preserving creator IDs and byte offsets."""
    changed = 0
    for directory in (home / "sessions", home / "archived_sessions"):
        for path in directory.rglob("*.jsonl"):
            if path.is_symlink():
                continue
            try:
                stream = path.open("r+b")
            except FileNotFoundError:
                continue
            with stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
                info = os.fstat(stream.fileno())
                header = stream.readline()
                if b'"account_scoped"' not in header:
                    continue
                try:
                    data = json.loads(header)
                except ValueError:
                    continue
                if not isinstance(data, dict) or data.get("type") != "session_meta":
                    continue
                payload = data.get("payload")
                if not isinstance(payload, dict) or payload.get("account_scoped") is not True:
                    continue
                marker = re.search(
                    rb'"account_scoped"\s*:\s*true\s*,|,\s*"account_scoped"\s*:\s*true', header)
                if marker is None:
                    raise ValueError(f"Unrecognized scoped session header: {path}")
                padding = b" " * len(marker.group())
                updated = header[:marker.start()] + padding + header[marker.end():]
                del payload["account_scoped"]
                if json.loads(updated) != data:
                    raise ValueError(f"Cannot preserve session metadata: {path}")
                record = {"path": str(path.relative_to(home)), "offset": marker.start(),
                          "original": marker.group().decode("utf-8"),
                          "atime_ns": info.st_atime_ns, "mtime_ns": info.st_mtime_ns}
                backup_path = home / "clusterfork-history-scopes.jsonl"
                descriptor = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "ab") as backup:
                    backup.write((json.dumps(record) + "\n").encode())
                    backup.flush()
                    os.fsync(backup.fileno())
                if os.pwrite(stream.fileno(), padding, marker.start()) != len(padding):
                    raise OSError(f"Incomplete session metadata update: {path}")
                if os.fstat(stream.fileno()).st_size == info.st_size:
                    os.utime(stream.fileno(), ns=(info.st_atime_ns, info.st_mtime_ns))
                os.fsync(stream.fileno())
                changed += 1
    return changed


def jwt_payload(token):
    if not isinstance(token, str):
        return {}
    try:
        payload = token.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return value if isinstance(value, dict) else {}
    except (ValueError, IndexError, TypeError):
        return {}


def account_identity(auth, provider="codex"):
    if not isinstance(auth, dict) or len(json.dumps(auth)) > 30_000:
        raise ValueError("Invalid account credentials")
    if provider == "cursor":
        if (set(auth) - {"accessToken", "refreshToken"}
                or not all(isinstance(auth.get(k), str) and auth[k] for k in ("accessToken", "refreshToken"))):
            raise ValueError("Incomplete Cursor CLI account credentials")
        subject = jwt_payload(auth["accessToken"]).get("sub")
        if not isinstance(subject, str) or not subject:
            raise ValueError("Cursor access token has no stable subject")
        identity = subject
    elif provider == "codex":
        if auth.get("auth_mode") != "chatgpt":
            raise ValueError("Only saved ChatGPT Codex accounts are supported")
        tokens = auth.get("tokens", {})
        if not isinstance(tokens, dict) or not all(isinstance(tokens.get(k), str) and tokens[k]
                                                  for k in ("account_id", "access_token", "refresh_token")):
            raise ValueError("Incomplete Codex account credentials")
        identity = tokens["account_id"]
    else:
        raise ValueError("Unsupported account provider")
    return hashlib.sha256(identity.encode()).hexdigest()


def login_fingerprints(auth, provider):
    """Remember token history and provider session claims, never account/user claims.

    Different rotated tokens alone cannot prove independent sign-ins. Registration
    additionally requires the operator's explicit fresh-login attestation.
    """
    account_identity(auth, provider)
    tokens = auth if provider == "cursor" else auth["tokens"]
    access, refresh = (("accessToken", "refreshToken") if provider == "cursor"
                       else ("access_token", "refresh_token"))
    result = {hashlib.sha256((kind + ":" + tokens[key]).encode()).hexdigest()
              for kind, key in (("access", access), ("refresh", refresh))}
    for key in (access, "id_token", refresh):
        for claim, value in jwt_payload(tokens.get(key, "")).items():
            if claim in {"sid", "session_id"} and isinstance(value, str) and value:
                result.add(hashlib.sha256(("session:" + value).encode()).hexdigest())
    return sorted(result)


def login_id(auth, provider):
    """A registration identifier retained across subsequent token rotations."""
    tokens = auth if provider == "cursor" else auth["tokens"]
    refresh = tokens["refreshToken" if provider == "cursor" else "refresh_token"]
    return hashlib.sha256(("login:" + refresh).encode()).hexdigest()


if __name__ == "__main__":
    try:
        count = share_codex_history(Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"))
    except (OSError, ValueError) as error:
        print(f"cc: cannot share local Codex history: {error}", file=sys.stderr)
        sys.exit(1)
    if count:
        print(f"cc: shared {count} saved Codex sessions across accounts", file=sys.stderr)
