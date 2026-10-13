from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager
import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
from agentclaw.community.core.repository.implementations.task.task_graph_repository import TaskGraphRepository
from agentclaw.community.core.repository.implementations.task.task_info_repository import TaskInfoRepository
from agentclaw.community.core.task.domain.intake import TaskIntakeStatus
from agentclaw.community.core.task.domain.intake import TaskExecutionMode
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult, AcceptanceVerdict, Context, Goal, NodeAction, RuntimeInfo,
    TaskInfo, TaskNode, TaskNodePatch, TaskReportCommand, TaskReportEventType,
    TaskSourceType, TaskSpec,
)
from agentclaw.community.core.task.repository.models import (
    TaskDeliveryIntentModel, TaskInfoModel, TaskNodeModel,
)
from agentclaw.community.core.task.repository.task_intake_repository import TaskIntakeRepository
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_center.task_intake import build_task_info_request
from tests.community.repository.task.test_task_intake_repository import _result


class _DB:
    def __init__(self):
        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.factory = sessionmaker(bind=engine)

    @contextmanager
    def orm_session(self):
        session = self.factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    transactional_orm_session = orm_session


def _setup(*, mode: TaskExecutionMode = TaskExecutionMode.RELAY):
    db = _DB()
    result = replace(
        _result(), status=TaskIntakeStatus.CONFIRMED, execution_mode=mode,
    )
    TaskIntakeRepository(db).create(result)
    repo = TaskGraphRepository(db)
    graph_service = TaskGraphService(graph_repo=repo)
    task_info = TaskInfo(
        task_id="task-1", task_spec=result.task_spec,
        source_type=TaskSourceType.API.value,
        owner_user_id=result.owner_user_id, owner_bot_id="bot-1",
        execution_config={
            "orchestration_mode": mode.value,
            "intake_session_id": result.session_id,
        },
    )
    return db, graph_service, repo, task_info, result


def test_intake_task_root_and_delivery_intent_commit_together() -> None:
    db, graph_service, _, task_info, result = _setup()
    graph = graph_service.initialize_intake_graph(task_info, result.session_id)
    assert graph.tasks[0].run_info.assignee == "bot-1"
    with db.orm_session() as session:
        info = session.query(TaskInfoModel).one()
        root = session.query(TaskNodeModel).one()
        intent = session.query(TaskDeliveryIntentModel).one()
        assert info.intake_session_id == result.session_id
        assert root.node_id == intent.node_id == task_info.task_id
        assert intent.state == "PENDING"


def test_intake_creation_rolls_back_on_graph_write_failure(monkeypatch) -> None:
    db, graph_service, repo, task_info, result = _setup()

    def fail(*args, **kwargs):
        raise RuntimeError("injected node write failure")

    monkeypatch.setattr(repo, "_upsert_current_rows", fail)
    with pytest.raises(RuntimeError, match="injected"):
        graph_service.initialize_intake_graph(task_info, result.session_id)
    with db.orm_session() as session:
        assert session.query(TaskInfoModel).count() == 0
        assert session.query(TaskNodeModel).count() == 0
        assert session.query(TaskDeliveryIntentModel).count() == 0


def test_delivery_intent_claim_is_exclusive_and_recoverable() -> None:
    db, graph_service, repo, task_info, result = _setup()
    graph_service.initialize_intake_graph(task_info, result.session_id)
    relay_token = graph_service.query_task_dashboard("task-1").tasks[0].run_info.extend_props["relay_token"]
    assert repo.list_pending_delivery_intents() == [("task-1", "task-1")]
    state, token = repo.claim_delivery_intent("task-1", "task-1", relay_token)
    assert state == "CLAIMED" and token
    assert repo.claim_delivery_intent("task-1", "task-1", relay_token)[0] == "DELIVERING"
    assert repo.list_pending_delivery_intents() == []

    repo.mark_delivery_intent("task-1", "task-1", token, delivered=False, error="timeout")
    assert repo.list_pending_delivery_intents() == [("task-1", "task-1")]
    state, retry_token = repo.claim_delivery_intent("task-1", "task-1", relay_token)
    assert state == "CLAIMED" and retry_token != token
    repo.mark_delivery_intent("task-1", "task-1", retry_token, delivered=True)
    assert repo.claim_delivery_intent("task-1", "task-1", relay_token) == ("DELIVERED", None)
    with db.orm_session() as session:
        assert session.query(TaskDeliveryIntentModel).one().attempt == 2


