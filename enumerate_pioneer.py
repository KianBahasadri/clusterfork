#!/usr/bin/env python3
"""List Pioneer models with pricing and reasoning effort tiers."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

PIONEER_API_BASE = "https://api.pioneer.ai"
REASONING_TIER_ORDER = ("minimal", "low", "medium", "high", "xhigh")
PROBE_EFFORT_ORDER = ("xhigh", "high", "medium", "low", "minimal")
GATEWAY_PROBE_MODEL = "claude-haiku-4-5"
INFERENCE_PROBE_MAX_TOKENS = 8192
QUOTED_EFFORT_RE = re.compile(r"'([^']+)'")
UPSTREAM_SUPPORTED_RE = re.compile(
    r"Supported values are:\s*'([^']+)'(?:,\s*'([^']+)')*",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Print Pioneer models with per-million pricing and available "
            "reasoning effort tiers."
        )
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of a table.",
    )
    parser.add_argument(
        "--include-deprecated",
        action="store_true",
        help="Include models marked deprecated in the catalog.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel workers for per-model detail lookups (default: 8).",
    )
    parser.add_argument(
        "--probe-workers",
        type=int,
        default=4,
        help="Parallel workers for /inference reasoning probes (default: 4).",
    )
    parser.add_argument(
        "--skip-probe",
        action="store_true",
        help="Skip live /inference probes and use model metadata only.",
    )
    args = parser.parse_args()
    args.workers = min(max(1, args.workers), 32)
    args.probe_workers = min(max(1, args.probe_workers), 16)
    return args


def require_api_key() -> str:
    api_key = os.environ.get("PIONEER_API_KEY", "").strip()
    if not api_key:
        print("error: PIONEER_API_KEY environment variable is required", file=sys.stderr)
        sys.exit(1)
    return api_key


def fetch_json(url: str, api_key: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"X-API-Key": api_key, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise RuntimeError(f"GET {url} failed ({exc.code}): {body[:400]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GET {url} failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError(f"GET {url} timed out") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GET {url} returned invalid JSON: {exc}") from exc


def first_not_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def format_price(value: float | None) -> str:
    if value is None:
        return "-"
    return f"${value:.4g}"


def format_context(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:,}"


def sort_efforts(efforts: list[str]) -> list[str]:
    order = {effort: index for index, effort in enumerate(REASONING_TIER_ORDER)}
    return sorted({effort for effort in efforts if effort in REASONING_TIER_ORDER}, key=order.get)


def highest_tier(efforts: list[str]) -> str | None:
    ordered = sort_efforts(efforts)
    return ordered[-1] if ordered else None


def parse_quoted_efforts(message: str) -> list[str]:
    if "Input should be" not in message:
        return []
    return QUOTED_EFFORT_RE.findall(message.split("Input should be", 1)[1])


def parse_upstream_supported_efforts(detail: str) -> list[str]:
    if "Supported values are:" not in detail:
        return []
    segment = detail.split("Supported values are:", 1)[1]
    return QUOTED_EFFORT_RE.findall(segment)


def api_reasoning_efforts(detail: dict[str, Any]) -> list[str]:
    return [
        entry.get("effort")
        for entry in (detail.get("supported_reasoning_levels") or [])
        if entry.get("effort")
    ]


def post_inference_generate(
    model_id: str,
    api_key: str,
    *,
    effort: str | None,
) -> tuple[int, str]:
    body: dict[str, Any] = {
        "model_id": model_id,
        "task": "generate",
        "messages": [{"role": "user", "content": "ok"}],
        "max_tokens": INFERENCE_PROBE_MAX_TOKENS,
    }
    if effort is not None:
        body["reasoning"] = {"effort": effort}

    request = urllib.request.Request(
        f"{PIONEER_API_BASE}/inference",
        data=json.dumps(body).encode(),
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return response.status, "ok"
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError:
            return exc.code, body_text
        detail = payload.get("detail", body_text)
        if isinstance(detail, dict):
            detail = json.dumps(detail)
        return exc.code, str(detail)
    except urllib.error.URLError as exc:
        return 0, f"network error: {exc.reason}"


def fetch_gateway_reasoning_efforts(api_key: str) -> list[str]:
    """Probe POST /inference with junk effort to read Pioneer's schema enum."""
    status, detail = post_inference_generate(
        GATEWAY_PROBE_MODEL,
        api_key,
        effort="banana",
    )
    if status != 422:
        print(
            f"warning: gateway effort probe on {GATEWAY_PROBE_MODEL} returned "
            f"HTTP {status}; falling back to default tier list",
            file=sys.stderr,
        )
        return [*REASONING_TIER_ORDER, "none"]

    efforts = parse_quoted_efforts(detail)
    if not efforts:
        print(
            "warning: could not parse gateway effort enum from 422 response; "
            "falling back to default tier list",
            file=sys.stderr,
        )
        return [*REASONING_TIER_ORDER, "none"]
    return efforts


