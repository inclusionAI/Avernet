"""TaskGraphService:任务图谱 SSOT + 原子变更唯一网关(7+2 API)。

对齐 plan.md §3.1 + 任务图谱文档 lunk1txfuv6gtwk2。
in-memory store(M1);ORM 适配按需后续。查询返回引用(D3-A:调用方不应 mutate)。
"""
from __future__ import annotations

import threading
from typing import Any

from agentclaw.community.core.task.domain.errors import (
    GraphAlreadyInitializedError,
    GraphIntegrityError,
    NodeNotFoundError,
    TaskNotFoundError,
    TaskStateError,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceVerdict,
    NodeOpResult,
    Relation,
    RelationType,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskNodeQueryCriteria,
)

# 合法状态转换
# acceptance 驱动(skill 回投):RUNNING->DONE(PASS)/FAILED(FAIL+gaps)
_ACCEPTANCE_TRANSITIONS: dict[Status, set[Status]] = {
    Status.RUNNING: {Status.DONE, Status.FAILED},
    Status.PLANNING: {Status.DONE, Status.FAILED},  # 根终验 PASS/FAIL 从 PLANNING 委托态回投
}
# status 直驱(框架内部:派发/复位/传播)
#   PENDING->RUNNING(派发) / RUNNING->PENDING(Harness 复位) / PLANNING->DONE(传播)
_DIRECT_TRANSITIONS: dict[Status, set[Status]] = {
    Status.PENDING: {Status.RUNNING},
    Status.RUNNING: {Status.PENDING},
    Status.PLANNING: {Status.DONE},
}
# 可被委托(进 PLANNING)的父节点状态(add_task_nodes 用)
_DELEGATABLE_PARENT: set[Status] = {Status.PENDING, Status.FAILED, Status.PLANNING}

_DEFAULT_MAX_DEPTH = 3
_DEFAULT_BBS_MAX_DEPTH = 3


