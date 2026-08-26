"""Declarative endpoint coverage for internal collaboration task routes."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import (
    NodeOpResult,
    Status,
    TaskExecutionGraph,
    TaskOpResult,
)
from agentclaw.community.core.task.repository.types import TaskInfoRecord
from agentclaw.community.core.task.task_discovery.discovery_service import DiscoveryService
from agentclaw.community.core.task.task_discovery.scheduler import TaskDiscoveryScheduler
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)

_BASE = "/api/v1/collaboration/tasks"
_TASK_SPEC = {
    "metadata": {"task_id": "task-endpoint-1", "title": "Endpoint case", "instruction": "test"},
    "context": {"background": "endpoint coverage"},
    "goal": {"objective": "exercise the route", "acceptances": []},
}
_EXECUTE_BODY = {
    "task_spec": _TASK_SPEC,
    "source_type": "bot",
    "owner_user_id": "user-endpoint-1",
    "owner_bot_id": "bot-endpoint-1",
    "execution_config": {"task_type": "dynamic"},
}
_CALLBACK_BODY = {
    "task_id": "task-endpoint-1",
    "workflow_source": "bcn",
    "workflow_id": "workflow-1",
    "workflow_instance_id": "instance-1",
    "status": "RUNNING",
    "is_success": True,
    "loop_task_id": "task-endpoint-1::root",
}


class _CallbackSink:
    async def start_run(self, _data) -> None:
        return None

    async def report_result(self, _data) -> None:
        return None


class _CallbackTaskService:
    def __init__(self) -> None:
        self.callback = _CallbackSink()


def _seed_task_service(world, *, expected_owner=None) -> None:
    async def execute(_self, _task_info):
        return TaskOpResult(
            task_id="task-endpoint-1",
            success=True,
            run_id=1,
            extend_props={"group_id": "bcs_grp_endpoint_1"},
        )

    def dashboard(_self, _task_id, _node_id=None):
        return TaskExecutionGraph(run_id=1, loop_round=0, status=Status.PENDING)

    def list_tasks(_self, _status=None, owner_user_id=None):
        assert owner_user_id == expected_owner
        return [
            TaskInfoRecord(
                id=1,
                task_id="task-endpoint-1",
                source_type="bot",
                owner_user_id="user-endpoint-1",
                owner_bot_id="bot-endpoint-1",
                execution_config={"task_type": "dynamic"},
                task_spec=_TASK_SPEC,
                status=Status.PENDING,
                gmt_create=datetime(2026, 8, 22, 10, 0, 0),
                gmt_modified=datetime(2026, 8, 22, 10, 0, 0),
            )
        ]

    def list_tasks_page(_self, status=None, owner_user_id=None, page=1, page_size=20):
        items = list_tasks(_self, status, owner_user_id=owner_user_id)
        return items[:page_size], len(items)

    def claim(_self, task_id, _bot_id):
        return NodeOpResult(task_id=task_id, node_id="root", success=True)

    def attach(_self, task_id, _parent_node_id, _task_spec, _bot_id):
        return SimpleNamespace(task_id=task_id, node_id="bbs-node-1")

    async def result(_self, task_id, node_id, _bot_id, **_kwargs):
        return NodeOpResult(task_id=task_id, node_id=node_id, success=True)

    bind_overrides(
        world,
        TaskServiceProtocol,
        {
            "execute": execute,
            "get_task_dashboard": dashboard,
            "list_tasks": list_tasks,
            "list_tasks_page": list_tasks_page,
            "claim_bbs_task": claim,
            "attach_bbs_node": attach,
            "report_bbs_result": result,
        },
    )


def _seed_callback_service(world) -> None:
    world.injector.binder.bind(
        TaskServiceProtocol,
        to=_CallbackTaskService(),
        scope=None,
    )


def _seed_callback_report(world) -> None:
    # /callback/report resolves TaskServiceProtocol and invokes svc.callback;
    # binding TaskLoopCallbackProtocol alone does not affect that dependency.
    _seed_callback_service(world)


def _seed_scheduler_error(world) -> None:
    def get_status(_self):
        raise RuntimeError("scheduler unavailable")

    bind_overrides(world, TaskDiscoveryScheduler, {"get_status": get_status})


def _seed_scheduled_trigger_error(world) -> None:
    async def discover_all_bots(_self):
        raise RuntimeError("discovery unavailable")

    bind_overrides(
        world,
        DiscoveryService,
        {"discover_all_bots": discover_all_bots},
    )


# Public-surface mirrors.
@endpoint_test(
    method="POST",
    path=f"{_BASE}/execute",
    scenario="happy_ok",
    seed=_seed_task_service,
    input=CaseInput(json_body=_EXECUTE_BODY),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "task_id": "task-endpoint-1",
                "extend_props": {"group_id": "bcs_grp_endpoint_1"},
            },
        },
    ),
)
def execute_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/execute",
    scenario="err_invalid_body",
    input=CaseInput(json_body={}),
    expect=ExpectError(status=422),
)
def execute_error():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/dashboard",
    scenario="happy_ok",
    seed=_seed_task_service,
    input=CaseInput(query_params={"task_id": "task-endpoint-1"}),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"run_id": 1}}),
)
def dashboard_happy():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/dashboard",
    scenario="err_missing_task_id",
    expect=ExpectError(status=422),
)
def dashboard_error():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/list",
    scenario="happy_ok",
    seed=_seed_task_service,
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": [{"task_id": "task-endpoint-1"}]}),
)
def list_happy():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/list",
    scenario="err_invalid_status",
    input=CaseInput(query_params={"status": "NOT_A_STATUS"}),
    expect=ExpectError(status=400),
)
def list_error():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/list",
    scenario="scoped_by_user_id",
    seed=lambda w: _seed_task_service(w, expected_owner="user-endpoint-1"),
    input=CaseInput(query_params={"user_id": "user-endpoint-1"}),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": [{"task_id": "task-endpoint-1"}]},
    ),
)
def list_scoped_by_user_id():
    pass


# Legacy callback/report adapter.
@endpoint_test(
    method="POST",
    path=f"{_BASE}/callback/report",
    scenario="happy_ok",
    seed=_seed_callback_report,
    input=CaseInput(json_body={"loop_task_id": "task-endpoint-1::root", "result": {"success": True}}),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"ok": True}}),
)
def callback_report_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/callback/report",
    scenario="err_invalid_body",
    input=CaseInput(json_body={}),
    expect=ExpectError(status=422),
)
def callback_report_error():
    pass


# BBS relay routes.
@endpoint_test(
    method="POST",
    path=f"{_BASE}/bbs/claim",
    scenario="happy_ok",
    seed=_seed_task_service,
    input=CaseInput(json_body={"task_id": "task-endpoint-1", "bot_id": "bot-1"}),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"root_node_id": "root"}}),
)
def bbs_claim_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/bbs/claim",
    scenario="err_invalid_body",
    input=CaseInput(json_body={}),
    expect=ExpectError(status=422),
)
def bbs_claim_error():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/bbs/attach",
    scenario="happy_ok",
    seed=_seed_task_service,
    input=CaseInput(json_body={"task_id": "task-endpoint-1", "parent_node_id": "root", "task_spec": _TASK_SPEC, "bot_id": "bot-1"}),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"node_id": "bbs-node-1"}}),
)
def bbs_attach_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/bbs/attach",
    scenario="err_invalid_body",
    input=CaseInput(json_body={}),
    expect=ExpectError(status=422),
)
def bbs_attach_error():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/bbs/result",
    scenario="happy_ok",
    seed=_seed_task_service,
    input=CaseInput(json_body={"task_id": "task-endpoint-1", "node_id": "bbs-node-1", "bot_id": "bot-1", "output_patch": {"done": True}}),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"ok": True}}),
)
def bbs_result_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/bbs/result",
    scenario="err_invalid_body",
    input=CaseInput(json_body={}),
    expect=ExpectError(status=422),
)
def bbs_result_error():
    pass


# task_loop callback ingress.
def _register_callback_cases(suffix: str, *, node: bool) -> None:
    happy_body = dict(_CALLBACK_BODY)
    if node:
        happy_body["node_id"] = "node-1"

    endpoint_test(
        method="POST",
        path=f"{_BASE}/callback/{suffix}",
        scenario="happy_ok",
        seed=_seed_callback_service,
        input=CaseInput(json_body=happy_body),
        expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"ok": True}}),
    )(lambda: None)

    endpoint_test(
        method="POST",
        path=f"{_BASE}/callback/{suffix}",
        scenario="err_invalid_json",
        input=CaseInput(raw_body=b"not-json"),
        expect=ExpectError(status=422),
    )(lambda: None)


_register_callback_cases("workflow_start", node=False)
_register_callback_cases("workflow_result", node=False)
_register_callback_cases("node_start", node=True)
_register_callback_cases("node_result", node=True)


# Discovery scheduler operations. These endpoints intentionally return HTTP 200
# for both business success and failure, so the expectation shape carries the distinction.
@endpoint_test(
    method="GET",
    path=f"{_BASE}/discovery/scheduler-status",
    scenario="happy_ok",
    expect=ExpectSuccess(status=200, json_contains={"success": True, "running": False, "jobs": []}),
)
def scheduler_status_happy():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/discovery/scheduler-status",
    scenario="err_scheduler_unavailable",
    seed=_seed_scheduler_error,
    expect=ExpectError(status=200, json_contains={"success": False, "running": False, "jobs": []}),
)
def scheduler_status_error():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/discovery/scheduled-trigger",
    scenario="happy_ok",
    expect=ExpectSuccess(status=200, json_contains={"success": True, "total_discovered": 0, "results": []}),
)
def scheduled_trigger_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/discovery/scheduled-trigger",
    scenario="err_discovery_unavailable",
    seed=_seed_scheduled_trigger_error,
    expect=ExpectError(status=200, json_contains={"success": False, "total_discovered": 0, "results": []}),
)
def scheduled_trigger_error():
    pass
