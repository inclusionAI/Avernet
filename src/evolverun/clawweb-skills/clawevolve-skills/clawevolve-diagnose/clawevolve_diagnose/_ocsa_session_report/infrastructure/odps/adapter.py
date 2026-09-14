"""
ODPS 宽表适配器 — 为 LLM-as-Judge 提供 ODPS 数据桥接

功能:
  1. ODPS 行适配: 将 snake_case 宽表字段自动转换为 camelCase 内部格式
  2. Cron 任务分组与抽样
"""

from __future__ import annotations

import logging
import random
import re
from collections import defaultdict
from typing import Any

from ...domain.features import (
    extract_judge_signals,
    format_judge_signals,
    rule_based_prefilter,
)
from ...parsers.conversation_parser import _strip_untrusted_metadata
from ...utils.type_utils import (
    parse_comma_list,
    parse_tool_positions,
    safe_bool,
    safe_float,
    safe_int,
    safe_json_parse,
)

logger = logging.getLogger(__name__)

CRON_SAMPLE_RATIO = 0.01
CRON_SAMPLE_MAX = 1000


# ═══════════════════════════════════════════════════════════
#  ODPS 字段映射
# ═══════════════════════════════════════════════════════════

# ODPS snake_case → (内部 camelCase, 类型转换)
# 类型: None=直传, int/BIGINT→int, float/DOUBLE→float, "json"→json.loads
_ODPS_FIELD_MAP: dict[str, tuple[str, type | str | None]] = {
    # 基础信息
    "session_id": ("session_id", None),
    "user_id": ("userId", None),
    "bot_id": ("botId", None),
    "file_path": ("filePath", None),
    "dt": ("dt", None),
    "start_time": ("start_time", None),
    "end_time": ("end_time", None),
    "total_tokens": ("totalTokens", int),
    "duration_seconds": ("durationSeconds", int),
    "session_duration_minutes": ("sessionDurationMinutes", float),
    # 消息统计
    "user_msg_cnt": ("userMsgCnt", int),
    "assistant_msg_cnt": ("assistantMsgCnt", int),
    "tool_result_cnt": ("toolResultCnt", int),
    "error_tool_result_cnt": ("errorToolResultCnt", int),
    "total_msg_cnt": ("totalMsgCnt", int),
    # Skill 统计
    "is_single_turn": ("isSingleTurn", None),
    "load_skill": ("loadSkill", None),
    "all_exe_skill": ("allExeSkill", None),
    "all_exe_skill_idx": ("allExeSkillIdx", None),
    "non_link_skills": ("nonLinkSkills", None),
    "failed_exe_skill": ("failedExeSkill", None),
    "failed_exe_skill_code": ("failedExeSkillCode", None),
    "exe_skill_cnt": ("exeSkillCnt", int),
    "distinct_exe_skill_cnt": ("distinctExeSkillCnt", int),
    "failed_exe_skill_cnt": ("failedExeSkillCnt", int),
    # MCP 统计
    "all_exe_mcp": ("allExeMcp", None),
    "all_exe_mcp_idx": ("allExeMcpIdx", None),
    "failed_exe_mcp": ("failedExeMcp", None),
    "failed_exe_mcp_code": ("failedExeMcpCode", None),
    "mcp_call_cnt": ("mcpCallCnt", int),
    "distinct_mcp_call_cnt": ("distinctMcpCallCnt", int),
    "failed_mcp_call_cnt": ("failedMcpCallCnt", int),
    # 维度标记 (布尔值，需正确转换字符串 "true"/"false")
    "is_cron": ("isCron", "bool"),
    "is_bcs": ("isBcs", "bool"),
    "is_from_dingtalk": ("isFromDingtalk", "bool"),
    # 模型与工具统计 (JSON 字符串)
    "model_cnt": ("modelCnt", "json"),
    "stop_reason_stats": ("stopReasonStats", "json"),
    "tool_result_status_distribution": ("toolResultStatusDistribution", "json"),
    "tool_result_handled_errors": ("toolResultHandledErrors", int),
    "tool_result_unhandled_errors": ("toolResultUnhandledErrors", int),
    "tool_use_stats": ("toolUseStats", "json"),
    "avg_assistant_time_in_ms": ("avgAssistantTimeInMs", float),
    "avg_tool_execution_time_ms": ("avgToolExecutionTimeMs", float),
    "tool_execution_count": ("toolExecutionCount", int),
    "exec_command_stats": ("execCommandStats", "json"),
    # Token 统计
    "error_count": ("errorCount", int),
    "input_tokens": ("inputTokens", int),
    "output_tokens": ("outputTokens", int),
    "cache_read_tokens": ("cacheReadTokens", int),
    "cache_write_tokens": ("cacheWriteTokens", int),
    # 消息内容 (JSON 字符串)
    "messages": ("messages", "json"),
    # 辅助字段
    "raw_message_cnt": ("rawMessageCnt", int),
    "has_session_meta": ("hasSessionMeta", int),
    "is_heartbeat_system_only": ("isHeartbeatSystemOnly", int),
}