class TaskGraphService:
    """任务图谱 SSOT + 原子变更唯一网关。

    边界:只做图结构 + 节点/图级状态原子写 + 派生只读查询;不含编排(不调编排核、不搜推、不规划)。
    结构归属由 relations 分解树(单入)表达;depth/结构子/结构父均从 relations 派生。
    """

    def __init__(self) -> None:
        self._graphs: dict[str, TaskExecutionGraph] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._registry_lock = threading.RLock()
        self._run_id_counter = 0

    # ===== internal helpers =====
    def _lock_for(self, task_id: str) -> threading.RLock:
        with self._registry_lock:
            lk = self._locks.get(task_id)
            if lk is None:
                lk = threading.RLock()
                self._locks[task_id] = lk
            return lk

    def _next_run_id(self) -> int:
        with self._registry_lock:
            self._run_id_counter += 1
            return self._run_id_counter

    def _require_graph(self, task_id: str) -> TaskExecutionGraph:
        graph = self._graphs.get(task_id)
        if graph is None:
            raise TaskNotFoundError(f"task_id={task_id} 图不存在")
        return graph

    def _get_node(self, graph: TaskExecutionGraph, node_id: str) -> TaskNode | None:
        for n in graph.tasks:
            if n.node_id == node_id:
                return n
        return None

    def _require_node(self, graph: TaskExecutionGraph, node_id: str) -> TaskNode:
        node = self._get_node(graph, node_id)
        if node is None:
            raise NodeNotFoundError(f"node_id={node_id} 不存在于 task_id={graph.tasks[0].task_id if graph.tasks else '?'}")
        return node

    def _has_child(self, graph: TaskExecutionGraph, node_id: str) -> bool:
        return any(r.src_id == node_id and r.type == RelationType.DEPENDENCY for r in graph.relations)

    def _collect_subtree(self, graph: TaskExecutionGraph, node_id: str) -> set[str]:
        """收集 node_id 及其所有后代 node_id(BFS over relations 分解树)。"""
        subtree: set[str] = {node_id}
        frontier = [node_id]
        while frontier:
            cur = frontier.pop()
            for r in graph.relations:
                if r.src_id == cur and r.type == RelationType.DEPENDENCY and r.dst_id not in subtree:
                    subtree.add(r.dst_id)
                    frontier.append(r.dst_id)
        return subtree

    # ===== 4 核心写/读 =====
    def initialize_graph(self, task_info: TaskInfo) -> TaskExecutionGraph:
        """建图首帧(全局 RUNNING,只含根节点 PENDING);幂等:同 task_id 重复调抛冲突。"""
        task_id = task_info.task_spec.metadata.task_id
        with self._lock_for(task_id):
            if task_id in self._graphs:
                raise GraphAlreadyInitializedError(f"task_id={task_id} 图已存在")
            run_id = self._next_run_id()
            root = TaskNode(
                node_id=task_id,
                task_id=task_id,
                status=Status.PENDING,
                task_spec=task_info.task_spec,
                run_info=RuntimeInfo(),
                node_run_graph=None,  # type: ignore[arg-type]  回填见下
            )
            graph = TaskExecutionGraph(
                run_id=run_id,
                loop_round=0,
                status=Status.RUNNING,
                tasks=[root],
                relations=[],
            )
            root.node_run_graph = graph  # 回填循环引用(in-memory)
            graph.extend_props["execution_config"] = dict(task_info.execution_config)
            self._graphs[task_id] = graph
            return graph

    def add_task_nodes(self, tasks: list[TaskNode], parent_node_id: str) -> TaskExecutionGraph:
        """并子图(单写 relations 分解树)。触发条件 a/b/c 由编排核判后调,本方法双检:
        a. 只有一个根节点且 status=PENDING(初始规划);
        b. 存在 FAILED 节点且 acceptance_result.gaps 非空的叶子(补救);
        c. 存在 PLANNING 节点 且 无 RUNNING(下一层规划)。
        登记分解树:每新子挂 ``parent_node_id`` 下写入 DEPENDENCY 边(src=parent,dst=新子,单入);
        parent 进/维持 PLANNING(委托态)。单层同构护栏:本批 node_id 不重复、不与已存重复、本批内不互父子。
        """
        if not tasks:
            raise GraphIntegrityError("add_task_nodes: tasks 不能为空")
        task_id = tasks[0].task_id
        if any(t.task_id != task_id for t in tasks):
            raise GraphIntegrityError("add_task_nodes: 同批 task_id 不一致")
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            self._assert_add_trigger(graph)  # 双检 a/b/c
            parent = self._require_node(graph, parent_node_id)
            if parent.status not in _DELEGATABLE_PARENT:
                raise GraphIntegrityError(
                    f"add_task_nodes: parent={parent_node_id} 状态={parent.status} 不可委托"
                )
            # 单层同构护栏
            existing_ids = {n.node_id for n in graph.tasks}
            new_ids = [t.node_id for t in tasks]
            if len(set(new_ids)) != len(new_ids):
                raise GraphIntegrityError("add_task_nodes: 本批 node_id 重复")
            duplicated = existing_ids & set(new_ids)
            if duplicated:
                raise GraphIntegrityError(f"add_task_nodes: 节点已存在 {duplicated}")
            # 写 relations + 节点;回填 node_run_graph
            for t in tasks:
                graph.tasks.append(t)
                t.node_run_graph = graph
                graph.relations.append(
                    Relation(src_id=parent_node_id, dst_id=t.node_id, type=RelationType.DEPENDENCY)
                )
            # 父进 PLANNING(委托态;已 PLANNING 维持)
            if parent.status != Status.PLANNING:
                parent.status = Status.PLANNING
            return graph

    def _assert_add_trigger(self, graph: TaskExecutionGraph) -> None:
        cond_a = (
            len(graph.tasks) == 1
            and graph.tasks[0].status == Status.PENDING
            and not graph.relations
        )
        cond_b = any(
            n.status == Status.FAILED
            and n.run_info.acceptance_result is not None
            and bool(n.run_info.acceptance_result.gaps)
            and not self._has_child(graph, n.node_id)
            for n in graph.tasks
        )
        has_running = any(n.status == Status.RUNNING for n in graph.tasks)
        cond_c = any(n.status == Status.PLANNING for n in graph.tasks) and not has_running
        if not (cond_a or cond_b or cond_c):
            raise GraphIntegrityError("add_task_nodes: 触发条件 a/b/c 均不满足")

    def update_task_node_info(self, patch: TaskNodePatch) -> NodeOpResult:
        """节点级原子状态流转网关。双模式:
        ① acceptance_result 驱动(skill 回投):PASS→DONE / FAIL+gaps→FAILED(强制要求 gaps);
        ② status 直驱(框架内部):PENDING→RUNNING(派发) / RUNNING→PENDING(Harness 复位) /
           PLANNING→DONE(传播)。两者都校验状态机。无 acceptance_result 且无 status 只 fold 不翻态。
        派发写:patch.run_mode(str)/assignee 落库 + 置 RUNNING。"""
        with self._lock_for(patch.task_id):
            graph = self._require_graph(patch.task_id)
            node = self._require_node(graph, patch.node_id)
            prev_status = node.status
            new_status: Status | None = None
            if patch.acceptance_result is not None:
                # 模式① acceptance 驱动
                verdict = patch.acceptance_result.verdict
                if verdict == AcceptanceVerdict.PASS:
                    new_status = Status.DONE
                else:  # FAIL
                    if not patch.acceptance_result.gaps:
                        raise TaskStateError("FAIL 验收强制要求 gaps(验收 skill 契约)")
                    new_status = Status.FAILED
                allowed = _ACCEPTANCE_TRANSITIONS.get(node.status, set())
                if new_status not in allowed:
                    raise TaskStateError(
                        f"acceptance 翻态非法: {node.status}+{verdict} → {new_status}"
                    )
                node.run_info.acceptance_result = patch.acceptance_result
            elif patch.status is not None:
                # 模式② status 直驱
                new_status = patch.status
                allowed = _DIRECT_TRANSITIONS.get(node.status, set())
                if new_status not in allowed:
                    raise TaskStateError(
                        f"status 直驱非法: {node.status} → {new_status}"
                    )
            # fold 非状态字段
            if patch.output_patch is not None:
                node.run_info.output.update(patch.output_patch)
            if patch.run_mode is not None:
                node.run_info.run_mode = patch.run_mode
            if patch.assignee is not None:
                node.run_info.assignee = patch.assignee
            if patch.extend_props_patch is not None:
                node.run_info.extend_props.update(patch.extend_props_patch)
            # 应用翻态
            if new_status is not None:
                node.status = new_status
            return NodeOpResult(
                task_id=patch.task_id,
                node_id=patch.node_id,
                success=True,
                prev_status=prev_status,
                new_status=node.status,
            )

    def query_task_dashboard(self, task_id: str, node_id: str | None = None) -> TaskExecutionGraph:
        """只读看板快照。node_id=None 返回整图引用;指定 node_id 返回该节点子树投影(新构造对象)。"""
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            if node_id is None:
                return graph
            self._require_node(graph, node_id)  # 校验存在
            subtree = self._collect_subtree(graph, node_id)
            return TaskExecutionGraph(
                run_id=graph.run_id,
                loop_round=graph.loop_round,
                status=graph.status,
                output=dict(graph.output),
                tasks=[n for n in graph.tasks if n.node_id in subtree],
                relations=[
                    r
                    for r in graph.relations
                    if r.src_id in subtree and r.dst_id in subtree
                ],
                extend_props=dict(graph.extend_props),
            )

    # ===== 派生只读查询(均从 relations 分解树派生)=====
    def query_task_nodes(self, task_id: str, criteria: TaskNodeQueryCriteria) -> list[TaskNode]:
        """按条件查节点。criteria={status=PENDING}→ 返回 PENDING 可派发节点
        (PLANNING 委托态不在 PENDING,天然排除);has_child_tasks 可筛叶/内部节点。"""
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            result = list(graph.tasks)
            if criteria.status is not None:
                result = [n for n in result if n.status == criteria.status]
            if criteria.node_ids is not None:
                idset = set(criteria.node_ids)
                result = [n for n in result if n.node_id in idset]
            if criteria.has_child_tasks is not None:
                want_leaf = criteria.has_child_tasks  # True=仅叶(无结构子)
                result = [
                    n
                    for n in result
                    if self._has_child(graph, n.node_id) != want_leaf
                ]
            return result

    def get_child_tasks(self, task_id: str, node_id: str) -> list[TaskNode]:
        """读某节点【结构子】=relations 中 src_id==node_id 的 dst 节点(直接分解产物)。"""
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            self._require_node(graph, node_id)
            child_ids = [
                r.dst_id
                for r in graph.relations
                if r.src_id == node_id and r.type == RelationType.DEPENDENCY
            ]
            return [n for n in graph.tasks if n.node_id in child_ids]

    def get_parent_task(self, task_id: str, node_id: str) -> TaskNode | None:
        """读某节点【结构父】=relations 中 dst_id==node_id 的 src 节点(单入,至多 1;根返回 None)。"""
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            self._require_node(graph, node_id)
            parent_ids = [
                r.src_id
                for r in graph.relations
                if r.dst_id == node_id and r.type == RelationType.DEPENDENCY
            ]
            if not parent_ids:
                return None
            return self._require_node(graph, parent_ids[0])

    def remove_subtree(self, task_id: str, node_id: str) -> TaskExecutionGraph:
        """删节点 + 其下整个子树(递归 get_child_tasks 删;含 relations 边)。
        触发:升 BBS 时——某 xx_node 搜推 MISS 且其下所有子都 MISS、没走 RUNNING(整子树无效)。"""
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            self._require_node(graph, node_id)
            subtree = self._collect_subtree(graph, node_id)
            graph.tasks = [n for n in graph.tasks if n.node_id not in subtree]
            graph.relations = [
                r
                for r in graph.relations
                if r.src_id not in subtree and r.dst_id not in subtree
            ]
            return graph

    def _node_depth(self, task_id: str, node_id: str) -> int:
        """从 relations 分解树递归自算深度(派生不持久)。根=0。"""
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            self._require_node(graph, node_id)
            depth = 0
            cur = node_id
            while True:
                parent_ids = [
                    r.src_id
                    for r in graph.relations
                    if r.dst_id == cur and r.type == RelationType.DEPENDENCY
                ]
                if not parent_ids:
                    break
                cur = parent_ids[0]
                depth += 1
            return depth

    def _execution_config(self, task_id: str) -> dict[str, Any]:
        """读 MAX_DEPTH(内层升 BBS 阈值)/ BBS_MAX_DEPTH(外层 STUCK 阈值,默认 3)等,填默认。"""
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            cfg: dict[str, Any] = dict(graph.extend_props.get("execution_config", {}))
            cfg.setdefault("MAX_DEPTH", _DEFAULT_MAX_DEPTH)
            cfg.setdefault("BBS_MAX_DEPTH", _DEFAULT_BBS_MAX_DEPTH)
            return cfg
