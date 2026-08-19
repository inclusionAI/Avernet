"""Endpoint-framework coverage for the task API surface.

Covers the task routes, split across two prefixes:
- public (``/openapi/v1/collaboration/tasks``): execute / dashboard / list (frontend-facing, gateway spanner).
- internal (``/api/v1/collaboration/tasks``): execute / dashboard / list 公开面镜像(供内部调用方 bot / 服务间
  免 spanner;同一 TaskServiceProtocol 委托)+ callback/report, bbs/{claim,attach,result},
  callback/workflow_{start,result}, callback/node_{start,result} (service-to-service, no spanner).
Each route has a happy (+ error) case so the coverage gate sees every registered route as covered.

Why the shared framework app is enough (no per-case mocking): the community
``TEST`` profile wires ``TaskModule`` with degraded transport ports
(``_resolve_ports()`` returns ``(None, None)`` — no BaaS/BCS/engine) and an
empty bot-discover stub, so ``execute``/``dashboard``/``callback``/``bbs`` run
on real in-memory kernel state with zero network. Seeds reach into that same
graph via ``world.get(TaskGraphService)`` / ``world.get(TaskServiceProtocol)``.
The root ``node_id`` is deterministic (``initialize_graph`` sets it to the
``task_id``), so callback ``loop_task_id`` bodies can be declared statically.

Error cases use *real input-driven* branches wherever the contract exposes one
(re-execute → 409, missing task → 404, malformed body → 422, unregistered
task-level callback → 400, non-holder BBS op → 409, invalid status → 400).
"""
from __future__ import annotations

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    Status,
    TaskGraphPatch,
    TaskInfo,
    TaskNodePatch,
    TaskSpec,
)
from agentclaw.community.core.task.task_graph.task_graph_service import (
    TaskGraphService,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


# --------------------------------------------------------------------------
# Shared fixtures
# --------------------------------------------------------------------------
def _task_info(task_id: str) -> TaskInfo:
    """A minimal but valid task spec for seeding an in-mem graph."""
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="存储尽调", instruction="produce DD"),
            context=Context(background="存储行业"),
            goal=Goal(
                objective="产出尽调报告",
                acceptances=[AcceptanceCriteria(id="ac1", description="d1")],
            ),
        ),
        source_channel_type="bot",
        source_channel_id="owner_bot",
        execution_config={"MAX_DEPTH": 3, "BBS_MAX_DEPTH": 3},
    )


def _task_spec_dto(task_id: str) -> dict:
    """DTO-shaped task spec for POST bodies (BbsAttachDTO.task_spec)."""
    return {
        "metadata": {"task_id": task_id, "title": "接力子任务", "instruction": "scoped relay"},
        "context": {"background": "bbs", "extend_props": {}},
        "goal": {
            "objective": "接力产出",
            "acceptances": [{"id": "ac1", "description": "d1"}],
        },
    }


def _seed_graph(world, task_id: str) -> None:
    """Initialize a clean PENDING root graph (no background on_execute)."""
    world.get(TaskGraphService).initialize_graph(_task_info(task_id))


def _seed_graph_bbs(world, task_id: str, *, claim_bot: str | None = None) -> None:
    """A bbs-ready graph: bbs_mode on, optionally claimed by ``claim_bot``."""
    gs = world.get(TaskGraphService)
    gs.initialize_graph(_task_info(task_id))
    gs.update_task_graph_info(
        task_id, TaskGraphPatch(extend_props_patch={"bbs_mode": True})
    )
    if claim_bot is not None:
        world.get(TaskServiceProtocol).claim_bbs_task(task_id, claim_bot)


def _run_root(world, task_id: str) -> None:
    """Flip the root node PENDING → RUNNING (for result-style callbacks)."""
    world.get(TaskGraphService).update_task_node_info(
        TaskNodePatch(task_id=task_id, node_id=task_id, status=Status.RUNNING)
    )


# ===== POST /openapi/v1/collaboration/tasks/execute =====

