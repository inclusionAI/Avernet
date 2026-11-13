"""L5 方法存储 binding + composition edge 切片(Task 5 先期交付)。

键位纪律:binding 键四段 (avernet_tenant, env, collab_context_id,
method_binding_id);edge 键四段,edge_id 即域键。幂等裁断以
``to_dict()`` 逐字段相等为准;同键异形态的重复提交抛
``ContextCollabValidationError``;``replace_*`` 要求键已存在,不存在抛
``ContextCollabNotFoundError``;DETACHED 边保留审计、不物理删除。
方法体自 ``method_store.py`` 原样搬迁(纯文件拆分,零行为变更)。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    MethodBinding,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionEdgeState,
    MethodCompositionEdge,
)

if TYPE_CHECKING:  # pragma: no cover - 仅供注解,type check 期解析
    from agentclaw.community.core.task.context_collab.persistence.method_store import (
        StoreAxes,
    )

# 统一存储键四段:(avernet_tenant, env, collab_context_id, <对象id>)。
_StoreKey = tuple[str, str, str, str]


class BindingEdgeSlice:
    """``MethodStoreProtocol`` 的 binding 与 composition edge 两段实现。"""

    def upsert_binding(
        self, axes: "StoreAxes", binding: MethodBinding
    ) -> MethodBinding:
        """幂等 upsert:新键落库返回原实例;同键同 to_dict 返回既有
        存储实例;同键异形态重复提交抛 ``ContextCollabValidationError``。"""
        key = self._binding_key(axes, binding)
        with self._lock:
            existing = self._bindings.get(key)
            if existing is None:
                self._bindings[key] = binding
                return binding
            if existing.to_dict() == binding.to_dict():
                return existing
            raise ContextCollabValidationError(
                f"idempotent re-submit of method binding "
                f"{binding.method_binding_id!r} differs from the stored shape"
            )

    def get_binding(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        collab_context_id: str,
    ) -> MethodBinding | None:
        key = (axes.avernet_tenant, axes.env, collab_context_id, method_binding_id)
        with self._lock:
            return self._bindings.get(key)

    def replace_binding(self, axes: "StoreAxes", binding: MethodBinding) -> None:
        """覆盖式换新实例(服务层乐观版本经其为语义推进状态);键必须
        已存在,否则抛 ``ContextCollabNotFoundError``。"""
        key = self._binding_key(axes, binding)
        with self._lock:
            if key not in self._bindings:
                raise ContextCollabNotFoundError(
                    f"method binding {binding.method_binding_id!r} not found "
                    f"in collaboration context {binding.collab_context_id!r}"
                )
            self._bindings[key] = binding

    def list_bindings(
        self, axes: "StoreAxes", collab_context_id: str
    ) -> list[MethodBinding]:
        """按插入序返回该 axes + context 下的全部绑定,不做状态过滤。"""
        with self._lock:
            return [
                binding
                for key, binding in self._bindings.items()
                if self._key_in_context(key, axes, collab_context_id)
            ]

    def insert_composition_edge(
        self, axes: "StoreAxes", edge: MethodCompositionEdge
    ) -> MethodCompositionEdge:
        """幂等插入:同 edge id 同 to_dict 返回既有实例;同 edge id 异
        形态抛校验错;唯一性裁断:同 axes + context 内已有另一条 ACTIVE
        边占用同一 (parent_binding, child_binding, relation_kind) 时拒绝。"""
        key = self._edge_key(axes, edge)
        with self._lock:
            existing = self._edges.get(key)
            if existing is not None:
                if existing.to_dict() == edge.to_dict():
                    return existing
                raise ContextCollabValidationError(
                    f"idempotent re-submit of composition edge "
                    f"{edge.edge_id!r} differs from the stored shape"
                )
            for stored in self._context_edges(axes, edge.collab_context_id):
                # 键已保证 edge_id 不同(同 edge id 分支在上面处理),
                # 这里只需命中同 pair 的 ACTIVE 占用即拒绝。
                if (
                    stored.state is CompositionEdgeState.ACTIVE
                    and stored.parent_binding == edge.parent_binding
                    and stored.child_binding == edge.child_binding
                    and stored.relation_kind == edge.relation_kind
                ):
                    raise ContextCollabValidationError(
                        f"duplicate active composition relation for pair "
                        f"(parent_binding={edge.parent_binding!r}, "
                        f"child_binding={edge.child_binding!r}, "
                        f"relation_kind={edge.relation_kind.value!r}) in "
                        f"collaboration context {edge.collab_context_id!r}: "
                        f"already held by edge {stored.edge_id!r}"
                    )
            self._edges[key] = edge
            return edge

    def replace_composition_edge(
        self, axes: "StoreAxes", edge: MethodCompositionEdge
    ) -> None:
        """覆盖式换新实例;detach 路径经其把 state 改写为 DETACHED;
        键必须已存在,否则抛 ``ContextCollabNotFoundError``。"""
        key = self._edge_key(axes, edge)
        with self._lock:
            if key not in self._edges:
                raise ContextCollabNotFoundError(
                    f"composition edge {edge.edge_id!r} not found in "
                    f"collaboration context {edge.collab_context_id!r}"
                )
            self._edges[key] = edge

    def list_composition_edges(
        self,
        axes: "StoreAxes",
        collab_context_id: str,
        *,
        active_only: bool = True,
    ) -> list[MethodCompositionEdge]:
        """按插入序返回该 axes + context 下的边;``active_only`` 只保留
        ACTIVE,DETACHED 仅在显式关闭过滤时可见(审计保留)。"""
        with self._lock:
            edges = self._context_edges(axes, collab_context_id)
            if not active_only:
                return edges
            return [
                edge
                for edge in edges
                if edge.state is CompositionEdgeState.ACTIVE
            ]

    def get_composition_edge(
        self,
        axes: "StoreAxes",
        edge_id: str,
        collab_context_id: str,
    ) -> MethodCompositionEdge | None:
        key = (axes.avernet_tenant, axes.env, collab_context_id, edge_id)
        with self._lock:
            return self._edges.get(key)

    # ------------------------------------------------------------------
    # 内部:键构造与锁内扫描
    # ------------------------------------------------------------------

    @staticmethod
    def _binding_key(axes: "StoreAxes", binding: MethodBinding) -> _StoreKey:
        return (
            axes.avernet_tenant,
            axes.env,
            binding.collab_context_id,
            binding.method_binding_id,
        )

    @staticmethod
    def _edge_key(axes: "StoreAxes", edge: MethodCompositionEdge) -> _StoreKey:
        return (
            axes.avernet_tenant,
            axes.env,
            edge.collab_context_id,
            edge.edge_id,
        )

    def _context_edges(
        self, axes: "StoreAxes", collab_context_id: str
    ) -> list[MethodCompositionEdge]:
        """锁内扫描:同 axes + context 下的全部边,保持插入序。"""
        return [
            edge
            for key, edge in self._edges.items()
            if self._key_in_context(key, axes, collab_context_id)
        ]
