"""HTTP routes for the task module (Phase 0.6 骨架, 0.10 DI-wired).

Handlers delegate to the api-layer service api ``TaskServiceProtocol`` via
``Injected(TaskServiceProtocol)`` (mirrors ``api/bot_service.BotServiceProtocol``);
the DI container (``CommunityTaskModule``) binds the api Protocol to the core
concrete ``TaskService``. Core never imports this router or the api Protocols.

Plan §2.1 — TaskService is the *only* write path (``on_event``). Scheduler
orchestration (dispatch/reroute) and owner-bot SKILL verification both enter as
events; this router exposes:
  - POST /api/tasks/create                 create           (n1 recognition)
  - GET  /api/tasks?user_id=...             list_by_user
  - GET  /api/tasks/{task_id}               get
  - GET  /api/tasks/{task_id}/progress      progress
  - POST /api/tasks/{task_id}/clarify       clarify spec       (n2 clarify)
  - POST /api/tasks/{task_id}/plan          finalize_plan    (DRAFTING→DEFINED)
  - POST /api/tasks/{task_id}/start         scheduler.start  (n3 execute_start)
  - POST /api/tasks/{task_id}/events        owner-bot 回投 (on_event)

Canvas (secondary panel, plan §1.4b/§7.2) — dynamic-workflow graph + drill-down:
  - GET  /api/tasks/{task_id}/graph                  top-level dynamic DAG snapshot
  - GET  /api/tasks/{task_id}/nodes/{node_id}        node execution detail
  - GET  /api/tasks/{task_id}/nodes/{node_id}/sub-dag  coop-group drill-down (live SM run graph)
  - WS   /api/tasks/{task_id}/graph/stream           incremental TaskGraphView push
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from agentclaw.community.api.task import TaskSchedulerProtocol, TaskServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.adapters.http.task.schemas import (
    ClarifyTaskRequest,
    CreateTaskRequest,
    EventReportRequest,
    EventReportResponse,
    FinalizePlanRequest,
    TaskCreatedResponse,
    TaskDetailResponse,
    TaskEventItem,
    TaskGraphView,
    TaskHistoryResponse,
    TaskListItem,
    TaskListResponse,
    TaskNodeDetailView,
    TaskProgressResponse,
)

router = APIRouter(prefix="/api/tasks", tags=["task"])


# --- helpers ----------------------------------------------------------------

def _attr(task: Any, name: str, default: Any = None) -> Any:
    if isinstance(task, dict):
        return task.get(name, default)
    return getattr(task, name, default)


def _status_of(task: Any) -> str:
    s = _attr(task, "status", None)
    if s is None:
        return ""
    return getattr(s, "value", str(s))


def _task_id_of(task: Any) -> str:
    return _attr(task, "id", None) or _attr(task, "task_id", "") or ""


def _user_id_of(task: Any) -> str:
    return _attr(task, "user_id", "") or ""


def _loop_round_of(task: Any) -> int:
    return int(_attr(task, "loop_round", 0) or 0)


def _spec_dict(task: Any) -> dict:
    spec = getattr(task, "spec", None)
    if spec is None and isinstance(task, dict):
        return task.get("spec", {})
    # best-effort: domain dataclass -> dict via asdict if available
    try:
        from dataclasses import asdict
        return asdict(spec)  # type: ignore[arg-type]
    except Exception:
        return {}


def _execution_graph_dict(task: Any) -> Optional[dict]:
    g = _attr(task, "execution_graph", None)
    if g is None:
        return None
    try:
        from dataclasses import asdict
        return asdict(g)  # type: ignore[arg-type]
    except Exception:
        return {}


# --- endpoints --------------------------------------------------------------

@router.post("/create", response_model=TaskCreatedResponse)
def create_task(req: CreateTaskRequest, service: TaskServiceProtocol = Injected(TaskServiceProtocol)) -> Any:
    task = service.create(title=req.title, source=req.source, background=req.background)
    return TaskCreatedResponse(
        task_id=_task_id_of(task),
        status=_status_of(task) or "drafting",
        seq=int(getattr(task, "latest_event_seq", 1) or 1),
    )


@router.get("", response_model=TaskListResponse)
def list_tasks(
    user_id: str = Query(..., description="Owner user id."),
    limit: int = Query(50, ge=1, le=200),
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),
) -> Any:
    tasks = service.list_by_user(user_id, limit=limit)
    items = [
        TaskListItem(
            task_id=_task_id_of(t),
            user_id=_user_id_of(t),
            status=_status_of(t),
            loop_round=_loop_round_of(t),
        )
        for t in tasks
    ]
    return TaskListResponse(items=items, total=len(items))


@router.get("/{task_id}", response_model=TaskDetailResponse)
def get_task(task_id: str, service: TaskServiceProtocol = Injected(TaskServiceProtocol)) -> Any:
    task = service.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskDetailResponse(
        task_id=_task_id_of(task),
        user_id=_user_id_of(task),
        status=_status_of(task),
        spec=_spec_dict(task),
        execution_graph=_execution_graph_dict(task),
        loop_round=_loop_round_of(task),
        nodes=[],
    )


@router.get("/{task_id}/progress", response_model=TaskProgressResponse)
def get_progress(task_id: str, service: TaskServiceProtocol = Injected(TaskServiceProtocol)) -> Any:
    prog = service.progress(task_id)
    if not isinstance(prog, dict):
        raise HTTPException(status_code=404, detail="task not found")
    return TaskProgressResponse(
        task_id=prog.get("task_id", task_id),
        status=str(prog.get("status", "")),
        loop_round=int(prog.get("loop_round", 0) or 0),
        done=int(prog.get("done", 0) or 0),
        total=int(prog.get("total", 0) or 0),
        nodes=[],
    )


@router.post("/{task_id}/clarify", response_model=TaskDetailResponse)
def clarify_task(
    task_id: str,
    req: ClarifyTaskRequest,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),
) -> Any:
    task = service.clarify(task_id, req.patch)
    return TaskDetailResponse(
        task_id=_task_id_of(task),
        user_id=_user_id_of(task),
        status=_status_of(task),
        spec=_spec_dict(task),
        execution_graph=_execution_graph_dict(task),
        loop_round=_loop_round_of(task),
        nodes=[],
    )


@router.post("/{task_id}/plan", response_model=TaskDetailResponse)
def finalize_plan(
    task_id: str,
    req: FinalizePlanRequest,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),
) -> Any:
    task = service.finalize_plan(task_id, req.plan_payload)
    return TaskDetailResponse(
        task_id=_task_id_of(task),
        user_id=_user_id_of(task),
        status=_status_of(task),
        spec=_spec_dict(task),
        execution_graph=_execution_graph_dict(task),
        loop_round=_loop_round_of(task),
        nodes=[],
    )


@router.post("/{task_id}/events", response_model=EventReportResponse)
def report_event(
    task_id: str,
    req: EventReportRequest,
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),
) -> Any:
    """Owner-bot SKILL 回投 entrypoint — folds an event via ``on_event``.

    Phase 0.6 passes a lightweight envelope; the service impl (Phase 2) will
    reconstruct a proper :class:`TaskEvent` from ``kind`` + ``payload``.
    """
    envelope = {"task_id": task_id, "kind": req.kind, "seq": req.seq, "payload": req.payload}
    task = service.on_event(envelope)  # type: ignore[arg-type]
    return EventReportResponse(
        task_id=_task_id_of(task),
        accepted=True,
        seq=int(getattr(task, "latest_event_seq", 0) or 0),
        note="",
    )


# --- scheduler orchestration endpoints (Phase 3.5, plan §3) -----------------


@router.post("/{task_id}/start", response_model=TaskDetailResponse)
def start_task(
    task_id: str,
    scheduler: TaskSchedulerProtocol = Injected(TaskSchedulerProtocol),
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),
) -> Any:
    """Approve a finalized plan → Scheduler.start (DEFINED → EXECUTING + build DAG)."""
    task = scheduler.start(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskDetailResponse(
        task_id=_task_id_of(task),
        user_id=_user_id_of(task),
        status=_status_of(task),
        spec=_spec_dict(task),
        execution_graph=_execution_graph_dict(task),
        loop_round=_loop_round_of(task),
        nodes=[],
    )


@router.post("/{task_id}/tick")
def tick_task(
    task_id: str,
    scheduler: TaskSchedulerProtocol = Injected(TaskSchedulerProtocol),
) -> Any:
    """Drive one Scheduler tick (topo-unlock + dispatch + settle/terminate guard)."""
    return scheduler.tick(task_id)


# --- canvas (secondary panel) endpoints (Phase 0.7, plan §7.2) --------------

def _graph_view_of(snapshot: Any) -> TaskGraphView:
    """Coerce a service-returned graph snapshot (dict or dataclass) into the
    wire model. The service layer is responsible for projecting the domain
    ``TaskExecutionGraph`` into this shape."""
    if isinstance(snapshot, TaskGraphView):
        return snapshot
    if isinstance(snapshot, dict):
        return TaskGraphView.model_validate(snapshot)
    # dataclass → dict → validate
    try:
        from dataclasses import asdict
        return TaskGraphView.model_validate(asdict(snapshot))
    except Exception:
        return TaskGraphView(task_id="", root_phase="executing")


def _node_detail_view_of(detail: Any) -> TaskNodeDetailView:
    if isinstance(detail, TaskNodeDetailView):
        return detail
    if isinstance(detail, dict):
        return TaskNodeDetailView.model_validate(detail)
    try:
        from dataclasses import asdict
        return TaskNodeDetailView.model_validate(asdict(detail))
    except Exception:
        return TaskNodeDetailView(node_id="")


@router.get("/{task_id}/graph", response_model=TaskGraphView)
def get_task_graph(task_id: str, service: TaskServiceProtocol = Injected(TaskServiceProtocol)) -> Any:
    """Top-level dynamic-workflow DAG snapshot (root_phase + nodes/edges).

    404 when the task is unknown or has no execution graph yet — never return a
    fake default. The panel treats 4xx as fatal, so a wrong/stale taskId surfaces
    as a visible "加载失败: 404" instead of silently spinning on an empty graph
    (which is what the old ``_graph_view_of(None)`` fallback caused)."""
    snapshot = service.get_task_graph(task_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _graph_view_of(snapshot)


@router.get("/{task_id}/nodes/{node_id}", response_model=TaskNodeDetailView)
def get_node_detail(task_id: str, node_id: str, service: TaskServiceProtocol = Injected(TaskServiceProtocol)) -> Any:
    """Node execution detail (aligns SM canvas node-detail panel)."""
    return _node_detail_view_of(service.get_node_detail(task_id, node_id))


@router.get("/{task_id}/nodes/{node_id}/sub-dag", response_model=TaskGraphView)
def get_sub_dag(task_id: str, node_id: str, service: TaskServiceProtocol = Injected(TaskServiceProtocol)) -> Any:
    """Cooperative-group drill-down: live SM run graph mapped via SmGraphAdapter
    (路 A, plan §1.3a). Non-coop node or no ref → 404."""
    snapshot = service.get_sub_dag(task_id, node_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="node has no sub-dag reference")
    return _graph_view_of(snapshot)


@router.get("/{task_id}/history", response_model=TaskHistoryResponse)
def get_task_history(
    task_id: str,
    after_seq: int = Query(0, ge=0, description="Return events with seq > after_seq (incremental follow)."),
    service: TaskServiceProtocol = Injected(TaskServiceProtocol),
) -> Any:
    """The append-only event log in seq order — the authoritative execution
    trace (task created → plan finalized → node dispatched/running →
    accepted/rejected/failed → ...). ``after_seq`` for incremental follow."""
    events = service.history(task_id, after_seq=after_seq)
    items = [
        TaskEventItem(
            seq=int(getattr(e, "seq", 0)),
            kind=str(getattr(getattr(e, "kind", None), "value", getattr(e, "kind", ""))),
            payload=dict(getattr(e, "payload", {}) or {}),
            reported=bool(getattr(e, "reported", False)),
            occurred_at=getattr(e, "occurred_at", None),
        )
        for e in events
    ]
    return TaskHistoryResponse(task_id=task_id, items=items, total=len(items))


@router.websocket("/{task_id}/graph/stream")
async def stream_task_graph(
    websocket: WebSocket,
    task_id: str,
) -> None:
    """Incremental TaskGraphView push (event-driven; canvas polls as fallback).
    Phase 0.7 = skeleton: accepts the WS and streams ``subscribe_task_graph``
    snapshots if the service exposes it; otherwise closes cleanly."""
    await websocket.accept()
    try:
        # Service lookup via the app-global injector (Injected relies on the
        # HTTP request scope, unavailable for WS). The DI-bound impl (Phase 2+)
        # provides ``subscribe_task_graph``.
        from agentclaw.community.di import get_app_injector
        try:
            injector = get_app_injector()
            service: TaskServiceProtocol = injector.get(TaskServiceProtocol)  # type: ignore[assignment]
        except Exception:
            await websocket.close()
            return
        subscribe = getattr(service, "subscribe_task_graph", None)
        if subscribe is None:
            await websocket.close()
            return
        async for snapshot in subscribe(task_id):  # type: ignore[misc]
            view = _graph_view_of(snapshot)
            await websocket.send_text(view.model_dump_json())
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await websocket.close()
        except Exception:
            return