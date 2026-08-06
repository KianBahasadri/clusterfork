#!/usr/bin/env python3
"""Probe whether OpenCode Go honours a client's reasoning-effort setting.

Two routes, two clients:

- `--route messages` (default): what Claude Code sends — `output_config.effort`
  on /v1/messages. Control is `thinking: {"type": "disabled"}`, which must come
  back with zero thinking.
- `--route chat`: what OpenCode itself sends — `reasoning_effort` on
  /v1/chat/completions. Control is `reasoning_effort: "none"`, same enum, which
  must come back with zero reasoning_content. This is the first-party control:
  the gateway's own client uses this route and field, so if a graded budget
  exists anywhere on this gateway it has to be here.

Both gateways validate the enum — a bogus value is a 400 — so the field is
clearly parsed, which makes "is it honoured?" impossible to answer from status
codes. This script answers it by measurement: same prompt at each effort level,
N samples, comparing how much thinking the model emits. Reasoning volume is
noisy, so if the control does not separate, the numbers mean nothing and the
run fails.

    python scripts/opencode_go_effort_probe.py                       # Claude Code route
    python scripts/opencode_go_effort_probe.py --route chat          # OpenCode route
    python scripts/opencode_go_effort_probe.py qwen3.8-max -n 20     # more samples

Exits non-zero if the control fails. See docs/opencode-go.md for the results
this was written to support, and re-run it before trusting them.
"""
import argparse
import json
import math
import os
import statistics
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = os.environ.get("OPENCODE_GO_BASE_URL", "https://opencode.ai/zen/go")
AUTH = os.path.expanduser("~/.local/share/opencode/auth.json")

# Cloudflare 403s a default urllib user agent with a bare `error code: 1010`
# body, before the request ever reaches the gateway. Look like a real client.
MESSAGES_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "anthropic-version": "2023-06-01",
    "anthropic-beta": "claude-code-20250219,thinking-token-count-2026-05-13,effort-2025-11-24",
    "user-agent": "claude-cli/2.1.223 (external, cli)",
}

CHAT_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "user-agent": "opencode/0.16.0",
}

LEVELS = ["low", "medium", "high", "xhigh", "max"]
# The chat enum validates two more values: `minimal` (sampled here) and `none`
# (the control — it must return zero reasoning for the run to mean anything).
CHAT_LEVELS = ["minimal"] + LEVELS

# Exactly what Claude Code sends alongside the effort field.
THINKING = {"type": "adaptive", "display": "omitted"}

# Needs enough rope for effort to show up as a difference: a prompt with a real
# trap in it, where more deliberation would plausibly buy a better answer.
PROMPT = (
    "Three friends check into a hotel room costing $30 and pay $10 each. The "
    "clerk realises the room is only $25 and sends a bellhop back with $5. The "
    "bellhop keeps $2 and returns $1 to each friend. Each friend paid $9, "
    "totalling $27, plus the bellhop's $2 is $29. Where is the missing dollar? "
    "Reason carefully and completely before answering."
)


def api_key():
    key = os.environ.get("OPENCODE_API_KEY")
    if key:
        return key
    with open(AUTH) as fh:
        return json.load(fh)["opencode-go"]["key"]


def post_json(key, route, body):
    if route == "messages":
        url = f"{BASE}/v1/messages?beta=true"
        headers = dict(MESSAGES_HEADERS, **{"x-api-key": key})
    else:
        url = f"{BASE}/v1/chat/completions"
        headers = dict(CHAT_HEADERS, authorization=f"Bearer {key}")

    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.read().decode()[:120]}"}
    except Exception as exc:  # noqa: BLE001 - probe reports, never raises
        return {"error": repr(exc)}


def sample(key, model, route, effort, thinking=THINKING):
    """One non-streaming turn. Returns reasoning volume, or an error."""
    body = {
        "model": model,
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": PROMPT}],
    }
    if route == "messages":
        body["thinking"] = thinking
        if effort is not None:
            body["output_config"] = {"effort": effort}
    elif effort is not None:
        body["reasoning_effort"] = effort

    data = post_json(key, route, body)
    if "error" in data:
        return data

    if route == "messages":
        blocks = data.get("content", [])
        thought = "".join(b.get("thinking", "") for b in blocks if b.get("type") == "thinking")
        tokens = (data.get("usage") or {}).get("output_tokens")
    else:
        choices = data.get("choices") or []
        if not choices:
            return {"error": f"no choices in response: {json.dumps(data)[:120]}"}
        message = choices[0].get("message") or {}
        thought = message.get("reasoning_content") or ""
        tokens = ((data.get("usage") or {}).get("completion_tokens_details") or {}) \
            .get("reasoning_tokens")
    return {"chars": len(thought), "tokens": tokens}


