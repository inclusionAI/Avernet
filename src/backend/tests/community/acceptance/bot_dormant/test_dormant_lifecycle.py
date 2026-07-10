"""Dormant recycle, notification, activation, and whitelist lifecycle."""
from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

from tests.community.acceptance._fixtures.live_personal_bot import (
    create_live_personal_bot,
    fresh_id,
)


INTERNAL_HEADERS = {
    "Authorization": "Bearer singlebox-dormant-token-local",
}


def _wait_reactivated_bot_ready(
    client: httpx.Client,
    bot_id: str,
    *,
    timeout_sec: int = 180,
) -> dict[str, Any]:
    """Wait through the release-to-reallocation window after activation."""
    deadline = time.monotonic() + timeout_sec
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/bots/{bot_id}/status")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload.get("success") is not True:
            assert payload.get("error_code") == 404, payload
            last = payload
            time.sleep(2)
            continue

        status = payload.get("data") or {}
        last = status
        if status.get("is_ready") is True:
            return status
        assert status.get("bot_status") not in {"FAILED", "RECYCLED"}, status
        time.sleep(2)

    pytest.fail(f"reactivated bot {bot_id} did not become ready; last={last}")


@pytest.mark.acceptance
def test_dormant_recycle_notification_and_reactivate_live(live_backend):
    owner_id = fresh_id("dormant_owner")
    headers = {"x-user-id": owner_id}

    with httpx.Client(base_url=live_backend, headers=headers, timeout=180.0) as client:
        bot = create_live_personal_bot(
            client,
            user_id=owner_id,
            bot_name_prefix="Dormant Acceptance",
            bot_desc="dormant recycle and reactivate acceptance bot",
        )
        bot_id = bot["bot_id"]

        recycled = client.post(
            "/api/internal/dormant/recycle-one",
            headers=INTERNAL_HEADERS,
            json={
                "bot_id": bot_id,
                "owner_id": owner_id,
                "dry_run": False,
                "reason": "singlebox coverage lifecycle",
            },
        )
        assert recycled.status_code == 200, recycled.text
        assert recycled.json()["data"]["status"] == "recycled"

        pending = client.get(
            "/api/internal/dormant/pending-notifications",
            headers=INTERNAL_HEADERS,
        )
        assert pending.status_code == 200, pending.text
        rows = pending.json()["data"]
        notification = next(row for row in rows if row["bot_id"] == bot_id)
        assert notification["notify_type"] == "recycle"

        marked = client.post(
            "/api/internal/dormant/mark-sent",
            headers=INTERNAL_HEADERS,
            json={"id": notification["id"], "success": True},
        )
        assert marked.status_code == 200, marked.text
        assert marked.json() == {"ok": True, "status": "sent"}

        repeated_mark = client.post(
            "/api/internal/dormant/mark-sent",
            headers=INTERNAL_HEADERS,
            json={"id": notification["id"], "success": True},
        )
        assert repeated_mark.status_code == 200, repeated_mark.text
        assert repeated_mark.json() == {
            "ok": True,
            "status": "already_resolved",
        }

        activated = client.post(f"/api/bots/{bot_id}/activate")
        assert activated.status_code == 200, activated.text
        activated_body = activated.json()
        assert activated_body["success"] is True, activated_body
        assert activated_body["data"]["status"] == "REACTIVATING"
        _wait_reactivated_bot_ready(client, bot_id)


@pytest.mark.acceptance
def test_dormant_whitelist_is_idempotent_live(live_backend):
    owner_id = fresh_id("dormant_whitelist_owner")
    bot_id = fresh_id("dormant_whitelist_bot")
    headers = {"x-user-id": owner_id}
    payload = {
        "entries": [
            {
                "bot_id": bot_id,
                "owner_id": owner_id,
                "governance_source": "singlebox",
                "reason": "coverage lifecycle",
            }
        ]
    }

    with httpx.Client(base_url=live_backend, headers=headers, timeout=30.0) as client:
        first = client.post("/api/bots/dormant-whitelist/batch", json=payload)
        assert first.status_code == 200, first.text
        assert first.json()["data"] == {"inserted": 1, "skipped": 0}

        second = client.post("/api/bots/dormant-whitelist/batch", json=payload)
        assert second.status_code == 200, second.text
        assert second.json()["data"] == {"inserted": 0, "skipped": 1}
