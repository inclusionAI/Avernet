"""TaskPlanner 内置规划优化策略库(引擎自带,不开放自定义)。

对齐 plan.md §3.4(first-match-wins by priority)。策略经 ``execution_config`` 动态匹配,
类 SQL optimizer rule-based 选择:config 有 ``workflow`` → WorkflowPlanningStrategy;否则兜底 GapBased。
GapBased 真实实现:组 planning prompt → 投 owner bot(``send_and_wait_async`` 同步收)→ 解析 PlanResult。
端口(OpenApiBotPort)由 DI 注入。无端口时(纯内核单测)退化为 stub 返空 PlanResult。

Step2 改造:apply 接**显式 target**(由 planner.plan 解析传入;on_fail/on_miss→失败叶,on_pass→父,
on_execute→根自发现)。plan 返 ``PlanResult(children, has_gap, gap_detail)`` 四象限驱动编排:
children 非空→add+dispatch;空+has_gap=F→gap 闭终验通过;空+has_gap=T→深度闸门(升 BBS/HUNG)。
"""
from __future__ import annotations

import logging
from typing import Protocol

from agentclaw.community.core.task.domain.json_extract import extract_json
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    PlanResult,
    RelationType,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.domain.prompt_constants import NO_WEB_SEARCH_CONSTRAINT
from agentclaw.community.core.task.domain.identity import compose_bot_identity

logger = logging.getLogger("task.planner")


class PlanningStrategy(Protocol):
    """规划优化策略契约(引擎内置,first-match-wins)。"""

    rule_id: str
    priority: int

    async def matches(self, graph: TaskExecutionGraph) -> bool:
        """纯读:据图级 execution_config 判本策略是否适用(workflow/yaml 信号)。协程化:corp LLM 判定可耗 IO。"""
        ...

    async def apply(self, graph: TaskExecutionGraph, target: TaskNode) -> PlanResult:
        """针对**显式 target**计算 gap,产它"下一步可执行子节点"(挂其下)。
        返回 ``PlanResult``:children=gap 未闭继续拆;[]+has_gap=F=gap 闭(验收通过);[]+has_gap=T=有 gap 拆不出。
        协程化:corp 真实 LLM 拆解是耗时 IO,await 不阻塞。"""
        ...


class WorkflowPlanningStrategy:
    """config 有 ``workflow``(yaml)→ 加载 yaml 拓扑产出固定 dag 子节点(非 gap 拆解)。

    Avernet stub:读 ``execution_config["workflow"]``(list[str] 子节点 id / dict{parent: [children]});
    corp 真实 yaml 解析+拓扑实例化在 ocb 仓替换本类实现。
    """

    rule_id = "workflow"
    priority = 10

    async def matches(self, graph: TaskExecutionGraph) -> bool:
        cfg = graph.extend_props.get("execution_config", {}) or {}
        return cfg.get("workflow") is not None

    async def apply(self, graph: TaskExecutionGraph, target: TaskNode) -> PlanResult:
        cfg = graph.extend_props.get("execution_config", {}) or {}
        wf = cfg.get("workflow")
        if not wf:
            return PlanResult(children=[], has_gap=False, gap_detail="done")
        task_spec = target.task_spec  # workflow 子节点复用目标 task_spec(stub)
        if isinstance(wf, list):
            children = [_wf_node(nid, target.task_id, task_spec) for nid in wf]
        elif isinstance(wf, dict):
            kids = wf.get(target.node_id, [])
            children = [_wf_node(nid, target.task_id, task_spec) for nid in kids]
        else:
            children = []
        return PlanResult(children=children, has_gap=bool(children), gap_detail="workflow" if children else "done")


