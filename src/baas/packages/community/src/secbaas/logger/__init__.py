"""Logger — standard ``logging.Logger`` factory.

The active logger plugin is managed via a context variable so it can
be swapped after the DI container initialises without a module-level
env-var switch.

Usage::

    from secbaas.logger import get_logger

    log = get_logger("mymodule")
    log.info("hello")
"""

from __future__ import annotations

import os

from secbaas.plugins.logger.bare import BareLoggerPlugin
from secbaas.spi.logger import LoggerPlugin

_is_sofa_mode = os.getenv("SECBAAS_RUN_MODE", "bare").lower() == "sofa"

if _is_sofa_mode:
    from secbaas_enterprise.plugins.logger.sofa import SofaLoggerPlugin

    _logger_plugin: LoggerPlugin = SofaLoggerPlugin()
else:
    _logger_plugin: LoggerPlugin = BareLoggerPlugin()


def get_logger(name: str | None = None):
    """Return a logger for the given name.

    Args:
        name: Logger name. If none, an app-level default is used.

    Returns:
        A standard Python ``logging.Logger`` instance.
    """
    return _logger_plugin.get_logger(name)


def get_logger_plugin() -> LoggerPlugin:
    return _logger_plugin


__all__ = ["get_logger", "get_logger_plugin"]
