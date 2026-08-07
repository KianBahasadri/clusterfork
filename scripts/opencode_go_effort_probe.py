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
- `--route responses`: what Codex sends — `reasoning.effort` on /v1/responses.
  Control is `effort: "none"` again. Reasoning text is not returned on this
  route (the reasoning item's summary is empty), so the signal is the
  upstream's own `output_tokens_details.reasoning_tokens` counter.

The chat and responses routes validate the enum gateway-side — a bogus value is
a 400 — but parsing says nothing about effect, and /v1/messages no longer
validates at all. This script answers it by measurement: same prompt at each
effort level, N samples, comparing how much thinking the model emits. Reasoning
volume is noisy, so if the control does not separate, the numbers mean nothing
and the run fails.

The measurement is prompt-sensitive: `hotel` (the missing-dollar riddle) is a
memorised classic that models recite with fixed-length reasoning, flatlining
even where a ladder exists — deepseek-v4-flash reads "ignored" with it and
"works" (p ≈ 0.00) with `absproof`. Prefer `--prompt absproof` or `stack`.

    python scripts/opencode_go_effort_probe.py                       # Claude Code route
    python scripts/opencode_go_effort_probe.py --route chat          # OpenCode route
    python scripts/opencode_go_effort_probe.py --route responses gpt-5.6-luna  # Codex
    python scripts/opencode_go_effort_probe.py --route chat --prompt absproof glm-5.1
    python scripts/opencode_go_effort_probe.py --route chat --prompt stack \
        --max-tokens 384000 --timeout 3600 deepseek-v4-flash
    python scripts/opencode_go_effort_probe.py qwen3.8-max -n 20     # more samples

The default 8192 `max_tokens` is itself a confound: deepseek-v4-flash on
`stack` flatlines every level against it and shows a clear ladder once the
cap is raised to the model's advertised 384000. Pass `--timeout` high enough
to match — a filled high cap takes minutes per request.

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

RESPONSES_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json",
    "user-agent": "codex_cli_rs/0.146.0",
}

LEVELS = ["low", "medium", "high", "xhigh", "max"]
# The OpenAI-route enums validate two more values: `minimal` (sampled here) and
# `none` (the control — it must return zero reasoning for the run to mean
# anything).
CHAT_LEVELS = ["minimal"] + LEVELS

# Exactly what Claude Code sends alongside the effort field.
THINKING = {"type": "adaptive", "display": "omitted"}

# Needs enough rope for effort to show up as a difference: a prompt with a real
# trap in it, where more deliberation would plausibly buy a better answer.
PROMPTS = {
    # A classic riddle — but a memorised one, which is its weakness: a model
    # can answer from cache with the same canned reasoning at every level.
    "hotel": (
        "Three friends check into a hotel room costing $30 and pay $10 each. The "
        "clerk realises the room is only $25 and sends a bellhop back with $5. The "
        "bellhop keeps $2 and returns $1 to each friend. Each friend paid $9, "
        "totalling $27, plus the bellhop's $2 is $29. Where is the missing dollar? "
        "Reason carefully and completely before answering."
    ),
    # The proposition is actually false as stated (a+|a|=0 holds at a=0), so a
    # careful model has to notice the edge case — deliberation buys correctness.
    "absproof": (
        "If a+|a|=0, try to prove that a<0.\n\n"
        "Step 1: List the conditions and questions in the original proposition.\n\n"
        "Step 2: Merge the conditions listed in Step 1 into one. Define it as wj.\n\n"
        "Step 3: Let us think it step by step. Please consider all possibilities. "
        "If the intersection between wj (defined in Step 2) and the negation of "
        "the question is not empty at least in one possibility, the original "
        "proposition is false. Otherwise, the original proposition is true."
    ),
    # Open-ended physical reasoning; no memorised answer to fall back on.
    "stack": (
        "Here we have a book, 9 eggs, a laptop, a bottle and a nail. Please tell "
        "me how to stack them onto each other in a stable manner."
    ),
}
PROMPT = PROMPTS["hotel"]


def api_key():
    key = os.environ.get("OPENCODE_API_KEY")
    if key:
        return key
    with open(AUTH) as fh:
        return json.load(fh)["opencode-go"]["key"]


