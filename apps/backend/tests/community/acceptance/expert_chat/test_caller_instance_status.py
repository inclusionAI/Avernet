"""Live caller status reads never allocate, revive, or rewrite an instance."""
from __future__ import annotations

import json
import os
import time

import httpx
import jwt
import pytest

from tests.community.acceptance._fixtures.live_personal_bot import fresh_id
from tests.community.acceptance.expert_chat.test_caller_connection_api import (
    _execute_local_sql,
    _seed_service_bot,
)


def _principal_headers(user_id: str) -> dict[str, str]:
    now = int(time.time())
    principal = jwt.encode(
        {
            "iss": "gateway", "aud": "backend", "iat": now, "exp": now + 120,
            "principals": [{
                "type": "user",
                "subject": {"id": user_id, "username": "caller-status@example.test"},
            }],
        },
        os.environ.get(
            "SINGLEBOX_GATEWAY_PRINCIPAL_SIGNING_KEY",
            "singlebox-gateway-principal-key-not-for-production",
        ),
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": principal}


def _instance_rows(client: httpx.Client, params: dict[str, str]) -> list[dict]:
    result = _execute_local_sql(client, [{
        "sql": (
            "SELECT id, user_id, bot_id, owner_id, env, status, ext, "
            "gmt_create, gmt_modified FROM ac_expert_chat_instance "
            "WHERE bot_id=:bot_id AND owner_id=:owner_id AND user_id=:user_id "
            "AND env='dev' ORDER BY id"
        ),
        "params": params,
    }])
    return result["results"][0]["rows"]


@pytest.mark.acceptance
def test_caller_status_observes_absent_pending_and_released_without_provision(live_backend):
    """Real public HTTP and BaaS 404 preserve the exact caller ledger snapshot."""
    owner_id = fresh_id("caller_status_owner")
    bot_id = fresh_id("caller_status_bot")
    user_id = fresh_id("caller_status_user")
    params = {"bot_id": bot_id, "owner_id": owner_id, "user_id": user_id}
    route = f"/openapi/v1/bots/{bot_id}/caller-instance/status"

    with httpx.Client(base_url=live_backend, timeout=30.0) as client:
        _seed_service_bot(client, owner_id=owner_id, bot_id=bot_id, bot_name="Caller Status Read Only")
        # A public chat user has no collaborator row and needs no edit grant.
        _execute_local_sql(client, [{
            "sql": "UPDATE ac_bots SET public='1' WHERE bot_id=:bot_id AND owner_id=:owner_id",
            "params": {"bot_id": bot_id, "owner_id": owner_id},
        }])
        assert _instance_rows(client, params) == []
        response = client.get(route, params={"user_id": user_id, "entity_id": owner_id}, headers=_principal_headers(user_id))
        assert response.status_code == 200, response.text
        assert response.json()["data"] == {"exists": False, "alive": False}
        assert _instance_rows(client, params) == []

        _execute_local_sql(client, [{
            "sql": (
                "INSERT INTO ac_expert_chat_instance (user_id, bot_id, owner_id, env, status, ext) "
                "VALUES (:user_id, :bot_id, :owner_id, 'dev', 'init', :ext)"
            ),
            "params": {**params, "ext": json.dumps({"version": 1})},
        }])
        pending = _instance_rows(client, params)
        assert len(pending) == 1
        response = client.get(route, params={"user_id": user_id, "entity_id": owner_id}, headers=_principal_headers(user_id))
        assert response.status_code == 200, response.text
        assert response.json()["data"] == {"exists": False, "alive": False}
        assert _instance_rows(client, params) == pending

        # This UUID was never provisioned. BaaS returns its real not-found
        # response; a registered Caller must stay exists=true, alive=false.
        _execute_local_sql(client, [{
            "sql": (
                "UPDATE ac_expert_chat_instance SET status='success', ext=:ext "
                "WHERE bot_id=:bot_id AND owner_id=:owner_id AND user_id=:user_id AND env='dev'"
            ),
            "params": {**params, "ext": json.dumps({"bot_uuid": fresh_id("missing_caller_uuid"), "version": 1})},
        }])
        released = _instance_rows(client, params)
        assert len(released) == 1
        response = client.get(route, params={"user_id": user_id, "entity_id": owner_id}, headers=_principal_headers(user_id))
        assert response.status_code == 200, response.text
        assert response.json()["data"] == {"exists": True, "alive": False}
        assert _instance_rows(client, params) == released
