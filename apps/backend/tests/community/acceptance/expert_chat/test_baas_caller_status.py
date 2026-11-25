"""Live service-authenticated status reads preserve the exact Caller ledger."""
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


def _principal_headers() -> dict[str, str]:
    now = int(time.time())
    principal = jwt.encode(
        {
            "iss": "baas", "iat": now, "exp": now + 120,
            "tenant": "untrusted-tenant",
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
            "gmt_create, gmt_modified, caller_token_expires_at FROM ac_expert_chat_instance "
            "WHERE bot_id=:bot_id AND owner_id=:owner_id AND user_id=:user_id "
            "AND env='dev' ORDER BY id"
        ),
        "params": params,
    }])
    return result["results"][0]["rows"]


@pytest.mark.acceptance
def test_baas_status_observes_private_callers_without_grant_or_provision(live_backend):
    """Real BaaS JWT, private Bot and provider 404 exercise the service read."""
    owner_id = fresh_id("caller_status_owner")
    bot_id = fresh_id("caller_status_bot")
    user_id = fresh_id("caller_status_user")
    params = {"bot_id": bot_id, "owner_id": owner_id, "user_id": user_id}
    route = "/api/v1/expert-chats/app-caller-instance/status"

    with httpx.Client(base_url=live_backend, timeout=30.0) as client:
        _seed_service_bot(client, owner_id=owner_id, bot_id=bot_id, bot_name="Caller Status Read Only")
        # The seeded Bot stays private and the Caller has no collaborator/grant.
        denied = client.get(route, params=params)
        assert denied.status_code == 401, denied.text
        assert _instance_rows(client, params) == []
        response = client.get(route, params=params, headers=_principal_headers())
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
        response = client.get(route, params=params, headers=_principal_headers())
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
        response = client.get(route, params=params, headers=_principal_headers())
        assert response.status_code == 200, response.text
        assert response.json()["data"] == {"exists": True, "alive": False}
        assert _instance_rows(client, params) == released
