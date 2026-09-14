"""
辅助信号提取与格式化 — 为 LLM Judge 提供结构化上下文

从 ODPS session 数据中提取预计算信号，格式化为可注入 Prompt 的文本。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..utils.type_utils import safe_bool, parse_comma_list, parse_tool_positions


def extract_judge_signals(session: dict) -> dict[str, Any]:
    """从适配后的 session 中提取 Judge 引擎专用辅助信号"""
    signals: dict[str, Any] = {}

    # 消息统计
    signals["user_msg_cnt"] = session.get("userMsgCnt") or 0
    signals["assistant_msg_cnt"] = session.get("assistantMsgCnt") or 0
    signals["tool_result_cnt"] = session.get("toolResultCnt") or 0

    # 维度标记
    signals["is_single_turn"] = safe_bool(session.get("isSingleTurn", False))
    signals["is_cron"] = safe_bool(session.get("isCron", False))
    signals["is_bcs"] = safe_bool(session.get("isBcs", False))
    signals["is_from_dingtalk"] = safe_bool(session.get("isFromDingtalk", False))

    # 分布统计（JSON 对象）
    signals["stop_reason_stats"] = session.get("stopReasonStats") or {}

    # 中止类信号 (用于程序层 aborted 打标, 跳过 is_complete LLM 调用)
    # stopReasonStats 形如 {"toolUse":3,"stop":1,"aborted":1,"error":0,"length":0,"total":5}
    _srs = signals["stop_reason_stats"] if isinstance(signals["stop_reason_stats"], dict) else {}
    try:
        signals["abort_stop_cnt"] = int(_srs.get("stop", 0) or 0)
    except (TypeError, ValueError):
        signals["abort_stop_cnt"] = 0
    try:
        signals["abort_aborted_cnt"] = int(_srs.get("aborted", 0) or 0)
    except (TypeError, ValueError):
        signals["abort_aborted_cnt"] = 0

    signals["tool_result_status_distribution"] = session.get("toolResultStatusDistribution") or {}
    signals["tool_use_stats"] = session.get("toolUseStats") or {}
    signals["exec_command_stats"] = session.get("execCommandStats") or {}

    # Skill / MCP 列表（仅提取成功执行的，去除干扰信息）
    signals["all_exe_skill"] = parse_comma_list(session.get("allExeSkill", ""))
    signals["all_exe_mcp"] = parse_comma_list(session.get("allExeMcp", ""))
    signals["load_skill"] = session.get("loadSkill", "")

    # Skill / MCP 计数（去除失败计数干扰）
    signals["distinct_exe_skill_cnt"] = session.get("distinctExeSkillCnt") or 0
    signals["distinct_mcp_call_cnt"] = session.get("distinctMcpCallCnt") or 0
    signals["mcp_call_cnt"] = session.get("mcpCallCnt") or 0
    signals["exe_skill_cnt"] = session.get("exeSkillCnt") or 0

    # 位置索引 — 原始字符串保留兼容
    signals["skill_idx"] = session.get("allExeSkillIdx", "")
    signals["mcp_idx"] = session.get("allExeMcpIdx", "")

    # 位置映射 — tool_name → [message_idx_list]
    skill_names_str = session.get("allExeSkill", "") or ""
    skill_idx_str = session.get("allExeSkillIdx", "") or ""
    mcp_names_str = session.get("allExeMcp", "") or ""
    mcp_idx_str = session.get("allExeMcpIdx", "") or ""
    signals["skill_positions"] = parse_tool_positions(skill_names_str, skill_idx_str)
    signals["mcp_positions"] = parse_tool_positions(mcp_names_str, mcp_idx_str)

    # 时长
    signals["session_duration_minutes"] = session.get("sessionDurationMinutes") or 0.0

    return signals


def _fmt_dict_stats(stats: dict | None, label: str) -> str | None:
    """将 dict 统计格式化为单行文本，无数据时返回 None"""
    if not stats or not isinstance(stats, dict):
        return None
    parts = [f"{k}={v}" for k, v in stats.items() if v]
    return f"- {label}: {', '.join(parts)}" if parts else None


def _fmt_message_stats(signals: dict) -> list[str]:
    """格式化消息统计"""
    lines = []
    lines.append(
        f"- 用户消息数: {signals.get('user_msg_cnt', 0)}, "
        f"助手消息数: {signals.get('assistant_msg_cnt', 0)}"
    )
    tool_cnt = signals.get("tool_result_cnt", 0)
    lines.append(f"- 工具结果数: {tool_cnt}")
    return lines


def _fmt_tool_stats(signals: dict) -> list[str]:
    """格式化工具调用统计"""
    lines = []

    line = _fmt_dict_stats(signals.get("tool_result_status_distribution"), "工具结果状态")
    if line:
        lines.append(line)

    for key, label in [
        ("stop_reason_stats", "停止原因"),
        ("tool_use_stats", "工具使用分布"),
        ("exec_command_stats", "执行命令统计"),
    ]:
        line = _fmt_dict_stats(signals.get(key), label)
        if line:
            lines.append(line)

    return lines


def _fmt_skill_info(signals: dict) -> list[str]:
    """格式化 Skill 信息（含 message index 位置标注）"""
    lines = []
    skills = signals.get("all_exe_skill", [])
    skill_positions = signals.get("skill_positions", {})
    load_skill = signals.get("load_skill", "")

    if skills:
        if skill_positions:
            parts = []
            for s in skills:
                idxs = skill_positions.get(s, [])
                if idxs:
                    idx_str = ", ".join(f"#{i}" for i in idxs)
                    parts.append(f"{s} (消息 {idx_str})")
                else:
                    parts.append(s)
            lines.append(f"- 执行的 skill: {', '.join(parts)}")
        else:
            lines.append(f"- 执行的 skill: {', '.join(skills)}")

    if load_skill:
        loaded = [s.strip() for s in load_skill.split(",") if s.strip()]
        unused = [s for s in loaded if s not in skills]
        if unused:
            lines.append(f"- 加载但未执行的 skill: {', '.join(unused)}")

    return lines


def _fmt_mcp_info(signals: dict) -> list[str]:
    """格式化 MCP 信息（含 message index 位置标注）"""
    lines = []
    mcps = signals.get("all_exe_mcp", [])
    mcp_positions = signals.get("mcp_positions", {})
    mcp_call_cnt = signals.get("mcp_call_cnt", 0)
    distinct_mcp = signals.get("distinct_mcp_call_cnt", 0)

    if mcps:
        mcp_counter = Counter(mcps)
        if mcp_positions:
            mcp_parts = []
            seen = set()
            for name in mcps:
                if name in seen:
                    continue
                seen.add(name)
                cnt = mcp_counter[name]
                idxs = mcp_positions.get(name, [])
                idx_str = ", ".join(f"#{i}" for i in idxs)
                base = f"{name}({cnt}次)" if cnt > 1 else name
                if idx_str:
                    mcp_parts.append(f"{base} (消息 {idx_str})")
                else:
                    mcp_parts.append(base)
        else:
            mcp_parts = [f"{name}({cnt}次)" if cnt > 1 else name for name, cnt in mcp_counter.items()]
        lines.append(f"- 执行的 MCP: {', '.join(mcp_parts)} [共{distinct_mcp}种, {mcp_call_cnt}次]")

    return lines


def _fmt_session_flags(signals: dict) -> list[str]:
    """格式化会话类型标记和时长"""
    lines = []

    flags = []
    if signals.get("is_cron"):
        flags.append("Cron 会话")
    if signals.get("is_bcs"):
        flags.append("BCS 会话")
    if signals.get("is_from_dingtalk"):
        flags.append("来自钉钉")
    if flags:
        lines.append(f"- 会话类型: {' | '.join(flags)}")

    duration = signals.get("session_duration_minutes", 0)
    if duration:
        lines.append(f"- 会话时长: {duration:.1f} 分钟")

    return lines


def format_judge_signals(signals: dict) -> str:
    """将辅助信号格式化为 Prompt 可注入的文本区域"""
    sections = [
        ["[辅助信号 — 以下为程序统计结果，可直接作为判断依据]"],
        _fmt_message_stats(signals),
        _fmt_tool_stats(signals),
        _fmt_skill_info(signals),
        _fmt_mcp_info(signals),
        _fmt_session_flags(signals),
    ]
    return "\n".join(line for section in sections for line in section)


def rule_based_prefilter(session: dict, signals: dict) -> dict:
    """基于结构化信号的规则前置判断，减少不必要的 LLM 调用

    返回:
        skip_task_split: 是否跳过多任务检测（确定单任务）
        is_multitask: 确定值 0 或 None（None 表示需 LLM 判断）
        skip_skill_inspector: 是否跳过 Skill Inspector
        skip_mcp_inspector: 是否跳过 MCP Inspector

    注意:
        failed_exe_skill_cnt、failed_mcp_call_cnt、error_tool_result_cnt 等字段
        在原始数据中为 0 并不代表没有错误，因此不能作为规则信号来跳过 LLM 调用。
        只有"无工具执行"这一事实可以作为确定性规则来跳过 Inspector。
    """
    result = {
        "skip_task_split": False,
        "is_multitask": None,
        "skip_skill_inspector": False,
        "skip_mcp_inspector": False,
        "is_aborted": None,        # None=非中止 | "system_aborted" | "user_stopped"
        "abort_reason": "",        # 中文/英文原因描述, 便于落 reasoning 字段单独分析
    }

    # 规则1: 单轮对话 → 跳过 Task Split
    user_msg_cnt = signals.get("user_msg_cnt", 0)
    is_single_turn = signals.get("is_single_turn", False)
    if is_single_turn or user_msg_cnt <= 1:
        result["skip_task_split"] = True
        result["is_multitask"] = 0

    # 规则2: Skill/MCP Inspector 不再跳过
    # 移除规则前置：让 LLM 根据对话内容自行判断，避免干扰信息影响评估结果

    # 规则3: 中止类会话程序层打标 (退出完成率分母)
    # ⚠️ 高精度约束: 仅在"几乎零误杀"的确定性场景才程序层硬标 aborted, 其余一律交 LLM 判完成度。
    # 历史教训: 曾用 stop_reason_stats.stop>0 判用户放弃, 但 stop 在正常成功会话中命中率高达 ~93%
    # (即 assistant 正常跑完任务的自然停止原因), 叠加"单轮"约束仍误杀 99.8% 单轮会话, 导致完成率暴跌。
    # 故: user_stopped 分支已废弃; system_aborted 收紧到"未真正干活就被系统掐断"的极窄判据。
    abort_tag = detect_abort(session, signals)
    if abort_tag:
        result["is_aborted"] = abort_tag["tag"]
        result["abort_reason"] = abort_tag["reason"]
        # 中止场景多为单任务 / 跳过 is_complete LLM 调用
        result["skip_task_split"] = True
        result["is_multitask"] = 0

    return result


# 系统中止硬标阈值: 助手产出上限 / 工具结果上限。
# 取值依据 7/9 备份数据实证(单任务, aborted>0 全集 171 个):
#   - 17 个"aborted>0 但实际 success"的会话, assistant_msg 全部 >=12, tool_result 全部 >=7
#     (长任务里零星 aborted 但整体完成 → 绝不能标)。
#   - 故阈值定在 asst<=3 & tres<=2: 对 success 实现零误杀(远低于其实际下界 12/7),
#     同时仍能抓住 ic=3 池里 asst min=2 的极短中止会话助手无产出的特征。
#   - 注: session_full.duration_seconds / duration_minutes 在该批次全为 0(未填充), 不可用作判据,
#     故不纳入条件, 避免永远为 False 的死条件。
# 宁可漏过(交 LLM 兜底), 不可误杀(误杀进完成率分母拉低指标)。
ABORT_ASSISTANT_MSG_MAX = 3
ABORT_TOOL_RESULT_MAX = 2


def detect_abort(session: dict, signals: dict) -> dict | None:
    """程序层判定 session 是否属于「系统中止类」(高精度, 仅零误杀判据)。

    返回 None 表示程序层不判定(交 LLM); 否则返回 {"tag","reason"}:
      - system_aborted: 会话被系统/运行时中止(stopReasonStats.aborted>0), 且「助手几乎无产出 +
        无有效工具结果」(assistant_msg<=3 AND tool_result<=2)。

    设计:
      - stop_reason_stats.stop 信号无区分度(正常会话命中率~93%), 不再用作判据(历史误杀根因)。
      - aborted 信号本身确定(系统中止), 但长任务里 aborted 也可能伴随整体完成(超时掐断但已出结果),
        故必须叠加"助手几乎无产出 + 无工具结果", 只标「没干成活就被掐」的那一小撮。
      - 阈值依据 7/9 备份数据: success+aborted 会话 asst>=12/tres>=7, 本阈值 3/2 对其零命中。
      - 宁可漏过(交 LLM 兜底), 不可误杀(误杀进完成率分母拉低指标)。
    """
    aborted_cnt = signals.get("abort_aborted_cnt", 0) or 0
    if aborted_cnt <= 0:
        return None

    assistant_msg_cnt = signals.get("assistant_msg_cnt", 0) or 0
    tool_result_cnt = signals.get("tool_result_cnt", 0) or 0

    if (
        assistant_msg_cnt <= ABORT_ASSISTANT_MSG_MAX
        and tool_result_cnt <= ABORT_TOOL_RESULT_MAX
    ):
        return {
            "tag": "system_aborted",
            "reason": (
                f"会话被系统中止(SIGTERM/aborted={aborted_cnt}), "
                f"且助手几乎无产出(asst={assistant_msg_cnt})+无有效工具结果(tres={tool_result_cnt}), "
                f"判定为未完成即被掐断, 退出完成率统计"
            ),
        }
    return None