from __future__ import annotations

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.tc_file_upload_integrations.resource_context_router import (
    TcResourceContextRequest,
    resolve_tc_resource_context,
)
from agentclaw.community.core.tc_file_upload_integrations.resource_context import (
    TcResourceContextSnapshot,
)
from agentclaw.community.di.config import TcFileServiceToken


class _Service:
    def __init__(self, result: TcResourceContextSnapshot | Exception) -> None:
        self.result = result

    def resolve(self, resource_id: str) -> TcResourceContextSnapshot:
        if isinstance(self.result, Exception):
            raise self.result
        assert resource_id == "sr_001"
        return self.result


def _snapshot() -> TcResourceContextSnapshot:
    return TcResourceContextSnapshot(
        resource_id="sr_001",
        status="ready",
        deleted=False,
        transfer_id="transfer-1",
        tenant="tenant-1",
        bot_uuid="bot-uuid-1",
        user_id="user-1",
        bot_id="bot-1",
        filename="note.md",
        size_bytes=7,
        content_sha256=None,
        session_namespace="tc",
        session_id="session-1",
        conversation_id="session-1",
        scope_type="direct",
        group_id=None,
        members=(),
        session_revision=1,
        session_active=True,
    )


def test_context_router_returns_strict_envelope_for_valid_bearer() -> None:
    response = resolve_tc_resource_context(
        TcResourceContextRequest(res_id="sr_001"),
        "Bearer service-secret",
        _Service(_snapshot()),
        TcFileServiceToken("service-secret"),
    )

    assert response["code"] == 0
    assert response["data"]["resource_id"] == "sr_001"
    assert response["data"]["members"] == []


@pytest.mark.parametrize(
    "authorization", [None, "", "Basic service-secret", "Bearer wrong"]
)
def test_context_router_rejects_missing_or_wrong_bearer(authorization) -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_tc_resource_context(
            TcResourceContextRequest(res_id="sr_001"),
            authorization,
            _Service(_snapshot()),
            TcFileServiceToken("service-secret"),
        )

    assert exc_info.value.status_code == 401


@pytest.mark.parametrize(
    ("error", "status_code"),
    [
        ("resource_not_found", 404),
        ("resource_context_incomplete", 409),
        ("resource_context_unsupported_scope", 409),
    ],
)
def test_context_router_maps_domain_failures(error: str, status_code: int) -> None:
    with pytest.raises(HTTPException) as exc_info:
        resolve_tc_resource_context(
            TcResourceContextRequest(res_id="sr_001"),
            "Bearer service-secret",
            _Service(ValueError(error)),
            TcFileServiceToken("service-secret"),
        )

    assert exc_info.value.status_code == status_code
    assert exc_info.value.detail == error
