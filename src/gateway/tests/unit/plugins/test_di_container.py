"""Unit tests for DI container — PluginConfig, PluginContainer Selectors, plugin_registry injection."""

from __future__ import annotations

import pytest

from gateway.community.bootstrap._configs import (
    AuthnPluginConfig,
    DatabasePluginConfig,
    PluginConfig,
    init_container_config,
)
from gateway.community.bootstrap._container import ApplicationContainer
from gateway.community.plugin_registry import (
    has_enterprise_plugins,
    inject_into_plugin_container,
    register_plugin_option,
)


class TestPluginConfig:
    def test_defaults_community_safe(self) -> None:
        cfg = PluginConfig()
        assert cfg.forwarder == "httpx"
        assert cfg.schema_catalog == "file"
        assert cfg.cache == "stub"
        assert cfg.authn.app_token == "stub"
        assert cfg.authn.tenant == "stub"
        assert cfg.database.plugin_database == "SQLITE_ORM"
        assert cfg.database.database_url == ""

    def test_arbitrary_forwarder_accepted(self) -> None:
        """Community does not restrict which values enterprise can use."""
        cfg = PluginConfig(forwarder="custom-backend")
        assert cfg.forwarder == "custom-backend"

    def test_arbitrary_cache_accepted(self) -> None:
        """Community does not restrict which values enterprise can use."""
        cfg = PluginConfig(cache="custom-backend")
        assert cfg.cache == "custom-backend"

    def test_custom_values_accepted(self) -> None:
        cfg = PluginConfig(
            forwarder="sofa",
            cache="real",
            authn=AuthnPluginConfig(app_token="real", tenant="real"),
        )
        assert cfg.forwarder == "sofa"
        assert cfg.cache == "real"
        assert cfg.authn.app_token == "real"

    def test_authn_defaults(self) -> None:
        cfg = AuthnPluginConfig()
        assert cfg.app_token == "stub"
        assert cfg.tenant == "stub"

    def test_database_defaults(self) -> None:
        cfg = DatabasePluginConfig()
        assert cfg.plugin_database == "SQLITE_ORM"
        assert cfg.database_url == ""


class TestPluginContainerSelectors:
    def test_default_config_resolves_stub_plugins(self) -> None:
        container = ApplicationContainer()
        init_container_config(container)
        plugins = container.plugins()

        db = plugins.database()
        assert db is not None

        forwarder = plugins.forwarder()
        assert forwarder is not None

        catalog = plugins.schema_catalog()
        assert catalog is not None

        cache = plugins.cache_plugin()
        assert cache is not None


class TestPluginRegistryInjection:
    def test_inject_adds_option_to_selector(self) -> None:
        container = ApplicationContainer()
        init_container_config(container)

        # Enterprise registers a "real" cache option
        def real_cache_factory() -> str:
            return "real-cache-instance"

        register_plugin_option("cache_plugin", "real", real_cache_factory)

        # Verify option was registered
        assert has_enterprise_plugins()

        # Inject into container
        inject_into_plugin_container(container)

        # Verify the Selector now has the "real" option
        plugin_container = container.plugins()
        selector = plugin_container.providers["cache_plugin"]
        existing = dict(selector.providers)
        assert "real" in existing

    def test_inject_idempotent(self) -> None:
        container = ApplicationContainer()
        init_container_config(container)

        register_plugin_option("cache_plugin", "real", lambda: "v1")
        inject_into_plugin_container(container)

        plugin_container = container.plugins()
        providers_before = dict(plugin_container.providers["cache_plugin"].providers)

        # Second injection should not change providers
        inject_into_plugin_container(container)
        providers_after = dict(plugin_container.providers["cache_plugin"].providers)

        assert providers_before == providers_after

    def test_inject_skips_unknown_selector(self) -> None:
        container = ApplicationContainer()
        init_container_config(container)

        register_plugin_option("nonexistent_plugin", "real", lambda: "v")
        # Should not raise
        inject_into_plugin_container(container)


class TestApplicationContainer:
    def test_container_creates_without_error(self) -> None:
        container = ApplicationContainer()
        assert container is not None
        assert hasattr(container, "config")
        assert hasattr(container, "plugins")

    def test_config_populated_after_init(self) -> None:
        container = ApplicationContainer()
        init_container_config(container)

        assert container.config.plugins.forwarder() == "httpx"
        assert container.config.plugins.cache() == "stub"
        assert container.config.plugins.database.plugin_database() == "SQLITE_ORM"

    def test_config_overridable(self) -> None:
        """Container config can be overridden from YAML-like dict."""
        container = ApplicationContainer()
        container.config.from_dict(
            {
                "plugins": {
                    "forwarder": "sofa",
                    "cache": "real",
                }
            }
        )

        assert container.config.plugins.forwarder() == "sofa"
        assert container.config.plugins.cache() == "real"


