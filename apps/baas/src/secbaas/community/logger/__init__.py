"""Logger — standard ``logging.Logger`` factory.

The active logger plugin is lazily discovered via entry_points on first
access, so it can be swapped without a module-level env-var switch.

Usage::

    from secbaas.community.logger import get_logger

    log = get_logger("mymodule")
    log.info("hello")
"""

from __future__ import annotations

from secbaas.community.plugin_accessor import PluginAccessor
from secbaas.community.plugins.logger.bare import BareLoggerPlugin
from secbaas.community.spi.logger import LoggerPlugin

_accessor = PluginAccessor[LoggerPlugin]("secbaas.logger", BareLoggerPlugin)


def get_logger(name: str | None = None):
    """Return a logger for the given name.

    Args:
        name: Logger name. If none, an app-level default is used.

    Returns:
        A standard Python ``logging.Logger`` instance.
    """
    return _accessor.get().get_logger(name)


def get_logger_plugin() -> LoggerPlugin:
    return _accessor.get()


def set_logger_plugin(plugin: LoggerPlugin) -> None:
    _accessor.set(plugin)


__all__ = ["get_logger", "get_logger_plugin", "set_logger_plugin"]