def summarise(label, results, tok_label):
    """Print one row; return the thinking lengths, or [] if every sample failed."""
    ok = [r for r in results if "chars" in r]
    bad = [r for r in results if "error" in r]
    if not ok:
        print(f"  {label:<22} all {len(bad)} failed: {bad[0]['error']}")
        return []
    chars = [r["chars"] for r in ok]
    tokens = [r["tokens"] for r in ok if r["tokens"] is not None]
    note = f"   ({len(bad)} failed)" if bad else ""
    print(f"  {label:<22} n={len(ok):<3} thinking chars med={statistics.median(chars):>6.0f} "
          f"min={min(chars):>5} max={max(chars):>6}   {tok_label} med="
          f"{statistics.median(tokens) if tokens else 0:>5.0f}{note}")
    return chars


def mann_whitney_p(a, b):
    """Two-sided p for "a and b come from the same distribution", normal approx.

    Medians alone cannot carry this call: reasoning volume is heavy-tailed
    enough that levels reorder run to run on noise. A rank test on the raw
    samples is what separates "no effect" from "small sample".
    """
    combined = sorted((v, i) for i, group in enumerate((a, b)) for v in group)
    ranks, pos = {}, 0
    while pos < len(combined):
        end = pos
        while end + 1 < len(combined) and combined[end + 1][0] == combined[pos][0]:
            end += 1
        shared = (pos + end) / 2 + 1
        for idx in range(pos, end + 1):
            ranks.setdefault(combined[idx][0], shared)
        pos = end + 1

    n1, n2 = len(a), len(b)
    u = sum(ranks[v] for v in a) - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    sigma = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
    if sigma == 0:
        return 1.0
    return math.erfc(abs(u - mu) / sigma / math.sqrt(2))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("model", nargs="?", default="deepseek-v4-flash")
    ap.add_argument("--route", choices=["messages", "chat"], default="messages",
                    help="messages = what Claude Code sends; chat = what OpenCode sends")
    ap.add_argument("-n", "--samples", type=int, default=12, help="samples per level")
    ap.add_argument("-j", "--jobs", type=int, default=12, help="parallel requests")
    args = ap.parse_args()

    chat = args.route == "chat"
    levels = CHAT_LEVELS if chat else LEVELS
    tok_label = "rsn_tok" if chat else "out_tok"
    control_label = "effort=none (control)" if chat else "thinking=off (control)"
    endpoint = "/v1/chat/completions" if chat else "/v1/messages"

    key = api_key()
    print(f"{args.model} over {endpoint} — {args.samples} samples per level\n")

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        # Submit round-robin, not level-by-level: with n == jobs, submitting all
        # of one level first runs each level as its own time slice, and a
        # mid-run upstream change then shows up as a spurious effort effect.
        pending = {lv: [] for lv in levels}
        for _ in range(args.samples):
            for lv in levels:
                pending[lv].append(pool.submit(sample, key, args.model, args.route, lv))
        if chat:
            control_futs = [pool.submit(sample, key, args.model, args.route, "none")
                            for _ in range(max(4, args.samples // 3))]
        else:
            control_futs = [pool.submit(sample, key, args.model, args.route, None,
                                        {"type": "disabled"})
                            for _ in range(max(4, args.samples // 3))]
        bogus = pool.submit(sample, key, args.model, args.route, "banana")
        samples = {lv: summarise(f"effort={lv}", [f.result() for f in futs], tok_label)
                   for lv, futs in pending.items()}
        control = summarise(control_label, [f.result() for f in control_futs], tok_label)

    print(f"\n  bogus effort value     "
          f"{bogus.result().get('error', 'ACCEPTED — enum no longer validated')}")

    if not control or max(control) > 0:
        print(f"\ncontrol failed: {control_label.split(' (')[0]} still returned thinking, "
              "so the harness cannot detect a real change. Numbers above mean nothing.",
              file=sys.stderr)
        return 1
    print(f"\ncontrol separated ({len(control)}/{len(control)} at zero), "
          "so the measurement can see a real change.")

    # minimal can be quasi-off, so the extremes worth testing are low vs max —
    # if the graded budget exists anywhere, it has to separate those.
    lo, hi = samples.get("low", []), samples.get("max", [])
    if not lo or not hi:
        print("could not compare the extremes", file=sys.stderr)
        return 1

    p = mann_whitney_p(lo, hi)
    verdict = ("effort is now doing something — re-measure and update docs/opencode-go.md"
               if p < 0.05 else "consistent with effort being ignored")
    print(f"low vs max: p={p:.2f} — {verdict}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
