"""task 级回调→节点寻址 registry(派发期登记,回调期 resolve)。

task 级回调(workflow_start/workflow_result)载荷无 node_id,只有 workflow_instance_id;
派发期(TaskRunner.start_run 派发到 claw_mind/bcn 时)登记 (source, instance_id_str)→节点,
回调期 resolve 得 (task_id, node_id, loop_task_id, SSOT int workflow_id/instance_id)。
线程安全(dict + RLock)。

REQ-P1 restart-durability: in-mem 仍是当前实例的路由真值;可选注入
``TaskCallbackCorrelationRepositoryProtocol`` 后,register 时 best-effort 旁写
``task_callback_correlation``(决策 #14:DB 写是重启恢复的观测增强,失败仅 WARNING、不阻断路由),
resolve 缓存未命中时回查 DB,把重启后到达的回调按 (source, instance_id_str) 关联回节点。
``correlation_repo is None`` → 退化为纯内存(原行为,轻量测试/未注入路径用)。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentclaw.community.core.repository.protocols.task import (
        TaskCallbackCorrelationRepositoryProtocol,
    )

logger = logging.getLogger("task.callback_correlation")


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
    """线程安全 in-mem 实现。key=(source, instance_id_str)。

    可选注入 ``correlation_repo`` 开启 REQ-P1 重启恢复:register 旁写 DB(best-effort,
    决策 #14)、resolve 缓存未命中时回查 DB 并把恢复出的记录回填内存(cache-aside)。未注入
    (``None``)时退化为纯内存,保持既有构造/调用契约不变。DB 旁写以组合键
    ``f"{source}:{instance_id_str}"`` 存入 ``event_id`` 列、``instance_id_str`` 存入
    ``main_session_id``;``retry=0`` 哨兵(本 registry 不追踪 retry)。DB 幂等由 P1b repo
    保证(重复 register 对 DB 是 no-op);内存则保持 last-write-wins(本实例路由真值)。
    """

    def __init__(
        self,
        correlation_repo: "TaskCallbackCorrelationRepositoryProtocol | None" = None,
    ) -> None:
        self._by_key: dict[tuple[str, str], CorrelationRecord] = {}
        self._lock = threading.RLock()
        self._correlation_repo = correlation_repo

    @staticmethod
    def _durable_key(source: str, instance_id_str: str) -> str:
        """Composite durable key for the ``event_id`` column — the registry's
        natural key ``(source, instance_id_str)``, stable across restart (the
        workflow_instance_id is a durable external identifier). ``source`` is a
        fixed enum-like token (``bcn`` / ``claw_mind``), so the ``:`` separator
        cannot collide."""
        return f"{source}:{instance_id_str}"

    def register(
        self, *, source: str, workflow_id: int, instance_id: int,
        task_id: str, node_id: str, loop_task_id: str,
        workflow_id_str: str, instance_id_str: str,
    ) -> None:
        rec = CorrelationRecord(
            task_id=task_id, node_id=node_id, loop_task_id=loop_task_id,
            workflow_id=workflow_id, instance_id=instance_id,
        )
        key = (source, instance_id_str)
        with self._lock:
            self._by_key[key] = rec
        # Best-effort durable write (决策 #14): the in-memory map is the routing
        # truth for the current instance; the DB write is a restart-recovery
        # enhancement. Failure → WARNING + continue (memory already populated,
        # current-instance routing unaffected; restart-recovery degraded for this
        # event). DB I/O is outside the lock so concurrent registers don't
        # serialize on persistence. Idempotent upsert (P1b): a duplicate
        # register is a no-op on the DB side.
        if self._correlation_repo is None:
            return
        try:
            self._correlation_repo.upsert_on_register(
                event_id=self._durable_key(source, instance_id_str),
                main_session_id=instance_id_str,
                task_id=task_id,
                node_id=node_id,
                retry=0,
            )
        except Exception as exc:  # noqa: BLE001 观测旁路:DB 旁写失败不阻断路由
            logger.warning(
                "[task][callback_correlation] durable register failed source=%s "
                "instance_id_str=%s: %s",
                source, instance_id_str, exc,
            )

    def resolve(self, source: str, instance_id_str: str) -> CorrelationRecord | None:
        key = (source, instance_id_str)
        with self._lock:
            rec = self._by_key.get(key)
        if rec is not None:
            return rec
        # Cache miss → DB fallback (REQ-P1 restart-recovery). A post-restart
        # resolve finds the in-memory map empty and recovers the correlation
        # from ``task_callback_correlation``. No repo → original None semantics.
        if self._correlation_repo is None:
            return None
        try:
            row = self._correlation_repo.find_by_event_id(
                self._durable_key(source, instance_id_str)
            )
        except Exception as exc:  # noqa: BLE001 查 DB 失败不抛:降级为"未关联",入站自决
            logger.warning(
                "[task][callback_correlation] DB fallback lookup failed source=%s "
                "instance_id_str=%s: %s",
                source, instance_id_str, exc,
            )
            return None
        if row is None:
            return None
        # Reconstruct the in-memory record from the table row. ``loop_task_id``
        # is rederived as ``f"{task_id}::{node_id}"`` (standard format). The
        # SSOT int ids are not persisted in the correlation table → degrade to 0,
        # matching the translator's existing unregistered-callback fallback.
        recovered = CorrelationRecord(
            task_id=row.task_id,
            node_id=row.node_id,
            loop_task_id=f"{row.task_id}::{row.node_id}",
            workflow_id=0,
            instance_id=0,
        )
        with self._lock:
            # cache-aside: don't clobber a concurrent register's richer record
            self._by_key.setdefault(key, recovered)
        return recovered
