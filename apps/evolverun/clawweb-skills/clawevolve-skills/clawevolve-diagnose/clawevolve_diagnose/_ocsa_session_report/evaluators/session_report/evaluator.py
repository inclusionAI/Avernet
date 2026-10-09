"""
Session 基本评估器 — 独立模块

产出表: dws_sec_log_teamclaw_arca_judge_result_di

支持版本:
  V1.0: 原始版本，统一评估（原 Phase 1+2 已合并）

新增版本只需在 version_registry.py 注册，无需修改本文件。

## 整体流程

```
evaluate(session) → SessionReportResult
    │
    ├─ 1. 数据适配 (adapt_odps_row)
    │      └─ 统一 snake_case/camelCase 输入格式
    │
    ├─ 2. 信号提取 (extract_judge_signals + rule_based_prefilter)
    │      └─ 提取辅助信号并生成规则跳过标志
    │
    ├─ 3. 多任务检测 (_detect_tasks)
    │      ├─ skip_task_split=true → 单任务(无LLM调用)
    │      └─ 否则 → LLM识别边界 → split_messages_at_boundaries
    │
    ├─ 4. 逐任务评估 (_judge_task × N)
    │      │
    │      ├─ 统一并行评估:
    │      │    ├─ is_complete        — 任务完成度判断（始终调用）
    │      │    ├─ skill_assessment   — Skill 统一评估(选择正确性+执行详情)（有skills时调用）
    │      │    └─ mcp_assessment     — MCP 统一评估(选择正确性+执行详情)（有mcps时调用）
    │      │
    │      └─ 结果组装
    │
    └─ 5. 报告组装 (_assemble_report → SessionReportResult)
           └─ 输出 judge_report JSON + ODPS 字段
```

## 关键优化

1. **规则前置跳过**: 根据信号跳过不必要的LLM调用(is_complete/skill/mcp)
2. **焦点上下文**: 根据工具位置提取对话片段，减少Prompt长度
3. **统一评估**: 原两阶段(初筛+深挖)合并为单次统一评估，减少LLM调用次数
4. **并行执行**: 各评估项内部并行调用，降低延迟

## Prompt 模板

- JUDGE_IS_COMPLETE_PROMPT  — 任务完成度判断
- JUDGE_SKILL_PROMPT        — Skill 统一评估(选择正确性+执行详情)
- JUDGE_MCP_PROMPT          — MCP 统一评估(选择正确性+执行详情)
- JUDGE_TASK_SPLIT_PROMPT   — 多任务边界识别
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, Type

from ....constants import DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS

from .schema import (
    SessionReportResult,
    EVALUATOR_VERSION,
    EVALUATOR_VERSIONS,
)
# 复用现有模块
from ...parsers.conversation_parser import (
    format_conversation,
    format_message,
    split_messages_at_boundaries,
)
from ...parsers.normalizers import (
    normalize_is_complete,
    normalize_tool_assessment,
)
from ...infrastructure.odps.adapter import (
    adapt_odps_row,
    extract_judge_signals,
    format_judge_signals,
    rule_based_prefilter,
    parse_comma_list,
)

# 从本模块 prompt 导入提示词常量
from .prompt import (
    JUDGE_SYSTEM_PROMPT,
    build_judge_is_complete_prompt,
    JUDGE_SKILL_PROMPT,
    JUDGE_MCP_PROMPT,
    JUDGE_TASK_SPLIT_PROMPT,
)

# LLM 调用函数统一从 infrastructure.llm 复用
from ...infrastructure.llm import (
    LLMCallResult,
    LLMCallTask,
    call_judge_llm_batch,
    _call_judge_llm_internal,
)

import logging

logger = logging.getLogger(__name__)


class SessionProgressLogger(Protocol):
    """Minimal progress logger contract consumed by the copied evaluator."""

    def phase(self, name: str):
        """Return a context manager for one evaluator phase."""
        ...


@dataclass(frozen=True)
class _LocalVersionDesc:
    report_version: str
    result_schema: type

@contextmanager
def _null_context():
    """空上下文管理器，用于 progress_logger 为 None 时"""
    yield None


# ═══════════════════════════════════════════════════════════
#  工具类型配置
# ═══════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ToolConfig:
    """封装 skill/mcp 工具类型的差异配置"""
    key: str
    list_prompt_key: str
    prompt: str
    prefilter_key: str


TOOL_CONFIGS = {
    "skill": ToolConfig(
        key="skills",
        list_prompt_key="skill_list",
        prompt=JUDGE_SKILL_PROMPT,
        prefilter_key="skip_skill_inspector",
    ),
    "mcp": ToolConfig(
        key="mcps",
        list_prompt_key="mcp_list",
        prompt=JUDGE_MCP_PROMPT,
        prefilter_key="skip_mcp_inspector",
    ),
}


# ═══════════════════════════════════════════════════════════
#  工具函数
# ═══════════════════════════════════════════════════════════

def extract_tool_lists(session: dict) -> tuple[list[str], list[str]]:
    """从 session JSON 中提取 skills 和 mcps 的唯一列表"""
    skills = parse_comma_list(session.get("allExeSkill", "") or session.get("all_exe_skill", ""))
    mcps = parse_comma_list(session.get("allExeMcp", "") or session.get("all_exe_mcp", ""))
    return skills, mcps


def format_tool_list(tools: list[str]) -> str:
    """将工具列表格式化为 prompt 可用的文本"""
    if not tools:
        return "无"
    return "\n".join(f"- {tool}" for tool in tools)


def _filter_tools_by_range(
    tool_names: list[str],
    positions: dict[str, list[int]],
    range_start: int,
    range_end: int,
) -> list[str]:
    """过滤出消息范围内实际出现的工具（无 positions 时退化为全量）"""
    if not positions:
        return tool_names
    result = []
    seen = set()
    for name in tool_names:
        if name in seen:
            continue
        idxs = positions.get(name, [])
        if any(range_start <= idx < range_end for idx in idxs):
            result.append(name)
            seen.add(name)
    return result


def _format_focus_section(focus_context: str) -> str:
    """格式化焦点上下文段落，为空时返回空字符串避免 Prompt 中出现空标题"""
    if not focus_context or not focus_context.strip():
        return ""
    return f"\n[焦点上下文 — 工具出现的对话片段]\n{focus_context}\n"


def _is_cron_session(session: dict) -> bool:
    """兼容 bool/string/int 形式判断是否为周期任务。"""
    value = session.get("isCron", session.get("is_cron", False))
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y"}


def _build_idx_map(messages: list[dict]) -> dict[int, dict]:
    """构建 message idx → message 的映射"""
    idx_map = {}
    for msg in messages:
        idx = msg.get("idx")
        if idx is not None:
            try:
                idx_map[int(idx)] = msg
            except (ValueError, TypeError):
                pass
    return idx_map


def _extract_skill_focus_context(messages: list[dict],
                                  skill_positions: dict[str, list[int]]) -> str:
    """提取每个 skill 的焦点对话上下文"""
    if not skill_positions or not messages:
        return ""

    idx_map = _build_idx_map(messages)
    if not idx_map:
        return ""

    all_skill_idxs = sorted(set(idx for idxs in skill_positions.values() for idx in idxs))
    conversation_end_idx = max(idx_map.keys())

    sections = []
    for skill_name, idxs in skill_positions.items():
        for skill_idx in idxs:
            next_skill_pos = None
            for pos in all_skill_idxs:
                if pos > skill_idx:
                    next_skill_pos = pos
                    break
            end_idx = (next_skill_pos - 1) if next_skill_pos else conversation_end_idx

            focus_msgs = []
            for idx in sorted(idx_map.keys()):
                if skill_idx <= idx <= end_idx:
                    focus_msgs.append(idx_map[idx])

            if not focus_msgs:
                continue

            formatted_lines = [format_message(msg, with_idx=True) for msg in focus_msgs]
            focus_text = "\n".join(line for line in formatted_lines if line)
            if not focus_text:
                continue

            range_desc = f"#{skill_idx} ~ #{end_idx}" if next_skill_pos else f"#{skill_idx} ~ 对话末尾"
            sections.append(
                f"{skill_name} (消息 #{skill_idx}, 评估范围: {range_desc}):\n{focus_text}"
            )

    return "\n\n".join(sections)


def _extract_mcp_focus_context(messages: list[dict],
                                mcp_positions: dict[str, list[int]]) -> str:
    """提取每个 MCP 的焦点对话上下文"""
    if not mcp_positions or not messages:
        return ""

    idx_map = _build_idx_map(messages)
    if not idx_map:
        return ""

    all_idxs = sorted(idx_map.keys())
    sections = []
    seen = set()

    for mcp_name, idxs in mcp_positions.items():
        for mcp_idx in idxs:
            focus_msgs = []
            for idx in all_idxs:
                if mcp_idx <= idx <= mcp_idx + 1:
                    focus_msgs.append(idx_map[idx])

            if not focus_msgs:
                continue

            call_label = f"#{mcp_idx}" if len(idxs) == 1 else f"#{mcp_idx} (第{idxs.index(mcp_idx)+1}次)"
            section_key = f"{mcp_name}_{mcp_idx}"
            if section_key in seen:
                continue
            seen.add(section_key)

            formatted_lines = [format_message(msg, with_idx=True) for msg in focus_msgs]
            focus_text = "\n".join(line for line in formatted_lines if line)
            sections.append(
                f"{mcp_name} (消息 {call_label}):\n{focus_text}"
            )

    return "\n\n".join(sections)


# ═══════════════════════════════════════════════════════════
#  SessionReportEvaluator
# ═══════════════════════════════════════════════════════════

class SessionReportEvaluator:
    """Session 基本评估器

    与原 JudgeEngine 功能一致，产出 judge_results 表数据。

    支持版本:
      V1.0: 原始版本，统一评估（原 Phase 1+2 已合并）

    新增版本只需在 version_registry.py 注册，无需修改本文件。
    """

    evaluator_name = "session_report"
    evaluator_version = EVALUATOR_VERSION

    def __init__(
        self,
        timeout: int = DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS,
        max_concurrent_tasks: int = 2,
        session_report_version: str = "V1.0",
    ):
        """
        Args:
            timeout: LLM 调用超时时间（秒）
            max_concurrent_tasks: 单次 batch LLM 调用中最大并行提交数
            session_report_version: 评估器版本号，已注册版本均可，默认 V1.0
        """
        if session_report_version not in EVALUATOR_VERSIONS:
            raise ValueError(f"Unsupported session_report_version: {session_report_version}")
        self.timeout = timeout
        self.max_concurrent_tasks = max_concurrent_tasks
        self.session_report_version = session_report_version
        self._version_desc = _LocalVersionDesc(report_version=session_report_version, result_schema=SessionReportResult)
        self.evaluator_version = self._version_desc.report_version

    def get_result_schema(self) -> Type[SessionReportResult]:
        """返回结果的数据类类型"""
        return self._version_desc.result_schema

    def evaluate(self, session: dict,
                 progress_logger: "SessionProgressLogger | None" = None) -> SessionReportResult:
        """评估单个 session

        支持两种输入格式:
          1. ODPS 宽表格式（snake_case 字段名，如 all_exe_skill）
          2. 旧 camelCase 格式（如 allExeSkill）
        自动检测并适配。

        Args:
            session: 输入的 session 数据
            progress_logger: 可选的进度日志追踪器，用于记录各阶段耗时
        """
        session_id = session.get("session_id") or session.get("sessionId", "unknown")
        logger.info(f"[DEBUG] [SessionReportEvaluator] 开始处理 session: {session_id}")

        # 适配 ODPS 数据格式
        session = adapt_odps_row(session)
        session_id = session.get("session_id") or session.get("sessionId", "unknown")

        # 提取辅助信号
        signals = extract_judge_signals(session)
        prefilters = rule_based_prefilter(session, signals)
        signals_text = format_judge_signals(signals)
        messages = session.get("messages", [])
        logger.info(f"[DEBUG] [SessionReportEvaluator] messages数量: {len(messages)}, prefilters: {prefilters}")

        skills, mcps = extract_tool_lists(session)

        # 多任务检测
        task_split_model = ""
        logger.info("[DEBUG] [SessionReportEvaluator] 开始任务检测")
        with progress_logger.phase("Task Split") if progress_logger else _null_context():
            task_splits, is_multitask, task_split_model = self._detect_tasks(
                messages, signals, signals_text, prefilters, session_id=session_id
            )
        num_tasks = len(task_splits)
        logger.info(f"[DEBUG] [SessionReportEvaluator] 任务检测完成, task_count={num_tasks}, is_multitask={is_multitask}")

        logger.info(f"[SessionReportEvaluator] Session {session_id}: {num_tasks} task(s) identified "
                    f"(skip_split={prefilters['skip_task_split']}, "
                    f"skip_skill={prefilters['skip_skill_inspector']}, "
                    f"skip_mcp={prefilters['skip_mcp_inspector']})")

        # 构建所有任务的 LLM 调用（一次性收集，批量执行）
        all_llm_tasks: list[LLMCallTask] = []
        task_build_info: list[dict] = []  # 保存每个 task 的上下文，用于后续解析

        if task_split_model:
            # 预留 task_split 模型记录
            pass

        for split in task_splits:
            task_idx = split["task_index"]
            task_messages = split["messages"]
            logger.info(f"[DEBUG] [SessionReportEvaluator] 构建 task {task_idx} LLM任务, messages={len(task_messages)}")

            llm_tasks, build_info = self._build_task_llm_calls(
                task_messages, split["task_index"],
                split.get("task_description", ""),
                split.get("message_range", [0, len(task_messages)]),
                session, signals_text, prefilters, signals,
            )
            all_llm_tasks.extend(llm_tasks)
            task_build_info.append(build_info)

        # 批量执行所有任务的所有 LLM 调用
        all_llm_models: dict[str, str] = {}
        if task_split_model:
            all_llm_models["task_split"] = task_split_model

        if all_llm_tasks:
            assess_start = time.time()
            logger.info(f"[SessionReportEvaluator] 批量提交 {len(all_llm_tasks)} 个 LLM 调用 "
                        f"({len(task_splits)} 个任务)")
            assess_results = call_judge_llm_batch(
                all_llm_tasks,
                max_concurrent_tasks=self.max_concurrent_tasks * len(task_splits),
            )
            assess_elapsed = time.time() - assess_start
            success_count = sum(1 for r in assess_results.values() if r.status == "success")
            logger.info(f"[SessionReportEvaluator] 批量调用完成, "
                        f"成功 {success_count}/{len(all_llm_tasks)}, 耗时 {assess_elapsed:.2f}s")

            for task_name, result in assess_results.items():
                if result.model_used:
                    all_llm_models[task_name] = result.model_used
                    # 也记录简洁名（去掉 task_N_ 前缀）用于顶层模型统计
                    if "_" in task_name:
                        short_name = task_name.split("_", 2)[-1]  # e.g. "is_complete"
                        if short_name not in all_llm_models:
                            all_llm_models[short_name] = result.model_used
        else:
            assess_results = {}

        # 逐任务解析结果
        judged_tasks = []
        for build_info in task_build_info:
            task_idx = build_info["task_index"]
            # 提取该任务对应的评估结果
            task_results = {
                name: assess_results[name]
                for name in build_info["llm_task_names"]
                if name in assess_results
            }
            task_result, task_models = self._parse_task_results(
                build_info, task_results, session,
            )
            # 合并模型映射
            for k, v in task_models.items():
                if k not in all_llm_models:
                    all_llm_models[k] = v
            judged_tasks.append(task_result)
            logger.info(f"[DEBUG] [SessionReportEvaluator] task {task_idx} 解析完成")

        # 组装 judge_report
        judge_report = self._assemble_report(session_id, is_multitask, judged_tasks, all_llm_models)

        # 构建 SessionReportResult
        result = SessionReportResult.from_judge_report(session, judge_report)

        logger.info(f"[DEBUG] [SessionReportEvaluator] session {session_id} 处理完成")
        return result

    # ── 任务检测 ───────────────────────────────────────────

    def _detect_tasks(self, messages, signals, signals_text, prefilters, *, session_id: str = "") -> tuple[list[dict], int, str]:
        """多任务检测"""
        if prefilters["skip_task_split"]:
            split = [{
                "task_index": 0,
                "task_description": "",
                "user_message_range": [1, max(signals.get("user_msg_cnt", 1), 1)],
                "message_range": [0, len(messages)],
                "messages": messages,
            }]
            return split, 0, ""

        task_boundaries, model_used = self._identify_task_boundaries(
            format_conversation(messages),
            session_id=session_id,
        )
        task_splits = split_messages_at_boundaries(messages, task_boundaries)
        is_multitask = 1 if len(task_splits) > 1 else 0
        return task_splits, is_multitask, model_used

    def _identify_task_boundaries(
        self,
        conversation_text: str,
        *,
        session_id: str = "",
    ) -> tuple[list[dict], str]:
        """调用 LLM 识别任务边界。"""
        user_prompt = JUDGE_TASK_SPLIT_PROMPT.format(conversation=conversation_text)
        try:
            result, model_used = _call_judge_llm_internal(
                user_prompt,
                system_prompt=JUDGE_SYSTEM_PROMPT,
                timeout=self.timeout,
                call_name=f"task_split:{session_id}",
            )
        except Exception as e:
            logger.warning(f"[SessionReportEvaluator] Task boundary identification failed: {e}, fallback to single task")
            return [], ""

        tasks = result.get("tasks", [])
        if not isinstance(tasks, list) or not tasks:
            return [], model_used

        valid_tasks = [t for t in tasks if isinstance(t, dict)]
        valid_tasks.sort(key=lambda t: t.get("start_at_user_message", 1))
        for i, t in enumerate(valid_tasks):
            t["task_index"] = i + 1

        return valid_tasks, model_used

    # ── 单任务评估（兼容入口）───────────────────────────────

    def _judge_task(self, task_messages: list[dict], task_index: int,
                    task_description: str, message_range: list[int],
                    session: dict,
                    signals_text: str, prefilters: dict, signals: dict,
                    progress_logger: "SessionProgressLogger | None" = None) -> tuple[dict, dict[str, str]]:
        """对一个 task 做完整评估（兼容入口，内部走构建+解析分离路径）"""
        llm_tasks, build_info = self._build_task_llm_calls(
            task_messages, task_index, task_description, message_range,
            session, signals_text, prefilters, signals,
        )

        if llm_tasks:
            assess_results = call_judge_llm_batch(
                llm_tasks, max_concurrent_tasks=self.max_concurrent_tasks,
            )
        else:
            assess_results = {}

        return self._parse_task_results(build_info, assess_results, session)

    def _build_task_llm_calls(
        self, task_messages: list[dict], task_index: int,
        task_description: str, message_range: list[int],
        session: dict,
        signals_text: str, prefilters: dict, signals: dict,
    ) -> tuple[list[LLMCallTask], dict]:
        """构建单个 task 的所有 LLM 调用任务

        多任务并行优化：上层 evaluate() 收集所有 task 的 LLM 调用后一次性
        提交 call_judge_llm_batch，避免逐 task 串行等待。

        Returns:
            (llm_tasks, build_info):
                llm_tasks: 该任务的 LLMCallTask 列表，name 格式为 "task_{idx}_{type}"
                build_info: 解析结果所需的上下文信息
        """
        conversation_text = format_conversation(task_messages, with_idx=True)
        all_skills, all_mcps = extract_tool_lists(session)

        # 按 task 消息范围过滤：只保留在该 task 中实际出现的 skill/mcp
        skill_positions = signals.get("skill_positions", {})
        mcp_positions = signals.get("mcp_positions", {})
        task_start, task_end = message_range
        skills = _filter_tools_by_range(all_skills, skill_positions, task_start, task_end)
        mcps = _filter_tools_by_range(all_mcps, mcp_positions, task_start, task_end)

        # 提取焦点上下文
        skill_focus = _extract_skill_focus_context(task_messages, skill_positions)
        mcp_focus = _extract_mcp_focus_context(task_messages, mcp_positions)

        prefix = f"task_{task_index}_"
        llm_tasks: list[LLMCallTask] = []
        task_names: list[str] = []

        # 中止类会话: 程序层已判 aborted, 跳过 is_complete 的 LLM 调用 (省算力, 退出完成率分母)
        is_aborted = prefilters.get("is_aborted")
        abort_reason = prefilters.get("abort_reason", "")
        is_cron = _is_cron_session(session)  # build_info/人工干预解析始终需要

        # 1. is_complete（非中止才调用；cron 任务不拼接人工干预评判段）
        if is_aborted is None:
            name = f"{prefix}is_complete"
            llm_tasks.append(LLMCallTask(
                name=name,
                user_prompt=build_judge_is_complete_prompt(
                    conversation=conversation_text,
                    signals=signals_text,
                    is_cron=is_cron,
                ),
                system_prompt=JUDGE_SYSTEM_PROMPT,
                timeout=self.timeout,
                session_id=str(session.get("session_id") or session.get("sessionId") or ""),
                phase=name.rsplit("_", 1)[-1],
            ))
            task_names.append(name)

        # 2. skill_assessment（有 skills 时调用）
        if skills:
            skill_config = TOOL_CONFIGS["skill"]
            name = f"{prefix}skill_assessment"
            llm_tasks.append(LLMCallTask(
                name=name,
                user_prompt=skill_config.prompt.format(
                    **{skill_config.list_prompt_key: format_tool_list(skills)},
                    conversation=conversation_text,
                    signals=signals_text,
                    focus_context=_format_focus_section(skill_focus),
                ),
                system_prompt=JUDGE_SYSTEM_PROMPT,
                timeout=self.timeout,
                session_id=str(session.get("session_id") or session.get("sessionId") or ""),
                phase=name.rsplit("_", 1)[-1],
            ))
            task_names.append(name)

        # 3. mcp_assessment（有 mcps 时调用）
        if mcps:
            mcp_config = TOOL_CONFIGS["mcp"]
            name = f"{prefix}mcp_assessment"
            llm_tasks.append(LLMCallTask(
                name=name,
                user_prompt=mcp_config.prompt.format(
                    **{mcp_config.list_prompt_key: format_tool_list(mcps)},
                    conversation=conversation_text,
                    signals=signals_text,
                    focus_context=_format_focus_section(mcp_focus),
                ),
                system_prompt=JUDGE_SYSTEM_PROMPT,
                timeout=self.timeout,
                session_id=str(session.get("session_id") or session.get("sessionId") or ""),
                phase=name.rsplit("_", 1)[-1],
            ))
            task_names.append(name)

        build_info = {
            "task_index": task_index,
            "task_description": task_description,
            "message_range": message_range,
            "skills": skills,
            "mcps": mcps,
            "llm_task_names": task_names,
            "prefix": prefix,
            "is_cron": is_cron,
            "is_aborted": is_aborted,
            "abort_reason": abort_reason,
        }
        return llm_tasks, build_info

    def _parse_task_results(
        self, build_info: dict, assess_results: dict[str, LLMCallResult],
        session: dict,
    ) -> tuple[dict, dict[str, str]]:
        """解析单个 task 的 LLM 调用结果

        Args:
            build_info: _build_task_llm_calls 返回的上下文
            assess_results: 批量调用结果（key 为 "task_{idx}_{type}" 格式）
            session: session 数据（用于 extract_tool_lists 兜底）

        Returns:
            (task_result, llm_models): 同 _judge_task 原始返回值
        """
        task_index = build_info["task_index"]
        task_description = build_info["task_description"]
        message_range = build_info["message_range"]
        skills = build_info["skills"]
        mcps = build_info["mcps"]
        prefix = build_info["prefix"]

        llm_models: dict[str, str] = {}

        # 提取模型映射
        for task_name, result in assess_results.items():
            if result.model_used:
                llm_models[task_name] = result.model_used

        # 解析 is_complete
        is_complete_key = f"{prefix}is_complete"
        is_complete_result = {"is_complete": "unknown", "reasoning": "Assessment failed"}

        # 中止类会话: 程序层直接置 aborted (不走 LLM), 统落 EXEC_INTERRUPTED 分类
        # (不再依赖 is_complete=3 特殊值, 统一进失败分类体系; _parse_is_complete 仍容错历史 3)
        aborted_tag = build_info.get("is_aborted")
        if aborted_tag:
            is_complete_result = {
                "is_complete": "aborted",
                "reasoning": build_info.get("abort_reason") or aborted_tag,
                "task_description": build_info.get("task_description", ""),
                "task_failure_class": "EXEC_INTERRUPTED",
            }
        elif is_complete_key in assess_results:
            result = assess_results[is_complete_key]
            if result.status == "success" and result.result:
                is_complete_result = normalize_is_complete(result.result)
            else:
                error_text = f"{type(result.error).__name__}: {result.error}" if result.error else "unknown error"
                logger.warning(f"[SessionReportEvaluator] {is_complete_key} failed: {result.error}")
                # Preserve transport/config failures in the structured task so
                # diagnose can count the session as judge-failed instead of
                # misleadingly reporting an empty qualified candidate pool.
                is_complete_result = {
                    "is_complete": "unknown",
                    "reasoning": f"Assessment failed: {error_text}",
                    "task_failure_class": "UNKNOWN",
                    "judge_error_class": type(result.error).__name__ if result.error else "UNKNOWN",
                    "judge_error": error_text[:1000],
                }

        # 解析 skill_assessment
        final_skills: list = []
        skill_key = f"{prefix}skill_assessment"
        if skill_key in assess_results:
            result = assess_results[skill_key]
            if result.status == "success" and result.result:
                final_skills = normalize_tool_assessment(result.result, "skills", skills)
            else:
                if skills:
                    logger.warning(f"[SessionReportEvaluator] {skill_key} failed: {result.error}")
                    final_skills = [{"name": s, "is_correct": "unknown", "preliminary_status": "unclear", "execution": None} for s in skills]

        # 解析 mcp_assessment
        final_mcps: list = []
        mcp_key = f"{prefix}mcp_assessment"
        if mcp_key in assess_results:
            result = assess_results[mcp_key]
            if result.status == "success" and result.result:
                final_mcps = normalize_tool_assessment(result.result, "mcps", mcps)
            else:
                if mcps:
                    logger.warning(f"[SessionReportEvaluator] {mcp_key} failed: {result.error}")
                    final_mcps = [{"name": m, "is_correct": "unknown", "preliminary_status": "unclear", "execution": None} for m in mcps]

        if not skills:
            final_skills = []
        if not mcps:
            final_mcps = []

        final_task_description = task_description
        if not final_task_description:
            final_task_description = is_complete_result.get("task_description", "")

        is_cron = bool(build_info.get("is_cron", False))
        if is_cron:
            human_intervention_level = "cron"
            human_intervention_reasoning = "周期任务，无需人工干预评判"
            human_intervention_evidence_idxs = []
            human_turn_count = 0
            system_user_message_count = 0
        else:
            human_intervention_level = is_complete_result.get("human_intervention_level", "unknown")
            human_intervention_reasoning = is_complete_result.get("human_intervention_reasoning", "") or "无真实人工参与"
            human_intervention_reasoning = str(human_intervention_reasoning)[:50]
            human_intervention_evidence_idxs = is_complete_result.get("human_intervention_evidence_message_indices", [])
            human_turn_count = is_complete_result.get("human_turn_count", 0)
            system_user_message_count = is_complete_result.get("system_user_message_count", 0)

        return {
            "task_index": task_index,
            "task_description": final_task_description,
            "message_range": message_range,
            "is_complete": is_complete_result.get("is_complete", "unknown"),
            "reasoning": is_complete_result.get("reasoning", ""),
            "task_failure_class": is_complete_result.get("task_failure_class", "UNKNOWN"),
            "human_intervention_level": human_intervention_level,
            "human_intervention_reasoning": human_intervention_reasoning,
            "human_intervention_evidence_message_indices": human_intervention_evidence_idxs,
            "human_turn_count": human_turn_count,
            "system_user_message_count": system_user_message_count,
            "skills": final_skills,
            "mcps": final_mcps,
        }, llm_models

    # ── 报告组装 ──────────────────────────────────────────

    def _assemble_report(self, session_id: str, is_multitask: int,
                         judged_tasks: list[dict],
                         llm_models: dict[str, str] | None = None) -> dict:
        """组装最终的 JudgeReport"""
        return {
            "judge_report": {
                "version": self._version_desc.report_version,
                "judged_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "session_id": session_id,
                "is_multitask": is_multitask,
                "task_count": len(judged_tasks),
                "tasks": judged_tasks,
                "llm_models": llm_models or {},
            }
        }


# ═══════════════════════════════════════════════════════════
#  便捷入口
# ═══════════════════════════════════════════════════════════

def evaluate_session_report(session: dict, timeout: int = DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS,
                             session_report_version: str = "V1.0") -> SessionReportResult:
    """便捷函数：评估单个 session

    Args:
        session: session 数据字典
        timeout: LLM 调用超时时间（秒）
        session_report_version: 评估器版本号，已注册版本均可，默认 V1.0

    Returns:
        SessionReportResult
    """
    evaluator = SessionReportEvaluator(
        timeout=timeout,
        session_report_version=session_report_version,
    )
    return evaluator.evaluate(session)
