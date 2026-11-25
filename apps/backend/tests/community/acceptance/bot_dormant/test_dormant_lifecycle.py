"""Dormant recycle, notification, activation, and whitelist lifecycle."""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
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
        # The singlebox runtime has no confirmed fastdisk: keep synchronous
        # recycle, with no queued operation or extra physical-status polling.
        assert recycled.status_code == 200, recycled.text
        result = recycled.json()["data"]
        assert result["status"] == "recycled", result
        assert result["operation_id"] is None and result["phase"] is None, result

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


def _seed_unbound_bot(client: httpx.Client, owner_id: str) -> dict[str, Any]:
    bot_id = fresh_id("recycle_bot")
    seeded = client.post(
        "/local/sql/execute",
        json={"statements": [{
            "sql": (
                "INSERT INTO ac_bots ("
                "bot_id, bot_name, entity_id, entity_type, creator_id, owner_id, "
                "owner_name, engine_types, active_engine, status, "
                "gmt_create, gmt_modified, is_delete, public, ext, env, bot_type, "
                "call_type, caller_config_revision"
                ") VALUES ("
                ":bot_id, :bot_id, :owner_id, 'staff', :owner_id, :owner_id, "
                ":owner_id, :engine_types, 'openclaw', 'ACTIVE', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, '0', '{}', 'dev', "
                "'personal', 'owner', 0)"
            ),
            "params": {"bot_id": bot_id, "owner_id": owner_id, "engine_types": '["openclaw"]'},
        }, {
            "sql": (
                "SELECT id, bot_id, env, avernet_tenant FROM ac_bots "
                "WHERE bot_id=:bot_id AND owner_id=:owner_id"
            ),
            "params": {"bot_id": bot_id, "owner_id": owner_id},
        }]},
    )
    assert seeded.status_code == 200, seeded.text
    results = seeded.json()["results"]
    assert results[0]["rowcount"] == 1, results
    assert len(results[1]["rows"]) == 1, results
    return results[1]["rows"][0]


def _seed_existing_recycle_task(
    client: httpx.Client, bot: dict[str, Any], owner_id: str,
) -> tuple[dict[str, Any], str]:
    """Restore a pre-upgrade queued task; never alter new-request admission."""
    payload = {
        "bot_id": bot["bot_id"], "owner_id": owner_id, "bot_pk": bot["id"],
        "tenant": bot["avernet_tenant"], "env": bot["env"], "app": "agentclaw",
        "engine": "openclaw", "operation_id": uuid.uuid4().hex,
        "target": {"binding_id": None, "provider": None, "device_id": None},
        "created_at": time.time(), "source": "openapi", "request_id": "upgrade-acceptance",
    }
    key = hashlib.sha256(json.dumps([payload["tenant"], owner_id, bot["bot_id"]]).encode()).hexdigest()
    seeded = client.post(
        "/local/sql/execute",
        json={"statements": [{
            "sql": (
                "INSERT INTO ac_task_queue (task_type, payload, status, run_at, "
                "attempts, deadline_at, idempotency_key, active_idempotency_key, "
                "env, app, gmt_create, gmt_modified) VALUES ("
                "'bot.recycle', :payload, 'PENDING', datetime('now', '+1 hour'), "
                "0, datetime('now', '+2 hours'), :key, :key, :env, :app, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            "params": {
                "payload": json.dumps(payload), "key": key,
                "env": payload["env"], "app": payload["app"],
            },
        }]},
    )
    assert seeded.status_code == 200, seeded.text
    assert seeded.json()["results"][0]["rowcount"] == 1, seeded.text
    return payload, key


