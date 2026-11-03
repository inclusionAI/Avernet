"""Final literal fallback contract for TC worker vector matching."""

from unittest.mock import Mock

from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.fallback import RegexFallbackIndex

from app.application.services.worker_vector_match_service import (
    FragmentRetrievalConfig,
    WorkerVectorMatchService,
)
from app.infra.metadatastores.file_metadata_store_adapter import (
    FileMetadataStoreAdapter,
)


def fallback_point(profile_key="worker:default", content="星河 数据分析助手"):
    return VectorPoint(
        id=profile_key,
        vector=[0.0],
        payload={
            "id": profile_key,
            "profile_key": profile_key,
            "worker_id": "worker",
            "profile_id": "default",
            "runtime_state": "online",
            "availability": "public",
            "content": content,
        },
    )


def service_for(vector_store, fallback_index, tmp_path):
    return WorkerVectorMatchService(
        vector_store=vector_store,
        metadata_store=FileMetadataStoreAdapter(storage_dir=str(tmp_path / "metadata")),
        fallback_index=fallback_index,
        fragment_config=FragmentRetrievalConfig(enable_fragment_embedding=True),
    )


def test_literal_fallback_returns_stable_result_when_dense_and_keyword_are_down(tmp_path):
    vector_store = Mock()
    vector_store.size.return_value = 1
    vector_store.search.side_effect = RuntimeError("qdrant unavailable")
    vector_store.text_search.side_effect = RuntimeError("keyword unavailable")
    service = service_for(
        vector_store, RegexFallbackIndex([fallback_point()]), tmp_path
    )

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        vector_min_score=0.9,
        runtime_config=runtime_config,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    assert results[0].score > 0.0
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["score_source"] == "regex_fallback"
    assert diagnostics["literal_fallback_used"] is True
    assert diagnostics["semantic_unavailable"] is True
    assert diagnostics["keyword_unavailable"] is True


def test_legacy_literal_fallback_reports_candidate_source(tmp_path):
    vector_store = Mock()
    vector_store.size.return_value = 1
    vector_store.search.side_effect = RuntimeError("qdrant unavailable")
    vector_store.text_search.side_effect = RuntimeError("keyword unavailable")
    service = service_for(
        vector_store, RegexFallbackIndex([fallback_point()]), tmp_path
    )

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="legacy",
        top_k=3,
        vector_min_score=0.9,
        runtime_config=runtime_config,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["score_source"] == "regex_fallback"
    assert diagnostics["candidate_source"] == "literal_fallback"


def test_auto_mode_degrades_to_keyword_when_dense_search_fails(tmp_path):
    from app.domain.models.vector_search_hit import VectorSearchHit

    vector_store = Mock()
    vector_store.size.return_value = 1
    vector_store.search.side_effect = RuntimeError("qdrant unavailable")
    vector_store.text_search.return_value = [
        VectorSearchHit(
            id="worker:default:full",
            score=1.0,
            payload={
                "profile_key": "worker:default",
                "worker_id": "worker",
                "profile_id": "default",
                "fragment_type": "full",
                "content": "星河 数据分析",
                "runtime_state": "online",
                "availability": "public",
            },
        )
    ]
    service = service_for(
        vector_store,
        RegexFallbackIndex([fallback_point()]),
        tmp_path,
    )
    service._fragment_config.enable_fragment_embedding = False

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="auto",
        top_k=3,
        vector_min_score=0.9,
        runtime_config=runtime_config,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["score_source"] == "hybrid_rrf"
    assert diagnostics["candidate_source"] == "keyword_fallback"
    assert diagnostics["keyword_search_used"] is True
    assert diagnostics["semantic_unavailable"] is True
    assert diagnostics["keyword_unavailable"] is False
    assert diagnostics["literal_fallback_used"] is False


