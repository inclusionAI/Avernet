from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from src.domain.models.vector_point import VectorPoint
from src.domain.services.vector_persistence_backend import VectorChangeSet
from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)


@dataclass
class FakePersistenceBackend:
    points: dict[str, VectorPoint] = field(default_factory=dict)
    changes: VectorChangeSet = field(default_factory=VectorChangeSet)
    closed: bool = False
    fail_load: bool = False

    def save(self, point: VectorPoint) -> None:
        self.points[point.id] = point

    def save_batch(self, points: list[VectorPoint]) -> None:
        for point in points:
            self.save(point)

    def load_all(self) -> list[VectorPoint]:
        if self.fail_load:
            raise RuntimeError("durable backend unavailable")
        return list(self.points.values())

    def load_changes_since(self, checkpoint: float) -> VectorChangeSet:
        return self.changes

    def delete(self, point_id: str) -> bool:
        return self.points.pop(point_id, None) is not None

    def delete_batch(self, ids: list[str]) -> int:
        return sum(self.delete(point_id) for point_id in ids)

    def exists(self, point_id: str) -> bool:
        return point_id in self.points

    def count(self) -> int:
        return len(self.points)

    def get_last_modified_time(self) -> float:
        return self.changes.checkpoint

    def close(self) -> None:
        self.closed = True


def _point(point_id: str, content: str, *, worker_id: str = "worker-1") -> VectorPoint:
    return VectorPoint(
        id=point_id,
        vector=[1.0, 0.0, 0.0],
        payload={"content": content, "worker_id": worker_id},
    )


def _store(backend: FakePersistenceBackend, tmp_path) -> QdrantMySQLVectorStore:
    return QdrantMySQLVectorStore(
        collection_name=f"durable-vector-test-{uuid4().hex}",
        qdrant_path=str(tmp_path / "qdrant"),
        dimension=3,
        persistence_backend=backend,
    )


def test_injected_backend_is_the_durable_source_of_truth(tmp_path) -> None:
    backend = FakePersistenceBackend()
    store = _store(backend, tmp_path)
    point = _point("worker-1:default:full", "python database reliability")

    store.upsert([point])

    assert backend.points[point.id] == point
    assert store.get(point.id) == point
    assert store.get_vector_ids() == [point.id]
    store.close()


def test_rebuild_restores_dense_and_text_indexes_from_durable_backend(tmp_path) -> None:
    backend = FakePersistenceBackend(
        points={
            "python": _point("python", "python database reliability"),
            "design": _point("design", "product interaction design"),
        }
    )
    store = _store(backend, tmp_path)

    result = store.rebuild_from_backend()
    hits = store.text_search("python database", top_k=5)

    assert result["loaded_count"] == 2
    assert result["indexed_count"] == 2
    assert [hit.id for hit in hits] == ["python"]
    store.close()


def test_text_search_indexes_content_preview_when_full_content_is_missing(
    tmp_path,
) -> None:
    backend = FakePersistenceBackend()
    store = _store(backend, tmp_path)
    preview_only = VectorPoint(
        id="preview-only",
        vector=[1.0, 0.0, 0.0],
        payload={"content_preview": "legacy python profile"},
    )
    store.upsert([preview_only])

    hits = store.text_search("python", top_k=5)

    assert [hit.id for hit in hits] == ["preview-only"]
    store.close()


def test_text_search_applies_the_same_exact_and_multi_value_filters_as_dense(
    tmp_path,
) -> None:
    backend = FakePersistenceBackend()
    store = _store(backend, tmp_path)
    store.upsert(
        [
            _point("allowed", "python database", worker_id="worker-1"),
            _point("blocked", "python database", worker_id="worker-2"),
        ]
    )

    exact = store.text_search(
        "python", top_k=5, filters={"worker_id": "worker-1"}
    )
    multi = store.text_search(
        "python", top_k=5, filters={"worker_id": ["worker-2", "worker-3"]}
    )

    assert [hit.id for hit in exact] == ["allowed"]
    assert [hit.id for hit in multi] == ["blocked"]
    store.close()


def test_incremental_sync_applies_upserts_and_deletions_without_rewriting_backend(
    tmp_path,
) -> None:
    old = _point("old", "legacy profile")
    updated = _point("new", "new python profile")
    backend = FakePersistenceBackend(points={old.id: old})
    store = _store(backend, tmp_path)
    store.rebuild_from_backend()
    backend.changes = VectorChangeSet(
        upserts=[updated], deleted_ids=[old.id], checkpoint=42.0
    )

    result = store.sync_incremental()

    assert result == {"upserted": 1, "deleted": 1}
    assert store.get_vector_ids() == [updated.id]
    assert [hit.id for hit in store.text_search("python", top_k=5)] == [updated.id]
    assert old.id in backend.points
    store.close()


def test_close_delegates_to_injected_backend(tmp_path) -> None:
    backend = FakePersistenceBackend()
    store = _store(backend, tmp_path)

    store.close()

    assert backend.closed is True


def test_rebuild_leaves_local_store_ready_for_new_writes(tmp_path) -> None:
    backend = FakePersistenceBackend()
    store = _store(backend, tmp_path)
    original = _point("original", "original profile")
    replacement = _point("replacement", "replacement profile")

    store.upsert([original])
    backend.points = {replacement.id: replacement}
    store.rebuild_from_backend()
    store.upsert([replacement])

    assert store.get("original") is None
    assert "original" not in store.get_vector_ids()
    stored = store.get("replacement")
    assert stored is not None
    assert stored.id == replacement.id
    assert stored.vector == replacement.vector
    store.close()


def test_rebuild_keeps_previous_index_when_durable_read_fails(tmp_path) -> None:
    backend = FakePersistenceBackend()
    store = _store(backend, tmp_path)
    original = _point("original", "original python profile")
    store.upsert([original])
    backend.fail_load = True

    try:
        store.rebuild_from_backend()
    except RuntimeError as error:
        assert str(error) == "durable backend unavailable"
    else:
        raise AssertionError("rebuild should preserve the durable read failure")

    assert store.get_vector_ids() == [original.id]
    assert [hit.id for hit in store.text_search("python", top_k=5)] == [original.id]
    store.close()
