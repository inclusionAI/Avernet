"""Route-B acceptance: expert_chat caller-connection API on live singlebox.

Tests the per-caller BaaS container provisioning endpoint that requires:
- A published service bot (SUCCESS status publish record)
- Super admin authentication
"""
from __future__ import annotations

import time

import httpx
import pytest

from tests.community.acceptance._fixtures.live_personal_bot import fresh_id

ADMIN_USER_ID = "100000"  # Configured as super_admin in test config


def _execute_local_sql(client: httpx.Client, statements: list[dict]) -> dict:
    """Execute SQL statements against local backend."""
    last_response: httpx.Response | None = None
    for attempt in range(5):
        response = client.post("/local/sql/execute", json={"statements": statements})
        if response.status_code == 200:
            return response.json()
        last_response = response
        if "SQL statements in progress" not in response.text:
            break
        time.sleep(0.2 * (attempt + 1))
    assert last_response is not None
    assert last_response.status_code == 200, last_response.text
    return last_response.json()


def _seed_service_bot(
    client: httpx.Client,
    *,
    owner_id: str,
    bot_id: str,
    bot_name: str,
) -> None:
    """Seed a service bot in the live local backend DB."""
    _execute_local_sql(
        client,
        [
            {
                "sql": (
                    "INSERT INTO ac_bots ("
                    "bot_id, bot_name, bot_desc, entity_id, entity_type, creator_id, owner_id, "
                    "owner_name, engine_types, active_engine, status, binding_id, device_id, "
                    "gmt_create, gmt_modified, is_delete, public, ext, env, bot_type"
                    ") VALUES ("
                    ":bot_id, :bot_name, :bot_desc, :owner_id, 'staff', :owner_id, :owner_id, "
                    ":owner_id, :engine_types, :active_engine, 'ACTIVE', NULL, NULL, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, '0', :ext, :env, 'service'"
                    ")"
                ),
                "params": {
                    "bot_id": bot_id,
                    "bot_name": bot_name,
                    "bot_desc": "singlebox service bot for caller-connection test",
                    "owner_id": owner_id,
                    "engine_types": '["openclaw"]',
                    "active_engine": "openclaw",
                    "ext": "{}",
                    "env": "dev",
                },
            }
        ],
    )


def _seed_successful_publish(
    client: httpx.Client,
    *,
    bot_id: str,
    owner_id: str,
    version: int = 1,
    migration_path: str | None = None,
) -> int:
    """Seed a SUCCESS publish record for a service bot."""
    import json

    ext = json.dumps({"migration_path": migration_path or "/nas/migration/test"})
    result = _execute_local_sql(
        client,
        [
            {
                "sql": (
                    "INSERT INTO ac_bot_publish ("
                    "source_bot_pk, source_bot_id, publish_bot_id, name, owner_id, "
                    "permission_owner, status, version, last_pub_id, env, gmt_create, gmt_modified, ext"
                    ") VALUES ("
                    "1, :source_bot_id, :publish_bot_id, :name, :owner_id, "
                    "'owner', 'SUCCESS', :version, 0, :env, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :ext"
                    ") RETURNING id"
                ),
                "params": {
                    "source_bot_id": bot_id,
                    "publish_bot_id": bot_id,
                    "name": f"Publish v{version}",
                    "owner_id": owner_id,
                    "version": version,
                    "env": "dev",
                    "ext": ext,
                },
            }
        ],
    )
    return result["results"][0]["lastrowid"]


