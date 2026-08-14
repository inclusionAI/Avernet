"""Route-B acceptance: bot_chat query lifecycle on a live backend.

Starts a real singlebox backend (in-memory SQLite via local_setup.sh),
runs the LOCAL-pure flows through httpx, asserts no-data state matches a JSON
baseline snapshot.

This file pins the empty/no-data query contract: list returns empty and detail
returns 4004 (SessionNotFoundError). The separate OTLP round-trip acceptance
test writes traces and business relations through the live HTTP API.

Off by default; enable with RUN_ACCEPTANCE=1.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from tests.community._flows.bot_chat.api_lifecycle import BOT_CHAT_LIFECYCLE_FLOWS
from tests.community.framework.flow_runner_live import run_flow_live

BASELINE_PATH = Path(__file__).parent / "baseline_chat_query.json"


@pytest.mark.acceptance
def test_bot_chat_list_empty_live(live_backend, acceptance_fs_root):
    """Empty list on a fresh LOCAL backend with no chat engine producing traces."""
    flow = next(c for c in BOT_CHAT_LIFECYCLE_FLOWS if c.name == "bot_chat-list-empty")
    ctx = run_flow_live(
        flow, base_url=live_backend, fs_root=acceptance_fs_root,
        default_headers={"x-user-id": "e2e_user"},
    )
    assert ctx is not None


@pytest.mark.acceptance
def test_bot_chat_detail_missing_returns_4004_live(live_backend, acceptance_fs_root):
    """Detail by non-existent trace_id returns 4004 (SessionNotFoundError)."""
    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": "e2e_user"},
        timeout=10.0,
    ) as client:
        r = client.get("/api/v1/bot-chats/trace_does_not_exist")
        assert r.status_code == 200  # router wraps with ApiResponse
        body = r.json()
        assert body["success"] is False
        assert body["error_code"] == 4004


@pytest.mark.acceptance
def test_bot_chat_lifecycle_baseline(live_backend, acceptance_fs_root):
    """Pin the LOCAL no-data observable state to JSON baseline.

    First-run capture mode: empty baseline → write + skip; reviewer commits;
    subsequent runs diff against it.
    """
    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": "e2e_user"},
        timeout=10.0,
    ) as client:
        list_resp = client.get("/api/v1/bot-chats").json()
        detail_resp = client.get("/api/v1/bot-chats/trace_does_not_exist").json()

    snapshot = {
        "list_no_seed": {
            "success": list_resp["success"],
            "data": {
                "sessions": list_resp["data"]["sessions"],
                "total": list_resp["data"]["total"],
                "page": list_resp["data"]["page"],
                "limit": list_resp["data"]["limit"],
            },
        },
        "detail_missing": {
            "success": detail_resp["success"],
            "error_code": detail_resp["error_code"],
        },
    }

    if not BASELINE_PATH.exists() or BASELINE_PATH.stat().st_size == 0:
        BASELINE_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        pytest.skip(
            f"baseline captured at {BASELINE_PATH}; review + commit it, "
            "next run will diff against it"
        )

    expected = json.loads(BASELINE_PATH.read_text())
    assert snapshot == expected, (
        f"bot_chat no-data baseline diverged.\n"
        f"  expected: {json.dumps(expected, indent=2, sort_keys=True)}\n"
        f"  actual:   {json.dumps(snapshot, indent=2, sort_keys=True)}"
    )
