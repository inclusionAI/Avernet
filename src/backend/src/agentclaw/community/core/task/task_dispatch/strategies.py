"""TaskDispatcher 内置派发优化策略库(引擎自带,不开放自定义)。

对齐 plan.md §3.4(first-match-wins by priority)。策略经 ``execution_config`` 动态匹配,
类 SQL optimizer:config 有 ``bot`` → DirectDispatchStrategy(跳过搜推直接填);
否则兜底 SearchBasedDispatchStrategy(搜推)。Avernet 默认 stub(search 恒 MISS);
真实 catalog 搜推 + 多 bot 拉群为引擎默认实现(端口由 DI 注入)。
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Protocol

from agentclaw.community.core.task.domain.json_extract import extract_json
from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.domain.models import TaskExecutionGraph, TaskNode
from agentclaw.community.core.task.domain.prompt_constants import (
    NO_WEB_SEARCH_CONSTRAINT,
)

logger = logging.getLogger("task.dispatcher")

# ===== PRE rule-mode test customization =====
# Keep this block self-contained so the pre-release test behavior can be removed
# without touching the generic dispatch strategy below.
_SINGLE_CANDIDATE_MISS_PROBABILITY = 0.4
_RULE_TEST_MAX_GROUP_MEMBERS = 3

# Rule 模式候选池不再写死:由 ``SearchBasedDispatchStrategy._load_rule_test_pool``
# 在派发时动态查询 ``task_claim_mode=true & visibility=public`` 的 bot_id(``product:owner``),
# 以适配不同环境 bot_id 不一致(见 ``_rule_based_search_result`` 的 ``bot_pool`` 形参)。


class SearchOutcome(StrEnum):
    """搜推 4 态结果。"""

    HIT_SINGLE = "HIT_SINGLE"  # 单 bot 命中
    HIT_GROUP = "HIT_GROUP"  # 协作群命中(已有群)
    HIT_MULTI_BOTS = "HIT_MULTI_BOTS"  # 多 bot 命中,需动态拉协作群
    MISS = "MISS"  # 未匹配执行者


@dataclass
class GroupFormation:
    """动态拉协作群参数(HIT_MULTI_BOTS 时 search 一并决出;内部参数,不持久 RuntimeInfo)。

    透传 BCS 建群(BcsCreateGroupRequest):``group_name``→``context``/``topic``(当前无 label 字段)/
    ``members_info``→``participants[].role``/``extend_props["definition_yaml"]``→``collaboration_definition_yaml``。
    """

    bot_ids: list[str]
    collab_mode: (
        str  # "chat"/"manager_worker"/"state_machine"(state_machine 注入 workflow yaml)
    )
    group_name: str | None = None  # skill 决出协作群名 → BCS 透传
    members_info: list[dict] | None = (
        None  # [{bot_id, role, responsibility}] → BCS participants[].role
    )
    extend_props: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form for persistence (run_info.extend_props round-trip)."""
        return {
            "bot_ids": list(self.bot_ids),
            "collab_mode": self.collab_mode,
            "group_name": self.group_name,
            "members_info": list(self.members_info) if self.members_info is not None else None,
            "extend_props": dict(self.extend_props),
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "GroupFormation":
        """Inverse of ``to_dict``; used when hydrating a graph from the shared store."""
        return cls(
            bot_ids=list(value.get("bot_ids") or []),
            collab_mode=value["collab_mode"],
            group_name=value.get("group_name"),
            members_info=list(value["members_info"]) if value.get("members_info") else None,
            extend_props=dict(value.get("extend_props") or {}),
        )


@dataclass
class SearchResult:
    """搜推结果。"""

    outcome: SearchOutcome
    bot_id: str | None = None  # HIT_SINGLE
    bot_name: str | None = None  # HIT_SINGLE Bot display name
    owner_id: str | None = None  # HIT_SINGLE Bot owner
    owner_name: str | None = None  # HIT_SINGLE Bot owner display name
    group_id: str | None = None  # HIT_GROUP
    group_formation: GroupFormation | None = None  # HIT_MULTI_BOTS
    miss_reason: str | None = None  # MISS
    unauthorized_bots: list[dict] | None = None  # JOIN 丢的候选(dashboard unauthorized_bots 契约)


class DispatchStrategy(Protocol):
    """派发优化策略契约(引擎内置,first-match-wins)。"""

    rule_id: str
    priority: int

    async def matches(self, node: TaskNode, graph: TaskExecutionGraph) -> bool:
        """纯读:据图级 execution_config 判本策略是否适用(bot 信号)。协程化:corp catalog 查询可耗 IO。"""
        ...

    async def apply(self, node: TaskNode, graph: TaskExecutionGraph) -> SearchResult:
        """对单节点决出 SearchResult(4 态)。HIT_MULTI_BOTS 携 GroupFormation;拉群由编排核+runner。
        协程化:corp 真实 bot catalog 搜推是耗时 IO,await 不阻塞。"""
        ...


class DirectDispatchStrategy:
    """config 有 ``bot`` → 跳过搜推,直接返 HIT_SINGLE(bot=cfg["bot"])。"""

    rule_id = "direct"
    priority = 10

    async def matches(self, node: TaskNode, graph: TaskExecutionGraph) -> bool:
        cfg = graph.extend_props.get("execution_config", {}) or {}
        return cfg.get("bot") is not None or bool(node.run_info.extend_props.get("static_bot_id")) or bool(node.run_info.extend_props.get("pending_group_formation"))

    async def apply(self, node: TaskNode, graph: TaskExecutionGraph) -> SearchResult:
        cfg = graph.extend_props.get("execution_config", {}) or {}
        static_group = node.run_info.extend_props.get("pending_group_formation")
        if static_group is not None:
            return SearchResult(outcome=SearchOutcome.HIT_MULTI_BOTS, group_formation=static_group)
        return SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id=node.run_info.extend_props.get("static_bot_id") or cfg.get("bot"))


