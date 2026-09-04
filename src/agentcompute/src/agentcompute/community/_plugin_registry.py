"""Plugin registry — mirrors ``secbaas.community.plugin_registry``.

Concrete implementations (agents, LLM providers, database, logger, tracer,
runner) register themselves from the ``community.plugins`` layer via
``register_plugin_option``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "LookupError",
    "PluginOption",
    "PluginRegistry",
    "get_registry",
    "register_plugin_option",
]


class LookupError(KeyError):
    """Raised when a plugin option is requested but not registered."""


@dataclass
class PluginOption:
    key: str
    name: str
    factory: Callable[..., Any]
    provider_type: str = "factory"  # "factory" | "callable"


@dataclass
class PluginRegistry:
    options: dict[str, dict[str, PluginOption]] = field(default_factory=dict)

    def register(
        self,
        key: str,
        name: str,
        factory: Callable[..., Any],
        provider_type: str = "factory",
    ) -> None:
        self.options.setdefault(key, {})[name] = PluginOption(
            key=key, name=name, factory=factory, provider_type=provider_type
        )

    def get(self, key: str, name: str) -> PluginOption:
        keyed = self.options.get(key)
        if keyed is None or name not in keyed:
            names = list(keyed) if keyed else []
            raise LookupError(
                f"plugin key '{key}' option '{name}' not registered (available: {names})"
            )
        return keyed[name]

    def names(self, key: str) -> list[str]:
        return list(self.options.get(key, {}))


_registry = PluginRegistry()


def get_registry() -> PluginRegistry:
    return _registry


def register_plugin_option(
    key: str,
    name: str,
    factory: Callable[..., Any],
    provider_type: str = "factory",
) -> None:
    _registry.register(key, name, factory, provider_type=provider_type)
