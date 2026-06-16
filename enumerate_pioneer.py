#!/usr/bin/env python3
"""List Pioneer models with pricing and reasoning effort tiers."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

PIONEER_API_BASE = "https://api.pioneer.ai"
GATEWAY_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh", "none")


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
    args = parser.parse_args()
    args.workers = min(max(1, args.workers), 32)
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


def reasoning_efforts(detail: dict[str, Any]) -> list[str]:
    api_levels = detail.get("supported_reasoning_levels") or []
    efforts = {entry.get("effort") for entry in api_levels if entry.get("effort")}
    efforts.update(GATEWAY_REASONING_EFFORTS)
    order = {effort: index for index, effort in enumerate(GATEWAY_REASONING_EFFORTS)}
    return sorted(efforts, key=lambda effort: order.get(effort, len(order)))


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


def build_catalog(api_key: str, workers: int) -> list[dict[str, Any]]:
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

    catalog: list[dict[str, Any]] = []
    for listed_model in listed:
        model_id = listed_model["id"]
        detail = details.get(model_id, {})
        pricing = pricing_by_id.get(model_id, {})

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
                "reasoning_efforts": reasoning_efforts(detail),
                "api_reasoning_efforts": [
                    entry.get("effort")
                    for entry in (detail.get("supported_reasoning_levels") or [])
                    if entry.get("effort")
                ],
            }
        )

    catalog.sort(key=lambda model: (model["deprecated"], model["label"].lower()))
    return catalog


def print_table(models: list[dict[str, Any]]) -> None:
    headers = (
        "id",
        "label",
        "input/M",
        "output/M",
        "context",
        "default",
        "api_reasoning_efforts",
    )
    rows = [
        (
            model["id"],
            model["label"] + (" [deprecated]" if model["deprecated"] else ""),
            format_price(model["input_price_per_million"]),
            format_price(model["output_price_per_million"]),
            format_context(model["context_window"]),
            model["default_reasoning_effort"] or "-",
            ", ".join(model["api_reasoning_efforts"]) or "-",
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
        "JSON output includes reasoning_efforts, which combines Pioneer gateway tiers "
        f"({', '.join(GATEWAY_REASONING_EFFORTS)}) with per-model metadata."
    )


def main() -> int:
    args = parse_args()
    api_key = require_api_key()
    catalog = build_catalog(api_key, args.workers)

    if not args.include_deprecated:
        catalog = [model for model in catalog if not model["deprecated"]]

    if args.json:
        print(json.dumps(catalog, indent=2))
    else:
        print_table(catalog)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
