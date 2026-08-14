"""TaskPlanner 规划编排壳(零 case 知识)+ 内置策略库(first-match-wins)。

对齐 plan §3.2 + §3.4 + 任务规划文档 uuq2tlue91q4lkal。零参构造,内置默认策略池
[WorkflowPlanningStrategy, GapBasedPlanningStrategy];策略库(first-match-wins by priority,
类 SQL optimizer,据 execution_config 动态匹配:config 有 workflow→workflow 策略;否则 gap 兜底)。
引擎自带能力,不开放自定义;corp 经 ocb 仓覆写 ``_build_*`` 替换策略版本(待后续 PR 落 strategies.py)。
触发条件:有可规划目标(根 PENDING / FAILED+gaps 叶 / PLANNING 父)即 first-match 选策略产子。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import TaskExecutionGraph, TaskNode


class TaskPlanner:
    """规划编排壳:判有无规划目标 → 对图级 execution_config first-match-wins 选策略 → apply 产子 → 去重。

    分层:TaskPlanner(编排壳,框架固定,零 case 知识)↔ PlanningStrategy(引擎内置策略,
    Avernet stub gap/workflow;corp 替换实现)。策略池内置默认;``set_strategies`` 仅供引擎
    工厂方法/corp 子类注入,不对外暴露自定义。策略契约 + 默认 stub 类定义待后续 PR 落
    task_plan/strategies.py。
    """

    def __init__(self, graph) -> None:
        """graph: TaskGraphService(派生查询用;策略 apply 自发现 target 经 graph 快照)。
        零参构造,内置默认策略池 [WorkflowPlanningStrategy, GapBasedPlanningStrategy]
        (首批壳,策略池接线待后续 PR 落 strategies.py)。"""
        self._graph = graph
        self._strategies = None  # list[PlanningStrategy](首批壳,待后续 PR)

    async def plan(self, graph: TaskExecutionGraph) -> list[TaskNode]:
        """读图判有无可规划目标 → first-match-wins 选策略(graph 级 config 匹配)→ await apply 产子 → 去重。
        协程化:策略 apply 在 corp 是 LLM 耗时 IO,锁内 await(同 task 串行,设计意图;不同 task 锁隔离)。

        可规划目标:① 根 PENDING(无父,初始规划);② FAILED+gaps 叶(无结构子,补救);
        ③ PLANNING 父(委托前向)。无目标 → 返回 []。plan 不接收外部 gaps,不判 RUNNING(时序由编排核管)。"""
        raise NotImplementedError
