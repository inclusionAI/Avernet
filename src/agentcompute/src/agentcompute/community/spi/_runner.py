"""Runner SPI — mirrors ``secbaas.community.spi.runner.AppRunnerPlugin``."""

from __future__ import annotations

from abc import ABC, abstractmethod

__all__ = ["AppRunnerPlugin"]


class AppRunnerPlugin(ABC):
    """Application runner: boots the service for a given runtime mode."""

    @abstractmethod
    def run(self, config_path: str | None = None) -> None:
        raise NotImplementedError
