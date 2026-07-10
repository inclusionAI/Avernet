from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from singlebox_coverage_report import (  # noqa: E402
    _load_jsonl_keys,
    _matches_core_path,
    _plugin_evidence_hits,
    acceptance_targets_for,
    build_module_report,
    select_module_names,
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


def test_select_module_names_defaults_to_all_enabled_modules():
    manifest = _manifest()
    manifest["modules"]["devices"]["acceptance_targets"] = [
        "tests/community/acceptance/devices"
    ]
    manifest["modules"]["cron"] = {
        "enabled": True,
        "acceptance_targets": ["tests/community/acceptance/cron"],
    }
    manifest["modules"]["future"] = {
        "enabled": False,
        "acceptance_targets": ["tests/community/acceptance/future"],
    }

    assert select_module_names(manifest, []) == ["devices", "cron"]
    assert acceptance_targets_for(manifest, ["devices", "cron"]) == [
        "tests/community/acceptance/devices",
        "tests/community/acceptance/cron",
    ]


def test_select_module_names_rejects_unknown_requested_module():
    with pytest.raises(ValueError, match="unknown coverage module: missing"):
        select_module_names(_manifest(), ["missing"])


def test_select_module_names_rejects_non_mapping_module_config():
    with pytest.raises(
        ValueError, match="coverage module config must be a mapping: empty"
    ):
        select_module_names({"modules": {"empty": None}}, [])


def test_acceptance_targets_are_deduplicated_in_module_order():
    manifest = {
        "modules": {
            "one": {"acceptance_targets": ["shared", "one"]},
            "two": {"acceptance_targets": ["shared", "two"]},
        }
    }

    assert acceptance_targets_for(manifest, ["one", "two"]) == [
        "shared",
        "one",
        "two",
    ]


def test_repository_manifest_registers_existing_coverage_modules_and_paths():
    repo_root = Path(__file__).resolve().parents[3]
    manifest = yaml.safe_load(
        (repo_root / "scripts/ci/singlebox_coverage_modules.yaml").read_text(
            encoding="utf-8"
        )
    )
    module_names = select_module_names(manifest, [])

    assert module_names == [
        "bot_dormant",
        "devices",
        "access",
        "bot_chat",
        "bot_collaborator",
        "cron",
        "expert_chat",
        "harness",
    ]
    for module_name in module_names:
        module = manifest["modules"][module_name]
        for target in module["acceptance_targets"]:
            assert (repo_root / "src/backend" / target).exists(), target
        for core_path in module["core_paths"]:
            assert (repo_root / "src/backend" / core_path).exists(), core_path
    for module in manifest["pending_modules"].values():
        for target in module["acceptance_targets"]:
            assert (repo_root / "src/backend" / target).exists(), target


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


def test_matches_core_path_requires_a_directory_boundary():
    prefix = ["src/agentclaw/community/core/devices"]

    assert _matches_core_path("src/agentclaw/community/core/devices/models.py", prefix)
    assert _matches_core_path(
        "/repo/src/agentclaw/community/core/devices/models.py", prefix
    )
    assert not _matches_core_path(
        "src/agentclaw/community/core/devices_other/models.py", prefix
    )


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


def test_build_module_report_supports_not_applicable_router_api():
    manifest = _manifest()
    manifest["modules"]["devices"]["router_api"] = {
        "status": "not_applicable",
        "reason": "The module has no independently attributable HTTP routes.",
        "items": [],
    }

    report = build_module_report(
        manifest=manifest,
        module_name="devices",
        coverage=_coverage(),
        router_hits=["GET /api/v1/devices"],
        plugin_hits=[],
    )

    assert report["router_api"] == {
        "status": "not_applicable",
        "reason": "The module has no independently attributable HTTP routes.",
        "covered": 0,
        "total": 0,
        "percent": None,
        "covered_items": [],
        "missing_items": [],
    }
    assert validate_thresholds(report) == []


def test_validate_thresholds_reports_applicable_plugin_failure():
    manifest = _manifest()
    manifest["modules"]["devices"]["plugin_api"] = {
        "status": "applicable",
        "items": ["AuthPlugin.get_current_user", "AuthPlugin.is_admin"],
    }
    manifest["modules"]["devices"]["thresholds"]["plugin_min_percent"] = 75.0

    report = build_module_report(
        manifest=manifest,
        module_name="devices",
        coverage=_coverage(),
        router_hits=DEVICE_ROUTES,
        plugin_hits=["AuthPlugin.get_current_user"],
    )

    assert report["plugin_api"]["percent"] == 50.0
    assert validate_thresholds(report) == [
        "devices plugin API coverage 50.00% < 75.00%"
    ]


def test_plugin_evidence_hits_uses_executed_implementation_body(tmp_path: Path):
    source = tmp_path / "src/agentclaw/community/plugins/local/passport.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class LocalPassportPlugin:\n"
        "    def freeze_agent_passport(self):\n"
        "        marker = 'called'\n"
        "        return marker\n"
        "\n"
        "    def unfreeze_agent_passport(self):\n"
        "        return 'not called'\n",
        encoding="utf-8",
    )
    manifest = {
        "modules": {
            "bot_dormant": {
                "plugin_api": {
                    "items": [
                        {
                            "key": "PassportPlugin.freeze_agent_passport",
                            "evidence": {
                                "path": "src/agentclaw/community/plugins/local/passport.py",
                                "symbol": "LocalPassportPlugin.freeze_agent_passport",
                            },
                        },
                        {
                            "key": "PassportPlugin.unfreeze_agent_passport",
                            "evidence": {
                                "path": "src/agentclaw/community/plugins/local/passport.py",
                                "symbol": "LocalPassportPlugin.unfreeze_agent_passport",
                            },
                        },
                    ]
                }
            }
        }
    }
    coverage = {
        "files": {
            "src/agentclaw/community/plugins/local/passport.py": {
                "executed_lines": [1, 2, 3, 4, 6]
            }
        }
    }

    assert _plugin_evidence_hits(
        manifest, ["bot_dormant"], coverage, backend_root=tmp_path
    ) == ["PassportPlugin.freeze_agent_passport"]


