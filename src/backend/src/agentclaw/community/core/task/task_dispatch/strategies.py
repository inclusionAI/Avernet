"""TaskDispatcher 内置派发优化策略库(引擎自带,不开放自定义)。

对齐 plan.md §3.4(first-match-wins by priority)。策略经 ``execution_config`` 动态匹配,
类 SQL optimizer:config 有 ``bot`` → DirectDispatchStrategy(跳过搜推直接填);
否则兜底 SearchBasedDispatchStrategy(搜推)。Avernet 默认 stub(search 恒 MISS);
真实 catalog 搜推 + 多 bot 拉群为引擎默认实现(端口由 DI 注入)。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from agentclaw.community.core.task.domain.json_extract import extract_json
from agentclaw.community.core.task.domain.models import TaskExecutionGraph, TaskNode
from agentclaw.community.core.task.domain.prompt_constants import NO_WEB_SEARCH_CONSTRAINT

logger = logging.getLogger("task.dispatcher")


class SearchOutcome(StrEnum):
    """搜推 4 态结果。"""

    HIT_SINGLE = "HIT_SINGLE"           # 单 bot 命中
    HIT_GROUP = "HIT_GROUP"             # 协作群命中(已有群)
    HIT_MULTI_BOTS = "HIT_MULTI_BOTS"   # 多 bot 命中,需动态拉协作群
    MISS = "MISS"                       # 未匹配执行者


@dataclass
class GroupFormation:
    """动态拉协作群参数(HIT_MULTI_BOTS 时 search 一并决出;内部参数,不持久 RuntimeInfo)。

    透传 BCS 建群(BcsCreateGroupRequest):``group_name``→``context``/``topic``(当前无 label 字段)/
    ``members_info``→``participants[].role``/``extend_props["definition_yaml"]``→``collaboration_definition_yaml``。
    """

    bot_ids: list[str]
    collab_mode: str                    # "chat"/"manager_worker"/"state_machine"(state_machine 注入 workflow yaml)
    group_name: str | None = None       # skill 决出协作群名 → BCS 透传
    members_info: list[dict] | None = None  # [{bot_id, role, responsibility}] → BCS participants[].role
    extend_props: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    """搜推结果。"""

    outcome: SearchOutcome
    bot_id: str | None = None                       # HIT_SINGLE
    group_id: str | None = None                     # HIT_GROUP
    group_formation: GroupFormation | None = None   # HIT_MULTI_BOTS
    miss_reason: str | None = None                  # MISS


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
        return cfg.get("bot") is not None

    async def apply(self, node: TaskNode, graph: TaskExecutionGraph) -> SearchResult:
        cfg = graph.extend_props.get("execution_config", {}) or {}
        return SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id=cfg.get("bot"))


class SearchBasedDispatchStrategy:
    """默认兜底:搜推匹配(决策非查找)。两步:① 框架语义预查候选集(分字段 title/objective/background)
    → ② 投 owner bot search skill 在候选里决出 who+how → 4 态 SearchResult。端口(bot/discover)由 DI 注入;
    省略端口 = stub 路径(纯内核单测)恒 MISS。搜推 skill 不自取 BCSFuse,候选集由框架预查喂入 prompt。

    owner bot = ``graph.extend_props["owner_bot_id"]``(框架派生取,零 case 知识)。
    """

    rule_id = "search"
    priority = 99

    def __init__(self, bot=None, discover=None, *, bcs=None) -> None:
        """bot: OpenApiBotPort(round-trip 投 search skill);discover: BotDiscoverServiceProtocol(语义预查候选)。
        None=stub 路径(恒 MISS)。

        bcs:可选,按任务模式开关圈定派发候选。provider 身份由 BCS 端口自带凭据提供；未配置时
        圈定关闭，沿用全部 discover 候选。roster 不可用时 fail-open；健康但为空时清空候选交 MISS。
        """
        self._bot = bot
        self._discover = discover
        self._bcs = bcs

    async def matches(self, node: TaskNode, graph: TaskExecutionGraph) -> bool:
        return True  # 兜底

    async def apply(self, node: TaskNode, graph: TaskExecutionGraph) -> SearchResult:
        if self._bot is None or self._discover is None:
            return SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_port_stub")
        owner = str(graph.extend_props.get("owner_bot_id") or "")
        if not owner:
            return SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_owner")
        candidates = await _prefetch_candidates(self._discover, node, graph)
        candidates = await _scope_by_task_mode_roster(self._bcs, candidates)
        prompt = _compose_search_prompt(node, candidates)
        logger.info("[search] owner=%s node=%s 候选=%s", owner, node.node_id,
                    [c.get("bot_id") for c in candidates])
        run = await self._bot.send_and_wait_async(
            bot_id=owner, message=prompt, metadata={"phase": "search"},
        )
        sr = _parse_search_result(run)
        # 把任务描述(目标)塞进 GroupFormation.extend_props,供 form_coop_group 设 BCS 建群 context
        # (→ <GroupContext> `目标`);与 _run_yaml 路径对齐。取 goal.objective→instruction→title。
        if sr.group_formation is not None:
            _spec = node.task_spec
            _tc = ((_spec.goal.objective or _spec.metadata.instruction or _spec.metadata.title) or "").strip()
            if _tc:
                sr.group_formation.extend_props["task_context"] = _tc
        logger.info("[task_dispatch_search] node=%s → outcome=%s bot_id=%s group=%s miss=%s",
                    node.node_id, sr.outcome, sr.bot_id, sr.group_id, sr.miss_reason)
        return sr


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


async def _scope_by_task_mode_roster(bcs, candidates: list[dict]) -> list[dict]:
    """按任务模式 roster 圈定派发候选(BCS provider 路由 ``list_bots_by_task_modes``)。

    - BCS 端口未配置 provider 身份或 ``bcs`` 缺省 → 圈定关闭，原样返回候选。
    - roster 调用异常(BCS 不可用 / 401 / 超时)→ fail-open 沿用候选(可用性优先,不阻断派发),仅 warn。
    - roster 健康但空(无 opted-in bot)→ 候选清空(交后续 MISS;即"无 bot opted-in 接任务"的预期裁剪)。
    - 否则候选 ∩ roster 保持(仅 ``task_claim_mode=true`` 的 provider bot 留下)。
    门槛默认 ``claim=true, match=any``(task claim opt-in flag);如需 dream/both 改下方 roster 查询参数。
    """
    if bcs is None:
        return candidates
    provider_id = getattr(bcs, "provider_id", "")
    if not provider_id:
        return candidates
    try:
        roster = await bcs.list_bots_by_task_modes(claim=True, match="any")
    except Exception as ex:  # noqa: BLE001  roster 不可用 → fail-open 沿用候选,不阻断派发
        logger.warning("[search] roster 不可用,fail-open 沿用候选(provider=%s): %s", provider_id, ex)
        return candidates
    allowed = {r.bot_id for r in roster}
    if not allowed:
        logger.info("[search] roster 空(task_claim_mode=true 无 bot),候选清空交 MISS(provider=%s)", provider_id)
        return []
    scoped = [c for c in candidates if c.get("bot_id") in allowed]
    logger.info("[search] roster 圈定 provider=%s 候选 %s→%s", provider_id,
                [c.get("bot_id") for c in candidates], [c.get("bot_id") for c in scoped])
    return scoped


async def _prefetch_candidates(discover, node: TaskNode, graph: TaskExecutionGraph) -> list[dict]:
    """框架候选预查:对 node 的 title/objective/background 各 jieba 分词,每 token 调 search_by_keyword
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
    logger.info("[search] node=%s 分词 tokens=%s", node.node_id, tokens)

    async def _q(kw: str) -> list[dict]:
        try:
            res = await asyncio.to_thread(
                discover.search_by_keyword,
                keyword=kw, user_id=user_id, top_k=10, min_score=0.01,
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
    return sorted(seen.values(), key=lambda x: (x.get("recommend") or {}).get("score", 0.0), reverse=True)


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
            "bot_name": c.get("bot_name"),
            "bot_desc": c.get("bot_desc"),
            "score": (c.get("recommend") or {}).get("score"),
            "short_profile": (c.get("recommend") or {}).get("short_profile"),
            "reasons": (c.get("recommend") or {}).get("reasons"),
        }
        for c in candidates
    ]

    return_fmt = (
        '## 返回数据格式约定\n'
        '返回 JSON 字符串,``outcome`` 标 4 态之一,其余字段随态而定: \n'
        '- **HIT_SINGLE**(单 bot 足够): ``{"outcome":"HIT_SINGLE","bot_id":"<bot_id>"}``\n'
        '- **HIT_GROUP**(已有协作群可复用): ``{"outcome":"HIT_GROUP","group_id":"<group_id>"}``\n'
        '- **HIT_MULTI_BOTS**(多 bot 协同,需动态拉协作群):\n'
        '  ``{"outcome":"HIT_MULTI_BOTS","bot_ids":["b1","b2"],"collab_mode":"chat|manager_worker|state_machine",\n'
        '   "group_name":"<协作群名>","members_info":[{"bot_id":"b1","role":"<角色>","responsibility":"<职责>"}],\n'
        '   "manager_bot_id":"<manager_bot_id>(collab_mode=manager_worker 时必填)",\n'
        '   "definition_yaml":"<workflow yaml>(collab_mode=state_machine 时必填)"}``\n'
        '- **MISS**(候选都不匹配): ``{"outcome":"MISS","miss_reason":"<原因>"}``\n\n'
        '### 示例数据(HIT_SINGLE)\n'
        '```json\n'
        '{"outcome":"HIT_SINGLE","bot_id":"供应链专家Bot"}\n'
        '```\n'
        '### 示例数据(HIT_MULTI_BOTS,主从协作群)\n'
        '```json\n'
        '{"outcome":"HIT_MULTI_BOTS","bot_ids":["市场需求分析Bot","资本市场投资Bot"],"collab_mode":"manager_worker",\n'
        ' "group_name":"存储行业市场发展趋势研究群","manager_bot_id":"市场需求分析Bot",\n'
        ' "members_info":[{"bot_id":"市场需求分析Bot","role":"manager","responsibility":"规模/增速/出货量"},\n'
        '                 {"bot_id":"资本市场投资Bot","role":"worker","responsibility":"资本开支周期/库存周期"}]}\n'
        '```\n'
        '### 示例数据(MISS)\n'
        '```json\n'
        '{"outcome":"MISS","miss_reason":"候选 bot 均无法覆盖子任务需求"}\n'
        '```'
    )
    return (f"[search] 请基于以下子任务需求与候选 bot 集决出执行者(who)与协作方式(how)。\n"
            f"子任务需求+候选集\n{_json.dumps({'demand': demand, 'catalog': catalog}, ensure_ascii=False)}\n\n{return_fmt}\n\n{NO_WEB_SEARCH_CONSTRAINT}")


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
        return SearchResult(outcome=SearchOutcome.MISS, miss_reason=f"run_status_{status or 'unknown'}")
    content = (run.get("result") or {}).get("content") if isinstance(run.get("result"), dict) else run.get("result")
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
        return SearchResult(outcome=SearchOutcome.HIT_SINGLE, bot_id=data.get("bot_id"))
    if outcome == "HIT_GROUP":
        return SearchResult(outcome=SearchOutcome.HIT_GROUP, group_id=data.get("group_id"))
    if outcome == "HIT_MULTI_BOTS":
        bot_ids = list(data.get("bot_ids") or [])
        if not bot_ids:
            return SearchResult(outcome=SearchOutcome.MISS, miss_reason="hit_multi_no_bot_ids")
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
    return SearchResult(outcome=SearchOutcome.MISS, miss_reason=data.get("miss_reason") or "unknown_outcome")
