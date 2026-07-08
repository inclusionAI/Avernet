"""Rule 25 conformance — TokenExchangePlugin.

Consumer under test: ``POST /api/v1/token/exchange``
(api/token_exchange/router.py:24). The endpoint resolves the plugin
via DI and forwards the request. The local impl
(``LocalTokenExchangePlugin``) returns a fixed mock token.

Plugin-hit assertion: the endpoint's JSON response must contain the
exact ``"mock_access_token"`` string produced only by the local
impl's ``exchange_from_request``. A consumer bypassing the plugin
could not produce that token.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from agentclaw.community.plugin_api.token_exchange import TokenExchangePlugin


def test_token_exchange_returns_local_mock_token(app_with_testing_modules) -> None:
    client = TestClient(app_with_testing_modules)
    resp = client.post("/api/v1/token/exchange")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"access_token": "mock_access_token"}


# ── community impl (B4) — passthrough, no internal exchange ──


@pytest.mark.asyncio
async def test_community_token_exchange_passes_inbound_token(
    community_world,
) -> None:
    plugin = community_world.get(TokenExchangePlugin)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"authorization", b"Bearer caller-tok")],
        }
    )
    assert await plugin.exchange_from_request(request) == {
        "access_token": "caller-tok"
    }
