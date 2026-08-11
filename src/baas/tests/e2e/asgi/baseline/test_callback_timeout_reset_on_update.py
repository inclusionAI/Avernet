"""E2E test for callback_timeout_seconds default behavior on bot update.

Verifies that when a bot is created with a custom callback_timeout_seconds=900
and then updated without specifying callback_timeout_seconds, the UPDATE publish
record uses the default 1800s (not the previous 900s).
"""

import uuid

import pytest


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_callback_timeout_resets_to_default_on_update(api, unique_id):
    """Create bot with timeout=900, update without timeout, verify publish records."""
    bot_name = f"test-cb-timeout-{unique_id}"

    # 1. Create bot with callback_timeout_seconds=900
    resp = await api.client.post(
        api.bot_url(),
        params=api.params(),
        json={
            "name": bot_name,
            "template_uuid": "test-template",
            "device_count": 1,
            "operator": "e2e-test",
            "request_id": uuid.uuid4().hex,
            "config": {
                "callback_timeout_seconds": 900,
            },
        },
    )
    assert resp.status_code == 200, f"Create bot failed: {resp.status_code} {resp.text}"
    data = resp.json()["data"]
    bot_uuid = data["bot_uuid"]
    create_publish_id = data["publish_id"]
    assert create_publish_id is not None

    # Verify CREATE publish record has callback_timeout_seconds=900
    resp = await api.client.get(
        api.publish_url(create_publish_id),
        params=api.params(),
    )
    assert resp.status_code == 200
    create_extra = resp.json()["data"].get("extra_config") or {}
    assert create_extra.get("callback_timeout_seconds") == 900, (
        f"CREATE publish should have callback_timeout_seconds=900, "
        f"got {create_extra.get('callback_timeout_seconds')}"
    )

    # 2. Update bot without specifying callback_timeout_seconds
    resp = await api.client.put(
        api.bot_url(bot_uuid),
        params=api.params(),
        json={
            "operator": "e2e-test",
            "request_id": uuid.uuid4().hex,
            "config": {
                "sla_grade": "standard",
            },
        },
    )
    assert resp.status_code == 200, f"Update bot failed: {resp.status_code} {resp.text}"
    update_publish_id = resp.json()["data"].get("publish_id")
    assert update_publish_id is not None

    # Verify UPDATE publish record has callback_timeout_seconds=1800 (default)
    resp = await api.client.get(
        api.publish_url(update_publish_id),
        params=api.params(),
    )
    assert resp.status_code == 200
    update_extra = resp.json()["data"].get("extra_config") or {}
    assert update_extra.get("callback_timeout_seconds") == 1800, (
        f"UPDATE publish should have callback_timeout_seconds=1800 (default), "
        f"got {update_extra.get('callback_timeout_seconds')}"
    )
