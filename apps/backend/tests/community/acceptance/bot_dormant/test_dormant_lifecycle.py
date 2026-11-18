"""Dormant recycle, notification, activation, and whitelist lifecycle."""
from __future__ import annotations

import os
import time
from typing import Any

import httpx
import jwt
import pytest

from tests.community.acceptance._fixtures.live_personal_bot import (
    create_live_personal_bot,
    fresh_id,
)


INTERNAL_HEADERS = {
    "Authorization": "Bearer singlebox-dormant-token-local",
}


_PRINCIPAL_KEY = os.environ.get(
    "SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY",
    "singlebox-gateway-principal-key-not-for-production",
)


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
        notifications = [row for row in rows if row["bot_id"] == bot_id]
        assert len(notifications) == 1, (
            f"expected one notification for bot {bot_id}, got {notifications}; "
            f"all rows={rows}"
        )
        notification = notifications[0]
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


def _principal_headers(owner_id: str) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": owner_id, "username": owner_id},
                }
            ],
        },
        _PRINCIPAL_KEY,
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": token}


@pytest.mark.acceptance
def test_openapi_async_recycle_and_reactivate_live(live_backend):
    """The public owner recycles via the real queue, observes it, then restores."""
    owner_id = fresh_id("recycle_owner")
    query = {"user_id": owner_id}
    with httpx.Client(
        base_url=live_backend, headers={"x-user-id": owner_id}, timeout=60.0,
    ) as client:
        bot = create_live_personal_bot(
            client, user_id=owner_id, bot_name_prefix="Recycle API",
        )
        bot_id = bot["bot_id"]
        path = f"/openapi/v1/bots/{bot_id}"
        submitted = client.post(
            f"{path}/recycle", params=query, headers=_principal_headers(owner_id),
        )
        assert submitted.status_code == 202, submitted.text
        admission = submitted.json()["data"]
        assert admission["changed"] is True, admission
        operation_id = admission["operation_id"]
        assert operation_id, admission
        operation_path = f"{path}/recycle-operations/{operation_id}"

        missing = client.get(
            f"{path}/recycle-operations/missing", params=query,
            headers=_principal_headers(owner_id),
        )
        assert missing.status_code == 404, missing.text

        deadline = time.monotonic() + 180
        while True:
            observed = client.get(
                operation_path, params=query, headers=_principal_headers(owner_id),
            )
            assert observed.status_code == 200, observed.text
            result = observed.json()["data"]
            assert result["operation_id"] == operation_id, result
            assert result["phase"] not in {"FAILED", "NEEDS_CONFIRMATION"}, result
            if result["phase"] == "SUCCEEDED":
                break
            assert time.monotonic() < deadline, result
            time.sleep(1)
        assert result["bot_status"] == "RECYCLED", result
        assert result["error_code"] is None, result

        repeated = client.post(
            f"{path}/recycle", params=query, headers=_principal_headers(owner_id),
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["data"]["changed"] is False, repeated.text
        assert repeated.json()["data"]["operation_id"] is None, repeated.text
        unchanged = client.get(
            operation_path, params=query, headers=_principal_headers(owner_id),
        )
        assert unchanged.status_code == 200, unchanged.text
        assert unchanged.json()["data"] == result, unchanged.text

        pending = client.get(
            "/api/internal/dormant/pending-notifications", headers=INTERNAL_HEADERS,
        )
        assert pending.status_code == 200, pending.text
        assert not any(row["bot_id"] == bot_id for row in pending.json()["data"])

        activated = client.post(
            f"{path}/activate", params=query, headers=_principal_headers(owner_id),
        )
        assert activated.status_code == 202, activated.text
        assert activated.json()["data"]["status"] == "REACTIVATING", activated.text
        assert activated.json()["data"]["changed"] is True, activated.text
        _wait_reactivated_bot_ready(client, bot_id)

        active = client.post(
            f"{path}/activate", params=query, headers=_principal_headers(owner_id),
        )
        assert active.status_code == 200, active.text
        assert active.json()["data"]["status"] == "ACTIVE", active.text
        assert active.json()["data"]["changed"] is False, active.text
        retained = client.get(
            operation_path, params=query, headers=_principal_headers(owner_id),
        )
        assert retained.status_code == 200, retained.text
        assert retained.json()["data"]["phase"] == "SUCCEEDED", retained.text
        assert retained.json()["data"]["bot_status"] == "ACTIVE", retained.text
