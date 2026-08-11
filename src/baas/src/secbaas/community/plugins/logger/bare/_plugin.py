"""Baas-mode logger plugin — SOFA-compatible logging without SOFA.

Extracted from ``logger/_facade.py``: creates per-logger file handlers
with trace ID injection, replicating SOFA's output format.

4 files per named logger (``{name}.log``, ``{name}-error.log``,
``{name}-fatal.log``, ``common-error.log``), all written to
``~/logs/{app_name}/``.
"""

from __future__ import annotations

import logging
import os
import threading
from logging.handlers import TimedRotatingFileHandler

from secbaas.community.spi.logger import LoggerPlugin

_FILE_FORMAT = (
    "%(asctime)s - %(name)s - %(levelname)s - "
    "[%(process)d] - [%(processName)s] - [%(traceid)s] - "
    "%(message)s"
)

_root_lock = threading.Lock()


class _TraceIdFilter(logging.Filter):
    """Inject the current trace ID into every log record as ``traceid``.

    Works with any tracer plugin that implements ``get_trace_id()``
    (e.g. ``BareTracerPlugin``, ``SofaTracerPlugin``).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        from secbaas.community.tracer import get_tracer_plugin

        record.traceid = get_tracer_plugin().get_trace_id()
        return True


def _resolve_log_level(level: str) -> int:
    upper = level.upper().strip()
    if upper in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        return getattr(logging, upper)
    try:
        return int(level)
    except (ValueError, TypeError):
        return logging.INFO


class BareLoggerPlugin(LoggerPlugin):
    """Logger plugin for baas (non-SOFA) mode.

    Creates per-logger file handlers with trace ID injection,
    replicating the exact output format SOFA produces.
    """

    def __init__(self) -> None:
        self._level: int = logging.INFO
        self._log_dir: str = ""
        self._app_name: str = ""
        self._loggers: dict[str, logging.Logger] = {}

    def configure(
        self,
        *,
        log_level: str = "INFO",
        log_dir: str = "",
        app_name: str = "secbaas",
        trace_log_dir: str = "",
    ) -> None:
        self._level = _resolve_log_level(log_level)
        self._log_dir = log_dir or os.path.expanduser(f"~/logs/{app_name}")
        self._app_name = app_name

        os.makedirs(self._log_dir, exist_ok=True)
        self._configure_root()

        for logger_name, logger in list(self._loggers.items()):
            self._configure_logger(logger, logger_name)

    def _configure_root(self) -> None:
        with _root_lock:
            root = logging.getLogger()
            root.setLevel(self._level)
            root.handlers.clear()
            ch = logging.StreamHandler()
            ch.setFormatter(logging.Formatter(_FILE_FORMAT))
            ch.addFilter(_TraceIdFilter())
            root.addHandler(ch)

    def get_logger(self, name: str | None = None) -> logging.Logger:
        name = name or self._app_name
        if name in self._loggers:
            return self._loggers[name]

        logger = logging.getLogger(name)
        self._loggers[name] = logger
        self._configure_logger(logger, name)
        return logger

    def _configure_logger(self, logger: logging.Logger, name: str) -> None:
        logger.handlers.clear()
        logger.setLevel(self._level)
        logger.propagate = False

        if not self._log_dir:
            return

        base = self._app_name if name == self._app_name else name

        self._add_file_handler(logger, f"{base}.log", self._level)
        self._add_file_handler(logger, f"{base}-error.log", logging.ERROR)
        self._add_file_handler(logger, f"{base}-fatal.log", logging.CRITICAL)
        self._add_file_handler(logger, "common-error.log", logging.ERROR)

    def _add_file_handler(
        self, logger: logging.Logger, filename: str, level: int
    ) -> None:
        path = os.path.join(self._log_dir, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        h = TimedRotatingFileHandler(
            path,
            when="midnight",
            backupCount=7,
            encoding="utf-8",
        )
        h.setFormatter(logging.Formatter(_FILE_FORMAT))
        h.addFilter(_TraceIdFilter())
        h.setLevel(level)
        logger.addHandler(h)
