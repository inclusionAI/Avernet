#!/usr/bin/env python3
"""Build module metrics from artifacts emitted by singlebox_coverage.sh."""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

import yaml


def _percent(covered: int, total: int) -> float:
    return round((covered * 100.0 / total) if total else 0.0, 2)


def _matches_core_path(filename: str, prefixes: list[str]) -> bool:
    normalized = filename.replace("\\", "/").lstrip("./")
    return any(
        normalized.startswith(prefix)
        or f"/{prefix}" in normalized
        for prefix in prefixes
    )


def _metric(items: list[str], hits: list[str]) -> dict[str, Any]:
    hit_set = set(hits)
    covered_items = [item for item in items if item in hit_set]
    missing_items = [item for item in items if item not in hit_set]
    return {
        "covered": len(covered_items),
        "total": len(items),
        "percent": _percent(len(covered_items), len(items)),
        "covered_items": covered_items,
        "missing_items": missing_items,
    }


def build_module_report(
    *,
    manifest: dict[str, Any],
    module_name: str,
    coverage: dict[str, Any],
    router_hits: list[str],
    plugin_hits: list[str],
) -> dict[str, Any]:
    module = (manifest.get("modules") or {}).get(module_name)
    if module is None:
        raise ValueError(f"unknown coverage module: {module_name}")

    core_covered = 0
    core_total = 0
    core_paths = list(module.get("core_paths") or [])
    for filename, file_data in (coverage.get("files") or {}).items():
        if not _matches_core_path(filename, core_paths):
            continue
        summary = file_data.get("summary") or {}
        core_covered += int(summary.get("covered_lines") or 0)
        core_total += int(summary.get("num_statements") or 0)

    plugin_config = module.get("plugin_api") or {}
    plugin_status = plugin_config.get("status", "applicable")
    if plugin_status == "not_applicable":
        plugin_metric = {
            "status": "not_applicable",
            "reason": str(plugin_config.get("reason") or ""),
            "covered": 0,
            "total": 0,
            "percent": None,
            "covered_items": [],
            "missing_items": [],
        }
    else:
        plugin_metric = {
            "status": "applicable",
            "reason": str(plugin_config.get("reason") or ""),
            **_metric(list(plugin_config.get("items") or []), plugin_hits),
        }

    return {
        "name": module_name,
        "system": str(module.get("system") or ""),
        "core": {
            "covered": core_covered,
            "total": core_total,
            "percent": _percent(core_covered, core_total),
        },
        "router_api": _metric(
            list((module.get("router_api") or {}).get("items") or []),
            router_hits,
        ),
        "plugin_api": plugin_metric,
        "thresholds": dict(module.get("thresholds") or {}),
    }


def validate_thresholds(report: dict[str, Any]) -> list[str]:
    thresholds = report.get("thresholds") or {}
    errors: list[str] = []
    checks = (
        ("core", "core coverage", "core_min_percent"),
        ("router_api", "router API coverage", "router_min_percent"),
    )
    for metric_name, label, threshold_name in checks:
        if threshold_name not in thresholds:
            continue
        actual = float(report[metric_name]["percent"])
        minimum = float(thresholds[threshold_name])
        if actual < minimum:
            errors.append(
                f"{report['name']} {label} {actual:.2f}% < {minimum:.2f}%"
            )
    return errors


def _metric_markdown(label: str, metric: dict[str, Any]) -> str:
    if metric.get("status") == "not_applicable":
        return f"- {label}: N/A - {metric['reason']}"
    return (
        f"- {label}: {metric['percent']:.2f}% "
        f"({metric['covered']}/{metric['total']})"
    )


def _render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Singlebox Coverage Summary",
        "",
        f"- mode: {summary.get('mode', 'unknown')}",
        f"- status: {summary.get('status', 'unknown')}",
    ]
    acceptance = summary.get("acceptance") or {}
    if acceptance.get("target"):
        lines.append(f"- acceptance: {acceptance['target']}")
    for report in (summary.get("modules") or {}).values():
        lines.extend(
            [
                "",
                f"## {str(report['name']).replace('_', ' ').title()}",
                "",
                _metric_markdown("Core Line", report["core"]),
                _metric_markdown("Router API", report["router_api"]),
                _metric_markdown("Plugin API", report["plugin_api"]),
            ]
        )
    return "\n".join(lines) + "\n"


def _metric_html(label: str, metric: dict[str, Any]) -> str:
    if metric.get("status") == "not_applicable":
        value = "Not applicable"
        detail = html.escape(str(metric.get("reason") or ""))
    else:
        value = f"{metric['percent']:.2f}%"
        detail = f"{metric['covered']}/{metric['total']}"
    return (
        "<div class='metric'>"
        f"<span>{html.escape(label)}</span><strong>{value}</strong>"
        f"<small>{detail}</small></div>"
    )


def _render_dashboard(summary: dict[str, Any]) -> str:
    cards = []
    for report in (summary.get("modules") or {}).values():
        cards.append(
            "<section><h2>"
            + html.escape(str(report["name"]).replace("_", " ").title())
            + "</h2><div class='metrics'>"
            + _metric_html("Core Line", report["core"])
            + _metric_html("Router API", report["router_api"])
            + _metric_html("Plugin API", report["plugin_api"])
            + "</div></section>"
        )
    return """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Singlebox Coverage</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;background:#f4f6f8;color:#18212b}main{max-width:1080px;margin:auto;padding:32px}
section{background:#fff;border:1px solid #dce2e8;border-radius:8px;padding:20px}.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.metric{border-left:4px solid #1677ff;padding:12px;background:#f8fafc}.metric span,.metric small{display:block;color:#5c6b7a}.metric strong{display:block;font-size:28px;margin:8px 0}
@media(max-width:720px){.metrics{grid-template-columns:1fr}}
</style><main><h1>Singlebox Coverage</h1>""" + "".join(cards) + "</main></html>\n"


def update_report_artifacts(report_dir: Path, report: dict[str, Any]) -> None:
    summary_path = report_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.setdefault("modules", {})[report["name"]] = report
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (report_dir / "summary.md").write_text(
        _render_markdown(summary),
        encoding="utf-8",
    )
    (report_dir / "dashboard.html").write_text(
        _render_dashboard(summary),
        encoding="utf-8",
    )


def _load_jsonl_keys(path: Path) -> list[str]:
    if not path.is_file():
        return []
    keys: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
        key = item.get("key")
        if isinstance(key, str):
            keys.append(key)
    return keys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--module", required=True)
    parser.add_argument("--coverage-json", required=True, type=Path)
    parser.add_argument("--router-hits", required=True, type=Path)
    parser.add_argument("--plugin-hits", required=True, type=Path)
    parser.add_argument("--report-dir", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = yaml.safe_load(args.manifest.read_text(encoding="utf-8")) or {}
    coverage = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    report = build_module_report(
        manifest=manifest,
        module_name=args.module,
        coverage=coverage,
        router_hits=_load_jsonl_keys(args.router_hits),
        plugin_hits=_load_jsonl_keys(args.plugin_hits),
    )
    update_report_artifacts(args.report_dir, report)
    errors = validate_thresholds(report)
    if errors:
        print("singlebox module coverage threshold failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        f"{args.module} coverage: core={report['core']['percent']:.2f}% "
        f"router={report['router_api']['percent']:.2f}% "
        "plugin=N/A"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
