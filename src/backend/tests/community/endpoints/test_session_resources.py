"""Endpoint coverage for session resource routes."""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

from agentclaw.community.api.session_resource_service import (
    SessionResourceServiceProtocol,
)
from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
    SessionUploadIntent,
    UploadGrant,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_AUTH_HEADERS = {"x-user-id": "session-resource-user"}
_QUERY = {"bot_id": "bot-1", "session_key": "session-1"}
_RESOURCE_ID = "sr-endpoint-1"
_TRANSFER_ID = "transfer-endpoint-1"
_TASK_ID = "task-endpoint-1"


def _record(
    status: SessionResourceStatus = SessionResourceStatus.READY,
) -> SessionResourceRecord:
    return SessionResourceRecord(
        resource_id=_RESOURCE_ID,
        owner_id="session-resource-user",
        bot_id="bot-1",
        scope_type="personal_bot_chat",
        scope_key_hash="scope-hash",
        session_key_hash="session-hash",
        engine_type="claude_code",
        tenant="tenant-1",
        bot_uuid="bot-uuid-1",
        display_name="notes.txt",
        filename="notes.txt",
        device_path="workspace/.teamclaw/session-files/notes.txt",
        workspace_relative_path=".teamclaw/session-files/notes.txt",
        transfer_id=_TRANSFER_ID,
        status=status,
        task_id=_TASK_ID,
        task_version=1,
        size_bytes=12,
        client_content_hash="sha256-notes",
    )


def _service() -> MagicMock:
    service = MagicMock(spec=SessionResourceServiceProtocol)
    service.create_upload_intent.return_value = SessionUploadIntent(
        resource=_record(SessionResourceStatus.UPLOAD_URL_ISSUED),
        grant=UploadGrant(
            upload_url="https://baas.example/upload",
            transfer_id=_TRANSFER_ID,
            upload_type="SINGLE",
            expires_at="2026-07-21T12:00:00Z",
        ),
    )
    service.complete_upload.return_value = _record(
        SessionResourceStatus.DEVICE_SYNCING
    )
    service.get_status.return_value = _record()
    service.list_resources.return_value = [_record()]
    service.reference.return_value = {
        "resource_id": _RESOURCE_ID,
        "display_name": "notes.txt",
        "size_bytes": 12,
        "content_hash": "sha256-notes",
    }
    service.delete.return_value = replace(
        _record(),
        status=SessionResourceStatus.DELETED,
    )
    service.materialized_callback.return_value = _record()
    return service


def _seed_ok(world) -> None:
    world.injector.binder.bind(
        SessionResourceServiceProtocol,
        to=_service(),
        scope=None,
    )


def _seed_not_found(world) -> None:
    service = _service()
    for method_name in (
        "complete_upload",
        "get_status",
        "reference",
        "delete",
    ):
        getattr(service, method_name).side_effect = ValueError("resource_not_found")
    world.injector.binder.bind(
        SessionResourceServiceProtocol,
        to=service,
        scope=None,
    )


def _seed_invalid_filename(world) -> None:
    service = _service()
    service.create_upload_intent.side_effect = ValueError("invalid_filename")
    world.injector.binder.bind(
        SessionResourceServiceProtocol,
        to=service,
        scope=None,
    )


_UPLOAD_INTENT_BODY = {
    **_QUERY,
    "scope_type": "personal_bot_chat",
    "engine_type": "claude_code",
    "files": [
        {
            "filename": "notes.txt",
            "size_bytes": 12,
            "content_hash": "sha256-notes",
        }
    ],
}
_UPLOAD_COMPLETE_BODY = {
    **_QUERY,
    "resource_id": _RESOURCE_ID,
    "transfer_id": _TRANSFER_ID,
}
_REFERENCE_BODY = {**_QUERY, "insert_id": "insert-1"}
_CALLBACK_BODY = {
    "transfer_id": _TRANSFER_ID,
    "task_id": _TASK_ID,
    "task_version": 1,
    "ready": True,
    "canonical_bot_absolute_path": "/home/admin/workspace/notes.txt",
    "relative_path": ".teamclaw/session-files/notes.txt",
    "size_bytes": 12,
    "content_hash": "sha256-notes",
}


@endpoint_test(
    method="POST",
    path="/api/session-resources/upload-intents",
    scenario="ok",
    input=CaseInput(headers=_AUTH_HEADERS, json_body=_UPLOAD_INTENT_BODY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"files": [{"resource_id": _RESOURCE_ID}]},
    ),
)
def upload_intents_ok():
    """Happy path: create a BaaS upload intent."""


@endpoint_test(
    method="POST",
    path="/api/session-resources/upload-intents",
    scenario="err",
    input=CaseInput(headers=_AUTH_HEADERS, json_body=_UPLOAD_INTENT_BODY),
    seed=_seed_invalid_filename,
    expect=ExpectError(status=400, json_contains={"detail": "invalid_filename"}),
)
def upload_intents_err():
    """Error path: reject an invalid filename reported by the service."""


@endpoint_test(
    method="POST",
    path="/api/session-resources/upload-complete",
    scenario="ok",
    input=CaseInput(headers=_AUTH_HEADERS, json_body=_UPLOAD_COMPLETE_BODY),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "resource_id": _RESOURCE_ID,
            "status": "device_syncing",
        },
    ),
)
def upload_complete_ok():
    """Happy path: dispatch materialization after upload completion."""


