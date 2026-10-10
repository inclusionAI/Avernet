"""Rebuild reads only bounded, active vector pages from durable storage."""

import pickle

from src.domain.models.vector_point import VectorPoint
from src.infra.vectorstore_backends.mysql_vector_persistence_backend import (
    MySQLVectorPersistenceBackend,
)
from src.infra.vectorstore_backends.sqlite_vector_persistence_backend import (
    SQLiteVectorPersistenceBackend,
)


class PageConnection:
    def __init__(self):
        self.rows = [
            ("a", pickle.dumps([1.0, 0.0]), "{}", False),
            ("b", pickle.dumps([0.0, 1.0]), "{}", False),
            ("c", pickle.dumps([1.0, 1.0]), "{}", True),
            ("d", pickle.dumps([0.5, 0.5]), "{}", False),
        ]
        self.queries = []
        self.page = []
        self.rollbacks = 0

    def is_connected(self):
        return True

    def cursor(self):
        return self

    def execute(self, sql, params):
        assert "is_deleted" in sql and "ORDER BY" in sql and "LIMIT %s" in sql
        self.queries.append(params)
        after, limit = (None, params[0]) if len(params) == 1 else params
        self.page = [
            row[:3]
            for row in self.rows
            if not row[3] and (after is None or row[0] > after)
        ][:limit]

    def fetchall(self):
        return self.page

    def close(self):
        pass

    def rollback(self):
        self.rollbacks += 1


def test_mysql_rebuild_pages_skip_deleted_rows_and_bound_each_query():
    connection = PageConnection()
    backend = MySQLVectorPersistenceBackend(vector_dimension=2)
    backend._conn = connection
    backend._schema_initialized = True

    batches = list(backend.iter_batches(2))

    assert [[point.id for point in batch] for batch in batches] == [["a", "b"], ["d"]]
    assert connection.queries == [(2,), ("b", 2)]
    assert connection.rollbacks == 2


def test_sqlite_rebuild_pages_without_loading_all_vectors(tmp_path):
    backend = SQLiteVectorPersistenceBackend(str(tmp_path / "vectors.sqlite"))
    try:
        backend.save_batch([
            VectorPoint(id=key, vector=[1.0, 0.0], payload={"content": key})
            for key in ("a", "b", "c")
        ])
        backend.load_all = lambda: (_ for _ in ()).throw(AssertionError("unbounded load"))

        batches = list(backend.iter_batches(2))

        assert [[point.id for point in batch] for batch in batches] == [["a", "b"], ["c"]]
    finally:
        backend._conn.close()


def test_mysql_change_replay_pages_include_tombstones():
    class ChangeConnection(PageConnection):
        def __init__(self):
            super().__init__()
            self.rows = [
                ("a", pickle.dumps([1.0, 0.0]), "{}", 0, 100.0),
                ("b", b"deleted", "{", 1, 101.0),
                ("c", pickle.dumps([0.0, 1.0]), "{}", 0, 102.0),
            ]

        def execute(self, sql, params):
            assert "ORDER BY" in sql and "LIMIT %s" in sql
            assert "is_deleted = 0" not in sql
            self.queries.append(params)
            floor = params[0]
            after = None if len(params) == 2 else (params[1], params[3])
            limit = params[-1]
            self.page = [
                row for row in self.rows
                if row[4] >= floor and (after is None or (row[4], row[0]) > after)
            ][:limit]

    connection = ChangeConnection()
    backend = MySQLVectorPersistenceBackend(vector_dimension=2)
    backend._conn = connection
    backend._schema_initialized = True

    batches = list(backend.iter_change_batches_since(100.0, 2))

    assert [[point.id for point in batch.upserts] for batch in batches] == [["a"], ["c"]]
    assert [batch.deleted_ids for batch in batches] == [["b"], []]
    assert [batch.checkpoint for batch in batches] == [101.0, 102.0]
    assert connection.queries == [(99.0, 2), (99.0, 101.0, 101.0, "b", 2)]
