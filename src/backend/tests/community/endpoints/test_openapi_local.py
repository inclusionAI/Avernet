"""Declarative happy/error coverage for the public local-Bot surface.

The nine ``/openapi/v1/bots/**/local**`` operations sat on
``coverage_baseline.txt`` as frozen debt for the reason the routines and
resources rows carried: the case runner authenticates with ``x-user-id``,
``require_principal`` accepts only a gateway-signed token, and the harness had
no minter — so a case could assert nothing but a 401.

That reason has expired. ``test_openapi_session_files.py`` mints a principal
here in the test tree, by pointing ``init_principal_verifier_config`` at a local
signing key and handing the runner a token signed with it. This file does the
same, so the local rows come off the baseline and the coverage gate holds them
from here on.

What these cases add over
``tests/community/adapters/http/openapi_v1/local/test_local_handlers.py`` —
which mounts the router on a bare ``FastAPI()`` with a hand-built injector and
``require_principal`` overridden — is the assembled application: the real DI
graph, the real router mount, the real principal verification, and the
app-level envelope/error handlers. Those are the parts that fail in assembly
rather than in a handler.

Every operation here takes ``user_id`` (``UserIdDep``), including the four that
name no bot, so the refusal each one owes is the same: ``user_id`` naming
someone other than the caller the principal authenticated.
"""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.local_bot_workflow_service import (
    LocalBotWorkflowServiceProtocol,
)
from agentclaw.community.core.bot_inventory.types import LocalAuthStatusResult
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

_OWNER = "local-owner"
_BOT_ID = "local-bot"
_MACHINE_ID = "local-machine"
_KEY = "local-framework-signing-key-at-least-32-bytes"
_COLLECTION_PATH = "/openapi/v1/bots/local"
_BOT_PATH = "/openapi/v1/bots/{bot_id}/local"
_PATH_PARAMS = {"bot_id": _BOT_ID}
_MACHINE_PATH_PARAMS = {"machine_id": _MACHINE_ID}


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
                    "subject": {"id": _OWNER, "username": "local@example.test"},
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_HEADERS = {PRINCIPAL_HEADER: _principal()}


def _query(**extra) -> dict:
    return {"user_id": _OWNER, **extra}


def _forbidden(query) -> dict:
    """The same request, aimed at a user the principal did not authenticate."""
    return {**query, "user_id": "another-user"}


def _row() -> dict:
    """One local bot exactly as the workflow service returns it."""
    return {
        "bot_id": _BOT_ID,
        "bot_name": "Local",
        "bot_desc": "a local bot",
        "active_engine": "openclaw",
        "bot_type": "desktop",
        "status": "ACTIVE",
        "owner_id": _OWNER,
        "machine_id": _MACHINE_ID,
        "mount_path": "/Users/local/workspace",
    }


def _device() -> dict:
    return {
        "machine_id": _MACHINE_ID,
        "machine_name": "Local Mac",
        "hostname": "local.example.test",
        "status": "ACTIVE",
        "ip_address": "10.0.0.1",
    }


