"""产品 Profile 上线 → 建任务 → Bot 自查机会/申请/录取 → L4 Work → 用户验收。

用户通过产品 Service API 操作和观察;case 显式以 Bot ActorContext 调用
L5 Open 查询和提交申请,再以发布人授权的策略身份直接调用 Open.screen/admit,
录取后直接调用 L4 协作服务的 Work.start;Bot 只负责执行并通过签名 A2A HTTP 入站上报。
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
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenAdmitCommand,
    OpenApplicationCommand,
    OpenDecisionOpCommand,
    OpenMethodOpsServiceProtocol,
    OpenOpportunityCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    STANDARD_TASK_CONTRACT_REF,
)
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    ProductProfileCreateCommand,
    create_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.publication import (
    OpenPublicationProfile,
)
from agentclaw.community.core.task.context_collab.products.task_commission.publication import (
    BotSelfApplicationAdmission,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    ContextCollabRepositoryProtocol as L4ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.repository import (
    ContextCollabRepository as L4ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.records import ContextStatusRecord
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.products.task_commission import (
    service as commission_service_module,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    ApplyForTaskCommand,
    PublishTaskCommand,
    TaskAdvanceCommand,
    TaskCommissionCreatedView,
    TaskCommissionServiceProtocol,
    TaskMethodOpsPort,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiateCommand,
    ProductInstantiationServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
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
OBJECTIVE = "为计算器增加求和能力:完成方案、编码和测试,生成待评审 PR,提交成果后由业务同学验收。"

"""产品上线 → 建任务 → Bot Query/Apply → 策略 Screen/Admit → scoped Participant → Work → accept。"""
def test_end_to_end_commission_flow(commission_deployment, tmp_path):
    # 1、调用L6层的方法，创建product profile
    l6_profile = create_product_profile(
        ProductProfileCreateCommand(
            ref=VersionedProfileRef("task_commission", 1),            # 版本
            composition=TASK_COMMISSION_PROFILE.composition,          # 绑定L5层方法，这里是OPEN方法
            scenario_roles=TASK_COMMISSION_PROFILE.scenario_roles,    # 角色信息
            governance=TASK_COMMISSION_PROFILE.governance,            # g1-g4配置
            compatibility=TASK_COMMISSION_PROFILE.compatibility,      # 能力需求声明
            publication=OpenPublicationProfile(                       # OPEN方法发布录取策略 TODO:change_name
                admission=BotSelfApplicationAdmission(                # 指定执行者、发布人代办录取的策略
                    applicant_role="assignee",
                    accountable_role="owner",
                    participation_type=ParticipationType.COLLET_PARTICIPANT,  # 录取为当前 Collet 的参与者
                    capacity=1,    # 最多录取一个申请者
                ),
            ),
        )
    )

    # 动态注入
    with commission_deployment(l6_profile) as (service, working_bot, injector):
        l4_context_operate = injector.get(ContextServiceProtocol)
        l2_executor_ref = AgentRef(agent_type="bot", agent_id=BOT_ASSIGNEE)
        store, axes = injector.get(MethodStoreProtocol), current_axes()
        l5_open_method = injector.get(OpenMethodOpsServiceProtocol)

        # 2、通过L4层操作创建Collet
        l4_collet = l4_context_operate.create_collet(
            ColletCreateCommand(
                to_space=SPACE,  # TODO: Collect所属的空间（长期持续管理的空间，比如小队、团队空间）
                initiator_ref=HUMAN.actor,  # 发起主体引用
                objective=OBJECTIVE,
                executor_refs=(l2_executor_ref,),  # () / (bot,) / (bot1, bot2, ...)
            ),
            HUMAN,  # 代办任务创建人
        )
        collect_id = l4_collet.collet_id

        # 3、L6 实例化选择产品并编排 L5 Method/角色引用;不是 L4 create 的副作用。
        instance = injector.get(ProductInstantiationServiceProtocol).instantiate(
            ProductInstantiateCommand(collect_id, l6_profile.ref, l4_collet.version),
            HUMAN,
        )
        assert instance.collet_ref == collect_id
        binding_id = top_level_binding_id(collect_id)

        # 4、调用L5的OPEN方法-创建机会
        published = l5_open_method.publish_opportunity(
            OpenOpportunityCommand(
                collet_id=collect_id,
                actor_context=HUMAN, # actor context
                participation_type=ParticipationType.COLLET_PARTICIPANT,
                admission_policy_refs=[
                    "task_commission.designated_bot_self_application/1" #录取策略：指定 Bot 自己申请，并由任务产品的策略自动审核
                ],
                capacity=1,              #表示这个机会最多允许多少个未终态申请占用名额
                reason="task_commission.owner_authorized_publish", #本次发布操作的审计原因
            ),
        )
        assert published.accepted and published.state == "OPEN"

        # 5、调用L5的OPEN方法-查询机会（实际应该是Agent自己调用）
        bot_context = ActorContext(PrincipalRef("bot", working_bot.bot_id), "a2a")
        [opportunity] = l5_open_method.query_opportunities(
            actor_context=bot_context,
        )
        assert opportunity.method_binding_id == binding_id
        assert opportunity.scope_ref == collect_id

        # 6、调用 L5 Open Method的-提交申请。
        record = injector.get(ContextCollabRepositoryProtocol).get_current_status(collect_id)
        assert record.task is not None
        task_id = record.task.task_id
        submitted = l5_open_method.submit_application(
            collab_context_id=collect_id,
            method_binding_id=opportunity.method_binding_id,
            opportunity_ref=opportunity.opportunity_id,
            command=OpenApplicationCommand(
                applicant_agent_ref=working_bot.bot_id,                  # 提交申请的bot自身信息, TODO: uuid
                accountable_principal_ref=HUMAN.actor.principal_id,      # TODO
                requested_role_refs=_selected_agent_role_refs(record),   # TODO
                reason="task_commission.bot_self_application",           # 声明本次查询的真实调用者，并经过授权检查；它不是查询目标，也不代表这个 Bot 已获得参与或执行任务的权限。
            ),
            context=bot_context,
        )
        assert submitted.accepted and submitted.state == "SUBMITTED"

        # 提交申请还不等于录取,不能启动 Work;拒绝不能写入 Task/Event。
        repository = injector.get(ContextCollabRepositoryProtocol)
        before_admission = repository.get_current_status(collect_id)
        with pytest.raises(ContextCollabValidationError):
            l4_context_operate.start(
                collect_id, WorkStartCommand(expected_version=l4_collet.version), bot_context
            )
        assert repository.get_current_status(collect_id) == before_admission

        # 7、针对每个机会，检查申请信息，并选择一个候选者录取
        policy_context = ActorContext(HUMAN.actor, source="product_policy")   # 采用product profile中定义的录取策略
        [policy_ref] = opportunity.admission_policy_refs
        screened = l5_open_method.screen(
            collab_context_id=collect_id,
            method_binding_id=binding_id,
            application_ref=submitted.object_id,
            command=OpenDecisionOpCommand(reason=policy_ref),
            context=policy_context,
        )
        assert screened.accepted and screened.state == "SCREENING"
        assert screened.object_id == submitted.object_id

        decision_id = f"dec_{uuid4().hex[:12]}"
        admitted = l5_open_method.admit(  # 要不要中心化的service
            collab_context_id=collect_id,
            method_binding_id=binding_id,
            application_ref=submitted.object_id,
            command=OpenAdmitCommand(
                admitted_scope_ref=collect_id,
                decision_id=decision_id,
                reason=policy_ref,
            ),
            context=policy_context,
        )
        assert admitted.accepted and admitted.state == "ADMITTED"
        assert admitted.object_id == submitted.object_id
        assert injector.get(A2aDeliveryPort).bridge_records() == ()
        [application] = store.list_applications(axes, binding_id, collect_id)
        assert application.state is ApplicationState.ADMITTED
        assert application.applicant_agent_ref == BOT_ASSIGNEE

        # 8、直接调用 L4 服务的 Work.start;不是产品 start_task 或通用事件追加接口。
        started = l4_context_operate.start(
            collect_id,
            WorkStartCommand(
                expected_version=l4_collet.version,
                note="录取后启动任务执行",
            ),
            bot_context,
        )
        assert started.accepted and started.current_status == "in_progress"
        assert service.detail_task(collect_id).status == "in_progress"
        assert injector.get(A2aDeliveryPort).bridge_records() == ()

        # 9. 模拟Agent执行任务，过程中上报信息
        created = TaskCommissionCreatedView(
            collet_id=l4_collet.collet_id,
            title=OBJECTIVE,
            body_event_id="",
            status="created",
            context_version=l4_collet.version,
        )
        artifacts = working_bot.run(
            created,
            lambda report_progress: _simulate_ai_coding(
                tmp_path=tmp_path,
                collet_id=collect_id,
                task_id=task_id,
                service=service,
                report_progress=report_progress,
            ),
            task_id=task_id,
            initial_revision=started.context_version,
        )
        review = service.detail_task(collect_id)
        assert review.status == "reviewing"
        assert review.events[-1]["payload"]["artifacts"] == artifacts


        # 10. 用户查看成果并明确 accept,而不是以 Bot 完工代替验收。
        _flow_log(_TEST_DIRECTORY, "6. 用户检查方案、代码、测试、PR 提案,确认 accept")
        accepted = service.accept_task(
            TaskAdvanceCommand(collect_id, note="成果符合要求,验收通过"), HUMAN
        )
        assert accepted.status == "done"

        # 11. 经应用门面的 L4 查询读取 Task 状态与产物,不依赖产品详情接口。
        # TODO: 管理时、运行时分开
        final = l4_context_operate.get_status(collect_id, trunk_events=False, context=HUMAN)
        assert final.current_snapshot is not None
        assert final.current_snapshot["status"] == "done"
        assert final.context_version == accepted.context_version
        assert final.current_snapshot["progress"]["percent"] == 100

        final_events = l4_context_operate.get_events(collect_id, context=HUMAN)
        assert [event.event_kind for event in final_events] == [
            "task.created",
            "task.started",
            *["task.progress.updated"] * len(_AI_CODING_STEPS),
            "task.submitted",
            "task.accepted",
        ]
        assert [
            event.payload["note"]
            for event in final_events
            if event.event_kind == "task.progress.updated"
        ] == [note for _, note in _AI_CODING_STEPS]
        progress_events = [
            event for event in final_events if event.event_kind == "task.progress.updated"
        ]
        assert task_id != collect_id
        for index, event in enumerate(progress_events):
            assert event.payload["task_id"] == task_id
            assert event.payload["progress"]
            assert event.payload["error_msg"] == ""
            assert event.payload["reason"] == f"产物已生成：{artifacts[index]}"
        [submitted_event] = [
            event for event in final_events if event.event_kind == "task.submitted"
        ]
        assert submitted_event.payload is not None
        assert submitted_event.payload["artifacts"] == artifacts
        assert final_events[-1].actor_ref == HUMAN.actor
        assert submitted_event.actor_ref == bot_context.actor
        _flow_log(_TEST_DIRECTORY, "完整用户旅程结束:L4 查询确认 status=done 且产物完整")



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
            status = snapshot.get("status") if isinstance(snapshot, dict) else snapshot.status
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
                "_advance": "按固定 Task/Workflow 合同推进 L4 Work 动作",
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
            ContextServiceProtocol,
            {
                "create_collet": "L4 服务创建 Collet、根 Task 与初始 Event",
                "start": "L4 服务经 G1/G4 启动根 Task,不派发 Bot",
                "get_status": "L4 服务读取授权后的状态投影",
                "get_events": "L4 服务读取可见事件事实",
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
            L4ContextCollabRepositoryProtocol[ContextStatusRecord],
            {
                "create_l4_collet": "L4 仓储原子持久化 Collet、独立 Task 与初始事件",
                "append_event": "L4 仓储追加事件并原子保存 Task;返回已保存状态",
            },
        ),
        (
            ContextCollabRepositoryProtocol,
            {"attach_product_instance": "L6 显式选择产品实例,不改写初始事件"},
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
    params=[
        TASK_COMMISSION_PROFILE,
        create_product_profile(
            ProductProfileCreateCommand(
                ref=TASK_COMMISSION_PROFILE.ref,
                composition=TASK_COMMISSION_PROFILE.composition,
                scenario_roles=TASK_COMMISSION_PROFILE.scenario_roles,
                domain=TASK_COMMISSION_PROFILE.domain,
                workflow=TASK_COMMISSION_PROFILE.workflow,
                governance=TASK_COMMISSION_PROFILE.governance,
                compatibility=TASK_COMMISSION_PROFILE.compatibility,
                publication=TASK_COMMISSION_PROFILE.publication,
            )
        ),
    ],
    ids=["legacy", "initial"],
)
def commission_runtime(commission_deployment, request):
    # Keep failure behavior for both the legacy and self-application flows.
    with commission_deployment(request.param) as (service, bot, _):
        yield service, bot
