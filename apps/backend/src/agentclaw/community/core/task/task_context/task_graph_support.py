"""Private implementation functions for the unified TaskGraphService.

This module deliberately defines no service, policy, query, or mode-specific classes.
TaskGraphService remains the single public domain owner; these functions only keep its
implementation below the repository's 1000-line source-file limit.
"""

from __future__ import annotations

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
    DoneOutput,
    NodeOpResult,
    RelationType,
    RuntimeInfo,
    Status,
    TaskCallbackData,
    TaskContext,
    TaskExecutionGraph,
    TaskGraphPatch,
    TaskInfo,
    TaskRuntimeProfile,
    TaskNode,
    TaskNodePatch,
    TaskNodeQueryCriteria,
    TaskSpec,
    TaskSummary,
)
from agentclaw.community.core.task.repository.types import BbsTaskOverviewRecord
from agentclaw.community.core.task.task_context.task_artifact.artifact_publish import (
    graph_node_attempt,
)

DEFAULT_MAX_DEPTH = 2
DEFAULT_MAX_LOOP = 3
DEFAULT_MAX_PLAN_ROUND = 3
DEFAULT_BBS_MAX_DEPTH = 3
MAX_GRAPH_VERSION_RETRIES = 3


def get_node(graph: TaskExecutionGraph, node_id: str) -> TaskNode | None:
    return next((node for node in graph.tasks if node.node_id == node_id), None)


def require_node(graph: TaskExecutionGraph, node_id: str) -> TaskNode:
    node = get_node(graph, node_id)
    if node is None:
        raise NodeNotFoundError(
            f"node_id={node_id} 不存在于 task_id={graph.tasks[0].task_id if graph.tasks else '?'}"
        )
    return node


def has_child(graph: TaskExecutionGraph, node_id: str) -> bool:
    return any(
        relation.src_id == node_id and relation.type == RelationType.DEPENDENCY
        for relation in graph.relations
    )


def collect_subtree(graph: TaskExecutionGraph, node_id: str) -> set[str]:
    """Collect descendants along the graph's structural dependency relation."""
    subtree = {node_id}
    frontier = [node_id]
    while frontier:
        current = frontier.pop()
        for relation in graph.relations:
            if (
                relation.src_id == current
                and relation.type == RelationType.DEPENDENCY
                and relation.dst_id not in subtree
            ):
                subtree.add(relation.dst_id)
                frontier.append(relation.dst_id)
    return subtree


def initialize_graph(self, task_info: TaskInfo) -> TaskExecutionGraph:
    """Build the first graph frame and persist its root runtime identity."""
    task_id = task_info.task_id
    with self._lock_for(task_id):
        if task_id in self._graphs:
            raise GraphAlreadyInitializedError(f"task_id={task_id} 图已存在")
        graph = _build_initial_graph(self, task_info)
        if self._graph_repo is not None:
            self._graph_versions[task_id] = self._graph_repo.create_graph(
                graph, runtime_status=Status.PENDING
            )
        self._graphs[task_id] = graph
        return graph


def _build_initial_graph(self, task_info: TaskInfo) -> TaskExecutionGraph:
    task_id = task_info.task_id
    run_id = self._next_run_id()
    # The graph starts measuring time at task creation. The root remains
    # PENDING, while its start_time records initialization time.
    started_at = int(time.time() * 1000)
    main_session_id = task_info.execution_config.get("main_session_id")
    lifecycle_stage = str(task_info.execution_config.get("lifecycle_stage") or "").strip()
    relay_mode = task_info.execution_config.get("orchestration_mode") == "relay"
    root_extend_props: dict[str, Any] = {}
    if isinstance(main_session_id, str) and main_session_id.strip():
        root_extend_props["session_id"] = main_session_id
        if lifecycle_stage:
            root_extend_props["lifecycle_stage"] = lifecycle_stage
    if relay_mode:
        root_extend_props["relay_root"] = True
        root_extend_props["relay_token"] = uuid.uuid4().hex
    root = TaskNode(
        node_id=task_id,
        task_id=task_id,
        status=Status.RUNNING if relay_mode else Status.PENDING,
        task_spec=task_info.task_spec,
        run_info=RuntimeInfo(
            run_mode="single_bot" if relay_mode else None,
            assignee=task_info.owner_bot_id if relay_mode else None,
            start_time=started_at,
            extend_props=root_extend_props,
        ),
        node_run_graph=None,  # type: ignore[arg-type]
    )
    graph = TaskExecutionGraph(
        run_id=run_id,
        loop_round=0,
        status=Status.RUNNING,
        tasks=[root],
        relations=[],
        task_id=task_id,
    )
    root.node_run_graph = graph
    graph.extend_props["execution_config"] = dict(task_info.execution_config)
    graph.extend_props["runtime_profile"] = TaskRuntimeProfile.from_execution_config(
        task_info.execution_config
    ).to_dict()
    graph.extend_props["source_type"] = task_info.source_type
    graph.extend_props["owner_bot_id"] = task_info.owner_bot_id
    graph.extend_props["owner_user_id"] = task_info.owner_user_id
    graph.extend_props["gaps"] = []
    return graph


