"""Bounded raw-max/weighted recall with production-visible elimination evidence."""

import json
import logging
import math

import pytest

from src.application.services.worker_vector_match_service import (
    FragmentRetrievalConfig,
    WorkerVectorMatchService,
)
from src.domain.models.vector_point import VectorPoint
from src.domain.services.fragment_reranker_service import FragmentRerankerService
from src.infra.metadatastores.file_metadata_store_adapter import FileMetadataStoreAdapter
from src.infra.public.vectorstores.qdrant_mysql_vector_store import QdrantMySQLVectorStore
from tests.unit.infra.test_qdrant_durable_vector_store import FakePersistenceBackend


class ControlledModel:
    def __init__(self, fail=False):
        self.inputs = []
        self.fail = fail

    def rerank(self, query, candidates, top_k):
        self.inputs.extend(candidate["id"] for candidate in candidates)
        if self.fail:
            raise RuntimeError("controlled-unavailable")
        # A rescued full-only candidate must survive through the actual service.
        return sorted(
            [{"id": c["id"], "score": 0.95 if c["id"] == "strong:default" else 0.5}
             for c in candidates],
            key=lambda item: item["score"], reverse=True,
        )[:top_k]


@pytest.fixture
def pipeline(tmp_path):
    store = QdrantMySQLVectorStore(
        dimension=2, qdrant_path=str(tmp_path / "vectors"),
        persistence_backend=FakePersistenceBackend(),
    )

    def build(rows, fail=False):
        points = []
        for worker, kind, similarity in rows:
            points.append(VectorPoint(
                id=f"{worker}:default:{kind}",
                vector=[similarity, math.sqrt(1 - similarity * similarity)],
                payload={"worker_id": worker, "profile_id": "default",
                         "profile_key": f"{worker}:default", "fragment_type": kind,
                         "runtime_state": "online", "availability": "public",
                         "content": "private-content-sentinel"},
            ))
        store.upsert(points)
        service = WorkerVectorMatchService(
            vector_store=store,
            metadata_store=FileMetadataStoreAdapter(storage_dir=str(tmp_path / "metadata")),
            fragment_config=FragmentRetrievalConfig(aggregation_strategy="weighted_sum"),
        )
        model = ControlledModel(fail)
        service._reranker_service = FragmentRerankerService(reranker=model)
        service._fragment_config.reranker_model = "controlled"
        return service, model

    yield build
    store.close()


def run(service, top_k=2, expand=2, rerank=True, **kwargs):
    return service.match(
        query_embedding=[1.0, 0.0], query="private-query-sentinel", mode="fragment",
        top_k=top_k, vector_min_score=0, rerank_min_score=0,
        runtime_config={"expand_factor": expand, "reranker_model": "controlled" if rerank else None},
        **kwargs,
    )


def test_raw_max_rescues_full_only_profile_without_increasing_rerank_budget(pipeline):
    service, model = pipeline([
        ("strong", "full", .99), ("second", "capabilities", .98),
        ("weighted1", "profile", .90), ("weighted2", "profile", .85),
        ("weighted3", "profile", .80), ("weighted4", "profile", .75),
    ])
    service._fragment_config.reranker_model = "controlled"
    results = run(service)
    assert set(model.inputs) == {
        "strong:default", "second:default", "weighted1:default", "weighted2:default",
    }
    assert len(model.inputs) == 4
    assert results[0].profile_key == "strong:default"


def test_overlapping_heads_refill_alternately_without_duplicate_profiles(pipeline):
    service, model = pipeline([
        ("a", "profile", .99), ("b", "profile", .98),
        ("c", "full", .97), ("d", "capabilities", .96),
        ("e", "profile", .90), ("f", "profile", .85),
        ("a", "full", .70),
    ])
    service._fragment_config.reranker_model = "controlled"
    run(service)
    assert set(model.inputs) == {"a:default", "b:default", "c:default", "e:default"}
    assert len(model.inputs) == len(set(model.inputs)) == 4


@pytest.mark.parametrize("top_k,expand,expected", [
    (1, 1, {"strong:default"}),
    (1, 3, {"strong:default", "second:default", "weighted:default"}),
    (2, 10, {"strong:default", "second:default", "weighted:default", "tail:default"}),
])
def test_odd_and_small_budgets_and_exhaustion(pipeline, top_k, expand, expected):
    service, model = pipeline([
        ("strong", "full", .99), ("second", "full", .98),
        ("weighted", "profile", .90), ("tail", "profile", .8),
    ])
    service._fragment_config.reranker_model = "controlled"
    run(service, top_k=top_k, expand=expand)
    assert set(model.inputs) == expected
    assert len(model.inputs) <= top_k * expand


