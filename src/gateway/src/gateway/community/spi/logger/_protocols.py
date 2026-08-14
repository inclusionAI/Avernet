"""LoggerPlugin Protocol — logging abstraction.

Implementations provide a standard ``get_logger(name)`` and ``configure(...)``
interface independent of the underlying logging framework.

Implementations:
- ``BareLoggerPlugin`` (community): stdlib ``logging`` with per-logger file
  handlers and trace-id injection, replicating the SOFA output format.
- ``SofaLoggerPlugin`` (enterprise): delegates to ``sofapy_base.logger``.
"""

from __future__ import annotations

import logging
from typing import Protocol


class LoggerPlugin(Protocol):
    """Plugin protocol for logger management."""

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
        app_name: str = "gateway",
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
