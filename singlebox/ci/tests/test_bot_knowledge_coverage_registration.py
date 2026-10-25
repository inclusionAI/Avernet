"""Pin the complete new module into the real default and focused gate scopes."""

from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from singlebox_coverage_manifest_check import validate_manifest
from singlebox_coverage_report import acceptance_targets_for, select_module_names

ROOT = Path(__file__).resolve().parents[3]


def test_bot_knowledge_is_registered_with_complete_runtime_denominators():
    manifest = yaml.safe_load(
        (ROOT / "singlebox/ci/singlebox_coverage_modules.yaml").read_text()
    )
    assert "bot_knowledge" in select_module_names(manifest, [])
    assert acceptance_targets_for(manifest, ["bot_knowledge"]) == [
        "tests/community/acceptance/bot_knowledge"
    ]
    module = manifest["modules"]["bot_knowledge"]
    assert module["core_paths"] == ["src/agentclaw/community/core/bot_knowledge/"]
    assert set(module["router_api"]["items"]) == {
        "GET /api/bot-knowledge/{bot_id}",
        "PUT /api/bot-knowledge/{bot_id}/{kb_id}",
    }
    assert [item["key"] for item in module["plugin_api"]["items"]] == [
        "KnowledgeCatalogPlugin.list_authorized",
        "KnowledgeCatalogPlugin.list_in_space",
    ]
    assert validate_manifest(manifest, backend_root=ROOT / "apps/backend") == []
    assert module["thresholds"] == dict(
        core_min_percent=87.43, router_min_percent=100.0, plugin_min_percent=50.0
    )
    for target in module["acceptance_targets"]:
        assert (ROOT / "apps/backend" / target / "test_selection_live.py").is_file()
