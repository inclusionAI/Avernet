"""TaskPlanner 内置规划优化策略库(引擎自带,不开放自定义)。

对齐 plan.md §3.4(first-match-wins by priority)。策略经 ``execution_config`` 动态匹配,
类 SQL optimizer rule-based 选择:config 有 ``workflow`` → WorkflowPlanningStrategy;否则兜底 GapBased。
GapBased 真实实现:组 planning prompt → 投 owner bot(``send_and_wait_async`` 同步收)→ 解析 children;
端口(OpenApiBotPort)由 DI 注入。无端口时(纯内核单测)退化为 stub 返 []。
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
    RelationType,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskNode,
    TaskSpec,
)

logger = logging.getLogger("task.planner")


class PlanningStrategy(Protocol):
    """规划优化策略契约(引擎内置,first-match-wins)。"""

    rule_id: str
    priority: int

    async def matches(self, graph: TaskExecutionGraph) -> bool:
        """纯读:据图级 execution_config 判本策略是否适用(workflow/yaml 信号)。协程化:corp LLM 判定可耗 IO。"""
        ...

    async def apply(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        """自发现可规划目标(FAIL 叶 / PLANNING 父 / 根 PENDING)+ 产"下一步可执行子节点"挂其下。
        返回 [] 表无可规划或 gap 已闭。协程化:corp 真实 LLM 拆解是耗时 IO,await 不阻塞。"""
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

    async def apply(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        cfg = graph.extend_props.get("execution_config", {}) or {}
        wf = cfg.get("workflow")
        if not wf:
            return []
        target = _find_planning_target(graph)
        if target is None:
            return []
        task_spec = target.task_spec  # workflow 子节点复用目标 task_spec(stub)
        if isinstance(wf, list):
            return [_wf_node(nid, target.task_id, task_spec) for nid in wf]
        if isinstance(wf, dict):
            kids = wf.get(target.node_id, [])
            return [_wf_node(nid, target.task_id, task_spec) for nid in kids]
        return []


class GapBasedPlanningStrategy:
    """默认兜底:基于 gap 的任务规划。组 planning prompt 投 owner bot → 同步收 children→造 TaskNode[]。
    端口(bot: OpenApiBotPort)由 DI 注入;省略端口= stub 路径(纯内核单测)返 []。

    gap 计算与验收同构:apply 返 children=gap 未闭(继续拆/+派发);返 []=gap 闭=验收通过(节点 DONE 上行)。
    owner bot = ``graph.extend_props["source_channel_id"]``(框架派生取,零 case 知识)。
    """

    rule_id = "gap_based"
    priority = 99

    def __init__(self, bot=None) -> None:
        """bot: OpenApiBotPort(真实 round-trip);None=stub 路径(返 [])。"""
        self._bot = bot

    async def matches(self, graph: TaskExecutionGraph) -> bool:
        return True  # 兜底

    async def apply(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        if self._bot is None:
            return []  # stub 路径(无端口)
        target = _find_planning_target(graph)
        if target is None:
            return []
        owner = str(graph.extend_props.get("source_channel_id") or "")
        if not owner:
            return []
        prompt = _compose_planning_prompt(graph, target)
        run = await self._bot.send_and_wait_async(
            bot_id=owner, message=prompt, metadata={"phase": "planning"},
        )
        children = _parse_children(run, target, graph)
        logger.info("[plan] owner=%s target=%s → %d children: %s",
                    owner, target.node_id, len(children), [c.node_id for c in children])
        return children


def _find_planning_target(graph: TaskExecutionGraph) -> TaskNode | None:
    """读图自发现可规划目标(根 PENDING / PLANNING 父 / FAILED+gaps 叶)。零 case 知识。"""

    def _has_child(g: TaskExecutionGraph, node_id: str) -> bool:
        return any(r.src_id == node_id and r.type == RelationType.DEPENDENCY for r in g.relations)

    def _parent_id(g: TaskExecutionGraph, node_id: str) -> str | None:
        for r in g.relations:
            if r.dst_id == node_id and r.type == RelationType.DEPENDENCY:
                return r.src_id
        return None

    for n in graph.tasks:
        if n.status == Status.PLANNING:
            return n
        if (
            n.status == Status.FAILED
            and n.run_info.acceptance_result is not None
            and bool(n.run_info.acceptance_result.gaps)
            and not _has_child(graph, n.node_id)
        ):
            return n
        if n.status == Status.PENDING and not _has_child(graph, n.node_id) and _parent_id(graph, n.node_id) is None:
            return n
    return None


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
        if n.node_id in child_ids and n.status == Status.DONE:
            out.append({
                "node_id": n.node_id,
                "title": n.task_spec.metadata.title,
                "output": (n.run_info.output if n.run_info else None),
            })
    return out


def _compose_planning_prompt(graph: TaskExecutionGraph, target: TaskNode) -> str:
    """组 planning prompt:{goal, context, target_node, graph_snapshot, gaps} + 约定返回格式 + 示例。

    约定返回数据格式 = ``List[TaskSpec]`` 的 JSON(对齐领域模型;每子任务自带 metadata/context/goal);
    metadata.task_id = 子节点 node_id;gap 已闭(验收通过)返回空数组 ``[]``。零 case 知识(不写节点名)。
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

    # 约定返回格式说明 + 示例(下游 skill 据此产出可解析的结构化 JSON)
    return_fmt = (
        '## 返回数据格式约定\n'
        '返回 JSON 字符串,内容为 **List[TaskSpec]** 的数组(对齐领域模型),每个元素结构:\n'
        '```json\n'
        '{"metadata": {"task_id": "<子节点node_id>", "title": "<子任务标题>", "instruction": "<子任务执行指令>"},\n'
        ' "context": {"background": "<子任务背景>", "extend_props": {}},\n'
        ' "goal": {"objective": "<子任务目标>", "acceptances": [{"id": "<ac_id>", "description": "<验收标准描述>"}]}}\n'
        '```\n'
        '- ``metadata.task_id`` 即为子节点的 node_id(须唯一,不可与已存节点重复);\n'
        '- gap 已闭(验收通过)→ 返回空数组 ``[]``;\n'
        '- 子任务的 ``goal.acceptances`` 为该子任务自身的验收标准(由本步 gap 计算细化);若无独立验收标准,可继承父任务 goal。\n\n'
        '### 示例数据(gap 未闭,产 1 个子任务)\n'
        '```json\n'
        '[{"metadata": {"task_id": "N_market", "title": "市场规模分析", "instruction": "分析存储行业过去5年市场规模与增速"},\n'
        '  "context": {"background": "存储行业尽调·市场维度", "extend_props": {}},\n'
        '  "goal": {"objective": "产出市场规模模型与周期判断", "acceptances": [{"id": "ac_scale", "description": "提供过去5年市场规模/增速/出货量/价格变化"}]}}]\n'
        '```\n'
        '### 示例数据(gap 已闭,验收通过)\n'
        '```json\n'
        '[]\n'
        '```'
    )
    return (f"[planning] 请基于以下任务状态计算 gap,产下一步可执行子任务 List[TaskSpec];gap 已闭返回 []。\n"
            f"目标节点 node_id={target.node_id}\n"
            f"已完成的子节点及其产出见快照 done_children;gap = 目标 - 已完成产出,据此产**尚未完成**的下一批子任务。\n"
            f"任务态快照\n{_json.dumps(snapshot, ensure_ascii=False)}\n\n{return_fmt}")


