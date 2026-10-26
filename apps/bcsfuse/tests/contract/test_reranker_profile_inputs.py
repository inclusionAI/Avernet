"""The wire request must honor the selected model and use only profile text."""

import logging

import pytest

from src.domain.models.profile_fragment import FragmentMatch
from src.domain.services.fragment_reranker_service import (
    FragmentRerankerService,
    ProfileCandidate,
    RerankRequest,
)
from src.infra.reranker.http_reranker import HttpReranker


QUERY = "UCT2.0 事件策略日志为空"
PREFIX = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"


def candidate(key="bot:1:default", *, with_profile=True):
    fragments = [FragmentMatch(
        fragment_type=kind, fragment_id=f"{key}:{kind}", score=0.7,
        content=content,
    ) for kind, content in [
        ("full", "full-default-skill-sentinel"),
        ("capabilities", "default-capability-sentinel"),
        ("skill_sets", "default-skill-sentinel"),
    ]]
    if with_profile:
        fragments.append(FragmentMatch(
            fragment_type="profile", fragment_id=f"{key}:profile", score=0.7,
            content="worker_id: bot:1\n【职责定位】\n名称: UCT20值班助手\n描述: 无\n身份: 无配置，无法提供身份。\n目标: 无配置，无法提供目标。",
        ))
    return ProfileCandidate(key, 0.45, fragments, {
        "display_name": "UCT20值班助手", "active_skills": ["default-skill-sentinel"],
    })


@pytest.fixture
def wire(monkeypatch):
    import requests

    monkeypatch.setenv("RERANKER_BASE_URL", "https://reranker.example.test/v1")
    monkeypatch.setenv("RERANKER_API_KEY", "test-placeholder")
    sent = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [
                {"index": i, "relevance_score": 0.8}
                for i, _ in enumerate(self.payload["documents"])
            ]}

    def post(url, *, headers, json, timeout):
        sent.append(json)
        return Response(json)

    monkeypatch.setattr(requests, "post", post)
    return sent


@pytest.mark.parametrize("configured,requested", [
    ("bge-reranker-v2-m3", "Qwen3-Reranker-4B"),
    ("Qwen3-Reranker-4B", "bge-reranker-v2-m3"),
])
def test_explicit_model_reaches_http_without_changing_process_default(
    monkeypatch, wire, configured, requested,
):
    import os

    monkeypatch.setenv("RERANKER_MODEL", configured)
    result = FragmentRerankerService(reranker_model=requested).rerank(
        RerankRequest(QUERY, [candidate()], 1)
    )
    assert wire[0]["model"] == requested
    assert result[0].rerank_metadata["reranker_model"] == requested
    assert os.environ["RERANKER_MODEL"] == configured


def test_qwen_wire_uses_exact_platform_template_and_clean_profile(monkeypatch, wire):
    monkeypatch.setenv("RERANKER_MODEL", "Qwen3-Reranker-4B")
    result = FragmentRerankerService(reranker_model="Qwen3-Reranker-4B").rerank(
        RerankRequest(QUERY, [candidate()], 1)
    )
    assert result[0].final_score == 0.8
    assert wire[0]["query"] == PREFIX + "<Instruct>: Given a web search query, retrieve relevant passages that answer the query\n<Query>: UCT2.0 事件策略日志为空\n<Document>: "
    assert wire[0]["documents"] == ["名称: UCT20值班助手" + SUFFIX]


@pytest.mark.parametrize("query,documents", [
    (QUERY, ["名称: UCT20值班助手"]),
    ("  查询\n下一行  ", ["  多行\n正文\t保留  ", "Passage: 原文自带前缀"]),
])
def test_bge_wire_preserves_original_query_and_documents(monkeypatch, wire, query, documents):
    monkeypatch.setenv("RERANKER_MODEL", "bge-reranker-v2-m3")
    result = HttpReranker().rerank(
        query, [{"id": str(i), "text": text} for i, text in enumerate(documents)], 1,
    )
    assert result[0].score == 0.8
    assert wire[0] == {
        "model": "bge-reranker-v2-m3", "query": query, "documents": documents,
    }


def test_missing_profile_uses_name_and_never_full_or_default_skills(monkeypatch, wire):
    monkeypatch.setenv("RERANKER_MODEL", "Qwen3-Reranker-4B")
    result = FragmentRerankerService(reranker_model="Qwen3-Reranker-4B").rerank(
        RerankRequest(QUERY, [candidate(with_profile=False)], 1)
    )
    assert len(result) == 1
    assert wire[0]["documents"] == ["名称: UCT20值班助手" + SUFFIX]


