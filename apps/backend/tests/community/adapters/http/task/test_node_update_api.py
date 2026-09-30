"""内部节点写口 POST /api/v1/collaboration/tasks/nodes/update 的 HTTP 层透传验证。

仅验证路由解包 + 委托 ``TaskServiceProtocol.update_task_node_info`` 的参数透传与 envelope 返回,
不验引擎收敛(收敛由 task_center 单测覆盖)。stub 只实现该单个方法。
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.router import router as task_internal_router
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import NodeOpResult, Status


class _StubService:
    """仅实现 update_task_node_info 的最小 TaskServiceProtocol stub。"""

    def __init__(self) -> None:
        self.last: dict | None = None

    async def update_task_node_info(
        self,
        task_id,
        node_id,
        *,
        status=None,
        run_mode=None,
        assignee=None,
        output_patch=None,
        acceptance_result=None,
        exec_error=None,
        extend_props_patch=None,
    ) -> NodeOpResult:
        self.last = dict(
            task_id=task_id,
            node_id=node_id,
            status=status,
            run_mode=run_mode,
            assignee=assignee,
            output_patch=output_patch,
            acceptance_result=acceptance_result,
            exec_error=exec_error,
            extend_props_patch=extend_props_patch,
        )
        return NodeOpResult(
            task_id=task_id,
            node_id=node_id,
            success=True,
            prev_status=Status.RUNNING,
            new_status=Status.HUNG,
            error=None,
        )


class _StubModule(Module):
    @singleton
    @provider
    def svc(self) -> TaskServiceProtocol:
        return _StubService()  # type: ignore[return-value]


@pytest.fixture
def client():
    injector = Injector([_StubModule()])
    svc = injector.get(TaskServiceProtocol)
    app = FastAPI()
    app.include_router(task_internal_router)
    attach_injector(app, injector)
    return TestClient(app), svc


class TestNodeUpdateApi:
    def test_status_direct_patch_passthrough(self, client):
        c, svc = client
        r = c.post(
            "/api/v1/collaboration/tasks/nodes/update",
            json={
                "task_id": "t1",
                "node_id": "n1",
                "status": "HUNG",
                "extend_props_patch": {"hung_reason": "test"},
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == 200000
        assert body["data"] == {
            "task_id": "t1",
            "node_id": "n1",
            "success": True,
            "prev_status": "RUNNING",
            "new_status": "HUNG",
            "error": None,
        }
        # 透传到 service 的入参
        assert svc.last is not None
        assert svc.last["task_id"] == "t1"
        assert svc.last["node_id"] == "n1"
        assert svc.last["status"] == "HUNG"
        assert svc.last["extend_props_patch"] == {"hung_reason": "test"}
        assert svc.last["acceptance_result"] is None
        assert svc.last["exec_error"] is None
        assert svc.last["output_patch"] is None

    def test_acceptance_result_passthrough(self, client):
        c, svc = client
        r = c.post(
            "/api/v1/collaboration/tasks/nodes/update",
            json={
                "task_id": "t2",
                "node_id": "n2",
                "acceptance_result": {
                    "verdict": "DONE",
                    "acceptances_metric": [{"ac1": "ok"}],
                    "gaps": [],
                },
                "output_patch": {"output": "done"},
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"]["success"] is True
        assert body["data"]["new_status"] == "HUNG"
        assert svc.last is not None
        assert svc.last["task_id"] == "t2"
        # acceptance_result DTO 经 acceptance_result_from_dto 转为 domain AcceptanceResult
        assert svc.last["acceptance_result"] is not None
        assert svc.last["acceptance_result"].verdict.value == "DONE"
        assert svc.last["output_patch"] == {"output": "done"}
        assert svc.last["status"] is None
        assert svc.last["exec_error"] is None

    def test_exec_error_passthrough(self, client):
        c, svc = client
        r = c.post(
            "/api/v1/collaboration/tasks/nodes/update",
            json={
                "task_id": "t3",
                "node_id": "n3",
                "exec_error": "boom",
                "run_mode": "single_bot",
                "assignee": "bot_x:1",
            },
        )
        assert r.status_code == 200, r.text
        assert svc.last is not None
        assert svc.last["exec_error"] == "boom"
        assert svc.last["run_mode"] == "single_bot"
        assert svc.last["assignee"] == "bot_x:1"
        assert svc.last["status"] is None
        assert svc.last["acceptance_result"] is None
