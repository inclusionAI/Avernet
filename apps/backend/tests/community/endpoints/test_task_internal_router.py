"""Declarative endpoint coverage for internal collaboration task routes."""
from __future__ import annotations

from types import SimpleNamespace

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.api.task.task_grant_service import (
    GRANTED,
    REVOKED,
    GrantResult,
    RevokeResult,
    TaskClaimGrantServiceProtocol,
)
from agentclaw.community.core.task.domain.intake import (
    IntakeTarget,
    IntakeTargetType,
    TaskExecutionMode,
    TaskIntakeResult,
    TaskIntakeStatus,
)
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    NodeOpResult,
    RuntimeInfo,
    Status,
    TaskContext,
    TaskExecutionGraph,
    TaskNode,
    TaskOpResult,
    TaskSourceType,
    TaskSpec,
)
from agentclaw.community.core.task.task_discovery.discovery_service import DiscoveryService
from agentclaw.community.core.task.task_discovery.scheduler import TaskDiscoveryScheduler
from agentclaw.community.core.task.task_runner.client.ports import (
    BotSendResult,
    OpenApiBotPort,
)
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

_STAFF_COOKIE = {"cookie": "staff_id=user-endpoint-1"}



class _CallbackSink:
    async def start_run(self, _data) -> None:
        return None

    async def report_result(self, _data) -> None:
        return None


class _CallbackTaskService:
    def __init__(self) -> None:
        self.callback = _CallbackSink()

    def get_task_dashboard(self, task_id, node_id=None, *, include_action_log=False):
        # The /callback happy path's legacy-result guard reads
        # ``graph.extend_props.get("execution_config", {})``; an empty stand-in
        # short-circuits the guard (no relay-mode reject).
        return SimpleNamespace(extend_props={})


def _seed_task_service(world) -> None:
    async def execute(_self, _task_info):
        return TaskOpResult(
            task_id="task-endpoint-1",
            success=True,
            run_id=1,
            extend_props={"group_id": "bcs_grp_endpoint_1"},
        )

    def claim(_self, task_id, bot_id=None, node_id=None, *, claim_id=None):
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


_DOMAIN_TASK_SPEC = TaskSpec(
    context=Context(
        title="Endpoint case",
        background="endpoint coverage",
        extend_props={"deliverables": ["report"], "constraints": []},
    ),
    goal=Goal(
        objective="exercise the task intake and relay routes",
        acceptances=[AcceptanceCriteria(id="ac1", description="return the case fixture")],
    ),
)
_TOKEN = "relay-1"


def _seed_internal_task(world) -> None:
    def get_task_dashboard(_self, _task_id, _node_id=None, *, include_action_log=False):
        return TaskExecutionGraph(
            run_id=1,
            loop_round=0,
            status=Status.PENDING,
            task_id="task-endpoint-1",
        )

    bind_overrides(world, TaskServiceProtocol, {"get_task_dashboard": get_task_dashboard})


def _seed_relay_context(world) -> None:
    node = TaskNode(
        node_id="relay-node-1",
        task_id="task-endpoint-1",
        status=Status.RUNNING,
        task_spec=_DOMAIN_TASK_SPEC,
        run_info=RuntimeInfo(extend_props={"relay_token": _TOKEN}),
        node_run_graph=None,
    )
    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        tasks=[node],
        extend_props={"execution_config": {"orchestration_mode": "relay"}},
        task_id="task-endpoint-1",
    )

    def get_task_dashboard(_self, _task_id, _node_id=None, *, include_action_log=False):
        return graph

    def get_node_context(_self, _task_id, _node_id):
        return TaskContext(
            spec=_DOMAIN_TASK_SPEC,
            all_done_output=[],
            gaps=[],
            task_id="task-endpoint-1",
            node_id="relay-node-1",
            root_task_spec=_DOMAIN_TASK_SPEC,
            current_task_spec=_DOMAIN_TASK_SPEC,
        )

    bind_overrides(
        world,
        TaskServiceProtocol,
        {"get_task_dashboard": get_task_dashboard, "get_node_context": get_node_context},
    )


def _seed_intake(world) -> None:
    async def intake(_self, _command, _context):
        return TaskIntakeResult(
            session_id="intake-session-1",
            owner_user_id="user-endpoint-1",
            source_type=TaskSourceType.API,
            status=TaskIntakeStatus.CLARIFYING,
            create_time_ms=1,
            update_time_ms=1,
        )

    bind_overrides(world, TaskServiceProtocol, {"intake": intake})


