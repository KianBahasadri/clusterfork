"""Provider account identities, quota profiles and fenced credential leases."""

import base64
import hashlib
import json
import re

AUDIENCE="clusterfork-worker"
ISSUER="https://token.actions.githubusercontent.com"
LEASE_SECONDS=120
WORKER_REPOSITORY="KianBahasadri/clusterfork"


class WorkerError(RuntimeError):
    def __init__(self, message, status=409, *, reason_code=None):
        super().__init__(message)
        self.status = status
        self.reason_code = reason_code


def account_provider(identity):
    return "cursor" if isinstance(identity, str) and identity.startswith("cursor:") else "codex"


def valid_account_id(identity):
    return isinstance(identity, str) and re.fullmatch(r"(?:cursor:)?[0-9a-f]{64}", identity) is not None


def account_identity(auth, provider="codex"):
    if provider == "cursor":
        if (not isinstance(auth, dict) or set(auth) - {"accessToken", "refreshToken"}
                or not all(isinstance(auth.get(k), str) and auth[k] for k in ("accessToken", "refreshToken"))
                or len(json.dumps(auth)) > 30_000):
            raise WorkerError("Incomplete Cursor CLI account credentials", 400)
        try:
            payload = auth["accessToken"].split(".")[1]
            subject = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"]
            if not isinstance(subject, str) or not subject:
                raise ValueError()
        except (ValueError, KeyError, IndexError, TypeError):
            raise WorkerError("Cursor access token has no stable subject", 400) from None
        # This decodes identity only. Live provider authentication is still required.
        return hashlib.sha256(subject.encode()).hexdigest()
    if provider != "codex":
        raise WorkerError("Unsupported account provider", 400)
    if not isinstance(auth, dict) or auth.get("auth_mode") != "chatgpt":
        raise WorkerError("Only saved ChatGPT Codex accounts are supported", 400)
    tokens = auth.get("tokens", {})
    if not all(isinstance(tokens.get(k), str) and tokens[k] for k in
               ("account_id", "access_token", "refresh_token")):
        raise WorkerError("Incomplete Codex account credentials", 400)
    if len(json.dumps(auth)) > 30_000:
        raise WorkerError("Account credentials are too large", 400)
    return hashlib.sha256(tokens["account_id"].encode()).hexdigest()


def execution_profiles(registry, row):
    provider = account_provider(row.get("assigned_account_id"))
    profile = registry.agent_profiles[provider]
    return {"provider": provider, "model": profile.model, "effort": profile.effort,
            "profiles": {name: {"model": value.model, "effort": value.effort}
                         for name, value in registry.agent_profiles.items() if name in {"codex", "cursor"}}}
