"""OPEN 广域招募 → 五 Bot 录取 → SWARM 五阶段 → 子 Task / 根 Task 成果验收。

真实 DI、SQLite、L4/L5/G4；复用 Swarm case 的五个 Bot 和执行替身。
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
    AGENTS,
    LocalStageExecutionService,
)

OWNER = ActorContext(PrincipalRef("human", "coding_owner"), source="api")
OBJECTIVE = "实现计算器求和并测试后提交 PR 草稿"
OTHER_AGENTS = (AgentRef("bot", "copywriter"), AgentRef("bot", "unqualified_coder"))
CANDIDATE_COUNT = len(AGENTS) + len(OTHER_AGENTS)


def test_task_commission_end_to_end_open_swarm(deploy_open_swarm, tmp_path):
    # 1. 第一步：L6 声明 OPEN + SWARM 产品
    profile = _create_product_profile()

    # 2. 第二步：DI 装配真实协议；规划和五阶段执行复用原 Swarm case 的本地替身。
    injector = deploy_open_swarm(profile, tmp_path)
    service = injector.get(ContextServiceProtocol)
    open_ops = injector.get(OpenMethodOpsServiceProtocol)
    store, axes = injector.get(MethodStoreProtocol), current_axes()

    # 3. 第三步：L6 Instantiate 内部创建 Collet，绑定五个工作 Bot 的既有角色；尚无 Task。
    # 当前角色合同要求明确提供 agent_ref；这些角色事实只是预配置，不替代后面的申请/录取证据。
    instance = injector.get(ProductInstantiationServiceProtocol).instantiate(
        ProductInstantiateNewCommand(
            collet=ColletCreateCommand(OWNER.actor, title="Open + Swarm AI coding"),
            profile_ref=profile.ref,
            participant_refs=AGENTS,
            executor_ref=AGENTS[-1],
        ),
        OWNER,
    )
    collet_id = instance.collet_ref
    open_binding_id, swarm_binding_id = instance.method_binding_refs
    assert open_binding_id != swarm_binding_id
    record = _record(injector, collet_id)
    assert record.task is None and record.tasks == ()
    for binding_id, method in ((open_binding_id, "Open"), (swarm_binding_id, "Swarm")):
        binding = store.get_binding(axes, binding_id, collet_id)
        assert binding.state is BindingState.ACTIVE
        assert binding.method_kind == method
        assert binding.method_names == (method,)
    [edge] = store.list_composition_edges(axes, collet_id)
    assert edge.parent_binding == swarm_binding_id
    assert edge.child_binding == open_binding_id
    assert edge.relation_kind is CompositionRelation.SUPPORT
    role_refs = {b.role: role_binding_ref(collet_id, b) for b in record.role_bindings}
    participants = {p.participant_id: p for p in record.participants}
    assert store.list_participant_bindings(axes, open_binding_id, collet_id) == []

    # 4. 第四步：发布一个面向更大候选池的 OPEN 机会，容量覆盖全部申请，而非只允许五个 Bot 报名。
    admission = profile.composition.require_policy("Open").publication.admission
    published = open_ops.publish_opportunity(
        OpenOpportunityCommand(
            collet_id,
            admission.participation_type,
            requirement_refs=[role_refs[role] for role in ROLES],
            admission_policy_refs=["mock.ai_coding.capability_review/1"],
            capacity=admission.capacity,
        )
    )
    assert published.accepted and published.state == "OPEN"
    assert published.method_binding_id == open_binding_id

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
            open_ops, published.object_id, role_refs[bot.role]
        )
        assert submitted.accepted and submitted.state == "SUBMITTED"
    applications = store.list_applications(axes, open_binding_id, collet_id)
    assert len(applications) == CANDIDATE_COUNT > len(AGENTS)
    assert {a.applicant_agent_ref for a in applications} == {
        agent.agent_id for agent in (*AGENTS, *OTHER_AGENTS)
    }
    assert all(a.state is ApplicationState.SUBMITTED for a in applications)
    applicants = {bot.agent.agent_id: bot for bot in bots}
    for application in applications:
        bot = applicants[application.applicant_agent_ref]
        assert application.opportunity_ref == published.object_id
        assert application.transitioned_by == bot.agent.agent_id
        assert application.accountable_principal_ref == f"owner_{bot.agent.agent_id}"
        assert application.capability_evidence_refs == bot.capability_evidence
        assert application.requested_role_refs == (role_refs[bot.role],)
    assert store.list_decisions(axes, open_binding_id, collet_id) == []
    assert store.list_participant_bindings(axes, open_binding_id, collet_id) == []

    # 6. 第六步：发布人按各阶段角色及能力证据评审，拒绝不匹配申请，录取原 Swarm 的全部五个 Bot。
    # 这里是本地产品评审策略；L5 负责状态机、录取决定及 scoped Participant 的真实写入。
    requirements = {
        role_refs[role]: (agent.agent_id, f"evidence://ai-coding/{role}")
        for role, agent in zip(ROLES, AGENTS)
    }
    for application in applications:
        screened = open_ops.screen(
            OpenScreenCommand(application.application_id, OWNER, collet_id)
        )
        assert screened.accepted and screened.state == "SCREENING"
        [requested_role] = application.requested_role_refs
        expected_agent, evidence = requirements[requested_role]
        if (
            application.applicant_agent_ref == expected_agent
            and evidence in application.capability_evidence_refs
        ):
            admitted = open_ops.admit(
                OpenAdmitCommand(application.application_id, OWNER, collet_id)
            )
            assert admitted.accepted and admitted.state == "ADMITTED"
            profile.composition.require_policy(
                "Open"
            ).publication.require_dispatch_ready(admitted.state)
        else:
            rejected = open_ops.reject(
                collet_id,
                open_binding_id,
                application.application_id,
                OpenDecisionOpCommand("阶段角色或能力证据不匹配"),
                OWNER,
            )
            assert rejected.accepted and rejected.state == "REJECTED"
    applications = store.list_applications(axes, open_binding_id, collet_id)
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
    decisions = store.list_decisions(axes, open_binding_id, collet_id)
    scoped = store.list_participant_bindings(axes, open_binding_id, collet_id)
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
    closed = open_ops.close_opportunity(
        collet_id,
        open_binding_id,
        published.object_id,
        OpenOpportunityOpCommand("五阶段所需 Bot 已全部录取"),
        OWNER,
    )
    assert closed.accepted and closed.state == "CLOSED"
    assert _record(injector, collet_id).task is None
    assert store.list_plans(axes, swarm_binding_id, collet_id) == []
    assert store.list_allocations(axes, swarm_binding_id, collet_id) == []
    assert store.list_execution_profiles(axes, swarm_binding_id, collet_id) == []

    # 7. 第七步：实例化、招募完成之后，通过 L4 创建根 Task，但不启动、不直接调用 Work.start。
    root = service.create_task(
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

    # 8. 第八步：只传 collet_id 构造真实 SwarmDriver；wake 启动根 Task，step 调用配置的 plan 服务。
    driver = injector.get(AssistedBuilder[SwarmDriver]).build(collet_id=collet_id)
    assert driver.binding_id == swarm_binding_id
    assert driver.plan() is None and not driver.is_completed()
    driver.wake()
    assert _record(injector, collet_id).task.status == "in_progress"
    assert driver.step()
    assert store.list_execution_profiles(axes, swarm_binding_id, collet_id) == []

    # 9. 第九步：应用统一规划和执行者分配，需求 Bot 执行并主动 G4 上报，推进 Stage 与独立子 Task。
    assert driver.step()
    assert driver.stages()[0].state is StageState.SUCCEEDED
    assert not driver.is_completed()

    # 10. 第十步：继续设计、编码、真实 unittest、模拟 PR；每次 plan 根据已完成阶段选择下一阶段及 Bot。
    driver.drain()
    assert driver.is_completed() and driver.plan().state is PlanState.COMPLETED

    # 11. 第十一步：逐 Stage / 子 Task 校验产物、上报人与验收证据；实际执行者必须都来自 OPEN 录取集合。
    stages = driver.stages()
    record = _record(injector, collet_id)
    tasks = {task.task_id: task for task in record.tasks}
    assert [stage.name for stage in stages] == list(ROLES)
    assert len(tasks) == len(ROLES) + 1
    assert len({stage.task_ref for stage in stages}) == len(ROLES)
    for stage, artifact, agent in zip(stages, ARTIFACTS, AGENTS):
        refs = (f"artifact://{artifact}",)
        assert stage.state is StageState.SUCCEEDED
        assert stage.output_artifact_refs == refs
        assert (tmp_path / artifact).read_text()
        task = tasks[stage.task_ref]
        assert task.status == "done" and task.parent_task_ref == root.task_id
        assert task.executor_refs == (agent,)
        assert admitted_by_agent[agent.agent_id].requested_role_refs == (
            role_refs[stage.name],
        )
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
    allocations = store.list_allocations(axes, swarm_binding_id, collet_id)
    profiles = store.list_execution_profiles(axes, swarm_binding_id, collet_id)
    assert len(allocations) == len(profiles) == len(ROLES)
    profiles_by_id = {p.execution_profile_id: p for p in profiles}
    allocations_by_stage = {a.stage_ref: a for a in allocations}
    assert set(allocations_by_stage) == {stage.stage_id for stage in stages}
    for stage, agent in zip(stages, AGENTS):
        allocation = allocations_by_stage[stage.stage_id]
        assert allocation.state is AllocationState.RELEASED
        participant = participants[allocation.target_ref]
        assert participant.agent_ref == agent
        assert participant.agent_ref.agent_id in admitted_by_agent
        assert (
            profiles_by_id[allocation.execution_profile_ref].target_ref
            == participant.participant_id
        )
    assert store.list_plans(axes, open_binding_id, collet_id) == []
    assert store.list_allocations(axes, open_binding_id, collet_id) == []
    test_report = json.loads((tmp_path / "test-report.json").read_text())
    assert test_report["exit_code"] == 0 and "Ran 4 tests" in test_report["output"]
    assert test_report["agent"] == AGENTS[3].agent_id
    pr_draft = json.loads((tmp_path / "pr-draft.json").read_text())
    assert (
        pr_draft["simulated"] is True and pr_draft["test_report"] == "test-report.json"
    )

    # 12. 第十二步：Swarm 完成不等于根 Task 已验收；PR Bot 显式提交汇总产物，再由用户最终验收。
    assert record.task.status == "in_progress"
    final_artifacts = [f"artifact://{artifact}" for artifact in ARTIFACTS]
    submitter = ActorContext(
        PrincipalRef("bot", AGENTS[-1].agent_id), source="local_agent"
    )
    submitted = _append(
        injector, collet_id, "task.submitted", submitter, artifacts=final_artifacts
    )
    assert submitted.accepted and submitted.current_status == "reviewing"
    accepted = _append(
        injector, collet_id, "task.accepted", OWNER, artifacts=final_artifacts
    )
    assert accepted.accepted and accepted.current_status == "done"

    # 13. 第十三步：从 L4 查询最终状态/事件，验证整个 Swarm、全部 Task 以及根 Task 最终产物。
    final = _record(injector, collet_id)
    status = service.get_status(collet_id, trunk_events=False, context=OWNER)
    events = service.get_events(collet_id, context=OWNER)
    assert status.current_snapshot["status"] == final.task.status == "done"
    assert all(task.status == "done" for task in final.tasks)
    assert driver.is_completed() and not driver.step()
    assert final.task.last_event_ref == accepted.event_id
    for receipt, actor in ((submitted, submitter), (accepted, OWNER)):
        [event] = [e for e in events if e.event_id == receipt.event_id]
        assert event.task_ref == root.task_id and event.actor_ref == actor.actor
        assert event.payload["artifacts"] == final_artifacts
    assert final_artifacts == [
        ref for stage in driver.stages() for ref in stage.output_artifact_refs
    ]
    root_starts = [
        e
        for e in events
        if e.task_ref == root.task_id and e.event_kind == "task.started"
    ]
    assert len(root_starts) == 1 and root_starts[0].actor_ref == OWNER.actor


def _create_product_profile():
    return create_product_profile(
        ProductProfileCreateCommand(
            ref=VersionedProfileRef("ai_coding_open_swarm", 1),
            swarm_plan_service_ref=PLAN_SERVICE,
            composition=MethodCompositionTemplate(
                "Open + Swarm",
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
