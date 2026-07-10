from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from singlebox_coverage_report import (  # noqa: E402
    build_module_report,
    update_report_artifacts,
    validate_thresholds,
)


DEVICE_ROUTES = [
    "GET /api/v1/devices",
    "GET /api/v1/devices/{binding_id:int}",
    "GET /api/v1/devices/by-id/{device_id}",
]


def _manifest() -> dict:
    return {
        "modules": {
            "devices": {
                "system": "backend",
                "core_paths": ["src/agentclaw/community/core/devices/"],
                "router_api": {"items": DEVICE_ROUTES},
                "plugin_api": {
                    "status": "not_applicable",
                    "reason": "No attributable device Plugin API denominator.",
                    "items": [],
                },
                "thresholds": {
                    "core_min_percent": 43.36,
                    "router_min_percent": 44.44,
                },
            }
        }
    }


def _coverage() -> dict:
    return {
        "files": {
            "src/agentclaw/community/core/devices/services/device_service.py": {
                "summary": {"covered_lines": 2, "num_statements": 4}
            },
            "/repo/src/agentclaw/community/core/devices/models.py": {
                "summary": {"covered_lines": 1, "num_statements": 1}
            },
            "src/agentclaw/community/core/bot_management/service.py": {
                "summary": {"covered_lines": 100, "num_statements": 100}
            },
        }
    }


def test_build_module_report_filters_core_and_deduplicates_router_hits():
    report = build_module_report(
        manifest=_manifest(),
        module_name="devices",
        coverage=_coverage(),
        router_hits=[
            "GET /api/v1/devices",
            "GET /api/v1/devices",
            "GET /api/v1/devices/{binding_id:int}",
            "GET /api/health",
        ],
        plugin_hits=["BotRepository.get_by_id"],
    )

    assert report["core"] == {
        "covered": 3,
        "total": 5,
        "percent": 60.0,
    }
    assert report["router_api"]["covered"] == 2
    assert report["router_api"]["total"] == 3
    assert report["router_api"]["percent"] == 66.67
    assert report["router_api"]["covered_items"] == DEVICE_ROUTES[:2]
    assert report["router_api"]["missing_items"] == DEVICE_ROUTES[2:]
    assert report["plugin_api"] == {
        "status": "not_applicable",
        "reason": "No attributable device Plugin API denominator.",
        "covered": 0,
        "total": 0,
        "percent": None,
        "covered_items": [],
        "missing_items": [],
    }


def test_build_module_report_rejects_unknown_module():
    with pytest.raises(ValueError, match="unknown coverage module: missing"):
        build_module_report(
            manifest=_manifest(),
            module_name="missing",
            coverage=_coverage(),
            router_hits=[],
            plugin_hits=[],
        )


def test_validate_thresholds_reports_core_and_router_failures():
    report = build_module_report(
        manifest=_manifest(),
        module_name="devices",
        coverage={
            "files": {
                "src/agentclaw/community/core/devices/models.py": {
                    "summary": {"covered_lines": 1, "num_statements": 4}
                }
            }
        },
        router_hits=["GET /api/v1/devices"],
        plugin_hits=[],
    )

    assert validate_thresholds(report) == [
        "devices core coverage 25.00% < 43.36%",
        "devices router API coverage 33.33% < 44.44%",
    ]


def test_update_report_artifacts_keeps_summary_and_dashboard_consistent(tmp_path: Path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "summary.json").write_text(
        json.dumps(
            {
                "mode": "real",
                "status": "passed",
                "acceptance": {"target": "tests/community/acceptance/devices"},
                "coverage": {"backend": {"router_hits": 4, "plugin_hits": 0}},
            }
        ),
        encoding="utf-8",
    )

    report = build_module_report(
        manifest=_manifest(),
        module_name="devices",
        coverage=_coverage(),
        router_hits=DEVICE_ROUTES[:2],
        plugin_hits=[],
    )
    update_report_artifacts(report_dir, report)

    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["modules"]["devices"] == report
    markdown = (report_dir / "summary.md").read_text(encoding="utf-8")
    dashboard = (report_dir / "dashboard.html").read_text(encoding="utf-8")
    assert "Devices" in markdown
    assert "Core Line: 60.00% (3/5)" in markdown
    assert "Router API: 66.67% (2/3)" in markdown
    assert "Plugin API: N/A" in markdown
    assert "60.00%" in dashboard
    assert "66.67%" in dashboard
    assert "Not applicable" in dashboard
