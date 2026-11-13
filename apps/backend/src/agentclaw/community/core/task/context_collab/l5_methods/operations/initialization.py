"""L5 top-level initialization from resolved Method facts, independent of products."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    GroupMemberBinding,
    GroupRoster,
    MemberState,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    MethodBinding,
    parse_method_kinds,
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
    StoreAxes,
)


@dataclass(frozen=True)
class ResolvedMethodSpec:
    method_kind: str
    profile_versions: dict[str, str]
    role_binding_refs: tuple[str, ...]
    principal_id: str
    executor_id: str | None


class TopLevelBindingRecovery(Protocol):
    """Application recovery supplies a missing deterministic binding to L5.

    Must retain existing bindings (including terminated ones), propagate errors,
    and recover only within the requested context. L5 never interprets products.
    """

    def recover(
        self, collab_context_id: str, actor: ActorContext, *, now: str
    ) -> MethodBinding: ...


_TOP_LEVEL_BIND_REASONS = frozenset({"create_context", "create_context_heal"})


def build_top_level_binding(
    collab_context_id: str,
    spec: ResolvedMethodSpec,
    actor: ActorContext,
    *,
    now: str,
    bind_reason: str,
    reason: str,
) -> MethodBinding:
    """按 spec §3.2/§12 第 2 条构造顶层 ACTIVE 绑定(创建投影/自愈共用)。

    顶层绑定直接以 ACTIVE 入存储(Collet 即刻是活跃上下文,"创建即可
    协作");``config`` 留空,组合深度缺省 8 由读取侧
    ``effective_max_compose_depth`` 取生效值,不在投影层回填。``reason``
    是审计来源说明:创建投影与自愈重建在审计上可区分。
    """
    method_names = tuple(kind.value for kind in parse_method_kinds(spec.method_kind))
    versions = spec.profile_versions
    return MethodBinding.from_dict(
        {
            "method_binding_id": top_level_binding_id(collab_context_id),
            "collab_context_id": collab_context_id,
            "method_kind": spec.method_kind,
            "method_names": list(method_names),
            "profile_versions": versions,
            "role_binding_refs": list(spec.role_binding_refs),
            "participant_ref": spec.executor_id,
            "config": {},
            "state": BindingState.ACTIVE.value,
            "version": 1,
            "bind_reason": bind_reason,
            "transitioned_from": "",
            "transition_reason": reason,
            "transitioned_by": actor.actor.principal_id,
            "transitioned_at": now,
        }
    )


def find_top_level_binding(
    store: MethodStoreProtocol, axes: StoreAxes, collab_context_id: str
) -> MethodBinding | None:
    """取该 Collet 既有的顶层绑定:无父作用域引用且 bind_reason 属顶层投影。"""
    for binding in store.list_bindings(axes, collab_context_id):
        if (
            binding.parent_scope_ref is None
            and binding.bind_reason in _TOP_LEVEL_BIND_REASONS
        ):
            return binding
    return None


def _project_initial_roster(
    store: MethodStoreProtocol,
    axes: StoreAxes,
    collab_context_id: str,
    binding: MethodBinding,
    spec: ResolvedMethodSpec,
    actor: ActorContext,
    *,
    now: str,
) -> None:
    """Open+Group 顶层投影的 roster + 初始成员行(spec §9.2 / §4.2)。

    初始成员 = 应用解析后的 principal 与 executor(principal 与
    executor 身份重合时只落一行),直接以 ACTIVE 创建(不经 INVITED /
    迁移规则,``transitioned_from`` 落空串,沿绑定初始 BOUND 行先例)。
    roster 自 revision 1 起,每个成员插入恰一次 bump(与"每成员变更
    revision +1"语义一致)。roster 不建 L3 Membership、不扩资源权限
    (spec §4.2 G1 断言)。store 写失败如实上抛。
    """
    if "Group" not in binding.method_names:
        return
    roster = store.insert_roster(
        axes,
        GroupRoster.from_dict(
            {
                "roster_id": f"roster_{binding.method_binding_id}",
                "method_binding_id": binding.method_binding_id,
                "revision": 1,
            }
        ),
        collab_context_id,
    )
    # 有序去重:principal 先于 executor;身份重合只落一行。
    participants: dict[str, str] = {}
    participants.setdefault(spec.principal_id, "principal")
    if spec.executor_id is not None:
        participants.setdefault(spec.executor_id, "executor")
    for index, (participant_id, role) in enumerate(participants.items()):
        member = GroupMemberBinding.from_dict(
            {
                "group_member_binding_id": (f"gm_{role}_{binding.method_binding_id}"),
                "method_binding_id": binding.method_binding_id,
                "participant_ref": participant_id,
                "address": "",
                "role_binding_refs": [],
                "state": MemberState.ACTIVE.value,
                "joined_at": now,
                "left_at": None,
                "transitioned_from": "",
                "transition_reason": "create_context",
                "transitioned_by": actor.actor.principal_id,
                "transitioned_at": now,
            }
        )
        # roster 自 revision 1 起每插入恰一次 bump:第 index 个成员的
        # 期望 revision = 1 + index,无需在投影内回读。
        store.insert_member(axes, member, roster.revision + index, collab_context_id)


def initialize_top_level_binding(
    store: MethodStoreProtocol,
    axes: StoreAxes,
    collab_context_id: str,
    spec: ResolvedMethodSpec,
    actor: ActorContext,
    *,
    now: str,
) -> MethodBinding:
    """create_context 成功后的顶层 ACTIVE 投影(spec §9.2)。

    由 ``ResolvedMethodSpec`` 单向派生,不回写创建合同;同一 Collet 的
    重复投影幂等返回既有权威对象(不重写审计、不复活已终止对象)。
    Group 面向的顶层(Open + Group)在创建投影时同步补挂 roster +
    初始成员行(principal/executor ACTIVE,§4.2);Single 顶层不建
    roster。store 写失败如实上抛(内存写基本不会失败,但失败绝不让
    "有 Collet 无 binding"的响应伪装成功)。
    """
    existing = find_top_level_binding(store, axes, collab_context_id)
    if existing is not None:
        return existing
    binding = build_top_level_binding(
        collab_context_id,
        spec,
        actor,
        now=now,
        bind_reason="create_context",
        reason="top-level projection",
    )
    stored = store.upsert_binding(axes, binding)
    _project_initial_roster(
        store, axes, collab_context_id, stored, spec, actor, now=now
    )
    return stored
