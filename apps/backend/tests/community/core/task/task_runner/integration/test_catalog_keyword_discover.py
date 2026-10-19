"""CatalogKeywordBotDiscover 单测:锁住"关键字命中→候选;命中 0 默认空(收窄,不塞全量噪音 bot)"策略。

这层是**决策非查找**:候选集不该被无关 bot 污染。bot 按能力命名时 BCS catalog 关键字能命中;
命中 0 → 默认返空(避免 search skill 在无关候选里自由组合)。``fallback_to_all=True`` 时显式回落全量
(产品搜索等需要"有结果"兜底的场景)。
"""
from __future__ import annotations

import unittest

from agentclaw.community.core.task.task_runner.client.catalog_keyword_discover import (
    CatalogKeywordBotDiscover,
)


class _FakeBps:
    """假 BotPublicServiceProtocol:按 search 关键字返回不同结果,模拟 BCS catalog 关键字搜索。"""

    def search_catalog_public_bots_by_keyword(
        self,
        *,
        search=None,
        page=1,
        page_size=20,
        caller=None,
        request_id="",
        filters=None,
        **_,
    ):
        all_bots = [
            {
                "bot_id": "B_MARKET",
                "entity_id": "U1",
                "owner_id": "U1",
                "bot_name": "市场需求分析Bot",
                "bot_uuid": "B_MARKET:U1",
                "ext": {"passport": {"agent_code": "ac-ready"}},
            },
            {
                "bot_id": "B_BBS",
                "entity_id": "U2",
                "owner_id": "U2",
                "bot_name": "开发者-146836",
                "bot_uuid": "B_BBS:U2",
                "ext": {"passport": {"agent_code": "ac-ready"}},
            },
        ]
        if not search:
            return {"total": len(all_bots), "items": list(all_bots)}
        # 模拟 BCS /bots/search 关键字 q 命中:只有名字含关键字的命中
        hits = [b for b in all_bots if search in b["bot_name"]]
        return {"total": len(hits), "items": hits}


class _RuntimeFakeBps:
    """Include catalog hits that expose conflicting backend runtime states."""

    def search_catalog_public_bots_by_keyword(
        self, *, search=None, page=1, page_size=20, caller=None, request_id="",
        filters=None, **_,
    ):
        items = [
            {
                "bot_id": "B_OK",
                "owner_id": "U1",
                "bot_uuid": "B_OK:U1",
                "ext": {"passport": {"agent_code": "ac-ready"}},
                "status": "ACTIVE",
                "is_online": True,
                "runtime_state": "online",
            },
            {
                "bot_id": "B_IS_OFFLINE",
                "owner_id": "U2",
                "bot_uuid": "B_IS_OFFLINE:U2",
                "ext": {"passport": {"agent_code": "ac-ready"}},
                "status": "ACTIVE",
                "is_online": False,
                "runtime_state": "online",
            },
            {
                "bot_id": "B_STATUS_OFFLINE",
                "owner_id": "U3",
                "bot_uuid": "B_STATUS_OFFLINE:U3",
                "ext": {"passport": {"agent_code": "ac-ready"}},
                "status": "PENDING",
                "is_online": True,
                "runtime_state": "online",
            },
            {
                "bot_id": "B_RUNTIME_OFFLINE",
                "owner_id": "U4",
                "bot_uuid": "B_RUNTIME_OFFLINE:U4",
                "ext": {"passport": {"agent_code": "ac-ready"}},
                "status": "ACTIVE",
                "is_online": True,
                "runtime_state": "offline",
            },
            {
                "bot_id": "B_UNKNOWN_FIELDS",
                "owner_id": "U5",
                "bot_uuid": "B_UNKNOWN_FIELDS:U5",
                "ext": {"passport": {"agent_code": "ac-ready"}},
            },
        ]
        return {"total": len(items), "items": items}



class _CredentialFakeBps:
    """Search hits for which one bot has missing runtime credentials."""

    def search_catalog_public_bots_by_keyword(
        self, *, search=None, page=1, page_size=20, caller=None, request_id="",
        filters=None, **_,
    ):
        items = [
            {"bot_id": "B_HAS_CODE", "owner_id": "U1", "bot_uuid": "B_HAS_CODE:U1",
             "status": "ACTIVE", "is_online": True,
             "ext": {"passport": {"agent_code": "ac-1"}}},
            {"bot_id": "B_EMPTY_CODE", "owner_id": "U2", "bot_uuid": "B_EMPTY_CODE:U2",
             "status": "ACTIVE", "is_online": True,
             "ext": {"passport": {"agent_code": ""}}},
            {"bot_id": "B_MISSING_CODE", "owner_id": "U3", "bot_uuid": "B_MISSING_CODE:U3",
             "status": "ACTIVE", "is_online": True,
             "ext": {"passport": {}}},
            {"bot_id": "B_BCS_ONLY", "owner_id": "U4", "bot_uuid": "B_BCS_ONLY:U4",
             "status": "ACTIVE", "is_online": True},
        ]
        if search == "bot":
            return {"total": len(items), "items": items}
        return {"total": 0, "items": []}


