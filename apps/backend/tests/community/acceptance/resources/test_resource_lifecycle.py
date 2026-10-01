"""Route-B acceptance: resources daas lifecycle on live backend.

Starts a real singlebox backend, exercises:
  - URL CRUD via run_flow_live (resources-url-resource-crud FlowCase)
  - Node CRUD via run_flow_live (resources-node-resource-crud FlowCase)
  - JSON baseline pinning URL+Node CRUD observable state

Workspace file lifecycle coverage lives in acceptance/files and creates a real
BaaS-backed bot before touching its workspace.

The 3 external-dep paths (arca / yuque / publish) intentionally not covered;
see docs/singlebox-eval/findings/resources-external-deps-unmocked.md.

Off by default; enable with RUN_ACCEPTANCE=1.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.community._flows.resources.api_lifecycle import RESOURCES_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner_live import run_flow_live

BASELINE_PATH = Path(__file__).parent / "baseline_resource_lifecycle.json"
HEADERS = {"x-user-id": "e2e_user"}


@pytest.mark.acceptance
def test_resources_url_crud_live(live_backend, acceptance_fs_root):
    """URL resource CRUD via run_flow_live."""
    flow = next(c for c in RESOURCES_LIFECYCLE_FLOWS if c.name == "resources-url-resource-crud")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert "url_resource_id" in ctx


@pytest.mark.acceptance
def test_resources_node_crud_live(live_backend, acceptance_fs_root):
    """Node resource CRUD via run_flow_live."""
    flow = next(c for c in RESOURCES_LIFECYCLE_FLOWS if c.name == "resources-node-resource-crud")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert "node_resource_id" in ctx
    assert "first_listed_id" in ctx
    assert ctx["first_listed_id"] == ctx["node_resource_id"]


@pytest.mark.acceptance
def test_resources_lifecycle_baseline(live_backend, acceptance_fs_root):
    """Pin URL+Node CRUD observable state to JSON baseline.

    Snapshots are projected to stable fields only (no ids, no timestamps).
    First-run captures + skips; subsequent runs diff.
    """
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=30.0) as client:
        # Seed: one URL + one Node under a fixed bot_id (function-scoped
        # fixture means fresh backend + fresh SQLite per test, so bot_baseline
        # is always empty before this test starts).
        client.post(
            "/api/resources/url?bot_id=bot_baseline",
            json={"name": "Baseline URL", "url": "https://baseline.example.com"},
        )
        client.post(
            "/api/resources/node?bot_id=bot_baseline",
            json={"name": "Baseline Node", "node_address": "ipfs://baseline"},
        )

        # List each type — strip ids/timestamps for a stable snapshot.
        list_url = client.get(
            "/api/resources?bot_id=bot_baseline&owner_id=e2e_user&resource_type=url"
        ).json()
        list_node = client.get(
            "/api/resources?bot_id=bot_baseline&owner_id=e2e_user&resource_type=node"
        ).json()

    def project(item):
        return {
            "name": item.get("name"),
            "resource_type": item.get("resource_type"),
            "status": item.get("status"),
            "url": item.get("url"),
            "node_address": item.get("node_address"),
        }

    snapshot = {
        "url_list": [project(r) for r in list_url["data"]],
        "node_list": [project(r) for r in list_node["data"]],
    }

    if not BASELINE_PATH.exists() or BASELINE_PATH.stat().st_size == 0:
        BASELINE_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        pytest.skip(
            f"baseline captured at {BASELINE_PATH}; review + commit it, next run will diff"
        )

    expected = json.loads(BASELINE_PATH.read_text())
    assert snapshot == expected, (
        f"resources lifecycle DB state diverged from baseline.\n"
        f"  expected: {json.dumps(expected, indent=2, sort_keys=True)}\n"
        f"  actual:   {json.dumps(snapshot, indent=2, sort_keys=True)}"
    )
