"""Logger — standard ``logging.Logger`` factory.

The active logger plugin is managed via a context variable so it can
be swapped after the DI container initialises without a module-level
env-var switch.

Usage::

    from secbaas.community.logger import get_logger

    log = get_logger("mymodule")
    log.info("hello")
"""

from __future__ import annotations

import os
from importlib.metadata import entry_points

from secbaas.community.plugins.logger.bare import BareLoggerPlugin
from secbaas.community.spi.logger import LoggerPlugin


def _load_logger_plugin() -> LoggerPlugin:
    """Discover logger plugin via entry_points; fall back to bare."""
    _is_sofa_mode = os.getenv("SECBAAS_RUN_MODE", "bare").lower() == "sofa"
    if _is_sofa_mode:
        for ep in entry_points(group="secbaas.logger"):
            if ep.name == "sofa":
                return ep.load()()
    return BareLoggerPlugin()


_logger_plugin: LoggerPlugin | None = None


def _get_logger_plugin() -> LoggerPlugin:
    global _logger_plugin
    if _logger_plugin is None:
        _logger_plugin = _load_logger_plugin()
    return _logger_plugin


def get_logger(name: str | None = None):
    """Return a logger for the given name.

    Args:
        name: Logger name. If none, an app-level default is used.

    Returns:
        A standard Python ``logging.Logger`` instance.
    """
    return _get_logger_plugin().get_logger(name)


def get_logger_plugin() -> LoggerPlugin:
    return _get_logger_plugin()


__all__ = ["get_logger", "get_logger_plugin"]