def initialize_intake_graph(
    self, task_info: TaskInfo, session_id: str
) -> TaskExecutionGraph:
    """Atomically create TaskInfo and root graph for either orchestration mode."""
    task_id = task_info.task_id
    with self._lock_for(task_id):
        if task_id in self._graphs:
            raise GraphAlreadyInitializedError(f"task_id={task_id} 图已存在")
        if self._graph_repo is None:
            raise TaskStateError(
                "intake task creation requires a durable graph repository"
            )
        graph = _build_initial_graph(self, task_info)
        target = task_info.execution_config.get("initial_target") or {}
        root = graph.tasks[0]
        if graph.is_relay:
            root.run_info.run_mode = (
                "coop_group" if target.get("target_type") == "GROUP" else "single_bot"
            )
            root.run_info.assignee = str(
                target.get("target_id") or task_info.owner_bot_id
            )
        version = self._graph_repo.create_intake_graph(task_info, graph, session_id)
        self._graph_versions[task_id] = version
        self._graphs[task_id] = graph
        return graph


def load_action_logs(self, graph: TaskExecutionGraph, *, limit: int = 200) -> None:
    """Attach bounded persisted action history for diagnostic Dashboard reads."""
    if self._graph_repo is None:
        return
    grouped = self._graph_repo.load_action_logs(graph.task_id, limit=limit)
    for node in graph.tasks:
        node.run_info.action_log = list(grouped.get(node.node_id, []))


def _relay_output_value(output: dict[str, Any]) -> Any:
    """Project a Relay node output to its graph-level aggregate value."""
    if not output:
        return None
    if len(output) == 1:
        for key in ("result", "markdown"):
            value = output.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return dict(output)


def _relay_graph_output(graph: TaskExecutionGraph) -> dict[str, Any]:
    """Aggregate accepted Relay outputs without mutating any prior node."""
    output: dict[str, Any] = {}
    for node in graph.tasks:
        runtime = node.run_info
        decision = str(runtime.extend_props.get("execution_decision") or "").upper()
        if decision != "ACCEPTED":
            continue
        value = _relay_output_value(runtime.output)
        if value is not None:
            output[node.node_id] = value
    return output


def query_task_dashboard(
    self, task_id: str, node_id: str | None = None
) -> TaskExecutionGraph:
    """只读看板快照。node_id=None 返回整图引用;指定 node_id 返回该节点子树投影(新构造对象)。

    跨实例版本感知缓存(spec §11):缓存命中时比对 ``task_info.graph_version`` 与本地图版本,
    不一致(他实例已推进图)→ 从共享存储重新 hydrate,保证看板总能反映最新已提交图态。
    """
    with self._lock_for(task_id):
        graph = self._graphs.get(task_id)
        if graph is None:
            graph = self._hydrate_locked(task_id)
        elif self._graph_repo is not None:
            db_version = self._graph_repo.get_version(task_id)
            if db_version is not None and db_version != self._graph_versions.get(
                task_id
            ):
                graph = self._hydrate_locked(task_id)  # 缓存过期 → 重新 hydrate
        if graph is None:
            raise TaskNotFoundError(f"task_id={task_id} 图不存在")
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
            task_id=graph.task_id,
        )


