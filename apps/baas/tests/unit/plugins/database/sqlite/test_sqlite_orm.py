"""Unit tests for SqliteOrmPlugin.init_database.

Mirrors the production layout ``src/secbaas/community/plugins/database/sqlite/``.
Exercises the ``create_schema`` / ``seed_data`` branches in ``init_database``.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from secbaas.community.plugins.database.sqlite.sqlite_orm import SqliteOrmPlugin


@pytest.fixture
def plugin() -> SqliteOrmPlugin:
    return SqliteOrmPlugin(create_schema=True, seed_data=True)


class TestInitDatabase:
    def test_full_init(self, plugin: SqliteOrmPlugin, monkeypatch) -> None:
        calls: dict = {}

        @contextmanager
        def fake_orm_session():
            yield object()

        monkeypatch.setattr(
            plugin, "create_all", lambda: calls.setdefault("create_all", True)
        )
        monkeypatch.setattr(plugin, "seed", lambda s: calls.setdefault("seed", s))
        monkeypatch.setattr(plugin, "orm_session", fake_orm_session)

        monkeypatch.setattr(
            "secbaas.community.core.database.db_manager",
            _FakeDbManager(calls),
        )

        plugin.init_database()
        assert calls["create_all"] is True
        assert "seed" in calls
        assert calls["plugin"] is plugin

    def test_init_skips_schema_and_seed(self, monkeypatch) -> None:
        plugin = SqliteOrmPlugin(create_schema=False, seed_data=False)
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

    def test_reads_database_url_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/env.db")
        plugin = SqliteOrmPlugin(create_schema=True, seed_data=True)
        calls: dict = {}

        @contextmanager
        def fake_orm_session():
            yield object()

        monkeypatch.setattr(
            plugin, "create_all", lambda: calls.setdefault("create_all", True)
        )
        monkeypatch.setattr(plugin, "seed", lambda s: calls.setdefault("seed", s))
        monkeypatch.setattr(plugin, "orm_session", fake_orm_session)
        monkeypatch.setattr(
            "secbaas.community.core.database.db_manager",
            _FakeDbManager(calls),
        )

        plugin.init_database()
        assert calls["create_all"] is True


class _FakeDbManager:
    def __init__(self, records: dict) -> None:
        self._records = records

    def init_plugin(self, p) -> None:
        self._records["plugin"] = p


def test_create_all_upgrades_existing_sqlite_file(tmp_path, monkeypatch):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import closing

    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = tmp_path / "baas.db"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.executescript("""
            CREATE TABLE baas_bot_run (run_id TEXT PRIMARY KEY, status TEXT, error TEXT);
            INSERT INTO baas_bot_run VALUES ('old-run', 'FAILED', 'existing error');
        """)

    first = SqliteOrmPlugin(f"sqlite:///{db_path}", seed_data=False)
    first.create_all()
    # Simulate two workers upgrading the same fully provisioned old database.
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("ALTER TABLE baas_bot_run DROP COLUMN error_code")
    second = SqliteOrmPlugin(f"sqlite:///{db_path}", seed_data=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(plugin.create_all) for plugin in (first, second)]
        for job in jobs:
            job.result(timeout=15)
    first.create_all()  # another startup must be a no-op
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT * FROM baas_bot_run").fetchall() == [
            ("old-run", "FAILED", "existing error", None)
        ]
        cols = connection.execute("PRAGMA table_info(baas_bot_run)").fetchall()
        assert [(c[2], c[3], c[4]) for c in cols if c[1] == "error_code"] == [
            ("TEXT", 0, None)
        ]


def test_create_all_serializes_concurrent_cold_boot(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import event, inspect

    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = f"sqlite:///{tmp_path / 'cold.db'}"
    plugins = [SqliteOrmPlugin(url, seed_data=False) for _ in range(2)]
    start = Barrier(len(plugins))

    def boot(plugin):
        statements = []

        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(plugin._sync_engine, "before_cursor_execute", record)
        start.wait(timeout=5)
        plugin.create_all()
        return statements

    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(boot, plugin) for plugin in plugins]
        for job in jobs:
            # Serialize even create_all's checkfirst reads, not just ALTER.
            assert job.result(timeout=15)[0] == "BEGIN IMMEDIATE"

    columns = inspect(plugins[0]._sync_engine).get_columns("baas_bot_run")
    assert [
        column["nullable"] for column in columns if column["name"] == "error_code"
    ] == [True]