class SearchBasedDispatchStrategy:
    """默认兜底:搜推匹配(决策非查找)。两步:① 框架关键字预查候选集(分字段 title/objective/background)
    → ② 投 owner bot search skill 在候选里决出 who+how → 4 态 SearchResult。端口(bot/discover)由 DI 注入;
    省略端口 = stub 路径(纯内核单测)恒 MISS。搜推 skill 不自取 BCSFuse,候选集由框架预查喂入 prompt。

    owner bot = ``graph.extend_props["owner_bot_id"]``(框架派生取,零 case 知识)。
    """

    rule_id = "search"
    priority = 99

    def __init__(
        self,
        bot=None,
        discover=None,
        bcn=None,
        join_gate=None,
        *,
        use_search_skill: bool = False,
        task_settings=None,
    ) -> None:
        """bot: OpenApiBotPort(round-trip 投 search skill);discover: BotDiscoverServiceProtocol(语义预查候选)。

        None=stub 路径(恒 MISS)。候选集由框架预查喂入。
        bcn/join_gate: 可选——JOIN 灰度开关(``join_gate.is_enabled()``)开启时,对 LLM 决出的 assignee 做
          ``task_claim_mode-on`` 名单交集(``bcn.list_bots_by_task_modes(claim=True)``,进程内 TTL 缓存)。
          二者任一为 None → JOIN 关闭(透传),保持 stub/测试/灰度默认关链路不变。
        """
        self._bot = bot
        self._discover = discover
        self._bcn = bcn
        self._join_gate = join_gate
        self._use_search_skill = use_search_skill
        self._task_settings = task_settings

    async def matches(self, node: TaskNode, graph: TaskExecutionGraph) -> bool:
        return True  # 兜底

    async def apply(self, node: TaskNode, graph: TaskExecutionGraph) -> SearchResult:
        if self._bot is None or self._discover is None:
            logger.warning(
                "[task][search] task=%s node=%s dispatch unavailable bot_port=%s discover=%s → MISS(no_port_stub)",
                node.task_id,
                node.node_id,
                type(self._bot).__name__ if self._bot is not None else "None",
                type(self._discover).__name__ if self._discover is not None else "None",
            )
            return SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_port_stub")
        owner = compose_bot_identity(
            str(graph.extend_props.get("owner_bot_id") or ""),
            graph.extend_props.get("owner_user_id"),
        )
        if not owner:
            logger.warning(
                "[task][search] task=%s node=%s owner identity missing owner_bot_id=%s owner_user_id=%s → MISS(no_owner)",
                node.task_id,
                node.node_id,
                graph.extend_props.get("owner_bot_id"),
                graph.extend_props.get("owner_user_id"),
            )
            return SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_owner")
        candidates = await _prefetch_candidates(self._discover, node, graph)
        if not candidates:
            logger.info(
                "[task][search] task=%s node=%s 候选为空→MISS(no_candidates)",
                node.task_id,
                node.node_id,
            )
            return SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_candidates")
        candidate_ids = [c.get("bot_id") for c in candidates]
        logger.info(
            "[task][search] task=%s owner=%s node=%s candidate_count=%d candidate_ids=%s",
            node.task_id,
            owner,
            node.node_id,
            len(candidate_ids),
            candidate_ids,
        )
        use_skill = self._use_search_skill
        setting_source = "constructor"
        if self._task_settings is not None:
            use_skill = self._task_settings.is_enabled("search_skill")
            setting_source = "task_settings"
        logger.info(
            "[task][search] task=%s node=%s decision_mode=%s use_search_skill=%s source=%s candidate_count=%d",
            node.task_id,
            node.node_id,
            "skill" if use_skill else "rule",
            use_skill,
            setting_source,
            len(candidates),
        )
        if use_skill:
            prompt = _compose_search_prompt(node, candidates)
            run = await self._bot.send_and_wait_async(
                bot_id=owner,
                message=prompt,
                metadata={"phase": "search"},
            )
            sr = _parse_search_result(run)
            logger.info(
                "[task][search] task=%s node=%s raw_skill_status=%s raw_skill_result=%s",
                node.task_id,
                node.node_id,
                run.get("status") if isinstance(run, dict) else None,
                _search_result_summary(sr),
            )
        else:
            bot_pool = await self._load_rule_test_pool()
            sr = _rule_based_search_result(candidates, bot_pool)
            logger.info(
                "[task][search] task=%s node=%s 使用候选数量规则 outcome=%s bot_id=%s bot_ids=%s pool_size=%s",
                node.task_id,
                node.node_id,
                sr.outcome,
                sr.bot_id,
                sr.group_formation.bot_ids if sr.group_formation else None,
                len(bot_pool),
            )
        before_join = _search_result_summary(sr)
        # JOIN 灰度开关:开启时对决出的 assignee 做 task_claim_mode-on 名单交集(下游 post-filter)
        sr = await self._apply_claim_join(sr, candidates)
        logger.info(
            "[task][search] task=%s node=%s final_result before_claim_join=%s after_claim_join=%s",
            node.task_id,
            node.node_id,
            before_join,
            _search_result_summary(sr),
        )
        # 把任务描述(目标)塞进 GroupFormation.extend_props,供 form_coop_group 设 BCS 建群 context
        # (→ <GroupContext> `目标`);与 _run_yaml 路径对齐。取 goal.objective→instruction→title。
        if sr.group_formation is not None:
            _spec = node.task_spec
            _tc = (
                (
                    _spec.goal.objective
                    or _spec.metadata.instruction
                    or _spec.metadata.title
                )
                or ""
            ).strip()
            if _tc:
                sr.group_formation.extend_props["task_context"] = _tc
        logger.info(
            "[task][task_dispatch_search] node=%s → outcome=%s bot_id=%s group=%s miss=%s",
            node.node_id,
            sr.outcome,
            sr.bot_id,
            sr.group_id,
            sr.miss_reason,
        )
        return sr

    async def _load_rule_test_pool(self) -> list[str]:
        """Rule 模式候选池:动态取 ``task_claim_mode=true & visibility=public`` 的 bot_id(``product:owner``)。

        不同环境 claim 名单不同,不再写死。bcn 缺失 / 查询失败 / 名单空 → 返回空列表,
        由 :func:`_rule_based_search_result` 兜底 ``MISS(no_claim_pool)``。复用与
        ``_apply_claim_join`` 完全相同的查询参数(进程内 TTL 缓存命中,无额外出网开销)。
        """
        if self._bcn is None:
            return []
        try:
            entries = await asyncio.to_thread(
                self._bcn.list_bots_by_task_modes,
                claim=True,
                dream=None,
                match="all",
                visibility="public",
            )
        except Exception as exc:  # noqa: BLE001 roster is a best-effort rule-pool input
            logger.warning("[task][search][rule-pool] roster 取失败→空池: %s", exc)
            return []
        return [e.get("bot_id") for e in (entries or []) if e.get("bot_id")]

    async def _apply_claim_join(
        self, sr: "SearchResult", candidates: list[dict]
    ) -> "SearchResult":
        """JOIN 灰度开关:对 LLM 决出的 assignee 做 ``task_claim_mode-on`` 名单交集(下游 post-filter)。

        开关关 / bcn 缺失 → 透传原 SearchResult(保持 stub/测试/灰度默认关链路)。
        开关开:
        - 取 claim_on 名单(``bcn.list_bots_by_task_modes(claim=True, dream=None, match="any")``,
          进程内 TTL 缓存);BCS 取名册异常 / 名单为空 → fail-open 透传(不阻断派发)。
        - 按 ``bot_id`` 归一比对:候选是 product(``{p}``),claim_on 条目是 bcs(``{p}:{o}``),按 product(首段)。
        - HIT_SINGLE: ``bot_id`` ∈ claim_on → 保留;否则降 MISS(``claim_mode_off``),并把丢掉的候选写
          ``unauthorized_bots``(dashboard 暴露,引导 owner 开「任务认领」grant)。
        - HIT_MULTI_BOTS: 保留 ``bot_ids ∩ claim_on``;全空 → MISS(``claim_mode_off_multi``)+丢全部候选;
          剩 ≥2 → HIT_MULTI(替换 bot_ids,保留 collab_mode 等)+丢未命中候选;剩 1 → 降 HIT_SINGLE + 丢未命中候选。
        - MISS / HIT_GROUP: 不动。
        """
        if (
            self._join_gate is None
            or not self._join_gate.is_enabled()
            or self._bcn is None
        ):
            return sr
        try:
            entries = await asyncio.to_thread(
                self._bcn.list_bots_by_task_modes,
                claim=True,
                dream=None,
                match="all",
                visibility="public",
            )
        except Exception as exc:
            logger.warning(
                "[task][search][claim-join] roster 取失败→fail-open 透传: %s", exc
            )
            return sr
        claim_on = {
            _claim_product(e.get("bot_id")) for e in (entries or []) if e.get("bot_id")
        }
        if not claim_on:
            logger.info("[task][search][claim-join] claim_on 名单为空→fail-open 透传")
            return sr

        if sr.outcome == SearchOutcome.HIT_SINGLE:
            if _claim_product(sr.bot_id) in claim_on:
                return sr
            logger.info(
                "[task][search][claim-join] HIT_SINGLE bot=%s claim_mode off→MISS",
                sr.bot_id,
            )
            return SearchResult(
                outcome=SearchOutcome.MISS,
                miss_reason="claim_mode_off",
                unauthorized_bots=_dropped_unauthorized(candidates, [sr.bot_id]),
            )

        if (
            sr.outcome == SearchOutcome.HIT_MULTI_BOTS
            and sr.group_formation is not None
        ):
            gf = sr.group_formation
            kept = [b for b in gf.bot_ids if _claim_product(b) in claim_on]
            dropped = [b for b in gf.bot_ids if _claim_product(b) not in claim_on]
            if len(kept) == len(gf.bot_ids):
                return sr  # 全命中,原样
            if not kept:
                logger.info(
                    "[task][search][claim-join] HIT_MULTI 全 claim_mode off→MISS bot_ids=%s",
                    gf.bot_ids,
                )
                return SearchResult(
                    outcome=SearchOutcome.MISS,
                    miss_reason="claim_mode_off_multi",
                    unauthorized_bots=_dropped_unauthorized(candidates, dropped),
                )
            if len(kept) >= 2:
                logger.info(
                    "[task][search][claim-join] HIT_MULTI 部分命中→保留 %s", kept
                )
                return SearchResult(
                    outcome=SearchOutcome.HIT_MULTI_BOTS,
                    group_formation=replace(gf, bot_ids=kept),
                    unauthorized_bots=_dropped_unauthorized(candidates, dropped),
                )
            # 恰剩 1:降 HIT_SINGLE,回查 candidates 取展示字段
            single = _find_candidate(candidates, kept[0])
            logger.info(
                "[task][search][claim-join] HIT_MULTI 命中剩 1→降 HIT_SINGLE bot=%s",
                kept[0],
            )
            unauth = _dropped_unauthorized(candidates, dropped)
            if single is None:
                return SearchResult(
                    outcome=SearchOutcome.HIT_SINGLE, bot_id=kept[0], unauthorized_bots=unauth
                )
            return SearchResult(
                outcome=SearchOutcome.HIT_SINGLE,
                bot_id=single.get("bot_id"),
                bot_name=single.get("bot_name"),
                owner_id=single.get("owner_id"),
                owner_name=single.get("owner_name"),
                unauthorized_bots=unauth,
            )
        return sr


