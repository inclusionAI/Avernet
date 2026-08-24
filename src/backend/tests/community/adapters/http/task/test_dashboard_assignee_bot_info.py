"""dashboard 给 single_bot/bbs 节点的 assignee 附加 owner_id/bot_name(经 BotServiceProtocol.get_bot_by_id,
不限 caller/owner 的单查)。coop_group(assignee 是 group_id 非机器人)跳过;未命中/未配 bot_service 不写。"""
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
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.repository.protocols.task import (
    TaskCallbackRepositoryProtocol, TaskInfoRepositoryProtocol,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, Metadata, TaskInfo, TaskNodePatch, TaskSpec,
)
from agentclaw.community.core.task.task_graph.task_graph_service import TaskGraphService


class _FakeBotService:
    def __init__(self, by_id: dict[str, dict]) -> None:
        self._by_id = by_id
        self.calls: list[str] = []

    def get_bot_by_id(self, bot_id: str):  # noqa: ANN001 stub
        self.calls.append(bot_id)
        return self._by_id.get(bot_id)


class _FakeCallbackRepo:
    def __init__(self) -> None:
        self.by_session: dict[str, object] = {}

    def insert(self, rec):  # noqa: ANN001
        raise NotImplementedError

    def upsert(self, rec):  # noqa: ANN001
        return rec

    def get(self, run_id, node_id):  # noqa: ANN001
        return None

    def list_by_session(self, main_session_id, *, limit=100):  # noqa: ANN001
        return []

    def get_latest_by_session(self, main_session_id):  # noqa: ANN001
        return self.by_session.get(main_session_id)


class _StubModule(Module):
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
        return _B()  # type: ignore[return-value]

    @provider
    def task_info_repo(self) -> TaskInfoRepositoryProtocol:
        return None  # type: ignore[return-value]

    @singleton
    @provider
    def callback_repo(self) -> TaskCallbackRepositoryProtocol:
        return _FakeCallbackRepo()

    @singleton
    @provider
    def bot_service(self) -> BotServiceProtocol:
        return _FakeBotService({
            "bot_a": {"bot_id": "bot_a", "owner_id": "o1", "bot_name": "BotA"},
            "bot_b": {"bot_id": "bot_b", "owner_id": "o2", "bot_name": "BotB"},
        })


@pytest.fixture
def harness():
    from agentclaw.community.di.modules.task_module import TaskModule
    injector = Injector([TaskModule(), _StubModule()])
    fake_bot = injector.get(BotServiceProtocol)
    app = FastAPI()
    app.include_router(task_router)
    app.include_router(task_internal_router)
    attach_injector(app, injector)
    return TestClient(app), injector, fake_bot


def _seed_node(injector: Injector, task_id: str, run_mode: str, assignee: str) -> None:
    gs = injector.get(TaskGraphService)
    gs.initialize_graph(TaskInfo(
        task_spec=TaskSpec(metadata=Metadata(task_id=task_id, title="t", instruction="i"),
                           context=Context(background="", extend_props={}),
                           goal=Goal(objective="o",
                                     acceptances=[AcceptanceCriteria(id="a1", description="d")])),
        source_type="bot", owner_bot_id="b1", execution_config={}))
    gs.update_task_node_info(TaskNodePatch(task_id=task_id, node_id=task_id,
                                           run_mode=run_mode, assignee=assignee))


def test_dashboard_attaches_assignee_owner_and_name_for_single_bot(harness):
    c, inj, fake_bot = harness
    tid = f"ab-{uuid.uuid4().hex[:6]}"
    _seed_node(inj, tid, "single_bot", "bot_a")
    d = c.get("/api/v1/collaboration/tasks/dashboard", params={"task_id": tid}).json()["data"]
    root = {t["node_id"]: t for t in d["tasks"]}[tid]
    ep = root["run_info"]["extend_props"]
    assert ep.get("assignee_owner_id") == "o1"
    assert ep.get("assignee_name") == "BotA"
    assert fake_bot.calls == ["bot_a"]


def test_dashboard_skips_coop_group_assignee(harness):
    c, inj, fake_bot = harness
    tid = f"ag-{uuid.uuid4().hex[:6]}"
    _seed_node(inj, tid, "coop_group", "grp_xxx")
    c.get("/api/v1/collaboration/tasks/dashboard", params={"task_id": tid})
    assert fake_bot.calls == []  # assignee 是 group_id 非 bot,不查 BotService


def test_dashboard_no_bot_record_leaves_no_attach(harness):
    c, inj, fake_bot = harness
    tid = f"an-{uuid.uuid4().hex[:6]}"
    _seed_node(inj, tid, "single_bot", "bot_missing")
    d = c.get("/api/v1/collaboration/tasks/dashboard", params={"task_id": tid}).json()["data"]
    root = {t["node_id"]: t for t in d["tasks"]}[tid]
    ep = root["run_info"]["extend_props"]
    assert "assignee_owner_id" not in ep
    assert "assignee_name" not in ep
