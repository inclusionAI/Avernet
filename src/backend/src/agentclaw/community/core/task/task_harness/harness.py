"""TaskHarness 旁路常驻:周期巡检 SLA 超时/崩溃复位。对齐 plan §3.6。"""
from __future__ import annotations


class TaskHarness:
    """旁路常驻:周期巡检超时/崩溃,经 graph.update_task_node_info 复位回 PENDING,不抢正向驱动。

    超时阈值从 execution_config/extend_props 读(SLA 不在 TaskSpec)。
    RUNNING 超时→复位 PENDING 重投(不直接写 HUNG;STUCK 走 on_miss 升 BBS 链路上限判)。
    """

    def __init__(self, graph):
        """graph: TaskGraphService(写同网关,不抢正向驱动)。"""
        self._graph = graph

    def run_poll_loop(self) -> None:
        """周期:query_task_nodes(status=RUNNING)→ 比对 start_time + sla_timeout → 超时/崩溃
        → update_task_node_info(TaskNodePatch{status=PENDING, extend_props_patch={崩溃栈/超时}})复位
        → 正常 dispatch 重投。不调编排核正向;主链下一轮事件自然续驱。"""
        raise NotImplementedError
