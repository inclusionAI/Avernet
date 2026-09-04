"""Tracer SPI — mirrors ``secbaas.community.spi.tracer``."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

__all__ = ["TracerPlugin"]


class TracerPlugin(ABC):
    """Request tracing abstraction: setup, middleware, and trace id access."""

    @abstractmethod
    def setup(self, app_name: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def install_middleware(self, app: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_trace_id(self) -> str:
        raise NotImplementedError
