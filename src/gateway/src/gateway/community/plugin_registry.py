"""Plugin registry — allows enterprise package to register additional plugin options.

Enterprise calls register_plugin_option() at import time to add new plugin
implementations (e.g. "sofa") alongside the community "bare" defaults.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_extra_options: dict[str, dict[str, Callable[..., Any]]] = {}


def register_plugin_option(
    plugin_name: str,
    option_name: str,
    factory: Callable[..., Any],
) -> None:
    """Register an additional option for a plugin accessor.

    Args:
        plugin_name: Entry point group name (e.g. "gateway.cache").
        option_name: Option key (e.g. "sofa").
        factory: Callable that returns the plugin instance.
    """
    if plugin_name not in _extra_options:
        _extra_options[plugin_name] = {}
    _extra_options[plugin_name][option_name] = factory


def has_enterprise_plugins() -> bool:
    """Check if any enterprise plugin options have been registered."""
    return bool(_extra_options)
