"""Selector-resolution tests for the gateway redis cache plugin.

Covers task 2.6: config-selected ``redis`` wires the ``RedisCachePlugin`` as
the active cache, secret references in the connection block are resolved via
the active ``SecretResolver`` before client construction, unresolvable
references fail fast, and the default remains ``in_memory``.
"""

from __future__ import annotations

import os

import pytest

from gateway.community.bootstrap import get_container
from gateway.community.bootstrap._configs import init_container_config
from gateway.community.plugins.cache.in_memory import InMemoryCachePlugin
from gateway.community.plugins.cache.redis import RedisCachePlugin


@pytest.fixture(autouse=True)
def _reset_container() -> None:
    yield
    # Ensure a clean singleton container for each test.
    import gateway.community.bootstrap as bootstrap_mod

    bootstrap_mod._container = None


def _init_redis(**overrides: object):
    from dependency_injector import containers, providers

    container = get_container()
    init_container_config(container)
    config = {"plugins": {"cache": "redis", "cache_redis": {"host": "127.0.0.1"}}}
    for key, value in overrides.items():
        config["plugins"][key] = value
    container.config.from_dict(config)
    return container


def test_redis_selected_wires_redis_plugin() -> None:
    container = _init_redis()
    cache = container.plugins().cache_plugin()
    assert isinstance(cache, RedisCachePlugin)


def test_default_remains_in_memory() -> None:
    container = get_container()
    init_container_config(container)
    cache = container.plugins().cache_plugin()
    assert isinstance(cache, InMemoryCachePlugin)


def test_secret_reference_password_resolved(monkeypatch) -> None:
    monkeypatch.setenv("AVERNET_SECRET_REDISPW_VALUE", "super-secret")
    container = _init_redis(
        cache_redis={"host": "h", "password": "@redispw"},
    )
    cache = container.plugins().cache_plugin()
    assert isinstance(cache, RedisCachePlugin)
    assert cache._config.password == "super-secret"


def test_unresolvable_secret_reference_fails_fast() -> None:
    container = _init_redis(
        cache_redis={"host": "h", "password": "@missing_secret"},
    )
    import pytest as _pytest

    with _pytest.raises(ValueError, match="Unresolvable secret reference"):
        container.plugins().cache_plugin()
