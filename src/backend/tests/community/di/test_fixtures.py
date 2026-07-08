"""Smoke tests for the shared DI test fixtures (Task 7).

Verifies:
- ``test_injector`` resolves the expected local/test bindings.
- A custom ``Module`` can override a binding on top of the fixture.
- The overridden provider is never executed when an override is in place.
- The ``client`` fixture wires the app's injector to the same instance.
"""
from __future__ import annotations

from typing import cast
from unittest.mock import patch

from injector import Module, provider, singleton

from agentclaw.community.plugin_api.auth import AuthPlugin
from agentclaw.community.plugin_api.cache import CachePlugin
from agentclaw.community.plugin_api.database import DatabasePlugin


class _SentinelAuth:
    """Minimal AuthPlugin-shaped stub used by the override tests."""

    async def authenticate(self, *_a, **_k):
        return "sentinel"


class _OverrideAuthModule(Module):
    @singleton
    @provider
    def auth(self) -> AuthPlugin:
        return cast(AuthPlugin, _SentinelAuth())


def test_test_injector_resolves_local_auth(test_injector):
    from agentclaw.community.plugins.local.auth import LocalAuth

    assert isinstance(test_injector.get(AuthPlugin), LocalAuth)


def test_test_injector_resolves_memory_cache(test_injector):
    from agentclaw.community.plugins.local.cache import MemoryCachePlugin

    assert isinstance(test_injector.get(CachePlugin), MemoryCachePlugin)


def test_test_injector_resolves_sqlite_database(test_injector):
    from agentclaw.community.plugins.local.database import SqliteDB

    assert isinstance(test_injector.get(DatabasePlugin), SqliteDB)


def test_per_test_override_module_wins(test_injector):
    """Demonstrates the override pattern per-module migrations will use."""
    test_injector.binder.install(_OverrideAuthModule())
    assert isinstance(test_injector.get(AuthPlugin), _SentinelAuth)


def test_overridden_provider_never_runs(test_injector):
    """The original ``LocalAuth`` provider must not execute when overridden.

    ``injector`` evaluates ``@singleton`` providers lazily — they only
    fire on first ``.get()``. Installing the override *before* any
    resolution should mean the original implementation is never
    constructed. Asserting this protects per-module migrations from
    silently double-instantiating both the default and override impls.
    """
    from agentclaw.community.plugins.local.auth import LocalAuth

    real_init = LocalAuth.__init__
    calls: list[tuple] = []

    def tracking_init(self, *args, **kwargs):
        calls.append((args, kwargs))
        real_init(self, *args, **kwargs)

    with patch.object(LocalAuth, "__init__", tracking_init):
        test_injector.binder.install(_OverrideAuthModule())
        resolved = test_injector.get(AuthPlugin)

    assert isinstance(resolved, _SentinelAuth)
    assert calls == [], "LocalAuth was instantiated despite override"


def test_client_attaches_test_injector(client, test_injector):
    from agentclaw.community.adapters.http.app import app

    assert app.state.injector is test_injector
    response = client.get("/api/health")
    assert response.status_code == 200
