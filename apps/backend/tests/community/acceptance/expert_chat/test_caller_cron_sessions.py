"""Live Backend and Engine acceptance for runtime-created Caller cron sessions."""
from __future__ import annotations

import json
from urllib.parse import quote

import httpx
import pytest

from tests.community.acceptance._fixtures.live_personal_bot import (
    assert_success,
    create_live_personal_bot,
    fresh_id,
)
from tests.community.acceptance.cron.test_cron_lifecycle import _seed_success_publish
from tests.community.acceptance.files.test_openapi_directory_download import _principal_headers
from tests.community.acceptance.expert_chat.test_caller_connection_api import _execute_local_sql


def _runtime_data(response: httpx.Response) -> dict:
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == 200000, body
    return body["data"]


@pytest.mark.acceptance
def test_existing_caller_discovers_runtime_cron_without_owned_index(live_backend):
    """An actual runtime cron key becomes visible and persists as an owned chat."""
    subject = fresh_id("caller_cron_owner")
    with httpx.Client(base_url=live_backend, headers={"x-user-id": subject}, timeout=90) as client:
        bot = create_live_personal_bot(client, user_id=subject, bot_name_prefix="Caller Cron")
        bot_id = bot["bot_id"]
        address = {"bot_id": bot_id, "owner_id": subject}
        route = f"/api/v1/expert-chats/{bot_id}/{subject}/sessions"
        key = f"agent:main:cron:{fresh_id('acceptance_job')}"
        runtime_route = f"/openapi/v1/bots/{bot_id}/sessions/{quote(key, safe='')}"
        runtime_params = {"user_id": subject, "owner_id": subject}
        # Engine's real session-update operation creates the supplied cron key.
        # No Backend-owned session is inserted by this operator-facing request.
        response = client.patch(runtime_route, params=runtime_params, headers=_principal_headers(subject), json={"title": "Scheduled acceptance"})
        _runtime_data(response)
        runtime_detail = _runtime_data(client.get(runtime_route, params=runtime_params, headers=_principal_headers(subject)))
        assert runtime_detail["session_id"] == key, runtime_detail

        rows = _execute_local_sql(client, [{
            "sql": "SELECT binding_id, device_id FROM ac_bots WHERE bot_id=:bot_id AND owner_id=:owner_id",
            "params": address,
        }])["results"][0]["rows"]
        binding_id = rows[0]["binding_id"]
        assert binding_id
        _seed_success_publish(client, bot_id=bot_id, owner_id=subject, binding_id=binding_id)
        _execute_local_sql(client, [{
            "sql": "UPDATE ac_bots SET call_type='caller' WHERE bot_id=:bot_id AND owner_id=:owner_id",
            "params": address,
        }])
        assert_success(client.post("/api/v1/expert-chats", json=address))
        # New discovery must remain read-only when the subject has no Caller.
        missing = assert_success(client.get(route))["data"]
        assert missing == {"total": 0, "items": []}, missing
        instance_params = {**address, "user_id": subject}
        instance_rows = _execute_local_sql(client, [{
            "sql": "SELECT id FROM ac_expert_chat_instance WHERE user_id=:user_id AND bot_id=:bot_id AND owner_id=:owner_id",
            "params": instance_params,
        }])["results"][0]["rows"]
        assert instance_rows == []
        # Fixture metadata associates this dedicated, already-live runtime with
        # the subject. Discovery must fill ownership through the normal service.
        _execute_local_sql(client, [{
            "sql": "INSERT INTO ac_expert_chat_instance (user_id, bot_id, owner_id, env, status, ext) VALUES (:user_id, :bot_id, :owner_id, 'dev', 'init', :ext)",
            "params": {**instance_params, "ext": json.dumps({"binding_id": binding_id, "bot_uuid": rows[0]["device_id"], "version": 1})},
        }])
        pending = assert_success(client.get(route))["data"]
        assert pending == {"total": 0, "items": [], "need_poll": True}, pending
        _execute_local_sql(client, [{
            "sql": "UPDATE ac_expert_chat_instance SET status='success' WHERE user_id=:user_id AND bot_id=:bot_id AND owner_id=:owner_id",
            "params": instance_params,
        }])
        discovered = assert_success(client.get(route, params={"session_key": key, "limit": 1}))["data"]
        assert discovered["total"] == 1, discovered
        assert discovered["items"][0]["id"] == key, discovered
        owned = _execute_local_sql(client, [{
            "sql": "SELECT session_key FROM ac_expert_chat_owned_sessions WHERE user_id=:user_id AND bot_id=:bot_id AND owner_id=:owner_id",
            "params": instance_params,
        }])["results"][0]["rows"]
        assert [row["session_key"] for row in owned] == [key], owned
        detail = _runtime_data(client.get(runtime_route, params={**runtime_params, "stage": "draft"}, headers=_principal_headers(subject)))
        assert detail["session_id"] == key, detail
        history = _runtime_data(client.get(f"{runtime_route}/messages", params={**runtime_params, "stage": "draft"}, headers=_principal_headers(subject)))
        assert history["items"] == [], history
