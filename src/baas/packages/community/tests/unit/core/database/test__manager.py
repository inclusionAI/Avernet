"""
DatabaseManager 单元测试

测试 DatabaseManager 单例: 初始化 (layotto / config / url),
session() 上下文管理器, get_session() 异步上下文管理器,
以及 _build_engine_url, create_tables, _run_migrations, close, is_initialized 等。

使用 pytest + MagicMock 模式，不连接真实数据库。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.core.database import DatabaseManager, db_manager

# ==================== Helpers ====================


@pytest.fixture
def fresh_manager():
    """返回一个干净的单例 — 重置所有类级状态后每次测试开始干净。"""
    DatabaseManager._instance = None
    DatabaseManager._engine = None
    DatabaseManager._session_factory = None
    DatabaseManager._connection_factory = None
    DatabaseManager._zdas_manager = None
    return DatabaseManager()


def _zdas_config(
    *,
    enabled: bool = True,
    database: str = "secbaas",
    user: str = "root",
    password: str = "secret",
    host: str = "127.0.0.1",
    port: str = "3306",
) -> dict:
    return {
        "enabled": enabled,
        "datasources": [
            {
                "database": database,
                "user": user,
                "password": password,
                "host": host,
                "port": port,
            }
        ],
    }


# ==================== __new__ singleton ====================


class TestNewSingleton:
    def test_new_returns_same_instance(self, fresh_manager):
        """连续两次 __new__ 返回同一个对象。"""
        mgr2 = DatabaseManager()
        assert mgr2 is fresh_manager

    def test_new_does_not_create_duplicate(self, fresh_manager):
        """即使先显式调用 __new__，后续构造仍返回同一实例。"""
        first = DatabaseManager.__new__(DatabaseManager)
        second = DatabaseManager()
        assert first is second


# ==================== init_from_layotto ====================


class TestInitFromLayotto:
    def test_sets_zdas_manager_and_connection_factory(self, fresh_manager):
        """通过 layotto manager 初始化后 _zdas_manager / _connection_factory 被设置。"""
        zdas_mgr = MagicMock()
        zdas_mgr.get_connection = MagicMock(return_value=MagicMock())

        fresh_manager.init_from_layotto(zdas_mgr)

        assert fresh_manager._zdas_manager is zdas_mgr
        assert fresh_manager._connection_factory is not None

    def test_connection_factory_calls_get_connection_with_ds(self, fresh_manager):
        """_connection_factory 把 datasource_name 传给了 zdas_manager.get_connection。"""
        zdas_mgr = MagicMock()
        zdas_mgr.get_connection = MagicMock(return_value="conn_obj")
        fresh_manager.init_from_layotto(zdas_mgr)

        result = fresh_manager._connection_factory("my_ds")
        zdas_mgr.get_connection.assert_called_once_with("my_ds")
        assert result == "conn_obj"


# ==================== init_from_config ====================


class TestInitFromConfig:
    def test_early_return_when_not_enabled(self, fresh_manager):
        """ZDAS disabled 时不会创建 engine。"""
        cfg = {"enabled": False, "datasources": []}
        fresh_manager.init_from_config(cfg)
        assert fresh_manager._engine is None

    def test_early_return_when_no_datasources(self, fresh_manager):
        """没有数据源时不会创建 engine。"""
        cfg = {"enabled": True, "datasources": []}
        fresh_manager.init_from_config(cfg)
        assert fresh_manager._engine is None

    def test_creates_engine_and_session_factory(self, fresh_manager):
        """正常初始化会创建 engine 和 session_factory。"""
        cfg = _zdas_config()
        with (
            patch(
                "secbaas.core.database._manager.create_async_engine"
            ) as mock_create_engine,
            patch(
                "secbaas.core.database._manager.async_sessionmaker"
            ) as mock_sessionmaker,
        ):
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            mock_factory = MagicMock()
            mock_sessionmaker.return_value = mock_factory

            fresh_manager.init_from_config(cfg)

            mock_create_engine.assert_called_once()
            mock_sessionmaker.assert_called_once()

            assert fresh_manager._engine is mock_engine
            assert fresh_manager._session_factory is mock_factory

    def test_engine_url_contains_all_parts(self, fresh_manager):
        """验证传给 create_async_engine 的 URL 包含所有 ZDAS 配置项。"""
        cfg = _zdas_config(
            user="user1", password="p@ss", host="db.host", port="3307", database="mydb"
        )
        with patch(
            "secbaas.core.database._manager.create_async_engine"
        ) as mock_create_engine:
            mock_create_engine.return_value = MagicMock()
            fresh_manager.init_from_config(cfg)
            url = mock_create_engine.call_args[0][0]
            assert "mysql+aiomysql://" in url
            assert "user1:p@ss" in url
            assert "db.host:3307" in url
            assert "/mydb" in url
            assert "charset=utf8mb4" in url

    def test_default_host_and_port(self, fresh_manager):
        """未提供 host/port 时使用默认值 127.0.0.1:3306。"""
        cfg = {
            "enabled": True,
            "datasources": [{"database": "db", "user": "u", "password": "p"}],
        }
        with patch(
            "secbaas.core.database._manager.create_async_engine"
        ) as mock_create_engine:
            mock_create_engine.return_value = MagicMock()
            fresh_manager.init_from_config(cfg)
            url = mock_create_engine.call_args[0][0]
            assert "127.0.0.1:3306" in url


# ==================== init_from_url ====================


class TestInitFromUrl:
    def test_creates_engine_and_session_factory(self, fresh_manager):
        """init_from_url 创建 engine 和 session_factory。"""
        url = "mysql+aiomysql://user:pass@127.0.0.1:3306/db"
        with (
            patch(
                "secbaas.core.database._manager.create_async_engine"
            ) as mock_create_engine,
            patch(
                "secbaas.core.database._manager.async_sessionmaker"
            ) as mock_sessionmaker,
        ):
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            mock_factory = MagicMock()
            mock_sessionmaker.return_value = mock_factory

            fresh_manager.init_from_url(url)

            mock_create_engine.assert_called_once()
            mock_sessionmaker.assert_called_once()

            assert fresh_manager._engine is mock_engine
            assert fresh_manager._session_factory is mock_factory

    def test_pool_settings_differ_from_config(self, fresh_manager):
        """init_from_url 使用较小的连接池 (pool_size=5, max_overflow=10)。"""
        url = "mysql+aiomysql://u:p@127.0.0.1:3306/db"
        with patch(
            "secbaas.core.database._manager.create_async_engine"
        ) as mock_create_engine:
            mock_create_engine.return_value = MagicMock()
            fresh_manager.init_from_url(url)
            call_kwargs = mock_create_engine.call_args[1]
            assert call_kwargs.get("pool_size") == 5
            assert call_kwargs.get("max_overflow") == 10


# ==================== session() — 同步 context manager ====================


class TestSession:
    def test_yields_connection_and_commits(self, fresh_manager):
        """session() 正常执行后 commit。"""
        conn = MagicMock()
        fresh_manager._connection_factory = MagicMock(return_value=conn)
        fresh_manager._zdas_manager = MagicMock()

        with fresh_manager.session("my_ds") as yielded:
            assert yielded is conn

        conn.commit.assert_called_once()
        conn.rollback.assert_not_called()

    def test_rollback_on_exception_and_reraise(self, fresh_manager):
        """session() 内异常时 rollback，并将异常往上抛。"""
        conn = MagicMock()
        fresh_manager._connection_factory = MagicMock(return_value=conn)
        fresh_manager._zdas_manager = MagicMock()

        with pytest.raises(ValueError, match="boom"):
            with fresh_manager.session("ds"):
                raise ValueError("boom")

        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_raises_runtime_error_when_not_initialized(self, fresh_manager):
        """未初始化 (无 _connection_factory) 时调用 session() 抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="not initialized"):
            with fresh_manager.session("ds"):
                pass


