"""Shared fixtures for Swarm Method contract and lifecycle tests."""

from datetime import datetime, timedelta, timezone

from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    StructuralMethodOpsAuthorizer,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l6_products.context_spec import (
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskStatus,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    MethodStateChangeCommand,
    SwarmAllocateCommand,
    SwarmExecutionProfileCommand,
    SwarmPlanCandidateCommand,
    SwarmPlanOpCommand,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    MethodLifecycleService,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    current_axes,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.service import (
    SwarmMethodService,
)

from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

COLLET_ID = "collet_swarm_001"


PRINCIPAL_ID = "u_2001"


EXECUTOR_ID = "bot_swarm_exec_001"


NOW = "2026-11-12T08:00:00+00:00"


OBJECTIVE = "为 BBS 公开协作落 Swarm 剖面计划/Stage/分配语义测试"


AXES = current_axes()


PLAN_MACHINE = METHOD_MACHINE_REGISTRY["swarm_plan"]


STAGE_MACHINE = METHOD_MACHINE_REGISTRY["swarm_stage"]


ALLOCATION_MACHINE = METHOD_MACHINE_REGISTRY["swarm_allocation"]


RECORD_MACHINE = METHOD_MACHINE_REGISTRY["swarm_execution_record"]


def make_actor(principal_id: str = PRINCIPAL_ID) -> ActorContext:
    return ActorContext(actor=PrincipalRef("human", principal_id), source="api")


def _future_iso(hours: float = 1.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _past_iso(hours: float = 1.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def _context_spec(*, method_kind: str = "Open + Group") -> ContextSpec:
    return ContextSpec(
        space=SpaceRef(space_id="space_workspace_001", environment="prod"),
        principal_ref=PrincipalRef("human", PRINCIPAL_ID),
        method_kind=method_kind,
        executor_ref=AgentRef("bot", EXECUTOR_ID),
        objective=OBJECTIVE,
        resource_refs=(),
        product_kind=None,
        workflow_kind=None,
    )


def _status_record(**overrides) -> ContextStatusRecord:
    """构造受控 ``ContextStatusRecord``(与 lifecycle 单测同款)。"""
    fields = {
        "collab_context_id": COLLET_ID,
        "status": TaskStatus.IN_PROGRESS.value,
        "method_kind": "Open + Group",
    }
    fields.update(overrides)
    spec = _context_spec(method_kind=fields["method_kind"])
    principal_ref = PrincipalRef("human", PRINCIPAL_ID)
    executor_ref = AgentRef("bot", EXECUTOR_ID)
    task = Task(
        objective=OBJECTIVE,
        status=fields["status"],
        principal_ref=principal_ref,
        executor_ref=executor_ref,
        task_id="task_" + fields["collab_context_id"],
        collet_ref=fields["collab_context_id"],
        version=1,
        parent_task_ref=None,
        completion_conditions=(),
        created_event_ref="evt_fixture",
        last_event_ref="evt_fixture",
    )
    collet = Collet(
        collet_id=fields["collab_context_id"],
        initiator_ref=principal_ref,
        state=ColletState.ACTIVE,
        scope=spec.space,
        current_event_id=None,
        version=1,
    )
    return ContextStatusRecord(
        collet=collet,
        context_spec=spec,
        tasks=(task,) if task is not None else (),
        task=task,
        events=[],
    )


class _FakeCollabRepository:
    """最小 collet 仓储 fake:按 context 返回受控 ``ContextStatusRecord``。"""

    def list_collets_by_product(self, product_kind: str):
        raise NotImplementedError("fake does not implement product listing")

    def count_message_events(self, collab_context_ids):
        raise NotImplementedError("fake does not implement message counting")

    def __init__(self, records: dict[str, ContextStatusRecord]) -> None:
        self._records = dict(records)

    def get_current_status(self, collab_context_id: str):
        return self._records.get(collab_context_id)

    def create_context(self, request):
        raise NotImplementedError("fake only implements get_current_status")

    def append_event(self, collab_context_id: str, request):
        raise NotImplementedError("fake only implements get_current_status")


def make_swarm_service():
    """真 store + 真 authorizer + 真 lifecycle + 受控 fake 仓储的服务四件套。"""
    store = InMemoryMethodStore()
    repo = _FakeCollabRepository({COLLET_ID: _status_record()})
    lifecycle = MethodLifecycleService(
        store=store,
        authorizer=StructuralMethodOpsAuthorizer(),
        collab_repo=repo,
        product_catalog=PRODUCT_CATALOG,
    )
    service = SwarmMethodService(
        store=store,
        authorizer=StructuralMethodOpsAuthorizer(),
        collab_repo=repo,
    )
    return store, repo, lifecycle, service


def active_swarm_binding(
    lifecycle: MethodLifecycleService,
    binding_id: str = "mb_swarm_alpha",
    collab_context_id: str = COLLET_ID,
) -> str:
    """bind + activate 一条嵌套 Swarm binding(11 操作的前置)。"""
    lifecycle.bind(
        collab_context_id,
        BindMethodCommand(
            method_kind="Swarm",
            method_binding_id=binding_id,
            parent_scope_ref="task_scope_root",
            bind_reason="compose",
        ),
        make_actor(),
    )
    lifecycle.activate(
        collab_context_id, binding_id, MethodStateChangeCommand(), make_actor()
    )
    return binding_id


def profile_command(**overrides) -> SwarmExecutionProfileCommand:
    """执行剖面声明命令基型;字段可覆盖。"""
    fields = {
        "mode": "A2A_AGENT",
        "target_ref": "agent://bot_compute_001",
        "supported_work_kinds": ["PLAN", "EXECUTE"],
        "input_contract_ref": "contract://swarm-input",
        "execution_profile_id": "sep_alpha",
        "output_contract_ref": "contract://swarm-output",
        "lifecycle_mapping": {"stage": "exec"},
    }
    fields.update(overrides)
    return SwarmExecutionProfileCommand(**fields)


def plan_command(**overrides) -> SwarmPlanCandidateCommand:
    """计划候选命令基型:双 Stage 链 st1→st2,结构合法。"""
    fields = {
        "task_ref": "task_swarm_build_001",
        "swarm_plan_id": "sp_alpha",
        "max_parallelism": 2,
        "max_depth": 3,
        "budget_resource_refs": ["res://compute/001"],
        "termination_condition": {"all_stages_succeeded": True},
        "stages": [
            {
                "stage_id": "st1",
                "name": "gather",
                "dependency_stage_refs": [],
                "requirement_refs": ["req://search"],
                "input_artifact_refs": [],
                "output_artifact_roles": ["draft"],
                "completion_condition": {"min_sources": 3},
            },
            {
                "stage_id": "st2",
                "name": "assemble",
                "dependency_stage_refs": ["st1"],
                "requirement_refs": [],
                "input_artifact_refs": ["artifact://draft"],
                "output_artifact_roles": ["report"],
                "completion_condition": {"reviewed": True},
            },
        ],
        "reason": "",
    }
    fields.update(overrides)
    return SwarmPlanCandidateCommand(**fields)


def running_plan_harness(
    binding_id: str = "mb_swarm_alpha",
    plan_id: str = "sp_alpha",
):
    """声明剖面 + 提交 + adopt + start 一路铺到 RUNNING 计划与 READY st1。"""
    store, _, lifecycle, service = make_swarm_service()
    binding = active_swarm_binding(lifecycle, binding_id)
    service.declare_execution_profile(
        COLLET_ID, binding, profile_command(), make_actor()
    )
    service.submit_plan_candidate(
        COLLET_ID, binding, plan_command(swarm_plan_id=plan_id), make_actor()
    )
    service.adopt_plan(COLLET_ID, binding, plan_id, SwarmPlanOpCommand(), make_actor())
    service.start_plan(COLLET_ID, binding, plan_id, SwarmPlanOpCommand(), make_actor())
    return store, lifecycle, service, binding, plan_id


def allocate_command(**overrides) -> SwarmAllocateCommand:
    """分配命令基型;字段可覆盖。"""
    fields = {
        "stage_ref": "st1",
        "target_kind": "AGENT",
        "target_ref": "agent://bot_worker_001",
        "execution_profile_ref": "sep_alpha",
        "lease_expires_at": _future_iso(),
        "allocation_id": "alloc_1",
        "reason": "",
    }
    fields.update(overrides)
    return SwarmAllocateCommand(**fields)


def plan_dict(**overrides) -> dict:
    fields = {
        "swarm_plan_id": "sp_001",
        "method_binding_id": "mb_swarm_001",
        "task_ref": "task_swarm_build_001",
        "max_parallelism": 2,
        "max_depth": 3,
        "budget_resource_refs": ["res://compute/001"],
        "termination_condition": {"all_stages_succeeded": True},
        "state": "DRAFT",
        "transitioned_from": "",
        "transition_reason": "swarm.submit_plan_candidate",
        "transitioned_by": PRINCIPAL_ID,
        "transitioned_at": NOW,
    }
    fields.update(overrides)
    return fields


def stage_dict(**overrides) -> dict:
    fields = {
        "stage_id": "st_001",
        "swarm_plan_ref": "sp_001",
        "task_ref": "task_swarm_build_001",
        "name": "gather",
        "dependency_stage_refs": [],
        "requirement_refs": [],
        "input_artifact_refs": [],
        "output_artifact_roles": ["draft"],
        "completion_condition": {"min_sources": 3},
        "state": "PENDING",
        "transitioned_from": "",
        "transition_reason": "",
        "transitioned_by": PRINCIPAL_ID,
        "transitioned_at": NOW,
    }
    fields.update(overrides)
    return fields


def allocation_dict(**overrides) -> dict:
    fields = {
        "allocation_id": "alloc_001",
        "stage_ref": "st_001",
        "target_kind": "AGENT",
        "target_ref": "agent://bot_worker_001",
        "execution_profile_ref": "sep_001",
        "lease_ref": "lease_alloc_001",
        "lease_expires_at": "2099-01-01T00:00:00+00:00",
        "qualification_evidence_refs": [],
        "authorization_evidence_refs": [],
        "attempt": 1,
        "state": "ACTIVE",
        "transitioned_from": "PROPOSED",
        "transition_reason": "swarm.stage.allocate",
        "transitioned_by": PRINCIPAL_ID,
        "transitioned_at": NOW,
    }
    fields.update(overrides)
    return fields


def record_dict(**overrides) -> dict:
    fields = {
        "execution_record_id": "exr_001",
        "operation_ref": "swarm.submit_plan_candidate:sp_001",
        "stage_allocation_ref": "alloc_001",
        "execution_profile_ref": "sep_001",
        "external_execution_id": "swarm.submit_plan_candidate:sp_001",
        "state": "SUBMITTED",
        "transitioned_from": "",
        "transition_reason": "",
        "transitioned_by": PRINCIPAL_ID,
        "transitioned_at": NOW,
    }
    fields.update(overrides)
    return fields


OWNER_OPS_ALL = (
    "declare_execution_profile",
    "submit_plan_candidate",
    "adopt_plan",
    "start_plan",
    "pause_plan",
    "resume_plan",
    "allocate_stage",
    "complete_stage",
    "fail_stage",
    "terminate_plan",
)
