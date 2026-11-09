"""Local Qdrant keeps durable records, keyword policy and explicit write errors."""

import pytest

from app.domain.models.vector_point import VectorPoint
from app.infra.public.vectorstores.qdrant_sqlite_vector_store import (
    QdrantSQLiteVectorStore,
)


def test_restart_recovers_dense_keyword_and_worker_state(tmp_path):
    path = str(tmp_path / "vectors.sqlite")
    store = QdrantSQLiteVectorStore(dimension=2, db_path=path)
    store.upsert(
        [
            VectorPoint(
                id="a:default:full",
                vector=[1.0, 0.0],
                payload={
                    "worker_id": "a",
                    "content": "支付助手",
                    "runtime_state": "online",
                },
            ),
            VectorPoint(
                id="a:b:default:full",
                vector=[0.0, 1.0],
                payload={
                    "worker_id": "a:b",
                    "content": "支付助手",
                    "runtime_state": "online",
                },
            ),
        ]
    )
    assert store.update_payload_by_worker("a", {"runtime_state": "offline"}) == 1
    store.close()
    store = QdrantSQLiteVectorStore(dimension=2, db_path=path)
    try:
        assert [
            h.id for h in store.text_search("支付", 2, {"runtime_state": "online"})
        ] == ["a:b:default:full"]
        assert store.text_search("a:b", 1)[0].payload["_keyword_exact_id"] is True
        assert store.get("a:default:full").payload["runtime_state"] == "offline"
        assert store.delete_by_worker("a") == 1
        assert store.get_vector_ids() == ["a:b:default:full"]
    finally:
        store.close()


def test_failed_durable_write_does_not_update_index(tmp_path, monkeypatch):
    store = QdrantSQLiteVectorStore(dimension=2, db_path=str(tmp_path / "db"))

    def fail(*args):
        raise RuntimeError("write failed")

    monkeypatch.setattr(store._persistence, "save_batch", fail)
    try:
        with pytest.raises(RuntimeError, match="write failed"):
            store.upsert([VectorPoint(id="a", vector=[1.0, 0.0])])
        assert store.size() == 0
    finally:
        store.close()


def test_failed_index_write_is_reported_and_recovered_from_sqlite(
    tmp_path, monkeypatch
):
    path = str(tmp_path / "db")
    store = QdrantSQLiteVectorStore(dimension=2, db_path=path)

    def fail(*args, **kwargs):
        raise RuntimeError("index failed")

    monkeypatch.setattr(store._qdrant, "upsert", fail)
    with pytest.raises(RuntimeError, match="DEGRADED_REBUILD_REQUIRED"):
        store.upsert(
            [VectorPoint(id="a", vector=[1.0, 0.0], payload={"content": "支付助手"})]
        )
    assert store.get("a") is not None
    store.close()
    restored = QdrantSQLiteVectorStore(dimension=2, db_path=path)
    try:
        assert restored.search([1.0, 0.0], 1)[0].id == "a"
        assert restored.text_search("支付", 1)[0].id == "a"
    finally:
        restored.close()


def test_delete_profile_removes_all_owned_fragments_and_survives_restart(tmp_path):
    path = str(tmp_path / "db")
    store = QdrantSQLiteVectorStore(dimension=2, db_path=path)
    records = [
        ("w", "p", "full"),
        ("w", "p", "skills:1"),
        ("w", "other", "full"),
        ("w:nested", "p", "full"),
    ]
    store.upsert(
        [
            VectorPoint(
                id=f"{w}:{p}:{f}",
                vector=[1.0, 0.0],
                payload={"worker_id": w, "profile_id": p, "content": "支付"},
            )
            for w, p, f in records
        ]
    )
    try:
        assert store.delete_by_profile("w", "p") == 2
        assert store.delete_by_profile("w", "p") == 0
        assert {h.id for h in store.text_search("支付", 10)} == {
            "w:other:full",
            "w:nested:p:full",
        }
    finally:
        store.close()
    restored = QdrantSQLiteVectorStore(dimension=2, db_path=path)
    try:
        assert set(restored.get_vector_ids()) == {"w:other:full", "w:nested:p:full"}
    finally:
        restored.close()


def test_corrupt_snapshot_fails_before_modifying_current_data(tmp_path):
    import sqlite3

    store = QdrantSQLiteVectorStore(dimension=2, db_path=str(tmp_path / "live.db"))
    try:
        store.upsert(
            [VectorPoint(id="keep", vector=[1.0, 0.0], payload={"content": "支付"})]
        )
        store.save_snapshot(str(tmp_path / "snapshot"))
        with sqlite3.connect(tmp_path / "snapshot/vector_embeddings.sqlite") as conn:
            conn.execute("UPDATE bcsfuse_vector_embeddings SET payload='not-json'")
        with pytest.raises(ValueError):
            store.load_snapshot(str(tmp_path / "snapshot"))
        assert store.get_vector_ids() == ["keep"]
        assert store.text_search("支付", 1)[0].id == "keep"
    finally:
        store.close()


def test_empty_file_is_not_an_empty_snapshot_and_self_backup_is_rejected(tmp_path):
    import sqlite3

    store = QdrantSQLiteVectorStore(
        dimension=2, db_path=str(tmp_path / "vector_embeddings.sqlite")
    )
    try:
        store.upsert([VectorPoint(id="keep", vector=[1.0, 0.0])])
        with pytest.raises(ValueError, match="differ"):
            store.save_snapshot(str(tmp_path))
        folder = tmp_path / "invalid"
        folder.mkdir()
        (folder / "vector_embeddings.sqlite").touch()
        with pytest.raises(sqlite3.DatabaseError):
            store.load_snapshot(str(folder))
        assert store.get_vector_ids() == ["keep"]
    finally:
        store.close()
