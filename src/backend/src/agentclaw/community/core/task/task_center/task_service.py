"""TaskService facade(2 API):系统唯一对外入口,内部持 ExecutionEngine 编排核。对齐 plan §3.7。

facade 内部 ``_build_engine`` 构造 ExecutionEngine(收传输端口 bot/bcs/discover,由 DI 从配置注入);
引擎 ``_build_*`` 内部 new 引擎自带策略 + 接线 TaskExecutor,无子类化、无外部 reach-in setter。
回投经 ``callback``(TaskLoopCallback)适配层 → 编排核 on_report(非 facade 直暴露)。
engine 对调用方不可见(无 engine property)。测试可经 facade/engine 子类覆写 ``_build_*`` 注入 stub 策略/投递(测试 seam)。
"""
from __future__ import annotations

import asyncio
import logging

from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult, NodeOpResult, Status, TaskExecutionGraph, TaskInfo, TaskNode, TaskNodePatch,
    TaskOpResult, TaskSpec, TaskSummary,
)
from agentclaw.community.core.task.task_center.engine import ExecutionEngine
from agentclaw.community.core.task.task_runner.callback_adapter import (
    CallbackAdapter,
    TaskLoopCallback,
)

logger = logging.getLogger("task.service")


class TaskService(TaskServiceProtocol):
    """对外 facade(2 API);内部持 ExecutionEngine 编排核 + TaskGraphService + Harness(可选)+ TaskLoopCallback。

    验收 100% 走回调回投;engine 不主动验,无 verify/bbs port。engine 对调用方不可见(无 property)。
    """

    def __init__(self, graph, harness=None, *, bot=None, bcs=None, discover=None,
                 bcs_identity=None) -> None:
        """graph: TaskGraphService;harness: TaskHarness | None(旁路复位,可选);
        bot/bcs/discover: 传输端口(DI 从配置注入 local/prod/double 实现传给引擎;省略=stub 路径/纯内核单测)。"""
        self._graph = graph
        self._harness = harness
        self._bcs_identity = bcs_identity
        self._engine = self._build_engine(bot=bot, bcs=bcs, discover=discover)
        # fire-and-forget 后台推进任务跟踪(防 GC + 异常可见 + drain seam)
        self._bg_tasks: set[asyncio.Task] = set()
        # 回投适配层:执行实体 PUSH → 适配 → 编排核 on_report
        self._callback = TaskLoopCallback(CallbackAdapter(), self._engine)
        # harness 复位重投入口回填(编排核已建,harness 才能拿到 on_harness)+ 启动旁路巡检 daemon 线程
        if self._harness is not None:
            self._harness.set_on_harness(self._engine.on_harness)
            import threading as _t
            _t.Thread(target=self._harness.run_poll_loop, daemon=True, name="task-harness").start()
            logger.info("[task-service] harness 旁路巡检线程已启动(SLA 超时/FAILED 重派/PENDING 派发超时重搜推)")

    def _build_engine(self, *, bot=None, bcs=None, discover=None) -> ExecutionEngine:
        """构造编排核:ExecutionEngine(graph, bot=, bcs=, discover=)。引擎内部 ``_build_*`` new 自带策略 +
        接线 TaskExecutor。测试可经 facade/engine 子类覆写本方法注入 stub 策略/投递的引擎(测试 seam)。"""
        return ExecutionEngine(
            self._graph, bot=bot, bcs=bcs, discover=discover,
            bcs_identity=self._bcs_identity,
        )

    @property
    def callback(self) -> TaskLoopCallback:
        """供执行实体(bot workflow / bcn 协作群)PUSH 回投的入口(适配层 → 编排核 on_report)。"""
        return self._callback

    async def execute(self, task_info: TaskInfo) -> TaskOpResult:
        """提交执行任务:initialize_graph(根 PENDING)+ harness.register 同步完成,
        随即后台调度编排核 on_execute 首帧推进(plan→add_task_nodes→dispatch→start_run),
        立即返回 TaskOpResult(含 run_id)。

        fire-and-forget:on_execute 在后台 asyncio.Task 推进,不阻塞调用方(HTTP 响应秒回);
        长编排(owner bot ``send_and_wait_async`` 分钟级 + dispatch 投递)异步进行,
        调用方经 ``get_task_dashboard`` 轮询观察推进。后台任务异常经 done_callback 记 log
        (不向调用方抛;图停在中间态由 harness 旁路巡检兜底复位)。"""
        graph = self._graph.initialize_graph(task_info)
        task_id = task_info.task_spec.metadata.task_id
        logger.info("[execute] task=%s source=%s title=%s → initialize(run_id=%s)+on_execute(后台推进)",
                    task_id, task_info.source_channel_id,
                    task_info.task_spec.metadata.title, graph.run_id)
        if self._harness is not None:
            self._harness.register(task_id)
        bg = asyncio.create_task(self._engine.on_execute(task_id))
        self._bg_tasks.add(bg)
        bg.add_done_callback(self._on_bg_done)
        return TaskOpResult(task_id=task_id, success=True, run_id=graph.run_id)

    def _on_bg_done(self, bg: "asyncio.Task") -> None:
        """后台 on_execute 完成:脱离跟踪集 + 异常可见(记 log,不抛)。"""
        self._bg_tasks.discard(bg)
        if bg.cancelled():
            return
        exc = bg.exception()
        if exc is not None:
            logger.error("[execute] 后台 on_execute 异常: %s", exc, exc_info=exc)

    async def drain_background(self) -> None:
        """await 所有在途后台 on_execute 推进完成。

        fire-and-forget 语义下供测试确定性(等首帧落定后再断言图态)与优雅停机用;
        生产 HTTP 调用方不调用(经 dashboard 观察)。"""
        if not self._bg_tasks:
            return
        await asyncio.gather(*self._bg_tasks, return_exceptions=True)

    def get_task_dashboard(
        self, task_id: str, node_id: str | None = None
    ) -> TaskExecutionGraph:
        """任务执行详情可视化(整图或按 node_id 子树投影),只读。"""
        return self._graph.query_task_dashboard(task_id, node_id)

    def list_tasks(self, status: str | None = None) -> list[TaskSummary]:
        """列任务摘要(轻量投影),按 run_id 降序;status 非 None 时按图级状态过滤。"""
        st = Status(status) if status else None
        return self._graph.list_task_summaries(st)

    def claim_bbs_task(self, task_id: str, bot_id: str) -> NodeOpResult:
        """BBS 接力步②:任务根级 CAS 占有(委托 TaskGraphService.claim_bbs_owner)。

        供 bbs/claim 路由(FR-PICK-02)调用:恰一赢,输者/非 bbs 任务 → TaskStateError。
        """
        return self._graph.claim_bbs_owner(task_id, bot_id)

    def attach_bbs_node(
        self, task_id: str, parent_node_id: str, task_spec: TaskSpec, bot_id: str
    ) -> TaskNode:
        """BBS 接力步④:在 parent 下挂 run_mode=bbs scoped 节点 + PENDING→RUNNING(create+start 合一)。

        供 bbs 接力执行实体(FR-PICK-04)调用,委托 TaskGraphService.attach_bbs_node:
        owner 校验 + 深度闸 + 翻 RUNNING + bbs_relay_count++。
        """
        return self._graph.attach_bbs_node(task_id, parent_node_id, task_spec, bot_id)

    async def report_bbs_result(
        self, task_id: str, node_id: str, bot_id: str,
        acceptance_result: AcceptanceResult | None = None,
        output_patch: dict | None = None, exec_error: str | None = None,
        root_verified: bool = False,
    ) -> NodeOpResult:
        """BBS 接力步⑤:回投 scoped 节点终态 + 释放 claim。collector-free(经 ``on_bbs_report``)。

        供 bbs 接力执行实体(FR-PICK-05)回投:``acceptance_result``(PASS→DONE / FAIL+gaps→FAILED)/
        ``output_patch``(checkpoint fold)/``exec_error``(执行报错 fold);``root_verified=True`` →
        根 PLANNING→DONE + 图 DONE。``bot_id`` 须为当前 ``bbs_owner``(经 on_bbs_report 持有者校验),
        否则 ``TaskStateError``。
        """
        patch = TaskNodePatch(
            task_id=task_id, node_id=node_id, assignee=bot_id,
            acceptance_result=acceptance_result, output_patch=output_patch, exec_error=exec_error,
        )
        return await self._engine.on_bbs_report(patch, root_verified=root_verified)


def run_execute(facade: TaskService, task_info: TaskInfo) -> TaskOpResult:
    """同步执行 ``execute``(无事件循环依赖的调用方/单测用)。"""
    return asyncio.new_event_loop().run_until_complete(facade.execute(task_info))
