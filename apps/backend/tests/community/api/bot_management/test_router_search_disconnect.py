"""Focused route tests for client disconnect on Bot search."""

from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from agentclaw.community.adapters.http.bot_management.router import (
    search_bots as search_bots_route,
)
from agentclaw.community.adapters.http.dependencies import (
    RequestContext,
)
from agentclaw.community.api.bot_service import BotServiceProtocol


def _request(scope, receive):
    return Request(scope, receive)


@pytest.mark.asyncio
async def test_search_bots_client_disconnect_returns_499():
    """Starlette disconnect on body parse returns 499 and skips the service."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/bots/search",
        "query_string": b"",
    }

    async def receive():
        return {"type": "http.disconnect"}

    req = _request(scope, receive)
    ctx = RequestContext(user_id="caller")
    svc = MagicMock(spec=BotServiceProtocol)

    response = await search_bots_route(
        request=req, ctx=ctx, bot_service=svc
    )
    assert response.status_code == 499
    assert response.body == b""
    svc.search_bots.assert_not_called()


@pytest.mark.asyncio
async def test_search_bots_success_path_returns_search_results():
    """Normal JSON body keeps the existing successful response behavior."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/bots/search",
        "query_string": b"",
    }
    body = b'{"key":"alpha","page":2,"page_size":10}'

    async def receive():
        return {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }

    req = _request(scope, receive)
    ctx = RequestContext(user_id="caller", nick_name="Caller")
    svc = MagicMock(spec=BotServiceProtocol)
    svc.search_bots.return_value = {"total": 1, "items": [{"bot_id": "b1"}]}

    response = await search_bots_route(
        request=req, ctx=ctx, bot_service=svc
    )
    assert response.data == {"total": 1, "items": [{"bot_id": "b1"}]}, response.data
    assert response.success is True
    svc.search_bots.assert_called_once()
    assert svc.search_bots.call_args.kwargs["key"] == "alpha"
    assert svc.search_bots.call_args.kwargs["page"] == 2
    assert svc.search_bots.call_args.kwargs["page_size"] == 10


@pytest.mark.asyncio
async def test_search_bots_service_error_keeps_existing_500_readout():
    """A non-disconnect service failure still returns the existing 500 readout."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/bots/search",
        "query_string": b"",
    }
    body = b'{"key":"alpha"}'

    async def receive():
        return {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }

    req = _request(scope, receive)
    ctx = RequestContext(user_id="caller")
    svc = MagicMock(spec=BotServiceProtocol)
    svc.search_bots.side_effect = RuntimeError("db down")

    response = await search_bots_route(
        request=req, ctx=ctx, bot_service=svc
    )
    assert response.error_code == 500
    assert response.success is False
    assert response.message == "搜索Bot失败: db down"
