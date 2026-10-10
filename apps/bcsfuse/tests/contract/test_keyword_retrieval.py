"""Keyword recall must survive filters, lifecycle changes and rerank failures."""

import math

import pytest

from src.application.services.worker_vector_match_service import (
    FragmentRetrievalConfig,
    WorkerVectorMatchService,
)
from src.domain.models.vector_point import VectorPoint
from src.domain.services.fragment_reranker_service import FragmentRerankerService
from src.domain.services.vector_persistence_backend import VectorChangeSet
from src.infra.metadatastores.file_metadata_store_adapter import (
    FileMetadataStoreAdapter,
)
from src.infra.public.vectorstores.qdrant_mysql_vector_store import (
    QdrantMySQLVectorStore,
)
from tests.unit.infra.test_qdrant_durable_vector_store import FakePersistenceBackend


def point(worker, content, *, kind="full", similarity=0.1, **payload):
    return VectorPoint(
        id=f"{worker}:default:{kind}",
        vector=[similarity, math.sqrt(1 - similarity**2)],
        payload={
            "worker_id": worker,
            "profile_id": "default",
            "profile_key": f"{worker}:default",
            "fragment_type": kind,
            "runtime_state": "online",
            "availability": "public",
            "content": content,
            **payload,
        },
    )


@pytest.fixture
def store(tmp_path):
    instance = QdrantMySQLVectorStore(
        dimension=2,
        qdrant_path=str(tmp_path / "vectors"),
        persistence_backend=FakePersistenceBackend(),
    )
    yield instance
    instance.close()


def test_chinese_name_and_numeric_id_work_for_historical_payload(store):
    store.upsert([point("bot:12345", "名称: 星河测试3\n身份: 数据工程助手")])
    hits = store.text_search("12345星河:", 10)
    assert [h.id for h in hits] == ["bot:12345:default:full"]
    assert hits[0].score > 0
    assert store.text_search("星河", 10)
    assert store.text_search("1234", 10) == []  # numeric IDs are whole tokens


def test_filters_apply_before_top_k_and_exact_id_does_not_bypass_them(store):
    store.upsert(
        [
            point(f"secret{i}:12345", "星河 " * 20, availability="private")
            for i in range(30)
        ]
        + [
            point("offline:12345", "星河", runtime_state="offline"),
            point("allowed:12345", "星河", availability="protected"),
        ]
    )
    filters = {"availability": ["public", "protected"], "runtime_state": ["online"]}
    assert [
        h.payload["worker_id"] for h in store.text_search("12345星河", 1, filters)
    ] == ["allowed:12345"]
    assert all(
        h.payload["availability"] != "private"
        for h in store.text_search("secret0:12345", 1, filters)
    )


@pytest.mark.parametrize("requested", ["backend", ["backend"], ["frontend", "backend"]])
def test_keyword_candidates_preserve_list_payload_matches(store, requested):
    from src.application.services.keyword_candidate_selection import keyword_candidates

    store.upsert([
        point("match", "编程", domains=["backend", "data"]),
        point("wrong-domain", "编程", domains=["mobile"]),
        point("offline", "编程", domains=["backend"], runtime_state="offline"),
    ])
    candidates = keyword_candidates(
        store, "编程", 10,
        {"domains": requested, "runtime_state": ["online"]},
        set(), None, {"full"},
    )
    assert [candidate.profile_key for candidate in candidates] == ["match:default"]


@pytest.mark.parametrize("metadata,requested,expected", [
    ({"domains": ["backend", "data"]}, ["mobile", "backend"], True),
    ({"domains": ["backend"]}, "backend", True),
    ({"domains": "backend"}, ["backend"], True),
    ({"domains": "backend"}, "backend", True),
    ({"domains": ["mobile"]}, ["backend"], False),
    ({"domains": []}, ["backend"], False),
    ({"domains": ["backend"]}, [], False),
    ({}, ["backend"], False),
    ({"domains": None}, ["backend"], False),
    ({"domains": "backend-services"}, "backend", False),
])
def test_keyword_legacy_filter_uses_contains_any(metadata, requested, expected):
    from src.application.services.keyword_candidate_selection import _group_keyword_hits
    from src.domain.models.vector_search_hit import VectorSearchHit

    # Legacy providers may return unfiltered hits; application filtering must
    # preserve valid array matches without admitting mismatches or substrings.
    hit = VectorSearchHit(
        id="worker:default:full", score=1.0,
        payload={"profile_key": "worker:default", "fragment_type": "full", **metadata},
    )
    candidates = _group_keyword_hits(
        [hit], {"domains": requested}, set(), None, {"full"}
    )
    assert list(candidates) == (["worker:default"] if expected else [])