def effective_graph_status(self, task_id: str) -> "Status":
    """图级有效态:中心化以根态为准;Relay 由各节点收敛态与最新 gaps 决定。

    与 ``query_task_dashboard(task_id).effective_status`` 同源;控制流不消费本方法(不改并发主线),
    供看板、任务列表和持久化派生统一使用。"""
    with self._lock_for(task_id):
        graph = self._require_graph(task_id)
        return graph.effective_status


def query_task_nodes(
    self, task_id: str, criteria: TaskNodeQueryCriteria
) -> list[TaskNode]:
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
                n for n in result if self._has_child(graph, n.node_id) != want_leaf
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


def list_task_summaries(self, status: "Status | None" = None) -> list[TaskSummary]:
    """列出全部任务摘要(轻量投影),按 run_id 降序(最新在前)。可选按图级 status 过滤。

    visualization / dashboard 列表视图用;不返回完整图对象。跨 task 读经 registry_lock 串行快照。"""
    with self._registry_lock:
        summaries: list[TaskSummary] = []
        for tid, graph in self._graphs.items():
            graph_status = graph.effective_status
            if status is not None and graph_status != status:
                continue
            root = next((n for n in graph.tasks if n.node_id == tid), None)
            title = root.task_spec.context.title if root else ""
            summaries.append(
                TaskSummary(
                    task_id=tid,
                    run_id=graph.run_id,
                    status=graph_status,
                    title=title,
                    node_count=len(graph.tasks),
                    loop_round=graph.loop_round,
                    bbs_mode=bool(graph.extend_props.get("bbs_mode", False)),
                )
            )
        summaries.sort(key=lambda s: s.run_id, reverse=True)
        return summaries


def list_bbs_tasks_overview(
    self,
    page: int = 1,
    page_size: int = 20,
    *,
    search_word: str | None = None,
    status: str | None = None,
) -> "tuple[list[BbsTaskOverviewRecord], int]":
    """列 BBS 接力任务概览的一页(run_mode='bbs' 的 run_info ⋈ node,补 publisher);只读。

    委托 ``graph_repo.list_bbs_tasks_overview``(透传 status/search_word 可选过滤,为空不过滤,退化为
    纯分页);无 repo 绑定(纯内核/测试)→ ([], 0),不阻断。"""
    if self._graph_repo is None:
        return [], 0
    return self._graph_repo.list_bbs_tasks_overview(
        page, page_size, search_word=search_word, status=status
    )


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
    """读 MAX_DEPTH(结构深度闸门,默认 2)/ MAX_LOOP(图级总轮次,默认 3)/ MAX_HARNESS(默认 2),填默认。"""
    with self._lock_for(task_id):
        graph = self._require_graph(task_id)
        cfg: dict[str, Any] = dict(graph.extend_props.get("execution_config", {}))
        cfg.setdefault("MAX_DEPTH", DEFAULT_MAX_DEPTH)
        cfg.setdefault("MAX_LOOP", DEFAULT_MAX_LOOP)
        cfg.setdefault("MAX_HARNESS", 2)
        cfg.setdefault("MAX_PLAN_ROUND", DEFAULT_MAX_PLAN_ROUND)
        cfg.setdefault("BBS_MAX_DEPTH", DEFAULT_BBS_MAX_DEPTH)
        return cfg


def report(self, data: TaskCallbackData) -> Any:
    """Accept graph facts and enqueue a durable notification atomically."""
    envelope = data.data if isinstance(data.data, dict) else {}
    payload = envelope.get("payload") or {}
    task_id = str(payload.get("task_id") or "")
    patch = payload.get("patch")
    if not task_id and patch is not None:
        task_id = str(getattr(patch, "task_id", "") or "")
    report_type = str(envelope.get("report_type") or "UNKNOWN").upper()
    node_id = str(payload.get("node_id") or getattr(patch, "node_id", "") or "")
    event_id = str(
        envelope.get("event_id")
        or f"{report_type}:{task_id}:{node_id}:{uuid.uuid4().hex}"
    )
    event = {
        "event_id": event_id,
        "event_type": report_type,
        "task_id": task_id,
        "node_id": node_id or None,
    }
    with self._report_outbox_scope(event):
        return self._dispatch_report(data)


