"""Public MCP override operations with synthetic App delegation."""

from secrets import token_hex
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import jwt
import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.openapi_v1 import dependencies
from agentclaw.community.adapters.http.openapi_v1.mcp.router import bot_mcp_router
from agentclaw.community.api.mcp_scoped_config_service import (
    MCPScopedConfigServiceProtocol,
)
from agentclaw.community.core.bot_app_grant.bot_app_grant_service_protocol import (
    BotAppGrantServiceProtocol,
    UserAppGrantServiceProtocol,
)
from agentclaw.community.core.gateway_principal import PrincipalVerifierConfig
from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.mcp.scoped_config_flow import MCPScopedConfigService
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.adapters.http.openapi_v1.bot_access import (
    CollaboratorLockServiceProtocol,
)
from tests.community.adapters.http.openapi_v1.conftest import (
    mount_public_error_handlers,
    user_scoped_client,
)

PATH = "/openapi/v1/bots/shared/mcps/weather/config?entity_id=entity-1"


class Collaborators:
    level = PermissionLevel.ADMIN

    def get_explicit_permission_level(self, **_):
        return self.level

    get_operable_permission_level = get_explicit_permission_level


@pytest.fixture
def mcp(monkeypatch):
    bots = Mock()
    row = {"id": 42, "bot_id": "shared", "entity_id": "entity-1", "owner_id": "owner"}
    bots.list_matches_by_legacy_locator.side_effect = lambda bot_id, locator: (
        [row] if (bot_id, locator) == ("shared", "entity-1") else []
    )
    grants = Mock()
    grants.find.side_effect = lambda **kw: (
        object()
        if kw
        == {
            "bot_id": "shared",
            "owner_id": "entity-1",
            "user_id": "delegate",
            "app_id": 5,
        }
        else None
    )
    collaborators = Collaborators()
    locks = Mock()
    locks.get_lock_info.return_value = SimpleNamespace(
        has_collaborators=True, lock=SimpleNamespace(holder_user_id="delegate")
    )
    config_service = Mock()
    config_service.validate_scoped_headers.return_value = {"valid": True}
    overrides = Mock()
    overrides.get_by_bot_and_server_code.return_value = {}
    commands = Mock()
    commands.patch_bot.return_value = ({}, True)
    market = Mock()
    market.get_mcp_detail.return_value = None
    sync = Mock()
    sync.sync_mcp_details_for_bot = AsyncMock(return_value={"success": True})
    effective = Mock()
    effective.effective_mcp_server_codes.return_value = set()
    service = MCPScopedConfigService(
        config_service, overrides, bots, commands, market, sync, effective
    )

    app = mount_public_error_handlers(FastAPI())
    app.include_router(bot_mcp_router)
    signing_key = token_hex(32)
    verifier = PrincipalVerifierConfig(
        signing_key, audience="backend", issuer="gateway"
    )
    monkeypatch.setattr(dependencies, "get_principal_verifier_config", lambda: verifier)
    now = int(time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 300,
            "principals": [
                {
                    "type": "app",
                    "tenant": "teamclaw",
                    "app": {
                        "app_id": 5,
                        "app_name": "test-app",
                        "owners": "test",
                        "tenant": "teamclaw",
                    },
                }
            ],
        },
        signing_key,
        algorithm="HS256",
    )

    def bind(binder):
        binder.bind(BotAppGrantServiceProtocol, to=grants)
        binder.bind(UserAppGrantServiceProtocol, to=Mock())
        binder.bind(BotRepository, to=bots)
        binder.bind(CollaboratorServiceProtocol, to=collaborators)
        binder.bind(CollaboratorLockServiceProtocol, to=locks)
        binder.bind(MCPScopedConfigServiceProtocol, to=service)

    attach_injector(app, Injector([bind]))
    return SimpleNamespace(
        client=user_scoped_client(
            app, "delegate", headers={dependencies.PRINCIPAL_HEADER: token}
        ),
        commands=commands,
        effective=effective,
        market=market,
        grants=grants,
        overrides=overrides,
        sync=sync,
        collaborators=collaborators,
        locks=locks,
        bots=bots,
    )


def test_member_reads_only_redacted_bot_overrides(mcp):
    mcp.collaborators.level = PermissionLevel.MEMBER
    mcp.overrides.get_by_bot_and_server_code.return_value = {
        "headers": {"Authorization": "synthetic", "X-Region": "sample"},
        "url": "https://example.invalid/path?synthetic=secret",
        "api_key": "unused",
    }
    response = mcp.client.get(PATH)
    assert response.status_code == 200, response.json()
    assert response.json()["data"] == {
        "server_code": "weather",
        "headers": {"Authorization": "****", "X-Region": "****"},
        "url": "****",
    }
    mcp.overrides.get_by_bot_and_server_code.assert_called_once_with(
        bot_id="shared", owner_id="entity-1", server_code="weather"
    )


