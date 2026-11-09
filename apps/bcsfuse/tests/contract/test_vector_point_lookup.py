"""Single-point reads must use the durable ID key without scanning vectors."""

import json
import pickle
from unittest.mock import MagicMock

import pytest
from app.domain.models.vector_point import VectorPoint
from app.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)
from app.infra.vectorstore_backends.mysql_vector_persistence_backend import (
    MySQLVectorPersistenceBackend,
)
from app.infra.vectorstore_backends.sqlite_vector_persistence_backend import (
    SQLiteVectorPersistenceBackend,
)


def mysql_backend(row):
    backend = MySQLVectorPersistenceBackend()
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.fetchone.return_value = row
    cursor.fetchall.side_effect = AssertionError("point lookup must not fetch all rows")
    backend._conn = connection
    return backend, connection, cursor


@pytest.mark.parametrize("present", [False, True])
def test_mysql_point_lookup_uses_exact_id_and_excludes_deleted(present):
    point = VectorPoint(id="bot_%:owner:default:full", vector=[1.0, 0.0], payload={"content": "old"})
    row = (point.id, pickle.dumps(point.vector), json.dumps(point.payload)) if present else None
    backend, connection, cursor = mysql_backend(row)

    result = backend.load_by_id(point.id)

    assert result == (point if present else None)
    sql, params = cursor.execute.call_args.args
    assert "`id` = %s" in sql and "`is_deleted` = 0" in sql
    assert "LIKE" not in sql
    assert params == (point.id,)
    cursor.close.assert_called_once()
    connection.rollback.assert_called_once()


def test_sqlite_consumer_reads_only_requested_point(monkeypatch, tmp_path):
    backend = SQLiteVectorPersistenceBackend()
    target = VectorPoint(id="bot:owner:default:full", vector=[1.0, 0.0], payload={"content": "target"})
    backend.save_batch([target, VectorPoint(id="other", vector=[0.0, 1.0])])

    def forbid_full_scan():
        raise AssertionError("get(id) must not load the entire vector table")

    monkeypatch.setattr(backend, "load_all", forbid_full_scan)
    store = QdrantMySQLVectorStore(dimension=2, qdrant_path=str(tmp_path / "qdrant"), persistence_backend=backend)
    try:
        assert store.get(target.id) == target
        assert store.get("missing") is None
    finally:
        store.close()


def test_mysql_consumer_preserves_decode_failure(monkeypatch, tmp_path):
    backend, _, _ = mysql_backend(("bad", b"invalid", "{}"))
    monkeypatch.setattr(backend, "load_all", lambda: pytest.fail("unexpected full scan"))
    store = QdrantMySQLVectorStore(qdrant_path=str(tmp_path / "qdrant"), persistence_backend=backend)
    try:
        with pytest.raises(RuntimeError, match="Failed to decode persisted vector bad"):
            store.get("bad")
    finally:
        store.close()


def test_legacy_provider_keeps_existing_get_fallback(tmp_path):
    point = VectorPoint(id="legacy", vector=[1.0, 0.0])

    class LegacyBackend:
        def load_all(self):
            return [point]

    store = QdrantMySQLVectorStore(qdrant_path=str(tmp_path / "qdrant"), persistence_backend=LegacyBackend())
    try:
        assert store.get("legacy") == point
        assert store.get("missing") is None
    finally:
        store.close()
