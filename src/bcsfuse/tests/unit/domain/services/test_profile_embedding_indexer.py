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
