"""Profile/DI → L6 实例化 → L4 创建 Task → L5 唤醒启动 → 统一 PLAN → Task 验收。

真实 DI/SQLite/L4 角色/L5/G4；规划分配、Agent 计算用本地服务替身。
不调用远程 LLM/A2A，不创建真实 PR；计算成功不自动改变权威状态。
L5 协调器不是持久化调度器，不声称支持重试/重规划或持久化产品 Decision。
"""

from agentclaw.community.core.task.context_collab.l6_products.profile_reader import (
    ProductProfileReader,
)

import json

import pytest
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.execution_report import (
    StageReportProtocol,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
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
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmMethodPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
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
    MethodTemplate,
    CompatibilityRequirements,
    GovernanceRequirements,
    MethodCompositionTemplate,
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
from injector import AssistedBuilder, Binder, Injector, InstanceProvider

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


def test_task_commission_end_to_end_swarm(deploy_swarm, tmp_path):
    # 1. 第一步：L6 定义可复用的 ProductProfile，显式指定统一 plan 接口和五阶段 Agent 角色。
    # Profile 只声明合同；此时不生成 Plan、执行者分配或运行时 execution profile。
    profile = _create_product_profile()

    # 2. 第二步：通过 DI 装配真实协议与本地替身；plan 实现在 L5 外，统一决定下一阶段及执行者。
    injector = deploy_swarm(profile, tmp_path)
    store = injector.get(MethodStoreProtocol)
    service = injector.get(ContextServiceProtocol)
    planner = injector.get(LocalCodingPlanService)
    executor = injector.get(StageExecutionService)
    axes = current_axes()

    # 3. 第三步：L6 Instantiate 内部创建 Collet，绑定产品、Swarm 和五阶段 Agent 角色。
    # 此时只有上下文与绑定证据，尚未创建根 Task，更不会启动或派发。
    instance = injector.get(ProductInstantiationServiceProtocol).instantiate(
        ProductInstantiateNewCommand(
            collet=ColletCreateCommand(OWNER.actor, title="Swarm AI coding"),
            profile_ref=profile.ref,
            participant_refs=AGENTS,
            executor_ref=AGENTS[-1],
        ),
        OWNER,
    )
    collet_id = instance.collet_ref
    [binding_id] = instance.method_binding_refs
    instantiated = _record(injector, collet_id)
    assert instantiated.task is None

    # 4. 第四步：实例化之后通过 L4 显式创建根 Task，明确 coding 目标，但不启动。
    # 沿用已绑定的根成果提交者；各 Stage 的执行者仍由后面的统一 plan 决定。
    root_task = service.create_task(
        collet_id,
        TaskCreateCommand(
            instantiated.collet.version,
            OWNER.actor,
            objective="实现计算器求和并测试后提交 PR 草稿",
        ),
        OWNER,
    )
    record = _record(injector, collet_id)
    assert record.task == root_task
    assert record.task.status == "created"

    # 5. 第五步：只传 collet_id 构造真实 L5 Driver；由 wake 统一启动根 Task 并进入 Swarm 规划。
    driver = injector.get(AssistedBuilder[SwarmDriver]).build(collet_id=collet_id)
    driver.wake()
    assert driver.step()

    # 6. 第六步：L5 应用规划后才生成 execution profile 并派发；需求 Agent 干活后主动走 G4 上报产物。
    # G4/协议推进需求 Stage 及其独立子 Task，而不是把 execute 返回当成完成。
    assert driver.step()
    first = driver.stages()[0]
    assert first.state is StageState.SUCCEEDED

    # 7. 第七步：继续设计、编码、测试、PR 草稿；每次 plan 根据已完成阶段及产物重新选择下一阶段和 Agent。
    # 各 Agent 均主动 G4 上报，直到 plan 声明结束且协议确认整个 Swarm 已完成。
    driver.drain()
    assert driver.is_completed()
    assert driver.plan().state is PlanState.COMPLETED
    assert len(planner.calls) == len(ROLES) + 1
    assert [participant.agent_ref for _, participant, _ in executor.calls] == list(
        AGENTS
    )

    # 8. 第八步：逐阶段验证不同产物、子 Task、实际执行者及上报事件，确认分配释放与运行时合同。
    stages = driver.stages()
    record = _record(injector, collet_id)
    tasks = {t.task_id: t for t in record.tasks}  # 一个根Task + 每个state一个子任务
    assert len(stages) == len(ROLES)
    assert [stage.name for stage in stages] == list(ROLES)
    assert len(tasks) == len(ROLES) + 1  # 任务数量校验
    root_starts = [
        event
        for event in record.events
        if event.task_ref == root_task.task_id and event.event_kind == "task.started"
    ]
    assert len(root_starts) == 1  # 后续阶段内部 wake 不会重复启动根 Task。
    assert root_starts[0].actor_ref == OWNER.actor

    for stage, artifact, agent in zip(stages, ARTIFACTS, AGENTS):
        assert stage.state is StageState.SUCCEEDED
        assert stage.output_artifact_refs == (
            f"artifact://{artifact}",
        )  # 验证每个stage的产物
        assert (tmp_path / artifact).read_text()
        task = tasks[stage.task_ref]
        assert task.status == "done"  # 验证每个task的状态
        assert task.parent_task_ref == record.task.task_id
        assert task.executor_refs == (agent,)
        reports = [
            e
            for e in record.events
            if e.task_ref == stage.task_ref and e.event_kind == "task.submitted"
        ]
        assert len(reports) == 1
        assert reports[0].actor_ref == PrincipalRef(agent.agent_type, agent.agent_id)
        assert reports[0].payload["artifacts"] == [f"artifact://{artifact}"]

    allocations = store.list_allocations(axes, binding_id, collet_id)
    assert len(allocations) == len(ROLES)
    assert all(a.state is AllocationState.RELEASED for a in allocations)
    profiles = store.list_execution_profiles(axes, binding_id, collet_id)
    assert len(profiles) == len(ROLES)
    by_id = {p.execution_profile_id: p for p in profiles}
    assert all(
        by_id[a.execution_profile_ref].target_ref == a.target_ref for a in allocations
    )
    test_report = json.loads((tmp_path / "test-report.json").read_text())
    assert test_report["exit_code"] == 0
    assert "Ran 4 tests" in test_report["output"]
    assert json.loads((tmp_path / "pr-draft.json").read_text())["simulated"] is True


def _create_product_profile():
    # Workflow 只管宏观 Work；Stage DAG 管内部阶段，不复制第二套阶段机。
    return create_product_profile(
        ProductProfileCreateCommand(
            ref=VersionedProfileRef("ai_coding_swarm", 1),
            composition=MethodCompositionTemplate(
                (
                    MethodTemplate(
                        get_profile("Swarm", "0.2"),
                        policy=SwarmMethodPolicy(PLAN_SERVICE),
                    ),
                )
            ),
            participant_scope=ParticipantScope(ParticipantScopeKind.ALLOWLIST, AGENTS),
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
                        "accept", "task.accepted", "Task", ("note",), "responsible_user"
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
def deploy_swarm(db):
    def deploy(profile, workspace):
        planner = LocalCodingPlanService()
        executor = LocalStageExecutionService(workspace)

        def local(binder: Binder):
            binder.bind(DatabasePlugin, to=db)
            binder.bind(ProductProfileReader, to=ProductCatalog((profile,)))
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


def _record(injector, collet_id):
    return injector.get(ContextCollabRepositoryProtocol).get_current_status(collet_id)


def _append(injector, collet_id, event, actor=OWNER, **payload):
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
