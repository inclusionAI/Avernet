"""Unit tests for MariaDbOrmPlugin.

Mirrors the production layout ``src/secbaas/community/plugins/database/mariadb/``.
These tests exercise the plugin's code paths without requiring a live MariaDB
server by mocking the SQLAlchemy engine/session construction.
"""

from __future__ import annotations

import types
from contextlib import contextmanager

import pytest

from secbaas.community.plugins.database.mariadb.mariadb_orm import (
    MariaDbOrmPlugin,
)


@pytest.fixture
def plugin() -> MariaDbOrmPlugin:
    return MariaDbOrmPlugin()


class TestConstructor:
    def test_defaults_are_empty(self, plugin: MariaDbOrmPlugin) -> None:
        assert plugin._database_url == ""
        assert plugin._sync_engine is None
        assert plugin._sync_session_factory is None
        assert plugin._async_engine is None
        assert plugin._async_session_factory is None

    def test_with_full_url(self) -> None:
        p = MariaDbOrmPlugin("mysql+aiomysql://u:p@h:3306/db?charset=utf8mb4")
        assert p._database_url == "mysql+aiomysql://u:p@h:3306/db?charset=utf8mb4"

    def test_ignores_sqlite_database_url_env(self, monkeypatch) -> None:
        """WR-04: a SQLite DATABASE_URL (the exact env var the SQLite plugin
        consumes) must not be picked up by MariaDbOrmPlugin — the plugin only
        uses the explicit ``database_url`` constructor arg, never the env var."""
        monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/leaked.db")
        p = MariaDbOrmPlugin()
        assert p._database_url == ""
        with pytest.raises(RuntimeError, match="database_url"):
            p.init_database()


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
            "secbaas.community.plugins.database.mariadb.mariadb_orm.create_engine",
            fake_create_engine,
        )
        monkeypatch.setattr(
            "secbaas.community.plugins.database.mariadb.mariadb_orm.create_async_engine",
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
    def test_calls_seed_database(self, plugin: MariaDbOrmPlugin, monkeypatch) -> None:
        called: dict = {}

        def fake_seed_database(session):
            called["session"] = session

        monkeypatch.setattr(
            "secbaas.community.plugins.database.seed.seed_database",
            fake_seed_database,
        )
        session = object()
        plugin.seed(session)
        assert called["session"] is session


class TestCreateAll:
    def test_creates_tables(self, plugin: MariaDbOrmPlugin, monkeypatch) -> None:
        created_with: dict = {}
        engine = _FakeSyncEngine()

        class Table:
            def __init__(self, name: str) -> None:
                self.name = name

        class FakeMetadata:
            sorted_tables = [Table("baas_bot"), Table("baas_tenant")]

            def create_all(self, engine, tables=None, checkfirst=True):
                created_with["engine"] = engine
                created_with["tables"] = tables

        class FakeBase:
            metadata = FakeMetadata()

        monkeypatch.setattr(
            "secbaas.community.spi.database.Base",
            FakeBase,
        )

        plugin._sync_engine = engine
        plugin.create_all()
        assert created_with["engine"] is engine
        assert [t.name for t in created_with["tables"]] == [
            "baas_bot",
            "baas_tenant",
        ]

    def test_creates_only_baas_owned_tables(
        self, plugin: MariaDbOrmPlugin, monkeypatch
    ) -> None:
        """Backend-owned `ac_*` tables are never created by BAAS."""
        created_with: dict = {}

        class Table:
            def __init__(self, name: str) -> None:
                self.name = name

        class FakeMetadata:
            sorted_tables = [
                Table("ac_bots"),
                Table("ac_bot_publish"),
                Table("ac_entity_device_binding"),
                Table("ac_lock_table"),
                Table("baas_bot"),
            ]

            def create_all(self, engine, tables=None, checkfirst=True):
                created_with["tables"] = tables

        class FakeBase:
            metadata = FakeMetadata()

        monkeypatch.setattr(
            "secbaas.community.spi.database.Base",
            FakeBase,
        )

        plugin._sync_engine = _FakeSyncEngine()
        plugin.create_all()

        names = sorted(t.name for t in created_with["tables"])
        assert names == ["ac_lock_table", "baas_bot"]

    def test_raises_when_named_lock_cannot_be_acquired(
        self, plugin: MariaDbOrmPlugin, monkeypatch
    ) -> None:
        class Table:
            def __init__(self, name: str) -> None:
                self.name = name

        class FakeMetadata:
            sorted_tables = [Table("baas_bot")]

            def create_all(self, engine, tables=None, checkfirst=True):
                raise AssertionError("must not create tables without the lock")

        class FakeBase:
            metadata = FakeMetadata()

        monkeypatch.setattr(
            "secbaas.community.spi.database.Base",
            FakeBase,
        )

        engine = _FakeSyncEngine(acquire_result=0)
        plugin._sync_engine = engine
        with pytest.raises(RuntimeError, match="could not acquire named lock"):
            plugin.create_all()
        assert not any("RELEASE_LOCK" in s for s in engine.connection.statements)
        assert engine.connection.closed is True

    def test_releases_lock_after_creation(
        self, plugin: MariaDbOrmPlugin, monkeypatch
    ) -> None:
        class Table:
            def __init__(self, name: str) -> None:
                self.name = name

        class FakeMetadata:
            sorted_tables = [Table("baas_bot")]

            def create_all(self, engine, tables=None, checkfirst=True):
                pass

        class FakeBase:
            metadata = FakeMetadata()

        monkeypatch.setattr(
            "secbaas.community.spi.database.Base",
            FakeBase,
        )

        engine = _FakeSyncEngine()
        plugin._sync_engine = engine
        plugin.create_all()
        assert any("GET_LOCK" in s for s in engine.connection.statements)
        assert any("RELEASE_LOCK" in s for s in engine.connection.statements)
        assert engine.connection.closed is True

    def test_tolerates_already_exists_race(
        self, plugin: MariaDbOrmPlugin, monkeypatch
    ) -> None:
        class Table:
            def __init__(self, name: str) -> None:
                self.name = name

        class FakeMetadata:
            sorted_tables = [Table("baas_bot")]

            def create_all(self, engine, tables=None, checkfirst=True):
                raise RuntimeError("(1050, \"Table 'baas_bot' already exists\")")

        class FakeBase:
            metadata = FakeMetadata()

        monkeypatch.setattr(
            "secbaas.community.spi.database.Base",
            FakeBase,
        )

        engine = _FakeSyncEngine()
        plugin._sync_engine = engine
        plugin.create_all()
        assert any("RELEASE_LOCK" in s for s in engine.connection.statements)

    def test_propagates_unrelated_creation_errors(
        self, plugin: MariaDbOrmPlugin, monkeypatch
    ) -> None:
        class Table:
            def __init__(self, name: str) -> None:
                self.name = name

        class FakeMetadata:
            sorted_tables = [Table("baas_bot")]

            def create_all(self, engine, tables=None, checkfirst=True):
                raise RuntimeError("Access denied for user 'baas'@'%'")

        class FakeBase:
            metadata = FakeMetadata()

        monkeypatch.setattr(
            "secbaas.community.spi.database.Base",
            FakeBase,
        )

        engine = _FakeSyncEngine()
        plugin._sync_engine = engine
        with pytest.raises(RuntimeError, match="Access denied"):
            plugin.create_all()
        assert engine.connection.closed is True


class TestInitDatabase:
    def test_full_init(self, monkeypatch) -> None:
        plugin = MariaDbOrmPlugin(
            "mysql+aiomysql://u:p@h:3306/db",
            create_schema=True,
            seed_data=True,
        )
        monkeypatch.setattr(plugin, "_init_engines", lambda url: None)
        calls: dict = {}

        @contextmanager
        def fake_orm_session():
            yield _FakeSession()

        monkeypatch.setattr(plugin, "orm_session", fake_orm_session)
        monkeypatch.setattr(
            plugin, "create_all", lambda: calls.setdefault("create_all", True)
        )
        monkeypatch.setattr(plugin, "seed", lambda s: calls.setdefault("seed", s))

        db_manager_records: dict = {}
        monkeypatch.setattr(
            "secbaas.community.core.database.db_manager",
            _FakeDbManager(db_manager_records),
        )

        plugin.init_database()
        assert calls["create_all"] is True
        assert "seed" in calls
        assert db_manager_records["plugin"] is plugin

    def test_init_skips_schema_and_seed(self, monkeypatch) -> None:
        plugin = MariaDbOrmPlugin(
            "mysql+aiomysql://u:p@h:3306/db",
            create_schema=False,
            seed_data=False,
        )
        monkeypatch.setattr(plugin, "_init_engines", lambda url: None)
        calls: dict = {}

        def boom(*a, **k):
            calls["boom"] = True
            raise AssertionError("should not be called")

        monkeypatch.setattr(plugin, "create_all", boom)
        monkeypatch.setattr(plugin, "seed", boom)
        monkeypatch.setattr(plugin, "orm_session", boom)

        monkeypatch.setattr(
            "secbaas.community.core.database.db_manager",
            _FakeDbManager(calls),
        )

        plugin.init_database()
        assert "boom" not in calls
        assert calls["plugin"] is plugin


class TestClose:
    async def test_disposes_engines(self, plugin: MariaDbOrmPlugin) -> None:
        sync = _FakeSyncDisposable()
        async_eng = _FakeDisposable()
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
        self.disposed = False

    def raw_connection(self):
        return self._raw

    def dispose(self) -> None:
        self.disposed = True


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


class _FakeDisposable:
    def __init__(self, async_dispose: bool = False) -> None:
        self.disposed = False
        self._async_dispose = async_dispose

    async def dispose(self) -> None:
        self.disposed = True


class _FakeSyncDisposable:
    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class _FakeDbManager:
    def __init__(self, records: dict) -> None:
        self._records = records

    def init_plugin(self, p) -> None:
        self._records["plugin"] = p


class _FakeLockConnection:
    def __init__(self, acquire_result=1) -> None:
        self.statements: list[str] = []
        self.closed = False
        self._acquire_result = acquire_result

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        value = self._acquire_result if "GET_LOCK" in sql else None
        return types.SimpleNamespace(scalar=lambda: value)

    def close(self) -> None:
        self.closed = True


class _FakeSyncEngine:
    def __init__(self, acquire_result=1) -> None:
        self.connection = _FakeLockConnection(acquire_result)

    def connect(self):
        return self.connection
