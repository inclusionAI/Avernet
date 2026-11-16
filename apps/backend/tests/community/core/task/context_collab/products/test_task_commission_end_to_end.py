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

import secrets
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import wraps
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
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations import (
    work as work_operations_module,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.collet_lifecycle import (
    ColletLifecycleService,
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
    OpenOpportunityCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _now,
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    prepare_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l6_products.creation_facts import (
    creation_payload,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepositoryProtocol,
    CreateColletRecordRequest,
    InitialEventDraft,
)
from agentclaw.community.core.task.context_collab.products.task_commission import (
    service as commission_service_module,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
    init_product_profile,
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
from agentclaw.community.core.task.context_collab.services.method_bootstrap import (
    project_created_context,
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
from tests.community.core.task.context_collab.products.task_commission_e2e_support import (
    _AI_CODING_STEPS,
    SimulatedWorkingBot,
    _participation_snapshot,
    _simulate_ai_coding,
    opportunities_query_request,
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
                "create_collet": "创建并持久化 Collet、独立 Task 与初始事件",
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
            "prepare_product_profile",
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


def _selected_agent_role_refs(record) -> list[str]:
    """从持久化 ProductInstance 读取已创建的 selected-agent 角色引用。"""
    assert record.product_instance is not None
    refs = [
        ref
        for ref in record.product_instance.role_binding_refs
        if "/role-bindings/selected-agent/" in ref
    ]
    assert refs
    return refs


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
    params=[TASK_COMMISSION_PROFILE, init_product_profile()],
    ids=["legacy", "initial"],
)
def commission_runtime(commission_deployment, request):
    # Keep failure behavior for both the legacy and self-application flows.
    with commission_deployment(request.param) as (service, bot, _):
        yield service, bot


"""产品上线 → 建任务 → Bot Query/Apply → 策略 Screen/Admit → scoped Participant → Work → accept。"""
def test_end_to_end_commission_flow(commission_deployment, tmp_path):
    # 1、初始化product profile
    profile = init_product_profile()

    # 2、动态注入
    with commission_deployment(profile) as (service, working_bot, injector):
        repository = injector.get(ContextCollabRepositoryProtocol)
        method_store = injector.get(MethodStoreProtocol)
        executor_ref = AgentRef(agent_type="bot", agent_id=BOT_ASSIGNEE)
        injector.get(ContextCollabAuthorizerProtocol).check_create(
            method_kind=profile.composition.method_kind,
            executor_ref=executor_ref,
            actor=HUMAN,
        )

        # 3、创建Collect（对应一个Task）
        creation_facts = creation_payload(
            method_kind=profile.composition.method_kind,
            executor_ref=executor_ref,
            objective=TITLE,
            resource_refs=(),
            product_kind=profile.ref.profile_id,
            workflow_kind=profile.workflow.ref.profile_id,
            product_profile_ref=profile.ref,
        )
        created_record = repository.create_collet(
            CreateColletRecordRequest(
                space=SPACE,
                principal_ref=HUMAN.actor,
                method_kind=profile.composition.method_kind,
                executor_ref=executor_ref,
                objective=TITLE,
                resource_refs=(),
                product_kind=profile.ref.profile_id,
                workflow_kind=profile.workflow.ref.profile_id,
                product_profile_ref=profile.ref,
                initial_event=InitialEventDraft(
                    event_kind="task.created",
                    primitive_type="Task",
                    actor_ref=HUMAN.actor,
                    payload=creation_facts,
                ),
                creation_facts=creation_facts,
            )
        )
        binding = project_created_context(
            method_store,
            current_axes(),
            created_record.collet.collet_id,
            method_kind=profile.composition.method_kind,
            principal_ref=HUMAN.actor,
            executor_ref=executor_ref,
            profile=profile,
            context=HUMAN,
            now=_now(),
        )

        created = TaskCommissionCreatedView(
            collet_id=created_record.collet.collet_id,
            title=TITLE,
            body_event_id="",
            status="created",
            context_version=created_record.collet.version,
        )
        task_id = created.collet_id

        store, axes = injector.get(MethodStoreProtocol), current_axes()
        binding_id = top_level_binding_id(task_id)
        open_method = injector.get(OpenMethodOpsServiceProtocol)

        # 4、调用L5的OPEN方法创建机会
        published = open_method.publish_opportunity(
            collab_context_id=task_id,
            method_binding_id=binding_id,
            command=OpenOpportunityCommand(
                scope_ref=task_id,
                participation_type="COLLET_PARTICIPANT",
                opens_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                admission_policy_refs=[
                    "task_commission.designated_bot_self_application/1" #录取策略：指定 Bot 自己申请，并由任务产品的策略自动审核
                ],
                capacity=1, #表示这个机会最多允许多少个未终态申请占用名额
                reason="task_commission.owner_authorized_publish", #本次发布操作的审计原因
            ),
            context=HUMAN, # actor context
        )
        assert published.accepted and published.state == "OPEN"

        # 5、调用L5的OPEN方法查询机会
        bot_context = ActorContext(PrincipalRef("bot", working_bot.bot_id), "a2a")
        [opportunity] = open_method.query_opportunities(
            collab_context_id=task_id,
            method_binding_id=binding_id,
            context=bot_context,
        )
        assert opportunity.method_binding_id == binding_id
        assert opportunity.scope_ref == task_id

        # 6、调用 L5 Open Method的提交申请。
        record = injector.get(ContextCollabRepositoryProtocol).get_current_status(task_id)
        assert record is not None
        submitted = open_method.submit_application(
            collab_context_id=task_id,
            method_binding_id=opportunity.method_binding_id,
            opportunity_ref=opportunity.opportunity_id,
            command=OpenApplicationCommand(
                applicant_agent_ref=working_bot.bot_id,
                accountable_principal_ref=working_bot.owner_id,
                requested_role_refs=_selected_agent_role_refs(record),
                reason="task_commission.bot_self_application",
            ),
            context=bot_context,
        )
        assert submitted.accepted and submitted.state == "SUBMITTED"

        # 7、针对每个机会，检查有哪些申请者，并选择一个候选者
        policy_context = ActorContext(HUMAN.actor, source="product_policy")
        [policy_ref] = opportunity.admission_policy_refs
        screened = open_method.screen(
            collab_context_id=task_id,
            method_binding_id=binding_id,
            application_ref=submitted.object_id,
            command=OpenDecisionOpCommand(reason=policy_ref),
            context=policy_context,
        )
        assert screened.accepted and screened.state == "SCREENING"
        assert screened.object_id == submitted.object_id

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

        # 8、通用事件追加入口,不表达 L4 Work.start 的语义。
        started = service.start_task(
            TaskAdvanceCommand(task_id, note="录取后启动任务执行"), bot_context
        )
        assert started.status == "in_progress"
        assert service.detail_task(task_id).status == "in_progress"
        assert injector.get(A2aDeliveryPort).bridge_records() == ()

        # 9. 模拟Agent执行任务，过程中上报信息
        artifacts = working_bot.run(
            created,
            lambda report_progress: _simulate_ai_coding(
                tmp_path=tmp_path,
                task_id=task_id,
                service=service,
                report_progress=report_progress,
            ),
            initial_revision=started.context_version,
        )
        review = service.detail_task(task_id)
        assert review.status == "reviewing"
        assert review.events[-1]["payload"]["artifacts"] == artifacts


        # 10. 用户查看成果并明确 accept,而不是以 Bot 完工代替验收。
        _flow_log(_TEST_DIRECTORY, "6. 用户检查方案、代码、测试、PR 提案,确认 accept")
        accepted = service.accept_task(
            TaskAdvanceCommand(task_id, note="成果符合要求,验收通过"), HUMAN
        )
        assert accepted.status == "done"

        final = service.detail_task(task_id)
        assert final.status == "done"
        assert [event["event_kind"] for event in final.events] == [
            "task.created",
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
