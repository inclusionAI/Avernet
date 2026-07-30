"""Unit tests for AuthnStrategyRegistry — register, resolve, and factory support."""

from __future__ import annotations

from unittest.mock import Mock, NonCallableMock

import pytest

from gateway.community.bootstrap.plugins._registry import (
    AuthnStrategyRegistry,
    get_authn_registry,
    register_authn_strategy,
)


class TestAuthnStrategyRegistry:
    def test_initially_empty(self) -> None:
        reg = AuthnStrategyRegistry()
        assert reg.resolve_all() == {}

    def test_register_and_resolve_instance(self) -> None:
        reg = AuthnStrategyRegistry()
        strategy = NonCallableMock()
        reg.register("google", strategy)
        assert reg.resolve_all() == {"google": strategy}

    def test_register_mixed_instances_and_factories(self) -> None:
        reg = AuthnStrategyRegistry()
        inst = NonCallableMock()
        factory_result = NonCallableMock()
        reg.register("google", inst)
        reg.register("bot_token", lambda: factory_result)
        result = reg.resolve_all()
        assert result["google"] is inst
        assert result["bot_token"] is factory_result

    def test_register_overwrites_previous(self) -> None:
        reg = AuthnStrategyRegistry()
        a = NonCallableMock()
        b = NonCallableMock()
        reg.register("x", a)
        reg.register("x", b)
        result = reg.resolve_all()
        assert result["x"] is b
        assert len(result) == 1

    def test_factory_is_called_lazily_on_resolve(self) -> None:
        reg = AuthnStrategyRegistry()
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            return NonCallableMock()

        reg.register("agentpass", factory)
        assert call_count == 0  # not called on register

        reg.resolve_all()
        assert call_count == 1  # called once on resolve

        reg.resolve_all()
        assert call_count == 2  # called again on second resolve

    def test_factory_overwrite_with_instance(self) -> None:
        """Overwriting a factory with a concrete instance should resolve as instance."""
        reg = AuthnStrategyRegistry()
        factory_result = NonCallableMock()
        inst = NonCallableMock()
        reg.register("x", lambda: factory_result)
        reg.register("x", inst)
        result = reg.resolve_all()
        assert result["x"] is inst

    def test_instance_overwrite_with_factory(self) -> None:
        """Overwriting an instance with a factory should resolve as factory result."""
        reg = AuthnStrategyRegistry()
        inst = NonCallableMock()
        factory_result = NonCallableMock()
        reg.register("x", inst)
        reg.register("x", lambda: factory_result)
        result = reg.resolve_all()
        assert result["x"] is factory_result


class TestModuleLevelFunctions:
    def test_get_authn_registry_returns_singleton(self) -> None:
        r1 = get_authn_registry()
        r2 = get_authn_registry()
        assert r1 is r2

    def test_get_authn_registry_creates_lazily(self) -> None:
        import gateway.community.bootstrap.plugins._registry as mod

        mod._authn_registry = None
        r = get_authn_registry()
        assert r is not None
        assert mod._authn_registry is r

    def test_register_authn_strategy_via_module_function(self) -> None:
        import gateway.community.bootstrap.plugins._registry as mod

        mod._authn_registry = None
        strategy = NonCallableMock()
        register_authn_strategy("test_s", strategy)
        reg = get_authn_registry()
        assert reg.resolve_all()["test_s"] is strategy

    def test_register_authn_strategy_factory_via_module_function(self) -> None:
        import gateway.community.bootstrap.plugins._registry as mod

        mod._authn_registry = None
        result = NonCallableMock()
        register_authn_strategy("test_f", lambda: result)
        reg = get_authn_registry()
        assert reg.resolve_all()["test_f"] is result
