from unittest.mock import MagicMock

from app.domain.services.profile_embedding_indexer import ProfileEmbeddingIndexer
from app.infra.config.feature_flags import FeatureFlags


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


def test_profiles_missing_fragments_detects_partial_profile_rebuilds():
    class Fragment:
        def __init__(self, fragment_type: str) -> None:
            self.fragment_type = fragment_type
            self.index = 0
            self.content_hash = "same"

        def compute_fragment_id(self, profile_key: str) -> str:
            return f"{profile_key}:{self.fragment_type}"

    class Decomposer:
        def decompose(self, profile):
            return [Fragment("full"), Fragment("profile")]

    class Profile:
        def __init__(self, profile_key: str) -> None:
            self.profile_key = profile_key

    class DecomposingIndexer(ProfileEmbeddingIndexer):
        def __init__(self) -> None:
            self._fragment_decomposer = Decomposer()

    indexer = DecomposingIndexer()
    indexed_ids = {
        "worker-a:default:full",
        "worker-b:default:full",
        "worker-b:default:profile",
    }

    missing = indexer.profiles_missing_fragments(
        [
            Profile("worker-a:default"),
            Profile("worker-b:default"),
            Profile("worker-c:default"),
        ],
        indexed_ids,
    )

    assert [profile.profile_key for profile in missing] == [
        "worker-a:default",
        "worker-c:default",
    ]


def test_profiles_missing_fragments_fails_closed_when_ids_cannot_be_enumerated():
    class Profile:
        profile_key = "worker-a:default"

    indexer = ProfileEmbeddingIndexer(
        MagicMock(),
        MagicMock(),
        fragment_decomposer=MagicMock(decompose=lambda profile: []),
    )

    missing = indexer.profiles_missing_fragments([Profile()], None)

    assert [profile.profile_key for profile in missing] == ["worker-a:default"]


def test_build_index_embeds_across_profiles_with_batch_provider(monkeypatch):
    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        lambda: True,
    )

    from types import SimpleNamespace

    from app.domain.models.profile_fragment import ProfileFragment

    def profile(index: int):
        return SimpleNamespace(
            profile_key=f"worker-{index}:default",
            staff_id=f"worker-{index}",
            profile_id="default",
            profile_type=SimpleNamespace(value="bot"),
            active_skills=[SimpleNamespace(name="general")],
            metadata={},
            short_profile="",
        )

    class StaticDecomposer:
        def decompose(self, worker_profile):
            return [
                ProfileFragment(
                    fragment_type="full",
                    content=(
                        f"worker_id: {worker_profile.staff_id} "
                        f"Skills: general token {worker_profile.staff_id}"
                    ),
                    weight=1.0,
                )
            ]

    class BatchEmbeddingProvider:
        def __init__(self):
            self.batches = []

        def embed_batch(self, texts):
            self.batches.append(list(texts))
            return [[0.25] for _ in texts]

    batcher = BatchEmbeddingProvider()
    profile_store = MagicMock()
    profile_store.upsert_embeddings.return_value = len
    indexer = ProfileEmbeddingIndexer(
        batcher,
        profile_store,
        batch_size=2,
        fragment_decomposer=StaticDecomposer(),
    )
    profiles = [profile(index) for index in range(4)]

    result = indexer.build_index(profiles, clear_existing=False)

    assert result.indexed_count == 4
    assert result.failed_count == 0
    assert [len(batch) for batch in batcher.batches] == [2, 2]
    assert profile_store.upsert_embeddings.call_count == 2
    first_call = profile_store.upsert_embeddings.call_args_list[0].args[0]
    assert [item[0] for item in first_call] == [
        "worker-0:default:full",
        "worker-1:default:full",
    ]


def test_build_index_reports_failed_profiles_without_partial_upsert(monkeypatch):
    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        lambda: True,
    )
    from types import SimpleNamespace

    def create_profile(index: int):
        return SimpleNamespace(
            profile_key=f"worker-failed-{index}:default",
            staff_id=f"worker-failed-{index}",
            profile_id="default",
            profile_type=SimpleNamespace(value="bot"),
            active_skills=[],
            metadata={},
            short_profile="",
        )

    class OneFragmentDecomposer:
        def decompose(self, profile):
            from app.domain.models.profile_fragment import ProfileFragment

            return [
                ProfileFragment(
                    fragment_type="full",
                    content=f"worker_id: {profile.staff_id} Skills: general",
                )
            ]

    class FailingProvider:
        def embed_batch(self, texts):
            raise RuntimeError("provider rate limited")

    profile_store = MagicMock()
    indexer = ProfileEmbeddingIndexer(
        FailingProvider(),
        profile_store,
        batch_size=2,
        fragment_decomposer=OneFragmentDecomposer(),
    )
    result = indexer.build_index([create_profile(0), create_profile(1)])

    assert result.indexed_count == 0
    assert result.failed_count == 2
    profile_store.upsert_embeddings.assert_not_called()
    assert indexer.batch_size == 2


def test_indexer_default_batch_size_matches_embedding_request_limit():
    assert ProfileEmbeddingIndexer(
        MagicMock(), MagicMock()
    ).batch_size == 32


def test_build_index_paces_provider_batches_and_skips_delay_after_last(
    monkeypatch,
):
    monkeypatch.setattr(
        FeatureFlags,
        "is_profile_embedding_index_enabled",
        lambda: True,
    )
    from types import SimpleNamespace

    provider = MagicMock()
    provider.embed_batch.side_effect = lambda texts: [[0.5] for _ in texts]
    profile_store = MagicMock()
    indexer = ProfileEmbeddingIndexer(
        provider,
        profile_store,
        batch_size=1,
        inter_batch_delay_seconds=0.25,
        fragment_decomposer=MagicMock(
            decompose=lambda profile: [MagicMock(
                compute_fragment_id=lambda key: f"{key}:full",
                to_embedding_text=lambda: "full text",
                metadata={},
            )]
        ),
    )
    delays = []
    monkeypatch.setattr(
        "app.domain.services.profile_embedding_indexer.time.sleep",
        delays.append,
    )
    indexer.build_index(
        [
            SimpleNamespace(
                profile_key=f"worker-paced-{index}:default",
                staff_id=f"worker-paced-{index}",
                profile_id="default",
                profile_type=SimpleNamespace(value="bot"),
                active_skills=[],
                metadata={},
                short_profile="",
            )
            for index in range(4)
        ]
    )

    assert delays == [0.25, 0.25, 0.25]
