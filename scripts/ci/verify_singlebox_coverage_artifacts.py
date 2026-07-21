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
import re
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
    "bcs/e2e.log",
    "bcs/cobertura.xml",
    "bcs/coverage.txt",
    "bcs/summary.json",
    "bcs/endpoint_coverage.xml",
    "bcs/endpoint_coverage.txt",
    "bcs/cli_command_coverage.txt",
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
    parser.add_argument("--backend-plugin-min", type=int, default=0)
    parser.add_argument("--baas-router-min", type=int, default=10)
    parser.add_argument("--bcs-line-min", type=float, default=40.0)
    parser.add_argument("--bcs-method-min", type=float, default=36.0)
    parser.add_argument("--bcs-router-min", type=float, default=100.0)
    parser.add_argument("--bcs-cli-min", type=float, default=100.0)
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
    backend_plugin_hits = sum(
        int((module.get("plugin_api") or {}).get("covered") or 0)
        for module in (summary.get("modules") or {}).values()
        if isinstance(module, dict)
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
    bcs_e2e_status = get_nested(summary, ("systems", "bcs", "e2e_status"))
    if bcs_e2e_status != "passed":
        errors.append(f"summary.systems.bcs.e2e_status expected 'passed', got {bcs_e2e_status!r}")


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


def ratio_percent(covered: int, total: int) -> float:
    return covered * 100.0 / total if total else 0.0


def validate_bcs_artifacts(
    reports_dir: Path,
    args: argparse.Namespace,
    errors: list[str],
) -> tuple[float, float, float, float]:
    bcs_dir = reports_dir / "bcs"
    summary = load_json(bcs_dir / "summary.json", errors)
    totals = get_nested(summary, ("data",))
    if not isinstance(totals, list) or not totals or not isinstance(totals[0], dict):
        errors.append("BCS summary has no data[0].totals")
        totals_data: dict[str, Any] = {}
    else:
        raw_totals = totals[0].get("totals") or {}
        if not isinstance(raw_totals, dict):
            errors.append("BCS summary data[0].totals must be an object")
            totals_data = {}
        else:
            totals_data = raw_totals

    def llvm_percent(name: str) -> float:
        metric = totals_data.get(name)
        if metric is None:
            metric = {}
        if not isinstance(metric, dict):
            errors.append(f"BCS {name} metric must be an object")
            return 0.0
        try:
            covered = int(metric.get("covered") or 0)
            total = int(metric.get("count") or 0)
        except (TypeError, ValueError):
            errors.append(f"BCS {name} metric is malformed")
            return 0.0
        if total <= 0:
            errors.append(f"BCS {name} metric has no executable denominator")
            return 0.0
        return ratio_percent(covered, total)

    line_percent = llvm_percent("lines")
    method_percent = llvm_percent("functions")

    endpoint_percent = 0.0
    try:
        endpoint_root = ET.parse(bcs_dir / "endpoint_coverage.xml").getroot()
        overall = endpoint_root.find("overall")
        if overall is None:
            errors.append("BCS endpoint coverage XML has no overall element")
        else:
            endpoint_covered = int(overall.attrib.get("covered", "0"))
            endpoint_total = int(overall.attrib.get("total", "0"))
            if endpoint_total <= 0:
                errors.append("BCS Router API coverage has no endpoint denominator")
            else:
                endpoint_percent = ratio_percent(endpoint_covered, endpoint_total)
    except (FileNotFoundError, ET.ParseError, ValueError) as exc:
        errors.append(f"invalid BCS endpoint coverage XML: {exc}")

    cli_percent = 0.0
    try:
        cli_text = (bcs_dir / "cli_command_coverage.txt").read_text(encoding="utf-8")
        match = re.search(r"coverage:\s*(\d+)\s*/\s*(\d+)", cli_text)
        if match is None:
            errors.append("BCS CLI coverage report has no covered/total summary")
        else:
            cli_covered = int(match.group(1))
            cli_total = int(match.group(2))
            if cli_total <= 0:
                errors.append("BCS CLI coverage has no command denominator")
            else:
                cli_percent = ratio_percent(cli_covered, cli_total)
    except FileNotFoundError as exc:
        errors.append(f"missing BCS CLI coverage report: {exc}")

    checks = (
        ("line", line_percent, args.bcs_line_min),
        ("method", method_percent, args.bcs_method_min),
        ("Router API", endpoint_percent, args.bcs_router_min),
        ("CLI command", cli_percent, args.bcs_cli_min),
    )
    for label, actual, minimum in checks:
        if actual < minimum:
            errors.append(f"BCS {label} coverage {actual:.2f}% < {minimum:.2f}%")
    return line_percent, method_percent, endpoint_percent, cli_percent


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
    bcs_metrics = validate_bcs_artifacts(reports_dir, args, errors)

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
    backend_plugin_hits = sum(
        int((module.get("plugin_api") or {}).get("covered") or 0)
        for module in (summary.get("modules") or {}).values()
        if isinstance(module, dict)
    )
    baas_router_hits = int(get_nested(summary, ("coverage", "baas", "router_hits")) or 0)

    print("singlebox coverage artifacts verified")
    print(f"backend coverage: {backend_percent:.2f}%")
    print(f"BaaS coverage: {baas_percent:.2f}%")
    print(
        "BCS runtime line/method/router/cli coverage: "
        f"{bcs_metrics[0]:.2f}%/{bcs_metrics[1]:.2f}%/"
        f"{bcs_metrics[2]:.2f}%/{bcs_metrics[3]:.2f}%"
    )
    print(
        "backend router hits/plugin evidence: "
        f"{backend_router_hits}/{backend_plugin_hits}"
    )
    print(f"BaaS router hits: {baas_router_hits}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
