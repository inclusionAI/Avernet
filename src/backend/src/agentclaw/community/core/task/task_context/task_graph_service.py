"""TaskGraphService:任务图谱 SSOT + 原子变更唯一网关(7+2 API)。

对齐 plan.md §3.1 + 任务图谱文档 lunk1txfuv6gtwk2。
in-memory store(M1);ORM 适配按需后续。查询返回引用(D3-A:调用方不应 mutate)。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from agentclaw.community.core.repository.protocols.task import (
    TaskGraphRepositoryProtocol,
    TaskInfoRepositoryProtocol,
)
from agentclaw.community.core.task.domain.errors import (
    GraphAlreadyInitializedError,
    GraphVersionConflictError,
    GraphIntegrityError,
    NodeNotFoundError,
    TaskNotFoundError,
    TaskStateError,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceVerdict,
    NodeAction,
    NodeActionEvent,
    NodeOpResult,
    Relation,
    RelationType,
    RuntimeInfo,
    Status,
    TaskCallbackData,
    TaskContext,
    TaskExecutionGraph,
    TaskGraphPatch,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskNodeQueryCriteria,
    TaskSpec,
    TaskSummary,
    TaskRuntimeProfile,
)
from agentclaw.community.core.task.repository.types import BbsTaskOverviewRecord
from agentclaw.community.core.task.task_context import task_graph_support

_LOG = logging.getLogger(__name__)

# 合法状态转换(PLANNING/RUNNING 解耦:PLANNING=规划中(显式委托态),RUNNING=执行中(子执行/自身执行))
# acceptance 驱动(skill 验收回投):RUNNING->SUCCESS(PASS)/HUNG(FAIL,动态)/DONE(FAIL,外部托管)
_ACCEPTANCE_TRANSITIONS: dict[Status, set[Status]] = {
    Status.RUNNING: {Status.SUCCESS, Status.DONE, Status.HUNG, Status.FAILED},
}
# status 直驱(框架内部:派发/复位/传播/HUNG)。DONE 只表示执行完成,
# SUCCESS 表示验收通过；BBS 经 engine.on_bbs_report 收口时由框架将 scoped
# 节点记为 SUCCESS,无 on_bbs_report 的轻量回退路径仍只写 DONE。
#   PENDING->PLANNING(初始根/MISS 叶进入规划) / PENDING->RUNNING(叶子派发执行) /
#   RUNNING->PENDING(harness 复位重投) / RUNNING->DONE(执行完成但未验收) / RUNNING->SUCCESS(框架确认验收通过) / RUNNING->HUNG
#   PLANNING->DONE(仅执行完成直驱) / PLANNING->SUCCESS(gap 闭合验收通过) / PLANNING->HUNG(depth>=MAX 拆不动)
#   FAILED->PENDING(harness 重新派发执行重试) / FAILED->HUNG(重试达上限)
_DIRECT_TRANSITIONS: dict[Status, set[Status]] = {
    Status.PENDING: {
        Status.PLANNING,
        Status.RUNNING,
        Status.HUNG,
        Status.DONE,
        Status.SUCCESS,
    },
    Status.PLANNING: {Status.DONE, Status.SUCCESS, Status.HUNG},
    Status.RUNNING: {
        Status.PENDING,
        Status.PLANNING,
        Status.DONE,
        Status.SUCCESS,
        Status.HUNG,
    },
    Status.FAILED: {Status.PENDING, Status.HUNG},
    Status.HUNG: {Status.PLANNING},
}
# 可委托(add_task_nodes 时 parent 允许的态):PENDING(初始/根)/FAILED(补救)/PLANNING(前向重规划)
_DELEGATABLE_PARENT: set[Status] = {
    Status.PENDING,
    Status.FAILED,
    Status.PLANNING,
    Status.HUNG,
}

# 终态集(无出边):已完成/失败/挂起/取消节点不再接受重复验收回投。
_TERMINAL_STATUSES: set[Status] = {
    Status.DONE,
    Status.SUCCESS,
    Status.FAILED,
    Status.HUNG,
    Status.CANCELLED,
}


def _pending_callback_audit(task_id: str):
    """Return the pending callback audit for ``task_id`` without consuming it.

    Keeping the record staged until the graph write commits is required for
    optimistic-version retries: a failed stale write must be replayed with the
    same audit record, not silently lose the callback audit.
    """
    from agentclaw.community.core.task.task_runner.callback_adapter import (
        _PENDING_CALLBACK_AUDIT,
    )

    record = _PENDING_CALLBACK_AUDIT.get()
    if record is None or getattr(record, "run_id", None) != task_id:
        return None
    return record


def _clear_pending_callback_audit(record) -> None:
    if record is None:
        return
    from agentclaw.community.core.task.task_runner.callback_adapter import (
        _PENDING_CALLBACK_AUDIT,
    )

    if _PENDING_CALLBACK_AUDIT.get() is record:
        _PENDING_CALLBACK_AUDIT.set(None)


class TaskGraphService:
    """任务图谱 SSOT + 原子变更唯一网关。

    边界:只做图结构 + 节点/图级状态原子写 + 派生只读查询;不含编排(不调编排核、不搜推、不规划)。
    结构归属由 relations 分解树(单入)表达;depth/结构子/结构父均从 relations 派生。
    """

    def __init__(
        self,
        graph_repo: TaskGraphRepositoryProtocol | None = None,
        task_info_repo: TaskInfoRepositoryProtocol | None = None,
    ) -> None:
        self._graphs: dict[str, TaskExecutionGraph] = {}
        self._graph_versions: dict[str, int] = {}
        self._graph_repo = graph_repo
        # Fallback task_info status sink for deployments that persist task_info
        # without attaching the aggregate graph repository. When graph_repo is
        # present, it updates task_info atomically with the graph snapshot.
        self._task_info_repo = task_info_repo
        self._locks: dict[str, threading.RLock] = {}
        self._registry_lock = threading.RLock()
        self._report_local = threading.local()
        self._run_id_counter = 0

    # ===== internal helpers =====
    def bind_repository(self, graph_repo: TaskGraphRepositoryProtocol) -> None:
        """Attach the shared repository at the composition root."""
        self._graph_repo = graph_repo

    @property
    def has_repository(self) -> bool:
        return self._graph_repo is not None

    def bind_task_info_repository(
        self, task_info_repo: TaskInfoRepositoryProtocol
    ) -> None:
        """Attach the task_info status sink at the composition root.

        The aggregate graph repository remains the preferred atomic persistence
        path. This sink covers lightweight/profile-specific wiring where only
        the task_info repository is available.
        """
        self._task_info_repo = task_info_repo

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
        if graph is None and self._graph_repo is not None:
            # Cross-instance correctness: a callback/mutation reaching an instance
            # that has never served this task must hydrate from the shared store
            # before mutating (spec §9/§12). Memory is a cache only.
            graph = self._hydrate_locked(task_id)
        if graph is None:
            raise TaskNotFoundError(f"task_id={task_id} 图不存在")
        return graph

    def _persist_locked(self, graph: TaskExecutionGraph, *, action_events=None) -> None:
        """Persist after a successful in-memory mutation, then advance cache version."""
        events = action_events or []
        if self._graph_repo is None:
            if self._task_info_repo is not None:
                # 乙' c+R2:图级有效态只读派生根态(与既有 root 派生等价,单源化)。
                self._task_info_repo.update_status(
                    graph.task_id, graph.effective_status
                )
            return
        expected = self._graph_versions.get(graph.task_id, 0)
        # 乙' c+R2:图级有效态只读派生根态(与既有 root 派生等价,单源化)。
        runtime_status = graph.effective_status
        # Attach the inbound callback audit to this mutation's transaction (spec §12).
        callback_audit = _pending_callback_audit(graph.task_id)
        try:
            version = self._graph_repo.save_graph(
                graph,
                expected_version=expected,
                runtime_status=runtime_status,
                action_events=events,
                callback_audit=callback_audit,
            )
        except Exception:
            # Never leave a dirty cache after a failed shared-store write. Keep
            # the pending audit staged so a version-conflict retry can commit it
            # together with the replayed graph mutation.
            restored = self._graph_repo.load_graph(graph.task_id)
            if restored is not None:
                self._graphs[graph.task_id] = restored
                self._graph_versions[graph.task_id] = (
                    self._graph_repo.get_version(graph.task_id) or 0
                )
            raise
        _clear_pending_callback_audit(callback_audit)
        self._graph_versions[graph.task_id] = version
        return

    def _hydrate_locked(self, task_id: str) -> TaskExecutionGraph | None:
        if self._graph_repo is None:
            return None
        graph = self._graph_repo.load_graph(task_id)
        if graph is None:
            return None
        self._graphs[task_id] = graph
        self._graph_versions[task_id] = self._graph_repo.get_version(task_id) or 0
        return graph

    @contextmanager
    def _report_outbox_scope(self, event: dict[str, Any]) -> Iterator[None]:
        previous = getattr(self._report_local, "event", None)
        self._report_local.event = event
        try:
            yield
        finally:
            self._report_local.event = previous

    def _append_report_outbox_event(self, graph: TaskExecutionGraph) -> None:
        event = getattr(self._report_local, "event", None)
        if not event:
            return
        events = list(graph.extend_props.get("_task_outbox") or [])
        event_id = str(event["event_id"])
        if any(str(item.get("event_id")) == event_id for item in events):
            return
        events.append(
            dict(event, status="PENDING", created_at_ms=int(time.time() * 1000))
        )
        graph.extend_props["_task_outbox"] = events[-500:]

    def emit_semantic_event(
        self,
        task_id: str,
        event_type: str,
        *,
        node_id: str | None = None,
        payload: dict[str, Any] | None = None,
        event_id: str | None = None,
    ) -> str:
        """Persist a centralized semantic event in the graph-owned outbox."""
        event_key = (
            event_id or f"{event_type}:{task_id}:{node_id or ''}:{uuid.uuid4().hex}"
        )
        event = {
            "event_id": event_key,
            "event_type": str(event_type),
            "task_id": task_id,
            "node_id": node_id,
            "payload": dict(payload or {}),
            "status": "PENDING",
            "created_at_ms": int(time.time() * 1000),
        }

        def mutation(graph):
            events = list(graph.extend_props.get("_semantic_outbox") or [])
            if any(str(item.get("event_id")) == event_key for item in events):
                return event_key, None, False
            events.append(event)
            graph.extend_props["_semantic_outbox"] = events[-500:]
            return event_key, None, True

        return str(self._mutate_with_version_retry(task_id, mutation))

    def list_pending_semantic_events(
        self, task_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        graph = self._require_graph(task_id)
        return [
            dict(item)
            for item in (graph.extend_props.get("_semantic_outbox") or [])
            if item.get("status") == "PENDING"
        ][:limit]

    def acknowledge_semantic_event(self, task_id: str, event_id: str) -> bool:
        def mutation(graph):
            events = list(graph.extend_props.get("_semantic_outbox") or [])
            changed = False
            for item in events:
                if (
                    str(item.get("event_id")) == event_id
                    and item.get("status") == "PENDING"
                ):
                    item["status"] = "ACKED"
                    item["acked_at_ms"] = int(time.time() * 1000)
                    changed = True
            return changed, None, changed

        return bool(self._mutate_with_version_retry(task_id, mutation))

    def list_pending_report_events(
        self, task_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        graph = self._require_graph(task_id)
        return [
            dict(item)
            for item in (graph.extend_props.get("_task_outbox") or [])
            if item.get("status") == "PENDING"
        ][:limit]

    def acknowledge_report_event(self, task_id: str, event_id: str) -> bool:
        def mutation(graph):
            events = list(graph.extend_props.get("_task_outbox") or [])
            changed = False
            for item in events:
                if (
                    str(item.get("event_id")) == event_id
                    and item.get("status") == "PENDING"
                ):
                    item["status"] = "ACKED"
                    item["acked_at_ms"] = int(time.time() * 1000)
                    changed = True
            return changed, None, changed

        return bool(self._mutate_with_version_retry(task_id, mutation))

    def _mutate_with_version_retry(
        self,
        task_id: str,
        mutation: Callable[
            [TaskExecutionGraph], tuple[Any, list[NodeActionEvent] | None, bool]
        ],
    ) -> Any:
        """Run a replayable graph mutation with bounded optimistic-lock retries.

        ``mutation`` must apply its complete change to the supplied graph and
        return ``(result, action_events, should_persist)``. A version conflict
        causes ``_persist_locked`` to hydrate the latest snapshot; the mutation
        is then invoked again against that fresh snapshot. This keeps retries
        from writing a stale graph over another instance's committed changes.
        """
        with self._lock_for(task_id):
            for attempt in range(1, task_graph_support.MAX_GRAPH_VERSION_RETRIES + 1):
                graph = self._require_graph(task_id)
                result, action_events, should_persist = mutation(graph)
                if should_persist:
                    self._append_report_outbox_event(graph)
                if not should_persist:
                    return result
                try:
                    self._persist_locked(graph, action_events=action_events)
                    return result
                except GraphVersionConflictError:
                    if attempt >= task_graph_support.MAX_GRAPH_VERSION_RETRIES:
                        _LOG.exception(
                            "[task][graph] version conflict retries exhausted task=%s attempts=%d",
                            task_id,
                            attempt,
                        )
                        raise
                    _LOG.warning(
                        "[task][graph] version conflict task=%s retry=%d/%d",
                        task_id,
                        attempt,
                        task_graph_support.MAX_GRAPH_VERSION_RETRIES,
                    )
            raise AssertionError("unreachable graph version retry loop")

    def _get_node(self, graph: TaskExecutionGraph, node_id: str) -> TaskNode | None:
        for n in graph.tasks:
            if n.node_id == node_id:
                return n
        return None

    def _require_node(self, graph: TaskExecutionGraph, node_id: str) -> TaskNode:
        node = self._get_node(graph, node_id)
        if node is None:
            raise NodeNotFoundError(
                f"node_id={node_id} 不存在于 task_id={graph.tasks[0].task_id if graph.tasks else '?'}"
            )
        return node

    def _has_child(self, graph: TaskExecutionGraph, node_id: str) -> bool:
        return any(
            r.src_id == node_id and r.type == RelationType.DEPENDENCY
            for r in graph.relations
        )

    def _collect_subtree(self, graph: TaskExecutionGraph, node_id: str) -> set[str]:
        """收集 node_id 及其所有后代 node_id(BFS over relations 分解树)。"""
        subtree: set[str] = {node_id}
        frontier = [node_id]
        while frontier:
            cur = frontier.pop()
            for r in graph.relations:
                if (
                    r.src_id == cur
                    and r.type == RelationType.DEPENDENCY
                    and r.dst_id not in subtree
                ):
                    subtree.add(r.dst_id)
                    frontier.append(r.dst_id)
        return subtree

    # ===== 4 核心写/读 =====
    def initialize_graph(self, task_info: TaskInfo) -> TaskExecutionGraph:
        """建图首帧(全局 RUNNING,只含根节点 PENDING);幂等:同 task_id 重复调抛冲突。"""
        task_id = task_info.task_id
        with self._lock_for(task_id):
            if task_id in self._graphs:
                raise GraphAlreadyInitializedError(f"task_id={task_id} 图已存在")
            run_id = self._next_run_id()
            # 任务从建图开始计时。根节点仍保持 PENDING,但其 start_time 代表
            # 任务创建/执行图初始化时间,不能等到后续进入 RUNNING 才补写。
            started_at = int(time.time() * 1000)
            root = TaskNode(
                node_id=task_id,
                task_id=task_id,
                status=Status.PENDING,
                task_spec=task_info.task_spec,
                run_info=RuntimeInfo(start_time=started_at),
                node_run_graph=None,  # type: ignore[arg-type]  回填见下
            )
            graph = TaskExecutionGraph(
                run_id=run_id,
                loop_round=0,
                status=Status.RUNNING,
                tasks=[root],
                relations=[],
                task_id=task_id,
            )
            root.node_run_graph = graph  # 回填循环引用(in-memory)
            graph.extend_props["execution_config"] = dict(task_info.execution_config)
            graph.extend_props["runtime_profile"] = (
                TaskRuntimeProfile.from_execution_config(
                    task_info.execution_config
                ).to_dict()
            )
            graph.extend_props["source_type"] = task_info.source_type
            graph.extend_props["owner_bot_id"] = task_info.owner_bot_id
            graph.extend_props["owner_user_id"] = task_info.owner_user_id
            graph.extend_props["gaps"] = []
            self._graphs[task_id] = graph
            if self._graph_repo is not None:
                self._graph_versions[task_id] = self._graph_repo.create_graph(
                    graph, runtime_status=Status.PENDING
                )
            return graph

    def add_task_nodes(
        self,
        tasks: list[TaskNode],
        parent_node_id: str,
        *,
        attach_dependency: bool = True,
        mark_parent_planning: bool = True,
    ) -> TaskExecutionGraph:
        """并子图(单写 relations 分解树)。触发条件 a/b/c 由编排核判后调,本方法双检:
        a. 只有一个根节点且 status=PENDING(初始规划);
        b. 存在 FAILED 节点且 acceptance_result.gaps 非空的叶子(补救);
        c. 存在 PLANNING 节点 且 无 RUNNING(下一层规划)。
        登记分解树:每新子挂 ``parent_node_id`` 下写入 DEPENDENCY 边(src=parent,dst=新子,单入);
        默认将 parent 置为 PLANNING(委托态)。Relay 串行接力可关闭该行为，
        由调用方先将已交接节点置为 DONE；BBS attach 可关闭该行为,待 scoped 节点
        SUCCESS 回投后再由编排核将根节点置为 PLANNING。单层同构护栏:本批 node_id 不重复、不与已存重复、本批内不互父子。
        """
        if not tasks:
            raise GraphIntegrityError("add_task_nodes: tasks 不能为空")
        task_id = tasks[0].task_id
        if any(t.task_id != task_id for t in tasks):
            raise GraphIntegrityError("add_task_nodes: 同批 task_id 不一致")

        def mutation(graph):
            parent = self._require_node(graph, parent_node_id)
            self._assert_add_trigger(graph, parent_node_id=parent_node_id)
            relay_parent_done = (
                graph.extend_props.get("execution_config", {}) or {}
            ).get("orchestration_mode") == "relay" and parent.status == Status.DONE
            if parent.status not in _DELEGATABLE_PARENT and not relay_parent_done:
                raise GraphIntegrityError(
                    f"add_task_nodes: parent={parent_node_id} 状态={parent.status} 不可委托"
                )
            existing_ids = {n.node_id for n in graph.tasks}
            new_ids = [t.node_id for t in tasks]
            if len(set(new_ids)) != len(new_ids):
                raise GraphIntegrityError("add_task_nodes: 本批 node_id 重复")
            duplicated = existing_ids & set(new_ids)
            if duplicated:
                raise GraphIntegrityError(f"add_task_nodes: 节点已存在 {duplicated}")
            for t in tasks:
                graph.tasks.append(t)
                t.node_run_graph = graph
                if attach_dependency:
                    graph.relations.append(
                        Relation(
                            src_id=parent_node_id,
                            dst_id=t.node_id,
                            type=RelationType.DEPENDENCY,
                        )
                    )
            if mark_parent_planning and parent.status != Status.PLANNING:
                parent.status = Status.PLANNING
            return graph, None, True

        return self._mutate_with_version_retry(task_id, mutation)

    def add_relations(
        self, task_id: str, edges: list[tuple[str, str]]
    ) -> TaskExecutionGraph:
        """追写 DEPENDENCY 结构边(仅作用于已存在节点)。

        静态 plan DAG 多入合并点用:``add_task_nodes`` 只能写单条 ``parent->child`` 结构边,
        四路合并(如 strategy_approval 依赖 risk/marketing/crowd/product)的其余入边由本方法补齐,
        使 relations 分解树之外的多入依赖在 dashboard 上可渲染为 DAG 合并点。

        约束:
        - 端点必须已入图(节点已存在);自环/空边拒绝;
        - 仅静态 plan 调用,不走动态规划触发校验(a/b/c/d),不改任何节点状态;
        - 同向边去重,幂等。
        """
        if not edges:
            return self._require_graph(task_id)

        def mutation(graph):
            existing_ids = {n.node_id for n in graph.tasks}
            existing_edges = {
                (r.src_id, r.dst_id)
                for r in graph.relations
                if r.type == RelationType.DEPENDENCY
            }
            added = 0
            for src, dst in edges:
                if src == dst:
                    raise GraphIntegrityError(f"add_relations: 自环禁止 {src}")
                if src not in existing_ids or dst not in existing_ids:
                    raise GraphIntegrityError(f"add_relations: 端点未入图 {src}->{dst}")
                if (src, dst) in existing_edges:
                    continue
                graph.relations.append(
                    Relation(src_id=src, dst_id=dst, type=RelationType.DEPENDENCY)
                )
                existing_edges.add((src, dst))
                added += 1
            _LOG.info(
                "[task][graph] add_relations task=%s requested=%s added=%s",
                task_id,
                len(edges),
                added,
            )
            return graph, None, added > 0

        return self._mutate_with_version_retry(task_id, mutation)

    def _assert_add_trigger(
        self, graph: TaskExecutionGraph, *, parent_node_id: str | None = None
    ) -> None:
        # 根节点由 graph.task_id 唯一标识，不能依赖 graph.tasks 的列表顺序。
        root = next(
            (node for node in graph.tasks if node.node_id == graph.task_id), None
        )
        cond_a = (
            len(graph.tasks) == 1
            and root is not None
            and root.status == Status.PENDING
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
            n.status == Status.PENDING and not self._has_child(graph, n.node_id)
            for n in graph.tasks
        )

        cond_e = root is not None and root.status == Status.HUNG
        relay_mode = (graph.extend_props.get("execution_config", {}) or {}).get(
            "orchestration_mode"
        ) == "relay"
        relay_parent_done = (
            relay_mode
            and parent_node_id is not None
            and any(
                node.node_id == parent_node_id and node.status == Status.DONE
                for node in graph.tasks
            )
        )

        if not (cond_a or cond_b or cond_c or cond_d or cond_e or relay_parent_done):
            raise GraphIntegrityError("add_task_nodes: 触发条件 a/b/c/d/e/f 均不满足")

    async def on_start(self, patch: TaskNodePatch) -> NodeOpResult:
        """Apply an executor start fact through the unified graph service."""
        return await task_graph_support.apply_start_fact(self, patch)

    def report(self, data: TaskCallbackData) -> Any:
        return task_graph_support.report(self, data)

    def _dispatch_report(self, data: TaskCallbackData) -> Any:
        return task_graph_support._dispatch_report(self, data)

    def update_task_node_info(self, patch: TaskNodePatch) -> NodeOpResult:
        """节点级原子状态流转网关。双模式:
        ① acceptance_result 驱动(skill 回投):PASS→SUCCESS / FAIL→HUNG(动态,记录未通过验收)或 DONE(外部托管);
           FAILED 仅表示执行失败(exec_error);
        ② status 直驱(框架内部):PENDING→RUNNING(派发) / RUNNING→PENDING(Harness 复位) /
           DONE(执行完成但未验收) / SUCCESS(框架确认验收通过) / PLANNING→SUCCESS(传播)。
           两者都校验状态机。无 acceptance_result 且无 status 只 fold 不翻态。
        派发写:patch.run_mode(str)/assignee 落库 + 置 RUNNING。
        """

        def mutation(graph):
            node = self._require_node(graph, patch.node_id)
            prev_status = node.status
            relay_mode = (
                graph.extend_props.get("execution_config", {}) or {}
            ).get("orchestration_mode") == "relay"
            if relay_mode:
                # Relay is baton-owned, not tree-aggregated. Once a node has
                # handed off a successor, a later baton must not rewrite it.
                has_successor = any(
                    relation.src_id == patch.node_id
                    and relation.type == RelationType.DEPENDENCY
                    for relation in graph.relations
                )
                if has_successor and (
                    patch.status is not None or patch.acceptance_result is not None
                ):
                    raise TaskStateError(
                        "relay successor node is immutable: "
                        f"task={patch.task_id} node={patch.node_id}"
                    )
                if node.status in _TERMINAL_STATUSES and patch.status is not None:
                    raise TaskStateError(
                        "relay terminal node status is immutable: "
                        f"task={patch.task_id} node={patch.node_id} status={node.status}"
                    )
            new_status: Status | None = None
            if patch.acceptance_result is not None:
                if node.status in _TERMINAL_STATUSES:
                    raise TaskStateError(
                        f"验收回投到已终态节点: {node.status}(task={patch.task_id} node={patch.node_id})"
                    )
                verdict = patch.acceptance_result.verdict
                if verdict == AcceptanceVerdict.DONE:
                    new_status = Status.SUCCESS
                else:
                    # 动态编排核通过 patch.status=HUNG 表示验收失败需要升级 BBS;
                    # 外部托管调用方保留 DONE,仅记录验收结论和 gaps。
                    new_status = (
                        Status.HUNG if patch.status == Status.HUNG else Status.DONE
                    )
                node.run_info.acceptance_result = patch.acceptance_result
            elif patch.exec_error is not None:
                if patch.extend_props_patch is not None:
                    node.run_info.extend_props.update(patch.extend_props_patch)
                return (
                    NodeOpResult(
                        task_id=patch.task_id,
                        node_id=patch.node_id,
                        success=True,
                        prev_status=prev_status,
                        new_status=node.status,
                    ),
                    None,
                    True,
                )
            elif patch.status is not None:
                new_status = patch.status
                allowed = _DIRECT_TRANSITIONS.get(node.status, set())
                if new_status not in allowed:
                    _LOG.warning(f"status 直驱非法: {node.status} → {new_status}")
            if patch.start_time is not None:
                node.run_info.start_time = patch.start_time
                node.run_info.end_time = None
            if patch.actual_goal is not None:
                node.run_info.actual_goal = patch.actual_goal
            if patch.local_acceptance_result is not None:
                node.run_info.acceptance_result = patch.local_acceptance_result
            if patch.execution_decision is not None:
                decision = patch.execution_decision.strip().upper()
                if decision not in {"ACCEPTED", "DECLINED"}:
                    raise TaskStateError(
                        "execution_decision must be ACCEPTED or DECLINED"
                    )
                node.run_info.extend_props["execution_decision"] = decision
            if patch.output_patch is not None:
                node.run_info.output.update(patch.output_patch)
            if patch.run_mode is not None:
                requested_mode = patch.run_mode or None
                profile = graph.extend_props.get("runtime_profile") or {}
                allowed_modes = profile.get("allowed_run_modes") or [
                    "single_bot",
                    "coop_group",
                    "bbs",
                ]
                if requested_mode is not None and requested_mode not in allowed_modes:
                    raise TaskStateError(
                        f"run_mode={requested_mode} is not allowed by frozen runtime profile"
                    )
                node.run_info.run_mode = requested_mode
            if patch.assignee is not None:
                node.run_info.assignee = patch.assignee or None
            if patch.progress_reason is not None:
                node.run_info.progress_reason = patch.progress_reason or None
            if patch.failure_reason is not None:
                node.run_info.failure_reason = patch.failure_reason or None
            if patch.extend_props_patch is not None:
                node.run_info.extend_props.update(patch.extend_props_patch)
            if (
                new_status == Status.RUNNING
                and prev_status != Status.RUNNING
                and node.run_info.start_time is None
            ):
                node.run_info.start_time = int(time.time() * 1000)
                node.run_info.end_time = None
            elif new_status in {
                Status.DONE,
                Status.SUCCESS,
                Status.FAILED,
                Status.HUNG,
            }:
                node.run_info.end_time = int(time.time() * 1000)
            elif new_status == Status.PENDING:
                # Harness retries must not erase the node's first dispatch
                # timestamp. Keep start_time for truthful total elapsed time;
                # end_time is cleared until the next terminal transition.
                node.run_info.end_time = None
            if new_status is not None:
                node.status = new_status
            return (
                NodeOpResult(
                    task_id=patch.task_id,
                    node_id=patch.node_id,
                    success=True,
                    prev_status=prev_status,
                    new_status=node.status,
                ),
                None,
                True,
            )

        return self._mutate_with_version_retry(patch.task_id, mutation)

    def append_action_event(
        self,
        task_id: str,
        node_id: str,
        action: NodeAction,
        payload: dict[str, Any],
        *,
        attempt: int = 0,
        status_from: Status | None = None,
        status_to: Status | None = None,
    ) -> None:
        """节点级动作历史快照追加口(append-only;纯可观测,不入状态机)。

        每次版本冲突都在最新 graph 上重新计算 action seq 并重建事件,避免
        复用旧 seq 导致跨实例动作历史重复。
        """

        def mutation(graph):
            node = self._require_node(graph, node_id)
            event_payload = dict(payload)
            event_payload.setdefault("__node_id", node_id)
            next_seq = len(node.run_info.action_log) + 1
            if self._graph_repo is not None:
                next_seq = max(
                    next_seq, self._graph_repo.next_action_seq(task_id, node_id)
                )
            event = NodeActionEvent(
                seq=next_seq,
                ts=int(time.time() * 1000),
                action=action,
                loop_round=graph.loop_round,
                attempt=attempt,
                status_from=status_from,
                status_to=status_to,
                payload=event_payload,
            )
            node.run_info.action_log.append(event)
            return None, [event], True

        self._mutate_with_version_retry(task_id, mutation)

    def update_task_graph_info(
        self, task_id: str, patch: TaskGraphPatch
    ) -> TaskExecutionGraph:
        """图级原子写口,以可重放 patch 处理跨实例版本冲突。"""

        def mutation(graph):
            if patch.loop_round_increment is not None:
                graph.loop_round += patch.loop_round_increment
            if patch.status is not None:
                graph.status = patch.status
            if patch.output_patch is not None:
                graph.output.update(patch.output_patch)
            if patch.extend_props_patch is not None:
                graph.extend_props.update(patch.extend_props_patch)
            return graph, None, True

        return self._mutate_with_version_retry(task_id, mutation)

    def delete_task_node(self, task_id: str, node_id: str) -> None:
        """删除单个节点(及其 DEPENDENCY 后代子树 + 相关边)。根(``task_id``)永不可删。

        用于 ``on_bbs_report`` 收到 verdict=FAILED:逻辑删除本次接力尝试的 scoped 节点
        (不物理删除 task_node；不翻 FAILED、不 fold output_patch)，图回到 root PLANNING + bbs_mode 可恢复态等下段
        重新 claim/attach。逻辑删除会保留节点行、运行记录和审计历史；bbs scoped 节点是叶子,但实现按子树标记
        (节点 + DEPENDENCY 后代)以通用。锁:再取同 task 的 RLock(re-entrant 安全,调用方通常已持)。
        """
        with self._lock_for(task_id):
            graph = self._require_graph(task_id)
            if node_id == task_id:
                raise TaskStateError(f"delete_task_node: 根节点不可删 task={task_id}")
            if not any(n.node_id == node_id for n in graph.tasks):
                raise NodeNotFoundError(
                    f"delete_task_node: node_id={node_id} 不存在于 task={task_id}"
                )
            children: dict[str, list[str]] = {}
            for rel in graph.relations:
                if rel.type == RelationType.DEPENDENCY:
                    children.setdefault(rel.src_id, []).append(rel.dst_id)
            prune: set[str] = set()
            stack = [node_id]
            while stack:
                nid = stack.pop()
                if nid in prune or nid == task_id:
                    continue
                prune.add(nid)
                for child in children.get(nid, []):
                    if child not in prune:
                        stack.append(child)
            graph.tasks = [n for n in graph.tasks if n.node_id not in prune]
            graph.relations = [
                r
                for r in graph.relations
                if r.src_id not in prune and r.dst_id not in prune
            ]
            self._persist_locked(graph)

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
                raise TaskStateError(
                    f"attach_bbs_node: BBS relay 深度达上限 task={task_id}"
                )
            node_id = f"bbs-{uuid.uuid4().hex[:8]}"
            claim_at = root.run_info.extend_props.get("bbs_claim_at") or int(
                time.time() * 1000
            )
            node = TaskNode(
                node_id=node_id,
                task_id=task_id,
                status=Status.PENDING,
                task_spec=task_spec,
                run_info=RuntimeInfo(
                    run_mode="bbs",
                    assignee=bot_id,
                    start_time=int(claim_at),
                    extend_props={"bbs_claim_at": int(claim_at)},
                ),
                node_run_graph=graph,
            )
            self.add_task_nodes(
                [node], parent_node_id=parent_node_id, mark_parent_planning=False
            )  # BBS 挂载只建 scoped 节点,根在 SUCCESS 回投后再→PLANNING
            self.update_task_node_info(
                TaskNodePatch(task_id=task_id, node_id=node_id, status=Status.RUNNING)
            )  # create+start:PENDING→RUNNING 是 _DIRECT_TRANSITIONS 合法翻
            self.update_task_graph_info(
                task_id,
                TaskGraphPatch(extend_props_patch={"bbs_relay_count": relay_count + 1}),
            )
            return node

    def claim_bbs_owner(
        self,
        task_id: str,
        bot_id: str,
        *,
        node_id: str | None = None,
        claim_id: str | None = None,
    ) -> NodeOpResult:
        return task_graph_support.claim_bbs_owner(
            self, task_id, bot_id, node_id=node_id, claim_id=claim_id
        )

    def apply_relay_plan_result(
        self,
        *,
        task_id: str,
        origin_node_id: str,
        gaps: list[str],
        next_task_spec: TaskSpec | None,
        holder_id: str,
        max_rounds: int,
    ) -> dict[str, Any]:
        return task_graph_support.apply_relay_plan_result(
            self,
            task_id=task_id,
            origin_node_id=origin_node_id,
            gaps=gaps,
            next_task_spec=next_task_spec,
            holder_id=holder_id,
            max_rounds=max_rounds,
        )

    def get_task_context(self, task_id: str) -> TaskContext:
        return task_graph_support.get_task_context(self, task_id)

    def load_action_logs(self, graph: TaskExecutionGraph, *, limit: int = 200) -> None:
        task_graph_support.load_action_logs(self, graph, limit=limit)

    def query_task_dashboard(
        self, task_id: str, node_id: str | None = None
    ) -> TaskExecutionGraph:
        return task_graph_support.query_task_dashboard(self, task_id, node_id)

    def effective_graph_status(self, task_id: str) -> Status:
        return task_graph_support.effective_graph_status(self, task_id)

    # ===== 派生只读查询(均从 relations 分解树派生)=====
    def query_task_nodes(
        self, task_id: str, criteria: TaskNodeQueryCriteria
    ) -> list[TaskNode]:
        return task_graph_support.query_task_nodes(self, task_id, criteria)

    def get_child_tasks(self, task_id: str, parent_node_id: str) -> list[TaskNode]:
        return task_graph_support.get_child_tasks(self, task_id, parent_node_id)

    def get_parent_task(self, task_id: str, child_node_id: str) -> TaskNode | None:
        return task_graph_support.get_parent_task(self, task_id, child_node_id)

    # v4:remove_subtree 已删——升 BBS 不再删子树,HUNG 节点保留在图里,靠终态传播(子含 HUNG→父 HUNG)
    # 冒泡驱动收敛。dashboard 子树投影仍用 _collect_subtree。

    def list_task_summaries(self, status: Status | None = None) -> list[TaskSummary]:
        return task_graph_support.list_task_summaries(self, status)

    def list_bbs_tasks_overview(
        self,
        page: int = 1,
        page_size: int = 20,
        *,
        search_word: str | None = None,
        status: str | None = None,
    ) -> tuple[list[BbsTaskOverviewRecord], int]:
        return task_graph_support.list_bbs_tasks_overview(
            self, page, page_size, search_word=search_word, status=status
        )

    def _node_depth(self, task_id: str, node_id: str) -> int:
        return task_graph_support._node_depth(self, task_id, node_id)

    def _execution_config(self, task_id: str) -> dict[str, Any]:
        return task_graph_support._execution_config(self, task_id)
