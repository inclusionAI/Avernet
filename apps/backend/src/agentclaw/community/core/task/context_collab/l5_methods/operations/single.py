"""Layer-owned Single/Group operations."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
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
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    BindMethodCommand,
    ComposeMethodCommand,
    CompositionEdgeResult,
    MethodStateChangeCommand,
    ProfileOpAcceptedResult,
    SingleDelegateCommand,
    SingleReplaceCommand,
    SingleSelectCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _shape_of,
    current_axes,
)
from agentclaw.community.core.task.context_collab.l5_methods.single_profile import (
    SelectionState,
    SingleSelection,
)

_SINGLE_MACHINE = METHOD_MACHINE_REGISTRY["single_selection"]

_SELECTION_REQUEST_FIELDS = (
    "method_binding_id",
    "scope_ref",
    "selected_agent_participant_ref",
    "selected_by_ref",
    "requirement_refs",
    "selection_evidence_refs",
    "valid_until",
    "transition_reason",
)


def _now() -> str:
    """UTC ISO 时间戳;与 lifecycle 服务同款,不注入时钟。"""
    return datetime.now(timezone.utc).isoformat()


def _resolve_single_state(from_state: str, event: str) -> str:
    """经 G4 机器裁决 single_selection 迁移;无出边即校验失败。"""
    new_state = resolve_transition(_SINGLE_MACHINE, from_state, event, {})
    if new_state is None:
        raise ContextCollabValidationError(
            f"single selection state {from_state!r} does not accept event {event!r}"
        )
    return new_state


def _require_agent_participant_ref(value: str) -> str:
    """RFC-301 §9:Query 结果不自动成为 selection——Select/Replace 必须
    显式携带被选 agent 参与者引用。"""
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"agent_participant_ref must be a non-empty string, got: "
            f"{value!r}; a Single selection must explicitly carry the "
            "selected agent participant reference (a Query result never "
            "becomes a selection by itself)"
        )
    return value


def _require_scope_ref(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"scope_ref must be a non-empty string, got: {value!r}"
        )
    return value


class SingleOperations:
    def _select_event_audit(
        self, command_reason: str, actor: ActorContext, now: str
    ) -> dict:
        """PROPOSED→ACTIVE 段的审计四字段值(select/replace 新选择共用)。"""
        return {
            "transitioned_from": "PROPOSED",
            "transition_reason": command_reason or "single.select",
            "transitioned_by": actor.actor.principal_id,
            "transitioned_at": now,
        }

    def select(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: SingleSelectCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """Select 用例:机器裁决 PROPOSED─single.select→ACTIVE,一次入账。

        命令面强制两条:agent 引用显式非空(RFC-301 断言 3)、scope 引用
        非空;"同 binding + scope 至多一个 ACTIVE"由 store 插入时强制
        (断言 1)。幂等键语义(§8.3,与 bind 同款):``selection_id`` 是
        命令侧幂等键——同 ID 重放先按"请求形态"白名单比对(见
        ``_SELECTION_REQUEST_FIELDS``;服务时间戳是时间事实不计入),
        同形态返回既有已受理对象、不新增行,异形态按 409 语义拒绝。
        """
        agent_ref = _require_agent_participant_ref(command.agent_participant_ref)
        scope_ref = _require_scope_ref(command.scope_ref)
        axes = current_axes()
        binding = self._load_binding(axes, collab_context_id, method_binding_id)
        self._authorize("select", binding, context, collab_context_id)
        self._require_active_binding("select", binding)
        new_state = _resolve_single_state("PROPOSED", "single.select")
        now = _now()
        selection = SingleSelection.from_dict(
            {
                "selection_id": command.selection_id or f"sel_{uuid4().hex[:12]}",
                "method_binding_id": method_binding_id,
                "scope_ref": scope_ref,
                "selected_agent_participant_ref": agent_ref,
                "selected_by_ref": context.actor.principal_id,
                "requirement_refs": list(command.requirement_refs),
                "selection_evidence_refs": list(command.selection_evidence_refs),
                "state": new_state,
                "valid_from": now,
                "valid_until": command.valid_until,
                **self._select_event_audit(command.reason, context, now),
            }
        )
        if command.selection_id is not None:
            existing = self._store.get_selection(
                axes, command.selection_id, method_binding_id, collab_context_id
            )
            if existing is not None:
                # §8.3 幂等重放(与 bind 同款):同 ID 同"请求形态"返回
                # 既有已受理对象(不重写审计、不新增行);异形态 409 语义。
                if _shape_of(
                    existing.to_dict(), _SELECTION_REQUEST_FIELDS
                ) == _shape_of(selection.to_dict(), _SELECTION_REQUEST_FIELDS):
                    return ProfileOpAcceptedResult(
                        accepted=True,
                        collab_context_id=collab_context_id,
                        method_binding_id=method_binding_id,
                        object_kind="single_selection",
                        object_id=existing.selection_id,
                        state=existing.state.value,
                    )
                raise ContextCollabValidationError(
                    f"idempotent re-submit of single selection "
                    f"{command.selection_id!r} differs from the stored shape"
                )
        stored = self._store.insert_selection(axes, selection, collab_context_id)
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="single_selection",
            object_id=stored.selection_id,
            state=stored.state.value,
        )

    def replace(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: SingleReplaceCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """Replace 用例:存储锁内一次落定旧 ACTIVE→REPLACED / 新选择→ACTIVE。

        前置必须有该 (binding, scope) 的现役 selection,缺失即
        ``ContextCollabNotFoundError``;旧选择经 ``single.replace`` 裁决、
        新选择经 ``single.select`` 裁定;旧换手瞬间的并发冲突由 store
        的现役身份比对仲裁(``ContextCollabVersionConflict``,409 语义)。
        结果对象描述新 ACTIVE selection(当前生效的被选 agent)。
        """
        agent_ref = _require_agent_participant_ref(command.agent_participant_ref)
        scope_ref = _require_scope_ref(command.scope_ref)
        axes = current_axes()
        binding = self._load_binding(axes, collab_context_id, method_binding_id)
        self._authorize("replace", binding, context, collab_context_id)
        self._require_active_binding("replace", binding)
        old = self._store.active_selection(
            axes, method_binding_id, scope_ref, collab_context_id
        )
        if old is None:
            raise ContextCollabNotFoundError(
                f"no ACTIVE single selection for scope {scope_ref!r} on "
                f"method binding {method_binding_id!r} to replace"
            )
        replaced_state = _resolve_single_state(old.state.value, "single.replace")
        active_state = _resolve_single_state("PROPOSED", "single.select")
        now = _now()
        old_replaced = replace(
            old,
            state=SelectionState.from_wire(replaced_state),
            transitioned_from=old.state.value,
            transition_reason=command.reason or "single.replace",
            transitioned_by=context.actor.principal_id,
            transitioned_at=now,
        )
        new_selection = SingleSelection.from_dict(
            {
                "selection_id": f"sel_{uuid4().hex[:12]}",
                "method_binding_id": method_binding_id,
                "scope_ref": scope_ref,
                "selected_agent_participant_ref": agent_ref,
                "selected_by_ref": context.actor.principal_id,
                "requirement_refs": [],
                "selection_evidence_refs": [],
                "state": active_state,
                "valid_from": now,
                # 换手不重定义作用域的生效窗口:沿用既有选择的 valid_until。
                "valid_until": old.valid_until,
                **self._select_event_audit(command.reason, context, now),
            }
        )
        self._store.replace_selection(
            axes, old_replaced, new_selection, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="single_selection",
            object_id=new_selection.selection_id,
            state=active_state,
        )

    def release(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: MethodStateChangeCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """Release 用例:ACTIVE─single.release→RELEASED。

        命令面无 scope 参数(协议 §5):release 是 binding 级操作——释放
        该 binding 名下全部现役 selection(各对象独立落审计四字段);
        没有任何现役 selection 即 ``ContextCollabNotFoundError``。结果
        对象描述插入序上第一个被释放的 selection。
        """
        axes = current_axes()
        binding = self._load_binding(axes, collab_context_id, method_binding_id)
        self._authorize("release", binding, context, collab_context_id)
        self._require_active_binding("release", binding)
        actives = [
            selection
            for selection in self._store.list_selections(
                axes, method_binding_id, collab_context_id
            )
            if selection.state is SelectionState.ACTIVE
        ]
        if not actives:
            raise ContextCollabNotFoundError(
                f"no ACTIVE single selection on method binding "
                f"{method_binding_id!r} to release"
            )
        new_state = _resolve_single_state(SelectionState.ACTIVE.value, "single.release")
        released_ids: list[str] = []
        for active in actives:
            released = replace(
                active,
                state=SelectionState.from_wire(new_state),
                transitioned_from=active.state.value,
                transition_reason=command.reason or "single.release",
                transitioned_by=context.actor.principal_id,
                transitioned_at=_now(),
            )
            self._store.update_selection_state(axes, released, collab_context_id)
            released_ids.append(released.selection_id)
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="single_selection",
            object_id=released_ids[0],
            state=new_state,
        )

    def delegate(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: SingleDelegateCommand,
        context: ActorContext,
    ) -> CompositionEdgeResult:
        """Delegate 便捷操作:嵌套 binding + delegate_to 边(spec §4.1)。

        编排复用 Task 8:lifecycle.bind 创建嵌套 BOUND binding
        (bind_reason="delegate",幂等键 ``child_binding_id``),随后
        lifecycle.compose 落 ``delegate-to`` 边(幂等键 ``edge_id``)。
        组合矩阵(NEST/SUPPORT/DELEGATE_TO → 任意核心方法)、环检测
        与深度上限全部由 compose 把守;bind/compose 的授权面(owner)
        与 delegate 自身一致。上游 selection 不被触碰(RFC-301 断言 5)。
        """
        axes = current_axes()
        binding = self._load_binding(axes, collab_context_id, method_binding_id)
        self._authorize("delegate", binding, context, collab_context_id)
        self._require_active_binding("delegate", binding)
        bound = self._lifecycle.bind(
            collab_context_id,
            BindMethodCommand(
                method_binding_id=command.child_binding_id,
                method_kind=command.method_kind,
                # Task-7 遗留裁决(coordinator 已定,不再重议):
                # parent_scope_ref=None 合法——语义是"不带作用域约束的
                # 委托",与 bind 命令的 parent_scope_ref: str | None 一致,
                # 嵌套 binding 原样落 None,不在此回填。
                parent_scope_ref=command.parent_scope_ref,
                config=command.config,
                bind_reason="delegate",
            ),
            context,
        )
        child_binding_id = bound.binding["method_binding_id"]
        return self._lifecycle.compose(
            collab_context_id,
            method_binding_id,
            ComposeMethodCommand(
                relation="delegate-to",
                child_binding_id=child_binding_id,
                child_method_kind=command.method_kind,
                edge_id=command.edge_id,
                reason=command.reason,
            ),
            context,
        )
