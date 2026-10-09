"""Request logging must correlate the real pipeline without exposing content."""

import io
import logging
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.domain.models.profile_fragment import FragmentMatch
from src.domain.services.fragment_reranker_service import (
    FragmentRerankerService, ProfileCandidate, RerankRequest,
)
from src.infra.public.observability.trace_middleware import TraceIdMiddleware
from src.infra.trace_context import bind_trace_id, get_trace_id, install_trace_record_factory


@pytest.fixture(autouse=True)
def restore_logging_factory():
    previous = logging.getLogRecordFactory()
    yield
    logging.setLogRecordFactory(previous)


def test_composed_trace_reaches_formatted_child_logs_without_configure_logging():
    # Model the internal runner: logging is configured externally, not by main.py.
    logging.setLogRecordFactory(logging.LogRecord)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("[%(traceid)s] %(message)s"))

    class ExternalTraceFallback(logging.Filter):
        def filter(self, record):
            if not hasattr(record, "traceid"):
                record.traceid = "-"
            return True

    handler.addFilter(ExternalTraceFallback())
    child = logging.getLogger("src.application.services.trace_probe")
    previous_level = child.level
    child.setLevel(logging.INFO)
    child.addHandler(handler)
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)

    @app.get("/probe")
    async def probe():
        child.info("matching candidates")
        return {"trace": get_trace_id()}

    try:
        with TestClient(app) as client:
            response = client.get("/probe", headers={"X-Trace-ID": "request-probe"})
        assert response.json() == {"trace": "request-probe"}
        assert "[request-probe] matching candidates" in stream.getvalue()
        child.info("outside request")
        assert "[-] outside request" in stream.getvalue()
    finally:
        child.removeHandler(handler)
        child.setLevel(previous_level)


def test_trace_install_preserves_existing_factory_and_is_idempotent():
    def external_factory(*args, **kwargs):
        record = logging.LogRecord(*args, **kwargs)
        record.external_marker = "preserved"
        return record

    logging.setLogRecordFactory(external_factory)
    install_trace_record_factory()
    first = logging.getLogRecordFactory()
    install_trace_record_factory()
    assert logging.getLogRecordFactory() is first
    with bind_trace_id("factory-probe"):
        record = first("probe", logging.INFO, __file__, 1, "message", (), None)
    assert record.external_marker == "preserved"
    assert record.traceid == "factory-probe"


def test_trace_factory_preserves_host_trace_outside_requests():
    def host_factory(*args, **kwargs):
        record = logging.LogRecord(*args, **kwargs)
        record.traceid = "host-background-trace"
        return record

    logging.setLogRecordFactory(host_factory)
    install_trace_record_factory()
    with bind_trace_id(""):
        record = logging.getLogRecordFactory()("probe", logging.INFO, __file__, 1, "message", (), None)
    assert record.traceid == "host-background-trace"


def test_candidate_details_are_debug_only_chunked_and_complete(caplog):
    import json
    from src.domain.services.retrieval_logging import log_candidates

    logger = logging.getLogger("src.application.services.diagnostic_probe")
    caplog.set_level(logging.INFO, logger=logger.name)
    log_candidates(logger, "reranker_input", ((f"bot:{i}:default", 0.5) for i in range(60)))
    assert not caplog.records
    caplog.set_level(logging.DEBUG, logger=logger.name)
    log_candidates(logger, "reranker_input", ((f"bot:{i}:default", 0.5) for i in range(60)))
    chunks = [json.loads(r.getMessage().split("entries=", 1)[1]) for r in caplog.records]
    assert [len(chunk) for chunk in chunks] == [25, 25, 10]
    entries = [entry for chunk in chunks for entry in chunk]
    assert [entry["key"] for entry in entries] == [f"bot:{i}:default" for i in range(60)]


def _candidate(key, content="private-profile-sentinel"):
    return ProfileCandidate(key, 0.4, [FragmentMatch(
        fragment_type="full", fragment_id=key + ":full", score=0.4,
        weighted_score=0.04, content=content, content_preview=content,
    )])


def test_parallel_rerank_keeps_each_requests_context(monkeypatch):
    observed = []

    class ContextCheckingReranker:
        def rerank(self, query, candidates, top_k):
            observed.append((query, get_trace_id()))
            return [{"id": candidate["id"], "score": 0.8} for candidate in candidates]

    service = FragmentRerankerService(reranker=ContextCheckingReranker())
    monkeypatch.setattr(service, "MAX_BATCH_SIZE", 1)

    def request(trace):
        with bind_trace_id(trace):
            results = service.rerank(RerankRequest(trace, [_candidate("bot:1"), _candidate("bot:2")], 2))
            assert len(results) == 2
        assert get_trace_id() == ""

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(request, ["trace-one", "trace-two"]))
    assert sorted(observed) == [
        ("trace-one", "trace-one"), ("trace-one", "trace-one"),
        ("trace-two", "trace-two"), ("trace-two", "trace-two"),
    ]


