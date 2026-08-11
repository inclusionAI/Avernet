"""P1 — TaskGraphStore,任务执行图的内部 SSOT 写网关.

设计 v4: ``design-review.md`` §2.1、tasks.md P1。

本类是图谱原子变更的**唯一**入口 (``TaskHarness`` 旁路也走同一写口)。领域对象
(``TaskNode``/``TaskExecutionGraph``) 是 frozen 值对象;变更一律经
``dataclasses.replace`` 生成新快照后回写到内部容器。

v4 变更 (相对 v3):
- ``Status`` 无 ``SPAWNING``。``add_task_graph`` 不再改父状态;"委托中" = 有分解子
  (``spawned_children`` 非空),是结构派生而非状态。
- ``_deps_satisfied`` 精确化 (model B): 父 ``DONE`` → 满足;父非 ``DONE`` 但我是其
  分解子 (``spawned_children`` 内) → 满足 (委托中,不阻塞);否则未满足。
- ``query_nodes(ready)`` 新增排除有分解子的节点 (委托中不直接派发)。
- ``cascade_rollback`` 仅人工 ``rollback_to_node`` 触发;自动 reroute 不调用
  (model B: 下游在 deps 满足前未入图,无下游可复位)。

派生量 (非持久、核内计算):
- ``dependencies_satisfied``: 节点所有 ``depends_on`` 的 deps 已满足 (见上规则);
  空父 (根) 视为就绪。
- ``depth``: 由 ``depends_on`` 的最长路径派生,``node_depth`` 暴露给引擎深度闸门。

边 (依赖) 隐式存在于 ``depends_on``;不引入 Edge 模型 (design Q1)。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Any

from agentclaw.community.core.task_goal_driven.models import (
    NodeOpResult,
    NodeQueryCriteria,
    NodeRuntimePatch,
    RuntimeInfo,
    Scope,
    Status,
    TaskExecutionGraph,
    TaskInfo,
    TaskNode,
)


# ============================================================================
# 异常
# ============================================================================


class TaskGraphStoreError(Exception):
    """TaskGraphStore 基础异常."""


class GraphNotFoundError(TaskGraphStoreError):
    """task_id 对应的图不存在."""


class NodeNotFoundError(TaskGraphStoreError):
    """node_id 对应的节点不存在."""

    def __init__(self, task_id: str, node_id: str) -> None:
        super().__init__(f"node not found: task={task_id} node={node_id}")
        self.task_id = task_id
        self.node_id = node_id


class InvalidStateTransition(TaskGraphStoreError):
    """非法状态流转."""


class InvalidScopeTag(TaskGraphStoreError):
    """AcceptanceCriteria.tag 不是合法 Scope 值 (在 compute_output_projection 校验)."""


# ============================================================================
# 状态流转表 (v4: 5 态,无 SPAWNING)
# patch_node_runtime_info 网关强制;cascade_rollback 的强制复位走内部口,不经此表)
# ============================================================================


_ALLOWED_TRANSITIONS: dict[Status, frozenset[Status]] = {
    Status.PENDING: frozenset({Status.RUNNING, Status.HUNG, Status.DONE}),
    # RUNNING=DONE(pass)/FAILED(fail+gaps)/HUNG(stuck/timeout)
    Status.RUNNING: frozenset({Status.DONE, Status.FAILED, Status.HUNG}),
    # FAILED=DONE(补救子全PASS传播)/HUNG(深度闸门)/PENDING(人工rollback)
    Status.FAILED: frozenset({Status.DONE, Status.HUNG, Status.PENDING}),
    # HUNG=RUNNING(升BBS)/DONE(BBS PASS传播)/PENDING(人工rollback)/FAILED
    Status.HUNG: frozenset({Status.RUNNING, Status.DONE, Status.PENDING, Status.FAILED}),
    # DONE=PENDING/FAILED/HUNG (人工 rollback/cascade)
    Status.DONE: frozenset({Status.PENDING, Status.FAILED, Status.HUNG}),
}

_ROOT_NODE_ID = "n_root"


# ============================================================================
# 内部可变容器 (领域对象冻结;容器持有最新快照)
# ============================================================================


@dataclass
class _GraphState:
    """图谱内部可变状态 (单图)."""

    task_id: str
    status: Status
    loop_round: int
    output: dict[str, Any]
    extend_props: dict[str, Any]
    root_id: str
    nodes: dict[str, TaskNode] = field(default_factory=dict)
    seq: int = 0
    # 节点追加顺序 (deterministic query 顺序)
    order: list[str] = field(default_factory=list)
    # 分解子关系:parent_node_id -> [spawned child node_ids] (add_task_graph 记录).
    # 区别于 depends_on 的"统一依赖边":分解子是 parent 主动 spawn 出的子任务,
    # 数据消费方 (依赖多个父) 不在此列。propagate_done 只看分解子。
    # v4: "委托中" = spawned_children[node] 非空 (结构派生,非状态)。
    spawned_children: dict[str, list[str]] = field(default_factory=dict)

    def _bump_seq(self) -> int:
        self.seq += 1
        return self.seq


def _now() -> float:
    return time.time()


def _merge(dst: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """浅合并 (patch 覆盖 dst),返回新 dict (不就地改)."""
    merged = dict(dst)
    merged.update(patch)
    return merged


# ============================================================================
# TaskGraphStore
# ============================================================================


class TaskGraphStore:
    """任务执行图 SSOT 写网关 (in-memory;生产存储选型 TBD,见 tasks.md P1.10)."""

    def __init__(self) -> None:
        self._graphs: dict[str, _GraphState] = {}

    # ------------------------------------------------------------------
    # 建图
    # ------------------------------------------------------------------

    def initialize_graph(self, task_info: TaskInfo) -> None:
        """建唯一 graph:全局 -> RUNNING,播种根 ``n_root`` (锁静态说明书)."""
        task_id = task_info.task_spec.metadata.id
        if task_id in self._graphs:
            raise TaskGraphStoreError(f"graph already exists: task={task_id}")
        root = TaskNode(
            node_id=_ROOT_NODE_ID,
            depends_on=[],
            task_spec=task_info.task_spec,
            status=Status.PENDING,
            run_info=RuntimeInfo(),
        )
        state = _GraphState(
            task_id=task_id,
            status=Status.RUNNING,
            loop_round=0,
            output={},
            extend_props={"execution_config": dict(task_info.execution_config)},
            root_id=_ROOT_NODE_ID,
            nodes={_ROOT_NODE_ID: root},
            order=[_ROOT_NODE_ID],
        )
        state._bump_seq()
        self._graphs[task_id] = state

    # ------------------------------------------------------------------
    # 子图
    # ------------------------------------------------------------------

    def add_task_graph(self, task_id: str, parent_node_id: str, nodes: list[TaskNode]) -> None:
        """挂子图:记录分解子关系;**不改父状态** (v4: 无 SPAWNING)。

        子节点的真实依赖边 (结构父 + 数据依赖,统一在 ``depends_on``) 由调用方
        ``TaskPlanner`` 设好;本方法不篡改 depends_on。

        v4: 父保持当前状态 (PENDING/FAILED 等),"委托中" = 有分解子 (结构派生)。
        """
        state = self._require_graph(task_id)
        if parent_node_id not in state.nodes:
            raise NodeNotFoundError(task_id, parent_node_id)
        # v4: 不改父状态 (无 SPAWNING);只记录分解子关系
        state.spawned_children.setdefault(parent_node_id, []).extend(n.node_id for n in nodes)
        for node in nodes:
            if node.node_id in state.nodes:
                raise TaskGraphStoreError(f"node already exists: {node.node_id}")
            state.nodes[node.node_id] = node
            state.order.append(node.node_id)
        state._bump_seq()

    # ------------------------------------------------------------------
    # 节点运行信息补丁 (原子状态流转网关)
    # ------------------------------------------------------------------

    def patch_node_runtime_info(
        self, task_id: str, node_id: str, patch: NodeRuntimePatch
    ) -> NodeOpResult:
        """原子状态流转网关:RUNNING 写 start_time,终态写 end_time;output/extend_props 增量 MERGE."""
        state = self._require_graph(task_id)
        node = self._require_node(state, node_id)
        new_status = patch.status if patch.status is not None else node.status
        self._validate_transition(node.status, new_status)
        new_run = node.run_info
        if patch.run_mode is not None:
            new_run = replace(new_run, run_mode=patch.run_mode)
        if patch.assignee is not None:
            new_run = replace(new_run, assignee=patch.assignee)
        if patch.collab_mode is not None:
            new_run = replace(new_run, collab_mode=patch.collab_mode)
        if patch.output_patch is not None:
            new_run = replace(new_run, output=_merge(new_run.output, patch.output_patch))
        if patch.acceptance_result is not None:
            new_run = replace(new_run, acceptance_result=patch.acceptance_result)
        if patch.extend_props_patch is not None:
            new_run = replace(new_run, extend_props=_merge(new_run.extend_props, patch.extend_props_patch))
        # 时间戳
        now = _now()
        if new_status == Status.RUNNING and new_run.start_time is None:
            new_run = replace(new_run, start_time=now)
        if new_status in {Status.DONE, Status.FAILED, Status.HUNG} and new_run.end_time is None:
            new_run = replace(new_run, end_time=now)
        finalized = replace(node, status=new_status, run_info=new_run)
        state.nodes[node_id] = finalized
        # DONE 传播到全图 output (task-scope 投影据此聚合)
        if new_status == Status.DONE and patch.status == Status.DONE:
            state.output = _merge(state.output, new_run.output)
        state._bump_seq()
        return NodeOpResult(
            task_id=task_id,
            node_id=node_id,
            node_status=new_status,
            runtime_task_id=new_run.extend_props.get("runtime_task_id"),
        )

    # ------------------------------------------------------------------
    # 全图状态
    # ------------------------------------------------------------------

    def patch_graph_status(self, task_id: str, status: Status) -> None:
        """全图 DONE / 全局 FAILED 终结."""
        state = self._require_graph(task_id)
        if status == Status.DONE:
            not_done = [n.node_id for n in state.nodes.values() if n.status != Status.DONE]
            if not_done:
                raise TaskGraphStoreError(
                    f"cannot mark graph DONE with non-DONE nodes: {not_done}"
                )
        state.status = status
        state._bump_seq()

    def bump_loop_round(self, task_id: str) -> int:
        """reroute 轮次自增 (reroute 触发时调用)."""
        state = self._require_graph(task_id)
        state.loop_round += 1
        state._bump_seq()
        return state.loop_round

    # ------------------------------------------------------------------
    # 级联回滚 (v4: 仅人工 rollback_to_node 触发;自动 reroute 不调用)
    # ------------------------------------------------------------------

    def cascade_rollback(self, task_id: str, rollback_node_id: str) -> TaskExecutionGraph:
        """下行 BFS 复位受污染下游 + 隔离无关 DONE.

        v4: **仅人工** ``rollback_to_node`` 触发。model B 下自动 reroute 不调用此方法
        (下游在 deps 满足前未入图,无下游可复位)。

        复位 ``rollback_node_id`` 及其所有后代:status -> PENDING,运行面整体重置
        (output/acceptance/start/end/run_mode/assignee/collab_mode 清空),保留节点与
        结构。不在该子树内的节点 (如已 PASS 的 ``N_market``) 状态不变 (隔离)。
        """
        state = self._require_graph(task_id)
        if rollback_node_id not in state.nodes:
            raise NodeNotFoundError(task_id, rollback_node_id)
        # BFS 收集后代 (经 depends_on 逆向邻接)
        children_of: dict[str, list[str]] = {}
        for nid, n in state.nodes.items():
            for pid in n.depends_on:
                children_of.setdefault(pid, []).append(nid)
        # 复位集合 = 回滚根自身 + 其所有后代
        to_reset: list[str] = [rollback_node_id]
        frontier = list(children_of.get(rollback_node_id, []))
        seen: set[str] = {rollback_node_id}
        while frontier:
            nid = frontier.pop()
            if nid in seen:
                continue
            seen.add(nid)
            to_reset.append(nid)
            frontier.extend(children_of.get(nid, []))
        # 复位 (强制 PENDING,绕过流转表 — 这是显式回滚)
        for nid in to_reset:
            node = state.nodes[nid]
            state.nodes[nid] = replace(node, status=Status.PENDING, run_info=RuntimeInfo())
        # 全图 output 重建 (剔除被复位节点贡献) — 重新聚合所有 DONE 节点
        merged: dict[str, Any] = {}
        for nid in state.order:
            n = state.nodes[nid]
            if n.status == Status.DONE and n.run_info.output:
                merged.update(n.run_info.output)
        state.output = merged
        state._bump_seq()
        return self.get_graph(task_id)

    # ------------------------------------------------------------------
    # 查询 / 派生
    # ------------------------------------------------------------------

    def query_nodes(self, task_id: str, criteria: NodeQueryCriteria) -> list[TaskNode]:
        """按 criteria 过滤;``dependencies_satisfied`` 核内 DAG 就绪拓扑计算.

        v4: 当 ``dependencies_satisfied=True`` 时,排除有分解子的节点 (委托中,不直接派发)。
        """
        state = self._require_graph(task_id)
        result: list[TaskNode] = []
        for nid in state.order:
            node = state.nodes[nid]
            if criteria.status is not None and node.status != criteria.status:
                continue
            if criteria.parent_node_id is not None and criteria.parent_node_id not in node.depends_on:
                continue
            if criteria.dependencies_satisfied:
                if not self._deps_satisfied(state, node):
                    continue
                # v4: 有分解子 → 委托中,不直接派发
                if state.spawned_children.get(nid):
                    continue
            result.append(node)
        return result

    def node_depth(self, task_id: str, node_id: str) -> int:
        """由 ``depends_on`` 核内派生递归深度 (根=0)."""
        state = self._require_graph(task_id)
        self._require_node(state, node_id)
        memo: dict[str, int] = {}

        def _depth(nid: str, stack: set[str]) -> int:
            if nid in memo:
                return memo[nid]
            if nid in stack:
                # 环保护:视作 0 (DAG 不应出现,防御)
                return 0
            node = state.nodes[nid]
            if not node.depends_on:
                memo[nid] = 0
                return 0
            stack.add(nid)
            deepest = max(_depth(p, stack) for p in node.depends_on)
            stack.discard(nid)
            memo[nid] = deepest + 1
            return memo[nid]

        return _depth(node_id, set())

    def compute_output_projection(self, task_id: str, node_id: str) -> dict[str, Any]:
        """按节点各 AC ``tag`` 的 scope 聚合相关 DONE output.

        返回 ``{"by_scope": {node|subtree|task: {...}}, "inputs": {...并集...}}``。
        非法 scope tag 抛 ``InvalidScopeTag``。
        """
        state = self._require_graph(task_id)
        node = self._require_node(state, node_id)
        # 校验 scope 合法
        scopes_used: set[str] = set()
        for ac in node.task_spec.goal.acceptances:
            try:
                scope = Scope(ac.tag)
            except ValueError as exc:
                raise InvalidScopeTag(
                    f"AcceptanceCriteria {ac.id!r} has invalid scope tag {ac.tag!r}; "
                    f"expected one of {[s.value for s in Scope]}"
                ) from exc
            scopes_used.add(scope.value)

        children_of: dict[str, list[str]] = {}
        for nid, n in state.nodes.items():
            for pid in n.depends_on:
                children_of.setdefault(pid, []).append(nid)

        def _done_output(nid: str) -> dict[str, Any]:
            n = state.nodes[nid]
            return dict(n.run_info.output) if n.status == Status.DONE and n.run_info.output else {}

        by_scope: dict[str, dict[str, Any]] = {}

        # NODE -> 直系父 (DONE) 产出并集
        node_inputs: dict[str, Any] = {}
        for pid in node.depends_on:
            node_inputs.update(_done_output(pid))
        by_scope[Scope.NODE.value] = node_inputs

        # SUBTREE -> 本节点后代 (DONE) 产出并集
        subtree_inputs: dict[str, Any] = {}
        frontier = list(children_of.get(node_id, []))
        seen: set[str] = set()
        while frontier:
            d = frontier.pop()
            if d in seen:
                continue
            seen.add(d)
            subtree_inputs.update(_done_output(d))
            frontier.extend(children_of.get(d, []))
        by_scope[Scope.SUBTREE.value] = subtree_inputs

        # TASK -> 全图 DONE 产出并集 (= graph.output)
        task_inputs: dict[str, Any] = {}
        for nid in state.order:
            task_inputs.update(_done_output(nid))
        by_scope[Scope.TASK.value] = task_inputs

        # inputs = 节点实际用到的 scope 的输出并集
        inputs: dict[str, Any] = {}
        for sc in scopes_used:
            inputs.update(by_scope.get(sc, {}))
        return {"by_scope": by_scope, "inputs": inputs}

    # ------------------------------------------------------------------
    # 只读看板
    # ------------------------------------------------------------------

    def export_dashboard_view(self, task_id: str) -> TaskExecutionGraph:
        """只读看板投影 (当前等价于 get_graph;预留剥离内部边的位置)."""
        return self.get_graph(task_id)

    def get_graph(self, task_id: str) -> TaskExecutionGraph:
        """核内只读快照."""
        state = self._require_graph(task_id)
        return TaskExecutionGraph(
            status=state.status,
            loop_round=state.loop_round,
            output=dict(state.output),
            tasks=[state.nodes[nid] for nid in state.order],
            extend_props=dict(state.extend_props),
        )

    def get_node(self, task_id: str, node_id: str) -> TaskNode:
        """读取单节点快照."""
        state = self._require_graph(task_id)
        return self._require_node(state, node_id)

    def decomposition_children(self, task_id: str, node_id: str) -> list[TaskNode]:
        """分解子 (add_task_graph spawn 出的子任务),区别于 depends_on 数据依赖消费方."""
        state = self._require_graph(task_id)
        return [state.nodes[cid] for cid in state.spawned_children.get(node_id, []) if cid in state.nodes]

    def execution_config(self, task_id: str) -> dict[str, Any]:
        """读取 execution_config (引擎深度闸门读 MAX_DEPTH)."""
        state = self._require_graph(task_id)
        return dict(state.extend_props.get("execution_config", {}))

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _require_graph(self, task_id: str) -> _GraphState:
        state = self._graphs.get(task_id)
        if state is None:
            raise GraphNotFoundError(f"graph not found: task={task_id}")
        return state

    def _require_node(self, state: _GraphState, node_id: str) -> TaskNode:
        node = state.nodes.get(node_id)
        if node is None:
            raise NodeNotFoundError(state.task_id, node_id)
        return node

    def _validate_transition(self, old: Status, new: Status) -> None:
        if new == old:
            return
        allowed = _ALLOWED_TRANSITIONS.get(old, frozenset())
        if new not in allowed:
            raise InvalidStateTransition(
                f"illegal transition {old.value} -> {new.value} (allowed: {[s.value for s in allowed]})"
            )

    def _deps_satisfied(self, state: _GraphState, node: TaskNode) -> bool:
        """依赖就绪 (v4 精确化, model B).

        规则:
        - 空父 (根) → 满足。
        - 父 ``DONE`` → 满足 (数据依赖已产出)。
        - 父非 ``DONE`` 但我是其**分解子** (``spawned_children`` 内) → 满足
          (父在委托,委托给子执行,子不阻塞)。区分 "分解子(就绪)" vs "下游消费者(待父 DONE)"。
        - 否则未满足。
        """
        if not node.depends_on:
            return True
        for pid in node.depends_on:
            parent = state.nodes.get(pid)
            if parent is None:
                return False
            if parent.status == Status.DONE:
                continue  # 数据依赖已产出
            # 非DONE:检查是否是分解子关系 (委托中,不阻塞)
            if node.node_id in state.spawned_children.get(pid, []):
                continue  # 分解父在委托 → 子就绪
            return False  # 数据消费方,父未 DONE → 未就绪
        return True


# Scope 仅用于触发 import 校验 (保持显式引用,便于静态检查 scope 语义未被遗忘).
_SCOPE_REFS = (Scope.NODE, Scope.SUBTREE, Scope.TASK)
