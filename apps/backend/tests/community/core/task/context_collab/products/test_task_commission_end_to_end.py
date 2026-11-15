"""产品 Profile 上线 → 建任务 → Bot 自查机会/申请/录取 → L4 Work → 用户验收。

用户通过产品 Service API 操作和观察;case 显式以 Bot ActorContext 调用
L5 Open 查询和提交申请,再以发布人授权的策略身份直接调用 Open.screen/admit,
录取后由任务产品的 Work 动作入口启动 L4 Work;Bot 只负责执行并通过签名 A2A HTTP 入站上报。
使用真实产品 Profile、仓储、L5 Open 和 G1/G4。直接 Open 调用不验证 JWT;
另保留真实产品策略的签名 HTTP Query/Apply 回归。Open 负责参与录取,
开始、进度与成果提交属于 L4 Work,不是 Bot 的内置方法或新的 Open 方法。
仅模拟 Bot 执行和网关签名;本地产物不上传,不声称验证真实远端 Bot、
网关转发或 LLM 执行。
"""

import runpy
import secrets
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Binder, Injector

from agentclaw.community.adapters.http.task import commission_a2a, commission_a2a_auth
from agentclaw.community.core.gateway_principal import PrincipalVerifierConfig
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.delivery import (
    A2aDeliveryPort,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations import (
    work as work_operations_module,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    role_binding_ref,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenAdmitCommand,
    OpenApplicationCommand,
    OpenDecisionOpCommand,
    OpenMethodOpsServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.products.task_commission import (
    service as commission_service_module,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
    build_self_service_commission_profile,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    ApplyForTaskCommand,
    PublishTaskCommand,
    TaskAdvanceCommand,
    TaskCommissionCreatedView,
    TaskCommissionServiceProtocol,
    TaskMethodOpsPort,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ContextCollabServiceProtocol,
    EventAppendCommand,
)
from agentclaw.community.di.modules.context_collab_products import (
    INSTALLED_PRODUCT_PROFILES,
)
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.di.modules.task_persistence_module import TaskPersistenceModule
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from tests.community.factories.task_commission import (
    BOT_ID as BOT_ASSIGNEE,
)
from tests.community.factories.task_commission import (
    OWNER,
    commission_report_request,
)

HUMAN = ActorContext(actor=PrincipalRef("human", "u_owner_001"), source="api")
SPACE = SpaceRef(space_id="space_workspace_001", environment="test")
TITLE = "为计算器增加求和能力"
BODY = "完成方案、编码和测试,生成待评审 PR,提交成果后由业务同学验收。"


_TEST_DIRECTORY = __name__.rsplit(".", 1)[0].replace(".", "/")


def _flow_log(directory: str, message: str) -> None:
    print(f"[commission-flow][{directory}] {message}", flush=True)


def _trace_call(monkeypatch, owner, name: str, description: str) -> None:
    """只观察真实调用,不替换业务结果,不打印参数/凭证/完整响应。"""
    original = getattr(owner, name)
    directory = original.__module__.removeprefix("agentclaw.community.")
    directory = directory.rsplit(".", 1)[0].replace(".", "/")
    label = original.__qualname__

    @wraps(original)
    def traced(*args, **kwargs):
        detail = description
        if name == "_advance":
            detail += f", action={args[1]}"
        elif name == "resolve_workflow_status":
            detail += (
                f", event={kwargs['event_kind']}, "
                f"from={getattr(kwargs['task'], 'status', None)}"
            )
        _flow_log(directory, f"→ {label}: {detail}")
        try:
            result = original(*args, **kwargs)
        except Exception as exc:
            _flow_log(directory, f"! {label}: 失败 ({type(exc).__name__})")
            raise
        snapshot = getattr(result, "current_snapshot", None)
        status = getattr(result, "status", getattr(result, "state", None))
        if snapshot is not None:
            status = snapshot.status
        if isinstance(result, str):
            status = result  # resolve_workflow_status 的返回值。
        suffix = f", status={status}" if status is not None else ""
        _flow_log(directory, f"← {label}: 完成{suffix}")
        return result

    monkeypatch.setattr(owner, name, traced)


def _trace_runtime(monkeypatch, injector: Injector) -> None:
    """测试局部安装跟踪;记录实际进入/返回路径,用 pytest -s 实时观察。"""
    for protocol, operations in (
        (
            TaskCommissionServiceProtocol,
            {
                "publish_task": "业务同学发布指定 Bot 的代办任务",
                "query_opportunities": "指定 Bot 只读查询机会,不提前加入协作",
                "apply_for_task": "指定 Bot 自己申请,触发产品自动审核",
                "review_task_application": "对既有申请运行产品自动审核并交付任务",
                "report_execution": "接收 Bot 的 start/progress/submit 上报并校验上下文",
                "_advance": "按产品 WorkflowProfile 推进 L4 Work 动作",
                "accept_task": "发布人验收成果",
                "list_tasks": "查询用户任务列表",
                "detail_task": "读取任务状态、正文及操作历史",
            },
        ),
        (
            TaskMethodOpsPort,
            {
                "admit_designated_assignee": "兼容 v1 发布人代申请",
                "publish_designated_opportunity": "只发布机会,等待 Bot 申请",
                "review_task_application": "复用发布人授权的策略,不再提交申请",
                "require_admitted": "确认有效的任务 scope 参与绑定",
            },
        ),
        (
            OpenMethodOpsServiceProtocol,
            {
                "query_opportunities": "L5 Open 只读查询可申请机会",
                "publish_opportunity": "L5 Open 发布参与机会",
                "submit_application": "L5 Open 记录调用主体提交的申请",
                "screen": "L5 Open 审核参与申请",
                "admit": "L5 Open 录取指定 Bot",
            },
        ),
        (
            A2aDeliveryPort,
            {"send_message": "本地 Mock 交付,不代表真实远端 Bot 收到通知"},
        ),
        (
            ContextCollabAuthorizerProtocol,
            {
                "check_create": "G1 校验创建权限",
                "check_append": "G1 校验事件写入权限",
            },
        ),
        (
            ContextCollabRepositoryProtocol,
            {
                "create_context": "创建并持久化 Collet、独立 Task 与初始事件",
                "append_event": "追加事件并原子保存 Task;返回已保存状态",
            },
        ),
    ):
        implementation = type(injector.get(protocol))
        for name, description in operations.items():
            _trace_call(monkeypatch, implementation, name, description)
    for module, name, description in (
        (
            commission_a2a_auth,
            "verify_bot_principal_token",
            "验证签名 Bot 身份,不记录凭证",
        ),
        (commission_a2a, "_execute", "A2A HTTP 入站转换命令并调用产品服务"),
        (
            commission_service_module,
            "prepare_product_spec",
            "L6 校验产品 Profile 并准备创建合同",
        ),
        (
            commission_service_module,
            "validate_product_event",
            "G4 校验产品合同、角色及时序",
        ),
        (
            commission_service_module,
            "prepare_event_append",
            "L4 Work 准备状态迁移与 Task 版本围栏",
        ),
        (
            work_operations_module.PreparedEventAppend,
            "commit",
            "L4 提交已裁决事件,由仓储原子保存",
        ),
        (
            work_operations_module,
            "resolve_workflow_status",
            "G4 状态机计算事件后的目标状态",
        ),
    ):
        _trace_call(monkeypatch, module, name, description)


@contextmanager
def _commission_runtime(
    db,
    monkeypatch,
    profile,
    *,
    bot_id=BOT_ASSIGNEE,
    owner_id=OWNER,
    tenant=DEFAULT_AVERNET_TENANT,
):
    """真实产品 DI/SQL/HTTP;仅替换本地签名配置,出站使用默认 MockA2aDelivery。"""

    def local_database(binder: Binder) -> None:
        binder.bind(DatabasePlugin, to=db)
        # Deployment uses TaskModule's real catalog seam, not a test-only registry.
        definitions = tuple(
            item for item in INSTALLED_PRODUCT_PROFILES if item.ref != profile.ref
        )
        binder.bind(ProductCatalog, to=ProductCatalog((*definitions, profile)))

    injector = Injector([TaskPersistenceModule(), TaskModule(), local_database])
    app = FastAPI()
    app.include_router(commission_a2a.router)
    attach_injector(app, injector)
    key = secrets.token_hex(32)
    monkeypatch.setattr(
        commission_a2a_auth,
        "get_principal_verifier_config",
        lambda: PrincipalVerifierConfig(key, "backend", "gateway"),
    )
    now = datetime.now(timezone.utc)
    signed_principal = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + timedelta(minutes=5),
            "principals": [
                {
                    "type": "bot",
                    "tenant": tenant,
                    "bot": {
                        "bot_uuid": bot_id,
                        "owner_id": owner_id,
                        "app_id": 1,
                        "agent_code": "writer",
                        "tenant": tenant,
                    },
                }
            ],
        },
        key,
        algorithm="HS256",
    )
    _trace_runtime(monkeypatch, injector)
    _flow_log(
        "di/modules", "TaskModule + TaskPersistenceModule 装配完成,启用本地调用跟踪"
    )
    with TestClient(app) as client:
        yield (
            injector.get(TaskCommissionServiceProtocol),
            SimulatedWorkingBot(
                client, signed_principal, profile.ref, bot_id, owner_id
            ),
            injector,
        )


