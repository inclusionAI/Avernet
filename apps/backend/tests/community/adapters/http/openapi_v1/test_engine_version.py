"""Signed principal -> Bot authorization -> real release service -> transport."""

from functools import partial
from agentclaw.community.adapters.engine_release_probe import probe_engine_release
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, InstanceProvider

from agentclaw.community.adapters.http.openapi_v1.bots.engine_version import router
from agentclaw.community.adapters.http.openapi_v1.dependencies import (
    PRINCIPAL_HEADER,
    require_principal,
)
from agentclaw.community.api.engine_version_service import EngineVersionServiceProtocol
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_management.engine_version.models import (
    EngineVersionConfig,
)
from agentclaw.community.core.bot_management.engine_version.service import (
    EngineVersionService,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
)
from tests.community.adapters.http.openapi_v1.test_principal_seam import (
    mint,
    signing_key as signing_key,
)
from tests.community.core.engine_version.test_engine_version_service import (
    dependencies as dependencies,
)


class Collaborators:
    level = PermissionLevel.NONE
    visible_level = PermissionLevel.NONE

    def get_permission_level(self, *args):
        return self.visible_level

    def get_operable_permission_level(self, **kwargs):
        return max(self.level, self.visible_level)

    def get_explicit_permission_level(self, *, bot, user_id, env=None):
        return self.level


_PATH = "/openapi/v1/bots/bot-1/engine-version"


@pytest.fixture
def version_client(dependencies):
    bots, bindings, resolver, transport = dependencies
    collaborators = Collaborators()
    service = EngineVersionService(
        lifecycle=MagicMock(),
        bots=bots,
        bindings=bindings,
        resolver=resolver,
        probe=partial(probe_engine_release, transport),
        collaborators=collaborators,
        list_devices=MagicMock(),
        config=EngineVersionConfig("REL20261014"),
    )
    injector = Injector()
    for contract, instance in [
        (EngineVersionServiceProtocol, service),
        (BotRepository, bots),
        (CollaboratorServiceProtocol, collaborators),
    ]:
        injector.binder.bind(contract, to=InstanceProvider(instance))
    app = mount_public_error_handlers(FastAPI())
    app.include_router(router, dependencies=[Depends(require_principal)])
    attach_injector(app, injector)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, transport, collaborators


def get(client, caller="owner", **params):
    return client.get(
        _PATH,
        params={"entity_id": "owner", "user_id": caller, **params},
        headers={PRINCIPAL_HEADER: mint(user_id=caller, tenant=None)},
    )


def test_http_owner_receives_release_facts_without_device_secrets(
    version_client, signing_key
):
    client, transport, _ = version_client
    response = get(client)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["comparison"] == "older"
    assert data["release_target"]["release_id"] == "REL20261014"
    assert data["context"]["stage"] == "draft"
    assert "private-target" not in response.text
    transport.invoke.assert_awaited_once()


def test_http_reading_member_is_admitted_without_restart_permission(
    version_client, signing_key
):
    client, transport, collaborators = version_client
    collaborators.level = PermissionLevel.MEMBER
    response = get(client, caller="member")
    assert response.status_code == 200, response.text
    transport.invoke.assert_awaited_once()


@pytest.mark.parametrize("stage", ["verify", "online"])
def test_http_published_stage_refuses_before_probe(version_client, signing_key, stage):
    client, transport, _ = version_client
    response = get(client, stage=stage)
    assert response.status_code == 409, response.text
    transport.invoke.assert_not_called()


def test_http_stranger_cannot_probe_owner_runtime(version_client, signing_key):
    client, transport, _ = version_client
    response = get(client, caller="stranger")
    assert response.status_code == 404, response.text
    transport.invoke.assert_not_called()


def test_http_anonymous_cannot_probe(version_client, signing_key):
    client, transport, _ = version_client
    assert client.get(_PATH).status_code == 401
    transport.invoke.assert_not_called()


@pytest.mark.parametrize("engine", ["teclaw", "aicoding", "claude_code"])
def test_http_other_engines_are_not_applicable_without_probe(
    version_client, dependencies, signing_key, engine
):
    bots, bindings, resolver, transport = dependencies
    bots.get_by_id_and_owner.return_value["active_engine"] = engine
    client, _, _ = version_client
    response = get(client)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["detection"] == {
        "status": "not_applicable",
        "reason": "engine_not_supported",
    }
    assert data["restart_hint"] == "none"
    assert data["comparison"] == "unknown"
    assert "current" not in data
    assert "restart_reason" not in data
    bindings.get_active_by_bot_and_owner.assert_not_called()
    transport.invoke.assert_not_called()


@pytest.mark.parametrize(
    "caller,stage,status", [("stranger", "draft", 404), ("owner", "online", 409)]
)
def test_http_teclaw_still_requires_authorization_and_draft_scope(
    version_client, dependencies, signing_key, caller, stage, status
):
    bots, bindings, resolver, transport = dependencies
    bots.get_by_id_and_owner.return_value["active_engine"] = "teclaw"
    client, _, _ = version_client
    response = get(client, caller=caller, stage=stage)
    assert response.status_code == status, response.text
    bindings.get_active_by_bot_and_owner.assert_not_called()
    transport.invoke.assert_not_called()


def test_visible_space_member_without_explicit_restart_gets_normal_advisory(version_client, signing_key):
    client, transport, collaborators = version_client
    collaborators.visible_level = PermissionLevel.MEMBER
    response = get(client, caller="space-member")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["detection"] == {"status": "not_applicable", "reason": "restart_not_permitted"}
    assert data["restart_hint"] == "restricted"
    assert data["comparison"] == "unknown"
    assert "current" not in data
    assert "instance_id" not in data["context"]
    transport.invoke.assert_not_called()


def test_missing_bot_is_still_404(version_client, dependencies, signing_key):
    dependencies[0].get_by_id_and_owner.return_value = None
    response = get(version_client[0])
    assert response.status_code == 404
    version_client[1].invoke.assert_not_called()