def _search_result_summary(result: SearchResult) -> dict[str, Any]:
    """Bounded result summary for dispatch logs, excluding prompt/output content."""
    gf = result.group_formation
    return {
        "outcome": result.outcome.value,
        "bot_id": result.bot_id,
        "group_id": result.group_id,
        "bot_ids": list(gf.bot_ids) if gf is not None else None,
        "collab_mode": gf.collab_mode if gf is not None else None,
        "manager_bot_id": (
            gf.extend_props.get("manager_bot_id") if gf is not None else None
        ),
        "miss_reason": result.miss_reason,
    }


def _dropped_unauthorized(candidates: list[dict], dropped_ids: list[str | None]) -> list[dict]:
    """JOIN 丢掉的候选 → ``unauthorized_bots`` 条目列表(供 dispatcher 写 run_info.extend_props)。

    与 dashboard 现有 ``unauthorized_bots`` 契约对齐:``{bot_id(无冒号 product), owner_user_id, reason}``。
    owner 优先取 ``_find_candidate`` 回查候选的 ``owner_id``(BCSFuse/search item 自带 owner_id);
    无候选回查时(规则派发动态 claim 池的 ``product:owner`` bot 不在 prefetch 候选内),
    退而从 bot_id 的 ``:owner`` 后缀解析,避免 ``owner_user_id`` 落空串。
    reason=``claim_mode_off``(claim_on 未开启)。"""
    out: list[dict] = []
    for bid in dropped_ids:
        c = _find_candidate(candidates, bid)
        owner = (c.get("owner_id") if c else "") or ""
        if not owner:
            _, sep, suffix = (bid or "").partition(":")
            owner = suffix if sep else ""
        out.append(
            {
                "bot_id": _claim_product(bid),
                "owner_user_id": owner or "",
                "reason": "claim_mode_off",
            }
        )
    return out