def probe_model_reasoning(model_id: str, api_key: str) -> dict[str, Any]:
    """Find accepted reasoning tiers by probing POST /inference per effort."""
    results: dict[str, tuple[int, str]] = {}
    probe_errors: dict[str, str] = {}

    def record(effort: str, status: int, detail: str) -> str | None:
        results[effort] = (status, detail)
        if status != 200:
            probe_errors[effort] = f"{status}: {detail[:240]}"
        if "Encoder model" in detail and "schema" in detail:
            return "encoder"
        return None

    for effort in ("xhigh", "minimal"):
        status, detail = post_inference_generate(model_id, api_key, effort=effort)
        if record(effort, status, detail) == "encoder":
            return {
                "probe_status": "skipped_encoder",
                "highest_reasoning_effort": None,
                "reasoning_efforts": [],
                "probed_reasoning_efforts": [],
                "upstream_reasoning_efforts": [],
                "probe_errors": probe_errors,
            }

        upstream_efforts = parse_upstream_supported_efforts(detail)
        if upstream_efforts:
            reasoning_efforts = sort_efforts(upstream_efforts)
            return {
                "probe_status": "upstream",
                "highest_reasoning_effort": highest_tier(reasoning_efforts),
                "reasoning_efforts": reasoning_efforts,
                "probed_reasoning_efforts": sort_efforts(
                    [effort for effort, (status, _) in results.items() if status == 200]
                ),
                "upstream_reasoning_efforts": upstream_efforts,
                "probe_errors": probe_errors,
            }

    accepted = [effort for effort, (status, _) in results.items() if status == 200]
    if len(accepted) == 2:
        reasoning_efforts = list(REASONING_TIER_ORDER)
        return {
            "probe_status": "probed",
            "highest_reasoning_effort": "xhigh",
            "reasoning_efforts": reasoning_efforts,
            "probed_reasoning_efforts": reasoning_efforts,
            "upstream_reasoning_efforts": [],
            "probe_errors": probe_errors,
        }

    for effort in ("high", "medium", "low"):
        if effort in results:
            continue
        status, detail = post_inference_generate(model_id, api_key, effort=effort)
        if record(effort, status, detail) == "encoder":
            return {
                "probe_status": "skipped_encoder",
                "highest_reasoning_effort": None,
                "reasoning_efforts": [],
                "probed_reasoning_efforts": [],
                "upstream_reasoning_efforts": [],
                "probe_errors": probe_errors,
            }

        upstream_efforts = parse_upstream_supported_efforts(detail)
        if upstream_efforts:
            reasoning_efforts = sort_efforts(upstream_efforts)
            return {
                "probe_status": "upstream",
                "highest_reasoning_effort": highest_tier(reasoning_efforts),
                "reasoning_efforts": reasoning_efforts,
                "probed_reasoning_efforts": sort_efforts(
                    [effort for effort, (status, _) in results.items() if status == 200]
                ),
                "upstream_reasoning_efforts": upstream_efforts,
                "probe_errors": probe_errors,
            }

    reasoning_efforts = sort_efforts(
        [effort for effort, (status, _) in results.items() if status == 200]
    )
    return {
        "probe_status": "probed" if reasoning_efforts else "none_accepted",
        "highest_reasoning_effort": highest_tier(reasoning_efforts),
        "reasoning_efforts": reasoning_efforts,
        "probed_reasoning_efforts": reasoning_efforts,
        "upstream_reasoning_efforts": [],
        "probe_errors": probe_errors,
    }


