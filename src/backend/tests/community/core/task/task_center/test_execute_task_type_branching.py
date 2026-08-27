import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agentclaw.community.core.base import Base
import agentclaw.community.core.task.repository.models  # noqa: F401
import agentclaw.community.core.task_queue.repository.models  # noqa: F401
from agentclaw.community.core.repository.implementations.task.task_node_repository import (
    TaskNodeRepository,
)
from agentclaw.community.core.repository.implementations.task.task_node_run_info_repository import (
    TaskNodeRunInfoRepository,
)
from agentclaw.community.core.task.domain.models import Status, TaskSourceType, TaskType
from agentclaw.community.core.task.domain.requests import (
    RequestAcceptance, RequestContext, RequestGoal, RequestMetadata,
    RequestTaskSpec, TaskInfoRequest,
)
from agentclaw.community.core.task.task_center.engine import CoopGroupStart
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


class _SqliteDB:
    def __init__(self, engine):
        self._f = sessionmaker(bind=engine, autoflush=False)
    @contextmanager
    def orm_session(self):
        db = self._f()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


class _FakeEngine:
    """Stands in for ExecutionEngine; records calls."""
    def __init__(self, graph):
        self._graph = graph
        self.workflow_session = "wf-session-1"
        self.group_start = CoopGroupStart(group_id="grp-1", session_id="yaml-session-1")
        self.calls: list[tuple] = []
    async def on_execute(self, task_id):
        self.calls.append(("on_execute", task_id))


def _request(task_type: TaskType, **xec) -> TaskInfoRequest:
    return TaskInfoRequest(
        task_spec=RequestTaskSpec(
            metadata=RequestMetadata(title="T", instruction="do"),
            context=RequestContext(background="bg"),
            goal=RequestGoal(objective="o", acceptances=[RequestAcceptance(id="a", acceptance="d")]),
        ),
        source_type=TaskSourceType.API,
        owner_user_id="u1",
        owner_bot_id="b1",
        execution_config={"task_type": task_type, **xec},
    )


def _repos():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    db = _SqliteDB(eng)
    return TaskNodeRepository(db), TaskNodeRunInfoRepository(db)


def _service(task_type_stub_engine):
    graph = TaskGraphService()
    node_repo, run_repo = _repos()
    svc = TaskService(graph, task_info_repo=None, task_node_repo=node_repo,
                      task_node_run_info_repo=run_repo, task_id_provider=lambda: "t1")
    svc._engine = task_type_stub_engine  # inject the fake engine
    return svc, node_repo, run_repo


def test_execute_workflow_persists_session_id():
    eng = _FakeEngine(graph=None)
    # patch the engine method used by execute:
    async def trig(*, task_id, bot_id, message):
        eng.calls.append(("workflow", task_id, bot_id, message))
        from agentclaw.community.core.task.task_runner.integration.ports import BotSendResult
        return BotSendResult(run_id="r", session_id=eng.workflow_session)
    eng.trigger_single_bot_workflow = trig
    svc, node_repo, run_repo = _service(eng)

    result = asyncio.new_event_loop().run_until_complete(svc.execute(_request(TaskType.STATIC_SINGLE_WORKFLOW, workflow_id="wf", args=["1", "2"])))
    assert result.success is True and result.task_id == "t1"
    run = run_repo.get_latest("t1", "t1")
    assert run is not None and run.run_mode == "single_bot" and run.session_id == "wf-session-1"
    assert run.assignee == "b1"
    node = node_repo.get("t1", "t1")
    assert node is not None and node.status is Status.RUNNING
    assert ("workflow", "t1", "b1", "/wf 1 2") in eng.calls
    # session_id surfaces in the persisted run_info extend_props AND the dashboard
    # (root node's in-memory run_info.extend_props, serialized by graph_to_dto).
    assert run.extend_props == {"session_id": "wf-session-1"}
    dash = svc.get_task_dashboard("t1")
    assert dash.tasks[0].run_info.extend_props == {"session_id": "wf-session-1"}


def test_execute_yaml_persists_session_id_with_state_machine():
    eng = _FakeEngine(graph=None)
    async def start(gf):
        eng.calls.append(("yaml", gf.collab_mode, gf.extend_props.get("definition_yaml")))
        return eng.group_start
    eng.start_coop_group = start
    svc, node_repo, run_repo = _service(eng)

    result = asyncio.new_event_loop().run_until_complete(
        svc.execute(_request(TaskType.STATIC_GROUP_WORKFLOW, yaml="def: x", participant_bot_ids=["b2"])))
    assert result.success is True
    run = run_repo.get_latest("t1", "t1")
    assert run.run_mode == "coop_group" and run.session_id == "yaml-session-1" and run.assignee == "grp-1"
    assert ("yaml", "state_machine", "def: x") in eng.calls
    # group_id + session_id surface in run_info extend_props (DB + dashboard root node).
    assert run.extend_props == {"group_id": "grp-1", "session_id": "yaml-session-1"}
    dash = svc.get_task_dashboard("t1")
    assert dash.tasks[0].run_info.extend_props == {"group_id": "grp-1", "session_id": "yaml-session-1"}


def test_execute_yaml_without_yaml_uses_manager_worker():
    eng = _FakeEngine(graph=None)
    async def start(gf):
        eng.calls.append(("yaml", gf.collab_mode))
        return eng.group_start
    eng.start_coop_group = start
    svc, _, _ = _service(eng)
    asyncio.new_event_loop().run_until_complete(svc.execute(_request(TaskType.STATIC_GROUP_WORKFLOW)))
    assert ("yaml", "manager_worker") in eng.calls


