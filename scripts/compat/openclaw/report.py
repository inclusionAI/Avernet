#!/usr/bin/env python3
"""Aggregate per-version OpenClaw compatibility results into CI artifacts."""

from __future__ import annotations

import argparse
import html
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PASSING_STATUSES = {"PASS", "PASS_WITH_WARNINGS"}


def version_key(result: dict[str, Any]) -> tuple[int, int, int, int]:
    raw = str(result.get("openclaw_version", "0.0.0"))
    core, _, suffix = raw.partition("-")
    parts = core.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return (0, 0, 0, 0)
    repair = int(suffix) if suffix.isdigit() else 0
    return (int(parts[0]), int(parts[1]), int(parts[2]), repair)


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def collect_results(results_dir: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in sorted(results_dir.glob("*/result.json")):
        result = read_json(path)
        result.setdefault("result_file", str(path))
        results.append(result)
    return sorted(results, key=version_key)


def phase_ok(result: dict[str, Any], name: str) -> bool | None:
    phase = result.get("phases", {}).get(name)
    if not isinstance(phase, dict):
        return None
    return bool(phase.get("ok"))


def phase_mark(result: dict[str, Any], name: str) -> str:
    value = phase_ok(result, name)
    if value is None:
        return "—"
    return "✅" if value else "❌"


def failure_detail(result: dict[str, Any]) -> str:
    if isinstance(result.get("error"), str):
        return result["error"]
    for phase in ("install", "sdk_imports", "typecheck", "runtime"):
        payload = result.get("phases", {}).get(phase)
        if not isinstance(payload, dict) or payload.get("ok") is not False:
            continue
        if isinstance(payload.get("error"), str):
            return payload["error"]
        if isinstance(payload.get("reason"), str):
            return payload["reason"]
        bcs = payload.get("bcs")
        if isinstance(bcs, dict) and isinstance(bcs.get("reason"), str):
            return bcs["reason"]
        return f"{phase} failed"
    return ""


def build_summary(
    results: list[dict[str, Any]],
    discovery: dict[str, Any] | None,
    *,
    setup_error: str | None = None,
) -> dict[str, Any]:
    counts = Counter(str(result.get("status", "UNKNOWN")) for result in results)
    expected_versions = discovery.get("versions", []) if discovery else []
    observed_versions = [str(result.get("openclaw_version", "unknown")) for result in results]
    missing_versions = [version for version in expected_versions if version not in observed_versions]
    passing = sum(count for status, count in counts.items() if status in PASSING_STATUSES)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "floor": discovery.get("floor") if discovery else None,
        "latest": discovery.get("latest") if discovery else None,
        "expected_count": len(expected_versions) if discovery else len(results),
        "tested_count": len(results),
        "passing_count": passing,
        "failing_count": len(results) - passing,
        "missing_versions": missing_versions,
        "compatible": bool(results) and passing == len(results) and not missing_versions and not setup_error,
        "setup_error": setup_error,
        "status_counts": dict(sorted(counts.items())),
        "results": results,
    }


def markdown_report(summary: dict[str, Any]) -> str:
    lines = [
        "# OpenClaw compatibility report",
        "",
        f"- generated: `{summary['generated_at']}`",
        f"- declared floor: `{summary.get('floor') or 'unknown'}`",
        f"- npm latest: `{summary.get('latest') or 'unknown'}`",
        f"- result: `{'COMPATIBLE' if summary['compatible'] else 'INCOMPATIBLE OR INCOMPLETE'}`",
        f"- tested: `{summary['tested_count']}/{summary['expected_count']}`; passing: `{summary['passing_count']}`; failing: `{summary['failing_count']}`",
        "",
    ]
    if summary["missing_versions"]:
        lines.extend(
            [
                f"Missing results: `{', '.join(summary['missing_versions'])}`",
                "",
            ]
        )
    if summary.get("setup_error"):
        setup_error = str(summary["setup_error"]).replace("`", "'")
        lines.extend(
            [
                f"Setup error: `{setup_error}`",
                "",
            ]
        )
    lines.extend(
        [
            "| OpenClaw | Status | Install | SDK imports | Real types | Runtime | LLM calls | Duration | Detail |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for result in summary["results"]:
        runtime = result.get("phases", {}).get("runtime", {})
        llm_calls = runtime.get("llm_request_count", "—") if isinstance(runtime, dict) else "—"
        detail = failure_detail(result).replace("|", "\\|").replace("\n", " ")[:240]
        lines.append(
            "| {version} | {status} | {install} | {sdk} | {types} | {runtime} | {llm} | {duration:.1f}s | {detail} |".format(
                version=result.get("openclaw_version", "unknown"),
                status=result.get("status", "UNKNOWN"),
                install=phase_mark(result, "install"),
                sdk=phase_mark(result, "sdk_imports"),
                types=phase_mark(result, "typecheck"),
                runtime=phase_mark(result, "runtime"),
                llm=llm_calls,
                duration=float(result.get("duration_seconds", 0)),
                detail=detail,
            )
        )
    lines.append("")
    return "\n".join(lines)


def junit_report(summary: dict[str, Any]) -> ET.ElementTree:
    suite = ET.Element(
        "testsuite",
        {
            "name": "openclaw-compatibility",
            "tests": str(
                summary["tested_count"]
                + len(summary["missing_versions"])
                + (1 if summary.get("setup_error") else 0)
            ),
            "failures": str(summary["failing_count"]),
            "errors": str(len(summary["missing_versions"]) + (1 if summary.get("setup_error") else 0)),
        },
    )
    for result in summary["results"]:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": "openclaw.compatibility",
                "name": str(result.get("openclaw_version", "unknown")),
                "time": str(result.get("duration_seconds", 0)),
            },
        )
        status = str(result.get("status", "UNKNOWN"))
        if status not in PASSING_STATUSES:
            failure = ET.SubElement(case, "failure", {"type": status, "message": failure_detail(result)})
            failure.text = json.dumps(result.get("phases", {}), ensure_ascii=False, indent=2)
    for version in summary["missing_versions"]:
        case = ET.SubElement(
            suite,
            "testcase",
            {"classname": "openclaw.compatibility", "name": version, "time": "0"},
        )
        ET.SubElement(case, "error", {"type": "MISSING_RESULT", "message": "no result artifact was produced"})
    if summary.get("setup_error"):
        case = ET.SubElement(
            suite,
            "testcase",
            {"classname": "openclaw.compatibility", "name": "matrix-setup", "time": "0"},
        )
        error = ET.SubElement(case, "error", {"type": "SETUP_ERROR", "message": str(summary["setup_error"])})
        error.text = str(summary["setup_error"])
    return ET.ElementTree(suite)


