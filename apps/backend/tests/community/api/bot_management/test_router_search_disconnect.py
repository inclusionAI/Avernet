"""Search route behaviour around client-side request cancellation."""

import pytest
from fastapi.responses import Response
from fastapi.testclient import TestClient
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector
from starlette.requests import ClientDisconnect
from tests.community.api.bot_management._bot_router_test_support import (
    AsyncMock,
    MagicMock,
    patch,
)
from tests.community.api.bot_management._bot_router_test_support import (
    _bind_bot_service,
    _make_ctx,
    client,
    get_request_context,
    mock_bot_service,
    mock_passport,
)
from agentclaw.community.adapters.http.bot_management.router import search_bots
import agentclaw.community.adapters.http.bot_management.router as router_module


def _app(mock_bot_service):
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[get_request_context] = lambda: _make_ctx()
    attach_injector(
        app,
        Injector([_bind_bot_service(mock_bot_service)]),
    )
    return app


class TestSearchBotsDisconnectRoute:
    @pytest.mark.asyncio
    async def test_client_disconnect_is_not_search_error(self, mock_bot_service):
        request = MagicMock()
        request.json = AsyncMock(side_effect=ClientDisconnect)
        logger = MagicMock()

        with patch.object(router_module, "logger", logger):
            result = await search_bots(request, _make_ctx(), mock_bot_service)

        assert isinstance(result, Response)
        assert result.status_code == 499
        mock_bot_service.search_bots.assert_not_called()
        logger.error.assert_not_called()
        logger.info.assert_called_once_with(
            "[bot_router.search_bots] request cancelled by client, not search error"
        )

    @pytest.mark.asyncio
    async def test_asgi_disconnect_reaches_search_route_branch(self, mock_bot_service):
        """Exercise the mounted route through Starlette's http.disconnect receive path."""
        logger = MagicMock()
        messages = []
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/bots/search",
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", b"2"),
            ],
            "query_string": b"",
            "state": {},
            "http_version": "1.1",
            "scheme": "http",
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 1),
            "root_path": "",
            "raw_path": b"/api/bots/search",
            "asgi": "3.0",
        }

        async def send(message):
            messages.append(message)

        async def receive():
            return {"type": "http.disconnect"}

        app = _app(mock_bot_service)
        with patch.object(router_module, "logger", logger):
            await app(scope, receive, send)

        mock_bot_service.search_bots.assert_not_called()
        logger.error.assert_not_called()
        logger.info.assert_called_once_with(
            "[bot_router.search_bots] request cancelled by client, not search error"
        )
        assert messages[0]["type"] == "http.response.start"
        assert messages[0]["status"] == 499
        assert messages[-1]["type"] == "http.response.body"
        assert messages[-1]["body"] == b""


class TestSearchBotsNormalSearch:
    def test_search_success_path_is_unchanged(self, client):
        tc, svc, _ = client
        svc.search_bots.return_value = {"total": 1, "items": [{"bot_id": "bot-1"}]}

        resp = tc.post("/api/bots/search", json={})

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["data"] == {"total": 1, "items": [{"bot_id": "bot-1"}]}


class TestSearchBotsErrors:
    def test_real_search_error_is_500_envelope(self, client):
        """Existing service failure contract should not disappear."""
        tc, svc, _ = client
        svc.search_bots.side_effect = RuntimeError("db unavailable")

        resp = tc.post("/api/bots/search", json={})

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is False
        assert data["error_code"] == 500
        assert data["message"].startswith("搜索Bot失败")
