"""L5 Single + Group 方法剖面服务(spec §4.1/§4.2/§5)。

实现 ``GroupMethodOpsServiceProtocol`` 全段:Single 四操作(``select`` /
``replace`` / ``release`` / ``delegate``,Task 9)+ Group 八操作
(``invite`` / ``add_member`` / ``suspend_member`` / ``resume_member`` /
``leave`` / ``remove_member`` / ``replace_member`` / ``change_role``,
Task 10);整体满足 ``GroupMethodOpsServiceProtocol`` 的 runtime
isinstance 面。

- RF#2(spec §4 通用规则):binding 非 ACTIVE 时副作用操作一律拒绝
  (Single 四操作与 group 八操作同守卫);
- 状态转换一律经 G4 声明式机器(``METHOD_MACHINE_REGISTRY``
  ["single_selection"] / ["group_member"])裁决(``resolve_transition``
  同构,守卫空集直接喂入),机器本身就是非法迁移/幂等重放的天然防线
  (spec §8.2/§8.3);
- RFC-301 §9 五条一致性断言中服务面可强制的三条在此落实:Select 必须
  显式携带 agent 引用(Query 结果不自动成为 selection)、Replace 经
  ``replace_selection`` 在存储锁内一次落定旧→REPLACED / 新→ACTIVE
  (单一、可排序事件)、Delegate 只新增嵌套 binding + delegate_to 边
  而不触碰上游 selection;
- Group 语义(spec §4.2):roster 每成员变更恰一次 revision bump 且
  比对在存储锁内(RF #5,不匹配 409);invite 建 INVITED 行、不经
  迁移规则,拒绝已非终态在册的参与者;invite/add 同用
  ``insert_member``、其余成员操作经 ``update_member_state`` 换新
  实例;``replace_member`` 存储锁内原子双写旧→REPLACED / 新→ACTIVE
  单次 bump;``change_role`` 只写 ``role_binding_refs``(格式校验)
  不迁态、审计不覆写,revision +1;成员终态(LEFT/REMOVED/REPLACED)
  取自注册机器治理类别;
- 授权经 G1 ``check_group_op``:select/replace/release/delegate 不在
  T6 治理 frozenset 中,按默认治理桶路由——owner 放行、陌生拒绝
  (T6-minor-1 pin 测试随 Task 9 落地);group 七个治理操作(invite/
  add_member/suspend_member/resume_member/remove_member/replace_member/
  change_role)owner 面(principal/executor 字符串相等),``change_role``
  **不经**自助路径(成员自改 role_binding_refs 属提权,2026-11-12
  复审裁决收窄);``leave`` 是成员本人自助——与目标行
  ``participant_ref`` 字符串相等才放行;owner 面操作先过 RF#2 再过
  授权、self-help 面(leave)先定位目标行再授权;
- ``delegate`` 编排 = Task 8 lifecycle.bind(嵌套 BOUND binding,
  bind_reason="delegate")+ lifecycle.compose("delegate-to" 边),复用
  组合矩阵/环检测/深度上限/幂等键全套守卫:矩阵允许 Single 委托任意
  核心方法(ACCS-301 §7)。

roster 不建 L3 Membership、不扩任何资源权限(spec §4.2 G1 断言)。
审计四字段(§6.3)每次转换覆写于对象;时间一律 UTC ISO 字符串,
不入时钟依赖。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from injector import inject

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    MethodOpsAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.method_state_machines import (
    METHOD_MACHINE_REGISTRY,
)
from agentclaw.community.core.task.context_collab.g4_contracts_consistency.state_machine import (
    resolve_transition,
)
from agentclaw.community.core.task.context_collab.l5_methods.group_profile import (
    MEMBER_TERMINAL_STATES,
    GroupMemberBinding,
    GroupRoster,
    MemberState,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    MethodBinding,
)
from agentclaw.community.core.task.context_collab.l5_methods.single_profile import (
    SelectionState,
    SingleSelection,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    BindMethodCommand,
    ComposeMethodCommand,
    CompositionEdgeResult,
    GroupChangeRoleCommand,
    GroupMemberCommand,
    GroupReplaceMemberCommand,
    MethodLifecycleServiceProtocol,
    MethodStateChangeCommand,
    ProfileOpAcceptedResult,
    SingleDelegateCommand,
    SingleReplaceCommand,
    SingleSelectCommand,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    MethodStoreProtocol,
    StoreAxes,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.services.method_lifecycle_service import (
    _shape_of,
    current_axes,
)

_SINGLE_MACHINE = METHOD_MACHINE_REGISTRY["single_selection"]
_GROUP_MACHINE = METHOD_MACHINE_REGISTRY["group_member"]

# select 幂等重放(§8.3)的"请求形态"白名单(与 bind 的
# ``_BINDING_REQUEST_FIELDS`` 同款纪律):排除服务端生成的时间事实
# (``valid_from`` / ``transitioned_at``)与状态机事实(``state`` /
# ``transitioned_from``);``transition_reason`` 在 select 面由命令
# ``reason`` 派生、``selected_by_ref`` 由 actor 派生,均属请求形态。
# 同 ID 同请求形态的重放返回既有已受理对象,异形态按 409 语义拒绝。
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

# invite 幂等重放(§8.3)的"请求形态"白名单:排除服务端生成的时间事实
# (``joined_at`` / ``transitioned_at``)与状态机事实(``state`` /
# ``transitioned_from``——重放时刻成员可能已被 add/suspend 推进,
# 那是时间事实不是请求差异);``address``/``role_binding_refs``/
# ``transition_reason`` 是命令请求形态。
_GROUP_MEMBER_REQUEST_FIELDS = (
    "method_binding_id",
    "participant_ref",
    "address",
    "role_binding_refs",
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
            f"single selection state {from_state!r} does not accept event "
            f"{event!r}"
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


def _resolve_member_state(from_state: str, event: str) -> str:
    """经 G4 机器裁决 group_member 迁移;无出边即校验失败。"""
    new_state = resolve_transition(_GROUP_MACHINE, from_state, event, {})
    if new_state is None:
        raise ContextCollabValidationError(
            f"group member state {from_state!r} does not accept event "
            f"{event!r}"
        )
    return new_state


def _require_participant_ref(value: str) -> str:
    """group 成员参与者引用格式校验(非空前缀形状,spec §12 第 11 条);
    存在性校验是 P2。"""
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"participant_ref must be a non-empty string, got: {value!r}"
        )
    return value


def _require_handover_ref(value: str | None) -> str | None:
    """remove_member 的未完成工作转移目标(作者裁决):P1 只做格式校验
    (可缺省;给了就必须是非空串),不执行任何转移。"""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContextCollabValidationError(
            f"handover_to must be a non-empty string or null, got: {value!r}; "
            "P1 validates the reference shape only and performs no handover"
        )
    return value


class SingleGroupMethodService:
    """Single 四操作(Task 9)+ Group 八操作(Task 10)用例;store/
    授权器/Collet 仓储/生命周期全协议注入,结构满足
    ``GroupMethodOpsServiceProtocol``(runtime_checkable isinstance 面)。

    构造形态与 ``MethodLifecycleService`` 同款(injector 显式注入),
    P2 替换任一实现零服务层改动。
    """

    @inject
    def __init__(
        self,
        store: MethodStoreProtocol,
        authorizer: MethodOpsAuthorizerProtocol,
        collab_repo: ContextCollabRepositoryProtocol,
        lifecycle: MethodLifecycleServiceProtocol,
    ) -> None:
        self._store = store
        self._authorizer = authorizer
        self._collab_repo = collab_repo
        self._lifecycle = lifecycle

    # ------------------------------------------------------------------
    # 内部:加载 / 身份解析 / 前置断言(与 lifecycle 服务同款模式)
    # ------------------------------------------------------------------

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
        member: GroupMemberBinding | None = None,
    ) -> None:
        """G1 身份解析 + 授权(spec §8.1):owner 标识取所属 Collet 的
        context_spec(principal_ref.principal_id / executor_ref.agent_id);
        Collet 行不可达时 principal 落 None、executor 回退
        binding.participant_ref,双 None 由授权器 fail-safe 拒绝。
        group 的 self-help 操作(leave)由授权器比对 ``member`` 目标行
        (调用方先定位行再授权),owner 面单传 ``member=None``。"""
        record = self._collab_repo.get_current_status(collab_context_id)
        if record is None:
            principal_id, executor_id = None, binding.participant_ref
        else:
            spec = record.context_spec
            executor_id = spec.executor_ref.agent_id if spec.executor_ref else None
            principal_id = spec.principal_ref.principal_id
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

    # ------------------------------------------------------------------
    # Single 操作族(spec §4.1 / RFC-301 §9)
    # ------------------------------------------------------------------

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
                "selection_id": command.selection_id
                or f"sel_{uuid4().hex[:12]}",
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
                ) == _shape_of(
                    selection.to_dict(), _SELECTION_REQUEST_FIELDS
                ):
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
        stored = self._store.insert_selection(
            axes, selection, collab_context_id
        )
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
        new_state = _resolve_single_state(
            SelectionState.ACTIVE.value, "single.release"
        )
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
            self._store.update_selection_state(
                axes, released, collab_context_id
            )
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

    # ------------------------------------------------------------------
    # Group 操作族(spec §4.2 / ACCS-302)
    # ------------------------------------------------------------------

    def _ensure_roster(
        self, axes: StoreAxes, method_binding_id: str, collab_context_id: str
    ) -> GroupRoster:
        """惰性建 roster(嵌套 Group binding 无创建投影):每 binding 至多
        一份,确定性 id ``roster_<binding_id>``,初始 revision 1;既有即返回。"""
        roster = self._store.get_roster(axes, method_binding_id, collab_context_id)
        if roster is None:
            return self._store.insert_roster(
                axes,
                GroupRoster.from_dict(
                    {
                        "roster_id": f"roster_{method_binding_id}",
                        "method_binding_id": method_binding_id,
                        "revision": 1,
                    }
                ),
                collab_context_id,
            )
        return roster

    def _roster(
        self, axes: StoreAxes, method_binding_id: str, collab_context_id: str
    ) -> GroupRoster:
        """读 roster 并取 snapshot revision(成员写操作的乐观并发基准);
        roster 缺失即 NotFound(成员行必然建在 roster 之上)。"""
        roster = self._store.get_roster(axes, method_binding_id, collab_context_id)
        if roster is None:
            raise ContextCollabNotFoundError(
                f"group roster for method binding {method_binding_id!r} not "
                f"found in collaboration context {collab_context_id!r}"
            )
        return roster

    def _current_member(
        self,
        axes: StoreAxes,
        method_binding_id: str,
        collab_context_id: str,
        participant_ref: str,
    ) -> GroupMemberBinding:
        """定位参与者的非终态在册成员行(终态 LEFT/REMOVED/REPLACED 行
        保留审计、不是操作目标);缺失抛 NotFound。"""
        for member in reversed(
            self._store.list_members(axes, method_binding_id, collab_context_id)
        ):
            if (
                member.participant_ref == participant_ref
                and member.state not in MEMBER_TERMINAL_STATES
            ):
                return member
        raise ContextCollabNotFoundError(
            f"non-terminal group member {participant_ref!r} not found on "
            f"method binding {method_binding_id!r} in collaboration "
            f"context {collab_context_id!r}"
        )

    def _owner_gate(
        self,
        op: str,
        collab_context_id: str,
        method_binding_id: str,
        context: ActorContext,
    ) -> StoreAxes:
        """group owner 面操作共用前置:加载 → RF#2 → G1 授权
        (load→机器→写的完整链由各用例自行收尾)。"""
        axes = current_axes()
        binding = self._load_binding(axes, collab_context_id, method_binding_id)
        self._require_active_binding(op, binding)
        self._authorize(op, binding, context, collab_context_id)
        return axes

    def _transition_member(
        self,
        op: str,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
        event: str,
        *,
        joined_now: bool,
        left_now: bool,
    ) -> ProfileOpAcceptedResult:
        """add/suspend/resume/leave/remove 共用编排:机器裁决 → 换新冻结
        实例(审计四字段 + joined_at/left_at)→ 存储锁内 revision 比对
        一次落定。leave 是 self-help(owner 面单列,与成员行比对授权)。"""
        participant_ref = _require_participant_ref(command.participant_ref)
        axes = current_axes()
        binding = self._load_binding(axes, collab_context_id, method_binding_id)
        self._require_active_binding(op, binding)
        if op == "leave":
            # self-help 先定位目标行再授权(G1 比对 member.participant_ref)。
            member = self._current_member(
                axes, method_binding_id, collab_context_id, participant_ref
            )
            self._authorize(op, binding, context, collab_context_id, member=member)
        else:
            self._authorize(op, binding, context, collab_context_id)
            member = self._current_member(
                axes, method_binding_id, collab_context_id, participant_ref
            )
        new_state = _resolve_member_state(member.state.value, event)
        now = _now()
        updated = replace(
            member,
            state=MemberState.from_wire(new_state),
            joined_at=now if joined_now else member.joined_at,
            left_at=now if left_now else member.left_at,
            transitioned_from=member.state.value,
            transition_reason=command.reason or event,
            transitioned_by=context.actor.principal_id,
            transitioned_at=now,
        )
        roster = self._roster(axes, method_binding_id, collab_context_id)
        self._store.update_member_state(
            axes, updated, roster.revision, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="group_member",
            object_id=member.group_member_binding_id,
            state=new_state,
        )

    def invite(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """Invite 用例:建 INVITED 成员行,不经迁移规则(机器初始态)。

        参与者已有非终态在册成员行即拒绝(存储层唯一断言);roster 缺失时
        惰性建(嵌套 Group binding);幂等键 ``member_binding_id``(§8.3
        同款:同 ID 同请求形态返回既有已受理对象,异形态 409 语义)。
        """
        participant_ref = _require_participant_ref(command.participant_ref)
        axes = self._owner_gate(
            "invite", collab_context_id, method_binding_id, context
        )
        roster = self._ensure_roster(axes, method_binding_id, collab_context_id)
        now = _now()
        member = GroupMemberBinding.from_dict(
            {
                "group_member_binding_id": (
                    command.member_binding_id or f"gm_{uuid4().hex[:12]}"
                ),
                "method_binding_id": method_binding_id,
                "participant_ref": participant_ref,
                "address": command.address,
                "role_binding_refs": list(command.role_binding_refs),
                "state": MemberState.INVITED.value,
                "joined_at": None,
                "left_at": None,
                "transitioned_from": "",
                "transition_reason": command.reason or "group.invite",
                "transitioned_by": context.actor.principal_id,
                "transitioned_at": now,
            }
        )
        if command.member_binding_id is not None:
            existing = next(
                (
                    row
                    for row in self._store.list_members(
                        axes, method_binding_id, collab_context_id
                    )
                    if row.group_member_binding_id == command.member_binding_id
                ),
                None,
            )
            if existing is not None:
                # §8.3 幂等重放(与 select/bind 同款):同 ID 同"请求形态"
                # 返回既有已受理对象(不重写审计、不重复 bump)。
                if _shape_of(
                    existing.to_dict(), _GROUP_MEMBER_REQUEST_FIELDS
                ) == _shape_of(
                    member.to_dict(), _GROUP_MEMBER_REQUEST_FIELDS
                ):
                    return ProfileOpAcceptedResult(
                        accepted=True,
                        collab_context_id=collab_context_id,
                        method_binding_id=method_binding_id,
                        object_kind="group_member",
                        object_id=existing.group_member_binding_id,
                        state=existing.state.value,
                    )
                raise ContextCollabValidationError(
                    f"idempotent re-submit of group member "
                    f"{command.member_binding_id!r} differs from the stored "
                    "shape"
                )
        stored = self._store.insert_member(
            axes, member, roster.revision, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="group_member",
            object_id=stored.group_member_binding_id,
            state=stored.state.value,
        )

    def add_member(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """AddMember 用例:INVITED─group.add_member→ACTIVE,落 joined_at。"""
        return self._transition_member(
            "add_member",
            collab_context_id,
            method_binding_id,
            command,
            context,
            "group.add_member",
            joined_now=True,
            left_now=False,
        )

    def suspend_member(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """SuspendMember 用例:ACTIVE─group.suspend_member→SUSPENDED。"""
        return self._transition_member(
            "suspend_member",
            collab_context_id,
            method_binding_id,
            command,
            context,
            "group.suspend_member",
            joined_now=False,
            left_now=False,
        )

    def resume_member(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """ResumeMember 用例(作者裁决补漏):SUSPENDED─group.resume_member
        →ACTIVE,保证成员状态机全状态可达。"""
        return self._transition_member(
            "resume_member",
            collab_context_id,
            method_binding_id,
            command,
            context,
            "group.resume_member",
            joined_now=False,
            left_now=False,
        )

    def leave(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """Leave 用例(成员本人自助,作者裁决):ACTIVE/SUSPENDED──
        group.leave→LEFT,落 left_at;授权比对 actor 与目标行
        ``participant_ref`` 字符串相等,owner 也不得替人退出。"""
        return self._transition_member(
            "leave",
            collab_context_id,
            method_binding_id,
            command,
            context,
            "group.leave",
            joined_now=False,
            left_now=True,
        )

    def remove_member(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """RemoveMember 用例:ACTIVE/SUSPENDED──group.remove_member→
        REMOVED;``handover_to`` 是未完成工作转移目标,P1 只做格式校验
        不执行转移(作者裁决)。"""
        _require_handover_ref(command.handover_to)
        return self._transition_member(
            "remove_member",
            collab_context_id,
            method_binding_id,
            command,
            context,
            "group.remove_member",
            joined_now=False,
            left_now=True,
        )

    def replace_member(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupReplaceMemberCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """ReplaceMember 用例:存储锁内一次落定旧 ACTIVE→REPLACED / 新成员
        →ACTIVE(单次 roster revision bump)。

        旧行经 ``group.replace_member`` 机器裁决(唯一出边在 ACTIVE),
        新行直接以 ACTIVE 创建(不经 INVITED);新参与者命中其他非终态
        在册成员由存储层拒绝;并发换手由锁内 revision 比对仲裁(409)。
        """
        old_participant = _require_participant_ref(command.old_participant_ref)
        new_participant = _require_participant_ref(command.new_participant_ref)
        axes = current_axes()
        binding = self._load_binding(axes, collab_context_id, method_binding_id)
        self._require_active_binding("replace_member", binding)
        self._authorize("replace_member", binding, context, collab_context_id)
        roster = self._roster(axes, method_binding_id, collab_context_id)
        old = self._current_member(
            axes, method_binding_id, collab_context_id, old_participant
        )
        replaced_state = _resolve_member_state(old.state.value, "group.replace_member")
        now = _now()
        old_replaced = replace(
            old,
            state=MemberState.from_wire(replaced_state),
            left_at=now,
            transitioned_from=old.state.value,
            transition_reason=command.reason or "group.replace_member",
            transitioned_by=context.actor.principal_id,
            transitioned_at=now,
        )
        new_member = GroupMemberBinding.from_dict(
            {
                "group_member_binding_id": f"gm_{uuid4().hex[:12]}",
                "method_binding_id": method_binding_id,
                "participant_ref": new_participant,
                "address": command.address,
                "role_binding_refs": list(command.role_binding_refs),
                "state": MemberState.ACTIVE.value,
                "joined_at": now,
                "left_at": None,
                "transitioned_from": "",
                "transition_reason": command.reason or "group.replace_member",
                "transitioned_by": context.actor.principal_id,
                "transitioned_at": now,
            }
        )
        self._store.replace_member(
            axes, old_replaced, new_member, roster.revision, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="group_member",
            object_id=new_member.group_member_binding_id,
            state=new_member.state.value,
        )

    def change_role(
        self,
        collab_context_id: str,
        method_binding_id: str,
        command: GroupChangeRoleCommand,
        context: ActorContext,
    ) -> ProfileOpAcceptedResult:
        """ChangeRole 用例(owner-only,不经自助路径——成员自改
        role_binding_refs 属提权,2026-11-12 复审裁决收窄)。

        只写 ``role_binding_refs``(格式校验经 ``from_dict`` 整形重验),
        无状态迁移、审计四字段不覆写;``expected_revision`` 与存储锁内
        roster revision 比对,不匹配抛 409 语义,roster revision +1。
        """
        participant_ref = _require_participant_ref(command.participant_ref)
        axes = self._owner_gate(
            "change_role", collab_context_id, method_binding_id, context
        )
        member = self._current_member(
            axes, method_binding_id, collab_context_id, participant_ref
        )
        updated = GroupMemberBinding.from_dict(
            {
                **member.to_dict(),
                "role_binding_refs": list(command.role_binding_refs),
            }
        )
        self._store.update_member_state(
            axes, updated, command.expected_revision, collab_context_id
        )
        return ProfileOpAcceptedResult(
            accepted=True,
            collab_context_id=collab_context_id,
            method_binding_id=method_binding_id,
            object_kind="group_member",
            object_id=member.group_member_binding_id,
            state=member.state.value,
        )
