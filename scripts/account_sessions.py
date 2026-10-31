"""Dependency-free provider identity and login-chain comparisons; never log tokens."""

import base64
import hashlib
import json


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
