"""Compare the running image with the published Symphony image."""

import json
import os
import re
from urllib.request import Request, urlopen


GHCR_REPO = "kianbahasadri/symphony"
MAIN_TAG = "main"
TIMEOUT = 15
CHECK_SECONDS = 60
ACCEPT = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}\Z")


def commit_tag():
    value = os.environ.get("SYMPHONY_COMMIT", "").strip().lower()
    return value if COMMIT_RE.fullmatch(value) else ""


def ghcr_token():
    url = f"https://ghcr.io/token?scope=repository:{GHCR_REPO}:pull"
    with urlopen(url, timeout=TIMEOUT) as response:
        payload = json.loads(response.read())
    token = payload.get("token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise RuntimeError("GHCR returned an empty pull token.")
    return token


def ghcr_digest(token, reference):
    request = Request(
        f"https://ghcr.io/v2/{GHCR_REPO}/manifests/{reference}",
        method="HEAD",
        headers={"Authorization": f"Bearer {token}", "Accept": ACCEPT},
    )
    with urlopen(request, timeout=TIMEOUT) as response:
        digest = response.headers.get("Docker-Content-Digest", "")
    if not DIGEST_RE.fullmatch(digest):
        raise RuntimeError(f"Invalid GHCR digest for {reference}: {digest!r}")
    return digest


class UpdateChecker:
    def __init__(self):
        self.running_digest = ""
        self.available = False

    def check(self):
        tag = commit_tag()
        if not tag:
            self.available = False
            return False
        token = ghcr_token()
        if not self.running_digest:
            self.running_digest = ghcr_digest(token, tag)
        self.available = self.running_digest != ghcr_digest(token, MAIN_TAG)
        return self.available