# ═══════════════════════════════════════════════════════════
#  ODPS 行适配
# ═══════════════════════════════════════════════════════════

def _is_odps_format(row: dict) -> bool:
    """检测行数据是否为 ODPS 宽表格式（snake_case 字段名）"""
    odps_keys = {"all_exe_skill", "user_msg_cnt", "tool_result_cnt", "is_single_turn"}
    return bool(odps_keys & set(row.keys()))


def adapt_odps_row(row: dict) -> dict:
    """将 ODPS 宽表行（snake_case）转换为内部 camelCase 格式

    对于已经是 camelCase 的输入（旧格式），直接返回（仅解析 JSON messages 字段）。

    注意：为了保证下游 Writer 能正确获取关键字段（如 session_id, user_id, bot_id,
    start_time, end_time），转换后会同时保留原始 snake_case 字段名和转换后的 camelCase
    字段名。这样可以确保无论下游代码使用哪种字段名，都能正确获取值。
    """
    if not _is_odps_format(row):
        result = dict(row)
        if "messages" in result and isinstance(result["messages"], str):
            parsed = safe_json_parse(result["messages"])
            if parsed is not None:
                result["messages"] = parsed
        return result

    result: dict[str, Any] = {}

    for odps_key, (internal_key, converter) in _ODPS_FIELD_MAP.items():
        value = row.get(odps_key)
        if value is None:
            continue

        if converter == "json":
            parsed = safe_json_parse(value)
            if parsed is not None:
                result[internal_key] = parsed
        elif converter == "bool":
            result[internal_key] = safe_bool(value)
        elif converter is int:
            converted = safe_int(value)
            if converted is not None:
                result[internal_key] = converted
        elif converter is float:
            converted = safe_float(value)
            if converted is not None:
                result[internal_key] = converted
        else:
            result[internal_key] = value

        # 同时保留原始 snake_case 字段名，确保下游 Writer 能正确获取关键字段
        # 这样无论下游使用 session.get("user_id") 还是 session.get("userId") 都能获取值
        if odps_key != internal_key:
            result[odps_key] = result[internal_key]

    # 保留未映射的字段
    for key, value in row.items():
        if key not in _ODPS_FIELD_MAP and key not in result:
            result[key] = value

    return result


# ═══════════════════════════════════════════════════════════
#  Cron 任务分组与抽样
# ═══════════════════════════════════════════════════════════

def extract_cron_task_description(session: dict) -> str | None:
    """从第一条用户消息中提取 cron 任务简要说明

    提取规则：从第一个 [] 中提取文字，按空格解析后取最后一个元素
    例如：[cron:6ce1fa1b-8dd5-4b58-b78c-c67c30d763e0 技能监控与自动修复] → "技能监控与自动修复"

    注意：新 ODPS 格式中用户消息可能带有 "Sender (untrusted metadata)" 前缀，
    需先去除后再查找 [cron:...] 模式。
    """
    # 先尝试从适配后的 session 获取 messages
    messages = session.get("messages", [])
    if not messages:
        return None

    # 找到第一条用户消息
    first_user_message = None
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "user" and content:
            first_user_message = content
            break

    if not first_user_message:
        return None

    # 去除 untrusted metadata 前缀和 BCS 上下文
    first_user_message = _strip_untrusted_metadata(first_user_message)

    # 在整个内容中搜索 [cron:...] 模式
    match = re.search(r"\[([^\]]*cron[^\]]*)\]", first_user_message, re.IGNORECASE)
    if not match:
        # 回退：匹配任意第一个 [] 内容
        match = re.search(r"\[([^\]]+)\]", first_user_message)
    if not match:
        return None

    bracket_content = match.group(1)
    # 按空格分割，取最后一个元素
    parts = bracket_content.split()
    if not parts:
        return None

    return parts[-1]


def build_sampling_group_key(session: dict, task_description: str | None = None) -> str:
    """构建抽样分组键

    格式: {user_id}_{bot_id}
    仅对 cron session 有效，非 cron session 返回空字符串。

    说明: 组键不含 task_description。若带 task_description (LLM 提取的细粒度文本),
    会导致同 user+bot 的不同 session 因任务文本略有差异被拆成大量 size=1 的小组,
    抽样退化成全保留 (sampled_cnt=max(1, int(1*0.1))=1, 1<=1 全留), 既不省算力
    也拿不到反推降波动收益。按 user+bot 分组可让高频户的 cron 聚成大组, 抽样真正生效。

    Args:
        session: 适配后的 session 数据
        task_description: 已弃用 (保留参数向后兼容, 不再参与组键)

    Returns:
        抽样分组键, 或空字符串（非 cron session）
    """
    is_cron = session.get("isCron", False)
    if not is_cron:
        return ""

    # 获取 user_id 和 bot_id
    user_id = session.get("userId") or session.get("user_id") or "unknown"
    bot_id = session.get("botId") or session.get("bot_id") or "unknown"

    return f"{user_id}_{bot_id}"


