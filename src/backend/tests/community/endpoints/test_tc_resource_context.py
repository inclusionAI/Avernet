"""Endpoint-framework coverage for the authenticated TC resource context API."""

from __future__ import annotations

from agentclaw.community.core.tc_file_upload_integrations.resource_context import (
    TcResourceContextService,
    TcResourceContextSnapshot,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_method,
    endpoint_test,
)

_PATH = "/api/internal/tc-resource-context"
_RESOURCE_ID = "sr_endpoint_tc_context"
_LOCAL_AUTH = "singlebox-tc-file-service-token-local"


def _resolve_context(_self, resource_id: str) -> TcResourceContextSnapshot:
    assert resource_id == _RESOURCE_ID
    return TcResourceContextSnapshot(
        resource_id=resource_id,
        status="ready",
        deleted=False,
        transfer_id="transfer-endpoint",
        tenant="tenant-endpoint",
        bot_uuid="bot-uuid-endpoint",
        user_id="user-endpoint",
        bot_id="bot-endpoint",
        filename="note.md",
        size_bytes=7,
        content_sha256=None,
        session_namespace="tc",
        session_id="session-endpoint",
        conversation_id="session-endpoint",
        scope_type="direct",
        group_id=None,
        members=(),
        session_revision=1,
        session_active=True,
    )


def _seed_context_service(world) -> None:
    bind_method(world, TcResourceContextService, "resolve", _resolve_context)


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="happy",
    input=CaseInput(
        headers={"Authorization": f"Bearer {_LOCAL_AUTH}"},
        json_body={"res_id": _RESOURCE_ID},
    ),
    seed=_seed_context_service,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 0,
            "data": {
                "resource_id": _RESOURCE_ID,
                "status": "ready",
                "transfer_id": "transfer-endpoint",
                "session_id": "session-endpoint",
                "session_active": True,
            },
        },
    ),
)
def resolve_tc_resource_context_happy():
    """A trusted ECB caller receives the authoritative resource context."""


@endpoint_test(
    method="POST",
    path=_PATH,
    scenario="invalid_bearer",
    input=CaseInput(
        headers={"Authorization": "Bearer wrong"},
        json_body={"res_id": _RESOURCE_ID},
    ),
    expect=ExpectError(
        status=401,
        json_contains={"detail": "Invalid token"},
    ),
)
def resolve_tc_resource_context_invalid_bearer():
    """An untrusted caller is rejected before resource context is resolved."""