def test_auto_mode_uses_literal_when_dense_and_keyword_fail(tmp_path):
    vector_store = Mock()
    vector_store.size.return_value = 1
    vector_store.search.side_effect = RuntimeError("qdrant unavailable")
    vector_store.text_search.side_effect = RuntimeError("keyword unavailable")
    service = service_for(
        vector_store, RegexFallbackIndex([fallback_point()]), tmp_path
    )
    service._fragment_config.enable_fragment_embedding = False

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="auto",
        top_k=3,
        vector_min_score=0.9,
        runtime_config=runtime_config,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["score_source"] == "regex_fallback"
    assert diagnostics["literal_fallback_used"] is True
    assert diagnostics["semantic_unavailable"] is True
    assert diagnostics["keyword_unavailable"] is True


def test_literal_fallback_matches_exact_worker_id(tmp_path):
    vector_store = Mock()
    vector_store.size.return_value = 1
    vector_store.search.side_effect = RuntimeError("qdrant unavailable")
    vector_store.text_search.side_effect = RuntimeError("keyword unavailable")
    service = service_for(
        vector_store, RegexFallbackIndex([fallback_point()]), tmp_path
    )

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="worker:default",
        mode="fragment",
        top_k=3,
        runtime_config=runtime_config,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    assert runtime_config["_retrieval"]["literal_fallback_used"] is True


def test_semantic_outage_uses_literal_when_keyword_has_no_hits(tmp_path):
    vector_store = Mock()
    vector_store.size.return_value = 1
    vector_store.search.side_effect = RuntimeError("qdrant unavailable")
    vector_store.text_search.return_value = []
    service = service_for(
        vector_store, RegexFallbackIndex([fallback_point()]), tmp_path
    )

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        runtime_config=runtime_config,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["score_source"] == "regex_fallback"
    assert diagnostics["literal_fallback_used"] is True
    assert diagnostics["semantic_unavailable"] is True
    assert diagnostics["keyword_unavailable"] is False
    assert diagnostics["literal_fallback_default_used"] is False


def test_semantic_outage_with_no_literal_overlap_uses_final_default(tmp_path):
    vector_store = Mock()
    vector_store.size.return_value = 1
    vector_store.search.side_effect = RuntimeError("qdrant unavailable")
    vector_store.text_search.return_value = []
    service = service_for(
        vector_store, RegexFallbackIndex([fallback_point()]), tmp_path
    )

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="unrelated alpha zeta omega",
        mode="fragment",
        top_k=1,
        runtime_config=runtime_config,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    assert results[0].score == 0.01
    assert results[0].reasons == ["literal_fallback_default"]
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["score_source"] == "regex_fallback"
    assert diagnostics["literal_fallback_used"] is True
    assert diagnostics["literal_fallback_default_used"] is True
    assert diagnostics["semantic_unavailable"] is True
    assert diagnostics["keyword_unavailable"] is False


def test_empty_vector_index_is_not_an_outage_and_has_no_literal_fallback(tmp_path):
    vector_store = Mock()
    vector_store.size.return_value = 0
    service = service_for(
        vector_store, RegexFallbackIndex([fallback_point()]), tmp_path
    )

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        runtime_config=runtime_config,
    )

    assert results == []
    assert runtime_config["_retrieval"]["literal_fallback_used"] is False


def test_literal_fallback_accepts_scalar_exact_filters():
    worker = "ha_worker_release_validator:8e155564f213"
    point = fallback_point(worker, "release validation and production failover specialist")
    point.payload["worker_id"] = worker
    index = RegexFallbackIndex([point])

    hits = index.keyword_search(worker, 3, {"worker_id": worker}, None)

    assert [hit.payload["profile_key"] for hit in hits] == [worker]


def test_literal_fallback_still_respects_exclusions(tmp_path):
    vector_store = Mock()
    vector_store.size.return_value = 1
    vector_store.search.side_effect = RuntimeError("qdrant unavailable")
    vector_store.text_search.side_effect = RuntimeError("keyword unavailable")
    service = service_for(
        vector_store,
        RegexFallbackIndex(
            [
                fallback_point("worker:default", "星河 数据分析助手"),
                fallback_point("second:default", "星河 数据分析助手"),
            ]
        ),
        tmp_path,
    )

    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        excluded_profile_keys=["worker:default"],
    )

    assert [result.profile_key for result in results] == ["second:default"]