@pytest.fixture
def commission_deployment(db, monkeypatch):
    """Product staff deploy a code-owned Profile through the real DI composition."""

    def deploy(profile, **identity):
        return _commission_runtime(db, monkeypatch, profile, **identity)

    return deploy


@pytest.fixture(
    params=[TASK_COMMISSION_PROFILE, build_self_service_commission_profile()],
    ids=["v1", "v2"],
)
def commission_runtime(commission_deployment, request):
    # Keep failure behavior for both the legacy and self-application flows.
    with commission_deployment(request.param) as (service, bot, _):
        yield service, bot


class SimulatedWorkingBot:
    """测试用 Bot runtime:执行中提供上报工具,成功返回后才自动提交成果。"""

    def __init__(
        self,
        client: TestClient,
        signed_principal: str,
        profile_ref,
        bot_id: str,
        owner_id: str,
    ):
        self.bot_id, self.owner_id = bot_id, owner_id
        self.profile_ref = profile_ref
        self.client = client
        self.headers = {"X-Avernet-Principal": signed_principal}

    def request(self, request: dict, *, expected_status="SUCCEEDED") -> dict:
        message = request["params"]["message"]
        operation = message["parts"][0]["data"]
        request["id"] = str(uuid4())
        message["messageId"] = str(uuid4())
        operation["operation_id"] = str(uuid4())
        operation["context"]["request_id"] = str(uuid4())
        operation["context"]["product_profile_ref"]["version"] = str(
            self.profile_ref.version
        )
        response = self.client.post(
            "/a2a/task-commission", json=request, headers=self.headers
        )
        assert response.status_code == 200, response.text
        parts = response.json()["result"]["parts"]
        assert parts[0]["data"]["status"] == expected_status, parts
        return {
            "result": parts[0]["data"],
            "data": parts[1]["data"] if len(parts) > 1 else {},
        }

    def report_request(
        self, created, action="start", revision=None, payload=None
    ) -> dict:
        request = commission_report_request(
            created,
            action,
            created.context_version if revision is None else revision,
            payload,
        )
        ctx = request["params"]["message"]["parts"][0]["data"]["context"]
        ctx["caller"]["identity_id"] = self.bot_id
        ctx["accountable_principal_ref"]["identity_id"] = self.owner_id
        return request

    def application_request(self, opportunity: dict) -> dict:
        context_id = opportunity["collab_context_id"]
        request = self.report_request(
            TaskCommissionCreatedView(context_id, "", "", "", 0)
        )
        op = request["params"]["message"]["parts"][0]["data"]
        op.update(
            layer="L5",
            operation_family="open",
            action="apply",
            mode="COMMAND",
            expected_revision=None,
            target_ref={
                "object_type": "OpenOpportunity",
                "object_id": opportunity["opportunity_id"],
                "version": None,
                "issuer": "avernet",
            },
            payload={},
        )
        op["context"]["method_binding_ref"]["object_id"] = opportunity[
            "method_binding_ref"
        ]
        return request

    def run(
        self,
        created: TaskCommissionCreatedView,
        execute: Callable[[Callable[[int, str], None]], list[str]],
        *,
        initial_revision: int,
    ) -> list[str]:
        """执行已由 L4 Work 启动的任务;Bot 不拥有 start_task 方法。"""
        revision = initial_revision

        def send(action: str, payload: dict) -> None:
            nonlocal revision
            request = self.report_request(created, action, revision, payload)
            _flow_log(
                _TEST_DIRECTORY,
                f"模拟 Bot → POST /a2a/task-commission: action={action}, "
                f"task={created.collet_id}, expected_revision={revision}",
            )
            result = self.request(request)["result"]
            revision = int(result["result_refs"][0]["version"])
            _flow_log(
                _TEST_DIRECTORY, f"模拟 Bot 收到 {action} 成功回执, revision={revision}"
            )

        def report_progress(percent: int, note: str) -> None:
            send("progress", {"percent": percent, "note": note})

        # start_task 已由调用方通过任务产品的 Work 动作入口完成。
        # Bot 只上报实际执行过程,不能把自身的运行器当成 L4 Work API。
        # 不是 finally:执行或进度上报失败时不能伪造成功并自动提交。
        _flow_log(_TEST_DIRECTORY, "模拟 Bot 开始执行任务业务函数")
        artifacts = execute(report_progress)
        _flow_log(_TEST_DIRECTORY, "模拟 Bot 业务函数成功返回产物,自动触发 submit")
        send(
            "submit",
            {"note": "执行完成,提交方案、代码、测试结果与 PR", "artifacts": artifacts},
        )
        return artifacts


