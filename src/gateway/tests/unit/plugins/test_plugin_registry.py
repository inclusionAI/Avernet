"""Unit tests for plugin registry — register and query enterprise plugin options."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import gateway.community.plugin_registry as registry_mod
from gateway.community.plugin_registry import (
    has_enterprise_plugins,
    register_plugin_option,
)


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Clear the global registry before and after each test."""
    registry_mod._extra_options.clear()
    yield
    registry_mod._extra_options.clear()


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