def test_exact_worker_id_first_and_preview_only_records_searchable(store):
    store.upsert(
        [
            point("bot:12345", "", content_preview="星河助手"),
            point("other", "bot 12345 " * 10),
        ]
    )
    assert store.text_search("bot:12345", 1)[0].payload["worker_id"] == "bot:12345"
    assert store.text_search("星河", 1)[0].id == "bot:12345:default:full"
    assert store.text_search("   ", 10) == []


@pytest.mark.parametrize("fragment_count", [3, 130])
def test_keyword_budget_counts_unique_profiles_not_fragments(store, fragment_count):
    from src.application.services.keyword_candidate_selection import keyword_candidates

    store.upsert(
        [point("many", "编程 " * 10, kind=f"skills:{i}", fragment_type="skills")
         for i in range(fragment_count)]
        + [point("second", "编程 编程"), point("third", "编程")]
        + [point("hidden", "编程 " * 20, availability="private")]
    )
    filters = {"runtime_state": ["online"], "availability": ["public"]}
    candidates = keyword_candidates(
        store, "编程", 3, filters, set(), None, {"full", "skills"}
    )
    assert [candidate.profile_key for candidate in candidates] == [
        "many:default", "second:default", "third:default",
    ]
    # Exhaustion returns fewer unique profiles, never duplicate padding.
    assert len(keyword_candidates(
        store, "编程", 10, filters, set(), None, {"full", "skills"}
    )) == 3
    assert len(candidates[0].fragments) == fragment_count


def test_exact_id_budget_counts_profiles_and_preserves_priority(store):
    from src.application.services.keyword_candidate_selection import keyword_candidates

    store.upsert(
        [point("target", "irrelevant", kind=f"skills:{i}", fragment_type="skills")
         for i in range(130)]
        + [VectorPoint(id="target:other:full", vector=[0., 1.], payload={
            "worker_id": "target", "profile_id": "other", "profile_key": "target:other",
            "fragment_type": "full", "content": "irrelevant", "runtime_state": "online",
        }), point("other", "target " * 20)]
    )
    candidates = keyword_candidates(
        store, "target", 3, {"runtime_state": ["online"]}, set(), None,
        {"full", "skills"},
    )
    assert {candidate.profile_key for candidate in candidates[:2]} == {
        "target:default", "target:other",
    }
    assert all(candidate.metadata["_keyword_exact_id"] for candidate in candidates[:2])
    assert candidates[2].profile_key == "other:default"


@pytest.mark.parametrize("scope", ["excluded", "allowed", "disabled_type"])
def test_keyword_refill_counts_only_eligible_profiles(store, scope):
    from src.application.services.keyword_candidate_selection import keyword_candidates

    store.upsert(
        [point("many", "编程 " * 10, kind=f"skills:{i}", fragment_type="skills")
         for i in range(10)]
        + [point("second", "编程 编程"), point("third", "编程")]
    )
    candidates = keyword_candidates(
        store, "编程", 2, None,
        {"many:default"} if scope == "excluded" else set(),
        {"second:default", "third:default"} if scope == "allowed" else None,
        {"full"} if scope == "disabled_type" else {"full", "skills"},
    )
    assert [candidate.profile_key for candidate in candidates] == [
        "second:default", "third:default",
    ]


def test_keyword_refill_failure_does_not_return_a_partial_pool(store, monkeypatch):
    from src.application.services.keyword_candidate_selection import keyword_candidates

    store.upsert([point("many", "编程", kind=f"skills:{i}", fragment_type="skills")
                  for i in range(3)])
    search = store.text_search

    def interrupted_search(query, top_k, filters=None):
        if top_k > 2:
            raise RuntimeError("keyword index unavailable during refill")
        return search(query, top_k, filters)

    monkeypatch.setattr(store, "text_search", interrupted_search)
    assert keyword_candidates(store, "编程", 2, None, set(), None, {"skills"}) == []


def test_keyword_index_tracks_rebuild_update_delete_and_tombstones(store):
    backend = store._persistence
    backend.save(point("bot", "旧名字"))
    store.rebuild_from_backend()
    assert store.text_search("旧名字", 10)
    store.upsert([point("bot", "新名称")])
    assert store.text_search("旧名字", 10) == []
    assert store.text_search("新名称", 10)
    backend.changes = VectorChangeSet(deleted_ids=["bot:default:full"], checkpoint=1)
    store.sync_incremental()
    assert store.text_search("新名称", 10) == []
    backend.changes = VectorChangeSet(checkpoint=2)
    store.rebuild_from_backend()
    assert store.text_search("新名称", 10)
    store.update_payload_by_worker("bot", {"runtime_state": "offline"})
    assert store.text_search("新名称", 10, {"runtime_state": ["online"]}) == []
    store.delete_by_worker("bot")
    assert store.text_search("新名称", 10) == []


