"""Endpoint-framework coverage for Bot Workshop configuration resources."""

from __future__ import annotations

import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.render_screen_service import RenderScreenServiceProtocol
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.factories.bot_collaborator import (
    make_bot,
    make_collaborator,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)

_OWNER = "workshop-config-owner"
_EDITOR = "workshop-config-editor"
_BOT = "workshop-config-bot"
_MISSING_BOT = "workshop-config-missing"
_EDITOR_ID = 1
_SCREEN_ID = 1
_KEY = "workshop-config-endpoint-signing-key-32-bytes"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal(user_id: str) -> str:
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
                        "id": user_id,
                        "username": f"{user_id}@example.test",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )


_OWNER_HEADERS = {PRINCIPAL_HEADER: _principal(_OWNER)}
_EDITOR_HEADERS = {PRINCIPAL_HEADER: _principal(_EDITOR)}
_OWNER_QUERY = {"user_id": _OWNER}
_EDITOR_QUERY = {"user_id": _EDITOR, "owner_id": _OWNER}


def _boot_verifier() -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _seed_no_bot(_world) -> None:
    _boot_verifier()


def _seed_bot(world) -> None:
    _boot_verifier()
    make_bot(world, bot_id=_BOT, owner_id=_OWNER, bot_type="service")


def _seed_editor(world) -> None:
    _seed_bot(world)
    record = make_collaborator(
        world,
        bot_id=_BOT,
        owner_id=_OWNER,
        user_id=_EDITOR,
        role="member",
        operator_id=_OWNER,
    )
    assert record.id == _EDITOR_ID


def _seed_render_screen(world) -> None:
    _seed_bot(world)
    record_id = world.get(RenderScreenServiceProtocol).create_render_screen(
        bot_id=_BOT,
        owner_id=_OWNER,
        name="dashboard",
        cdn_url="https://cdn.example.test/dashboard.js",
        creator_id=_OWNER,
    )
    assert record_id == _SCREEN_ID