_AI_CODING_STEPS = (
    (20, "写方案完成"),
    (50, "编码完成"),
    (80, "测试通过"),
    (100, "生成 PR 提案,等待用户验收"),
)


def _simulate_ai_coding(
    *,
    tmp_path: Path,
    task_id: str,
    service: TaskCommissionServiceProtocol,
    report_progress: Callable[[int, str], None],
) -> list[str]:
    """执行本地 AI coding 模拟,并通过回调逐步上报 L4 Work 进度。

    这是测试夹具的业务动作,不是 Bot 的内置方法;Bot runtime 只负责
    调用该函数、发送 progress/submit 入站消息。PR 仍是本地提案文件,
    不冒充真实远端建 PR 或 LLM 执行。
    """
    plan, code = tmp_path / "plan.md", tmp_path / "calculator.py"
    test_result, pr = tmp_path / "test-result.txt", tmp_path / "pull-request.md"
    artifacts = [path.as_uri() for path in (plan, code, test_result, pr)]

    assert service.detail_task(task_id).status == "in_progress"
    for index, (percent, note) in enumerate(_AI_CODING_STEPS):
        if index == 0:
            plan.write_text(
                "# 方案\n新增 add(a, b),覆盖正数、负数及零。", encoding="utf-8"
            )
        elif index == 1:
            code.write_text(
                "def add(a, b):\n    return a + b\n", encoding="utf-8"
            )
        elif index == 2:
            add = runpy.run_path(str(code))["add"]
            for a, b, expected in ((1, 2, 3), (-1, 1, 0), (0, 0, 0)):
                assert add(a, b) == expected
            test_result.write_text("3 passed", encoding="utf-8")
        else:
            pr.write_text(
                "# PR 提案\n新增求和能力;方案、代码、3 项测试已完成。",
                encoding="utf-8",
            )

        _flow_log(_TEST_DIRECTORY, f"5. AI coding: {note}, progress={percent}%")
        report_progress(percent, note)
        live = service.detail_task(task_id)
        assert live.status == "in_progress"  # 100% is not accept or submit.
        assert live.events[-1]["payload"] == {"note": note, "percent": percent}
        assert live.events[-1]["actor_id"] == BOT_ASSIGNEE
        assert all(
            event["event_kind"] != "task.submitted" for event in live.events
        )

    assert test_result.read_text(encoding="utf-8") == "3 passed"
    assert all(path.exists() for path in (plan, code, test_result, pr))
    return artifacts


