"""
ODPS 结果字段映射器 — 将 JudgeReport 转换为 ODPS 表字段

职责：
  - 将 judge_report JSON 转换为 ODPS 表字段格式 (llm_ 前缀)
  - 成功经验提取与写入
  - 不负责通用的 ODPS 写入（由 infrastructure/storage/result_writer.py 处理）

注意：
  - write_to_odps_table, save_to_jsonl 等通用写入函数已移至 result_writer.py
  - 本文件保留 session_report 特有的字段转换和成功经验提取逻辑
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import logging
get_logger = logging.getLogger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════
#  通用写入函数（从 result_writer 导入）
# ═══════════════════════════════════════════════════════════

def write_to_odps_table(odps_rows: list[dict], table_name: str, partition: str) -> int:
    """写入 ODPS 表（diagnose 内置副本不启用 ODPS 写入）。"""
    raise NotImplementedError("diagnose local session_report copy does not write ODPS")


def save_judge_results_to_json(odps_rows: list[dict], output_jsonl_path: str | Path) -> int:
    """将结果保存为本地 JSONL 文件。"""
    path = Path(output_jsonl_path)
    with path.open("w", encoding="utf-8") as f:
        for row in odps_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(odps_rows)


# ═══════════════════════════════════════════════════════════
#  字段安全转换
# ═══════════════════════════════════════════════════════════

def _safe_int_or_null(val: Any) -> int | None:
    """安全地转换为整数或 None，处理 NaN/inf 等非有限值"""
    if val is None:
        return None
    try:
        if isinstance(val, float):
            if not math.isfinite(val):
                return None
        n = int(val)
        return n if n >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_json_dumps(value: Any) -> str:
    """安全 JSON 序列化，失败时返回空数组字符串。"""
    try:
        return json.dumps(value if value is not None else [], ensure_ascii=False)
    except (TypeError, ValueError):
        return "[]"


# ═══════════════════════════════════════════════════════════
#  Session Report ODPS 字段转换
# ═══════════════════════════════════════════════════════════

def convert_judge_report_to_odps_fields(
    session_input: dict,
    judge_report: dict,
    is_sampled: bool = False,
    sampling_group_key: str | None = None,
) -> dict:
    """将 JudgeReport 转换为 ODPS 表字段

    Args:
        session_input: 原始 ODPS 输入行 (snake_case)
        judge_report: JudgeEngine 输出的 judge_report
        is_sampled: 是否经过抽样
        sampling_group_key: 抽样分组键

    Returns:
        ODPS 表字段字典 (字段名 -> 值)
    """
    report = judge_report.get("judge_report", {})
    tasks = report.get("tasks", [])
    first_task = tasks[0] if tasks else {}

    skill_stats = _aggregate_tool_stats(tasks, "skills")
    mcp_stats = _aggregate_tool_stats(tasks, "mcps")

    failure_categories = _collect_failure_categories(tasks)
    llm_failure_categories = ",".join(failure_categories) if failure_categories else ""

    # 获取 LLM 模型映射，转换为 JSON 字符串
    llm_models = report.get("llm_models", {})
    llm_version = json.dumps(llm_models, ensure_ascii=False) if llm_models else "{}"

    result = {
        # 主键 (dt 作为分区列，不写入数据字段)
        "session_id": session_input.get("session_id") or session_input.get("sessionId", ""),
        # 原始会话信息 (适配 camelCase: ODPS 数据经 adapt_odps_row 转换后为 userId/botId)
        "user_id": session_input.get("userId") or session_input.get("user_id", ""),
        "bot_id": session_input.get("botId") or session_input.get("bot_id", ""),
        "start_time": session_input.get("start_time", ""),
        "end_time": session_input.get("end_time", ""),
        # LLM 评估结果
        "llm_version": llm_version,  # JSON 字符串：任务名称 -> 模型名称映射
        "llm_judged_at": report.get("judged_at", ""),
        "llm_is_multitask": report.get("is_multitask", 0),
        "llm_task_count": report.get("task_count", 0),
        # Tasks JSON
        "llm_tasks_json": json.dumps(tasks, ensure_ascii=False),
        # 抽样标识
        "is_sampled": 1 if is_sampled else 0,
        "sampling_group_key": sampling_group_key or "",
        # 错误标识
        "llm_has_skill_error": 1 if skill_stats["failed"] > 0 or skill_stats["incorrect"] > 0 else 0,
        "llm_has_mcp_error": 1 if mcp_stats["failed"] > 0 or mcp_stats["incorrect"] > 0 else 0,
        "llm_failure_categories": llm_failure_categories,
        # 第一个任务便捷字段
        "llm_first_task_is_complete": _parse_is_complete(first_task.get("is_complete")),
        "llm_first_task_description": first_task.get("task_description", ""),
        "llm_first_task_reasoning": first_task.get("reasoning", ""),
        # 任务级失败分类 (LLM 产出, 与 llm_failure_categories 工具码正交; 加列见 sql_25)
        "llm_first_task_failure_class": str(first_task.get("task_failure_class") or "UNKNOWN"),
        "llm_task_failure_classes": ",".join(
            str(t.get("task_failure_class") or "UNKNOWN") for t in tasks
        ) if tasks else "",
        "llm_first_task_human_intervention_level": first_task.get("human_intervention_level", "unknown"),
        "llm_first_task_human_intervention_reasoning": first_task.get("human_intervention_reasoning", ""),
        "llm_first_task_human_intervention_evidence_idxs": _safe_json_dumps(first_task.get("human_intervention_evidence_message_indices", [])),
        "llm_first_task_human_turn_count": _safe_int_or_null(first_task.get("human_turn_count")) or 0,
        "llm_first_task_system_user_message_count": _safe_int_or_null(first_task.get("system_user_message_count")) or 0,
    }

    # 合并 Skill / MCP 汇总 + 明细字段
    result.update(_build_tool_odps_fields("skill", skill_stats))
    result.update(_build_tool_odps_fields("mcp", mcp_stats))

    return result


def _parse_is_complete(value: Any) -> int | None:
    """解析 is_complete 值"""
    if value in (1, "1", True):
        return 1
    if value in (0, "0", False):
        return 0
    if value in ("aborted", 3, "3"):
        return 3  # aborted (程序层判定中止, 退出完成率分母)
    if value in ("unknown", None):
        return 2  # unknown
    return None


def _resolve_exec_status(prelim_status: str, exec_status: str) -> tuple[str, bool]:
    """根据 preliminary_status 和 exec_status 解析最终执行状态

    Returns:
        (exec_val, is_failure) — 状态字符串和是否为失败状态
    """
    if prelim_status == "success_clean" or exec_status == "success":
        return "success", False
    if prelim_status == "success_suspected_retry" or exec_status == "success_retry":
        return "success_retry", False
    if prelim_status == "failure" or exec_status == "failure":
        return "failure", True
    if prelim_status == "unclear":
        return "unclear", False
    return exec_status or "unclear", False


def _aggregate_tool_stats(tasks: list[dict], tool_key: str) -> dict:
    """汇总 skill 或 MCP 的评估统计，同时生成平行逗号分隔列

    返回:
        dict 含:
          - 汇总计数: total, correct, incorrect, success, failed, unclear
          - 平行明细列 (一一对应): names, is_corrects, exec_statuses, failure_categories, task_indices
    """
    total = 0
    correct = 0
    incorrect = 0
    success = 0
    failed = 0
    unclear = 0

    names = []
    is_corrects = []
    exec_statuses = []
    failure_categories = []
    task_indices = []

    for task in tasks:
        tools = task.get(tool_key, [])
        if not isinstance(tools, list):
            continue

        task_idx = _safe_int_or_null(task.get("task_index")) or 1

        for tool in tools:
            if not isinstance(tool, dict):
                continue

            name = tool.get("name", "")
            is_correct = tool.get("is_correct")
            prelim_status = tool.get("preliminary_status", "")
            exec_info = tool.get("execution", {})
            exec_status = exec_info.get("status", "") if isinstance(exec_info, dict) else ""
            fail_cat = (exec_info.get("failure_category") or "") if isinstance(exec_info, dict) else ""

            total += 1
            if is_correct == 1:
                correct += 1
            elif is_correct == 0:
                incorrect += 1

            exec_val, is_failure = _resolve_exec_status(prelim_status, exec_status)
            if is_failure:
                failed += 1
            elif exec_val == "success":
                success += 1
            else:
                unclear += 1

            names.append(name)
            is_corrects.append(str(is_correct) if is_correct is not None else "unknown")
            exec_statuses.append(exec_val)
            failure_categories.append(fail_cat if fail_cat else "-")
            task_indices.append(str(task_idx))

    return {
        "total": total,
        "correct": correct,
        "incorrect": incorrect,
        "success": success,
        "failed": failed,
        "unclear": unclear,
        "names": names,
        "is_corrects": is_corrects,
        "exec_statuses": exec_statuses,
        "failure_categories": failure_categories,
        "task_indices": task_indices,
    }


def _build_tool_odps_fields(prefix: str, stats: dict) -> dict:
    """将 _aggregate_tool_stats 的结果展开为 ODPS 字段

    Args:
        prefix: "skill" 或 "mcp"
        stats: _aggregate_tool_stats 返回值
    """
    p = f"llm_{prefix}"
    return {
        f"{p}_total_cnt": stats["total"],
        f"{p}_correct_cnt": stats["correct"],
        f"{p}_incorrect_cnt": stats["incorrect"],
        f"{p}_success_cnt": stats["success"],
        f"{p}_failed_cnt": stats["failed"],
        f"{p}_unclear_cnt": stats["unclear"],
        f"{p}_names": ",".join(stats["names"]),
        f"{p}_is_correct": ",".join(stats["is_corrects"]),
        f"{p}_exec_status": ",".join(stats["exec_statuses"]),
        f"{p}_failure_category": ",".join(stats["failure_categories"]),
        f"{p}_task_idx": ",".join(stats["task_indices"]),
    }


def _collect_failure_categories(tasks: list[dict]) -> list[str]:
    """收集所有失败分类码"""
    categories = set()

    for task in tasks:
        for tool_key in ("skills", "mcps"):
            tools = task.get(tool_key, [])
            if not isinstance(tools, list):
                continue

            for tool in tools:
                if not isinstance(tool, dict):
                    continue

                exec_info = tool.get("execution", {})
                if isinstance(exec_info, dict):
                    category = exec_info.get("failure_category")
                    if category and category != "unknown":
                        categories.add(str(category))

    return sorted(list(categories))


# ═══════════════════════════════════════════════════════════
#  成功经验提取与写入
# ═══════════════════════════════════════════════════════════

def extract_success_experiences(
    session_input: dict,
    judge_report: dict,
) -> list[dict]:
    """从 JudgeReport 中提取成功经验记录

    条件: is_correct=1 AND exec_status='success_retry' AND retry_detected=true

    Args:
        session_input: 原始 ODPS 输入行 (snake_case)
        judge_report: JudgeEngine 输出的 judge_report

    Returns:
        成功经验记录列表，每个记录符合 success_experience 表结构
    """
    report = judge_report.get("judge_report", {})
    tasks = report.get("tasks", [])
    llm_models = report.get("llm_models", {})  # 获取 LLM 模型映射

    results = []
    for task in tasks:
        task_idx = _safe_int_or_null(task.get("task_index")) or 1
        task_desc = task.get("task_description", "")

        # 处理 skills 和 mcps
        for tool_type in ("skills", "mcps"):
            tools = task.get(tool_type, [])
            if not isinstance(tools, list):
                continue

            for tool in tools:
                if not isinstance(tool, dict):
                    continue

                # 检查条件: is_correct=1
                is_correct = tool.get("is_correct")
                if is_correct != 1:
                    continue

                # 检查条件: exec_status='success_retry'
                prelim_status = tool.get("preliminary_status", "")
                exec_info = tool.get("execution")

                # 只有 prelim_status='success_suspected_retry' 才可能是 success_retry
                if prelim_status != "success_suspected_retry":
                    continue

                # 检查 execution 是否存在
                if not isinstance(exec_info, dict):
                    continue

                # 检查条件: retry_detected=true
                retry_detected = exec_info.get("retry_detected")
                if retry_detected is not True:
                    continue

                # 检查最终状态是否为 success
                final_status = exec_info.get("status", "")
                if final_status != "success":
                    continue

                # 提取成功经验 (QA 对结构)
                success_experience = exec_info.get("success_experience")
                if not success_experience:
                    continue

                # 解析 QA 对结构
                if isinstance(success_experience, dict):
                    problem = success_experience.get("problem", "").strip()
                    solution = success_experience.get("solution", "").strip()
                elif isinstance(success_experience, str):
                    # 向后兼容：旧格式字符串作为 solution
                    problem = ""
                    solution = success_experience.strip()
                else:
                    continue

                # 必须有有效的 solution 才记录
                if not solution:
                    continue

                # 拼接为 Q:xxx\nA:xxx 格式
                if problem:
                    qa_string = f"Q:{problem}\nA:{solution}"
                else:
                    # 向后兼容：无 problem 时只存 solution
                    qa_string = solution

                # 获取重试次数 (使用安全转换处理 NaN/inf)
                retry_count = _safe_int_or_null(exec_info.get("retry_count"))

                # 根据 tool_type 选择合适的 LLM 模型名称
                # 统一评估后使用 {tool}_assessment；向后兼容旧的 dive/inspector 键
                single_tool_type = tool_type[:-1]  # "skills" -> "skill", "mcps" -> "mcp"
                assessment_key = f"{single_tool_type}_assessment"
                dive_key = f"{single_tool_type}_dive"
                inspector_key = f"{single_tool_type}_inspector"
                llm_version = (llm_models.get(assessment_key)
                               or llm_models.get(dive_key)
                               or llm_models.get(inspector_key)
                               or "")

                result = {
                    "session_id": session_input.get("session_id") or session_input.get("sessionId", ""),
                    # dt 作为分区列，不写入数据字段 (适配 camelCase)
                    "user_id": session_input.get("userId") or session_input.get("user_id", ""),
                    "bot_id": session_input.get("botId") or session_input.get("bot_id", ""),
                    "start_time": session_input.get("start_time", ""),
                    "end_time": session_input.get("end_time", ""),
                    "task_index": task_idx,
                    "task_description": task_desc,
                    "tool_type": single_tool_type,
                    "tool_name": tool.get("name", ""),
                    "retry_count": retry_count,
                    "success_experience": qa_string,
                    "llm_judged_at": report.get("judged_at", ""),
                    "llm_version": llm_version,  # 评估该工具使用的 LLM 模型名称
                }
                results.append(result)

    return results


def write_success_experiences_to_odps(
    experience_rows: list[dict],
    partition: str,
    table_name: str | None = None,
) -> int:
    """写入成功经验到 ODPS 表

    Args:
        experience_rows: 成功经验记录列表
        partition: 分区 (如 '20260508')
        table_name: 表名，默认为 dws_sec_log_teamclaw_arca_tool_success_experience_di

    Returns:
        写入行数
    """
    if not experience_rows:
        return 0

    table_name = table_name or "dws_sec_log_teamclaw_arca_tool_success_experience_di"
    return write_to_odps_table(experience_rows, table_name, partition)


def extract_and_write_success_experiences(
    session_input: dict,
    judge_report: dict,
    partition: str | None = None,
    output_jsonl_path: str | Path | None = None,
    table_name: str | None = None,
) -> list[dict]:
    """提取并写入成功经验 (一站式接口)

    Args:
        session_input: 原始 ODPS 输入行
        judge_report: JudgeEngine 输出
        partition: ODPS 分区，默认从 session_input.dt 获取
        output_jsonl_path: 可选，保存到本地 JSONL 文件
        table_name: ODPS 表名，默认为 dws_sec_log_teamclaw_arca_tool_success_experience_di

    Returns:
        成功经验记录列表
    """
    experiences = extract_success_experiences(session_input, judge_report)
    if not experiences:
        return []

    if partition is None:
        partition = session_input.get("dt", "")

    # 写入 ODPS（仅当指定表名时）
    if partition and table_name:
        written = write_success_experiences_to_odps(experiences, partition, table_name=table_name)
        if written > 0:
            logger.info(f"[ODPS] Wrote {written} success experience(s) to ODPS table {table_name}")

    # 保存到本地文件
    if output_jsonl_path:
        save_success_experiences_to_json(experiences, output_jsonl_path)

    return experiences


def save_success_experiences_to_json(
    experience_rows: list[dict],
    output_path: str | Path,
) -> int:
    """将成功经验保存为本地 JSONL 文件

    Args:
        experience_rows: 成功经验记录列表
        output_path: 输出文件路径

    Returns:
        写入行数
    """
    if not experience_rows:
        return 0

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    count = 0
    with open(path, "w", encoding="utf-8") as f:
        for row in experience_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1

    logger.info(f"[ODPS] Saved {count} success experience(s) to {path}")
    return count