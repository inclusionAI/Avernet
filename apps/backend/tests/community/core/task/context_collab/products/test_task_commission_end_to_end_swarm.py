"""多角色 coding: L6 → Single 嵌套 Swarm → 五阶段交接 → Task 验收。

真实 DI/SQLite/L4 角色/L5/G4;仅模拟 Agent 计算与 PR 草稿，不调用远程
LLM/A2A、不创建真实 PR。不声称验证 Activity、Grant 或 MethodStore 重启恢复。
计算成功不改 Stage 状态，协调人验证本地产物后显式 complete_stage。
"""

import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from injector import Binder, Injector

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
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
    WorkStartCommand,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    role_binding_ref,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.participation import (
    initial_participation,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    BindMethodCommand,
    ComposeMethodCommand,
    MethodLifecycleServiceProtocol,
    MethodStateChangeCommand,
    SwarmAllocateCommand,
    SwarmExecutionProfileCommand,
    SwarmMethodOpsServiceProtocol,
    SwarmPlanCandidateCommand,
    SwarmPlanOpCommand,
    SwarmStageOutcomeCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.matching import (
    stage_role_candidates,
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
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.di.modules.task_persistence_module import TaskPersistenceModule
from agentclaw.community.plugin_api.database import DatabasePlugin

OWNER = ActorContext(PrincipalRef("human", "coding_owner"), source="api")
ROLES = ("requirements", "design", "coding", "verification", "pr_submission")
AGENTS = tuple(AgentRef("bot", f"coding_{role}") for role in ROLES)
ARTIFACTS = (
    "requirements.md",
    "design.md",
    "calculator.py",
    "test-report.json",
    "pr-draft.json",
)
BINDING, PLAN = "coding_swarm", "coding_plan"



def test_task_commission_end_to_end_swarm(runtime, tmp_path):
    injector, instance = runtime
    collet_id, axes = instance.collet_ref, current_axes()
    initial = _record(injector, collet_id)
    assert initial.task is None
    facts = initial_participation(initial.events)
    assert {p.agent_ref for p in facts.participants if p.agent_ref} == set(AGENTS)
    assert len(facts.role_bindings) == 7
    assert set(instance.role_binding_refs) == {
        role_binding_ref(collet_id, b) for b in facts.role_bindings
    }
    swarm, store, facts = _start_swarm(injector, instance)
    selected = []
    for expected_role in ROLES:
        # 当前阶段来自权威 READY 状态，不由循环计数器决定。
        ready = [
            s
            for s in store.list_stages(axes, BINDING, collet_id)
            if s.state is StageState.READY
        ]
        assert len(ready) == 1
        stage = ready[0]
        assert stage.name == expected_role
        candidates = stage_role_candidates(collet_id, stage, facts)
        assert len(candidates) == 1
        participant = candidates[0]
        selected.append(participant.agent_ref)
        allocated = swarm.allocate_stage(
            collet_id, BINDING, _command(stage, participant), OWNER
        )
        assert allocated.state == "ACTIVE"
        output = _simulate_agent(stage.name, participant, tmp_path)
        assert (
            store.get_stage(axes, stage.stage_id, BINDING, collet_id).state
            is StageState.RUNNING
        )
        assert output.name == stage.completion_condition["required_artifact"]
        assert output.read_text()
        done = swarm.complete_stage(
            collet_id,
            BINDING,
            stage.stage_id,
            SwarmStageOutcomeCommand(
                [allocated.object_id], reason=f"validated artifact://{output.name}"
            ),
            OWNER,
        )
        assert done.state == "SUCCEEDED"
        assert (
            store.get_allocation(axes, allocated.object_id, BINDING, collet_id).state
            is AllocationState.RELEASED
        )
        _append(
            injector,
            collet_id,
            "task.progress.updated",
            note=f"{stage.name}: artifact://{output.name}",
            percent=len(selected) * 20,
        )
    assert selected == list(AGENTS)
    assert all(
        s.state is StageState.SUCCEEDED
        for s in store.list_stages(axes, BINDING, collet_id)
    )
    # P1 暂无 complete_plan 操作，不能直接改 store 假装 COMPLETED。
    assert store.get_plan(axes, PLAN, BINDING, collet_id).state is PlanState.RUNNING
    submitter = ActorContext(
        PrincipalRef("bot", AGENTS[-1].agent_id), source="local_agent"
    )
    assert (
        _append(
            injector,
            collet_id,
            "task.submitted",
            submitter,
            artifacts=[f"artifact://{name}" for name in ARTIFACTS],
        ).current_status
        == "reviewing"
    )
    assert (
        _append(
            injector,
            collet_id,
            "task.accepted",
            note="验收代码、真实单测报告与模拟 PR 草稿",
        ).current_status
        == "done"
    )
    # 新仓储重读 SQL；MethodStore 仍为进程内，不声称支持重启恢复。
    repo = injector.get(ContextCollabRepositoryProtocol).__class__(
        injector.get(DatabasePlugin), injector.get(ProductCatalog)
    )
    reread = repo.get_current_status(collet_id)
    assert reread.task.status == "done"
    assert reread.product_instance.root_task_ref == reread.task.task_id
    assert initial_participation(reread.events) == facts


def _profile():
    # Workflow 只管宏观 Work；Stage DAG 管内部阶段，不复制第二套阶段机。
    return create_product_profile(
        ProductProfileCreateCommand(
            ref=VersionedProfileRef("ai_coding_swarm", 1),
            # 现有顶层不支持裸 Swarm，使用真实 nest，而非发明新的 wire 枚举。
            composition=MethodCompositionTemplate(
                "Single", (get_profile("Single", "0.2"),)
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
def runtime(db):
    profile = _profile()

    def local(binder: Binder):
        binder.bind(DatabasePlugin, to=db)
        binder.bind(ProductCatalog, to=ProductCatalog((profile,)))

    injector = Injector([TaskPersistenceModule(), TaskModule(), local])
    instance = injector.get(ProductInstantiationServiceProtocol).instantiate(
        ProductInstantiateNewCommand(
            collet=ColletCreateCommand(OWNER.actor, title="Swarm AI coding"),
            profile_ref=profile.ref,
            participant_refs=AGENTS,
            executor_ref=AGENTS[-1],
        ),
        OWNER,
    )
    return injector, instance


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


def _start_swarm(injector, instance):
    collet_id = instance.collet_ref
    record = _record(injector, collet_id)
    injector.get(ContextServiceProtocol).start(
        collet_id,
        WorkStartCommand(
            record.collet.version, objective="实现计算器求和并测试后提交 PR 草稿"
        ),
        OWNER,
    )
    record = _record(injector, collet_id)
    lifecycle = injector.get(MethodLifecycleServiceProtocol)
    lifecycle.bind(
        collet_id,
        BindMethodCommand("Swarm", BINDING, parent_scope_ref=record.task.task_id),
        OWNER,
    )
    lifecycle.activate(collet_id, BINDING, MethodStateChangeCommand(), OWNER)
    edge = lifecycle.compose(
        collet_id,
        instance.method_binding_refs[0],
        ComposeMethodCommand("nest", BINDING, "Swarm"),
        OWNER,
    )
    assert edge.edge["relation_kind"] == "nest"
    facts = initial_participation(record.events)
    refs = {b.role: role_binding_ref(collet_id, b) for b in facts.role_bindings}
    swarm = injector.get(SwarmMethodOpsServiceProtocol)
    for role in ROLES:
        participant = next(
            p
            for p in facts.participants
            if any(
                b.role == role and b.participant_id == p.participant_id
                for b in facts.role_bindings
            )
        )
        swarm.declare_execution_profile(
            collet_id,
            BINDING,
            SwarmExecutionProfileCommand(
                mode="A2A_AGENT",
                target_ref=participant.participant_id,
                supported_work_kinds=["EXECUTE"],
                input_contract_ref="coding-input/1",
                output_contract_ref="coding-output/1",
                execution_profile_id=f"execution_{role}",
            ),
            OWNER,
        )
    swarm.submit_plan_candidate(
        collet_id,
        BINDING,
        SwarmPlanCandidateCommand(
            task_ref=record.task.task_id,
            swarm_plan_id=PLAN,
            max_parallelism=1,
            max_depth=5,
            budget_resource_refs=["resource://local-coding-budget"],
            termination_condition={"all_stages_succeeded": True},
            stages=[
                {
                    "stage_id": role,
                    "name": role,
                    "dependency_stage_refs": [] if i == 0 else [ROLES[i - 1]],
                    "requirement_refs": [refs[role]],
                    "input_artifact_refs": []
                    if i == 0
                    else [f"artifact://{ARTIFACTS[i - 1]}"],
                    "output_artifact_roles": [ARTIFACTS[i]],
                    "completion_condition": {"required_artifact": ARTIFACTS[i]},
                }
                for i, role in enumerate(ROLES)
            ],
        ),
        OWNER,
    )
    assert swarm.validate_plan(collet_id, BINDING, PLAN, OWNER)["valid"]
    store = injector.get(MethodStoreProtocol)
    assert all(
        s.state is StageState.PENDING
        for s in store.list_stages(current_axes(), BINDING, collet_id)
    )
    swarm.adopt_plan(collet_id, BINDING, PLAN, SwarmPlanOpCommand(), OWNER)
    swarm.start_plan(collet_id, BINDING, PLAN, SwarmPlanOpCommand(), OWNER)
    return swarm, store, facts


def _command(stage, participant):
    return SwarmAllocateCommand(
        stage_ref=stage.stage_id,
        target_kind="AGENT",
        target_ref=participant.participant_id,
        execution_profile_ref=f"execution_{stage.name}",
        lease_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        allocation_id=f"allocation_{stage.stage_id}",
        qualification_evidence_refs=list(stage.requirement_refs),
    )


def _simulate_agent(role, participant, workspace):
    """唯一计算面模拟；显式读前序产物，测试 Agent 真正运行 unittest。"""
    index = ROLES.index(role)
    if index:
        assert (workspace / ARTIFACTS[index - 1]).read_text()
    if role == "requirements":
        text = "sum_numbers(values): 空列表为 0，支持负数与浮点数，不修改输入。\n"
    elif role == "design":
        assert "空列表" in (workspace / "requirements.md").read_text()
        text = "采用 Python sum(values)，使用 unittest 验证边界，无外部依赖。\n"
    elif role == "coding":
        assert "unittest" in (workspace / "design.md").read_text()
        text = "def sum_numbers(values):\n    return sum(values)\n"
    elif role == "verification":
        (workspace / "test_calculator.py").write_text(
            "import unittest\nfrom calculator import sum_numbers\n"
            "class CalculatorTest(unittest.TestCase):\n"
            "    def test_empty(self): self.assertEqual(sum_numbers([]), 0)\n"
            "    def test_values(self): self.assertEqual(sum_numbers([1, 2, 3]), 6)\n"
            "    def test_negative_float(self): self.assertEqual(sum_numbers([-2, 0.5]), -1.5)\n"
            "    def test_unchanged(self):\n"
            "        values = [1, 2]\n        sum_numbers(values)\n        self.assertEqual(values, [1, 2])\n"
        )
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "-v", "test_calculator"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        text = json.dumps(
            {
                "exit_code": result.returncode,
                "output": result.stderr,
                "agent": participant.agent_ref.agent_id,
            }
        )
    else:
        report = json.loads((workspace / "test-report.json").read_text())
        assert report["exit_code"] == 0
        assert "Ran 4 tests" in report["output"]
        text = json.dumps(
            {
                "simulated": True,
                "title": "Add calculator sum",
                "files": ["calculator.py", "test_calculator.py"],
                "test_report": "test-report.json",
            }
        )
    output = workspace / ARTIFACTS[index]
    output.write_text(text)
    return output
