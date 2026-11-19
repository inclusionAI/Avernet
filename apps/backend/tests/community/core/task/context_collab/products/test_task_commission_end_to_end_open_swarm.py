"""OPEN 广域招募 → 五 Bot 录取 → SWARM 五阶段 → 子 Task / 根 Task 成果验收。

真实 DI、SQLite、L4/L5/G4；本文件显式声明五个 Bot，复用 Swarm 执行替身。
候选 Bot 自己发现/申请，发布人调用本地评审方法按 Profile 所需能力筛选；仅模拟身份与计算，
不验证远端认证、LLM、真实 PR 或持久化调度。产品只声明角色需求；
申请者在运行时才出现，录取后绑定角色，Swarm 在当前有效的录取集合中选择执行者。
"""

from dataclasses import dataclass

import pytest
from injector import AssistedBuilder, Binder, Injector, InstanceProvider

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.execution_report import (
    StageReportProtocol,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.context_service_protocol import (
    ContextServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletCreateCommand,
    EventAppendCommand,
    TaskCreateCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionRelation,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmMethodPolicy,
    OpenMethodPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
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
    OpenOpportunityOpCommand,
    OpenScreenCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l5_methods.publication import (
    OpenAdmissionPolicy,
    OpenPublicationProfile,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver import (
    SwarmDriver,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_driver_contracts import (
    StageExecutionService,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    AllocationState,
    PlanState,
    StageState,
)
from agentclaw.community.core.task.context_collab.l6_products.open_enrollment import (
    AdmittedAgent,
    OpenEnrollmentCommand,
    ProductOpenEnrollmentServiceProtocol,
    scenario_role_ref,
)
from agentclaw.community.core.task.context_collab.l6_products.catalog import (
    ProductCatalog,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodTemplate,
    MethodCompositionTemplate,
    MethodRelationTemplate,
    VersionedProfileRef,
)
from agentclaw.community.core.task.context_collab.l6_products.task_contracts import (
    STANDARD_TASK_CONTRACT,
    resolve_event_contract,
)
from agentclaw.community.core.task.context_collab.l6_products.operations import (
    ProductProfileCreateCommand,
    create_product_profile,
)
from agentclaw.community.core.task.context_collab.l6_products.operations.contracts import (
    ProductInstantiateNewCommand,
    ProductInstantiationServiceProtocol,
)
from agentclaw.community.core.task.context_collab.l6_products.participant_scope import (
    ParticipantScope,
    ParticipantScopeKind,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
)
from agentclaw.community.di.modules.swarm_runtime import SwarmPlanningCapabilities
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.di.modules.task_persistence_module import TaskPersistenceModule
from agentclaw.community.plugin_api.database import DatabasePlugin
from tests.community.core.task.context_collab.products.open_admission_support import (
    LocalCapabilityAdmissionService,
)
from tests.community.core.task.context_collab.products.swarm_coding_support import (
    ARTIFACTS,
    PLAN_SERVICE,
    ROLES,
    LocalCodingPlanService,
)
from tests.community.core.task.context_collab.products.swarm_execution_support import (
    LocalStageExecutionService,
)

OWNER = ActorContext(PrincipalRef("human", "coding_owner"), source="api")
OBJECTIVE = "实现计算器求和并测试后提交 PR 草稿"
# 五阶段协作 Agent：按需求分析 → 方案设计 → 编码 → 测试验证 → PR 提交排列。
AGENTS = (
    AgentRef("bot", "coding_requirements"),  # 需求分析
    AgentRef("bot", "coding_design"),  # 方案设计
    AgentRef("bot", "coding_coding"),  # 编写代码
    AgentRef("bot", "coding_verification"),  # 测试验证
    AgentRef("bot", "coding_pr_submission"),  # 提交 PR 草稿
)
# 同样参与 OPEN 申请、但声明的能力不匹配或为空而被拒绝的候选 Agent。
OTHER_AGENTS = (
    AgentRef("bot", "copywriter"),
    AgentRef("bot", "unqualified_coder"),
)
CANDIDATE_COUNT = len(AGENTS) + len(OTHER_AGENTS)

"""
协议适配问题总结：
- 语义不明
  - [ ] Collet语义明确性问题：这份 ACCS 协议没有给出 Collet 的完整成员变量定义
  - [ ] open模式下，任务执行到底是在录取之后，还是在录取之前，协议没有明确说明，还是说都可以？
  - [ ] open模式下，当录取了某个agent进入协作之后，谁来通知agent真正开始干活，没有明确的语义
  - [ ] 任务如何推进的语义不明确：现在只有Workflow的时候建议通过Advance操作族推进，没有workflow的时候没有说明
- 能力缺少
  - [ ] open模式下，协议中的DataModel只有机会发布、参与申请、决策结果这三个，缺少录取判断策略（谁来做录取判断、怎么判断）
- 问题  
  - [ ] open方法中的参与类型，协议中的枚举不在一个维度：participation_type: COLLET_PARTICIPANT | GROUP_MEMBER | SWARM_ALLOCATION | SINGLE_CONTRIBUTION
"""

def test_task_commission_end_to_end_open_swarm(deploy_open_swarm, tmp_path):
    # 1、基于L6层协议发布一个代办任务 （协作方法基于L5层OPEN + SWARM方法的组合）
    (
        profile,
        injector,
        l4_context_service,
        l5_open_method_operate,
        storage_service,
        storage_envs,
        collet_id,
        open_binding_id,
        swarm_binding_id,
        role_refs,
    ) = publish_todo_task(deploy_open_swarm, tmp_path)

    # 2. 基于L5层OPEN方法公开招募多个Coding相关Agent
    recruit_multi_coding_agents(
        injector,
        l5_open_method_operate,
        storage_service,
        storage_envs,
        profile,
        collet_id,
        role_refs,
        open_binding_id,
    )

    # 3. 招募完成后，仅创建根 Task，进入 Swarm 执行前保持 created。
    l4_context_service.create_task(
        collet_id,
        TaskCreateCommand(
            _record(injector, collet_id).collet.version,
            OWNER.actor,
            objective=OBJECTIVE,
        ),
        OWNER,
    )
    assert _record(injector, collet_id).task.status == "created"

    # 4. 开始协作进行AiCoding（执行L5层SWARM方法：plan-划分stage-指派task-执行）
    stages = swarm_run(injector, collet_id)

    # 5. 任务完成之后，校验每个阶段、每个Task的产物
    judge_artifacts(
        injector, storage_service, storage_envs, stages, collet_id, swarm_binding_id
    )


def publish_todo_task(deploy_open_swarm, tmp_path):
    # 1. L6 声明 OPEN + SWARM 产品
    profile = _create_product_profile()
    assert profile.workflow is None
    assert resolve_event_contract(profile) is STANDARD_TASK_CONTRACT

    # 测试用例DI装配真实协议，可忽略
    injector = deploy_open_swarm(profile, tmp_path)
    l4_context_service = injector.get(ContextServiceProtocol)
    l5_open_method_operate = injector.get(OpenMethodOpsServiceProtocol)
    storage_service, storage_envs = injector.get(MethodStoreProtocol), current_axes()

    # 2. 协议要求Instantiate：创建 ProductInstance，并实例化 Collet、MethodBinding、RoleBinding 与策略引用
    instance = injector.get(ProductInstantiationServiceProtocol).instantiate(
        ProductInstantiateNewCommand(
            collet=ColletCreateCommand(
                initiator_ref=OWNER.actor,
                title="AI coding",
                description="AI coding",
                objective=OBJECTIVE,
                acceptance_criteria="",
                resources=()
            ),
            profile_ref=profile.ref,
        ),
        OWNER,
    )
    collet_id = instance.collet_ref
    open_binding_id, swarm_binding_id = instance.method_binding_refs
    role_refs = {role: scenario_role_ref(profile.ref, role) for role in ROLES}

    return (
        profile,
        injector,
        l4_context_service,
        l5_open_method_operate,
        storage_service,
        storage_envs,
        collet_id,
        open_binding_id,
        swarm_binding_id,
        role_refs
    )


def swarm_run(injector, collet_id):
    swarm_driver = injector.get(AssistedBuilder[SwarmDriver]).build(collet_id=collet_id)

    # 启动swarm driver
    swarm_driver.wake()
    started = _record(injector, collet_id)
    assert started.task.status == "in_progress"

    # 推进一个step
    assert swarm_driver.step()
    assert swarm_driver.step()
    assert swarm_driver.stages()[0].state is StageState.SUCCEEDED
    assert not swarm_driver.is_completed()

    # drain自动推动任务，直到结束
    swarm_driver.drain()

    # swarm任务结束
    assert swarm_driver.is_completed() and swarm_driver.plan().state is PlanState.COMPLETED

    return swarm_driver.stages()


def judge_artifacts(injector, storage_service, storage_envs, stages, collet_id, swarm_binding_id):
    # 11. 第十一步：逐 Stage / 子 Task 校验产物、上报人与验收证据；实际执行者必须都来自 OPEN 录取集合。
    record = _record(injector, collet_id)
    tasks = {task.task_id: task for task in record.tasks}
    for stage, artifact, agent in zip(stages, ARTIFACTS, AGENTS):
        refs = (f"artifact://{artifact}",)
        assert stage.state is StageState.SUCCEEDED
        assert stage.output_artifact_refs == refs
        task = tasks[stage.task_ref]
        assert task.executor_refs == (agent,)
        [submitted] = [
            e
            for e in record.events
            if e.task_ref == task.task_id and e.event_kind == "task.submitted"
        ]
        [accepted] = [
            e
            for e in record.events
            if e.task_ref == task.task_id and e.event_kind == "task.accepted"
        ]
        assert submitted.actor_ref == PrincipalRef(agent.agent_type, agent.agent_id)
        assert (
            accepted.actor_ref == OWNER.actor
            and task.last_event_ref == accepted.event_id
        )
        assert (
            submitted.payload["artifacts"]
            == accepted.payload["artifacts"]
            == list(refs)
        )
    allocations = storage_service.list_allocations(storage_envs, swarm_binding_id, collet_id)
    profiles = storage_service.list_execution_profiles(storage_envs, swarm_binding_id, collet_id)
    assert len(allocations) == len(profiles) == len(ROLES)
    profiles_by_id = {p.execution_profile_id: p for p in profiles}
    allocations_by_stage = {a.stage_ref: a for a in allocations}
    assert set(allocations_by_stage) == {stage.stage_id for stage in stages}
    for stage, agent in zip(stages, AGENTS):
        allocation = allocations_by_stage[stage.stage_id]
        assert allocation.state is AllocationState.RELEASED


def _create_product_profile():
    # Swarm 的 Plan/Stage 决定协作阶段；Task 使用 standard_task@1 + G4，
    # 无需再声明一份产品 workflow 或阶段状态机。
    return create_product_profile(
        ProductProfileCreateCommand(
            ref=VersionedProfileRef("ai_coding_open_swarm", 1),
            composition=MethodCompositionTemplate(
                (
                    MethodTemplate(
                        get_profile("Open", "0.2"),
                        policy=OpenMethodPolicy(
                            OpenPublicationProfile(
                                OpenAdmissionPolicy(
                                    applicant_role="submitter",
                                    accountable_role="owner",
                                    participation_type=ParticipationType.COLLET_PARTICIPANT,
                                    capacity=CANDIDATE_COUNT,
                                    decision_rule="capability_evidence",
                                    required_capabilities=(
                                        "capability://ai-coding/requirements",
                                        "capability://ai-coding/design",
                                        "capability://ai-coding/coding",
                                        "capability://ai-coding/verification",
                                        "capability://ai-coding/pr_submission",
                                    ),
                                )
                            )
                        ),
                    ),
                    MethodTemplate(
                        get_profile("Swarm", "0.2"),
                        policy=SwarmMethodPolicy("common_task_plan"),
                    ),
                ),
                relations=(
                    MethodRelationTemplate(
                        "Swarm", "Open", CompositionRelation.SUPPORT
                    ),
                ),
            ),
            participant_scope=ParticipantScope(ParticipantScopeKind.OPEN),

        )
    )


def recruit_multi_coding_agents(injector, l5_open_method_operate, storage_service, storage_envs,
                                profile, collet_id, role_refs, open_binding_id):
    admission = profile.composition.require_policy("Open").publication.admission

    ## 使用L5的open方法 发布公开机会
    published = l5_open_method_operate.publish_opportunity(
        OpenOpportunityCommand(
            collet_id,
            admission.participation_type,
            requirement_refs=[role_refs[role] for role in ROLES],
            admission_policy_refs=["mock.ai_coding.capability_review/1"],
            capacity=admission.capacity,
        )
    )

    bots = (
        SimulatedApplicantBot(OTHER_AGENTS[0], "coding", ("capability://copywriting",)),
        *(
            SimulatedApplicantBot(agent, role, (f"capability://ai-coding/{role}",))
            for role, agent in zip(ROLES, AGENTS)
        ),
        SimulatedApplicantBot(OTHER_AGENTS[1], "coding", ()),
    )
    for bot in bots:
        assert profile.participant_scope.allows(bot.agent)

        ##每个bot自动发现机会、提交参与申请(使用L5的open方法)
        bot.discover_and_apply(
            l5_open_method_operate, published.object_id, role_refs[bot.role]
        )

    applications = storage_service.list_applications(storage_envs, open_binding_id, collet_id)
    applicants = {bot.agent.agent_id: bot for bot in bots}
    reviewer = LocalCapabilityAdmissionService()
    for application in applications:
        ## 针对所有bot提交的申请，进行录取判断
        screened = l5_open_method_operate.screen(
            OpenScreenCommand(application.application_id, OWNER, collet_id)
        )
        assert screened.accepted and screened.state == "SCREENING"
        if reviewer.should_admit(admission, application):
            ## 能力匹配，录取
            admitted = l5_open_method_operate.admit(
                OpenAdmitCommand(
                    application.application_id,
                    OWNER,
                    collet_id,
                    reason="能力声明命中所需能力列表",
                )
            )
            assert admitted.accepted and admitted.state == "ADMITTED"
            profile.composition.require_policy(
                "Open"
            ).publication.require_dispatch_ready(admitted.state)
        else:
            ## 能力不匹配，拒绝
            rejected = l5_open_method_operate.reject(
                collet_id,
                open_binding_id,
                application.application_id,
                OpenDecisionOpCommand("能力声明未命中所需能力列表"),
                OWNER,
            )
            assert rejected.accepted and rejected.state == "REJECTED"
    assert len(reviewer.calls) == CANDIDATE_COUNT
    assert {a.application_id for _, a in reviewer.calls} == {
        a.application_id for a in applications
    }
    assert all(policy is admission for policy, _ in reviewer.calls)
    applications = storage_service.list_applications(storage_envs, open_binding_id, collet_id)
    for application in applications:
        assert application.transitioned_by == OWNER.actor.principal_id
        assert application.transition_reason == (
            "能力声明命中所需能力列表"
            if application.state is ApplicationState.ADMITTED
            else "能力声明未命中所需能力列表"
        )
    admitted_by_agent = {
        a.applicant_agent_ref: a
        for a in applications
        if a.state is ApplicationState.ADMITTED
    }
    assert set(admitted_by_agent) == {agent.agent_id for agent in AGENTS}
    assert {
               a.applicant_agent_ref
               for a in applications
               if a.state is ApplicationState.REJECTED
           } == {agent.agent_id for agent in OTHER_AGENTS}
    decisions = storage_service.list_decisions(storage_envs, open_binding_id, collet_id)
    scoped = storage_service.list_participant_bindings(storage_envs, open_binding_id, collet_id)
    assert len(decisions) == len(scoped) == len(AGENTS)
    scoped_by_id = {p.participant_binding_id: p for p in scoped}
    for decision in decisions:
        application = next(
            a
            for a in admitted_by_agent.values()
            if a.application_id == decision.application_ref
        )
        [participant_ref] = decision.resulting_binding_refs
        participant = scoped_by_id[participant_ref]
        assert decision.admitted_scope_ref == participant.scope_ref == collet_id
        assert participant.participant_ref == application.applicant_agent_ref
        assert participant.role_binding_refs == application.requested_role_refs
    closed = l5_open_method_operate.close_opportunity(
        collet_id,
        open_binding_id,
        published.object_id,
        OpenOpportunityOpCommand("五阶段所需 Bot 已全部录取"),
        OWNER,
    )
    assert closed.accepted and closed.state == "CLOSED"
    assert _record(injector, collet_id).task is None

    # 录取后才绑定具体 Agent：身份取实际申请者，角色取被审核录取的申请。
    # 根执行者由本案例的协调策略选择已录取的 PR 提交者，不取 AGENTS 的位置。
    [root_application] = [
        application for application in admitted_by_agent.values()
        if role_refs["pr_submission"] in application.requested_role_refs
    ]
    before = _record(injector, collet_id)
    injector.get(ProductOpenEnrollmentServiceProtocol).bind(
        OpenEnrollmentCommand(
            collet_id,
            before.collet.version,
            tuple(
                AdmittedAgent(application.application_id, applicants[agent_id].agent)
                for agent_id, application in admitted_by_agent.items()
            ),
            applicants[root_application.applicant_agent_ref].agent,
        ),
        OWNER,
    )
    bound = _record(injector, collet_id)
    assert bound.task is None  # 绑定不会创建或启动 Task。
    assert bound.product_instance == before.product_instance
    assert {p.agent_ref for p in bound.participants if p.agent_ref is not None} == {
        applicants[agent_id].agent for agent_id in admitted_by_agent
    }
    assert set(ROLES) <= {binding.role for binding in bound.role_bindings}



@pytest.fixture
def deploy_open_swarm(db):
    def deploy(profile, workspace):
        planner = LocalCodingPlanService()
        executor = LocalStageExecutionService(workspace)

        def local(binder: Binder):
            binder.bind(DatabasePlugin, to=db)
            binder.bind(ProductCatalog, to=ProductCatalog((profile,)))
            binder.bind(LocalCodingPlanService, to=InstanceProvider(planner))
            binder.bind(
                SwarmPlanningCapabilities,
                to=SwarmPlanningCapabilities({PLAN_SERVICE: planner}),
            )
            binder.bind(StageExecutionService, to=InstanceProvider(executor))

        injector = Injector([TaskPersistenceModule(), TaskModule(), local])
        executor.reports = injector.get(StageReportProtocol)
        return injector

    return deploy


@dataclass(frozen=True)
class SimulatedApplicantBot:
    agent: AgentRef
    role: str
    capability_declarations: tuple[str, ...]

    def discover_and_apply(self, open_ops, opportunity_id, requested_role_ref):
        # 测试构造可信认证来源；实际入口必须验证 Bot 身份及 owner，不能从用户 JSON 信任这些字段。
        actor = ActorContext(
            PrincipalRef(self.agent.agent_type, self.agent.agent_id),
            source="local_bot_skill",
            accountable_principal_ref=PrincipalRef(
                "human", f"owner_{self.agent.agent_id}"
            ),
        )
        [opportunity] = open_ops.query_opportunities(actor)
        assert opportunity.opportunity_id == opportunity_id
        return open_ops.submit_application(
            OpenApplicationCommand(
                opportunity.opportunity_id,
                actor,
                self.agent.agent_id,
                # 本地模拟以能力引用承载自我声明，不声称验证证据内容或真实能力。
                capability_evidence_refs=list(self.capability_declarations),
                requested_role_refs=[requested_role_ref],
            )
        )


def _record(injector, collet_id):
    return injector.get(ContextCollabRepositoryProtocol).get_current_status(collet_id)


def _append(injector, collet_id, event, actor, **payload):
    record = _record(injector, collet_id)
    return injector.get(ContextServiceProtocol).append_event(
        collet_id,
        EventAppendCommand(
            record.collet.version,
            event,
            "Task",
            actor.actor,
            record.events[-1].event_id,
            "public",
            payload,
        ),
        actor,
    )