@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/execute",
    scenario="ok",
    input=CaseInput(json_body={
        "task_spec": {
            "metadata": {"task_id": "t_exec_ok", "title": "存储尽调", "instruction": "produce DD"},
            "context": {"background": "存储行业", "extend_props": {}},
            "goal": {"objective": "产出尽调报告",
                     "acceptances": [{"id": "ac1", "description": "d1"}]},
        },
        "source_channel_type": "bot",
        "source_channel_id": "owner_bot",
        "execution_config": {"MAX_DEPTH": 3, "BBS_MAX_DEPTH": 3},
    }),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def execute_ok():
    """New task → graph initialized → 200 with run_id (fire-and-forget)."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/execute",
    scenario="conflict_on_reexecute",
    input=CaseInput(json_body={
        "task_spec": {
            "metadata": {"task_id": "t_exec_conflict", "title": "x", "instruction": "y"},
            "context": {"background": "", "extend_props": {}},
            "goal": {"objective": "", "acceptances": []},
        },
        "source_channel_type": "bot",
        "source_channel_id": "owner_bot",
        "execution_config": {},
    }),
    seed=lambda w: _seed_graph(w, "t_exec_conflict"),
    expect=ExpectError(status=409),
)
def execute_conflict_on_reexecute():
    """Same task_id already initialized → GraphAlreadyInitializedError → 409."""


# ===== GET /openapi/v1/collaboration/tasks/dashboard =====

@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/dashboard",
    scenario="ok",
    input=CaseInput(query_params={"task_id": "t_dash_ok"}),
    seed=lambda w: _seed_graph(w, "t_dash_ok"),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def dashboard_ok():
    """Existing task → graph snapshot → 200."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/dashboard",
    scenario="task_not_found",
    input=CaseInput(query_params={"task_id": "t_dash_ghost"}),
    expect=ExpectError(status=404),
)
def dashboard_task_not_found():
    """Unknown task_id → TaskNotFoundError → 404."""


# ===== GET /openapi/v1/collaboration/tasks/list =====

@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/list",
    scenario="ok",
    seed=lambda w: _seed_graph(w, "t_list_ok"),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def list_ok():
    """List summaries (seeded graph present) → 200."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/list",
    scenario="invalid_status_filter",
    input=CaseInput(query_params={"status": "INVALID_STATUS"}),
    expect=ExpectError(status=400),
)
def list_invalid_status_filter():
    """Non-enum status → router rejects with 400 (not an uncaught ValueError 500)."""


# ===== internal mirror: POST/GET /api/v1/collaboration/tasks/{execute,dashboard,list} =====
# 公开面(execute/dashboard/list)在 /openapi/v1 上的内部副本:同一 TaskServiceProtocol 委托,
# 供内部调用方(bot / 服务间)免 gateway spanner。happy 覆盖即可——逻辑/错误分支与公开面一致,
# 已由上面 /openapi/v1 用例覆盖;此处仅让 coverage gate 见到内部副本路由被驱动。

@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/execute",
    scenario="ok",
    input=CaseInput(json_body={
        "task_spec": {
            "metadata": {"task_id": "t_exec_int_ok", "title": "存储尽调", "instruction": "produce DD"},
            "context": {"background": "存储行业", "extend_props": {}},
            "goal": {"objective": "产出尽调报告",
                     "acceptances": [{"id": "ac1", "description": "d1"}]},
        },
        "source_channel_type": "bot",
        "source_channel_id": "owner_bot",
        "execution_config": {"MAX_DEPTH": 3, "BBS_MAX_DEPTH": 3},
    }),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def execute_internal_ok():
    """Internal-mirror execute → 200 (same delegate as the /openapi/v1 face)."""


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/dashboard",
    scenario="ok",
    input=CaseInput(query_params={"task_id": "t_dash_int_ok"}),
    seed=lambda w: _seed_graph(w, "t_dash_int_ok"),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def dashboard_internal_ok():
    """Internal-mirror dashboard → 200 graph snapshot (seeded graph)."""


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/list",
    scenario="ok",
    seed=lambda w: _seed_graph(w, "t_list_int_ok"),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def list_internal_ok():
    """Internal-mirror list → 200 summaries (seeded graph present)."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/execute",
    scenario="conflict_on_reexecute",
    input=CaseInput(json_body={
        "task_spec": {
            "metadata": {"task_id": "t_exec_int_conflict", "title": "x", "instruction": "y"},
            "context": {"background": "", "extend_props": {}},
            "goal": {"objective": "", "acceptances": []},
        },
        "source_channel_type": "bot",
        "source_channel_id": "owner_bot",
        "execution_config": {},
    }),
    seed=lambda w: _seed_graph(w, "t_exec_int_conflict"),
    expect=ExpectError(status=409),
)
def execute_internal_conflict_on_reexecute():
    """Internal-mirror execute: same task_id already initialized → 409."""


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/dashboard",
    scenario="task_not_found",
    input=CaseInput(query_params={"task_id": "t_dash_int_ghost"}),
    expect=ExpectError(status=404),
)
def dashboard_internal_task_not_found():
    """Internal-mirror dashboard: unknown task_id → 404."""