def _seed_confirmed_start(world) -> None:
    confirmed = TaskIntakeResult(
        session_id="intake-session-1",
        owner_user_id="user-endpoint-1",
        source_type=TaskSourceType.API,
        status=TaskIntakeStatus.CONFIRMED,
        target=IntakeTarget(IntakeTargetType.BOT, "bot-endpoint-1"),
        execution_mode=TaskExecutionMode.RELAY,
        task_spec=_DOMAIN_TASK_SPEC,
        create_time_ms=1,
        update_time_ms=1,
    )

    def get_confirmed_intake(_self, _session_id, _owner_user_id):
        return confirmed

    async def execute(_self, _request):
        return TaskOpResult(task_id="task-confirmed-1", success=True, run_id=1)

    bind_overrides(
        world,
        TaskServiceProtocol,
        {"get_confirmed_intake": get_confirmed_intake, "execute": execute},
    )


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


# Authenticated dashboard lookup for a single goal.
@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{task_id}}",
    scenario="happy_ok",
    seed=_seed_internal_task,
    input=CaseInput(headers=_STAFF_COOKIE, path_params={"task_id": "task-endpoint-1"}),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"run_id": 1, "status": "DEFINED"}},
    ),
)
def internal_task_happy():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{task_id}}",
    scenario="err_unauthenticated",
    input=CaseInput(path_params={"task_id": "task-endpoint-1"}),
    expect=ExpectError(status=401),
)
def internal_task_unauthenticated():
    pass


# Human-authenticated two-phase intake and formal execution.
@endpoint_test(
    method="POST",
    path=f"{_BASE}/intake",
    scenario="happy_ok",
    seed=_seed_intake,
    input=CaseInput(
        headers=_STAFF_COOKIE,
        json_body={
            "action": "START",
            "message": "Clarify this endpoint case",
            "requested_target": {"target_type": "BOT", "target_id": "bot-endpoint-1"},
        },
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"session_id": "intake-session-1", "status": "CLARIFYING"},
        },
    ),
)
def intake_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/intake",
    scenario="err_unauthenticated",
    input=CaseInput(json_body={"action": "START", "message": "Clarify this"}),
    expect=ExpectError(status=401),
)
def intake_unauthenticated():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/start",
    scenario="happy_ok",
    seed=_seed_confirmed_start,
    input=CaseInput(
        headers=_STAFF_COOKIE,
        json_body={"session_id": "intake-session-1"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"task_id": "task-confirmed-1"}},
    ),
)
def confirmed_start_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/start",
    scenario="err_unauthenticated",
    input=CaseInput(json_body={"session_id": "intake-session-1"}),
    expect=ExpectError(status=401),
)
def confirmed_start_unauthenticated():
    pass


# Distributed Relay fetches node context under its current execution generation.
@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{task_id}}/nodes/{{node_id}}/context",
    scenario="happy_ok",
    seed=_seed_relay_context,
    input=CaseInput(
        headers={"X-Relay-Token": _TOKEN},
        path_params={"task_id": "task-endpoint-1", "node_id": "relay-node-1"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {"task_id": "task-endpoint-1", "node_id": "relay-node-1"},
        },
    ),
)
def relay_context_happy():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/{{task_id}}/nodes/{{node_id}}/context",
    scenario="err_expired_relay_attempt",
    seed=_seed_relay_context,
    input=CaseInput(
        headers={"X-Relay-Token": "expired"},
        path_params={"task_id": "task-endpoint-1", "node_id": "relay-node-1"},
    ),
    expect=ExpectError(status=409),
)
def relay_context_expired_attempt():
    pass


# Authenticated mirrors of the public task read surfaces.
@endpoint_test(
    method="GET",
    path=f"{_BASE}/dashboard",
    scenario="happy_ok",
    seed=_seed_internal_task,
    input=CaseInput(
        headers=_STAFF_COOKIE,
        query_params={"task_id": "task-endpoint-1"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": {"run_id": 1, "status": "DEFINED"}},
    ),
)
def dashboard_happy():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/dashboard",
    scenario="err_unauthenticated",
    input=CaseInput(query_params={"task_id": "task-endpoint-1"}),
    expect=ExpectError(status=401),
)
def dashboard_unauthenticated():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/list",
    scenario="happy_ok",
    input=CaseInput(
        headers=_STAFF_COOKIE,
        query_params={"user_id": "user-endpoint-1"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={"code": 200000, "data": []},
    ),
)
def list_happy():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/list",
    scenario="err_unauthenticated",
    expect=ExpectError(status=401),
)
def list_unauthenticated():
    pass