def test_plugin_evidence_does_not_count_one_line_definition(tmp_path: Path):
    source = tmp_path / "src/agentclaw/community/plugins/local/device.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "class DeviceAdapter:\n"
        "    def invoke(self): return None\n",
        encoding="utf-8",
    )
    manifest = {
        "modules": {
            "devices": {
                "plugin_api": {
                    "items": [
                        {
                            "key": "DeviceAdapterTransport.invoke",
                            "evidence": {
                                "path": "src/agentclaw/community/plugins/local/device.py",
                                "symbol": "DeviceAdapter.invoke",
                            },
                        }
                    ]
                }
            }
        }
    }
    coverage = {
        "files": {
            str(source): {
                "executed_lines": [2],
            }
        }
    }

    assert (
        _plugin_evidence_hits(
            manifest,
            ["devices"],
            coverage,
            backend_root=tmp_path,
        )
        == []
    )


def test_build_module_report_uses_mapping_keys_for_plugin_metric():
    manifest = _manifest()
    manifest["modules"]["devices"]["plugin_api"] = {
        "items": [
            {
                "key": "DeviceAdapterTransport.invoke",
                "evidence": {
                    "path": "src/plugin.py",
                    "symbol": "LocalTransport.invoke",
                },
            }
        ]
    }

    report = build_module_report(
        manifest=manifest,
        module_name="devices",
        coverage=_coverage(),
        router_hits=[],
        plugin_hits=["DeviceAdapterTransport.invoke"],
    )

    assert report["plugin_api"]["covered_items"] == [
        "DeviceAdapterTransport.invoke"
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
                "systems": {
                    "bcs": {
                        "name": "bcs",
                        "runtime_line": {
                            "covered": 45,
                            "total": 100,
                            "percent": 45.0,
                        },
                        "method": {
                            "covered": 40,
                            "total": 100,
                            "percent": 40.0,
                        },
                        "router_api": {
                            "covered": 12,
                            "total": 12,
                            "percent": 100.0,
                        },
                        "cli_command": {
                            "covered": 8,
                            "total": 8,
                            "percent": 100.0,
                        },
                    }
                },
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
    assert "BCS System" in markdown
    assert "Runtime Line: 45.00% (45/100)" in markdown
    assert "Method: 40.00% (40/100)" in markdown
    assert "Router API: 100.00% (12/12)" in markdown
    assert "CLI Commands: 100.00% (8/8)" in markdown
    assert "BCS System" in dashboard
    assert "Runtime Line" in dashboard
    assert "CLI Commands" in dashboard


def test_update_report_artifacts_marks_threshold_failure(tmp_path: Path):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "summary.json").write_text(
        json.dumps({"mode": "real", "status": "passed"}),
        encoding="utf-8",
    )
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
        router_hits=[],
        plugin_hits=[],
    )
    errors = validate_thresholds(report)

    update_report_artifacts(report_dir, report, threshold_errors=errors)

    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "failed"
    assert summary["threshold_errors"] == errors


def test_update_report_artifacts_aggregates_multiple_modules_and_errors(
    tmp_path: Path,
):
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    (report_dir / "summary.json").write_text(
        json.dumps({"mode": "real", "status": "passed"}),
        encoding="utf-8",
    )
    devices = build_module_report(
        manifest=_manifest(),
        module_name="devices",
        coverage=_coverage(),
        router_hits=DEVICE_ROUTES,
        plugin_hits=[],
    )
    cron = {
        **devices,
        "name": "cron",
        "core": {"covered": 1, "total": 4, "percent": 25.0},
    }
    errors = ["cron core coverage 25.00% < 40.00%"]

    update_report_artifacts(report_dir, [devices, cron], threshold_errors=errors)

    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    assert list(summary["modules"]) == ["devices", "cron"]
    assert summary["status"] == "failed"
    assert summary["threshold_errors"] == errors


def test_load_jsonl_keys_ignores_non_object_json_values(tmp_path: Path):
    hits_path = tmp_path / "hits.jsonl"
    hits_path.write_text(
        "\n".join(
            [
                '{"key": "GET /api/v1/devices"}',
                '["not", "an", "object"]',
                "null",
                '"plain string"',
                '{"key": 42}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert _load_jsonl_keys(hits_path) == ["GET /api/v1/devices"]


def test_load_jsonl_keys_streams_lines_without_read_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    hits_path = tmp_path / "hits.jsonl"
    hits_path.write_text(
        '{"key": "GET /api/v1/devices"}\n{"key": "DevicePlugin.read"}\n',
        encoding="utf-8",
    )

    def fail_read_text(*_args, **_kwargs):
        raise AssertionError("JSONL input must be streamed")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    assert _load_jsonl_keys(hits_path) == [
        "GET /api/v1/devices",
        "DevicePlugin.read",
    ]