@endpoint_test(
    method="GET",
    path="/api/v1/collaboration/tasks/list",
    scenario="invalid_status_filter",
    input=CaseInput(query_params={"status": "INVALID_STATUS"}),
    expect=ExpectError(status=400),
)
def list_internal_invalid_status_filter():
    """Internal-mirror list: non-enum status → 400 (not an uncaught 500)."""


# ===== POST /api/v1/collaboration/tasks/callback/report =====

@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/callback/report",
    scenario="ok",
    input=CaseInput(json_body={
        "loop_task_id": "t_cb_report_ok::t_cb_report_ok",
        "workflow_type": "single_bot",
        "result": {"code": 200000, "data": {"report": "dd"}},
    }),
    seed=lambda w: (_seed_graph(w, "t_cb_report_ok"), _run_root(w, "t_cb_report_ok")),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def callback_report_ok():
    """Result fold on a RUNNING root → 200 {ok: true}."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/callback/report",
    scenario="invalid_body",
    input=CaseInput(json_body={"workflow_type": "single_bot"}),
    expect=ExpectError(status=422),
)
def callback_report_invalid_body():
    """Missing required loop_task_id → RequestValidationError → 422."""


# ===== POST /api/v1/collaboration/tasks/bbs/claim =====

@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/claim",
    scenario="ok",
    input=CaseInput(json_body={"task_id": "t_bbs_claim_ok", "bot_id": "bot1"}),
    seed=lambda w: _seed_graph_bbs(w, "t_bbs_claim_ok"),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def bbs_claim_ok():
    """CAS claim on a bbs_mode task → 200 {root_node_id, task_id}."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/claim",
    scenario="non_bbs_task",
    input=CaseInput(json_body={"task_id": "t_bbs_claim_err", "bot_id": "bot1"}),
    seed=lambda w: _seed_graph(w, "t_bbs_claim_err"),
    expect=ExpectError(status=409),
)
def bbs_claim_non_bbs_task():
    """Claim on a plain (non-bbs_mode) task → TaskStateError → 409."""


# ===== POST /api/v1/collaboration/tasks/bbs/attach =====

@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/attach",
    scenario="ok",
    input=CaseInput(json_body={
        "task_id": "t_bbs_attach_ok",
        "parent_node_id": "t_bbs_attach_ok",
        "task_spec": _task_spec_dto("t_bbs_attach_ok"),
        "bot_id": "bot1",
    }),
    seed=lambda w: _seed_graph_bbs(w, "t_bbs_attach_ok", claim_bot="bot1"),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def bbs_attach_ok():
    """Holder attaches a scoped bbs child under the PENDING root → 200."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/attach",
    scenario="not_holder",
    input=CaseInput(json_body={
        "task_id": "t_bbs_attach_err",
        "parent_node_id": "t_bbs_attach_err",
        "task_spec": _task_spec_dto("t_bbs_attach_err"),
        "bot_id": "bot1",
    }),
    seed=lambda w: _seed_graph_bbs(w, "t_bbs_attach_err"),
    expect=ExpectError(status=409),
)
def bbs_attach_not_holder():
    """Attach without holding the claim → TaskStateError → 409."""


# ===== POST /api/v1/collaboration/tasks/bbs/result =====

@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/result",
    scenario="ok",
    input=CaseInput(json_body={
        "task_id": "t_bbs_result_ok",
        "node_id": "t_bbs_result_ok",
        "bot_id": "bot1",
        "output_patch": {"checkpoint": "v1"},
    }),
    seed=lambda w: _seed_graph_bbs(w, "t_bbs_result_ok", claim_bot="bot1"),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def bbs_result_ok():
    """Holder reports a checkpoint fold (no terminal flip) on the root → 200."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/bbs/result",
    scenario="not_holder",
    input=CaseInput(json_body={
        "task_id": "t_bbs_result_err",
        "node_id": "t_bbs_result_err",
        "bot_id": "bot1",
        "output_patch": {"k": "v"},
    }),
    seed=lambda w: _seed_graph_bbs(w, "t_bbs_result_err"),
    expect=ExpectError(status=409),
)
def bbs_result_not_holder():
    """Report without holding the claim → TaskStateError → 409."""


# ===== POST /api/v1/collaboration/tasks/callback/workflow_start =====

