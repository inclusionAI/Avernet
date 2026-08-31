"""ClawMind 回调解析出错时的落库保护(router guard 单测)。

口径:claw_mind 内嵌 JSON 非法 → enrich_claw_mind 构图抛错 → router claw_mind 分支 try/except 捕获,
打 error 日志并返回 200 ack,**不落库**(不调 callback.ingest → 不 upsert task_callback,避免脏数据
覆盖已有记录)。复用 dashboard harness(TaskModule + _StubModule + 记账 _FakeCallbackRepo)。
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.router import task_callback_router
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.repository.protocols.task import (
    TaskCallbackRepositoryProtocol, TaskInfoRepositoryProtocol,
)


class _FakeCallbackRepo:
    """记账 upsert:guard 生效时不应有任何落库。"""

    def __init__(self):
        self.upserts: list = []

    def upsert(self, rec):
        self.upserts.append(rec)
        return rec


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


def _harness():
    from agentclaw.community.di.modules.task_module import TaskModule

    injector = Injector([TaskModule(), _StubModule()])
    fake = injector.get(TaskCallbackRepositoryProtocol)
    app = FastAPI()
    app.include_router(task_callback_router)
    attach_injector(app, injector)
    # raise_server_exceptions=False:guard 缺失时未捕获异常以 500 返回(而非上抛),
    # 便于断言"guard 把异常收为 200 ack"。
    return TestClient(app, raise_server_exceptions=False), fake


def test_malformed_claw_mind_acks_without_persist():
    """内嵌 result_json 非法 → router 打日志、返回 200、不落库(不 upsert task_callback)。"""
    c, fake = _harness()
    body = {"workflow_id": "w", "flow_id": "f", "status": "succeeded",
            "ext_info": {"flow_runs": {"status": "succeeded",
                         "result_json": "not-a-valid-json{"},
                         "node_executions": []}}
    r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=body)
    assert r.status_code == 200, r.text          # guard 收为 ack,而非 500
    assert fake.upserts == []                     # 未落库,不污染已有 task_callback


def test_valid_claw_mind_persists():
    """合法 claw_mind 回调仍正常落库(guard 不影响正常路径)。"""
    c, fake = _harness()
    body = {"workflow_id": "w", "flow_id": "f", "status": "succeeded",
            "ext_info": {"flow_runs": {"status": "succeeded",
                         "origin_session_key": "agent:main:session:S1:user:1",
                         "result_json": '{"phase":"P3"}'},
                         "node_executions": []}}
    r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=body)
    assert r.status_code == 200, r.text
    assert len(fake.upserts) == 1                 # 正常落库