def _dispatch_report(self, data: TaskCallbackData) -> Any:
    """Dispatch a typed report to the graph mutation implementation.

    Task modules submit typed internal facts in ``TaskCallbackData``; this
    gateway owns the actual graph mutation methods so callers do not reach
    into graph persistence directly. Transport adapters may use the same
    envelope with serializable payloads, while in-process callers may pass
    domain objects to avoid a second command model during migration.
    """
    envelope = data.data
    if not isinstance(envelope, dict):
        raise TaskStateError("graph report payload must be an object")
    report_type = str(envelope.get("report_type") or "").upper()
    payload = envelope.get("payload") or {}
    if report_type == "NODE_PATCH":
        patch = payload.get("patch")
        if not isinstance(patch, TaskNodePatch):
            raise TaskStateError("NODE_PATCH report requires TaskNodePatch")
        return self.update_task_node_info(patch)
    if report_type == "GRAPH_PATCH":
        task_id = str(payload.get("task_id") or "")
        patch = payload.get("patch")
        if not task_id or not isinstance(patch, TaskGraphPatch):
            raise TaskStateError(
                "GRAPH_PATCH report requires task_id and TaskGraphPatch"
            )
        return self.update_task_graph_info(task_id, patch)
    if report_type == "ADD_NODES":
        task_id = str(payload.get("task_id") or "")
        nodes = payload.get("nodes")
        if (
            not task_id
            or not isinstance(nodes, list)
            or not all(isinstance(node, TaskNode) for node in nodes)
        ):
            raise TaskStateError("ADD_NODES report requires task_id and TaskNode list")
        return self.add_task_nodes(
            nodes,
            parent_node_id=payload.get("parent_node_id"),
            attach_dependency=bool(payload.get("attach_dependency", True)),
            mark_parent_planning=bool(payload.get("mark_parent_planning", True)),
        )
    if report_type == "ADD_RELATIONS":
        task_id = str(payload.get("task_id") or "")
        edges = payload.get("edges")
        if not task_id or not isinstance(edges, list):
            raise TaskStateError("ADD_RELATIONS report requires task_id and edges")
        return self.add_relations(task_id, edges)
    if report_type == "BBS_ATTACH":
        task_id = str(payload.get("task_id") or "")
        parent_node_id = str(payload.get("parent_node_id") or "")
        bot_id = str(payload.get("bot_id") or "")
        task_spec = payload.get("task_spec")
        if (
            not task_id
            or not parent_node_id
            or not bot_id
            or not isinstance(task_spec, TaskSpec)
        ):
            raise TaskStateError("BBS_ATTACH report payload is invalid")
        return self.attach_bbs_node(task_id, parent_node_id, task_spec, bot_id)
    if report_type == "BBS_CLAIM":
        task_id = str(payload.get("task_id") or "")
        bot_id = str(payload.get("bot_id") or "")
        node_id = str(payload.get("node_id") or "") or None
        claim_id = str(payload.get("claim_id") or "") or None
        if not task_id or not bot_id:
            raise TaskStateError("BBS_CLAIM report requires task_id and bot_id")
        return self.claim_bbs_owner(task_id, bot_id, node_id=node_id, claim_id=claim_id)
    raise TaskStateError(f"unsupported graph report_type={report_type}")


async def apply_start_fact(self, patch: TaskNodePatch) -> NodeOpResult:
    """Apply an executor start fact without invoking orchestration policy."""
    with self._lock_for(patch.task_id):
        graph = self.query_task_dashboard(patch.task_id)
        node = next(
            (item for item in graph.tasks if item.node_id == patch.node_id), None
        )
        if node is None:
            raise NodeNotFoundError(
                f"on_start: node not found {patch.task_id}::{patch.node_id}"
            )
        if node.status == Status.RUNNING:
            return NodeOpResult(
                task_id=patch.task_id,
                node_id=patch.node_id,
                success=True,
                prev_status=Status.RUNNING,
                new_status=Status.RUNNING,
            )
        if node.status in {
            Status.DONE,
            Status.FAILED,
            Status.HUNG,
            Status.PLANNING,
        }:
            raise TaskStateError(
                f"on_start: stale/illegal start on {node.status} node "
                f"{patch.task_id}::{patch.node_id}"
            )
        return self.report(
            TaskCallbackData(
                data={"report_type": "NODE_PATCH", "payload": {"patch": patch}}
            )
        )