def _opportunities_query_request(bot: SimulatedWorkingBot) -> dict:
    """HTTP 边界测试用 L4 Query 信封;不是 Bot 内置的机会查询能力。"""
    request = bot.report_request(TaskCommissionCreatedView("", "", "", "", 0))
    op = request["params"]["message"]["parts"][0]["data"]
    ctx = op["context"]
    # Product-wide discovery needs no Collet handoff or existing membership.
    ctx["collet_ref"] = ctx["method_binding_ref"] = None
    ctx["product_profile_ref"]["version"] = str(bot.profile_ref.version)
    op.update(
        layer="L4",
        operation_family="query",
        action="opportunities",
        mode="QUERY",
        target_ref=ctx["product_profile_ref"].copy(),
        expected_revision=None,
        payload={},
    )
    return request


def test_end_to_end_commission_flow(commission_deployment, tmp_path):
    """产品上线 → 建任务 → Bot Query/Apply → 策略 Screen/Admit → scoped Participant → Work → accept。"""
    # 1. 产品同学新建 v2 Profile 并上线。当前产品上线是代码定义 + DI
    #    安装,不是管理后台动态注册 API;不更改或迁移已上线的 v1。
    profile = build_self_service_commission_profile()
    assert profile.ref.version == 2
    assert profile.composition.method_kind == "Open"
    _flow_log(_TEST_DIRECTORY, "1. 产品同学定义任务代办 v2 Profile,通过 DI 部署上线")
    with commission_deployment(profile) as (service, working_bot, injector):
        assert injector.get(ProductCatalog).get("task_commission", 2) == profile
        # 2. 业务同学在已上线产品上创建任务,只指定 Bot,不代 Bot 申请。
        _flow_log(_TEST_DIRECTORY, "2. 业务同学创建代办任务,指定执行 Bot")
        created = service.publish_task(
            PublishTaskCommand(
                title=TITLE,
                body=BODY,
                space=SPACE,
                assignee_agent_id=BOT_ASSIGNEE,
                product_profile_ref=profile.ref,
            ),
            HUMAN,
        )
        task_id = created.collet_id
        assert created.status == "created"
        assert service.detail_task(task_id).body.content == BODY
        [summary] = service.list_tasks()
        assert (summary.collet_id, summary.title, summary.initiator_id) == (
            task_id,
            TITLE,
            HUMAN.actor.principal_id,
        )
        store, axes = injector.get(MethodStoreProtocol), current_axes()
        binding_id = top_level_binding_id(task_id)
        assert injector.get(A2aDeliveryPort).bridge_records() == ()
        assert store.list_applications(axes, binding_id, task_id) == []
        [binding] = store.list_bindings(axes, task_id)
        assert binding.method_kind == "Open"
        assert binding.method_names == ("Open",)
        assert binding.profile_versions == {"Open": "0.2"}
        assert store.get_roster(axes, binding_id, task_id) is None
        assert store.list_members(axes, binding_id, task_id) == []
        assert store.list_participant_bindings(axes, binding_id, task_id) == []
        # Designation is not admission; even the real signed Bot cannot start yet.
        working_bot.request(
            commission_report_request(created), expected_status="REJECTED"
        )
        before_query = _participation_snapshot(service, injector, created)

        # 3. 指定 Bot 先查询机会,再以自己身份提交申请;查询不能产生参与。
        _flow_log(
            _TEST_DIRECTORY, "3. 指定 Bot Query 机会,自己 Apply,产品策略自动审核通过"
        )
        # 直接调用真正的 L5 Open Method;Bot 只是调用主体,不拥有查询实现。
        # 这是已知 task/binding 的进程内查询,ActorContext 由测试建立;
        # Query/Apply 都不冒充跨 Agent 调用或 JWT 认证。
        open_method = injector.get(OpenMethodOpsServiceProtocol)
        bot_context = ActorContext(PrincipalRef("bot", working_bot.bot_id), "a2a")
        [opportunity] = open_method.query_opportunities(
            collab_context_id=task_id,
            method_binding_id=binding_id,
            context=bot_context,
        )
        assert opportunity.method_binding_id == binding_id
        assert opportunity.scope_ref == task_id
        assert opportunity.participation_type.value == "COLLET_PARTICIPANT"
        assert open_method.query_opportunities(task_id, binding_id, bot_context) == (
            opportunity,
        )  # repeatable read
        # 同时保留产品级签名 HTTP Query 的真实路由/最小投影验证。
        [discovered] = working_bot.request(_opportunities_query_request(working_bot))[
            "data"
        ]["opportunities"]
        assert discovered == {
            "collab_context_id": task_id,
            "opportunity_id": opportunity.opportunity_id,
            "method_binding_ref": opportunity.method_binding_id,
            "product_profile_ref": {"profile_id": "task_commission", "version": 2},
            "title": TITLE,
            "scope_ref": task_id,
        }
        assert _participation_snapshot(service, injector, created) == before_query
        assert store.list_applications(axes, binding_id, task_id) == []
        # Apply 也直接调用 L5 Open Method,引用来自查询结果;Bot 只是申请主体。
        record = injector.get(ContextCollabRepositoryProtocol).get_current_status(task_id)
        assert record is not None
        submitted = open_method.submit_application(
            collab_context_id=task_id,
            method_binding_id=opportunity.method_binding_id,
            opportunity_ref=opportunity.opportunity_id,
            command=OpenApplicationCommand(
                applicant_agent_ref=working_bot.bot_id,
                accountable_principal_ref=working_bot.owner_id,
                requested_role_refs=[
                    role_binding_ref(task_id, role)
                    for role in record.context_spec.role_bindings
                    if role.role == "selected-agent"
                ],
                reason="task_commission.bot_self_application",
            ),
            context=bot_context,
        )
        assert submitted.accepted and submitted.state == "SUBMITTED"
        [pending] = store.list_applications(axes, binding_id, task_id)
        assert pending.application_id == submitted.object_id
        assert pending.transitioned_by == working_bot.bot_id
        assert store.list_participant_bindings(axes, binding_id, task_id) == []
        assert store.list_decisions(axes, binding_id, task_id) == []
        assert injector.get(A2aDeliveryPort).bridge_records() == ()
        working_bot.request(
            working_bot.report_request(created), expected_status="REJECTED"
        )
        # case 显式展开已发布策略的审核/录取步骤,不调用产品 review 封装。
        # 由发布人授权的 product_policy actor 执行,不是 Bot 自审。
        policy_context = ActorContext(
            record.context_spec.principal_ref, source="product_policy"
        )
        [policy_ref] = opportunity.admission_policy_refs
        _flow_log(_TEST_DIRECTORY, "3.1. 产品策略调用 Open.screen: SUBMITTED → SCREENING")
        screened = open_method.screen(
            collab_context_id=task_id,
            method_binding_id=binding_id,
            application_ref=submitted.object_id,
            command=OpenDecisionOpCommand(reason=policy_ref),
            context=policy_context,
        )
        assert screened.accepted and screened.state == "SCREENING"
        assert screened.object_id == submitted.object_id
        [screening] = store.list_applications(axes, binding_id, task_id)
        assert screening.state is ApplicationState.SCREENING
        assert screening.transitioned_by == HUMAN.actor.principal_id
        assert store.list_participant_bindings(axes, binding_id, task_id) == []
        assert store.list_decisions(axes, binding_id, task_id) == []
        working_bot.request(
            working_bot.report_request(created), expected_status="REJECTED"
        )
        assert injector.get(A2aDeliveryPort).bridge_records() == ()
        _flow_log(_TEST_DIRECTORY, "3.2. 产品策略调用 Open.admit: SCREENING → ADMITTED")
        decision_id = f"dec_{uuid4().hex[:12]}"
        admitted = open_method.admit(
            collab_context_id=task_id,
            method_binding_id=binding_id,
            application_ref=submitted.object_id,
            command=OpenAdmitCommand(
                admitted_scope_ref=task_id,
                decision_id=decision_id,
                reason=policy_ref,
            ),
            context=policy_context,
        )
        assert admitted.accepted and admitted.state == "ADMITTED"
        assert admitted.object_id == submitted.object_id
        assert injector.get(A2aDeliveryPort).bridge_records() == ()
        [application] = store.list_applications(axes, binding_id, task_id)
        assert application.state is ApplicationState.ADMITTED
        assert application.applicant_agent_ref == BOT_ASSIGNEE
        assert (
            application.accountable_principal_ref == OWNER
        )  # Bot owner, not publisher.
        assert application.transitioned_by == HUMAN.actor.principal_id

        # 4. 真正录取后才建立本任务 Collet scope 内的参与绑定,不创建 Group。
        _flow_log(
            _TEST_DIRECTORY,
            "4. 录取建立限定任务 scope 的参与绑定,单个 Bot 在纯 Open 录取后执行代办",
        )
        [decision] = store.list_decisions(axes, binding_id, task_id)
        assert decision.admission_decision_id == decision_id
        assert decision.admitted_scope_ref == task_id
        [participant_ref] = decision.resulting_binding_refs
        [participant] = store.list_participant_bindings(axes, binding_id, task_id)
        assert participant.participant_binding_id == participant_ref
        assert (participant.participant_ref, participant.scope_ref) == (
            BOT_ASSIGNEE, task_id,
        )
        assert participant.role_binding_refs  # Scoped role correlation, no L3 grant.
        assert decision.application_ref == application.application_id
        assert decision.resulting_binding_refs == (participant_ref,)
        assert store.get_roster(axes, binding_id, task_id) is None
        assert store.list_members(axes, binding_id, task_id) == []
        assert store.list_selections(axes, binding_id, task_id) == []
        assert store.list_composition_edges(axes, task_id) == []
        assert open_method.query_opportunities(task_id, binding_id, bot_context) == ()
        assert (
            service.detail_task(task_id).status == "created"
        )  # admit is not Work.start

        # Open 只负责录取和建立参与绑定;任务执行通过任务产品暴露的 Work
        # 动作入口启动。该入口映射到 L4 语义 Work.start,不是 Bot 的内置方法;
        # Bot 仅以 ActorContext 作为调用主体,之后通过 A2A 入站上报执行过程。
        # 不能把通用 ContextCollabService.append_event 当成 Work API:它是
        # 通用事件追加入口,不表达 L4 Work.start 的语义。
        started = service.start_task(
            TaskAdvanceCommand(task_id, note="录取后启动任务执行"), bot_context
        )
        assert started.status == "in_progress"
        assert service.detail_task(task_id).status == "in_progress"
        assert injector.get(A2aDeliveryPort).bridge_records() == ()

        # 5. Bot runtime 只保留核心的 Work 执行入口;具体 AI coding 模拟
        #    位于 case 外部的测试夹具中。
        artifacts = working_bot.run(
            created,
            lambda report_progress: _simulate_ai_coding(
                tmp_path=tmp_path,
                task_id=task_id,
                service=service,
                report_progress=report_progress,
            ),
            initial_revision=started.context_version,
        )  # L4 已 start;Bot 成功返回后通过入站上报 submit,从不自行 accept。
        review = service.detail_task(task_id)
        assert review.status == "reviewing"
        assert review.events[-1]["payload"]["artifacts"] == artifacts

        # 6. 用户查看成果并明确 accept,而不是以 Bot 完工代替验收。
        _flow_log(_TEST_DIRECTORY, "6. 用户检查方案、代码、测试、PR 提案,确认 accept")
        accepted = service.accept_task(
            TaskAdvanceCommand(task_id, note="成果符合要求,验收通过"), HUMAN
        )
        assert accepted.status == "done"
        final = service.detail_task(task_id)
        assert final.status == "done"
        assert [event["event_kind"] for event in final.events] == [
            "task.created",
            "user_interaction.posted",
            "task.started",
            *["task.progress.updated"] * len(_AI_CODING_STEPS),
            "task.submitted",
            "task.accepted",
        ]
        assert [
            event["payload"]["note"]
            for event in final.events
            if event["event_kind"] == "task.progress.updated"
        ] == [note for _, note in _AI_CODING_STEPS]
        assert final.events[-1]["actor_id"] == HUMAN.actor.principal_id
        assert final.events[-2]["actor_id"] == BOT_ASSIGNEE
        _flow_log(_TEST_DIRECTORY, "完整用户旅程结束:用户 accept 后 status=done")