def _claim_product(bot_id: str | None) -> str:
    """归一 bcs(``{p}:{o}``) / product(``{p}``) → product(首段)。"""
    bid = (bot_id or "").strip()
    return bid.split(":", 1)[0] if bid else ""


def _find_candidate(candidates: list[dict], bot_id: str | None) -> dict | None:
    """在 prefetch 候选里按 product 找展示字段(bot_name/owner_id/owner_name),供 multi→single 降级回填。"""
    if not bot_id:
        return None
    prod = _claim_product(bot_id)
    if not prod:
        return None
    for c in candidates or []:
        if isinstance(c, dict) and _claim_product(c.get("bot_id")) == prod:
            return c
    return None


def _query_text(node: TaskNode) -> dict:
    """提取 node 三字段(title/objective/background)供分字段语义预查。"""
    spec = node.task_spec
    return {
        "title": spec.metadata.title or "",
        "objective": spec.goal.objective or "",
        "background": spec.context.background if spec.context else "",
    }


def _tokenize(text: str) -> list[str]:
    """中文分词(jieba)取 ≥2 字语义词供 LIKE 预查;jieba 未装→退回整串(可跑但精度降级)。
    拆词避免整串 ``LIKE '%长句%'`` 命中 0 → fallback 塞全量噪音 bot 的问题(决策非查找)。"""
    if not text:
        return []
    try:
        import jieba  # type: ignore[import-untyped]
    except ImportError:
        return [text]
    return [w for w in jieba.cut(text) if len(w.strip()) >= 2]


