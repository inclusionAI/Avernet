"""TaskGraphService:任务图谱 SSOT + 原子变更唯一网关(7+2 API)。

对齐 plan.md §3.1 + 任务图谱文档 lunk1txfuv6gtwk2。
in-memory store(M1);ORM 适配按需后续。查询返回引用(D3-A:调用方不应 mutate)。
"""
from __future__ import annotations

import threading
import time
import uuid
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
    TaskGraphPatch,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskNodeQueryCriteria,
    TaskSpec,
    TaskSummary,
)

# 合法状态转换(PLANNING/RUNNING 解耦:PLANNING=规划中(显式委托态),RUNNING=执行中(子执行/自身执行))
# acceptance 驱动(skill 验收回投):仅 RUNNING->DONE(PASS)/FAILED(FAIL+gaps);FAILED 不再经 acceptance 翻
_ACCEPTANCE_TRANSITIONS: dict[Status, set[Status]] = {
    Status.RUNNING: {Status.DONE, Status.FAILED},
}
# status 直驱(框架内部:派发/复位/传播/HUNG)。v4:父节点规划出子由 add_task_nodes 直置 PLANNING(不走此表),
#   故 PLANNING->RUNNING 已废弃;RUNNING 已不再表示委托态(委托态=PLANNING),RUNNING->PLANNING 已废弃。
#   PENDING->RUNNING(叶子派发执行) / RUNNING->PENDING(harness 复位重投) / RUNNING->DONE(gap 闭) / RUNNING->HUNG
#   PLANNING->DONE(gap 闭传播) / PLANNING->HUNG(depth>=MAX 拆不动)
#   FAILED->PENDING(harness 重新派发执行重试) / FAILED->HUNG(重试达上限)
_DIRECT_TRANSITIONS: dict[Status, set[Status]] = {
    Status.PENDING: {Status.RUNNING, Status.HUNG, Status.DONE},
    Status.PLANNING: {Status.DONE, Status.HUNG},
    Status.RUNNING: {Status.PENDING, Status.DONE, Status.HUNG},
    Status.FAILED: {Status.PENDING, Status.HUNG},
}
# 可委托(add_task_nodes 时 parent 允许的态):PENDING(初始/根)/FAILED(补救)/PLANNING(前向重规划)
_DELEGATABLE_PARENT: set[Status] = {Status.PENDING, Status.FAILED, Status.PLANNING}

