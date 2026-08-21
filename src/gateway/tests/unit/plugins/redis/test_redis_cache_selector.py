"""Selector-resolution tests for the gateway redis cache plugin.

Covers config-selected ``redis`` wiring the ``RedisCachePlugin`` as the active
cache, and the default remaining ``in_memory``. The ``redis`` option reads its
connection params from the sibling top-level ``cache_redis`` section (mirroring
BaaS, no secret-reference resolution).
"""

from __future__ import annotations

import pytest

from gateway.community.bootstrap import get_container
from gateway.community.bootstrap._configs import init_container_config
from gateway.community.plugins.cache.in_memory import InMemoryCachePlugin
from gateway.community.plugins.cache.redis import RedisCachePlugin


@pytest.fixture(autouse=True)
def _reset_container() -> None:
    yield
    import gateway.community.bootstrap as bootstrap_mod

    bootstrap_mod._container = None


def _init_redis():
    container = get_container()
    init_container_config(container)
    container.config.from_dict({"plugins": {"cache": "redis"}})
    return container


class TestRedisSelector:
    def test_redis_selected_wires_redis_plugin(self, monkeypatch) -> None:
        from unittest.mock import patch

        container = _init_redis()
        with patch(
            "gateway.community.plugins.cache.redis._plugin.Redis.from_url",
            new=lambda url, **kwargs: _StubClient(),
        ):
            cache = container.plugins().cache_plugin()
        assert isinstance(cache, RedisCachePlugin)

    def test_default_remains_in_memory(self) -> None:
        container = get_container()
        init_container_config(container)
        cache = container.plugins().cache_plugin()
        assert isinstance(cache, InMemoryCachePlugin)


class _StubClient:
    """Minimal redis client so the selector can resolve without a live server."""

    def ping(self) -> bool:
        return True

    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        pass

    def close(self) -> None:
        pass