async def _prefetch_candidates(
    discover, node: TaskNode, graph: TaskExecutionGraph
) -> list[dict]:
    """框架候选预查:对 node 的 title/objective/background 各 jieba 分词,每 token 调 name/owner LIKE
    ``search_by_keyword``
    (命中 0→空,不 fallback 全量),合并去重按 recommend.score 降序。discover.search_by_keyword 是同步
    requests,经 asyncio.to_thread 包;多 token 用 asyncio.gather 并发。user_id 取 graph 派生
    owner_bot_id;filters={"runtime_state":["online"]},top_k=10,min_score=0.01。"""
    import asyncio

    texts = _query_text(node)
    user_id = str(graph.extend_props.get("owner_bot_id") or "")
    # 三字段分词 → tokens 去重保序
    tokens: list[str] = []
    seen_tok: set[str] = set()
    for fld in ("title", "objective", "background"):
        for t in _tokenize(texts.get(fld) or ""):
            if t not in seen_tok:
                seen_tok.add(t)
                tokens.append(t)
    if not tokens:
        return []
    logger.info("[task][search] task=%s node=%s 分词 tokens=%s", node.task_id, node.node_id, tokens)

    async def _q(kw: str) -> list[dict]:
        try:
            res = await asyncio.to_thread(
                discover.search_by_keyword,
                keyword=kw,
                user_id=user_id,
                top_k=10,
                min_score=0.01,
                filters={"runtime_state": ["online"]},
            )
        except Exception:  # noqa: BLE001  端口异常→该 token 无候选,不阻断其它
            return []
        return (res or {}).get("items") or []

    items_lists = await asyncio.gather(*[_q(t) for t in tokens])
    seen: dict[str, dict] = {}
    for items in items_lists:
        for item in items:
            bid = item.get("bot_id")
            if bid and bid not in seen:
                seen[bid] = item
    result = sorted(
        seen.values(),
        key=lambda x: (x.get("recommend") or {}).get("score", 0.0),
        reverse=True,
    )
    logger.info(
        "[task][search] task=%s node=%s prefetch_complete token_count=%d candidate_count=%d candidate_ids=%s",
        node.task_id,
        node.node_id,
        len(tokens),
        len(result),
        [c.get("bot_id") for c in result],
    )
    return result


