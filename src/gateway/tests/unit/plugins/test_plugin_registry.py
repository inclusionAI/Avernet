"""Unit tests for plugin registry — register and query enterprise plugin options."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import gateway.community.plugin_registry as registry_mod
from gateway.community.plugin_registry import (
    has_enterprise_plugins,
    register_plugin_option,
    register_plugin_option_provider,
)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Clear the global registry before and after each test."""
    registry_mod._extra_options.clear()
    registry_mod._plugin_option_providers.clear()
    registry_mod._authn_strategy_providers.clear()
    yield
    registry_mod._extra_options.clear()
    registry_mod._plugin_option_providers.clear()
    registry_mod._authn_strategy_providers.clear()


class TestRegisterPluginOption:
    def test_initially_no_enterprise_plugins(self) -> None:
        assert has_enterprise_plugins() is False

    def test_register_single_option(self) -> None:
        def factory() -> str:
            return "sofa-plugin"

        register_plugin_option("gateway.cache", "sofa", factory)
        assert has_enterprise_plugins() is True
        stored_factory, _, _ = registry_mod._extra_options["gateway.cache"]["sofa"]
        assert stored_factory is factory

    def test_register_multiple_plugins(self) -> None:
        register_plugin_option("gateway.cache", "sofa", lambda: "cache-sofa")
        register_plugin_option("gateway.logger", "sofa", lambda: "logger-sofa")
        assert has_enterprise_plugins() is True
        assert "gateway.cache" in registry_mod._extra_options
        assert "gateway.logger" in registry_mod._extra_options

    def test_register_multiple_options_same_plugin(self) -> None:
        register_plugin_option("gateway.tracer", "sofa", lambda: "tracer-sofa")
        register_plugin_option("gateway.tracer", "custom", lambda: "tracer-custom")
        assert len(registry_mod._extra_options["gateway.tracer"]) == 2
        assert "sofa" in registry_mod._extra_options["gateway.tracer"]
        assert "custom" in registry_mod._extra_options["gateway.tracer"]

    def test_overwrite_existing_option(self) -> None:
        factory1: Callable[[], Any] = lambda: "v1"
        factory2: Callable[[], Any] = lambda: "v2"
        register_plugin_option("gateway.cache", "sofa", factory1)
        register_plugin_option("gateway.cache", "sofa", factory2)
        stored_factory, _, _ = registry_mod._extra_options["gateway.cache"]["sofa"]
        assert stored_factory is factory2

    def test_factory_is_stored_not_called(self) -> None:
        call_count = 0

        def factory() -> str:
            nonlocal call_count
            call_count += 1
            return "result"

        register_plugin_option("gateway.auth", "sofa", factory)
        assert call_count == 0
        stored_factory, _, _ = registry_mod._extra_options["gateway.auth"]["sofa"]
        result = stored_factory()
        assert result == "result"
        assert call_count == 1


class TestRegisterPluginOptionProvider:
    def test_register_provider_factory(self) -> None:
        def provider_factory(_root):
            return object()

        register_plugin_option_provider("cache_plugin", "sofa", provider_factory)

        assert has_enterprise_plugins() is True
        assert (
            registry_mod._plugin_option_providers["cache_plugin"]["sofa"]
            is provider_factory
        )

    def test_inject_provider_factory_receives_root_container(self) -> None:
        from dependency_injector import providers

        from gateway.community.bootstrap._configs import init_container_config
        from gateway.community.bootstrap._container import ApplicationContainer
        from gateway.community.plugin_registry import inject_into_plugin_container

        seen = []

        def provider_factory(root):
            seen.append(root)
            return providers.Object("configured-cache")

        register_plugin_option_provider("cache_plugin", "configured", provider_factory)
        container = ApplicationContainer()
        init_container_config(container)
        container.config.plugins.cache.override("configured")

        inject_into_plugin_container(container)

        assert seen == [container]
        assert container.plugins().cache_plugin() == "configured-cache"

    def test_inject_provider_skips_unknown_selector(self) -> None:
        from dependency_injector import providers

        from gateway.community.bootstrap._configs import init_container_config
        from gateway.community.bootstrap._container import ApplicationContainer
        from gateway.community.plugin_registry import inject_into_plugin_container

        register_plugin_option_provider(
            "nonexistent_selector", "sofa", lambda _root: providers.Object("x")
        )
        container = ApplicationContainer()
        init_container_config(container)

        # Should not raise
        inject_into_plugin_container(container)

    def test_inject_provider_skips_existing_option_name(self) -> None:
        from dependency_injector import providers

        from gateway.community.bootstrap._configs import init_container_config
        from gateway.community.bootstrap._container import ApplicationContainer
        from gateway.community.plugin_registry import inject_into_plugin_container

        call_count = 0

        def provider_factory(_root):
            nonlocal call_count
            call_count += 1
            return providers.Object("should-not-be-used")

        # "stub" already exists on cache_plugin selector
        register_plugin_option_provider("cache_plugin", "stub", provider_factory)
        container = ApplicationContainer()
        init_container_config(container)

        inject_into_plugin_container(container)

        # factory not called because "stub" already exists
        assert call_count == 0


class TestRegisterExtraAuthnStrategy:
    def test_register_and_inject(self) -> None:
        from dependency_injector import providers

        from gateway.community.bootstrap._configs import init_container_config
        from gateway.community.bootstrap._container import ApplicationContainer
        from gateway.community.plugin_registry import (
            inject_into_plugin_container,
            register_authn_strategy_provider,
        )

        result_obj = object()

        register_authn_strategy_provider(
            "agentpass", lambda _plugins: providers.Object(result_obj)
        )
        assert "agentpass" in registry_mod._authn_strategy_providers

        container = ApplicationContainer()
        init_container_config(container)
        inject_into_plugin_container(container)

        pool = container.plugins().authn_strategies()
        assert pool["agentpass"] is result_obj

    def test_inject_idempotent(self) -> None:
        from dependency_injector import providers

        from gateway.community.bootstrap._configs import init_container_config
        from gateway.community.bootstrap._container import ApplicationContainer
        from gateway.community.plugin_registry import (
            inject_into_plugin_container,
            register_authn_strategy_provider,
        )

        register_authn_strategy_provider(
            "x", lambda _plugins: providers.Object("result")
        )
        container = ApplicationContainer()
        init_container_config(container)

        inject_into_plugin_container(container)
        keys_before = set(container.plugins().authn_strategies().keys())
        inject_into_plugin_container(container)
        keys_after = set(container.plugins().authn_strategies().keys())

        assert keys_before == keys_after
        assert "x" in keys_after

    def test_inject_with_no_strategies_is_safe(self) -> None:
        from gateway.community.bootstrap._configs import init_container_config
        from gateway.community.bootstrap._container import ApplicationContainer
        from gateway.community.plugin_registry import inject_into_plugin_container

        container = ApplicationContainer()
        init_container_config(container)

        inject_into_plugin_container(container)
        pool = container.plugins().authn_strategies()
        assert "google" in pool
