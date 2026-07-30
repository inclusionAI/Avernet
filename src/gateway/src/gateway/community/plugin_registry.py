"""Plugin registry — allows enterprise package to register additional plugin options.

Enterprise calls register_plugin_option() at import time to add new plugin
implementations (e.g. "sofa") alongside the community "bare" defaults.
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
    """Register an additional option for a plugin accessor.

    Args:
        plugin_name: Entry point group name (e.g. "gateway.cache").
        option_name: Option key (e.g. "sofa").
        factory: Callable that returns the plugin instance.
    """
    if plugin_name not in _extra_options:
        _extra_options[plugin_name] = {}
    _extra_options[plugin_name][option_name] = (factory, provider_type, provider_kwargs)


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


def inject_extra_authn_strategies() -> None:
    """Inject enterprise authn strategies into the shared AuthnStrategyRegistry."""
    from gateway.community.bootstrap.plugins import register_authn_strategy

    options = _extra_options.pop("authn_strategies", {})
    for name, (factory, _ptype, _kwargs) in options.items():
        register_authn_strategy(name, factory)


def register_extra_authn_strategy(name: str, factory: Callable[..., Any]) -> None:
    _extra_options.setdefault("authn_strategies", {})[name] = (
        factory,
        "singleton",
        {},
    )


def has_enterprise_plugins() -> bool:
    """Check if any enterprise plugin options have been registered."""
    return bool(_extra_options)
