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
    Status, TaskExecutionGraph, TaskInfo, TaskOpResult, TaskSummary,
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

    def __init__(self, graph, harness=None, *, bot=None, bcs=None, discover=None) -> None:
        """graph: TaskGraphService;harness: TaskHarness | None(旁路复位,可选);
        bot/bcs/discover: 传输端口(DI 从配置注入 local/prod/double 实现传给引擎;省略=stub 路径/纯内核单测)。"""
        self._graph = graph
        self._harness = harness
        self._engine = self._build_engine(bot=bot, bcs=bcs, discover=discover)
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
        return ExecutionEngine(self._graph, bot=bot, bcs=bcs, discover=discover)

    @property
    def callback(self) -> TaskLoopCallback:
        """供执行实体(bot workflow / bcn 协作群)PUSH 回投的入口(适配层 → 编排核 on_report)。"""
        return self._callback

    async def execute(self, task_info: TaskInfo) -> TaskOpResult:
        """提交执行任务:initialize_graph(根 PENDING)→ 编排核 on_execute(首帧推进:
        条件 a 根 PENDING → plan → add_task_nodes → dispatch → start_run)。返回 TaskOpResult(含 run_id)。
        协程化:await on_execute(async 链路),耗时投递(BCS/真实 workflow)不阻塞调用方。"""
        graph = self._graph.initialize_graph(task_info)
        task_id = task_info.task_spec.metadata.task_id
        logger.info("[execute] task=%s source=%s title=%s → initialize(run_id=%s)+on_execute",
                    task_id, task_info.source_channel_id,
                    task_info.task_spec.metadata.title, graph.run_id)
        if self._harness is not None:
            self._harness.register(task_id)
        await self._engine.on_execute(task_id)
        logger.info("[execute] task=%s 首帧推进完成", task_id)
        return TaskOpResult(task_id=task_id, success=True, run_id=graph.run_id)

    def get_task_dashboard(
        self, task_id: str, node_id: str | None = None
    ) -> TaskExecutionGraph:
        """任务执行详情可视化(整图或按 node_id 子树投影),只读。"""
        return self._graph.query_task_dashboard(task_id, node_id)

    def list_tasks(self, status: str | None = None) -> list[TaskSummary]:
        """列任务摘要(轻量投影),按 run_id 降序;status 非 None 时按图级状态过滤。"""
        st = Status(status) if status else None
        return self._graph.list_task_summaries(st)


def run_execute(facade: TaskService, task_info: TaskInfo) -> TaskOpResult:
    """同步执行 ``execute``(无事件循环依赖的调用方/单测用)。"""
    return asyncio.new_event_loop().run_until_complete(facade.execute(task_info))
