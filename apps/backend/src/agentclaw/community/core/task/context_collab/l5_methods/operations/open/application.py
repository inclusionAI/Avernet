"""L5 Open 服务申请/录取面操作切片(spec §4.4):五个状态机操作 + 录取产物。

状态转换一律经 G4 声明式机器(``open_application``,``resolve_transition``
同构)裁决——机器内 withdraw 只有 SUBMITTED→WITHDRAWN 一条规则,进入
评审(SCREENING)后本人退出复用 reject 面;admit 只有
SCREENING→ADMITTED 一条规则,跳过评审直接 admit 由机器拒绝。

录取产物裁决(spec §4.4,按机会的 participation_type):
- ``GROUP_MEMBER`` → 在本 binding 名下创建 ACTIVE ``GroupMemberBinding``
  (roster 惰性建,沿 group 服务 ``_ensure_roster`` 先例);
- ``SWARM_ALLOCATION`` → 必须携带 ``target_stage_ref`` 且校验其属本
  binding 作用域内计划的 READY/RUNNING Stage(缺省/越域/非法状态一律
  拒绝——作者裁决);产物是一条 ACTIVE ``StageAllocation`` **语义记录**
  (P1 无 Computation Plane 派发:profile 与 lease 引用按派生哨兵落
  值,能力证据承接申请的 capability_evidence_refs;终态治理经
  ``revoke_admission`` 的 swarm.allocation.revoke 裁决);
- ``SINGLE_CONTRIBUTION`` → 仅记 artifact refs(resulting_binding_refs
  落申请的 proposed_artifact_refs),不产生参与绑定;
- ``COLLET_PARTICIPANT`` → P1 拒绝(L4 参与者治理是 P2)。

录取仅对 ``admitted_scope_ref`` 生效,不产生 Space Membership / Grant
(G1 断言);``admit`` 的幂等键是 ``decision_id``(§8.3,同 ID 同形态
早退、产物不重建);``revoke_admission`` 按 opportunity 类型终结产物:
成员行经 group.remove_member 裁决 → REMOVED(roster 乐观并发,逐次
bump 后取新基准)、分配经 swarm.allocation.revoke → REVOKED、
SINGLE_CONTRIBUTION 无产物可终结。
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    resolve_transition,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    GroupMemberBinding,
    GroupRoster,
    MemberState,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    AdmissionDecision,
    ApplicationState,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    OpenAdmitCommand,
    OpenDecisionOpCommand,
    ProfileOpAcceptedResult,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.open.base import (
    OpenMethodServiceBase,
    _now,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _shape_of,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    AllocationState,
    AllocationTargetKind,
    StageAllocation,
    StageState,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    StoreAxes,
)

_APPLICATION_MACHINE = METHOD_MACHINE_REGISTRY["open_application"]
_GROUP_MEMBER_MACHINE = METHOD_MACHINE_REGISTRY["group_member"]
_ALLOCATION_MACHINE = METHOD_MACHINE_REGISTRY["swarm_allocation"]

# P1 语义哨兵(§2 双平面):admit 产生的 StageAllocation 语义记录没有
# 真实执行剖面与租约治理——P2 Computation Plane 接入后由真实派发回写
# 替换;哨兵是结构上合法的非空引用,不与 allocate_stage 的真实 lease
# 断言冲突(语义记录不经那条操作路径创建)。
_ADMITTED_LEASE_EXPIRES_AT = "9999-12-31T23:59:59+00:00"

# admit 幂等重放(§8.3)的比对面:admitted_scope_ref 是请求形态;
# target_stage_ref 的产物只建一次(decision 早退),重放不重建。
_ADMIT_REQUEST_FIELDS = ("admitted_scope_ref",)


def _resolve_application_state(from_state: str, event: str) -> str:
    """经 G4 机器裁决 open_application 迁移;无出边即校验失败。"""
    new_state = resolve_transition(_APPLICATION_MACHINE, from_state, event, {})
    if new_state is None:
        raise ContextCollabValidationError(
            f"open application state {from_state!r} does not accept event {event!r}"
        )
    return new_state


def _resolve_member_state(from_state: str, event: str) -> str:
    """经 G4 机器裁决 group_member 迁移(revoke_admission 终结成员产物)。"""
    new_state = resolve_transition(_GROUP_MEMBER_MACHINE, from_state, event, {})
    if new_state is None:
        raise ContextCollabValidationError(
            f"group member state {from_state!r} does not accept event {event!r}"
        )
    return new_state


def _resolve_allocation_state(from_state: str, event: str) -> str:
    """经 G4 机器裁决 swarm_allocation 迁移(revoke_admission 终结分配
    产物;与 swarm_stage_ops 同一注册机器,经注册表单点引用)。"""
    new_state = resolve_transition(_ALLOCATION_MACHINE, from_state, event, {})
    if new_state is None:
        raise ContextCollabValidationError(
            f"swarm allocation state {from_state!r} does not accept event {event!r}"
        )
    return new_state


class OpenApplicationOpsMixin(OpenMethodServiceBase):
    """申请/录取面五操作 + 录取产物编排。"""

    def _transition_application(
        self,
        axes: StoreAxes,
        collab_context_id: str,
        method_binding_id: str,
        application,
        context: ActorContext,
        reason: str,
        event: str,
    ) -> ProfileOpAcceptedResult:
        """screen/reject/withdraw/admit/revoke 五态机操作共用编排:
        机器裁决 → 换新冻结实例(审计四字段)→ 覆写一次。"""
        new_state = _resolve_application_state(application.state.value, event)
        now = _now()
        updated = replace(
            application,
            state=ApplicationState.from_wire(new_state),
            transitioned_from=application.state.value,
            transition_reason=reason or event,
            transitioned_by=context.actor.principal_id,
            transitioned_at=now,
        )
        self._store.update_application_state(
            axes, method_binding_id, updated, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="participation_application",
            object_id=application.application_id,
            state=new_state,
        )

    def screen(
        self,
        collab_context_id: str,
        method_binding_id: str,
        application_ref: str,
        command: OpenDecisionOpCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """Screen 用例(owner 面,SUBMITTED──screen→SCREENING,标记
        进入评审)。"""
        axes = self._gate("screen", collab_context_id, method_binding_id, context)
        application = self._require_application(
            axes, collab_context_id, method_binding_id, application_ref
        )
        return self._transition_application(
            axes,
            collab_context_id,
            method_binding_id,
            application,
            context,
            command.reason,
            "open.application.screen",
        )

    def reject(
        self,
        collab_context_id: str,
        method_binding_id: str,
        application_ref: str,
        command: OpenDecisionOpCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """Reject 用例(owner 面,SUBMITTED/SCREENING──reject→REJECTED,
        机器双规则)。"""
        axes = self._gate("reject", collab_context_id, method_binding_id, context)
        application = self._require_application(
            axes, collab_context_id, method_binding_id, application_ref
        )
        return self._transition_application(
            axes,
            collab_context_id,
            method_binding_id,
            application,
            context,
            command.reason,
            "open.application.reject",
        )

    def withdraw(
        self,
        collab_context_id: str,
        method_binding_id: str,
        application_ref: str,
        command: OpenDecisionOpCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """Withdraw 用例(申请责任主体本人自助,spec §4.4):
        先定位目标行再授权(G1 比对 ``accountable_principal_ref``
        字符串相等),SUBMITTED──withdraw→WITHDRAWN。"""
        axes, binding = self._gate_preface(
            "withdraw", collab_context_id, method_binding_id
        )
        application = self._require_application(
            axes, collab_context_id, method_binding_id, application_ref
        )
        self._authorize(
            "withdraw",
            binding,
            context,
            collab_context_id,
            application=application,
        )
        return self._transition_application(
            axes,
            collab_context_id,
            method_binding_id,
            application,
            context,
            command.reason,
            "open.application.withdraw",
        )

    def _ensure_roster(
        self, axes: StoreAxes, method_binding_id: str, collab_context_id: str
    ) -> GroupRoster:
        """惰性建 roster(admit 的 GROUP_MEMBER 产物前置):每 binding
        至多一份,确定性 id ``roster_<binding_id>``;既有即返回。"""
        roster = self._store.get_roster(axes, method_binding_id, collab_context_id)
        if roster is None:
            return self._store.insert_roster(
                axes,
                GroupRoster.from_dict(
                    {
                        "roster_id": f"roster_{method_binding_id}",
                        "method_binding_id": method_binding_id,
                        "revision": 1,
                    }
                ),
                collab_context_id,
            )
        return roster

    def admit(
        self,
        collab_context_id: str,
        method_binding_id: str,
        application_ref: str,
        command: OpenAdmitCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """Admit 用例(owner 面,SCREENING──admit→ADMITTED):按机会
        participation_type 产生绑定产物并落一条 ``AdmissionDecision``
        (幂等键 ``decision_id``,§8.3)。录取仅对 ``admitted_scope_ref``
        生效,不产生 Space Membership / Grant(G1 断言)。"""
        axes = self._gate("admit", collab_context_id, method_binding_id, context)
        application = self._require_application(
            axes, collab_context_id, method_binding_id, application_ref
        )
        opportunity = self._require_opportunity(
            axes,
            collab_context_id,
            method_binding_id,
            application.opportunity_ref,
        )
        # §8.3 幂等早退:同 decision_id 的录取重放返回既有受理结果,
        # 产物(成员/分配)不重建、申请不被重复迁移。
        if command.decision_id is not None:
            existing_decision = self._store.get_decision(
                axes,
                command.decision_id,
                method_binding_id,
                collab_context_id,
            )
            if existing_decision is not None:
                if _shape_of(
                    existing_decision.to_dict(), _ADMIT_REQUEST_FIELDS
                ) == _shape_of(
                    {"admitted_scope_ref": command.admitted_scope_ref},
                    _ADMIT_REQUEST_FIELDS,
                ):
                    return ProfileOpAcceptedResult(
                        accepted=True,
                        collab_context_id=collab_context_id,
                        method_binding_id=method_binding_id,
                        object_kind="participation_application",
                        object_id=application.application_id,
                        state=application.state.value,
                    )
                raise ContextCollabValidationError(
                    f"idempotent re-submit of admission decision "
                    f"{command.decision_id!r} differs from the stored shape"
                )
        # 机器前置先裁:非 SCREENING 的录取在产物/裁决落库之前即拒
        # (与 swarm allocate 的"失败抛错于任何写之前"同款纪律)。
        _resolve_application_state(application.state.value, "open.application.admit")
        if opportunity.participation_type is (ParticipationType.COLLET_PARTICIPANT):
            raise ContextCollabValidationError(
                f"open opportunity {opportunity.opportunity_id!r} declares "
                "participation type COLLET_PARTICIPANT, which requires L4 "
                "participant governance and is not available in P1"
            )
        resulting_refs: list[str] = []
        now = _now()
        reason = command.reason or "open.application.admit"
        if opportunity.participation_type is ParticipationType.GROUP_MEMBER:
            roster = self._ensure_roster(axes, method_binding_id, collab_context_id)
            member = GroupMemberBinding.from_dict(
                {
                    "group_member_binding_id": f"gm_{uuid4().hex[:12]}",
                    "method_binding_id": method_binding_id,
                    "participant_ref": application.applicant_agent_ref,
                    "address": "",
                    "role_binding_refs": list(application.requested_role_refs),
                    # 录取即 ACTIVE 行:录取就是成员身份的权威落库,不经
                    # INVITED 邀请面(机器初始态语义由 admit 承载)。
                    "state": MemberState.ACTIVE.value,
                    "joined_at": now,
                    "left_at": None,
                    "transitioned_from": "",
                    "transition_reason": reason,
                    "transitioned_by": context.actor.principal_id,
                    "transitioned_at": now,
                }
            )
            self._store.insert_member(axes, member, roster.revision, collab_context_id)
            resulting_refs.append(member.group_member_binding_id)
        elif opportunity.participation_type is (ParticipationType.SWARM_ALLOCATION):
            allocation = self._admit_allocation(
                axes,
                collab_context_id,
                method_binding_id,
                opportunity,
                application,
                command,
                context,
                now,
            )
            self._store.insert_allocation(
                axes, method_binding_id, allocation, collab_context_id
            )
            resulting_refs.append(allocation.allocation_id)
        else:
            # SINGLE_CONTRIBUTION:仅记 artifact refs,不产生参与绑定。
            resulting_refs.extend(application.proposed_artifact_refs)
        decision = AdmissionDecision.from_dict(
            {
                "admission_decision_id": (
                    command.decision_id or f"dec_{uuid4().hex[:12]}"
                ),
                "application_ref": application.application_id,
                "decision_ref": (
                    f"open.admit:{opportunity.opportunity_id}:"
                    f"{application.application_id}"
                ),
                "admitted_scope_ref": command.admitted_scope_ref,
                "resulting_binding_refs": list(resulting_refs),
            }
        )
        self._store.insert_decision(
            axes, method_binding_id, decision, collab_context_id
        )
        return self._transition_application(
            axes,
            collab_context_id,
            method_binding_id,
            application,
            context,
            command.reason,
            "open.application.admit",
        )

    def _admit_allocation(
        self,
        axes: StoreAxes,
        collab_context_id: str,
        method_binding_id: str,
        opportunity,
        application,
        command: OpenAdmitCommand,
        context: ActorContext,
        now: str,
    ) -> StageAllocation:
        """校验 ``target_stage_ref`` 并构造 SWARM_ALLOCATION 录取语义
        记录(哨兵口径与能力证据承接见模块 docstring)。"""
        if not command.target_stage_ref:
            raise ContextCollabValidationError(
                f"admitting application {application.application_id!r} on "
                f"opportunity {opportunity.opportunity_id!r} requires a "
                "target_stage_ref pointing at a READY or RUNNING stage of "
                "this method binding (author ruling, spec §4.4)"
            )
        stage = self._store.get_stage(
            axes,
            command.target_stage_ref,
            method_binding_id,
            collab_context_id,
        )
        if stage is None:
            raise ContextCollabValidationError(
                f"admit target stage {command.target_stage_ref!r} not "
                f"found in the scope of method binding "
                f"{method_binding_id!r}"
            )
        if stage.state not in (StageState.READY, StageState.RUNNING):
            raise ContextCollabValidationError(
                f"admit target stage {command.target_stage_ref!r} is "
                f"{stage.state.value!r}; only READY or RUNNING stages can "
                "receive a SWARM_ALLOCATION admission"
            )
        allocation_id = f"alloc_{uuid4().hex[:12]}"
        return StageAllocation.from_dict(
            {
                "allocation_id": allocation_id,
                "stage_ref": command.target_stage_ref,
                "target_kind": AllocationTargetKind.AGENT.value,
                "target_ref": application.applicant_agent_ref,
                # P1 语义哨兵:profile 引用按机会派生、lease 不治理,
                # P2 Computation Plane 接入后由真实派发回写替换。
                "execution_profile_ref": (f"admitted:{application.opportunity_ref}"),
                "lease_ref": f"lease_{allocation_id}",
                "lease_expires_at": _ADMITTED_LEASE_EXPIRES_AT,
                "qualification_evidence_refs": list(
                    application.capability_evidence_refs
                ),
                "authorization_evidence_refs": [],
                "attempt": 1,
                "state": AllocationState.ACTIVE.value,
                "transition_reason": command.reason or "open.application.admit",
                "transitioned_by": context.actor.principal_id,
                "transitioned_at": now,
                "transitioned_from": AllocationState.PROPOSED.value,
            }
        )

    def revoke_admission(
        self,
        collab_context_id: str,
        method_binding_id: str,
        application_ref: str,
        command: OpenDecisionOpCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """RevokeAdmission 用例(owner 面,ADMITTED──revoke→REVOKED):
        按 opportunity 类型终结录取产物——成员行经 group.remove_member
        → REMOVED(roster 乐观并发,每次写后基准 +1)、分配经
        swarm.allocation.revoke → REVOKED、SINGLE_CONTRIBUTION 仅记
        refs 无产物可终结(spec §4.4)。"""
        axes = self._gate(
            "revoke_admission", collab_context_id, method_binding_id, context
        )
        application = self._require_application(
            axes, collab_context_id, method_binding_id, application_ref
        )
        opportunity = self._require_opportunity(
            axes,
            collab_context_id,
            method_binding_id,
            application.opportunity_ref,
        )
        decisions = [
            decision
            for decision in self._store.list_decisions(
                axes, method_binding_id, collab_context_id
            )
            if decision.application_ref == application.application_id
        ]
        now = _now()
        reason = command.reason or "open.application.revoke"
        if opportunity.participation_type is ParticipationType.GROUP_MEMBER:
            roster = self._store.get_roster(axes, method_binding_id, collab_context_id)
            expected_revision = roster.revision if roster is not None else 0
            members = {
                member.group_member_binding_id: member
                for member in self._store.list_members(
                    axes, method_binding_id, collab_context_id
                )
            }
            for decision in decisions:
                for member_id in decision.resulting_binding_refs:
                    member = members.get(member_id)
                    if member is None:
                        continue
                    removed_state = _resolve_member_state(
                        member.state.value, "group.remove_member"
                    )
                    updated = replace(
                        member,
                        state=MemberState.from_wire(removed_state),
                        left_at=now,
                        transitioned_from=member.state.value,
                        transition_reason=reason,
                        transitioned_by=context.actor.principal_id,
                        transitioned_at=now,
                    )
                    self._store.update_member_state(
                        axes, updated, expected_revision, collab_context_id
                    )
                    # roster revision 每次 bump +1:连续终结逐次取新基准。
                    expected_revision += 1
        elif opportunity.participation_type is (ParticipationType.SWARM_ALLOCATION):
            for decision in decisions:
                for allocation_id in decision.resulting_binding_refs:
                    allocation = self._store.get_allocation(
                        axes,
                        allocation_id,
                        method_binding_id,
                        collab_context_id,
                    )
                    if allocation is None:
                        continue
                    revoked_state = _resolve_allocation_state(
                        allocation.state.value, "swarm.allocation.revoke"
                    )
                    updated = replace(
                        allocation,
                        state=AllocationState.from_wire(revoked_state),
                        transitioned_from=allocation.state.value,
                        transition_reason=reason,
                        transitioned_by=context.actor.principal_id,
                        transitioned_at=now,
                    )
                    self._store.update_allocation_state(
                        axes, method_binding_id, updated, collab_context_id
                    )
        # SINGLE_CONTRIBUTION 决策只记 artifact refs,无产物可终结。
        return self._transition_application(
            axes,
            collab_context_id,
            method_binding_id,
            application,
            context,
            command.reason,
            "open.application.revoke",
        )
