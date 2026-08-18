"""task 级回调→节点寻址 registry(派发期登记,回调期 resolve)。

task 级回调(workflow_start/workflow_result)载荷无 node_id,只有 workflow_instance_id;
派发期(TaskRunner.start_run 派发到 claw_mind/bcn 时)登记 (source, instance_id_str)→节点,
回调期 resolve 得 (task_id, node_id, loop_task_id, SSOT int workflow_id/instance_id)。
in-mem(与 TaskHarness._dispatched_at 同级),不落库;线程安全(dict + RLock)。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class CorrelationRecord:
    task_id: str
    node_id: str
    loop_task_id: str
    workflow_id: int          # SSOT int(供 TaskCallbackData)
    instance_id: int          # SSOT int


@runtime_checkable
class CallbackCorrelationRegistry(Protocol):
    """派发期登记 / 回调期 resolve 的寻址端口。"""

    def register(
        self, *, source: str, workflow_id: int, instance_id: int,
        task_id: str, node_id: str, loop_task_id: str,
        workflow_id_str: str, instance_id_str: str,
    ) -> None: ...

    def resolve(self, source: str, instance_id_str: str) -> CorrelationRecord | None: ...


class InMemoryCallbackCorrelationRegistry:
    """线程安全 in-mem 实现。key=(source, instance_id_str)。"""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], CorrelationRecord] = {}
        self._lock = threading.RLock()

    def register(
        self, *, source: str, workflow_id: int, instance_id: int,
        task_id: str, node_id: str, loop_task_id: str,
        workflow_id_str: str, instance_id_str: str,
    ) -> None:
        rec = CorrelationRecord(
            task_id=task_id, node_id=node_id, loop_task_id=loop_task_id,
            workflow_id=workflow_id, instance_id=instance_id,
        )
        with self._lock:
            self._by_key[(source, instance_id_str)] = rec

    def resolve(self, source: str, instance_id_str: str) -> CorrelationRecord | None:
        with self._lock:
            return self._by_key.get((source, instance_id_str))