def test_rerank_logs_never_dump_profile_text_even_at_debug(caplog):
    class FixedReranker:
        def rerank(self, query, candidates, top_k):
            return [{"id": candidates[0]["id"], "score": 0.8}]

    caplog.set_level(logging.DEBUG, logger="src")
    results = FragmentRerankerService(reranker=FixedReranker()).rerank(
        RerankRequest("private-query-sentinel", [_candidate("bot:1:default")], 1)
    )
    assert results[0].final_score == 0.8
    assert "private-profile-sentinel" not in caplog.text
    assert "private-query-sentinel" not in caplog.text


def test_pipeline_logs_counts_and_distinct_removed_profile_keys(tmp_path, caplog):
    from src.application.services.worker_vector_match_service import (
        WorkerVectorMatchService, FragmentRetrievalConfig,
    )
    from src.domain.models.vector_point import VectorPoint
    from src.infra.metadatastores.file_metadata_store_adapter import FileMetadataStoreAdapter
    from src.infra.public.vectorstores.qdrant_mysql_vector_store import QdrantMySQLVectorStore
    from tests.unit.infra.test_qdrant_durable_vector_store import FakePersistenceBackend

    vectors = QdrantMySQLVectorStore(
        dimension=2, qdrant_path=str(tmp_path / "vectors"),
        persistence_backend=FakePersistenceBackend(),
    )
    # Both keys end in default: diagnostics must not log just the last segment.
    for worker, vector in [("bot:kept", [1.0, 0.0]), ("bot:removed", [0.6, 0.8])]:
        key = worker + ":default"
        vectors.upsert([VectorPoint(id=key + ":full", vector=vector, payload={
            "profile_key": key, "worker_id": worker, "profile_id": "default",
            "fragment_type": "full", "runtime_state": "online", "availability": "public",
            "content": "private-profile-sentinel", "short_profile": "private-summary-sentinel",
        })])
    service = WorkerVectorMatchService(
        vector_store=vectors, metadata_store=FileMetadataStoreAdapter(storage_dir=str(tmp_path / "metadata")),
        fragment_config=FragmentRetrievalConfig(aggregation_strategy="max"),
    )
    caplog.set_level(logging.DEBUG, logger="src")
    try:
        # Exercise dense threshold logging without lexical matches rescuing rows.
        results = service.match(query_embedding=[1.0, 0.0], query="unmatched-query",
                                top_k=10, mode="fragment", vector_min_score=0.08)
        assert [result.profile_key for result in results] == ["bot:kept:default"]
        stages = {getattr(record, "retrieval_stage", ""): record for record in caplog.records}
        assert stages["vector_search"].hit_count == 2
        assert stages["candidate_selection"].candidate_count == 2
        assert stages["threshold"].before_count == 2
        assert stages["threshold"].after_count == 1
        # Explicit INFO decision records are separate from the count summary.
        assert "bot:removed:default" not in stages["threshold"].getMessage()
        assert any(r.levelno == logging.INFO and "threshold_removed" in r.getMessage()
                   and "bot:removed:default" in r.getMessage() for r in caplog.records)
        assert "bot:removed:default" in caplog.text
        assert "private-profile-sentinel" not in caplog.text
        assert "private-summary-sentinel" not in caplog.text
        assert "private-query-sentinel" not in caplog.text
    finally:
        vectors.close()


@pytest.mark.asyncio
async def test_recommend_route_logs_summary_without_query_or_response_content(monkeypatch, caplog):
    from src.interfaces.api import recommend_routes
    from src.domain.models.bot_recommendation import BotRecommendationRequest
    from src.domain.models.candidate_recommendation import CandidateRecommendation, CandidateRecommendationResponse
    from src.domain.models.retrieval_mode import RetrievalMode

    class CandidateService:
        _vector_match_service = object()
        _embedding_generator = object()

        def recommend(self, **kwargs):
            return CandidateRecommendationResponse(
                question=kwargs["question"], mode=RetrievalMode.EXPERT_DIAGNOSIS,
                recommendations=[CandidateRecommendation(
                    profile_key="bot:1:default", worker_id="bot:1", score=0.8,
                    reasons=["private-reason-sentinel"], short_profile="private-summary-sentinel",
                )], total_candidates=1, selected_candidates=1,
            )

    monkeypatch.setattr(recommend_routes, "get_candidate_recommendation_service", CandidateService)
    caplog.set_level(logging.DEBUG, logger="src")
    response = await recommend_routes.recommend_bots(BotRecommendationRequest(
        question="private-query-sentinel", topK=10, min_score=0.005, enable_rerank=True,
    ))
    assert response.recommendations[0].score == 0.8
    assert response.recommendations[0].reasons == ["private-reason-sentinel"]
    assert "private-query-sentinel" not in caplog.text
    assert "private-summary-sentinel" not in caplog.text
    assert "private-reason-sentinel" not in caplog.text
