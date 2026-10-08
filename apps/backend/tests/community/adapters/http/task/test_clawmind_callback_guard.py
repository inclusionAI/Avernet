"""ClawMind 回调解析出错时的兜底落库(router guard 单测)。

口径:claw_mind 内嵌 JSON 非法 → enrich_claw_mind 构图抛错 → router claw_mind 分支 try/except 捕获,
打 error 日志并返回 200 ack;**不跳过落库**,而是经 ``ingest_parse_error`` 兜底:仅写 ``exec_error``
(错误信息)+ ``extend_props``(原始上报数据),其它已有字段不动(callback_repo.upsert_error 部分更新)。
复用 dashboard harness(TaskModule + _StubModule + 记账 _FakeCallbackRepo)。
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
    """记账 upsert / upsert_error:正常路径走 upsert,解析失败兜底走 upsert_error。"""

    def __init__(self):
        self.upserts: list = []
        self.upsert_errors: list = []

    def upsert(self, rec):
        self.upserts.append(rec)
        return rec

    def upsert_error(self, rec):
        self.upsert_errors.append(rec)
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


def test_malformed_claw_mind_persists_error_record():
    """内嵌 result_json 非法 → router 返回 200,并兜底落错误记录(exec_error + extend_props=原始 body),
    其它字段不动;不走正常 ingest upsert。"""
    c, fake = _harness()
    body = {"workflow_id": "w", "flow_id": "f", "status": "succeeded",
            "ext_info": {"flow_runs": {"status": "succeeded",
                         "result_json": "not-a-valid-json{"},
                         "node_executions": []}}
    r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=body)
    assert r.status_code == 200, r.text                  # guard 收为 ack,而非 500
    assert fake.upserts == []                            # 未走正常 ingest 全量 upsert
    assert len(fake.upsert_errors) == 1                  # 走兜底 upsert_error 一次
    rec = fake.upsert_errors[0]
    assert rec.exec_error                                # 错误信息已落 exec_error
    assert rec.extend_props == body                      # 原始上报数据落 extend_props
    assert rec.run_id == "f"                             # 主键按 flow_id 取


def test_valid_claw_mind_persists():
    """合法 claw_mind 回调仍走正常 ingest 落库(不进兜底 upsert_error)。"""
    c, fake = _harness()
    body = {"workflow_id": "w", "flow_id": "f", "status": "succeeded",
            "ext_info": {"flow_runs": {"status": "succeeded",
                         "origin_session_key": "agent:main:session:S1:user:1",
                         "result_json": '{"phase":"P3"}'},
                         "node_executions": []}}
    r = c.post("/api/v1/collaboration/tasks/callback/workflow_result", json=body)
    assert r.status_code == 200, r.text
    assert len(fake.upserts) == 1                        # 正常落库
    assert fake.upsert_errors == []                      # 未进兜底
