"""Unit tests for authn strategy provider registration."""

from __future__ import annotations

from dependency_injector import providers

import gateway.community.plugin_registry as registry_mod
from gateway.community.bootstrap._configs import init_container_config
from gateway.community.bootstrap._container import ApplicationContainer
from gateway.community.plugin_registry import (
    inject_into_plugin_container,
    register_authn_strategy_provider,
    register_extra_authn_strategy,
)


def setup_function() -> None:
    registry_mod._extra_options.clear()
    registry_mod._plugin_option_providers.clear()
    registry_mod._authn_strategy_providers.clear()


def teardown_function() -> None:
    registry_mod._extra_options.clear()
    registry_mod._plugin_option_providers.clear()
    registry_mod._authn_strategy_providers.clear()


def test_register_authn_strategy_provider_adds_to_container_dict() -> None:
    result = object()

    def provider_factory(_plugins):
        return providers.Object(result)

    register_authn_strategy_provider("agentpass", provider_factory)
    container = ApplicationContainer()
    init_container_config(container)

    inject_into_plugin_container(container)

    pool = container.plugins().authn_strategies()
    assert pool["agentpass"] is result
    assert "google" in pool


def test_register_extra_authn_strategy_compatibility_shim() -> None:
    result = object()

    register_extra_authn_strategy("xoneid", lambda: result)
    container = ApplicationContainer()
    init_container_config(container)

    inject_into_plugin_container(container)

    assert container.plugins().authn_strategies()["xoneid"] is result


def test_authn_provider_injection_is_idempotent() -> None:
    result = object()
    register_authn_strategy_provider("x", lambda _plugins: providers.Object(result))
    container = ApplicationContainer()
    init_container_config(container)

    inject_into_plugin_container(container)
    kwargs_before = dict(container.plugins().providers["authn_strategies"].kwargs)
    inject_into_plugin_container(container)
    kwargs_after = dict(container.plugins().providers["authn_strategies"].kwargs)

    assert kwargs_before == kwargs_after
