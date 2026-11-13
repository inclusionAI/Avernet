"""L5 InMemoryMethodStore 单测(Task 5:binding/edge 切片 + 整锁骨架)。

覆盖:
- ``StoreAxes`` 轴校验(空租户/环境拒绝);
- upsert 幂等(同键同 to_dict 返回既有存储实例)与异形态冲突
  (报错点名 method_binding_id,存储不落半态);
- 键按 (avernet_tenant, env, collab_context_id, id) 隔离:同一 binding id
  在两套 axes 下互不可见、各自成王(re-shape 不视为冲突);
- replace 不存在抛 ``ContextCollabNotFoundError``、存在换新实例;
  ``list_bindings`` 保插入序且不串 context;
- edge 插入幂等、同端点同关系 ACTIVE 重复(不同 edge_id)拒绝、
  replace 不存在抛 NotFound、detach 经 replace 落 DETACHED 后仅
  ``active_only=False`` 可见、DETACHED 后同 pair 允许新 ACTIVE 边;
- 两线程 barrier 起跑交错 upsert 50 个不同 id:整锁序列化,不丢、不炸。
"""
import threading

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_binding import (
    BindingState,
    MethodBinding,
    parse_method_kinds,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    MethodCompositionEdge,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
    StoreAxes,
)

# 两套独立 axes:同 binding id 在其下互不可见。
AXES_A = StoreAxes(avernet_tenant="tenant-a", env="prod")
AXES_B = StoreAxes(avernet_tenant="tenant-b", env="prod")


def _binding(
    method_binding_id: str = "binding-1",
    collab_context_id: str = "ctx-1",
    *,
    state: str = "BOUND",
    version: int = 1,
) -> MethodBinding:
    """构造一份合法 MethodBinding;字段形状沿用 Task 4 值对象测试。"""
    names = [kind.value for kind in parse_method_kinds("Single")]
    return MethodBinding.from_dict(
        {
            "method_binding_id": method_binding_id,
            "collab_context_id": collab_context_id,
            "method_kind": "Single",
            "method_names": names,
            "profile_versions": {names[0]: "0.2"},
            "state": state,
            "version": version,
            "bind_reason": "create_context",
            "transitioned_from": "",
            "transition_reason": "initial bind",
            "transitioned_by": "principal-1",
            "transitioned_at": "2026-11-12T10:00:00Z",
        }
    )


def _edge(
    edge_id: str = "edge-1",
    collab_context_id: str = "ctx-1",
    *,
    parent_binding: str = "binding-parent",
    state: str = "ACTIVE",
) -> MethodCompositionEdge:
    """构造一份合法 MethodCompositionEdge;字段形状沿用 Task 4 值对象测试。"""
    return MethodCompositionEdge.from_dict(
        {
            "edge_id": edge_id,
            "collab_context_id": collab_context_id,
            "parent_binding": parent_binding,
            "child_binding": "binding-child",
            "relation_kind": "delegate_to",
            "state": state,
            "transitioned_from": "",
            "transition_reason": "delegate selected task",
            "transitioned_by": "principal-1",
            "transitioned_at": "2026-11-12T10:00:00Z",
        }
    )


# ---------------------------------------------------------------------------
# StoreAxes 轴校验
# ---------------------------------------------------------------------------


def test_store_axes_rejects_empty_fields():
    with pytest.raises(ContextCollabValidationError):
        StoreAxes(avernet_tenant="", env="prod")
    with pytest.raises(ContextCollabValidationError):
        StoreAxes(avernet_tenant="tenant-a", env=" ")


# ---------------------------------------------------------------------------
# binding 切片
# ---------------------------------------------------------------------------


def test_upsert_binding_idempotent_same_shape():
    store = InMemoryMethodStore()
    first = _binding()
    assert store.upsert_binding(AXES_A, first) is first
    # 相等但不同的实例(同 to_dict)→ 幂等返回首次存储的实例,不换存。
    duplicate = _binding()
    assert duplicate is not first
    assert store.upsert_binding(AXES_A, duplicate) is first
    assert len(store.list_bindings(AXES_A, "ctx-1")) == 1