def test_app_admin_can_prestore_and_retry_projection(mcp):
    url = "https://example.invalid/custom"
    response = mcp.client.put(PATH, json={"url": url})
    assert response.status_code == 200, response.json()
    assert response.json()["data"]["runtime_projection"]["status"] == "SKIPPED"
    mcp.commands.patch_bot.assert_called_once_with(
        bot_pk=42,
        bot_id="shared",
        entity_id="entity-1",
        current_owner_id="owner",
        server_code="weather",
        patch={"url": url},
    )
    mcp.sync.sync_mcp_details_for_bot.assert_not_awaited()

    mcp.effective.effective_mcp_server_codes.return_value = {"weather", "other"}
    mcp.commands.patch_bot.return_value = ({"url": url}, False)
    mcp.sync.sync_mcp_details_for_bot.side_effect = RuntimeError(url)
    response = mcp.client.put(PATH, json={})
    assert response.status_code == 200, response.json()
    data = response.json()["data"]
    assert data["url"] == "****"
    assert data["desired_state"]["status"] == "UNCHANGED"
    assert data["runtime_projection"]["status"] == "DEGRADED"
    assert url not in response.text
    assert mcp.commands.patch_bot.call_args.kwargs["patch"] == {}
    delivery = dict(mcp.sync.sync_mcp_details_for_bot.call_args.kwargs)
    assert delivery.pop("config_target").bot_pk == 42
    assert delivery == {
        "user_id": "owner",
        "entity_id": "entity-1",
        "bot_id": "shared",
        "engine_type": None,
        "mcp_entries": [{"serverCode": "weather"}],
    }
    mcp.overrides.get_by_bot_and_server_code.return_value = {"url": url}
    assert mcp.client.get(PATH).json()["data"]["url"] == "****"
    mcp.sync.sync_mcp_details_for_bot.side_effect = None
    response = mcp.client.put(PATH, json={"url": None, "headers": {"X-Region": None}})
    assert response.json()["data"]["runtime_projection"]["status"] == "CONVERGED"
    assert mcp.commands.patch_bot.call_args.kwargs["patch"] == {
        "url": None,
        "headers": {"X-Region": None},
    }


@pytest.mark.parametrize("denial", ["grant", "owner", "ambiguous", "member", "lock"])
def test_denials_cannot_mutate_or_project(mcp, denial):
    path = PATH
    if denial == "grant":
        mcp.grants.find.side_effect = None
        mcp.grants.find.return_value = None
    elif denial == "owner":
        path = PATH.replace("entity-1", "other")
    elif denial == "ambiguous":
        mcp.bots.list_matches_by_legacy_locator.side_effect = None
        mcp.bots.list_matches_by_legacy_locator.return_value = [{}, {}]
    elif denial == "member":
        mcp.collaborators.level = PermissionLevel.MEMBER
    else:
        mcp.locks.get_lock_info.return_value.lock = None
    response = mcp.client.put(path, json={"headers": {"X-Region": "sample"}})
    assert response.status_code == (423 if denial == "lock" else 404), response.json()
    mcp.commands.patch_bot.assert_not_called()
    mcp.sync.sync_mcp_details_for_bot.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"headers": None},
        {"bots": []},
        {"url": ""},
        {"url": "file:///tmp/mcp"},
        {"headers": {"X-Region": "first", "x-region": "second"}},
    ],
)
def test_invalid_patch_never_reaches_storage(mcp, payload):
    response = mcp.client.put(PATH, json=payload)
    assert response.status_code in {400, 422}, response.json()
    mcp.commands.patch_bot.assert_not_called()


def test_clear_is_saved_even_when_catalog_lookup_for_projection_fails(mcp):
    mcp.effective.effective_mcp_server_codes.return_value = {"weather"}
    mcp.market.get_mcp_detail.side_effect = RuntimeError("synthetic upstream outage")
    response = mcp.client.put(PATH, json={"url": None})
    assert response.status_code == 200, response.json()
    mcp.commands.patch_bot.assert_called_once()
    assert response.json()["data"]["desired_state"]["status"] == "COMMITTED"
    assert response.json()["data"]["runtime_projection"]["status"] == "DEGRADED"
    mcp.sync.sync_mcp_details_for_bot.assert_not_called()
