"""Compare the running image with the published Symphony image."""

import json
import os
import re
from urllib.request import Request, urlopen


GHCR_REPO = "kianbahasadri/symphony"
GITHUB_REPO = "KianBahasadri/clusterfork"
MAIN_TAG = "main"
TIMEOUT = 15
CHECK_SECONDS = 60
MAX_COMMITS = 5
MAX_SUBJECT_LEN = 120
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


def ghcr_json(token, url, accept=ACCEPT):
    headers = {"Authorization": f"Bearer {token}"}
    if accept:
        headers["Accept"] = accept
    request = Request(url, headers=headers)
    with urlopen(request, timeout=TIMEOUT) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise RuntimeError("GHCR returned a non-object JSON payload.")
    return payload


def ghcr_commit_for_digest(token, digest):
    if not DIGEST_RE.fullmatch(digest):
        return ""
    manifest = ghcr_json(token, f"https://ghcr.io/v2/{GHCR_REPO}/manifests/{digest}")
    manifests = manifest.get("manifests")
    if isinstance(manifests, list):
        candidates = [
            entry for entry in manifests
            if isinstance(entry, dict)
            and DIGEST_RE.fullmatch(str(entry.get("digest") or ""))
            and (entry.get("annotations") or {}).get("vnd.docker.reference.type") != "attestation-manifest"
        ]
        chosen = next(
            (
                entry for entry in candidates
                if (entry.get("platform") or {}).get("os") == "linux"
                and (entry.get("platform") or {}).get("architecture") == "amd64"
            ),
            candidates[0] if candidates else None,
        )
        if not chosen:
            return ""
        manifest = ghcr_json(token, f"https://ghcr.io/v2/{GHCR_REPO}/manifests/{chosen['digest']}")
    config = manifest.get("config")
    config_digest = config.get("digest") if isinstance(config, dict) else ""
    if not isinstance(config_digest, str) or not DIGEST_RE.fullmatch(config_digest):
        return ""
    blob = ghcr_json(
        token, f"https://ghcr.io/v2/{GHCR_REPO}/blobs/{config_digest}", accept="application/json",
    )
    env_list = (blob.get("config") or {}).get("Env")
    if not isinstance(env_list, list):
        return ""
    for item in env_list:
        if isinstance(item, str) and item.startswith("SYMPHONY_COMMIT="):
            value = item.partition("=")[2].strip().lower()
            if COMMIT_RE.fullmatch(value):
                return value
    return ""


def github_compare(running_commit, available_commit):
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/compare/{running_commit}...{available_commit}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=TIMEOUT) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub compare returned a non-object JSON payload.")
    raw_commits = payload.get("commits")
    subjects = []
    if isinstance(raw_commits, list):
        for entry in reversed(raw_commits):
            if not isinstance(entry, dict):
                continue
            lines = ((entry.get("commit") or {}).get("message") or "").splitlines()
            subject = " ".join(lines[0].split()) if lines else ""
            if subject:
                subjects.append(subject[:MAX_SUBJECT_LEN])
            if len(subjects) >= MAX_COMMITS:
                break
    total = payload.get("total_commits")
    if isinstance(total, bool) or not isinstance(total, int) or total < len(subjects):
        total = len(raw_commits) if isinstance(raw_commits, list) else len(subjects)
    return {"commitCount": total, "commits": subjects}


class UpdateChecker:
    def __init__(self):
        self.running_digest = ""
        self.running_commit = ""
        self.main_digest = ""
        self.available = False
        self.release = None
        self._compare_cache = {}

    def details(self):
        if not self.available or not self.release:
            return None
        return {**self.release, "commits": list(self.release.get("commits") or [])}

    def _resolve_release(self, token, tag, main_digest):
        try:
            available_commit = ghcr_commit_for_digest(token, main_digest)
        except (OSError, RuntimeError, TimeoutError, ValueError):
            self.release = None
            return
        if not available_commit or available_commit == tag:
            self.release = None
            return
        release = {
            "runningCommit": tag,
            "availableCommit": available_commit,
            "compareUrl": f"https://github.com/{GITHUB_REPO}/compare/{tag}...{available_commit}",
            "commitCount": None,
            "commits": [],
        }
        cache_key = (tag, available_commit)
        compared = self._compare_cache.get(cache_key)
        if compared is None:
            try:
                compared = github_compare(tag, available_commit)
                self._compare_cache[cache_key] = compared
            except (OSError, RuntimeError, TimeoutError, ValueError):
                compared = None
        if compared:
            release["commitCount"] = compared["commitCount"]
            release["commits"] = list(compared["commits"])
        self.release = release

    def check(self):
        tag = commit_tag()
        if not tag:
            self.available = False
            self.release = None
            self.main_digest = ""
            return False
        self.running_commit = tag
        token = ghcr_token()
        if not self.running_digest:
            self.running_digest = ghcr_digest(token, tag)
        main_digest = ghcr_digest(token, MAIN_TAG)
        self.available = self.running_digest != main_digest
        if not self.available:
            self.main_digest = main_digest
            self.release = None
        elif main_digest != self.main_digest:
            self.main_digest = main_digest
            self._resolve_release(token, tag, main_digest)
        return self.available
