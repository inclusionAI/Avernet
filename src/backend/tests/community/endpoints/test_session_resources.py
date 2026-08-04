"""Real-DI endpoint coverage for session resource routes.

Each case runs the application's actual SessionResourceService, SQLite-backed
repository, task queue, and local Engine adapter. The only external dependency
is a small in-process Session File API server reached through the production
HttpxClient, so endpoint tests do not replace application services with mocks.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Annotated, Any
from urllib.parse import urlparse

from injector import singleton

from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.bot_public.repository.bot_friend_repository import (
    BotFriendRepositoryProtocol,
)
from agentclaw.community.core.session_resources.repository.protocol import (
    SessionResourceRepositoryProtocol,
)
from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
    TransferApiVersion,
    hash_identifier,
)
from agentclaw.community.plugin_api.device_adapter_transport import (
    DeviceAdapterTransport,
)
from agentclaw.community.plugin_api.http_client import (
    QUALIFIER_BAAS,
    HttpClient,
)
from agentclaw.community.plugins.http_client import HttpxClient
from agentclaw.community.plugins.local.device_adapter_transport import (
    InMemoryDeviceAdapterTransport,
)
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.factories.devices import make_active_local_device
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


_OWNER = "session-resource-user"
_TARGET_OWNER = "session-resource-target-owner"
_BOT_ID = "bot-session-resource"
_DEVICE_ID = "device-session-resource"
_TARGET_DEVICE_ID = "device-session-resource-target"
_SESSION_KEY = "session-resource-key"
_UPSTREAM_ERROR_SESSION_KEY = "session-resource-upstream-error"
_AUTH_HEADERS = {"x-user-id": _OWNER}
_QUERY = {"bot_id": _BOT_ID, "session_key": _SESSION_KEY}
_RESOURCE_ID = "sr-endpoint-1"
_TRANSFER_ID = "transfer-endpoint-1"
_TASK_ID = "task-endpoint-1"
_CONTENT_BYTES = b"materialized session file"


class _SessionFileApiHandler(BaseHTTPRequestHandler):
    """Minimal local Session File API used through a real HTTP client."""

    def _write(self, data: dict[str, Any]) -> None:
        body = json.dumps({"code": 0, "data": data}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _write_error(self, status_code: int) -> None:
        body = b'{"detail":"upstream diagnostic must not reach callers"}'
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        path = urlparse(self.path).path
        if path.endswith("/ws-info"):
            self._write(
                {
                    "ws_url": "ws://local.test/api/openclaw/ws",
                    "token": "local-test-token",
                    "target": "local.test:20003",
                    "expires_at": "2099-01-01T00:00:00Z",
                }
            )
            return
        if path.endswith("/http-info"):
            self._write(
                {
                    "http_url": "http://local.test:20003",
                    "token": "local-test-token",
                    "target": "local.test:20003",
                }
            )
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        path = urlparse(self.path).path
        if path.endswith("/files/upload-url"):
            if f"/{_UPSTREAM_ERROR_SESSION_KEY}/" in path:
                self._write_error(503)
                return
            self._write(
                {
                    "transfer_id": _TRANSFER_ID,
                    "type": "SINGLE",
                    "upload_url": "https://upload.example.invalid/session-resource",
                    "http_method": "PUT",
                    "expires_at": "2099-01-01T00:00:00Z",
                }
            )
            return
        if "/files/upload-url/" in path and path.endswith("/complete"):
            self._write({"transfer_id": _TRANSFER_ID, "status": "DONE"})
            return
        self.send_error(404)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


_SESSION_FILE_API_BASE: str | None = None


def _session_file_api_base() -> str:
    global _SESSION_FILE_API_BASE
    if _SESSION_FILE_API_BASE is None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SessionFileApiHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        host, port = server.server_address
        _SESSION_FILE_API_BASE = f"http://{host}:{port}"
    return _SESSION_FILE_API_BASE


def _use_real_session_file_api(world) -> None:
    world.injector.binder.bind(
        Annotated[HttpClient, QUALIFIER_BAAS],
        to=HttpxClient(_session_file_api_base()),
        scope=singleton,
    )


def _seed_bot(world) -> None:
    _use_real_session_file_api(world)
    make_staff_user(world, user_id=_OWNER)
    binding_id = make_active_local_device(
        world,
        owner_id=_OWNER,
        device_id=_DEVICE_ID,
    )
    make_bot(
        world,
        bot_id=_BOT_ID,
        owner_id=_OWNER,
        owner_name="Session Resource Owner",
        bot_type="service",
        status="ACTIVE",
        binding_id=binding_id,
    )


def _seed_friend_target_bot(world) -> None:
    _use_real_session_file_api(world)
    make_staff_user(world, user_id=_OWNER)
    make_staff_user(world, user_id=_TARGET_OWNER)
    binding_id = make_active_local_device(
        world,
        owner_id=_TARGET_OWNER,
        device_id=_TARGET_DEVICE_ID,
    )
    make_bot(
        world,
        bot_id=_BOT_ID,
        owner_id=_TARGET_OWNER,
        owner_name="Session Resource Target Owner",
        bot_type="service",
        status="ACTIVE",
        binding_id=binding_id,
    )
    world.get(BotRepository).update_by_owner(
        _BOT_ID,
        _TARGET_OWNER,
        {"public": "1"},
    )
    world.get(BotFriendRepositoryProtocol).insert(
        {
            "requester_entity_id": _OWNER,
            "target_entity_id": _TARGET_OWNER,
            "target_bot_id": _BOT_ID,
            "status": "ACCEPTED",
        }
    )


def _record(
    *,
    resource_id: str = _RESOURCE_ID,
    status: SessionResourceStatus = SessionResourceStatus.READY,
) -> SessionResourceRecord:
    syncing = status is SessionResourceStatus.DEVICE_SYNCING
    return SessionResourceRecord(
        resource_id=resource_id,
        owner_id=_OWNER,
        bot_id=_BOT_ID,
        scope_type="personal_bot_chat",
        scope_key_hash=hash_identifier(f"personal_bot_chat:{_OWNER}:{_BOT_ID}"),
        session_key_hash=hash_identifier(_SESSION_KEY),
        engine_type="claude_code",
        tenant="test-tenant",
        bot_uuid=_DEVICE_ID,
        display_name="notes.txt",
        filename="notes.txt",
        device_path="workspace/.teamclaw/session-files/notes.txt",
        workspace_relative_path=".teamclaw/session-files/notes.txt",
        transfer_id=_TRANSFER_ID,
        status=status,
        transfer_api_version=TransferApiVersion.SESSION_V2,
        session_key_ciphertext=_SESSION_KEY,
        task_id=_TASK_ID if syncing else None,
        task_version=1 if syncing else 0,
        size_bytes=len(_CONTENT_BYTES),
        client_content_hash="sha256-notes",
    )


def _insert_record(
    world,
    *,
    status: SessionResourceStatus = SessionResourceStatus.READY,
) -> SessionResourceRecord:
    return world.get(SessionResourceRepositoryProtocol).create(_record(status=status))


def _seed_ready_record(world) -> None:
    _insert_record(world)


def _seed_upload_pending_record(world) -> None:
    _seed_bot(world)
    _insert_record(world, status=SessionResourceStatus.UPLOAD_URL_ISSUED)


def _seed_content_ready(world) -> None:
    _seed_bot(world)
    _insert_record(world)
    transport = world.get(DeviceAdapterTransport)
    assert isinstance(transport, InMemoryDeviceAdapterTransport)
    transport.register_materialized_content(
        resource_id=_RESOURCE_ID,
        content=_CONTENT_BYTES,
        filename="notes.txt",
        content_type="text/plain",
    )


def _seed_content_missing(world) -> None:
    _seed_bot(world)
    _insert_record(world)


def _seed_callback_pending(world) -> None:
    _insert_record(world, status=SessionResourceStatus.DEVICE_SYNCING)


def _assert_content_response(response, world) -> None:
    del world
    assert response.content == _CONTENT_BYTES
    assert response.headers["content-type"] == "text/plain"
    assert response.headers["content-disposition"] == 'inline; filename="notes.txt"'


_UPLOAD_INTENT_BODY = {
    **_QUERY,
    "scope_type": "personal_bot_chat",
    "engine_type": "claude_code",
    "files": [
        {
            "filename": "notes.txt",
            "size_bytes": len(_CONTENT_BYTES),
            "content_hash": "sha256-notes",
        }
    ],
}
_FRIEND_UPLOAD_INTENT_BODY = {
    **_UPLOAD_INTENT_BODY,
    "scope_type": "friend_bot_chat",
    "target_entity_id": _TARGET_OWNER,
}
_INVALID_UPLOAD_INTENT_BODY = {
    **_UPLOAD_INTENT_BODY,
    "files": [{"filename": "../notes.txt"}],
}
_UPSTREAM_ERROR_UPLOAD_INTENT_BODY = {
    **_UPLOAD_INTENT_BODY,
    "session_key": _UPSTREAM_ERROR_SESSION_KEY,
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
    "size_bytes": len(_CONTENT_BYTES),
    "content_hash": "sha256-notes",
}


@endpoint_test(
    method="POST",
    path="/api/session-resources/upload-intents",
    scenario="ok_real_session_file_api",
    input=CaseInput(headers=_AUTH_HEADERS, json_body=_UPLOAD_INTENT_BODY),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "files": [
                {
                    "status": "upload_url_issued",
                    "transfer_id": _TRANSFER_ID,
                    "upload_type": "SINGLE",
                }
            ]
        },
    ),
)
def upload_intents_ok():
    """Create an intent through the real service and local HTTP API."""


@endpoint_test(
    method="POST",
    path="/api/session-resources/upload-intents",
    scenario="friend_target_entity_routes_to_target_binding",
    input=CaseInput(headers=_AUTH_HEADERS, json_body=_FRIEND_UPLOAD_INTENT_BODY),
    seed=_seed_friend_target_bot,
    expect=ExpectSuccess(
        status=200,
        json_contains={"files": [{"status": "upload_url_issued"}]},
    ),
)
def upload_intents_friend_target_entity():
    """Route an interaction user's upload to the approved target Bot binding."""


