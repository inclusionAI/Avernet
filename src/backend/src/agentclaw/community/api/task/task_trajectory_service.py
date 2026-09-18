"""对外轨迹服务契约(REQ-8 ``GET /trajectory`` 的 service facade)。

与 ``TaskServiceProtocol`` 平级的独立 service contract —— 消费 P4 assembler +
P5a analyzer + P1b trajectory repo,组合成单一入口
``get_trajectory(task_id, *, do_analysis=False) -> TaskTrajectory``:

- ``do_analysis=False``(默认,纯读):assembler 读事件表拼装 ``TaskTrajectory``,
  ``analysis`` 取已落库值或 ``None``,不写库、不调 bot。
- ``do_analysis=True``(触发 bot 总体分析):组装轨迹 → 构建 ``ext_info_lookup``
  → 调 analyzer ``tc_bot`` 执行者 → 序列化 ``TrajectoryAnalysis`` JSON →
  ``backfill_analysis`` 覆盖回填两表 → 返回携带新 analysis 的同形态 ``TaskTrajectory``。

决策 #10:原独立 ``GET /tasks/{id}/trajectory/analysis`` 端点取消、并入此入口;
  ``analysis_bot_id`` 由部署级 DI 配置注入(非请求参数,调用方不可选 bot)。
决策 #14 的吞错豁免仅限观测旁路发射;分析触发是同步 on-demand 动作,失败必须可见
  (bot 失败/超时 → ``TrajectoryAnalysisError`` → HTTP 504 且不回填;bot 未配置 →
  ``TrajectoryAnalysisNotConfiguredError`` → HTTP 503)。

权威源:``specs/2026-09-16-task-trajectory-collection-and-analysis/spec.md`` REQ-8。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.core.task.task_trajectory.models import TaskTrajectory


@runtime_checkable
class TaskTrajectoryServiceProtocol(Protocol):
    """轨迹读 + 分析触发的统一对外入口(REQ-8 ``GET /trajectory``)。

    单方法 ``get_trajectory`` 承担两模式(由 ``do_analysis`` 切换;两模式返回形态一致
    均为 ``TaskTrajectory``,仅 ``analysis`` 是否被刷新不同):
    - ``do_analysis=False``(默认,纯读,不写库):返回 assembler 拼装的 ``TaskTrajectory``,
      ``analysis`` 取已落库值或 ``None``。
    - ``do_analysis=True``(触发 bot 总体分析):组装轨迹 → 调 DI 配置注入的 bot
      (``analysis_type=tc_bot``、``analysis_executor=<bot_id>``)做总体分析 → 覆盖回填
      两表 ``analysis``+``gmt_modified`` → 返回携新 analysis 的同形态 ``TaskTrajectory``。
      bot 未配置 → 抛 ``TrajectoryAnalysisNotConfiguredError``(→503);
      bot 失败/超时 → 抛 ``TrajectoryAnalysisError``(→504,不回填)。
    """

    async def get_trajectory(
        self,
        task_id: str,
        *,
        do_analysis: bool = False,
    ) -> TaskTrajectory:
        """Read the trajectory for ``task_id``; optionally trigger bot analysis.

        ``do_analysis=False`` is a PURE READ (no DB write, no bot call): the
        returned ``TaskTrajectory.analysis`` is the persisted value or ``None``.

        ``do_analysis=True`` triggers the configured ``tc_bot`` analysis executor,
        backfills the resulting ``TrajectoryAnalysis`` JSON (overwrite, 决策 #13),
        and returns the same-shape ``TaskTrajectory`` carrying the fresh analysis.
        """
        ...
