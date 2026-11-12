"""L5 Open 服务机会面操作切片(spec §4.4):四个机会状态机操作 +
公开提交。

五个用例:``publish_opportunity`` / ``pause_opportunity`` /
``reopen_opportunity`` / ``close_opportunity`` / ``submit_application``。

- 状态转换一律经 G4 声明式机器(``open_opportunity`` /
  ``open_application`` 两键,``resolve_transition`` 同构)裁决;
  publish 是创建+迁移一步(DRAFT──publish→ OPEN,落终态一次写,
  transitioned_from="DRAFT");``ReopenOpportunity``(PAUSED→OPEN)
  是作者裁决补齐的可达性;open_opportunity 机器的 CANCELLED 态与
  cancel 事件在 P1 协议无对应操作(§5),词汇按机器保留;
- 窗口裁决(spec §4.4):publish 校验窗口形态——``opens_at`` 可解析、
  ``closes_at`` 给了就必须可解析且晚于 ``opens_at``(发布表达的是
  声明意图,不与 now 比对);``submit_application`` 校验窗口归属
  (opens_at ≤ now, closes_at 缺省或 now ≤ closes_at)且机会必须
  OPEN 且容量未满——同机会非终态申请(SUBMITTED/SCREENING/
  ADMITTED)计数 < capacity,capacity 为 ``None`` 不设上限;
- ``submit_application`` 是公开面(G1 OPEN_PUBLIC_OPS):任意已认证
  actor 可投递,冒名顶替的防线是"申请责任主体必填"——授权比对在
  withdraw 的 self-help 面;产物行带审计四字段 + submitted_at;
- 幂等重放(§8.3):publish 按 ``opportunity_id``、submit 按
  ``application_id``,同 ID 同请求形态返回既有已受理对象,异形态
  409 语义;"同 (opportunity, applicant) 非终态唯一"在存储层
  (open 切片)。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    resolve_transition,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    ApplicationState,
    OpenOpportunity,
    OpportunityState,
    ParticipationApplication,
    ParticipationType,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    OpenApplicationCommand,
    OpenOpportunityCommand,
    OpenOpportunityOpCommand,
    ProfileOpAcceptedResult,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    StoreAxes,
)
from agentclaw.community.core.task.context_collab.service_api import ActorContext
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    _shape_of,
)
from agentclaw.community.core.task.context_collab.services.open_method_service_base import (
    OpenMethodServiceBase,
    _now,
    _parse_timestamp,
)

_OPPORTUNITY_MACHINE = METHOD_MACHINE_REGISTRY["open_opportunity"]
_APPLICATION_MACHINE = METHOD_MACHINE_REGISTRY["open_application"]

# 幂等重放(§8.3)的"请求形态"白名单(排服务端时间/审计/状态机事实):
# opportunity 重放比对机会结构;application 重放比对申请四引用 + 三组
# refs(责任主体与申请人都是请求形态,不参与身份判定)。
_OPPORTUNITY_REQUEST_FIELDS = (
    "method_binding_id",
    "scope_ref",
    "participation_type",
    "requirement_refs",
    "admission_policy_refs",
    "capacity",
    "opens_at",
    "closes_at",
)
_APPLICATION_REQUEST_FIELDS = (
    "opportunity_ref",
    "applicant_agent_ref",
    "accountable_principal_ref",
    "capability_evidence_refs",
    "proposed_artifact_refs",
    "requested_role_refs",
)

# 容量计数的活状态集合:自 open_application 注册机器治理类别派生
# (terminal_* 不占容量),不建平行词汇。
_CAPACITY_OCCUPYING_STATES = frozenset(
    ApplicationState.from_wire(state.name)
    for state in _APPLICATION_MACHINE.states
    if not state.governance_kind.startswith("terminal_")
)


def _resolve_opportunity_state(from_state: str, event: str) -> str:
    """经 G4 机器裁决 open_opportunity 迁移;无出边即校验失败。"""
    new_state = resolve_transition(
        _OPPORTUNITY_MACHINE, from_state, event, {}
    )
    if new_state is None:
        raise ContextCollabValidationError(
            f"open opportunity state {from_state!r} does not accept event "
            f"{event!r}"
        )
    return new_state


class OpenOpportunityOpsMixin(OpenMethodServiceBase):
    """机会面四操作 + 公开提交。"""

    def _transition_opportunity(
        self,
        axes: StoreAxes,
        collab_context_id: str,
        method_binding_id: str,
        opportunity: OpenOpportunity,
        context: ActorContext,
        reason: str,
        event: str,
    ) -> ProfileOpAcceptedResult:
        """pause/reopen/close 三态机操作共用编排:机器裁决 → 换新冻结
        实例(审计四字段)→ 覆写一次。"""
        new_state = _resolve_opportunity_state(opportunity.state.value, event)
        now = _now()
        updated = replace(
            opportunity,
            state=OpportunityState.from_wire(new_state),
            transitioned_from=opportunity.state.value,
            transition_reason=reason or event,
            transitioned_by=context.actor.principal_id,
            transitioned_at=now,
        )
        self._store.update_opportunity_state(
            axes, updated, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="open_opportunity",
            object_id=opportunity.opportunity_id,
            state=new_state,
        )

    def publish_opportunity(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: OpenOpportunityCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """PublishOpportunity 用例:建机会并一步发布(DRAFT──publish→
        OPEN,落终态一次写);窗口形态校验见模块 docstring;幂等键
        ``opportunity_id``(§8.3)。

        P1 结构裁决:``COLLET_PARTICIPANT`` 参与型机会在发布面即拒
        (L4 参与者治理是 P2,不产生"可发布但不可录取"的悬空机会)。
        """
        axes = self._gate(
            "publish_opportunity", collab_context_id, method_binding_id,
            context,
        )
        opportunity_id = (
            command.opportunity_id or f"opp_{uuid4().hex[:12]}"
        )
        opens_at = _parse_timestamp(
            command.opens_at, "OpenOpportunityCommand", "opens_at"
        )
        if command.closes_at is not None:
            closes_at = _parse_timestamp(
                command.closes_at, "OpenOpportunityCommand", "closes_at"
            )
            if closes_at <= opens_at:
                raise ContextCollabValidationError(
                    f"open opportunity closes_at {command.closes_at!r} must "
                    f"be later than opens_at {command.opens_at!r}"
                )
        reason = command.reason or "open.opportunity.publish"
        now = _now()
        candidate = OpenOpportunity.from_dict(
            {
                "opportunity_id": opportunity_id,
                "method_binding_id": method_binding_id,
                "scope_ref": command.scope_ref,
                "participation_type": command.participation_type,
                "requirement_refs": list(command.requirement_refs),
                "admission_policy_refs": list(command.admission_policy_refs),
                "capacity": command.capacity,
                "opens_at": command.opens_at,
                "closes_at": command.closes_at,
                # open_opportunity 机器的初始态;publish 迁移后落 OPEN。
                "state": OpportunityState.OPEN.value,
                "transitioned_from": OpportunityState.DRAFT.value,
                "transition_reason": reason,
                "transitioned_by": context.actor.principal_id,
                "transitioned_at": now,
            }
        )
        # 发布形态校验之后、落库之前,机器再裁一遍 DRAFT──publish→OPEN。
        _resolve_opportunity_state(
            OpportunityState.DRAFT.value, "open.opportunity.publish"
        )
        if command.opportunity_id is not None:
            existing = self._store.get_opportunity(
                axes, command.opportunity_id, method_binding_id,
                collab_context_id,
            )
            if existing is not None:
                if _shape_of(
                    existing.to_dict(), _OPPORTUNITY_REQUEST_FIELDS
                ) == _shape_of(
                    candidate.to_dict(), _OPPORTUNITY_REQUEST_FIELDS
                ):
                    return ProfileOpAcceptedResult(
                        accepted=True,
                        collab_context_id=collab_context_id,
                        method_binding_id=method_binding_id,
                        object_kind="open_opportunity",
                        object_id=existing.opportunity_id,
                        state=existing.state.value,
                    )
                raise ContextCollabValidationError(
                    f"idempotent re-submit of open opportunity "
                    f"{command.opportunity_id!r} differs from the stored "
                    "shape"
                )
        # P1 结构裁决(模块 docstring):COLLET_PARTICIPANT 机会发布即拒
        # ——不产生"可发布但不可录取"的悬空机会;admit 面的同名裁断
        # 保留为 belt-and-suspenders。
        if (
            candidate.participation_type
            is ParticipationType.COLLET_PARTICIPANT
        ):
            raise ContextCollabValidationError(
                f"open opportunity {candidate.opportunity_id!r} declares "
                "participation type COLLET_PARTICIPANT, which requires L4 "
                "participant governance and is not available in P1"
            )
        self._store.insert_opportunity(axes, candidate, collab_context_id)
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="open_opportunity",
            object_id=candidate.opportunity_id,
            state=candidate.state.value,
        )

    def pause_opportunity(
        self,
        collab_context_id: str,
        method_binding_id: str,
        opportunity_ref: str,
        command: OpenOpportunityOpCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """PauseOpportunity 用例(OPEN──pause→PAUSED)。"""
        axes = self._gate(
            "pause_opportunity", collab_context_id, method_binding_id,
            context,
        )
        opportunity = self._require_opportunity(
            axes, collab_context_id, method_binding_id, opportunity_ref
        )
        return self._transition_opportunity(
            axes, collab_context_id, method_binding_id, opportunity,
            context, command.reason, "open.opportunity.pause",
        )

    def reopen_opportunity(
        self,
        collab_context_id: str,
        method_binding_id: str,
        opportunity_ref: str,
        command: OpenOpportunityOpCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """ReopenOpportunity 用例(PAUSED──reopen→OPEN,作者裁决补齐
        机会状态机可达性)。"""
        axes = self._gate(
            "reopen_opportunity", collab_context_id, method_binding_id,
            context,
        )
        opportunity = self._require_opportunity(
            axes, collab_context_id, method_binding_id, opportunity_ref
        )
        return self._transition_opportunity(
            axes, collab_context_id, method_binding_id, opportunity,
            context, command.reason, "open.opportunity.reopen",
        )

    def close_opportunity(
        self,
        collab_context_id: str,
        method_binding_id: str,
        opportunity_ref: str,
        command: OpenOpportunityOpCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """CloseOpportunity 用例(OPEN──close→CLOSED,终态)。"""
        axes = self._gate(
            "close_opportunity", collab_context_id, method_binding_id,
            context,
        )
        opportunity = self._require_opportunity(
            axes, collab_context_id, method_binding_id, opportunity_ref
        )
        return self._transition_opportunity(
            axes, collab_context_id, method_binding_id, opportunity,
            context, command.reason, "open.opportunity.close",
        )

    def submit_application(
        self,
        collab_context_id: str,
        method_binding_id: str,
        opportunity_ref: str,
        command: OpenApplicationCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """SubmitApplication 用例(公开面,任意已认证 actor,spec §4.4):
        机会必须 OPEN、在窗口内、容量未满;产物是 SUBMITTED 申请行
        (提交即责任声明确认,无机器迁移,transitioned_from 空串沿
        invite 先例)。幂等键 ``application_id``(§8.3);"同 (机会,
        申请人) 非终态唯一"在存储层。
        """
        axes = self._gate(
            "submit_application", collab_context_id, method_binding_id,
            context,
        )
        opportunity = self._require_opportunity(
            axes, collab_context_id, method_binding_id, opportunity_ref
        )
        if opportunity.state is not OpportunityState.OPEN:
            raise ContextCollabValidationError(
                f"open opportunity {opportunity_ref!r} is "
                f"{opportunity.state.value!r}; applications can only be "
                "submitted while the opportunity is OPEN"
            )
        now_dt = datetime.now(timezone.utc)
        opens_at = _parse_timestamp(
            opportunity.opens_at, "OpenOpportunity", "opens_at"
        )
        if now_dt < opens_at:
            raise ContextCollabValidationError(
                f"open opportunity {opportunity_ref!r} opens at "
                f"{opportunity.opens_at!r}, which is in the future; "
                "applications cannot be submitted before the window opens"
            )
        if opportunity.closes_at is not None:
            closes_at = _parse_timestamp(
                opportunity.closes_at, "OpenOpportunity", "closes_at"
            )
            if now_dt > closes_at:
                raise ContextCollabValidationError(
                    f"open opportunity {opportunity_ref!r} closed at "
                    f"{opportunity.closes_at!r}; applications cannot be "
                    "submitted after the window closes"
                )
        applications = [
            application
            for application in self._store.list_applications(
                axes, method_binding_id, collab_context_id
            )
            if application.opportunity_ref == opportunity_ref
        ]
        if opportunity.capacity is not None:
            occupying = sum(
                1
                for application in applications
                if application.state in _CAPACITY_OCCUPYING_STATES
            )
            if occupying >= opportunity.capacity:
                raise ContextCollabValidationError(
                    f"open opportunity {opportunity_ref!r} is at capacity "
                    f"({occupying}/{opportunity.capacity}); applications "
                    "cannot be submitted until slots free up"
                )
        reason = command.reason or "open.application.submit"
        now = _now()
        candidate = ParticipationApplication.from_dict(
            {
                "application_id": (
                    command.application_id
                    or f"app_{uuid4().hex[:12]}"
                ),
                "opportunity_ref": opportunity_ref,
                "applicant_agent_ref": command.applicant_agent_ref,
                "accountable_principal_ref": (
                    command.accountable_principal_ref
                ),
                "capability_evidence_refs": list(
                    command.capability_evidence_refs
                ),
                "proposed_artifact_refs": list(
                    command.proposed_artifact_refs
                ),
                "requested_role_refs": list(command.requested_role_refs),
                "state": ApplicationState.SUBMITTED.value,
                "submitted_at": now,
                "transitioned_from": "",
                "transition_reason": reason,
                "transitioned_by": context.actor.principal_id,
                "transitioned_at": now,
            }
        )
        if command.application_id is not None:
            existing = self._store.get_application(
                axes, command.application_id, method_binding_id,
                collab_context_id,
            )
            if existing is not None:
                if _shape_of(
                    existing.to_dict(), _APPLICATION_REQUEST_FIELDS
                ) == _shape_of(
                    candidate.to_dict(), _APPLICATION_REQUEST_FIELDS
                ):
                    return ProfileOpAcceptedResult(
                        accepted=True,
                        collab_context_id=collab_context_id,
                        method_binding_id=method_binding_id,
                        object_kind="participation_application",
                        object_id=existing.application_id,
                        state=existing.state.value,
                    )
                raise ContextCollabValidationError(
                    f"idempotent re-submit of open application "
                    f"{command.application_id!r} differs from the stored "
                    "shape"
                )
        self._store.insert_application(
            axes, method_binding_id, candidate, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="participation_application",
            object_id=candidate.application_id,
            state=candidate.state.value,
        )