def html_report(summary: dict[str, Any]) -> str:
    rows: list[str] = []
    for result in summary["results"]:
        status = str(result.get("status", "UNKNOWN"))
        css = "pass" if status in PASSING_STATUSES else "fail"
        rows.append(
            "<tr class='{css}'><td>{version}</td><td>{status}</td><td>{install}</td>"
            "<td>{sdk}</td><td>{types}</td><td>{runtime}</td><td>{duration:.1f}s</td><td>{detail}</td></tr>".format(
                css=css,
                version=html.escape(str(result.get("openclaw_version", "unknown"))),
                status=html.escape(status),
                install=phase_mark(result, "install"),
                sdk=phase_mark(result, "sdk_imports"),
                types=phase_mark(result, "typecheck"),
                runtime=phase_mark(result, "runtime"),
                duration=float(result.get("duration_seconds", 0)),
                detail=html.escape(failure_detail(result)[:500]),
            )
        )
    setup_error = ""
    if summary.get("setup_error"):
        setup_error = f"<p class='fail'><strong>Setup error:</strong> {html.escape(str(summary['setup_error']))}</p>"
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>OpenClaw compatibility</title>
<style>body{font:14px system-ui;margin:2rem}table{border-collapse:collapse;width:100%%}th,td{border:1px solid #ddd;padding:.5rem;text-align:left}.pass{background:#effaf2}.fail{background:#fff0f0}code{background:#eee;padding:.1rem .3rem}</style>
</head><body><h1>OpenClaw compatibility report</h1>
<p>Result: <code>%s</code>; tested %s/%s; generated %s.</p>%s
<table><thead><tr><th>OpenClaw</th><th>Status</th><th>Install</th><th>SDK imports</th><th>Real types</th><th>Runtime</th><th>Duration</th><th>Detail</th></tr></thead>
<tbody>%s</tbody></table></body></html>
""" % (
        "COMPATIBLE" if summary["compatible"] else "INCOMPATIBLE OR INCOMPLETE",
        summary["tested_count"],
        summary["expected_count"],
        html.escape(summary["generated_at"]),
        setup_error,
        "\n".join(rows),
    )


def write_reports(
    *,
    results_dir: Path,
    output_dir: Path,
    discovery_file: Path | None,
    setup_error: str | None = None,
) -> dict[str, Any]:
    discovery = read_json(discovery_file) if discovery_file and discovery_file.is_file() else None
    results = collect_results(results_dir)
    if discovery:
        by_version = {str(result.get("openclaw_version")): result for result in results}
        results = [by_version[version] for version in discovery.get("versions", []) if version in by_version]
    summary = build_summary(results, discovery, setup_error=setup_error)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "summary.md").write_text(markdown_report(summary), encoding="utf-8")
    junit_report(summary).write(output_dir / "junit.xml", encoding="utf-8", xml_declaration=True)
    (output_dir / "report.html").write_text(html_report(summary), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--discovery-file", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = write_reports(
            results_dir=args.results_dir,
            output_dir=args.output_dir,
            discovery_file=args.discovery_file,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"OpenClaw compatibility report failed: {error}", file=sys.stderr)
        return 2
    print((args.output_dir / "summary.md").read_text(encoding="utf-8"))
    return 0 if summary["compatible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