class GapBasedPlanningStrategy:
    """默认兜底:基于 gap 的任务规划。组 planning prompt 投 owner bot → 同步收 → 解析 PlanResult。
    端口(bot: OpenApiBotPort)由 DI 注入;省略端口/无 owner = 无法规划 → 返「有 gap 拆不出」
    (has_gap=True,children=[]),编排核走深度闸门 HUNG(不能假 done:没规划过不可能闭 gap)。

    gap 计算与验收同构:apply 返 children=gap 未闭(继续拆/+派发);返 []+has_gap=F=gap 闭=验收通过(节点 DONE 上行)。
    owner bot = ``graph.extend_props["owner_bot_id"]``(框架派生取,零 case 知识)。
    """

    rule_id = "gap_based"
    priority = 99

    def __init__(self, bot=None) -> None:
        """bot: OpenApiBotPort(真实 round-trip);None= stub 路径(无规划端口,返有 gap 拆不出→HUNG)。"""
        self._bot = bot

    async def matches(self, graph: TaskExecutionGraph) -> bool:
        return True  # 兜底

    async def apply(self, graph: TaskExecutionGraph, target: TaskNode) -> PlanResult:
        if self._bot is None:
            # 无规划端口:无法计算 gap / 产子 → 有 gap 拆不出(编排核走深度闸门 HUNG,不假 done)
            return PlanResult(children=[], has_gap=True, gap_detail="no_planning_port")
        owner = compose_bot_identity(
            str(graph.extend_props.get("owner_bot_id") or ""),
            graph.extend_props.get("owner_user_id"),
        )
        if not owner:
            # 有端口但无 owner bot(owner_bot_id 缺失):无人可投规划 prompt → 有 gap 拆不出(→ HUNG)
            return PlanResult(children=[], has_gap=True, gap_detail="no_owner_bot")
        prompt = _compose_planning_prompt(graph, target)
        run = await self._bot.send_and_wait_async(
            bot_id=owner, message=prompt, metadata={"phase": "planning"},
        )
        pr = _parse_plan_result(run, target, graph)
        logger.info("[plan] owner=%s target=%s → children=%d has_gap=%s gap_detail=%s",
                    owner, target.node_id, len(pr.children), pr.has_gap, pr.gap_detail)
        return pr


def _wf_node(node_id: str, task_id: str, task_spec) -> TaskNode:
    """构造 workflow 子节点(PENDING,空 run_info)。"""
    return TaskNode(
        node_id=node_id,
        task_id=task_id,
        status=Status.PENDING,
        task_spec=task_spec,
        run_info=RuntimeInfo(),
        node_run_graph=None,  # type: ignore[arg-type]
    )


def _done_children(graph: TaskExecutionGraph, target: TaskNode) -> list[dict]:
    """取 target 的**已 DONE 子节点**列表 [{node_id, title, output}](结构子,经 DEPENDENCY 边)。

    供 planning prompt 的 ``snapshot.done_children``:让 skill 据已产出算 gap 产下一批,
    而非在只看到 target 自身 + 空 gaps 时误判"gap 已闭"提前结束。零 case 知识(不写节点名)。
    """
    child_ids = [
        r.dst_id for r in graph.relations
        if r.src_id == target.node_id and r.type == RelationType.DEPENDENCY
    ]
    out: list[dict] = []
    for n in graph.tasks:
        if n.node_id in child_ids and n.status == Status.SUCCESS:
            out.append({
                "node_id": n.node_id,
                "title": n.task_spec.metadata.title,
                "output": (n.run_info.output if n.run_info else None),
            })
    return out