def fetch_model_detail(model_id: str, api_key: str) -> dict[str, Any]:
    encoded_id = urllib.parse.quote(model_id, safe="")
    return fetch_json(f"{PIONEER_API_BASE}/v1/models/{encoded_id}", api_key)


def fetch_listed_models(api_key: str) -> list[dict[str, Any]]:
    listed: list[dict[str, Any]] = []
    after: str | None = None

    while True:
        url = f"{PIONEER_API_BASE}/v1/models"
        if after:
            url = f"{url}?{urllib.parse.urlencode({'after': after})}"

        payload = fetch_json(url, api_key)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise RuntimeError(f"GET {url} returned an unexpected response shape")

        listed.extend(payload["data"])
        if not payload.get("has_more"):
            return listed

        next_after = payload.get("last_id")
        if not isinstance(next_after, str) or not next_after or next_after == after:
            raise RuntimeError(f"GET {url} reported more pages without a usable last_id")
        after = next_after


def build_catalog(
    api_key: str,
    workers: int,
    *,
    skip_probe: bool,
    probe_workers: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    listed_payload = fetch_listed_models(api_key)
    listed: list[dict[str, Any]] = []
    for model in listed_payload:
        if not isinstance(model, dict) or not model.get("id"):
            print(f"warning: skipping listed model without id: {model!r}", file=sys.stderr)
            continue
        listed.append(model)

    base_payload = fetch_json(f"{PIONEER_API_BASE}/base-models", api_key)
    if not isinstance(base_payload, dict) or not isinstance(base_payload.get("models"), list):
        raise RuntimeError("GET /base-models returned an unexpected response shape")

    pricing_by_id: dict[str, dict[str, Any]] = {}
    for model in base_payload["models"]:
        if not isinstance(model, dict) or not model.get("id"):
            print(f"warning: skipping pricing model without id: {model!r}", file=sys.stderr)
            continue
        pricing_by_id[model["id"]] = model

    gateway_reasoning_efforts = (
        [*REASONING_TIER_ORDER, "none"] if skip_probe else fetch_gateway_reasoning_efforts(api_key)
    )

    details: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_model_detail, model["id"], api_key): model["id"]
            for model in listed
        }
        for future in as_completed(futures):
            model_id = futures[future]
            try:
                detail = future.result()
            except Exception as exc:
                print(f"warning: skipping details for {model_id}: {exc}", file=sys.stderr)
                continue
            if not isinstance(detail, dict):
                print(
                    f"warning: skipping details for {model_id}: unexpected response shape",
                    file=sys.stderr,
                )
                continue
            details[model_id] = detail

    probe_results: dict[str, dict[str, Any]] = {}
    if not skip_probe:
        probe_targets = [
            model["id"]
            for model in listed
            if pricing_by_id.get(model["id"], {}).get("task_type") != "encoder"
        ]
        with ThreadPoolExecutor(max_workers=probe_workers) as pool:
            futures = {
                pool.submit(probe_model_reasoning, model_id, api_key): model_id
                for model_id in probe_targets
            }
            for future in as_completed(futures):
                model_id = futures[future]
                try:
                    probe_results[model_id] = future.result()
                except Exception as exc:
                    print(
                        f"warning: reasoning probe failed for {model_id}: {exc}",
                        file=sys.stderr,
                    )
                    probe_results[model_id] = {
                        "probe_status": "error",
                        "highest_reasoning_effort": None,
                        "reasoning_efforts": [],
                        "probed_reasoning_efforts": [],
                        "upstream_reasoning_efforts": [],
                        "probe_errors": {"_probe": str(exc)},
                    }

    catalog: list[dict[str, Any]] = []
    for listed_model in listed:
        model_id = listed_model["id"]
        detail = details.get(model_id, {})
        pricing = pricing_by_id.get(model_id, {})
        metadata_efforts = api_reasoning_efforts(detail)
        probe = probe_results.get(model_id, {})

        if skip_probe:
            reasoning_efforts = sort_efforts(metadata_efforts)
            highest_reasoning_effort = highest_tier(reasoning_efforts)
            probe_status = "skipped"
        elif pricing.get("task_type") == "encoder":
            reasoning_efforts = []
            highest_reasoning_effort = None
            probe_status = "skipped_encoder"
        else:
            reasoning_efforts = probe.get("reasoning_efforts", [])
            highest_reasoning_effort = probe.get("highest_reasoning_effort")
            probe_status = probe.get("probe_status", "missing")

        catalog.append(
            {
                "id": model_id,
                "label": pricing.get("label") or detail.get("display_name") or model_id,
                "deprecated": bool(
                    listed_model.get("deprecated")
                    or pricing.get("deprecated")
                ),
                "context_window": first_not_none(
                    pricing.get("context_window"),
                    detail.get("context_window"),
                    listed_model.get("max_input_tokens"),
                ),
                "input_price_per_million": pricing.get("input_price_per_million"),
                "output_price_per_million": pricing.get("output_price_per_million"),
                "cache_read_price_per_million": pricing.get("cache_read_price_per_million"),
                "cache_write_price_per_million": pricing.get(
                    "cache_write_price_per_million"
                ),
                "default_reasoning_effort": detail.get("default_reasoning_level"),
                "gateway_reasoning_efforts": gateway_reasoning_efforts,
                "highest_reasoning_effort": highest_reasoning_effort,
                "reasoning_efforts": reasoning_efforts,
                "probed_reasoning_efforts": probe.get("probed_reasoning_efforts", []),
                "upstream_reasoning_efforts": probe.get("upstream_reasoning_efforts", []),
                "api_reasoning_efforts": metadata_efforts,
                "probe_status": probe_status,
                "probe_errors": probe.get("probe_errors", {}),
            }
        )

    catalog.sort(key=lambda model: (model["deprecated"], model["label"].lower()))
    return catalog, gateway_reasoning_efforts


