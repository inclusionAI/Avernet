"""OPEN 广域招募 → 五 Bot 录取 → SWARM 五阶段 → 子 Task / 根 Task 成果验收。

真实 DI、SQLite、L4/L5/G4；本文件显式声明五个 Bot，复用 Swarm 执行替身。
候选 Bot 自己发现/申请，发布人按本地能力证据筛选；仅模拟身份与计算，
不验证远端认证、LLM、真实 PR 或持久化调度。角色预绑定不等于 OPEN 录取；
本 case 显式编排录取后启动，不声称通用 SwarmDriver 会自动执行 OPEN 准入检查。
"""

import json
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
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    role_binding_ref,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionRelation,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
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
    WorkflowAction,
    WorkflowProfile,
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
    AgentRef("bot", "coding_requirements"),   # 需求分析
    AgentRef("bot", "coding_design"),         # 方案设计
    AgentRef("bot", "coding_coding"),         # 编写代码
    AgentRef("bot", "coding_verification"),   # 测试验证
    AgentRef("bot", "coding_pr_submission"),  # 提交 PR 草稿
)
# 同样参与 OPEN 申请、但因角色或能力证据不匹配而被拒绝的候选 Agent。
OTHER_AGENTS = (
    AgentRef("bot", "copywriter"),
    AgentRef("bot", "unqualified_coder"),
)
CANDIDATE_COUNT = len(AGENTS) + len(OTHER_AGENTS)


def test_task_commission_end_to_end_open_swarm(deploy_open_swarm, tmp_path):
    # 1. L6 声明 OPEN + SWARM 产品
    profile = _create_product_profile()

    # 测试用例DI装配真实协议，可忽略
    injector = deploy_open_swarm(profile, tmp_path)
    l4_context_service = injector.get(ContextServiceProtocol)
    l5_open_method_operate = injector.get(OpenMethodOpsServiceProtocol)
    storage_service, storage_envs = injector.get(MethodStoreProtocol), current_axes()

    # 2. 协议要求Instantiate：创建 ProductInstance，并实例化 Collet、MethodBinding、RoleBinding 与策略引用
    instance = injector.get(ProductInstantiationServiceProtocol).instantiate(
        ProductInstantiateNewCommand(
            collet=ColletCreateCommand(OWNER.actor, title="AI coding", description=OBJECTIVE),
            profile_ref=profile.ref,
            participant_refs=AGENTS,
            executor_ref=AGENTS[-1],
        ),
        OWNER,
    )
    collet_id = instance.collet_ref
    open_binding_id, swarm_binding_id = instance.method_binding_refs
    record = _record(injector, collet_id)
    role_refs = {b.role: role_binding_ref(collet_id, b) for b in record.role_bindings}
    participants = {p.participant_id: p for p in record.participants}

    # 3. 公开招募多个Coding Agents
    recruit_multi_coding_agents(injector, l5_open_method_operate, storage_service, storage_envs, profile, collet_id, role_refs, open_binding_id, swarm_binding_id)

    # 4. 实例化、招募完成之后，通过 L4 创建根 Task，但不启动
    root = l4_context_service.create_task(
        collet_id,
        TaskCreateCommand(
            _record(injector, collet_id).collet.version,
            OWNER.actor,
            objective=OBJECTIVE,
        ),
        OWNER,
    )
    assert root.status == "created" and root.parent_task_ref is None
    assert root.executor_refs == (AGENTS[-1],)

    # 5. 招募到agents之后，执行swarm方法：规划-划分stage和task-分派执行
    stages = swarm_run(injector, collet_id)

    # 6. 校验产物
    judge_artifacts(injector, storage_service, storage_envs, stages, collet_id, swarm_binding_id)


def swarm_run(injector, collet_id):
    # 8. 第八步：只传 collet_id 构造真实 SwarmDriver；wake 启动根 Task，step 调用配置的 plan 服务。
    driver = injector.get(AssistedBuilder[SwarmDriver]).build(collet_id=collet_id)
    driver.wake()
    assert driver.step()

    # 9. 第九步：应用统一规划和执行者分配，需求 Bot 执行并主动 G4 上报，推进 Stage 与独立子 Task。
    assert driver.step()
    assert driver.stages()[0].state is StageState.SUCCEEDED
    assert not driver.is_completed()

    # 10. 第十步：继续设计、编码、真实 unittest、模拟 PR；每次 plan 根据已完成阶段选择下一阶段及 Bot。
    driver.drain()
    assert driver.is_completed() and driver.plan().state is PlanState.COMPLETED

    return driver.stages()


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
    return create_product_profile(
        ProductProfileCreateCommand(
            ref=VersionedProfileRef("ai_coding_open_swarm", 1),
            swarm_plan_service_ref=PLAN_SERVICE,
            composition=MethodCompositionTemplate(
                (get_profile("Open", "0.2"), get_profile("Swarm", "0.2")),
                relations=(
                    MethodRelationTemplate(
                        "Swarm", "Open", CompositionRelation.SUPPORT
                    ),
                ),
                policies=(
                    OpenMethodPolicy(
                        OpenPublicationProfile(
                            OpenAdmissionPolicy(
                                applicant_role="submitter",
                                accountable_role="owner",
                                participation_type=ParticipationType.COLLET_PARTICIPANT,
                                capacity=CANDIDATE_COUNT,
                                decision_actor="accountable_role",
                                decision_rule="capability_evidence",
                            )
                        )
                    ),
                ),
            ),
            participant_scope=ParticipantScope(ParticipantScopeKind.OPEN),
            scenario_roles=(
                ScenarioRole(
                    "owner",
                    "responsible_user",
                    "principal_ref",
                    True,
                    ("coordinate", "accept"),
                ),
                ScenarioRole(
                    "submitter",
                    "selected-agent",
                    "executor_ref",
                    True,
                    ("submit_result",),
                ),
                *(
                    ScenarioRole(role, role, "agent_ref", True, (role,), agent)
                    for role, agent in zip(ROLES, AGENTS)
                ),
            ),
            workflow=WorkflowProfile(
                ref=VersionedProfileRef("ai_coding_delivery", 1),
                state_machine_ref=VersionedProfileRef("task_commission_goal", 1),
                completion_states=("done",),
                compensation_events=("task.rejected", "task.cancelled"),
                strict_control_events=True,
                public_events_only=True,
                actions=(
                    WorkflowAction(
                        "start", "task.started", "Task", ("note",), "responsible_user"
                    ),
                    WorkflowAction(
                        "progress",
                        "task.progress.updated",
                        "Task",
                        ("note", "percent"),
                        "responsible_user",
                    ),
                    WorkflowAction(
                        "submit",
                        "task.submitted",
                        "Task",
                        ("note", "artifacts"),
                        "pr_submission",
                    ),
                    WorkflowAction(
                        "accept",
                        "task.accepted",
                        "Task",
                        ("note", "artifacts"),
                        "responsible_user",
                    ),
                ),
            ),
            governance=GovernanceRequirements(
                "g1.structural/1",
                "g2.optimistic-version/1",
                "g3.event-projection/1",
                "g4.product-contract/1",
            ),
            compatibility=CompatibilityRequirements(
                "0.2",
                ("L2", "L3", "L4", "L5"),
                (),
                (),
                "reject_missing_required",
                "new_instances_only",
            ),
        )
    )


