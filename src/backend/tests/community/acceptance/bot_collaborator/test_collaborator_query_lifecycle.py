"""Route-B acceptance: bot_collaborator query lifecycle on live backend.

Starts a real --local backend, runs 3 read-only flows + asserts JSON baseline.

The 2 exempt paths (AgentPass admin sync, cross-process lock concurrency)
are NOT exercised — see findings/bot_collaborator-passport-and-concurrency.md.

Note: route B doesn't have access to DB seed via DatabasePlugin.session()
because the subprocess backend uses a separate SQLite engine. So route B
only covers the no-data contract; the seeded collaborator CRUD and lock
lifecycle are covered by route A's e2e tests.

Off by default; enable with RUN_ACCEPTANCE=1.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.community._flows.bot_collaborator.api_lifecycle import BOT_COLLABORATOR_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner_live import run_flow_live

BASELINE_PATH = Path(__file__).parent / "baseline_collaborator_query.json"
HEADERS = {"x-user-id": "e2e_user"}


@pytest.mark.acceptance
def test_bot_collaborator_list_no_bot_live(live_backend, acceptance_fs_root):
    """List collaborators for a non-existent bot."""
    flow = next(c for c in BOT_COLLABORATOR_LIFECYCLE_FLOWS if c.name == "bot_collaborator-list-no-bot")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert ctx is not None


@pytest.mark.acceptance
def test_bot_collaborator_check_permission_no_bot_live(live_backend, acceptance_fs_root):
    """check_permission for a non-existent bot."""
    flow = next(c for c in BOT_COLLABORATOR_LIFECYCLE_FLOWS if c.name == "bot_collaborator-check-permission-no-bot")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert ctx is not None


@pytest.mark.acceptance
def test_bot_collaborator_lock_info_not_held_live(live_backend, acceptance_fs_root):
    """lock/info for a never-acquired lock."""
    flow = next(c for c in BOT_COLLABORATOR_LIFECYCLE_FLOWS if c.name == "bot_collaborator-lock-info-not-held")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers=HEADERS,
    )
    assert ctx is not None


@pytest.mark.acceptance
def test_bot_collaborator_lifecycle_baseline(live_backend, acceptance_fs_root):
    """Pin LOCAL no-seed observable state to JSON baseline.

    First-run captures + skips; subsequent runs diff with full equality.
    """
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=10.0) as client:
        list_resp = client.get("/api/bot/collaborator/list?bot_id=bot_baseline&owner_id=e2e_user").json()
        check_resp = client.post(
            "/api/bot/collaborator/check_permission",
            json={"bot_id": "bot_baseline", "owner_id": "e2e_user", "user_id": "e2e_user"},
        ).json()
        lock_info_resp = client.get(
            "/api/bot/collaborator/lock/info?bot_id=bot_baseline_unlocked&owner_id=e2e_user"
        ).json()

    snapshot = {
        "list_no_bot": {
            "success": list_resp["success"],
            "error_code": list_resp["error_code"],
            "data_is_none": list_resp["data"] is None,
        },
        "check_permission_no_bot": {
            "success": check_resp["success"],
            "error_code": check_resp["error_code"],
            "data_is_none": check_resp["data"] is None,
        },
        "lock_info_not_held": {
            "success": lock_info_resp["success"],
            "locked": lock_info_resp["data"]["locked"] if isinstance(lock_info_resp.get("data"), dict) else None,
        },
    }

    if not BASELINE_PATH.exists() or BASELINE_PATH.stat().st_size == 0:
        BASELINE_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        pytest.skip(
            f"baseline captured at {BASELINE_PATH}; review + commit it, next run will diff"
        )

    expected = json.loads(BASELINE_PATH.read_text())
    assert snapshot == expected, (
        f"bot_collaborator no-data baseline diverged.\n"
        f"  expected: {json.dumps(expected, indent=2, sort_keys=True)}\n"
        f"  actual:   {json.dumps(snapshot, indent=2, sort_keys=True)}"
    )
