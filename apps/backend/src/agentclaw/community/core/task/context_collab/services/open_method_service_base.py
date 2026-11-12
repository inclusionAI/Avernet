"""L5 Open 方法服务共享骨架(spec §4.4 / §8):构造注入与治理面前置。

``OpenMethodService`` 与 Swarm 服务同款按 1000 行 CI 边界拆片
(spec §5):机会面操作在 ``open_opportunity_ops.py``,申请与录取面
操作在 ``open_application_ops.py``,本模块持有共用的构造注入
(``@inject``)、Collet 身份解析、Swift 前置断面(结构 Open binding
守卫 + RF#2 + G1 授权)与机会/申请加载助手;切片不互相 import。

RF#2 与结构守卫在 ``_gate`` 落地:``"Open"`` 必须在 ``method_names``
中(顶层 "Open + Group" 或嵌套 "Open" 均满足)——Group/Swarm binding
上的 Open 剖面操作是结构漂移,直接拒绝;binding 非 ACTIVE 时副作用
操作一律拒绝(spec §4 通用规则)。授权经 G1
``MethodOpsAuthorizerProtocol.check_open_op``:七个治理面操作 owner
面;``submit_application`` 公开面(任意已认证 actor);``withdraw``
是 self-help(与目标行 ``accountable_principal_ref`` 字符串相等才
放行,调用方先定位目标行再授权,沿 group leave 先例)。P1 无身份
注册表,字符串相等只保证"声明一致"(G1 §8.1)。
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
from agentclaw.community.core.task.context_collab.l5_methods.open_profile import (
    OpenOpportunity,
    ParticipationApplication,
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


def _parse_timestamp(value: str, owner: str, field: str) -> datetime:
    """ISO 时间串解析;坏格式抛校验错误;naive 串按 UTC 对待。"""
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ContextCollabValidationError(
            f"{owner}.{field} must be an ISO timestamp string, got: "
            f"{value!r}"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


class OpenMethodServiceBase:
    """Open 服务共用骨架:store / 授权器 / Collet 仓储注入 + 机会/申请
    加载与治理面前置(``_gate``);切片 mixin 经 ``self._*`` 共享访问。"""

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
        application: ParticipationApplication | None = None,
    ) -> None:
        """G1 身份解析 + 授权(spec §8.1):身份取所属 Collet 的
        context_spec(principal_ref.principal_id / executor_ref.agent_id),
        与 Single/Group/Swarm 服务同款;self-help 操作(withdraw)由
        授权器比对 ``application.accountable_principal_ref``,调用方
        先定位目标行再授权。"""
        record = self._collab_repo.get_current_status(collab_context_id)
        if record is None:
            principal_id, executor_id = None, binding.participant_ref
        else:
            spec = record.collet.context_spec
            executor_id = spec.executor_ref.agent_id if spec.executor_ref else None
            principal_id = spec.principal_ref.principal_id
        self._authorizer.check_open_op(
            op,
            binding,
            context,
            application=application,
            principal_id=principal_id,
            executor_id=executor_id,
        )

    def _gate_preface(
        self,
        op: str,
        collab_context_id: str,
        method_binding_id: str,
    ) -> tuple[StoreAxes, MethodBinding]:
        """Open 治理面前置前段:加载 → 结构 Open binding 守卫 → RF#2
        (不含授权——self-help 面先定位目标行再授权)。"""
        axes = current_axes()
        binding = self._load_binding(
            axes, collab_context_id, method_binding_id
        )
        if "Open" not in binding.method_names:
            raise ContextCollabValidationError(
                f"method op {op!r} targets method binding "
                f"{method_binding_id!r} which does not declare Open in "
                f"method_names {list(binding.method_names)!r}"
            )
        if binding.state is not BindingState.ACTIVE:
            raise ContextCollabValidationError(
                f"method op {op!r} requires the method binding "
                f"{binding.method_binding_id!r} to be ACTIVE, got "
                f"{binding.state.value!r}"
            )
        return axes, binding

    def _gate(
        self,
        op: str,
        collab_context_id: str,
        method_binding_id: str,
        context: ActorContext,
    ) -> StoreAxes:
        """owner/公开面操作共用前置:preface → G1 授权;用例各自
        收尾机器裁决与写。"""
        axes, binding = self._gate_preface(
            op, collab_context_id, method_binding_id
        )
        self._authorize(op, binding, context, collab_context_id)
        return axes

    def _require_opportunity(
        self,
        axes: StoreAxes,
        collab_context_id: str,
        method_binding_id: str,
        opportunity_ref: str,
    ) -> OpenOpportunity:
        opportunity = self._store.get_opportunity(
            axes, opportunity_ref, method_binding_id, collab_context_id
        )
        if opportunity is None:
            raise ContextCollabNotFoundError(
                f"open opportunity {opportunity_ref!r} not found on "
                f"method binding {method_binding_id!r} in collaboration "
                f"context {collab_context_id!r}"
            )
        return opportunity

    def _require_application(
        self,
        axes: StoreAxes,
        collab_context_id: str,
        method_binding_id: str,
        application_ref: str,
    ) -> ParticipationApplication:
        application = self._store.get_application(
            axes, application_ref, method_binding_id, collab_context_id
        )
        if application is None:
            raise ContextCollabNotFoundError(
                f"open application {application_ref!r} not found on "
                f"method binding {method_binding_id!r} in collaboration "
                f"context {collab_context_id!r}"
            )
        return application
