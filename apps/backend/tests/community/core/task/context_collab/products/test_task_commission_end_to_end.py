"""ProductProfile → 创建 Collet → 实例化 ProductInstance → Open 申请/录取 → Single 选定 → L4 Work → 验收。

用户通过产品 Service API 操作和观察;模拟 Bot 以自身 ActorContext 调用注入的
L5 Open 服务查询机会,再自己构造申请命令并提交申请。
发布人授权的策略身份调用 Open.screen/admit,再显式 Single.select 后调用 L4 Work.start;
Bot 随后执行,直接调用 G3 记录步骤 Observation、进度 MetricSample 和结果 Outcome,通过签名 A2A HTTP 提交成果。
使用真实产品 Profile、仓储、L5 Open 和 G1/G4。直接 Open 调用不验证 JWT;
另保留真实产品策略的签名 HTTP Query/Apply 回归。Open 负责参与录取,
开始与成果提交属于 L4 Work;步骤详情是 G3 观测,不推进 Work 状态。
仅模拟 Bot 执行和网关签名;本地产物不上传,不声称验证真实远端 Bot、
网关转发或 LLM 执行。
"""

import secrets
from contextlib import ExitStack, contextmanager
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
from agentclaw.community.core.task.context_collab.g3_observe_evaluate_evolve.service_api import (
    TelemetryServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.delivery import (
    A2aDeliveryPort,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations import (
    work as work_operations_module,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.persistence.records import (
    ContextCollabRepositoryProtocol as L4ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionRelation,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    GroupMethodOpsServiceProtocol,
    OpenAdmitCommand,
    OpenMethodOpsServiceProtocol,
    OpenOpportunityCommand,
    OpenScreenCommand,
    SingleSelectCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    CompatibilityRequirements,
    GovernanceRequirements,
    MethodCompositionTemplate,
    MethodRelationTemplate,
    ScenarioRole,
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    OpenMethodPolicy,
)
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    ProductProfileCreateCommand,
    create_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiationServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.participant_scope import (
    ParticipantScope,
    ParticipantScopeKind,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.records import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    BotSelfApplicationAdmission,
    OpenPublicationProfile,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.products.task_commission import (
    service as commission_service_module,
)
from agentclaw.community.core.task.context_collab.products.task_commission.profile import (
    TASK_COMMISSION_PROFILE,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    TaskAdvanceCommand,
    TaskCommissionCreatedView,
    TaskCommissionServiceProtocol,
    TaskMethodOpsPort,
)
from agentclaw.community.di.modules.context_collab_products import (
    INSTALLED_PRODUCT_PROFILES,
)
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.di.modules.task_persistence_module import TaskPersistenceModule
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from tests.community.core.task.context_collab.products.task_commission_e2e_support import (
    SimulatedWorkingBot,
    _simulate_ai_coding,
)
from tests.community.core.task.context_collab.products.task_commission_e2e_support import (
    _instantiate_product_instance as _instantiate_product_with_resources,
)
from tests.community.factories.task_commission import (
    BOT_ID as BOT_ASSIGNEE,
)
from tests.community.factories.task_commission import OWNER

HUMAN = ActorContext(actor=PrincipalRef("human", "u_owner_001"), source="api")
SPACE = SpaceRef(space_id="space_workspace_001", environment="test")
INPUT_RESOURCES = (
    ResourceRef("document", "calculator_requirements", "v1"),
    ResourceRef("repository", "calculator", "baseline-v1"),
)
OTHER_ELIGIBLE_AGENT = AgentRef("bot", "bot_candidate_002")
ADMISSION_AGENT = AgentRef("bot", "bot_admission_judge_001")
ADMISSION_JUDGE = ActorContext(
    PrincipalRef("bot", ADMISSION_AGENT.agent_id), source="agent_admission"
)
OUTSIDE_AGENT = AgentRef("bot", "bot_outside_scope")
OBJECTIVE = (
    "为计算器增加求和能力:完成方案、编码和测试,生成待评审 PR,提交成果后由业务同学验收。"
)

"""产品上线 → Collet/Instantiate → Bot Query/Apply → Screen/Admit → Single → start 建 Task → accept。"""
"""
协议适配问题总结：
- 语义不明
  - [ ] Collet语义明确性问题：这份 ACCS 协议没有给出 Collet 的完整成员变量定义
  - [ ] open模式下，任务执行到底是在录取之后，还是在录取之前，协议没有明确说明，还是说都可以？
  - [ ] open模式下，当录取了某个agent进入协作之后，谁来通知agent真正开始干活，没有明确的语义
- 能力缺少
  - [ ] open模式下，协议中的DataModel只有机会发布、参与申请、决策结果这三个，缺少录取判断策略（谁来做录取判断、怎么判断）
- 问题  
  - [ ] open方法中的参与类型，协议中的枚举不在一个维度：participation_type: COLLET_PARTICIPANT | GROUP_MEMBER | SWARM_ALLOCATION | SINGLE_CONTRIBUTION
"""


def test_end_to_end_commission_flow(deploy_commission, tmp_path):
    # 1. L6层产品定义是可复用合同,创建 Profile 本身不产生协作运行对象。
    profile = _create_product_profile()

    # 测试用例需要的模块动态注入
    service, working_bot, injector = deploy_commission(profile)
    store, axes = injector.get(MethodStoreProtocol), current_axes()
    l4_context_operate = injector.get(ContextServiceProtocol)
    l5_open_method = injector.get(OpenMethodOpsServiceProtocol)

    # 2. 使用L4层操作创建Collet
    collet = _create_commission_collet(l4_context_operate)

    # 3. 在已有Collet上实例化产品,不是创建 Collet和产品。Collet中并没有Profile信息，所以这里是将精确版本的 Profile 应用于该 Collet,生成记录本次协作配置/绑定引用的 ProductInstance:
    # - 资源:绑定产品传入的需求文档、代码仓库版本引用(INPUT_RESOURCES),不上传内容或授予访问权限。
    # - 参与者/角色:helper 传入发布者 HUMAN 和工作 Bot,建立实际 Participant / RoleBinding。
    #     录取审核 Agent 只由 Profile 精确配置,不是初始工作参与者,也没有执行者 RoleBinding。
    # - 方法:创建 Open@0.2、Single@0.2 的 MethodBinding,并建立 Single --support--> Open 组合关系。
    # ProductInstance 关联已有 Collet、Profile 版本及上述绑定证据,不是另一份 Collet 或 Task。
    # 初始参与者绑定不等于 Open 录取或 Single 执行者选择;这些由后续步骤完成。
    # 此时仍没有 Task(root_task_ref 为 None);第 8 步调用 L4 start 时才创建并启动根 Task。
    instance = _instantiate_product_instance(
        injector,
        collet,
        profile,
        resource_refs=INPUT_RESOURCES,
    )

    # 4. 发布机会， "METHOD"方法
    _publish_opportunity(l5_open_method, collet.collet_id)

    # 5. Agent主动发现机会
    opportunity, submitted = _discover_opportunity(store, axes, collet, working_bot)

    # 6. 由 Profile 配置的审核 Agent 按能力证据执行录取判断。
    decision = _admit_application_agent(
        injector,
        l5_open_method,
        store,
        axes,
        collet.collet_id,
        opportunity,
        submitted,
        profile,
    )

    # 7. 明确选择该次录取产生的参与者，进入"SINGLE:方法
    _select_admitted_agent(injector, store, axes, instance, decision)

    # 8. 通知agent开始执行任务
    started, task_id = _notify_agent_work(
        injector, l4_context_operate, service, collet, working_bot.actor_context
    )

    # 9. 模型Agent自主执行。
    artifacts = _agent_run_simulate(
        service, collet, working_bot, started, tmp_path, task_id
    )

    # 10. 查询和验证结果
    _query_task_result(
        l4_context_operate,
        service,
        collet.collet_id,
        working_bot.actor_context,
        task_id,
        artifacts,
    )


def _create_product_profile():
    # TODO: collet不应该传到scope？    (fixed: 已去掉参数)
    authored = ProductProfileCreateCommand(
        # Open + Single 使用 v2,保留 v1 的纯 Open 合同。
        ref=VersionedProfileRef("task_commission", 2),
        # 绑定 L5 的 Open 和 Single 方法。
        composition=MethodCompositionTemplate(
            method_kind="Open + Single",
            required_methods=(
                get_profile("Open", "0.2"),
                get_profile("Single", "0.2"),
            ),
            relations=(
                MethodRelationTemplate("Single", "Open", CompositionRelation.SUPPORT),
            ),
            policies=(
                OpenMethodPolicy(
                    publication=OpenPublicationProfile(
                        admission=BotSelfApplicationAdmission(
                            applicant_role="assignee",
                            accountable_role="owner",
                            participation_type=ParticipationType.COLLET_PARTICIPANT,
                            capacity=1,
                            decision_actor="configured_agent",
                            decision_agent_ref=ADMISSION_AGENT,
                            decision_rule="capability_evidence",
                        ),
                    ),
                ),
            ),
        ),
        # 三个 Agent 有参与资格;只有 BOT_ASSIGNEE 在实例化时绑定为执行者。
        participant_scope=ParticipantScope(
            ParticipantScopeKind.ALLOWLIST,
            (
                AgentRef("bot", BOT_ASSIGNEE),
                OTHER_ELIGIBLE_AGENT,
                ADMISSION_AGENT,
            ),
        ),
        # 角色信息
        scenario_roles=(
            ScenarioRole(
                "owner",
                "responsible_user",
                "principal_ref",
                True,
                ("define_goal", "accept", "reject", "cancel"),
            ),
            ScenarioRole(
                "assignee",
                "selected-agent",
                "executor_ref",
                True,
                ("execute", "report_progress", "submit"),
            ),
        ),
        # g1-g4的定义
        governance=GovernanceRequirements(
            policy="g1.structural/1",
            reliability="g2.optimistic-version/1",
            observation="g3.event-projection/1",
            consistency="g4.product-contract/1",
        ),
        # 能力需求声明
        compatibility=CompatibilityRequirements(
            accs_revision="0.2",
            required_layers=("L2", "L3", "L4", "L5"),
            required_extensions=(),
            optional_extensions=(),
            downgrade_policy="reject_missing_required",
            migration_policy="new_instances_only",
        ),
    )

    profile = create_product_profile(
        ProductProfileCreateCommand(
            ref=authored.ref,
            participant_scope=authored.participant_scope,
            composition=authored.composition,
            scenario_roles=authored.scenario_roles,
            governance=authored.governance,
            compatibility=authored.compatibility,
        )
    )
    assert profile.ref == VersionedProfileRef("task_commission", 2)
    assert profile.participant_scope.kind is ParticipantScopeKind.ALLOWLIST
    assert profile.participant_scope.agent_refs == (
        AgentRef("bot", BOT_ASSIGNEE),
        OTHER_ELIGIBLE_AGENT,
        ADMISSION_AGENT,
    )
    assert profile.composition.method_kind == "Open + Single"
    assert profile.composition.required_methods == (
        get_profile("Open", "0.2"),
        get_profile("Single", "0.2"),
    )
    admission = profile.composition.require_policy("Open").publication.admission
    assert admission.decision_actor == "configured_agent"
    assert admission.decision_agent_ref == ADMISSION_AGENT
    assert admission.decision_rule == "capability_evidence"
    return profile


def _create_commission_collet(l4_context_operate):
    # Collet 只提供上下文身份/描述;Task 在后续 L4 start 中才创建。
    # TODO l4_context_operate需要协议层统一提供                (fixed：本身就是L4层协议提供)
    # TODO: to_space, executor_refs 是不是该从其他地方提取？    (fixed: 这里不再传入)
    collet = l4_context_operate.create_collet(
        ColletCreateCommand(
            initiator_ref=HUMAN.actor,
            title="XX代办任务",
            description="Open 参与 + Single 选定执行者,成果由用户验收",
        ),
        HUMAN,  # 代办任务创建人 TODO: 一次待办的Product，因为任务发起者不同，需要有多个collet.  (fixed: 一个collet本就对应一个代办任务)
    )
    assert collet.title == "XX代办任务"
    assert collet.created_by == collet.updated_by == HUMAN.actor
    assert collet.created_at == collet.updated_at
    assert collet.schema_version is not None and collet.etag

    return collet


def _instantiate_product_instance(
    injector, collet, profile, *, resource_refs: tuple[ResourceRef, ...] = ()
):
    return _instantiate_product_with_resources(
        injector,
        collet,
        profile,
        context=HUMAN,
        participant_ref=AgentRef("bot", BOT_ASSIGNEE),
        resource_refs=resource_refs,
    )


def _select_admitted_agent(injector, store, axes, instance, decision):
    open_binding_id, binding_id = instance.method_binding_refs
    [participant_ref] = decision.resulting_binding_refs
    selected = injector.get(GroupMethodOpsServiceProtocol).select(
        instance.collet_ref,
        binding_id,
        SingleSelectCommand(
            agent_participant_ref=participant_ref,
            scope_ref=instance.collet_ref,
            selection_evidence_refs=[
                decision.application_ref,
                decision.admission_decision_id,
            ],
            reason="task_commission.select_admitted_assignee",
        ),
        ActorContext(HUMAN.actor, source="product_policy"),
    )
    assert selected.accepted and selected.state == "ACTIVE"
    [selection] = store.list_selections(axes, binding_id, instance.collet_ref)
    assert store.list_selections(axes, open_binding_id, instance.collet_ref) == []
    assert selection.method_binding_id == binding_id
    assert selection.selected_agent_participant_ref == participant_ref
    assert selection.scope_ref == instance.collet_ref
    assert selection.selected_by_ref == HUMAN.actor.principal_id
    return selection


def _publish_opportunity(l5_open_method, collet_id_1):
    # 4、调用L5的OPEN方法-创建机会
    # TODO: actor_context为什么要传HUMAN, HUMAN是collet里面已经有了。  (fixed: 已去掉)
    published = l5_open_method.publish_opportunity(
        OpenOpportunityCommand(
            collet_id=collet_id_1,  # collet
            participation_type=ParticipationType.COLLET_PARTICIPANT,  # 协议中明确规定的字段及取值
            admission_policy_refs=[
                "task_commission.designated_bot_self_application/1"  # 兼容策略 ref;实际决策者/规则由 Profile 中的 admission 配置决定
            ],
            capacity=1,  # 表示这个机会最多允许多少个未终态申请占用名额
            reason="task_commission.owner_authorized_publish",  # 本次发布操作的审计原因
        ),
    )
    assert published.accepted and published.state == "OPEN"

    return published


def _discover_opportunity(store, axes, l4_collet, working_bot):
    collet_id_1 = l4_collet.collet_id

    # Step1：由 Bot 自己调用注入的 L5 Open 服务查询机会，比如bot自己通过skill查询。
    # TODO: 每个bot skill         (fixed)这里方法名突出一下bot通过skill“发现机会”、“提交申请”
    [opportunity] = working_bot.query_opportunities_by_skill()

    # Step2：由 Bot 自己调用注入的 L5 Open 服务提交申请
    # 问题：从协议中的如下描述，提交应用、录取agent之后，只是将agent拉到协作中，然后才开始真正干活,不过确实存在意义不明
    """
        publish opportunity
          → Query/discover opportunity
          → submit application or contribution
          → authenticate applicant
          → evaluate Requirement and policy
          → apply capacity/rate/anti-abuse controls
          → Decision: admit or reject
          → create scoped Participant or Method-specific participation binding
    """
    submitted = working_bot.submit_application_by_skill(
        opportunity=opportunity,
        capability_evidence_refs=["evidence://calculator-coding"],
        requested_role="assignee",
    )

    assert submitted.accepted and submitted.state == "SUBMITTED"
    application = store.get_application(
        axes, submitted.object_id, opportunity.method_binding_id, collet_id_1
    )
    assert application.state is ApplicationState.SUBMITTED
    assert application.applicant_agent_ref == working_bot.bot_uuid
    assert application.transitioned_by == working_bot.bot_uuid
    assert application.requested_role_refs == (
        f"{collet_id_1}/role-bindings/selected-agent/participant_executor_ref",
    )

    return opportunity, submitted


def _configured_agent_decides(admission, application):
    """模拟配置的审核Agent执行已声明的 capability_evidence 规则。"""
    assert admission.decision_rule == "capability_evidence"
    return "admit" if application.capability_evidence_refs else "reject"


def _admit_application_agent(
    injector,
    l5_open_method,
    store,
    axes,
    collet_id_1,
    opportunity,
    submitted,
    profile,
):
    policy_context = ADMISSION_JUDGE
    [policy_ref] = opportunity.admission_policy_refs
    # TODO: 系统服务进行录用判断？      (fixed: l5_open_method本就是协议提供的操作)
    screened = l5_open_method.screen(
        command=OpenScreenCommand(
            application_ref=submitted.object_id,
            context=policy_context,
            collect_id=collet_id_1,
            reason=policy_ref,
        ),
    )
    assert screened.accepted and screened.state == "SCREENING"
    assert screened.object_id == submitted.object_id

    decision_id = f"dec_{uuid4().hex[:12]}"
    admitted = l5_open_method.admit(
        command=OpenAdmitCommand(
            application_ref=submitted.object_id,
            context=policy_context,
            collect_id=collet_id_1,
            decision_id=decision_id,
            reason=policy_ref,
        ),
    )

    assert admitted.accepted and admitted.state == "ADMITTED"
    assert admitted.object_id == submitted.object_id
    assert injector.get(A2aDeliveryPort).bridge_records() == ()
    [application] = store.list_applications(
        axes, opportunity.method_binding_id, collet_id_1
    )
    assert application.state is ApplicationState.ADMITTED
    assert application.applicant_agent_ref == BOT_ASSIGNEE
    decision = store.get_decision(
        axes, decision_id, opportunity.method_binding_id, collet_id_1
    )
    assert decision.admitted_scope_ref == collet_id_1
    assert store.list_selections(axes, opportunity.method_binding_id, collet_id_1) == []
    return decision


def _notify_agent_work(injector, l4_context_operate, service, l4_collet, bot_context):
    collet_id_1 = l4_collet.collet_id

    # 同一实例验证 G1:范围外 Agent 拒绝;范围内但未绑定的候选也没有执行权。
    repository = injector.get(ContextCollabRepositoryProtocol)
    before = repository.get_current_status(collet_id_1)
    assert before.task is None and before.tasks == ()
    for agent, error in (
        (OUTSIDE_AGENT, "ProductProfile participant scope"),
        (OTHER_ELIGIBLE_AGENT, "only the principal, executor"),
    ):
        with pytest.raises(ContextCollabValidationError, match=error):
            l4_context_operate.start(
                collet_id_1,
                WorkStartCommand(
                    expected_version=before.collet.version, objective=OBJECTIVE
                ),
                ActorContext(
                    PrincipalRef(agent.agent_type, agent.agent_id), source="a2a"
                ),
            )
        assert repository.get_current_status(collet_id_1) == before

    # 8、真实执行 Bot 在允许范围内且已经录取/选定,经真实 G1/G4 后启动。
    started = l4_context_operate.start(
        collet_id_1,
        WorkStartCommand(
            expected_version=before.collet.version,
            note="录取并经 Single 选定后启动任务执行",
            objective=OBJECTIVE,
            completion_conditions=("提交成果并由用户验收通过",),
        ),
        bot_context,
    )
    assert started.accepted and started.current_status == "in_progress"
    assert service.detail_task(collet_id_1).status == "in_progress"
    assert injector.get(A2aDeliveryPort).bridge_records() == ()

    record = injector.get(ContextCollabRepositoryProtocol).get_current_status(
        collet_id_1
    )
    assert record.task is not None and len(record.tasks) == 1
    assert record.task.objective == OBJECTIVE
    assert record.task.executor_refs == (AgentRef("bot", BOT_ASSIGNEE),)
    assert record.task.version == 2
    assert record.collet.version == before.collet.version + 2
    assert record.product_instance.root_task_ref == record.task.task_id
    assert (
        record.product_instance.creation_event_ref
        == before.product_instance.creation_event_ref
    )
    assert [e.event_kind for e in record.events[-2:]] == [
        "task.created",
        "task.started",
    ]
    assert record.task.created_event_ref == record.events[-2].event_id
    task_id = record.task.task_id

    return started, task_id


def _agent_run_simulate(service, l4_collet, working_bot, started, tmp_path, task_id):
    collet_id_1 = l4_collet.collet_id

    # 9. 模拟Agent执行任务，过程中上报自定义的步骤及进度信息
    # G3:步骤用 Observation,百分比用 MetricDefinition/MetricSample,结果用 Outcome。
    created = TaskCommissionCreatedView(
        collet_id=l4_collet.collet_id,
        title=OBJECTIVE,
        body_event_id="",
        status="created",
        context_version=l4_collet.version,
    )

    # TODO 上报协议要提供远程服务  (fixed: 上报通过G3提供的可观测数据上报接口进行)
    artifacts = working_bot.run(
        created,
        lambda report_telemetry: _simulate_ai_coding(
            tmp_path=tmp_path,
            collet_id=collet_id_1,
            task_id=task_id,
            service=service,
            working_bot=working_bot,
            report_telemetry=report_telemetry,
        ),
        task_id=task_id,
        initial_revision=started.context_version,
    )
    review = service.detail_task(collet_id_1)
    assert review.status == "reviewing"
    assert review.events[-1]["payload"]["artifacts"] == artifacts

    return artifacts


def _query_task_result(
    l4_context_operate, service, collet_id_1, bot_context, task_id, artifacts
):
    # 10. 用户查看成果并明确 accept,而不是以 Bot 完工代替验收。
    _flow_log(_TEST_DIRECTORY, "6. 用户检查方案、代码、测试、PR 提案,确认 accept")
    accepted = service.accept_task(
        TaskAdvanceCommand(collet_id_1, note="成果符合要求,验收通过"), HUMAN
    )
    assert accepted.status == "done"

    # 11. 经应用门面的 L4 查询读取 Task 状态与产物,不依赖产品详情接口。
    # TODO: 管理时、运行时分开。        （fixed:这里只是一个Collet查询服务，并不是管理服务。)
    final = l4_context_operate.get_status(
        collet_id_1, trunk_events=False, context=HUMAN
    )
    assert final.current_snapshot is not None
    assert final.current_snapshot["status"] == "done"
    assert final.context_version == accepted.context_version
    assert final.current_snapshot["progress"]["percent"] == 100

    final_events = l4_context_operate.get_events(collet_id_1, context=HUMAN)
    assert [event.event_kind for event in final_events] == [
        "collet.participants_bound",
        "task.created",
        "task.started",
        "task.submitted",
        "task.accepted",
    ]

    assert final_events[0].payload["resource_refs"] == [
        ref.to_dict() for ref in INPUT_RESOURCES
    ]
    assert task_id != collet_id_1
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
            status = (
                snapshot.get("status")
                if isinstance(snapshot, dict)
                else snapshot.status
            )
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
            ProductInstantiationServiceProtocol,
            {"instantiate": "L6 记录 ProductInstance 并初始化资源及 Method/Role 绑定"},
        ),
        (
            GroupMethodOpsServiceProtocol,
            {"select": "L5 Single 从已录取参与者中选定唯一执行者"},
        ),
        (
            ContextServiceProtocol,
            {
                "create_collet": "L4 服务只创建 Collet",
                "create_task": "L4 独立创建根 Task 与 task.created 证据",
                "start": "L4 服务经 G1/G4 创建并启动根 Task,不派发 Bot",
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
                "check_append": "G1 校验真实执行者的 Profile 参与范围及事件写入权限",
            },
        ),
        (
            L4ContextCollabRepositoryProtocol[ContextStatusRecord],
            {
                "create_l4_collet": "L4 仓储只持久化 Collet",
                "create_l4_task": "L4 仓储独立创建 Task/Event 并围栏 Collet",
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
                client,
                signed_principal,
                profile,
                bot_id,
                owner_id,
                open_method=injector.get(OpenMethodOpsServiceProtocol),
                telemetry_service=injector.get(TelemetryServiceProtocol),
            ),
            injector,
        )


@pytest.fixture
def commission_deployment(db, monkeypatch):
    """Product staff deploy a code-owned Profile through the real DI composition."""

    def deploy(profile, **identity):
        return _commission_runtime(db, monkeypatch, profile, **identity)

    return deploy


@pytest.fixture
def deploy_commission(commission_deployment):
    """端到端用例专用:测试体内显式创建profile后以赋值方式部署,
    所有部署的teardown由fixture析构时统一执行(含测试失败场景)。"""

    with ExitStack() as stack:

        def deploy(profile, **identity):
            return stack.enter_context(commission_deployment(profile, **identity))

        yield deploy


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
            )
        ),
    ],
    ids=["legacy", "initial"],
)
def commission_runtime(commission_deployment, request):
    # Keep failure behavior for both the legacy and self-application flows.
    with commission_deployment(request.param) as (service, bot, _):
        yield service, bot