def test_keyword_write_failure_requires_rebuild_and_preserves_durable_payload(
    store, monkeypatch
):
    def fail(*args, **kwargs):
        raise RuntimeError("keyword index unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(store._keywords, "upsert", fail)
        with pytest.raises(RuntimeError, match="REBUILD_REQUIRED"):
            store.upsert([point("bot", "星河助手")])
    with pytest.raises(RuntimeError, match="requires rebuild"):
        store.text_search("星河", 10)
    persisted = store._persistence.points["bot:default:full"]
    assert not any(key.startswith("_keyword") for key in persisted.payload)
    store.rebuild_from_backend()
    assert [hit.id for hit in store.text_search("星河", 10)] == [persisted.id]


class Model:
    def __init__(self, mode):
        self.mode = mode
        self.inputs = []

    def rerank(self, query, candidates, top_k):
        self.inputs.extend(c["id"] for c in candidates)
        if self.mode in {"malformed", "boolean"} or (
            self.mode in {"malformed_partial", "boolean_partial"}
            and any(c["id"] == "target:12345:default" for c in candidates)
        ):
            return [
                {"id": c["id"], **({"score": True} if "boolean" in self.mode else {})}
                for c in candidates[:top_k]
            ]
        if self.mode == "fail" or (
            self.mode == "partial"
            and any(c["id"] == "target:12345:default" for c in candidates)
        ):
            raise RuntimeError("model unavailable")
        return sorted(
            [
                {
                    "id": c["id"],
                    "score": (0.9 if c["id"] == "target:12345:default" else 0.0),
                }
                for c in candidates
            ],
            key=lambda r: -r["score"],
        )[:top_k]


def service_for(store, tmp_path, mode="ok"):
    service = WorkerVectorMatchService(
        vector_store=store,
        metadata_store=FileMetadataStoreAdapter(storage_dir=str(tmp_path / "metadata")),
        fragment_config=FragmentRetrievalConfig(aggregation_strategy="weighted_sum"),
    )
    model = Model(mode)
    service._reranker_service = FragmentRerankerService(reranker=model)
    service._fragment_config.reranker_model = "controlled"
    return service, model


def run(service, rerank, *, query="12345星河:", top_k=3, vector_threshold=0.01):
    config = {"expand_factor": 2, "reranker_model": "controlled" if rerank else None}
    results = service.match(
        query_embedding=[1.0, 0.0],
        query=query,
        mode="fragment",
        top_k=top_k,
        filters={"availability": ["public", "protected"], "runtime_state": ["online"]},
        vector_min_score=vector_threshold,
        rerank_min_score=0.5,
        runtime_config=config,
    )
    return results, config


def seed(store):
    store.upsert(
        [
            point(f"dense{i}", "数据分析", kind="profile", similarity=0.98 - i * 0.001)
            for i in range(160)
        ]
        + [
            point("target:12345", "名称: 星河测试", similarity=-0.9),
            point("target:12345", "名称: 星河测试", kind="profile", similarity=-0.8),
        ]
    )


@pytest.mark.parametrize("policy", ["empty", "degrade"])
@pytest.mark.parametrize("failure", ["model", "exception", "unavailable", "degraded"])
def test_configured_rerank_failure_policy_is_preserved(
    store, tmp_path, policy, failure
):
    from src.domain.services.fragment_reranker_service import RerankFailAction

    seed(store)
    service, model = service_for(store, tmp_path, "fail")
    service._fragment_config.reranker_fail_action = policy
    service._reranker_service = FragmentRerankerService(
        reranker=model,
        fail_action=RerankFailAction.DEGRADE
        if failure == "degraded"
        else RerankFailAction(policy),
    )
    if failure == "unavailable":
        service._reranker_service = None
    elif failure == "exception":

        class FailingAdapter:
            def rerank(self, request):
                raise RuntimeError("adapter failure")

        service._reranker_service = FailingAdapter()
    baseline, _ = run(service, False)
    assert baseline
    results, config = run(service, True)
    assert config["_retrieval"]["rerank_degraded"] is True
    assert [(r.profile_key, r.score) for r in results] == (
        [] if policy == "empty" else [(r.profile_key, r.score) for r in baseline]
    )


def test_keyword_only_candidate_enters_bounded_deduplicated_rerank(store, tmp_path):
    seed(store)
    service, model = service_for(store, tmp_path)
    results, config = run(service, True)
    assert results[0].profile_key == "target:12345:default"
    assert results[0].score == 0.9
    assert len(model.inputs) <= 6
    assert len(model.inputs) == len(set(model.inputs))
    assert config["_retrieval"]["score_source"] == "reranker"


@pytest.mark.parametrize(
    "mode",
    [
        "fail",
        "partial",
        "unavailable",
        "malformed",
        "malformed_partial",
        "boolean",
        "boolean_partial",
    ],
)
def test_rerank_failure_is_identical_to_disabled_in_order_scores_and_threshold(
    store, tmp_path, mode
):
    seed(store)
    service, _ = service_for(store, tmp_path, mode)
    if mode in {"partial", "malformed_partial", "boolean_partial"}:
        service._reranker_service.MAX_BATCH_SIZE = 2
    if mode == "unavailable":
        service._reranker_service = None
    baseline, baseline_config = run(service, False)
    fallback, config = run(service, True)
    assert any(r.profile_key == "target:12345:default" for r in baseline)
    assert [(r.profile_key, r.score) for r in fallback] == [
        (r.profile_key, r.score) for r in baseline
    ]
    assert all(not r.is_reranked for r in fallback)
    assert config["_retrieval"]["rerank_degraded"] is True
    assert config["_retrieval"]["score_source"] == "hybrid_rrf"
    assert baseline_config["_retrieval"]["rerank_degraded"] is False


def test_keyword_score_is_not_subject_to_vector_or_rerank_threshold_when_disabled(
    store, tmp_path
):
    seed(store)
    service, _ = service_for(store, tmp_path)
    results, _ = run(service, False, vector_threshold=0.99)
    assert [r.profile_key for r in results] == ["target:12345:default"]


def test_full_worker_id_priority_without_rerank(store, tmp_path):
    seed(store)
    service, _ = service_for(store, tmp_path)
    results, _ = run(service, False, query="target:12345")
    assert results[0].profile_key == "target:12345:default"
    assert results[0].score == 1.0


def test_keyword_outage_preserves_dense_order_and_scores(store, tmp_path, monkeypatch):
    seed(store)
    service, _ = service_for(store, tmp_path)
    baseline, _ = run(service, False, query="完全不匹配")

    def unavailable(*args, **kwargs):
        raise RuntimeError("keyword unavailable")

    monkeypatch.setattr(store, "text_search", unavailable)
    actual, config = run(service, False)
    assert [(r.profile_key, r.score) for r in actual] == [
        (r.profile_key, r.score) for r in baseline
    ]
    assert config["_retrieval"]["score_source"] == "vector_weighted"


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"scores": []},
        {"scores": [float("nan")]},
        {"results": [{"index": 2, "relevance_score": 0.9}]},
        {"scores": [0.1, 0.2]},
    ],
)
def test_http_model_rejects_invalid_results_instead_of_fabricating_scores(response):
    from src.infra.reranker.http_reranker import HttpReranker

    with pytest.raises(ValueError):
        HttpReranker()._parse_response(response, 1)


