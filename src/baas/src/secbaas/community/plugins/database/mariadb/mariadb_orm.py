"""MariaDB ORM plugin — SQLAlchemy with the MariaDB/MySQL dialect.

Wire up as ``plugins.database.plugin_database: MARIADB_ORM`` with the three
``database_url`` / ``database_user`` / ``database_passwd`` settings, where
``database_url`` is a plain ``host:port/database`` string and credentials are
supplied separately. Uses ``mysql+aiomysql`` for the async path and
``mysql+mysqlconnector`` for the sync ORM path — MariaDB is wire-compatible
with the MySQL protocol, so the same driver dialects used by
``core/database/_manager.py`` apply.

Runtime env injection (e.g. ``${DATABASE_URL}`` in YAML) is owned by
``ConfigLoader._expand_env_placeholders``; this plugin never reads
``os.environ`` directly (AGENTS.md: raw env access belongs to config loading).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from secbaas.community.logger import get_logger
from secbaas.community.spi.database import DataSourcePlugin

logger = get_logger("database")


class MariaDbOrmPlugin(DataSourcePlugin):
    def __init__(
        self,
        database_url: str = "",
        *,
        create_schema: bool = True,
        seed_data: bool = True,
    ) -> None:
        """Create a MariaDB plugin.

        Args:
            database_url: Full ``mysql+aiomysql://user:pass@host:port/db?charset=utf8mb4``
                URL. If empty, engines are not built (``init_database`` will raise).
            create_schema: Whether to run ``create_all()`` on ``init_database``.
            seed_data: Whether to run ``seed()`` on ``init_database``.
        """
        self._database_url = database_url
        self._create_schema = create_schema
        self._seed_data = seed_data
        self._sync_engine: Engine | None = None
        self._async_engine = None
        self._sync_session_factory = None
        self._async_session_factory = None
        logger.info("MariaDbOrmPlugin constructed (engines created on init_database)")

    def _init_engines(self, async_url: str) -> None:
        """Build sync (mysqlconnector) and async (aiomysql) engines.

        The sync URL is derived from the async URL host/port/database by
        swapping the dialect.  The original user/password components are reused
        to avoid re-encoding credentials.
        """
        from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

        parts = urlsplit(async_url)
        query = parse_qs(parts.query)
        query["charset"] = ["utf8mb4"]
        sync_url = urlunsplit(
            (
                "mysql+mysqlconnector",
                parts.netloc,
                parts.path,
                urlencode(query, doseq=True),
                parts.fragment,
            )
        )

        self._sync_engine = create_engine(sync_url, echo=False, pool_pre_ping=True)
        self._sync_session_factory = sessionmaker(
            bind=self._sync_engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )

        self._async_engine = create_async_engine(
            async_url,
            echo=False,
            pool_pre_ping=True,
        )
        self._async_session_factory = async_sessionmaker(
            bind=self._async_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info("MariaDbOrmPlugin engines initialized")

    def create_all(self) -> None:
        pass

    def sync_connection(self, datasource_name: str) -> AbstractContextManager[Any]:
        if self._sync_engine is None:
            raise RuntimeError(
                "MariaDB plugin not initialized — call init_database first"
            )

        @contextmanager
        def _conn() -> AbstractContextManager[Any]:
            raw = self._sync_engine.raw_connection()
            try:
                yield raw
            finally:
                raw.close()

        return _conn()

    @contextmanager
    def orm_session(self) -> Generator[Session, None, None]:
        if self._sync_session_factory is None:
            raise RuntimeError(
                "MariaDB plugin not initialized — call init_database first"
            )
        session = self._sync_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    async def session(self) -> AsyncIterator[AsyncSession]:
        if self._async_session_factory is None:
            raise RuntimeError(
                "MariaDB plugin not initialized — call init_database first"
            )
        session = self._async_session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    def seed(self, session: Session) -> None:
        pass

    def init_database(self) -> None:
        """Build engines, create schema, seed data, and register with db_manager.

        Uses the URL and flags resolved at construction time. Called by
        ``DatabaseManagerLifecycle.start()`` after the plugin is resolved
        from the DI container.
        """
        if not self._database_url:
            raise RuntimeError(
                "MariaDB backend requires database_url "
                "(format: host:port/database or full mysql+...:// URL)"
            )
        self._init_engines(self._database_url)

        if self._create_schema:
            self.create_all()
        else:
            logger.info(
                "MariaDbOrmPlugin: schema creation disabled (create_schema=false)"
            )

        if self._seed_data:
            with self.orm_session() as session:
                self.seed(session)
        else:
            logger.info("MariaDbOrmPlugin: seed disabled (seed_data=false)")

        from secbaas.community.core.database import db_manager

        db_manager.init_plugin(self)
        logger.info(
            "init_database: MariaDbOrmPlugin registered with db_manager (database=%s)",
            self._resolve_database_label(self._database_url),
        )

    @staticmethod
    def _resolve_database_label(async_url: str) -> str:
        """Return a log-safe label (host/database only, never credentials)."""
        from urllib.parse import urlsplit

        parts = urlsplit(async_url)
        hostport = parts.netloc.rsplit("@", 1)[-1]
        return f"{hostport}{parts.path}"

    async def close(self) -> None:
        if self._sync_engine:
            self._sync_engine.dispose()
        if self._async_engine:
            await self._async_engine.dispose()
        logger.info("MariaDbOrmPlugin: engines disposed")