def _parse_children(run: dict, target: TaskNode, graph: TaskExecutionGraph) -> list[TaskNode]:
    """解析 owner bot round-trip 结果 run{status,result,error} → TaskNode[](PENDING,空 RuntimeInfo)。

    约定 result.content 为 JSON(裸或被散文/```json 代码块包裹均支持,经 ``extract_json`` 提取)表示的 ``List[TaskSpec]`` 数组:
    每元素 {"metadata":{task_id=node_id,title,instruction}, "context":{background,...}, "goal":{objective,acceptances:[...]}}
    空数组 ``[]`` 表 gap 已闭(验收通过)。异常/非终态/解析失败 → 返 [](由编排核据 gap 闭语义处理)。
    node_id 取自 metadata.task_id;与已存 nodes 去重;task_id(根任务)取自 target.task_id。
    """
    status = str(run.get("status") or "").upper()
    if status != "COMPLETED":
        return []
    content = (run.get("result") or {}).get("content") if isinstance(run.get("result"), dict) else run.get("result")
    if not content:
        return []
    try:
        data = extract_json(content)  # 鲁棒解析:裸 JSON / ```json 代码块 / 散文包裹
    except (ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []  # 非数组(强制 List[TaskSpec])
    out: list[TaskNode] = []
    existing = {n.node_id for n in graph.tasks}
    for ch in data:
        spec = _build_child_task_spec(ch, target)
        if spec is None:
            continue
        nid = spec.metadata.task_id
        if not nid or nid in existing:
            continue
        out.append(TaskNode(
            node_id=nid, task_id=target.task_id, status=Status.PENDING,
            task_spec=spec, run_info=RuntimeInfo(),
            node_run_graph=None,  # type: ignore[arg-type]  store 回填
        ))
    return out


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