@endpoint_test(
    method="POST",
    path="/api/session-resources/upload-intents",
    scenario="invalid_filename",
    input=CaseInput(headers=_AUTH_HEADERS, json_body=_INVALID_UPLOAD_INTENT_BODY),
    expect=ExpectError(status=400, json_contains={"detail": "invalid_filename"}),
)
def upload_intents_invalid_filename():
    """Reject an unsafe filename before external transfer work begins."""


@endpoint_test(
    method="POST",
    path="/api/session-resources/upload-intents",
    scenario="session_file_upstream_unavailable",
    input=CaseInput(
        headers=_AUTH_HEADERS, json_body=_UPSTREAM_ERROR_UPLOAD_INTENT_BODY
    ),
    seed=_seed_bot,
    expect=ExpectError(
        status=502,
        json_contains={"detail": "session_file_upstream_unavailable"},
    ),
)
def upload_intents_upstream_unavailable():
    """Map Session File failures without exposing its response body."""


@endpoint_test(
    method="POST",
    path="/api/session-resources/upload-complete",
    scenario="ok_done_queues_materialization",
    input=CaseInput(headers=_AUTH_HEADERS, json_body=_UPLOAD_COMPLETE_BODY),
    seed=_seed_upload_pending_record,
    expect=ExpectSuccess(
        status=200,
        json_contains={"resource_id": _RESOURCE_ID, "status": "device_syncing"},
    ),
)
def upload_complete_ok():
    """Complete a real transfer and persist the materialization task."""