@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/callback/workflow_start",
    scenario="ok",
    input=CaseInput(json_body={
        "task_id": "t_wf_start_ok",
        "workflow_source": "claw_mind",
        "workflow_id": "wf1",
        "workflow_instance_id": "wi1",
        "status": "RUNNING",
        "is_success": True,
        "loop_task_id": "t_wf_start_ok::t_wf_start_ok",
    }),
    seed=lambda w: _seed_graph(w, "t_wf_start_ok"),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def workflow_start_ok():
    """Task-level start callback on a PENDING root → PENDING→RUNNING → 200."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/callback/workflow_start",
    scenario="invalid_body",
    input=CaseInput(raw_body=b"not-json"),
    expect=ExpectError(status=422),
)
def workflow_start_invalid_body():
    """Malformed raw body → model_validate_json fails → 422."""


# ===== POST /api/v1/collaboration/tasks/callback/workflow_result =====

@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/callback/workflow_result",
    scenario="ok",
    input=CaseInput(json_body={
        "task_id": "t_wf_result_ok",
        "workflow_source": "claw_mind",
        "workflow_id": "wf1",
        "workflow_instance_id": "wi1",
        "status": "DONE",
        "is_success": True,
        "output": {"report": "dd"},
        "loop_task_id": "t_wf_result_ok::t_wf_result_ok",
    }),
    seed=lambda w: (_seed_graph(w, "t_wf_result_ok"), _run_root(w, "t_wf_result_ok")),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def workflow_result_ok():
    """Task-level result callback on a RUNNING root → 200."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/callback/workflow_result",
    scenario="unregistered_no_loop_task_id",
    input=CaseInput(json_body={
        "task_id": "t_wf_result_err",
        "workflow_source": "claw_mind",
        "workflow_id": "wf1",
        "workflow_instance_id": "wi_unregistered",
        "status": "DONE",
        "is_success": True,
    }),
    expect=ExpectError(status=400),
)
def workflow_result_unregistered():
    """Task-level result with no loop_task_id and unregistered instance → 400."""


# ===== POST /api/v1/collaboration/tasks/callback/node_start =====

@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/callback/node_start",
    scenario="ok",
    input=CaseInput(json_body={
        "task_id": "t_node_start_ok",
        "node_id": "t_node_start_ok",
        "workflow_source": "claw_mind",
        "workflow_id": "wf1",
        "workflow_instance_id": "wi1",
        "status": "RUNNING",
        "is_success": True,
    }),
    seed=lambda w: _seed_graph(w, "t_node_start_ok"),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def node_start_ok():
    """Node-level start callback on the PENDING root → 200."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/callback/node_start",
    scenario="node_not_found",
    input=CaseInput(json_body={
        "task_id": "t_node_start_err",
        "node_id": "ghost_node",
        "workflow_source": "claw_mind",
        "workflow_id": "wf1",
        "workflow_instance_id": "wi1",
        "status": "RUNNING",
        "is_success": True,
    }),
    seed=lambda w: _seed_graph(w, "t_node_start_err"),
    expect=ExpectError(status=404),
)
def node_start_node_not_found():
    """Node-level start on a non-existent node → NodeNotFoundError → 404."""


# ===== POST /api/v1/collaboration/tasks/callback/node_result =====

@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/callback/node_result",
    scenario="ok",
    input=CaseInput(json_body={
        "task_id": "t_node_result_ok",
        "node_id": "t_node_result_ok",
        "workflow_source": "claw_mind",
        "workflow_id": "wf1",
        "workflow_instance_id": "wi1",
        "status": "DONE",
        "is_success": True,
        "output": {"report": "dd"},
    }),
    seed=lambda w: (_seed_graph(w, "t_node_result_ok"), _run_root(w, "t_node_result_ok")),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def node_result_ok():
    """Node-level result callback on a RUNNING root → 200."""


@endpoint_test(
    method="POST",
    path="/api/v1/collaboration/tasks/callback/node_result",
    scenario="node_not_found",
    input=CaseInput(json_body={
        "task_id": "t_node_result_err",
        "node_id": "ghost_node",
        "workflow_source": "claw_mind",
        "workflow_id": "wf1",
        "workflow_instance_id": "wi1",
        "status": "DONE",
        "is_success": True,
    }),
    seed=lambda w: _seed_graph(w, "t_node_result_err"),
    expect=ExpectError(status=404),
)
def node_result_node_not_found():
    """Node-level result on a non-existent node → NodeNotFoundError → 404."""
