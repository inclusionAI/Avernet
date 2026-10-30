"""Database connection manager — delegates to the active DataSourcePlugin.

The global ``db_manager`` singleton bridges the DI-resolved plugin and
the repository layer. Plugins call ``init_plugin(self)`` during
``init_database()``, and repositories obtain sessions via
``orm_session()`` / ``get_session()`` / ``session()``.
"""

import asyncio
import threading
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from secbaas.community.logger import get_logger
from secbaas.community.spi.database import DataSourcePlugin

logger = get_logger("database")


class TransactionRollbackError(RuntimeError):
    """An inner transaction participant failed; the whole scope was rolled back."""


def _execution_owner() -> tuple[int, asyncio.Task | None]:
    try:
        task = asyncio.current_task()
    except RuntimeError:  # Synchronous caller without a running event loop.
        task = None
    return threading.get_ident(), task


@dataclass
class _OrmTransaction:
    session: Session
    owner: tuple[int, asyncio.Task | None]
    rollback_only: bool = False


class DatabaseManager:
    """Global singleton that delegates all DB operations to a DataSourcePlugin.

    Once ``init_plugin`` is called, every session/connection request
    is forwarded to the plugin. No direct engine management happens here.
    """

    _instance: "DatabaseManager | None" = None
    _plugin: DataSourcePlugin | None = None
    _transaction: ContextVar[_OrmTransaction | None] = ContextVar(
        "database_orm_transaction", default=None
    )

    def __new__(cls) -> "DatabaseManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def init_plugin(self, plugin: DataSourcePlugin) -> None:
        """Register the active DataSourcePlugin.

        Called by the plugin's ``init_database()`` during startup.
        """
        logger.info("DatabaseManager: plugin set — type=%s", type(plugin).__name__)
        self._plugin = plugin

    @contextmanager
    def session(self, datasource_name: str = "default") -> Any:
        if self._plugin is None:
            raise RuntimeError("Database not initialized. Call init_plugin() first.")
        with self._plugin.sync_connection(datasource_name) as conn:
            yield conn

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        if self._plugin is None:
            raise RuntimeError("Database not initialized. Call init_plugin() first.")
        async for session in self._plugin.session():
            yield session

    async def close(self) -> None:
        if self._plugin is not None:
            await self._plugin.close()
            logger.info("DatabaseManager: plugin connections closed")

    @contextmanager
    def scoped_orm_session(self) -> Generator[Session, None, None]:
        """Explicit short synchronous transaction; nested scopes join its owner.

        Do not await, do external work or cross a thread/task boundary here.
        The plugin, not the manager, owns commit/rollback/close.
        """
        if self._transaction.get() is not None:
            with self.orm_session() as session:
                yield session
            return
        if self._plugin is None:
            raise RuntimeError("Database not initialized. Call init_plugin() first.")
        with self._plugin.orm_session() as session:
            state = _OrmTransaction(session, _execution_owner())
            token = self._transaction.set(state)
            try:
                yield session
                if state.rollback_only:
                    raise TransactionRollbackError(
                        "Transaction rolled back because an inner operation failed."
                    )
            finally:
                self._transaction.reset(token)

    @contextmanager
    def orm_session(self) -> Generator[Session, None, None]:
        state = self._transaction.get()
        if state is not None:
            if state.owner != _execution_owner():
                raise RuntimeError(
                    "An ORM transaction cannot cross a thread or asyncio task boundary."
                )
            try:
                yield state.session
            except BaseException:
                state.rollback_only = True
                raise
            return
        if self._plugin is None:
            raise RuntimeError("Database not initialized. Call init_plugin() first.")
        with self._plugin.orm_session() as session:
            yield session

    @property
    def is_initialized(self) -> bool:
        return self._plugin is not None


# Global singleton
db_manager = DatabaseManager()
