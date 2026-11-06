"""Read provider quota without activating profiles, refreshing logins or running models."""

from __future__ import annotations

from datetime import datetime
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from common.selection import QuotaWindow, finite_number


class UsageError(Exception):
    """A bounded, credential-free explanation suitable for the rotator's output."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def timestamp(value):
    if value is None:
        return None
    if finite_number(value):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.timestamp()
    raise UsageError("invalid quota reset time")


def request_json(url, token, *, body=None, headers=None):
    if not isinstance(token, str) or not token:
        raise UsageError("missing saved access token")
    request = Request(url, data=None if body is None else json.dumps(body).encode(), headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json",
        "Content-Type": "application/json", "User-Agent": "clusterfork-account-picker",
        **(headers or {}),
    })
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            raise UsageError("quota response is too large")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise UsageError("invalid quota response")
        return result
    except HTTPError as error:
        status = error.code
        error.close()
        if status in {401, 403}:
            raise UsageError("saved login expired or was rejected", status) from None
        raise UsageError(f"quota request failed (HTTP {status})", status) from None
    except (OSError, URLError, ValueError):
        raise UsageError("quota request unavailable") from None


def codex(auth, model, *, allow_exhausted=False):
    tokens = auth["tokens"]
    data = request_json("https://chatgpt.com/backend-api/wham/usage", tokens["access_token"],
                        headers={"ChatGPT-Account-Id": tokens["account_id"]})
    buckets = [("codex", data["rate_limit"])]
    for extra in data.get("additional_rate_limits") or []:
        if model in (extra.get("model"), extra.get("limit_id"), extra.get("limit_name"), extra.get("normal_model_slug")):
            buckets.append((model, extra["rate_limit"]))
    windows = []
    for name, bucket in buckets:
        exhausted = bucket.get("allowed") is False or bucket.get("limit_reached") is True
        if exhausted and not allow_exhausted:
            raise UsageError("account quota is exhausted")
        for key in ("primary_window", "secondary_window"):
            window = bucket.get(key)
            if window is not None:
                seconds = window.get("limit_window_seconds")
                windows.append(QuotaWindow(window.get("used_percent"), timestamp(window.get("reset_at")),
                                           f"{name}/{key}", seconds / 60 if finite_number(seconds) else None))
        if exhausted:
            # Local rotation retains this reading for its last resort. A provider
            # exhaustion flag takes precedence over any positive window headroom.
            windows.append(QuotaWindow(100, name=f"{name}/exhausted"))
    return tuple(windows)


def claude(auth, model):
    data = request_json("https://api.anthropic.com/api/oauth/usage", auth["claudeAiOauth"]["accessToken"],
                        headers={"anthropic-beta": "oauth-2025-04-20"})
    windows = []
    for name, window in data.items():
        if (name == "five_hour" or name.startswith("seven_day")) and window is not None:
            windows.append(QuotaWindow(window.get("utilization"), timestamp(window.get("resets_at")), name,
                                       300 if name == "five_hour" else 10080))
    return tuple(windows)


def cursor(auth, model):
    data = request_json("https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage",
                        auth["accessToken"], body={}, headers={"Connect-Protocol-Version": "1"})
    if data.get("enabled") is not True:
        raise UsageError("included usage is unavailable")
    plan = data.get("planUsage") or {}
    bucket = "auto" if model in data.get("autoBucketModels", []) else "api"
    start, reset = float(data["billingCycleStart"]) / 1000, float(data["billingCycleEnd"]) / 1000
    if not finite_number(start) or not finite_number(reset) or reset <= start or start > time.time():
        raise UsageError("invalid billing period")
    windows = [QuotaWindow(plan.get(bucket + "PercentUsed"), reset, "cursor/" + bucket, (reset - start) / 60)]
    if plan.get("totalPercentUsed") is not None:
        windows.append(QuotaWindow(plan["totalPercentUsed"], reset, "cursor/included", (reset - start) / 60))
    return tuple(windows)


def grok_login(auth):
    return next(row for row in auth.values() if isinstance(row, dict) and row.get("key"))


def grok(auth, model):
    url, token = "https://cli-chat-proxy.grok.com/v1/billing", grok_login(auth)["key"]
    headers = {"X-XAI-Token-Auth": "xai-grok-cli"}
    try:
        data = request_json(url + "?format=credits", token, headers=headers)
    except UsageError as error:
        if error.status not in {400, 404}:
            raise
        data = request_json(url, token, headers=headers)
    config = data["config"]
    used, limit = config.get("used"), config.get("monthlyLimit")
    used = used.get("val") if isinstance(used, dict) else used
    limit = limit.get("val") if isinstance(limit, dict) else limit
    percent = used / limit * 100 if finite_number(used) and finite_number(limit) and limit > 0 else config.get("creditUsagePercent")
    period = config.get("currentPeriod") or {}
    start = timestamp(config.get("billingPeriodStart") or period.get("start"))
    reset = timestamp(config.get("billingPeriodEnd") or period.get("end"))
    duration = (reset - start) / 60 if start is not None and reset is not None and reset > start else None
    return (QuotaWindow(percent, reset, "grok/billing", duration),)


def opencode(auth, model):
    data = request_json("https://opencode.ai/inference/go/v1/usage", auth["opencode-go"]["key"])
    windows = []
    for name, duration in (("rolling", 300), ("weekly", 10080), ("monthly", None)):
        window = data["usage"][name]
        if window.get("status") != "ok":
            raise UsageError("Go quota is unavailable or exhausted")
        windows.append(QuotaWindow(window.get("percent"), timestamp(window.get("resetsAt")),
                                   "opencode/" + name, duration))
    return tuple(windows)


def antigravity(auth, model):
    token = auth["token"]["access_token"]
    endpoint = "https://daily-cloudcode-pa.googleapis.com/v1internal:"
    headers = {"User-Agent": "antigravity/cli/1.4.2"}
    account = request_json(endpoint + "loadCodeAssist", token,
                           body={"metadata": {"ideType": "IDE_UNSPECIFIED", "platform": "LINUX_AMD64", "pluginType": "GEMINI"}},
                           headers=headers)
    project = account["cloudaicompanionProject"]
    if not project:
        raise UsageError("missing Code Assist project")
    try:
        data = request_json(endpoint + "retrieveUserQuotaSummary", token, body={"project": project}, headers=headers)
    except UsageError as error:
        if error.status not in {400, 404, 405, 501}:
            raise
        data = {}
    groups = data.get("groups")
    if groups:
        buckets = [bucket for group in groups for bucket in group["buckets"]]
    else:
        data = request_json(endpoint + "retrieveUserQuota", token, body={"project": project}, headers=headers)
        buckets = [bucket for bucket in data["buckets"] if bucket.get("tokenType", "REQUESTS") in {"REQUESTS", "WTUS"}]
    windows = []
    for bucket in buckets:
        remaining = bucket.get("remainingFraction")
        used = (1 - remaining) * 100 if finite_number(remaining) and 0 <= remaining <= 1 else None
        windows.append(QuotaWindow(used, timestamp(bucket.get("resetTime")),
                                   str(bucket.get("bucketId") or bucket.get("modelId") or "quota")))
    return tuple(windows)


READERS = {"codex": codex, "claude": claude, "cursor": cursor, "grok": grok,
           "opencode": opencode, "antigravity": antigravity}
