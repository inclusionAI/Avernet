"""Route-B acceptance: expert_chat list no-data contract on live backend."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest


BASELINE_PATH = Path(__file__).parent / "baseline_chat_query.json"
HEADERS = {"x-user-id": "e2e_user"}


@pytest.mark.acceptance
def test_expert_chat_list_empty_live(live_backend, acceptance_fs_root):
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=30.0) as client:
        resp = client.get("/api/v1/expert-chats")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    snapshot = {
        "list_empty": {
            "success": body["success"],
            "total": body["data"]["total"],
            "entries_count": len(body["data"]["items"]),
        }
    }
    assert snapshot == json.loads(BASELINE_PATH.read_text())
