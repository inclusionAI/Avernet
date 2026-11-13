"""``InMemoryMethodStore`` 的切片组装(spec §6.1)。

协议与 ``StoreAxes`` 在 ``persistence/method_store.py``;本包只持有
进程内实现,按切片 mixin 组合(锁与仓库字典在 ``base.py``,binding/
edge、single selection、group roster/member、swarm、open 各自成片),
单一 ``InMemoryMethodStore`` 类因此整体满足
``MethodStoreProtocol`` 的 runtime_checkable isinstance 面——结构满足
即可替换(沿 ``StructuralMethodOpsAuthorizer`` 先例,不继承 Protocol,
避免 protocol ↔ impl 的回环 import)。
"""

from __future__ import annotations

from .base import InMemoryMethodStoreBase
from .binding_slice import BindingEdgeSlice
from .group_slice import GroupSlice
from .open_slice import OpenSlice
from .selection_slice import SingleSelectionSlice
from .swarm_slice import SwarmSlice


class InMemoryMethodStore(
    OpenSlice,
    SwarmSlice,
    GroupSlice,
    SingleSelectionSlice,
    BindingEdgeSlice,
    InMemoryMethodStoreBase,
):
    """binding/edge/selection/group/swarm/open 切片的内存存储;构造无参,
    DI 单例由容器接管。

    值对象是 frozen dataclass,按引用原样存取、不复制:不可变性由构造
    保证,返回的即存储实例,调用方没有任何途径绕过冻结改写仓库状态,
    因此也无需防御性拷贝。
    """


__all__ = ["InMemoryMethodStore"]