def _rule_based_search_result(
    candidates: list[dict], bot_pool: list[str]
) -> SearchResult:
    """Build a deterministic-shape result with random test Bot identities.

    The discovered candidate count controls the dispatch shape, while the
    final assignees come from ``bot_pool`` — a dynamic roster of
    ``task_claim_mode=true & visibility=public`` bots in ``product:owner`` form
    (fetched by :meth:`SearchBasedDispatchStrategy._load_rule_test_pool`), so the
    pool adapts to each environment instead of a hardcoded internal list. One or
    two candidates produce one single-Bot assignee. More than two candidates
    produce a manager-worker group with the same number of sampled Bots. An empty
    pool (no claim-enabled public bot) degrades to ``MISS(no_claim_pool)``.
    """
    candidate_count = sum(1 for candidate in candidates if candidate.get("bot_id"))
    if candidate_count == 0:
        return SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_candidates")

    if (
        candidate_count == 1
        and random.random() < _SINGLE_CANDIDATE_MISS_PROBABILITY
    ):
        logger.info(
            "[task][search] rule mode single candidate randomly missed "
            "to allow BBS escalation probability=%s",
            _SINGLE_CANDIDATE_MISS_PROBABILITY,
        )
        return SearchResult(
            outcome=SearchOutcome.MISS,
            miss_reason="rule_single_candidate_random_miss",
        )

    if not bot_pool:
        logger.info(
            "[task][search] rule mode claim pool 为空→MISS(no_claim_pool) "
            "candidate_count=%s",
            candidate_count,
        )
        return SearchResult(
            outcome=SearchOutcome.MISS, miss_reason="no_claim_pool"
        )

    dispatch_count = 1 if candidate_count <= 2 else candidate_count
    # PRE 定制：固定候选池建协作群最多保留 3 个成员，避免搜推结果直接拉超大群。
    dispatch_count = min(
        dispatch_count, _RULE_TEST_MAX_GROUP_MEMBERS, len(bot_pool)
    )
    selected = random.sample(bot_pool, dispatch_count)
    if dispatch_count == 1:
        bot_id = selected[0]
        _, _, owner_id = bot_id.partition(":")
        return SearchResult(
            outcome=SearchOutcome.HIT_SINGLE,
            bot_id=bot_id,
            owner_id=owner_id or None,
        )

    members_info = [
        {
            "bot_id": bot_id,
            "role": "manager" if index == 0 else "worker",
            "responsibility": (
                "统筹任务、汇总所有成员产出并负责最终结果"
                if index == 0
                else "执行当前任务并向 manager 汇报产出"
            ),
        }
        for index, bot_id in enumerate(selected)
    ]
    return SearchResult(
        outcome=SearchOutcome.HIT_MULTI_BOTS,
        group_formation=GroupFormation(
            bot_ids=selected,
            collab_mode="manager_worker",
            group_name="任务主从协作群",
            members_info=members_info,
        ),
    )


