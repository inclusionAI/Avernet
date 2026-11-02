"""Helpers for acceptance tests that need a live personal bot.

These helpers intentionally use the public backend API instead of repository
seeding: route-B acceptance should exercise Backend -> BaaS device allocation
before module-specific assertions run.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import pytest


def fresh_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def assert_success(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.get("success") is True, payload
    return payload


def wait_bot_ready(
    client: httpx.Client,
    bot_id: str,
    *,
    timeout_sec: int = 180,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        payload = assert_success(client.get(f"/api/bots/{bot_id}/status"))
        last = payload.get("data") or {}
        if last.get("is_ready") is True:
            return last
        assert last.get("bot_status") != "FAILED", last
        time.sleep(2)
    pytest.fail(f"bot {bot_id} did not become ready; last={last}")


def create_live_personal_bot(
    client: httpx.Client,
    *,
    request: pytest.FixtureRequest,
    user_id: str,
    bot_name_prefix: str,
    engine_type: str = "openclaw",
    bot_desc: str = "singlebox acceptance bot",
) -> dict[str, Any]:
    create_payload = assert_success(
        client.post(
            "/api/bots",
            json={
                "bot_name": fresh_id(bot_name_prefix),
                "bot_desc": bot_desc,
                "entity_id": user_id,
                "entity_type": "staff",
                "engine_type": engine_type,
                "bot_type": "personal",
            },
        )
    )
    bot = create_payload["data"]["bot"]
    # Register before readiness: a failed publish can also leave a live runtime.
    # The original client may already be closed when pytest runs finalizers.
    def release_created_device() -> None:
        with httpx.Client(base_url=str(client.base_url),
                          headers={"x-user-id": user_id}, timeout=60.0) as cleanup:
            latest = assert_success(cleanup.get(f"/api/bots/{bot['bot_id']}"))["data"]
            # Dormant reactivation can allocate a replacement binding.
            for binding_id in {bot["binding_id"], latest["binding_id"]} - {None}:
                path = f"/api/v1/devices/{binding_id}"
                binding = assert_success(cleanup.get(path))["data"]
                if binding["status"] != "RELEASED":
                    assert_success(cleanup.post(path + "/release", json={
                        "release_reason": "acceptance test finished"}))

    request.addfinalizer(release_created_device)
    ready = wait_bot_ready(client, bot["bot_id"])
    assert ready["device_provider"] == "baas", ready
    return bot
