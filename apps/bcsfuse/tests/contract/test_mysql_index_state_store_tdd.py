"""Startup and failover contracts for the production index-state store."""

from __future__ import annotations


def test_mysql_index_state_store_construction_is_lazy(monkeypatch) -> None:
    """A MySQL blip must not prevent composition or index-owner construction."""
    from app.infra.public.stores.mysql_index_state_store import (
        MySQLIndexStateStore,
    )

    original = MySQLIndexStateStore._ensure_schema
    called: list[str] = []

    def fail_if_initialized(*args: object, **kwargs: object) -> None:
        called.append("schema")
        raise AssertionError("composition must not open the index-state database")

    monkeypatch.setattr(MySQLIndexStateStore, "_ensure_schema", fail_if_initialized)
    try:
        MySQLIndexStateStore(connection_pool=object())
    finally:
        monkeypatch.setattr(MySQLIndexStateStore, "_ensure_schema", original)

    assert called == []
