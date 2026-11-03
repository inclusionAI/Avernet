"""Pin bot_knowledge's pending registration after the AGENTS.md projection landed."""

from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from singlebox_coverage_manifest_check import validate_manifest
from singlebox_coverage_report import select_module_names

ROOT = Path(__file__).resolve().parents[3]


def test_bot_knowledge_is_pending_until_device_bound_acceptance_exists():
    manifest = yaml.safe_load(
        (ROOT / "singlebox/ci/singlebox_coverage_modules.yaml").read_text(
            encoding="utf-8"
        )
    )
    # The AGENTS.md projection (agents_md_sync.py) writes the enabled-knowledge
    # section through the device-bound identity seam; the SQL-seeded selection
    # story bots carry no device binding, so those statements cannot be reached
    # by the legacy acceptance path and the module sits in pending_modules
    # until device-bound acceptance coverage exists.
    assert "bot_knowledge" not in select_module_names(manifest, [])
    module = manifest["pending_modules"]["bot_knowledge"]
    assert module["acceptance_targets"] == [
        "tests/community/acceptance/bot_knowledge"
    ]
    assert "device-bound identity seam" in module["reason"]
    assert validate_manifest(manifest, backend_root=ROOT / "apps/backend") == []
    for target in module["acceptance_targets"]:
        assert (ROOT / "apps/backend" / target / "test_selection_live.py").is_file()