def test_execute_dynamic_unchanged():
    eng = _FakeEngine(graph=None)
    svc, _, _ = _service(eng)
    result = asyncio.new_event_loop().run_until_complete(svc.execute(_request(TaskType.DYNAMIC)))
    assert result.success is True
    assert ("on_execute", "t1") in eng.calls


def test_execute_yaml_forwards_participant_bindings_to_group():
    """execution_config 中的 participant_bindings 经 _run_yaml 透传进 GroupFormation.extend_props
    (创建 bcn 协作群接口的入参,非 yaml 模板内字段),供 TaskExecutor.form_coop_group 注入 BCS create_group。
    群 master 复用底层 driver_bot(bot_ids[0]=owner),不另设 master_bot 字段。"""
    captured: dict = {}
    eng = _FakeEngine(graph=None)

    async def start(gf):
        captured["extend_props"] = dict(gf.extend_props)
        eng.calls.append(("yaml", gf.collab_mode))
        return eng.group_start

    eng.start_coop_group = start
    svc, _, _ = _service(eng)

    bindings = {
        "writer": {"bot_ids": ["b2"], "source": "manual"},
        "editor": {"bot_ids": ["b3"], "source": "manual"},
    }
    result = asyncio.new_event_loop().run_until_complete(svc.execute(_request(
        TaskType.STATIC_GROUP_WORKFLOW, yaml="def: x", participant_bot_ids=["b2", "b3"],
        participant_bindings=bindings)))
    assert result.success is True
    ep = captured["extend_props"]
    assert ep.get("definition_yaml") == "def: x"
    assert ep.get("participant_bindings") == bindings
    # 任务描述(目标)从 task_spec.goal.objective 透传进 extend_props(→ BCS 建群 context → <GroupContext> 目标)
    assert ep.get("task_context") == "o"


# ===== execution_config.group_kind(补充,不破坏既有 has_yaml→state_machine 判断) =====

def _start_recording(eng):
    async def start(gf):
        eng.calls.append(("yaml", gf.collab_mode))
        return eng.group_start
    eng.start_coop_group = start


def test_execute_yaml_group_kind_chat_without_yaml():
    """group_kind=chat + 无 yaml → collab_mode=chat(新增口子)。"""
    eng = _FakeEngine(graph=None)
    _start_recording(eng)
    svc, _, _ = _service(eng)
    asyncio.new_event_loop().run_until_complete(
        svc.execute(_request(TaskType.STATIC_GROUP_WORKFLOW, group_kind="chat")))
    assert ("yaml", "chat") in eng.calls


def test_execute_yaml_group_kind_explicit_manager_worker_without_yaml():
    """group_kind=manager_worker + 无 yaml → collab_mode=manager_worker(显式确认,现有默认)。"""
    eng = _FakeEngine(graph=None)
    _start_recording(eng)
    svc, _, _ = _service(eng)
    asyncio.new_event_loop().run_until_complete(
        svc.execute(_request(TaskType.STATIC_GROUP_WORKFLOW, group_kind="manager_worker")))
    assert ("yaml", "manager_worker") in eng.calls


def test_execute_yaml_group_kind_absent_without_yaml_still_manager_worker():
    """group_kind 缺省 + 无 yaml → manager_worker(既有默认不变)。"""
    eng = _FakeEngine(graph=None)
    _start_recording(eng)
    svc, _, _ = _service(eng)
    asyncio.new_event_loop().run_until_complete(svc.execute(_request(TaskType.STATIC_GROUP_WORKFLOW)))
    assert ("yaml", "manager_worker") in eng.calls


def test_execute_yaml_group_kind_state_machine_without_yaml_raises():
    """group_kind=state_machine 但无 yaml 定义 → ValueError(没定义跑不了 state_machine)。"""
    eng = _FakeEngine(graph=None)
    _start_recording(eng)
    svc, _, _ = _service(eng)
    with pytest.raises(ValueError):
        asyncio.new_event_loop().run_until_complete(
            svc.execute(_request(TaskType.STATIC_GROUP_WORKFLOW, group_kind="state_machine")))
    assert not any(c[0] == "yaml" for c in eng.calls)   # 没建群


def test_execute_yaml_group_kind_unknown_raises():
    """group_kind 未知值 → ValueError。"""
    eng = _FakeEngine(graph=None)
    _start_recording(eng)
    svc, _, _ = _service(eng)
    with pytest.raises(ValueError):
        asyncio.new_event_loop().run_until_complete(
            svc.execute(_request(TaskType.STATIC_GROUP_WORKFLOW, group_kind="nonsense")))


def test_execute_yaml_with_yaml_ignores_group_kind_chat():
    """有 yaml → state_machine(既有 has_yaml 判断不变;group_kind=chat 被忽略,不介入)。"""
    eng = _FakeEngine(graph=None)

    async def start(gf):
        eng.calls.append(("yaml", gf.collab_mode, gf.extend_props.get("definition_yaml")))
        return eng.group_start
    eng.start_coop_group = start
    svc, _, _ = _service(eng)
    asyncio.new_event_loop().run_until_complete(
        svc.execute(_request(TaskType.STATIC_GROUP_WORKFLOW, yaml="def: x", group_kind="chat")))
    assert ("yaml", "state_machine", "def: x") in eng.calls
