"""Pre-only fixed candidate discover tests."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agentclaw.community.core.task.task_runner.client.candidate_search import (
    search_candidates,
)
from agentclaw.community.core.task.task_runner.client.fixed_candidate_bot_discover import (
    FixedCandidateBotDiscover,
)
from agentclaw.community.di.task_fixed_search import fixed_pre_candidate_search


class FixedCandidateBotDiscoverTest(unittest.IsolatedAsyncioTestCase):
    async def test_every_query_returns_the_same_fixed_candidates(self) -> None:
        discover = FixedCandidateBotDiscover(
            [
                {
                    "bot_id": "B_FIX",
                    "bot_name": "固定Bot",
                    "recommend": {"score": 1.0},
                }
            ]
        )
        result = await search_candidates(discover, "任意固定查询")

        self.assertEqual([item["bot_id"] for item in result.candidates], ["B_FIX"])
        self.assertEqual(result.tokens, ["任意固定查询"])
        self.assertEqual(result.raw_item_count, 1)
        self.assertEqual(result.failed_keywords, [])
        self.assertEqual(result.keyword_hits[0].bot_ids, ("B_FIX",))

    async def test_empty_search_returns_empty_result(self) -> None:
        discover = FixedCandidateBotDiscover(
            [{"bot_id": "B_FIX", "bot_name": "固定Bot"}]
        )
        result = await search_candidates(discover, "")

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.raw_item_count, 0)
        self.assertEqual(result.failed_keywords, [])


class FixedPreCandidateSearchTest(unittest.TestCase):
    def test_disabled_outside_pre(self) -> None:
        with patch(
            "agentclaw.community.di.task_fixed_search.get_current_env",
            return_value="prod",
        ):
            self.assertIsNone(fixed_pre_candidate_search())

    def test_enabled_by_default_in_pre(self) -> None:
        with patch(
            "agentclaw.community.di.task_fixed_search.get_current_env",
            return_value="pre",
        ):
            discover = fixed_pre_candidate_search()

        assert discover is not None
        self.assertEqual(len(discover.candidates), 4)
        self.assertEqual(discover.candidates[0]["bot_id"], "20261020_xa4pi64f:146836")
        self.assertEqual(discover.candidates[1]["bot_id"], "20261014_dpv2dfuk:35983")
        self.assertEqual(discover.candidates[2]["bot_id"], "20261014_im6mcv40:35983")
        self.assertEqual(discover.candidates[3]["bot_id"], "20261014_fnr8p3xe:35983")


if __name__ == "__main__":
    unittest.main()
