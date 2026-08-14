"""Plugin registry — allows enterprise package to register DI extensions.

Enterprise calls these functions at import time to add new provider options
alongside the community defaults. Community never imports enterprise; the
bootstrap composition root injects the registered providers into its DI
container before resolving services.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_extra_options: dict[
    str, dict[str, tuple[Callable[..., Any], str, dict[str, Any]]]
] = {}
_plugin_option_providers: dict[str, dict[str, Callable[[Any], Any]]] = {}
_authn_strategy_providers: dict[str, Callable[[Any], Any]] = {}


def register_plugin_option_provider(
    plugin_name: str,
    option_name: str,
    provider_factory: Callable[[Any], Any],
) -> None:
    """Register an additional DI provider option for a plugin selector.

    ``provider_factory`` receives the root application container and must
    return a dependency-injector provider. This is the preferred extension API
    for plugins that need access to loaded config or other providers during
    composition.
    """
    _plugin_option_providers.setdefault(plugin_name, {})[option_name] = provider_factory


def register_plugin_option(
    plugin_name: str,
    option_name: str,
    factory: Callable[..., Any],
    *,
    provider_type: str = "singleton",
    **provider_kwargs: Any,
) -> None:
    """Register an additional option for a plugin selector."""
    if plugin_name not in _extra_options:
        _extra_options[plugin_name] = {}
    _extra_options[plugin_name][option_name] = (factory, provider_type, provider_kwargs)


def register_authn_strategy_provider(
    name: str,
    provider_factory: Callable[[Any], Any],
) -> None:
    """Register an authn strategy provider extension.

    ``provider_factory`` receives the DI ``PluginContainer`` instance and must
    return a dependency-injector provider for an ``AuthStrategy``. The provider
    is merged into ``PluginContainer.authn_strategies`` under ``name``.
    """
    _authn_strategy_providers[name] = provider_factory


def register_extra_authn_strategy(name: str, factory: Callable[..., Any]) -> None:
    """Compatibility shim: register a zero-arg factory as an authn provider."""

    def _provider_factory(_plugin_container: Any) -> Any:
        from dependency_injector import providers

        return providers.Singleton(factory)

    register_authn_strategy_provider(name, _provider_factory)


def inject_into_plugin_container(container: Any) -> None:
    from dependency_injector import providers

    plugin_container = container.plugins()

    for plugin_name, options in _extra_options.items():
        selector: providers.Selector | None = getattr(
            plugin_container, plugin_name, None
        )
        if selector is None:
            continue
        existing = dict(selector.providers)
        for option_name, (factory, provider_type, kwargs) in options.items():
            if option_name in existing:
                continue
            if provider_type == "callable":
                existing[option_name] = providers.Callable(factory, **kwargs)
            else:
                existing[option_name] = providers.Singleton(factory, **kwargs)
        selector.set_providers(**existing)

    for plugin_name, options in _plugin_option_providers.items():
        selector: providers.Selector | None = getattr(
            plugin_container, plugin_name, None
        )
        if selector is None:
            continue
        existing = dict(selector.providers)
        for option_name, provider_factory in options.items():
            if option_name in existing:
                continue
            existing[option_name] = provider_factory(container)
        selector.set_providers(**existing)

    if _authn_strategy_providers:
        strategy_dict = plugin_container.providers.get("authn_strategies")
        if strategy_dict is not None:
            additions = {
                name: provider_factory(plugin_container)
                for name, provider_factory in _authn_strategy_providers.items()
                if name not in strategy_dict.kwargs
            }
            if additions:
                strategy_dict.add_kwargs(**additions)


def inject_extra_authn_strategies() -> None:
    """Deprecated no-op kept for old callers.

    Authn strategies are injected into ``PluginContainer.authn_strategies`` by
    ``inject_into_plugin_container()``; there is no global AuthnStrategyRegistry
    injection path anymore.
    """


def has_enterprise_plugins() -> bool:
    """Check if any enterprise plugin options have been registered."""
    return bool(_extra_options or _plugin_option_providers or _authn_strategy_providers)
