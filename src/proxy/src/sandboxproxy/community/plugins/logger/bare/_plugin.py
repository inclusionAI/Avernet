"""Bare logger plugin — stdlib logging, no sidecar."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from typing import Any

_FILE_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s %(message)s"
)


class BareLoggerPlugin:
    """Logger plugin backed by stdlib ``logging`` (configured once)."""

    _configured = False

    def __init__(self) -> None:
        self._level: int = logging.INFO
        self._log_dir: str = ""
        self._app_name: str = "sandboxproxy"
        self._loggers: dict[str, logging.Logger] = {}

    def configure(
        self,
        *,
        log_level: str = "INFO",
        log_dir: str = "",
        app_name: str = "sandboxproxy",
        trace_log_dir: str = "",
    ) -> None:
        if BareLoggerPlugin._configured:
            return
        self._level = getattr(logging, log_level.upper(), logging.INFO)
        self._log_dir = log_dir or os.path.expanduser(f"~/logs/{app_name}")
        self._app_name = app_name
        os.makedirs(self._log_dir, exist_ok=True)

        root = logging.getLogger()
        root.setLevel(self._level)
        root.handlers.clear()

        ch = logging.StreamHandler(sys.stderr)
        ch.setFormatter(logging.Formatter(_FILE_FORMAT))
        root.addHandler(ch)

        fh = TimedRotatingFileHandler(
            os.path.join(self._log_dir, f"{app_name}.log"),
            when="midnight",
            backupCount=7,
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter(_FILE_FORMAT))
        fh.setLevel(self._level)
        root.addHandler(fh)

        err_fh = TimedRotatingFileHandler(
            os.path.join(self._log_dir, f"{app_name}-error.log"),
            when="midnight",
            backupCount=7,
            encoding="utf-8",
        )
        err_fh.setFormatter(logging.Formatter(_FILE_FORMAT))
        err_fh.setLevel(logging.ERROR)
        root.addHandler(err_fh)

        BareLoggerPlugin._configured = True

        for logger_name, logger in list(self._loggers.items()):
            self._configure_logger(logger, logger_name)

    def get_logger(self, name: str) -> Any:
        if name in self._loggers:
            return self._loggers[name]
        logger = logging.getLogger(name)
        self._loggers[name] = logger
        if BareLoggerPlugin._configured:
            self._configure_logger(logger, name)
        return logger

    def _configure_logger(self, logger: logging.Logger, name: str) -> None:
        logger.handlers.clear()
        logger.setLevel(self._level)
        logger.propagate = True
