"""TaskRunner 任务执行模块:三模态自适应 + 回投。对齐 plan §3.5 + 执行文档 lxg2mwgmtfqg6d95。"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import Status, TaskNode


class TaskRunner:
    """将已派发 TaskNode 发送给单 bot/协作群/BBS 执行,并回收状态/详情/结果。

    调用方:编排核(经 TaskService facade 驱动)。一个 start_run(批量)入口三模态自适应。
    """

    def start_run(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        """图谱上有 TaskNode 完成派发后立即触发执行。入参批量(刚被 dispatcher patch 完
        run_mode/assignee 的节点);返回每个任务派发是否成功 list[bool]。内部按 run_mode 自适应:
        single_bot→单 bot workflow;coop_group→bcn 协作群(已有群 or 刚 form_coop_group 拉的);
        bbs→BBS bot 认领任务后自算 gap+规划子任务(add_task_nodes 落图 run_mode="bbs")→自执行→上报。"""
        raise NotImplementedError

    def query_status(self, task_id: str) -> Status:
        """产品/系统触发:查询某任务及其所有子任务的状态。"""
        raise NotImplementedError

    def query_detail(self, node: TaskNode) -> TaskNode:
        """产品触发:查询任务最新详情(回填 node.run_info)。"""
        raise NotImplementedError

    def query_result(self, node: TaskNode) -> TaskNode:
        """产品/系统触发:查询某任务及其所有子任务的产出结果(回填 node.run_info.output)。"""
        raise NotImplementedError

    def query_bot_tasks(self, bot_id: str) -> list[TaskNode]:
        """获取某个 Bot 下的所有任务实例列表。"""
        raise NotImplementedError

    def form_coop_group(self, gf) -> str:
        """(内部)HIT_MULTI_BOTS 动态拉协作群,复用 BCS 建群 → group_id。
        CHAT/MANAGER_WORKER/STATE_MACHINE 三模式(collab_mode=group_strategy;state_machine 注入
        workflow yaml)。gf: GroupFormation(内部参数,首批不强类型;定义延后到
        task_dispatch/protocols.py)。collab_mode 不进 RuntimeInfo 持久字段。"""
        raise NotImplementedError