def claim_bbs_owner(
    self,
    task_id: str,
    bot_id: str,
    *,
    node_id: str | None = None,
    claim_id: str | None = None,
) -> NodeOpResult:
    """Claim centralized root BBS or a Relay target BBS node atomically."""
    with self._lock_for(task_id):
        graph = self._require_graph(task_id)
        if "bbs" not in TaskRuntimeProfile.run_modes_from_graph(graph):
            raise TaskStateError(
                f"claim_bbs_owner: BBS run mode is not allowed by runtime profile task={task_id}"
            )
        if not graph.extend_props.get("bbs_mode"):
            raise TaskStateError(f"claim_bbs_owner: task={task_id} 非 bbs_mode 任务")
        target_id = node_id or task_id
        if graph.is_relay and target_id == task_id:
            raise TaskStateError(
                f"claim_bbs_owner: Relay root cannot be claimed as a BBS baton task={task_id}"
            )
        target = self._require_node(graph, target_id)
        if node_id is not None:
            if target.run_info.run_mode != "bbs" or target.status != Status.PENDING:
                raise TaskStateError(
                    f"claim_bbs_owner: node={node_id} 非可认领 BBS 节点"
                )
            prior_claim_id = target.run_info.extend_props.get("bbs_claim_id")
            if claim_id and prior_claim_id == claim_id:
                return NodeOpResult(
                    task_id=task_id,
                    node_id=target_id,
                    success=True,
                    prev_status=target.status,
                    new_status=target.status,
                )

            def mutation(latest_graph):
                node = self._require_node(latest_graph, target_id)
                owner = node.run_info.extend_props.get("bbs_owner")
                if owner is not None and owner != bot_id:
                    raise TaskStateError(
                        f"claim_bbs_owner: task={task_id} node={target_id} 已被 {owner} 占有"
                    )
                prev = node.status
                node.run_info.extend_props.update(
                    {
                        "bbs_owner": bot_id,
                        "bbs_claim_at": int(time.time() * 1000),
                        "bbs_claim_id": claim_id,
                        "driver_bot_id": bot_id,
                        "next_relay_bots": [bot_id],
                    }
                )
                node.run_info.assignee = bot_id
                node.status = Status.RUNNING
                if node.run_info.start_time is None:
                    node.run_info.start_time = int(time.time() * 1000)
                return (
                    NodeOpResult(
                        task_id=task_id,
                        node_id=target_id,
                        success=True,
                        prev_status=prev,
                        new_status=node.status,
                    ),
                    None,
                    True,
                )

            return self._mutate_with_version_retry(task_id, mutation)

        # Centralized legacy BBS keeps its established root-row CAS.
        root = target
        owner = root.run_info.extend_props.get("bbs_owner")
        if owner is not None and owner != bot_id:
            raise TaskStateError(f"claim_bbs_owner: task={task_id} 已被 {owner} 占有")
        persisted = (
            self._graph_repo is not None
            and self._graph_repo.get_version(task_id) is not None
        )
        if persisted:
            if not self._graph_repo.claim_bbs_owner(task_id, bot_id):
                raise TaskStateError(f"claim_bbs_owner: task={task_id} DB CAS 失败")
            root.run_info.extend_props.update(
                {
                    "bbs_owner": bot_id,
                    "bbs_claim_at": int(time.time() * 1000),
                }
            )
            self._graph_versions[task_id] = self._graph_repo.get_version(task_id) or 0
            return NodeOpResult(
                task_id=task_id,
                node_id=task_id,
                success=True,
                prev_status=root.status,
                new_status=root.status,
            )
        return self.update_task_node_info(
            TaskNodePatch(
                task_id=task_id,
                node_id=task_id,
                extend_props_patch={
                    "bbs_owner": bot_id,
                    "bbs_claim_at": int(time.time() * 1000),
                },
            )
        )


