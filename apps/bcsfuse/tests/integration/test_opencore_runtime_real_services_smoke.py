"""Explicit real-model smoke, separate from offline lifecycle acceptance.

BCSFUSE_RUN_REAL_SERVICES_E2E=1 opts into configured external model endpoints.
Missing configuration and provider fallback are failures after opt-in. These
tests do not read/write deployed workers or assume a particular vector size.
"""

import math
import os

import pytest

# Root conftest intentionally clears model environment for ordinary tests.
# Capture only the explicitly supplied model settings before that fixture runs.
_MODEL_KEYS = (
    "LLM_BASE_URL", "LLM_AUTH_TOKEN", "LLM_FAST_MODEL",
    "EMBEDDING_BASE_URL", "EMBEDDING_AUTH_TOKEN", "EMBEDDING_MODEL",
    "EMBEDDING_DIMENSION", "RERANKER_BASE_URL", "RERANKER_API_KEY", "RERANKER_MODEL",
)
_CONFIG = {key: os.getenv(key, "") for key in _MODEL_KEYS}


@pytest.fixture(autouse=True)
def explicit_model_target(monkeypatch):
    if os.getenv("BCSFUSE_RUN_REAL_SERVICES_E2E") != "1":
        pytest.skip("Real models not requested: set BCSFUSE_RUN_REAL_SERVICES_E2E=1 and explicit model configuration")
    for key, value in _CONFIG.items():
        if value:
            monkeypatch.setenv(key, value)


def require(*keys):
    missing = [key for key in keys if not os.getenv(key)]
    assert not missing, "Explicit real-provider configuration required: " + ", ".join(missing)


def embedding_provider():
    require("EMBEDDING_BASE_URL", "EMBEDDING_AUTH_TOKEN", "EMBEDDING_MODEL", "EMBEDDING_DIMENSION")
    from src.infra.embedding.config.embedding_settings import EmbeddingSettings
    from src.infra.public.embedding.real_embedding_provider import RealEmbeddingProvider
    return RealEmbeddingProvider(EmbeddingSettings())


def rerank():
    require("RERANKER_BASE_URL", "RERANKER_API_KEY", "RERANKER_MODEL")
    from src.infra.public.reranker.http_reranker import HttpReranker
    candidates = [
        {"id": "python", "text": "Python FastAPI code review and backend testing specialist"},
        {"id": "design", "text": "Visual designer specializing in illustrations and typography"},
        {"id": "database", "text": "Database migration and SQL administration specialist"},
    ]
    result = HttpReranker().rerank("Python FastAPI code review", candidates, top_k=3)
    assert len(result) == 3
    scores = [item.score for item in result]
    assert all(math.isfinite(score) for score in scores)
    assert len(set(scores)) > 1, "Uniform scores indicate fallback/no-op or invalid ranking"
    assert scores == sorted(scores, reverse=True)
    assert result[0].candidate_id == "python"
    return result


def test_real_llm_connectivity():
    require("LLM_BASE_URL", "LLM_AUTH_TOKEN", "LLM_FAST_MODEL")
    from src.domain.models.llm_request import LLMRequest
    from src.domain.models.llm_task_spec import LLMTaskSpec, TaskType
    from src.infra.llm.config.llm_settings import LLMSettings
    from src.infra.public.llm.anthropic_compatible_provider import AnthropicCompatibleProvider

    provider = AnthropicCompatibleProvider(LLMSettings())
    try:
        response = provider.generate(
            LLMRequest(task_spec=LLMTaskSpec(task_type=TaskType.SUMMARY),
                       user_prompt="Reply with hello.", max_tokens=32),
            model=os.environ["LLM_FAST_MODEL"],
        )
    finally:
        provider.close()
    assert response.raw_text.strip()
    assert response.provider_id != "fake"


def test_real_embedding_dimension_and_batch():
    provider = embedding_provider()
    try:
        vectors = provider.embed_batch(["Python API specialist", "Typography illustrator"])
    finally:
        provider.close()
    assert len(vectors) == 2
    dimension = int(os.environ["EMBEDDING_DIMENSION"])
    assert all(len(vector) == dimension for vector in vectors)
    assert all(all(math.isfinite(value) for value in vector) for vector in vectors)
    assert all(sum(value * value for value in vector) > 0 for vector in vectors)
    assert vectors[0] != vectors[1], "Different inputs must not return a constant test embedding"


def test_real_reranker_ranks_without_fallback():
    rerank()


def test_real_embedding_and_reranker_workflow():
    provider = embedding_provider()
    try:
        vector = provider.embed("Python FastAPI code review")
    finally:
        provider.close()
    assert len(vector) == int(os.environ["EMBEDDING_DIMENSION"])
    assert rerank()[0].candidate_id == "python"
