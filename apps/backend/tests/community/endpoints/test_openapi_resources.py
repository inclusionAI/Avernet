"""Declarative happy/error coverage for the public Resources surface.

The seven resources operations sat on ``coverage_baseline.txt`` as frozen
debt, with the note: *"the case runner authenticates with x-user-id, this
surface requires a gateway-signed principal, and the harness has no minter, so
a case could assert nothing but a 401 … The handlers themselves are covered by
unit tests in tests/community/adapters/http/openapi_v1/resources/."*

Both halves of that note can now be answered. The minter exists —
``test_openapi_session_files.py`` builds one in the test tree by pointing
``init_principal_verifier_config`` at a local signing key — and the handler
unit tests, thorough as they are, call ``await handler(...)`` with every
dependency passed by keyword, so FastAPI's wiring and the injector never run.
Those are precisely the parts that fail in assembly rather than in a handler:
a binding that does not resolve, a router that is not mounted, a query
parameter that does not bind. These cases put them on the wire.

``download`` is the one operation here whose success body is not an envelope —
it answers raw ``application/octet-stream`` bytes — so its happy case asserts
the status alone.
"""

from __future__ import annotations

import time

import httpx
import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.resource_service import ResourceServiceFactoryProtocol
from agentclaw.community.core.services.resource_file_service import ResourceFileService
from agentclaw.community.core.devices.services.device_context import DeviceOfflineError
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_OWNER = "resource-owner"
_BOT_ID = "resource-bot"
_KEY = "resources-framework-signing-key-at-least-32-bytes"
_BASE_PATH = "/openapi/v1/bots/{bot_id}/resources"
_PATH_PARAMS = {"bot_id": _BOT_ID}

#: A path the workspace already holds — what ``stat``, ``download``,
#: ``preview`` and ``delete`` address.
_EXISTING_PATH = "docs/a.txt"
#: A free path, so ``upload`` takes its fresh branch rather than 409ing.
_NEW_PATH = "docs/new.txt"
_NEW_DIR = "docs/sub"


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
                    "subject": {"id": _OWNER, "username": "resources@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}
_OCTET_HEADERS = {**_HEADERS, "content-type": "application/octet-stream"}


def _query(**extra) -> dict:
    return {"user_id": _OWNER, **extra}


def _forbidden_query(**extra) -> dict:
    return {"user_id": "another-user", **extra}


def _listed_entry() -> dict:
    """One entry exactly as ``ResourceFileService.list_dir`` returns it.

    ``stat`` lists the *parent* directory and matches on ``path``, so this
    entry serves the listing and the single-entry lookup alike.
    """
    return {
        "name": "a.txt",
        "path": _EXISTING_PATH,
        "absolute_path": f"/home/admin/.aicoding/workspace/{_EXISTING_PATH}",
        "is_dir": False,
        "readonly": False,
        "size": 11,
        "size_human": None,
        "modified_at": None,
    }


class _Records:
    """The record side of the resources seam — writes the manifest rows."""

    async def record_uploaded_file(self, **_kwargs):
        return None

    async def delete_file_record(self, **_kwargs) -> bool:
        return True


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_happy_services(world) -> None:
    _seed_verifier(world)
    # ``_file_coords`` resolves the bot's engine through the real
    # ``BotRepository``, so every operation here needs a Bot row to exist.
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER)

    async def list_dir(_self, **_kwargs):
        return [_listed_entry()]

    async def read_file(_self, **_kwargs):
        return b"hello world"

    async def iter_directory_files(_self, **_kwargs):
        # The download-dir walk: one file, exactly the service's contract.
        yield ("a.txt", b"hello world")

    async def exists(_self, *, path, **_kwargs) -> bool:
        # ``upload`` must find its target free (fresh branch, no 409) while
        # ``delete`` must find its target present (or it 404s).
        return path == _EXISTING_PATH

    async def upload_file(_self, **kwargs):
        rel = kwargs["filename"]
        return {
            "name": rel.rsplit("/", 1)[-1],
            "path": rel,
            "size": len(kwargs["data"]),
        }

    async def create_directory(_self, **_kwargs) -> None:
        return None

    async def delete(_self, **_kwargs) -> bool:
        return True

    bind_overrides(
        world,
        ResourceFileService,
        {
            "list_dir": list_dir,
            "read_file": read_file,
            "iter_directory_files": iter_directory_files,
            "exists": exists,
            "upload_file": upload_file,
            "create_directory": create_directory,
            "delete": delete,
        },
    )

    def create(_self, **_kwargs):
        return _Records()

    bind_overrides(world, ResourceServiceFactoryProtocol, {"create": create})


