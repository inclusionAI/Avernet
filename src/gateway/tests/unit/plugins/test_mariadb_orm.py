"""Unit tests for MariaDbOrmPlugin.

Mirrors the production layout
``src/gateway/community/plugins/database/mariadb/_plugin.py``. These tests
exercise the plugin's code paths without requiring a live MariaDB server by
mocking the SQLAlchemy engine/session construction.
"""

from __future__ import annotations

import types

import pytest

from gateway.community.plugins.database.mariadb._plugin import MariaDbOrmPlugin


@pytest.fixture
def plugin() -> MariaDbOrmPlugin:
    return MariaDbOrmPlugin()


def _config(**overrides):
    from gateway.community.bootstrap import DatabaseConfig
    from gateway.community.spi.database import PluginDatabaseType

    defaults = {
        "plugin_type": PluginDatabaseType.MARIADB_ORM,
        "create_schema": True,
        "seed_data": True,
        "mariadb_host": "127.0.0.1",
        "mariadb_port": 3306,
        "mariadb_database": "mydb",
        "mariadb_user": "user",
        "mariadb_password": "pass",
    }
    defaults.update(overrides)
    return DatabaseConfig(**defaults)


class TestConstructor:
    def test_defaults_are_none(self, plugin: MariaDbOrmPlugin) -> None:
        assert plugin._database_url is None
        assert plugin._sync_engine is None
        assert plugin._sync_session_factory is None
        assert plugin._async_engine is None
        assert plugin._async_session_factory is None

    def test_with_database_url(self) -> None:
        p = MariaDbOrmPlugin("mysql+aiomysql://u:p@h:3306/db")
        assert p._database_url == "mysql+aiomysql://u:p@h:3306/db"


class TestResolveUrl:
    def test_prefers_constructor_url(self, monkeypatch) -> None:
        monkeypatch.delenv("MARIADB_DATABASE", raising=False)
        p = MariaDbOrmPlugin("mysql+aiomysql://u:p@h:3306/db")
        assert p._resolve_url(_config(mariadb_database="")) == (
            "mysql+aiomysql://u:p@h:3306/db"
        )

    def test_uses_structured_fields(self) -> None:
        p = MariaDbOrmPlugin()
        url = p._resolve_url(_config(mariadb_host="db.internal", mariadb_port=3307))
        assert url.startswith("mysql+aiomysql://")
        assert "db.internal:3307/mydb" in url

    def test_uses_non_sqlite_db_url(self) -> None:
        p = MariaDbOrmPlugin()
        url = p._resolve_url(
            _config(
                mariadb_database="",
                db_url="mysql+aiomysql://u:p@h:3306/existing_db",
            )
        )
        assert url == "mysql+aiomysql://u:p@h:3306/existing_db"

    def test_ignores_sqlite_db_url_and_raises_when_no_database(self) -> None:
        p = MariaDbOrmPlugin()
        with pytest.raises(RuntimeError, match="requires a database"):
            p._resolve_url(_config(mariadb_database="", db_url="sqlite:////tmp/x.db"))

    def test_database_url_env_precedence(self, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "mysql+aiomysql://u:p@h:3306/db")
        p = MariaDbOrmPlugin()
        assert p._resolve_url(_config()) == "mysql+aiomysql://u:p@h:3306/db"


class TestInitEngines:
    def test_builds_sync_and_async_engines(
        self, plugin: MariaDbOrmPlugin, monkeypatch
    ) -> None:
        calls: dict = {}

        def fake_create_engine(url, **kwargs):
            calls["sync_url"] = url
            return "sync-engine"

        def fake_create_async_engine(url, **kwargs):
            calls["async_url"] = url
            return "async-engine"

        monkeypatch.setattr(
            "gateway.community.plugins.database.mariadb._plugin.create_engine",
            fake_create_engine,
        )
        monkeypatch.setattr(
            "gateway.community.plugins.database.mariadb._plugin.create_async_engine",
            fake_create_async_engine,
        )

        plugin._init_engines(
            "mysql+aiomysql://user:pass@db.internal:3306/mydb?charset=utf8mb4"
        )

        assert (
            "mysql+mysqlconnector://user:pass@db.internal:3306/mydb"
            in calls["sync_url"]
        )
        assert calls["async_url"].startswith("mysql+aiomysql://")
        assert plugin._sync_engine == "sync-engine"
        assert plugin._async_engine == "async-engine"
        assert plugin._sync_session_factory is not None
        assert plugin._async_session_factory is not None


