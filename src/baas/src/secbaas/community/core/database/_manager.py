"""Database connection manager — delegates to the active DataSourcePlugin.

The global ``db_manager`` singleton bridges the DI-resolved plugin and
the repository layer. Plugins call ``init_plugin(self)`` during
``init_database()``, and repositories obtain sessions via
``orm_session()`` / ``get_session()`` / ``session()``.
"""

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from secbaas.community.logger import get_logger
from secbaas.community.spi.database import DataSourcePlugin

logger = get_logger("database")


class DatabaseManager:
    """Global singleton that delegates all DB operations to a DataSourcePlugin.

    Once ``init_plugin`` is called, every session/connection request
    is forwarded to the plugin. No direct engine management happens here.
    """

    _instance: "DatabaseManager | None" = None
    _plugin: DataSourcePlugin | None = None
    _scoped_sessions: list[Session] = []

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
        if self._plugin is None:
            raise RuntimeError("Database not initialized. Call init_plugin() first.")
        session = self._plugin.orm_session().__enter__()
        self._scoped_sessions.append(session)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            self._scoped_sessions.pop()
            session.close()

    @contextmanager
    def orm_session(self) -> Generator[Session, None, None]:
        if self._scoped_sessions:
            yield self._scoped_sessions[-1]
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