def test_debug_input_logging_does_not_emit_one_record_per_candidate(monkeypatch, wire, caplog):
    monkeypatch.setenv("RERANKER_MODEL", "Qwen3-Reranker-4B")
    caplog.set_level(logging.DEBUG, logger="src")
    result = FragmentRerankerService(reranker_model="Qwen3-Reranker-4B").rerank(
        RerankRequest(QUERY, [candidate(f"bot:{i}:default") for i in range(100)], 10)
    )
    assert len(result) == 10
    assert len([r for r in caplog.records if "[RERANKER-INPUT]" in r.getMessage()]) <= 1
    assert "UCT20值班助手" not in caplog.text


def test_prepub_defaults_to_info_but_explicit_debug_is_preserved(monkeypatch):
    from src.bootstrap.logging_setup import resolve_business_log_level

    monkeypatch.setenv("SERVER_ENV", "prepub")
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert resolve_business_log_level() == logging.INFO
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    assert resolve_business_log_level() == logging.DEBUG


def test_candidate_decision_rows_require_debug(caplog):
    from src.domain.services.retrieval_logging import log_rows

    logger = logging.getLogger("src.test.reranker_rows")
    caplog.set_level(logging.INFO, logger=logger.name)
    log_rows(logger, "candidate_decisions", ["id", "score"], [["bot:1", 0.8]])
    assert not caplog.records
    caplog.set_level(logging.DEBUG, logger=logger.name)
    log_rows(logger, "candidate_decisions", ["id", "score"], [["bot:1", 0.8]])
    assert len(caplog.records) == 1
    assert "bot:1" in caplog.records[0].getMessage()


@pytest.mark.parametrize("legacy_dict", [False, True])
@pytest.mark.parametrize("rerank", [False, True])
@pytest.mark.parametrize("config_updated", [False, True])
def test_full_only_recall_loads_profile_only_when_reranking(
    tmp_path, monkeypatch, wire, legacy_dict, rerank, config_updated,
):
    from src.application.services.worker_vector_match_service import (
        FragmentRetrievalConfig, WorkerVectorMatchService,
    )
    from src.domain.models.worker_profile_content import WorkerProfileContent
    from src.domain.models.vector_point import VectorPoint
    from src.infra.metadatastores.file_metadata_store_adapter import FileMetadataStoreAdapter
    from src.infra.public.vectorstores.qdrant_mysql_vector_store import QdrantMySQLVectorStore
    from tests.unit.infra.test_qdrant_durable_vector_store import FakePersistenceBackend

    calls = []
    record = WorkerProfileContent(
        worker_id="bot:1", profile_id="default", display_name="UCT20值班助手",
        contents={"profile": "描述: 排查 UCT 事件策略日志", "capabilities": ["ignored-capability"]},
    )

    class Profiles:
        def get(self, worker_id, profile_id):
            calls.append((worker_id, profile_id))
            return record.model_dump() if legacy_dict else record

    monkeypatch.setenv("RERANKER_MODEL", "bge-reranker-v2-m3")
    vectors = QdrantMySQLVectorStore(
        dimension=2, qdrant_path=str(tmp_path / "vectors"),
        persistence_backend=FakePersistenceBackend(),
    )
    vectors.upsert([VectorPoint(
        id="bot:1:default:full", vector=[1.0, 0.0],
        payload={"worker_id": "bot:1", "profile_id": "default", "fragment_type": "full",
                 "runtime_state": "online", "availability": "public",
                 "content": "default-skill-sentinel"},
    )])
    try:
        service = WorkerVectorMatchService(
            vector_store=vectors,
            metadata_store=FileMetadataStoreAdapter(storage_dir=str(tmp_path / "metadata")),
            fragment_config=FragmentRetrievalConfig(reranker_model="bge-reranker-v2-m3"),
            profile_content_store=Profiles(),
        )
        config = {"reranker_model": "qwen3-reranker-4b" if rerank else None}
        if config_updated:
            # A changed setting must not disguise an adapter built with the old model.
            service._fragment_config.reranker_model = "qwen3-reranker-4b"
        results = service.match(
            query=QUERY, query_embedding=[1.0, 0.0], top_k=1, mode="fragment",
            runtime_config=config, vector_min_score=0, rerank_min_score=0,
        )
        assert [result.profile_key for result in results] == ["bot:1:default"]
        if rerank:
            assert calls == [("bot:1", "default")]
            assert wire[0]["model"] == "Qwen3-Reranker-4B"
            assert wire[0]["documents"] == ["名称: UCT20值班助手\n描述: 排查 UCT 事件策略日志" + SUFFIX]
            assert config["_retrieval"]["reranker_model"] == "Qwen3-Reranker-4B"
        else:
            assert calls == [] and wire == []
            assert config["_retrieval"]["score_source"] == "vector_weighted"
    finally:
        vectors.close()