def test_retry_reopens_same_node_delivery_with_new_generation() -> None:
    db, graph_service, repo, task_info, result = _setup()
    graph_service.initialize_intake_graph(task_info, result.session_id)
    old_token = graph_service.query_task_dashboard("task-1").tasks[0].run_info.extend_props["relay_token"]
    state, old_lease = repo.claim_delivery_intent("task-1", "task-1", old_token)
    assert state == "CLAIMED" and old_lease
    repo.mark_delivery_intent("task-1", "task-1", old_lease, delivered=True)

    graph_service.update_task_node_info(TaskNodePatch(
        task_id="task-1", node_id="task-1",
        extend_props_patch={"harness_retries": 1},
    ))
    new_token = graph_service.query_task_dashboard("task-1").tasks[0].run_info.extend_props["relay_token"]
    assert new_token != old_token
    assert repo.claim_delivery_intent("task-1", "task-1", old_token) == ("STALE", None)
    assert repo.list_pending_delivery_intents() == [("task-1", "task-1")]
    state, new_lease = repo.claim_delivery_intent("task-1", "task-1", new_token)
    assert state == "CLAIMED" and new_lease
    repo.mark_delivery_intent("task-1", "task-1", new_lease, delivered=True)
    with db.orm_session() as session:
        intent = session.query(TaskDeliveryIntentModel).one()
        assert intent.relay_token == new_token
        assert intent.attempt == 2


def test_execute_confirmed_intake_reuses_the_same_root() -> None:
    db, graph_service, repo, _, result = _setup()
    service = TaskService(
        graph_service, task_info_repo=TaskInfoRepository(db),
        delivery_intents=repo, task_id_provider=lambda: "task-1",
    )
    request = build_task_info_request(result)

    async def create_twice():
        first = await service.execute(request)
        second = await service.execute(request)
        return first, second

    first, second = asyncio.run(create_twice())
    assert first.success and second.success
    assert first.task_id == second.task_id == "task-1"
    assert first.node_id == second.node_id == "task-1"
    with db.orm_session() as session:
        assert session.query(TaskInfoModel).count() == 1
        assert session.query(TaskNodeModel).count() == 1
        assert session.query(TaskDeliveryIntentModel).count() == 1


def test_execute_confirmed_centralized_intake_reuses_root_without_delivery_intent() -> None:
    db, graph_service, repo, _, result = _setup(mode=TaskExecutionMode.CENTRALIZED)
    service = TaskService(
        graph_service, task_info_repo=TaskInfoRepository(db),
        delivery_intents=repo, task_id_provider=lambda: "task-centralized",
    )
    request = build_task_info_request(result)

    async def create_twice():
        first = await service.execute(request)
        second = await service.execute(request)
        return first, second

    first, second = asyncio.run(create_twice())
    assert first.success and second.success
    assert first.task_id == second.task_id == "task-centralized"
    assert first.extend_props is None
    assert second.extend_props["orchestration_mode"] == "centralized"
    with db.orm_session() as session:
        assert session.query(TaskInfoModel).count() == 1
        assert session.query(TaskNodeModel).count() == 1
        assert session.query(TaskDeliveryIntentModel).count() == 0


def test_dispatch_node_acknowledges_only_after_real_dispatch() -> None:
    db, graph_service, repo, task_info, result = _setup()
    graph_service.initialize_intake_graph(task_info, result.session_id)
    service = TaskService(graph_service, delivery_intents=repo)

    class Dispatcher:
        calls = 0

        async def dispatch(self, node):
            self.calls += 1
            return node

    dispatcher = Dispatcher()
    service._relay_adapter._dispatcher = dispatcher

    async def dispatch_twice():
        first = await service.dispatch_node("task-1", "task-1")
        second = await service.dispatch_node("task-1", "task-1")
        return first, second

    first, second = asyncio.run(dispatch_twice())
    assert first.node_id == second.node_id == "task-1"
    assert dispatcher.calls == 1
    actions = repo.load_action_logs("task-1")["task-1"]
    assert actions[-1].action is NodeAction.DISPATCH
    assert actions[-1].payload["action_result"] == "delivered"
    with db.orm_session() as session:
        assert session.query(TaskDeliveryIntentModel).one().state == "DELIVERED"


