"""TaskRunner 任务执行模块:三模态自适应 + 回投。对齐 plan.md §3.5 + tasks.md T4b。

Avernet 阶段:form_coop_group stub(不真实 BCS)、start_run stub 投递(不真实 bot workflow/群/BBS)。
prod BCS wiring / 真实执行在 ocb 仓。
"""
from __future__ import annotations

import uuid
from typing import Any

from agentclaw.community.core.task.domain.models import (
    Status,
    TaskNode,
    TaskNodeQueryCriteria,
)
from agentclaw.community.core.task.task_dispatch.protocols import GroupFormation


class TaskRunner:
    """将已派发 TaskNode 发送给单 bot/协作群/BBS 执行,并回收状态/详情/结果。

    调用方:编排核(经 TaskService facade 驱动)。一个 start_run(批量)入口三模态自适应。
    """

    def __init__(self, graph) -> None:
        """graph: TaskGraphService(派生查询 + 投递映射用)。"""
        self._graph = graph
        self._groups: dict[str, GroupFormation] = {}   # group_id -> GroupFormation(form_coop_group stub 记录)
        self._run_log: list[dict[str, Any]] = []        # 投递日志(stub,不真实发起)

    def start_run(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        """图谱上有 TaskNode 完成派发后立即触发执行。入参批量(刚被 dispatcher patch 完
        run_mode/assignee 的节点);返回每个任务派发是否成功 list[bool]。
        内部按 run_mode(str)自适应分发。Avernet stub:三模态均记投递日志返回 True;
        真实 single_bot workflow / bcn 协作群 / BBS bot 自规划 在 corp ocb 仓。"""
        results: list[bool] = []
        for node in toDoTaskList:
            mode = node.run_info.run_mode
            if mode in ("single_bot", "coop_group", "bbs"):
                self._run_log.append(
                    {
                        "task_id": node.task_id,
                        "node_id": node.node_id,
                        "run_mode": mode,
                        "assignee": node.run_info.assignee,
                        "loop_task_id": f"{node.task_id}::{node.node_id}",
                    }
                )
                results.append(True)
            else:
                results.append(False)
        return results

    def query_status(self, task_id: str) -> Status:
        """产品/系统触发:查询某任务及所有子任务的状态(返回图级 status)。"""
        return self._graph.query_task_dashboard(task_id).status

    def query_detail(self, node: TaskNode) -> TaskNode:
        """产品触发:查询任务最新详情(从图回填 node.run_info)。"""
        graph = self._graph.query_task_dashboard(node.task_id)
        for n in graph.tasks:
            if n.node_id == node.node_id:
                return n
        return node

    def query_result(self, node: TaskNode) -> TaskNode:
        """产品/系统触发:查询某任务及其所有子任务的产出结果(回填 node.run_info.output)。"""
        return self.query_detail(node)

    def query_bot_tasks(self, bot_id: str) -> list[TaskNode]:
        """获取某个 Bot 下的所有任务实例列表。
        Avernet stub:需全局 task_id 索引(后续补);当前返回空列表。"""
        return []

    def form_coop_group(self, gf: GroupFormation) -> str:
        """(内部)HIT_MULTI_BOTS 动态拉协作群,复用 BCS 建群 → group_id。
        Avernet stub:生成 group_id 并记录 GroupFormation,不真实调 BCS。
        prod BCS wiring(group_strategy=collab_mode;state_machine 注入 workflow yaml)在 ocb 仓。"""
        gid = f"grp_{uuid.uuid4().hex[:8]}"
        self._groups[gid] = gf
        return gid

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
                    c.node_id: c.run_info.output for c in children if c.status == Status.DONE
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
            if s.status == Status.DONE and s.node_id != node_id
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
