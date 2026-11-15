"""Keep collaboration repair coverage aligned with its current Plugin API."""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from singlebox_coverage_manifest_check import validate_manifest  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def _manifest():
    return yaml.safe_load(
        (ROOT / "singlebox/ci/singlebox_coverage_modules.yaml").read_text(
            encoding="utf-8"
        )
    )


def test_repository_manifest_references_only_declared_plugin_methods():
    assert validate_manifest(_manifest(), backend_root=ROOT / "apps/backend") == []


def test_collaboration_repair_registers_complete_current_denominators():
    module = _manifest()["modules"]["collaboration_repair"]

    assert module["acceptance_targets"] == [
        "tests/community/acceptance/collaboration_repair"
    ]
    assert module["core_paths"] == [
        "src/agentclaw/community/core/bot_management/collaboration_repair/"
    ]
    assert module["router_api"]["items"] == [
        "POST /openapi/v1/bots/{bot_id}/collaboration/repair"
    ]
    assert module["plugin_api"]["status"] == "applicable"
    assert module["plugin_api"]["items"] == [
        {
            "key": f"BcsCollaborationMetadataPlugin.{method}",
            "evidence": {
                "path": "src/agentclaw/community/plugins/community/bcs_collaboration_metadata.py",
                "symbol": f"BcnCollaborationMetadataAdapter.{method}",
            },
        }
        for method in ("exists", "update_profile", "aclose")
    ]
    assert module["thresholds"] == {
        "core_min_percent": 70.0,
        "router_min_percent": 100.0,
        "plugin_min_percent": 100.0,
    }