class _ExplodingBps:
    """search 抛异常→应被收口为空,不阻断。"""

    def search_catalog_public_bots_by_keyword(self, **_):
        raise RuntimeError("boom")


class TestCatalogKeywordDiscover(unittest.TestCase):
    def test_keyword_hit_returns_hits_no_fallback(self) -> None:
        d = CatalogKeywordBotDiscover(_FakeBps())
        res = d.search_by_keyword(keyword="市场", user_id="146836")
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["items"][0]["bot_id"], "B_MARKET")
        self.assertFalse(res["context"]["fallback_to_all"])

    def test_keyword_miss_returns_empty_by_default(self) -> None:
        """核心:关键字命不中→默认返空(收窄),不盲目塞全量噪音 bot。"""
        d = CatalogKeywordBotDiscover(_FakeBps())
        res = d.search_by_keyword(keyword="存储行业尽调", user_id="146836")
        self.assertEqual(res["total"], 0)
        self.assertEqual(res["items"], [])
        self.assertFalse(res["context"]["fallback_to_all"])

    def test_keyword_miss_explicit_fallback_to_all(self) -> None:
        """显式 fallback_to_all=True→回落全量公开 bot(产品搜索等兜底场景)。"""
        d = CatalogKeywordBotDiscover(_FakeBps())
        res = d.search_by_keyword(keyword="存储行业尽调", user_id="146836", fallback_to_all=True)
        self.assertEqual(res["total"], 2)
        self.assertEqual({it["bot_id"] for it in res["items"]}, {"B_MARKET", "B_BBS"})
        self.assertTrue(res["context"]["fallback_to_all"])

    def test_empty_keyword_returns_all_not_marked_fallback(self) -> None:
        """空关键字→search=None 直接返全量,非"关键字命不中后才回落",fallback_to_all=False。"""
        d = CatalogKeywordBotDiscover(_FakeBps())
        res = d.search_by_keyword(keyword="", user_id="146836")
        self.assertEqual(res["total"], 2)
        self.assertFalse(res["context"]["fallback_to_all"])

    def test_exit_exception_returns_empty_not_raising(self) -> None:
        d = CatalogKeywordBotDiscover(_ExplodingBps())
        res = d.search_by_keyword(keyword="x", user_id="146836")
        self.assertEqual(res["total"], 0)
        self.assertEqual(res["items"], [])

    def test_offline_or_abnormal_runtime_candidates_are_filtered(self) -> None:
        d = CatalogKeywordBotDiscover(_RuntimeFakeBps())
        res = d.search_by_keyword(keyword="bot", user_id="146836")
        self.assertEqual(
            {item["bot_id"] for item in res["items"]},
            {"B_OK", "B_UNKNOWN_FIELDS"},
        )

    def test_agent_code_missing_candidates_are_filtered(self) -> None:
        d = CatalogKeywordBotDiscover(_CredentialFakeBps())
        res = d.search_by_keyword(keyword="bot", user_id="146836")
        self.assertEqual(
            {item["bot_id"] for item in res["items"]},
            {"B_HAS_CODE"},
        )

    def test_missing_runtime_fields_are_not_filtered(self) -> None:
        d = CatalogKeywordBotDiscover(_RuntimeFakeBps())
        res = d.search_by_keyword(keyword="bot", user_id="146836")
        self.assertIn(
            "B_UNKNOWN_FIELDS", {item["bot_id"] for item in res["items"]}
        )

    def test_items_carry_recommend_score(self) -> None:
        d = CatalogKeywordBotDiscover(_FakeBps())
        res = d.search_by_keyword(keyword="存储行业尽调", user_id="146836", fallback_to_all=True)
        for it in res["items"]:
            self.assertIn("recommend", it)
            self.assertIn("score", it["recommend"])

    def test_items_carry_bot_uuid(self) -> None:
        """catalog item 自带完整 bot_uuid({bot_id}:{entity}),下游 BCS 派发身份解析可直接消费。"""
        d = CatalogKeywordBotDiscover(_FakeBps())
        res = d.search_by_keyword(keyword="存储行业尽调", user_id="146836", fallback_to_all=True)
        self.assertEqual(res["total"], 2)
        self.assertEqual(
            {it["bot_uuid"] for it in res["items"]},
            {"B_MARKET:U1", "B_BBS:U2"},
        )


if __name__ == "__main__":
    unittest.main()
