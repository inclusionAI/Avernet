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
from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.domain.models import TaskExecutionGraph, TaskNode
from agentclaw.community.core.task.domain.prompt_constants import (
    NO_WEB_SEARCH_CONSTRAINT,
)
from agentclaw.community.core.task.task_dispatch.rationale import (
    _build_search_rationale,
    _extract_skill_response_content,
)
from agentclaw.community.core.task.task_context.task_trajectory.models import DispatchRationale
from agentclaw.community.core.task.task_runner.client.candidate_search import (
    MAX_SEARCH_TOKENS as _PREFETCH_MAX_TOKENS,
    PER_KEYWORD_LIMIT as _PREFETCH_PER_KEYWORD_LIMIT,
    search_candidates as _search_candidates,
    search_tokens as _prefetch_tokens,
    tokenize_query,
)

logger = logging.getLogger("task.dispatcher")

# Candidate retrieval is shared with distributed Relay `/search`; this module
# only makes the centralized dispatch decision after retrieval.
_RULE_TEST_MAX_GROUP_MEMBERS = 3


def _tokenize(text: str) -> list[str]:
    """Backward-compatible test/helper alias for the shared tokenizer."""
    return tokenize_query(text)

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
    # REQ-2 DISPATCH rationale —— 策略 apply 填充,经 dispatcher 写入
    # ``node.run_info.extend_props["_dispatch_rationale"]`` 透出到引擎 DISPATCH 闸门;
    # ``None`` 表示策略未填(直驱/重投/装配失败 —— 装配全程 try/except 见
    # ``_build_search_rationale``)。候选/分/join_dropped 一并由此字段传递,
    rationale: DispatchRationale | None = None


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
            sr = SearchResult(outcome=SearchOutcome.HIT_MULTI_BOTS, group_formation=static_group)
        else:
            sr = SearchResult(
                outcome=SearchOutcome.HIT_SINGLE,
                bot_id=node.run_info.extend_props.get("static_bot_id") or cfg.get("bot"),
            )
        # REQ-2 DISPATCH rationale —— 直驱策略:跳过搜推/JOIN;rationale 仅标
        # strategy=direct/decision=direct,候选/分/join_dropped 空集(REQ-9
        # boost_reason 兜底"策略=direct 模式=direct …"经 ext_info 还原)。
        # 字面量化字段构造:无外部输入(无 candidate.score / 等),不会因装配抛错 —— 不
        # 包 try/except,任何字段名拼写错误直接抛(相对地,search 路径有外部输入,经
        # ``_build_search_rationale`` 全程 try/except 兜底成 None — 两条路径的装配风险不对称,
        # 防御性包一层仅必要于搜索路径)。
        sr.rationale = DispatchRationale(
            strategy_name="direct",
            decision_mode="direct",
            join_filter_applied=False,
        )
        return sr


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
        *,
        use_search_skill: bool = False,
        task_settings=None,
    ) -> None:
        """bot: OpenApiBotPort(round-trip 投 search skill);discover: BotDiscoverServiceProtocol(语义预查候选)。

        None=stub 路径(恒 MISS)。候选集由框架预查喂入。
        bcn: legacy compatibility parameter; BBS claim eligibility is resolved by BBS
        execution code and never constrains task-dispatch candidates.
        """
        self._bot = bot
        self._discover = discover
        self._bcn = bcn
        self._use_search_skill = use_search_skill
        self._task_settings = task_settings

    async def matches(self, node: TaskNode, graph: TaskExecutionGraph) -> bool:
        return True  # 兜底

    def _resolve_use_skill(self) -> bool:
        """``use_search_skill`` 决议:``task_settings`` 优先,否则构造器默认。"""
        if self._task_settings is not None:
            return bool(self._task_settings.is_enabled("search_skill"))
        return bool(self._use_search_skill)

    def _set_rationale(
        self,
        node: TaskNode,
        candidates: list[dict],
        sr: SearchResult,
        *,
        use_skill: bool,
        prompt_text: str | None,
        response_text: str | None,
    ) -> None:
        """attach ``DispatchRationale`` to ``sr.rationale``(try/except-safe;失败赋 None)。

        ``prefetch_tokens`` 由本模块 ``_prefetch_tokens`` 计算(与 ``_prefetch_candidates``
        共用,审计可重放)并按 kw 传入 builder —— ``rationale._build_search_rationale``
        保持为 leaf 模块(不 back-import 本模块)。
        """
        sr.rationale = _build_search_rationale(
            node=node,
            candidates=candidates,
            sr=sr,
            use_skill=use_skill,
            prompt_text=prompt_text,
            response_text=response_text,
            filter_ran=False,
            prefetch_tokens=_prefetch_tokens(node.task_spec.goal.objective or ""),
        )

    async def apply(self, node: TaskNode, graph: TaskExecutionGraph) -> SearchResult:
        use_skill = self._resolve_use_skill()
        if self._bot is None or self._discover is None:
            logger.warning(
                "[task][search] task=%s node=%s dispatch unavailable bot_port=%s discover=%s → MISS(no_port_stub)",
                node.task_id,
                node.node_id,
                type(self._bot).__name__ if self._bot is not None else "None",
                type(self._discover).__name__ if self._discover is not None else "None",
            )
            sr = SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_port_stub")
            self._set_rationale(node, [], sr, use_skill=use_skill, prompt_text=None, response_text=None)
            return sr
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
            sr = SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_owner")
            self._set_rationale(node, [], sr, use_skill=use_skill, prompt_text=None, response_text=None)
            return sr
        candidates = await _prefetch_candidates(self._discover, node, graph)
        if not candidates:
            logger.info(
                "[task][search] task=%s node=%s 候选为空→MISS(no_candidates)",
                node.task_id,
                node.node_id,
            )
            sr = SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_candidates")
            self._set_rationale(node, [], sr, use_skill=use_skill, prompt_text=None, response_text=None)
            return sr
        candidate_ids = [c.get("bot_id") for c in candidates]
        logger.info(
            "[task][search] task=%s owner=%s node=%s candidate_count=%d candidate_ids=%s",
            node.task_id,
            owner,
            node.node_id,
            len(candidate_ids),
            candidate_ids,
        )
        setting_source = "task_settings" if self._task_settings is not None else "constructor"
        logger.info(
            "[task][search] task=%s node=%s decision_mode=%s use_search_skill=%s source=%s candidate_count=%d",
            node.task_id,
            node.node_id,
            "skill" if use_skill else "rule",
            use_skill,
            setting_source,
            len(candidates),
        )
        prompt_text: str | None = None
        response_text: str | None = None
        if use_skill:
            prompt_text = _compose_search_prompt(node, candidates)
            run = await self._bot.send_and_wait_async(
                bot_id=owner,
                message=prompt_text,
                metadata={"phase": "search"},
            )
            response_text = _extract_skill_response_content(run)
            sr = _parse_search_result(run)
            logger.info(
                "[task][search] task=%s node=%s raw_skill_status=%s raw_skill_result=%s",
                node.task_id,
                node.node_id,
                run.get("status") if isinstance(run, dict) else None,
                _search_result_summary(sr),
            )
        else:
            # 规则模式同样直接使用预查候选；task_claim_mode 只约束 BBS 广场
            # 可认领 Bot，不参与任务派发候选的后置过滤。
            dispatch_ids = _candidate_dispatch_ids(candidates)
            sr = _offpath_normal(dispatch_ids)
            logger.info(
                "[task][search] task=%s node=%s rule outcome=%s bot_id=%s bot_ids=%s "
                "candidate_count=%d",
                node.task_id,
                node.node_id,
                sr.outcome,
                sr.bot_id,
                sr.group_formation.bot_ids if sr.group_formation else None,
                len(dispatch_ids),
            )
        logger.info(
            "[task][search] task=%s node=%s final_result=%s",
            node.task_id,
            node.node_id,
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
        # REQ-2 DispatchRationale 装配(全程 try/except,失败→None,不阻断派发)。
        # use_skill 时填 prompt/response digest;rule 模式 None。
        self._set_rationale(
            node, candidates, sr,
            use_skill=use_skill,
            prompt_text=prompt_text,
            response_text=response_text,
        )
        logger.info(
            "[task][task_dispatch_search] node=%s → outcome=%s bot_id=%s group=%s miss=%s rationale=%s",
            node.node_id,
            sr.outcome,
            sr.bot_id,
            sr.group_id,
            sr.miss_reason,
            "set" if sr.rationale is not None else "none",
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


async def _prefetch_candidates(
    discover, node: TaskNode, graph: TaskExecutionGraph
) -> list[dict]:
    """Retrieve the shared tokenized candidate catalog for centralized dispatch."""
    query = node.task_spec.goal.objective or ""
    user_id = str(graph.extend_props.get("owner_bot_id") or "")
    result = await _search_candidates(
        discover,
        query,
        user_id=user_id,
        per_keyword_limit=_PREFETCH_PER_KEYWORD_LIMIT,
        max_tokens=_PREFETCH_MAX_TOKENS,
    )
    logger.info(
        "[task][search] task=%s node=%s prefetch_complete tokens=%s token_count=%d "
        "raw_item_count=%d failed_keywords=%s candidate_count=%d candidate_ids=%s",
        node.task_id,
        node.node_id,
        result.tokens,
        len(result.tokens),
        result.raw_item_count,
        result.failed_keywords,
        len(result.candidates),
        [c.get("bot_uuid") or c.get("bot_id") for c in result.candidates],
    )
    return result.candidates

async def prefetch_candidates(
    discover, node: TaskNode, graph: TaskExecutionGraph
) -> list[dict]:
    """Return the existing dispatch candidate catalog without making a decision."""
    if discover is None:
        return []
    return await _prefetch_candidates(discover, node, graph)


def _candidate_dispatch_ids(candidates: list[dict]) -> list[str]:
    """Return executable identities from the unrestricted dispatch catalog.

    ``bot_uuid`` is preferred because it already contains the owner/entity part.
    Older catalog responses may only expose ``bot_id`` + ``owner_id``; compose
    the same canonical identity locally without consulting the BBS claim roster.
    """
    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        bot_uuid = candidate.get("bot_uuid")
        identity = str(bot_uuid or "").strip()
        if not identity:
            identity = compose_bot_identity(
                str(candidate.get("bot_id") or ""), candidate.get("owner_id")
            )
        if identity and identity not in seen:
            seen.add(identity)
            result.append(identity)
    return result


def _build_manager_worker_group(bot_ids: list[str]) -> GroupFormation:
    """构造 manager_worker 协作群(首位 manager,其余 worker)。供 off-path/coverage 共用。"""
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
        for index, bot_id in enumerate(bot_ids)
    ]
    return GroupFormation(
        bot_ids=bot_ids,
        collab_mode="manager_worker",
        group_name="任务主从协作群",
        members_info=members_info,
        extend_props={"dynamic_task_node_protocol": True},
    )


def _offpath_normal(joined: list[str]) -> SearchResult:
    """off-path 正常派发:join 结果 + candidate-count,无随机/无回退/无定制。

    joined 空 → ``MISS(no_candidates)``(不回退池);``len(joined)≤2 → HIT_SINGLE``(取 joined[0],
    owner_id 从 ``:owner`` 后缀解析);``len(joined)≥3 → HIT_MULTI_BOTS``(取前
    ``_RULE_TEST_MAX_GROUP_MEMBERS`` 个,manager_worker)。命中啥就是啥。
    """
    if not joined:
        return SearchResult(outcome=SearchOutcome.MISS, miss_reason="no_candidates")
    if len(joined) <= 2:
        bot_id = joined[0]
        _, _, owner_id = bot_id.partition(":")
        return SearchResult(
            outcome=SearchOutcome.HIT_SINGLE,
            bot_id=bot_id,
            owner_id=owner_id or None,
        )
    return SearchResult(
        outcome=SearchOutcome.HIT_MULTI_BOTS,
        group_formation=_build_manager_worker_group(joined[:_RULE_TEST_MAX_GROUP_MEMBERS]),
    )


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
            # 动态规划当前的默认协作实现是 BCS 主从群。策略可显式返回
            # chat/state_machine（以及将来的群形态），执行编排核只透传，
            # 不在下游以默认值或强制改写掩盖策略选择。
            collab_mode=str(data.get("collab_mode") or "manager_worker"),
            group_name=data.get("group_name"),
            members_info=data.get("members_info"),
            extend_props={"dynamic_task_node_protocol": True},
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