# ==================== get_session() — 异步 context manager ====================


class TestGetSession:
    @pytest.mark.asyncio
    async def test_yields_session_and_commits(self, fresh_manager):
        """get_session() 正常执行后 await session.commit()。"""
        session = AsyncMock()
        fresh_manager._session_factory = MagicMock(return_value=session)

        async with fresh_manager.get_session() as yielded:
            assert yielded is session

        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
        session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rollback_on_exception_and_reraise(self, fresh_manager):
        """get_session() 内异常时 await session.rollback()，异常继续抛出。"""
        session = AsyncMock()
        fresh_manager._session_factory = MagicMock(return_value=session)

        with pytest.raises(ValueError, match="boom"):
            async with fresh_manager.get_session():
                raise ValueError("boom")

        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()
        # finally 仍执行
        session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_not_initialized(self, fresh_manager):
        """未初始化 (无 _session_factory) 时调用 get_session() 抛出 RuntimeError。"""
        with pytest.raises(RuntimeError, match="not initialized"):
            async with fresh_manager.get_session():
                pass


# ==================== close ====================


class TestClose:
    @pytest.mark.asyncio
    async def test_close_disposes_engine(self, fresh_manager):
        """close() 调用 engine.dispose()。"""
        engine = AsyncMock()
        fresh_manager._engine = engine
        await fresh_manager.close()
        engine.dispose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_does_nothing_when_no_engine(self, fresh_manager):
        """没有 engine 时 close() 不报错。"""
        await fresh_manager.close()  # no-op


# ==================== is_initialized ====================


class TestIsInitialized:
    def test_false_by_default(self, fresh_manager):
        assert fresh_manager.is_initialized is False

    def test_true_when_engine_set(self, fresh_manager):
        fresh_manager._engine = MagicMock()
        assert fresh_manager.is_initialized is True

    def test_true_when_connection_factory_set(self, fresh_manager):
        fresh_manager._connection_factory = MagicMock()
        assert fresh_manager.is_initialized is True


# ==================== global db_manager ====================


class TestGlobalDbManager:
    def test_db_manager_is_database_manager_instance(self):
        """模块级 db_manager 是 DatabaseManager 实例。"""
        assert isinstance(db_manager, DatabaseManager)

    def test_db_manager_is_singleton(self, fresh_manager):
        """db_manager 与 DatabaseManager() 新建对象是同一个单例 — 重置后重建的 2 个 DatabaseManager() 也相同。"""
        mgr_a = DatabaseManager()
        mgr_b = DatabaseManager()
        assert mgr_a is mgr_b