# ===== END PRE rule-mode test customization =====

def _compose_search_prompt(node: TaskNode, candidates: list[dict]) -> str:
    """组 search prompt:{子任务需求, 候选集} + 约定返回格式(4 态)+ 示例。零 case 知识。

    dispatch 是决策非查找:框架预查候选集喂入 prompt,skill 在候选里决出谁执行(who)+ 怎么执行(how,多 bot 拉哪种协作群)。
    约定返回数据格式 = JSON 字符串,``outcome`` 字段标 4 态之一: HIT_SINGLE / HIT_GROUP / HIT_MULTI_BOTS / MISS。
    """
    import json as _json

    spec = node.task_spec
    demand = {
        "node_id": node.node_id,
        "goal": spec.goal.objective,
        "instruction": spec.metadata.instruction,
        "acceptances": [
            {"id": a.id, "description": a.description} for a in spec.goal.acceptances
        ],
    }
    catalog = [
        {
            "bot_id": c.get("bot_id"),
            "owner_id": c.get("owner_id"),
            "owner_name": c.get("owner_name"),
            "bot_name": c.get("bot_name"),
            "bot_desc": c.get("bot_desc"),
            "score": (c.get("recommend") or {}).get("score"),
            "short_profile": (c.get("recommend") or {}).get("short_profile"),
            "reasons": (c.get("recommend") or {}).get("reasons"),
        }
        for c in candidates
    ]

    return_fmt = (
        "## 返回数据格式约定\n"
        "返回 JSON 字符串,``outcome`` 标 4 态之一,其余字段随态而定: \n"
        '- **HIT_SINGLE**(单 bot 足够): ``{"outcome":"HIT_SINGLE","bot_id":"<bot_id>","bot_name":"<bot_name>","owner_id":"<owner_id>","owner_name":"<owner_name>"}``\n'
        '- **HIT_GROUP**(已有协作群可复用): ``{"outcome":"HIT_GROUP","group_id":"<group_id>"}``\n'
        "- **HIT_MULTI_BOTS**(多 bot 协同,需动态拉协作群；协作群成员最多 3 个，超过 3 个只保留最匹配的 3 个，manager 放在首位):\n"
        '  ``{"outcome":"HIT_MULTI_BOTS","bot_ids":["b1","b2"],"collab_mode":"chat|manager_worker|state_machine",\n'
        '   "group_name":"<协作群名>","members_info":[{"bot_id":"b1","role":"<角色>","responsibility":"<职责>"}],\n'
        '   "manager_bot_id":"<manager_bot_id>(collab_mode=manager_worker 时必填)",\n'
        '   "definition_yaml":"<workflow yaml>(collab_mode=state_machine 时必填)"}``\n'
        '- **MISS**(候选都不匹配): ``{"outcome":"MISS","miss_reason":"<原因>"}``\n\n'
        "### 示例数据(HIT_SINGLE)\n"
        "```json\n"
        '{"outcome":"HIT_SINGLE","bot_id":"供应链专家Bot","bot_name":"供应链专家Bot","owner_id":"<owner_id>","owner_name":"<owner_name>"}\n'
        "```\n"
        "### 示例数据(HIT_MULTI_BOTS,主从协作群)\n"
        "```json\n"
        '{"outcome":"HIT_MULTI_BOTS","bot_ids":["市场需求分析Bot","资本市场投资Bot"],"collab_mode":"manager_worker",\n'
        ' "group_name":"存储行业市场发展趋势研究群","manager_bot_id":"市场需求分析Bot",\n'
        ' "members_info":[{"bot_id":"市场需求分析Bot","role":"manager","responsibility":"规模/增速/出货量"},\n'
        '                 {"bot_id":"资本市场投资Bot","role":"worker","responsibility":"资本开支周期/库存周期"}]}\n'
        "```\n"
        "### 示例数据(MISS)\n"
        "```json\n"
        '{"outcome":"MISS","miss_reason":"候选 bot 均无法覆盖子任务需求"}\n'
        "```"
    )
    return (
        f"[task-search] 请基于以下子任务需求与候选 bot 集决出执行者(who)与协作方式(how)。协作群最多 3 个成员，禁止返回超过 3 个 bot_ids 或 members_info。\n"
        f"子任务需求+候选集\n{_json.dumps({'demand': demand, 'catalog': catalog}, ensure_ascii=False)}\n\n{return_fmt}\n\n{NO_WEB_SEARCH_CONSTRAINT}"
    )


