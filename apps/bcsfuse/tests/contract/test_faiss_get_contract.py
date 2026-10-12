"""FAISS implements the point-read contract used by vector consumers."""

from types import SimpleNamespace

import pytest

from src.domain.models.vector_point import VectorPoint
from src.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
from src.domain.services.vector_store_adapter import VectorStoreAdapter
from src.infra.public.vectorstores.faiss_sqlite_vector_store import (
    FaissSqliteVectorStore,
)
from src.infra.vectorstores.faiss_vector_store_adapter import FaissVectorStoreAdapter


def test_get_returns_latest_live_point_and_defensive_payload(tmp_path):
    store = FaissVectorStoreAdapter(dimension=2)
    assert isinstance(store, VectorStoreAdapter)
    assert store.get("missing") is None
    store.upsert([VectorPoint(id="bot:1:default", vector=[3, 4], payload={"tags": ["old"]})])
    point = store.get("bot:1:default")
    assert point.vector == pytest.approx([0.6, 0.8])
    assert point.payload == {"tags": ["old"]}
    point.payload["tags"].append("mutated")
    assert store.get("bot:1:default").payload == {"tags": ["old"]}
    store.upsert([VectorPoint(id="bot:1:default", vector=[0, 1], payload={"tags": ["new"]})])
    assert store.get("bot:1:default").vector == pytest.approx([0, 1])
    store.save_snapshot(str(tmp_path))
    restored = FaissVectorStoreAdapter(dimension=2)
    restored.load_snapshot(str(tmp_path))
    assert restored.get("bot:1:default").payload == {"tags": ["new"]}
    restored.delete(["bot:1:default"])
    assert restored.get("bot:1:default") is None


def test_sqlite_worker_cleanup_removes_orphans_and_preserves_other_owners(tmp_path):
    store = FaissSqliteVectorStore(dimension=2, db_path=str(tmp_path / "vectors.db"))
    store.upsert([
        VectorPoint(id="a:orphan:full", vector=[1., 0.], payload={"worker_id": "a"}),
        VectorPoint(id="a:b:default:full", vector=[0., 1.], payload={"worker_id": "a:b"}),
    ])
    indexer = ProfileEmbeddingIndexer.__new__(ProfileEmbeddingIndexer)
    indexer._profile_store = SimpleNamespace(vector_store=store)
    assert indexer.delete_by_worker("a") == 1
    assert indexer.delete_by_worker("a") == 0
    assert store.get_vector_ids() == ["a:b:default:full"]
    restored = FaissSqliteVectorStore(dimension=2, db_path=str(tmp_path / "vectors.db"))
    assert restored.get_vector_ids() == ["a:b:default:full"]
