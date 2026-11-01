"""TDD contracts for removing the cold recommendation retrieval bottleneck."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from search_core.plugin_api.vector_point import VectorPoint
from app.domain.models.vector_search_hit import VectorSearchHit

from app.application.services.keyword_candidate_selection import (
    keyword_candidates_with_status,
)
from app.application.services.retrieval_branch_runner import RetrievalBranchRunner
from app.application.services.worker_vector_match_service import (
    FragmentRetrievalConfig,
    WorkerVectorMatchService,
)
from app.infra.metadatastores.file_metadata_store_adapter import (
    FileMetadataStoreAdapter,
)

ENABLED_TYPES = {"full"}


class BlockingVectorStore:
    """Force dense and keyword calls into an observable rendezvous."""

    def __init__(self) -> None:
        self.barrier = threading.Barrier(2, timeout=1.0)
        self.overlapped = threading.Event()
        self.search_started = threading.Event()
        self.keyword_started = threading.Event()

    def search(self, *args: Any, **kwargs: Any) -> list[VectorSearchHit]:
        self.search_started.set()
        try:
            self.barrier.wait()
        except threading.BrokenBarrierError:
            return []
        self.overlapped.set()
        return [
            VectorSearchHit(
                id="dense:owner:default:full",
                score=0.9,
                payload={
                    "profile_key": "dense:owner:default",
                    "fragment_type": "full",
                    "profile_id": "default",
                },
            )
        ]

    def text_search(self, *args: Any, **kwargs: Any) -> list[VectorSearchHit]:
        self.keyword_started.set()
        try:
            self.barrier.wait()
        except threading.BrokenBarrierError:
            return []
        self.overlapped.set()
        return [
            VectorSearchHit(
                id="keyword:writer:default:full",
                score=0.5,
                payload={
                    "profile_key": "keyword:writer:default",
                    "fragment_type": "full",
                    "profile_id": "default",
                },
            )
        ]


def _service(vector_store: Any, tmp_path: Path) -> WorkerVectorMatchService:
    return WorkerVectorMatchService(
        vector_store=vector_store,
        metadata_store=FileMetadataStoreAdapter(
            storage_dir=str(tmp_path / "metadata")
        ),
        fragment_config=FragmentRetrievalConfig(
            enable_fragment_embedding=True,
            reranker_model=None,
        ),
        retrieval_runner=RetrievalBranchRunner(max_workers=2),
    )


def test_dense_and_keyword_retrieval_paths_overlap(tmp_path: Path) -> None:
    vector_store = BlockingVectorStore()
    service = _service(vector_store, tmp_path)

    results = service._match_with_fragments(
        query="payment writer",
        query_embedding=[1.0],
        top_k=2,
        filters=None,
        excluded_profile_keys=None,
        vector_min_score=0.0,
    )

    assert vector_store.overlapped.is_set()
    assert {result.profile_key for result in results} == {
        "dense:owner:default",
        "keyword:writer:default",
    }


def test_keyword_retrieval_avoids_a_second_lookup_for_typical_profiles() -> None:
    class TwelveHitStore:
        def __init__(self) -> None:
            self.calls: list[int] = []

        def text_search(
            self, query: str, top_k: int, filters: dict | None = None
        ) -> list[VectorSearchHit]:
            self.calls.append(top_k)
            return [
                VectorSearchHit(
                    id=f"worker:{index}:default:full",
                    score=1.0 - index / 100,
                    payload={
                        "profile_key": f"worker:{index}:default",
                        "worker_id": f"worker:{index}",
                        "profile_id": "default",
                        "fragment_type": "full",
                        "availability": "public",
                        "runtime_state": "online",
                    },
                )
                for index in range(12)
            ]

    store = TwelveHitStore()
    outcome = keyword_candidates_with_status(
        store,
        "writer",
        3,
        {
            "availability": ["public"],
            "runtime_state": ["online"],
        },
        set(),
        None,
        ENABLED_TYPES,
    )

    assert len(outcome.candidates) == 3
    assert store.calls == [12]


def test_dense_failure_keeps_keyword_candidates_and_degradation_metadata(
    tmp_path: Path,
) -> None:
    class DenseFailureStore:
        def search(self, *args: Any, **kwargs: Any) -> list[VectorSearchHit]:
            raise RuntimeError("dense cluster unavailable")

        def text_search(
            self, query: str, top_k: int, filters: dict | None = None
        ) -> list[VectorSearchHit]:
            return [
                VectorSearchHit(
                    id="keyword:writer:default:full",
                    score=0.4,
                    payload={
                        "profile_key": "keyword:writer:default",
                        "fragment_type": "full",
                        "profile_id": "default",
                    },
                )
            ]

    vector_store = DenseFailureStore()
    service = _service(vector_store, tmp_path)
    runtime_config: dict[str, Any] = {}
    results = service._match_with_fragments(
        query="writer",
        query_embedding=[1.0],
        top_k=1,
        filters=None,
        excluded_profile_keys=None,
        runtime_config=runtime_config,
        vector_min_score=0.0,
    )

    assert [result.profile_key for result in results] == [
        "keyword:writer:default"
    ]
    assert runtime_config["_retrieval"]["semantic_unavailable"] is True
    assert runtime_config["_retrieval"]["keyword_search_used"] is True
    assert runtime_config["_retrieval"]["score_source"] == "hybrid_rrf"


def test_keyword_failure_keeps_dense_candidates_and_degradation_metadata(
    tmp_path: Path,
) -> None:
    class KeywordFailureStore:
        def search(self, *args: Any, **kwargs: Any) -> list[VectorSearchHit]:
            return [
                VectorSearchHit(
                    id="dense:owner:default:full",
                    score=0.9,
                    payload={
                        "profile_key": "dense:owner:default",
                        "fragment_type": "full",
                        "profile_id": "default",
                    },
                )
            ]

        def text_search(
            self, query: str, top_k: int, filters: dict | None = None
        ) -> list[VectorSearchHit]:
            raise RuntimeError("keyword cluster unavailable")

    vector_store = KeywordFailureStore()
    service = _service(vector_store, tmp_path)
    runtime_config: dict[str, Any] = {}
    results = service._match_with_fragments(
        query="owner",
        query_embedding=[1.0],
        top_k=1,
        filters=None,
        excluded_profile_keys=None,
        runtime_config=runtime_config,
        vector_min_score=0.0,
    )

    assert [result.profile_key for result in results] == [
        "dense:owner:default"
    ]
    assert runtime_config["_retrieval"]["semantic_unavailable"] is False
    assert runtime_config["_retrieval"]["keyword_unavailable"] is True


def test_parallel_runner_reports_both_results_when_one_branch_fails() -> None:
    runner = RetrievalBranchRunner(max_workers=2)

    def fail() -> int:
        raise RuntimeError("branch failure")

    def success() -> int:
        return 7

    dense, keyword = runner.run(fail, success)

    assert dense.value is None
    assert isinstance(dense.error, RuntimeError)
    assert keyword.value == 7
    assert keyword.error is None


def test_healthy_zero_hit_still_serves_bounded_literal_fallback(tmp_path: Path) -> None:
    """No candidate is an availability condition, not an empty success."""

    from search_core.providers.fallback import RegexFallbackIndex

    class EmptyRetrievalStore:
        def search(self, *args: Any, **kwargs: Any) -> list[VectorSearchHit]:
            return []

        def text_search(
            self, query: str, top_k: int, filters: dict | None = None
        ) -> list[VectorSearchHit]:
            return []

    fallback = RegexFallbackIndex(
        [
            VectorPoint(
                id="worker:default:full",
                vector=[],
                payload={
                    "profile_key": "worker:default",
                    "fragment_type": "full",
                    "profile_id": "default",
                    "content": "reserved search availability worker",
                    "runtime_state": "online",
                    "availability": "public",
                },
            )
        ]
    )
    service = WorkerVectorMatchService(
        vector_store=EmptyRetrievalStore(),
        metadata_store=FileMetadataStoreAdapter(
            storage_dir=str(tmp_path / "zero-hit-metadata")
        ),
        fallback_index=fallback,
        fragment_config=FragmentRetrievalConfig(
            enable_fragment_embedding=True,
            reranker_model=None,
        ),
        retrieval_runner=RetrievalBranchRunner(max_workers=2),
    )

    results = service._match_with_fragments(
        query="reserved search availability worker",
        query_embedding=[1.0],
        top_k=1,
        filters={
            "runtime_state": ["online"],
            "availability": ["public"],
        },
        excluded_profile_keys=None,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