def _seed_verifier(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_happy_services(world) -> None:
    _seed_verifier(world)
    # The five bot-scoped operations declare ``require_granted_own_bot``; the
    # handlers then resolve the bot through the real repository. The workflow
    # override below answers without a Bot row, so seed one.
    make_bot(world, bot_id=_BOT_ID, owner_id=_OWNER, bot_type="desktop")

    def list_devices(_self, **_kwargs):
        return 1, [_device()]

    def list_device_files(_self, **_kwargs):
        return {"name": "Desktop", "children": []}

    def start_create(_self, **_kwargs):
        # The already-authorized branch: a device that needs no Passport
        # consent answers with the created bot, which is the documented 201.
        # (The 202 ``need_authorization`` branch is a different response and
        # is pinned by the handler unit tests.)
        return _row()

    def list_bots(_self, **_kwargs):
        return 1, [_row()]

    def get_bot(_self, **_kwargs):
        return _row()

    def poll_auth_status(_self, **_kwargs):
        return LocalAuthStatusResult(status="ISSUED", message=None, bot=_row())

    def restart(_self, **_kwargs):
        return {"bot_id": _BOT_ID, "status": "PENDING"}

    def delete(_self, **_kwargs) -> None:
        return None

    def open_folder(_self, **_kwargs):
        return {"bot_id": _BOT_ID}

    bind_overrides(
        world,
        LocalBotWorkflowServiceProtocol,
        {
            "list_devices": list_devices,
            "list_device_files": list_device_files,
            "start_create": start_create,
            "list_bots": list_bots,
            "get_bot": get_bot,
            "poll_auth_status": poll_auth_status,
            "restart": restart,
            "delete": delete,
            "open_folder": open_folder,
        },
    )


_CREATE_BODY = {
    "bot_name": "Local",
    "machine_id": _MACHINE_ID,
    "engine": "openclaw",
}
_OPEN_FOLDER_BODY = {"folder_path": "src"}
#: ``auth-status`` completes a pending creation, so it carries the create
#: command in the query string; both are required parameters.
_AUTH_STATUS_QUERY = {"bot_name": "Local", "machine_id": _MACHINE_ID}

#: ``(method, path, input, status, body_subset)``. The subset is not decoration:
#: a status-only assertion would still hold if the projection dropped every
#: field, so each case pins the part of the envelope its own handler builds.
_HAPPY_CASES = (
    (
        "GET",
        _COLLECTION_PATH,
        CaseInput(query_params=_query(), headers=_HEADERS),
        200,
        {"code": 200000, "data": {"total": 1, "items": [{"bot_id": _BOT_ID}]}},
    ),
    (
        "POST",
        _COLLECTION_PATH,
        CaseInput(query_params=_query(), headers=_HEADERS, json_body=_CREATE_BODY),
        201,
        {"code": 201000, "data": {"bot_id": _BOT_ID, "engine": "openclaw"}},
    ),
    (
        "GET",
        f"{_COLLECTION_PATH}/devices",
        CaseInput(query_params=_query(), headers=_HEADERS),
        200,
        {"data": {"total": 1, "items": [{"machine_id": _MACHINE_ID}]}},
    ),
    (
        "GET",
        f"{_COLLECTION_PATH}/devices/{{machine_id}}/files",
        CaseInput(
            path_params=_MACHINE_PATH_PARAMS,
            query_params=_query(dir="~/Desktop"),
            headers=_HEADERS,
        ),
        200,
        {"data": {"name": "Desktop"}},
    ),
    (
        "GET",
        _BOT_PATH,
        CaseInput(path_params=_PATH_PARAMS, query_params=_query(), headers=_HEADERS),
        200,
        {"data": {"bot_id": _BOT_ID, "owner_entity_id": _OWNER}},
    ),
    (
        "GET",
        f"{_BOT_PATH}/auth-status",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(**_AUTH_STATUS_QUERY),
            headers=_HEADERS,
        ),
        200,
        {"data": {"status": "ISSUED", "bot": {"bot_id": _BOT_ID}}},
    ),
    (
        "POST",
        f"{_BOT_PATH}/restart",
        CaseInput(path_params=_PATH_PARAMS, query_params=_query(), headers=_HEADERS),
        200,
        {"data": {"bot_id": _BOT_ID, "status": "PENDING"}},
    ),
    (
        "POST",
        f"{_BOT_PATH}/open-folder",
        CaseInput(
            path_params=_PATH_PARAMS,
            query_params=_query(),
            headers=_HEADERS,
            json_body=_OPEN_FOLDER_BODY,
        ),
        200,
        {"data": {"bot_id": _BOT_ID}},
    ),
    (
        "DELETE",
        _BOT_PATH,
        CaseInput(path_params=_PATH_PARAMS, query_params=_query(), headers=_HEADERS),
        200,
        {"data": {"deleted": True}},
    ),
)


for _method, _path, _input, _status, _contains in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        input=_input,
        seed=_seed_happy_services,
        expect=ExpectSuccess(status=_status, json_contains=_contains),
    )(lambda: None)


# The refusal every operation on this surface owes: ``user_id`` names someone
# other than the caller the principal authenticated. Everything else about the
# request stays valid — same body, same required query parameters — so the 403
# can only come from the id, not from a missing field. ``require_user_id``
# raises ahead of the handler, so no workflow service is seeded: reaching one
# would itself be the bug.
for _method, _path, _input, _status, _contains in _HAPPY_CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="forbidden_user_scope",
        input=CaseInput(
            path_params=_input.path_params,
            query_params=_forbidden(_input.query_params),
            headers=_HEADERS,
            json_body=_input.json_body,
        ),
        seed=_seed_verifier,
        expect=ExpectError(
            status=403, json_contains={"code": 403000, "data": None}
        ),
    )(lambda: None)
