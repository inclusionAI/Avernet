"""对外任务服务契约(任务中心 TaskService facade)。对齐 plan §3.7 + 任务中心文档 yugg6dorsxo8sgmp。"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.domain.models import (
    AcceptanceResult,
    NodeOpResult,
    TaskExecutionGraph,
    TaskNode,
    TaskOpResult,
    TaskSpec,
)
from agentclaw.community.core.task.domain.requests import TaskInfoRequest
from agentclaw.community.core.task.repository.types import (
    BbsTaskOverviewRecord,
    TaskInfoRecord,
)


@runtime_checkable
class TaskServiceProtocol(Protocol):
    """系统唯一对外入口。facade 内部由 ExecutionEngine 编排核协调
    TaskGraphService/TaskPlanner/TaskDispatcher/TaskRunner。"""

    async def execute(self, request: TaskInfoRequest) -> TaskOpResult:
        """提交执行任务:持久化 task_info(PENDING)→ initialize_graph(根 PENDING)→ 编排核 on_execute
        首帧推进。task_id 服务端生成(uuid4)。返回 TaskOpResult(含 task_id + run_id)。"""
        ...

    def get_task_dashboard(
        self,
        task_id: str,
        node_id: str | None = None,
        *,
        include_action_log: bool = False,
    ) -> TaskExecutionGraph:
        """任务执行详情可视化(整图或按 node_id 子树投影),只读。"""
        ...

    def list_tasks(
        self,
        status: "str | None" = None,
        owner_user_id: "str | None" = None,
    ) -> list[TaskInfoRecord]:
        """列持久化任务记录,可选按状态(单值或逗号分隔多值)和 owner 过滤。"""
        ...

    def list_tasks_page(
        self,
        status: "str | None" = None,
        owner_user_id: "str | None" = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[TaskInfoRecord], int]:
        """列持久化任务记录的一页(1-based),可选按状态(单值或逗号分隔多值)和 owner 过滤,返回 (items, total)。"""
        ...

    def list_bbs_tasks(
        self,
        page: int = 1,
        page_size: int = 20,
        *,
        search_word: str | None = None,
        status: str | None = None,
    ) -> "tuple[list[BbsTaskOverviewRecord], int]":
        """列 BBS 接力任务(run_mode='bbs')的一页(1-based):``task_node_run_info`` ⋈ ``task_node`` 联合,
        再按 task_id 补 ``task_info.owner_bot_id``(publisher)。返回 ``(records, total)``——``records`` 为
        ``BbsTaskOverviewRecord``(含 task_spec/extend_props 原始 dict),title/goal/acceptances/
        assignee_name 由 adapter translator 二次解析;``total`` 为**过滤后**行数。status/search_word
        可选过滤(为空不过滤),供 bbs/list 路由调用,委托 TaskGraphService.list_bbs_tasks_overview。"""
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
    ) -> NodeOpResult:
        """BBS 接力步⑤:回投 scoped 节点终态 + 释放 claim;收口由框架经 owner 复核根 gap 自行收口(非 bot 声明)。

        acceptance_result(PASS→SUCCESS / FAIL+gaps→DONE)/ output_patch(checkpoint fold)/
        exec_error(执行报错 fold)。bot_id 须为当前 bbs_owner,否则 TaskStateError。委托 ExecutionEngine.on_bbs_report。
        """
        ...

    async def update_task_node_info(
        self,
        task_id: str,
        node_id: str,
        *,
        status: "str | None" = None,
        run_mode: "str | None" = None,
        assignee: "str | None" = None,
        output_patch: "dict | None" = None,
        acceptance_result: AcceptanceResult | None = None,
        exec_error: "str | None" = None,
        extend_props_patch: "dict | None" = None,
    ) -> NodeOpResult:
        """内部节点写口:直接更新节点 run_info(透传 ``TaskNodePatch`` 经 ``ExecutionEngine.on_report`` 落库)。

        与回投走同一入口(``on_report``):``acceptance_result`` 验收驱动翻态 / ``exec_error`` 执行报错
        (→ on_harness 重投)/ ``status`` 框架直驱;三者全空仅 fold 非状态字段(**会触发引擎收敛旁路**,
        如根子节点全终态收敛)。供内部调用方/功能测试直驱节点状态,不经 BBS claim 校验。
        """
        ...

    async def converge_by_session(
        self, session_id: str, *, success: bool, output: object = None,
    ) -> bool:
        """BCN/ClawMind 终态回调后收敛:按 ``session_id`` 查 ``task_node_run_info`` →
        框架 ``(task_id, node_id)`` → ``report_result`` → ``on_report`` → 翻态(引擎验收+传播+根收敛)。

        ``session_id`` = BCN 回调 ``scope.session_id`` / ClawMind ``flow_runs.origin_session_id``,
        与 ``task_node_run_info.session_id``(派发时写入 BCS session)同源。
        """
        ...

    async def apply_manager_worker_event(self, raw: dict) -> None:
        """manager_worker(BCN 任务协作群)CloudEvent 回调处理:按 ``scope.session_id`` 把事件 merge 进
        ``execution_graph`` 并 upsert ``task_callback`` 单 session 行;``session.completed`` →
        ``converge_by_session`` 收敛整协作(ManagerWorker 无整协作级 run,终态由 session.completed 表征)。
        非 manager_worker 事件 → no-op。"""
        ...

    async def redrive_task(self, task_id: str) -> None:
        """Recovery resume entrypoint: re-dispatch a hydrated non-terminal task
        after an instance restart / rolling deploy. Idempotent; non-terminal
        runtime status only. Drives ``ExecutionEngine.redrive``."""
        ...