def _participation_snapshot(service, injector, created):
    """SQL observable state and all relevant Method facts, including dispatch."""
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    context_id, binding = created.collet_id, top_level_binding_id(created.collet_id)
    return (
        service.detail_task(context_id),
        store.get_binding(axes, binding, context_id),
        store.get_roster(axes, binding, context_id),
        tuple(store.list_members(axes, binding, context_id)),
        tuple(store.list_participant_bindings(axes, binding, context_id)),
        tuple(store.list_opportunities(axes, binding, context_id)),
        tuple(store.list_applications(axes, binding, context_id)),
        tuple(store.list_decisions(axes, binding, context_id)),
        injector.get(A2aDeliveryPort).bridge_records(),
    )


def _publish_v2(service, profile, assignee=BOT_ASSIGNEE):
    return service.publish_task(
        PublishTaskCommand(
            TITLE, BODY, SPACE, assignee, product_profile_ref=profile.ref
        ),
        HUMAN,
    )


def _apply_to_created(bot, created):
    """独立验证现有签名 HTTP Apply 入口;不把 Open 能力封装成 Bot 方法。"""
    opportunities = bot.request(_opportunities_query_request(bot))["data"]["opportunities"]
    [opportunity] = [
        item for item in opportunities if item["collab_context_id"] == created.collet_id
    ]
    return bot.request(bot.application_request(opportunity))["data"]["admission"]