def post_json(key, route, body, timeout=240):
    if route == "messages":
        url = f"{BASE}/v1/messages?beta=true"
        headers = dict(MESSAGES_HEADERS, **{"x-api-key": key})
    elif route == "chat":
        url = f"{BASE}/v1/chat/completions"
        headers = dict(CHAT_HEADERS, authorization=f"Bearer {key}")
    else:
        url = f"{BASE}/v1/responses"
        headers = dict(RESPONSES_HEADERS, authorization=f"Bearer {key}")

    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.read().decode()[:120]}"}
    except Exception as exc:  # noqa: BLE001 - probe reports, never raises
        return {"error": repr(exc)}


def sample(key, model, route, effort, thinking=THINKING, prompt=PROMPT,
           max_tokens=8192, timeout=240):
    """One non-streaming turn. Returns reasoning volume, or an error.

    Volume is measured in whatever the route exposes: thinking-text characters
    on messages and chat, the upstream's reasoning_tokens counter on responses
    (where reasoning text is not returned at all).
    """
    if route == "responses":
        body = {
            "model": model,
            "input": [{"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": prompt}]}],
            # Codex itself never sends this, but the ceiling confound on chat
            # was real — set it so a flat ladder is not a silent default cap.
            "max_output_tokens": max_tokens,
        }
        if effort is not None:
            body["reasoning"] = {"effort": effort}
    else:
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if route == "messages":
            body["thinking"] = thinking
            if effort is not None:
                body["output_config"] = {"effort": effort}
        elif effort is not None:
            body["reasoning_effort"] = effort

    data = post_json(key, route, body, timeout=timeout)
    # NB: a *successful* /v1/responses payload carries "error": null, so this
    # must be a value check, not a key-presence check.
    if data.get("error") is not None:
        return data

    if route == "messages":
        blocks = data.get("content", [])
        thought = "".join(b.get("thinking", "") for b in blocks if b.get("type") == "thinking")
        return {"volume": len(thought), "tokens": (data.get("usage") or {}).get("output_tokens")}

    if route == "chat":
        choices = data.get("choices") or []
        if not choices:
            return {"error": f"no choices in response: {json.dumps(data)[:120]}"}
        message = choices[0].get("message") or {}
        thought = message.get("reasoning_content") or ""
        tokens = ((data.get("usage") or {}).get("completion_tokens_details") or {}) \
            .get("reasoning_tokens")
        return {"volume": len(thought), "tokens": tokens}

    usage = data.get("usage") or {}
    volume = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
    if volume is None:
        return {"error": f"no reasoning_tokens in response: {json.dumps(data)[:120]}"}
    if data.get("status") and data["status"] != "completed":
        return {"error": f"status={data['status']}"}
    return {"volume": volume, "tokens": usage.get("output_tokens")}


