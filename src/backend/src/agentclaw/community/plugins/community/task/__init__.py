"""Community Noop impls for the task module (Phase 0.7).

These let the DI container wire a default binding so the router + smoke pipeline
run before the real TaskService (Phase 2) / TaskScheduler + Ports (Phase 3-4)
land. Every method is side-effect-free and returns a neutral value — Noops never
raise. They are NOT ``@plugin_impl`` entries: TaskService / Ports are api-layer
business Protocols, bound via injector ``@provider`` (like aicoding's
WorkspaceServiceProtocol), not via the plugin_api impl_registry (which is for
infrastructure Plugins under Rule 20/21).
"""
from __future__ import annotations

from typing import Optional

from agentclaw.community.core.task.protocols import (
    BcsCollaborationProtocol,
    BotDiscoverPort,
    DecomposerPort,
    DispatchResult,
    ExecutionPort,
    RouteRecommendation,
    TaskDriverPort,
    TaskService,
)
from agentclaw.community.core.task.domain.events import TaskEvent
from agentclaw.community.core.task.domain.models import (
    Plan,
    RouteClass,
    RunMode,
    Task,
    TaskSource,
    TaskSpec,
    TaskSpecMetadata,
)


class NoopTaskService(TaskService):
    """No-op TaskService — query returns empty, intake create yields a Task
    at INTAKE (so smoke tests can trace an id), amend/finalize/on_event/claim
    are None. Phase 2 replaces this with the real event-fold authority."""

    def get(self, task_id: str) -> Optional[Task]:
        return None

    def list_by_user(self, user_id: str, limit: int = 50) -> list[Task]:
        return []

    def progress(self, task_id: str) -> dict:
        return {}

    def create(self, title: str, source: str = "api", background: str = "") -> Task:
        source_enum = TaskSource(source) if source in {e.value for e in TaskSource} else TaskSource.API
        tid = "noop-task"
        return Task(
            id=tid,
            user_id="",
            source=source_enum,
            spec=TaskSpec(
                metadata=TaskSpecMetadata(id=tid, title=title),
            ),
        )

    def amend(self, task_id: str, patch: dict) -> Optional[Task]:
        return None

    def finalize_plan(self, task_id: str, plan: Plan) -> Optional[Task]:
        return None

    def on_event(self, event: TaskEvent) -> Optional[Task]:
        return None

    def claim_node(self, task_id: str, node_id: str, executor_id: str) -> Optional[DispatchResult]:
        return None

    # --- canvas (secondary panel) query face (Phase 0.8, plan §1.4b) -------
    # Neutral snapshots so the router/canvas smoke runs before the real query
    # group (Phase 2) and SmGraphAdapter (Phase 4) land. Never raise.
    def get_task_graph(self, task_id: str) -> dict:
        return {
            "task_id": task_id,
            "root_phase": "intake",
            "graph_status": "on_plaza",
            "loop_round": 0,
            "definition_meta": None,
            "nodes": [],
            "edges": [],
        }

    def get_node_detail(self, task_id: str, node_id: str) -> dict:
        return {"node_id": node_id, "display_name": node_id, "status": "pending"}

    def get_sub_dag(self, task_id: str, node_id: str) -> Optional[dict]:
        # No SubDagRef on a Noop node → router returns 404 (not a raise).
        return None

    async def subscribe_task_graph(self, task_id: str):
        # Phase 0 skeleton: yield nothing; the WS endpoint closes cleanly.
        return
        yield  # type: ignore[unreachable]  # pragma: no cover


class NoopBotDiscoverPort(BotDiscoverPort):
    def recommend(self, task_id: str, node_id: str) -> RouteRecommendation:
        return RouteRecommendation(
            route_class=RouteClass.C1,
            run_mode=RunMode.SINGLE_BOT,
            candidates=[],
            confidence=0.0,
        )


class NoopDecomposerPort(DecomposerPort):
    def decompose(self, task_id: str) -> Plan:
        return Plan()


class NoopTaskDriverPort(TaskDriverPort):
    def dispatch_node(self, task_id: str, node_id: str) -> DispatchResult:
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.SINGLE_BOT)

    def redispatch(self, task_id: str, node_id: str, route_class: RouteClass) -> DispatchResult:
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.SINGLE_BOT)

    def escalate_to_bbs(self, task_id: str, reason: str = "") -> DispatchResult:
        return DispatchResult(node_id="", executor_id="", run_mode=RunMode.BBS)


class NoopExecutionPort(ExecutionPort):
    def dispatch_single_bot(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def coop_group(self, task_id: str, node_id: str, bot_ids: list[str]) -> DispatchResult:
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.COOP_GROUP)

    def redispatch_node(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def probe(self, task_id: str, node_id: str, bot_id: str) -> DispatchResult:
        return DispatchResult(node_id=node_id, executor_id=bot_id, run_mode=RunMode.SINGLE_BOT)

    def bbs(self, task_id: str, node_id: str, reason: str = "") -> DispatchResult:
        return DispatchResult(node_id=node_id, executor_id="", run_mode=RunMode.BBS)


class NoopBcsCollaborationPort(BcsCollaborationProtocol):
    """No-op BCS collaboration query Port — returns a伪造 state-machine run graph
    snapshot so the canvas + SmGraphAdapter can be brought up before real BCS
    wiring (Phase 4). The shape mirrors BCS ``StateMachineRunGraphView``
    (run + definition + nodes + edges) just enough for mapping tests."""

    def fetch_state_machine_run_graph(self, bcs_run_id: str) -> dict:
        return {
            "run": {
                "run_id": bcs_run_id,
                "status": "running",
                "input": None,
                "output": None,
            },
            "definition": {
                "id": "noop-def",
                "version": 1,
                "name": "noop",
                "graph_mode": "acyclic",
                "initial_nodes": ["n1"],
            },
            "nodes": [
                {
                    "node_id": "n1",
                    "display_name": "noop node",
                    "kind": "bot_task",
                    "final_output": False,
                    "status": "running",
                    "attempt": 1,
                    "sub_status": "awaiting_response",
                }
            ],
            "edges": [],
        }

    def fetch_node_detail(self, bcs_run_id: str, node_id: str) -> dict:
        return {
            "node": {
                "run_id": bcs_run_id,
                "node_id": node_id,
                "status": "running",
                "attempt": 1,
                "artifact_text": None,
                "error": None,
            },
            "sub_status": "awaiting_response",
            "judge_outputs": [],
        }


__all__ = [
    "HangingBotExecutor",
    "LocalBotExecutorPort",
    "NoopBcsCollaborationPort",
    "NoopBotDiscoverPort",
    "NoopDecomposerPort",
    "NoopExecutionPort",
    "NoopTaskDriverPort",
    "NoopTaskService",
]


# 6.5.4: local in-process ExecutionPort doubles (well-behaved self-reporting
# bot + hung bot for watchdog exercising). Imported at end of __all__ to keep the
# Noop impls above as the primary reference; these are re-exported for DI/tests.
from agentclaw.community.plugins.community.task.local_executor import (  # noqa: E402
    HangingBotExecutor,
    LocalBotExecutorPort,
)