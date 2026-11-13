"""L5 方法存储 Open 切片(Task 12,spec §4.4)。

键位纪律:与 swarm 切片同款,五段键 (avernet_tenant, env,
collab_context_id, method_binding_id, <对象id>),对象 id 只在 binding
域内充当幂等键。spec §4.4 YAML 只给 ``OpenOpportunity`` 落
method_binding_id 字段;application 只携带 ``opportunity_ref``、
decision 只携带 ``application_ref``,因此 application / decision 的
键段一律显式入参(沿 selection 的 collab_context_id 显式入参先例)。

存储层不变式(spec §6.1):同 (opportunity_ref, applicant_agent_ref)
至多一条非终态 application(SUBMITTED/SCREENING/ADMITTED 为活状态,
REJECTED/WITHDRAWN/REVOKED 终态可再投);insert_application 在锁内
裁断。幂等裁断与其他切片同款:同键同 ``to_dict`` 返回既有实例,同键
异形态抛 ``ContextCollabValidationError``;``update_*_state`` 覆盖式
换新实例,键不在库抛 ``ContextCollabNotFoundError``。decision 是
裁决实纬记录,P1 只增不改(录取回写一律经显式 RevokeAdmission 的
application 状态机)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    AdmissionDecision,
    ApplicationState,
    OpenOpportunity,
    ParticipationApplication,
)

if TYPE_CHECKING:  # pragma: no cover - 仅供注解,type check 期解析
    from agentclaw.community.core.task.context_collab.persistence.method_store import (
        StoreAxes,
    )

# 与 selection 同形的五段键:<对象id> 段均为 (binding, 对象id) 复合。
_OpenKey = tuple[str, str, str, str, str]

# 非终态申请集合(唯一断言的"活状态"口径):自 open_application 注册
# 机器治理类别派生(terminal_* 为终态,其余为活),不建平行词汇。
_ACTIVE_APPLICATION_STATES = frozenset(
    ApplicationState.from_wire(state.name)
    for state in METHOD_MACHINE_REGISTRY["open_application"].states
    if not state.governance_kind.startswith("terminal_")
)


class OpenSlice:
    """``MethodStoreProtocol`` 的 open opportunity/application/decision
    切片实现。"""

    # ------------------------------------------------------------------
    # open opportunity 切片(spec §4.4)
    # ------------------------------------------------------------------

    def insert_opportunity(
        self,
        axes: "StoreAxes",
        opportunity: OpenOpportunity,
        collab_context_id: str,
    ) -> OpenOpportunity:
        """幂等插入机会:同键同 ``to_dict`` 返回既有实例;同键异形态
        重复提交抛 ``ContextCollabValidationError``。opportunity 值对象
        自带 method_binding_id,键段取自对象。"""
        key = self._id_key(
            axes, collab_context_id, opportunity.method_binding_id,
            opportunity.opportunity_id,
        )
        with self._lock:
            return self._insert_open_object_locked(
                self._opportunities, key, opportunity, "open opportunity"
            )

    def get_opportunity(
        self,
        axes: "StoreAxes",
        opportunity_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> OpenOpportunity | None:
        with self._lock:
            return self._opportunities.get(
                self._id_key(
                    axes, collab_context_id, method_binding_id,
                    opportunity_id,
                )
            )

    def list_opportunities(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[OpenOpportunity]:
        """按插入序返回该 axes + context + binding 下的全部机会,
        不做状态过滤。"""
        with self._lock:
            return [
                opportunity
                for key, opportunity in self._opportunities.items()
                if self._key_in_binding(key, axes, collab_context_id,
                                        method_binding_id)
            ]

    def update_opportunity_state(
        self,
        axes: "StoreAxes",
        opportunity: OpenOpportunity,
        collab_context_id: str,
    ) -> None:
        """覆盖式换新机会实例(pause/reopen/close 经其落值);键必须
        已存在,否则抛 ``ContextCollabNotFoundError``。"""
        key = self._id_key(
            axes, collab_context_id, opportunity.method_binding_id,
            opportunity.opportunity_id,
        )
        with self._lock:
            if key not in self._opportunities:
                raise ContextCollabNotFoundError(
                    f"open opportunity {opportunity.opportunity_id!r} not "
                    f"found on method binding "
                    f"{opportunity.method_binding_id!r} in collaboration "
                    f"context {collab_context_id!r}"
                )
            self._opportunities[key] = opportunity

    # ------------------------------------------------------------------
    # open application 切片(spec §4.4 + §6.1 唯一断言)
    # ------------------------------------------------------------------

    def insert_application(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        application: ParticipationApplication,
        collab_context_id: str,
    ) -> ParticipationApplication:
        """幂等插入申请 + 非终态 (opportunity, applicant) 唯一断言。

        同键同 ``to_dict`` 返回既有实例;同键异形态抛校验错。新行若与
        同机会非终态在册申请同 ``applicant_agent_ref`` 拒绝(终态
        REJECTED/WITHDRAWN/REVOKED 后可再投,spec §4.4 / §6.1)。"""
        key = self._id_key(
            axes, collab_context_id, method_binding_id,
            application.application_id,
        )
        with self._lock:
            existing = self._applications.get(key)
            if existing is not None:
                if existing.to_dict() == application.to_dict():
                    return existing
                raise ContextCollabValidationError(
                    "idempotent re-submit of open application differs "
                    "from the stored shape"
                )
            self._reject_active_application_duplicate_locked(
                axes, collab_context_id, method_binding_id, application
            )
            self._applications[key] = application
            return application

    def get_application(
        self,
        axes: "StoreAxes",
        application_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> ParticipationApplication | None:
        with self._lock:
            return self._applications.get(
                self._id_key(
                    axes, collab_context_id, method_binding_id,
                    application_id,
                )
            )

    def list_applications(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[ParticipationApplication]:
        """按插入序返回该 axes + context + binding 下的全部申请,
        不做状态过滤;机会内过滤由调用方按 ``opportunity_ref`` 投影。"""
        with self._lock:
            return [
                application
                for key, application in self._applications.items()
                if self._key_in_binding(key, axes, collab_context_id,
                                        method_binding_id)
            ]

    def update_application_state(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        application: ParticipationApplication,
        collab_context_id: str,
    ) -> None:
        """覆盖式换新申请实例(screen/reject/withdraw/admit/revoke 经其
        落值);键必须已存在,否则抛 ``ContextCollabNotFoundError``。"""
        key = self._id_key(
            axes, collab_context_id, method_binding_id,
            application.application_id,
        )
        with self._lock:
            if key not in self._applications:
                raise ContextCollabNotFoundError(
                    f"open application {application.application_id!r} not "
                    f"found on method binding {method_binding_id!r} in "
                    f"collaboration context {collab_context_id!r}"
                )
            self._applications[key] = application

    # ------------------------------------------------------------------
    # admission decision 切片(spec §4.4,P1 只增不改)
    # ------------------------------------------------------------------

    def insert_decision(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        decision: AdmissionDecision,
        collab_context_id: str,
    ) -> AdmissionDecision:
        """幂等插入录取裁决(admit 经其落实纬记录);同键同 ``to_dict``
        返回既有实例,同键异形态抛校验错。decision 只增不改——撤销录取
        经 application 状态机 + 产物终结,不覆写决策记录。"""
        key = self._id_key(
            axes, collab_context_id, method_binding_id,
            decision.admission_decision_id,
        )
        with self._lock:
            return self._insert_open_object_locked(
                self._admission_decisions, key, decision,
                "admission decision",
            )

    def get_decision(
        self,
        axes: "StoreAxes",
        decision_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> AdmissionDecision | None:
        with self._lock:
            return self._admission_decisions.get(
                self._id_key(
                    axes, collab_context_id, method_binding_id, decision_id
                )
            )

    def list_decisions(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[AdmissionDecision]:
        """按插入序返回该 axes + context + binding 下的全部裁决
        (revoke_admission 按应用引用投影产物)。"""
        with self._lock:
            return [
                decision
                for key, decision in self._admission_decisions.items()
                if self._key_in_binding(key, axes, collab_context_id,
                                        method_binding_id)
            ]

    # ------------------------------------------------------------------
    # 内部:键构造与锁内裁断
    # ------------------------------------------------------------------

    @staticmethod
    def _id_key(
        axes: "StoreAxes",
        collab_context_id: str,
        method_binding_id: str,
        object_id: str,
    ) -> _OpenKey:
        return (
            axes.avernet_tenant,
            axes.env,
            collab_context_id,
            method_binding_id,
            object_id,
        )

    @staticmethod
    def _key_in_binding(
        key: _OpenKey,
        axes: "StoreAxes",
        collab_context_id: str,
        method_binding_id: str,
    ) -> bool:
        """五段键的租户/环境/上下文/binding 四段命中断言(list 投影用)。"""
        return (
            key[0] == axes.avernet_tenant
            and key[1] == axes.env
            and key[2] == collab_context_id
            and key[3] == method_binding_id
        )

    def _insert_open_object_locked(
        self,
        collection: dict,
        key: _OpenKey,
        obj,
        kind_name: str,
    ):
        """锁内幂等插入:同键同 ``to_dict`` 返回既有实例,同键异形态抛
        ``ContextCollabValidationError``;调用方必须已持 store 锁。"""
        existing = collection.get(key)
        if existing is not None:
            if existing.to_dict() == obj.to_dict():
                return existing
            raise ContextCollabValidationError(
                f"idempotent re-submit of {kind_name} differs from the "
                "stored shape"
            )
        collection[key] = obj
        return obj

    def _reject_active_application_duplicate_locked(
        self,
        axes: "StoreAxes",
        collab_context_id: str,
        method_binding_id: str,
        application: ParticipationApplication,
    ) -> None:
        """锁内唯一断言:同 (opportunity_ref, applicant_agent_ref) 已有
        非终态在册申请即拒绝(SubmitApplication 共用;终态后可再投)。"""
        for key, stored in self._applications.items():
            if (
                key[0] == axes.avernet_tenant
                and key[1] == axes.env
                and key[2] == collab_context_id
                and key[3] == method_binding_id
                and stored.opportunity_ref == application.opportunity_ref
                and stored.applicant_agent_ref
                == application.applicant_agent_ref
                and stored.state in _ACTIVE_APPLICATION_STATES
            ):
                raise ContextCollabValidationError(
                    f"applicant {application.applicant_agent_ref!r} already "
                    f"has a non-terminal application "
                    f"({stored.state.value!r}) for opportunity "
                    f"{application.opportunity_ref!r} on method binding "
                    f"{method_binding_id!r}; terminal applications can "
                    "re-apply"
                )
