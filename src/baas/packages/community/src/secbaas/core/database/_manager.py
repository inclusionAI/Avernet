"""
数据库连接管理

支持从 application.yaml 的 ZDAS 配置中读取数据库连接信息
参考 agentclaw 项目，支持 layotto zdas_manager 和直接配置两种方式
"""

from collections.abc import AsyncGenerator, Callable, Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool, QueuePool, StaticPool

from secbaas.logger import get_logger

logger = get_logger("database")
_error_logger = get_logger("orm-error")


class DatabaseManager:
    """数据库连接管理器

    支持两种初始化方式:
    1. 从 application.yaml 的 ZDAS 配置读取 (异步 SQLAlchemy)
    2. 从 layotto_manager 的 zdas_manager 获取连接 (同步)

    Also supports plugin-based initialization via init_plugin() for
    multi-backend support (ZDAS, ORM MySQL, ORM SQLite).
    """

    _instance: "DatabaseManager | None" = None
    _engine: Any = None
    _session_factory: Any = None
    _sync_engine: Engine | None = None
    _sync_session_factory: Any = None
    _connection_factory: Callable[[str], Any] | None = None
    _zdas_manager: Any = None
    _plugin: Any = None  # DataSourcePlugin instance
    _scoped_sessions: list[Session] = []  # nested scoped_orm_session stack

    def __new__(cls) -> "DatabaseManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def init_plugin(self, plugin: Any) -> None:
        """Initialize from a DataSourcePlugin instance.

        All session/sync_connection/orm_session calls delegate to
        the plugin when one is set. This enables multi-backend
        support (ZDAS_ORM, SQLITE_ORM) via PLUGIN_DATABASE.

        Args:
            plugin: A DataSourcePlugin-conforming instance.
        """
        logger.info("=== DatabaseManager.init_plugin ===")
        logger.info(f"  plugin class: {type(plugin).__name__}")
        logger.info(f"  plugin module: {type(plugin).__module__}")
        logger.info(f"  has sync_connection: {hasattr(plugin, 'sync_connection')}")
        logger.info(f"  has session (async): {hasattr(plugin, 'session')}")
        logger.info(f"  has orm_session: {hasattr(plugin, 'orm_session')}")
        logger.info(f"  has close: {hasattr(plugin, 'close')}")

        self._plugin = plugin
        logger.info(f"DatabaseManager: plugin set — type={type(plugin).__name__}")

        try:
            if hasattr(plugin, "session"):
                logger.info("  session interface: OK")
            if hasattr(plugin, "sync_connection"):
                logger.info("  sync_connection interface: OK")
            if hasattr(plugin, "orm_session"):
                logger.info("  orm_session interface: OK")
        except Exception as e:
            logger.error(f"Plugin interface validation failed: {e}", exc_info=True)
            raise

        logger.info("=== DatabaseManager.init_plugin: DONE ===")

    def init_from_layotto(self, zdas_manager: Any) -> None:
        """
        从 layotto_manager 的 zdas_manager 初始化

        Args:
            zdas_manager: layotto_manager.zdas_manager 实例
        """
        logger.info("Initializing database from layotto zdas_manager")
        self._zdas_manager = zdas_manager
        self._connection_factory = lambda ds: zdas_manager.get_connection(ds)
        logger.info("Database connection initialized from layotto successfully")

    def init_from_config(self, zdas_config: dict[str, Any]) -> None:
        """
        从 ZDAS 配置初始化数据库连接 (异步 SQLAlchemy)

        Args:
            zdas_config: application.yaml 中的 zdas 配置
        """
        if not zdas_config.get("enabled", False):
            logger.warning(
                "ZDAS is not enabled, database connection will not be initialized"
            )
            return

        datasources = zdas_config.get("datasources", [])
        if not datasources:
            logger.warning("No datasources configured in ZDAS")
            return

        # 使用第一个数据源
        ds = datasources[0]
        database = ds.get("database", "")
        user = ds.get("user", "")
        password = ds.get("password", "")
        host = ds.get("host", "127.0.0.1")
        port = ds.get("port", "3306")

        # 构建异步 MySQL 连接 URL
        url = f"mysql+aiomysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"

        logger.info(f"Initializing database connection to {host}:{port}/{database}")

        self._engine = create_async_engine(
            url,
            echo=False,
            poolclass=QueuePool,
            pool_size=10,
            max_overflow=20,
        )

        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        logger.info("Database connection initialized successfully")

    def init_from_url(self, database_url: str) -> None:
        """
        直接从数据库 URL 初始化

        Args:
            database_url: 数据库连接URL, e.g., mysql+aiomysql://user:pass@host:port/db
        """
        logger.info("Initializing database connection from URL")

        self._engine = create_async_engine(
            database_url,
            echo=False,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
        )

        self._session_factory = async_sessionmaker(
            bind=self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        logger.info("Database connection initialized successfully")

    @contextmanager
    def session(self, datasource_name: str = "default") -> Any:
        if self._plugin is not None:
            logger.debug(
                f"Delegating sync connection to plugin: {type(self._plugin).__name__}"
            )
            try:
                with self._plugin.sync_connection(datasource_name) as conn:
                    yield conn
                return
            except Exception:
                logger.error(
                    f"Plugin sync_connection failed: plugin={type(self._plugin).__name__}, "
                    f"datasource={datasource_name}",
                    exc_info=True,
                )
                raise

        if self._connection_factory is None:
            raise RuntimeError(
                "Database not initialized. Call init_from_layotto() first."
            )

        conn = self._connection_factory(datasource_name)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        if self._plugin is not None:
            logger.debug(
                f"Delegating async session to plugin: {type(self._plugin).__name__}"
            )
            try:
                async for session in self._plugin.session():
                    yield session
                return
            except Exception:
                logger.error(
                    f"Plugin async session failed: plugin={type(self._plugin).__name__}",
                    exc_info=True,
                )
                raise

        if self._session_factory is None:
            raise RuntimeError(
                "Database not initialized. Call init_from_config() first."
            )

        session = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def close(self) -> None:
        """Close database connections"""
        if self._plugin is not None:
            await self._plugin.close()
            logger.info("DatabaseManager: plugin connections closed")
            return
        if self._engine:
            await self._engine.dispose()
            logger.info("Database connection closed")
        if self._sync_engine:
            self._sync_engine.dispose()
            logger.info("Sync database connection closed")

    @contextmanager
    def scoped_orm_session(self) -> Generator[Session, None, None]:
        session = None
        if self._plugin is not None:
            session = self._plugin.orm_session().__enter__()
        elif self._sync_session_factory is not None:
            session = self._sync_session_factory()
        else:
            raise RuntimeError("Sync ORM session not initialized.")

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

        if self._plugin is not None:
            logger.debug(
                f"Delegating ORM session to plugin: {type(self._plugin).__name__}"
            )
            try:
                with self._plugin.orm_session() as session:
                    yield session
                return
            except Exception:
                _error_logger.error(
                    f"Plugin ORM session failed: plugin={type(self._plugin).__name__}",
                    exc_info=True,
                )
                raise

        if self._sync_session_factory is None:
            raise RuntimeError(
                "Sync ORM session not initialized. Call init_orm_session() first."
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

    def init_orm_session(self, sync_database_url: str) -> None:
        """Initialize sync ORM session factory.

        Args:
            sync_database_url: Sync database URL.
                e.g. mysql+mysqlconnector://user:pass@host:port/db?charset=utf8mb4
        """
        logger.info("Initializing sync ORM session factory")
        self._sync_engine = create_engine(
            sync_database_url,
            echo=False,
            poolclass=NullPool,
        )
        self._sync_session_factory = sessionmaker(
            bind=self._sync_engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        logger.info("Sync ORM session factory initialized successfully")

    def init_orm_session_from_connection(self, connection: Any) -> None:
        """Initialize sync ORM session factory using an existing raw connection.

        Binds the ORM session to the provided mysql.connector connection,
        avoiding URL-parsing issues with special characters in credentials
        (e.g. Layotto zdas_manager user:password format).

        Args:
            connection: An existing raw mysql.connector connection object.
        """
        from sqlalchemy import create_engine

        # Create a connection pool that always returns the same connection
        self._sync_engine = create_engine(
            "mysql+mysqlconnector://",
            creator=lambda: connection,
            poolclass=StaticPool,
            echo=False,
        )
        self._sync_session_factory = sessionmaker(
            bind=self._sync_engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        logger.info("Sync ORM session factory initialized from raw connection")

    @property
    def is_initialized(self) -> bool:
        """检查数据库是否已初始化"""
        return self._engine is not None or self._connection_factory is not None


# 全局数据库管理器实例
db_manager = DatabaseManager()
