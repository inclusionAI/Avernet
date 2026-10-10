from unittest.mock import MagicMock

from src.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
from src.infra.config.feature_flags import FeatureFlags


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


def test_smart_update_deletes_fragments_removed_from_profile(monkeypatch):
    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        lambda: True,
    )
    profile_key = "bot:owner:default"
    full_fragment = MagicMock(content_hash="same")
    full_fragment.compute_fragment_id.return_value = f"{profile_key}:full"
    profile = MagicMock(profile_key=profile_key)
    profile_store = MagicMock()
    profile_store.get_fragments_by_profile.return_value = [
        (f"{profile_key}:full", [0.1], {"content_hash": "same"}),
        (
            f"{profile_key}:capabilities",
            [0.2],
            {"content_hash": "old-empty-placeholder"},
        ),
    ]
    indexer = ProfileEmbeddingIndexer(MagicMock(), profile_store)
    indexer._fragment_decomposer.decompose = MagicMock(
        return_value=[full_fragment],
    )

    result = indexer.update_index_smart([profile])

    assert result.failed_count == 0
    profile_store.delete.assert_called_once_with(
        [f"{profile_key}:capabilities"],
    )
