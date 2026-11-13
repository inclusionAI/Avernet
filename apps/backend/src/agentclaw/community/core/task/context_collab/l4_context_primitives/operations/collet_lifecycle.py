"""Collet 生命周期五操作服务(spec §5.3;ACCS-104 §3 Context 操作族)。

五个操作(``activate`` / ``suspend`` / ``resume`` / ``complete`` /
``archive``)输入 ``collet_id`` + ``ActorContext``(+可选 ``reason``),经
G4 ``collet_lifecycle`` 机器 ``resolve_transition`` 裁决后替换进程内
store 状态;不落事件、不写库(仓储仅做存在性与守卫读用)。P1 纯内部
API,不上 HTTP wire。

守卫语义(同 G1 ``_is_principal`` / ``_is_executor`` 判定):发起者本人
或结构匹配的 executor 可调,陌生 actor 抛 ``ContextCollabValidationError``;
不扩 ``ContextCollabAuthorizerProtocol`` 合同。重复操作幂等拒绝——非法
迁移即天然防重(沿 G2 §8.3 既有裁决)。操作返回小型结果对象,聚合根不
加审计字段(审计与事件化一起归 P2)。
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.context_state_machines import (
    COLLET_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    resolve_transition,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    CollaborationReader,
    ColletLifecycleStore,
)
from injector import inject

# 单键注册表取机器;"collet_lifecycle" 逐字(G4 conformance 已钉死)。
_COLLET_LIFECYCLE_MACHINE = COLLET_MACHINE_REGISTRY["collet_lifecycle"]


@dataclass(frozen=True)
class ColletLifecycleResult:
    """生命周期操作结果:两态快照与操作者/理由(P1 无审计字段,归 P2)。"""

    collet_id: str
    old_state: ColletState
    new_state: ColletState
    initiator_id: str
    reason: str | None


class ColletLifecycleService:
    """Collet 顶层生命周期操作编排:存在性 → 守卫 → 机器裁决 → store 替换。"""

    @inject
    def __init__(
        self,
        collet_lifecycle_store: ColletLifecycleStore,
        collab_repo: CollaborationReader,
    ) -> None:
        self._store = collet_lifecycle_store
        self._repository = collab_repo

    def activate(
        self,
        collet_id: str,
        context: ActorContext,
        reason: str | None = None,
    ) -> ColletLifecycleResult:
        return self._transition(collet_id, context, "collet.activated", reason)

    def suspend(
        self,
        collet_id: str,
        context: ActorContext,
        reason: str | None = None,
    ) -> ColletLifecycleResult:
        return self._transition(collet_id, context, "collet.suspended", reason)

    def resume(
        self,
        collet_id: str,
        context: ActorContext,
        reason: str | None = None,
    ) -> ColletLifecycleResult:
        return self._transition(collet_id, context, "collet.resumed", reason)

    def complete(
        self,
        collet_id: str,
        context: ActorContext,
        reason: str | None = None,
    ) -> ColletLifecycleResult:
        return self._transition(collet_id, context, "collet.completed", reason)

    def archive(
        self,
        collet_id: str,
        context: ActorContext,
        reason: str | None = None,
    ) -> ColletLifecycleResult:
        return self._transition(collet_id, context, "collet.archived", reason)

    def _transition(
        self,
        collet_id: str,
        context: ActorContext,
        event_kind: str,
        reason: str | None,
    ) -> ColletLifecycleResult:
        record = self._repository.get_current_status(collet_id)
        if record is None:
            raise ContextCollabNotFoundError(f"context {collet_id!r} not found")
        self._assert_guard(record.collet, record.designated_executor_id, context)
        # 未见过的 collet(重启后)与新建的一样种子 ACTIVE(spec §5.3)。
        old_state = self._store.get_or_seed(collet_id)
        resolved = resolve_transition(
            _COLLET_LIFECYCLE_MACHINE,
            old_state.value,
            event_kind,
            {},
        )
        if resolved is None:
            # 无规则命中 = 非法迁移:拒绝而非静默保持(幂等即天然防重)。
            raise ContextCollabValidationError(
                f"illegal lifecycle transition {event_kind!r} from state "
                f"{old_state.value!r} on collet {collet_id!r}"
            )
        new_state = ColletState(resolved)
        self._store.set(collet_id, new_state)
        return ColletLifecycleResult(
            collet_id=collet_id,
            old_state=old_state,
            new_state=new_state,
            initiator_id=record.collet.initiator_ref.principal_id,
            reason=reason,
        )

    @staticmethod
    def _assert_guard(
        collet: Collet,
        executor_id: str | None,
        context: ActorContext,
    ) -> None:
        """守卫:发起者本人或 executor 结构匹配可调(内联断言,不扩协议)。"""
        is_principal = context.actor.principal_id == collet.initiator_ref.principal_id
        # executor 判定沿 G1 ``_is_executor`` 先例:principal_id 与
        # agent_id 字符串相等。
        is_executor = (
            executor_id is not None and context.actor.principal_id == executor_id
        )
        if is_principal or is_executor:
            return
        raise ContextCollabValidationError(
            "only the principal or executor may operate the lifecycle of "
            f"context {collet.collet_id!r}"
        )