class TestSyncConnection:
    def test_raises_when_not_initialized(self, plugin: MariaDbOrmPlugin) -> None:
        with pytest.raises(RuntimeError, match="not initialized"):
            plugin.sync_connection("default")

    def test_yields_and_closes_raw_connection(self, plugin: MariaDbOrmPlugin) -> None:
        raw = _FakeRawConnection()
        plugin._sync_engine = _FakeEngine(raw)

        with plugin.sync_connection("default") as conn:
            assert conn is raw
        assert raw.closed is True


class TestOrmSession:
    def test_raises_when_not_initialized(self, plugin: MariaDbOrmPlugin) -> None:
        with pytest.raises(RuntimeError, match="not initialized"):
            with plugin.orm_session():
                pass

    def test_commits_on_success(self, plugin: MariaDbOrmPlugin) -> None:
        fake_session = _FakeSession()
        plugin._sync_session_factory = lambda: fake_session
        with plugin.orm_session() as session:
            assert session is fake_session
        assert fake_session.committed is True
        assert fake_session.closed is True

    def test_rollbacks_on_error(self, plugin: MariaDbOrmPlugin) -> None:
        fake_session = _FakeSession()
        plugin._sync_session_factory = lambda: fake_session
        with pytest.raises(RuntimeError):
            with plugin.orm_session():
                raise RuntimeError("boom")
        assert fake_session.rolledback is True
        assert fake_session.closed is True


class TestAsyncSession:
    async def test_raises_when_not_initialized(self, plugin: MariaDbOrmPlugin) -> None:
        with pytest.raises(RuntimeError, match="not initialized"):
            async for _ in plugin.session():
                pass

    async def test_commits_and_closes(self, plugin: MariaDbOrmPlugin) -> None:
        fake_session = _FakeAsyncSession()
        plugin._async_session_factory = lambda: fake_session
        async for session in plugin.session():
            assert session is fake_session
        assert fake_session.committed is True
        assert fake_session.closed is True

    async def test_rollbacks_when_commit_fails(self, plugin: MariaDbOrmPlugin) -> None:
        class BoomSession(_FakeAsyncSession):
            async def commit(self) -> None:
                raise RuntimeError("commit failed")

        fake_session = BoomSession()
        plugin._async_session_factory = lambda: fake_session
        with pytest.raises(RuntimeError, match="commit failed"):
            async for _ in plugin.session():
                pass
        assert fake_session.rolledback is True
        assert fake_session.closed is True


class TestSeed:
    def test_seed_is_noop(self, plugin: MariaDbOrmPlugin) -> None:
        # The community plugin's seed is a no-op; seeding lives in the
        # bootstrap composition root to respect plugin-not-import-core rules.
        plugin.seed(object())
        assert True


class TestCreateAll:
    def test_creates_only_gateway_owned_tables(
        self, plugin: MariaDbOrmPlugin, monkeypatch
    ) -> None:
        created_tables: list = []

        class Table:
            def __init__(self, name):
                self.name = name

            def create(self, engine, checkfirst=True):
                created_tables.append((self.name, engine, checkfirst))

        class FakeBase:
            metadata = types.SimpleNamespace(
                sorted_tables=[
                    Table("avernet_application"),
                    Table("avernet_tenant"),
                    Table("avernet_access_key_token"),
                    Table("bcs_bots"),
                ]
            )

        monkeypatch.setattr(
            "gateway.community.spi.database.Base",
            FakeBase,
        )

        plugin._sync_engine = "sync-engine"
        plugin.create_all()

        names = [t[0] for t in created_tables]
        assert names == [
            "avernet_application",
            "avernet_tenant",
            "avernet_access_key_token",
        ]
        assert "bcs_bots" not in names
        assert all(engine == "sync-engine" for _, engine, _ in created_tables)
        assert all(checkfirst is True for _, _, checkfirst in created_tables)

    def test_creates_tables(self, plugin: MariaDbOrmPlugin, monkeypatch) -> None:
        created_with: dict = {}

        class Table:
            def __init__(self, name):
                self.name = name

            def create(self, engine, checkfirst=True):
                created_with["engine"] = engine
                created_with["checkfirst"] = checkfirst

        class FakeBase:
            metadata = types.SimpleNamespace(
                sorted_tables=[Table("avernet_application"), Table("avernet_tenant")]
            )

        monkeypatch.setattr(
            "gateway.community.spi.database.Base",
            FakeBase,
        )

        plugin._sync_engine = "sync-engine"
        plugin.create_all()
        assert created_with["engine"] == "sync-engine"
        assert created_with["checkfirst"] is True

    def test_raises_when_not_initialized(self, plugin: MariaDbOrmPlugin) -> None:
        with pytest.raises(RuntimeError, match="not initialized"):
            plugin.create_all()