def test_report_and_successor_delivery_intent_commit_together() -> None:
    db, graph_service, _, task_info, result = _setup()
    graph_service.initialize_intake_graph(task_info, result.session_id)
    relay_token = graph_service.query_task_dashboard("task-1").tasks[0].run_info.extend_props["relay_token"]
    execution = TaskReportCommand(
        task_id="task-1", node_id="task-1",
        event_id="execution-1", relay_token=relay_token,
        event_type=TaskReportEventType.EXECUTION_RESULT,
        event=TaskNodePatch(
            task_id="task-1", node_id="task-1", execution_decision="ACCEPTED",
            actual_goal=Goal("完成首棒", []),
            local_acceptance_result=AcceptanceResult(AcceptanceVerdict.DONE),
            output_patch={"result": "首棒事实"},
        ),
    )
    graph_service.apply_relay_report(execution)
    plan = TaskReportCommand(
        task_id="task-1", node_id="task-1",
        event_id="plan-1", relay_token=relay_token,
        event_type=TaskReportEventType.PLAN_RESULT,
        event=TaskNode(
            node_id=None, task_id="task-1", status=None,
            task_spec=TaskSpec(Context("下一棒", title="下一棒"), Goal("完成剩余目标", [])),
            run_info=RuntimeInfo(run_mode="single_bot", assignee="bot-2"),
            node_run_graph=None,
        ),
    )
    planned = graph_service.apply_relay_report(plan)
    assert planned.target_node_id is not None
    with db.orm_session() as session:
        assert session.query(TaskNodeModel).count() == 2
        assert session.query(TaskDeliveryIntentModel).count() == 2
        assert session.query(TaskDeliveryIntentModel).filter_by(node_id=planned.target_node_id).one().state == "PENDING"

    # Another service instance hydrates the same immutable result after a lost response.
    other = TaskGraphService(graph_repo=TaskGraphRepository(db))
    assert other.apply_relay_report(plan) == planned
    assert other.get_node_context("task-1", "task-1").accepted_plan_report == planned
    assert other.get_node_context("task-1", planned.target_node_id).all_done_output[0].node_id == "task-1"


def test_bbs_plan_registers_durable_publish_intent() -> None:
    db, graph_service, _, task_info, result = _setup()
    graph_service.initialize_intake_graph(task_info, result.session_id)
    relay_token = graph_service.query_task_dashboard("task-1").tasks[0].run_info.extend_props["relay_token"]
    graph_service.apply_relay_report(TaskReportCommand(
        task_id="task-1", node_id="task-1",
        event_id="execution-1", relay_token=relay_token,
        event_type=TaskReportEventType.EXECUTION_RESULT,
        event=TaskNodePatch(
            task_id="task-1", node_id="task-1", execution_decision="DECLINED",
        ),
    ))
    planned = graph_service.apply_relay_report(TaskReportCommand(
        task_id="task-1", node_id="task-1",
        event_id="plan-1", relay_token=relay_token,
        event_type=TaskReportEventType.PLAN_RESULT,
        event=TaskNode(
            node_id=None, task_id="task-1", status=None,
            task_spec=TaskSpec(Context("待认领", title="待认领"), Goal("补齐目标", [])),
            run_info=RuntimeInfo(run_mode="bbs"), node_run_graph=None,
        ),
    ))
    assert planned.bbs_publish_pending
    with db.orm_session() as session:
        assert session.query(TaskDeliveryIntentModel).filter_by(node_id=planned.target_node_id).one().state == "PENDING"
    assert graph_service.query_task_dashboard("task-1").extend_props["bbs_mode"] is True
