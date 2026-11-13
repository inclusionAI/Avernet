"""G1 授权接缝:结构授权协议与 P1 默认实现(spec §13)。

P1 只做结构校验(``StructuralContextCollabAuthorizer``),外部
Membership / Grant / Policy Authority 的真实授权留给 P2,通过
``ContextCollabAuthorizerProtocol`` 接缝替换,不改服务层。
身份匹配规则:P1 不建 Principal/Agent 注册表,新建的版本化产品通过实际 L4 RoleBinding 与认证 ActorContext 的类型+ID 比对;
未版本化历史行保留原 ID 比对语义。
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
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)

from agentclaw.community.core.task.context_collab.l4_context_primitives.participants import (
    ROLE_RESPONSIBLE_USER,
    ROLE_SELECTED_AGENT,
)
from agentclaw.community.core.task.context_collab.l6_products.binding import (
    actor_has_role,
)

VISIBILITY_PUBLIC = "public"
VISIBILITY_WORKSPACE = "workspace"
VISIBILITY_PRIVATE = "private"

# 顶层 method 白名单(2026-11-12 L5 method objects spec §9.5/§12 第 4 条;
# 2026-11-13 task_commission spec 裁决 B 扩一项):
# create_context 接受 Single / Open + Group / Open 三个产品形态;Open 单值
# 为任务代办的顶层方法形态(P1 闭协作,开放录取族 P2 接 ACCS-304);裸
# Group / Swarm 作为顶层无责任主体结构拒绝,仅可经嵌套 bind 进入。
TOP_LEVEL_METHOD_KINDS = frozenset(
    {
        MethodKind.SINGLE.value,
        MethodKind.OPEN_GROUP.value,
        MethodKind.OPEN.value,
    }
)


@runtime_checkable
class ContextCollabAuthorizerProtocol(Protocol):
    """授权接缝;服务层只依赖本协议,不依赖具体实现。

    ``spec`` 是仓储 record 的伴随投影字段(载体迁移自 Collet 聚合根,
    spec §5.2):授权规则与错误语义不因迁移改变。
    """

    def check_create(self, spec: ContextSpec, actor: ActorContext) -> None: ...
    def check_append(
        self, collet: Collet, spec: ContextSpec, command, actor: ActorContext
    ) -> None: ...
    def filter_visible_events(
        self,
        collet: Collet,
        spec: ContextSpec,
        events: Sequence[EventRecord],
        actor: ActorContext,
    ) -> list[EventRecord]: ...


def _is_principal(spec: ContextSpec, actor: ActorContext) -> bool:
    if spec.product_profile_version is not None:
        return actor_has_role(spec, actor.actor, ROLE_RESPONSIBLE_USER)
    # 身份取自创建参数(record 伴随投影的 context_spec),不经快照(可空)。
    return actor.actor.principal_id == spec.principal_ref.principal_id


def _is_executor(spec: ContextSpec, actor: ActorContext) -> bool:
    if spec.product_profile_version is not None:
        return actor_has_role(spec, actor.actor, ROLE_SELECTED_AGENT)
    executor = spec.executor_ref
    return executor is not None and actor.actor.principal_id == executor.agent_id


def _is_public_open_message(spec: ContextSpec, command) -> bool:
    """Whether Open+Group grants this actor only a public Message append.

    Open participation is deliberately narrower than an Open+Group Group
    membership: an unrelated authenticated actor may contribute a public
    ``user_interaction.posted`` event, but cannot create private/workspace
    events or drive Task state transitions. Those remain principal/executor
    responsibilities under G1.
    """
    return (
        spec.method_kind == MethodKind.OPEN_GROUP.value
        and command.event_kind == "user_interaction.posted"
        and command.primitive_type == "Message"
        and command.visibility == VISIBILITY_PUBLIC
    )


class StructuralContextCollabAuthorizer(ContextCollabAuthorizerProtocol):
    """P1 默认实现(spec §13):仅结构校验。

    - ``check_create``:顶层 method_kind 必须是 Single / Open + Group / Open
      (裸 Group/Swarm 只能经嵌套 bind 进入),且
      method=Single 时 ``executor_ref`` 不得为 None
      (principal/executor 引用非空已在 value object 校验);
    - ``check_append``:actor 必须是 principal、executor,或 Open+Group
      中的公开 Message 贡献者(仅 public 可见范围);
    - ``filter_visible_events``:public 永远可见,workspace 仅
      principal/executor 可见,private 仅 principal 可见;visibility
      为 NULL 的部分行按 private 处理(fail-safe)。
    """

    def check_create(self, spec: ContextSpec, actor: ActorContext) -> None:
        # 顶层白名单先于 Single executor 校验:未声明的顶层形态一律拒绝,
        # Group/Swarm 裸值经嵌套 bind 进入(spec §9.5)。
        if spec.method_kind not in TOP_LEVEL_METHOD_KINDS:
            raise ContextCollabValidationError(
                f"top-level method_kind must be one of {sorted(TOP_LEVEL_METHOD_KINDS)}"
                f" (spec §9.5), got: {spec.method_kind!r}; bare Group/Swarm may "
                "only enter via a nested method bind"
            )
        if spec.method_kind == MethodKind.SINGLE.value and spec.executor_ref is None:
            raise ContextCollabValidationError(
                "method_kind 'Single' requires a non-null executor_ref"
            )

    def check_append(
        self, collet: Collet, spec: ContextSpec, command, actor: ActorContext
    ) -> None:
        # P1 结构规则只看认证 actor;command 保留给 P2 策略引用。
        if (
            _is_principal(spec, actor)
            or _is_executor(spec, actor)
            or _is_public_open_message(spec, command)
        ):
            return
        raise ContextCollabValidationError(
            "only the principal, executor, or an Open+Group public-message "
            f"contributor may append events to collab_context "
            f"{collet.collet_id!r}"
        )

    def filter_visible_events(
        self,
        collet: Collet,
        spec: ContextSpec,
        events: Sequence[EventRecord],
        actor: ActorContext,
    ) -> list[EventRecord]:
        is_principal = _is_principal(spec, actor)
        is_executor = _is_executor(spec, actor)
        visible: list[EventRecord] = []
        for record in events:
            if record.visibility == VISIBILITY_PUBLIC:
                visible.append(record)
            elif record.visibility == VISIBILITY_WORKSPACE and (
                is_principal or is_executor
            ):
                visible.append(record)
            elif (
                record.visibility == VISIBILITY_PRIVATE or record.visibility is None
            ) and is_principal:
                # visibility=NULL(外部部分行)按 private 处理:收紧不泄露。
                visible.append(record)
        return visible
