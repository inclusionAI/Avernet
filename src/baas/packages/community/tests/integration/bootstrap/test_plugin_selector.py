"""Integration tests for plugin selector mechanism.

Verifies the Selector-based plugin resolution correctly dispatches
to stub implementations in the it-zdas overlay and that each
resolved plugin is functional.
"""

from __future__ import annotations

import pytest

from secbaas.bootstrap import ApplicationContainer
from secbaas.plugins.auth.stub import StubAuthPlugin
from secbaas.plugins.cache.stub import StubCachePlugin
from secbaas.plugins.sandbox.arca import StubArcaSandboxPlugin
from secbaas.plugins.sandbox.desktop import StubDesktopSandboxPlugin
from secbaas.plugins.sandbox.k8s import StubK8sSandboxPlugin
from secbaas.plugins.sandbox.teclaw import StubTeClawBotPlugin
from secbaas.plugins.secret.stub import StubSecretStorePlugin


class TestPluginSelector:
    """Verify plugin Selector wiring resolves stub implementations."""

    @pytest.mark.integration
    def test_all_stub_plugins_loaded(
        self, bootstrap_init: ApplicationContainer
    ) -> None:
        """Every Selector branch resolves to its stub class in it-zdas mode."""
        plugins = bootstrap_init.plugins

        # Singleton providers → instances
        cache = plugins.cache_plugin()
        assert isinstance(cache, StubCachePlugin)

        secret = plugins.secret_plugin()
        assert isinstance(secret, StubSecretStorePlugin)

        auth = plugins.auth_plugin()
        assert isinstance(auth, StubAuthPlugin)

        desktop = plugins.desktop_sandbox_plugin()
        assert isinstance(desktop, StubDesktopSandboxPlugin)

        # Object providers → classes (not instances)
        arca = plugins.arca_sandbox_plugin_factory()
        assert arca is StubArcaSandboxPlugin

        teclaw = plugins.teclaw_bot_plugin_factory()
        assert teclaw is StubTeClawBotPlugin

        k8s = plugins.k8s_sandbox_plugin_factory()
        assert k8s is StubK8sSandboxPlugin

    @pytest.mark.integration
    def test_plugin_metadata_present(
        self, bootstrap_init: ApplicationContainer
    ) -> None:
        """Every resolved plugin has non-None class name and module."""
        plugins = bootstrap_init.plugins
        plugin_names = [
            "auth_plugin",
            "cache_plugin",
            "secret_plugin",
            "arca_sandbox_plugin_factory",
            "desktop_sandbox_plugin",
            "teclaw_bot_plugin_factory",
            "k8s_sandbox_plugin_factory",
        ]

        for name in plugin_names:
            provider = getattr(plugins, name)
            instance = provider()
            cls = instance.__class__
            assert cls.__name__ is not None, f"{name}: class name is None"
            assert cls.__name__ != "", f"{name}: class name is empty"
            assert cls.__module__ is not None, f"{name}: module is None"
            assert cls.__module__ != "", f"{name}: module is empty"

    @pytest.mark.integration
    def test_stub_plugins_are_functional(
        self, bootstrap_init: ApplicationContainer
    ) -> None:
        """Spot-check: stub plugins behave correctly at runtime."""
        plugins = bootstrap_init.plugins

        # Secret plugin: set and retrieve a secret
        secret = plugins.secret_plugin()
        secret.set_secret("test-key", "test-value")
        assert secret.get_secret("test-key") == "test-value"

        # Secret plugin: resolve_secret with @ prefix
        secret.set_secret("resolved-key", "resolved-value")
        assert secret.resolve_secret("@resolved-key") == "resolved-value"

        # Auth plugin: always-allowed permissions
        auth = plugins.auth_plugin()
        assert auth.is_allowed(auth._default_user) is True
        assert auth.check_permission("any-user", "any-permission") is True

        # Cache plugin: set and get a value (non-expired)
        cache = plugins.cache_plugin()
        cache.set("cache-key", "cache-value", ttl_seconds=300)
        assert cache.get("cache-key") == "cache-value"
