from unittest.mock import MagicMock

import pytest

from src.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
from src.infra.indexing.profile_embedding_store import ProfileEmbeddingStore
from src.infra.public.vectorstores.qdrant_local_vector_store import (
    QdrantLocalVectorStore,
)


def test_profile_cleanup_removes_qdrant_exact_and_fragment_ids(tmp_path):
    vector_store = QdrantLocalVectorStore(
        collection_name="profile_cleanup",
        path=str(tmp_path / "qdrant"),
        dimension=4,
        distance="Euclid",
    )
    profile_store = ProfileEmbeddingStore(
        dimension=4,
        vector_store=vector_store,
    )
    indexer = ProfileEmbeddingIndexer(MagicMock(), profile_store)
    target_ids = [
        "worker:default",
        "worker:default:full",
        "worker:default:skills:0",
    ]
    retained_id = "worker:secondary:full"

    try:
        for vector_id in [*target_ids, retained_id]:
            vector_store.upsert(vector_id, [0.5] * 4, {})

        deleted = indexer.delete_by_profile("worker:default")

        assert deleted == 3
        assert all(vector_store.get(vector_id) is None for vector_id in target_ids)
        assert vector_store.get(retained_id) is not None
    finally:
        vector_store.clear()
        vector_store.close()


def test_replacement_cleanup_skips_retained_vectors_and_removes_legacy_id(
    tmp_path, monkeypatch
):
    vector_store = QdrantLocalVectorStore(
        collection_name="replacement_cleanup",
        path=str(tmp_path / "vectors"),
        dimension=2,
    )
    profile_store = ProfileEmbeddingStore(dimension=2, vector_store=vector_store)
    try:
        vector_store.upsert("worker:default", [1.0, 0.0], {})
        vector_store.upsert("worker:default:full", [1.0, 0.0], {})
        original_get = vector_store.get

        def get_obsolete_only(vector_id):
            if vector_id == "worker:default:full":
                pytest.fail("cleanup must not reload retained embeddings")
            return original_get(vector_id)

        with monkeypatch.context() as patch:
            patch.setattr(vector_store, "get", get_obsolete_only)
            profile_store.delete_stale_fragments(
                "worker:default", {"worker:default:full"}, "worker"
            )
        assert vector_store.get("worker:default") is None
        assert vector_store.get("worker:default:full") is not None
    finally:
        vector_store.close()
