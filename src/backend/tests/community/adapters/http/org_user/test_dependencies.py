"""Unit tests for the ordinary HTTP signed-user access gate."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.openapi_v1.errors import MissingPrincipalError
from agentclaw.community.adapters.http.org_user import dependencies
from agentclaw.community.core.gateway_principal import VerifiedCaller


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", [None, MagicMock(has_user=False)])
async def test_refuses_missing_or_app_only_caller(monkeypatch, caller) -> None:
    monkeypatch.setattr(dependencies, "resolve_caller", lambda _connection: caller)

    with pytest.raises(MissingPrincipalError):
        await dependencies.require_gateway_user(_request())


@pytest.mark.asyncio
async def test_returns_verified_user_without_reading_tenant(monkeypatch) -> None:
    caller = MagicMock(spec=VerifiedCaller)
    caller.has_user = True
    monkeypatch.setattr(dependencies, "resolve_caller", lambda _connection: caller)

    assert await dependencies.require_gateway_user(_request()) is caller
    assert "tenant" not in caller.mock_calls
