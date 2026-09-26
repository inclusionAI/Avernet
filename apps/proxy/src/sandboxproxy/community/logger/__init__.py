"""Logger accessor — lazily resolve the logger plugin via entry points."""

from __future__ import annotations

from typing import Any

from sandboxproxy.community.plugin_accessor import PluginAccessor


def _fallback_logger() -> Any:
    from sandboxproxy.community.plugins.logger.bare import BareLoggerPlugin

    return BareLoggerPlugin()


_logger_accessor: PluginAccessor[Any] = PluginAccessor(
    "sandboxproxy.logger", _fallback_logger
)


def get_logger(name: str) -> Any:
    """Return a logger bound to ``name`` (canonical logger naming)."""
    return _logger_accessor.get().get_logger(name)


def get_logger_plugin() -> Any:
    """Return the active logger plugin."""
    return _logger_accessor.get()