def print_table(models: list[dict[str, Any]], gateway_reasoning_efforts: list[str]) -> None:
    headers = (
        "id",
        "label",
        "input/M",
        "output/M",
        "context",
        "default",
        "highest",
        "reasoning_efforts",
    )
    rows = [
        (
            model["id"],
            model["label"] + (" [deprecated]" if model["deprecated"] else ""),
            format_price(model["input_price_per_million"]),
            format_price(model["output_price_per_million"]),
            format_context(model["context_window"]),
            model["default_reasoning_effort"] or "-",
            model["highest_reasoning_effort"] or "-",
            ", ".join(model["reasoning_efforts"]) or "-",
        )
        for model in models
    ]

    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def render_row(cells: tuple[str, ...]) -> str:
        return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells))

    print(render_row(headers))
    print(render_row(tuple("-" * width for width in widths)))
    for row in rows:
        print(render_row(row))
    print(f"\n{len(models)} models")
    print(
        "Reasoning tiers are probed via POST /inference: junk effort reads the "
        "gateway enum; valid efforts are tested per model. Upstream 400 errors "
        "with 'Supported values are:' override probe results when present."
    )
    print(f"Gateway schema efforts: {', '.join(gateway_reasoning_efforts)}")


def main() -> int:
    args = parse_args()
    api_key = require_api_key()
    catalog, gateway_reasoning_efforts = build_catalog(
        api_key,
        args.workers,
        skip_probe=args.skip_probe,
        probe_workers=args.probe_workers,
    )

    if not args.include_deprecated:
        catalog = [model for model in catalog if not model["deprecated"]]

    if args.json:
        print(json.dumps(catalog, indent=2))
    else:
        print_table(catalog, gateway_reasoning_efforts)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
