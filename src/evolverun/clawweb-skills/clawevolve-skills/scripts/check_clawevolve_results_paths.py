#!/usr/bin/env python3
"""Regression guard for the canonical ClawEvolve results directory spelling.

All online flows must write under ``clawevolve_results``. Prior spellings and
the legacy ``/workspace/evolve_results`` root caused artifacts to be written to
wrong workspace paths, so this lightweight check scans source/docs and verifies
the runtime constants used by diagnose and plan.
"""

from __future__ import annotations

import sys
from pathlib import Path

CANONICAL = "clawevolve_results"
FORBIDDEN = "clawevolve_" + "result" + "es"
LEGACY_ROOT = "/home/admin/.openclaw/workspace/" + "evolve_results"
ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "dist", "__pycache__", ".pytest_cache", ".mypy_cache"}
TEXT_SUFFIXES = {
    ".bash",
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def iter_text_files(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and (path.suffix in TEXT_SUFFIXES or path.name == "SKILL.md"):
            yield path


def scan_for_forbidden_spelling() -> list[str]:
    hits: list[str] = []
    for path in iter_text_files(ROOT):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if FORBIDDEN in line or LEGACY_ROOT in line:
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    return hits


def assert_runtime_constants() -> None:
    sys.path.insert(0, str(ROOT / "clawevolve-diagnose"))
    from clawevolve_diagnose.constants import EVOLVE_RESULTS_BASE_DIR as diagnose_base

    sys.path.insert(0, str(ROOT / "clawevolve-plan"))
    from clawevolve_plan.constants import EVOLVE_RESULTS_BASE_DIR as plan_base
    from clawevolve_plan.pipeline.paths import resolve_run_dir

    expected = f"/home/admin/.openclaw/workspace/{CANONICAL}"
    assert diagnose_base == expected, diagnose_base
    assert plan_base == expected, plan_base
    assert resolve_run_dir("", "EV-TEST-001") == f"{expected}/EV-TEST-001/diagnose"


def main() -> int:
    hits = scan_for_forbidden_spelling()
    if hits:
        print("Forbidden ClawEvolve results path found:", file=sys.stderr)
        print("\n".join(hits), file=sys.stderr)
        return 1
    assert_runtime_constants()
    print(f"OK: all ClawEvolve paths use {CANONICAL!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
