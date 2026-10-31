"""Provider account identities, quota profiles and fenced credential leases."""

import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from account_sessions import account_identity as _account_identity, login_fingerprints, login_id

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
    try:
        return _account_identity(auth, provider)
    except (ValueError, TypeError):
        raise WorkerError("Invalid or incomplete provider account credentials", 400) from None


def execution_profiles(registry, row):
    provider = account_provider(row.get("assigned_account_id"))
    profile = registry.agent_profiles[provider]
    return {"provider": provider, "model": profile.model, "effort": profile.effort,
            "profiles": {name: {"model": value.model, "effort": value.effort}
                         for name, value in registry.agent_profiles.items() if name in {"codex", "cursor"}}}