@endpoint_test(
    method="POST",
    path="/api/session-resources/upload-complete",
    scenario="resource_not_found",
    input=CaseInput(headers=_AUTH_HEADERS, json_body=_UPLOAD_COMPLETE_BODY),
    expect=ExpectError(status=404, json_contains={"detail": "resource_not_found"}),
)
def upload_complete_not_found():
    """Reject completion for a resource outside the persisted state."""


@endpoint_test(
    method="GET",
    path="/api/session-resources/{resource_id}/materialize-status",
    scenario="ready_record",
    input=CaseInput(
        path_params={"resource_id": _RESOURCE_ID},
        query_params=_QUERY,
        headers=_AUTH_HEADERS,
    ),
    seed=_seed_ready_record,
    expect=ExpectSuccess(
        status=200,
        json_contains={"resource_id": _RESOURCE_ID, "status": "ready"},
    ),
)
def materialize_status_ready():
    """Read the real repository state without contacting the Engine."""


@endpoint_test(
    method="GET",
    path="/api/session-resources/{resource_id}/materialize-status",
    scenario="resource_not_found",
    input=CaseInput(
        path_params={"resource_id": "missing"},
        query_params=_QUERY,
        headers=_AUTH_HEADERS,
    ),
    expect=ExpectError(status=404, json_contains={"detail": "resource_not_found"}),
)
def materialize_status_not_found():
    """Require a resource owned by the requested session."""


