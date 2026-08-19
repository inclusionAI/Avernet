"""Endpoint-framework coverage for the task API surface.

Covers the 13 routes mounted under ``/openapi/v1/collaboration/tasks(...)`` (task router,
task-callback router, task-discovery router) with a happy + error case each,
so the coverage gate sees every route as covered.

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

The two discovery routes (``/discover``, ``/status``) build their service from
env (not DI-wired), so no request input produces a failure. Their error case
substitutes the module-level builder the handler calls — a forced raise that the
handler catches and converts to ``InternalError`` → ``ErrorEnvelope``. This
is direct attribute assignment on the router module (not a mock library, not
``setattr``); the ``test_no_mock_in_endpoint_tests`` scanner permits it, and each
happy case restores the originals so nothing leaks.
"""
from __future__ import annotations

import os
import tempfile

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


# --------------------------------------------------------------------------
# task-discovery seam substitution
# --------------------------------------------------------------------------
# The discovery router self-builds its service from env (not DI-wired). Its
# handlers catch build/read failures and raise ``InternalError``. The error
# cases below force the builder the handler calls to raise, exercising that
# ``except → InternalError`` end-to-end. The happy cases restore the
# originals (and point the reader at an absent db so ``discover()`` makes no
# engine call), so the seam never leaks past this file.
import agentclaw.community.adapters.http.openapi_v1.task.discovery.router as _disc_router  # noqa: E402

_orig_build_service = _disc_router._build_service
_orig_resolve_db_path = _disc_router._resolve_db_path
_orig_task_reader = _disc_router.SqliteTaskReader


class _ForcedDiscoveryFailure(RuntimeError):
    """Raised by the stand-in builders to drive the handler's ``except``."""


def _build_service_failure(*_args, **_kwargs):
    raise _ForcedDiscoveryFailure("forced discovery build failure")


class _FailingTaskReader:
    """Stand-in for ``SqliteTaskReader`` whose read raises inside the handler's try.

    ``/status`` calls ``SqliteTaskReader(db_path).read_discovered_tasks()`` *inside*
    its ``try/except`` (unlike ``_resolve_db_path``, which sits outside). Substituting
    the reader — not the path resolver — is what keeps the forced raise caught by the
    handler and converted to ``InternalError`` rather than escaping.
    """

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def read_discovered_tasks(self):
        raise _ForcedDiscoveryFailure("forced status read failure")

    def read_pending_tasks(self):
        raise _ForcedDiscoveryFailure("forced status read failure")


def _disc_clean_env(_world) -> None:
    """Point discovery at a guaranteed-absent db so the reader returns ``[]``."""
    os.environ["TASK_DISCOVERY_DATA_FILE"] = os.path.join(
        tempfile.gettempdir(), "avernet_task_disc_absent.db"
    )


def _seed_discover_ok(_world) -> None:
    _disc_router._build_service = _orig_build_service
    _disc_clean_env(_world)


def _seed_discover_err(_world) -> None:
    _disc_router._build_service = _build_service_failure


def _seed_status_ok(_world) -> None:
    # Restore every seam (earlier error cases may have patched them) so the final
    # discovery case leaves the router module pristine.
    _disc_router._build_service = _orig_build_service
    _disc_router._resolve_db_path = _orig_resolve_db_path
    _disc_router.SqliteTaskReader = _orig_task_reader
    _disc_clean_env(_world)


def _seed_status_err(_world) -> None:
    _disc_router.SqliteTaskReader = _FailingTaskReader


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


# ===== POST /openapi/v1/collaboration/tasks/callback/report =====

@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/callback/report",
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
    path="/openapi/v1/collaboration/tasks/callback/report",
    scenario="invalid_body",
    input=CaseInput(json_body={"workflow_type": "single_bot"}),
    expect=ExpectError(status=422),
)
def callback_report_invalid_body():
    """Missing required loop_task_id → RequestValidationError → 422."""


# ===== POST /openapi/v1/collaboration/tasks/bbs/claim =====

@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/bbs/claim",
    scenario="ok",
    input=CaseInput(json_body={"task_id": "t_bbs_claim_ok", "bot_id": "bot1"}),
    seed=lambda w: _seed_graph_bbs(w, "t_bbs_claim_ok"),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def bbs_claim_ok():
    """CAS claim on a bbs_mode task → 200 {root_node_id, task_id}."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/bbs/claim",
    scenario="non_bbs_task",
    input=CaseInput(json_body={"task_id": "t_bbs_claim_err", "bot_id": "bot1"}),
    seed=lambda w: _seed_graph(w, "t_bbs_claim_err"),
    expect=ExpectError(status=409),
)
def bbs_claim_non_bbs_task():
    """Claim on a plain (non-bbs_mode) task → TaskStateError → 409."""


# ===== POST /openapi/v1/collaboration/tasks/bbs/attach =====

@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/bbs/attach",
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
    path="/openapi/v1/collaboration/tasks/bbs/attach",
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


# ===== POST /openapi/v1/collaboration/tasks/bbs/result =====

@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/bbs/result",
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
    path="/openapi/v1/collaboration/tasks/bbs/result",
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


# ===== POST /openapi/v1/collaboration/tasks/callback/workflow_start =====

@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/callback/workflow_start",
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
    path="/openapi/v1/collaboration/tasks/callback/workflow_start",
    scenario="invalid_body",
    input=CaseInput(raw_body=b"not-json"),
    expect=ExpectError(status=422),
)
def workflow_start_invalid_body():
    """Malformed raw body → model_validate_json fails → 422."""


# ===== POST /openapi/v1/collaboration/tasks/callback/workflow_result =====

@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/callback/workflow_result",
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
    path="/openapi/v1/collaboration/tasks/callback/workflow_result",
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


# ===== POST /openapi/v1/collaboration/tasks/callback/node_start =====

@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/callback/node_start",
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
    path="/openapi/v1/collaboration/tasks/callback/node_start",
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


# ===== POST /openapi/v1/collaboration/tasks/callback/node_result =====

@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/callback/node_result",
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
    path="/openapi/v1/collaboration/tasks/callback/node_result",
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


# ===== POST /openapi/v1/collaboration/tasks/discovery/discover =====

@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/discovery/discover",
    scenario="ok",
    input=CaseInput(query_params={"user_id": "u1", "agent_id": "bot_001"}),
    seed=_seed_discover_ok,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def discovery_discover_ok():
    """Manual discovery trigger over an absent db → {success: true, discovered: 0}."""


@endpoint_test(
    method="POST",
    path="/openapi/v1/collaboration/tasks/discovery/discover",
    scenario="build_failure",
    input=CaseInput(query_params={"user_id": "u1", "agent_id": "bot_001"}),
    seed=_seed_discover_err,
    expect=ExpectError(status=500),
)
def discovery_discover_build_failure():
    """Forced ``_build_service`` raise → handler catches → ``InternalError`` → app ``DomainError`` handler → 500 ``ErrorEnvelope``."""


# ===== GET /openapi/v1/collaboration/tasks/discovery/status =====

@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/discovery/status",
    scenario="build_failure",
    seed=_seed_status_err,
    expect=ExpectError(status=500),
)
def discovery_status_build_failure():
    """Forced ``SqliteTaskReader.read`` raise (inside the try) → ``InternalError`` → app ``DomainError`` handler → 500 ``ErrorEnvelope``."""


@endpoint_test(
    method="GET",
    path="/openapi/v1/collaboration/tasks/discovery/status",
    scenario="ok",
    seed=_seed_status_ok,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000}),
)
def discovery_status_ok():
    """Status read over an absent db → {success: true, total: 0}."""