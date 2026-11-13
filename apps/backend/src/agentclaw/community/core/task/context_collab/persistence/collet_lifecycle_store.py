"""进程内 Collet 生命周期状态 store(spec §5.3,方案 b 裁决)。

``collet_lifecycle`` 顶层状态只存在于进程内:store 键为全局唯一的
``collet_id``,值是 ``ColletState``;不落数据库、不落 store 列,P2 经新增
列或 JSON 字段换成持久化实现,服务层只依赖 L4 存储端口零改动。重启归零
(方案 b 弱化):进程重启后 store 为空,未见过的 collet 与 create_context
新建的一样,由 ``get_or_seed`` 种子 ``ACTIVE``(沿"创建即可协作"既有
裁决;``CREATED`` 保留在机器词汇,可达性由 conformance 测试直接构造值
验证)。键不带租户前缀——Collet ID 全局唯一,
``ac_context_collab_collet`` 唯一键语义不变。
"""

from __future__ import annotations

import threading

from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
    ColletState,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.operations.contracts import (
    ColletLifecycleStore as ColletLifecycleStore,
)

# 种子状态:创建即可协作(spec §5.3;"collet.created" 仅注册不配规则)。
_SEED_STATE = ColletState.ACTIVE


class InMemoryColletLifecycleStore:
    """整 ``threading.Lock`` 的进程内实现(DI singleton)。

    所有读写共享一把锁——P1 操作频度低,粗粒度互斥换零死锁复杂度;
    持久化实现(P2)在同一协议键下整体替换,调用方无感。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, ColletState] = {}

    def get(self, collet_id: str) -> ColletState | None:
        with self._lock:
            return self._states.get(collet_id)

    def set(self, collet_id: str, state: ColletState) -> None:
        with self._lock:
            self._states[collet_id] = state

    def get_or_seed(self, collet_id: str) -> ColletState:
        with self._lock:
            state = self._states.get(collet_id)
            if state is None:
                state = _SEED_STATE
                self._states[collet_id] = state
            return state
