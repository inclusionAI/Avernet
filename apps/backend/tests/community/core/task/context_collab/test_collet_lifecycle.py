"""Collet 生命周期五操作单测(plan Task 3;spec §5.3)。

覆盖:
- 五操作状态机正反例(suspend→resume 回 ACTIVE、complete→archive 终态链、
  activate 自 ACTIVE 幂等拒绝、COMPLETED 后 activate 拒、ARCHIVED 后
  complete 拒的终态防线);
- store 种子语义(``get_or_seed`` 未见即写 ACTIVE;直接 ``get`` 不落种);
- 守卫:发起者本人 / executor(agent_id 字符串相等)放行,陌生 actor 拒;
- collet 不存在抛 ``ContextCollabNotFoundError``;
- 重启归零:新 store 实例同 collet 回 ACTIVE(方案 b 弱化语义);
- 操作返回结果对象(old_state / new_state / initiator_id / reason)。

collet 仓储用手写 fake(受控返回 ``ContextStatusRecord``),不触数据库;
store 用真 ``InMemoryColletLifecycleStore``。
"""

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    Collet,
    ColletState,
)
from agentclaw.community.core.task.context_collab.l6_products.context_spec import (
    ContextSpec,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import Task
from agentclaw.community.core.task.context_collab.l4_context_primitives.task import (
    TaskStatus,
)
from agentclaw.community.core.task.context_collab.persistence.collet_lifecycle_store import (
    InMemoryColletLifecycleStore,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.service_api import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.services.collet_lifecycle_service import (
    ColletLifecycleResult,
    ColletLifecycleService,
)

COLLET_ID = "collet_lifecycle_001"
PRINCIPAL_ID = "u_1001"
EXECUTOR_ID = "bot_coding_001"
OBJECTIVE = "整理 ACCS-104 L4 对齐评审结论并驱动 Collet 生命周期"


def make_actor(principal_id: str = PRINCIPAL_ID) -> ActorContext:
    return ActorContext(actor=PrincipalRef("human", principal_id), source="api")


def _context_spec(*, method_kind: str = "Single") -> ContextSpec:
    return ContextSpec(
        space=SpaceRef(space_id="space_workspace_001", environment="prod"),
        principal_ref=PrincipalRef("human", PRINCIPAL_ID),
        method_kind=method_kind,
        executor_ref=AgentRef("bot", EXECUTOR_ID),
        objective=OBJECTIVE,
        resource_refs=(),
        product_kind=None,
        workflow_kind=None,
    )


def _status_record(collab_context_id: str = COLLET_ID) -> ContextStatusRecord:
    spec = _context_spec()
    principal_ref = PrincipalRef("human", PRINCIPAL_ID)
    task = Task(
        objective=OBJECTIVE,
        status=TaskStatus.IN_PROGRESS.value,
        principal_ref=principal_ref,
        executor_ref=AgentRef("bot", EXECUTOR_ID),
        task_id="task_" + collab_context_id,
        collet_ref=collab_context_id,
        version=1,
        parent_task_ref=None,
        completion_conditions=(),
        created_event_ref="evt_fixture",
        last_event_ref="evt_fixture",
    )
    collet = Collet(
        collet_id=collab_context_id,
        initiator_ref=principal_ref,
        state=ColletState.ACTIVE,
        scope=spec.space,
        current_event_id=None,
        version=1,
    )
    return ContextStatusRecord(
        collet=collet,
        context_spec=spec,
        tasks=(task,) if task is not None else (),
        task=task,
        events=[],
    )


class _FakeCollabRepository:
    """最小 collet 仓储 fake:按 ID 返回受控 ``ContextStatusRecord``。"""

    def list_collets_by_product(self, product_kind: str):
        raise NotImplementedError("fake does not implement product listing")

    def count_message_events(self, collab_context_ids):
        raise NotImplementedError("fake does not implement message counting")

    def __init__(self, records: dict[str, ContextStatusRecord]) -> None:
        self._records = dict(records)

    def get_current_status(self, collab_context_id: str):
        return self._records.get(collab_context_id)

    def create_context(self, request):
        raise NotImplementedError("fake only implements get_current_status")

    def append_event(self, request):
        raise NotImplementedError("fake only implements get_current_status")


def _service(records: dict[str, ContextStatusRecord] | None = None):
    repo = _FakeCollabRepository(records or {COLLET_ID: _status_record()})
    service = ColletLifecycleService(
        collet_lifecycle_store=InMemoryColletLifecycleStore(),
        collab_repo=repo,
    )
    return service


# ------------------------------------------------------------- store 种子语义


def test_get_or_seed_seeds_active_and_persists():
    store = InMemoryColletLifecycleStore()
    assert store.get(COLLET_ID) is None
    assert store.get_or_seed(COLLET_ID) is ColletState.ACTIVE
    # 种子即写入:同 key 再次读取取已落状态。
    assert store.get(COLLET_ID) is ColletState.ACTIVE


def test_set_then_get_roundtrip():
    store = InMemoryColletLifecycleStore()
    store.set(COLLET_ID, ColletState.SUSPENDED)
    assert store.get(COLLET_ID) is ColletState.SUSPENDED
    store.set(COLLET_ID, ColletState.COMPLETED)
    assert store.get(COLLET_ID) is ColletState.COMPLETED


# ------------------------------------------------------------------ 五操作


def test_unseeded_collet_defaults_active():
    """焊死种子语义:store 未见过(重启后)与新建的 collet 都种子 ACTIVE。"""
    service = _service()
    result = service.suspend(COLLET_ID, make_actor())
    assert isinstance(result, ColletLifecycleResult)
    assert result.old_state is ColletState.ACTIVE
    assert result.new_state is ColletState.SUSPENDED


def test_suspend_resume_roundtrip_returns_active():
    service = _service()
    service.suspend(COLLET_ID, make_actor(), reason="暂停协作")
    result = service.resume(COLLET_ID, make_actor())
    assert result.old_state is ColletState.SUSPENDED
    assert result.new_state is ColletState.ACTIVE


def test_result_object_carries_collet_id_initiator_and_reason():
    service = _service()
    result = service.complete(COLLET_ID, make_actor(), reason="目标达成")
    assert result.collet_id == COLLET_ID
    assert result.initiator_id == PRINCIPAL_ID
    assert result.reason == "目标达成"


def test_activate_after_seed_is_idempotent_rejection():
    """store 种子 ACTIVE 后 activate 无规则命中 → 非法迁移拒绝。"""
    service = _service()
    with pytest.raises(ContextCollabValidationError):
        service.activate(COLLET_ID, make_actor())


def test_complete_then_archive_reaches_terminal():
    service = _service()
    completed = service.complete(COLLET_ID, make_actor())
    assert completed.old_state is ColletState.ACTIVE
    assert completed.new_state is ColletState.COMPLETED
    archived = service.archive(COLLET_ID, make_actor())
    assert archived.old_state is ColletState.COMPLETED
    assert archived.new_state is ColletState.ARCHIVED


def test_completed_collet_rejects_activate_and_suspend():
    service = _service()
    service.complete(COLLET_ID, make_actor())
    with pytest.raises(ContextCollabValidationError):
        service.activate(COLLET_ID, make_actor())
    with pytest.raises(ContextCollabValidationError):
        service.suspend(COLLET_ID, make_actor())


def test_archived_collet_rejects_complete_terminal_guard():
    """ARCHIVED 终态防线:archive 后 complete/activate 一律拒。"""
    service = _service()
    service.complete(COLLET_ID, make_actor())
    service.archive(COLLET_ID, make_actor())
    with pytest.raises(ContextCollabValidationError):
        service.complete(COLLET_ID, make_actor(), reason="复活尝试")
    with pytest.raises(ContextCollabValidationError):
        service.activate(COLLET_ID, make_actor())


def test_suspend_on_suspended_is_rejected():
    """重复 suspend 幂等拒绝:SUSPENDED 无 collet.suspended 出边。"""
    service = _service()
    service.suspend(COLLET_ID, make_actor())
    with pytest.raises(ContextCollabValidationError):
        service.suspend(COLLET_ID, make_actor())


# --------------------------------------------------------------------- 守卫


def test_stranger_actor_is_rejected():
    service = _service()
    with pytest.raises(ContextCollabValidationError):
        service.suspend(COLLET_ID, make_actor("u_stranger"))


def test_executor_actor_matches_by_agent_id():
    """executor 判定沿 ``_is_executor`` 先例:principal_id 与 agent_id 字符串相等。"""
    service = _service()
    result = service.suspend(COLLET_ID, make_actor(EXECUTOR_ID))
    assert result.new_state is ColletState.SUSPENDED


# ----------------------------------------------------------------- 边界


def test_unknown_collet_raises_not_found():
    service = _service(records={})
    with pytest.raises(ContextCollabNotFoundError):
        service.suspend("collet_missing_404", make_actor())


def test_fresh_store_instance_loses_state():
    """重启归零(方案 b):新 store 实例同 collet 回 ACTIVE 种子。"""
    repo = _FakeCollabRepository({COLLET_ID: _status_record()})
    first = ColletLifecycleService(
        collet_lifecycle_store=InMemoryColletLifecycleStore(), collab_repo=repo
    )
    first.complete(COLLET_ID, make_actor())
    second = ColletLifecycleService(
        collet_lifecycle_store=InMemoryColletLifecycleStore(), collab_repo=repo
    )
    assert second.suspend(COLLET_ID, make_actor()).old_state is ColletState.ACTIVE
