"""Plugin registry — allows enterprise package to register additional plugin options.

Community's PluginContainer reads from this registry to augment its Selector
options. Enterprise calls register_plugin_option() at import time.

The registry stores deferred factory callables (not providers) so that
enterprise module imports only happen when the factory is actually called.
PluginContainer wraps them in providers.Singleton or providers.Callable.
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


def get_extra_options(plugin_name: str) -> dict[str, Any]:
    """Get registered extra options as dependency_injector providers.

    Returns a dict mapping option_name → provider, suitable for
    splatting into a providers.Selector() call.
    """
    from dependency_injector import providers

    result: dict[str, Any] = {}
    for option_name, (factory, provider_type, kwargs) in _extra_options.get(
        plugin_name, {}
    ).items():
        if provider_type == "callable":
            result[option_name] = providers.Callable(factory, **kwargs)
        else:
            result[option_name] = providers.Singleton(factory, **kwargs)
    return result


def has_enterprise_plugins() -> bool:
    """Check if any enterprise plugin options have been registered."""
    return bool(_extra_options)
