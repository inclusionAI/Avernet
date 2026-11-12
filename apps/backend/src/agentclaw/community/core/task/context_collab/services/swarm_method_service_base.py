"""L5 Swarm 方法服务共享骨架(spec §4.3 / §8):构造注入与治理面前置。

``SwarmMethodService`` 按 1000 行 CI 边界拆片(spec §5 "按 1000 行
边界拆"):计划面操作在 ``swarm_plan_ops.py``,Stage 分配面操作在
``swarm_stage_ops.py``,本模块持有共用的构造注入(``@inject``)、
Collet 身份解析、Swift 前置断面(结构 Swarm binding 守卫 + RF#2 +
G1 授权)与计划加载/投影助手。Import 方向安全:切片不互相 import,
全部经本模块共享。

RF#2 与结构的 Swarm binding 守卫在 ``_gate`` 落地:``"Swarm"`` 必须在
``method_names`` 中——Group binding 上的 Swarm 剖面操作是结构漂移,
直接拒绝;binding 非 ACTIVE 时副作用操作一律拒绝(spec §4 通用规则)。
授权经 G1 ``MethodOpsAuthorizerProtocol``:owner 标识取所属 Collet 的
context_spec(principal_ref.principal_id / executor_ref.agent_id),
Collet 行不可达时 principal 落 None、executor 不可解析,授权器
fail-safe 拒绝(P1 无身份注册表)。
"""

from __future__ import annotations

from datetime import datetime, timezone

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    MethodOpsAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    MethodBinding,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    Stage,
    SwarmPlan,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
    StoreAxes,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.service_api import ActorContext
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    current_axes,
)


def _now() -> str:
    """UTC ISO 时间戳;与 lifecycle 服务同款,不注入时钟。"""
    return datetime.now(timezone.utc).isoformat()


class SwarmMethodServiceBase:
    """Swarm 服务共用骨架:store / 授权器 / Collet 仓储注入 + 计划加载
    与治理面前置(``_gate``);切片 mixin 经 ``self._*`` 共享访问。"""

    @inject
    def __init__(
        self,
        store: MethodStoreProtocol,
        authorizer: MethodOpsAuthorizerProtocol,
        collab_repo: ContextCollabRepositoryProtocol,
    ) -> None:
        self._store = store
        self._authorizer = authorizer
        self._collab_repo = collab_repo

    def _load_binding(
        self, axes: StoreAxes, collab_context_id: str, method_binding_id: str
    ) -> MethodBinding:
        binding = self._store.get_binding(
            axes, method_binding_id, collab_context_id
        )
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
    ) -> None:
        """G1 身份解析 + 授权(spec §8.1):与 Single/Group 服务同款,
        经 ``check_swarm_op`` 裁决(读面/治理面路由在授权器内部)。"""
        record = self._collab_repo.get_current_status(collab_context_id)
        if record is None:
            principal_id, executor_id = None, binding.participant_ref
        else:
            spec = record.collet.context_spec
            executor_id = spec.executor_ref.agent_id if spec.executor_ref else None
            principal_id = spec.principal_ref.principal_id
        self._authorizer.check_swarm_op(
            op,
            binding,
            context,
            principal_id=principal_id,
            executor_id=executor_id,
        )

    def _gate(
        self,
        op: str,
        collab_context_id: str,
        method_binding_id: str,
        context: ActorContext,
    ) -> StoreAxes:
        """Swarm 治理面操作共用前置:加载 → 结构 Swarm binding 守卫 →
        RF#2 → G1 授权;用例各自收尾机器裁决与写。"""
        axes = current_axes()
        binding = self._load_binding(
            axes, collab_context_id, method_binding_id
        )
        if "Swarm" not in binding.method_names:
            raise ContextCollabValidationError(
                f"method op {op!r} targets method binding "
                f"{method_binding_id!r} which does not declare Swarm in "
                f"method_names {list(binding.method_names)!r}"
            )
        if binding.state is not BindingState.ACTIVE:
            raise ContextCollabValidationError(
                f"method op {op!r} requires the method binding "
                f"{binding.method_binding_id!r} to be ACTIVE, got "
                f"{binding.state.value!r}"
            )
        self._authorize(op, binding, context, collab_context_id)
        return axes

    def _require_plan(
        self,
        axes: StoreAxes,
        collab_context_id: str,
        method_binding_id: str,
        swarm_plan_id: str,
    ) -> SwarmPlan:
        plan = self._store.get_plan(
            axes, swarm_plan_id, method_binding_id, collab_context_id
        )
        if plan is None:
            raise ContextCollabNotFoundError(
                f"swarm plan {swarm_plan_id!r} not found on method binding "
                f"{method_binding_id!r} in collaboration context "
                f"{collab_context_id!r}"
            )
        return plan

    def _plan_stages(
        self,
        axes: StoreAxes,
        collab_context_id: str,
        method_binding_id: str,
        swarm_plan_id: str,
    ) -> list[Stage]:
        """插入序返回计划名下全部 Stage(axes + ctx + binding 内过滤
        ``swarm_plan_ref``)。"""
        return [
            stage
            for stage in self._store.list_stages(
                axes, method_binding_id, collab_context_id
            )
            if stage.swarm_plan_ref == swarm_plan_id
        ]