def _compose_planning_prompt(graph: TaskExecutionGraph, target: TaskNode) -> str:
    """组 planning prompt:{goal, context, target_node, graph_snapshot, gaps} + 约定返回格式 + 示例。

    约定返回数据格式 = JSON 对象 ``{"tasks": List[TaskSpec], "has_gap": bool, "gap_detail": str}``;
    tasks 为子任务数组(gap 未闭);gap 已闭(验收通过)→ ``{"tasks": [], "has_gap": false, "gap_detail": "done"}``;
    有 gap 但拆不出子(无规划能力)→ ``{"tasks": [], "has_gap": true, "gap_detail": "<原因>"}``。零 case 知识。
    """
    import json as _json
    goal = target.task_spec.goal
    ctx = target.task_spec.context
    acc_result = target.run_info.acceptance_result
    gaps = acc_result.gaps if acc_result else []
    snapshot = {
        "node_id": target.node_id,
        "status": str(target.status),
        "goal": goal.objective,
        "instruction": target.task_spec.metadata.instruction,
        "background": ctx.background if ctx else None,
        "acceptances": [
            {"id": a.id, "description": a.description} for a in goal.acceptances
        ],
        "done_children": _done_children(graph, target),
        "gaps": gaps,
        "loop_round": graph.loop_round,
    }

    return_fmt = (
        '## 返回数据格式约定\n'
        '硬约束：单次规划最多返回 3 个子任务，``tasks`` 数组长度不得超过 3；如果剩余事项超过 3 个，按依赖关系和优先级选择当前最重要的 3 个，未选事项留给后续规划，禁止返回第 4 个及之后的子任务。\n'
        '返回 JSON 字符串,结构为对象 ``{"tasks": List[TaskSpec], "has_gap": bool, "gap_detail": str, "acceptance_verdicts": List[{"ac_id": str, "passed": bool, "reason": str}]}``:\n'
        '```json\n'
        '{"tasks": [{"metadata": {"task_id": "<子节点node_id>", "title": "<标题>", "instruction": "<指令>"},\n'
        '              "context": {"background": "<背景>", "extend_props": {}},\n'
        '              "goal": {"objective": "<目标>", "acceptances": [{"id": "<ac_id>", "description": "<描述>"}]}}],\n'
        ' "has_gap": true,\n'
        ' "gap_detail": ""}\n'
        '```\n'
        '- ``tasks`` = 下一批可执行子任务;gap 已闭(验收通过)→ ``{"tasks": [], "has_gap": false, "gap_detail": "done"}``;\n'
        '- 有 gap 但无规划能力拆不出子 → ``{"tasks": [], "has_gap": true, "gap_detail": "<原因>"}``;\n'
        '- ``has_gap`` = 目标 - 已完成产出 是否仍有差距;``done_children`` 已列出已 DONE 子节点及产出,据此产**尚未完成**的下一批(不重复产已 DONE 的)。\n'
        '- ``acceptance_verdicts`` = 逐条对照目标节点自身 acceptances(id 取自 acceptances.id)给结论:``passed``=true/false、``reason``=判定依据;gap 闭(验收通过)全为 true 且 ``has_gap`` 应为 false。\n\n'
        '### 示例(gap 未闭,产 1 个子任务)\n'
        '```json\n'
        '{"tasks": [{"metadata": {"task_id": "N_market", "title": "市场规模分析", "instruction": "分析存储行业过去5年市场规模与增速"},\n'
        '              "context": {"background": "存储行业尽调·市场维度", "extend_props": {}},\n'
        '              "goal": {"objective": "产出市场规模模型与周期判断", "acceptances": [{"id": "ac_scale", "description": "提供过去5年市场规模/增速/出货量/价格变化"}]}}],\n'
        ' "has_gap": true, "gap_detail": "", "acceptance_verdicts": [{"ac_id": "ac_scale", "passed": false, "reason": "市场规模未产出"}]}\n'
        '```\n'
        '### 示例(gap 已闭,验收通过)\n'
        '```json\n'
        '{"tasks": [], "has_gap": false, "gap_detail": "done", "acceptance_verdicts": [{"ac_id": "<acceptance的id>", "passed": true, "reason": "已由已 DONE 子节点交付达成"}]}\n'
        '```'
    )
    return (f"[task-planning] 请基于以下任务状态计算 gap,产下一步可执行子任务;gap 已闭返回 has_gap=false。单次最多产出 3 个子任务，禁止超过 3 个。\n"
            f"目标节点 node_id={target.node_id}\n"
            f"已完成的子节点及其产出见快照 done_children;gap = 目标 - 已完成产出,据此产**尚未完成**的下一批子任务。\n"
            f"任务态快照\n{_json.dumps(snapshot, ensure_ascii=False)}\n\n{return_fmt}\n\n{NO_WEB_SEARCH_CONSTRAINT}")