class TestRenderProviderTree:
    """Cover the new Singleton/Callable branches in _render_provider_tree."""

    def test_singleton_resolved(self) -> None:
        from dependency_injector import containers, providers

        from gateway.community.bootstrap._container import _render_provider_tree

        class MyPlugin:
            pass

        class C(containers.DeclarativeContainer):
            p = providers.Singleton(MyPlugin)

        lines = _render_provider_tree(C())
        assert any("Singleton → MyPlugin" in line for line in lines)

    def test_singleton_unresolved(self) -> None:
        from dependency_injector import containers, providers

        from gateway.community.bootstrap._container import _render_provider_tree

        def _raiser() -> str:
            raise RuntimeError("boom")

        class C(containers.DeclarativeContainer):
            p = providers.Singleton(_raiser)

        lines = _render_provider_tree(C())
        assert any("Singleton (unresolved)" in line for line in lines)

    def test_callable_dict_resolved(self) -> None:
        from dependency_injector import containers, providers

        from gateway.community.bootstrap._container import _render_provider_tree

        class A:
            pass

        class B:
            pass

        class C(containers.DeclarativeContainer):
            p = providers.Callable(lambda: {"x": A(), "y": B()})

        lines = _render_provider_tree(C())
        assert any("Callable" in line and "x: A, y: B" in line for line in lines)

    def test_callable_non_dict_resolved(self) -> None:
        from dependency_injector import containers, providers

        from gateway.community.bootstrap._container import _render_provider_tree

        class MyPlugin:
            pass

        class C(containers.DeclarativeContainer):
            p = providers.Callable(MyPlugin)

        lines = _render_provider_tree(C())
        assert any("Callable → MyPlugin" in line for line in lines)

    def test_callable_unresolved(self) -> None:
        from dependency_injector import containers, providers

        from gateway.community.bootstrap._container import _render_provider_tree

        def _raiser() -> str:
            raise RuntimeError("boom")

        class C(containers.DeclarativeContainer):
            p = providers.Callable(_raiser)

        lines = _render_provider_tree(C())
        assert any("Callable (unresolved)" in line for line in lines)


class TestInjectEnterprisePlugins:
    """Cover _inject_enterprise_plugins and the new inject_extra_authn_strategies path."""

    def test_inject_runs_authn_strategies(self) -> None:
        """When enterprise plugins are registered, _inject_enterprise_plugins
        calls inject_extra_authn_strategies which populates AuthnStrategyRegistry."""
        import gateway.community.bootstrap.plugins._registry as reg_mod
        import gateway.community.plugin_registry as registry_mod
        from gateway.community.bootstrap import get_container, set_container
        from gateway.community.plugin_registry import (
            has_enterprise_plugins,
            register_extra_authn_strategy,
            register_plugin_option,
        )

        # Reset state
        reg_mod._authn_registry = None
        registry_mod._extra_options.clear()

        # Register an enterprise authn strategy
        result = object()
        register_extra_authn_strategy("test_strategy", lambda: result)
        # Also register a selector plugin to trigger has_enterprise_plugins
        register_plugin_option("cache_plugin", "test", lambda: "test-cache")
        assert has_enterprise_plugins()

        # Create container — _inject_enterprise_plugins runs here
        container = get_container()
        set_container(container)

        # AuthnStrategyRegistry should now have the test strategy
        pool = reg_mod.get_authn_registry().resolve_all()
        assert "test_strategy" in pool
        assert pool["test_strategy"] is result

        # Cleanup
        reg_mod._authn_registry = None
        registry_mod._extra_options.clear()

    def test_no_enterprise_plugins_no_injection(self) -> None:
        """When no enterprise plugins are registered, inject paths are skipped."""
        import gateway.community.bootstrap.plugins._registry as reg_mod
        import gateway.community.plugin_registry as registry_mod
        from gateway.community.bootstrap import get_container

        reg_mod._authn_registry = None
        registry_mod._extra_options.clear()

        get_container()

        pool = reg_mod.get_authn_registry().resolve_all()
        assert pool == {}

        reg_mod._authn_registry = None
        registry_mod._extra_options.clear()