# Editors


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/editors",
    scenario="lists_bot_editors",
    input=CaseInput(
        path_params={"bot_id": _BOT},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
    ),
    seed=_seed_editor,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "total": 1,
                "items": [{"id": _EDITOR_ID, "user_id": _EDITOR, "role": "member"}],
            },
        },
    ),
)
def list_editors_ok():
    """The assembled route returns the persisted public Editor projection."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/editors",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": _MISSING_BOT},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def list_editors_unknown_bot():
    """An absent Bot is masked as not found."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/editors",
    scenario="adds_member_editor",
    input=CaseInput(
        path_params={"bot_id": _BOT},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
        json_body={"editor_user_id": _EDITOR, "role": "member"},
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {"id": _EDITOR_ID, "user_id": _EDITOR, "role": "member"},
        },
    ),
)
def add_editor_ok():
    """The owner can add a member Editor through the public contract."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/editors",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": _MISSING_BOT},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
        json_body={"editor_user_id": _EDITOR, "role": "member"},
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def add_editor_unknown_bot():
    """An Editor relation cannot be created for an absent Bot."""


@endpoint_test(
    method="PATCH",
    path="/openapi/v1/bots/{bot_id}/editors/{editor_id}",
    scenario="promotes_editor",
    input=CaseInput(
        path_params={"bot_id": _BOT, "editor_id": _EDITOR_ID},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
        json_body={"role": "admin"},
    ),
    seed=_seed_editor,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"id": _EDITOR_ID, "user_id": _EDITOR, "role": "admin"},
        },
    ),
)
def update_editor_ok():
    """The addressed relation is updated after Bot/owner/env rebinding."""


@endpoint_test(
    method="PATCH",
    path="/openapi/v1/bots/{bot_id}/editors/{editor_id}",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": _MISSING_BOT, "editor_id": _EDITOR_ID},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
        json_body={"role": "admin"},
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def update_editor_unknown_bot():
    """The Bot check precedes use of a public relation id."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/editors/{editor_id}",
    scenario="removes_editor",
    input=CaseInput(
        path_params={"bot_id": _BOT, "editor_id": _EDITOR_ID},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
    ),
    seed=_seed_editor,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"deleted": True}},
    ),
)
def remove_editor_ok():
    """The owner can remove an Editor from the addressed Bot."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/editors/{editor_id}",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": _MISSING_BOT, "editor_id": _EDITOR_ID},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def remove_editor_unknown_bot():
    """A relation id cannot be aimed at an absent Bot."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/editors/me",
    scenario="editor_leaves",
    input=CaseInput(
        path_params={"bot_id": _BOT},
        query_params=_EDITOR_QUERY,
        headers=_EDITOR_HEADERS,
    ),
    seed=_seed_editor,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"deleted": True}},
    ),
)
def leave_editors_ok():
    """A member can remove only their own Editor relation."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/editors/me",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": _MISSING_BOT},
        query_params=_EDITOR_QUERY,
        headers=_EDITOR_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def leave_editors_unknown_bot():
    """Leaving an absent Bot does not disclose collaborator state."""


# Render screens


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/render-screens",
    scenario="lists_render_screens",
    input=CaseInput(
        path_params={"bot_id": _BOT},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
    ),
    seed=_seed_render_screen,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "total": 1,
                "items": [
                    {
                        "id": _SCREEN_ID,
                        "name": "dashboard",
                        "cdn_url": "https://cdn.example.test/dashboard.js",
                    }
                ],
            },
        },
    ),
)
def list_render_screens_ok():
    """The public list exposes only the render-screen projection."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/bots/{bot_id}/render-screens",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": _MISSING_BOT},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def list_render_screens_unknown_bot():
    """An absent addressed Bot is masked as not found."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/render-screens",
    scenario="creates_render_screen",
    input=CaseInput(
        path_params={"bot_id": _BOT},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
        json_body={
            "name": "dashboard",
            "cdn_url": "https://cdn.example.test/dashboard.js",
        },
    ),
    seed=_seed_bot,
    expect=ExpectSuccess(
        status=201,
        json_contains={
            "code": 201000,
            "data": {"id": _SCREEN_ID, "name": "dashboard"},
        },
    ),
)
def create_render_screen_ok():
    """The owner can create a valid HTTP(S) CDN mapping."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/bots/{bot_id}/render-screens",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": _MISSING_BOT},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
        json_body={
            "name": "dashboard",
            "cdn_url": "https://cdn.example.test/dashboard.js",
        },
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def create_render_screen_unknown_bot():
    """A mapping cannot be created for an absent Bot."""


@endpoint_test(
    method="PATCH",
    path="/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}",
    scenario="updates_render_screen",
    input=CaseInput(
        path_params={"bot_id": _BOT, "render_screen_id": _SCREEN_ID},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
        json_body={
            "name": "dashboard-v2",
            "cdn_url": "https://cdn.example.test/dashboard-v2.js",
        },
    ),
    seed=_seed_render_screen,
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"id": _SCREEN_ID, "name": "dashboard-v2"},
        },
    ),
)
def update_render_screen_ok():
    """The mapping is updated after record-to-Bot rebinding."""


@endpoint_test(
    method="PATCH",
    path="/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": _MISSING_BOT, "render_screen_id": _SCREEN_ID},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
        json_body={
            "name": "dashboard-v2",
            "cdn_url": "https://cdn.example.test/dashboard-v2.js",
        },
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def update_render_screen_unknown_bot():
    """The Bot authorization runs before a mapping id is trusted."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}",
    scenario="deletes_render_screen",
    input=CaseInput(
        path_params={"bot_id": _BOT, "render_screen_id": _SCREEN_ID},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
    ),
    seed=_seed_render_screen,
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"deleted": True}},
    ),
)
def delete_render_screen_ok():
    """The owner can soft-delete the addressed mapping."""


@endpoint_test(
    method="DELETE",
    path="/openapi/v1/bots/{bot_id}/render-screens/{render_screen_id}",
    scenario="unknown_bot",
    input=CaseInput(
        path_params={"bot_id": _MISSING_BOT, "render_screen_id": _SCREEN_ID},
        query_params=_OWNER_QUERY,
        headers=_OWNER_HEADERS,
    ),
    seed=_seed_no_bot,
    expect=ExpectError(status=404, json_contains={"data": None}),
)
def delete_render_screen_unknown_bot():
    """An absent Bot masks the mapping id as not found."""
