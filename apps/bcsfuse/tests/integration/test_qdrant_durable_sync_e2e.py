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
            for hit in store.search(
                remote.vector, top_k=10, filters={"worker_id": prefix}
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


def test_worker_payload_state_persists_and_replays_to_another_index(tmp_path):
    from src.domain.models.vector_point import VectorPoint
    from src.infra.public.vectorstores.qdrant_mysql_vector_store import QdrantMySQLVectorStore
    from src.infra.vectorstore_backends.mysql_vector_persistence_backend import TABLE_NAME, MySQLVectorPersistenceBackend

    dimension = int(os.getenv("EMBEDDING_DIMENSION", "4096"))
    owner = f"runtime-e2e-{uuid4().hex}:owner"
    ids = [f"{owner}:old:full", f"{owner}:release:skills:1", f"{owner}:nested:default:full"]
    primary = MySQLVectorPersistenceBackend(vector_dimension=dimension)
    replica_backend = MySQLVectorPersistenceBackend(vector_dimension=dimension)
    store = QdrantMySQLVectorStore(dimension=dimension, qdrant_path=str(tmp_path / "primary"), persistence_backend=primary)
    replica = QdrantMySQLVectorStore(dimension=dimension, qdrant_path=str(tmp_path / "replica"), persistence_backend=replica_backend)
    try:
        # Commit through the other instance: the primary local index is empty.
        replica.upsert([
            VectorPoint(id=key, vector=[0.1] * dimension, payload={
                "worker_id": owner if index < 2 else f"{owner}:nested",
                "runtime_state": "online", "content": "preserved",
            }) for index, key in enumerate(ids)
        ])
        for state in ("offline", "online"):
            assert store.update_payload_by_worker(owner, {"runtime_state": state}) == 2
            records = {point.id: point for point in replica_backend.load_all() if point.id in ids}
            assert set(records) == set(ids)
            assert all(records[key].payload["runtime_state"] == state for key in ids[:2])
            assert records[ids[2]].payload["runtime_state"] == "online"
            assert all(point.payload["content"] == "preserved" for point in records.values())
            replica.sync_incremental()
            assert len(replica.search([0.1] * dimension, top_k=10, filters={
                "worker_id": owner, "runtime_state": "online",
            })) == (0 if state == "offline" else 2)
        replica.rebuild_from_backend()
        assert set(replica.get_vector_ids()) >= set(ids)
        # Missing local points must not prevent durable deletion or replay.
        for key in ids[:2]:
            store._qdrant.delete(key)
        assert store.delete_by_worker(owner) == 2
        assert not any(primary.exists(key) for key in ids[:2])
        assert primary.exists(ids[2])
        replica.sync_incremental()
        assert not (set(replica.get_vector_ids()) & set(ids[:2]))
        assert ids[2] in replica.get_vector_ids()
    finally:
        replica_backend._ensure_connection()
        cursor = replica_backend._conn.cursor()
        try:
            cursor.execute(f"DELETE FROM {TABLE_NAME} WHERE id IN (%s, %s, %s)", tuple(ids))
            replica_backend._conn.commit()
        finally:
            cursor.close()
        store.close()
        replica.close()