@pytest.mark.parametrize("rerank,fail", [(False, False), (True, True)])
def test_no_rerank_or_failure_keeps_weighted_descending_order(pipeline, caplog, rerank, fail):
    service, model = pipeline([
        ("strong", "full", .99), ("second", "full", .98),
        ("weighted1", "profile", .90), ("weighted2", "profile", .85),
        ("weighted3", "profile", .8),
    ], fail=fail)
    service._fragment_config.reranker_model = "controlled"
    caplog.set_level(logging.INFO, logger="src.application.services.worker_vector_match_service")
    results = run(service, rerank=rerank)
    assert [r.profile_key for r in results] == ["weighted1:default", "weighted2:default"]
    assert results[0].score > results[1].score
    if not rerank:
        assert model.inputs == []
    if fail:
        records = [r for r in caplog.records if getattr(r, "retrieval_stage", "") == "reranker_returned"]
        assert records
        assert all(row[-1] == "aggregate_fallback" for r in records for row in r.entries)


def test_info_logs_explain_ranks_admission_and_elimination_without_content(pipeline, caplog):
    service, model = pipeline([
        ("strong", "full", .99), ("second", "full", .98),
        ("weighted1", "profile", .90), ("weighted2", "profile", .85),
        ("weighted3", "profile", .8), ("excluded", "profile", 1.0),
    ])
    service._fragment_config.reranker_model = "controlled"
    caplog.set_level(logging.INFO, logger="src.application.services.worker_vector_match_service")
    run(service, excluded_profile_keys=["excluded:default"])
    chunks = [r for r in caplog.records if getattr(r, "retrieval_stage", "") == "candidate_decisions"]
    entries = [entry for record in chunks for entry in record.entries]
    decisions = {entry[0]: dict(zip(chunks[0].fields, entry)) for entry in entries}
    assert decisions["strong:default"]["max_rank"] == 1
    assert decisions["strong:default"]["weighted_rank"] == 4
    assert decisions["strong:default"]["decision"] == "max_head"
    assert decisions["weighted3:default"]["decision"] == "budget_rejected"
    assert decisions["strong:default"]["max_score"] == pytest.approx(.99, abs=1e-5)
    assert "excluded:default" in caplog.text
    assert "reranker_not_returned" in caplog.text
    assert "before_threshold" in caplog.text
    assert "private-content-sentinel" not in caplog.text
    assert "private-query-sentinel" not in caplog.text
    assert "excluded:default" not in model.inputs
    assert all(record.levelno == logging.INFO for record in chunks)


def test_info_candidate_decisions_are_chunked_without_losing_tail(pipeline, caplog):
    service, _ = pipeline([(f"worker{i:03}", "full", .99 - i * .001) for i in range(61)])
    service._fragment_config.reranker_model = "controlled"
    caplog.set_level(logging.INFO, logger="src.application.services.worker_vector_match_service")
    run(service, top_k=2)
    chunks = [r for r in caplog.records if getattr(r, "retrieval_stage", "") == "candidate_decisions"]
    assert [len(r.entries) for r in chunks] == [25, 25, 11]
    assert len({entry[0] for record in chunks for entry in record.entries}) == 61
    assert all(json.loads(r.getMessage().split(" ", 2)[2])["entries"] for r in chunks)


def test_real_hundred_candidate_budget_is_split_without_expanding_model_load(pipeline):
    rows = [(f"max{i:03}", "full", .99 - i * .001) for i in range(55)]
    rows += [(f"weighted{i:03}", "profile", .85 - i * .001) for i in range(55)]
    service, model = pipeline(rows)
    run(service, top_k=10, expand=10)
    expected = {f"max{i:03}:default" for i in range(50)}
    expected |= {f"weighted{i:03}:default" for i in range(50)}
    assert set(model.inputs) == expected
    assert len(model.inputs) == 100


def test_unavailable_reranker_uses_weighted_order_not_max_admission_order(pipeline):
    service, _ = pipeline([
        ("strong", "full", .99), ("second", "full", .98),
        ("weighted1", "profile", .90), ("weighted2", "profile", .85),
    ])
    service._reranker_service = None
    results = run(service)
    assert [r.profile_key for r in results] == ["weighted1:default", "weighted2:default"]
    assert not any(r.is_reranked for r in results)


def test_tied_scores_have_stable_selection(pipeline):
    service, model = pipeline([("z", "full", .8), ("a", "full", .8)])
    run(service, top_k=1, expand=1)
    assert model.inputs == ["a:default"]


def test_empty_index_never_calls_reranker(pipeline):
    service, model = pipeline([])
    assert run(service) == []
    assert model.inputs == []
