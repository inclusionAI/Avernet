from __future__ import annotations

import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("BCSFUSE_RUN_MYSQL_INTEGRATION") != "1",
    reason="requires an explicitly enabled real MySQL integration database",
)


def test_incremental_sync_propagates_remote_upsert_and_soft_delete(tmp_path) -> None:
    from src.domain.models.vector_point import VectorPoint
    from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
        QdrantMySQLVectorStore,
    )
    from src.infra.vectorstore_backends.mysql_vector_persistence_backend import (
        TABLE_NAME,
        MySQLVectorPersistenceBackend,
    )

    dimension = int(os.getenv("EMBEDDING_DIMENSION", "4096"))
    prefix = f"sync-e2e-{uuid4().hex}"
    original_id = f"{prefix}:original"
    remote_id = f"{prefix}:remote"
    original = VectorPoint(
        id=original_id,
        vector=[0.1] * dimension,
        payload={"content": "legacy operations profile", "worker_id": prefix},
    )
    remote = VectorPoint(
        id=remote_id,
        vector=[0.2] * dimension,
        payload={"content": "python database reliability", "worker_id": prefix},
    )
    primary_backend = MySQLVectorPersistenceBackend(vector_dimension=dimension)
    remote_backend = MySQLVectorPersistenceBackend(vector_dimension=dimension)
    store = QdrantMySQLVectorStore(
        collection_name=f"sync-e2e-{uuid4().hex}",
        qdrant_path=str(tmp_path / "qdrant"),
        dimension=dimension,
        persistence_backend=primary_backend,
    )

    try:
        store.upsert([original])
        store.rebuild_from_backend()

        remote_backend.save(remote)
        remote_backend.delete(original_id)
        result = store.sync_incremental()

        vector_ids = set(store.get_vector_ids())
        assert result["upserted"] >= 1
        assert result["deleted"] >= 1
        assert remote_id in vector_ids
        assert original_id not in vector_ids
        assert remote_id in {
            hit.id
            for hit in store.text_search(
                "python database", top_k=10, filters={"worker_id": prefix}
            )
        }
    finally:
        remote_backend._ensure_connection()
        cursor = remote_backend._conn.cursor()
        try:
            cursor.execute(
                f"DELETE FROM {TABLE_NAME} WHERE `id` IN (%s, %s)",
                (original_id, remote_id),
            )
            remote_backend._conn.commit()
        finally:
            cursor.close()
        store.close()
        remote_backend.close()