@endpoint_test(
    method="GET",
    path="/api/session-resources/pending",
    scenario="upload_pending_record",
    input=CaseInput(query_params=_QUERY, headers=_AUTH_HEADERS),
    seed=_seed_upload_pending_record,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "files": [
                {"resource_id": _RESOURCE_ID, "status": "upload_url_issued"}
            ]
        },
    ),
)
def list_pending_resources_ok():
    """List non-ready resources for page reload recovery."""


@endpoint_test(
    method="GET",
    path="/api/session-resources/pending",
    scenario="missing_session_key",
    input=CaseInput(query_params={"bot_id": _BOT_ID}, headers=_AUTH_HEADERS),
    expect=ExpectError(status=422),
)
def list_pending_resources_missing_session_key():
    """Require the session key at the HTTP boundary."""


@endpoint_test(
    method="GET",
    path="/api/session-resources",
    scenario="ready_record",
    input=CaseInput(query_params=_QUERY, headers=_AUTH_HEADERS),
    seed=_seed_ready_record,
    expect=ExpectSuccess(
        status=200,
        json_contains={"files": [{"resource_id": _RESOURCE_ID}]},
    ),
)
def list_resources_ok():
    """List records from the real session-resource repository."""


@endpoint_test(
    method="GET",
    path="/api/session-resources",
    scenario="missing_session_key",
    input=CaseInput(query_params={"bot_id": _BOT_ID}, headers=_AUTH_HEADERS),
    expect=ExpectError(status=422),
)
def list_resources_missing_session_key():
    """Require the session key at the HTTP boundary."""


@endpoint_test(
    method="GET",
    path="/api/session-resources/referable-files",
    scenario="ready_record",
    input=CaseInput(query_params=_QUERY, headers=_AUTH_HEADERS),
    seed=_seed_ready_record,
    expect=ExpectSuccess(
        status=200,
        json_contains={"files": [{"resource_id": _RESOURCE_ID, "status": "ready"}]},
    ),
)
def referable_files_ok():
    """Expose only real ready records as references."""


@endpoint_test(
    method="GET",
    path="/api/session-resources/referable-files",
    scenario="missing_bot_id",
    input=CaseInput(
        query_params={"session_key": _SESSION_KEY},
        headers=_AUTH_HEADERS,
    ),
    expect=ExpectError(status=422),
)
def referable_files_missing_bot_id():
    """Require the bot identifier at the HTTP boundary."""