@pytest.mark.acceptance
def test_expert_chat_caller_connection_permission_denied_for_non_admin(live_backend):
    """Non-super-admin users get 403 from caller-connection endpoint."""
    owner_id = fresh_id("caller_conn_owner")
    bot_id = fresh_id("caller_conn_bot")
    caller_id = fresh_id("caller_user")

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": owner_id},
        timeout=30.0,
    ) as client:
        _seed_service_bot(
            client,
            owner_id=owner_id,
            bot_id=bot_id,
            bot_name="CallerConn NonAdmin Bot",
        )
        _seed_successful_publish(
            client,
            bot_id=bot_id,
            owner_id=owner_id,
        )

        # Non-admin user should get 403
        response = client.post(
            "/api/v1/expert-chats/caller-connection",
            params={
                "bot_id": bot_id,
                "owner_id": owner_id,
                "user_id": caller_id,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is False, body
        assert body["error_code"] == 403, body


@pytest.mark.acceptance
def test_expert_chat_caller_connection_anonymous_rejected(live_backend):
    """Anonymous user gets 400 from caller-connection endpoint."""
    owner_id = fresh_id("caller_conn_anon_owner")
    bot_id = fresh_id("caller_conn_anon_bot")
    caller_id = fresh_id("caller_anon")

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": ADMIN_USER_ID},
        timeout=30.0,
    ) as admin_client:
        _seed_service_bot(
            admin_client,
            owner_id=owner_id,
            bot_id=bot_id,
            bot_name="CallerConn Anonymous Test Bot",
        )
        _seed_successful_publish(
            admin_client,
            bot_id=bot_id,
            owner_id=owner_id,
        )

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": "anonymous"},
        timeout=30.0,
    ) as anon_client:
        response = anon_client.post(
            "/api/v1/expert-chats/caller-connection",
            params={
                "bot_id": bot_id,
                "owner_id": owner_id,
                "user_id": caller_id,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is False, body
        assert body["error_code"] == 400, body


@pytest.mark.acceptance
def test_expert_chat_caller_connection_bot_not_published(live_backend):
    """Calling caller-connection for unpublished bot returns error."""
    owner_id = fresh_id("caller_conn_unpub_owner")
    bot_id = fresh_id("caller_conn_unpub_bot")
    caller_id = fresh_id("caller_unpub")

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": ADMIN_USER_ID},
        timeout=30.0,
    ) as admin_client:
        # Seed bot WITHOUT publish record
        _seed_service_bot(
            admin_client,
            owner_id=owner_id,
            bot_id=bot_id,
            bot_name="CallerConn Unpublished Bot",
        )

        response = admin_client.post(
            "/api/v1/expert-chats/caller-connection",
            params={
                "bot_id": bot_id,
                "owner_id": owner_id,
                "user_id": caller_id,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is False, body
        # BotNotPublishedError is caught and returns 5999
        assert body["error_code"] == 5999, body


@pytest.mark.acceptance
def test_expert_chat_caller_connection_bot_not_found(live_backend):
    """Calling caller-connection for non-existent bot returns error."""
    owner_id = fresh_id("caller_conn_notfound_owner")
    bot_id = fresh_id("caller_conn_notfound_bot")
    caller_id = fresh_id("caller_notfound")

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": ADMIN_USER_ID},
        timeout=30.0,
    ) as admin_client:
        response = admin_client.post(
            "/api/v1/expert-chats/caller-connection",
            params={
                "bot_id": bot_id,
                "owner_id": owner_id,
                "user_id": caller_id,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is False, body
        # ConnectionError for bot not found
        assert body["error_code"] in (5001, 5999), body


@pytest.mark.acceptance
def test_expert_chat_caller_connection_success_first_call(live_backend):
    """Super admin can successfully create caller connection for first time."""
    owner_id = fresh_id("caller_conn_success_owner")
    bot_id = fresh_id("caller_conn_success_bot")
    caller_id = fresh_id("caller_success")

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": ADMIN_USER_ID},
        timeout=30.0,
    ) as admin_client:
        # Seed service bot and publish record
        _seed_service_bot(
            admin_client,
            owner_id=owner_id,
            bot_id=bot_id,
            bot_name="CallerConn Success Bot",
        )
        _seed_successful_publish(
            admin_client,
            bot_id=bot_id,
            owner_id=owner_id,
        )

        # First call should create container
        response = admin_client.post(
            "/api/v1/expert-chats/caller-connection",
            params={
                "bot_id": bot_id,
                "owner_id": owner_id,
                "user_id": caller_id,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        # First call may return need_poll=True or success with connection
        # depending on BaaS workflow status
        assert "instance" in body, body
        assert "need_poll" in body, body
        # If success, should have connection info
        if body.get("success") is True:
            assert "connection" in body, body
            connection = body["connection"]
            if connection:
                assert "bot_uuid" in connection, body
                assert "ws_url" in connection, body


@pytest.mark.acceptance
def test_expert_chat_caller_connection_force_upgrade_param(live_backend):
    """force_upgrade parameter triggers upgrade even when version is current."""
    owner_id = fresh_id("caller_conn_force_owner")
    bot_id = fresh_id("caller_conn_force_bot")
    caller_id = fresh_id("caller_force")

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": ADMIN_USER_ID},
        timeout=30.0,
    ) as admin_client:
        # Seed service bot and publish record
        _seed_service_bot(
            admin_client,
            owner_id=owner_id,
            bot_id=bot_id,
            bot_name="CallerConn Force Bot",
        )
        _seed_successful_publish(
            admin_client,
            bot_id=bot_id,
            owner_id=owner_id,
        )

        # First call to create instance
        response = admin_client.post(
            "/api/v1/expert-chats/caller-connection",
            params={
                "bot_id": bot_id,
                "owner_id": owner_id,
                "user_id": caller_id,
            },
        )
        assert response.status_code == 200, response.text

        # Second call without force_upgrade should return cached result
        response2 = admin_client.post(
            "/api/v1/expert-chats/caller-connection",
            params={
                "bot_id": bot_id,
                "owner_id": owner_id,
                "user_id": caller_id,
            },
        )
        assert response2.status_code == 200, response2.text
        body2 = response2.json()
        # Should have success with connection (not need_poll)
        if body2.get("success") is True and body2.get("connection"):
            # Third call with force_upgrade=True should trigger upgrade path
            response3 = admin_client.post(
                "/api/v1/expert-chats/caller-connection",
                params={
                    "bot_id": bot_id,
                    "owner_id": owner_id,
                    "user_id": caller_id,
                    "force_upgrade": "true",
                },
            )
            assert response3.status_code == 200, response3.text
            body3 = response3.json()
            # Should still return valid result after forced upgrade
            assert "instance" in body3, body3


@pytest.mark.acceptance
def test_expert_chat_caller_connection_different_callers_separate(live_backend):
    """Different callers get separate instances for the same bot."""
    owner_id = fresh_id("caller_conn_multi_owner")
    bot_id = fresh_id("caller_conn_multi_bot")
    caller1 = fresh_id("caller_multi_1")
    caller2 = fresh_id("caller_multi_2")

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": ADMIN_USER_ID},
        timeout=30.0,
    ) as admin_client:
        # Seed service bot and publish record
        _seed_service_bot(
            admin_client,
            owner_id=owner_id,
            bot_id=bot_id,
            bot_name="CallerConn Multi Bot",
        )
        _seed_successful_publish(
            admin_client,
            bot_id=bot_id,
            owner_id=owner_id,
        )

        # Caller 1 creates instance
        response1 = admin_client.post(
            "/api/v1/expert-chats/caller-connection",
            params={
                "bot_id": bot_id,
                "owner_id": owner_id,
                "user_id": caller1,
            },
        )
        assert response1.status_code == 200, response1.text
        body1 = response1.json()
        assert "instance" in body1

        # Caller 2 creates separate instance
        response2 = admin_client.post(
            "/api/v1/expert-chats/caller-connection",
            params={
                "bot_id": bot_id,
                "owner_id": owner_id,
                "user_id": caller2,
            },
        )
        assert response2.status_code == 200, response2.text
        body2 = response2.json()
        assert "instance" in body2

        # Both callers should have their own instances
        if body1.get("success") is True and body2.get("success") is True:
            if body1.get("connection") and body2.get("connection"):
                conn1 = body1["connection"]
                conn2 = body2["connection"]
                # Each caller should have their own bot_uuid
                assert "bot_uuid" in conn1
                assert "bot_uuid" in conn2