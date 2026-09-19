"""CatalogKeywordBotDiscover — task 派发候选预查适配,实现 ``BotDiscoverServiceProtocol.search_by_keyword``。

profile 无关:corp / prod / community / singlebox 各 profile 的 task 派发候选预查均复用此实现
(``TaskModule._resolve_discover`` 无条件返回本类)。底层走 ``BotPublicService.search_catalog_public_bots_by_keyword``
(BCS catalog 关键字搜索),刻意不依赖 BCSFuse recommendation——其可用性不应决定派发路由。

早期与 ``SingleboxEngineAdapter`` 同处 ``singlebox_engine_adapter.py``;因本类非 singlebox 专属,
独立成模块以避免文件名误导。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogCaller,
    BotCatalogSearchFilters,
)
from agentclaw.community.core.task.task_runner.client.protocols import (
    BotPublicServiceProtocol,
)

logger = logging.getLogger("task.catalog.search")

# 派发候选预查可见性判据:锁 ``visibility=public``(bot↔bot 协作走 agent 域,不用人类
# user_visibility);``user_visibility`` 三值全匹配以中和 BCS None/Human 分支对该字段的默认
# {public,protected}(见 bcs-http search_bots viewer_kind / bcs-bot-store search_candidates)。
_DISPATCH_CATALOG_FILTERS = BotCatalogSearchFilters(
    visibility=("public",),
    user_visibility=("public", "protected", "private"),  # 中和 user_visibility 默认,见下
    status="online",
)


class CatalogKeywordBotDiscover:
    """Catalog 关键字搜推适配:实现 ``BotDiscoverServiceProtocol.search_by_keyword``,
    底层走 ``BotPublicService.search_catalog_public_bots_by_keyword``(BCS catalog 关键字搜索,
    适配 OSS ``public='0'`` 部署 bot 也能命中)。

    非单 ``singlebox`` 专用:corp / prod / community / singlebox 各 profile 的 task 派发候选预查
    均复用此实现(``TaskModule._resolve_discover``)。底层经 BCS ``/bots/search`` 关键字检索后回 join
    后端 bot 元数据,每个 item 自带完整 ``bot_uuid``(``{bot_id}:{entity_id}``,对齐 BCN onboard 的
    ``bot_id:owner_id`` 形态),供下游 BCS 派发身份解析直接消费,无需搜推层再做 product→复合兜底。

    - 关键词为空 → 返空(对齐 catalog 空关键词行为);
    - 无语义 score → 合成 ``recommend.score``(命中次序降权),保 stable 排序供策略排序;
    - ``filters``(runtime_state 等) 本类忽略(catalog 无 runtime 在线态维度);catalog 视野用
      ``_DISPATCH_CATALOG_FILTERS`` 锁 ``visibility=public`` 且中和 ``user_visibility``
      (BCS None/Human 分支默认 {public,protected}),只取公开可认领 bot,不含 protected;
    - 身份参数:catalog ``caller`` 在 ``search_public_bot_metadata`` 顶部即 ``del caller``(废弃),
      占位 ``BotCatalogCaller(tenant_id="", user_id=None, app_id=None)`` 不影响检索结果;
      ``request_id`` 仅日志,生成 ``task-prefetch-<uuid12>``;
    - 端口/catalog 不可用(``BotCatalogSearchUnavailableError`` 等) → 返空,不阻断字段预查。
    """

    def __init__(self, bot_public_service: "BotPublicServiceProtocol") -> None:
        self._bps = bot_public_service

    def search_by_keyword(
        self,
        *,
        keyword: str,
        user_id: str,
        top_k: int = 10,
        min_score: float = 0.01,
        filters: dict[str, Any] | None = None,
        fallback_to_all: bool = False,
    ) -> dict[str, Any]:
        """catalog 候选预查:**决策非查找**——关键字命中返命中;命中 0 默认返空(收窄,不盲目塞全量
        噪音 bot,避免 search skill 在无关候选里自由组合)。``fallback_to_all=True`` 时回落全量公开 bot
        (显式场景:产品搜索等需要"有结果"兜底)。谁执行仍由 search skill 在候选里决,本层只供候选。"""
        # 1) catalog 关键字命中(bot 按能力命名时能命中)
        hits = self._query(user_id=user_id, search=keyword or None, top_k=top_k)
        used_fallback = False
        if not hits and fallback_to_all:
            # 2) 显式回落:全部公开 bot(仅 fallback_to_all=True 时)
            hits = self._query(user_id=user_id, search=None, top_k=top_k)
            used_fallback = bool(hits)
        # 合成 recommend.score(命中次序降权),对齐 BCSFuse items 形态供策略排序
        for i, it in enumerate(hits):
            rec = it.get("recommend")
            if not isinstance(rec, dict):
                rec = {}
            if "score" not in rec:
                rec["score"] = max(min_score, 1.0 - i * 0.05)
            it["recommend"] = rec
        return {
            "total": len(hits),
            "items": hits,
            "context": {"mode": "catalog_keyword", "fallback_to_all": used_fallback},
        }

    def _query(
        self, *, user_id: str, search: str | None, top_k: int
    ) -> list[dict[str, Any]]:
        """调 ``search_catalog_public_bots_by_keyword``(BCS catalog)并收口异常→空列表(不阻断预查)。

        ``caller`` 占位(catalog service 顶部 ``del caller``)、``request_id`` 仅日志;
        ``filters=_DISPATCH_CATALOG_FILTERS`` 锁 ``visibility=public`` 并中和 ``user_visibility``。
        返回 item 自带 ``bot_uuid``(``{bot_id}:{entity_id}``)。"""
        request_id = f"task-prefetch-{uuid.uuid4().hex[:12]}"
        logger.debug(
            "catalog_search_start request_id=%s search=%r top_k=%d filters=%s",
            request_id,
            (search or "")[:500],
            top_k,
            _DISPATCH_CATALOG_FILTERS,
        )
        try:
            res = self._bps.search_catalog_public_bots_by_keyword(
                search=search,
                page=1,
                page_size=top_k,
                caller=BotCatalogCaller(tenant_id="", user_id=None, app_id=None),
                request_id=request_id,
                filters=_DISPATCH_CATALOG_FILTERS,
            )
        except Exception as exc:  # noqa: BLE001  端口/catalog 不可用→空候选
            logger.warning(
                "catalog_search_failed request_id=%s search=%r error_type=%s error=%s",
                request_id,
                (search or "")[:500],
                type(exc).__name__,
                str(exc)[:500],
                exc_info=True,
            )
            return []
        items = res.get("items") or [] if isinstance(res, dict) else []
        logger.debug(
            "catalog_search_complete request_id=%s search=%r raw_total=%s item_count=%d candidate_ids=%s",
            request_id,
            (search or "")[:500],
            res.get("total") if isinstance(res, dict) else None,
            len(items),
            [item.get("bot_uuid") or item.get("bot_id") for item in items if isinstance(item, dict)],
        )
        return items
