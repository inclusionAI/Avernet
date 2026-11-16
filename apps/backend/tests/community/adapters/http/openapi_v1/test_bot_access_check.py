"""Transitional ``GET /openapi/v1/bots/{bot_id}/access`` — allowlisted apps only.

Wired through the real signed-principal seam and public error handlers, with a
fake Bot repository and collaborator service, so each case pins one rule:

- the caller must be an allowlisted application (by ``app_name``), else 403
  before anything is read;
- the level is the operable ladder the ``/openapi/v1`` gate uses, measured
  against ``required_level`` (default member);
- ``entity_id`` is optional: without it ``bot_id`` must be unique, and an
  ambiguous ``bot_id`` is a 409 rather than a guess;
- no Bot at the address is a real "no" (200), while a failed
  collaborator lookup is a 500, never a fabricated "no";
- the route never enters the published OpenAPI document.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.openapi_v1 import build_public_router
from agentclaw.community.adapters.http.openapi_v1.bot_access_check import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    PRINCIPAL_HEADER,
    require_principal,
)
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_management.errors import BotLookupAmbiguousError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.di.openapi_bot_access_check_config import (
    OpenApiBotAccessCheckConfig,
)
from tests.community.adapters.http.openapi_v1._route_walk import (
    effective_routes,
    path_of,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
)
from tests.community.adapters.http.openapi_v1.test_principal_seam import (
    mint,
    signing_key,  # noqa: F401 — installs/resets the real verifier per test
)

#: The ``app_name`` ``mint`` puts on its app principal.
ALLOWED_APP = "bot-logs-test-app"
OWNER = "owner-1"
BOT = {"id": 7, "bot_id": "bot-1", "entity_id": "ent-1", "owner_id": OWNER}
_PATH = "/openapi/v1/bots/bot-1/access"


class _Collaborators:
    """Explicit levels by user, plus Space members granted MEMBER."""

    def __init__(self) -> None:
        self.levels: dict[str, PermissionLevel] = {}
        self.space_members: set[str] = set()
        self.raise_on_lookup = False
        self.calls: list[str] = []

    def get_operable_permission_level(
        self, *, bot: dict[str, Any], user_id: str, env: str | None = None
    ) -> PermissionLevel:
        self.calls.append(user_id)
        if self.raise_on_lookup:
            raise RuntimeError("collaborator table unavailable")
        level = self.levels.get(user_id, PermissionLevel.NONE)
        if level is PermissionLevel.NONE and user_id in self.space_members:
            return PermissionLevel.MEMBER
        return level


@pytest.fixture
def access_client():
    bots = MagicMock(spec=BotRepository)
    bots.get_by_id_and_entity.side_effect = lambda bot_id, entity_id: (
        dict(BOT) if (bot_id, entity_id) == ("bot-1", "ent-1") else None
    )
    bots.get_unique_by_id.side_effect = lambda bot_id: (
        dict(BOT) if bot_id == "bot-1" else None
    )
    collaborators = _Collaborators()
    app = mount_public_error_handlers(FastAPI())
    app.include_router(router, dependencies=[Depends(require_principal)])
    injector = Injector()
    injector.binder.bind(BotRepository, to=bots)
    injector.binder.bind(CollaboratorServiceProtocol, to=collaborators)
    injector.binder.bind(
        OpenApiBotAccessCheckConfig,
        to=OpenApiBotAccessCheckConfig(app_names=frozenset({ALLOWED_APP})),
    )
    attach_injector(app, injector)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, bots, collaborators


def _get(client, *, token: str | None = None, **params):
    query = {"entity_id": "ent-1", "user_id": "u-9", **params}
    query = {k: v for k, v in query.items() if v is not None}
    headers = {PRINCIPAL_HEADER: token if token is not None else mint(user_id=None)}
    return client.get(_PATH, params=query, headers=headers)


def test_allowlisted_app_gets_owner_level_for_the_owner(access_client):
    client, bots, _ = access_client

    response = _get(client, user_id=OWNER)

    assert response.status_code == 200
    assert response.json()["data"] == {
        "bot_id": "bot-1",
        "entity_id": "ent-1",
        "user_id": OWNER,
        "has_access": True,
        "level": "owner",
        "required_level": "member",
    }
    bots.get_by_id_and_entity.assert_called_once_with("bot-1", "ent-1")


def test_explicit_admin_editor_passes_admin_bar(access_client):
    client, _, collaborators = access_client
    collaborators.levels["u-9"] = PermissionLevel.ADMIN

    data = _get(client, required_level="admin").json()["data"]

    assert (data["has_access"], data["level"], data["required_level"]) == (
        True, "admin", "admin",
    )


def test_member_fails_admin_bar_but_reports_member_level(access_client):
    client, _, collaborators = access_client
    collaborators.levels["u-9"] = PermissionLevel.MEMBER

    data = _get(client, required_level="admin").json()["data"]

    assert (data["has_access"], data["level"]) == (False, "member")


def test_space_member_gets_member_access(access_client):
    client, _, collaborators = access_client
    collaborators.space_members.add("u-9")

    data = _get(client).json()["data"]

    assert (data["has_access"], data["level"]) == (True, "member")


def test_user_without_relation_or_revoked_editor_has_no_access(access_client):
    client, _, _ = access_client

    data = _get(client).json()["data"]

    assert (data["has_access"], data["level"]) == (False, "none")


def test_owner_only_bar_refuses_an_admin(access_client):
    client, _, collaborators = access_client
    collaborators.levels["u-9"] = PermissionLevel.ADMIN

    data = _get(client, required_level="owner").json()["data"]

    assert (data["has_access"], data["level"]) == (False, "admin")


def test_unknown_bot_is_200_without_access(access_client):
    client, _, collaborators = access_client

    response = _get(client, entity_id="ent-other")

    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["has_access"], data["level"]) == (False, "none")
    assert collaborators.calls == []


def test_user_id_is_required(access_client):
    client, bots, _ = access_client

    response = client.get(
        _PATH,
        params={"entity_id": "ent-1"},
        headers={PRINCIPAL_HEADER: mint(user_id=None)},
    )

    assert response.status_code == 422
    bots.get_by_id_and_entity.assert_not_called()


def test_without_entity_id_a_unique_bot_id_resolves(access_client):
    client, bots, _ = access_client

    response = _get(client, entity_id=None, user_id=OWNER)

    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["entity_id"], data["has_access"], data["level"]) == (
        None, True, "owner",
    )
    bots.get_unique_by_id.assert_called_once_with("bot-1")
    bots.get_by_id_and_entity.assert_not_called()


def test_without_entity_id_an_unknown_bot_is_200_without_access(access_client):
    client, bots, collaborators = access_client
    bots.get_unique_by_id.side_effect = lambda bot_id: None

    response = _get(client, entity_id=None)

    assert response.status_code == 200
    data = response.json()["data"]
    assert (data["has_access"], data["level"]) == (False, "none")
    assert collaborators.calls == []


def test_without_entity_id_an_ambiguous_bot_id_is_409(access_client):
    client, bots, collaborators = access_client
    bots.get_unique_by_id.side_effect = BotLookupAmbiguousError

    response = _get(client, entity_id=None)

    assert response.status_code == 409
    assert collaborators.calls == []


def test_an_empty_entity_id_is_422(access_client):
    client, bots, _ = access_client

    assert _get(client, entity_id="").status_code == 422
    bots.get_unique_by_id.assert_not_called()


def test_an_unknown_required_level_is_422(access_client):
    client, _, _ = access_client

    assert _get(client, required_level="none").status_code == 422


@pytest.mark.parametrize(
    "token",
    [
        # A person calling directly: no app principal at all.
        pytest.param(lambda: mint(tenant=None), id="human"),
        # An application that is not on the allowlist.
        pytest.param(lambda: _mint_app("stranger"), id="other-app"),
    ],
)
def test_callers_other_than_allowlisted_apps_get_403_before_any_read(
    access_client, token
):
    client, bots, collaborators = access_client

    response = _get(client, token=token(), user_id=OWNER)

    assert response.status_code == 403
    assert response.json()["message"] == "Forbidden"
    bots.get_by_id_and_entity.assert_not_called()
    assert collaborators.calls == []


def test_refusal_is_identical_for_existing_and_missing_bots(access_client):
    client, _, _ = access_client
    token = _mint_app("stranger")

    found = _get(client, token=token)
    missing = _get(client, token=token, entity_id="ent-other")

    assert found.status_code == missing.status_code == 403
    assert _strip_request_id(found.json()) == _strip_request_id(missing.json())


def test_collaborator_lookup_failure_is_500_not_false(access_client):
    client, _, collaborators = access_client
    collaborators.raise_on_lookup = True

    response = _get(client)

    assert response.status_code == 500
    assert "has_access" not in response.text


def test_route_is_absent_from_the_openapi_document():
    app = FastAPI()
    app.include_router(build_public_router())

    paths = app.openapi()["paths"]

    assert "/openapi/v1/bots/{bot_id}/access" not in paths
    served = {path_of(ctx) for ctx in effective_routes(build_public_router())}
    assert "/openapi/v1/bots/{bot_id}/access" in served


def _mint_app(app_name: str) -> str:
    """An app-only token whose app principal carries ``app_name``."""
    import jwt

    from tests.community.adapters.http.openapi_v1.test_principal_seam import KEY

    claims = jwt.decode(mint(user_id=None), KEY, algorithms=["HS256"], audience="backend")
    claims["principals"][0]["app"]["app_name"] = app_name
    return jwt.encode(claims, KEY, algorithm="HS256")


def _strip_request_id(body: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key != "request_id"}
