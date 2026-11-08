"""Hybrid semantic-first relay candidate retrieval unit tests."""

from __future__ import annotations

from typing import Any
import unittest

from agentclaw.community.core.task.task_dispatch.search import TaskSearch
from agentclaw.community.core.task.task_runner.client.candidate_search import (
    search_candidates,
)
from agentclaw.community.core.task.task_runner.client.catalog_keyword_discover import (
    CatalogKeywordBotDiscover,
)
from agentclaw.community.core.task.task_runner.client.semantic_keyword_bot_discover import (
    SemanticKeywordBotDiscover,
)


class _KeywordBps:
    def search_catalog_public_bots_by_keyword(self, **_: Any) -> dict[str, Any]:
        return {
            "total": 1,
            "items": [
                {
                    "bot_id": "B_KEYWORD",
                    "owner_id": "U1",
                    "bot_uuid": "B_KEYWORD:U1",
                    "bot_name": "关键词Bot",
                    "ext": {"passport": {"agent_code": "ac-ready"}},
                }
            ],
        }


class _FakeSemantic:
    def __init__(
        self,
        items: list[dict[str, Any]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.items = items or []
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def search_by_keyword(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"total": len(self.items), "items": self.items}


class SemanticKeywordBotDiscoverTest(unittest.IsolatedAsyncioTestCase):
    async def test_semantic_result_wins_without_keyword_search(self) -> None:
        semantic = _FakeSemantic(
            [
                {
                    "bot_id": "B_SEMANTIC",
                    "owner_id": "U2",
                    "bot_uuid": "B_SEMANTIC:U2",
                    "recommend": {"score": 0.93, "reasons": ["职责语义匹配"]},
                    "ext": {"passport": {"agent_code": "ac-ready"}},
                }
            ]
        )
        discover = SemanticKeywordBotDiscover(
            CatalogKeywordBotDiscover(_KeywordBps()), semantic
        )
        result = await search_candidates(discover, "存储行业竞争格局深度分析")

        self.assertEqual([item["bot_id"] for item in result.candidates], ["B_SEMANTIC"])
        self.assertEqual(semantic.calls[0]["keyword"], "存储行业竞争格局深度分析")
        self.assertEqual(result.tokens, ["存储行业竞争格局深度分析"])
        self.assertEqual(
            [hit.keyword for hit in result.keyword_hits],
            ["存储行业竞争格局深度分析"],
        )
        self.assertEqual(len(result.tokens), len(result.keyword_hits))
        self.assertFalse(result.failed_keywords)

    async def test_semantic_empty_falls_back_to_keyword_tokens(self) -> None:
        semantic = _FakeSemantic([])
        keyword_discover = CatalogKeywordBotDiscover(_KeywordBps())
        discover = SemanticKeywordBotDiscover(keyword_discover, semantic)
        result = await search_candidates(discover, "存储行业")

        self.assertEqual([item["bot_id"] for item in result.candidates], ["B_KEYWORD"])
        self.assertTrue(semantic.calls)

    async def test_semantic_error_falls_back_without_failing_relay_search(self) -> None:
        semantic = _FakeSemantic(error=RuntimeError("bcsfuse unavailable"))
        discover = SemanticKeywordBotDiscover(
            CatalogKeywordBotDiscover(_KeywordBps()), semantic
        )
        result = await search_candidates(discover, "存储行业")

        self.assertEqual([item["bot_id"] for item in result.candidates], ["B_KEYWORD"])
        self.assertFalse(result.failed_keywords)

    async def test_non_executable_semantic_hit_falls_back(self) -> None:
        semantic = _FakeSemantic(
            [
                {
                    "bot_id": "B_NO_CODE",
                    "owner_id": "U3",
                    "bot_uuid": "B_NO_CODE:U3",
                    "recommend": {"score": 0.99},
                    "ext": {"passport": {}},
                }
            ]
        )
        discover = SemanticKeywordBotDiscover(
            CatalogKeywordBotDiscover(_KeywordBps()), semantic
        )
        result = await search_candidates(discover, "存储行业")

        self.assertEqual([item["bot_id"] for item in result.candidates], ["B_KEYWORD"])

    async def test_semantic_search_returns_at_most_top_five_candidates(self) -> None:
        semantic = _FakeSemantic(
            [
                {
                    "bot_id": f"B_{score}",
                    "owner_id": f"U{score}",
                    "bot_uuid": f"B_{score}:U{score}",
                    "recommend": {"score": score},
                    "ext": {"passport": {"agent_code": "ac-ready"}},
                }
                for score in (0.1, 0.3, 0.5, 0.2, 0.4, 0.6)
            ]
        )
        discover = SemanticKeywordBotDiscover(
            CatalogKeywordBotDiscover(_KeywordBps()), semantic
        )
        response = await TaskSearch(discover, user_id="146836").search_catalog(
            "存储行业竞争格局深度分析"
        )

        self.assertEqual(semantic.calls[0]["top_k"], 5)
        self.assertEqual(len(response["candidates"]), 5)
        self.assertEqual(
            [candidate["bot_uuid"] for candidate in response["candidates"]],
            ["B_0.6:U0.6", "B_0.5:U0.5", "B_0.4:U0.4", "B_0.3:U0.3", "B_0.2:U0.2"],
        )

    async def test_task_search_projection_keeps_response_shape(self) -> None:
        semantic = _FakeSemantic(
            [
                {
                    "bot_id": "B_SEMANTIC",
                    "owner_id": "U2",
                    "bot_uuid": "B_SEMANTIC:U2",
                    "recommend": {"score": 0.93},
                    "ext": {"passport": {"agent_code": "ac-ready"}},
                }
            ]
        )
        discover = SemanticKeywordBotDiscover(
            CatalogKeywordBotDiscover(_KeywordBps()), semantic
        )
        response = await TaskSearch(discover, user_id="146836").search_catalog(
            "存储行业竞争格局深度分析"
        )

        self.assertEqual(response["candidates"][0]["bot_uuid"], "B_SEMANTIC:U2")
        self.assertEqual(response["tokens"], ["存储行业竞争格局深度分析"])
        self.assertEqual(
            [hit["keyword"] for hit in response["keyword_hits"]],
            ["存储行业竞争格局深度分析"],
        )
        self.assertEqual(len(response["tokens"]), len(response["keyword_hits"]))
        self.assertEqual(
            set(response),
            {
                "candidates",
                "total",
                "tokens",
                "raw_item_count",
                "failed_keywords",
                "keyword_hits",
            },
        )

    async def test_keyword_port_stays_compatible(self) -> None:
        discover = SemanticKeywordBotDiscover(
            CatalogKeywordBotDiscover(_KeywordBps()), _FakeSemantic([])
        )
        result = discover.search_by_keyword(keyword="市场", user_id="146836")
        self.assertEqual([item["bot_id"] for item in result["items"]], ["B_KEYWORD"])


if __name__ == "__main__":
    unittest.main()
