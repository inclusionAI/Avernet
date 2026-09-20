"""TC resource-ready lifecycle flow for the singlebox registry."""

from __future__ import annotations

import hashlib

from tests.community.framework.flow import FlowCase, FlowStep

_RESOURCE_ID = "sr_tc_flow"
_OWNER_ID = "user_tc_flow"
_BOT_ID = "bot_tc_flow"
_SESSION_ID = "session_tc_flow"
_LOCAL_AUTH = "singlebox-tc-file-service-token-local"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


TC_FILE_UPLOAD_INTEGRATION_FLOWS: list[FlowCase] = [
    FlowCase(
        name="tc-resource-ready-authoritative-context",
        covers=["tc_file_upload_integrations"],
        live_only=True,
        steps=[
            FlowStep(
                method="POST",
                path="/local/sql/execute",
                body={
                    "statements": [
                        {
                            "sql": (
                                "INSERT OR REPLACE INTO ac_session_resource ("
                                "resource_id, owner_id, bot_id, scope_type, scope_key_hash, "
                                "session_key_hash, engine_type, tenant, bot_uuid, display_name, "
                                "filename, device_path, workspace_relative_path, transfer_id, "
                                "status, transfer_api_version, session_key_ciphertext, task_version, "
                                "size_bytes, client_content_hash, gmt_create, gmt_modified"
                                ") VALUES ("
                                ":resource_id, :owner_id, :bot_id, 'session', :scope_key_hash, "
                                ":session_key_hash, 'openclaw', 'tenant-singlebox', 'uuid-flow', "
                                "'note.md', 'note.md', 'workspace/note.md', 'note.md', "
                                "'transfer-flow', 'ready', 'session_v2', :session_key, 1, 7, "
                                ":content_hash, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                            ),
                            "params": {
                                "resource_id": _RESOURCE_ID,
                                "owner_id": _OWNER_ID,
                                "bot_id": _BOT_ID,
                                "scope_key_hash": _hash(_SESSION_ID),
                                "session_key_hash": _hash(_SESSION_ID),
                                "session_key": _SESSION_ID,
                                "content_hash": hashlib.sha256(b"content").hexdigest(),
                            },
                        }
                    ]
                },
                expect_status=200,
            ),
            FlowStep(
                method="GET",
                path=f"/api/session-resources/{_RESOURCE_ID}/materialize-status",
                query={"bot_id": _BOT_ID, "session_key": _SESSION_ID},
                headers={"x-user-id": _OWNER_ID},
                expect_status=200,
                expect={"status": "ready"},
            ),
            FlowStep(
                method="POST",
                path="/api/internal/tc-resource-context",
                body={"res_id": _RESOURCE_ID},
                headers={"Authorization": f"Bearer {_LOCAL_AUTH}"},
                expect_status=200,
                expect={"code": 0, "data": {"resource_id": _RESOURCE_ID}},
            ),
        ],
    )
]
