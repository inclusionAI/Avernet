"""Real TaskService — unified task authority (Phase 2, plan §2.1-§2.4).

One service carries the query face, the intake face, the event-fold/guard face,
and the secondary-panel (副屏) read face. State is mutated ONLY through
``on_event`` / ``claim_node`` / the intake helpers, each of which:

1. loads the aggregate (event log is source of truth; repo is a materialized
   fold for fast reads),
2. consults the :mod:`state_machine` guard before any phase/node move,
3. appends a :class:`TaskEvent` (the single writer assigns the monotonic
   ``seq``), then
4. re-saves the aggregate snapshot.

``create`` additionally publishes a :class:`PanelMessage` so the副屏 pops the
overall task execution DAG at task creation (FR-OBS-11, mirroring BCS
``publish_state_machine_panel_event``). The publisher is an injected Port so the
service stays free of frontend-channel mechanics.

Avernet rules: ``from __future__ import annotations`` first; ``Optional[T]``
not ``T | None``; required non-optional; ``@inject`` constructor injection.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from injector import inject

from agentclaw.community.core.task.protocols import (
    BcsCollaborationProtocol,
    DispatchResult,
    PanelEventPublisher,
    PanelMessage,
)
from agentclaw.community.core.task.domain.events import (
    EventKind,
    TaskEvent,
    next_seq,
)
from agentclaw.community.core.task.domain.models import (
    AttemptOutcome,
    AttemptTrigger,
    Edge,
    EdgeKind,
    GraphStatus,
    Node,
    NodeStatus,
    NodeType,
    Plan,
    RouteClass,
    RunMode,
    SubDagRef,
    SubTaskSpec,
    SubtaskState,
    Task,
    TaskExecutionGraph,
    TaskSource,
    TaskSpec,
    TaskSpecMetadata,
    TaskState,
    TaskStatus,
)
from agentclaw.community.core.task.domain.repository import (
    TaskEventRepo,
    TaskNotFoundError,
    TaskRepo,
)
from agentclaw.community.core.task.domain.state_machine import (
    IllegalTransitionError,
    require_graph_transition,
    require_node_transition,
    require_task_transition,
)
from agentclaw.community.core.task.services.graph_state_ops import GraphStateOpsMixin
from agentclaw.community.log import get_logger

logger = get_logger()


# --- helpers ----------------------------------------------------------------


def _new_task_id() -> str:
    return "task-" + uuid.uuid4().hex[:16]


def _new_accept_token() -> str:
    return "tok-" + uuid.uuid4().hex[:12]


def _coerce_status(value: Any, enum_cls: type) -> Any:
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value))
    except ValueError:
        return None


# --- service ----------------------------------------------------------------


class TaskService(GraphStateOpsMixin):
    """Unified task authority (plan §2.1). Holds NO编排 decision."""

    @inject
    def __init__(
        self,
        task_repo: TaskRepo,
        event_repo: TaskEventRepo,
        panel_publisher: PanelEventPublisher,
        bcs_collab: Optional[BcsCollaborationProtocol] = None,
    ) -> None:
        self._task_repo = task_repo
        self._event_repo = event_repo
        self._panel_publisher = panel_publisher
        self._bcs_collab = bcs_collab
        # lazy: adapter built only when a sub-dag drill-down is first requested
        self._graph_adapter: Any = None

    # --- intake face --------------------------------------------------------

    def create(
        self,
        title: str,
        source: str = "api",
        background: str = "",
        user_id: str = "",
    ) -> Task:
        """Create a Task at DRAFTING, init the root phase, emit TASK_CREATED,
        and publish a副屏 panel message so the dynamic DAG pops at creation
        (FR-OBS-11)."""
        source_enum = (
            TaskSource(source)
            if source in {e.value for e in TaskSource}
            else TaskSource.API
        )
        task_id = _new_task_id()
        graph = TaskExecutionGraph(root_phase=TaskStatus.DRAFTING)
        task = Task(
            id=task_id,
            user_id=user_id,
            source=source_enum,
            spec=TaskSpec(
                metadata=TaskSpecMetadata(id=task_id, title=title),
            ),
            execution_graph=graph,
        )
        if background:
            task.spec.context.background = background
        self._task_repo.save(task)
        self._emit(
            task,
            EventKind.TASK_CREATED,
            title=title,
            source=source_enum.value,
        )
        logger.info(
            "[Task] task=%s create source=%s title=%r status=drafting seq=1",
            task_id, source_enum.value, title,
        )
        # ★ FR-OBS-11: popup the task-entry dynamic-workflow canvas on create.
        self._panel_publisher.publish(
            PanelMessage(
                component="taskPanel.TaskWorkflowView",
                params={"task_id": task_id},
            )
        )
        return self._task_repo.get_by_id(task_id)

    def clarify(self, task_id: str, patch: dict) -> Optional[Task]:
        task = self._load(task_id)
        if task is None:
            return None
        self._apply_spec_patch(task, patch)
        # clarify does NOT transition (spec R2): the task stays DRAFTING through
        # the entire element-completion phase until finalize_plan → DEFINED.
        self._emit(task, EventKind.SPEC_AMENDED, patch=patch)
        self._task_repo.save(task)
        return self._task_repo.get_by_id(task_id)

    def finalize_plan(self, task_id: str, plan: Plan) -> Optional[Task]:
        # Accept a dict plan_payload (HTTP /plan endpoint) by coercing it into a
        # Plan via the same _plan_from_dict used by clarify; e2e callers pass a
        # Plan object unchanged. Phase 6.9 smoke gap ② fix.
        if isinstance(plan, dict):
            plan = self._plan_from_dict(plan)
        task = self._load(task_id)
        if task is None:
            return None
        # Plan freeze is legal from DRAFTING → DEFINED (spec R2/R3). Re-plan from a
# later phase is not allowed; clarify+refinalize must restart at DRAFTING.
        if task.status not in {TaskStatus.DRAFTING}:
            raise IllegalTransitionError(
                f"finalize_plan illegal from {task.status.value}"
            )
        task.plan = plan
        self._advance_phase(task, TaskStatus.DEFINED)
        self._emit(
            task,
            EventKind.PLAN_FINALIZED,
            node_count=len(plan.sub_tasks),
            confidence=float(plan.confidence),
        )
        logger.info(
            "[Task] task=%s finalize_plan nodes=%d confidence=%.2f → defined",
            task_id, len(plan.sub_tasks), float(plan.confidence),
        )
        self._task_repo.save(task)
        return self._task_repo.get_by_id(task_id)

    def cancel(self, task_id: str, by: str = "user", reason: str = "") -> Optional[Task]:
        task = self._load(task_id)
        if task is None:
            return None
        self._advance_phase(task, TaskStatus.CANCELLED)
        self._emit(task, EventKind.CANCELLED, by=by, reason=reason)
        self._task_repo.save(task)
        return self._task_repo.get_by_id(task_id)

    # --- event-fold / guard face -------------------------------------------

    def on_event(self, event: Any) -> Optional[Task]:
        """Fold an external event (router ``/events`` or Scheduler/owner-bot
        回投) through the guard into the aggregate AND append it to the event
        log (the single-writer assigns ``seq``). ``event`` may be a
        :class:`TaskEvent` or a raw envelope dict ``{task_id, kind, payload}``."""
        task_id, kind, payload = self._unpack(event)
        task = self._load(task_id)
        if task is None:
            return None
        # Persist the event to the log first (single writer assigns seq) so the
        # log is the complete source of truth — internal emits (create/clarify)
        # and external 回投 both land here.
        log_event = TaskEvent(
            task_id=task_id,
            seq=next_seq(self._event_repo.latest_seq(task_id)),
            kind=kind,
            payload=dict(payload),
        )
        self._event_repo.append(log_event)
        task.latest_event_seq = log_event.seq
        self._apply_event(task, kind, payload)
        self._task_repo.save(task)
        logger.info(
            "[Task] task=%s on_event kind=%s seq=%d → status=%s",
            task_id, kind.value, log_event.seq, task.status.value,
        )
        return self._task_repo.get_by_id(task_id)

    def claim_node(
        self, task_id: str, node_id: str, executor_id: str
    ) -> Optional[DispatchResult]:
        """CAS: PENDING → RUNNING + assignee + record the attempt. Raises if
        the node is already claimed or terminal."""
        task = self._load(task_id)
        if task is None:
            return None
        node = self._find_node(task, node_id)
        if node is None:
            raise TaskNotFoundError(f"node {node_id} not in task {task_id}")
        require_node_transition(node.status, NodeStatus.RUNNING)
        node.status = NodeStatus.RUNNING
        node.assignee = executor_id
        node.run_mode = node.run_mode or RunMode.SINGLE_BOT
        node.attempted_executors.append(
            self._attempt_record(executor_id, node)
        )
        self._emit(
            task,
            EventKind.NODE_RUNNING,
            node_id=node_id,
            from_status=NodeStatus.PENDING.value,
        )
        token = _new_accept_token()
        self._task_repo.save(task)
        logger.info(
            "[Task] task=%s claim_node node=%s → running executor=%s run_mode=%s",
            task_id, node_id, executor_id, node.run_mode.value,
        )
        return DispatchResult(
            node_id=node_id,
            executor_id=executor_id,
            run_mode=node.run_mode,
            accept_token=token,
        )

    # --- query face --------------------------------------------------------

    def get(self, task_id: str) -> Optional[Task]:
        return self._load(task_id)

    def history(self, task_id: str, after_seq: int = 0) -> list[TaskEvent]:
        """Return the append-only event log in seq order (the authoritative
        execution trace). ``after_seq`` for incremental follow."""
        return self._event_repo.load_events(task_id, after_seq=after_seq)

    def list_by_user(self, user_id: str, limit: int = 50) -> list[Task]:
        tasks = self._task_repo.list_by_user(user_id)
        return tasks[:limit]

    def progress(self, task_id: str) -> dict:
        task = self._load(task_id)
        if task is None:
            return {}
        graph = task.execution_graph
        # 进度只计执行子任务;recognition/clarify/execute_start 是历史规划脚手架(落图即
        # DONE),不计入进度分子,免得 3 个 DONE 规划节点虚增 done/total(§2.2)。
        nodes = [
            n for n in (graph.nodes if graph else [])
            if n.node_type not in (
                NodeType.RECOGNITION, NodeType.CLARIFY, NodeType.EXECUTE_START,
            )
        ]
        done = sum(1 for n in nodes if n.status == NodeStatus.DONE)
        return {
            "task_id": task_id,
            "status": task.status.value,
            "loop_round": task.loop_round,
            "done": done,
            "total": len(nodes),
            "nodes": [
                {
                    "node_id": n.node_id,
                    "seq": i + 1,
                    "way": (n.run_mode.value if n.run_mode else ""),
                    "status": n.status.value,
                    "external": n.sub_dag is not None,
                }
                for i, n in enumerate(nodes)
            ],
        }

    # --- secondary-panel (副屏) read face (plan §1.4b) ---------------------

    def get_task_graph(self, task_id: str) -> Optional[dict]:
        """Project the domain ``TaskExecutionGraph`` into a ``TaskGraphView``
        snapshot (顶层动态 DAG). Field superset per §1.3b — covers the
        state_machine canvas fields and adds task-graph-only dimensions."""
        task = self._load(task_id)
        if task is None or task.execution_graph is None:
            logger.debug(
                "[Task] get_task_graph miss task_id=%s (task_found=%s, has_execution_graph=%s)",
                task_id,
                task is not None,
                bool(task and task.execution_graph),
            )
            return None
        g = task.execution_graph
        return {
            "task_id": task_id,
            "root_phase": g.root_phase.value,
            "graph_status": g.graph_status.value,
            "loop_round": g.loop_round,
            "definition_meta": self._definition_meta(task),
            "nodes": [self._node_view(n) for n in g.nodes],
            "edges": [self._edge_view(e) for e in g.edges],
        }

    def get_node_detail(self, task_id: str, node_id: str) -> Optional[dict]:
        task = self._load(task_id)
        if task is None:
            return None
        node = self._find_node(task, node_id)
        if node is None:
            return None
        rec = node.attempted_executors[-1] if node.attempted_executors else None
        return {
            "node_id": node.node_id,
            "display_name": node.spec,
            "status": node.status.value,
            "sub_status": (node.properties.get("sub_status") or "idle"),
            "attempt": (rec.round if rec else 0),
            "assignee": node.assignee or "",
            "run_mode": (node.run_mode.value if node.run_mode else None),
            "collab_mode": (node.properties.get("collab_mode")),
            "started_at": (rec.at if rec else None),
            "completed_at": (node.properties.get("completed_at")),
            "is_final_output": bool(node.properties.get("is_final_output", False)),
            "attempted_executors": [self._attempt_view(a) for a in node.attempted_executors],
            "artifacts": [
                {"name": a.name, "location": a.location, "type": a.type}
                for a in node.artifacts
            ],
            "acceptance_result": node.properties.get("acceptance_result"),
            "targets_acceptance": [
                {"kind": c.kind.value, "properties": c.properties}
                for c in node.targets_acceptance
            ],
            "instruction": node.instruction,
            "sub_dag_ref": self._sub_dag_ref_view(node.sub_dag),
            "properties": dict(node.properties),
        }

    def get_sub_dag(self, task_id: str, node_id: str) -> Optional[dict]:
        """Cooperative-group drill-down (路 A, plan §1.3a). Returns None when
        the node has no ``SubDagRef`` (router → 404) or the live fetch is empty.
        Phase 4: fetches the live BCS SM run graph via :class:`SmGraphAdapter`
        and maps it into a ``TaskGraphView`` subtree (§1.3b/§1.3c). Falls back
        to a self-describing stub when no BCS collaboration Port is wired."""
        task = self._load(task_id)
        if task is None:
            return None
        node = self._find_node(task, node_id)
        if node is None or node.sub_dag is None:
            return None
        ref = node.sub_dag
        adapter = self._graph_adapter_of()
        if adapter is None:
            # No BCS Port wired (e.g. isolated unit tests) → self-describing stub.
            return self._stub_sub_dag(task_id, ref)
        return adapter.fetch_sub_dag_view(task_id, node_id, ref)

    def _graph_adapter_of(self) -> Any:
        if self._graph_adapter is not None:
            return self._graph_adapter
        if self._bcs_collab is None:
            return None
        from agentclaw.community.core.task.services.graph_adapter import (
            SmGraphAdapter,
        )

        self._graph_adapter = SmGraphAdapter(self._bcs_collab)
        return self._graph_adapter

    @staticmethod
    def _stub_sub_dag(task_id: str, ref: SubDagRef) -> dict:
        return {
            "task_id": task_id,
            "root_phase": "executing",
            "graph_status": "on_plaza",
            "loop_round": 0,
            "definition_meta": {
                "ref_kind": ref.ref_kind,
                "bcs_run_id": ref.bcs_run_id,
                "group_id": ref.group_id,
                "drill_down_live": False,
            },
            "nodes": [
                {
                    "node_id": ref.bcs_run_id,
                    "display_name": f"coop-group {ref.group_id}",
                    "status": "running",
                    "run_mode": "coop_group",
                    "sub_dag_ref": {
                        "ref_kind": ref.ref_kind,
                        "bcs_run_id": ref.bcs_run_id,
                        "group_id": ref.group_id,
                    },
                }
            ],
            "edges": [],
        }

    async def subscribe_task_graph(self, task_id: str):
        """Incremental TaskGraphView push over WS. Phase 2 = snapshot-then-close
        skeleton; Phase 4 wires EventBus-driven增量 pushes."""
        snap = self.get_task_graph(task_id)
        if snap is not None:
            yield snap

    # --- internal: load / emit --------------------------------------------

    def _load(self, task_id: str) -> Optional[Task]:
        try:
            task = self._task_repo.get_by_id(task_id)
        except TaskNotFoundError:
            logger.debug("[Task] _load miss task_id=%s (TaskNotFoundError)", task_id)
            return None
        if task is None:
            logger.debug("[Task] _load miss task_id=%s (repo returned None)", task_id)
        return task

    def _emit(self, task: Task, kind: EventKind, **fields: Any) -> TaskEvent:
        seq = next_seq(self._event_repo.latest_seq(task.id))
        event = TaskEvent(task_id=task.id, seq=seq, kind=kind, payload={**fields})
        return self._event_repo.append(event)

    def _apply_event(
        self, task: Task, kind: EventKind, payload: dict
    ) -> None:
        """Guard + fold one event into the aggregate (no save; caller saves)."""
        graph = task.execution_graph
        node_id = payload.get("node_id") or ""
        if kind == EventKind.TASK_CREATED:
            if graph is None:
                task.execution_graph = TaskExecutionGraph(root_phase=TaskStatus.DRAFTING)
            return
        if graph is None:
            return
        if kind == EventKind.SPEC_AMENDED:
            self._apply_spec_patch(task, payload.get("patch") or {})
            # clarify does NOT transition (spec R2): task stays DRAFTING.
            return
        if kind == EventKind.PLAN_FINALIZED:
            # Plan already set by finalize_plan; just ensure phase.
            return
        if kind == EventKind.NODE_DISPATCHED:
            node = self._find_node(task, node_id)
            if node is not None:
                node.run_mode = _coerce_status(payload.get("run_mode"), RunMode) or node.run_mode
                node.assignee = payload.get("executor_id") or node.assignee
            return
        if kind == EventKind.NODE_RUNNING:
            node = self._find_node(task, node_id)
            if node is not None:
                require_node_transition(node.status, NodeStatus.RUNNING)
                node.status = NodeStatus.RUNNING
            self._sync_subtask_status(task, node_id, NodeStatus.RUNNING)
            return
        if kind == EventKind.NODE_ACCEPTED:
            node = self._find_node(task, node_id)
            if node is not None:
                require_node_transition(node.status, NodeStatus.DONE)
                node.status = NodeStatus.DONE
                node.properties["acceptance_result"] = "pass"
            self._sync_subtask_status(task, node_id, NodeStatus.DONE)
            return
        if kind == EventKind.NODE_REJECTED:
            node = self._find_node(task, node_id)
            if node is not None:
                require_node_transition(node.status, NodeStatus.FAILED)
                node.status = NodeStatus.FAILED
                node.properties["acceptance_result"] = "fail"
            self._sync_subtask_status(task, node_id, NodeStatus.FAILED)
            return
        if kind == EventKind.NODE_FAILED:
            node = self._find_node(task, node_id)
            if node is not None:
                require_node_transition(node.status, NodeStatus.FAILED)
                node.status = NodeStatus.FAILED
            self._sync_subtask_status(task, node_id, NodeStatus.FAILED)
            return
        if kind == EventKind.LOOP_REROUTED:
            task.loop_round += 1
            if graph is not None:
                graph.loop_round = task.loop_round
            return
        if kind == EventKind.EXECUTION_ATTEMPTED:
            node = self._find_node(task, node_id)
            if node is not None:
                node.attempted_executors.append(
                    self._attempt_record(
                        payload.get("executor_id") or "",
                        node,
                        round_=int(payload.get("round") or 1),
                        paradigm=_coerce_status(payload.get("paradigm"), RunMode),
                        route_class=_coerce_status(payload.get("route_class"), RouteClass),
                        trigger=_coerce_status(payload.get("trigger"), AttemptTrigger),
                        outcome=_coerce_status(payload.get("outcome"), AttemptOutcome),
                    )
                )
            return
        if kind == EventKind.GOAL_VERIFIED:
            self._apply_goal_verdict(task, verdict="pass")
            return
        if kind == EventKind.GOAL_REJECTED:
            # v2 三终止(O-P2/§13):BBS 后 → FAILED 终态;BBS 前 → 回 gap 重跑(限轮次
            # 由 scheduler 守,超限 force-hang)。由 run_mode 或图含 BBS_DISPATCH 判 BBS 后。
            self._apply_goal_verdict(
                task, verdict="fail", run_mode=str(payload.get("run_mode") or "")
            )
            return
        if kind == EventKind.CANCELLED:
            self._advance_phase(task, TaskStatus.CANCELLED)
            return
        # --- v2 判定节点 fold + BBS 确认/cancel 通道(plan §5.2/§12A/§13/§18.1-10) ---
        if kind == EventKind.EXEC_AGGREGATED:
            # exec-aggregate 判验回投:verdict=pass → EXEC_AGGREGATE 节点 + SubtaskState
            # =DONE(父 subtask 闭合);verdict=fail → FAILED(回 gap 由 scheduler 续处理)。
            node = self._find_node(task, node_id)
            verdict = str(payload.get("verdict") or "pass")
            next_status = NodeStatus.DONE if verdict == "pass" else NodeStatus.FAILED
            if node is not None:
                node.properties["acceptance_result"] = verdict
                if node.status not in (NodeStatus.DONE, NodeStatus.FAILED):
                    node.status = next_status
            st = task.execution_graph.state.subtasks.get(node_id)  # type: ignore[union-attr]
            if st is not None:
                st.status = next_status
            return
        if kind == EventKind.NODE_HANG:
            # mark-hang 挂起:node → HUMAN_REQUIRED;graph ON_PLAZA → AWAITING_HUMAN_ACCEPT
            # (等人确认升 BBS / 不升)。直接 fold(与 _apply_goal_verdict 同;不经 mark_graph_status
            # 以免重读覆盖正在 fold 的 task)。
            node = self._find_node(task, node_id)
            if node is not None and node.status not in (
                NodeStatus.DONE,
                NodeStatus.FAILED,
                NodeStatus.HUMAN_REQUIRED,
            ):
                node.status = NodeStatus.HUMAN_REQUIRED
            if (
                graph is not None
                and graph.graph_status is GraphStatus.ON_PLAZA
            ):
                graph.graph_status = GraphStatus.AWAITING_HUMAN_ACCEPT
            return
        if kind == EventKind.BBS_CONFIRMED:
            # 人确认升 BBS(§13/§18.1-10,经 POST /events 回投):AWAITING_HUMAN_ACCEPT
            # → ON_PLAZA + 落 BBS_DISPATCH 节点(同图延续)。直接 append 到正在 fold 的 task,
            # 不走 add_node(内部 get_by_id+save 会覆盖本 fold)。
            if (
                graph is not None
                and graph.graph_status is GraphStatus.AWAITING_HUMAN_ACCEPT
            ):
                graph.graph_status = GraphStatus.ON_PLAZA
            if graph is not None:
                bbs_id = f"{node_id}_bbs" if node_id else "n_bbs"
                graph.nodes.append(
                    Node(
                        node_id=bbs_id,
                        spec="bbs-dispatch",
                        node_type=NodeType.BBS_DISPATCH,
                        status=NodeStatus.DONE,  # system-bridge 记录,落图即完成,不供 BbsExecutor 认领
                    )
                )
                graph.state.subtasks[bbs_id] = SubtaskState(node_id=bbs_id, status=NodeStatus.DONE)
                if node_id:
                    graph.edges.append(
                        Edge(
                            edge_id=f"e-{node_id}-{bbs_id}",
                            from_node=node_id,
                            to_node=bbs_id,
                            kind=EdgeKind.DEPENDENCY,
                        )
                    )
            return
        if kind == EventKind.HANG_CANCELLED:
            # 人确认不升 → task FAILED 终态(§13 三终止之一)。
            if task.status is TaskStatus.REVIEWING:
                self._advance_phase(task, TaskStatus.EXECUTING)
            self._advance_phase(task, TaskStatus.FAILED)
            return
        # EventKind.HUNG is retained on the enum for forward-compat of the event
        # log, but has no writer now (task-level HUNG terminal removed). Unknown
        # kinds — ignore (forward-compat).

    def _apply_goal_verdict(self, task: Task, verdict: str, run_mode: str = "") -> None:
        graph = task.execution_graph
        if graph is None:
            return
        if verdict == "pass":
            graph.graph_status = GraphStatus.VERIFIED
            self._advance_phase(task, TaskStatus.DONE)
            return
        # FAIL — v2 三终止(O-P2/§13):BBS 后 → FAILED 终态;BBS 前 → 回 gap 重跑。
        bbs_escalated = run_mode == "bbs" or any(
            n.node_type is NodeType.BBS_DISPATCH for n in graph.nodes
        )
        if bbs_escalated:
            # REVIEWING → EXECUTING → FAILED(两段合法边;BBS 后不回环/不再上升)
            if task.status is TaskStatus.REVIEWING:
                self._advance_phase(task, TaskStatus.EXECUTING)
            self._advance_phase(task, TaskStatus.FAILED)
            logger.info(
                "[TaskService] goal rejected (post-BBS) task=%s → FAILED", task.id,
            )
        else:
            # BBS 前 → 回 gap:REVIEWING → EXECUTING(重跑 loop;限轮次由 scheduler 守)
            graph.graph_status = GraphStatus.ON_PLAZA
            if task.status is TaskStatus.REVIEWING:
                self._advance_phase(task, TaskStatus.EXECUTING)
            logger.info(
                "[TaskService] goal rejected (pre-BBS) task=%s → gap loop", task.id,
            )

    # --- internal: state-group helpers (plan §2.2) ------------------------

    def _advance_phase(self, task: Task, target: TaskStatus) -> None:
        require_task_transition(task.status, target)
        # EXECUTING → EXECUTING is the loop_round++ self-edge (legal, no raise).
        task.status = target
        # keep execution_graph.root_phase in sync so a snapshot is self-describing
        if task.execution_graph is not None:
            task.execution_graph.root_phase = target

    def _apply_spec_patch(self, task: Task, patch: dict) -> None:
        meta = task.spec.metadata
        if "title" in patch and patch["title"]:
            meta.title = str(patch["title"])
        if "summary" in patch:
            meta.summary = str(patch.get("summary") or "")
        if "tags" in patch and isinstance(patch["tags"], list):
            meta.tags = list(patch["tags"])
        if "background" in patch:
            task.spec.context.background = str(patch.get("background") or "")
        # plan 只经 finalize_plan(/plan 端点)入库;clarify 只补 spec,不再回挂 plan。

    def _find_node(self, task: Task, node_id: str) -> Optional[Node]:
        if task.execution_graph is None:
            return None
        for n in task.execution_graph.nodes:
            if n.node_id == node_id:
                return n
        return None

    def _sync_subtask_status(self, task: Task, node_id: str, status: NodeStatus) -> None:
        """实体维度 SubtaskState.status 跟随动作维度 fold(NODE_RUNNING/ACCEPTED/
        REJECTED/FAILED)同步——聚合触发(_v2_detect_and_aggregate)看的是 SubtaskState
        .status,故终态 fold 必须同时落实体维度,否则叶子闭合但聚合不触发。"""
        if task.execution_graph is None:
            return
        st = task.execution_graph.state.subtasks.get(node_id)
        if st is not None:
            st.status = status

    def _attempt_record(
        self,
        executor_id: str,
        node: Node,
        round_: Optional[int] = None,
        paradigm: Optional[RunMode] = None,
        route_class: Optional[RouteClass] = None,
        trigger: Optional[AttemptTrigger] = None,
        outcome: Optional[AttemptOutcome] = None,
    ) -> Any:
        from agentclaw.community.core.task.domain.models import AttemptedRecord

        prior_round = (
            node.attempted_executors[-1].round if node.attempted_executors else 0
        )
        return AttemptedRecord(
            executor_id=executor_id,
            paradigm=paradigm or node.run_mode or RunMode.SINGLE_BOT,
            round=round_ if round_ is not None else prior_round + 1,
            route_class=route_class,
            trigger=trigger or AttemptTrigger.ROUTED,
            outcome=outcome,
        )

    # --- internal: spawn_build_dag / spawn_sub_dag (plan §2.2) -----------

    def spawn_build_dag(self, task: Task, plan: Optional[Plan] = None) -> None:
        """构建全生命周期动作节点骨架(plan §2.2 伪代码 n1..n4)并持久化。

        直接 mutate 本地图(不走 add_node,免内部重读覆盖),构建完成后 ``save`` 一次,
        调用方随后可安全 re-fetch。recognition→clarify→execute_start 规划链(skill 在
        创建/澄清/批准时已跑)落图即 DONE,不参与 tick 推进 / 不被 BbsExecutor 认领。
        有计划时 plan.sub_tasks 为 DISPATCH 节点(已搜推,直接派发);无计划时根
        BOT_SEARCH(搜推 task-spec)。"""
        if task.execution_graph is None:
            task.execution_graph = TaskExecutionGraph(root_phase=task.status)
        g = task.execution_graph
        g.nodes = []
        g.edges = []
        g.state = TaskState()

        def _append(
            nt: NodeType, nid: str, spec: str, parent: Optional[str],
            done: bool = False, run_mode: Optional[RunMode] = None,
        ) -> str:
            node = Node(node_id=nid, spec=spec, node_type=nt, run_mode=run_mode)
            st = SubtaskState(node_id=nid)
            if done:
                node.status = NodeStatus.DONE
                st.status = NodeStatus.DONE
            g.nodes.append(node)
            g.state.subtasks[nid] = st
            if parent:
                g.edges.append(
                    Edge(edge_id=f"e-{parent}-{nid}", from_node=parent, to_node=nid, kind=EdgeKind.DEPENDENCY)
                )
            return nid

        # 规划链:recognition/clarify/execute_start 是已完成的历史动作(skill 在创建/
        # 澄清/批准时跑过)→ 落图即 DONE,不参与 tick 推进 / 不被 BbsExecutor 认领。
        prev: Optional[str] = None
        for nt in (NodeType.RECOGNITION, NodeType.CLARIFY, NodeType.EXECUTE_START):
            prev = _append(nt, f"n_{nt.value}", nt.value, prev, done=True)
        execute_start_id = prev
        p = plan or task.plan
        if p is not None and p.sub_tasks:
            for sub in p.sub_tasks:
                _append(NodeType.DISPATCH, sub.node_id, sub.spec, execute_start_id, run_mode=sub.run_mode)
            for e in p.edges:
                g.edges.append(Edge(edge_id=e.edge_id, from_node=e.from_node, to_node=e.to_node, kind=e.kind))
        else:
            # 无计划 → 根 BOT_SEARCH:spec 用真实需求文本(目标/标题),让搜推与后续
            # 分解拿得到真实内容,而非字面占位(plan §2.2 n4;搜推先行)。
            objective = (
                task.spec.goal.objective
                if task.spec.goal and task.spec.goal.objective
                else (task.spec.metadata.title or "task")
            )
            _append(NodeType.BOT_SEARCH, "n_bot_search", objective, execute_start_id)
        self._task_repo.save(task)

    def spawn_sub_dag(
        self,
        task: Task,
        node_id: str,
        ref_kind: str,
        bcs_run_id: str,
        group_id: str,
        workflow_yaml_snapshot: Optional[str] = None,
    ) -> None:
        """Attach a :class:`SubDagRef` pointer to a coop-group node (plan §1.3a).
        Holds ONLY the reference — no child state tracked, so the group
        self-loop invariant stays intact."""
        node = self._find_node(task, node_id)
        if node is None:
            raise TaskNotFoundError(f"node {node_id} not in task {task.id}")
        node.sub_dag = SubDagRef(
            ref_kind=ref_kind,
            bcs_run_id=bcs_run_id,
            group_id=group_id,
            workflow_yaml_snapshot=workflow_yaml_snapshot,
        )

    def mark_graph_status(self, task: Task, status: GraphStatus) -> None:
        if task.execution_graph is None:
            return
        g = task.execution_graph
        # v2 guard(plan §5.1/§18.1-8):graph_status 迁移走 GRAPH_TRANSITIONS。
        # 初始落 ON_PLAZA(从 None 状态首次赋值)不经 guard;其余迁移必经 guard。
        if g.graph_status is not None and g.graph_status != status:
            require_graph_transition(g.graph_status, status)
        g.graph_status = status
        self._task_repo.save(task)

    def mark_terminal(self, task: Task, status: TaskStatus) -> None:
        self._advance_phase(task, status)
        self._task_repo.save(task)

    def add_sibling_node(
        self,
        task: Task,
        after_node_id: str,
        new_node: Node,
        edge_kind: EdgeKind = EdgeKind.DEPENDENCY,
    ) -> None:
        if task.execution_graph is None:
            return
        g = task.execution_graph
        g.nodes.append(new_node)
        g.edges.append(
            Edge(
                edge_id=f"e-{after_node_id}-{new_node.node_id}",
                from_node=after_node_id,
                to_node=new_node.node_id,
                kind=edge_kind,
            )
        )

    def set_node_status(self, task: Task, node_id: str, status: NodeStatus) -> None:
        node = self._find_node(task, node_id)
        if node is None:
            return
        require_node_transition(node.status, status)
        node.status = status

    # v2 图操作 + State 写口/读口/快照 见 GraphStateOpsMixin(plan §4.3/§8,
    # 守 architecture 1000-line cap 抽出至 services/graph_state_ops.py)。

    # --- internal: wire projections ---------------------------------------

    def _definition_meta(self, task: Task) -> Optional[dict]:
        p = task.plan
        if p is None:
            return None
        return {
            "node_count": len(p.sub_tasks),
            "edge_count": len(p.edges),
            "confidence": float(p.confidence),
            "title": task.spec.metadata.title,
        }

    def _node_view(self, n: Node) -> dict:
        rec = n.attempted_executors[-1] if n.attempted_executors else None
        return {
            "node_id": n.node_id,
            "display_name": n.spec,
            "status": n.status.value,
            "node_type": n.node_type.value,  # v2(§3.1)
            "render_kind": self._render_kind(n.node_type),  # v2(O-P5):exec/control-gate/system-bridge
            "sub_status": (n.properties.get("sub_status") or "idle"),
            "attempt": (rec.round if rec else 0),
            "assignee": n.assignee or "",
            "run_mode": (n.run_mode.value if n.run_mode else None),
            "collab_mode": (n.properties.get("collab_mode")),
            "started_at": (rec.at if rec else None),
            "completed_at": (n.properties.get("completed_at")),
            "is_final_output": bool(n.properties.get("is_final_output", False)),
            "attempted_executors": [self._attempt_view(a) for a in n.attempted_executors],
            "artifacts": [
                {"name": a.name, "location": a.location, "type": a.type}
                for a in n.artifacts
            ],
            "acceptance_result": n.properties.get("acceptance_result"),
            "judge_outputs": n.properties.get("judge_outputs"),  # v2(§18.1-13):拆出 judge 历史
            "targets_acceptance": [
                {"kind": c.kind.value, "properties": c.properties}
                for c in n.targets_acceptance
            ],
            "sub_dag_ref": self._sub_dag_ref_view(n.sub_dag),
        }

    def _edge_view(self, e: Edge) -> dict:
        return {
            "edge_id": e.edge_id,
            "from_node": e.from_node,
            "to_node": e.to_node,
            "kind": e.kind.value,
        }

    def _attempt_view(self, a: Any) -> dict:
        return {
            "executor_id": a.executor_id,
            "paradigm": (a.paradigm.value if a.paradigm else None),
            "round": a.round,
            "route_class": (a.route_class.value if a.route_class else None),
            "trigger": (a.trigger.value if a.trigger else None),
            "outcome": (a.outcome.value if a.outcome else None),
            "at": a.at,
            "note": a.note,
        }

    def _sub_dag_ref_view(self, ref: Optional[SubDagRef]) -> Optional[dict]:
        if ref is None:
            return None
        return {
            "ref_kind": ref.ref_kind,
            "bcs_run_id": ref.bcs_run_id,
            "group_id": ref.group_id,
        }

    def _plan_from_dict(self, data: Any) -> Plan:
        if isinstance(data, Plan):
            return data
        if not isinstance(data, dict):
            return Plan()
        from agentclaw.community.core.task.domain.models import (
            EdgeSpec,
        )

        plan = Plan(confidence=float(data.get("confidence") or 0.0))
        for s in data.get("sub_tasks") or []:
            if not isinstance(s, dict):
                continue
            plan.sub_tasks.append(
                SubTaskSpec(
                    node_id=str(s.get("node_id") or ""),
                    spec=str(s.get("spec") or ""),
                    run_mode=_coerce_status(s.get("run_mode"), RunMode),
                    depend_on=list(s.get("depend_on") or []),
                )
            )
        for e in data.get("edges") or []:
            if not isinstance(e, dict):
                continue
            plan.edges.append(
                EdgeSpec(
                    edge_id=str(e.get("edge_id") or ""),
                    from_node=str(e.get("from_node") or ""),
                    to_node=str(e.get("to_node") or ""),
                    kind=_coerce_status(e.get("kind"), EdgeKind) or EdgeKind.DEPENDENCY,
                )
            )
        return plan

    # --- internal: envelope unpack ---------------------------------------

    def _unpack(self, event: Any) -> tuple[str, EventKind, dict]:
        if isinstance(event, dict):
            task_id = str(event.get("task_id") or "")
            kind_raw = event.get("kind") or ""
            payload = dict(event.get("payload") or {})
            # flatten envelope top-level fields (router passes kind+seq+payload)
            for k in ("node_id", "title", "source", "patch"):
                if k in event and k not in payload:
                    payload[k] = event[k]
        else:
            task_id = getattr(event, "task_id", "")
            kind_raw = getattr(event, "kind", "")
            payload = dict(getattr(event, "payload", {}) or {})
        try:
            kind = EventKind(str(kind_raw))
        except ValueError:
            kind = EventKind.TASK_CREATED  # unknown → no-op fold
        return task_id, kind, payload