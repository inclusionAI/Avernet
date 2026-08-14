"""SingleboxKeywordBotDiscover 单测:锁住"关键字命不中→回落全量"的候选预查策略。

这层是**决策非查找**:候选集不该被"bot 名字与需求关键字对不上"误杀空。本地 bot 不按能力命名、
目录极小,关键字 LIKE 命中 0 时回落到全部公开 bot 当候选,谁执行仍由下游 search skill 在候选里决。
"""
from __future__ import annotations

import unittest

from agentclaw.community.core.task.task_runner.integration.singlebox_engine_adapter import (
    SingleboxKeywordBotDiscover,
)


class _FakeBps:
    """假 BotPublicServiceProtocol:按 search 关键字返回不同结果,模拟 singlebox 行为。"""

    def search_public_bots_by_keyword(self, *, user_id, search=None, page=1, page_size=10, **_):
        all_bots = [
            {"bot_id": "B_MARKET", "bot_name": "市场需求分析Bot"},
            {"bot_id": "B_BBS", "bot_name": "开发者-146836"},
        ]
        if not search:
            return {"total": len(all_bots), "items": list(all_bots)}
        # 模拟 DB LIKE:只有名字含关键字的命中
        hits = [b for b in all_bots if search in b["bot_name"]]
        return {"total": len(hits), "items": hits}


class _ExplodingBps:
    """search 抛异常→应被收口为空,不阻断。"""

    def search_public_bots_by_keyword(self, **_):
        raise RuntimeError("boom")


class TestSingleboxKeywordDiscoverFallback(unittest.TestCase):
    def test_keyword_hit_returns_hits_no_fallback(self) -> None:
        d = SingleboxKeywordBotDiscover(_FakeBps())
        res = d.search_by_keyword(keyword="市场", user_id="146836")
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["items"][0]["bot_id"], "B_MARKET")
        self.assertFalse(res["context"]["fallback_to_all"])

    def test_keyword_miss_falls_back_to_all(self) -> None:
        """核心:需求关键字跟任何 bot 名都对不上→回落全量,候选不误杀空。"""
        d = SingleboxKeywordBotDiscover(_FakeBps())
        res = d.search_by_keyword(keyword="存储行业尽调", user_id="146836")
        self.assertEqual(res["total"], 2)  # 全量公开 bot
        bot_ids = {it["bot_id"] for it in res["items"]}
        self.assertEqual(bot_ids, {"B_MARKET", "B_BBS"})
        self.assertTrue(res["context"]["fallback_to_all"])

    def test_empty_keyword_returns_all_not_marked_fallback(self) -> None:
        """空关键字→search=None 直接返全量,非"关键字命不中后才回落",fallback_to_all=False。"""
        d = SingleboxKeywordBotDiscover(_FakeBps())
        res = d.search_by_keyword(keyword="", user_id="146836")
        self.assertEqual(res["total"], 2)
        self.assertFalse(res["context"]["fallback_to_all"])

    def test_exit_exception_returns_empty_not_raising(self) -> None:
        d = SingleboxKeywordBotDiscover(_ExplodingBps())
        res = d.search_by_keyword(keyword="x", user_id="146836")
        self.assertEqual(res["total"], 0)
        self.assertEqual(res["items"], [])

    def test_items_carry_recommend_score(self) -> None:
        d = SingleboxKeywordBotDiscover(_FakeBps())
        res = d.search_by_keyword(keyword="存储行业尽调", user_id="146836")
        for it in res["items"]:
            self.assertIn("recommend", it)
            self.assertIn("score", it["recommend"])


if __name__ == "__main__":
    unittest.main()