@endpoint_test(
    method="POST",
    path="/api/session-resources/upload-complete",
    scenario="err",
    input=CaseInput(headers=_AUTH_HEADERS, json_body=_UPLOAD_COMPLETE_BODY),
    seed=_seed_not_found,
    expect=ExpectError(status=404, json_contains={"detail": "resource_not_found"}),
)
def upload_complete_err():
    """Error path: reject an unknown resource."""


@endpoint_test(
    method="GET",
    path="/api/session-resources/{resource_id}/materialize-status",
    scenario="ok",
    input=CaseInput(
        path_params={"resource_id": _RESOURCE_ID},
        query_params=_QUERY,
        headers=_AUTH_HEADERS,
    ),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"resource_id": _RESOURCE_ID, "status": "ready"},
    ),
)
def materialize_status_ok():
    """Happy path: poll a materialized resource."""


@endpoint_test(
    method="GET",
    path="/api/session-resources/{resource_id}/materialize-status",
    scenario="err",
    input=CaseInput(
        path_params={"resource_id": "missing"},
        query_params=_QUERY,
        headers=_AUTH_HEADERS,
    ),
    seed=_seed_not_found,
    expect=ExpectError(status=404, json_contains={"detail": "resource_not_found"}),
)
def materialize_status_err():
    """Error path: polling an unknown resource returns 404."""


@endpoint_test(
    method="GET",
    path="/api/session-resources",
    scenario="ok",
    input=CaseInput(query_params=_QUERY, headers=_AUTH_HEADERS),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"files": [{"resource_id": _RESOURCE_ID}]},
    ),
)
def list_resources_ok():
    """Happy path: list session resources."""


@endpoint_test(
    method="GET",
    path="/api/session-resources",
    scenario="err",
    input=CaseInput(
        query_params={"bot_id": "bot-1"},
        headers=_AUTH_HEADERS,
    ),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def list_resources_err():
    """Error path: require the session key."""


@endpoint_test(
    method="GET",
    path="/api/session-resources/referable-files",
    scenario="ok",
    input=CaseInput(query_params=_QUERY, headers=_AUTH_HEADERS),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"files": [{"status": "ready"}]},
    ),
)
def referable_files_ok():
    """Happy path: list resources that can be referenced."""


@endpoint_test(
    method="GET",
    path="/api/session-resources/referable-files",
    scenario="err",
    input=CaseInput(
        query_params={"session_key": "session-1"},
        headers=_AUTH_HEADERS,
    ),
    seed=_seed_ok,
    expect=ExpectError(status=422),
)
def referable_files_err():
    """Error path: require the bot id."""


@endpoint_test(
    method="POST",
    path="/api/session-resources/{resource_id}/reference",
    scenario="ok",
    input=CaseInput(
        path_params={"resource_id": _RESOURCE_ID},
        headers=_AUTH_HEADERS,
        json_body=_REFERENCE_BODY,
    ),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"resource_id": _RESOURCE_ID, "insert_id": "insert-1"},
    ),
)
def create_reference_ok():
    """Happy path: create a chat reference token."""


@endpoint_test(
    method="POST",
    path="/api/session-resources/{resource_id}/reference",
    scenario="err",
    input=CaseInput(
        path_params={"resource_id": "missing"},
        headers=_AUTH_HEADERS,
        json_body=_REFERENCE_BODY,
    ),
    seed=_seed_not_found,
    expect=ExpectError(status=404, json_contains={"detail": "resource_not_found"}),
)
def create_reference_err():
    """Error path: reject a reference to an unknown resource."""


def _resource_input(resource_id: str = _RESOURCE_ID) -> CaseInput:
    return CaseInput(
        path_params={"resource_id": resource_id},
        query_params=_QUERY,
        headers=_AUTH_HEADERS,
    )


@endpoint_test(
    method="DELETE",
    path="/api/session-resources/{resource_id}",
    scenario="ok",
    input=_resource_input(),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"resource_id": _RESOURCE_ID, "status": "deleted"},
    ),
)
def delete_resource_ok():
    """Happy path: soft-delete a resource."""


@endpoint_test(
    method="DELETE",
    path="/api/session-resources/{resource_id}",
    scenario="err",
    input=_resource_input("missing"),
    seed=_seed_not_found,
    expect=ExpectError(status=404, json_contains={"detail": "resource_not_found"}),
)
def delete_resource_err():
    """Error path: reject deletion of an unknown resource."""


@endpoint_test(
    method="POST",
    path="/internal/session-resources/{resource_id}/materialized",
    scenario="ok",
    input=CaseInput(
        path_params={"resource_id": _RESOURCE_ID},
        headers={"x-materialization-task-id": _TASK_ID},
        json_body=_CALLBACK_BODY,
    ),
    seed=_seed_ok,
    expect=ExpectSuccess(
        status=200,
        json_contains={"applied": True, "status": "ready"},
    ),
)
def materialized_callback_ok():
    """Happy path: apply the engine materialization callback."""


@endpoint_test(
    method="POST",
    path="/internal/session-resources/{resource_id}/materialized",
    scenario="err",
    input=CaseInput(
        path_params={"resource_id": _RESOURCE_ID},
        headers={"x-materialization-task-id": "wrong-task"},
        json_body=_CALLBACK_BODY,
    ),
    seed=_seed_ok,
    expect=ExpectError(
        status=401,
        json_contains={"detail": "invalid materialization capability"},
    ),
)
def materialized_callback_err():
    """Error path: reject a mismatched task capability."""
