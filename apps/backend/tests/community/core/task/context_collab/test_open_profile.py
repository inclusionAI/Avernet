"""Task 12:Open 方法剖面(三值对象 + 10 操作)单测。

覆盖(spec §4.4):
- 值对象:round-trip / 状态 wire 闭合 / participation_type 闭合词汇 /
  未知字段拒绝 / capacity 正整数或 null / 审计与审计外字段纪律
  (AdmissionDecision 无状态列、无审计四字段)/ G4 机器词汇对齐
  (open_opportunity / open_application 两枚举);
- store 切片:opportunity/application/decision 幂等插入、同键异形态
  拒绝、binding/context 域隔离、update_*_state 缺键 NotFound、
  同 (opportunity, applicant) 非终态唯一断言(终态后可再投);
- 机会面:publish(窗口形态校验 + COLLET_PARTICIPANT 发布面拒绝)、
  ReopenOpportunity 可达性 pin、CloseOpportunity 终态、幂等键
  opportunity_id 重放 + 异形态拒绝;
- 申请面:submit_application 公开面(任意已认证 actor)、机会必须
  OPEN/窗口内/容量未满(capacity 计 SUBMITTED+SCREENING+ADMITTED)、
  幂等键 application_id、同申请人非终态重复拒、终态后可再投;
- screen/reject(SUBMITTED/SCREENING 双规则)/withdraw(责任主体本人
  self-help;机器仅 SUBMITTED 可退);
- admit 十操作矩阵:GROUP_MEMBER → ACTIVE 成员行(roster 惰性建)、
  SWARM_ALLOCATION → target_stage_ref 必填 + READY/RUNNING 域内校验
  + ACTIVE 语义分配、SINGLE_CONTRIBUTION → 仅记 artifact refs、
  COLLET_PARTICIPANT → 拒;decision 幂等重放产物不重建;
- revoke_admission:成员 → REMOVED、分配 → REVOKED、
  SINGLE_CONTRIBUTION 无产物可终结;
- RF#2 + 结构 Open binding 守卫 + 授权矩阵(G1/T6:8 owner / 1 公开
  / 1 self-help);G1 断言:录取不产生新 binding/selection/composition
  edge(admitted ≠ Space Membership 的 service 面投影);
- 协议:``OpenMethodService`` 整体满足
  ``OpenMethodOpsServiceProtocol`` 的 runtime_checkable isinstance 面。

collet 仓储用手写 fake,store/授权器/lifecycle 用真实现。
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    OPEN_OWNER_OPS,
    OPEN_PUBLIC_OPS,
    OPEN_SELF_HELP_OPS,
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
    ContextSpec,
    CurrentSnapshot,
    ProgressSnapshot,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskStatus,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    MemberState,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    AdmissionDecision,
    ApplicationState,
    OpenOpportunity,
    OpportunityState,
    ParticipationApplication,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    AllocationState,
    Stage,
    StageState,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    MethodStateChangeCommand,
    OpenAdmitCommand,
    OpenApplicationCommand,
    OpenDecisionOpCommand,
    OpenMethodOpsServiceProtocol,
    OpenOpportunityCommand,
    OpenOpportunityOpCommand,
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
    return (
        datetime.now(timezone.utc) + timedelta(hours=hours)
    ).isoformat()


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
    collet = Collet(
        collab_context_id=fields["collab_context_id"],
        actor_ref=None,
        context_spec=spec,
        current_snapshot=CurrentSnapshot(
            objective=OBJECTIVE,
            status=fields["status"],
            principal_ref=principal_ref,
            executor_ref=executor_ref,
            method_kind=fields["method_kind"],
            progress=ProgressSnapshot(),
        ),
        current_event_id=None,
        context_version=1,
    )
    return ContextStatusRecord(collet=collet, events=[])


class _FakeCollabRepository:
    """最小 collet 仓储 fake:按 context 返回受控 ``ContextStatusRecord``。"""

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
        COLLET_ID, binding_id, opportunity_command(**opportunity_overrides),
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
        COLLET_ID, binding_id, opportunity_ref,
        application_command(**application_overrides), make_actor(),
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


# ---------------------------------------------------------------------------
# 值对象(spec §4.4 YAML + §6.3 审计四字段)
# ---------------------------------------------------------------------------


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


class TestOpenValueObjects:
    def test_round_trips(self):
        for build, owner in (
            (opportunity_dict, OpenOpportunity),
            (application_dict, ParticipationApplication),
            (decision_dict, AdmissionDecision),
        ):
            obj = owner.from_dict(build())
            assert owner.from_dict(obj.to_dict()) == obj

    def test_state_wire_validation(self):
        assert (
            OpportunityState.from_wire("PAUSED") is OpportunityState.PAUSED
        )
        assert (
            ApplicationState.from_wire("WITHDRAWN")
            is ApplicationState.WITHDRAWN
        )
        for enum_cls in (OpportunityState, ApplicationState):
            with pytest.raises(
                ContextCollabValidationError, match="wire"
            ):
                enum_cls.from_wire("BOUND")

    def test_state_vocabularies_match_registered_machines(self):
        """防漂移:两个状态枚举与 G4 注册机器逐字一致。"""
        for enum_cls, machine in (
            (OpportunityState, OPPORTUNITY_MACHINE),
            (ApplicationState, APPLICATION_MACHINE),
        ):
            assert {s.value for s in enum_cls} == {
                s.name for s in machine.states
            }

    def test_participation_type_closed_vocab(self):
        assert (
            ParticipationType.from_wire("SWARM_ALLOCATION")
            is ParticipationType.SWARM_ALLOCATION
        )
        with pytest.raises(
            ContextCollabValidationError, match="participation type"
        ):
            ParticipationType.from_wire("TEAM_MEMBER")

    def test_unknown_field_rejected(self):
        for build, owner in (
            (opportunity_dict, OpenOpportunity),
            (application_dict, ParticipationApplication),
            (decision_dict, AdmissionDecision),
        ):
            with pytest.raises(
                ContextCollabValidationError, match="unknown field"
            ):
                owner.from_dict({**build(), "extra": 1})

    def test_required_fields_non_empty(self):
        for field in ("opportunity_id", "method_binding_id", "scope_ref",
                      "opens_at"):
            with pytest.raises(ContextCollabValidationError):
                OpenOpportunity.from_dict(opportunity_dict(**{field: ""}))
        for field in ("application_id", "opportunity_ref",
                      "applicant_agent_ref", "accountable_principal_ref",
                      "submitted_at"):
            with pytest.raises(ContextCollabValidationError):
                ParticipationApplication.from_dict(
                    application_dict(**{field: ""})
                )
        for field in ("admission_decision_id", "application_ref",
                      "decision_ref"):
            with pytest.raises(ContextCollabValidationError):
                AdmissionDecision.from_dict(decision_dict(**{field: ""}))

    def test_capacity_positive_int_or_none(self):
        assert OpenOpportunity.from_dict(opportunity_dict()).capacity is None
        assert (
            OpenOpportunity.from_dict(opportunity_dict(capacity=5)).capacity
            == 5
        )
        for bad in (0, -1, "3", 3.5, True):
            with pytest.raises(ContextCollabValidationError, match="capacity"):
                OpenOpportunity.from_dict(opportunity_dict(capacity=bad))

    def test_optional_and_ref_fields(self):
        assert (
            OpenOpportunity.from_dict(
                opportunity_dict(closes_at="2099-01-01T00:00:00+00:00")
            ).closes_at
            == "2099-01-01T00:00:00+00:00"
        )
        with pytest.raises(ContextCollabValidationError, match="closes_at"):
            OpenOpportunity.from_dict(opportunity_dict(closes_at=""))
        with pytest.raises(
            ContextCollabValidationError, match="requirement_refs"
        ):
            OpenOpportunity.from_dict(opportunity_dict(requirement_refs=[""]))
        decision = AdmissionDecision.from_dict(
            decision_dict(admitted_scope_ref=None, resulting_binding_refs=[])
        )
        assert decision.admitted_scope_ref is None
        assert decision.resulting_binding_refs == ()


# ---------------------------------------------------------------------------
# store 切片(幂等 CRUD + 域隔离 + 非终态唯一;spec §6.1)
# ---------------------------------------------------------------------------


class TestOpenStoreSlice:
    def test_opportunity_idempotent_insert_and_shape_conflict(self):
        store = InMemoryMethodStore()
        opportunity = OpenOpportunity.from_dict(opportunity_dict())
        assert (
            store.insert_opportunity(AXES, opportunity, COLLET_ID)
            is opportunity
        )
        assert (
            store.insert_opportunity(AXES, opportunity, COLLET_ID)
            is opportunity
        )
        with pytest.raises(
            ContextCollabValidationError, match="stored shape"
        ):
            store.insert_opportunity(
                AXES,
                OpenOpportunity.from_dict(opportunity_dict(capacity=3)),
                COLLET_ID,
            )
        updated = replace(
            opportunity,
            state=OpportunityState.from_wire("PAUSED"),
            transitioned_from="OPEN",
        )
        store.update_opportunity_state(AXES, updated, COLLET_ID)
        with pytest.raises(ContextCollabNotFoundError):
            store.update_opportunity_state(
                AXES,
                OpenOpportunity.from_dict(
                    opportunity_dict(opportunity_id="opp_ghost")
                ),
                COLLET_ID,
            )

    def test_application_unique_active_applicant_and_reentry(self):
        """存储层唯一断言:同 (机会, 申请人) 非终态唯一;终态后可再投。"""
        store = InMemoryMethodStore()
        application = store.insert_application(
            AXES, "mb_open_001",
            ParticipationApplication.from_dict(application_dict()),
            COLLET_ID,
        )
        assert application.state is ApplicationState.SUBMITTED
        with pytest.raises(
            ContextCollabValidationError, match="non-terminal application"
        ):
            store.insert_application(
                AXES, "mb_open_001",
                ParticipationApplication.from_dict(
                    application_dict(application_id="app_002")
                ),
                COLLET_ID,
            )
        # 同申请人在别的机会/别的 binding 不占用。
        store.insert_application(
            AXES, "mb_open_001",
            ParticipationApplication.from_dict(
                application_dict(
                    application_id="app_003", opportunity_ref="opp_002"
                )
            ),
            COLLET_ID,
        )
        withdrawn = replace(
            application, state=ApplicationState.from_wire("WITHDRAWN")
        )
        store.update_application_state(
            AXES, "mb_open_001", withdrawn, COLLET_ID
        )
        revived = store.insert_application(
            AXES, "mb_open_001",
            ParticipationApplication.from_dict(
                application_dict(application_id="app_004")
            ),
            COLLET_ID,
        )
        assert revived.state is ApplicationState.SUBMITTED
        with pytest.raises(ContextCollabNotFoundError):
            store.update_application_state(
                AXES, "mb_open_001",
                ParticipationApplication.from_dict(
                    application_dict(application_id="app_ghost")
                ),
                COLLET_ID,
            )

    def test_decision_insert_idempotent_and_scoped(self):
        store = InMemoryMethodStore()
        decision = AdmissionDecision.from_dict(decision_dict())
        assert (
            store.insert_decision(AXES, "mb_open_001", decision, COLLET_ID)
            is decision
        )
        assert (
            store.get_decision(AXES, "dec_001", "mb_open_001", COLLET_ID)
            is decision
        )
        assert (
            store.get_decision(AXES, "dec_001", "mb_open_002", COLLET_ID)
            is None
        )
        assert [
            d.admission_decision_id
            for d in store.list_decisions(AXES, "mb_open_001", COLLET_ID)
        ] == ["dec_001"]


# ---------------------------------------------------------------------------
# 机会面操作(publish / pause / reopen / close / submit;spec §4.4)
# ---------------------------------------------------------------------------


class TestOpenOpportunityLifecycle:
    def test_publish_projects_open_opportunity(self):
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        result = service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        assert result.accepted is True
        assert result.object_kind == "open_opportunity"
        assert result.state == "OPEN"
        opportunity = store.get_opportunity(AXES, "opp_alpha", binding, COLLET_ID)
        assert opportunity.state is OpportunityState.OPEN
        assert opportunity.transitioned_from == "DRAFT"
        assert opportunity.participation_type is (
            ParticipationType.GROUP_MEMBER
        )

    def test_publish_window_shape_validation(self):
        _, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        with pytest.raises(
            ContextCollabValidationError, match="ISO timestamp"
        ):
            service.publish_opportunity(
                COLLET_ID, binding, opportunity_command(opens_at="soon"),
                make_actor(),
            )
        with pytest.raises(
            ContextCollabValidationError, match="later than opens_at"
        ):
            service.publish_opportunity(
                COLLET_ID,
                binding,
                opportunity_command(
                    opens_at=_iso_offset(1), closes_at=_iso_offset(-1)
                ),
                make_actor(),
            )

    def test_publish_replay_and_shape_conflict(self):
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        # 同一份命令两次提交(时间戳字段是请求形态,重放必须同值)。
        command = opportunity_command()
        first = service.publish_opportunity(
            COLLET_ID, binding, command, make_actor()
        )
        replay = service.publish_opportunity(
            COLLET_ID, binding, command, make_actor()
        )
        assert replay.object_id == first.object_id
        assert (
            len(store.list_opportunities(AXES, binding, COLLET_ID)) == 1
        )
        with pytest.raises(
            ContextCollabValidationError, match="stored shape"
        ):
            service.publish_opportunity(
                COLLET_ID,
                binding,
                opportunity_command(scope_ref="task_scope_other"),
                make_actor(),
            )

    def test_pause_reopen_close_reachability(self):
        """作者裁决补漏:ReopenOpportunity(PAUSED→OPEN)必须可达;
        CloseOpportunity 是终态,后继迁移由机器拒绝。"""
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        paused = service.pause_opportunity(
            COLLET_ID, binding, "opp_alpha",
            OpenOpportunityOpCommand(), make_actor(),
        )
        assert paused.state == "PAUSED"
        reopened = service.reopen_opportunity(
            COLLET_ID, binding, "opp_alpha",
            OpenOpportunityOpCommand(reason="重新开放"), make_actor(),
        )
        assert reopened.state == "OPEN"
        opportunity = store.get_opportunity(
            AXES, "opp_alpha", binding, COLLET_ID
        )
        assert opportunity.state is OpportunityState.OPEN
        assert opportunity.transition_reason == "重新开放"
        closed = service.close_opportunity(
            COLLET_ID, binding, "opp_alpha", OpenOpportunityOpCommand(),
            make_actor(),
        )
        assert closed.state == "CLOSED"
        with pytest.raises(
            ContextCollabValidationError, match="does not accept event"
        ):
            service.reopen_opportunity(
                COLLET_ID, binding, "opp_alpha", OpenOpportunityOpCommand(),
                make_actor(),
            )
        # 未发布(无机会行)的 close 走 NotFound。
        with pytest.raises(ContextCollabNotFoundError):
            service.close_opportunity(
                COLLET_ID, binding, "opp_ghost", OpenOpportunityOpCommand(),
                make_actor(),
            )

    def test_submit_application_public_plane_window_and_capacity(self):
        """submit_application 是公开面:任意已认证 actor 可投;机会必须
        OPEN、在窗口内、容量未满。"""
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        submitted = service.submit_application(
            COLLET_ID, binding, "opp_alpha", application_command(),
            make_actor("u_stranger_001"),
        )
        assert submitted.accepted is True
        assert submitted.object_id == "app_1"
        assert submitted.state == "SUBMITTED"
        application = store.get_application(
            AXES, "app_1", binding, COLLET_ID
        )
        assert application.opportunity_ref == "opp_alpha"
        assert application.accountable_principal_ref == "u_member_001"
        assert application.transitioned_from == ""
        # 幂等重放:同 ID 同形态返回既有对象,不新增行。
        replay = service.submit_application(
            COLLET_ID, binding, "opp_alpha", application_command(),
            make_actor("u_stranger_002"),
        )
        assert replay.object_id == "app_1"
        assert len(store.list_applications(AXES, binding, COLLET_ID)) == 1
        # 未开窗(未来 opens_at)拒绝。
        service.publish_opportunity(
            COLLET_ID,
            binding,
            opportunity_command(
                opportunity_id="opp_future", opens_at=_iso_offset(2)
            ),
            make_actor(),
        )
        with pytest.raises(
            ContextCollabValidationError, match="before the window opens"
        ):
            service.submit_application(
                COLLET_ID, binding, "opp_future", application_command(),
                make_actor(),
            )
        # 已闭窗拒绝。
        service.publish_opportunity(
            COLLET_ID,
            binding,
            opportunity_command(
                opportunity_id="opp_closed",
                opens_at=_iso_offset(-2),
                closes_at=_iso_offset(-1),
            ),
            make_actor(),
        )
        with pytest.raises(
            ContextCollabValidationError, match="after the window closes"
        ):
            service.submit_application(
                COLLET_ID, binding, "opp_closed", application_command(),
                make_actor(),
            )
        # 容量:capacity=1,两个申请人,第二个拒绝。
        service.publish_opportunity(
            COLLET_ID,
            binding,
            opportunity_command(
                opportunity_id="opp_capped", capacity=1
            ),
            make_actor(),
        )
        service.submit_application(
            COLLET_ID, binding, "opp_capped",
            application_command(applicant_agent_ref="bot_a", application_id="app_ca"),
            make_actor(),
        )
        with pytest.raises(ContextCollabValidationError, match="capacity"):
            service.submit_application(
                COLLET_ID, binding, "opp_capped",
                application_command(
                    applicant_agent_ref="bot_b", application_id="app_cb"
                ),
                make_actor(),
            )

    def test_submit_requires_open_opportunity_and_unique_applicant(self):
        _, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        service.pause_opportunity(
            COLLET_ID, binding, "opp_alpha", OpenOpportunityOpCommand(),
            make_actor(),
        )
        with pytest.raises(
            ContextCollabValidationError, match="only be submitted while"
        ):
            service.submit_application(
                COLLET_ID, binding, "opp_alpha", application_command(),
                make_actor(),
            )
        service.reopen_opportunity(
            COLLET_ID, binding, "opp_alpha", OpenOpportunityOpCommand(),
            make_actor(),
        )
        service.submit_application(
            COLLET_ID, binding, "opp_alpha", application_command(),
            make_actor(),
        )
        with pytest.raises(
            ContextCollabValidationError, match="non-terminal application"
        ):
            service.submit_application(
                COLLET_ID, binding, "opp_alpha",
                application_command(application_id="app_dup"),
                make_actor(),
            )
        # 终态后可再投(WITHDRAWN 后同申请人重新申请)。


# ---------------------------------------------------------------------------
# 申请与录取面(screen / reject / withdraw / admit / revoke;spec §4.4)
# ---------------------------------------------------------------------------


def screening_application(
    service: OpenMethodService,
    lifecycle: MethodLifecycleService,
    binding_id: str = "mb_open_alpha",
    **opportunity_overrides,
) -> tuple[str, str]:
    """发布机会 + 投申请 + 评审,返回 (opportunity_id, application_id)。"""
    opp = published_opportunity(
        service, lifecycle, binding_id, **opportunity_overrides
    )
    app = submitted_application(service, opp, binding_id)
    service.screen(
        COLLET_ID, binding_id, app, OpenDecisionOpCommand(), make_actor()
    )
    return opp, app


class TestOpenApplicationLifecycle:
    def test_screen_then_reject_both_rules(self):
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        service.submit_application(
            COLLET_ID, binding, "opp_alpha", application_command(),
            make_actor(),
        )
        screened = service.screen(
            COLLET_ID, binding, "app_1", OpenDecisionOpCommand(), make_actor()
        )
        assert screened.state == "SCREENING"
        rejected = service.reject(
            COLLET_ID, binding, "app_1", OpenDecisionOpCommand(reason="不合条件"),
            make_actor(),
        )
        assert rejected.state == "REJECTED"
        application = store.get_application(AXES, "app_1", binding, COLLET_ID)
        assert application.transitioned_from == "SCREENING"
        assert application.transition_reason == "不合条件"
        # reject_r1:未经评审直接拒。
        service.submit_application(
            COLLET_ID, binding, "opp_alpha",
            application_command(application_id="app_r1", applicant_agent_ref="bot_x"),
            make_actor(),
        )
        directly = service.reject(
            COLLET_ID, binding, "app_r1", OpenDecisionOpCommand(), make_actor()
        )
        assert directly.state == "REJECTED"

    def test_withdraw_is_self_help_only(self):
        """withdraw 是责任主体本人自助:与目标行 accountable_principal_ref
        字符串相等才放行;机器仅 SUBMITTED 可退(SCREENING 后不可自助退)。"""
        _, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        service.submit_application(
            COLLET_ID, binding, "opp_alpha", application_command(),
            make_actor(),
        )
        # 陌生人拒绝;owner(PRINCIPAL_ID ≠ u_member_001)也拒绝。
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.withdraw(
                COLLET_ID, binding, "app_1", OpenDecisionOpCommand(),
                make_actor("u_9999"),
            )
        with pytest.raises(
            ContextCollabValidationError, match=PRINCIPAL_ID
        ):
            service.withdraw(
                COLLET_ID, binding, "app_1", OpenDecisionOpCommand(),
                make_actor(PRINCIPAL_ID),
            )
        withdrawn = service.withdraw(
            COLLET_ID, binding, "app_1", OpenDecisionOpCommand(),
            make_actor("u_member_001"),
        )
        assert withdrawn.state == "WITHDRAWN"
        # SCREENING 后机器无 withdraw 出边。
        service.submit_application(
            COLLET_ID, binding, "opp_alpha",
            application_command(application_id="app_s", applicant_agent_ref="bot_y"),
            make_actor(),
        )
        service.screen(
            COLLET_ID, binding, "app_s", OpenDecisionOpCommand(), make_actor()
        )
        with pytest.raises(
            ContextCollabValidationError, match="does not accept event"
        ):
            service.withdraw(
                COLLET_ID, binding, "app_s", OpenDecisionOpCommand(),
                make_actor("u_member_001"),
            )

    def test_admit_group_member_product(self):
        """GROUP_MEMBER 录取:落 ACTIVE 成员行(roster 惰性建) +
        AdmissionDecision 产物引用。"""
        store, _, lifecycle, service = make_open_service()
        opp, app = screening_application(service, lifecycle)
        binding = "mb_open_alpha"
        admitted = service.admit(
            COLLET_ID, binding, app,
            OpenAdmitCommand(admitted_scope_ref="scope://admitted"),
            make_actor(),
        )
        assert admitted.state == "ADMITTED"
        members = store.list_members(AXES, binding, COLLET_ID)
        assert len(members) == 1
        assert members[0].state is MemberState.ACTIVE
        assert members[0].participant_ref == "bot_worker_001"
        assert members[0].joined_at
        assert (
            store.get_roster(AXES, binding, COLLET_ID).roster_id
            == f"roster_{binding}"
        )
        decision = store.list_decisions(AXES, binding, COLLET_ID)[0]
        assert decision.admitted_scope_ref == "scope://admitted"
        assert decision.resulting_binding_refs == (
            members[0].group_member_binding_id,
        )
        # G1 断言:录取不产生新 binding / selection / composition edge
        # (admitted ≠ Space Membership 的 service 面投影)。
        assert len(store.list_bindings(AXES, COLLET_ID)) == 1  # 仅既有 Open binding
        assert store.list_selections(AXES, binding, COLLET_ID) == []
        assert store.list_composition_edges(AXES, COLLET_ID) == []

    def test_admit_swarm_allocation_requires_target_stage(self):
        """SWARM_ALLOCATION 录取:target_stage_ref 必填且必须指向本
        binding 作用域内计划的 READY/RUNNING Stage;缺省/越域/非法状态
        一律拒绝,通过后落 ACTIVE 语义分配。"""
        store, _, lifecycle, service = make_open_service()
        binding = "mb_open_alpha"  # screening_application 负责激活
        opp, app = screening_application(
            service, lifecycle, binding,
            participation_type="SWARM_ALLOCATION",
        )
        # 缺省 target_stage_ref 拒。
        with pytest.raises(
            ContextCollabValidationError, match="target_stage_ref"
        ):
            service.admit(
                COLLET_ID, binding, app, OpenAdmitCommand(), make_actor()
            )
        # 域内不存在的 Stage 拒。
        with pytest.raises(
            ContextCollabValidationError, match="not found in the scope"
        ):
            service.admit(
                COLLET_ID, binding, app,
                OpenAdmitCommand(target_stage_ref="st_ghost"), make_actor(),
            )
        # 直落一条 PENDING(未 READY)Stage 拒——谓 READY/RUNNING。
        store.insert_stage(
            AXES, binding,
            replace(
                make_ready_stage("st_pending"),
                state=StageState.from_wire("PENDING"),
            ),
            COLLET_ID,
        )
        with pytest.raises(ContextCollabValidationError, match="PENDING"):
            service.admit(
                COLLET_ID, binding, app,
                OpenAdmitCommand(target_stage_ref="st_pending"), make_actor(),
            )
        # READY Stage 放行:落 ACTIVE 语义分配,能力证据承接申请。
        store.insert_stage(
            AXES, binding, make_ready_stage("st_ready"), COLLET_ID
        )
        admitted = service.admit(
            COLLET_ID, binding, app,
            OpenAdmitCommand(target_stage_ref="st_ready"), make_actor(),
        )
        assert admitted.state == "ADMITTED"
        decisions = store.list_decisions(AXES, binding, COLLET_ID)
        allocation_id = decisions[0].resulting_binding_refs[0]
        allocation = store.get_allocation(
            AXES, allocation_id, binding, COLLET_ID
        )
        assert allocation.state is AllocationState.ACTIVE
        assert allocation.stage_ref == "st_ready"
        assert allocation.target_ref == "bot_worker_001"
        assert allocation.qualification_evidence_refs == (
            "evidence://skill",
        )

    def test_admit_single_contribution_records_refs_only(self):
        store, _, lifecycle, service = make_open_service()
        opp, app = screening_application(
            service, lifecycle, participation_type="SINGLE_CONTRIBUTION"
        )
        admitted = service.admit(
            COLLET_ID, "mb_open_alpha", app,
            OpenAdmitCommand(admitted_scope_ref="scope://draft"),
            make_actor(),
        )
        assert admitted.state == "ADMITTED"
        binding = "mb_open_alpha"
        decision = store.list_decisions(AXES, binding, COLLET_ID)[0]
        assert decision.resulting_binding_refs == ("artifact://draft",)
        # 不产生成员行与分配。
        assert store.list_members(AXES, binding, COLLET_ID) == []
        assert store.list_allocations(AXES, binding, COLLET_ID) == []

    def test_admit_collet_participant_is_p1_rejected(self):
        _, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        with pytest.raises(
            ContextCollabValidationError,
            match="COLLET_PARTICIPANT",
        ):
            service.publish_opportunity(
                COLLET_ID,
                binding,
                opportunity_command(participation_type="COLLET_PARTICIPANT"),
                make_actor(),
            )

    def test_admit_requires_screening_state_and_is_idempotent(self):
        """机器裁决:未评审(SCREENING 前)不可录取;同 decision_id 重放
        产物不重建。"""
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        service.publish_opportunity(
            COLLET_ID,
            binding,
            opportunity_command(participation_type="GROUP_MEMBER"),
            make_actor(),
        )
        submitted_application(service, "opp_alpha", binding)
        with pytest.raises(
            ContextCollabValidationError, match="does not accept event"
        ):
            service.admit(
                COLLET_ID, binding, "app_1", OpenAdmitCommand(), make_actor()
            )
        service.screen(
            COLLET_ID, binding, "app_1", OpenDecisionOpCommand(), make_actor()
        )
        first = service.admit(
            COLLET_ID, binding, "app_1",
            OpenAdmitCommand(decision_id="dec_1"), make_actor(),
        )
        assert first.state == "ADMITTED"
        replay = service.admit(
            COLLET_ID, binding, "app_1",
            OpenAdmitCommand(decision_id="dec_1"), make_actor(),
        )
        assert replay.state == "ADMITTED"
        # 产物不重建:成员行恰一条、decision 恰一条。
        assert len(store.list_members(AXES, binding, COLLET_ID)) == 1
        assert len(store.list_decisions(AXES, binding, COLLET_ID)) == 1
        with pytest.raises(
            ContextCollabValidationError, match="stored shape"
        ):
            service.admit(
                COLLET_ID, binding, "app_1",
                OpenAdmitCommand(
                    decision_id="dec_1", admitted_scope_ref="scope://other"
                ),
                make_actor(),
            )

    def test_revoke_admission_terminates_products(self):
        """revoke_admission:成员 → REMOVED、分配 → REVOKED;
        SINGLE_CONTRIBUTION 无产物可终结;申请经机器 ADMITTED→REVOKED。"""
        # GROUP_MEMBER 产物终结。
        store, _, lifecycle, service = make_open_service()
        opp, app = screening_application(
            service, lifecycle, participation_type="GROUP_MEMBER"
        )
        binding = "mb_open_alpha"
        service.admit(
            COLLET_ID, binding, app, OpenAdmitCommand(), make_actor()
        )
        member_id = store.list_members(AXES, binding, COLLET_ID)[
            0
        ].group_member_binding_id
        revoked = service.revoke_admission(
            COLLET_ID, binding, app, OpenDecisionOpCommand(), make_actor()
        )
        assert revoked.state == "REVOKED"
        member = next(
            m
            for m in store.list_members(AXES, binding, COLLET_ID)
            if m.group_member_binding_id == member_id
        )
        assert member.state is MemberState.REMOVED
        assert member.left_at
        assert member.transitioned_from == "ACTIVE"
        # SWARM_ALLOCATION 产物终结:语义分配 → REVOKED。
        store2, _, lifecycle2, service2 = make_open_service()
        binding2 = "mb_open_alpha"  # screening_application 负责激活
        opp2, app2 = screening_application(
            service2, lifecycle2, binding2,
            participation_type="SWARM_ALLOCATION",
        )
        store2.insert_stage(
            AXES, binding2,
            make_ready_stage("st_ready", binding2), COLLET_ID,
        )
        service2.admit(
            COLLET_ID, binding2, app2,
            OpenAdmitCommand(target_stage_ref="st_ready"), make_actor(),
        )
        allocation_id = store2.list_decisions(
            AXES, binding2, COLLET_ID
        )[0].resulting_binding_refs[0]
        revoked2 = service2.revoke_admission(
            COLLET_ID, binding2, app2, OpenDecisionOpCommand(), make_actor()
        )
        assert revoked2.state == "REVOKED"
        allocation = store2.get_allocation(
            AXES, allocation_id, binding2, COLLET_ID
        )
        assert allocation.state is AllocationState.REVOKED
        assert allocation.transitioned_from == "ACTIVE"


# ---------------------------------------------------------------------------
# 守卫:RF#2 + 结构 Open binding + 授权矩阵(G1/T6)
# ---------------------------------------------------------------------------


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


class TestOpenGuards:
    def test_rf2_non_active_binding_rejected(self):
        """RF#2:binding 非 ACTIVE 时副作用操作一律拒绝(公开提交
        submit_application 同为副作用面)。"""
        _, _, lifecycle, service = make_open_service()
        lifecycle.bind(
            COLLET_ID,
            BindMethodCommand(
                method_kind="Open",
                method_binding_id="mb_open_bound",
                parent_scope_ref="task_scope_root",
            ),
            make_actor(),
        )
        bound = "mb_open_bound"
        callables = [
            lambda: service.publish_opportunity(
                COLLET_ID, bound, opportunity_command(), make_actor()
            ),
            lambda: service.pause_opportunity(
                COLLET_ID, bound, "opp_x", OpenOpportunityOpCommand(),
                make_actor(),
            ),
            lambda: service.reopen_opportunity(
                COLLET_ID, bound, "opp_x", OpenOpportunityOpCommand(),
                make_actor(),
            ),
            lambda: service.close_opportunity(
                COLLET_ID, bound, "opp_x", OpenOpportunityOpCommand(),
                make_actor(),
            ),
            lambda: service.submit_application(
                COLLET_ID, bound, "opp_x", application_command(), make_actor()
            ),
            lambda: service.screen(
                COLLET_ID, bound, "app_x", OpenDecisionOpCommand(),
                make_actor(),
            ),
            lambda: service.reject(
                COLLET_ID, bound, "app_x", OpenDecisionOpCommand(),
                make_actor(),
            ),
            lambda: service.withdraw(
                COLLET_ID, bound, "app_x", OpenDecisionOpCommand(),
                make_actor("u_member_001"),
            ),
            lambda: service.admit(
                COLLET_ID, bound, "app_x", OpenAdmitCommand(), make_actor()
            ),
            lambda: service.revoke_admission(
                COLLET_ID, bound, "app_x", OpenDecisionOpCommand(),
                make_actor(),
            ),
        ]
        for op_call in callables:
            with pytest.raises(
                ContextCollabValidationError, match="ACTIVE"
            ):
                op_call()

    def test_open_ops_reject_non_open_binding(self):
        """结构守卫:Group binding 上的 Open 剖面操作是结构漂移,拒绝。"""
        _, _, lifecycle, service = make_open_service()
        group_binding = "mb_group_only"
        lifecycle.bind(
            COLLET_ID,
            BindMethodCommand(
                method_kind="Group",
                method_binding_id=group_binding,
                parent_scope_ref="task_scope_root",
            ),
            make_actor(),
        )
        lifecycle.activate(
            COLLET_ID, group_binding, MethodStateChangeCommand(), make_actor()
        )
        with pytest.raises(
            ContextCollabValidationError, match="declare Open"
        ):
            service.publish_opportunity(
                COLLET_ID, group_binding, opportunity_command(), make_actor()
            )

    def test_owner_ops_reject_stranger(self):
        """T6:八个 owner 操作不放行陌生人(frozenset pin + 行为 pin);
        submit 是公开面;withdraw 是 self-help。"""
        assert OPEN_OWNER_OPS == frozenset(OWNER_OPS_ALL)
        assert OPEN_PUBLIC_OPS == frozenset({"submit_application"})
        assert OPEN_SELF_HELP_OPS == frozenset({"withdraw"})
        store, _, lifecycle, service = make_open_service()
        binding = active_open_binding(lifecycle)
        stranger = make_actor("u_9999")
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.publish_opportunity(
                COLLET_ID, binding, opportunity_command(), stranger
            )
        service.publish_opportunity(
            COLLET_ID, binding, opportunity_command(), make_actor()
        )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.pause_opportunity(
                COLLET_ID, binding, "opp_alpha",
                OpenOpportunityOpCommand(), stranger,
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.close_opportunity(
                COLLET_ID, binding, "opp_alpha",
                OpenOpportunityOpCommand(), stranger,
            )
        service.submit_application(
            COLLET_ID, binding, "opp_alpha", application_command(),
            make_actor(),
        )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.screen(
                COLLET_ID, binding, "app_1", OpenDecisionOpCommand(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.reject(
                COLLET_ID, binding, "app_1", OpenDecisionOpCommand(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.admit(
                COLLET_ID, binding, "app_1", OpenAdmitCommand(), stranger
            )
        with pytest.raises(ContextCollabValidationError, match="u_9999"):
            service.revoke_admission(
                COLLET_ID, binding, "app_1", OpenDecisionOpCommand(), stranger
            )
        # 公开面不拒陌生人(submit 已在上方覆盖)。

    def test_missing_binding_and_opportunity_not_found(self):
        _, _, _, service = make_open_service()
        with pytest.raises(ContextCollabNotFoundError):
            service.publish_opportunity(
                COLLET_ID, "mb_ghost", opportunity_command(), make_actor()
            )
        with pytest.raises(ContextCollabNotFoundError):
            service.pause_opportunity(
                COLLET_ID, "mb_ghost", "opp_none",
                OpenOpportunityOpCommand(), make_actor(),
            )
        with pytest.raises(ContextCollabNotFoundError):
            service.submit_application(
                COLLET_ID, "mb_ghost", "opp_none", application_command(),
                make_actor(),
            )


class TestOpenProtocolConformance:
    def test_service_satisfies_open_protocol(self):
        """``OpenMethodService`` 满足 ``OpenMethodOpsServiceProtocol``
        的 runtime_checkable isinstance 面。"""
        _, _, _, service = make_open_service()
        assert isinstance(service, OpenMethodOpsServiceProtocol)