def test_literal_fallback_builder_creates_deduplicated_profile_snapshot():
    from app.application.services.literal_fallback_builder import (
        build_literal_fallback_index,
    )
    from app.domain.models.vector_point import VectorPoint as DomainVectorPoint

    class SnapshotStore:
        def __init__(self, points):
            self._points = {point.id: point for point in points}

        def get_vector_ids(self):
            return list(self._points)

        def get(self, point_id):
            return self._points.get(point_id)

    store = SnapshotStore([
        DomainVectorPoint(
            id="worker:default:full",
            vector=[1.0],
            payload={
                "profile_key": "worker:default",
                "content": "first short",
                "runtime_state": "online",
                "availability": "public",
            },
        ),
        DomainVectorPoint(
            id="worker:default:skills:1",
            vector=[0.5],
            payload={
                "profile_key": "worker:default",
                "content": "星河 数据分析 second richer content",
                "runtime_state": "online",
                "availability": "public",
            },
        ),
        DomainVectorPoint(
            id="offline:default:full",
            vector=[0.5],
            payload={
                "profile_key": "offline:default",
                "content": "星河 数据分析",
                "runtime_state": "offline",
                "availability": "public",
            },
        ),
    ])

    fallback = build_literal_fallback_index(store)

    assert fallback is not None
    hits = fallback.keyword_search(
        "星河 数据分析", 10,
        {"runtime_state": ("online",), "availability": ("public",)},
        None,
    )
    assert [hit.payload["profile_key"] for hit in hits] == ["worker:default"]
    assert "richer" in hits[0].payload["content"]


def test_literal_fallback_builder_returns_none_on_snapshot_read_failure():
    from app.application.services.literal_fallback_builder import (
        build_literal_fallback_index,
    )

    class FailingStore:
        def get_vector_ids(self):
            raise RuntimeError("durable snapshot unavailable")

        def get(self, point_id):
            raise AssertionError("must not read individual points after listing fails")

    assert build_literal_fallback_index(FailingStore()) is None


def test_composition_root_injects_literal_fallback_snapshot(monkeypatch, tmp_path):
    from app.adapters.http.dependencies import fusion_dependencies
    from app.application.services.literal_fallback_builder import (
        build_literal_fallback_index,
    )
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry

    class SnapshotStore:
        def get_vector_ids(self):
            return ["worker:default"]

        def get(self, point_id):
            return DomainPoint(
                id=point_id,
                vector=[1.0],
                payload={
                    "profile_key": "worker:default",
                    "content": "星河 数据分析",
                    "runtime_state": "online",
                    "availability": "public",
                },
            )

        def size(self):
            return 1

    from app.domain.models.vector_point import VectorPoint as DomainPoint
    context = ApplicationContext(
        mode="test",
        startup_profile="acceptance",
        registry=ProviderRegistry(),
    )
    context.registry.register("vector_store", SnapshotStore())
    monkeypatch.setenv("QDRANT_LOCAL_PATH", str(tmp_path / "qdrant"))
    monkeypatch.delenv("ENABLE_REGISTRY_AWARE_FILTERING", raising=False)

    try:
        fusion_dependencies.reset_fusion_services()
        fusion_dependencies.set_app_context(context)
        service = fusion_dependencies._get_vector_match_service()
        assert service is not None
        expected = build_literal_fallback_index(context.registry.get("vector_store"))
        assert service._fallback_index is not None
        assert service._fallback_index.keyword_search(
            "星河 数据分析", 1,
            {"runtime_state": ("online",), "availability": ("public",)},
            None,
        ) == expected.keyword_search(
            "星河 数据分析", 1,
            {"runtime_state": ("online",), "availability": ("public",)},
            None,
        )
    finally:
        fusion_dependencies.reset_fusion_services()
        fusion_dependencies.set_app_context(None)