@pytest.mark.asyncio
async def test_recommend_metadata_preserves_actual_model(monkeypatch):
    from src.interfaces.api import recommend_routes
    from src.domain.models.bot_recommendation import BotRecommendationRequest
    from src.domain.models.candidate_recommendation import CandidateRecommendationResponse
    from src.domain.models.retrieval_mode import RetrievalMode

    class CandidateService:
        _vector_match_service = object()
        _embedding_generator = object()

        def recommend(self, **kwargs):
            return CandidateRecommendationResponse(
                question=kwargs["question"], mode=RetrievalMode.EXPERT_DIAGNOSIS,
                recommendations=[], total_candidates=0, selected_candidates=0,
                metadata={"reranker_model": "Qwen3-Reranker-4B", "reranker_called": True},
            )

    monkeypatch.setattr(recommend_routes, "get_candidate_recommendation_service", CandidateService)
    response = await recommend_routes.recommend_bots(BotRecommendationRequest(
        question=QUERY, enable_rerank=True, reranker_model="qwen3-reranker-4b",
    ))
    assert response.metadata["reranker_model"] == "Qwen3-Reranker-4B"


def test_valid_profile_keeps_failure_descriptions_and_excludes_score_and_identity():
    item = candidate()
    item.fragments[-1].content = "名称：UCT20值班助手\n描述：排查无法查询日志问题\n目标：定位事件策略日志为空\n无配置，无法提供能力。"
    text = FragmentRerankerService()._extract_description(item)
    assert text == "名称：UCT20值班助手\n描述：排查无法查询日志问题\n目标：定位事件策略日志为空"


def test_http_default_uses_qwen_without_model_configuration(monkeypatch, wire):
    monkeypatch.delenv("RERANKER_MODEL", raising=False)
    result = HttpReranker().rerank(QUERY, [{"id": "bot:1", "text": "名称: UCT20值班助手"}], 1)
    assert result[0].score == 0.8
    assert wire[0]["model"] == "Qwen3-Reranker-4B"
    assert wire[0]["query"].startswith(PREFIX)
    assert wire[0]["documents"] == ["名称: UCT20值班助手" + SUFFIX]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["recommend", "profiles"])
@pytest.mark.parametrize("configured,requested,enabled,expected", [
    (None, None, True, "Qwen3-Reranker-4B"),
    ("bge-reranker-v2-m3", None, True, "bge-reranker-v2-m3"),
    ("Qwen3-Reranker-4B", "bge-reranker-v2-m3", True, "bge-reranker-v2-m3"),
    ("bge-reranker-v2-m3", "Qwen3-Reranker-4B", True, "Qwen3-Reranker-4B"),
    (None, None, False, None),
])
async def test_api_model_default_preserves_overrides_and_disable(
    monkeypatch, endpoint, configured, requested, enabled, expected,
):
    from types import SimpleNamespace
    from src.domain.models.bot_recommendation import BotRecommendationRequest
    from src.domain.models.candidate_recommendation import CandidateRecommendationResponse
    from src.domain.models.retrieval_mode import RetrievalMode
    from src.interfaces.api import profile_routes, recommend_routes

    if configured is None:
        monkeypatch.delenv("RERANKER_MODEL", raising=False)
    else:
        monkeypatch.setenv("RERANKER_MODEL", configured)
    calls = []

    class Service:
        _vector_match_service = object()
        _embedding_generator = object()

        def recommend(self, **kwargs):
            calls.append(kwargs["runtime_config"])
            return CandidateRecommendationResponse(
                question=kwargs["question"], mode=RetrievalMode.EXPERT_DIAGNOSIS,
                recommendations=[], total_candidates=0, selected_candidates=0,
            )

        def match(self, **kwargs):
            calls.append(kwargs["runtime_config"])
            return []

    if endpoint == "recommend":
        monkeypatch.setattr(recommend_routes, "get_candidate_recommendation_service", Service)
        response = await recommend_routes.recommend_bots(BotRecommendationRequest(
            question=QUERY, enable_rerank=enabled, reranker_model=requested,
        ))
        assert response.metadata["reranker_model"] == expected
    else:
        monkeypatch.setattr(profile_routes, "_get_vector_match_service", Service)
        monkeypatch.setattr(profile_routes, "_get_embedding_generator", lambda: SimpleNamespace(embed=lambda _: [1.0]))
        response = await profile_routes.search_profiles(profile_routes.SearchRequest(
            query=QUERY, enable_rerank=enabled, reranker_model=requested,
        ))
        assert response.config_applied["reranker_model"] == expected
    assert calls[0]["reranker_model"] == expected
