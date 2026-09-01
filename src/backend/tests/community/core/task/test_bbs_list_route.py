"""BBS 任务列表 HTTP 路由契约测试:GET /api/v1/collaboration/tasks/bbs/list。

内部面(不经 spanner),与 /bbs/claim|attach|result 同处。adapter 只转协议(Rule 22):
手写 stub TaskServiceProtocol 返 canned ``BbsTaskOverviewRecord``,验证 envelope + DTO
解析(task_spec.metadata.title→title / goal.objective→goal / goal.acceptances→acceptances /
extend_props.assignee_name→assignee_name / task_info.owner_bot_id→publisher)。
"""
from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.router import router as task_internal_router
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.types import BbsTaskOverviewRecord

_TASK_SPEC = {
    "metadata": {"task_id": "bbs-1", "title": "BBS 任务标题", "instruction": "执行"},
    "context": {"background": "bg"},
    "goal": {
        "objective": "达成目标",
        "acceptances": [{"id": "a1", "description": "验收1"}],
    },
}


def _canned_record() -> BbsTaskOverviewRecord:
    return BbsTaskOverviewRecord(
        task_id="bbs-1",
        node_id="n1",
        run_mode="bbs",
        retry=0,
        assignee_id="asg-1",
        status=Status.RUNNING,
        acceptance_result={"verdict": "PASS", "acceptances_metric": [], "gaps": []},
        extend_props={"assignee_name": "Alice"},
        relay_create_time=datetime(2026, 9, 1, 10, 0, 0),
        relay_begin_time=datetime(2026, 9, 1, 10, 0, 1),
        relay_end_time=datetime(2026, 9, 1, 10, 5, 0),
        task_spec=_TASK_SPEC,
        publisher="pub-1",
    )


class _StubTaskService:
    def __init__(self, records: list[BbsTaskOverviewRecord]) -> None:
        self._records = records

    def list_bbs_tasks(self) -> list[BbsTaskOverviewRecord]:
        return list(self._records)


class _StubTaskServiceModule(Module):
    """仅绑定 TaskServiceProtocol → 手写 stub(返预设 record);/bbs/list 路由只依赖它。"""

    def __init__(self, records: list[BbsTaskOverviewRecord]) -> None:
        super().__init__()
        self._records = records

    @singleton
    @provider
    def task_service(self) -> TaskServiceProtocol:
        return _StubTaskService(self._records)  # type: ignore[return-value]


def _client(records: list[BbsTaskOverviewRecord]) -> TestClient:
    injector = Injector([_StubTaskServiceModule(records)])
    app = FastAPI()
    app.include_router(task_internal_router)
    attach_injector(app, injector)
    return TestClient(app)


@pytest.fixture
def client():
    return _client([_canned_record()])


def test_bbs_list_route_returns_envelope_with_parsed_fields(client):
    r = client.get("/api/v1/collaboration/tasks/bbs/list")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 200000
    items = body["data"]
    assert len(items) == 1
    it = items[0]
    # ── SQL 直投字段 ──
    assert it["task_id"] == "bbs-1"
    assert it["node_id"] == "n1"
    assert it["run_mode"] == "bbs"
    assert it["retry"] == 0
    assert it["assignee_id"] == "asg-1"
    assert it["status"] == "RUNNING"
    assert it["acceptance_result"] == {"verdict": "PASS", "acceptances_metric": [], "gaps": []}
    assert it["extend_props"] == {"assignee_name": "Alice"}
    assert it["task_spec"] == _TASK_SPEC
    assert it["publisher"] == "pub-1"
    # ── relay 时间三态(ISO 字符串)──
    assert it["relay_create_time"].startswith("2026-09-01T10:00:00")
    assert it["relay_begin_time"].startswith("2026-09-01T10:00:01")
    assert it["relay_end_time"].startswith("2026-09-01T10:05:00")
    # ── task_spec / extend_props 二次解析字段 ──
    assert it["title"] == "BBS 任务标题"
    assert it["goal"] == "达成目标"
    assert it["acceptances"] == [{"id": "a1", "description": "验收1"}]
    assert it["assignee_name"] == "Alice"


def test_bbs_list_route_empty_when_no_bbs():
    c = _client([])
    r = c.get("/api/v1/collaboration/tasks/bbs/list")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 200000
    assert body["data"] == []
