"""TaskRunner 任务执行模块:三模态投递 + 回投。对齐 plan.md §3.5 + tasks.md T4b。

Avernet 阶段:form_coop_group stub(不真实 BCS)、start_run stub 投递(记日志,不真实 bot workflow/群/BBS)。
三类投递后端经 ``set_delivery`` 注入(corp ocb 仓:真实 workflow engine/BCS/BBS 广场);缺省 stub fallback。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Protocol

from agentclaw.community.core.task.domain.models import (
    Status,
    TaskNode,
    TaskNodeQueryCriteria,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation

logger = logging.getLogger(__name__)


class DeliveryPort(Protocol):
    """执行投递后端 seam(单 bot workflow / bcn 协作群 / BBS 广场)。corp 注入真实实现。"""

    async def deliver(self, node: TaskNode) -> bool:
        """投递任务节点给执行主体,返回是否投递成功(完成结果经 TaskLoopCallback PUSH 回投)。"""
        ...


class TaskRunner:
    """将已派发 TaskNode 发送给单 bot/协作群/BBS 执行,并回收状态/详情/结果。

    调用方:编排核(经 TaskService facade 驱动)。一个 start_run(批量)入口三模态自适应。
    三类投递后端经 ``set_delivery(mode, port)`` 注入(corp);缺省 stub 记投递日志返回 True。
    投递并发:``start_run`` 内部 ``asyncio.gather`` + ``_DELIVER_CONCURRENCY`` Semaphore 限流
    (对齐 backend lifecycle 模式,多节点网络投递并发防雪崩)。
    """

    # 投递并发上限(多节点投递 gather 限流;对齐 backend lifecycle Semaphore 模式)。
    _DELIVER_CONCURRENCY = 8

    def __init__(self, graph, execution_backend=None) -> None:
        """graph: TaskGraphService(派生查询 + 投递映射用);execution_backend: TaskExecutor | None
        (注入则真实派发 single_bot/coop_group/bbs;缺省 stub fallback 记日志)。"""
        self._graph = graph
        self._execution_backend = execution_backend
        self._deliveries: dict[str, DeliveryPort] = {}
        self._groups: dict[str, GroupFormation] = {}   # group_id -> GroupFormation(form_coop_group stub 记录)
        self._run_log: list[dict[str, Any]] = []        # 投递日志(stub fallback,不真实发起)

    def set_delivery(self, mode: str, port: DeliveryPort) -> None:
        """(非公开)注入执行投递后端。mode∈{"single_bot","coop_group","bbs"};corp ocb 仓注入真实实现。"""
        self._deliveries[mode] = port

    async def start_run(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        """图谱上有 TaskNode 完成派发后立即触发执行。入参批量(刚被 dispatcher/adaptor patch 完
        run_mode/assignee 的节点);返回每个任务派发是否成功 list[bool]。

        内部按 run_mode(str)自适应分发:有注入 delivery → ``await`` delivery.deliver(投递耗时 IO),
        多节点经 ``asyncio.gather`` + ``_DELIVER_CONCURRENCY`` Semaphore 并发限流(对齐 backend lifecycle
        模式,防投递雪崩);否则 stub 记日志返 True。
        协程化:真实投递(单 bot workflow/BCS 协作群/BBS 广场)是网络 IO,并发 await 不阻塞编排核。"""
        if self._execution_backend is not None:
            # 真实后端一次接收完整批次，由其统一 semaphore 控制三种模态的并发。
            return list(await self._execution_backend.dispatch(toDoTaskList))

        sem = asyncio.Semaphore(self._DELIVER_CONCURRENCY)

        async def _deliver_one(node: TaskNode) -> bool:
            mode = node.run_info.run_mode
            if mode not in ("single_bot", "coop_group", "bbs"):
                return False
            async with sem:
                port = self._deliveries.get(mode)
                if port is not None:
                    return bool(await port.deliver(node))
                logger.warning(
                    "[task][task_runner] start_run 退桩(无 execution_backend 且无 %s delivery 注入)→ node=%s 记日志返 True,不真实发起",
                    mode, node.node_id)
                self._run_log.append(
                    {
                        "task_id": node.task_id,
                        "node_id": node.node_id,
                        "run_mode": mode,
                        "assignee": node.run_info.assignee,
                        "loop_task_id": f"{node.task_id}::{node.node_id}",
                    }
                )
                return True

        return list(await asyncio.gather(*[_deliver_one(n) for n in toDoTaskList]))

    async def form_coop_group(self, gf: GroupFormation) -> str:
        """(内部)HIT_MULTI_BOTS 动态拉协作群,复用 BCS 建群 → group_id。
        协程化:BCS 建群是网络 IO,``await`` 不阻塞编排核(由 engine 锁外 await 调用)。
        注入 execution_backend 时委托其真实建群;否则 Avernet stub:生成 group_id 并记录 GroupFormation。
        prod BCS wiring(group_strategy=collab_mode;state_machine 注入 workflow yaml)在 ocb 仓。"""
        logger.info("[task][task_runner] form_coop_group begin, group_formation=%s", gf)

        if self._execution_backend is not None:
            return await self._execution_backend.form_coop_group(gf)
        gid = f"grp_{uuid.uuid4().hex[:8]}"
        self._groups[gid] = gf
        logger.warning(
            "[task][task_runner] form_coop_group 退桩(无 execution_backend)→ 造假 group_id=%s;"
            "无真群/无 poller,任务将卡 RUNNING 不收敛。排查: grep [task][engine] execution_backend 不装配",
            gid)
        return gid

    async def get_group_session(self, group_id: str) -> str | None:
        """Fetch the initial session_id for a coop group; create one if absent."""
        if self._execution_backend is not None:
            return await self._execution_backend.get_group_session(group_id)
        logger.debug("[task][task_runner] get_group_session 退桩→ None(group_id=%s 无 execution_backend)", group_id)
        return None

    def _build_context(self, task_id: str, node_id: str) -> dict[str, Any]:
        """上下文组装(Runner 内聚;内部自动判定,无 NODE/SUBTREE/TASK scope 入参)。

        有结构子(``get_child_tasks`` 非空)→**验收模式**:聚合【结构子(子树)DONE 的 run_info.output
        + 本节点 ``task_spec.goal/acceptances``】→ 组装验证 prompt(经 source_channel 派 owner/master bot)。
        无结构子→**执行模式**:取结构父 ``P = get_parent_task``;聚合【``P.task_spec/goal`` + P 已 DONE 结构子
        (本节点兄弟)``run_info.output`` + 本节点 ``task_spec``】→ 组装执行 prompt 注入执行主体。
        数据流一律经结构父 P 中转,无跨兄弟直接数据边。"""
        node = self._get_node(task_id, node_id)
        children = self._graph.get_child_tasks(task_id, node_id)
        if children:
            return {
                "mode": "verify",
                "child_outputs": {
                    c.node_id: c.run_info.output for c in children if c.status == Status.SUCCESS
                },
                "goal": node.task_spec.goal if node else None,
                "acceptances": node.task_spec.goal.acceptances if node else None,
                "node_instruction": node.task_spec.metadata.instruction if node else None,
            }
        parent = self._graph.get_parent_task(task_id, node_id)
        if parent is None:
            return {"mode": "execute", "parent_node_id": None, "parent_spec": None, "sibling_outputs": {}, "node_spec": node.task_spec if node else None}
        siblings = self._graph.get_child_tasks(task_id, parent.node_id)
        sibling_outputs = {
            s.node_id: s.run_info.output
            for s in siblings
            if s.status == Status.SUCCESS and s.node_id != node_id
        }
        return {
            "mode": "execute",
            "parent_node_id": parent.node_id,
            "parent_spec": parent.task_spec,
            "sibling_outputs": sibling_outputs,
            "node_spec": node.task_spec if node else None,
        }

    def _get_node(self, task_id: str, node_id: str) -> TaskNode | None:
        """从图回读单节点(经公开 ``query_task_nodes``;Runner 不持有图对象引用篡改)。"""
        hits = self._graph.query_task_nodes(
            task_id, TaskNodeQueryCriteria(node_ids=[node_id])
        )
        return hits[0] if hits else None