def group_and_sample_cron_sessions(
    sessions: list[dict],
    sample_ratio: float = CRON_SAMPLE_RATIO,
    max_sample_per_group: int = CRON_SAMPLE_MAX,
    random_seed: int | None = None,
    return_group_weights: bool = False
):
    """对 is_cron=true 的 session 进行分组和抽样

    分组依据：user_id, bot_id, isCron, task_description
    每组按比例随机抽样（默认 10%），最少保留 1 个，最多保留 max_sample_per_group 个

    Args:
        sessions: session 列表
        sample_ratio: 抽样比例，默认 CRON_SAMPLE_RATIO
        max_sample_per_group: 每组最大抽样数量，默认 CRON_SAMPLE_MAX
        random_seed: 随机种子，设置后保证每次抽样结果一致；为 None 时不固定种子
        return_group_weights: 若 True，额外返回 cron 分组权重行列表 (group_total/sampled_cnt/
            sampling_weight)，用于写组权重 side output 表支撑反推统计 (B2 修复)。
            默认 False 保持原返回语义 (仅返回抽样后 session list)。

    Returns:
        return_group_weights=False: list[dict] (抽样后 session，行为与历史一致)
        return_group_weights=True : (list[dict], list[dict]) —— session list + 分组权重行
    """
    # 先适配 sessions，提取辅助信息
    adapted_sessions = []
    for session in sessions:
        adapted = adapt_odps_row(session)
        is_cron = adapted.get("isCron", False)
        task_description = extract_cron_task_description(adapted) if is_cron else None

        adapted_sessions.append({
            "session": adapted,
            "is_cron": is_cron,
            "task_description": task_description,
        })

    # 分离 cron 和非 cron
    cron_sessions = [s for s in adapted_sessions if s["is_cron"]]
    non_cron_sessions = [s for s in adapted_sessions if not s["is_cron"]]

    if not cron_sessions:
        # 无 cron 任务，直接返回原始顺序的非 cron sessions
        if return_group_weights:
            return [s["session"] for s in non_cron_sessions], []
        return [s["session"] for s in non_cron_sessions]

    # 按 (user_id, bot_id, task_description) 分组
    groups: dict[tuple, list[dict]] = defaultdict(list)

    for s in cron_sessions:
        session = s["session"]
        user_id = session.get("userId") or session.get("user_id") or "unknown"
        bot_id = session.get("botId") or session.get("bot_id") or "unknown"

        # 组键 = (user_id, bot_id), 不含 task_description (避免组过细退化成全保留)
        key = (user_id, bot_id)
        groups[key].append(session)

    # 对每个组按比例随机抽样
    # 使用独立 Random 实例，避免多线程下全局 random 状态被并发干扰导致 seed 失效
    rng = random.Random(random_seed)

    sampled_cron = []
    dropped_count = 0
    group_weight_rows: list[dict] = []  # B2: 组权重 side output 行

    for key, group_sessions in groups.items():
        group_size = len(group_sessions)
        # 计算抽样数量：按比例，最多 max_sample_per_group 个，最少 1 个
        sample_count = int(group_size * sample_ratio)
        sample_count = min(sample_count, max_sample_per_group)
        sample_count = max(1, sample_count)
        sample_count = min(sample_count, group_size)

        if group_size <= sample_count:
            # 组大小不超过抽样数量，全部保留
            sampled_cron.extend(group_sessions)
        else:
            # 随机抽样
            sampled = rng.sample(group_sessions, sample_count)
            sampled_cron.extend(sampled)
            dropped_count += group_size - sample_count

        # B2: 收集本组权重行 —— 组键与抽样判定同一 key (复用 key 元组拼接, 与
        # build_sampling_group_key 输出一致)，保证下游反推 JOIN 不错配
        user_id, bot_id = key
        sampling_weight = (group_size / sample_count) if sample_count > 0 else 1.0
        group_weight_rows.append({
            "sampling_group_key": f"{user_id}_{bot_id}",
            "group_total": group_size,
            "sampled_cnt": sample_count,
            "sampling_weight": round(sampling_weight, 6),
            "sample_ratio": sample_ratio,
            "max_sample_per_group": max_sample_per_group,
        })

    # 日志输出
    logger.info(f"[Cron Sampling] Total cron sessions: {len(cron_sessions)}, "
                f"Groups: {len(groups)}, Sampled: {len(sampled_cron)}, Dropped: {dropped_count}, "
                f"Ratio: {sample_ratio*100:.0f}%, Max per group: {max_sample_per_group}")

    # 合并结果：非 cron 全部保留 + 抽样后的 cron
    result = [s["session"] for s in non_cron_sessions]
    result.extend(sampled_cron)

    if return_group_weights:
        return result, group_weight_rows
    return result


# 导出信号相关函数（向后兼容）
__all__ = [
    "adapt_odps_row",
    "extract_judge_signals",
    "format_judge_signals",
    "rule_based_prefilter",
    "parse_comma_list",
    "parse_tool_positions",
    "extract_cron_task_description",
    "build_sampling_group_key",
    "group_and_sample_cron_sessions",
]