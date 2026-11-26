"""L5 方法存储 single selection 切片(Task 9,spec §4.1 / RFC-301)。

键位纪律:selection 键五段,<对象id> 段是 ``(method_binding_id,
selection_id)`` 复合——selection_id 只在 binding 域内充当幂等键;
``SingleSelection`` 值对象连 collab_context_id 也不携带,各方法的
``collab_context_id`` 一律显式入参,两个协作上下文可合法持有同 id
binding,各自名下的 selection 互不可见、互不占用 ACTIVE 唯一位。
store 层强制同 context 内 (method_binding_id, scope_ref) 至多一个
ACTIVE(RFC-301 §9 断言 1)。方法体自 ``method_store.py`` 原样搬迁
(纯文件拆分,零行为变更)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.l5_methods.single_profile import (
    SelectionState,
    SingleSelection,
)

if TYPE_CHECKING:  # pragma: no cover - 仅供注解,type check 期解析
    from agentclaw.community.core.task.context_collab.persistence.method_store import (
        StoreAxes,
    )

_SelectionKey = tuple[str, str, str, str, str]


class SingleSelectionSlice:
    """``MethodStoreProtocol`` 的 single selection 切片实现。"""

    def insert_selection(
        self,
        axes: "StoreAxes",
        selection: SingleSelection,
        collab_context_id: str,
    ) -> SingleSelection:
        """幂等插入 + ACTIVE 唯一断言(同 binding + scope 至多一个 ACTIVE)。

        同键(tenant, env, collab_context_id, method_binding_id,
        selection_id)同 ``to_dict`` 返回既有实例;同键异形态重复提交抛
        ``ContextCollabValidationError``。新插入对象若为 ACTIVE 且该
        context 内 (binding, scope) 已有 ACTIVE selection,抛校验错点名
        占用(RFC-301 §9 断言 1 的存储层强制);非 ACTIVE 对象
        (REPLACED/RELEASED)保留审计、不受唯一断言限制。
        """
        key = self._selection_key(axes, collab_context_id, selection)
        with self._lock:
            existing = self._selections.get(key)
            if existing is not None:
                if existing.to_dict() == selection.to_dict():
                    return existing
                raise ContextCollabValidationError(
                    f"idempotent re-submit of single selection "
                    f"{selection.selection_id!r} differs from the stored shape"
                )
            if selection.state is SelectionState.ACTIVE:
                current = self._active_selection_locked(
                    axes, collab_context_id,
                    selection.method_binding_id, selection.scope_ref,
                )
                if current is not None:
                    raise ContextCollabValidationError(
                        f"an ACTIVE single selection already exists for scope "
                        f"{selection.scope_ref!r} on method binding "
                        f"{selection.method_binding_id!r} (held by "
                        f"{current.selection_id!r}); at most one ACTIVE "
                        f"selection per (method_binding_id, scope_ref)"
                    )
            self._selections[key] = selection
            return selection

    def get_selection(
        self,
        axes: "StoreAxes",
        selection_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> SingleSelection | None:
        key = (
            axes.avernet_tenant,
            axes.env,
            collab_context_id,
            method_binding_id,
            selection_id,
        )
        with self._lock:
            return self._selections.get(key)

    def list_selections(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[SingleSelection]:
        """按插入序返回该 axes + context + binding 下的全部 selection,
        不做状态过滤。"""
        with self._lock:
            return [
                selection
                for key, selection in self._selections.items()
                if key[0] == axes.avernet_tenant
                and key[1] == axes.env
                and key[2] == collab_context_id
                and key[3] == method_binding_id
            ]

    def active_selection(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        scope_ref: str,
        collab_context_id: str,
    ) -> SingleSelection | None:
        """取该 axes + context + (binding, scope) 的现役 selection;跨 scope
        与跨 context 互不占用。"""
        with self._lock:
            return self._active_selection_locked(
                axes, collab_context_id, method_binding_id, scope_ref
            )

    def replace_selection(
        self,
        axes: "StoreAxes",
        old: SingleSelection,
        new: SingleSelection,
        collab_context_id: str,
    ) -> None:
        """锁内原子换手对(RFC-301 §9 断言 4:单一、可排序事件)。

        前置:``old`` / ``new`` 必须同 (method_binding_id, scope_ref) 且
        selection_id 相异(同 id 覆写走 ``update_selection_state``)。锁内
        裁决:``old`` 的键必须仍在库,否则 ``ContextCollabNotFoundError``;
        该 (binding, scope) 的现役 selection 必须仍以 ``old.selection_id``
        持有,否则 ``ContextCollabVersionConflict``(409 语义)。裁决通过
        后一次写两条:``old`` 键位落其 REPLACED 实例、``new`` 键位落其
        ACTIVE 实例——新 ACTIVE 顶替 old 的唯一占位。
        """
        if (
            new.method_binding_id != old.method_binding_id
            or new.scope_ref != old.scope_ref
        ):
            raise ContextCollabValidationError(
                f"replace_selection requires the same (method_binding_id, "
                f"scope_ref): old={old.method_binding_id!r}/{old.scope_ref!r}, "
                f"new={new.method_binding_id!r}/{new.scope_ref!r}"
            )
        if new.selection_id == old.selection_id:
            raise ContextCollabValidationError(
                f"replace_selection requires a distinct selection_id for the "
                f"new selection, got {new.selection_id!r} twice"
            )
        old_key = self._selection_key(axes, collab_context_id, old)
        with self._lock:
            if old_key not in self._selections:
                raise ContextCollabNotFoundError(
                    f"single selection {old.selection_id!r} not found on "
                    f"method binding {old.method_binding_id!r} in "
                    f"collaboration context {collab_context_id!r}"
                )
            current = self._active_selection_locked(
                axes, collab_context_id,
                old.method_binding_id, old.scope_ref,
            )
            if current is None or current.selection_id != old.selection_id:
                raise ContextCollabVersionConflict(
                    f"the ACTIVE single selection for scope "
                    f"{old.scope_ref!r} on method binding "
                    f"{old.method_binding_id!r} is no longer "
                    f"{old.selection_id!r}"
                )
            self._selections[old_key] = old
            self._selections[
                self._selection_key(axes, collab_context_id, new)
            ] = new

    def update_selection_state(
        self,
        axes: "StoreAxes",
        selection: SingleSelection,
        collab_context_id: str,
    ) -> None:
        """覆盖式换新实例(release/replace 转换经其落值);键必须已存在,
        否则抛 ``ContextCollabNotFoundError``;改回 ACTIVE 时同样受同
        (binding, scope) 至多一个 ACTIVE 的唯一断言约束。"""
        key = self._selection_key(axes, collab_context_id, selection)
        with self._lock:
            if key not in self._selections:
                raise ContextCollabNotFoundError(
                    f"single selection {selection.selection_id!r} not found "
                    f"on method binding {selection.method_binding_id!r} in "
                    f"collaboration context {collab_context_id!r}"
                )
            if selection.state is SelectionState.ACTIVE:
                current = self._active_selection_locked(
                    axes, collab_context_id,
                    selection.method_binding_id, selection.scope_ref,
                )
                if (
                    current is not None
                    and current.selection_id != selection.selection_id
                ):
                    raise ContextCollabValidationError(
                        f"an ACTIVE single selection already exists for scope "
                        f"{selection.scope_ref!r} on method binding "
                        f"{selection.method_binding_id!r} (held by "
                        f"{current.selection_id!r}); at most one ACTIVE "
                        f"selection per (method_binding_id, scope_ref)"
                    )
            self._selections[key] = selection

    # ------------------------------------------------------------------
    # 内部:键构造与锁内扫描
    # ------------------------------------------------------------------

    @staticmethod
    def _selection_key(
        axes: "StoreAxes", collab_context_id: str, selection: SingleSelection
    ) -> _SelectionKey:
        # selection 键位锚定所属协作上下文(T9 fix):``SingleSelection``
        # 值对象不携带 collab_context_id,由调用方显式传入;``selection_id``
        # 只在 binding 域内充当幂等键,故 id 段取 (binding, selection)
        # 复合——两个协作上下文可合法持有同 id 的 binding,各自名下的
        # selection 互不可见、互不占用 ACTIVE 唯一位。
        return (
            axes.avernet_tenant,
            axes.env,
            collab_context_id,
            selection.method_binding_id,
            selection.selection_id,
        )

    def _active_selection_locked(
        self,
        axes: "StoreAxes",
        collab_context_id: str,
        method_binding_id: str,
        scope_ref: str,
    ) -> SingleSelection | None:
        """锁内扫描:该 axes + context + (binding, scope) 的现役 selection。"""
        for key, selection in self._selections.items():
            if (
                key[0] == axes.avernet_tenant
                and key[1] == axes.env
                and key[2] == collab_context_id
                and key[3] == method_binding_id
                and selection.scope_ref == scope_ref
                and selection.state is SelectionState.ACTIVE
            ):
                return selection
        return None
