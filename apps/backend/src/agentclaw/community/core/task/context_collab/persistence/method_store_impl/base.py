"""L5 方法存储共享骨架:锁、各切片仓库字典与跨切片键位助手。

L5 值对象不携带租户/环境,存储键自带 (avernet_tenant, env,
collab_context_id, ...) 前缀,由调用方显式传 ``StoreAxes``;store 不读
运行环境,保持纯内存语义。单把粗粒度 ``threading.Lock`` 覆盖全部写与
list 读——store 操作短小,先以一锁压住正确性,成为热点再细分。

切片模块(binding/selection/group/swarm...)按 mixin 组合到
``InMemoryMethodStore`` 上(见 ``method_store_impl/__init__.py``),
共享本骨架的锁与仓库字典;构造无参(DI 单例,Task 8/13 接线)。
"""

from __future__ import annotations

import threading


class InMemoryMethodStoreBase:
    """切片仓库字典的持有者:全部实例状态集中在此 ``__init__``,
    切片 mixin 经 ``self._lock`` / ``self._<collection>`` 共享访问。"""

    def __init__(self) -> None:
        # 单锁骨架:全部写与 list 读共用一把锁,操作短小,不做热点细分。
        self._lock = threading.Lock()
        self._bindings: dict = {}
        self._edges: dict = {}
        self._selections: dict = {}
        self._rosters: dict = {}
        self._members: dict = {}
        # swarm 切片(Task 11):plan / stage / allocation /
        # execution profile / execution record 五个仓库字典。
        self._plans: dict = {}
        self._stages: dict = {}
        self._allocations: dict = {}
        self._execution_profiles: dict = {}
        self._execution_records: dict = {}
        # open 切片(Task 12):opportunity / application / decision
        # 三个仓库字典。
        self._opportunities: dict = {}
        self._applications: dict = {}
        self._admission_decisions: dict = {}

    @staticmethod
    def _key_in_context(key: tuple, axes, collab_context_id: str) -> bool:
        """四段键的租户/环境/上下文命中断言(binding/edge 切片共用)。"""
        return (
            key[0] == axes.avernet_tenant
            and key[1] == axes.env
            and key[2] == collab_context_id
        )