@endpoint_test(
    method="POST",
    path="/api/session-resources/{resource_id}/reference",
    scenario="ready_record",
    input=CaseInput(
        path_params={"resource_id": _RESOURCE_ID},
        headers=_AUTH_HEADERS,
        json_body=_REFERENCE_BODY,
    ),
    seed=_seed_ready_record,
    expect=ExpectSuccess(
        status=200,
        json_contains={"resource_id": _RESOURCE_ID, "insert_id": "insert-1"},
    ),
)
def create_reference_ok():
    """Create a reference from a ready persisted record."""


@endpoint_test(
    method="POST",
    path="/api/session-resources/{resource_id}/reference",
    scenario="resource_not_found",
    input=CaseInput(
        path_params={"resource_id": "missing"},
        headers=_AUTH_HEADERS,
        json_body=_REFERENCE_BODY,
    ),
    expect=ExpectError(status=404, json_contains={"detail": "resource_not_found"}),
)
def create_reference_not_found():
    """Reject references to records outside the session."""


def _resource_input(resource_id: str = _RESOURCE_ID) -> CaseInput:
    return CaseInput(
        path_params={"resource_id": resource_id},
        query_params=_QUERY,
        headers=_AUTH_HEADERS,
    )


@endpoint_test(
    method="GET",
    path="/api/session-resources/{resource_id}/content",
    scenario="ready_engine_content",
    input=_resource_input(),
    seed=_seed_content_ready,
    expect=ExpectSuccess(status=200),
    extra_assertions=(_assert_content_response,),
)
def content_ok():
    """Proxy a ready Engine workspace stream through the real service."""


@endpoint_test(
    method="GET",
    path="/api/session-resources/{resource_id}/content",
    scenario="engine_file_missing_requires_reupload",
    input=_resource_input(),
    seed=_seed_content_missing,
    expect=ExpectError(
        status=409,
        json_contains={"detail": "resource_missing"},
    ),
)
def content_missing_requires_reupload():
    """A missing Engine file does not re-materialize from BaaS."""


@endpoint_test(
    method="DELETE",
    path="/api/session-resources/{resource_id}",
    scenario="ready_record",
    input=_resource_input(),
    seed=_seed_ready_record,
    expect=ExpectSuccess(
        status=200,
        json_contains={"resource_id": _RESOURCE_ID, "status": "deleted"},
    ),
)
def delete_resource_ok():
    """Soft-delete the real persisted record."""


@endpoint_test(
    method="DELETE",
    path="/api/session-resources/{resource_id}",
    scenario="resource_not_found",
    input=_resource_input("missing"),
    expect=ExpectError(status=404, json_contains={"detail": "resource_not_found"}),
)
def delete_resource_not_found():
    """Reject deletion of a missing record."""


@endpoint_test(
    method="POST",
    path="/internal/session-resources/{resource_id}/materialized",
    scenario="ready_callback",
    input=CaseInput(
        path_params={"resource_id": _RESOURCE_ID},
        headers={"x-materialization-task-id": _TASK_ID},
        json_body=_CALLBACK_BODY,
    ),
    seed=_seed_callback_pending,
    expect=ExpectSuccess(
        status=200,
        json_contains={"applied": True, "status": "ready"},
    ),
)
def materialized_callback_ok():
    """Apply a valid callback with real task-version CAS."""


@endpoint_test(
    method="POST",
    path="/internal/session-resources/{resource_id}/materialized",
    scenario="invalid_task_capability",
    input=CaseInput(
        path_params={"resource_id": _RESOURCE_ID},
        headers={"x-materialization-task-id": "wrong-task"},
        json_body=_CALLBACK_BODY,
    ),
    expect=ExpectError(
        status=401,
        json_contains={"detail": "invalid materialization capability"},
    ),
)
def materialized_callback_invalid_capability():
    """Reject a callback whose header capability does not match its body."""
