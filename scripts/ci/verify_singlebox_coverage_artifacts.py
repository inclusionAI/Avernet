#!/usr/bin/env python3
"""Validate Singlebox coverage artifacts produced by singlebox_coverage.sh.

This script is intentionally a first-version anti-regression gate. It verifies
that the real Singlebox stack ran, the live acceptance smoke produced a passing
JUnit report, runtime hit counters are non-trivial, and backend/BaaS coverage
stays above low-water marks based on the current baseline.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


REQUIRED_FILES = (
    "summary.json",
    "summary.md",
    "dashboard.html",
    "acceptance.log",
    "acceptance-junit.xml",
    "backend-coverage.json",
    "backend-coverage.txt",
    "html/backend/index.html",
    "baas-coverage.json",
    "baas-coverage.txt",
    "html/baas/index.html",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify Singlebox coverage reports and low-water thresholds.",
    )
    parser.add_argument(
        "--reports-dir",
        default="scripts/.dependencies/coverage/singlebox/reports",
        help="Directory containing summary.json and coverage artifacts.",
    )
    parser.add_argument("--backend-min", type=float, default=38.0)
    parser.add_argument("--baas-min", type=float, default=45.0)
    parser.add_argument("--backend-router-min", type=int, default=10)
    parser.add_argument("--backend-plugin-min", type=int, default=300)
    parser.add_argument("--baas-router-min", type=int, default=10)
    return parser.parse_args()


def load_json(path: Path, errors: list[str]) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except FileNotFoundError:
        errors.append(f"missing required JSON file: {path}")
        return {}
    except json.JSONDecodeError as exc:
        errors.append(f"invalid JSON file {path}: {exc}")
        return {}

    if not isinstance(data, dict):
        errors.append(f"JSON file must contain an object: {path}")
        return {}
    return data


def get_nested(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def as_number(value: Any, label: str, errors: list[str]) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    errors.append(f"{label} is missing or not numeric")
    return 0.0


def validate_required_files(reports_dir: Path, errors: list[str]) -> None:
    for relative in REQUIRED_FILES:
        path = reports_dir / relative
        if not path.is_file():
            errors.append(f"missing required artifact: {path}")


def validate_summary(summary: dict[str, Any], args: argparse.Namespace, errors: list[str]) -> None:
    expected_fields = {
        ("status",): "passed",
        ("mode",): "real",
        ("stack",): "standalone start all",
    }
    for path, expected in expected_fields.items():
        actual = get_nested(summary, path)
        if actual != expected:
            errors.append(f"summary.{'.'.join(path)} expected {expected!r}, got {actual!r}")

    backend_router_hits = as_number(
        get_nested(summary, ("coverage", "backend", "router_hits")),
        "summary.coverage.backend.router_hits",
        errors,
    )
    backend_plugin_hits = as_number(
        get_nested(summary, ("coverage", "backend", "plugin_hits")),
        "summary.coverage.backend.plugin_hits",
        errors,
    )
    baas_router_hits = as_number(
        get_nested(summary, ("coverage", "baas", "router_hits")),
        "summary.coverage.baas.router_hits",
        errors,
    )

    if backend_router_hits < args.backend_router_min:
        errors.append(
            f"backend router hits {backend_router_hits:.0f} < {args.backend_router_min}",
        )
    if backend_plugin_hits < args.backend_plugin_min:
        errors.append(
            f"backend plugin hits {backend_plugin_hits:.0f} < {args.backend_plugin_min}",
        )
    if baas_router_hits < args.baas_router_min:
        errors.append(f"BaaS router hits {baas_router_hits:.0f} < {args.baas_router_min}")


def validate_acceptance_junit(junit_path: Path, errors: list[str]) -> None:
    try:
        root = ET.parse(junit_path).getroot()
    except FileNotFoundError:
        errors.append(f"missing acceptance JUnit: {junit_path}")
        return
    except ET.ParseError as exc:
        errors.append(f"invalid acceptance JUnit {junit_path}: {exc}")
        return

    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if not suites:
        errors.append(f"acceptance JUnit contains no testsuite: {junit_path}")
        return

    tests = sum(int(suite.attrib.get("tests", "0")) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", "0")) for suite in suites)
    errors_count = sum(int(suite.attrib.get("errors", "0")) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", "0")) for suite in suites)

    if tests <= 0:
        errors.append("acceptance JUnit contains zero tests")
    if tests - skipped <= 0:
        errors.append("acceptance JUnit contains no executed tests")
    if failures or errors_count:
        errors.append(
            f"acceptance JUnit has failures/errors: failures={failures}, errors={errors_count}",
        )


def coverage_percent(coverage_path: Path, label: str, errors: list[str]) -> float:
    coverage = load_json(coverage_path, errors)
    percent = get_nested(coverage, ("totals", "percent_covered"))
    return as_number(percent, f"{label} totals.percent_covered", errors)


def main() -> int:
    args = parse_args()
    reports_dir = Path(args.reports_dir)
    errors: list[str] = []

    validate_required_files(reports_dir, errors)
    summary = load_json(reports_dir / "summary.json", errors)
    if summary:
        validate_summary(summary, args, errors)
    validate_acceptance_junit(reports_dir / "acceptance-junit.xml", errors)

    backend_percent = coverage_percent(reports_dir / "backend-coverage.json", "backend", errors)
    baas_percent = coverage_percent(reports_dir / "baas-coverage.json", "BaaS", errors)

    if backend_percent < args.backend_min:
        errors.append(f"backend coverage {backend_percent:.2f}% < {args.backend_min:.2f}%")
    if baas_percent < args.baas_min:
        errors.append(f"BaaS coverage {baas_percent:.2f}% < {args.baas_min:.2f}%")

    if errors:
        print("singlebox coverage artifact verification failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    backend_router_hits = int(get_nested(summary, ("coverage", "backend", "router_hits")) or 0)
    backend_plugin_hits = int(get_nested(summary, ("coverage", "backend", "plugin_hits")) or 0)
    baas_router_hits = int(get_nested(summary, ("coverage", "baas", "router_hits")) or 0)

    print("singlebox coverage artifacts verified")
    print(f"backend coverage: {backend_percent:.2f}%")
    print(f"BaaS coverage: {baas_percent:.2f}%")
    print(f"backend router/plugin hits: {backend_router_hits}/{backend_plugin_hits}")
    print(f"BaaS router hits: {baas_router_hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
