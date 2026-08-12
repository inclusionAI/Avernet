"""TaskHarness 旁路常驻:周期巡检 SLA 超时/崩溃 → 复位 PENDING 重投。对齐 plan §3.6。

不抢正向驱动:只做"读 RUNNING → 比对超时 → 复位 PENDING 重投(经编排核 on_harness)";
不直接写 HUNG(STUCK 走 on_miss/on_fail 升 BBS 链路上限判)。复位阈值从 execution_config/extend_props 读(SLA 不在 TaskSpec)。
Avernet:in-memory 巡检(注入 clock);prod 接真实定时器/崩溃探针不变编排口。
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from agentclaw.community.core.task.domain.models import Status, TaskNodePatch, TaskNodeQueryCriteria

_DEFAULT_SLA_TIMEOUT = 30.0
_DEFAULT_INTERVAL = 1.0


class TaskHarness:
    """旁路常驻巡检器。

    驱动口:``on_harness_fn``=编排核 ``ExecutionEngine.on_harness``(复位 PENDING + 正常重投)。
    时钟/阈值为可注入 seam(单测定确定性);``register(task_id)`` 登记巡检集(facade.execute 调),
    不依赖 TaskGraphService 暴露"列出全部 task"。
    """

    def __init__(
        self,
        graph,
        on_harness_fn: Callable[[TaskNodePatch], object] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        default_sla_timeout: float = _DEFAULT_SLA_TIMEOUT,
        interval: float = _DEFAULT_INTERVAL,
    ) -> None:
        """graph: TaskGraphService(只读查询 RUNNING + execution_config);on_harness_fn: 编排核复位入口。"""
        self._graph = graph
        self._on_harness_fn = on_harness_fn
        self._clock = clock
        self._sleep = sleep
        self._default_sla = default_sla_timeout
        self._interval = interval
        self._registered: set[str] = set()
        self._dispatched_at: dict[tuple[str, str], float] = {}  # (task_id,node_id) -> 首见 RUNNING 时钟
        self._lock = threading.RLock()

    def register(self, task_id: str) -> None:
        """登记巡检任务(facade.execute 后调;未登记不巡检,避免越权巡检非本 harness 的任务)。"""
        with self._lock:
            self._registered.add(task_id)

    def set_on_harness(self, fn: Callable[[TaskNodePatch], object]) -> None:
        """组合根(facade)在构造完编排核后回填复位重投入口(编排核 ``on_harness``)。"""
        self._on_harness_fn = fn

    def _sla_timeout(self, task_id: str) -> float:
        """读 SLA_TIMEOUT(优先 execution_config,缺省 default)。"""
        try:
            cfg = self._graph._execution_config(task_id)
        except Exception:  # noqa: BLE001 - 图不存在/已删 → 退保守默认
            return self._default_sla
        t = cfg.get("SLA_TIMEOUT")
        return float(t) if t is not None else self._default_sla

    def _poll_once(self) -> list[TaskNodePatch]:
        """巡检一轮:遍历已登记 task 的 RUNNING 节点,首见记时,超时复位。

        复位经 ``on_harness_fn``(编排核 on_harness:复位 PENDING + 正常重投);未注入则只返复位 patch(测试用)。
        返回本轮应用的复位 patch 列表(测试断言用)。"""
        if self._on_harness_fn is None:
            return []
        resets: list[TaskNodePatch] = []
        seen: set[tuple[str, str]] = set()
        with self._lock:
            task_ids = list(self._registered)
        for task_id in task_ids:
            try:
                nodes = self._graph.query_task_nodes(
                    task_id, TaskNodeQueryCriteria(status=Status.RUNNING)
                )
            except Exception:  # noqa: BLE001 - task 已删 → 跳过
                continue
            sla = self._sla_timeout(task_id)
            now = self._clock()
            for n in nodes:
                key = (task_id, n.node_id)
                seen.add(key)
                t0 = self._dispatched_at.get(key)
                if t0 is None:
                    self._dispatched_at[key] = now  # 首见:记时,本轮不判
                    continue
                if now - t0 > sla:
                    resets.append(
                        TaskNodePatch(
                            task_id=task_id,
                            node_id=n.node_id,
                            status=Status.PENDING,
                            extend_props_patch={"harness_reset": "timeout", "prev_start_time": t0},
                        )
                    )
        with self._lock:
            # 淘汰已非 RUNNING 的记时项
            self._dispatched_at = {k: v for k, v in self._dispatched_at.items() if k in seen}
        for p in resets:
            self._on_harness_fn(p)
        return resets

    def run_poll_loop(self, stop_event: threading.Event | None = None) -> None:
        """周期巡检直到 stop_event.set()(未传则永不停止;编排核主链事件驱动续推,本循环仅旁路复位)。

        不抢正向:复位后由编排核 on_harness 内部重投(非本循环直接驱动)。"""
        if stop_event is None:
            stop_event = threading.Event()
        while not stop_event.is_set():
            self._poll_once()
            self._sleep(self._interval)
