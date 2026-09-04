"""Database SPI — mirrors ``secbaas.community.spi.database``.

Abstracts a relational store behind a session/execute contract, so the rest of
the system persists request/plan/execution data without knowing the engine.
Concrete implementations (SQLite, later ZDAS/MySQL) register via the plugin
registry and are selected by config.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from typing import Any

__all__ = ["DatabasePlugin"]


class DatabasePlugin(ABC):
    """Relational persistence abstraction: connect, execute, session, close."""

    @abstractmethod
    def connect(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Any:
        """Run a statement and return a cursor-like result (rows where applicable)."""
        raise NotImplementedError

    def session(self) -> AbstractContextManager[Any]:
        raise NotImplementedError

    def close(self) -> None:
        pass
