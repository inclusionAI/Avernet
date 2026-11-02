"""Explicit v1 search modes must map to bounded core execution policies."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from app.application.agent_search_service import (
    AgentSearchCommand,
    AgentSearchService,
    resolve_search_mode_policy,
)
from app.bootstrap.oss_business_support import SearchRequestOSS


class RecordingCore:
    def __init__(self) -> None:
        self.match_calls: list[dict[str, Any]] = []

    def embed(self, query: str) -> list[float]:
        return [0.1, 0.2]

    def match(self, **kwargs: Any) -> list[dict[str, Any]]:
        self.match_calls.append(kwargs)
        return [{"profile_key": "team:finance:default", "score": 0.9}]


def test_schema_rejects_unknown_search_mode_and_accepts_explicit_modes() -> None:
    assert SearchRequestOSS(query="支付", mode="auto").mode == "auto"
    assert SearchRequestOSS(query="支付", mode="fast").mode == "fast"
    assert SearchRequestOSS(query="支付", mode="balanced").mode == "balanced"
    assert SearchRequestOSS(query="支付", mode="accurate").mode == "accurate"

    with pytest.raises(ValidationError):
        SearchRequestOSS(query="支付", mode="cheap")


def test_policy_species_are_explicit_and_bounded() -> None:
    fast = resolve_search_mode_policy("fast")
    balanced = resolve_search_mode_policy("balanced")
    accurate = resolve_search_mode_policy("accurate")
    auto = resolve_search_mode_policy("auto")

    assert fast.retrieval_mode == "fragment"
    assert fast.rerank_enabled is False
    assert fast.expand_factor == 1

    assert balanced.retrieval_mode == "fragment"
    assert balanced.rerank_enabled is True
    assert balanced.expand_factor == 2
    assert accurate.retrieval_mode == "fragment"
    assert accurate.rerank_enabled is True
    assert accurate.expand_factor == 3

    assert auto.retrieval_mode == "auto"
    assert auto.expand_factor is None

    with pytest.raises(ValueError, match="unsupported search mode"):
        resolve_search_mode_policy("cheap")


def test_fast_mode_never_reaches_rerank_and_uses_the_smallest_budget() -> None:
    core = RecordingCore()
    service = AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
    )

    asyncio.run(
        service.search(
            AgentSearchCommand(
                query="支付",
                top_k=3,
                mode="fast",
                reranker_model="Qwen3-Reranker-4B",
            )
        )
    )

    call = core.match_calls[0]
    assert call["mode"] == "fragment"
    assert call["runtime_config"]["reranker_model"] is None
    assert call["runtime_config"]["expand_factor"] == 1


def test_balanced_inherits_a_configured_model_when_request_model_is_absent() -> None:
    core = RecordingCore()
    service = AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
    )

    asyncio.run(
        service.search(
            AgentSearchCommand(query="支付", top_k=3, mode="balanced")
        )
    )

    runtime_config = core.match_calls[0]["runtime_config"]
    assert runtime_config["expand_factor"] == 2
    assert "reranker_model" not in runtime_config


def test_balanced_and_accurate_enable_rerank_with_increasing_budgets() -> None:
    core = RecordingCore()
    service = AgentSearchService(
        embedding_generator=core,
        vector_match_service=core,
    )

    for mode, expected_budget in (("balanced", 2), ("accurate", 3)):
        asyncio.run(
            service.search(
                AgentSearchCommand(
                    query="支付",
                    top_k=3,
                    mode=mode,
                    reranker_model="Qwen3-Reranker-4B",
                )
            )
        )
        call = core.match_calls[-1]
        assert call["mode"] == "fragment"
        assert call["runtime_config"]["reranker_model"] == "Qwen3-Reranker-4B"
        assert call["runtime_config"]["expand_factor"] == expected_budget