@pytest.mark.parametrize("mismatch", [
    "actor", "accountable", "opportunity", "application", "binding", "profile",
    "task", "roles", "applicant", "already_admitted",
])
def test_task_application_review_rejects_unmatched_submission(
    commission_deployment, mismatch,
):
    """A known L5 application ID cannot bypass the real product admission policy."""
    profile = build_self_service_commission_profile()
    with commission_deployment(profile) as (service, bot, injector):
        created = _publish_v2(service, profile)
        context_id, binding = created.collet_id, top_level_binding_id(created.collet_id)
        context = ActorContext(PrincipalRef("bot", bot.bot_id), "a2a")
        open_method = injector.get(OpenMethodOpsServiceProtocol)
        [opportunity] = open_method.query_opportunities(context_id, binding, context)
        record = injector.get(ContextCollabRepositoryProtocol).get_current_status(context_id)
        assert record is not None
        submitted = open_method.submit_application(
            context_id, binding, opportunity.opportunity_id,
            OpenApplicationCommand(
                applicant_agent_ref="other_bot" if mismatch == "applicant" else bot.bot_id,
                accountable_principal_ref=bot.owner_id,
                requested_role_refs=[] if mismatch == "roles" else [
                    role_binding_ref(context_id, role)
                    for role in record.context_spec.role_bindings
                    if role.role == "selected-agent"
                ],
            ),
            context,
        )
        command = ApplyForTaskCommand(
            context_id, opportunity.opportunity_id, binding, profile.ref, bot.owner_id,
        )
        application_id = submitted.object_id
        if mismatch == "actor":
            context = HUMAN
        elif mismatch == "accountable":
            command = replace(command, accountable_principal_id="another_owner")
        elif mismatch == "opportunity":
            command = replace(command, opportunity_id="another_opportunity")
        elif mismatch == "application":
            application_id = "another_application"
        elif mismatch == "binding":
            command = replace(command, method_binding_ref="another_binding")
        elif mismatch == "profile":
            command = replace(command, product_profile_ref=TASK_COMMISSION_PROFILE.ref)
        elif mismatch == "task":
            other = _publish_v2(service, profile)
            command = replace(
                command, collab_context_id=other.collet_id,
                method_binding_ref=top_level_binding_id(other.collet_id),
            )
        elif mismatch == "already_admitted":
            service.review_task_application(command, application_id, context)
        before = _participation_snapshot(service, injector, created)
        with pytest.raises(ContextCollabValidationError):
            service.review_task_application(command, application_id, context)
        assert _participation_snapshot(service, injector, created) == before


