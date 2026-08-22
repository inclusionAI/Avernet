"""Declarative happy/error coverage for the public Session File lifecycle."""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.engine_runtime_service import EngineRuntimeRelayProtocol
from agentclaw.community.api.session_resource_service import (
    SessionResourceServiceProtocol,
)
from agentclaw.community.core.engine_runtime.models import (
    BotFacts,
)
from agentclaw.community.core.session_resources.types import (
    SessionResourceRecord,
    SessionResourceStatus,
    SessionUploadIntent,
    UploadGrant,
    hash_identifier,
)
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_OWNER = "session-file-owner"
_BOT_ID = "session-file-bot"
_SESSION_ID = "session-file-session"
_RESOURCE_ID = "session-file-resource"
_TRANSFER_ID = "session-file-transfer"
_KEY = "session-file-framework-signing-key-at-least-32-bytes"
_BASE_PATH = "/openapi/v1/bots/{bot_id}/sessions/{session_id}/files"
_PATH_PARAMS = {"bot_id": _BOT_ID, "session_id": _SESSION_ID}
_RESOURCE_PATH_PARAMS = {**_PATH_PARAMS, "resource_id": _RESOURCE_ID}


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "user",
                    "subject": {
                        "id": _OWNER,
                        "username": "session-file@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_QUERY = {"user_id": _OWNER}
_FORBIDDEN_QUERY = {"user_id": "another-user"}


def _record() -> SessionResourceRecord:
    return SessionResourceRecord(
        resource_id=_RESOURCE_ID,
        owner_id=_OWNER,
        bot_id=_BOT_ID,
        scope_type="openapi_session",
        scope_key_hash=hash_identifier(f"openapi_session:{_OWNER}:{_BOT_ID}"),
        session_key_hash=hash_identifier(_SESSION_ID),
        engine_type="openclaw",
        tenant="test-tenant",
        bot_uuid="session-file-device",
        display_name="brief.txt",
        filename="brief.txt",
        device_path="/workspace/brief.txt",
        workspace_relative_path="brief.txt",
        transfer_id=_TRANSFER_ID,
        status=SessionResourceStatus.READY,
        binding_id=101,
        size_bytes=3,
        client_content_hash="abc",
    )


class _ContentUpstream:
    status_code = 200
    headers = {"content-type": "text/plain", "content-length": "3"}

    @property
    def body(self):
        async def chunks():
            yield b"abc"

        return chunks()

    async def close(self) -> None:
        return None


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_happy_services(world) -> None:
    _seed_verifier(world)
    record = _record()
    async def resolve_bot(_self, *_args, **_kwargs):
        return BotFacts(
            bot_id=_BOT_ID,
            bot_type="personal",
            active_engine="openclaw",
            owner_id=_OWNER,
        )

    bind_overrides(
        world,
        EngineRuntimeRelayProtocol,
        {
            "resolve_bot_off_loop": resolve_bot,
        },
    )

    def resolve_binding(_self, _request):
        return SimpleNamespace(
            binding_id=101,
        )

    bind_overrides(
        world,
        RuntimeBindingResolutionService,
        {
            "resolve": resolve_binding,
        },
    )

    def create_upload_intent(_self, **_kwargs):
        return SessionUploadIntent(
            resource=record,
            grant=UploadGrant(
                transfer_id=_TRANSFER_ID,
                upload_type="SINGLE",
                upload_url="https://upload.example.invalid/session-file",
            ),
        )

    def answer_record(_self, **_kwargs):
        return record

    def answer_records(_self, **_kwargs):
        return [record]

    async def open_session_file_content(_self, **_kwargs):
        return record, _ContentUpstream()

    bind_overrides(
        world,
        SessionResourceServiceProtocol,
        {
            "create_upload_intent": create_upload_intent,
            "complete_upload": answer_record,
            "get_status": answer_record,
            "list_resources": answer_records,
            "open_session_file_content": open_session_file_content,
            "delete": answer_record,
        },
    )


_UPLOAD_BODY = {
    "files": [
        {"filename": "brief.txt", "size_bytes": 3, "content_hash": "abc"}
    ]
}
_COMPLETE_BODY = {"resource_id": _RESOURCE_ID, "transfer_id": _TRANSFER_ID}

_HAPPY_CASES = (
    (
        "POST",
        f"{_BASE_PATH}/upload-intents",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
            json_body=_UPLOAD_BODY,
        ),
        201,
    ),
    (
        "POST",
        f"{_BASE_PATH}/upload-complete",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
            json_body=_COMPLETE_BODY,
        ),
        200,
    ),
    (
        "GET",
        f"{_BASE_PATH}/{{resource_id}}/materialize-status",
        CaseInput(
            path_params=_RESOURCE_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
        ),
        200,
    ),
    (
        "GET",
        _BASE_PATH,
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
        ),
        200,
    ),
    (
        "GET",
        f"{_BASE_PATH}/{{resource_id}}/content",
        CaseInput(
            path_params=_RESOURCE_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
        ),
        200,
    ),
    (
        "DELETE",
        f"{_BASE_PATH}/{{resource_id}}",
        CaseInput(
            path_params=_RESOURCE_PATH_PARAMS,
            query_params=_QUERY,
            headers=_HEADERS,
        ),
        200,
    ),
)


for _method, _path, _input, _status in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed_happy_services,
        expect=ExpectSuccess(status=_status),
    )(lambda: None)


for _method, _path, _input, _status in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="forbidden_user_scope",
        input=CaseInput(
            path_params=_input.path_params,
            query_params=_FORBIDDEN_QUERY,
            headers=_HEADERS,
            json_body=_input.json_body,
        ),
        seed=_seed_verifier,
        expect=ExpectError(status=403, json_contains={"data": None}),
    )(lambda: None)