@pytest.mark.acceptance
def test_openapi_sync_recycle_without_binding_and_reactivate_live(live_backend):
    """An unbound non-coding Bot stays synchronous, then activates normally.

    Seed only the initial record through the existing local SQL test boundary;
    recycle, idempotent retry and activation remain real HTTP flows. No queue or
    operation-query state should be introduced for this Bot.
    """
    owner_id = fresh_id("recycle_owner")
    query = {"user_id": owner_id}
    with httpx.Client(
        base_url=live_backend, headers={"x-user-id": owner_id}, timeout=60.0,
    ) as client:
        bot_id = _seed_unbound_bot(client, owner_id)["bot_id"]
        path = f"/openapi/v1/bots/{bot_id}"
        submitted = client.post(
            f"{path}/recycle", params=query, headers=_principal_headers(owner_id),
        )
        assert submitted.status_code == 200, submitted.text
        result = submitted.json()["data"]
        assert result["changed"] is True and result["status"] == "RECYCLED", result
        assert result["operation_id"] is None and result["phase"] is None, result
        operation_path = f"{path}/recycle-operations/missing"
        missing = client.get(
            operation_path, params=query, headers=_principal_headers(owner_id),
        )
        assert missing.status_code == 404, missing.text

        repeated = client.post(
            f"{path}/recycle", params=query, headers=_principal_headers(owner_id),
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["data"]["changed"] is False, repeated.text
        assert repeated.json()["data"]["operation_id"] is None, repeated.text
        unchanged = client.get(
            operation_path, params=query, headers=_principal_headers(owner_id),
        )
        assert unchanged.status_code == 404, unchanged.text

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
        assert retained.status_code == 404, retained.text


@pytest.mark.acceptance
def test_existing_queued_recycle_completes_and_queries_agree_live(live_backend):
    """Pre-upgrade queued Bots must drain even though new requests stay sync."""
    owner_id = fresh_id("queued_recycle_owner")
    query = {"user_id": owner_id}
    with httpx.Client(
        base_url=live_backend, headers={"x-user-id": owner_id}, timeout=60.0,
    ) as client:
        bot = _seed_unbound_bot(client, owner_id)
        bot_id = bot["bot_id"]
        payload, key = _seed_existing_recycle_task(client, bot, owner_id)
        path = f"/openapi/v1/bots/{bot_id}"
        operation_id = payload["operation_id"]
        operation_path = f"{path}/recycle-operations/{operation_id}"
        internal_path = f"/api/internal/dormant/bots/{bot_id}/recycle-operations/{operation_id}"

        existing = client.post(f"{path}/recycle", params=query, headers=_principal_headers(owner_id))
        duplicate = client.post(
            "/api/internal/dormant/recycle-one", headers=INTERNAL_HEADERS,
            json={"bot_id": bot_id, "owner_id": owner_id, "dry_run": False},
        )
        for response in (existing, duplicate):
            assert response.status_code == 202, response.text
            admitted = response.json()["data"]
            assert admitted["operation_id"] == operation_id and admitted["changed"] is False, admitted
            assert admitted["status"] == "ACTIVE" and admitted["phase"] == "QUEUED", admitted

        # Parking the fixture avoids racing dedup assertions with the real worker.
        # Making it due lets the existing worker, not this test, execute all steps.
        due = client.post(
            "/local/sql/execute",
            json={"statements": [{
                "sql": (
                    "UPDATE ac_task_queue SET run_at=CURRENT_TIMESTAMP "
                    "WHERE idempotency_key=:key AND env=:env AND app=:app"
                ),
                "params": {"key": key, "env": payload["env"], "app": payload["app"]},
            }]},
        )
        assert due.status_code == 200 and due.json()["results"][0]["rowcount"] == 1, due.text
        deadline = time.monotonic() + 180
        while True:
            observed = client.get(operation_path, params=query, headers=_principal_headers(owner_id))
            assert observed.status_code == 200, observed.text
            result = observed.json()["data"]
            assert result["phase"] not in {"FAILED", "NEEDS_CONFIRMATION"}, result
            if result["phase"] == "SUCCEEDED":
                break
            assert time.monotonic() < deadline, result
            time.sleep(0.2)
        assert result["bot_status"] == "RECYCLED" and result["error_code"] is None, result
        internal = client.get(internal_path, params={"owner_id": owner_id}, headers=INTERNAL_HEADERS)
        assert internal.status_code == 200 and internal.json()["data"] == result, internal.text

        counts = client.post(
            "/local/sql/execute",
            json={"statements": [{
                "sql": (
                    "SELECT COUNT(*) AS n FROM ac_task_queue "
                    "WHERE idempotency_key=:key AND env=:env AND app=:app"
                ),
                "params": {"key": key, "env": payload["env"], "app": payload["app"]},
            }, {
                "sql": (
                    "SELECT COUNT(*) AS n FROM ac_bot_dormant_check_audit "
                    "WHERE run_id=:operation_id AND bot_id=:bot_id AND owner_id=:owner_id"
                ),
                "params": {"operation_id": operation_id, "bot_id": bot_id, "owner_id": owner_id},
            }]},
        )
        assert counts.status_code == 200, counts.text
        assert [row["rows"][0]["n"] for row in counts.json()["results"]] == [1, 1], counts.text
        repeated = client.post(f"{path}/recycle", params=query, headers=_principal_headers(owner_id))
        assert repeated.status_code == 200 and repeated.json()["data"]["changed"] is False, repeated.text
        activated = client.post(f"{path}/activate", params=query, headers=_principal_headers(owner_id))
        assert activated.status_code == 202 and activated.json()["data"]["status"] == "REACTIVATING", activated.text
        _wait_reactivated_bot_ready(client, bot_id)
        retained = client.get(operation_path, params=query, headers=_principal_headers(owner_id))
        assert retained.status_code == 200, retained.text
        assert retained.json()["data"]["phase"] == "SUCCEEDED", retained.text
        assert retained.json()["data"]["bot_status"] == "ACTIVE", retained.text
