"""Shared fixtures for Open Method contract and lifecycle tests."""

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
    ContextSpec,
    CurrentSnapshot,
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskStatus,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    Stage,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    MethodStateChangeCommand,
    OpenApplicationCommand,
    OpenDecisionOpCommand,
    OpenOpportunityCommand,
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
    current_axes,
)
from agentclaw.community.core.task.context_collab.services.open_method_service import (
    OpenMethodService,
)

from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

COLLET_ID = "collet_open_001"


PRINCIPAL_ID = "u_2001"


EXECUTOR_ID = "bot_open_exec_001"


NOW = "2026-11-12T08:00:00+00:00"


OBJECTIVE = "为 BBS 公开协作落 Open 剖面机会/申请/录取语义测试"


AXES = current_axes()


OPPORTUNITY_MACHINE = METHOD_MACHINE_REGISTRY["open_opportunity"]


APPLICATION_MACHINE = METHOD_MACHINE_REGISTRY["open_application"]


def make_actor(principal_id: str = PRINCIPAL_ID) -> ActorContext:
    return ActorContext(actor=PrincipalRef("human", principal_id), source="api")


def _iso_offset(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


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
    snapshot = CurrentSnapshot(
        objective=OBJECTIVE,
        status=fields["status"],
        principal_ref=principal_ref,
        executor_ref=executor_ref,
        method_kind=fields["method_kind"],
        progress=ProgressSnapshot(),
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
        current_snapshot=snapshot,
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


def make_open_service():
    """真 store + 真 authorizer + 真 lifecycle + 受控 fake 仓储服务四件套。"""
    store = InMemoryMethodStore()
    repo = _FakeCollabRepository({COLLET_ID: _status_record()})
    lifecycle = MethodLifecycleService(
        store=store,
        authorizer=StructuralMethodOpsAuthorizer(),
        collab_repo=repo,
        product_catalog=PRODUCT_CATALOG,
    )
    service = OpenMethodService(
        store=store,
        authorizer=StructuralMethodOpsAuthorizer(),
        collab_repo=repo,
    )
    return store, repo, lifecycle, service


def active_open_binding(
    lifecycle: MethodLifecycleService,
    binding_id: str = "mb_open_alpha",
    collab_context_id: str = COLLET_ID,
) -> str:
    """bind + activate 一条嵌套 Open binding(10 操作的前置)。"""
    lifecycle.bind(
        collab_context_id,
        BindMethodCommand(
            method_kind="Open",
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


def opportunity_command(**overrides) -> OpenOpportunityCommand:
    """机会发布命令基型:未来开窗、无穷闭窗、GROUP_MEMBER 型。"""
    fields = {
        "scope_ref": "task_scope_open_001",
        "participation_type": "GROUP_MEMBER",
        "opens_at": _iso_offset(-1),
        "opportunity_id": "opp_alpha",
        "requirement_refs": ["req://search"],
        "admission_policy_refs": ["policy://review"],
        "capacity": None,
        "closes_at": None,
        "reason": "",
    }
    fields.update(overrides)
    return OpenOpportunityCommand(**fields)


def application_command(**overrides) -> OpenApplicationCommand:
    """申请命令基型;字段可覆盖。"""
    fields = {
        "applicant_agent_ref": "bot_worker_001",
        "accountable_principal_ref": "u_member_001",
        "capability_evidence_refs": ["evidence://skill"],
        "proposed_artifact_refs": ["artifact://draft"],
        "requested_role_refs": ["role_binding_py"],
        "application_id": "app_1",
        "reason": "",
    }
    fields.update(overrides)
    return OpenApplicationCommand(**fields)


def published_opportunity(
    service: OpenMethodService,
    lifecycle: MethodLifecycleService,
    binding_id: str = "mb_open_alpha",
    **opportunity_overrides,
) -> str:
    """发布一个开放机会并返回 opportunity_id。"""
    active_open_binding(lifecycle, binding_id)
    service.publish_opportunity(
        COLLET_ID,
        binding_id,
        opportunity_command(**opportunity_overrides),
        make_actor(),
    )
    return "opp_alpha"


def submitted_application(
    service: OpenMethodService,
    opportunity_ref: str = "opp_alpha",
    binding_id: str = "mb_open_alpha",
    **application_overrides,
) -> str:
    """对机会投一条 SUBMITTED 申请并返回 application_id。"""
    service.submit_application(
        COLLET_ID,
        binding_id,
        opportunity_ref,
        application_command(**application_overrides),
        make_actor(),
    )
    return "app_1"


def make_ready_stage(stage_id: str, swarm_plan_ref: str = "sp_fixture") -> Stage:
    """录取测试用的 READY Stage 夹具(直落 store,绕开 swarm 创建面)。"""
    return Stage.from_dict(
        {
            "stage_id": stage_id,
            "swarm_plan_ref": swarm_plan_ref,
            "task_ref": "task_x",
            "name": f"stage-{stage_id}",
            "dependency_stage_refs": [],
            "requirement_refs": [],
            "input_artifact_refs": [],
            "output_artifact_roles": [],
            "completion_condition": {"k": 1},
            "state": "READY",
            "transitioned_from": "",
            "transition_reason": "",
            "transitioned_by": PRINCIPAL_ID,
            "transitioned_at": NOW,
        }
    )


def opportunity_dict(**overrides) -> dict:
    fields = {
        "opportunity_id": "opp_001",
        "method_binding_id": "mb_open_001",
        "scope_ref": "task_scope_open_001",
        "participation_type": "GROUP_MEMBER",
        "requirement_refs": ["req://search"],
        "admission_policy_refs": ["policy://review"],
        "capacity": None,
        "opens_at": "2026-11-12T08:00:00+00:00",
        "closes_at": None,
        "state": "OPEN",
        "transitioned_from": "DRAFT",
        "transition_reason": "open.opportunity.publish",
        "transitioned_by": PRINCIPAL_ID,
        "transitioned_at": NOW,
    }
    fields.update(overrides)
    return fields


def application_dict(**overrides) -> dict:
    fields = {
        "application_id": "app_001",
        "opportunity_ref": "opp_001",
        "applicant_agent_ref": "bot_worker_001",
        "accountable_principal_ref": "u_member_001",
        "capability_evidence_refs": ["evidence://skill"],
        "proposed_artifact_refs": ["artifact://draft"],
        "requested_role_refs": ["role_binding_py"],
        "state": "SUBMITTED",
        "submitted_at": NOW,
        "transitioned_from": "",
        "transition_reason": "open.application.submit",
        "transitioned_by": PRINCIPAL_ID,
        "transitioned_at": NOW,
    }
    fields.update(overrides)
    return fields


def decision_dict(**overrides) -> dict:
    fields = {
        "admission_decision_id": "dec_001",
        "application_ref": "app_001",
        "decision_ref": "open.admit:opp_001:app_001",
        "admitted_scope_ref": "scope://admitted",
        "resulting_binding_refs": ["gm_001"],
    }
    fields.update(overrides)
    return fields


def screening_application(
    service: OpenMethodService,
    lifecycle: MethodLifecycleService,
    binding_id: str = "mb_open_alpha",
    **opportunity_overrides,
) -> tuple[str, str]:
    """发布机会 + 投申请 + 评审,返回 (opportunity_id, application_id)。"""
    opp = published_opportunity(service, lifecycle, binding_id, **opportunity_overrides)
    app = submitted_application(service, opp, binding_id)
    service.screen(COLLET_ID, binding_id, app, OpenDecisionOpCommand(), make_actor())
    return opp, app


OWNER_OPS_ALL = (
    "publish_opportunity",
    "pause_opportunity",
    "reopen_opportunity",
    "close_opportunity",
    "screen",
    "admit",
    "reject",
    "revoke_admission",
)