def test_pure_open_admission_is_task_scoped_and_revocable(commission_deployment):
    profile = build_self_service_commission_profile()
    with commission_deployment(profile) as (service, bot, injector):
        first, second = _publish_v2(service, profile), _publish_v2(service, profile)
        receipt = _apply_to_created(bot, first)
        context = ActorContext(PrincipalRef("bot", BOT_ASSIGNEE), "a2a")
        generic = injector.get(ContextCollabServiceProtocol)
        # Same designated Bot, but only task A has admitted it.
        before_second = _participation_snapshot(service, injector, second)
        bot.request(bot.report_request(second), expected_status="REJECTED")
        with pytest.raises(ContextCollabValidationError, match="participation binding"):
            generic.append_event(
                second.collet_id,
                EventAppendCommand(
                    second.context_version, "task.started", "Task", context.actor,
                    None, "public", {},
                ),
                context,
            )
        assert _participation_snapshot(service, injector, second) == before_second
        # Admission is live until revoked, independent of designation.
        injector.get(TaskMethodOpsPort).require_admitted(first.collet_id, context)
        injector.get(OpenMethodOpsServiceProtocol).revoke_admission(
            first.collet_id, top_level_binding_id(first.collet_id),
            receipt["application_id"], OpenDecisionOpCommand(reason="owner revoke"),
            HUMAN,
        )
        before_first = _participation_snapshot(service, injector, first)
        bot.request(bot.report_request(first), expected_status="REJECTED")
        with pytest.raises(ContextCollabValidationError, match="participation binding"):
            service.start_task(TaskAdvanceCommand(first.collet_id), context)
        with pytest.raises(ContextCollabValidationError, match="participation binding"):
            generic.append_event(
                first.collet_id,
                EventAppendCommand(
                    first.context_version, "task.started", "Task", context.actor,
                    None, "public", {},
                ),
                context,
            )
        assert _participation_snapshot(service, injector, first) == before_first
        store, axes = injector.get(MethodStoreProtocol), current_axes()
        binding = top_level_binding_id(first.collet_id)
        # Preserve evidence without granting authority after revocation.
        assert len(store.list_participant_bindings(axes, binding, first.collet_id)) == 1
        assert store.get_roster(axes, binding, first.collet_id) is None
        assert store.list_members(axes, binding, first.collet_id) == []


