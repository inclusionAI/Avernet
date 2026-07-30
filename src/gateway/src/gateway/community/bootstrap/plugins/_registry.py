"""Authn strategy registry — a DI-friendly dict of named AuthStrategy instances.

All strategies (both community built-ins and enterprise extras) are registered
via ``register_authn_strategy()`` into this shared registry. At bootstrap,
``_strategy_chains()`` calls ``resolve_all()`` to build the identity-chain pool.
"""

from __future__ import annotations

from collections.abc import Callable

from gateway.community.spi.authn import AuthStrategy


class AuthnStrategyRegistry:
    """Mutable per-name collector of authn strategies.

    Strategies can be registered either as concrete instances (community
    built-ins, constructed at bootstrap with DI-resolved dependencies) or as
    factory callables (enterprise extras, registered at import time but
    instantiated later during ``resolve_all()`` to avoid early side-effects
    like SDK startup).
    """

    def __init__(self) -> None:
        self._entries: dict[str, AuthStrategy | Callable[[], AuthStrategy]] = {}

    def register(
        self, name: str, strategy: AuthStrategy | Callable[[], AuthStrategy]
    ) -> None:
        self._entries[name] = strategy

    def resolve_all(self) -> dict[str, AuthStrategy]:
        result: dict[str, AuthStrategy] = {}
        for name, entry in self._entries.items():
            if callable(entry):
                result[name] = entry()
            else:
                result[name] = entry
        return result


_authn_registry: AuthnStrategyRegistry | None = None


def get_authn_registry() -> AuthnStrategyRegistry:
    global _authn_registry
    if _authn_registry is None:
        _authn_registry = AuthnStrategyRegistry()
    return _authn_registry


def register_authn_strategy(
    name: str, strategy: AuthStrategy | Callable[[], AuthStrategy]
) -> None:
    get_authn_registry().register(name, strategy)
