"""L5 Method lifecycle and composition operations.

Identity reads use the lower collaboration reader; missing top-level bindings
are recovered via an injected application port. Product parsing stays outside.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    MethodOpsAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
    validate_method_machine,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    resolve_transition,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    CollaborationReader,
    CollaborationRecord,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    MethodBinding,
    MethodKind,
    effective_max_compose_depth,
    parse_method_kinds,
    top_level_binding_id,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionEdgeState,
    CompositionRelation,
    MethodCompositionEdge,
    detect_cycle,
    validate_composition,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    METHOD_PROFILE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.contracts import (
    BindMethodCommand,
    ComposeMethodCommand,
    CompositionEdgeResult,
    MethodBindingResult,
    MethodInspectionResult,
    MethodLifecycleServiceProtocol,
    MethodOpAcceptedResult,
    MethodStateChangeCommand,
    UpdateBindingConfigCommand,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.initialization import (
    TopLevelBindingRecovery,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.runtime import (
    _now,
    _shape_of,
    current_axes,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
    StoreAxes,
)
from injector import inject

# spec §3.2 状态机事件 token(T2 注册表 method.*);一操作一事件。
_STATE_CHANGE_EVENTS = {
    "activate": "method.activate",
    "suspend": "method.suspend",
    "resume": "method.resume",
    "terminate": "method.terminate",
}

# 幂等键语义(§8.3)的"请求形态"字段:bind 重放比对排除审计四字段、
# version 与 state——重放时刻与生命周期推进是时间事实,不是请求差异;
# 同 ID 同请求形态返回已存在对象(现状),同 ID 异形态 409 语义冲突。
_BINDING_REQUEST_FIELDS = (
    "method_kind",
    "method_names",
    "profile_versions",
    "parent_scope_ref",
    "participant_ref",
    "role_binding_refs",
    "config",
    "bind_reason",
)

# compose 重放的"请求形态"字段:边的结构四要素 + 状态(重放只接受
# ACTIVE 边;已 DETACHED 的同 edge_id 重提交是形态冲突,须换新边)。
_EDGE_REQUEST_FIELDS = (
    "collab_context_id",
    "parent_binding",
    "child_binding",
    "relation_kind",
    "state",
)

# 顶层 terminate 门槛的根 Task 终态(spec §3.2/§12 第 10 条)。
# spec 用事件词汇 task.cancelled/task.accepted 表达,行存的 TaskStatus
# wire 值是 cancelled / done(task.accepted 经 G4 机器落为 done);两种
# 拼写都接受,读取层不做双词汇误杀。
_TERMINAL_COLLET_STATUSES = frozenset(
    {"task.cancelled", "task.accepted", "cancelled", "done"}
)

# 顶层 binding 的 bind_reason 取值集合(创建投影 / 自愈重建),顶层
# terminate 门槛据其判定;嵌套 bind(compose/delegate)无门槛。
_TOP_LEVEL_BIND_REASONS = frozenset({"create_context", "create_context_heal"})


def _profile_versions(method_names: tuple[str, ...]) -> dict[str, str]:
    """各核心方法的 Profile 版本:取自代码内注册表(代码即合同来源)。"""
    return {
        name: METHOD_PROFILE_REGISTRY[name].profile_version for name in method_names
    }


def _ancestor_chain_depth(parents: dict[str, list[str]], binding_id: str) -> int:
    """沿 active 边向上数祖先链长(存在既有环时防御性跳过已访问节点)。

    输出 = 从该 binding 到最远根的边数;compose 守卫已挡新环,健在图
    的链长上限即组合深度 cap,递归深度有界。
    """
    seen: set[str] = set()

    def walk(node: str) -> int:
        if node in seen:
            return 0
        seen.add(node)
        return max(
            (1 + walk(parent) for parent in parents.get(node, ())),
            default=0,
        )

    return walk(binding_id)


class MethodLifecycleService(MethodLifecycleServiceProtocol):
    """L5 公共合同生命周期用例实现;store/授权器/Collet 仓储全协议注入。"""

    @inject
    def __init__(
        self,
        store: MethodStoreProtocol,
        authorizer: MethodOpsAuthorizerProtocol,
        collab_repo: CollaborationReader,
        *,
        recovery: TopLevelBindingRecovery,
    ) -> None:
        self._recovery = recovery
        self._store = store
        self._authorizer = authorizer
        self._collab_repo = collab_repo

    # ------------------------------------------------------------------
    # 内部:加载与身份解析
    # ------------------------------------------------------------------

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

    def _resolve_owner_identity(
        self, collab_context_id: str, binding: MethodBinding
    ) -> tuple[str | None, str | None, CollaborationRecord | None]:
        """授权身份解析(spec §8.1):owner 标识取所属 Collet 的只读创建身份事实。

        ``MethodBinding`` 可能只携带 executor 侧 ``participant_ref``,
        不足以判定身份——principal 一定来自 Collet 行;Collet 行不可达
        (P1 仓储缝下的极端路径)时 principal 落 None、executor 回退
        binding.participant_ref(字符串相等比对沿 ``_is_executor`` 先例),
        双 None 由授权器 fail-safe 拒绝治理面操作。
        """
        record = self._collab_repo.get_current_status(collab_context_id)
        if record is None:
            return None, binding.participant_ref, None
        return record.owner_principal_id, record.designated_executor_id, record

    def _authorize(
        self,
        op: str,
        binding: MethodBinding,
        context: ActorContext,
        collab_context_id: str,
    ) -> CollaborationRecord | None:
        principal_id, executor_id, record = self._resolve_owner_identity(
            collab_context_id, binding
        )
        self._authorizer.check_lifecycle_op(
            op,
            binding,
            context,
            principal_id=principal_id,
            executor_id=executor_id,
        )
        return record

    @staticmethod
    def _check_terminate_gate(
        op: str, binding: MethodBinding, record: CollaborationRecord | None
    ) -> None:
        """顶层 terminate 门槛(spec §3.2/§12 第 10 条):仅 terminate 受限。

        activate / suspend / resume 是活上下文的常规生命周期治理,不受
        任务终态门槛约束(活 Collet 上可暂停/恢复顶层方法);嵌套绑定
        (bind_reason=compose/delegate)连 terminate 也不设门槛;顶层绑定
        仅在所属 Collet 的根 Task 为终态(done / cancelled)时放行 terminate;
        活上下文上拒绝,避免摧毁仍活协作的 L5 权威状态。Collet 行不可达或 Task 缺失一律不放行(fail-safe)。
        """
        if op != "terminate":
            return
        is_top_level = (
            binding.parent_scope_ref is None
            and binding.bind_reason in _TOP_LEVEL_BIND_REASONS
        )
        if not is_top_level:
            return
        task = record.task if record is not None else None
        status = task.status if task is not None else None
        if status not in _TERMINAL_COLLET_STATUSES:
            raise ContextCollabValidationError(
                f"top-level method binding {binding.method_binding_id!r} may "
                "only terminate after the owning collet reaches a terminal "
                f"task status (task.cancelled / task.accepted), got {status!r}"
            )

    def _apply_state_change(
        self,
        op: str,
        collab_context_id: str,
        method_binding_id: str,
        command: MethodStateChangeCommand,
        context: ActorContext,
    ) -> MethodOpAcceptedResult:
        """activate/suspend/resume/terminate 共用编排:

        加载 → G1 授权 → 顶层门槛(仅 terminate)→ G4 机器裁决 →
        换新冻结实例(version+1 + 审计四字段)→ replace 落库。
        """
        axes = current_axes()
        binding = self._load_binding(axes, collab_context_id, method_binding_id)
        record = self._authorize(op, binding, context, collab_context_id)
        self._check_terminate_gate(op, binding, record)
        event = _STATE_CHANGE_EVENTS[op]
        new_state = resolve_transition(
            METHOD_MACHINE_REGISTRY["method_binding"],
            binding.state.value,
            event,
            {},
        )
        if new_state is None:
            raise ContextCollabValidationError(
                f"method binding state {binding.state.value!r} does not "
                f"accept event {event!r}"
            )
        updated = replace(
            binding,
            state=BindingState.from_wire(new_state),
            version=binding.version + 1,
            transitioned_from=binding.state.value,
            transition_reason=command.reason or event,
            transitioned_by=context.actor.principal_id,
            transitioned_at=_now(),
        )
        self._store.replace_binding(axes, updated)
        return MethodOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            state=new_state,
            version=updated.version,
        )

    # ------------------------------------------------------------------
    # 生命周期操作族(spec §3.2)
    # ------------------------------------------------------------------

    def bind(
        self,
        collab_context_id: str,
        command: BindMethodCommand,
        context: ActorContext,
    ) -> MethodBindingResult:
        """嵌套/委托绑定用例:初始 BOUND(§12 第 2 条),须显式 activate。

        幂等键语义(§8.3):``method_binding_id`` 缺省服务端生成;
        同 ID 同请求形态返回已存在对象,异形态 409 语义冲突。
        """
        axes = current_axes()
        method_binding_id = command.method_binding_id or f"mb_{uuid4().hex[:12]}"
        bind_reason = command.bind_reason or "compose"
        method_kind = MethodKind.from_wire(command.method_kind).value
        method_names = tuple(kind.value for kind in parse_method_kinds(method_kind))
        # 经 from_dict 整形校验(method_kind 投影一致、config 结构等),
        # 垃圾入参在落库前拒绝。
        binding = MethodBinding.from_dict(
            {
                "method_binding_id": method_binding_id,
                "collab_context_id": collab_context_id,
                "parent_scope_ref": command.parent_scope_ref,
                "method_kind": method_kind,
                "method_names": list(method_names),
                "profile_versions": _profile_versions(method_names),
                "participant_ref": command.participant_ref,
                "config": command.config,
                "state": BindingState.BOUND.value,
                "version": 1,
                "bind_reason": bind_reason,
                "transitioned_from": "",
                "transition_reason": bind_reason,
                "transitioned_by": context.actor.principal_id,
                "transitioned_at": _now(),
            }
        )
        self._authorize("bind", binding, context, collab_context_id)
        if command.method_binding_id is not None:
            existing = self._store.get_binding(
                axes, method_binding_id, collab_context_id
            )
            if existing is not None:
                if _shape_of(existing.to_dict(), _BINDING_REQUEST_FIELDS) == _shape_of(
                    binding.to_dict(), _BINDING_REQUEST_FIELDS
                ):
                    # 重放成功:返回已存在对象(不重写审计/生命周期)。
                    return MethodBindingResult(binding=existing.to_dict())
                raise ContextCollabValidationError(
                    f"idempotent re-submit of method binding "
                    f"{method_binding_id!r} differs from the stored shape"
                )
        stored = self._store.upsert_binding(axes, binding)
        return MethodBindingResult(binding=stored.to_dict())

    def activate(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: MethodStateChangeCommand,
        context: ActorContext,
    ) -> MethodOpAcceptedResult:
        return self._apply_state_change(
            "activate", collab_context_id, method_binding_id, command, context
        )

    def suspend(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: MethodStateChangeCommand,
        context: ActorContext,
    ) -> MethodOpAcceptedResult:
        return self._apply_state_change(
            "suspend", collab_context_id, method_binding_id, command, context
        )

    def resume(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: MethodStateChangeCommand,
        context: ActorContext,
    ) -> MethodOpAcceptedResult:
        return self._apply_state_change(
            "resume", collab_context_id, method_binding_id, command, context
        )

    def terminate(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: MethodStateChangeCommand,
        context: ActorContext,
    ) -> MethodOpAcceptedResult:
        return self._apply_state_change(
            "terminate", collab_context_id, method_binding_id, command, context
        )

    def update_config(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: UpdateBindingConfigCommand,
        context: ActorContext,
    ) -> MethodOpAcceptedResult:
        """config 变更用例(§3.2 update):不改状态,乐观版本自增。

        新 config 经 ``from_dict`` round-trip 整形重校验(config 结构、
        ``max_compose_depth`` 1..64 等),非法形态在改写前拒绝;
        ``expected_version`` 与当前 version 不匹配即 409 语义冲突。
        """
        axes = current_axes()
        binding = self._load_binding(axes, collab_context_id, method_binding_id)
        self._authorize("update_config", binding, context, collab_context_id)
        if command.expected_version != binding.version:
            raise ContextCollabVersionConflict(
                f"method binding {method_binding_id!r} is at version "
                f"{binding.version}, expected {command.expected_version!r}"
            )
        candidate = MethodBinding.from_dict(
            {**binding.to_dict(), "config": command.config}
        )
        updated = replace(
            candidate,
            version=binding.version + 1,
            transition_reason="method.update_config",
            transitioned_by=context.actor.principal_id,
            transitioned_at=_now(),
        )
        self._store.replace_binding(axes, updated)
        return MethodOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            state=binding.state.value,
            version=updated.version,
        )

    # ------------------------------------------------------------------
    # inspection 投影(§5)
    # ------------------------------------------------------------------

    def inspect(
        self,
        collab_context_id: str,
        method_binding_id: str,
        context: ActorContext,
    ) -> MethodInspectionResult:
        """只读投影:binding + state summary + capabilities + conformance
        evidence;读面授权,任意已认证 actor 放行。

        读路径自愈(§6.4):store 未命中**确定性顶层 ID** 时静默重建
        ACTIVE 投影后照常投影,读路径不报错;其余缺失 ID 照旧
        ``ContextCollabNotFoundError``。
        """
        axes = current_axes()
        binding = self._store.get_binding(axes, method_binding_id, collab_context_id)
        if binding is None:
            if method_binding_id == top_level_binding_id(collab_context_id):
                binding = self._recovery.recover(collab_context_id, context, now=_now())
            else:
                raise ContextCollabNotFoundError(
                    f"method binding {method_binding_id!r} not found in "
                    f"collaboration context {collab_context_id!r}"
                )
        self._authorize("inspect", binding, context, collab_context_id)
        edges = self._store.list_composition_edges(axes, collab_context_id)
        active_edges = sum(
            1
            for edge in edges
            if binding.method_binding_id in (edge.parent_binding, edge.child_binding)
        )
        capabilities = {
            name: {
                "method_kind": profile.method_kind,
                "profile_version": profile.profile_version,
                "capabilities": list(profile.capabilities),
                "primitives_used": list(profile.primitives_used),
                "participation_model": profile.participation_model,
                "composable_relations": list(profile.composable_relations),
            }
            for name, profile in (
                (name, METHOD_PROFILE_REGISTRY.get(name))
                for name in binding.method_names
            )
            if profile is not None
        }
        # 注册期已校验一次,inspection 再跑一遍作 conformance evidence
        # 的当次事实(纯声明校验,开销可忽略)。
        validate_method_machine(METHOD_MACHINE_REGISTRY["method_binding"])
        return MethodInspectionResult(
            binding=binding.to_dict(),
            state_summary={
                "state": binding.state.value,
                "version": binding.version,
                "composition_edges_active": active_edges,
            },
            capabilities=capabilities,
            conformance_evidence={
                "machines_valid": True,
                "binding_state": binding.state.value,
                "audit": {
                    "transitioned_from": binding.transitioned_from,
                    "transitioned_at": binding.transitioned_at,
                },
            },
        )

    # ------------------------------------------------------------------
    # 组合关系操作族(§3.3)
    # ------------------------------------------------------------------

    def compose(
        self,
        collab_context_id: str,
        parent_binding_id: str,
        command: ComposeMethodCommand,
        context: ActorContext,
    ) -> CompositionEdgeResult:
        """组合边创建用例:矩阵 → 环 → 深度 → 落边(边限定同一 Collet 内)。

        父 binding 必须 ACTIVE(spec §4 通用规则:非 ACTIVE 拒绝副作用;
        terminated 后出边创建一律拒绝是其推论);环检测覆盖 active
        全图 + 新边;深度按 ``effective_max_compose_depth``(缺省 8,
        父 config 可覆盖)裁断:新边深度 = 父系最长祖先链 + 1。同
        (parent, child, relation) 的 ACTIVE 重复由 store 唯一性裁断把守。
        """
        axes = current_axes()
        parent = self._load_binding(axes, collab_context_id, parent_binding_id)
        self._authorize("compose", parent, context, collab_context_id)
        if parent.state is not BindingState.ACTIVE:
            raise ContextCollabValidationError(
                f"compose requires the parent method binding "
                f"{parent_binding_id!r} to be ACTIVE, got {parent.state.value!r}"
            )
        child = self._load_binding(axes, collab_context_id, command.child_binding_id)
        relation = CompositionRelation.from_wire(command.relation)
        validate_composition(parent.method_kind, relation, child.method_kind)

        edge_id = command.edge_id or f"edge_{uuid4().hex[:12]}"
        if command.edge_id is not None:
            existing = self._store.get_composition_edge(
                axes, edge_id, collab_context_id
            )
            if existing is not None:
                # 幂等重放:同 edge_id 同结构(且仍是 ACTIVE 边)返回
                # 既有对象;其余(含已 DETACHED 的同 edge_id)按
                # 形态冲突拒绝——DETACHED 边的重提须换新边 id。
                candidate_shape = (
                    collab_context_id,
                    parent.method_binding_id,
                    child.method_binding_id,
                    relation.value,
                    CompositionEdgeState.ACTIVE.value,
                )
                if (
                    _shape_of(existing.to_dict(), _EDGE_REQUEST_FIELDS)
                    == candidate_shape
                ):
                    return CompositionEdgeResult(edge=existing.to_dict())
                raise ContextCollabValidationError(
                    f"idempotent re-submit of composition edge {edge_id!r} "
                    "differs from the stored shape"
                )

        edges = self._store.list_composition_edges(axes, collab_context_id)
        # 环检测先于深度回溯:防脏数据下的递归发散(健在图新环本就被拦)。
        if detect_cycle(
            [(edge.parent_binding, edge.child_binding) for edge in edges]
            + [(parent.method_binding_id, child.method_binding_id)]
        ):
            raise ContextCollabValidationError(
                f"composition edge from {parent_binding_id!r} to "
                f"{command.child_binding_id!r} would introduce a cycle"
            )
        cap = effective_max_compose_depth(parent.config)
        parents_by_child: dict[str, list[str]] = {}
        for edge in edges:
            parents_by_child.setdefault(edge.child_binding, []).append(
                edge.parent_binding
            )
        depth = _ancestor_chain_depth(parents_by_child, parent_binding_id) + 1
        if depth > cap:
            raise ContextCollabValidationError(
                f"composition depth {depth} from parent {parent_binding_id!r} "
                f"would exceed max_compose_depth cap {cap}"
            )
        edge = MethodCompositionEdge(
            edge_id=edge_id,
            collab_context_id=collab_context_id,
            parent_binding=parent.method_binding_id,
            child_binding=child.method_binding_id,
            relation_kind=relation,
            state=CompositionEdgeState.ACTIVE,
            transitioned_from="",
            transition_reason=command.reason or relation.value,
            transitioned_by=context.actor.principal_id,
            transitioned_at=_now(),
        )
        stored = self._store.insert_composition_edge(axes, edge)
        return CompositionEdgeResult(edge=stored.to_dict())

    def detach(
        self,
        collab_context_id: str,
        edge_id: str,
        command: MethodStateChangeCommand,
        context: ActorContext,
    ) -> CompositionEdgeResult:
        """detach 用例:仅置 DETACHED,审计保留,不改边的端点/关系
        (复用 replace 只允许状态翻转;改端点须新边)。"""
        axes = current_axes()
        edge = self._store.get_composition_edge(axes, edge_id, collab_context_id)
        if edge is None:
            raise ContextCollabNotFoundError(
                f"composition edge {edge_id!r} not found in collaboration "
                f"context {collab_context_id!r}"
            )
        if edge.state is CompositionEdgeState.DETACHED:
            raise ContextCollabValidationError(
                f"composition edge {edge_id!r} is already detached"
            )
        parent = self._load_binding(axes, collab_context_id, edge.parent_binding)
        self._authorize("detach", parent, context, collab_context_id)
        updated = replace(
            edge,
            state=CompositionEdgeState.DETACHED,
            transitioned_from=edge.state.value,
            transition_reason=command.reason or "detach",
            transitioned_by=context.actor.principal_id,
            transitioned_at=_now(),
        )
        self._store.replace_composition_edge(axes, updated)
        return CompositionEdgeResult(edge=updated.to_dict())