@pytest.mark.parametrize("actor", [
    HUMAN,
    ActorContext(PrincipalRef("bot", "another_bot"), "a2a"),
    ActorContext(PrincipalRef("human", BOT_ASSIGNEE), "api"),
])
def test_scoped_open_binding_cannot_authorize_another_identity(
    commission_deployment, actor,
):
    profile = build_self_service_commission_profile()
    with commission_deployment(profile) as (service, bot, injector):
        created = _publish_v2(service, profile)
        _apply_to_created(bot, created)
        before = _participation_snapshot(service, injector, created)
        with pytest.raises(ContextCollabValidationError):
            injector.get(TaskMethodOpsPort).require_admitted(created.collet_id, actor)
        with pytest.raises(ContextCollabValidationError):
            service.start_task(TaskAdvanceCommand(created.collet_id), actor)
        assert _participation_snapshot(service, injector, created) == before


def test_partial_open_admission_does_not_authorize_execution(
    commission_deployment, monkeypatch,
):
    profile = build_self_service_commission_profile()
    with commission_deployment(profile) as (service, bot, injector):
        created = _publish_v2(service, profile)
        [opportunity] = bot.request(_opportunities_query_request(bot))["data"][
            "opportunities"
        ]
        before_sql = service.detail_task(created.collet_id)
        store, axes = injector.get(MethodStoreProtocol), current_axes()
        update_application = store.update_application_state

        def fail_admitted_write(axes, binding, application, context_id):
            if application.state is ApplicationState.ADMITTED:
                raise RuntimeError("application storage unavailable")
            return update_application(axes, binding, application, context_id)

        # Binding and decision are written, but the final application write fails.
        # This is not a successful admission and must not dispatch or permit Work.
        monkeypatch.setattr(store, "update_application_state", fail_admitted_write)
        bot.request(bot.application_request(opportunity), expected_status="FAILED")
        context_id, binding = created.collet_id, top_level_binding_id(created.collet_id)
        [application] = store.list_applications(axes, binding, context_id)
        assert application.state is ApplicationState.SCREENING
        [participant] = store.list_participant_bindings(axes, binding, context_id)
        [decision] = store.list_decisions(axes, binding, context_id)
        assert decision.resulting_binding_refs == (participant.participant_binding_id,)
        assert decision.application_ref == application.application_id
        assert injector.get(A2aDeliveryPort).bridge_records() == ()
        context = ActorContext(PrincipalRef("bot", BOT_ASSIGNEE), "a2a")
        before = _participation_snapshot(service, injector, created)
        with pytest.raises(ContextCollabValidationError, match="participation binding"):
            injector.get(TaskMethodOpsPort).require_admitted(context_id, context)
        bot.request(bot.report_request(created), expected_status="REJECTED")
        with pytest.raises(ContextCollabValidationError, match="participation binding"):
            service.start_task(TaskAdvanceCommand(context_id), context)
        with pytest.raises(ContextCollabValidationError, match="participation binding"):
            injector.get(ContextCollabServiceProtocol).append_event(
                context_id,
                EventAppendCommand(
                    created.context_version, "task.started", "Task", context.actor,
                    None, "public", {},
                ),
                context,
            )
        assert _participation_snapshot(service, injector, created) == before
        assert service.detail_task(context_id) == before_sql
