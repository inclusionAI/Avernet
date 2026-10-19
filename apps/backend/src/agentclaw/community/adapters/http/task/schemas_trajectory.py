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
    action_result: str = Field(..., description="动作结果。系统动作使用结果词(success|hit_single|miss|failed|sla_timeout 等);Relay S-step/dispatch trace 使用该步骤推理输出 JSON(含 status 与实际输出字段)")
    attempt: int = Field(..., description="harness 重试序号快照")
    gmt_create: int = Field(..., description="事件发射时间(ms epoch;timeline 排序依据)")
    gmt_modified: int = Field(..., description="最后修改时间(分析回填时更新,未回填时等于 gmt_create)")
    action_input: str | None = Field(None, description="动作输入内容(submit=task_spec_digest/plan=prompt_digest/execute|verify=request_input;reset|transition=None)")
    status_from: str | None = Field(None, description="动作前节点状态(未翻态时 None)")
    status_to: str | None = Field(None, description="动作后节点状态(未翻态时 None)")
    error_type: str | None = Field(None, description="ReasonCatalog 错误分类(成功为 None)")
    error_msg: str | None = Field(None, description="截断后的错误消息(成功为 None)")
    boost_reason: str | None = Field(None, description="执行过程证据：为什么推进、为什么选择等等维度信息")
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
        description="子任务真实执行模态(single_bot|coop_group|bbs;读时规则(2026-10-16):"
                    "task_node_run_info.run_mode=bbs 时直判 bbs(不看 extend_props."
                    "actual_run_mode);其余模态 extend_props.actual_run_mode 优先、"
                    "缺失回退 run_mode;仅该 node 在 timeline 中的最后一条事件携带,"
                    "无模态为 None)",
    )
    system_logs: "list[TrajectoryEventDTO] | None" = Field(
        None,
        description="主事件(S 码)嵌挂的系统子事件列表(读时嵌套,不落库):本子任务"
                    "内非 S 码 action_type、且 id 落在〔本主事件,下一主事件〕区间"
                    "的系统侧事件,按 id 正序;无嵌挂为 None",
    )
    action_name: str = Field(
        "",
        description="action_type 的中文人读名(submit/S 码 step/虚拟展示事件的"
                    "既定映射;未命中映射兜底为 action_type 原文)",
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


# action_type → action_name 中文人读名(2026-10-16):已知码取映射,未知码兜底
# 为 action_type 原文(自由形态——S 码 step / harness-engine- 前缀 / 枚举裸词皆可)。
_ACTION_NAME_MAP = {
    "submit": "任务明确并提交",
    "S1_CONTEXT": "步骤1:获取任务上下文",
    "S2_GAPS": "步骤2:计算任务GAPs",
    "S3_CAPABILITY": "步骤3:当前Bot能力匹配",
    "S4A_EXECUTION": "步骤4A:任务执行",
    "S4B_REPORT": "步骤4B:当前结果上报",
    "S5A_REFRESH_GAPS": "步骤5A:更新任务GAPs",
    "S5B_PLAN_NEXT_NODE": "步骤5B:规划下一步目标",
    "S5C_SEARCH_CANDIDATES": "步骤5C:搜索下一棒候选者",
    "S5D_SELECT_EXECUTOR": "步骤5D:确定下一棒执行人",
    "S5E_REPORT_PLAN": "步骤5E:上报下一棒任务规划信息",
    "task_result_display": "当前子任务结果及消息详情展示",
}


def _resolve_action_name(action_type: object) -> str:
    """``action_type``(枚举或自由字符串)→ 中文人读名;未命中映射兜底原文。"""
    value = str(getattr(action_type, "value", action_type) or "")
    return _ACTION_NAME_MAP.get(value, value)


def _event_to_dto(ev) -> TrajectoryEventDTO:
    """单事件领域→DTO 翻译(顶层与 system_logs 嵌挂复用;嵌挂层不含再嵌挂)。"""
    return TrajectoryEventDTO(
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
        system_logs=(
            [_event_to_dto(sub) for sub in ev.system_logs]
            if ev.system_logs
            else None
        ),
        action_name=_resolve_action_name(ev.action_type),
    )


def trajectory_to_dto(trajectory: "TaskTrajectory") -> TaskTrajectoryDTO:
    """``TaskTrajectory``(领域)→ ``TaskTrajectoryDTO``(边界 DTO;Rule 22)。

    扁平翻译:事件枚举(``action_type``/``status_from``/``status_to``/``error_type``)取 ``.value`` 字符串
    (S 码主事件的 action_type 为自由字符串,``_enum_value`` 兜底透传);
    ``analysis`` 透传 JSON 字符串(客户端解析,不在边界反序列化为对象——执行者多源 + 保持 P0 扁平);
    ``ext_info`` 不整体进入领域对象/DTO(REQ-1),仅透出定向投影的 ``holder_id``。
    两模式(do_analysis 真/假)返回同形态;S 码主事件的 ``system_logs`` 递归透出。
    """
    return TaskTrajectoryDTO(
        task_id=trajectory.task_id,
        timeline=[_event_to_dto(ev) for ev in trajectory.timeline],
        analysis=trajectory.analysis,
        gmt_create=trajectory.gmt_create,
        gmt_modified=trajectory.gmt_modified,
    )