def test_upsert_binding_conflict_different_shape():
    store = InMemoryMethodStore()
    store.upsert_binding(AXES_A, _binding())
    resubmit = _binding(state="ACTIVE", version=2)
    with pytest.raises(ContextCollabValidationError, match="binding-1"):
        store.upsert_binding(AXES_A, resubmit)
    # 冲突拒绝不落半态:存储内仍是首份形态。
    stored = store.get_binding(AXES_A, "binding-1", "ctx-1")
    assert stored.version == 1
    assert stored.state is BindingState.BOUND


def test_binding_keys_isolated_per_tenant_env():
    store = InMemoryMethodStore()
    # 同一 binding id、不同形态:跨 axes 不构成冲突,各自独立可取。
    a_binding = _binding(version=1)
    b_binding = _binding(version=2)
    assert store.upsert_binding(AXES_A, a_binding) is a_binding
    assert store.upsert_binding(AXES_B, b_binding) is b_binding
    assert store.get_binding(AXES_A, "binding-1", "ctx-1") is a_binding
    assert store.get_binding(AXES_B, "binding-1", "ctx-1") is b_binding


def test_get_binding_missing_returns_none():
    store = InMemoryMethodStore()
    assert store.get_binding(AXES_A, "binding-1", "ctx-1") is None


def test_replace_binding_requires_existing_key():
    store = InMemoryMethodStore()
    with pytest.raises(ContextCollabNotFoundError):
        store.replace_binding(AXES_A, _binding())


def test_replace_binding_overwrites_existing():
    store = InMemoryMethodStore()
    store.upsert_binding(AXES_A, _binding())
    replacement = _binding(state="ACTIVE", version=2)
    store.replace_binding(AXES_A, replacement)
    replaced = store.get_binding(AXES_A, "binding-1", "ctx-1")
    assert replaced is replacement
    assert replaced.state is BindingState.ACTIVE


def test_list_bindings_preserves_insertion_order_and_context_scope():
    store = InMemoryMethodStore()
    for index in range(3):
        store.upsert_binding(AXES_A, _binding(method_binding_id=f"binding-{index}"))
    # 其它 context 的绑定不串进来。
    store.upsert_binding(
        AXES_A, _binding(method_binding_id="binding-x", collab_context_id="ctx-2")
    )
    assert [b.method_binding_id for b in store.list_bindings(AXES_A, "ctx-1")] == [
        "binding-0",
        "binding-1",
        "binding-2",
    ]


# ---------------------------------------------------------------------------
# composition edge 切片
# ---------------------------------------------------------------------------


def test_insert_composition_edge_idempotent_same_shape():
    store = InMemoryMethodStore()
    first = _edge()
    assert store.insert_composition_edge(AXES_A, first) is first
    again = store.insert_composition_edge(AXES_A, _edge())
    assert again is first
    assert len(store.list_composition_edges(AXES_A, "ctx-1")) == 1


def test_insert_composition_edge_conflict_different_shape():
    store = InMemoryMethodStore()
    store.insert_composition_edge(AXES_A, _edge())
    with pytest.raises(ContextCollabValidationError, match="edge-1"):
        store.insert_composition_edge(AXES_A, _edge(state="DETACHED"))


def test_insert_composition_edge_rejects_duplicate_active_pair():
    store = InMemoryMethodStore()
    store.insert_composition_edge(AXES_A, _edge(edge_id="edge-1"))
    with pytest.raises(ContextCollabValidationError) as excinfo:
        store.insert_composition_edge(AXES_A, _edge(edge_id="edge-2"))
    # 报错点重复的 (parent_binding, child_binding, relation_kind) pair。
    message = str(excinfo.value)
    assert "binding-parent" in message
    assert "binding-child" in message
    # 拒绝的边不落库。
    assert store.get_composition_edge(AXES_A, "edge-2", "ctx-1") is None


