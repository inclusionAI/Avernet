"""Pin the complete ownership runtime denominator and its delivery evidence."""

from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from singlebox_coverage_manifest_check import validate_manifest  # noqa: E402
from singlebox_coverage_report import select_module_names  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def test_ownership_sync_is_enabled_with_complete_runtime_denominators():
    manifest = yaml.safe_load(
        (ROOT / "singlebox/ci/singlebox_coverage_modules.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert "ownership_sync" in select_module_names(manifest, [])
    assert "ownership_sync" not in manifest.get("pending_modules", {})
    module = manifest["modules"]["ownership_sync"]
    assert module["system"] == "backend"
    assert module["acceptance_targets"] == [
        "tests/community/acceptance/bot_ownership_sync"
    ]
    assert module["core_paths"] == [
        "src/agentclaw/community/core/bot_management/ownership_sync/"
    ]
    assert not any("excl" in key for key in module)
    assert module["router_api"]["status"] == "applicable"
    assert module["router_api"]["items"] == ["POST /api/bots/owner-handoffs/bcn-repair"]
    assert "upstream" in module["router_api"]["reason"]
    assert "handoff" in module["router_api"]["reason"]
    assert module["plugin_api"]["status"] == "applicable"
    assert module["plugin_api"]["items"] == [
        {
            "key": f"BcsOwnershipPlugin.{method}",
            "evidence": {
                "path": "src/agentclaw/community/plugins/community/bcs_ownership.py",
                "symbol": f"HttpBcsOwnershipPlugin.{method}",
            },
        }
        for method in ("replace_ownership", "aclose")
    ]
    # First valid live Backend measurement: floor(87 / 132 * 10000) / 100.
    # No unit or maintenance-driver coverage contributes to this initial floor.
    assert module["thresholds"] == {
        "core_min_percent": 65.90,
        "router_min_percent": 100.0,
        "plugin_min_percent": 100.0,
    }
    assert manifest["modules"]["manager_sync"]["thresholds"] == {
        "core_min_percent": 54.03
    }
    assert validate_manifest(manifest, backend_root=ROOT / "apps/backend") == []
