"""TaskHarness 旁路常驻:周期巡检 SLA 超时/崩溃 → 复位 PENDING 重投。对齐 plan §3.6。

harness 三路巡检——① RUNNING 真执行叶子超 SLA → 复位重投(bbs 走 lease-expire 终态不重派);② status=FAILED(执行层失败)真执行叶子 → re-dispatch 重试;③ PENDING 未派发超时 → 重搜推。acceptance-FAIL 不入 harness:验收 verdict FAILED 经 on_report 记录为节点 DONE(内容未通过验收,不重派),故 Scan② 扫到的 FAILED 仅执行层失败、不含验收不通过。经编排核 on_harness 计 harness_retries:<MAX 重派 / >=MAX HUNG→升 BBS。不抢正向驱动。
不直接写 HUNG(HUNG 由编排核 _hung_and_escalate 落:on_miss 深度闸门 / on_harness 重试达 MAX_HARNESS → 节点 HUNG + 升 BBS)。复位阈值从 execution_config/extend_props 读(SLA 不在 TaskSpec)。
Avernet:in-memory 巡检(注入 clock);prod 接真实定时器/崩溃探针不变编排口。
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import threading
import time
from dataclasses import replace
from typing import Any, Callable

from agentclaw.community.core.task.domain.models import (
    TaskNode,
    AcceptanceResult,
    AcceptanceVerdict,
    Status,
    TaskCallbackData,
    TaskGraphPatch,
    TaskNodePatch,
    TaskNodeQueryCriteria,
    effective_run_mode,
)
from agentclaw.community.core.task.task_harness.relay_runtime_policy import (
    active_no_progress_cycle,
    resolve_relay_resume_step,
    relay_resume_attempts,
    terminal_success_unreported_seconds,
)
from agentclaw.community.core.task.task_harness.relay_resume_trajectory import (
    emit_relay_resume_trajectory,
)
from agentclaw.community.core.task.task_harness.relay_session_recovery import (
    RelaySessionRecovery,
    RelaySessionRecoveryError,
)
from agentclaw.community.core.task.task_harness.runtime_probe import (
    RuntimeProbeResult,
    TaskRuntimeLivenessProbe,
)

logger = logging.getLogger("task.harness")

_DEFAULT_SLA_TIMEOUT = (
    900.0  # single_bot/BBS RUNNING:统一给 15 分钟窗口
)
_DEFAULT_COOP_GROUP_SLA_TIMEOUT = 900.0  # coop_group RUNNING:15 分钟
_DEFAULT_PENDING_TIMEOUT = 180.0  # PENDING 派发异常/未派发→重搜推(短阈值尽快重试)
_DEFAULT_INTERVAL = 120.0  # 巡检间隔 2min(RUNNING/PENDING/FAILED 三扫一次;FAILED 仅执行层失败:验收 FAIL 已记录 DONE 不在此,external FAILED 由 on_harness 入口跳过)

class TaskHarness:
    """旁路常驻巡检器。

    驱动口:``on_harness_fn``=编排核 ``CentralizedExecutionAdapter.on_harness``(复位 PENDING + 正常重投)。
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
        wall_clock_ms: Callable[[], int] | None = None,
        runtime_probe: TaskRuntimeLivenessProbe | None = None,
        runtime_probe_threshold: float = 120.0,
        runtime_retry_threshold: int = 3,
        relay_recovery: RelaySessionRecovery | None = None,
        relay_resume_max_attempts: int = 2,
        task_context_service: Any | None = None,
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
        self._wall_clock_ms = wall_clock_ms or (lambda: int(time.time() * 1000))
        self._registered: set[str] = set()
        self._dispatched_at: dict[
            tuple[str, str], float
        ] = {}  # (task_id,node_id) -> 首见 RUNNING 时钟
        self._runtime_probe = runtime_probe
        self._runtime_probe_threshold = runtime_probe_threshold
        self._runtime_retry_threshold = runtime_retry_threshold
        self._relay_recovery = relay_recovery
        self._relay_resume_max_attempts = max(1, int(relay_resume_max_attempts))
        self._task_context_service = task_context_service
        self._runtime_seen: dict[tuple[str, str], dict[str, Any]] = {}
        self._pending_seen_at: dict[
            tuple[str, str], float
        ] = {}  # (task_id,node_id) -> 首见 PENDING(未派发)时钟
        self._lock = threading.RLock()

    def register(self, task_id: str) -> None:
        """登记巡检任务(facade.execute 后调;未登记不巡检,避免越权巡检非本 harness 的任务)。"""
        with self._lock:
            self._registered.add(task_id)

    def set_on_harness(self, fn: Callable[[TaskNodePatch], object]) -> None:
        """组合根(facade)在构造完编排核后回填复位重投入口(编排核 ``on_harness``)。"""
        self._on_harness_fn = fn

    def _report_node_patch(self, patch: TaskNodePatch):
        return self._graph.report(
            TaskCallbackData(
                data={
                    "report_type": "NODE_PATCH",
                    "payload": {"patch": patch},
                }
            )
        )

    def _report_graph_patch(self, task_id: str, patch: TaskGraphPatch) -> None:
        self._graph.report(
            TaskCallbackData(
                data={
                    "report_type": "GRAPH_PATCH",
                    "payload": {"task_id": task_id, "patch": patch},
                }
            )
        )

    def _on_harness_safely(self, patch: TaskNodePatch) -> None:
        """Execute one recovery patch without letting one task kill the poll loop."""

        try:
            res = self._on_harness_fn(patch)
            if asyncio.iscoroutine(res):
                asyncio.run(res)
        except Exception:  # noqa: BLE001 - harness is a daemon independent of main flow
            logger.exception(
                "[task][harness] on_harness failed task=%s node=%s",
                patch.task_id, patch.node_id,
            )

    def _sla_timeout(self, task_id: str, node=None) -> float:
        """读节点 RUNNING SLA。

        execution_config.SLA_TIMEOUT 仍可统一覆盖;未配置时,三种模态默认 15 分钟。
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

    def _is_relay_root(self, graph, node) -> bool:
        """The first Relay baton lives in the originating conversation.

        Its token is embedded in a one-time root instruction. Unlike a
        Runner-delivered successor, Backend has no authoritative redelivery
        attempt for that baton, so Harness must not expire or rotate it.
        """
        return bool(
            graph.is_relay
            and (
                node.node_id == graph.task_id
                or node.run_info.extend_props.get("relay_root")
            )
        )

    def _is_relay_mode(self, graph) -> bool:
        """Return True for a Relay-mode task graph."""
        return bool(graph.is_relay)

    async def _relay_runtime_harness(
        self,
        task_id: str,
        node: TaskNode,
        *,
        elapsed: float,
        sla: float,
    ) -> TaskNodePatch | None:
        """Turn one Relay runtime observation into a harness decision.

        Pure elapsed time is only a duty-cycle trigger here, never failure
        evidence. A node is retried only after authoritative terminal failure,
        terminal success without a task report, or repeated unknown/unresponsive
        probes. Active state without progress is counted but remains engine-owned
        until the engine reaches its own timeout or terminal state.
        """
        if self._runtime_probe is None:
            return None
        key = (task_id, node.node_id)
        current = self._runtime_seen.setdefault(
            key,
            {
                "cursor": None,
                "unknown": 0,
                "no_progress": 0,
                "success_unreported": 0,
                "resume_attempts": 0,
                "resume_send_failures": 0,
                "probe_unavailable": 0,
                "recovery_epoch_started_at": self._clock(),
            },
        )
        try:
            result = await self._runtime_probe.probe(
                node, previous_cursor=current.get("cursor")
            )
        except Exception as exc:  # noqa: BLE001 - probing must never break harness
            logger.warning(
                "[task][harness] runtime probe failed task=%s node=%s error=%s",
                task_id, node.node_id, str(exc)[:500], exc_info=True,
            )
            result = RuntimeProbeResult(
                state="unknown", error=f"{type(exc).__name__}: {exc}"[:500]
            )

        if result.state != "probe_unavailable":
            # Successful/observable probes update the cursor; an unavailable probe
            # has no observation and therefore must not overwrite the last known
            # cursor. Otherwise the next successful probe could be misjudged as
            # fresh progress after service instability.
            current["cursor"] = result.heartbeat_cursor
        resume_step = resolve_relay_resume_step(task_id, node, self._graph)
        attempts = relay_resume_attempts(node, current)
        recovery_epoch_elapsed = max(
            0.0,
            self._clock() - float(current.get("recovery_epoch_started_at", 0.0)),
        )
        detail = {
            "runtime_state": result.state,
            "runtime_status": result.status,
            "runtime_source": result.source,
            "runtime_error": result.error,
            "resume_step": resume_step,
            "recovery_epoch_seconds": recovery_epoch_elapsed,
        }
        reason: str | None = None

        if result.state == "terminal_failed":
            reason = "runtime_failed"
        elif result.state == "terminal_success":
            current["success_unreported"] += 1
            current["unknown"] = 0
            current["no_progress"] = 0
            if recovery_epoch_elapsed >= terminal_success_unreported_seconds(
                attempts
            ):
                reason = "terminal_success_unreported"
        elif result.state == "active":
            current["success_unreported"] = 0
            current["unknown"] = 0
            if result.progressed:
                current["no_progress"] = 0
            else:
                current["no_progress"] += 1
            if current["no_progress"] >= active_no_progress_cycle(
                attempts
            ):
                reason = "runtime_no_progress"
        elif result.state == "probe_unavailable":
            # Transport/app request failure is not Bot unresponsiveness. Predeploy
            # and single-server deployments can make the probe read path itself
            # intermittently unavailable; therefore this state must never consume
            # recovery attempts or advance the task's failure state.
            current["unknown"] = 0
            current["no_progress"] = 0
            current["probe_unavailable"] = (
                current.get("probe_unavailable", 0) + 1
            )
        elif result.state == "unknown":
            current["unknown"] += 1
            current["no_progress"] = 0
            current["probe_unavailable"] = 0
            if current["unknown"] >= self._runtime_retry_threshold:
                reason = "runtime_unresponsive"

        if reason is None:
            logger.info(
                "[task][harness] runtime probe observed task=%s node=%s state=%s "
                "status=%s source=%s progressed=%s no_progress=%s elapsed=%.0fs",
                task_id, node.node_id, result.state, result.status, result.source,
                result.progressed,
                current.get("no_progress", 0) if result.state == "active" else 0,
                elapsed,
            )
            return None

        logger.warning(
            "[task][harness] relay runtime recovery task=%s node=%s reason=%s state=%s "
            "status=%s source=%s elapsed=%.0fs sla=%.0fs",
            task_id, node.node_id, reason, result.state, result.status,
            result.source, elapsed, sla,
        )
        if attempts < self._relay_resume_max_attempts:
            error_msg = None
            channel = None
            try:
                if self._relay_recovery is None:
                    raise RelaySessionRecoveryError("relay recovery service is not configured")
                resumed = await self._relay_recovery.resume(
                    node, resume_step=resume_step
                )
                channel = resumed.channel
            except Exception as exc:  # noqa: BLE001 - recoverable transport error
                error_msg = f"{type(exc).__name__}: {exc}"[:500]
                current["resume_send_failures"] += 1
                logger.warning(
                    "[task][harness] relay session resume send failed task=%s "
                    "node=%s delivered_attempts=%d/%d send_failures=%d error=%s",
                    task_id, node.node_id, attempts,
                    self._relay_resume_max_attempts,
                    current["resume_send_failures"], error_msg,
                )
                # A request/deployment failure means the resume prompt was not
                # delivered to the Bot session. It is not a resume attempt and
                # must not advance the same-session recovery budget.
                return None
            current["resume_attempts"] = attempts + 1
            current["resume_send_failures"] = 0
            # Open a new recovery epoch after a delivered resume.  The same-node
            # resume budget is cumulative, and the heartbeat baseline is cleared
            # so this prompt itself cannot be counted as fresh progress.
            current["recovery_epoch_started_at"] = self._clock()
            current["cursor"] = None
            current["unknown"] = 0
            current["no_progress"] = 0
            current["success_unreported"] = 0
            current["probe_unavailable"] = 0
            self._report_node_patch(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=node.node_id,
                    extend_props_patch={
                        "relay_harness_resume_attempts": attempts + 1,
                        **(
                            {"relay_harness_resume_run_id": resumed.dispatch_handle}
                            if resumed.dispatch_handle
                            else {}
                        ),
                        "harness_probe": {
                            **detail,
                            "reason": reason,
                            "elapsed_seconds": elapsed,
                            "sla_seconds": sla,
                            "unknown_count": current.get("unknown", 0),
                            "no_progress_count": current.get("no_progress", 0),
                            "success_unreported_count": current.get(
                                "success_unreported", 0
                            ),
                            "resume_attempts": attempts + 1,
                            "resume_channel": channel,
                            "resume_error": error_msg,
                        },
                    },
                )
            )
            emit_relay_resume_trajectory(
                self._task_context_service,
                task_id,
                node,
                attempt=attempts + 1,
                reason=reason,
                channel=channel,
                error_msg=error_msg,
                terminal=False,
            )
            if error_msg is None:
                current["unknown"] = 0
                current["no_progress"] = 0
                current["success_unreported"] = 0
            return None

        terminal_reason = f"relay_harness_resume_exhausted: {reason}"
        logger.error(
            "[task][harness] relay session resume exhausted task=%s node=%s "
            "reason=%s attempts=%d/%d",
            task_id, node.node_id, reason, attempts,
            self._relay_resume_max_attempts,
        )
        return TaskNodePatch(
            task_id=task_id,
            node_id=node.node_id,
            status=Status.HUNG,
            failure_reason=terminal_reason,
            extend_props_patch={
                "relay_harness_resume_attempts": attempts,
                "harness_probe": {
                    **detail,
                    "reason": reason,
                    "elapsed_seconds": elapsed,
                    "sla_seconds": sla,
                    "unknown_count": current.get("unknown", 0),
                    "no_progress_count": current.get("no_progress", 0),
                    "success_unreported_count": current.get(
                        "success_unreported", 0
                    ),
                    "resume_attempts": attempts,
                    "terminal_reason": terminal_reason,
                },
            },
        )

    def _await_runtime_harness(
        self, task_id: str, node: TaskNode, *, elapsed: float, sla: float
    ) -> TaskNodePatch | None:
        coroutine = self._relay_runtime_harness(
            task_id, node, elapsed=elapsed, sla=sla
        )
        return asyncio.run(coroutine)

    def _pending_timeout(self, task_id: str) -> float:
        """读 PENDING_TIMEOUT(派发异常/未派发→重搜推;优先 execution_config,缺省 default)。"""
        try:
            cfg = self._graph._execution_config(task_id)
        except Exception:  # noqa: BLE001
            return self._default_pending
        t = cfg.get("PENDING_TIMEOUT")
        return float(t) if t is not None else self._default_pending

    def _poll_once(self) -> list[TaskNodePatch]:
        """巡检一轮 RUNNING/PENDING/FAILED 节点。

        RUNNING 节点按 actual_run_mode 计时;Relay 模式的 RUNNING 节点
        不做纯时间 reset,因为没有真实失败事实时,超时不能证明会话已死。
        PENDING 与 FAILED 仍按原逻辑派发/重投。
        """
        if self._on_harness_fn is None:
            return []
        resets: list[TaskNodePatch] = []
        relay_patches: list[TaskNodePatch] = []
        seen: set[tuple[str, str]] = set()
        with self._lock:
            task_ids = list(self._registered)
        for task_id in task_ids:
            try:
                task_graph = self._graph.query_task_dashboard(task_id)
                nodes = self._graph.query_task_nodes(
                    task_id,
                    TaskNodeQueryCriteria(status=Status.RUNNING, has_child_tasks=True),
                )
            except Exception:  # noqa: BLE001 - task 已删 → 跳过
                continue
            # 只监控真正派发执行的叶子(run_mode ∈ 三模态);委托态父节点是 Status.PLANNING(非 RUNNING),
            # 不执行 bot run,不纳入 SLA 超时巡检(避免误复位委托中的分解/聚合节点)。
            _EXEC_MODES = ("single_bot", "coop_group", "bbs")
            nodes = [
                n
                for n in nodes
                if effective_run_mode(n) in _EXEC_MODES
            ]
            now = self._clock()
            for n in nodes:
                sla = self._sla_timeout(task_id, n)
                key = (task_id, n.node_id)
                seen.add(key)
                t0 = self._dispatched_at.get(key)
                if t0 is None:
                    self._dispatched_at[key] = now  # 首见:记时,本轮不判
                    continue
                if task_graph.is_relay:
                    elapsed = now - t0
                    if elapsed < self._runtime_probe_threshold:
                        continue
                    runtime_patch = self._await_runtime_harness(
                        task_id, n, elapsed=elapsed, sla=sla
                    )
                    if runtime_patch is not None:
                        self._report_node_patch(runtime_patch)
                        emit_relay_resume_trajectory(
                            self._task_context_service,
                            task_id,
                            n,
                            attempt=relay_resume_attempts(
                                n, self._runtime_seen.get((task_id, n.node_id), {})
                            ),
                            reason=str(
                                (runtime_patch.extend_props_patch or {}).get(
                                    "harness_probe", {}
                                ).get("reason") or runtime_patch.failure_reason or ""
                            ),
                            channel=None,
                            error_msg=runtime_patch.failure_reason,
                            terminal=True,
                        )
                        relay_patches.append(runtime_patch)
                    continue
                if now - t0 > sla:
                    if effective_run_mode(n) == "bbs" and not task_graph.is_relay:
                        # BBS lease 到期(FR-EXT-06):owner bot 崩溃/挂起导致 RUNNING 超 SLA。
                        # 直写图(self._graph),不走 on_harness_fn:后者复位 RUNNING→PENDING 重派,
                        # 与"标终态不重派"语义相反。① scoped 节点验收 FAIL→DONE(终态);
                        # ② 清根 bbs_owner(root node_id == task_id)释放接力所有权;continue 跳过 PENDING reset。
                        self._report_node_patch(
                            TaskNodePatch(
                                task_id=task_id,
                                node_id=n.node_id,
                                acceptance_result=AcceptanceResult(
                                    verdict=AcceptanceVerdict.FAILED,
                                    gap_items=["bbs_lease_expired"],
                                ),
                            )
                        )
                        self._report_node_patch(
                            TaskNodePatch(
                                task_id=task_id,
                                node_id=task_id,
                                extend_props_patch={"bbs_owner": None},
                            )
                        )
                        continue
                    resets.append(
                        TaskNodePatch(
                            task_id=task_id,
                            node_id=n.node_id,
                            status=Status.PENDING,
                            extend_props_patch={
                                "harness_reset": "timeout",
                                "prev_start_time": t0,
                            },
                        )
                    )
        with self._lock:
            # 淘汰已非 RUNNING 的记时项。对本轮触发的重试节点也必须清零
            # 首次 RUNNING 计时,否则重试后的下一轮会沿用上一次尝试的 t0,
            # 把多次尝试累计计时,导致刚重试约一个巡检周期就再次超时。
            self._dispatched_at = {
                k: v for k, v in self._dispatched_at.items() if k in seen
            }
            self._runtime_seen = {
                k: v for k, v in self._runtime_seen.items() if k in seen
            }
            for patch in (*resets, *relay_patches):
                self._dispatched_at.pop((patch.task_id, patch.node_id), None)
                self._runtime_seen.pop((patch.task_id, patch.node_id), None)
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
                if self._is_relay_root(task_graph, n):
                    continue
                if effective_run_mode(n) == "bbs":
                    # bbs 节点 bot 自驱;FAILED 后由下个 bot 接力挂新节点(§10.4),harness 不重派。
                    # 与 RUNNING-scan 的 bbs lease-expire 分支一致(标终态不重派 FR-EXT-06)。
                    continue
                failed_resets.append(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=n.node_id,
                        exec_error="exec_failed_retry",
                    )
                )
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
            pnodes = [
                n
                for n in pnodes
                if not (n.run_info.run_mode and n.run_info.assignee)
                and not n.run_info.extend_props.get("dispatching")
            ]
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
                    pending_resets.append(
                        TaskNodePatch(
                            task_id=task_id,
                            node_id=n.node_id,
                            exec_error="pending_dispatch_stuck",
                        )
                    )
                    self._pending_seen_at[key] = (
                        now  # 重启 backoff:下次仍需等满 PENDING_TIMEOUT 才再重试
                    )
        with self._lock:
            self._pending_seen_at = {
                k: v for k, v in self._pending_seen_at.items() if k in pending_seen
            }
        with self._lock:
            for patch in (*failed_resets, *pending_resets):
                self._dispatched_at.pop((patch.task_id, patch.node_id), None)
        for p in resets:
            self._on_harness_safely(p)
        for p in failed_resets:
            self._on_harness_safely(p)
        for p in pending_resets:
            self._on_harness_safely(p)
        return [*resets, *relay_patches]

    def run_poll_loop(self, stop_event: threading.Event | None = None) -> None:
        """周期巡检直到 stop_event.set()(未传则永不停止;编排核主链事件驱动续推,本循环仅旁路复位)。

        不抢正向:复位后由编排核 on_harness 内部重投(非本循环直接驱动)。"""
        if stop_event is None:
            stop_event = threading.Event()
        while not stop_event.is_set():
            try:
                self._poll_once()
            except Exception:  # noqa: BLE001 - daemon must survive one bad cycle
                logger.exception("[task][harness] poll cycle failed")
            self._sleep(self._interval)

    def _static_fallback_delay(self, task_id: str) -> float:
        """固定流程真实上报的兜底超时:节点真实派发后,若该时长内无真实回投,则以 mock 兜底推进,

        避免整流程因单节点不上报而卡死。仅固定 plan 任务生效(由调度点保证)。

        优先级:execution_config.static_fallback_timeout → env OCB_TASK_STATIC_FALLBACK_TIMEOUT → 80.0。"""

        cfg = self._graph._execution_config(task_id)

        v = cfg.get("static_fallback_timeout")

        if v in (None, ""):
            raw = os.environ.get("OCB_TASK_STATIC_FALLBACK_TIMEOUT")

            v = raw if raw not in (None, "") else None

        try:
            return float(v) if v is not None else 80.0

        except (TypeError, ValueError):
            return 80.0

    def _static_mock_fallback_delay(self, task_id: str, node_id: str) -> float:
        """固定流程真实上报兜底 mock 的随机延迟(取消固定 fallback 超时):

        单 bot 节点随机 20-40s,协作群节点随机 40-80s;auto 演示模式仍走 _static_auto_report_delay 短延迟。

        无法定 node_type 时按单 bot 处理(20-40)。真实回投先到则本兜底自跳过,不被使用。"""

        runtime = self._static_runtime(task_id)

        definition = runtime.by_id.get(node_id) if runtime is not None else None

        is_group = definition is not None and definition.node_type == "collaboration"

        return random.uniform(40.0, 80.0) if is_group else random.uniform(20.0, 40.0)

    def _static_auto_report_delay(self, task_id: str) -> float:
        """自驱 mock 上报延迟秒数:execution_config.static_auto_report_delay →

        env OCB_TASK_STATIC_AUTO_REPORT_DELAY → random.uniform(20,60)(每节点完成节奏不一,

        演示时能看出节点状态逐次流转而非瞬间全 DONE)。"""

        cfg = self._graph._execution_config(task_id)

        v = cfg.get("static_auto_report_delay")

        if v in (None, ""):
            raw = os.environ.get("OCB_TASK_STATIC_AUTO_REPORT_DELAY")

            v = raw if raw not in (None, "") else None

        try:
            return float(v) if v is not None else random.uniform(20.0, 60.0)

        except (TypeError, ValueError):
            return random.uniform(20.0, 60.0)

    def _bbs_handoff_delay(self, task_id: str) -> float:
        """BBS 交接"被接"延迟秒数(①入广场→②被接):execution_config.bbs_handoff_claim_delay →

        env OCB_BBS_HANDOFF_CLAIM_DELAY → 30.0。"""

        cfg = self._graph._execution_config(task_id)

        v = cfg.get("bbs_handoff_claim_delay")

        if v in (None, ""):
            raw = os.environ.get("OCB_BBS_HANDOFF_CLAIM_DELAY")

            v = raw if raw not in (None, "") else None

        try:
            return float(v) if v is not None else 30.0

        except (TypeError, ValueError):
            return 30.0

    def _on_bbs_handoff_done(self, t: "asyncio.Task") -> None:
        """BBS 交接后台任务完成:脱离跟踪集 + 异常可见。"""

        self._bg_tasks.discard(t)

        if t.cancelled():
            return

        exc = t.exception()

        if exc is not None:
            logger.error(
                "[task][static-plan] bbs_handoff bg task 异常: %s", exc, exc_info=exc
            )

    async def _bbs_handoff_claim(
        self, task_id: str, node_id: str, rnd_bot_id: str, items: Any
    ) -> None:
        """BBS 交接"被接":延迟后真实 start_run 发安全架构师,成功翻 claimed + 展示安全架构师;

        auto 模式随即 mock 上报 PASS→SUCCESS(派发完成即交接完成),真实模式留给 poller。

        失败留 PENDING(post_failed),不掩盖真实派发失败。"""

        delay = self._bbs_handoff_delay(task_id)

        logger.info(
            "[task][static-plan] bbs_handoff claim scheduled task=%s node=%s in %.1fs rnd_bot=%s",
            task_id,
            node_id,
            delay,
            rnd_bot_id,
        )

        await asyncio.sleep(delay)

        graph = self._graph.query_task_dashboard(task_id)

        node = next((n for n in graph.tasks if n.node_id == node_id), None)

        if node is None or node.status != Status.PENDING:
            logger.info(
                "[task][static-plan] bbs_handoff skip task=%s node=%s status=%s (非 PENDING)",
                task_id,
                node_id,
                node.status.value if node is not None else None,
            )

            return

        # ① 图中保留 bbs 来源语义；实际交接给胜出的研发 bot 时，构造 single_bot

        #    执行视图并走统一 start_run，避免把已 claim 的任务再次投回 BBS 广场。

        node.run_info.assignee = rnd_bot_id

        node.run_info.run_mode = "bbs"

        with self._lock_for(task_id):
            self._report_node_patch(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=node_id,
                    run_mode="bbs",
                    assignee=rnd_bot_id,
                    extend_props_patch={
                        "bbs_owner": rnd_bot_id,
                        "bbs_handed_to": rnd_bot_id,
                    },
                )
            )

        delivery_node = replace(
            node,
            run_info=replace(
                node.run_info,
                run_mode="single_bot",
                output=dict(node.run_info.output),
                extend_props=dict(node.run_info.extend_props),
                action_log=list(node.run_info.action_log),
            ),
        )

        # ② start_run(delivery_node) 真实投递给研发 bot；持久化节点保持 bbs 语义。

        ok = False

        node.run_info.run_mode = "single_bot"

        try:
            ok = bool(await self._runner.start_run(delivery_node))

        except Exception as ex:  # noqa: BLE101
            logger.warning(
                "[task][static-plan] bbs_handoff 安全架构师 派发异常 task=%s node=%s rnd_bot=%s: %s",
                task_id,
                node_id,
                rnd_bot_id,
                ex,
            )

            ok = False

        finally:
            node.run_info.run_mode = "bbs"

            # 清掉临时 single_bot 派发(单 bot→group 旁路)在 extend_props 泄漏的 actual_run_mode,

            # 否则 effective_run_mode() 优先读 actual_run_mode 仍判 single_bot,致 dashboard 误显单人。

            node.run_info.extend_props["actual_run_mode"] = "bbs"

        with self._lock_for(task_id):
            if not ok:
                # 真派发失败:不回 PENDING/不清 assignee,直接 no-op 翻 RUNNING(bbs 路径仍由兜底推进),

                # dashboard 可按 assignee 点开安全架构师 主会话;dispatch_error 留痕便于排查。

                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node_id,
                        status=Status.RUNNING,
                        run_mode="bbs",
                        assignee=rnd_bot_id,
                        extend_props_patch={
                            "dispatching": None,
                            "actual_run_mode": "bbs",
                            "dispatch_error": "bbs_rnd_dispatch_fallback_noop",
                            "bbs_status": "claimed_by_rnd",
                        },
                    )
                )

            else:
                # 安全架构师 真发成功:session_id/run_id 已由 dispatcher 写入 extend_props,bbs 翻 RUNNING+claimed。

                self._report_node_patch(
                    TaskNodePatch(
                        task_id=task_id,
                        node_id=node_id,
                        status=Status.RUNNING,
                        run_mode="bbs",
                        assignee=rnd_bot_id,
                        extend_props_patch={
                            "dispatching": None,
                            "actual_run_mode": "bbs",
                            "bbs_status": "claimed_by_rnd",
                        },
                    )
                )

        logger.info(
            "[task][static-plan] bbs_handoff claimed task=%s node=%s rnd_bot=%s items=%s",
            task_id,
            node_id,
            rnd_bot_id,
            items if isinstance(items, list) else type(items).__name__,
        )

        # 交接完成:固定流程 bbs_handoff 始终调度兜底上报——真实 poller 在 timeout 内闭环则自跳过;

        # 否则超时后 mock PASS→SUCCESS,避免旁路节点长挂致整个固定流程永不终态。

        # auto 演示模式用短延迟;默认(非 auto)用 fallback 超时(80s)兜底。

        _bbs_t = asyncio.create_task(
            self._static_bbs_handoff_auto_report(task_id, node_id, rnd_bot_id, items)
        )

        self._bg_tasks.add(_bbs_t)

        _bbs_t.add_done_callback(self._on_auto_report_done)


# ---------------------------------------------------------------------------
# SLA threshold helpers for the trajectory RESET gate (REQ-4)
# ---------------------------------------------------------------------------
#
# The engine RESET gate (``task_center/execution_adapters.py::_on_harness_collect``) needs
# the effective SLA threshold **in ms** for ``action_result=sla_timeout`` and
# ``pending_dispatch_stuck`` rows. The engine does NOT hold a ``self._harness``
# reference (the harness is owned by ``TaskService`` and bound back via
# ``set_on_harness`` — a chicken-and-egg that makes wiring ``self._harness``
# into the engine constructor intrusive). These **pure module-level** helpers
# read ``execution_config`` + ``run_mode`` and reuse the harness's own default
# constants, giving the engine a clean public seam without duplicating the
# numbers. They mirror ``TaskHarness._sla_timeout`` / ``_pending_timeout`` for
# the **default-constructed** harness (prod wiring: 900 / 900 / 180 s); a
# harness instance constructed with custom defaults (test-only) is out of
# scope for the trajectory gate — the spec (REQ-4) records the 900 / 900 / 180
# baseline. If ``_sla_timeout`` / ``_pending_timeout`` selection logic changes,
# update these mirrors in lockstep.


def effective_sla_threshold_ms(
    execution_config: dict | None,
    run_mode: str | None,
) -> int:
    """Effective RUNNING-SLA threshold in **ms** for the trajectory RESET gate.

    Honors the per-node ``execution_config["SLA_TIMEOUT"]`` override; otherwise
    all execution modes default to 900 s. Mirrors
    ``TaskHarness._sla_timeout`` (default-constructed).
    """
    cfg = execution_config or {}
    t = cfg.get("SLA_TIMEOUT")
    if t is not None:
        return int(float(t) * 1000)
    if run_mode == "coop_group":
        return int(_DEFAULT_COOP_GROUP_SLA_TIMEOUT * 1000)
    return int(_DEFAULT_SLA_TIMEOUT * 1000)


def effective_pending_timeout_ms(execution_config: dict | None) -> int:
    """Effective pending-dispatch timeout in **ms** for the trajectory RESET gate.

    Honors ``execution_config["PENDING_TIMEOUT"]``; falls back to 180 s.
    Mirrors ``TaskHarness._pending_timeout`` (default-constructed).
    """
    cfg = execution_config or {}
    t = cfg.get("PENDING_TIMEOUT")
    return (
        int(float(t) * 1000) if t is not None else int(_DEFAULT_PENDING_TIMEOUT * 1000)
    )


from agentclaw.community.core.task.task_harness.centralized_recovery import (  # noqa: E402
    redrive as _redrive,
    _reset_action_result as __reset_action_result,
    _reset_sla_threshold_ms as __reset_sla_threshold_ms,
    _reset_elapsed_ms as __reset_elapsed_ms,
    _emit_reset_trajectory as __emit_reset_trajectory,
    _emit_enter_bbs_trajectory as __emit_enter_bbs_trajectory,
    _on_harness_collect as __on_harness_collect,
    on_miss as _on_miss,
    on_harness as _on_harness,
    _sync_graph_status_to_root as __sync_graph_status_to_root,
    _bump_loop_round as __bump_loop_round,
    _escalate_hung as __escalate_hung,
    _hung_and_escalate as __hung_and_escalate,
    _on_bg_done as __on_bg_done,
    _ensure_bbs_loop as __ensure_bbs_loop,
    _on_auto_report_done as __on_auto_report_done,
    _reset_root_plan_round as __reset_root_plan_round,
    _schedule_bbs_notify as __schedule_bbs_notify,
    _enter_root_bbs as __enter_root_bbs,
    _maybe_propagate_hung as __maybe_propagate_hung,
    _reconcile_root_hung_if_blocked as __reconcile_root_hung_if_blocked,
)

TaskHarness.redrive = _redrive
TaskHarness._reset_action_result = __reset_action_result
TaskHarness._reset_sla_threshold_ms = __reset_sla_threshold_ms
TaskHarness._reset_elapsed_ms = __reset_elapsed_ms
TaskHarness._emit_reset_trajectory = __emit_reset_trajectory
TaskHarness._emit_enter_bbs_trajectory = __emit_enter_bbs_trajectory
TaskHarness._on_harness_collect = __on_harness_collect
TaskHarness.on_miss = _on_miss
TaskHarness.on_harness = _on_harness
TaskHarness._sync_graph_status_to_root = __sync_graph_status_to_root
TaskHarness._bump_loop_round = __bump_loop_round
TaskHarness._escalate_hung = __escalate_hung
TaskHarness._hung_and_escalate = __hung_and_escalate
TaskHarness._on_bg_done = __on_bg_done
TaskHarness._ensure_bbs_loop = __ensure_bbs_loop
TaskHarness._on_auto_report_done = __on_auto_report_done
TaskHarness._reset_root_plan_round = __reset_root_plan_round
TaskHarness._schedule_bbs_notify = __schedule_bbs_notify
TaskHarness._enter_root_bbs = __enter_root_bbs
TaskHarness._maybe_propagate_hung = __maybe_propagate_hung
TaskHarness._reconcile_root_hung_if_blocked = __reconcile_root_hung_if_blocked