def summarise(label, results, vol_label, tok_label):
    """Print one row; return the volumes, or [] if every sample failed."""
    ok = [r for r in results if "volume" in r]
    bad = [r for r in results if r.get("error") is not None]
    if not ok:
        print(f"  {label:<22} all {len(bad)} failed: {bad[0]['error']}")
        return []
    volumes = [r["volume"] for r in ok]
    tokens = [r["tokens"] for r in ok if r["tokens"] is not None]
    note = f"   ({len(bad)} failed)" if bad else ""
    print(f"  {label:<22} n={len(ok):<3} {vol_label} med={statistics.median(volumes):>6.0f} "
          f"min={min(volumes):>5} max={max(volumes):>6}   {tok_label} med="
          f"{statistics.median(tokens) if tokens else 0:>5.0f}{note}")
    return volumes


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
    ap.add_argument("--route", choices=["messages", "chat", "responses"],
                    default="messages",
                    help="messages = what Claude Code sends; chat = what OpenCode "
                         "sends; responses = what Codex sends")
    ap.add_argument("-n", "--samples", type=int, default=12, help="samples per level")
    ap.add_argument("-j", "--jobs", type=int, default=12, help="parallel requests")
    ap.add_argument("--prompt", choices=sorted(PROMPTS), default="hotel",
                    help="which built-in prompt to sample with")
    ap.add_argument("--max-tokens", type=int, default=8192,
                    help="output cap: max_tokens on messages/chat, "
                         "max_output_tokens on responses (default 8192; "
                         "deepseek flash advertises 384000)")
    ap.add_argument("--timeout", type=int, default=240,
                    help="per-request HTTP timeout in seconds")
    args = ap.parse_args()

    levels = LEVELS if args.route == "messages" else CHAT_LEVELS
    vol_label = "reasoning tok" if args.route == "responses" else "thinking chars"
    tok_label = "rsn_tok" if args.route == "chat" else "out_tok"
    control_label = ("thinking=off (control)" if args.route == "messages"
                     else "effort=none (control)")
    endpoint = {"messages": "/v1/messages", "chat": "/v1/chat/completions",
                "responses": "/v1/responses"}[args.route]

    key = api_key()
    prompt = PROMPTS[args.prompt]
    sample_kw = {"prompt": prompt, "max_tokens": args.max_tokens,
                 "timeout": args.timeout}
    print(f"{args.model} over {endpoint} — {args.samples} samples per level, "
          f"prompt={args.prompt}, max_tokens={args.max_tokens}\n")

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        # Submit round-robin, not level-by-level: with n == jobs, submitting all
        # of one level first runs each level as its own time slice, and a
        # mid-run upstream change then shows up as a spurious effort effect.
        pending = {lv: [] for lv in levels}
        for _ in range(args.samples):
            for lv in levels:
                pending[lv].append(pool.submit(sample, key, args.model, args.route,
                                               lv, **sample_kw))
        if args.route == "messages":
            control_futs = [pool.submit(sample, key, args.model, args.route, None,
                                        {"type": "disabled"}, **sample_kw)
                            for _ in range(max(4, args.samples // 3))]
        else:
            control_futs = [pool.submit(sample, key, args.model, args.route, "none",
                                        **sample_kw)
                            for _ in range(max(4, args.samples // 3))]
        bogus = pool.submit(sample, key, args.model, args.route, "banana",
                            **sample_kw)
        samples = {lv: summarise(f"effort={lv}", [f.result() for f in futs],
                                 vol_label, tok_label)
                   for lv, futs in pending.items()}
        control = summarise(control_label, [f.result() for f in control_futs],
                            vol_label, tok_label)

    print(f"\n  bogus effort value     "
          f"{bogus.result().get('error', 'ACCEPTED — enum no longer validated')}")

    if not control or max(control) > 0:
        print(f"\ncontrol failed: {control_label.split(' (')[0]} still returned thinking, "
              "so the harness cannot detect a real change. Numbers above mean nothing.",
              file=sys.stderr)
        return 1
    print(f"\ncontrol separated ({len(control)}/{len(control)} at zero), "
          "so the measurement can see a real change.")

    # A control at zero is necessary but not sufficient: some shims expose no
    # reasoning signal at all (gpt-5.6-luna on chat returns neither
    # reasoning_content nor a token count), which zeroes every level and would
    # print a vacuous p=1.00 that reads as "ignored" instead of "unmeasurable".
    if all(v == 0 for vs in samples.values() for v in vs):
        print("every level also returned zero — this route exposes no reasoning "
              "signal for this model, so effort is unmeasurable here, not "
              "proven ignored.", file=sys.stderr)
        return 1

    # Compare the widest pair that actually has samples: upstreams may reject
    # the top of the ladder (grok-4.5 400s on max), and minimal can be
    # quasi-off, so low is the preferred bottom rung.
    lo_name = next((lv for lv in ("low", "minimal") if samples.get(lv)), None)
    hi_name = next((lv for lv in reversed(levels) if samples.get(lv)), None)
    if lo_name is None or hi_name is None or lo_name == hi_name:
        print("could not compare the extremes", file=sys.stderr)
        return 1

    p = mann_whitney_p(samples[lo_name], samples[hi_name])
    verdict = ("effort is now doing something — re-measure and update docs/opencode-go.md"
               if p < 0.05 else "consistent with effort being ignored")
    print(f"{lo_name} vs {hi_name}: p={p:.2f} — {verdict}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
