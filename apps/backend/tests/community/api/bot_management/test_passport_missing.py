"""Passport lookup keeps missing Bot distinct from downstream failures."""

from types import SimpleNamespace

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
)
from agentclaw.community.core.bot_management.addressing import BotAddressResolver
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.plugin_api.passport import PassportError
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.bot_collaborator.interceptor.collaborator import (
    CollaboratorPermissionInterceptor,
)
from tests.community.api.bot_management._bot_router_test_support import (
    client as client,
    mock_bot_service as mock_bot_service,
    mock_passport as mock_passport,
)


@pytest.mark.parametrize("query", ["", "?owner_id=test_user"])
def test_missing_bot_returns_404_without_passport_lookup(client, query, caplog):
    tc, service, passport = client
    repo = tc.app.state.injector.get(BotRepository)
    repo.list_matches_by_legacy_locator.side_effect = None
    repo.list_matches_by_legacy_locator.return_value = []
    repo.get_by_id.return_value = None
    service.get_bot.side_effect = BotNotFoundError("missing")
    response = tc.get("/api/bots/missing/passport" + query)
    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "data": None,
        "message": "Bot not found",
        "error_code": 404,
    }
    passport.query_agent_passport.assert_not_called()
    assert not any(
        r.levelname == "ERROR" and "get_bot_passport" in r.message
        for r in caplog.records
    )


def test_unauthorized_owner_remains_forbidden(client, monkeypatch):
    tc, service, passport = client

    def missing(*_args, **_kwargs):
        raise BotAddressNotFoundError("missing")

    monkeypatch.setattr(BotAddressResolver, "resolve", missing)
    monkeypatch.setattr(
        CollaboratorPermissionInterceptor, "_get_lock_service", lambda *_: None
    )
    response = tc.get("/api/bots/missing/passport?owner_id=other")
    assert response.status_code == 403
    service.get_bot.assert_not_called()
    passport.query_agent_passport.assert_not_called()


def test_authorized_collaborator_keeps_owner_lookup(client, monkeypatch):
    tc, service, passport = client

    def missing(*_args, **_kwargs):
        raise BotAddressNotFoundError("missing")

    monkeypatch.setattr(BotAddressResolver, "resolve", missing)
    monkeypatch.setattr(
        CollaboratorPermissionInterceptor,
        "_get_lock_service",
        lambda *_: SimpleNamespace(
            get_lock_info=lambda **_: SimpleNamespace(has_collaborators=True)
        ),
    )
    monkeypatch.setattr(
        CollaboratorPermissionInterceptor,
        "_get_collaborator_service",
        lambda *_: SimpleNamespace(
            check_collaborator_permission=lambda **_: {
                "has_permission": True,
                "level": "ADMIN",
            }
        ),
    )
    service.get_bot.side_effect = BotNotFoundError("missing")
    response = tc.get("/api/bots/missing/passport?owner_id=other")
    assert response.status_code == 200
    assert response.json()["error_code"] == 404
    service.get_bot.assert_called_once_with("missing", "other")
    passport.query_agent_passport.assert_not_called()


@pytest.mark.parametrize(
    "error", [BotNotFoundError("downstream"), RuntimeError("downstream")]
)
def test_downstream_exception_stays_server_error(client, caplog, error):
    tc, service, passport = client
    service.get_bot.return_value = {"bot_id": "b1"}
    passport.query_agent_passport.side_effect = error
    response = tc.get("/api/bots/b1/passport?owner_id=test_user")
    assert response.status_code == 200
    assert response.json()["error_code"] == 500
    assert any(
        r.levelname == "ERROR" and "get_bot_passport" in r.message
        for r in caplog.records
    )


def test_existing_bot_passport_success_and_missing(client):
    tc, service, passport = client
    service.get_bot.return_value = {"bot_id": "b1"}
    passport.query_agent_passport.return_value = {"agent_code": "a1"}
    assert tc.get("/api/bots/b1/passport?owner_id=test_user").json()["success"] is True
    passport.query_agent_passport.return_value = None
    result = tc.get("/api/bots/b1/passport?owner_id=test_user").json()
    assert result["error_code"] == 404
    assert result["message"] == "Passport not found"


def test_passport_provider_error_keeps_existing_code(client):
    tc, service, passport = client
    service.get_bot.return_value = {"bot_id": "b1"}
    passport.query_agent_passport.side_effect = PassportError("unavailable")
    response = tc.get("/api/bots/b1/passport?owner_id=test_user")
    assert response.status_code == 200
    assert response.json()["error_code"] == 5400
