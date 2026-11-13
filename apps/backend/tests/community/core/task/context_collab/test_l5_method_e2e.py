"""L5 method objects E2E:colit with real SQLite repository + L5 with real services sharing an in-memory store.

Following the repository-backed pattern of ``test_l6_product_e2e.py`` (rather than fake-repository-only),
the two test cases run the full authority-plane chain from L6 product form: create_context(Open + Group) → top-level
binding ACTIVE projection → group member operations → open opportunity application and admission (member product assertions) →
nested Swarm plans (assurance-side machine chain) → Single delegated chain (compose NEST/DELEGATE_TO + select) →
inspection conformance_evidence non-empty. Authorization uses the real structural implementation (owner principal),
the store uses the real ``InMemoryMethodStore``, and all state transitions are adjudicated by G4 machines.
The HTTP facet has been covered by adapters' delivery tests (no double-testing).
"""

from datetime import datetime, timedelta, timezone

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    StructuralMethodOpsAuthorizer,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    ResourceRef,
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    MemberState,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    AllocationState,
    StageState,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    GroupMemberCommand,
    MethodStateChangeCommand,
    OpenAdmitCommand,
    OpenApplicationCommand,
    OpenDecisionOpCommand,
    OpenOpportunityCommand,
    SingleDelegateCommand,
    SingleSelectCommand,
    SwarmAllocateCommand,
    SwarmExecutionProfileCommand,
    SwarmPlanCandidateCommand,
    SwarmPlanOpCommand,
    SwarmStageOutcomeCommand,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
    ContextCreateCommand,
)
from agentclaw.community.core.task.context_collab.services.context_collab_service import (
    ContextCollabService,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    MethodLifecycleService,
    current_axes,
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.services.open_method_service import (
    OpenMethodService,
)
from agentclaw.community.core.task.context_collab.services.single_group_method_service import (
    SingleGroupMethodService,
)
from agentclaw.community.core.task.context_collab.services.swarm_method_service import (
    SwarmMethodService,
)
from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

SPACE = SpaceRef(space_id="bbs_community_space", environment="prod")
OWNER = PrincipalRef(principal_type="human", principal_id="u_topic_owner")
HELPER = AgentRef(agent_type="bot", agent_id="bot_help_helper_001")

AXES = current_axes()


def _actor(principal: PrincipalRef) -> ActorContext:
    return ActorContext(actor=principal, source="api")


def _create_command(**overrides) -> ContextCreateCommand:
    fields = {
        "space": SPACE,
        "principal_ref": OWNER,
        "product_kind": "bbs_public_discussion",
        "workflow_kind": "bbs_public_discussion",
        "method_kind": "Open + Group",
        "executor_ref": HELPER,
        "objective": "收集 ACCS 公开讨论协议的社区反馈",
        "resource_refs": [
            ResourceRef(
                resource_type="forum_topic", resource_id="topic_001", revision="3"
            )
        ],
    }
    fields.update(overrides)
    return ContextCreateCommand(**fields)


def _future_iso(hours: float = 1.0) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


class _Services:
    """Collet with real SQLite repository + L5 four-piece real service sharing in-memory store."""

    def __init__(self, db) -> None:
        self.store = InMemoryMethodStore()
        self.repo = ContextCollabRepository(db, product_catalog=PRODUCT_CATALOG)
        self.authorizer = StructuralMethodOpsAuthorizer()
        self.context_service = ContextCollabService(
            repository=self.repo,
            method_store=self.store,
            authorizer=StructuralContextCollabAuthorizer(),
            product_catalog=PRODUCT_CATALOG,
        )
        self.lifecycle = MethodLifecycleService(
            store=self.store,
            authorizer=self.authorizer,
            collab_repo=self.repo,
            product_catalog=PRODUCT_CATALOG,
        )
        self.single_group = SingleGroupMethodService(
            store=self.store,
            authorizer=self.authorizer,
            collab_repo=self.repo,
            lifecycle=self.lifecycle,
        )
        self.swarm = SwarmMethodService(
            store=self.store,
            authorizer=self.authorizer,
            collab_repo=self.repo,
        )
        self.open = OpenMethodService(
            store=self.store,
            authorizer=self.authorizer,
            collab_repo=self.repo,
        )


@pytest.fixture
def services(db):
    return _Services(db)


def _bind_and_activate(
    services: _Services,
    collab_id: str,
    kind: str,
    binding_id: str,
) -> str:
    services.lifecycle.bind(
        collab_id,
        BindMethodCommand(
            method_kind=kind,
            method_binding_id=binding_id,
            parent_scope_ref="task_scope_root",
            bind_reason="compose",
        ),
        _actor(OWNER),
    )
    services.lifecycle.activate(
        collab_id,
        binding_id,
        MethodStateChangeCommand(reason="e2e"),
        _actor(OWNER),
    )
    return binding_id


class TestL5MethodObjectsE2E:
    def test_open_admission_group_product_and_single_chain(self, db, services):
        """Open+Group 顶层 → group 成员 → open 申请/录取(成员产物)→
        Single 委托链(bind/activate/select/delegate)→ 顶层 inspection
        conformance evidence 非空。"""
        created = services.context_service.create_context(
            _create_command(), _actor(OWNER)
        )
        collab_id = created.collab_context_id
        top_binding = top_level_binding_id(collab_id)
        # 顶层 ACTIVE 投影(spec §9.2)。
        top = services.store.get_binding(AXES, top_binding, collab_id)
        assert top is not None
        assert top.state.value == "ACTIVE"
        assert set(top.method_names) == {"Open", "Group"}
        # group:invite + add_member(顶层 Group 面)。
        services.single_group.invite(
            collab_id,
            top_binding,
            GroupMemberCommand(participant_ref="u_forum_1001", reason="e2e"),
            _actor(OWNER),
        )
        services.single_group.add_member(
            collab_id,
            top_binding,
            GroupMemberCommand(participant_ref="u_forum_1001"),
            _actor(OWNER),
        )
        # open:嵌套 Open binding → publish(GROUP_MEMBER 型)→ 公开提交
        # → screen → admit(录取产物 = 本 binding 的 ACTIVE 成员行)。
        open_binding = _bind_and_activate(services, collab_id, "Open", "mb_open_e2e")
        published = services.open.publish_opportunity(
            collab_id,
            open_binding,
            OpenOpportunityCommand(
                scope_ref="task_scope_open_e2e",
                participation_type="GROUP_MEMBER",
                opens_at=_future_iso(-1),
                opportunity_id="opp_e2e",
            ),
            _actor(OWNER),
        )
        assert published.state == "OPEN"
        application = services.open.submit_application(
            collab_id,
            open_binding,
            "opp_e2e",
            OpenApplicationCommand(
                applicant_agent_ref="bot_contrib_001",
                accountable_principal_ref="u_contrib_001",
                proposed_artifact_refs=["artifact://reply"],
            ),
            _actor(PrincipalRef("human", "u_stranger_001")),
        )
        assert application.state == "SUBMITTED"
        services.open.screen(
            collab_id,
            open_binding,
            application.object_id,
            OpenDecisionOpCommand(),
            _actor(OWNER),
        )
        admitted = services.open.admit(
            collab_id,
            open_binding,
            application.object_id,
            OpenAdmitCommand(admitted_scope_ref="scope://contrib"),
            _actor(OWNER),
        )
        assert admitted.state == "ADMITTED"
        products = [
            member
            for member in services.store.list_members(AXES, open_binding, collab_id)
            if member.participant_ref == "bot_contrib_001"
        ]
        assert len(products) == 1
        assert products[0].state is MemberState.ACTIVE
        application_row = services.store.get_application(
            AXES, application.object_id, open_binding, collab_id
        )
        assert application_row.state is ApplicationState.ADMITTED
        # single 委托链:嵌套 Single bind → select → delegate(DELEGATE_TO
        # 边 + 嵌套子 binding)→ 子 binding activate + select。
        single_parent = _bind_and_activate(
            services, collab_id, "Single", "mb_single_e2e"
        )
        selected = services.single_group.select(
            collab_id,
            single_parent,
            SingleSelectCommand(
                agent_participant_ref="bot_sel_e2e_001",
                scope_ref="task_scope_sel_e2e",
                selection_id="sel_e2e",
            ),
            _actor(OWNER),
        )
        assert selected.state == "ACTIVE"
        delegated = services.single_group.delegate(
            collab_id,
            single_parent,
            SingleDelegateCommand(
                method_kind="Single",
                parent_scope_ref="task_scope_sel_e2e",
                child_binding_id="mb_single_child_e2e",
                edge_id="edge_e2e",
            ),
            _actor(OWNER),
        )
        assert delegated.edge["child_binding"] == "mb_single_child_e2e"
        assert delegated.edge["relation_kind"] == "delegate_to"
        assert delegated.edge["parent_binding"] == single_parent
        services.lifecycle.activate(
            collab_id,
            "mb_single_child_e2e",
            MethodStateChangeCommand(reason="e2e"),
            _actor(OWNER),
        )
        child_selected = services.single_group.select(
            collab_id,
            "mb_single_child_e2e",
            SingleSelectCommand(
                agent_participant_ref="bot_sel_child_001",
                scope_ref="task_scope_sel_e2e",
                selection_id="sel_child_e2e",
            ),
            _actor(OWNER),
        )
        assert child_selected.state == "ACTIVE"
        # 顶层 inspection:capabilities + conformance evidence 非空(spec §5)。
        inspection = services.lifecycle.inspect(collab_id, top_binding, _actor(OWNER))
        assert inspection.capabilities
        assert inspection.conformance_evidence

    def test_swarm_authority_plane_chain(self, db, services):
        """嵌套 Swarm binding:declare profile → submit plan → validate →
        adopt → start(前沿 READY)→ allocate(分配即活即跑)→
        complete(stage SUCCEEDED + 分配 RELEASED)→ inspection。"""
        created = services.context_service.create_context(
            _create_command(), _actor(OWNER)
        )
        collab_id = created.collab_context_id
        swarm_binding = _bind_and_activate(services, collab_id, "Swarm", "mb_swarm_e2e")
        # 执行剖面声明。
        profile = services.swarm.declare_execution_profile(
            collab_id,
            swarm_binding,
            SwarmExecutionProfileCommand(
                mode="NATIVE_COMPUTE",
                target_ref="compute://runner/e2e",
                supported_work_kinds=["EXECUTE"],
                input_contract_ref="contract://e2e-in",
                execution_profile_id="sep_e2e",
            ),
            _actor(OWNER),
        )
        assert profile.state == "ACTIVE"
        # 计划提交 + 校验报告 + adopt + start。
        plan = services.swarm.submit_plan_candidate(
            collab_id,
            swarm_binding,
            SwarmPlanCandidateCommand(
                task_ref="task_swarm_e2e",
                swarm_plan_id="sp_e2e",
                max_parallelism=2,
                max_depth=2,
                termination_condition={"all_stages_succeeded": True},
                stages=[
                    {
                        "stage_id": "st_e2e_1",
                        "name": "scan",
                        "dependency_stage_refs": [],
                        "completion_condition": {"found": True},
                    },
                    {
                        "stage_id": "st_e2e_2",
                        "name": "assemble",
                        "dependency_stage_refs": ["st_e2e_1"],
                        "completion_condition": {"reviewed": True},
                    },
                ],
            ),
            _actor(OWNER),
        )
        assert plan.state == "DRAFT"
        report = services.swarm.validate_plan(
            collab_id, swarm_binding, "sp_e2e", _actor(OWNER)
        )
        assert report["valid"] is True
        services.swarm.adopt_plan(
            collab_id, swarm_binding, "sp_e2e", SwarmPlanOpCommand(), _actor(OWNER)
        )
        started = services.swarm.start_plan(
            collab_id, swarm_binding, "sp_e2e", SwarmPlanOpCommand(), _actor(OWNER)
        )
        assert started.state == "RUNNING"
        stage1 = services.store.get_stage(AXES, "st_e2e_1", swarm_binding, collab_id)
        assert stage1.state.value == "READY"  # 前沿 Stage 同步 READY
        # 分配:lease 未过期 + 已声明剖面 → allocation ACTIVE + Stage RUNNING。
        allocated = services.swarm.allocate_stage(
            collab_id,
            swarm_binding,
            SwarmAllocateCommand(
                stage_ref="st_e2e_1",
                target_kind="COMPUTE",
                target_ref="compute://runner/e2e",
                execution_profile_ref="sep_e2e",
                lease_expires_at=_future_iso(1),
                allocation_id="alloc_e2e_1",
            ),
            _actor(OWNER),
        )
        assert allocated.state == "ACTIVE"
        allocation = services.store.get_allocation(
            AXES, "alloc_e2e_1", swarm_binding, collab_id
        )
        assert allocation.state is AllocationState.ACTIVE
        # 完成:Stage SUCCEEDED、分配 RELEASED、provenance record 仍在。
        completed = services.swarm.complete_stage(
            collab_id,
            swarm_binding,
            "st_e2e_1",
            SwarmStageOutcomeCommand(),
            _actor(OWNER),
        )
        assert completed.state == "SUCCEEDED"
        stage1_done = services.store.get_stage(
            AXES, "st_e2e_1", swarm_binding, collab_id
        )
        assert stage1_done.state is StageState.SUCCEEDED
        released = services.store.get_allocation(
            AXES, "alloc_e2e_1", swarm_binding, collab_id
        )
        assert released.state is AllocationState.RELEASED
        records = services.store.list_execution_records(AXES, swarm_binding, collab_id)
        assert records, "provenance 执行记录必须留存"
        # inspection 的 conformance evidence 聚合 L5 对象状态计数。
        inspection = services.lifecycle.inspect(collab_id, swarm_binding, _actor(OWNER))
        assert inspection.conformance_evidence
        # 域外 binding 的 inspection 照常 NotFound(读面越域不误报)。
        with pytest.raises(ContextCollabNotFoundError):
            services.lifecycle.inspect(collab_id, "mb_ghost_binding", _actor(OWNER))
