from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.task.domain.errors import TaskStateError
from agentclaw.community.core.task.domain.models import (
    TaskCallbackData,
    Context,
    Goal,
    Relation,
    RelationType,
    RuntimeInfo,
    Status,
    TaskExecutionGraph,
    TaskGraphPatch,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.task_center.recovery_lifecycle import (
    TaskRecoveryLifecycle,
)
from agentclaw.community.core.task.task_center.task_service_execution import (
    TaskServiceExecutionMixin,
)
from agentclaw.community.core.task.task_center.task_service_queries import (
    _attach_assignee_bot_info,
    _enrich_bbs_publisher_names,
    _enrich_task_owner_display,
    _hydrate_root_dashboard_runtime,
)
from agentclaw.community.core.task.task_center.task_service_relay import (
    TaskServiceRelayMixin,
)
from agentclaw.community.core.task.task_context.task_graph_service import (
    TaskGraphService,
    _clear_pending_callback_audit,
)
from agentclaw.community.core.task.task_context.task_graph_support import (
    _dispatch_report,
)
from tests.community.core.task.test_task_center_cov_gaps import (
    _bbs_overview_record,
    _info,
    _info_record,
)
from tests.community.core.task.task_center.test_relay_execution import _child_spec


def _node(node_id: str, *, assignee: str = "", owner_id: str = "") -> TaskNode:
    return TaskNode(
        node_id=node_id,
        task_id="t1",
        status=Status.RUNNING,
        task_spec=TaskSpec(
            context=Context(background="", title=node_id),
            goal=Goal(objective="goal", acceptances=[]),
        ),
        run_info=RuntimeInfo(
            run_mode="single_bot",
            assignee=assignee,
            extend_props={"assignee_owner_id": owner_id} if owner_id else {},
        ),
        node_run_graph=None,
    )


def test_recovery_shutdown_dead_thread_and_empty_recovery_result():
    lifecycle = TaskRecoveryLifecycle.__new__(TaskRecoveryLifecycle)
    lifecycle._enabled = True
    lifecycle._stop_event = SimpleNamespace(
        set=MagicMock(), is_set=MagicMock(side_effect=[False, True]), wait=MagicMock()
    )
    lifecycle._interval = 1
    lifecycle._thread = SimpleNamespace(is_alive=lambda: False, join=MagicMock())

    import asyncio

    asyncio.run(lifecycle.shutdown())
    lifecycle._thread.join.assert_not_called()

    worker = SimpleNamespace(recover_once=MagicMock())

    async def recover_once(*, limit):
        assert limit == 100
        return []

    worker.recover_once = recover_once
    lifecycle._resolve_worker = lambda: worker
    lifecycle._loop()
    lifecycle._stop_event.wait.assert_called_once()


def test_persist_node_run_allows_each_legacy_repository_to_be_absent():
    info = SimpleNamespace(task_spec=SimpleNamespace(to_dict=lambda: {}))
    base = {
        "_graph": SimpleNamespace(has_repository=False),
        "_task_node_repo": None,
        "_run_info_repo": None,
    }
    owner = SimpleNamespace(**base)
    TaskServiceExecutionMixin._persist_node_run(
        owner,
        "t1",
        info,
        run_mode="single_bot",
        assignee="b1",
        session_id="s1",
    )

    node_repo = SimpleNamespace(insert=MagicMock())
    owner._task_node_repo = node_repo
    TaskServiceExecutionMixin._persist_node_run(
        owner,
        "t1",
        info,
        run_mode="single_bot",
        assignee="b1",
        session_id="s1",
    )
    node_repo.insert.assert_called_once()


def test_dashboard_hydration_and_assignee_lookup_cache_edges():
    root = _node("t1", assignee="already", owner_id="already-owner")
    graph = TaskExecutionGraph(
        task_id="t1",
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        tasks=[root],
        extend_props={"source_type": "bot", "execution_config": {}},
    )
    _hydrate_root_dashboard_runtime(graph, "t1")
    assert root.run_info.extend_props["assignee_owner_id"] == "already-owner"

    calls = []

    class _Bots:
        def list_bots_by_owner_bot_pairs(self, *, pairs, page, page_size):
            calls.append(pairs)
            return {"items": [{"bot_id": "b1", "owner_id": "u1", "bot_name": "B"}]}

        def get_bot_by_id(self, bot_id):
            calls.append(bot_id)
            return {"owner_id": "legacy", "bot_name": "Legacy"}

    graph.tasks = [
        _node("n1", assignee="b1:u1", owner_id="u1"),
        _node("n2", assignee="b1:u1", owner_id="u1"),
        _node("n3", assignee="legacy"),
        _node("n4", assignee="legacy"),
    ]
    _attach_assignee_bot_info(SimpleNamespace(_bot_service=_Bots()), graph)
    assert calls == [[("b1", "u1")], "legacy"]

    graph.tasks = [_node("n5", assignee="b2:u2", owner_id="u2")]
    _attach_assignee_bot_info(
        SimpleNamespace(_bot_service=SimpleNamespace(get_bot_by_id=lambda _id: {})),
        graph,
    )
    assert "assignee_name" not in graph.tasks[0].run_info.extend_props


def test_list_enrichment_empty_pairs_and_invalid_catalog_items():
    bots = SimpleNamespace(
        list_bots_by_owner_bot_pairs=lambda **_kwargs: {
            "items": ["bad", {"bot_id": "", "owner_id": "u1"}]
        }
    )
    owner = SimpleNamespace(_bot_service=bots, _staff_dept=None)
    record = _info_record("", "")
    assert _enrich_task_owner_display(owner, [record])[0].owner_bot_name is None

    valid_record = _info_record("b1", "u1")
    assert _enrich_task_owner_display(owner, [valid_record])[0].owner_bot_name is None

    bbs = _bbs_overview_record()
    assert _enrich_bbs_publisher_names(owner, [bbs])[0].publisher_name is None
    no_pairs = bbs.__class__(**{**bbs.__dict__, "publisher": "", "owner_user_id": ""})
    assert _enrich_bbs_publisher_names(owner, [no_pairs])[0].publisher_name is None


def test_legacy_relay_plan_supplies_default_gap():
    captured = {}
    owner = SimpleNamespace(
        _graph=SimpleNamespace(_execution_config=lambda _task_id: {}),
        _report_fact=lambda kind, payload: captured.update(kind=kind, payload=payload)
        or payload,
    )
    result = TaskServiceRelayMixin._apply_plan_result(
        owner,
        SimpleNamespace(task_id="t1"),
        SimpleNamespace(node_id="n1"),
        "holder",
        {"has_gap": True, "children": [{"task_spec": _child_spec()}]},
        "progress",
        "failure",
    )
    assert result["gaps"] == ["failure"]
    assert result["next_task_spec"].goal.objective


def test_graph_repository_hydration_restore_none_and_callback_identity():
    graph = TaskExecutionGraph(task_id="t1", run_id=1, loop_round=0, status=Status.PENDING)

    class _Repo:
        def __init__(self):
            self.loads = 0

        def load_graph(self, task_id):
            self.loads += 1
            return graph if self.loads == 1 else None

        def get_version(self, task_id):
            return 2

        def save_graph(self, *args, **kwargs):
            raise RuntimeError("write failed")

    service = TaskGraphService(graph_repo=_Repo())
    assert service._require_graph("t1") is graph
    with pytest.raises(RuntimeError, match="write failed"):
        service._persist_locked(graph)

    sentinel = object()
    from agentclaw.community.core.task.task_runner.callback_adapter import (
        _PENDING_CALLBACK_AUDIT,
    )

    reset_handle = _PENDING_CALLBACK_AUDIT.set(sentinel)
    try:
        _clear_pending_callback_audit(object())
        assert _PENDING_CALLBACK_AUDIT.get() is sentinel
    finally:
        _PENDING_CALLBACK_AUDIT.reset(reset_handle)


def test_graph_empty_output_and_delete_relation_loop_edges():
    service = TaskGraphService()
    service.initialize_graph(_info("t1"))
    service.update_task_graph_info(
        "t1", TaskGraphPatch(output_patch={}, extend_props_patch={"x": 1})
    )

    graph = service.query_task_dashboard("t1")
    child = _node("n1")
    child.task_id = "t1"
    graph.tasks.append(child)
    service.delete_task_node("t1", "n1")
    assert all(node.node_id != "n1" for node in graph.tasks)

    graph.tasks.extend([_node("n2"), _node("n3")])
    graph.relations.extend(
        [
            Relation(src_id="n2", dst_id="n3", type=RelationType.DEPENDENCY),
            Relation(src_id="n2", dst_id="n3", type=RelationType.DEPENDENCY),
        ]
    )
    service.delete_task_node("t1", "n2")
    assert all(node.node_id not in {"n2", "n3"} for node in graph.tasks)


def test_unknown_relay_turn_report_falls_through_to_error():
    with pytest.raises(TaskStateError, match="unsupported graph report_type"):
        _dispatch_report(
            SimpleNamespace(),
            TaskCallbackData(data={
                "report_type": "RELAY_TURN_UNKNOWN",
                "payload": {"task_id": "t1", "node_id": "n1", "holder_id": "h1"},
            }),
        )


def test_hydration_without_owner_bot_leaves_assignee_empty():
    root = _node("t1")
    graph = TaskExecutionGraph(
        task_id="t1",
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        tasks=[root],
        extend_props={"source_type": "bot", "execution_config": {}},
    )
    _hydrate_root_dashboard_runtime(graph, "t1")
    assert root.run_info.assignee == ""


def test_legacy_relay_plan_preserves_existing_gaps():
    owner = SimpleNamespace(
        _graph=SimpleNamespace(_execution_config=lambda _task_id: {}),
        _report_fact=lambda _kind, payload: payload,
    )
    result = TaskServiceRelayMixin._apply_plan_result(
        owner,
        SimpleNamespace(task_id="t1"),
        SimpleNamespace(node_id="n1"),
        "holder",
        {
            "gaps": ["existing"],
            "has_gap": True,
            "children": [{"task_spec": _child_spec()}],
        },
        "progress",
        "failure",
    )
    assert result["gaps"] == ["existing"]


def test_manager_worker_event_without_callback_repo_and_nonterminal_event():
    from tests.community.core.task.test_task_center_cov_gaps import (
        _build_service,
        _mw_completed_event,
        _run,
    )

    service, _ = _build_service(callback_repo=None)
    service.converge_by_session = AsyncMock()
    _run(service.apply_manager_worker_event(_mw_completed_event()))
    service.converge_by_session.reset_mock()

    started = _mw_completed_event()
    started["event_type"] = "session.started"
    _run(service.apply_manager_worker_event(started))
    service.converge_by_session.assert_not_called()


def test_anniversary_enrichment_ignores_bot_without_name():
    from tests.community.core.task.test_task_center_cov_gaps import (
        _build_service,
        _request,
    )

    service, _ = _build_service(
        bot_service=SimpleNamespace(get_bot_by_id=lambda _bot_id: {"bot_id": "b1"})
    )
    request = _request()
    request.execution_config["static_plan_id"] = "merchant-operations-goal-to-plan"
    graph = TaskExecutionGraph(
        task_id="t1",
        run_id=1,
        loop_round=0,
        status=Status.PENDING,
        extend_props={"owner_bot_id": "b1"},
    )
    service._enrich_anniversary_trigger_bot_name(request, graph)
    assert "trigger_bot_name" not in graph.extend_props


def test_materialize_static_plan_keeps_required_input_empty_without_fallback(monkeypatch):
    from dataclasses import replace

    from agentclaw.community.core.task.domain.requests import (
        RequestContext,
        RequestGoal,
        RequestTaskSpec,
    )
    from agentclaw.community.core.task.task_plan.static_plan import StaticPlanDefinition
    from tests.community.core.task.test_task_center_cov_gaps import (
        _build_service,
        _request,
    )

    service, _ = _build_service()
    service._resolve_static_plan_template_id = lambda _request: "okr-implementation"
    definition = SimpleNamespace(
        input_schema={
            "required_empty": {"required": True},
            "second": {"required": False},
        },
        validate_input=MagicMock(),
        validate_bindings=MagicMock(),
        nodes=[],
        entry_bot_id="",
    )
    monkeypatch.setattr(
        StaticPlanDefinition, "from_file", classmethod(lambda cls, *args, **kwargs: definition)
    )
    request = replace(
        _request(),
        execution_config={},
        task_spec=RequestTaskSpec(
            context=RequestContext(title="", background=""),
            goal=RequestGoal(objective="", acceptances=[]),
        ),
    )
    materialized = service._materialize_static_plan_if_needed(request)
    assert materialized.execution_config["template_input"] == {}
    definition.validate_input.assert_called_once_with({})


def test_manager_worker_nonterminal_accepted_event_exits_without_converging():
    from tests.community.core.task.test_task_center_cov_gaps import (
        _build_service,
        _run,
    )

    service, _ = _build_service(callback_repo=None)
    service.converge_by_session = AsyncMock()
    event = {
        "event_id": "evt-created",
        "event_type": "session.created",
        "scope": {"session_id": "sess-1", "group_id": "grp-1"},
        "data": {},
    }
    _run(service.apply_manager_worker_event(event))
    service.converge_by_session.assert_not_called()


def test_delete_node_relation_false_and_already_pruned_child_edges():
    service = TaskGraphService()
    service.initialize_graph(_info("t1"))
    graph = service._graphs["t1"]
    for node_id in ("n1", "n2"):
        node = _node(node_id)
        node.task_id = "t1"
        graph.tasks.append(node)
    graph.relations.extend(
        [
            SimpleNamespace(src_id="n1", dst_id="n2", type="OTHER"),
            Relation(src_id="n1", dst_id="n1", type=RelationType.DEPENDENCY),
        ]
    )
    service.delete_task_node("t1", "n1")
    assert all(node.node_id != "n1" for node in service._graphs["t1"].tasks)


def test_claim_bbs_node_preserves_existing_start_time():
    from tests.community.core.task.test_context_dispatch_plan_cov_gaps import (
        FakeGraphRepo,
        _child,
        _task_info,
    )

    service = TaskGraphService(FakeGraphRepo())
    service.initialize_graph(_task_info("t1"))
    service.update_task_graph_info(
        "t1", TaskGraphPatch(extend_props_patch={"bbs_mode": True})
    )
    service.add_task_nodes([_child("b1")], parent_node_id="t1")
    graph = service._graphs["t1"]
    child = next(node for node in graph.tasks if node.node_id == "b1")
    child.run_info.run_mode = "bbs"
    child.run_info.start_time = 123
    service._persist_locked(graph)

    service.claim_bbs_owner("t1", "botX", node_id="b1", claim_id="claim-1")
    claimed = next(
        node for node in service.query_task_dashboard("t1").tasks if node.node_id == "b1"
    )
    assert claimed.run_info.start_time == 123
