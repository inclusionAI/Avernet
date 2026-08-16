"""对外任务服务契约(任务中心 TaskService facade)。对齐 plan §3.7 + 任务中心文档 yugg6dorsxo8sgmp。"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    NodeOpResult,
    TaskExecutionGraph,
    TaskInfo,
    TaskNode,
    TaskOpResult,
    TaskSpec,
    TaskSummary,
)


@runtime_checkable
class TaskServiceProtocol(Protocol):
    """系统唯一对外入口(2 API)。facade 内部由 ExecutionEngine 编排核协调
    TaskGraphService/TaskPlanner/TaskDispatcher/TaskRunner。"""

    async def execute(self, task_info: TaskInfo) -> TaskOpResult:
        """提交执行任务:initialize_graph(根 PENDING)→ 编排核 on_execute
        首帧推进(plan→add_task_nodes→dispatch→start_run)。返回 TaskOpResult。"""
        ...

    def get_task_dashboard(
        self, task_id: str, node_id: str | None = None
    ) -> TaskExecutionGraph:
        """任务执行详情可视化(整图或按 node_id 子树投影),只读。"""
        ...

    def list_tasks(self, status: "str | None" = None) -> list[TaskSummary]:
        """列出任务摘要(轻量投影),按 run_id 降序(最新在前);可选按图级 status 过滤。

        visualization/看板列表视图用;不返回完整图对象。"""
        ...

    def claim_bbs_task(self, task_id: str, bot_id: str) -> NodeOpResult:
        """BBS 接力步②:任务根级 CAS 占有(恰一赢;输者/非 bbs 任务 → TaskStateError)。

        供 bbs/claim 路由(FR-PICK-02)调用,委托 TaskGraphService.claim_bbs_owner。"""
        ...

    def attach_bbs_node(
        self, task_id: str, parent_node_id: str, task_spec: TaskSpec, bot_id: str
    ) -> TaskNode:
        """BBS 接力步④:在 parent 下挂 run_mode=bbs scoped 子节点 + 翻 PENDING→RUNNING(create+start 合一)。

        owner 校验 + 深度闸 BBS_MAX_DEPTH + bbs_relay_count++;委托 TaskGraphService.attach_bbs_node。
        """
        ...

    async def report_bbs_result(
        self, task_id: str, node_id: str, bot_id: str,
        acceptance_result: AcceptanceResult | None = None,
        output_patch: dict | None = None, exec_error: str | None = None,
        root_verified: bool = False,
    ) -> NodeOpResult:
        """BBS 接力步⑤:回投 scoped 节点终态 + 释放 claim(collector-free)。

        acceptance_result(PASS→DONE / FAIL+gaps→FAILED)/ output_patch(checkpoint fold)/
        exec_error(执行报错 fold);root_verified=True → 根 PLANNING→DONE + 图 DONE。
        bot_id 须为当前 bbs_owner,否则 TaskStateError。委托 ExecutionEngine.on_bbs_report。
        """
        ...
