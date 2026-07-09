"""Route-B acceptance: harness read-only query lifecycle on live backend.

Starts a real singlebox backend, runs the 5 read-only flows + asserts no-data
state matches baseline.

harness write paths (POST /diagnose / /generate-patches / /apply / /rollback)
all depend on LLM (antchat HTTP, disabled in LOCAL without token) or Arca
file ops (LOCAL no arca binding) — see findings/harness-llm-and-arca-unmocked.md.

Acceptance covers the empty/no-data contract:
  - templates list empty {total: 0, items: []}
  - template/999999 missing → raw 404 {detail: "Template 999999 not found"}
  - patches list empty {templates: []}  ← key 'templates' not 'items'!
  - patch-records list empty {items: []}
  - diagnose/records list empty {total: 0, items: []}

Off by default; enable with RUN_ACCEPTANCE=1.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.community._flows.harness.api_lifecycle import HARNESS_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner_live import run_flow_live

BASELINE_PATH = Path(__file__).parent / "baseline_harness_query.json"
HEADERS = {"x-user-id": "e2e_user"}


@pytest.mark.acceptance
def test_harness_templates_list_empty_live(live_backend, acceptance_fs_root):
    """Templates list empty on a fresh LOCAL backend with no seed."""
    flow = next(c for c in HARNESS_LIFECYCLE_FLOWS if c.name == "harness-templates-list-empty")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert ctx is not None


@pytest.mark.acceptance
def test_harness_template_get_missing_live(live_backend, acceptance_fs_root):
    """GET /api/harness/templates/999999 returns raw 404."""
    flow = next(c for c in HARNESS_LIFECYCLE_FLOWS if c.name == "harness-template-get-missing")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert ctx is not None


@pytest.mark.acceptance
def test_harness_patches_list_empty_live(live_backend, acceptance_fs_root):
    """Patches list empty on a fresh LOCAL backend with no seed."""
    flow = next(c for c in HARNESS_LIFECYCLE_FLOWS if c.name == "harness-patches-list-empty")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert ctx is not None


@pytest.mark.acceptance
def test_harness_patch_records_list_empty_live(live_backend, acceptance_fs_root):
    """Patch-records list empty on a fresh LOCAL backend."""
    flow = next(c for c in HARNESS_LIFECYCLE_FLOWS if c.name == "harness-patch-records-list-empty")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert ctx is not None


@pytest.mark.acceptance
def test_harness_diagnose_records_list_empty_live(live_backend, acceptance_fs_root):
    """Diagnose-records list empty on a fresh LOCAL backend."""
    flow = next(c for c in HARNESS_LIFECYCLE_FLOWS if c.name == "harness-diagnose-records-list-empty")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert ctx is not None


@pytest.mark.acceptance
def test_harness_lifecycle_baseline(live_backend, acceptance_fs_root):
    """Pin LOCAL no-seed observable state to JSON baseline.

    First-run captures + skips; subsequent runs diff with full equality.
    """
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=10.0) as client:
        templates_resp = client.get("/api/harness/templates").json()
        # Missing template returns raw 404 → check status_code, not envelope.
        template_missing_r = client.get("/api/harness/templates/999999")
        patches_resp = client.get(
            "/api/harness/patches?bot_id=bot_baseline&entity_id=e2e_user"
        ).json()
        patch_records_resp = client.get(
            "/api/harness/patch-records?bot_id=bot_baseline&entity_id=e2e_user"
        ).json()
        diagnose_records_resp = client.get(
            "/api/harness/diagnose/records?bot_id=bot_baseline&entity_id=e2e_user"
        ).json()

    snapshot = {
        "templates_no_seed": {
            "success": templates_resp["success"],
            "total": templates_resp["total"],
            "items_count": len(templates_resp["items"]),
        },
        "template_missing": {
            "status_code": template_missing_r.status_code,
            "detail": template_missing_r.json().get("detail"),
        },
        "patches_no_seed": {
            "success": patches_resp["success"],
            "bot_id": patches_resp["bot_id"],
            "entity_id": patches_resp["entity_id"],
            "templates_count": len(patches_resp["templates"]),  # key is 'templates'!
        },
        "patch_records_no_seed": {
            "success": patch_records_resp["success"],
            "bot_id": patch_records_resp["bot_id"],
            "entity_id": patch_records_resp["entity_id"],
            "items_count": len(patch_records_resp["items"]),
        },
        "diagnose_records_no_seed": {
            "success": diagnose_records_resp["success"],
            "total": diagnose_records_resp["total"],
            "page": diagnose_records_resp["page"],
            "size": diagnose_records_resp["size"],
            "items_count": len(diagnose_records_resp["items"]),
        },
    }

    if not BASELINE_PATH.exists() or BASELINE_PATH.stat().st_size == 0:
        BASELINE_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        pytest.skip(
            f"baseline captured at {BASELINE_PATH}; review + commit it, next run will diff"
        )

    expected = json.loads(BASELINE_PATH.read_text())
    assert snapshot == expected, (
        f"harness no-data baseline diverged.\n"
        f"  expected: {json.dumps(expected, indent=2, sort_keys=True)}\n"
        f"  actual:   {json.dumps(snapshot, indent=2, sort_keys=True)}"
    )
