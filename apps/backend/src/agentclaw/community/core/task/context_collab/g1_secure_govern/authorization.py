"""G1 授权接缝:结构授权协议与 P1 默认实现(spec §13)。

P1 只做结构校验(``StructuralContextCollabAuthorizer``),外部
Membership / Grant / Policy Authority 的真实授权留给 P2,通过
``ContextCollabAuthorizerProtocol`` 接缝替换,不改服务层。
身份匹配规则:P1 不建 Principal/Agent 注册表,principal 以
``principal_id``、executor 以 ``agent_id`` 与认证 ActorContext 比对。
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.event_envelope import (
    EventRecord,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodKind,
)
from agentclaw.community.core.task.context_collab.service_api import ActorContext

VISIBILITY_PUBLIC = "public"
VISIBILITY_WORKSPACE = "workspace"
VISIBILITY_PRIVATE = "private"


@runtime_checkable
class ContextCollabAuthorizerProtocol(Protocol):
    """授权接缝;服务层只依赖本协议,不依赖具体实现。"""

    def check_create(self, spec: ContextSpec, actor: ActorContext) -> None: ...
    def check_append(self, collet: Collet, command, actor: ActorContext) -> None: ...
    def filter_visible_events(self, collet: Collet, events: Sequence[EventRecord],
                              actor: ActorContext) -> list[EventRecord]: ...


def _is_principal(collet: Collet, actor: ActorContext) -> bool:
    return actor.actor.principal_id == collet.current_snapshot.principal_ref.principal_id


def _is_executor(collet: Collet, actor: ActorContext) -> bool:
    executor = collet.current_snapshot.executor_ref
    return executor is not None and actor.actor.principal_id == executor.agent_id


class StructuralContextCollabAuthorizer(ContextCollabAuthorizerProtocol):
    """P1 默认实现(spec §13):仅结构校验。

    - ``check_create``:method=Single 时 ``executor_ref`` 不得为 None
      (principal/executor 引用非空已在 value object 校验);
    - ``check_append``:actor 必须是 principal 或 executor;
    - ``filter_visible_events``:public 永远可见,workspace 仅
      principal/executor 可见,private 仅 principal 可见。
    """

    def check_create(self, spec: ContextSpec, actor: ActorContext) -> None:
        if spec.method_kind == MethodKind.SINGLE.value and spec.executor_ref is None:
            raise ContextCollabValidationError(
                "method_kind 'Single' requires a non-null executor_ref"
            )

    def check_append(self, collet: Collet, command, actor: ActorContext) -> None:
        # P1 结构规则只看认证 actor;command 保留给 P2 策略引用。
        if _is_principal(collet, actor) or _is_executor(collet, actor):
            return
        raise ContextCollabValidationError(
            "only the principal or the executor may append events to "
            f"collab_context {collet.collab_context_id!r}"
        )

    def filter_visible_events(
        self, collet: Collet, events: Sequence[EventRecord],
        actor: ActorContext,
    ) -> list[EventRecord]:
        is_principal = _is_principal(collet, actor)
        is_executor = _is_executor(collet, actor)
        visible: list[EventRecord] = []
        for record in events:
            if record.visibility == VISIBILITY_PUBLIC:
                visible.append(record)
            elif record.visibility == VISIBILITY_WORKSPACE and (
                is_principal or is_executor
            ):
                visible.append(record)
            elif record.visibility == VISIBILITY_PRIVATE and is_principal:
                visible.append(record)
        return visible
