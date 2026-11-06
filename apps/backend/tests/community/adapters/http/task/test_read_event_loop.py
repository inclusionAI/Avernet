"""Slow synchronous task enrichment must leave unrelated HTTP requests responsive."""
from __future__ import annotations

import asyncio
import inspect
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from injector import Injector

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.openapi_v1.dependencies import require_principal
from agentclaw.community.adapters.http.openapi_v1.task.router import router as public_router
from agentclaw.community.adapters.http.task.router import router as internal_router
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.api.task.task_artifact_service import TaskArtifactApiServiceProtocol
from agentclaw.community.core.task.domain.errors import TaskNotFoundError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria, Context, Goal, TaskInfo, TaskSpec,
)
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.types import TaskInfoRecord
from agentclaw.community.core.task.task_center.task_service import TaskService
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService


@pytest.mark.parametrize("service_type", [TaskServiceProtocol, TaskService])
@pytest.mark.parametrize("method_name", ["get_task_dashboard", "list_tasks"])
def test_same_named_task_read_targets_remain_synchronous(service_type, method_name):
    """Pin the sync targets explicitly exempted from the name-based async guard."""
    assert not inspect.iscoroutinefunction(getattr(service_type, method_name))


def _app(service, artifact_service=None):
    injector = Injector()
    injector.binder.bind(TaskServiceProtocol, to=service)
    injector.binder.bind(
        TaskArtifactApiServiceProtocol,
        to=artifact_service or SimpleNamespace(list_artifacts=lambda *a: []),
    )
    app = FastAPI()
    app.include_router(public_router)
    app.include_router(internal_router)
    app.dependency_overrides[require_principal] = lambda: {"user_id": "user-1"}
    app.dependency_overrides[get_current_user] = lambda: None
    attach_injector(app, injector)
    return app


class _SlowLookup:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.started = asyncio.Event()
        self.release = threading.Event()
        self.probe_completed_before_lookup = []

    def wait(self):
        self.loop.call_soon_threadsafe(self.started.set)
        # Bound the broken path too: it must fail instead of hanging the suite.
        self.probe_completed_before_lookup.append(self.release.wait(timeout=0.5))

    async def request(self, app, url, params):
        @app.get("/probe")
        async def probe():
            self.release.set()
            return {"ok": True}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            pending = asyncio.create_task(client.get(url, params=params))
            try:
                await asyncio.wait_for(self.started.wait(), timeout=2)
                assert (await client.get("/probe")).status_code == 200
                response = await pending
            finally:
                self.release.set()
                await pending
        assert self.probe_completed_before_lookup == [True], (
            "slow synchronous lookup blocked unrelated HTTP requests"
        )
        return response


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["/openapi/v1", "/api/v1"])
@pytest.mark.parametrize("paginated", [False, True])
async def test_slow_staff_enrichment_does_not_block_other_requests(prefix, paginated):
    lookup = _SlowLookup()

    class Staff:
        def get_profile_by_work_no(self, *, work_no):
            lookup.wait()
            return SimpleNamespace(nick_name="Initiator")

    record = TaskInfoRecord(
        id=1, task_id="task-1", source_type="bot", owner_user_id="user-1",
        owner_bot_id="bot-1:user-1", execution_config={}, task_spec={},
        status=Status.PENDING,
    )
    repo = SimpleNamespace(
        list_records=lambda *a, **kw: [record],
        list_records_page=lambda *a, **kw: ([record], 1),
    )
    service = TaskService(TaskGraphService(), task_info_repo=repo, staff_dept=Staff())
    params = {"user_id": "user-1"}
    if paginated:
        params.update(page=1, page_size=20)
    response = await lookup.request(_app(service), f"{prefix}/collaboration/tasks/list", params)

    assert response.status_code == 200, response.text
    items = response.json()["data"]
    if paginated:
        assert items["total"] == 1
        items = items["items"]
    assert items[0]["owner_user_name"] == "Initiator"


@pytest.mark.asyncio
@pytest.mark.parametrize("path,params", [
    ("/openapi/v1/collaboration/tasks/dashboard", {"task_id": "task-1"}),
    ("/openapi/v1/collaboration/tasks/dashboard", {"task_id": "task-1", "include_action_log": True}),
    ("/api/v1/collaboration/tasks/dashboard", {"task_id": "task-1"}),
    ("/api/v1/collaboration/tasks/dashboard", {"task_id": "task-1", "include_action_log": True}),
    ("/api/v1/collaboration/tasks/task-1", {}),
])
@pytest.mark.parametrize("dependency", ["repository", "artifacts"])
async def test_slow_dashboard_reads_do_not_block_other_requests(path, params, dependency):
    lookup = _SlowLookup()
    graph = TaskGraphService()
    graph.initialize_graph(TaskInfo(
        task_id="task-1", source_type="bot", owner_bot_id="bot-1:user-1",
        execution_config={}, task_spec=TaskSpec(
            context=Context(background="", extend_props={}, title="Task"),
            goal=Goal(objective="Work", acceptances=[AcceptanceCriteria(id="a", description="Done")]),
        ),
    ))

    def get_record(task_id):
        if dependency == "repository":
            lookup.wait()
        return None

    def list_artifacts(task_id):
        if dependency == "artifacts":
            lookup.wait()
        return []

    service = TaskService(graph, task_info_repo=SimpleNamespace(get=get_record))
    app = _app(service, SimpleNamespace(list_artifacts=list_artifacts))
    response = await lookup.request(app, path, params)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["tasks"][0]["node_id"] == "task-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", ["/openapi/v1", "/api/v1"])
@pytest.mark.parametrize("fail", [False, True])
async def test_session_read_remains_responsive_and_preserves_errors(prefix, fail):
    lookup = _SlowLookup()

    def list_runs(session_id):
        lookup.wait()
        if fail:
            raise TaskNotFoundError("task-1")
        return [SimpleNamespace(task_id="task-1", node_id="task-1", gmt_modified=None, gmt_create=None)]

    service = TaskService(
        TaskGraphService(), task_info_repo=SimpleNamespace(get=lambda _: SimpleNamespace(owner_user_id="user-1")),
        task_node_run_info_repo=SimpleNamespace(list_by_session_id=list_runs),
    )
    response = await lookup.request(
        _app(service), f"{prefix}/collaboration/tasks/by_session", {"session_id": "s1"}
    )
    assert response.status_code == (404 if fail else 200), response.text
    if fail:
        assert response.json()["code"] == 404000
    else:
        assert response.json()["data"]["session_id"] == "s1"
