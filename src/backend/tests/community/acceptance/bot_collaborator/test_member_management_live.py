"""Live singlebox coverage for template-ext member management."""
from __future__ import annotations

import json
import time
from uuid import uuid4

import httpx
import pytest


def _fresh_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def _execute_local_sql(client: httpx.Client, statements: list[dict]) -> dict:
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


def _seed_member_managed_bot(
    client: httpx.Client,
    *,
    owner_id: str,
    bot_id: str,
) -> None:
    """Seed a non-service, non-applicationCoding bot with ac_templates.ext enabled."""
    _execute_local_sql(
        client,
        [
            {
                "sql": (
                    "INSERT INTO ac_bots ("
                    "bot_id, bot_name, bot_desc, entity_id, entity_type, creator_id, owner_id, "
                    "owner_name, engine_types, active_engine, status, binding_id, device_id, "
                    "gmt_create, gmt_modified, is_delete, public, ext, env, bot_type, template_type, "
                    "call_type, caller_config_revision"
                    ") VALUES ("
                    ":bot_id, :bot_name, :bot_desc, :owner_id, 'staff', :owner_id, :owner_id, "
                    ":owner_id, :engine_types, 'openclaw', 'ACTIVE', NULL, NULL, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0, '0', :bot_ext, 'dev', 'personal', 'chat', "
                    "'owner', 0"
                    ")"
                ),
                "params": {
                    "bot_id": bot_id,
                    "bot_name": f"Member managed {bot_id}",
                    "bot_desc": "singlebox member-management template-ext seed",
                    "owner_id": owner_id,
                    "engine_types": json.dumps(["openclaw"]),
                    "bot_ext": json.dumps({"member_management": False}),
                },
            },
            {
                "sql": (
                    "INSERT INTO ac_templates (bot_id, ext, gmt_create, gmt_modified) "
                    "VALUES (:bot_id, :template_ext, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                "params": {
                    "bot_id": bot_id,
                    "template_ext": json.dumps(
                        {
                            "bot_template_config": {
                                "advanced_config": {"member_management": True}
                            }
                        }
                    ),
                },
            },
        ],
    )


@pytest.mark.acceptance
def test_template_ext_member_management_allows_live_collaborator_add(live_backend):
    """A live API add uses ac_templates.ext to allow Bot member management."""
    owner_id = _fresh_id("collab_owner")
    bot_id = _fresh_id("collab_member_mgmt")
    member_id = _fresh_id("collab_member")

    with httpx.Client(
        base_url=live_backend,
        headers={"x-user-id": owner_id},
        timeout=60.0,
    ) as client:
        _seed_member_managed_bot(client, owner_id=owner_id, bot_id=bot_id)

        add_response = client.post(
            "/api/bot/collaborator/add",
            json={
                "bot_id": bot_id,
                "owner_id": owner_id,
                "user_id": member_id,
                "user_name": "Singlebox Member",
                "role": "member",
            },
        )
        assert add_response.status_code == 200, add_response.text
        add_body = add_response.json()
        assert add_body["success"] is True, add_body
        assert add_body["data"]["bot_id"] == bot_id
        assert add_body["data"]["user_id"] == member_id
        assert add_body["data"]["role"] == "member"

        permission = client.post(
            "/api/bot/collaborator/check_permission",
            json={
                "bot_id": bot_id,
                "owner_id": owner_id,
                "user_id": member_id,
                "required_level": "MEMBER",
            },
        )
        assert permission.status_code == 200, permission.text
        permission_body = permission.json()
        assert permission_body["success"] is True, permission_body
        assert permission_body["data"]["has_permission"] is True
        assert permission_body["data"]["level"] == "MEMBER"
