"""TaskDispatcher 内置派发优化策略库(引擎自带,不开放自定义)。

对齐 plan.md §3.4(first-match-wins by priority)。策略经 ``execution_config`` 动态匹配,
类 SQL optimizer:config 有 ``bot`` → DirectDispatchStrategy(跳过搜推直接填);
否则兜底 SearchBasedDispatchStrategy(搜推)。Avernet 默认 stub(search 恒 MISS);
真实 catalog 搜推 + 多 bot 拉群为引擎默认实现(端口由 DI 注入)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from agentclaw.community.core.task.domain.models import TaskExecutionGraph, TaskNode


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

    owner bot = ``graph.extend_props["source_channel_id"]``(框架派生取,零 case 知识)。
    """

    rule_id = "search"
    priority = 99

    def __init__(self, bot=None, discover=None) -> None:
        """bot: OpenApiBotPort(round-trip 投 search skill);discover: BotDiscoverServiceProtocol(语义预查候选)。
        None=stub 路径(恒 MISS)。"""
        self._bot = bot
        self._discover = discover

    async def matches(self, node: TaskNode, graph: TaskExecutionGraph) -> bool:
        return True  # 兜底

    async def apply(self, node: TaskNode, graph: TaskExecutionGraph) -> SearchResult:
        if self._bot is None or self._discover is None:
            return SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_port_stub")
        owner = str(graph.extend_props.get("source_channel_id") or "")
        if not owner:
            return SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_owner")
        candidates = await _prefetch_candidates(self._discover, node, graph)
        prompt = _compose_search_prompt(node, candidates)
        run = await self._bot.send_and_wait_async(
            bot_id=owner, message=prompt, metadata={"phase": "search"},
        )
        return _parse_search_result(run)


def _query_text(node: TaskNode) -> dict:
    """提取 node 三字段(title/objective/background)供分字段语义预查。"""
    spec = node.task_spec
    return {
        "title": spec.metadata.title or "",
        "objective": spec.goal.objective or "",
        "background": spec.context.background if spec.context else "",
    }


async def _prefetch_candidates(discover, node: TaskNode, graph: TaskExecutionGraph) -> list[dict]:
    """框架语义预查候选集:分字段(title/objective/background)各调 discover.search_by_keyword 一次,
    合并去重按 recommend.score 降序。discover.search_by_keyword 是同步 requests,经 asyncio.to_thread 包。
    user_id 取 graph 派生 source_channel_id;filters={"runtime_state":["online"]},top_k=10,min_score=0.01。"""
    import asyncio
    texts = _query_text(node)
    user_id = str(graph.extend_props.get("source_channel_id") or "")
    seen: dict[str, dict] = {}
    for field in ("title", "objective", "background"):
        kw = texts.get(field)
        if not kw:
            continue
        try:
            res = await asyncio.to_thread(
                discover.search_by_keyword,
                keyword=kw, user_id=user_id, top_k=10, min_score=0.01,
                filters={"runtime_state": ["online"]},
            )
        except Exception:  # noqa: BLE001  端口异常→该字段无候选,不阻断其它字段
            continue
        for item in (res or {}).get("items") or []:
            bid = item.get("bot_id")
            if not bid:
                continue
            if bid not in seen:
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
            f"子任务需求+候选集\n{_json.dumps({'demand': demand, 'catalog': catalog}, ensure_ascii=False)}\n\n{return_fmt}")


def _parse_search_result(run: dict) -> SearchResult:
    """解析 owner bot round-trip 结果 run{status,result,error} → SearchResult 4 态。

    约定 result.content 为 JSON 字符串:
        {"outcome": "HIT_SINGLE", "bot_id": "..."}
        {"outcome": "HIT_GROUP", "group_id": "..."}
        {"outcome": "HIT_MULTI_BOTS", "bot_ids": [...], "collab_mode": "chat|manager_worker|state_machine",
         "group_name": "...", "members_info": [...], "definition_yaml": "...", "manager_bot_id": "..."}
        {"outcome": "MISS", "miss_reason": "..."}
    异常/非终态 → MISS(parse_error / run_status_xxx)。
    """
    import json as _json
    status = str(run.get("status") or "").upper()
    if status != "COMPLETED":
        return SearchResult(outcome=SearchOutcome.MISS, miss_reason=f"run_status_{status or 'unknown'}")
    content = (run.get("result") or {}).get("content") if isinstance(run.get("result"), dict) else run.get("result")
    if not content:
        return SearchResult(outcome=SearchOutcome.MISS, miss_reason="empty_content")
    try:
        data = _json.loads(content) if isinstance(content, str) else content
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