def test_insert_composition_edge_duplicate_pair_ignored_across_axes():
    store = InMemoryMethodStore()
    store.insert_composition_edge(AXES_A, _edge(edge_id="edge-1"))
    # 不同 tenant 下的同 pair 是另一个世界的边,不受本 axes 唯一性约束。
    assert store.insert_composition_edge(AXES_B, _edge(edge_id="edge-2")) is not None


def test_replace_composition_edge_requires_existing_key():
    store = InMemoryMethodStore()
    with pytest.raises(ContextCollabNotFoundError):
        store.replace_composition_edge(AXES_A, _edge())


def test_detach_via_replace_visible_only_without_active_only():
    store = InMemoryMethodStore()
    store.insert_composition_edge(AXES_A, _edge())
    detached = _edge(state="DETACHED")
    store.replace_composition_edge(AXES_A, detached)
    assert store.list_composition_edges(AXES_A, "ctx-1") == []
    assert store.list_composition_edges(AXES_A, "ctx-1", active_only=False) == [
        detached
    ]
    assert store.get_composition_edge(AXES_A, "edge-1", "ctx-1") is detached


def test_detached_pair_allows_new_active_edge():
    store = InMemoryMethodStore()
    store.insert_composition_edge(AXES_A, _edge(edge_id="edge-1"))
    store.replace_composition_edge(AXES_A, _edge(edge_id="edge-1", state="DETACHED"))
    # detach 后同 pair 允许一条新 ACTIVE 边(重绑路径)。
    fresh = _edge(edge_id="edge-2")
    assert store.insert_composition_edge(AXES_A, fresh) is fresh
    active = store.list_composition_edges(AXES_A, "ctx-1")
    assert [edge.edge_id for edge in active] == ["edge-2"]


def test_list_composition_edges_preserves_insertion_order_and_context_scope():
    store = InMemoryMethodStore()
    # 各边 parent 不同,避免撞上同 pair ACTIVE 唯一性裁断。
    for index, edge_id in enumerate(("edge-a", "edge-b", "edge-c")):
        store.insert_composition_edge(
            AXES_A, _edge(edge_id=edge_id, parent_binding=f"binding-parent-{index}")
        )
    store.insert_composition_edge(AXES_A, _edge(edge_id="edge-x", collab_context_id="ctx-2"))
    assert [edge.edge_id for edge in store.list_composition_edges(AXES_A, "ctx-1")] == [
        "edge-a",
        "edge-b",
        "edge-c",
    ]


def test_get_composition_edge_missing_returns_none():
    store = InMemoryMethodStore()
    assert store.get_composition_edge(AXES_A, "edge-1", "ctx-1") is None


# ---------------------------------------------------------------------------
# 整锁骨架
# ---------------------------------------------------------------------------


def test_lock_serializes_concurrent_upserts():
    store = InMemoryMethodStore()
    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def worker(binding_ids: list[str]) -> None:
        try:
            barrier.wait()
            for binding_id in binding_ids:
                store.upsert_binding(AXES_A, _binding(method_binding_id=binding_id))
        except BaseException as exc:  # noqa: BLE001 —— 测试线程内收集任意异常
            failures.append(exc)

    thread_a = threading.Thread(
        target=worker, args=([f"binding-{i}" for i in range(0, 25)],)
    )
    thread_b = threading.Thread(
        target=worker, args=([f"binding-{i}" for i in range(25, 50)],)
    )
    thread_a.start()
    thread_b.start()
    thread_a.join()
    thread_b.join()
    assert failures == []
    bindings = store.list_bindings(AXES_A, "ctx-1")
    assert len(bindings) == 50
    assert {b.method_binding_id for b in bindings} == {
        f"binding-{i}" for i in range(50)
    }
