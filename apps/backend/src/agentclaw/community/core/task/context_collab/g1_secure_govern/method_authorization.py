"""G1 L5 方法操作授权接缝:协议 + P1 结构默认实现(spec §8.1)。

与既有 ``ContextCollabAuthorizerProtocol``(create/append/visibility)
并列、互不引用、互不改写。L5 方法面操作经本接缝裁决:服务层(T8+)
解析所属 Collet 的 ``principal_ref.principal_id`` 与
``executor_ref.agent_id`` 后显式传入 ``principal_id`` / ``executor_id``
参数——``MethodBinding`` 自身可能只携带 executor 侧 ``participant_ref``,
不足以判定身份;本模块因此保持纯函数(无 store、无 repo、无 Collet 依赖)。

身份判定沿 ``authorization.py`` 既有 ``_is_principal`` / ``_is_executor``
先例:认证 actor 的 ``actor.actor.principal_id`` 与目标标识**字符串精确
相等**。P2 注意:P1 不建 Principal/Agent 身份注册表,字符串相等只保证
"声明一致",不证明身份主体真实归属;P2 引入注册表或外部 Membership /
Grant / Policy Authority 后经本协议整体替换实现,服务层无需改动。

结构规则(作者裁决,spec §8.1 + §12 第 8 条):
- 治理面操作(lifecycle/group/swarm/open 属主操作):actor 必须是
  principal 或 executor;``principal_id`` 与 ``executor_id`` 同时为 None
  表示上下文不可归属,治理操作一律拒绝(fail-safe);
- 读面操作(``inspect`` / ``validate_plan``)与 ``submit_application``:
  任意已认证 actor(``actor.actor.principal_id`` 非空)通过——spec §3
  inspection 只返回投影,结构 P1 比照既有 GET status 路径按公开读处理;
- self-help:``leave`` 比对 ``member.participant_ref``,
  ``withdraw`` 比对 ``application.accountable_principal_ref``,字符串相等
  才通过,目标行缺失即拒绝;
- 一切 L5 授权均为纯断言,不授予任何 Resource 权限。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodBinding,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import ActorContext

# lifecycle 治理面操作(inspect 为读面,单列)。
LIFECYCLE_OWNER_OPS = frozenset(
    {
        "bind",
        "activate",
        "suspend",
        "resume",
        "terminate",
        "update_config",
        "compose",
        "detach",
    }
)
# lifecycle 读面操作:任意已认证 actor。
LIFECYCLE_READ_OPS = frozenset({"inspect"})

# group 治理面操作(leave 为 self-help,单列)。
GROUP_OWNER_OPS = frozenset(
    {
        "invite",
        "add_member",
        "suspend_member",
        "resume_member",
        "remove_member",
        "replace_member",
        "change_role",
    }
)
# group self-help 操作:比对 member.participant_ref。
GROUP_SELF_HELP_OPS = frozenset({"leave"})

# swarm 治理面操作(validate_plan 为读面,单列)。
SWARM_OWNER_OPS = frozenset(
    {
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
    }
)
# swarm 读面操作:任意已认证 actor。
SWARM_READ_OPS = frozenset({"validate_plan"})

# open 治理面操作(submit_application / withdraw 单列)。
OPEN_OWNER_OPS = frozenset(
    {
        "publish_opportunity",
        "pause_opportunity",
        "reopen_opportunity",
        "close_opportunity",
        "screen",
        "admit",
        "reject",
        "revoke_admission",
    }
)
# open 公开提交:任意已认证 actor。
OPEN_PUBLIC_OPS = frozenset({"submit_application"})
# open self-help 操作:比对 application.accountable_principal_ref。
OPEN_SELF_HELP_OPS = frozenset({"withdraw"})


@runtime_checkable
class MethodOpsAuthorizerProtocol(Protocol):
    """L5 方法操作授权接缝;服务层只依赖本协议,不依赖具体实现。

    ``principal_id`` / ``executor_id`` 是所属 Collet 的责任主体标识,
    由调用方(服务层)显式传入;``member`` / ``application`` 是操作目标
    行投影(duck-typed,分别暴露 ``participant_ref`` /
    ``accountable_principal_ref``),self-help 操作据其判定本人。
    各方法通过即返回 ``None``,拒绝即抛 ``ContextCollabValidationError``。
    """

    def check_lifecycle_op(
        self,
        op: str,
        binding: MethodBinding,
        actor: ActorContext,
        principal_id: str | None = None,
        executor_id: str | None = None,
    ) -> None: ...

    def check_group_op(
        self,
        op: str,
        binding: MethodBinding,
        actor: ActorContext,
        member: Any = None,
        principal_id: str | None = None,
        executor_id: str | None = None,
    ) -> None: ...

    def check_swarm_op(
        self,
        op: str,
        binding: MethodBinding,
        actor: ActorContext,
        principal_id: str | None = None,
        executor_id: str | None = None,
    ) -> None: ...

    def check_open_op(
        self,
        op: str,
        binding: MethodBinding,
        actor: ActorContext,
        application: Any = None,
        principal_id: str | None = None,
        executor_id: str | None = None,
    ) -> None: ...


def _actor_identity(actor: ActorContext) -> str:
    # 已认证 actor 身份恒在 ActorContext.actor.principal_id 上。
    return actor.actor.principal_id


def _deny(op: str, binding: MethodBinding, actor: ActorContext) -> None:
    actor_id = _actor_identity(actor)
    raise ContextCollabValidationError(
        f"method op {op!r} is not allowed for actor {actor_id!r} "
        f"on method_binding {binding.method_binding_id!r}"
    )


def _is_authenticated_identity(actor_id: str) -> bool:
    # 空 identity 视为未认证。
    return bool(actor_id)


def _is_owner(
    actor_id: str, principal_id: str | None, executor_id: str | None
) -> bool:
    # 沿 _is_principal / _is_executor 先例:字符串精确相等;
    # principal_id / executor_id 为 None 时该侧不参与判定。
    if principal_id is not None and actor_id == principal_id:
        return True
    if executor_id is not None and actor_id == executor_id:
        return True
    return False


def _require_authenticated(
    op: str, binding: MethodBinding, actor: ActorContext
) -> None:
    """读面/公开提交的裁决:任意已认证 actor 通过,空 identity 拒绝。"""
    if _is_authenticated_identity(_actor_identity(actor)):
        return
    _deny(op, binding, actor)


def _require_owner(
    op: str,
    binding: MethodBinding,
    actor: ActorContext,
    principal_id: str | None,
    executor_id: str | None,
) -> None:
    """治理面操作的裁决:actor 必须 principal 或 executor;

    principal_id/executor_id 双 None 表示上下文不可归属,任何 actor
    (含名义 owner)都拒绝——无责任主体的上下文不能授权治理操作。
    """
    actor_id = _actor_identity(actor)
    if _is_authenticated_identity(actor_id) and _is_owner(
        actor_id, principal_id, executor_id
    ):
        return
    _deny(op, binding, actor)


def _require_self_help(
    op: str,
    binding: MethodBinding,
    actor: ActorContext,
    target: Any,
    target_attr: str,
) -> None:
    """self-help 操作的裁决:actor 与目标行存储标识字符串精确相等。

    目标行缺失(None)或缺少目标字段即拒绝;garbage-in 不放行。
    """
    actor_id = _actor_identity(actor)
    stored_ref = getattr(target, target_attr, None) if target is not None else None
    if (
        _is_authenticated_identity(actor_id)
        and stored_ref is not None
        and actor_id == stored_ref
    ):
        return
    _deny(op, binding, actor)


class StructuralMethodOpsAuthorizer:
    """P1 结构默认实现(spec §8.1);不继承协议,结构满足即可替换。

    纯断言、无状态:不建身份注册表,不读写 store;P2 的
    Membership/Grant/Policy Authority 实现经
    ``MethodOpsAuthorizerProtocol`` 接缝整体替换。
    """

    def check_lifecycle_op(
        self,
        op: str,
        binding: MethodBinding,
        actor: ActorContext,
        principal_id: str | None = None,
        executor_id: str | None = None,
    ) -> None:
        if op in LIFECYCLE_READ_OPS:
            # inspect 是只读投影:任意已认证 actor(比照 GET status 路径)。
            _require_authenticated(op, binding, actor)
            return
        _require_owner(op, binding, actor, principal_id, executor_id)

    def check_group_op(
        self,
        op: str,
        binding: MethodBinding,
        actor: ActorContext,
        member: Any = None,
        principal_id: str | None = None,
        executor_id: str | None = None,
    ) -> None:
        if op in GROUP_SELF_HELP_OPS:
            # leave:本人退出,比对 member.participant_ref。
            _require_self_help(op, binding, actor, member, "participant_ref")
            return
        _require_owner(op, binding, actor, principal_id, executor_id)

    def check_swarm_op(
        self,
        op: str,
        binding: MethodBinding,
        actor: ActorContext,
        principal_id: str | None = None,
        executor_id: str | None = None,
    ) -> None:
        if op in SWARM_READ_OPS:
            # validate_plan 是只读校验投影:任意已认证 actor。
            _require_authenticated(op, binding, actor)
            return
        _require_owner(op, binding, actor, principal_id, executor_id)

    def check_open_op(
        self,
        op: str,
        binding: MethodBinding,
        actor: ActorContext,
        application: Any = None,
        principal_id: str | None = None,
        executor_id: str | None = None,
    ) -> None:
        if op in OPEN_PUBLIC_OPS:
            # submit_application:任意已认证 actor 可投递。
            _require_authenticated(op, binding, actor)
            return
        if op in OPEN_SELF_HELP_OPS:
            # withdraw:本人撤回,比对 application.accountable_principal_ref。
            _require_self_help(
                op, binding, actor, application, "accountable_principal_ref"
            )
            return
        _require_owner(op, binding, actor, principal_id, executor_id)
