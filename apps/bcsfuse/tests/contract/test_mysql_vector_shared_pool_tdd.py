"""TDD contracts: durable vector persistence must use the shared MySQL pool."""

from __future__ import annotations

from types import SimpleNamespace


class FakeCursor:
    def __init__(self) -> None:
        self.closed = False
        self.executed: list[str] = []

    def execute(self, sql: str, params: object = ()) -> None:
        self.executed.append(sql)

    def close(self) -> None:
        self.closed = True


class FakePooledConnection:
    def __init__(self) -> None:
        self.cursor_obj = FakeCursor()
        self.close_count = 0
        self.connected = True

    def cursor(self) -> FakeCursor:
        return self.cursor_obj

    def is_connected(self) -> bool:
        return self.connected

    def commit(self) -> None:
        self.commit_count = getattr(self, "commit_count", 0) + 1

    def close(self) -> None:
        self.close_count += 1
        self.connected = False


class FakePool:
    def __init__(self) -> None:
        self.connection = FakePooledConnection()
        self.get_connection_count = 0

    def get_connection(self) -> FakePooledConnection:
        self.get_connection_count += 1
        return self.connection


def test_vector_backend_constructor_does_not_borrow_connection() -> None:
    from app.infra.vectorstore_backends.mysql_vector_persistence_backend import (
        MySQLVectorPersistenceBackend,
    )

    pool = FakePool()
    backend = MySQLVectorPersistenceBackend(connection_pool=pool)

    assert backend._connection_pool is pool
    assert pool.get_connection_count == 0
    assert backend._conn is None


def test_vector_backend_first_operation_uses_shared_pool_and_lazy_schema() -> None:
    from app.infra.vectorstore_backends.mysql_vector_persistence_backend import (
        MySQLVectorPersistenceBackend,
    )

    pool = FakePool()
    backend = MySQLVectorPersistenceBackend(connection_pool=pool)

    backend._ensure_connection()

    assert pool.get_connection_count == 1
    assert backend._conn is pool.connection
    assert backend._schema_initialized is True
    assert pool.connection.cursor_obj.closed is True
    assert pool.connection.close_count == 0


def test_vector_backend_close_returns_pooled_connection() -> None:
    from app.infra.vectorstore_backends.mysql_vector_persistence_backend import (
        MySQLVectorPersistenceBackend,
    )

    pool = FakePool()
    backend = MySQLVectorPersistenceBackend(connection_pool=pool)
    backend._ensure_connection()
    backend.close()

    assert pool.connection.close_count == 1
    assert backend._conn is None


def test_qdrant_mysql_store_passes_shared_pool_to_persistence(monkeypatch) -> None:
    import app.infra.public.vectorstores.qdrant_mysql_vector_store as module

    monkeypatch.setattr(
        module,
        "QdrantLocalVectorStore",
        lambda **kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        module,
        "QdrantKeywordIndex",
        lambda **kwargs: SimpleNamespace(),
    )

    pool = FakePool()
    connection = SimpleNamespace(mode="local")
    store = module.QdrantMySQLVectorStore(
        dimension=2,
        connection=connection,
        mysql_connection_pool=pool,
    )

    assert store._persistence._connection_pool is pool


def test_runtime_composition_injects_shared_mysql_pool_into_vector_store() -> None:
    import inspect

    from app.bootstrap import opensource

    source = inspect.getsource(opensource._build_runtime_providers)
    vector_store_call = source.split("vector_store = QdrantMySQLVectorStore(", 1)[1]

    assert "mysql_connection_pool=mysql_pool" in vector_store_call
