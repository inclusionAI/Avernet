"""Route-B acceptance: resources daas lifecycle on live backend.

Starts a real --local backend, exercises:
  - URL CRUD via run_flow_live (resources-url-resource-crud FlowCase)
  - Node CRUD via run_flow_live (resources-node-resource-crud FlowCase)
  - File daas round-trip via httpx (mkdir Form + multipart upload + list +
    preview), with FS rglob assertion that the uploaded file exists at the
    real bot_data_dir under ~/.aidesktop/aidesktop_dev/bolt_data/.../workspace/
  - JSON baseline pinning URL+Node CRUD observable state

The 3 external-dep paths (arca / yuque / publish) intentionally not covered;
see docs/singlebox-eval/findings/resources-external-deps-unmocked.md.

Off by default; enable with RUN_ACCEPTANCE=1.
"""
from __future__ import annotations

import io
import json
import time
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
def test_resources_file_daas_lifecycle_live(live_backend, acceptance_fs_root):
    """File round-trip via httpx + real FS artifact assertion.

    LOCAL device_provider != arca → file_router falls through to daas branch
    (real local FS via FileService). The uploaded file lives somewhere under
    ~/.aidesktop/.../bolt_data/staff_e2e_user/bot_<ns>/<engine>/workspace/<dir>/.
    rglob from $HOME to find it by name (the engine type segment is dynamic).
    """
    bot_id = f"bot_e2e_live_{time.time_ns()}"
    parent = f"live_dir_{time.time_ns()}"
    filename = "live_test.txt"
    payload = b"live daas content\n"

    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=30.0) as client:
        # mkdir — Form
        r = client.post(
            f"/api/resources/files/mkdir?bot_id={bot_id}",
            data={"path": parent},
        )
        assert r.status_code == 200, r.text
        assert r.json()["success"] is True

        # upload — multipart; ?path= query for target dir (NOT parent_path)
        r = client.post(
            f"/api/resources/files/upload?bot_id={bot_id}&path={parent}",
            files={"files": (filename, io.BytesIO(payload), "text/plain")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        assert len(body["uploaded"]) == 1, body
        assert body["uploaded"][0]["name"] == filename

        # list — FileListResponse {items}
        r = client.get(f"/api/resources/files?bot_id={bot_id}&path={parent}")
        assert r.status_code == 200
        items = r.json()["items"]
        assert any(item.get("name") == filename for item in items), items

        # preview — round-trip content
        r = client.get(
            f"/api/resources/files/preview?bot_id={bot_id}&path={parent}/{filename}"
        )
        assert r.status_code == 200, r.text
        assert payload.decode() in r.json()["data"]["content"]

    # FS artifact assertion: rglob from $HOME (acceptance_fs_root) for the
    # unique filename. Should find exactly one match under this bot's workspace.
    home = Path(acceptance_fs_root)
    matches = list(home.rglob(filename))
    # Filter to matches that include our unique bot_id in the path — defends
    # against unrelated test artifacts elsewhere on the dev host.
    bot_matches = [m for m in matches if bot_id in str(m)]
    assert bot_matches, f"physical file {filename} not found under {home} for bot {bot_id}; all matches: {matches}"
    actual_content = bot_matches[0].read_bytes()
    assert actual_content == payload, (
        f"physical file content mismatch: expected {payload!r}, got {actual_content!r}"
    )


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
