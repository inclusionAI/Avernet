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
    return SqliteOrmPlugin()


def _config(db_url="sqlite:///:memory:", **overrides):
    from secbaas.community.bootstrap import DatabaseConfig
    from secbaas.community.spi.database import PluginDatabaseType

    defaults = {
        "plugin_type": PluginDatabaseType.SQLITE_ORM,
        "db_url": db_url,
        "create_schema": True,
        "seed_data": True,
    }
    defaults.update(overrides)
    return DatabaseConfig(**defaults)


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

        plugin.init_database(_config())
        assert calls["create_all"] is True
        assert "seed" in calls
        assert calls["plugin"] is plugin

    def test_init_skips_schema_and_seed(
        self, plugin: SqliteOrmPlugin, monkeypatch
    ) -> None:
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

        plugin.init_database(_config(create_schema=False, seed_data=False))
        assert "boom" not in calls
        assert calls["plugin"] is plugin

    def test_reads_database_url_from_env(
        self, plugin: SqliteOrmPlugin, monkeypatch
    ) -> None:
        monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/env.db")
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

        plugin.init_database(_config(db_url="sqlite:///cfg.db"))
        assert calls["create_all"] is True


class _FakeDbManager:
    def __init__(self, records: dict) -> None:
        self._records = records

    def init_plugin(self, p) -> None:
        self._records["plugin"] = p