@pytest.mark.parametrize("mode", ["missing_key", "http_error", "zero", "malformed"])
def test_actual_http_adapter_propagates_failure_but_zero_is_a_valid_model_score(
    store,
    tmp_path,
    monkeypatch,
    mode,
):
    import requests

    from src.infra.reranker.http_reranker import HttpReranker

    seed(store)
    monkeypatch.setenv(
        "RERANKER_API_KEY", "" if mode == "missing_key" else "test-placeholder"
    )
    monkeypatch.setenv("RERANKER_BASE_URL", "https://reranker.example.test")

    class Response:
        def raise_for_status(self):
            if mode == "http_error":
                raise requests.HTTPError("model unavailable")

        def json(self):
            return {"scores": [0.0] * 6} if mode == "zero" else {}

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: Response())
    service, _ = service_for(store, tmp_path)
    service._reranker_service = FragmentRerankerService(reranker=HttpReranker())
    baseline, _ = run(service, False)
    results, config = run(service, True)
    if mode == "zero":
        assert results == []  # Valid zero model scores fail the rerank threshold.
        assert config["_retrieval"]["score_source"] == "reranker"
        assert not config["_retrieval"]["rerank_degraded"]
    else:
        assert [(r.profile_key, r.score) for r in results] == [
            (r.profile_key, r.score) for r in baseline
        ]
        assert config["_retrieval"]["rerank_degraded"]
