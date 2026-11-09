"""LOCAL SQLite bootstrap reuses one schema snapshot per process.

``SqliteDB.bootstrap`` runs once per test on a brand-new in-memory database.
After the first real ``create_all`` it copies a cached snapshot instead; these
tests pin that the copy is the same schema, that it never overwrites a database
that already holds tables, and that a changed table layout rebuilds for real.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from agentclaw.community.core import schema
from agentclaw.community.plugins.local import database as db_mod


def _schema_rows() -> list[tuple]:
    with db_mod._engine.connect() as conn:
        return conn.execute(
            text("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name")
        ).fetchall()


def _fresh_bootstrap() -> None:
    db_mod.reset_for_tests()
    asyncio.run(db_mod.SqliteDB().bootstrap())


@pytest.fixture(autouse=True)
def _isolated_template(monkeypatch):
    monkeypatch.setattr(db_mod, "_schema_template", None)
    yield
    db_mod.reset_for_tests()


def test_snapshot_copy_matches_a_real_create_all(monkeypatch):
    _fresh_bootstrap()
    created = _schema_rows()
    assert db_mod._schema_template is not None

    calls = []
    real_create_all = schema.create_all
    monkeypatch.setattr(schema, "create_all", lambda *a, **k: calls.append(1) or real_create_all(*a, **k))
    _fresh_bootstrap()

    assert calls == []
    assert _schema_rows() == created


def test_copied_schema_keeps_foreign_keys_enforced():
    _fresh_bootstrap()
    _fresh_bootstrap()

    with db_mod._engine.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).scalar() == 1


def test_bootstrap_never_overwrites_existing_tables():
    _fresh_bootstrap()
    plugin = db_mod.SqliteDB()
    with plugin.orm_session() as session:
        session.execute(text("CREATE TABLE keep_me (id INTEGER PRIMARY KEY)"))
        session.execute(text("INSERT INTO keep_me (id) VALUES (7)"))

    asyncio.run(plugin.bootstrap())

    with plugin.session() as session:
        assert session.execute(text("SELECT id FROM keep_me")).scalar() == 7


def test_a_changed_table_layout_runs_the_real_create_all(monkeypatch):
    _fresh_bootstrap()
    monkeypatch.setattr(schema, "schema_signature", lambda: ("changed",))
    calls = []
    real_create_all = schema.create_all
    monkeypatch.setattr(schema, "create_all", lambda *a, **k: calls.append(1) or real_create_all(*a, **k))

    _fresh_bootstrap()

    assert calls == [1]
    assert db_mod._schema_template[0] == ("changed",)
