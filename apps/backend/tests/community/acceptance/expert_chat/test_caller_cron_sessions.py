"""Live Backend and Engine acceptance for runtime-created Caller sessions."""
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
@pytest.mark.parametrize("session_kind", ["ordinary", "cron", "subagent"])
def test_existing_caller_discovers_runtime_sessions_without_owned_index(live_backend, session_kind):
    """Runtime-created sessions support every Caller operation without an owned row."""
    from tests.community.acceptance.bot_collaborator.test_manager_sync_bcs_projection import (
        _BCS_BASE_URL, _ensure_human, _ensure_projection_target,
        _fresh_staff_id, _initialize_fixture_ownership,
    )

    # BCN's human registration requires a nonempty ASCII alphanumeric staff ID.
    subject = _fresh_staff_id("callercronowner")
    with httpx.Client(base_url=live_backend, headers={"x-user-id": subject}, timeout=90) as client:
        bot = create_live_personal_bot(client, user_id=subject, bot_name_prefix="Caller Cron")
        bot_id = bot["bot_id"]
        address = {"bot_id": bot_id, "owner_id": subject}
        route = f"/api/v1/expert-chats/{bot_id}/{subject}/sessions"
        key = f"agent:main:{session_kind}:{fresh_id('acceptance_session')}"
        runtime_route = f"/openapi/v1/bots/{bot_id}/sessions/{quote(key, safe='')}"
        runtime_params = {"user_id": subject, "owner_id": subject}
        # Engine's real session-update operation creates the supplied session key.
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
        # the subject. Runtime access must not populate the ownership index.
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
        assert owned == [], owned
        # Bootstrap this fresh fixture's real BCN ownership; no authorization
        # service is mocked. f_user_id then selects the ExpertChat HTTP backend.
        with httpx.Client(base_url=_BCS_BASE_URL, timeout=30) as bcs:
            _ensure_human(bcs, subject)
            _ensure_projection_target(bcs, bcs_bot_id=f"{bot_id}:{subject}", owner_id=subject)
        _initialize_fixture_ownership(f"{bot_id}:{subject}")
        # Owner metadata is not a runtime friendship grant. Exercise the actual
        # human request/owner approval flow before entering friend-chat APIs.
        target = f"{bot_id}:{subject}"
        with httpx.Client(base_url=_BCS_BASE_URL, timeout=30) as bcs:
            admission_params = {"actor": subject, "actor_kind": "human"}
            admission_route = f"/bots/{quote(target, safe='')}/admission"
            before = bcs.get(admission_route, params=admission_params)
            assert before.status_code == 200
            assert before.json()["allowed"] is False
            assert before.json()["reason_code"] == "no_edge"
            human_headers = {"X-Mock-User-Id": subject, "X-Mock-Nick-Name": subject}
            request = assert_success(bcs.post(
                "/collaboration/friend-connections/requests",
                headers=human_headers, json={"to_bot": target},
            ))["data"]
            assert request["status"] == "pending"
            assert request["request_ids"]
            for request_id in request["request_ids"]:
                accepted = assert_success(bcs.post(
                    f"/collaboration/friend-connections/requests/{request_id}/accept",
                    headers=human_headers,
                ))["data"]
                assert accepted["edge_ids"]
            admitted = bcs.get(admission_route, params=admission_params)
            assert admitted.status_code == 200
            admission = admitted.json()
            assert admission["allowed"] is True
            assert admission["reason_code"] == "ok"
            assert admission["public_default"] is False
        friend_params = {**runtime_params, "f_user_id": subject, "stage": "online"}
        principal = _principal_headers(subject)
        detail = _runtime_data(client.get(runtime_route, params=friend_params, headers=principal))
        assert detail["session_id"] == key, detail
        history = _runtime_data(client.get(f"{runtime_route}/messages", params=friend_params, headers=principal))
        assert history["items"] == [], history
        connected = assert_success(client.post(f"{route}/connect", json={"session_key": key}))["data"]
        assert connected["session_key"] == key
        renamed = _runtime_data(client.patch(runtime_route, params=friend_params, headers=principal, json={"title": "Caller renamed"}))
        assert renamed["title"] == "Caller renamed"
        favorite = _runtime_data(client.put(f"{runtime_route}/favorite", params=friend_params, headers=principal))
        assert favorite["favorited"] is True
        favorite_list = assert_success(client.get(route, params={"favorite_only": True}))["data"]
        assert key in {item["id"] for item in favorite_list["items"]}
        _runtime_data(client.delete(f"{runtime_route}/messages", params=friend_params, headers=principal))
        _runtime_data(client.delete(runtime_route, params=friend_params, headers=principal))
        after_delete = assert_success(client.get(route, params={"session_key": key}))["data"]
        assert after_delete == {"total": 0, "items": []}
        owned_after = _execute_local_sql(client, [{
            "sql": "SELECT session_key FROM ac_expert_chat_owned_sessions WHERE user_id=:user_id AND bot_id=:bot_id AND owner_id=:owner_id",
            "params": instance_params,
        }])["results"][0]["rows"]
        assert owned_after == [], owned_after