def get_task_context(self, task_id: str) -> TaskContext:
    """Project the latest persisted business facts without graph topology."""
    with self._lock_for(task_id):
        graph = self._require_graph(task_id)
        root = self._require_node(graph, graph.task_id)
        outputs: list[DoneOutput] = []
        for node in graph.tasks:
            runtime = node.run_info
            if (
                str(runtime.extend_props.get("execution_decision") or "").upper()
                != "ACCEPTED"
                or not runtime.output
                or runtime.acceptance_result is None
            ):
                continue
            # Older Relay graphs were persisted before actual_goal gained its
            # own column. On ACCEPTED nodes, the effective goal was the node
            # TaskSpec goal, so project it without mutating the prior node.
            actual_goal = runtime.actual_goal or node.task_spec.goal
            outputs.append(
                DoneOutput(
                    node_id=node.node_id,
                    actual_goal=actual_goal,
                    output=dict(runtime.output),
                    acceptance_result=runtime.acceptance_result,
                )
            )
        return TaskContext(
            spec=root.task_spec,
            all_done_output=outputs,
            gaps=[str(item) for item in graph.extend_props.get("gaps", [])],
        )


def get_node_context(self, task_id: str, node_id: str) -> TaskContext:
    """Project the baton input from the authoritative graph, without writing it.

    Prior accepted outputs are limited to the unique predecessor chain. The
    current baton's accepted report is separate, so a retry cannot count its
    own output as an earlier fact. Task-level gaps are deliberately not read.
    """
    with self._lock_for(task_id):
        # query_task_dashboard revalidates the persisted graph version when a
        # different Backend worker accepted the preceding report.
        graph = self.query_task_dashboard(task_id)
        root = self._require_node(graph, graph.task_id)
        current = self._require_node(graph, node_id)
        parent_by_child = {
            relation.dst_id: relation.src_id
            for relation in graph.relations
            if relation.type == RelationType.DEPENDENCY
        }
        lineage: list[TaskNode] = []
        seen = {node_id}
        predecessor_id = parent_by_child.get(node_id)
        while predecessor_id is not None:
            if predecessor_id in seen:
                raise GraphIntegrityError("relay predecessor chain contains a cycle")
            seen.add(predecessor_id)
            lineage.append(self._require_node(graph, predecessor_id))
            predecessor_id = parent_by_child.get(predecessor_id)
        lineage.reverse()

        def accepted_output(node: TaskNode) -> DoneOutput | None:
            runtime = node.run_info
            if (
                str(runtime.extend_props.get("execution_decision") or "").upper()
                != "ACCEPTED"
                or runtime.acceptance_result is None
            ):
                return None
            return DoneOutput(
                node_id=node.node_id,
                actual_goal=runtime.actual_goal or node.task_spec.goal,
                output=dict(runtime.output),
                acceptance_result=runtime.acceptance_result,
            )

        outputs = [
            fact for node in lineage if (fact := accepted_output(node)) is not None
        ]
        runtime = current.run_info
        decision = str(runtime.extend_props.get("execution_decision") or "").upper()
        receipts = graph.extend_props.get("_relay_report_receipts") or {}
        if (
            receipts
            and (
                f"{node_id}:EXECUTION_RESULT:"
                f"{int(runtime.extend_props.get('harness_retries', 0) or 0)}"
            )
            not in receipts
        ):
            decision = ""
        accepted_patch = None
        if decision in {"ACCEPTED", "DECLINED"}:
            accepted_patch = TaskNodePatch(
                task_id=task_id,
                node_id=node_id,
                execution_decision=decision,
                actual_goal=runtime.actual_goal if decision == "ACCEPTED" else None,
                output_patch=dict(runtime.output) if decision == "ACCEPTED" else {},
                local_acceptance_result=(
                    runtime.acceptance_result if decision == "ACCEPTED" else None
                ),
                failure_reason=runtime.failure_reason,
            )
        return TaskContext(
            spec=root.task_spec,
            all_done_output=outputs,
            gaps=[],
            task_id=task_id,
            node_id=node_id,
            attempt=graph_node_attempt(current),
            root_task_spec=root.task_spec,
            current_task_spec=current.task_spec,
            actual_goal=runtime.actual_goal if decision == "ACCEPTED" else None,
            capability_failure_reason=runtime.failure_reason
            if decision == "DECLINED"
            else None,
            accepted_execution_patch=accepted_patch,
            accepted_plan_report=_accepted_plan_report(graph, node_id),
        )


def _accepted_plan_report(graph: TaskExecutionGraph, node_id: str):
    from agentclaw.community.core.task.task_context.relay_report import (
        accepted_plan_report,
    )

    return accepted_plan_report(graph, node_id)
