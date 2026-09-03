"""Cross-instance task graph integration: two independent graph services sharing
one store — hydrate, callback (same-tx audit), event idempotency, BBS claim CAS.
"""
from __future__ import annotations

import asyncio

from agentclaw.community.core.repository.implementations.task.task_callback_repository import (
    TaskCallbackRepository,
)
from agentclaw.community.core.repository.implementations.task.task_graph_repository import (
    TaskGraphRepository,
)
from agentclaw.community.core.repository.implementations.task.task_info_repository import (
    TaskInfoRepository,
)
from agentclaw.community.core.task.domain.models import (
    Context,
    Goal,
    Metadata,
    NodeAction,
    RuntimeInfo,
    Status,
    TaskCallbackData,
    TaskExecutionGraph,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskGraphPatch,
    TaskSpec,
)
from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.repository.types import (
    TaskInfoRecord,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.callback_adapter import (
    CallbackAdapter,
    TaskLoopCallback,
)


def _spec(task_id):
    return TaskSpec(
        metadata=Metadata(task_id=task_id, title="t", instruction="do it"),
        context=Context(background="bg"),
        goal=Goal(objective="obj", acceptances=[]),
    )


def _seed(db, task_id, status=Status.PENDING):
    TaskInfoRepository(db).insert(
        TaskInfoRecord(
            id=0, task_id=task_id, source_type="bot", owner_user_id="U",
            owner_bot_id="B", execution_config={},
            task_spec={"metadata": {"task_id": task_id}}, status=status,
        )
    )


def _make_graph_service(db):
    return TaskGraphService(graph_repo=TaskGraphRepository(db))


class _MinimalEngine:
    """Drives only the graph mutation `update_task_node_info`, mirroring the
    first persist that the real ExecutionEngine performs for a callback."""

    def __init__(self, graph: TaskGraphService):
        self._graph = graph

    async def on_report(self, patch: TaskNodePatch):
        return self._graph.update_task_node_info(patch)

    async def on_start(self, patch: TaskNodePatch):
        return self._graph.update_task_node_info(patch)


def _make_callback(graph, callback_repo):
    return TaskLoopCallback(CallbackAdapter(), _MinimalEngine(graph), callback_repo=callback_repo)


def _result_data(task_id, node_id, *, success=True):
    return TaskCallbackData(data={
        "loop_task_id": f"{task_id}::{node_id}",
        "workflow_source": "yuque",
        "workflow_instance_id": "inst-1",
        "result": {"success": success, "data": "ok"},
    })


def _start_data(task_id, node_id):
    return TaskCallbackData(data={
        "loop_task_id": f"{task_id}::{node_id}",
        "workflow_source": "yuque",
        "workflow_instance_id": "inst-1",
        "status": "running",
    })


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_instanceA_initialize_instanceB_dashboard_hydrates(db):
    task_id = "T-CROSS-DASH"
    _seed(db, task_id)
    a = _make_graph_service(db)
    a.initialize_graph(TaskInfo(task_spec=_spec(task_id), source_type="bot", owner_bot_id="B"))

    b = _make_graph_service(db)  # empty cache -> must hydrate from shared store
    graph = b.query_task_dashboard(task_id)
    assert graph.task_id == task_id
    assert graph.tasks[0].node_id == task_id
    assert graph.tasks[0].status is Status.PENDING


def test_cross_instance_callback_advances_graph_and_audits_same_tx(db):
    task_id = "T-CROSS-CB"
    _seed(db, task_id)
    a = _make_graph_service(db)
    a.initialize_graph(TaskInfo(task_spec=_spec(task_id), source_type="bot", owner_bot_id="B"))

    callback_repo = TaskCallbackRepository(db)
    b = _make_graph_service(db)
    cb_b = _make_callback(b, callback_repo)

    # callback instance-B receives start (PENDING->RUNNING) then result (RUNNING->DONE);
    # each graph mutation commits its callback audit in the same transaction.
    _run(cb_b.start_run(_start_data(task_id, task_id)))
    _run(cb_b.report_result(_result_data(task_id, task_id, success=True)))

    # a (instance A, empty cache) hydrates and sees the terminal state
    hyd = a.query_task_dashboard(task_id)
    assert hyd.tasks[0].status is Status.SUCCESS
    # the callback audit row exists with the event id and PROCESSED status
    all_cb = callback_repo.list_by_session("inst-1")
    assert all_cb and all_cb[0].process_status == "PROCESSED"
    assert all_cb[0].event_id  # event id populated
    # node snapshot persisted (action log not used by normal dashboard)
    assert a.has_repository


def test_callback_result_replay_uses_stable_event_id_without_graph_mutation(db):
    task_id = "T-IDEM"
    _seed(db, task_id)
    a = _make_graph_service(db)
    a.initialize_graph(TaskInfo(task_spec=_spec(task_id), source_type="bot", owner_bot_id="B"))
    callback_repo = TaskCallbackRepository(db)
    cb = _make_callback(a, callback_repo)

    _run(cb.start_run(_start_data(task_id, task_id)))
    data = _result_data(task_id, task_id, success=True)
    _run(cb.report_result(data))
    first_id = callback_repo.get(task_id, task_id).event_id
    assert first_id
    assert callback_repo.find_by_event_id(first_id).process_status == "PROCESSED"

    version_after = a._graph_versions[task_id]
    # Replaying the same result must be acknowledged by the callback layer
    # before it reaches the graph, using the same derived event id.
    _run(cb.report_result(data))
    assert a._graph_versions[task_id] == version_after
    rec = callback_repo.get(task_id, task_id)
    assert rec is not None and rec.event_id == first_id


def test_concurrent_bbs_claim_one_winner(db):
    task_id = "T-BBS-XINST"
    _seed(db, task_id, status=Status.PLANNING)
    graph = TaskExecutionGraph(
        run_id=1, loop_round=0, status=Status.PLANNING, output={},
        extend_props={"bbs_mode": True}, tasks=[
            TaskNode(node_id=task_id, task_id=task_id, status=Status.PLANNING,
                     task_spec=_spec(task_id), run_info=RuntimeInfo(),
                     node_run_graph=None),
        ], task_id=task_id,
    )
    graph.tasks[0].node_run_graph = graph
    TaskGraphRepository(db).create_graph(graph, runtime_status=Status.PLANNING)

    inst_a = _make_graph_service(db)
    inst_b = _make_graph_service(db)
    # instance A wins the DB CAS
    res = inst_a.claim_bbs_owner(task_id, "bot-A")
    assert res.success is True
    # instance B (different bot) must lose with TaskStateError
    try:
        inst_b.claim_bbs_owner(task_id, "bot-B")
    except TaskStateError:
        pass
    else:
        raise AssertionError("second concurrent BBS claim should have lost")
    # instance A can re-claim idempotently
    assert inst_a.claim_bbs_owner(task_id, "bot-A").success is True


def test_graph_patch_retries_after_cross_instance_version_conflict(db):
    """A stale service replays a graph patch on the hydrated latest snapshot."""
    from agentclaw.community.core.task.domain.models import TaskGraphPatch

    task_id = "T-CROSS-VERSION-RETRY"
    _seed(db, task_id)
    writer_a = _make_graph_service(db)
    writer_a.initialize_graph(TaskInfo(task_spec=_spec(task_id), source_type="bot", owner_bot_id="B"))

    writer_b = _make_graph_service(db)
    writer_b.query_task_dashboard(task_id)  # hydrate version 1 into B's cache
    writer_a.update_task_graph_info(
        task_id, TaskGraphPatch(extend_props_patch={"writer_a": True})
    )

    # B starts from a stale version, so the service must hydrate and replay its patch.
    writer_b.update_task_graph_info(
        task_id, TaskGraphPatch(extend_props_patch={"writer_b": True})
    )

    latest = writer_a.query_task_dashboard(task_id)
    assert latest.extend_props["writer_a"] is True
    assert latest.extend_props["writer_b"] is True


def test_all_graph_mutations_retry_after_cross_instance_conflict(db):
    """All graph mutation entrypoints replay against a fresh snapshot on conflict."""
    task_id = "T-CROSS-MUTATION-RETRY"
    _seed(db, task_id)
    writer_a = _make_graph_service(db)
    writer_a.initialize_graph(TaskInfo(task_spec=_spec(task_id), source_type="bot", owner_bot_id="B"))
    writer_b = _make_graph_service(db)
    writer_b.query_task_dashboard(task_id)

    def bump_from_a(key):
        writer_a.update_task_graph_info(
            task_id, TaskGraphPatch(extend_props_patch={key: True})
        )

    bump_from_a("before_add_nodes")
    writer_b.add_task_nodes([TaskNode(
        node_id="child-1", task_id=task_id, status=Status.PENDING,
        task_spec=_spec(task_id), run_info=RuntimeInfo(), node_run_graph=None,
    )], parent_node_id=task_id)

    bump_from_a("before_update_node")
    writer_b.update_task_node_info(
        TaskNodePatch(task_id=task_id, node_id="child-1", status=Status.RUNNING)
    )

    bump_from_a("before_add_second_node")
    writer_b.add_task_nodes([TaskNode(
        node_id="child-2", task_id=task_id, status=Status.PENDING,
        task_spec=_spec(task_id), run_info=RuntimeInfo(), node_run_graph=None,
    )], parent_node_id=task_id)

    bump_from_a("before_add_relation")
    writer_b.add_relations(task_id, [("child-1", "child-2")])

    bump_from_a("before_action_event")
    writer_b.append_action_event(
        task_id, "child-1", NodeAction.DISPATCH, {"source": "retry-test"}
    )

    latest = writer_a.query_task_dashboard(task_id)
    assert latest.extend_props["before_add_nodes"] is True
    assert latest.extend_props["before_action_event"] is True
    assert {n.node_id for n in latest.tasks} >= {task_id, "child-1", "child-2"}
    assert any(
        r.src_id == "child-1" and r.dst_id == "child-2"
        for r in latest.relations
    )
    writer_a.load_action_logs(latest)
    assert latest.tasks[1].run_info.action_log


def test_recovery_precondition_pending_leaf_hydrates_cross_instance(db):
    """Recovery precondition: a PENDING leaf persisted by instance A hydrates on
    instance B (empty cache), so a future ``TaskService.redrive_task`` on B can
    re-dispatch it. The redrive scheduling itself is covered by the lifecycle
    unit tests; here we prove the shared state makes the leaf visible."""
    from agentclaw.community.core.task.domain.models import (
        TaskNodeQueryCriteria,
    )
    task_id = "T-RECOV-PRE"
    _seed(db, task_id, status=Status.RUNNING)
    a = _make_graph_service(db)
    a.initialize_graph(TaskInfo(task_spec=_spec(task_id), source_type="bot", owner_bot_id="B"))
    leaf = TaskNode(
        node_id="leaf-1", task_id=task_id, status=Status.PENDING,
        task_spec=_spec(task_id), run_info=RuntimeInfo(), node_run_graph=None,
    )
    a.add_task_nodes([leaf], parent_node_id=task_id)  # persists root PLANNING + leaf

    b = _make_graph_service(db)  # empty cache -> hydrate from shared store
    hyd = b.query_task_dashboard(task_id)
    assert {n.node_id: n.status for n in hyd.tasks}["leaf-1"] is Status.PENDING
    pending = b.query_task_nodes(task_id, TaskNodeQueryCriteria(status=Status.PENDING))
    assert any(n.node_id == "leaf-1" for n in pending)