def recruit_multi_coding_agents(injector, l5_open_method_operate, storage_service, storage_envs,
                                profile, collet_id, role_refs, open_binding_id, swarm_binding_id):
    # 4. 第四步：发布一个面向更大候选池的 OPEN 机会，容量覆盖全部申请，而非只允许五个 Bot 报名。
    admission = profile.composition.require_policy("Open").publication.admission
    published = l5_open_method_operate.publish_opportunity(
        OpenOpportunityCommand(
            collet_id,
            admission.participation_type,
            requirement_refs=[role_refs[role] for role in ROLES],
            admission_policy_refs=["mock.ai_coding.capability_review/1"],
            capacity=admission.capacity,
        )
    )

    # 5. 第五步：七个 Bot 分别以自身身份发现机会并申请；其中两个不是 Swarm 所需的工作 Bot。
    # 不合格 Bot 也真正申请编码角色，不能仅用更大的 allowlist 冒充更大范围的招募。
    bots = (
        SimulatedApplicantBot(OTHER_AGENTS[0], "coding", ("evidence://copywriting",)),
        *(
            SimulatedApplicantBot(agent, role, (f"evidence://ai-coding/{role}",))
            for role, agent in zip(ROLES, AGENTS)
        ),
        SimulatedApplicantBot(OTHER_AGENTS[1], "coding", ()),
    )
    for bot in bots:
        assert profile.participant_scope.allows(bot.agent)
        submitted = bot.discover_and_apply(
            l5_open_method_operate, published.object_id, role_refs[bot.role]
        )
        assert submitted.accepted and submitted.state == "SUBMITTED"
    applications = storage_service.list_applications(storage_envs, open_binding_id, collet_id)
    assert all(a.state is ApplicationState.SUBMITTED for a in applications)
    applicants = {bot.agent.agent_id: bot for bot in bots}
    for application in applications:
        bot = applicants[application.applicant_agent_ref]

    # 6. 第六步：发布人按各阶段角色及能力证据评审，拒绝不匹配申请，录取原 Swarm 的全部五个 Bot。
    # 这里是本地产品评审策略；L5 负责状态机、录取决定及 scoped Participant 的真实写入。
    requirements = {
        role_refs[role]: (agent.agent_id, f"evidence://ai-coding/{role}")
        for role, agent in zip(ROLES, AGENTS)
    }
    for application in applications:
        screened = l5_open_method_operate.screen(
            OpenScreenCommand(application.application_id, OWNER, collet_id)
        )
        assert screened.accepted and screened.state == "SCREENING"
        [requested_role] = application.requested_role_refs
        expected_agent, evidence = requirements[requested_role]
        if (
                application.applicant_agent_ref == expected_agent
                and evidence in application.capability_evidence_refs
        ):
            admitted = l5_open_method_operate.admit(
                OpenAdmitCommand(application.application_id, OWNER, collet_id)
            )
            assert admitted.accepted and admitted.state == "ADMITTED"
            profile.composition.require_policy(
                "Open"
            ).publication.require_dispatch_ready(admitted.state)
        else:
            rejected = l5_open_method_operate.reject(
                collet_id,
                open_binding_id,
                application.application_id,
                OpenDecisionOpCommand("阶段角色或能力证据不匹配"),
                OWNER,
            )
            assert rejected.accepted and rejected.state == "REJECTED"
    applications = storage_service.list_applications(storage_envs, open_binding_id, collet_id)
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
    assert storage_service.list_plans(storage_envs, swarm_binding_id, collet_id) == []
    assert storage_service.list_allocations(storage_envs, swarm_binding_id, collet_id) == []
    assert storage_service.list_execution_profiles(storage_envs, swarm_binding_id, collet_id) == []

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
    capability_evidence: tuple[str, ...]

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
                capability_evidence_refs=list(self.capability_evidence),
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