#: The projection each operation owes, pinned alongside the status — otherwise
#: an empty listing, or one path's metadata answered for another, would pass.
#: ``download`` is the sole ``None``: its body is raw bytes, not an envelope.
_HAPPY_CASES = (
    (
        "GET",
        _BASE_PATH,
        CaseInput(
            path_params=_PATH_PARAMS, query_params=_query(path="docs"), headers=_HEADERS
        ),
        200,
        {
            "data": {
                "total": 1,
                "items": [{"path": _EXISTING_PATH, "name": "a.txt", "type": "file"}],
            }
        },
    ),
    (
        "GET",
        f"{_BASE_PATH}/stat",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(path=_EXISTING_PATH),
            headers=_HEADERS,
        ),
        200,
        {"data": {"path": _EXISTING_PATH, "name": "a.txt", "type": "file", "size": 11}},
    ),
    (
        "GET",
        f"{_BASE_PATH}/download",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(path=_EXISTING_PATH),
            headers=_HEADERS,
        ),
        200,
        None,
    ),
    (
        "GET",
        f"{_BASE_PATH}/download-dir",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(path="docs"),
            headers=_HEADERS,
        ),
        200,
        # Raw zip bytes, not an envelope — same ``None`` projection as
        # ``download``.
        None,
    ),
    (
        "GET",
        f"{_BASE_PATH}/preview",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(path=_EXISTING_PATH),
            headers=_HEADERS,
        ),
        200,
        {"data": {"path": _EXISTING_PATH, "content": "hello world"}},
    ),
    (
        "POST",
        f"{_BASE_PATH}/upload",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(path=_NEW_PATH),
            headers=_OCTET_HEADERS,
            raw_body=b"hello world",
        ),
        201,
        {"data": {"path": _NEW_PATH, "name": "new.txt", "type": "file", "size": 11}},
    ),
    (
        "POST",
        f"{_BASE_PATH}/mkdir",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(path=_NEW_DIR),
            headers=_HEADERS,
        ),
        201,
        {"data": {"path": _NEW_DIR, "name": "sub", "type": "folder"}},
    ),
    (
        "DELETE",
        _BASE_PATH,
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(path=_EXISTING_PATH),
            headers=_HEADERS,
        ),
        200,
        {"data": {"deleted": True}},
    ),
)


for _method, _path, _input, _status, _body in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed_happy_services,
        expect=ExpectSuccess(status=_status, json_contains=_body or {}),
    )(lambda: None)


endpoint_test(
    method="GET",
    path=_BASE_PATH,
    scenario="legacy_preview_action",
    input=CaseInput(
        path_params=_PATH_PARAMS,
        query_params=_query(path=_EXISTING_PATH, action="preview"),
        headers=_HEADERS,
    ),
    seed=_seed_happy_services,
    expect=ExpectSuccess(
        status=200,
        json_contains={"data": {"path": _EXISTING_PATH, "content": "hello world"}},
    ),
)(lambda: None)


# The refusal every user-scoped operation on this surface owes: ``user_id``
# names someone other than the caller the principal authenticated.
# ``require_user_id`` raises ahead of the handler, so no workspace is seeded —
# reaching one would itself be the bug.
for _method, _path, _input, _status, _body in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="forbidden_user_scope",
        input=CaseInput(
            path_params=_input.path_params,
            query_params=_forbidden_query(path=_input.query_params["path"]),
            headers=_input.headers,
            raw_body=_input.raw_body,
        ),
        seed=_seed_verifier,
        expect=ExpectError(status=403, json_contains={"data": None}),
    )(lambda: None)


def _seed_offline_services(world) -> None:
    _seed_happy_services(world)

    async def list_dir(_self, **_kwargs):
        raise DeviceOfflineError("device offline")

    bind_overrides(world, ResourceFileService, {"list_dir": list_dir})


endpoint_test(
    method="GET",
    path=_BASE_PATH,
    scenario="device_offline",
    input=CaseInput(
        path_params=_PATH_PARAMS, query_params=_query(path="docs"), headers=_HEADERS
    ),
    seed=_seed_offline_services,
    expect=ExpectError(
        status=502, json_contains={"code": 502000, "message": "Bot device is offline", "data": None}
    ),
)(lambda: None)


# The CDO-reported shape on this surface: an upstream transport fault from the
# device filesystem escaping the handler as an unhandled ``HTTPStatusError``
# (500 + ERROR traceback), instead of this group's classified 502. Storage
# faults must answer 502 whichever leg raises — the listing itself, or the
# existence probe delete and upload make before touching the workspace.
def _http_status_error(code: int):
    request = httpx.Request("POST", "http://baas/api/file/list")
    return httpx.HTTPStatusError(
        f"HTTP {code}", request=request, response=httpx.Response(code, request=request)
    )


def _seed_upstream_fault_services(world) -> None:
    _seed_happy_services(world)

    async def list_dir(_self, **_kwargs):
        raise _http_status_error(503)

    async def read_file(_self, **_kwargs):
        raise _http_status_error(500)

    async def exists(_self, *, path, **_kwargs):
        raise _http_status_error(500)

    bind_overrides(
        world,
        ResourceFileService,
        {"list_dir": list_dir, "read_file": read_file, "exists": exists},
    )


for _method, _path, _input, _status in (
    (
        "GET",
        _BASE_PATH,
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(path="docs"),
            headers=_HEADERS,
        ),
        502,
    ),
    (
        "GET",
        f"{_BASE_PATH}/download",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(path=_EXISTING_PATH),
            headers=_HEADERS,
        ),
        502,
    ),
    (
        "DELETE",
        _BASE_PATH,
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(path=_EXISTING_PATH),
            headers=_HEADERS,
        ),
        502,
    ),
    (
        "POST",
        f"{_BASE_PATH}/upload",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(path=_NEW_PATH),
            headers=_OCTET_HEADERS,
            raw_body=b"hello world",
        ),
        502,
    ),
):
    endpoint_test(
        method=_method,
        path=_path,
        scenario="upstream_storage_fault",
        input=_input,
        seed=_seed_upstream_fault_services,
        # The public message is the status phrase by house convention; the
        # handler's per-operation detail ("List storage failed" etc.) is
        # logged, not returned.
        expect=ExpectError(
            status=_status,
            json_contains={"code": _status * 1000, "message": "Bad Gateway", "data": None},
        ),
    )(lambda: None)
