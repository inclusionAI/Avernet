"""Layer-owned Single/Group operations."""

from __future__ import annotations

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    MethodOpsAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    CollaborationReader,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    GroupMemberBinding,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    MethodBinding,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    MethodLifecycleServiceProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
    StoreAxes,
)
from injector import inject


class SingleGroupServiceBase:
    @inject
    def __init__(
        self,
        store: MethodStoreProtocol,
        authorizer: MethodOpsAuthorizerProtocol,
        collab_repo: CollaborationReader,
        lifecycle: MethodLifecycleServiceProtocol,
    ) -> None:
        self._store = store
        self._authorizer = authorizer
        self._collab_repo = collab_repo
        self._lifecycle = lifecycle

    def _load_binding(
        self, axes: StoreAxes, collab_context_id: str, method_binding_id: str
    ) -> MethodBinding:
        binding = self._store.get_binding(axes, method_binding_id, collab_context_id)
        if binding is None:
            raise ContextCollabNotFoundError(
                f"method binding {method_binding_id!r} not found in "
                f"collaboration context {collab_context_id!r}"
            )
        return binding

    def _authorize(
        self,
        op: str,
        binding: MethodBinding,
        context: ActorContext,
        collab_context_id: str,
        member: GroupMemberBinding | None = None,
    ) -> None:
        """G1 身份解析 + 授权(spec §8.1):owner 标识取所属 Collet 的
        CollaborationReader 提供的创建身份事实;
        Collet 行不可达时 principal 落 None、executor 回退
        binding.participant_ref,双 None 由授权器 fail-safe 拒绝。
        group 的 self-help 操作(leave)由授权器比对 ``member`` 目标行
        (调用方先定位行再授权),owner 面单传 ``member=None``。"""
        record = self._collab_repo.get_current_status(collab_context_id)
        if record is None:
            principal_id, executor_id = None, binding.participant_ref
        else:
            executor_id = record.designated_executor_id
            principal_id = record.owner_principal_id
        self._authorizer.check_group_op(
            op,
            binding,
            context,
            member=member,
            principal_id=principal_id,
            executor_id=executor_id,
        )

    @staticmethod
    def _require_active_binding(op: str, binding: MethodBinding) -> None:
        """RF#2(spec §4 通用规则):binding 非 ACTIVE 时副作用操作一律拒绝。"""
        if binding.state is not BindingState.ACTIVE:
            raise ContextCollabValidationError(
                f"method op {op!r} requires the method binding "
                f"{binding.method_binding_id!r} to be ACTIVE, got "
                f"{binding.state.value!r}"
            )