_DEFAULT_MAX_DEPTH = 2
_DEFAULT_MAX_LOOP = 10  # 图级总轮次(根 gap 不闭 + 反复升 BBS)
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
            graph.extend_props["source_channel_type"] = task_info.source_channel_type
            graph.extend_props["source_channel_id"] = task_info.source_channel_id
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
            # 父进 PLANNING(委托/编排态:等子完成 / 待重算 gap)。v4:规划出子的父永不为 RUNNING,
            # RUNNING 只给真正派发执行的叶子。
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
        # 前向重规划:on_pass 翻父 RUNNING->PLANNING 后 add(节点显式委托态)
        cond_c = any(n.status == Status.PLANNING for n in graph.tasks)
        # miss 补救 / 派发前分解:节点 PENDING(+miss_events MISS 补救,或纯 PENDING 叶结构构造)
        cond_d = any(
            n.status == Status.PENDING
            and not self._has_child(graph, n.node_id)
            for n in graph.tasks
        )
        if not (cond_a or cond_b or cond_c or cond_d):
            raise GraphIntegrityError("add_task_nodes: 触发条件 a/b/c/d 均不满足")

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
            elif patch.exec_error is not None:
                # 执行报错(非验收):不翻终态,仅 fold extend_props(供 on_harness 读 harness_retries);
                # 翻态/复位由编排核 on_harness 直驱 patch.status 处理。
                if patch.extend_props_patch is not None:
                    node.run_info.extend_props.update(patch.extend_props_patch)
                return NodeOpResult(
                    task_id=patch.task_id, node_id=patch.node_id, success=True,
                    prev_status=prev_status, new_status=node.status,
                )
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

    def update_task_graph_info(self, task_id: str, patch: TaskGraphPatch) -> TaskExecutionGraph:
        """图级原子写口:收口图级终态(``status``=DONE/HUNG、``loop_round`` 原子加、``output`` 浅合并、
        ``extend_props`` 浅合并承载 ``bbs_mode``/``hung_reason``)。图谱 SSOT 唯一图级写口;编排核升 BBS /
        根终验完成等图级终态变更一律经此方法,不直写返回的 graph 引用。所有字段增量:未给不动。"""
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            if patch.loop_round_increment is not None:
                graph.loop_round += patch.loop_round_increment
            if patch.status is not None:
                graph.status = patch.status
            if patch.output_patch is not None:
                graph.output.update(patch.output_patch)
            if patch.extend_props_patch is not None:
                graph.extend_props.update(patch.extend_props_patch)
            return graph

    def claim_bbs_owner(self, task_id: str, bot_id: str) -> NodeOpResult:
        """BBS 接力:任务根级 CAS 占有(root.run_info.extend_props['bbs_owner'])。

        恰一赢:首个 bot 写入成功;后续不同 bot 重 claim 抛 ``TaskStateError``(CAS 输者)。
        同 bot 重 claim 幂等(成功)。非 ``bbs_mode`` 任务拒绝(``TaskStateError``)。
        实现:取 ``_lock_for(task_id)``(RLock 可重入)后调 ``update_task_node_info`` 折叠
        ``extend_props_patch={'bbs_owner','bbs_claim_at'}`` —— 不翻态,PLANNING/HUNG 根均可写。
        """
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            if not graph.extend_props.get("bbs_mode"):
                raise TaskStateError(f"claim_bbs_owner: task={task_id} 非 bbs_mode 任务")
            root = next((n for n in graph.tasks if n.node_id == task_id), None)
            if root is None:
                raise TaskNotFoundError(f"claim_bbs_owner: root not found task={task_id}")
            owner = root.run_info.extend_props.get("bbs_owner")
            if owner is not None and owner != bot_id:
                raise TaskStateError(f"claim_bbs_owner: task={task_id} 已被 {owner} 占有")
            return self.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=task_id,
                    extend_props_patch={"bbs_owner": bot_id, "bbs_claim_at": time.time()},
                )
            )

    def attach_bbs_node(
        self, task_id: str, parent_node_id: str, task_spec: TaskSpec, bot_id: str
    ) -> TaskNode:
        """BBS 接力步④:在 parent 下新建 run_mode=bbs scoped 子节点 + 翻 PENDING→RUNNING(create+start 合一)。

        前置:调用者须为当前 ``bbs_owner``(root.run_info.extend_props['bbs_owner'] == bot_id,否则 TaskStateError);
        parent 须满足 add 触发条件(根 PLANNING 等);深度闸 ``bbs_relay_count >= BBS_MAX_DEPTH`` →
        图级 HUNG(``hung_reason=bbs_relay_exhausted``)+ TaskStateError。
        实现:add_task_nodes(挂 parent 下,parent→PLANNING)→ update_task_node_info(PENDING→RUNNING)
        → update_task_graph_info(bbs_relay_count++);返回新建节点(add_task_nodes 挂入为同一引用,
        update_task_node_info 原地翻 RUNNING,故返回节点 status=RUNNING)。
        """
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            root = next((n for n in graph.tasks if n.node_id == task_id), None)
            if root is None or root.run_info.extend_props.get("bbs_owner") != bot_id:
                raise TaskStateError(f"attach_bbs_node: 非claim持有者 task={task_id}")
            relay_count = int(graph.extend_props.get("bbs_relay_count", 0))
            if relay_count >= self._execution_config(task_id)["BBS_MAX_DEPTH"]:
                self.update_task_graph_info(
                    task_id,
                    TaskGraphPatch(
                        status=Status.HUNG,
                        extend_props_patch={"hung_reason": "bbs_relay_exhausted"},
                    ),
                )
                raise TaskStateError(f"attach_bbs_node: BBS relay 深度达上限 task={task_id}")
            node_id = f"bbs-{uuid.uuid4().hex[:8]}"
            node = TaskNode(
                node_id=node_id,
                task_id=task_id,
                status=Status.PENDING,
                task_spec=task_spec,
                run_info=RuntimeInfo(run_mode="bbs", assignee=bot_id, start_time=time.time()),
                node_run_graph=graph,
            )
            self.add_task_nodes([node], parent_node_id=parent_node_id)  # a/b/c/d 校验 + 父→PLANNING
            self.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=node_id, status=Status.RUNNING)
            )  # create+start:PENDING→RUNNING 是 _DIRECT_TRANSITIONS 合法翻
            self.update_task_graph_info(
                task_id,
                TaskGraphPatch(extend_props_patch={"bbs_relay_count": relay_count + 1}),
            )
            return node

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

    # v4:remove_subtree 已删——升 BBS 不再删子树,HUNG 节点保留在图里,靠终态传播(子含 HUNG→父 HUNG)
    # 冒泡驱动收敛。dashboard 子树投影仍用 _collect_subtree。


    def list_task_summaries(self, status: "Status | None" = None) -> list[TaskSummary]:
        """列出全部任务摘要(轻量投影),按 run_id 降序(最新在前)。可选按图级 status 过滤。

        visualization / dashboard 列表视图用;不返回完整图对象。跨 task 读经 registry_lock 串行快照。"""
        with self._registry_lock:
            summaries: list[TaskSummary] = []
            for tid, graph in self._graphs.items():
                if status is not None and graph.status != status:
                    continue
                root = next((n for n in graph.tasks if n.node_id == tid), None)
                title = root.task_spec.metadata.title if root else ""
                summaries.append(TaskSummary(
                    task_id=tid, run_id=graph.run_id, status=graph.status,
                    title=title, node_count=len(graph.tasks), loop_round=graph.loop_round,
                    bbs_mode=bool(graph.extend_props.get("bbs_mode", False))))
            summaries.sort(key=lambda s: s.run_id, reverse=True)
            return summaries

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
        """读 MAX_DEPTH(结构深度闸门,默认 2)/ MAX_LOOP(图级总轮次,默认 10)/ MAX_HARNESS(默认 3),填默认。"""
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            cfg: dict[str, Any] = dict(graph.extend_props.get("execution_config", {}))
            cfg.setdefault("MAX_DEPTH", _DEFAULT_MAX_DEPTH)
            cfg.setdefault("MAX_LOOP", _DEFAULT_MAX_LOOP)
            cfg.setdefault("MAX_HARNESS", 3)
            cfg.setdefault("BBS_MAX_DEPTH", _DEFAULT_BBS_MAX_DEPTH)
            return cfg
