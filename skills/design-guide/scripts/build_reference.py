#!/usr/bin/env python3
"""Assemble a design-guide reference without adding a runtime dependency."""

import argparse
import re
from pathlib import Path
from textwrap import indent


ASSETS = Path(__file__).resolve().parents[1] / "assets"
INCLUDE = re.compile(
    r"^(?P<indent> *)<!-- include: (?P<path>(?:\.\./component-reference/)?(?:components|shared)/[a-z0-9-]+\.html) -->$",
    re.MULTILINE,
)


def render_reference(reference):
    template = (reference / "index.template.html").read_text(encoding="utf-8")

    def include(match):
        fragment = (reference / match["path"]).read_text(encoding="utf-8")
        return indent(fragment.rstrip("\n"), match["indent"])

    rendered = INCLUDE.sub(include, template)
    if "<!-- include:" in rendered:
        raise ValueError("Invalid or nested include; put includes in index.template.html only")
    return rendered.encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", choices=("component-reference", "dashboard-reference"), default="component-reference")
    parser.add_argument("--check", action="store_true", help="fail if index.html needs rebuilding; write nothing")
    args = parser.parse_args()
    try:
        reference = ASSETS / args.reference
        rendered = render_reference(reference)
        output = reference / "index.html"
        current = output.read_bytes() if output.exists() else None
        if current == rendered:
            print(f"{args.reference} is up to date.")
            return 0
        if args.check:
            print(f"{args.reference} is stale. Run python3 skills/design-guide/scripts/build_reference.py --reference {args.reference}")
            return 1
        output.write_bytes(rendered)
        print(f"Built {args.reference}/index.html")
        return 0
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot build {args.reference}: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
