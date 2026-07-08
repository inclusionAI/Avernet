"""Logger plugin Protocol — logging abstraction.

Implementations provide a standard ``get_logger(name)`` and
``configure(**kwargs)`` interface that the application can use
independently of the underlying logging framework.
"""

from __future__ import annotations

import logging
from typing import Protocol


class LoggerPlugin(Protocol):
    """Plugin protocol for logger management.

    Implementations:
    - SofaLoggerPlugin: delegates to ``sofapy_base.logger.logger.get_logger``,
      ``configure`` is a no-op.
    - BareLoggerPlugin: creates per-logger file handlers with trace ID
      injection, replicating SOFA's output format.
    """

    def get_logger(self, name: str | None = None) -> logging.Logger:
        """Return a logger for the given name.

        Args:
            name: Logger name. If None, an app-level default is used.

        Returns:
            A standard Python ``logging.Logger`` instance.
        """
        ...

    def configure(
        self,
        *,
        log_level: str = "INFO",
        log_dir: str = "",
        app_name: str = "secbaas",
        trace_log_dir: str = "",
    ) -> None:
        """Configure the logging system.

        Args:
            log_level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
            log_dir: Base directory for log files.
            app_name: Application name (used in log directory path).
            trace_log_dir: Directory for trace log files (if applicable).
        """
        ...