class TestCreateTableNoTables:
    def test_raises_when_no_owned_tables_registered(
        self, plugin: MariaDbOrmPlugin, monkeypatch
    ) -> None:
        fake_base = types.SimpleNamespace(
            metadata=types.SimpleNamespace(
                sorted_tables=[
                    _simple_table("avernet_unowned"),
                    _simple_table("other"),
                ]
            )
        )
        monkeypatch.setattr("gateway.community.spi.database.Base", fake_base)
        plugin._sync_engine = "sync-engine"
        with pytest.raises(RuntimeError, match="No gateway-owned ORM tables"):
            plugin.create_all()


def _simple_table(name: str):
    return types.SimpleNamespace(name=name)


class TestInitDatabase:
    def test_creates_schema_when_enabled(self, plugin: MariaDbOrmPlugin) -> None:
        calls: dict = {}

        def fake_resolve(config):
            return "mysql+aiomysql://u:p@h:3306/db"

        def fake_init_engines(url):
            calls["engines_url"] = url

        def fake_create_all():
            calls["create_all"] = True

        plugin._resolve_url = fake_resolve  # type: ignore[method-assign]
        plugin._init_engines = fake_init_engines  # type: ignore[method-assign]
        plugin.create_all = fake_create_all  # type: ignore[method-assign]

        plugin.init_database(_config(create_schema=True))

        assert calls["create_all"] is True
        assert calls["engines_url"] == "mysql+aiomysql://u:p@h:3306/db"

    def test_skips_schema_when_disabled(self, plugin: MariaDbOrmPlugin) -> None:
        calls: dict = {}

        def fake_resolve(config):
            return "mysql+aiomysql://u:p@h:3306/db"

        def fake_init_engines(url):
            calls["engines_url"] = url

        def boom():
            calls["create_all"] = True
            raise AssertionError("create_all should not run")

        plugin._resolve_url = fake_resolve  # type: ignore[method-assign]
        plugin._init_engines = fake_init_engines  # type: ignore[method-assign]
        plugin.create_all = boom  # type: ignore[method-assign]

        plugin.init_database(_config(create_schema=False))

        assert "create_all" not in calls
        assert calls["engines_url"] == "mysql+aiomysql://u:p@h:3306/db"


class TestClose:
    async def test_disposes_engines(self, plugin: MariaDbOrmPlugin) -> None:
        sync = _FakeSyncDisposable()
        async_eng = _FakeAsyncDisposable()
        plugin._sync_engine = sync
        plugin._async_engine = async_eng
        await plugin.close()
        assert sync.disposed is True
        assert async_eng.disposed is True

    async def test_close_with_none_engines(self, plugin: MariaDbOrmPlugin) -> None:
        await plugin.close()


# ── Test doubles ────────────────────────────────────────────────────────────


class _FakeRawConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeEngine:
    def __init__(self, raw_connection) -> None:
        self._raw = raw_connection

    def raw_connection(self):
        return self._raw


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolledback = False
        self.closed = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolledback = True

    def close(self) -> None:
        self.closed = True


class _FakeAsyncSession:
    def __init__(self) -> None:
        self.committed = False
        self.rolledback = False
        self.closed = False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolledback = True

    async def close(self) -> None:
        self.closed = True


class _FakeAsyncDisposable:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


class _FakeSyncDisposable:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True
