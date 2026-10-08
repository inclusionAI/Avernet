from unittest.mock import MagicMock

from src.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer


def test_delete_by_profile_uses_profile_store_delete_contract():
    vector_store = MagicMock()
    vector_store.get_vector_ids.return_value = [
        "bot:owner:default:full",
        "bot:owner:default:skills:0",
        "another:default:full",
    ]
    profile_store = MagicMock(spec=["vector_store", "delete"])
    profile_store.vector_store = vector_store
    indexer = ProfileEmbeddingIndexer(MagicMock(), profile_store)

    deleted = indexer.delete_by_profile("bot:owner:default")

    assert deleted == 2
    profile_store.delete.assert_called_once_with(
        ["bot:owner:default:full", "bot:owner:default:skills:0"]
    )


def test_delete_by_profile_keeps_prefix_cleanup_when_store_has_native_delete():
    class ExactOnlyVectorStore:
        def get_vector_ids(self):
            return [
                "bot:owner:default",
                "bot:owner:default:full",
                "bot:owner:default:skills:0",
                "bot:owner:secondary:full",
            ]

        def delete_by_profile(self, worker_id, profile_id):
            return 1

    profile_store = MagicMock(spec=["vector_store", "delete"])
    profile_store.vector_store = ExactOnlyVectorStore()
    indexer = ProfileEmbeddingIndexer(MagicMock(), profile_store)

    deleted = indexer.delete_by_profile("bot:owner:default")

    assert deleted == 3
    profile_store.delete.assert_called_once_with(
        [
            "bot:owner:default",
            "bot:owner:default:full",
            "bot:owner:default:skills:0",
        ]
    )


def test_delete_by_profile_uses_native_cleanup_when_ids_cannot_be_enumerated():
    class NativeOnlyVectorStore:
        def delete_by_profile(self, worker_id, profile_id):
            assert (worker_id, profile_id) == ("bot:owner", "default")
            return 4

    profile_store = MagicMock(spec=["vector_store", "delete"])
    profile_store.vector_store = NativeOnlyVectorStore()
    indexer = ProfileEmbeddingIndexer(MagicMock(), profile_store)

    deleted = indexer.delete_by_profile("bot:owner:default")

    assert deleted == 4
    profile_store.delete.assert_not_called()
