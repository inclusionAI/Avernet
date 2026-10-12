"""Corrupt durable rows must not be acknowledged by rebuild or replay."""

import pickle

import pytest

from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)
from src.infra.vectorstore_backends.mysql_vector_persistence_backend import (
    MySQLVectorPersistenceBackend,
)


class ReadConnection:
    def __init__(self, rows):
        self.rows = rows
        self.closed_reads = 0
        self.rollbacks = 0
        self.parameters = []

    def is_connected(self):
        return True

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        self.parameters.append(params)

    def fetchall(self):
        return self.rows

    def close(self):
        self.closed_reads += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.mark.parametrize("corrupt_field", ["vector", "payload"])
def test_failed_decode_keeps_sync_checkpoint_and_retries_complete_batch(
    tmp_path, corrupt_field
):
    valid = pickle.dumps([1.0, 0.0])
    broken = b"invalid vector" if corrupt_field == "vector" else valid
    payload = "{" if corrupt_field == "payload" else '{"content":"target"}'
    connection = ReadConnection(
        [
            ("deleted", b"invalid", "{", 1, 101.0),
            ("target", broken, payload, 0, 102.0),
            ("later", valid, '{"content":"later"}', 0, 110.0),
        ]
    )
    backend = MySQLVectorPersistenceBackend(vector_dimension=2)
    backend._conn = connection
    backend._schema_initialized = True
    store = QdrantMySQLVectorStore(
        dimension=2, qdrant_path=str(tmp_path / "vectors"), persistence_backend=backend
    )
    store._last_sync_time = 100.0
    try:
        with pytest.raises(RuntimeError, match="target"):
            store.sync_incremental()
        assert store._last_sync_time == 100.0
        assert store.get_vector_ids() == []
        assert connection.closed_reads == connection.rollbacks == 1
        connection.rows[1] = ("target", valid, '{"content":"target"}', 0, 102.0)
        assert store.sync_incremental() == {"upserted": 2, "deleted": 1}
        assert connection.parameters == [(99.0,), (99.0,)]
        assert store._last_sync_time == 110.0
        assert set(store.get_vector_ids()) == {"target", "later"}
        assert [hit.id for hit in store.text_search("target", top_k=5)] == ["target"]
        assert connection.closed_reads == connection.rollbacks == 2
    finally:
        store.close()


@pytest.mark.parametrize("corrupt_field", ["vector", "payload"])
def test_snapshot_does_not_silently_skip_corrupt_rows(corrupt_field):
    valid = pickle.dumps([1.0, 0.0])
    connection = ReadConnection(
        [
            (
                "bad",
                b"invalid" if corrupt_field == "vector" else valid,
                "{" if corrupt_field == "payload" else "{}",
            ),
        ]
    )
    backend = MySQLVectorPersistenceBackend()
    backend._conn = connection
    backend._schema_initialized = True
    with pytest.raises(RuntimeError, match="bad"):
        backend.load_all()
    assert connection.closed_reads == connection.rollbacks == 1
