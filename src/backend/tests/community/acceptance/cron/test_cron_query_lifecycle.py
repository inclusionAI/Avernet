"""Route-B acceptance: cron no-bot query contract on live backend."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest


BASELINE_PATH = Path(__file__).parent / "baseline_cron_query.json"
HEADERS = {"x-user-id": "e2e_user"}


@pytest.mark.acceptance
def test_cron_query_empty_live(live_backend, acceptance_fs_root):
    with httpx.Client(base_url=live_backend, headers=HEADERS, timeout=30.0) as client:
        list_resp = client.get("/api/cron", params={"bot_id": "all"})
        running_resp = client.get("/api/cron/running", params={"bot_id": "all"})

    assert list_resp.status_code == 200, list_resp.text
    assert running_resp.status_code == 200, running_resp.text
    list_body = list_resp.json()
    running_body = running_resp.json()
    snapshot = {
        "list_empty": {
            "success": list_body["success"],
            "entries_count": len(list_body["data"]),
        },
        "running_empty": {
            "success": running_body["success"],
            "entries_count": len(running_body["data"]),
        },
    }
    assert snapshot == json.loads(BASELINE_PATH.read_text())
