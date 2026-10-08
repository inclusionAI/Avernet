"""Plugin registry — allows an enterprise package to register DI extensions.

Community never imports enterprise; the bootstrap composition root injects
registered providers into its DI container before resolving services.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_extra_options: dict[
    str, dict[str, tuple[Callable[..., Any], str, dict[str, Any]]]
] = {}


def register_plugin_option(
    plugin_name: str,
    option_name: str,
    factory: Callable[..., Any],
    *,
    provider_type: str = "singleton",
    **provider_kwargs: Any,
) -> None:
    if plugin_name not in _extra_options:
        _extra_options[plugin_name] = {}
    _extra_options[plugin_name][option_name] = (
        factory,
        provider_type,
        provider_kwargs,
    )


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


def has_enterprise_plugins() -> bool:
    return bool(_extra_options)