def test_missing_query_embedding_still_uses_keyword_branch(tmp_path):
    from app.application.services.worker_vector_match_service import (
        FragmentRetrievalConfig,
        WorkerVectorMatchService,
    )
    from app.infra.metadatastores.file_metadata_store_adapter import (
        FileMetadataStoreAdapter,
    )
    from app.domain.models.vector_search_hit import VectorSearchHit

    class KeywordStore:
        def __init__(self):
            self.searches = []
            self.dense_called = False

        def size(self):
            return 1

        def search(self, vector, top_k, filters=None):
            self.dense_called = True
            raise AssertionError("dense search must be skipped without an embedding")

        def text_search(self, query, top_k, filters=None):
            self.searches.append((query, top_k, filters))
            return [
                VectorSearchHit(
                    id="worker:default:full",
                    score=1.0,
                    payload={
                        "profile_key": "worker:default",
                        "worker_id": "worker",
                        "profile_id": "default",
                        "fragment_type": "full",
                        "content": "星河 数据分析",
                        "runtime_state": "online",
                        "availability": "public",
                    },
                )
            ]

    store = KeywordStore()
    service = WorkerVectorMatchService(
        vector_store=store,
        metadata_store=FileMetadataStoreAdapter(storage_dir=str(tmp_path / "metadata")),
        fragment_config=FragmentRetrievalConfig(enable_fragment_embedding=True),
    )
    runtime_config = {}
    results = service.match(
        query_embedding=[],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        vector_min_score=0.9,
        runtime_config=runtime_config,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    assert store.dense_called is False
    assert runtime_config["_retrieval"]["semantic_unavailable"] is True
    assert runtime_config["_retrieval"]["keyword_unavailable"] is False
    assert runtime_config["_retrieval"]["score_source"] == "hybrid_rrf"


def test_candidate_recommendation_continues_with_empty_embedding(monkeypatch):
    from app.application.services.worker_candidate_recommendation_impl import (
        WorkerCandidateRecommendationImpl,
    )
    from app.application.services.worker_vector_match_types import MatchResult
    from app.domain.models.candidate_recommendation import CandidateRecommendation
    from app.domain.models.metadata_record import MetadataRecord

    class VectorMatch:
        def __init__(self):
            self.calls = []

        def match(self, **kwargs):
            self.calls.append(kwargs)
            return [
                MatchResult(
                    profile_key="worker:default",
                    metadata=MetadataRecord(
                        profile_key="worker:default",
                        staff_id="worker",
                        profile_id="default",
                        profile_type="default",
                        source_root="test",
                        payload={"content": "星河 数据分析"},
                    ),
                    score=1.0,
                    reasons=["literal_fallback"],
                )
            ]

    vector_match = VectorMatch()
    service = WorkerCandidateRecommendationImpl(
        retrieval_service=object(),
        vector_match_service=vector_match,
        embedding_generator=None,
    )
    monkeypatch.setattr(
        service,
        "_build_recommendation_from_metadata",
        lambda **kwargs: CandidateRecommendation(
            profile_key="worker:default",
            worker_id="worker",
            score=kwargs["score"],
        ),
    )
    monkeypatch.setenv("ENABLE_REAL_EMBEDDING", "true")

    result = service._try_vector_match(
        question="星河 数据分析",
        top_k=3,
        exclude_profile_keys=[],
        filters={},
        vector_min_score=0.01,
    )

    assert result is not None
    assert vector_match.calls[0]["query_embedding"] == []
    assert vector_match.calls[0]["query"] == "星河 数据分析"
    assert [rec.profile_key for rec in result[0]] == ["worker:default"]


def test_vector_match_service_builds_when_index_size_probe_fails(monkeypatch, tmp_path):
    from app.bootstrap.application_context import ApplicationContext
    from app.bootstrap.provider_registry import ProviderRegistry
    from app.adapters.http.dependencies import fusion_dependencies

    class FailingSizeStore:
        literal_fallback_index = RegexFallbackIndex([fallback_point()])
        path = str(tmp_path / "qdrant")
        collection_name = "bcsfuse_profiles"

        def size(self):
            raise RuntimeError("qdrant unavailable")

    context = ApplicationContext(
        mode="runtime",
        startup_profile="acceptance",
        registry=ProviderRegistry(),
    )
    context.registry.register("vector_store", FailingSizeStore())
    monkeypatch.setenv("QDRANT_LOCAL_PATH", str(tmp_path / "qdrant"))
    monkeypatch.delenv("ENABLE_REGISTRY_AWARE_FILTERING", raising=False)

    try:
        fusion_dependencies.reset_fusion_services()
        fusion_dependencies.set_app_context(context)
        service = fusion_dependencies._get_vector_match_service()

        assert service is not None
        assert service._fallback_index is not None
    finally:
        fusion_dependencies.reset_fusion_services()
        fusion_dependencies.set_app_context(None)


def test_embedding_admission_failure_uses_literal_snapshot_without_qdrant(tmp_path):
    vector_store = Mock()
    dense_called = False
    keyword_called = False

    def reject_dense(*args, **kwargs):
        nonlocal dense_called
        dense_called = True
        raise AssertionError("dense search must not be called")

    def reject_keyword(*args, **kwargs):
        nonlocal keyword_called
        keyword_called = True
        raise AssertionError("keyword search must not be called")

    vector_store.search.side_effect = reject_dense
    vector_store.text_search.side_effect = reject_keyword
    service = service_for(
        vector_store, RegexFallbackIndex([fallback_point()]), tmp_path
    )

    runtime_config = {
        "_semantic_failure": {
            "semantic_error": "EMBEDDING_QUOTA_EXHAUSTED",
            "semantic_status_code": 429,
        }
    }
    results = service.match(
        query_embedding=[],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        runtime_config=runtime_config,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["score_source"] == "regex_fallback"
    assert diagnostics["literal_fallback_used"] is True
    assert diagnostics["semantic_unavailable"] is True
    assert diagnostics["keyword_bypassed"] is True
    assert diagnostics["keyword_unavailable"] is False
    assert dense_called is False
    assert keyword_called is False


def test_successful_semantic_query_skips_remote_keyword_when_local_snapshot_suffices(
    tmp_path,
):
    from app.domain.models.vector_search_hit import VectorSearchHit

    vector_store = Mock()
    vector_store.size.return_value = 2
    vector_store.search.return_value = [
        VectorSearchHit(
            id="worker:default:full",
            score=0.8,
            payload={
                "profile_key": "worker:default",
                "worker_id": "worker",
                "profile_id": "default",
                "fragment_type": "full",
                "content": "dense semantic result",
                "runtime_state": "online",
                "availability": "public",
            },
        )
    ]
    vector_store.text_search.side_effect = AssertionError(
        "remote keyword search must be skipped"
    )
    service = service_for(
        vector_store,
        RegexFallbackIndex(
            [
                fallback_point("worker:default", "星河 数据分析助手"),
                fallback_point("second:default", "星河 数据分析平台"),
            ]
        ),
        tmp_path,
    )
    service._fragment_config.expand_factor = 1

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="fragment",
        top_k=2,
        runtime_config=runtime_config,
        vector_min_score=0.0,
    )

    assert {result.profile_key for result in results} == {
        "worker:default",
        "second:default",
    }
    vector_store.text_search.assert_not_called()
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["keyword_search_used"] is True
    assert diagnostics["keyword_source"] == "local_snapshot"
    assert diagnostics["local_keyword_sufficient"] is True
    assert diagnostics["literal_fallback_used"] is False


def test_empty_embedding_uses_sufficient_local_keyword_without_remote_qdrant(
    tmp_path,
):
    vector_store = Mock()
    vector_store.text_search.side_effect = AssertionError(
        "dense-outage keyword search must use the local snapshot"
    )
    service = service_for(
        vector_store,
        RegexFallbackIndex(
            [
                fallback_point("worker:default", "星河 数据分析助手"),
                fallback_point("second:default", "星河 数据分析平台"),
            ]
        ),
        tmp_path,
    )
    service._fragment_config.expand_factor = 1

    runtime_config = {}
    results = service.match(
        query_embedding=[],
        query="星河 数据分析",
        mode="fragment",
        top_k=2,
        runtime_config=runtime_config,
        vector_min_score=0.0,
    )

    vector_store.text_search.assert_not_called()
    assert {result.profile_key for result in results} == {
        "worker:default",
        "second:default",
    }
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["keyword_source"] == "local_snapshot"
    assert diagnostics["local_keyword_sufficient"] is True
    assert diagnostics["keyword_unavailable"] is False


def test_insufficient_local_keyword_still_uses_remote_keyword_candidates(tmp_path):
    from app.domain.models.vector_search_hit import VectorSearchHit

    vector_store = Mock()
    vector_store.size.return_value = 2
    vector_store.search.return_value = [
        VectorSearchHit(
            id="worker:default:full",
            score=0.8,
            payload={
                "profile_key": "worker:default",
                "worker_id": "worker",
                "profile_id": "default",
                "fragment_type": "full",
                "content": "dense semantic result",
                "runtime_state": "online",
                "availability": "public",
            },
        )
    ]
    vector_store.text_search.return_value = [
        VectorSearchHit(
            id="second:default:full",
            score=1.0,
            payload={
                "profile_key": "second:default",
                "worker_id": "second",
                "profile_id": "default",
                "fragment_type": "full",
                "content": "星河 数据分析平台",
                "runtime_state": "online",
                "availability": "public",
            },
        )
    ]
    service = service_for(
        vector_store,
        RegexFallbackIndex([fallback_point("worker:default", "星河 数据分析助手")]),
        tmp_path,
    )
    service._fragment_config.expand_factor = 1

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="fragment",
        top_k=2,
        runtime_config=runtime_config,
        vector_min_score=0.0,
    )

    assert vector_store.text_search.call_count == 1
    assert {result.profile_key for result in results} == {
        "worker:default",
        "second:default",
    }
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["keyword_source"] == "hybrid_local"
    assert diagnostics["local_keyword_sufficient"] is False
    assert diagnostics["local_keyword_count"] == 1


