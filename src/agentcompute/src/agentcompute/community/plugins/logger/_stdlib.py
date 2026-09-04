"""Stdlib logging-based LoggerPlugin implementation."""

from __future__ import annotations

import logging
from pathlib import Path

from ..._logger import RequestIdFilter
from ...spi._logger import LoggerPlugin

__all__ = ["StdlibLoggerPlugin"]


class StdlibLoggerPlugin(LoggerPlugin):
    """Logger backed by the stdlib ``logging`` module."""

    def get_logger(self, name: str | None = None) -> logging.Logger:
        return logging.getLogger(name or "agentcompute")

    def configure(
        self,
        *,
        log_level: str = "INFO",
        log_dir: str = "",
        app_name: str = "agentcompute",
        trace_log_dir: str = "",
    ) -> None:
        level = getattr(logging, log_level.upper(), logging.INFO)
        root = logging.getLogger()
        root.setLevel(level)
        for handler in list(root.handlers):
            root.removeHandler(handler)
        if log_dir:
            path = Path(log_dir).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(path / "agentcompute.log")
            handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s %(levelname)s %(name)s [job_id=%(job_id)s] %(message)s"
                )
            )
            handler.addFilter(RequestIdFilter())
            root.addHandler(handler)
        logging.basicConfig(level=level)