def _parse_plan_result(run: dict, target: TaskNode, graph: TaskExecutionGraph) -> PlanResult:
    """解析 owner bot round-trip 结果 run{status,result,error} → PlanResult。

    约定 result.content 为 JSON(裸或被散文/```json 包裹,经 ``extract_json`` 提取),结构:
    ``{"tasks": List[TaskSpec], "has_gap": bool, "gap_detail": str}``。
    向后兼容:裸 ``List[TaskSpec]`` 数组 → tasks=数组, has_gap=len>0。
    异常/非终态/解析失败 → PlanResult([], has_gap=False, "plan_parse_fail")(编排核据 gap 闭语义处理)。
    node_id 取自 metadata.task_id;与已存 nodes 去重;task_id(根任务)取自 target.task_id。
    """
    status = str(run.get("status") or "").upper()
    if status != "COMPLETED":
        return PlanResult(children=[], has_gap=True, gap_detail="plan_not_completed")
    content = (run.get("result") or {}).get("content") if isinstance(run.get("result"), dict) else run.get("result")
    if not content:
        return PlanResult(children=[], has_gap=True, gap_detail="plan_empty_content")
    try:
        data = extract_json(content)  # 鲁棒解析:裸 JSON / ```json 代码块 / 散文包裹
    except (ValueError, TypeError):
        return PlanResult(children=[], has_gap=True, gap_detail="plan_parse_fail")
    # 归一:对象 {tasks,has_gap,gap_detail} 或裸 list
    tasks_data: list = []
    has_gap = False
    gap_detail = ""
    acceptance_verdicts: list[dict] = []
    if isinstance(data, list):
        tasks_data = data
        has_gap = len(data) > 0
    elif isinstance(data, dict):
        tasks_data = data.get("tasks") or []
        has_gap = bool(data.get("has_gap", False))
        gap_detail = str(data.get("gap_detail") or "")
        if not isinstance(tasks_data, list):
            tasks_data = []
        _av = data.get("acceptance_verdicts") or []
        acceptance_verdicts = [v for v in _av if isinstance(v, dict)] if isinstance(_av, list) else []
    else:
        return PlanResult(children=[], has_gap=True, gap_detail="plan_shape_unexpected")
    existing = {n.node_id for n in graph.tasks}
    children: list[TaskNode] = []
    for ch in tasks_data:
        spec = _build_child_task_spec(ch, target)
        if spec is None:
            continue
        nid = spec.metadata.task_id
        if not nid or nid in existing or any(c.node_id == nid for c in children):
            continue
        children.append(TaskNode(
            node_id=nid, task_id=target.task_id, status=Status.PENDING,
            task_spec=spec, run_info=RuntimeInfo(),
            node_run_graph=None,  # type: ignore[arg-type]  store 回填
        ))
    return PlanResult(children=children, has_gap=has_gap, gap_detail=gap_detail, acceptance_verdicts=acceptance_verdicts)


def _build_child_task_spec(data: dict, parent: TaskNode) -> TaskSpec | None:
    """从返回的 dict 构造子 TaskSpec(对齐领域模型)。缺失字段宽松继承 parent,保证下游可执行/验收。

    输入约定 ``List[TaskSpec]`` 元素:{"metadata":{task_id,title,instruction},"context":{background,...},"goal":{objective,acceptances}}。
    metadata.task_id(=node_id)、instruction 为必需;title/context/background/goal/acceptances 缺失则继承 parent。
    """
    if not isinstance(data, dict):
        return None
    md = data.get("metadata") or {}
    nid = md.get("task_id")
    if not nid:
        return None
    parent_meta = parent.task_spec.metadata
    instr = md.get("instruction") or parent_meta.instruction
    title = md.get("title") or parent_meta.title
    ctx_d = data.get("context") or {}
    ctx = Context(
        background=ctx_d.get("background") or parent.task_spec.context.background,
        extend_props=dict(ctx_d.get("extend_props") or {}),
    )
    goal_d = data.get("goal") or {}
    accs_in = goal_d.get("acceptances") or parent.task_spec.goal.acceptances
    accs = [
        AcceptanceCriteria(id=a.get("id", f"ac_{i}"), description=a.get("description", ""))
        if isinstance(a, dict) else a
        for i, a in enumerate(accs_in)
    ]
    goal = Goal(
        objective=goal_d.get("objective") or parent.task_spec.goal.objective,
        acceptances=accs,
    )
    return TaskSpec(metadata=Metadata(task_id=nid, title=title, instruction=instr), context=ctx, goal=goal)
