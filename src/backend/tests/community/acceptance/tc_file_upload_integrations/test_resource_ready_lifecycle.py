"""Live singlebox coverage for the TC resource-ready integration boundary."""

from __future__ import annotations

import hashlib
import time
import uuid

import httpx
import pytest

_LOCAL_AUTH = "singlebox-tc-file-service-token-local"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _execute_local_sql(client: httpx.Client, statement: dict) -> None:
    last_response: httpx.Response | None = None
    for attempt in range(5):
        response = client.post(
            "/local/sql/execute",
            json={"statements": [statement]},
        )
        if response.status_code == 200:
            return
        last_response = response
        if "SQL statements in progress" not in response.text:
            break
        time.sleep(0.2 * (attempt + 1))
    assert last_response is not None
    assert last_response.status_code == 200, last_response.text


@pytest.mark.acceptance
def test_resource_ready_event_and_authoritative_context_live(live_backend) -> None:
    suffix = uuid.uuid4().hex[:10]
    resource_id = f"sr_tc_{suffix}"
    owner_id = f"user_tc_{suffix}"
    bot_id = f"bot_tc_{suffix}"
    session_id = f"session_tc_{suffix}"
    transfer_id = f"transfer_tc_{suffix}"
    content_hash = hashlib.sha256(b"content").hexdigest()

    with httpx.Client(base_url=live_backend, timeout=30.0) as client:
        _execute_local_sql(
            client,
            {
                "sql": (
                    "INSERT INTO ac_session_resource ("
                    "resource_id, owner_id, bot_id, scope_type, scope_key_hash, "
                    "session_key_hash, engine_type, tenant, bot_uuid, display_name, "
                    "filename, device_path, workspace_relative_path, transfer_id, "
                    "status, transfer_api_version, session_key_ciphertext, task_version, "
                    "size_bytes, client_content_hash, gmt_create, gmt_modified"
                    ") VALUES ("
                    ":resource_id, :owner_id, :bot_id, 'session', :scope_key_hash, "
                    ":session_key_hash, 'openclaw', :tenant, :bot_uuid, :display_name, "
                    ":filename, :device_path, :workspace_relative_path, :transfer_id, "
                    "'ready', 'session_v2', :session_key_ciphertext, 3, "
                    ":size_bytes, :client_content_hash, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                    ")"
                ),
                "params": {
                    "resource_id": resource_id,
                    "owner_id": owner_id,
                    "bot_id": bot_id,
                    "scope_key_hash": _hash(session_id),
                    "session_key_hash": _hash(session_id),
                    "tenant": "tenant-singlebox",
                    "bot_uuid": f"uuid-{suffix}",
                    "display_name": "note.md",
                    "filename": "note.md",
                    "device_path": "workspace/note.md",
                    "workspace_relative_path": "note.md",
                    "transfer_id": transfer_id,
                    "session_key_ciphertext": session_id,
                    "size_bytes": 7,
                    "client_content_hash": content_hash,
                },
            },
        )

        status_response = client.get(
            f"/api/session-resources/{resource_id}/materialize-status",
            params={"bot_id": bot_id, "session_key": session_id},
            headers={"x-user-id": owner_id},
        )
        assert status_response.status_code == 200, status_response.text
        assert status_response.json()["status"] == "ready"

        unauthorized = client.post(
            "/api/internal/tc-resource-context",
            json={"res_id": resource_id},
            headers={"Authorization": "Bearer wrong"},
        )
        assert unauthorized.status_code == 401, unauthorized.text

        context_response = client.post(
            "/api/internal/tc-resource-context",
            json={"res_id": resource_id},
            headers={"Authorization": f"Bearer {_LOCAL_AUTH}"},
        )
        assert context_response.status_code == 200, context_response.text
        assert context_response.json() == {
            "code": 0,
            "data": {
                "resource_id": resource_id,
                "status": "ready",
                "deleted": False,
                "transfer_id": transfer_id,
                "tenant": "tenant-singlebox",
                "bot_uuid": f"uuid-{suffix}",
                "user_id": owner_id,
                "bot_id": bot_id,
                "filename": "note.md",
                "size_bytes": 7,
                "content_sha256": content_hash,
                "session_namespace": "tc",
                "session_id": session_id,
                "conversation_id": session_id,
                "scope_type": "direct",
                "group_id": None,
                "members": [],
                "session_revision": 3,
                "session_active": True,
            },
        }
