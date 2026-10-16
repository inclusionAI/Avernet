"""Shared SQLite connections must not expose another thread's transaction."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import text

from secbaas.community.plugins.database.sqlite.sqlite_orm import SqliteOrmPlugin


@pytest.mark.parametrize("rollback", [False, True])
def test_sync_sessions_isolate_thread_transactions(monkeypatch, caplog, rollback):
    caplog.set_level("DEBUG", logger="database")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    plugin = SqliteOrmPlugin(create_schema=False, seed_data=False)
    with plugin.orm_session() as session:
        session.execute(text("CREATE TABLE transaction_probe (value INTEGER)"))

    writer_ready = Event()
    release_writer = Event()
    reader_attempted = Event()
    reader_entered = Event()

    def write():
        try:
            with plugin.orm_session() as session:
                session.execute(
                    text("INSERT INTO transaction_probe (value) VALUES (:value)"),
                    {"value": 1},
                )
                writer_ready.set()
                assert release_writer.wait(5), "writer was not released"
                if rollback:
                    raise ValueError("transaction probe rollback")
        except ValueError:
            if not rollback:
                raise

    def read():
        reader_attempted.set()
        with plugin.orm_session() as session:
            reader_entered.set()
            return session.execute(
                text("SELECT COUNT(*) FROM transaction_probe")
            ).scalar()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(write)
            try:
                assert writer_ready.wait(5), "writer did not open its transaction"
                reader = pool.submit(read)
                assert reader_attempted.wait(5), "reader did not start"
                assert not reader_entered.wait(0.2), (
                    "reader entered the shared connection before writer commit/rollback"
                )
            finally:
                release_writer.set()
            writer.result(timeout=5)
            assert reader.result(timeout=5) == (0 if rollback else 1)
            if rollback:
                assert (
                    "sqlite.orm_session.rollback error_type=ValueError" in caplog.text
                )
                assert "transaction probe rollback" not in caplog.text
    finally:
        asyncio.run(plugin.close())