def test_local_keyword_path_uses_final_topk_not_expand_budget_for_sufficiency(
    tmp_path,
):
    from app.domain.models.vector_search_hit import VectorSearchHit

    vector_store = Mock()
    vector_store.size.return_value = 3
    vector_store.search.return_value = [
        VectorSearchHit(
            id="worker:default:full",
            score=0.8,
            payload={
                "profile_key": f"worker:{index}:default",
                "worker_id": f"worker:{index}",
                "profile_id": "default",
                "fragment_type": "full",
                "content": "dense semantic result",
                "runtime_state": "online",
                "availability": "public",
            },
        )
        for index in range(3)
    ]
    vector_store.text_search.side_effect = AssertionError(
        "expanded keyword budget must not force a Qdrant lookup"
    )
    service = service_for(
        vector_store,
        RegexFallbackIndex(
            [
                fallback_point(
                    f"local:{index}:default", f"星河 数据分析专家 {index}"
                )
                for index in range(3)
            ]
        ),
        tmp_path,
    )
    service._fragment_config.expand_factor = 3

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        runtime_config=runtime_config,
        vector_min_score=0.0,
    )

    vector_store.text_search.assert_not_called()
    assert len(results) >= 3
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["keyword_source"] == "local_snapshot"
    assert diagnostics["local_keyword_sufficient"] is True


def test_fragment_regex_fallback_overwrites_precomputed_dense_provenance(tmp_path):
    vector_store = Mock()
    vector_store.size.return_value = 1
    vector_store.search.return_value = []
    vector_store.text_search.side_effect = RuntimeError("keyword unavailable")
    service = service_for(
        vector_store, RegexFallbackIndex([fallback_point()]), tmp_path
    )
    service._fragment_config.enable_fragment_embedding = True

    runtime_config = {}
    results = service.match(
        query_embedding=[1.0],
        query="星河 数据分析",
        mode="fragment",
        top_k=3,
        vector_min_score=0.9,
        runtime_config=runtime_config,
    )

    assert [result.profile_key for result in results] == ["worker:default"]
    diagnostics = runtime_config["_retrieval"]
    assert diagnostics["score_source"] == "regex_fallback"
    assert diagnostics["literal_fallback_used"] is True
    assert diagnostics["candidate_source"] == "literal_fallback"
