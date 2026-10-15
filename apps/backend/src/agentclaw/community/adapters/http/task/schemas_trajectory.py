"""Task trajectory HTTP schemas and transport projection."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from agentclaw.community.core.task.task_context.task_trajectory.models import TaskTrajectory

# ===== 任务轨迹(REQ-8 ``GET /tasks/{id}/trajectory``)DTO =====
# 扁平投影:领域对象 ``TaskTrajectory`` / ``TrajectoryEvent`` → DTO(Rule 22 边界翻译)。
# ``analysis`` 为 ``TrajectoryAnalysis`` JSON 字符串(客户端自行解析;执行者多源,保持 string 透出);
# ``ext_info`` 不整体进入领域对象/DTO(REQ-1),仅定向投影 ``holder_id`` 与
# ``search_probe``(派发搜推三问采样)供排障展示。两模式(do_analysis 真/假)同形态。


class TrajectoryEventDTO(BaseModel):
    """单条轨迹事件的扁平 DTO(对应领域 TrajectoryEvent,无 ext_info)。"""

    model_config = ConfigDict(from_attributes=True)

    task_id: str = Field(..., description="归属任务 ID")
    node_id: str = Field(..., description="节点 ID")
    action_type: str = Field(
        ..., description="动作类型(submit|plan|dispatch|execute|verify|reset|transition)"
    )
    action_result: str = Field(..., description="动作结果枚举(success|hit_single|miss|failed|sla_timeout|...)")
    attempt: int = Field(..., description="harness 重试序号快照")
    gmt_create: int = Field(..., description="事件发射时间(ms epoch;timeline 排序依据)")
    gmt_modified: int = Field(..., description="最后修改时间(分析回填时更新,未回填时等于 gmt_create)")
    action_input: str | None = Field(None, description="动作输入内容(submit=task_spec_digest/plan=prompt_digest/execute|verify=request_input;reset|transition=None)")
    status_from: str | None = Field(None, description="动作前节点状态(未翻态时 None)")
    status_to: str | None = Field(None, description="动作后节点状态(未翻态时 None)")
    error_type: str | None = Field(None, description="ReasonCatalog 错误分类(成功为 None)")
    error_msg: str | None = Field(None, description="截断后的错误消息(成功为 None)")
    boost_reason: str | None = Field(None, description="本次动作的推进原因")
    holder_id: str | None = Field(None, description="Relay 当前动作执行人")
    analysis: str | None = Field(None, description="内嵌 TrajectoryAnalysis JSON 字符串(未回填为 None)")
    output: dict[str, Any] | None = Field(
        None,
        description="子任务当前产出(node.run_info.output;读时经 task_execution_graph 富化,"
                    "仅该 node 在 timeline 中的最后一条事件携带,无产出为 None)",
    )
    session_msgs: list[dict[str, Any]] | None = Field(
        None,
        description="子任务会话消息(最近 running_session_message_limit 条,原文;"
                    "读时按 session_id 形态分通道实拉(BCS 会话键→BCS 读口;引擎键"
                    "agent:main:session:*/user:...:session:...:agent:...→BaaS Open API),"
                    "失败降级末位事件"
                    " ext_info.session_msgs 快照,仅该 node 最后一条事件携带,"
                    "未拉到/无会话为 None;纯读实拉不落库)",
    )
    search_probe: dict[str, Any] | None = Field(
        None,
        description="派发搜推三问采样摘要(关键词/返回结果/最终选择;读时自本事件 "
                    "ext_info 定向投影:dispatch 事件取 _dispatch_rationale,relay "
                    "action_result=search 事件取顶层 search_sampling+candidates;"
                    "无搜推素材的事件为 None)",
    )
    actual_run_mode: str | None = Field(
        None,
        description="子任务真实执行模态(single_bot|coop_group|bbs;读时经 "
                    "effective_run_mode 定——task_node_run_info.extend_props."
                    "actual_run_mode 优先,缺失回退 run_mode;仅该 node 在 timeline "
                    "中的最后一条事件携带,无模态为 None)",
    )
    trace_steps: list[dict[str, Any]] | None = Field(
        None,
        description="Bot 上报的执行检查点(step/status/detail 三槽;读时自本事件 "
                    "ext_info.trace 投影——relay 上报写时发射已随 ext_info 落表;"
                    "无 trace 素材为 None)",
    )


class TaskTrajectoryDTO(BaseModel):
    """任务轨迹 DTO(仅时间线;无 phases / 无 graph_snapshot)。"""

    model_config = ConfigDict(from_attributes=True)

    task_id: str = Field(..., description="任务 ID")
    timeline: list[TrajectoryEventDTO] = Field(
        default_factory=list, description="按 gmt_create 升序的事件时间线"
    )
    analysis: str | None = Field(
        None,
        description="内嵌 TrajectoryAnalysis JSON 字符串(do_analysis=false 时取已落库值或 None;"
        "do_analysis=true 时为本次分析结果;客户端自行解析)",
    )
    gmt_create: int = Field(..., description="组装产出时间(ms epoch)")
    gmt_modified: int = Field(..., description="最后修改时间(分析回填时更新,未分析时等于 gmt_create)")


def _enum_value(v: object) -> str | None:
    """``StrEnum`` / ``Status`` → ``.value`` (str);``None`` → ``None``。"""
    if v is None:
        return None
    return v.value if hasattr(v, "value") else str(v)


def trajectory_to_dto(trajectory: "TaskTrajectory") -> TaskTrajectoryDTO:
    """``TaskTrajectory``(领域)→ ``TaskTrajectoryDTO``(边界 DTO;Rule 22)。

    扁平翻译:事件枚举(``action_type``/``status_from``/``status_to``/``error_type``)取 ``.value`` 字符串;
    ``analysis`` 透传 JSON 字符串(客户端解析,不在边界反序列化为对象——执行者多源 + 保持 P0 扁平);
    ``ext_info`` 不整体进入领域对象/DTO(REQ-1),仅透出定向投影的 ``holder_id``。
    两模式(do_analysis 真/假)返回同形态。
    """
    return TaskTrajectoryDTO(
        task_id=trajectory.task_id,
        timeline=[
            TrajectoryEventDTO(
                task_id=ev.task_id,
                node_id=ev.node_id,
                action_type=_enum_value(ev.action_type) or "",
                action_result=ev.action_result,
                attempt=ev.attempt,
                gmt_create=ev.gmt_create,
                gmt_modified=ev.gmt_modified,
                action_input=ev.action_input,
                status_from=_enum_value(ev.status_from),
                status_to=_enum_value(ev.status_to),
                error_type=_enum_value(ev.error_type),
                error_msg=ev.error_msg,
                boost_reason=ev.boost_reason,
                holder_id=ev.holder_id,
                analysis=ev.analysis,
                output=ev.output,
                session_msgs=ev.session_msgs,
                search_probe=ev.search_probe,
                actual_run_mode=ev.actual_run_mode,
                trace_steps=ev.trace_steps,
            )
            for ev in trajectory.timeline
        ],
        analysis=trajectory.analysis,
        gmt_create=trajectory.gmt_create,
        gmt_modified=trajectory.gmt_modified,
    )
