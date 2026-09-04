"""Logger SPI — mirrors ``secbaas.community.spi.logger``."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

__all__ = ["LoggerPlugin"]


class LoggerPlugin(ABC):
    """Logger abstraction: obtain and configure named loggers."""

    @abstractmethod
    def get_logger(self, name: str | None = None) -> logging.Logger:
        raise NotImplementedError

    def configure(
        self,
        *,
        log_level: str = "INFO",
        log_dir: str = "",
        app_name: str = "agentcompute",
        trace_log_dir: str = "",
    ) -> None:
        pass
