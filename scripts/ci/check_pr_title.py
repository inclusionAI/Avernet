#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re


ALLOWED_TYPES = (
    "feat",
    "fix",
    "refactor",
    "docs",
    "test",
    "ci",
    "build",
    "chore",
)
OPTIONAL_SCOPE = r"(?:\([^()\s](?:[^()\r\n]*[^()\s])?\))?"
TITLE_PATTERN = re.compile(
    rf"(?:{'|'.join(ALLOWED_TYPES)})"
    rf"{OPTIONAL_SCOPE}: "
    r"\S(?:.*\S)?"
)


def is_valid_pr_title(title: str) -> bool:
    return TITLE_PATTERN.fullmatch(title) is not None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the pull request title against the repository convention."
    )
    parser.add_argument(
        "--title",
        default=os.environ.get("PR_TITLE"),
        help="PR title to validate; defaults to the PR_TITLE environment variable",
    )
    args = parser.parse_args()

    if args.title is None:
        parser.error("provide --title or set PR_TITLE")

    if is_valid_pr_title(args.title):
        print(f"OK: valid PR title: {args.title}")
        return 0

    allowed_types = " | ".join(ALLOWED_TYPES)
    print(f"ERROR: invalid PR title: {args.title}")
    print("Expected: <type>: <concise outcome>")
    print("      or: <type>(<scope>): <concise outcome>")
    print(f"Allowed types: {allowed_types}")
    print("Scope is optional. When present, it must be non-empty and enclosed in ().")
    print("Examples:")
    print("  feat: add whitelist observed state")
    print("  feat(backend): add whitelist observed state")
    print("  fix(bcs): reject routing updates for unknown bot ids")
    print("  docs(arch): document plugin protocol conformance shape")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
