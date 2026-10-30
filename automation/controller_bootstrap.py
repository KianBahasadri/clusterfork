#!/usr/bin/env python3
"""Authorize an immutable Actions role before accessing its target repository."""

import base64
import json
import os
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).parent))
from worker import Broker
from readiness_policy import PROTOCOL


def main():
    revision = os.environ["WORKER_REVISION"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("A full approved runtime revision is required")
    request_url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]
    request = Request(request_url + ("&" if "?" in request_url else "?") + "audience=clusterfork-worker",
                      headers={"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
    with urlopen(request, timeout=20) as response:
        payload = json.load(response)["value"].split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    if claims.get("job_workflow_sha") != revision:
        raise ValueError("Runtime checkout does not match the signed job revision")
    broker = Broker(os.environ["CLUSTERFORK_BROKER_URL"], os.environ["DISPATCH_KEY"])
    config = broker.call("start", protocol=PROTOCOL)
    if config.get("serial_admission") is not True:
        raise ValueError("Controller did not grant repository admission")
    preflight = os.environ.get("PREFLIGHT_ONLY") == "true"
    if config.get("account_selection") is not preflight:
        raise ValueError("Signed job role does not match the bootstrap mode")
    if preflight:
        return  # Pool inspection has no target checkout or publication permission.
    token = broker.call("checkout")["token"]
    print("::add-mask::" + token)
    repository, context_sha = config["repository"], config["binding"]["context_sha"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not re.fullmatch(r"[0-9a-f]{40}", context_sha):
        raise ValueError("Invalid target checkout identity")
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"repository={repository}\ncontext_sha={context_sha}\ntoken={token}\n")


if __name__ == "__main__":
    main()
