#!/usr/bin/env python3
"""Build module metrics from artifacts emitted by singlebox_coverage.sh."""

from __future__ import annotations

import argparse
import ast
import html
import json
import sys
from pathlib import Path
from typing import Any

import yaml


def _percent(covered: int, total: int) -> float:
    return round((covered * 100.0 / total) if total else 0.0, 2)


def _module_configs(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    modules = manifest.get("modules") or {}
    if not isinstance(modules, dict):
        raise ValueError("coverage manifest modules must be a mapping")
    for module_name, config in modules.items():
        if not isinstance(config, dict):
            raise ValueError(f"coverage module config must be a mapping: {module_name}")
    return modules


def select_module_names(
    manifest: dict[str, Any], requested_modules: list[str]
) -> list[str]:
    modules = _module_configs(manifest)
    if requested_modules:
        selected: list[str] = []
        for module_name in requested_modules:
            if module_name not in modules:
                raise ValueError(f"unknown coverage module: {module_name}")
            if module_name not in selected:
                selected.append(module_name)
        return selected
    return [
        module_name
        for module_name, config in modules.items()
        if config.get("enabled", True)
    ]


def acceptance_targets_for(
    manifest: dict[str, Any], module_names: list[str]
) -> list[str]:
    modules = _module_configs(manifest)
    targets: list[str] = []
    for module_name in module_names:
        module = modules.get(module_name)
        if module is None:
            raise ValueError(f"unknown coverage module: {module_name}")
        for target in module.get("acceptance_targets") or []:
            target = str(target)
            if target not in targets:
                targets.append(target)
    return targets


def _matches_core_path(filename: str, prefixes: list[str]) -> bool:
    normalized = f"/{filename.replace('\\', '/').strip('./')}/"
    return any(
        f"/{prefix.replace('\\', '/').strip('./').rstrip('/')}/" in normalized
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


def _configured_item_keys(config: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for item in config.get("items") or []:
        if isinstance(item, str):
            keys.append(item)
            continue
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            keys.append(item["key"])
    return keys


def _configured_metric(config: dict[str, Any], hits: list[str]) -> dict[str, Any]:
    status = config.get("status", "applicable")
    reason = str(config.get("reason") or "")
    if status == "not_applicable":
        return {
            "status": "not_applicable",
            "reason": reason,
            "covered": 0,
            "total": 0,
            "percent": None,
            "covered_items": [],
            "missing_items": [],
        }
    return {
        "status": "applicable",
        "reason": reason,
        **_metric(_configured_item_keys(config), hits),
    }


def _coverage_file_data(coverage: dict[str, Any], evidence_path: str) -> dict[str, Any]:
    wanted = evidence_path.replace("\\", "/").lstrip("./")
    for filename, data in (coverage.get("files") or {}).items():
        normalized = str(filename).replace("\\", "/").lstrip("./")
        if normalized == wanted or normalized.endswith(f"/{wanted}"):
            return data if isinstance(data, dict) else {}
    return {}


def _symbol_body_lines(path: Path, symbol: str) -> set[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parts = symbol.split(".")
    nodes: list[ast.AST] = list(tree.body)
    target: ast.AST | None = None
    for part in parts:
        target = next(
            (
                node
                for node in nodes
                if isinstance(
                    node,
                    (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
                )
                and node.name == part
            ),
            None,
        )
        if target is None:
            return set()
        nodes = list(getattr(target, "body", []))
    if not isinstance(target, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set()
    body = list(target.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    lines: set[int] = set()
    for statement in body:
        end_lineno = getattr(statement, "end_lineno", statement.lineno)
        lines.update(range(statement.lineno, end_lineno + 1))
    # Importing a class can mark a one-line method's definition line as
    # executed. A definition line alone is not evidence that the method ran.
    return {line for line in lines if line > target.lineno}


def _plugin_evidence_hits(
    manifest: dict[str, Any],
    module_names: list[str],
    coverage: dict[str, Any],
    *,
    backend_root: Path,
) -> list[str]:
    hits: list[str] = []
    modules = _module_configs(manifest)
    for module_name in module_names:
        plugin_api = modules[module_name].get("plugin_api") or {}
        if plugin_api.get("status", "applicable") == "not_applicable":
            continue
        for item in plugin_api.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            evidence = item.get("evidence")
            if not isinstance(key, str) or not isinstance(evidence, dict):
                continue
            evidence_path = evidence.get("path")
            symbol = evidence.get("symbol")
            if not isinstance(evidence_path, str) or not isinstance(symbol, str):
                continue
            source_path = backend_root / evidence_path
            if not source_path.is_file():
                continue
            executable_lines = _symbol_body_lines(source_path, symbol)
            file_data = _coverage_file_data(coverage, evidence_path)
            executed_lines = {
                int(line) for line in (file_data.get("executed_lines") or [])
            }
            if executable_lines & executed_lines and key not in hits:
                hits.append(key)
    return hits


def build_module_report(
    *,
    manifest: dict[str, Any],
    module_name: str,
    coverage: dict[str, Any],
    router_hits: list[str],
    plugin_hits: list[str],
) -> dict[str, Any]:
    module = _module_configs(manifest).get(module_name)
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

    router_metric = _configured_metric(module.get("router_api") or {}, router_hits)
    plugin_metric = _configured_metric(module.get("plugin_api") or {}, plugin_hits)

    return {
        "name": module_name,
        "system": str(module.get("system") or ""),
        "core": {
            "covered": core_covered,
            "total": core_total,
            "percent": _percent(core_covered, core_total),
        },
        "router_api": router_metric,
        "plugin_api": plugin_metric,
        "thresholds": dict(module.get("thresholds") or {}),
    }


def validate_thresholds(report: dict[str, Any]) -> list[str]:
    thresholds = report.get("thresholds") or {}
    errors: list[str] = []
    checks = (
        ("core", "core coverage", "core_min_percent"),
        ("router_api", "router API coverage", "router_min_percent"),
        ("plugin_api", "plugin API coverage", "plugin_min_percent"),
    )
    for metric_name, label, threshold_name in checks:
        if threshold_name not in thresholds:
            continue
        if report[metric_name].get("status") == "not_applicable":
            continue
        actual = float(report[metric_name]["percent"])
        minimum = float(thresholds[threshold_name])
        if actual < minimum:
            errors.append(f"{report['name']} {label} {actual:.2f}% < {minimum:.2f}%")
    return errors


def _metric_markdown(label: str, metric: dict[str, Any]) -> str:
    if metric.get("status") == "not_applicable":
        return f"- {label}: N/A - {metric['reason']}"
    return (
        f"- {label}: {metric['percent']:.2f}% ({metric['covered']}/{metric['total']})"
    )


def _system_metrics(report: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Return the runtime metrics that are meaningful for a system report."""
    metric_names = (
        ("runtime_line", "Runtime Line"),
        ("method", "Method"),
        ("router_api", "Router API"),
        ("cli_command", "CLI Commands"),
    )
    return [
        (label, report[name])
        for name, label in metric_names
        if isinstance(report.get(name), dict)
    ]


def _render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Singlebox Coverage Summary",
        "",
        f"- mode: {summary.get('mode', 'unknown')}",
        f"- status: {summary.get('status', 'unknown')}",
    ]
    acceptance = summary.get("acceptance") or {}
    targets = acceptance.get("targets") or []
    if targets:
        lines.append(f"- acceptance: {', '.join(targets)}")
    elif acceptance.get("target"):
        lines.append(f"- acceptance: {acceptance['target']}")
    for report in (summary.get("systems") or {}).values():
        lines.extend(
            [
                "",
                f"## {str(report['name']).upper()} System",
                "",
                *[
                    _metric_markdown(label, metric)
                    for label, metric in _system_metrics(report)
                ],
            ]
        )
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
    for report in (summary.get("systems") or {}).values():
        cards.append(
            "<section><h2>"
            + html.escape(str(report["name"]).upper())
            + " System</h2><div class='metrics'>"
            + "".join(
                _metric_html(label, metric)
                for label, metric in _system_metrics(report)
            )
            + "</div></section>"
        )
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
    return (
        """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Singlebox Coverage</title>
<style>
body{font-family:system-ui,sans-serif;margin:0;background:#f4f6f8;color:#18212b}main{max-width:1080px;margin:auto;padding:32px}
section{background:#fff;border:1px solid #dce2e8;border-radius:8px;padding:20px}.metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
.metric{border-left:4px solid #1677ff;padding:12px;background:#f8fafc}.metric span,.metric small{display:block;color:#5c6b7a}.metric strong{display:block;font-size:28px;margin:8px 0}
@media(max-width:720px){.metrics{grid-template-columns:1fr}}
</style><main><h1>Singlebox Coverage</h1>"""
        + "".join(cards)
        + "</main></html>\n"
    )


def update_report_artifacts(
    report_dir: Path,
    reports: dict[str, Any] | list[dict[str, Any]],
    *,
    threshold_errors: list[str] | None = None,
) -> None:
    if isinstance(reports, dict):
        reports = [reports]
    summary_path = report_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    modules = summary.setdefault("modules", {})
    for report in reports:
        modules[report["name"]] = report
    if threshold_errors:
        summary["status"] = "failed"
        summary["threshold_errors"] = threshold_errors
    else:
        summary.pop("threshold_errors", None)
    summary_path.write_text(
        json.dumps(summary, indent=2) + "\n",
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
    with path.open(encoding="utf-8") as lines:
        for line_number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSONL at {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if isinstance(key, str):
                keys.append(key)
    return keys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--module", action="append", default=[])
    parser.add_argument("--list-modules", action="store_true")
    parser.add_argument("--list-acceptance-targets", action="store_true")
    parser.add_argument("--coverage-json", type=Path)
    parser.add_argument("--router-hits", type=Path)
    parser.add_argument(
        "--backend-root",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "src/backend",
    )
    parser.add_argument("--report-dir", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = yaml.safe_load(args.manifest.read_text(encoding="utf-8")) or {}
    try:
        module_names = select_module_names(manifest, args.module)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.list_modules:
        print("\n".join(module_names))
        return 0
    if args.list_acceptance_targets:
        print("\n".join(acceptance_targets_for(manifest, module_names)))
        return 0
    required = {
        "--coverage-json": args.coverage_json,
        "--router-hits": args.router_hits,
        "--report-dir": args.report_dir,
    }
    missing = [flag for flag, value in required.items() if value is None]
    if missing:
        print(
            f"missing required reporting arguments: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 2
    coverage = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    router_hits = _load_jsonl_keys(args.router_hits)
    plugin_hits = _plugin_evidence_hits(
        manifest,
        module_names,
        coverage,
        backend_root=args.backend_root,
    )
    reports = [
        build_module_report(
            manifest=manifest,
            module_name=module_name,
            coverage=coverage,
            router_hits=router_hits,
            plugin_hits=plugin_hits,
        )
        for module_name in module_names
    ]
    errors = [error for report in reports for error in validate_thresholds(report)]
    update_report_artifacts(args.report_dir, reports, threshold_errors=errors)
    if errors:
        print("singlebox module coverage threshold failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    def display(metric: dict[str, Any]) -> str:
        if metric.get("status") == "not_applicable":
            return "N/A"
        return f"{metric['percent']:.2f}%"

    for report in reports:
        print(
            f"{report['name']} coverage: core={report['core']['percent']:.2f}% "
            f"router={display(report['router_api'])} "
            f"plugin={display(report['plugin_api'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
