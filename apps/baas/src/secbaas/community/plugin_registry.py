"""Plugin registry — allows enterprise package to register additional plugin options.

Enterprise calls register_plugin_option() at import time.
inject_into_plugin_container() merges the registered options into each
PluginContainer instance's Selectors at container creation time.

The registry stores deferred factory callables (not providers) so that
enterprise module imports only happen when the factory is actually called.
inject_into_plugin_container wraps them in providers.Singleton or
providers.Callable.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_extra_options: dict[str, dict[str, tuple[Callable[..., Any], dict[str, Any]]]] = {}


def register_plugin_option(
    plugin_name: str,
    option_name: str,
    factory: Callable[..., Any],
    *,
    provider_type: str = "singleton",
    **provider_kwargs: Any,
) -> None:
    """Register an additional option for a plugin Selector.

    Args:
        plugin_name: Selector attribute name in PluginContainer (e.g. "cache_plugin").
        option_name: Selector key (e.g. "real", "buservice").
        factory: Deferred callable that returns the plugin class or instance.
        provider_type: "singleton" or "callable" — how to wrap in dependency_injector.
        **provider_kwargs: Extra kwargs passed to the provider (e.g. dependency injections).
    """
    if plugin_name not in _extra_options:
        _extra_options[plugin_name] = {}
    _extra_options[plugin_name][option_name] = (factory, provider_type, provider_kwargs)


def inject_into_plugin_container(container: Any) -> None:
    """Inject registered extra options into a PluginContainer instance's Selectors.

    Enterprise registers options *after* PluginContainer is defined (because
    importing ``secbaas.community.bootstrap`` triggers the full
    bootstrap.__init__ → _container → _plugin_core import chain, which
    defines PluginContainer before enterprise's register calls run).

    This function merges the extra options into the **instance-level**
    Selectors via ``set_providers()``, leaving the class-level Selectors
    untouched so that other container instances are not affected.

    Args:
        container: An ``ApplicationContainer`` whose ``plugins()`` returns
            the ``PluginContainer`` instance to patch.
    """
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


def has_enterprise_plugins() -> bool:
    """Check if any enterprise plugin options have been registered."""
    return bool(_extra_options)
