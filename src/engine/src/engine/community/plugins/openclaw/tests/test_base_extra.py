from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.community.plugins.openclaw._base import OpenClawPortBase
from engine.community.plugins.openclaw.token_pool import TokenClientPool


@pytest.mark.asyncio
async def test_base_uses_injected_connected_client_for_default_client():
    client = MagicMock()
    client.connected = True
    pool = MagicMock(spec=TokenClientPool)
    base = OpenClawPortBase(client=client, pool=pool)

    assert base.pool is pool
    assert base._model_provider_map is None
    assert await base._default_client() is client


@pytest.mark.asyncio
async def test_base_falls_back_to_global_default_client_when_missing_or_disconnected():
    default = MagicMock(name="default")

    with patch("engine.community.plugins.openclaw._base.get_client", new=AsyncMock(return_value=default)) as get_client:
        assert await OpenClawPortBase(client=None)._default_client() is default
        get_client.assert_awaited_once()

    injected = MagicMock()
    injected.connected = False
    with patch("engine.community.plugins.openclaw._base.get_client", new=AsyncMock(return_value=default)) as get_client:
        assert await OpenClawPortBase(client=injected)._default_client() is default
        get_client.assert_awaited_once()


@pytest.mark.asyncio
async def test_base_pooled_client_delegates_to_pool():
    expected = MagicMock(name="pooled")
    pool = MagicMock(spec=TokenClientPool)
    pool.get = AsyncMock(return_value=expected)
    base = OpenClawPortBase(pool=pool)

    assert await base._pooled_client("tok") is expected
    pool.get.assert_awaited_once_with("tok")
