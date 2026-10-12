"""INFO describes retrieval decisions, not dependency construction internals."""

import logging

import pytest

from src.bootstrap.logging_setup import configure_business_logging


@pytest.fixture(autouse=True)
def restore_logger_state():
    previous_factory = logging.getLogRecordFactory()
    previous_levels = {
        name: logger.level for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    yield
    for name, logger in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(logger, logging.Logger):
            logger.setLevel(previous_levels.get(name, logging.NOTSET))
    logging.setLogRecordFactory(previous_factory)


@pytest.mark.parametrize("name", [
    "src.interfaces.api.dependencies.fusion_dependencies",
    "src.interfaces.api.dependencies.worker_dependencies",
    "src.infra.public.vectorstores.qdrant_local_vector_store",
    "src.infra.embedding.providers.real_provider",
])
def test_infrastructure_detail_requires_debug_but_warnings_always_visible(caplog, name):
    caplog.set_level(logging.DEBUG)
    logger = logging.getLogger(name)
    configure_business_logging(logging.INFO)
    logger.info("initialization-detail")
    logger.warning("provider-degraded")
    assert "initialization-detail" not in caplog.text
    assert "provider-degraded" in caplog.text
    caplog.clear()
    configure_business_logging(logging.DEBUG)
    logger.info("initialization-detail")
    logger.debug("diagnostic-detail")
    assert "initialization-detail" in caplog.text
    assert "diagnostic-detail" in caplog.text
    caplog.clear()
    configure_business_logging(logging.INFO)
    logger.info("initialization-detail")
    assert not caplog.records


def test_http_request_chatter_is_hidden_without_losing_warnings(caplog):
    caplog.set_level(logging.DEBUG)
    configure_business_logging(logging.DEBUG)
    logging.getLogger("httpx").info("request-url-detail")
    logging.getLogger("httpx").warning("request-failed")
    assert "request-url-detail" not in caplog.text
    assert "request-failed" in caplog.text


@pytest.mark.parametrize("rerank_enabled,fail", [(True, False), (False, False), (True, True)])
def test_real_pipeline_info_is_ordered_and_reports_request_rerank(
    tmp_path, caplog, monkeypatch, rerank_enabled, fail,
):
    from src.application.services.worker_vector_match_service import (
        WorkerVectorMatchService, FragmentRetrievalConfig,
    )
    from src.domain.models.vector_point import VectorPoint
    from src.infra.metadatastores.file_metadata_store_adapter import FileMetadataStoreAdapter
    from src.infra.public.vectorstores.qdrant_mysql_vector_store import QdrantMySQLVectorStore
    from src.infra import reranker as reranker_provider
    from tests.unit.infra.test_qdrant_durable_vector_store import FakePersistenceBackend

    class FixedModel:
        def rerank(self, query, candidates, top_k):
            if fail:
                raise RuntimeError("model-unavailable")
            return [{"id": candidate["id"], "score": 0.8} for candidate in candidates[:top_k]]

    monkeypatch.setattr(reranker_provider, "get_reranker", FixedModel)
    vectors = QdrantMySQLVectorStore(
        dimension=2, qdrant_path=str(tmp_path / "vectors"),
        persistence_backend=FakePersistenceBackend(),
    )
    vectors.upsert([VectorPoint(id="bot:1:default:full", vector=[1.0, 0.0], payload={
        "worker_id": "bot:1", "profile_id": "default", "profile_key": "bot:1:default",
        "fragment_type": "full", "runtime_state": "online", "availability": "public",
        "content": "private-profile-sentinel",
    })])
    caplog.set_level(logging.INFO)
    configure_business_logging(logging.INFO)
    caplog.clear()
    try:
        service = WorkerVectorMatchService(
            vector_store=vectors,
            metadata_store=FileMetadataStoreAdapter(storage_dir=str(tmp_path / "metadata")),
            fragment_config=FragmentRetrievalConfig(aggregation_strategy="max"),
        )
        results = service.match(
            query_embedding=[1.0, 0.0], query="private-query-sentinel", top_k=10,
            mode="fragment", vector_min_score=0.01, rerank_min_score=0.005,
            runtime_config={"reranker_model": "test-model" if rerank_enabled else None},
        )
        assert [result.profile_key for result in results] == ["bot:1:default"]
        if rerank_enabled and not fail:
            assert results[0].score == 0.8
        stages = [r for r in caplog.records if hasattr(r, "retrieval_stage")]
        names = [r.retrieval_stage for r in stages]
        selection = next(r for r in stages if r.retrieval_stage == "candidate_selection")
        assert selection.rerank_enabled is rerank_enabled
        assert selection.rerank_input_count == (1 if rerank_enabled else 0)
        if rerank_enabled and not fail:
            assert names.index("candidate_selection") < names.index("reranker")
            rerank = next(r for r in stages if r.retrieval_stage == "reranker")
            assert rerank.input_count == 1 and rerank.output_count == 1
            assert rerank.degraded_count == 0
        if fail:
            assert any(r.levelno >= logging.WARNING and "model-unavailable" in r.getMessage()
                       for r in caplog.records)
        assert "Rerank disabled" not in caplog.text
        assert "qdrant_collection_ready" not in caplog.text
        assert "before_score_sample" not in caplog.text
        assert "private-profile-sentinel" not in caplog.text
        assert "private-query-sentinel" not in caplog.text
        assert all(r.getMessage().startswith("[RETRIEVAL")
                   for r in caplog.records if r.levelno == logging.INFO)
    finally:
        vectors.close()