def _parse_search_result(run: dict) -> SearchResult:
    """解析 owner bot round-trip 结果 run{status,result,error} → SearchResult 4 态。

    约定 result.content 为 JSON(裸或被散文/```json 代码块包裹均支持,经 ``extract_json`` 提取):
        {"outcome": "HIT_SINGLE", "bot_id": "..."}
        {"outcome": "HIT_GROUP", "group_id": "..."}
        {"outcome": "HIT_MULTI_BOTS", "bot_ids": [...], "collab_mode": "chat|manager_worker|state_machine",
         "group_name": "...", "members_info": [...], "definition_yaml": "...", "manager_bot_id": "..."}
        {"outcome": "MISS", "miss_reason": "..."}
    异常/非终态 → MISS(parse_error / run_status_xxx)。
    """
    status = str(run.get("status") or "").upper()
    if status != "COMPLETED":
        return SearchResult(
            outcome=SearchOutcome.MISS, miss_reason=f"run_status_{status or 'unknown'}"
        )
    content = (
        (run.get("result") or {}).get("content")
        if isinstance(run.get("result"), dict)
        else run.get("result")
    )
    if not content:
        return SearchResult(outcome=SearchOutcome.MISS, miss_reason="empty_content")
    try:
        data = extract_json(content)  # 鲁棒解析:裸 JSON / ```json 代码块 / 散文包裹
    except (ValueError, TypeError):
        return SearchResult(outcome=SearchOutcome.MISS, miss_reason="parse_error")
    if not isinstance(data, dict):
        return SearchResult(outcome=SearchOutcome.MISS, miss_reason="not_object")
    outcome = str(data.get("outcome") or "").upper()
    if outcome == "HIT_SINGLE":
        return SearchResult(
            outcome=SearchOutcome.HIT_SINGLE,
            bot_id=data.get("bot_id"),
            bot_name=data.get("bot_name"),
            owner_id=data.get("owner_id"),
            owner_name=data.get("owner_name"),
        )
    if outcome == "HIT_GROUP":
        return SearchResult(
            outcome=SearchOutcome.HIT_GROUP, group_id=data.get("group_id")
        )
    if outcome == "HIT_MULTI_BOTS":
        bot_ids = list(data.get("bot_ids") or [])
        if not bot_ids:
            return SearchResult(
                outcome=SearchOutcome.MISS, miss_reason="hit_multi_no_bot_ids"
            )
        gf = GroupFormation(
            bot_ids=bot_ids,
            collab_mode=str(data.get("collab_mode") or "chat"),
            group_name=data.get("group_name"),
            members_info=data.get("members_info"),
            extend_props={},
        )
        def_yaml = data.get("definition_yaml")
        if def_yaml:
            gf.extend_props["definition_yaml"] = def_yaml
        mgr = data.get("manager_bot_id")
        if mgr:
            gf.extend_props["manager_bot_id"] = mgr
        return SearchResult(outcome=SearchOutcome.HIT_MULTI_BOTS, group_formation=gf)
    return SearchResult(
        outcome=SearchOutcome.MISS,
        miss_reason=data.get("miss_reason") or "unknown_outcome",
    )