@endpoint_test(
    method="GET",
    path=f"{_BASE}/bbs/list",
    scenario="not_exposed",
    expect=ExpectError(status=404),
)
def bbs_list_not_exposed():
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


# ── 任务认领 grant/revoke + claim_on JOIN 灰度开关(internal /api/v1 face)─────────
_GRANT_BODY = {"bcs_bot_id": "bot-endpoint-1:ent"}


def _seed_grant_service(world) -> None:
    async def grant(_self, *, bcs_bot_id, cookie, referer, operator):
        return GrantResult(bcs_bot_id=bcs_bot_id, api_key_prefix="ep", grant_status=GRANTED, operator=operator)

    async def revoke(_self, *, bcs_bot_id, cookie, referer, operator):
        return RevokeResult(bcs_bot_id=bcs_bot_id, grant_status=REVOKED)

    bind_overrides(world, TaskClaimGrantServiceProtocol, {"grant": grant, "revoke": revoke})


@endpoint_test(
    method="POST",
    path=f"{_BASE}/grant",
    scenario="happy_ok",
    seed=_seed_grant_service,
    input=CaseInput(headers=_STAFF_COOKIE, json_body=_GRANT_BODY),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"grant_status": "granted"}}),
)
def internal_grant_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/grant",
    scenario="err_unauthenticated",
    input=CaseInput(json_body=_GRANT_BODY),
    expect=ExpectError(status=401),
)
def internal_grant_unauthenticated():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/revoke",
    scenario="happy_ok",
    seed=_seed_grant_service,
    input=CaseInput(headers=_STAFF_COOKIE, json_body=_GRANT_BODY),
    expect=ExpectSuccess(status=200, json_contains={"code": 200000, "data": {"grant_status": "revoked"}}),
)
def internal_revoke_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/revoke",
    scenario="err_unauthenticated",
    input=CaseInput(json_body=_GRANT_BODY),
    expect=ExpectError(status=401),
)
def internal_revoke_unauthenticated():
    pass


# ── 最终结果回写(不鉴权;按 root 节点 session 续写,不改任务态) ──────────────
class _WritebackBotPort:
    """OpenApiBotPort 替身:runner world 里该 Port 解析为 None(可选注入),
    不能用 bind_overrides(对 None 无法建子类),直接 binder.bind 完整实例。"""

    async def send_message(self, *, bot_id, message, metadata, message_id=None):
        return BotSendResult(run_id="wb-run-endpoint-1", session_id="sess-endpoint-1")


def _seed_writeback(world) -> None:
    root = TaskNode(
        node_id="task-endpoint-1",
        task_id="task-endpoint-1",
        status=Status.RUNNING,
        task_spec=_DOMAIN_TASK_SPEC,
        run_info=RuntimeInfo(
            assignee="bot-endpoint-1",
            extend_props={"session_id": "sess-endpoint-1", "assignee_owner_id": "owner-1"},
        ),
        node_run_graph=None,
    )
    graph = TaskExecutionGraph(
        run_id=1,
        loop_round=0,
        status=Status.RUNNING,
        tasks=[root],
        task_id="task-endpoint-1",
    )

    def get_task_dashboard(_self, _task_id, _node_id=None, *, include_action_log=False):
        return graph

    bind_overrides(world, TaskServiceProtocol, {"get_task_dashboard": get_task_dashboard})
    world.injector.binder.bind(OpenApiBotPort, to=_WritebackBotPort(), scope=None)


@endpoint_test(
    method="POST",
    path=f"{_BASE}/result_writeback",
    scenario="happy_ok",
    seed=_seed_writeback,
    input=CaseInput(
        json_body={"task_id": "task-endpoint-1", "message": "任务最终结果:已完成"},
    ),
    expect=ExpectSuccess(
        status=200,
        json_contains={
            "code": 200000,
            "data": {
                "task_id": "task-endpoint-1",
                "session_id": "sess-endpoint-1",
                "run_id": "wb-run-endpoint-1",
            },
        },
    ),
)
def result_writeback_happy():
    pass


@endpoint_test(
    method="POST",
    path=f"{_BASE}/result_writeback",
    scenario="err_invalid_body",
    input=CaseInput(json_body={}),
    expect=ExpectError(status=422),
)
def result_writeback_error():
    pass
