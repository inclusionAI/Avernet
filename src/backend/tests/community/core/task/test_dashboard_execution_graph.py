"""dashboard endpoint 按 root.run_info.extend_props['session_id'] 反查 ``task_callback.execution_graph``
(回调审计表最新一条)挂在图级;无 session_id / 无对应 callback → None。本地路由契约测(不拉起 singlebox)。"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.router import router as task_internal_router
from agentclaw.community.adapters.http.openapi_v1.task.router import router as task_router
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.repository.protocols.task import (
    TaskCallbackRepositoryProtocol, TaskInfoRepositoryProtocol,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, TaskNodePatch, TaskInfo, TaskSpec,
)
from agentclaw.community.core.task.repository.types import TaskCallbackRecord
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService

_EG = {"nodes": [{"id": "draft"}, {"id": "finalize"}], "edges": []}


def _cb(session, execution_graph, **kw) -> TaskCallbackRecord:
    base = dict(id=0, invoker="bcn", run_id="R-1", node_id="N-1", main_session_id=session,
                status="completed", orig_callback_data="{}", execution_graph=execution_graph,
                result={"success": True}, result_success=True, exec_error=None, extend_props=None)
    base.update(kw)
    return TaskCallbackRecord(**base)


class _FakeCallbackRepo:
    def __init__(self):
        self.by_session = {}

    def insert(self, rec):  # noqa: ANN001 stub
        raise NotImplementedError

    def upsert(self, rec):  # noqa: ANN001 stub
        raise NotImplementedError

    def get(self, run_id, node_id):  # noqa: ANN001 stub
        return None

    def list_by_session(self, main_session_id, *, limit=100):  # noqa: ANN001 stub
        return []

    def get_latest_by_session(self, main_session_id):  # noqa: ANN001 stub
        return self.by_session.get(main_session_id)


class _StubModule(Module):
    """Botdiscover/bot_public/task_info_repo stub + 一个 fake TaskCallbackRepositoryProtocol。"""

    @singleton
    @provider
    def discover(self) -> BotDiscoverServiceProtocol:
        class _D:
            def search_by_keyword(self, **kw):
                return {"total": 0, "items": []}
        return _D()  # type: ignore[return-value]

    @singleton
    @provider
    def bot_public(self) -> BotPublicServiceProtocol:
        class _B:
            def search_public_bots_by_keyword(self, **kw):
                return {"total": 0, "items": []}
        return _B()

    @provider
    def task_info_repo(self) -> TaskInfoRepositoryProtocol:
        return None  # type: ignore[return-value]

    @singleton
    @provider
    def callback_repo(self) -> TaskCallbackRepositoryProtocol:
        return _FakeCallbackRepo()


def _seed_root(injector: Injector, task_id: str, session_id):
    gs = injector.get(TaskGraphService)
    gs.initialize_graph(TaskInfo(
        task_spec=TaskSpec(metadata=Metadata(task_id=task_id, title="t", instruction="i"),
                           context=Context(background="", extend_props={}),
                           goal=Goal(objective="o",
                                     acceptances=[AcceptanceCriteria(id="a1", description="d")])),
        source_type="bot", owner_bot_id="b1", execution_config={}))
    if session_id is not None:
        gs.update_task_node_info(TaskNodePatch(task_id=task_id, node_id=task_id,
                                               extend_props_patch={"session_id": session_id}))


@pytest.fixture
def harness():
    from agentclaw.community.di.modules.task_module import TaskModule
    injector = Injector([TaskModule(), _StubModule()])
    fake = injector.get(TaskCallbackRepositoryProtocol)
    fake.by_session["s1"] = _cb(session="s1", execution_graph=_EG)
    app = FastAPI()
    app.include_router(task_router)
    app.include_router(task_internal_router)
    attach_injector(app, injector)
    return TestClient(app), injector


def test_dashboard_attaches_execution_graph_by_session(harness):
    c, inj = harness
    task_id = f"eg-{uuid.uuid4().hex[:6]}"
    _seed_root(inj, task_id, "s1")
    d = c.get("/api/v1/collaboration/tasks/dashboard",
              params={"task_id": task_id}).json()["data"]
    assert d["execution_graph"] == _EG


def test_dashboard_root_without_session_id_leaves_none(harness):
    c, inj = harness
    task_id = f"eg-{uuid.uuid4().hex[:6]}"
    _seed_root(inj, task_id, None)
    d = c.get("/api/v1/collaboration/tasks/dashboard",
              params={"task_id": task_id}).json()["data"]
    assert d.get("execution_graph") is None


def test_dashboard_no_callback_for_session_leaves_none(harness):
    c, inj = harness
    task_id = f"eg-{uuid.uuid4().hex[:6]}"
    _seed_root(inj, task_id, "s_missing")
    d = c.get("/api/v1/collaboration/tasks/dashboard",
              params={"task_id": task_id}).json()["data"]
    assert d.get("execution_graph") is None
