"""TDD for community Noop task impls (Phase 0.7).

Noop impls let DI wire a default binding so the router/smoke can run before the
real TaskService/TaskScheduler land (Phase 2/3). They never raise; they return
neutral empties. Noop is NOT a plugin_api ``@plugin_impl`` — TaskService/Ports
are api-layer business Protocols (not infrastructure Plugins), so they bind via
injector ``@provider`` like aicoding's WorkspaceServiceProtocol, not via the
impl_registry.
"""
from __future__ import annotations

import pytest

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
from agentclaw.community.core.task.domain.models import RouteClass, RunMode, TaskState
from agentclaw.community.plugins.community.task import (
    NoopBcsCollaborationPort,
    NoopBotDiscoverPort,
    NoopDecomposerPort,
    NoopExecutionPort,
    NoopTaskDriverPort,
    NoopTaskService,
)


# --- structural conformance to Protocols ------------------------------------

@pytest.mark.parametrize(
    "proto,noop",
    [
        (TaskService, NoopTaskService()),
        (BotDiscoverPort, NoopBotDiscoverPort()),
        (DecomposerPort, NoopDecomposerPort()),
        (TaskDriverPort, NoopTaskDriverPort()),
        (ExecutionPort, NoopExecutionPort()),
        (BcsCollaborationProtocol, NoopBcsCollaborationPort()),
    ],
)
def test_noop_satisfies_protocol(proto, noop):
    assert isinstance(noop, proto)


# --- TaskService Noop never raises, returns neutral -------------------------

def test_noop_task_service_query_faces():
    s = NoopTaskService()
    assert s.get("t1") is None
    assert s.list_by_user("u1") == []
    assert s.progress("t1") == {}


def test_noop_task_service_intake_faces():
    s = NoopTaskService()
    # create returns a Task-shaped object with id + INTAKE status
    t = s.create(title="x", source="api", background="b")
    assert t is not None
    assert getattr(t, "id", None) is not None
    # clarify / clarify(confirmed) are no-ops returning None (no real impl yet)
    assert s.clarify("t1", {"g": "x"}) is None
    assert s.clarify("t1", {}, confirmed=True) is None


def test_noop_task_service_on_event_and_claim():
    s = NoopTaskService()
    ev = TaskEvent(task_id="t1", seq=1, kind="task.created")
    assert s.on_event(ev) is None
    assert s.claim_node("t1", "n1", "b1") is None


# --- TaskService Noop canvas query face (Phase 0.8, plan §1.4b) -------------

def test_noop_task_service_canvas_query_faces_never_raise():
    """get_task_graph / get_node_detail / get_sub_dag return neutral snapshots
    (or None for sub_dag without a ref) so the canvas smoke can run before the
    real query group (Phase 2) / SmGraphAdapter (Phase 4) land."""
    s = NoopTaskService()
    g = s.get_task_graph("t1")
    assert g is not None
    # snapshot is a dict-shaped object the router can coerce into TaskGraphView
    assert _attr(g, "task_id") == "t1" or _attr(g, "root_phase") is not None

    d = s.get_node_detail("t1", "n1")
    assert d is not None
    assert _attr(d, "node_id") == "n1"

    # sub_dag without a ref returns None (router → 404, not a raise)
    assert s.get_sub_dag("t1", "n1") is None


def test_noop_task_service_subscribe_task_graph_is_async_iterable_or_none():
    s = NoopTaskService()
    subscribe = getattr(s, "subscribe_task_graph", None)
    # Phase 0 skeleton: either absent or a no-op async generator
    if subscribe is not None:
        import inspect
        assert inspect.iscoroutinefunction(subscribe) or inspect.isasyncgenfunction(subscribe)


def _attr(obj, name):
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


# --- BcsCollaboration Noop returns a fake SM graph (canvas bring-up) --------

def test_noop_bcs_collab_returns_fake_sm_graph():
    """NoopBcsCollaborationPort returns a伪造 StateMachineRunGraphView-shaped
    snapshot so the canvas + SmGraphAdapter can be brought up before real BCS
    wiring (Phase 4)."""
    p = NoopBcsCollaborationPort()
    snap = p.fetch_state_machine_run_graph("sm-1")
    assert snap is not None
    # fake snapshot carries run + nodes + edges (SM graph shape)
    assert "run" in snap or "nodes" in snap

    detail = p.fetch_node_detail("sm-1", "n1")
    assert detail is not None
    assert "node" in detail or "node_id" in detail


# --- Port Noops return neutral DTOs -----------------------------------------

def test_noop_discover_returns_empty_recommendation():
    r = NoopBotDiscoverPort().recommend("t1", "n1")
    assert isinstance(r, RouteRecommendation)
    assert r.candidates == []
    assert r.confidence == 0.0


def test_noop_decomposer_returns_empty_children():
    subs = NoopDecomposerPort().decompose_subtasks("spec", TaskState())
    assert subs == []


def test_noop_driver_returns_dispatch_result():
    d = NoopTaskDriverPort().dispatch_node("t1", "n1")
    assert isinstance(d, DispatchResult)
    assert d.node_id == "n1"
    redispatch = NoopTaskDriverPort().redispatch("t1", "n1", RouteClass.C5)
    assert isinstance(redispatch, DispatchResult)
    esc = NoopTaskDriverPort().escalate_to_bbs("t1")
    assert isinstance(esc, DispatchResult)


def test_noop_execution_all_four_methods():
    e = NoopExecutionPort()
    assert isinstance(e.dispatch_single_bot("t1", "n1", "b1"), DispatchResult)
    assert isinstance(e.coop_group("t1", "n1", ["b1", "b2"]), DispatchResult)
    assert isinstance(e.redispatch_node("t1", "n1", "b1"), DispatchResult)
    assert isinstance(e.bbs("t1", "n1"), DispatchResult)


# --- DispatchResult Noop run_mode default -----------------------------------

def test_noop_driver_dispatch_result_run_mode_single_bot():
    d = NoopTaskDriverPort().dispatch_node("t1", "n1")
    assert d.run_mode is RunMode.SINGLE_BOT
    assert d.executor_id == ""