"""TaskHarness 旁路常驻:周期巡检 SLA 超时/崩溃 → 复位 PENDING 重投。对齐 plan §3.6。

harness 三路巡检——① RUNNING 真执行叶子超 SLA → 复位重投(bbs 走 lease-expire 终态不重派);② status=FAILED(执行层失败)真执行叶子 → re-dispatch 重试;③ PENDING 未派发超时 → 重搜推。acceptance-FAIL 不入 harness:验收 verdict FAILED 经 on_report 记录为节点 DONE(内容未通过验收,不重派),故 Scan② 扫到的 FAILED 仅执行层失败、不含验收不通过。经编排核 on_harness 计 harness_retries:<MAX 重派 / >=MAX HUNG→升 BBS。不抢正向驱动。
不直接写 HUNG(HUNG 由编排核 _hung_and_escalate 落:on_miss 深度闸门 / on_harness 重试达 MAX_HARNESS → 节点 HUNG + 升 BBS)。复位阈值从 execution_config/extend_props 读(SLA 不在 TaskSpec)。
Avernet:in-memory 巡检(注入 clock);prod 接真实定时器/崩溃探针不变编排口。
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Callable

from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    AcceptanceVerdict,
    Status,
    TaskNodePatch,
    TaskNodeQueryCriteria,
    effective_run_mode,
)

_DEFAULT_SLA_TIMEOUT = 600.0  # single_bot/BBS RUNNING 卡死 backstop(>poller execute SLA 600s)
_DEFAULT_COOP_GROUP_SLA_TIMEOUT = 900.0  # coop_group 超时 15 分钟,给群会话更长的收敛窗口
_DEFAULT_PENDING_TIMEOUT = 180.0  # PENDING 派发异常/未派发→重搜推(短阈值尽快重试)
_DEFAULT_INTERVAL = 120.0        # 巡检间隔 2min(RUNNING/PENDING/FAILED 三扫一次;FAILED 仅执行层失败:验收 FAIL 已记录 DONE 不在此,external FAILED 由 on_harness 入口跳过)


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
        default_coop_group_sla_timeout: float = _DEFAULT_COOP_GROUP_SLA_TIMEOUT,
        default_pending_timeout: float = _DEFAULT_PENDING_TIMEOUT,
        interval: float = _DEFAULT_INTERVAL,
    ) -> None:
        """graph: TaskGraphService(只读查询 RUNNING + execution_config);on_harness_fn: 编排核复位入口。
        sla_timeout:RUNNING 卡死 backstop(>poller execute SLA);pending_timeout:PENDING 派发异常重搜推。"""
        self._graph = graph
        self._on_harness_fn = on_harness_fn
        self._clock = clock
        self._sleep = sleep
        self._default_sla = default_sla_timeout
        self._default_coop_group_sla = default_coop_group_sla_timeout
        self._default_pending = default_pending_timeout
        self._interval = interval
        self._registered: set[str] = set()
        self._dispatched_at: dict[tuple[str, str], float] = {}  # (task_id,node_id) -> 首见 RUNNING 时钟
        self._pending_seen_at: dict[tuple[str, str], float] = {}  # (task_id,node_id) -> 首见 PENDING(未派发)时钟
        self._lock = threading.RLock()

    def register(self, task_id: str) -> None:
        """登记巡检任务(facade.execute 后调;未登记不巡检,避免越权巡检非本 harness 的任务)。"""
        with self._lock:
            self._registered.add(task_id)

    def set_on_harness(self, fn: Callable[[TaskNodePatch], object]) -> None:
        """组合根(facade)在构造完编排核后回填复位重投入口(编排核 ``on_harness``)。"""
        self._on_harness_fn = fn

    def _sla_timeout(self, task_id: str, node=None) -> float:
        """读节点 RUNNING SLA。

        execution_config.SLA_TIMEOUT 仍可统一覆盖;未配置时,协作群默认 15 分钟,
        single_bot/BBS 继续使用原默认值。
        """
        try:
            cfg = self._graph._execution_config(task_id)
        except Exception:  # noqa: BLE001 - 图不存在/已删 → 退保守默认
            cfg = {}
        t = cfg.get("SLA_TIMEOUT")
        if t is not None:
            return float(t)
        if node is not None and effective_run_mode(node) == "coop_group":
            return self._default_coop_group_sla
        return self._default_sla

    def _pending_timeout(self, task_id: str) -> float:
        """读 PENDING_TIMEOUT(派发异常/未派发→重搜推;优先 execution_config,缺省 default)。"""
        try:
            cfg = self._graph._execution_config(task_id)
        except Exception:  # noqa: BLE001
            return self._default_pending
        t = cfg.get("PENDING_TIMEOUT")
        return float(t) if t is not None else self._default_pending

    def _poll_once(self) -> list[TaskNodePatch]:
        """巡检一轮:遍历已登记 task 的 RUNNING 节点,首见记时,超时复位。节点执行模态优先读取 actual_run_mode。

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
                    task_id,
                    TaskNodeQueryCriteria(status=Status.RUNNING, has_child_tasks=True),
                )
            except Exception:  # noqa: BLE001 - task 已删 → 跳过
                continue
            # 只监控真正派发执行的叶子(run_mode ∈ 三模态);委托态父节点是 Status.PLANNING(非 RUNNING),
            # 不执行 bot run,不纳入 SLA 超时巡检(避免误复位委托中的分解/聚合节点)。
            _EXEC_MODES = ("single_bot", "coop_group", "bbs")
            nodes = [n for n in nodes if effective_run_mode(n) in _EXEC_MODES]
            now = self._clock()
            for n in nodes:
                sla = self._sla_timeout(task_id, n)
                key = (task_id, n.node_id)
                seen.add(key)
                t0 = self._dispatched_at.get(key)
                if t0 is None:
                    self._dispatched_at[key] = now  # 首见:记时,本轮不判
                    continue
                if now - t0 > sla:
                    if effective_run_mode(n) == "bbs":
                        # BBS lease 到期(FR-EXT-06):owner bot 崩溃/挂起导致 RUNNING 超 SLA。
                        # 直写图(self._graph),不走 on_harness_fn:后者复位 RUNNING→PENDING 重派,
                        # 与"标终态不重派"语义相反。① scoped 节点验收 FAIL→DONE(终态);
                        # ② 清根 bbs_owner(root node_id == task_id)释放接力所有权;continue 跳过 PENDING reset。
                        self._graph.update_task_node_info(TaskNodePatch(
                            task_id=task_id, node_id=n.node_id,
                            acceptance_result=AcceptanceResult(
                                verdict=AcceptanceVerdict.FAILED, gaps=["bbs_lease_expired"])))
                        self._graph.update_task_node_info(TaskNodePatch(
                            task_id=task_id, node_id=task_id,
                            extend_props_patch={"bbs_owner": None}))
                        continue
                    resets.append(
                        TaskNodePatch(
                            task_id=task_id,
                            node_id=n.node_id,
                            status=Status.PENDING,
                            extend_props_patch={"harness_reset": "timeout", "prev_start_time": t0},
                        )
                    )
        with self._lock:
            # 淘汰已非 RUNNING 的记时项。对本轮触发的重试节点也必须清零
            # 首次 RUNNING 计时,否则重试后的下一轮会沿用上一次尝试的 t0,
            # 把多次尝试累计计时,导致刚重试约一个巡检周期就再次超时。
            self._dispatched_at = {k: v for k, v in self._dispatched_at.items() if k in seen}
            for patch in resets:
                self._dispatched_at.pop((patch.task_id, patch.node_id), None)
        # Scan②:扫描 status=FAILED(执行层失败:terminal_invalid/exec 报错等)真执行叶子 → harness
        # 重新派发执行重试。**验收不过(verdict FAILED)已由 on_report 记录为节点 DONE,不在此扫**——
        # 故此处 FAILED 仅执行层失败,与验收 gap(DONE,不重派)语义不同。FAILED 不走 SLA 计时,
        # 立即交 on_harness(计数 harness_retries:<MAX 复位重派 / >=MAX HUNG 升 BBS)。
        failed_resets: list[TaskNodePatch] = []
        _EXEC_MODES = ("single_bot", "coop_group", "bbs")
        for task_id in task_ids:
            try:
                failed = self._graph.query_task_nodes(
                    task_id,
                    TaskNodeQueryCriteria(status=Status.FAILED, has_child_tasks=True),
                )
            except Exception:  # noqa: BLE001
                continue
            for n in failed:
                if effective_run_mode(n) not in _EXEC_MODES:
                    continue
                if effective_run_mode(n) == "bbs":
                    # bbs 节点 bot 自驱;FAILED 后由下个 bot 接力挂新节点(§10.4),harness 不重派。
                    # 与 RUNNING-scan 的 bbs lease-expire 分支一致(标终态不重派 FR-EXT-06)。
                    continue
                failed_resets.append(TaskNodePatch(
                    task_id=task_id, node_id=n.node_id, exec_error="exec_failed_retry"))
        # v4:扫描 PENDING(搜推无响应/推理失败/派发失败)未派发节点,按 SLA 超时触发 harness 重试搜推。
        # 只盯「未派发」PENDING(无 run_mode+assignee);已派发待 start_run 翻转的不纳入(避免误重投)。
        # backoff:首见记时,等满 SLA 才触发;触发后重置计时(下次仍需等满 SLA)。MISS→on_miss 自闭环,不在此。
        pending_resets: list[TaskNodePatch] = []
        pending_seen: set[tuple[str, str]] = set()
        for task_id in task_ids:
            try:
                pnodes = self._graph.query_task_nodes(
                    task_id, TaskNodeQueryCriteria(status=Status.PENDING)
                )
            except Exception:  # noqa: BLE001
                continue
            # 只盯「未派发」PENDING:无 run_mode+assignee(未决出执行者);排除 dispatching 飞行态
            # (已交付 _drain 待 start_run/拉群翻 RUNNING,慢 IO 不应误判超时)与已有 assignee 的 reset 节点(reset 由 RUNNING/FAILED 巡检 inline 处理)
            pnodes = [n for n in pnodes
                      if not (n.run_info.run_mode and n.run_info.assignee)
                      and not n.run_info.extend_props.get("dispatching")]
            pto = self._pending_timeout(task_id)  # PENDING 派发超时(独立于 RUNNING SLA)
            now = self._clock()
            for n in pnodes:
                key = (task_id, n.node_id)
                pending_seen.add(key)
                t0 = self._pending_seen_at.get(key)
                if t0 is None:
                    self._pending_seen_at[key] = now  # 首见:记时,本轮不判
                    continue
                if now - t0 > pto:
                    pending_resets.append(TaskNodePatch(
                        task_id=task_id, node_id=n.node_id, exec_error="pending_dispatch_stuck"))
                    self._pending_seen_at[key] = now  # 重启 backoff:下次仍需等满 PENDING_TIMEOUT 才再重试
        with self._lock:
            self._pending_seen_at = {k: v for k, v in self._pending_seen_at.items() if k in pending_seen}
        with self._lock:
            for patch in (*failed_resets, *pending_resets):
                self._dispatched_at.pop((patch.task_id, patch.node_id), None)
        for p in resets:
            res = self._on_harness_fn(p)
            if asyncio.iscoroutine(res):
                asyncio.run(res)
        for p in failed_resets:
            res = self._on_harness_fn(p)
            if asyncio.iscoroutine(res):
                asyncio.run(res)
        for p in pending_resets:
            res = self._on_harness_fn(p)
            if asyncio.iscoroutine(res):
                asyncio.run(res)
        return resets

    def run_poll_loop(self, stop_event: threading.Event | None = None) -> None:
        """周期巡检直到 stop_event.set()(未传则永不停止;编排核主链事件驱动续推,本循环仅旁路复位)。

        不抢正向:复位后由编排核 on_harness 内部重投(非本循环直接驱动)。"""
        if stop_event is None:
            stop_event = threading.Event()
        while not stop_event.is_set():
            self._poll_once()
            self._sleep(self._interval)
