"""Unit tests for the community PassthroughTokenExchangePlugin (B4 T8)."""
from __future__ import annotations

import pytest
from starlette.requests import Request

from agentclaw.community.plugins.community.token_exchange import (
    PassthroughTokenExchangePlugin,
)


def _request(headers: dict | None = None, cookies: dict | None = None) -> Request:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        raw_headers.append((b"cookie", cookie_str.encode()))
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw_headers})


@pytest.mark.asyncio
async def test_passes_through_bearer_header():
    plugin = PassthroughTokenExchangePlugin()
    result = await plugin.exchange_from_request(
        _request(headers={"authorization": "Bearer tok-abc"})
    )
    assert result == {"access_token": "tok-abc"}


@pytest.mark.asyncio
async def test_falls_back_to_cookie():
    plugin = PassthroughTokenExchangePlugin()
    result = await plugin.exchange_from_request(
        _request(cookies={"access_token": "cookie-tok"})
    )
    assert result == {"access_token": "cookie-tok"}


@pytest.mark.asyncio
async def test_tolerates_absent_token():
    plugin = PassthroughTokenExchangePlugin()
    result = await plugin.exchange_from_request(_request())
    assert result == {"access_token": ""}


def test_not_a_mock_seam():
    from agentclaw.community.plugins.local._mock_seam import MockSeam

    assert not issubclass(PassthroughTokenExchangePlugin, MockSeam)
