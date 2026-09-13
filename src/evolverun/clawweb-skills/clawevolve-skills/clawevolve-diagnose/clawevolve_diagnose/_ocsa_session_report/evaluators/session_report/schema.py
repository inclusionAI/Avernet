"""
Session 基本评估结果 Schema — 对应 dws_sec_log_teamclaw_arca_judge_result_di 表

支持版本:
  V1.0: 原始版本，统一评估（原 Phase 1+2 已合并）

新增版本只需在 version_registry.py 注册，无需修改本文件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

@dataclass
class EvaluatorResult:
    session_id: str = ""
    status: str = "success"
    evaluator_name: str = ""
    evaluator_model_name: str = ""
    evaluator_version: str = ""
    evaluator_judged_at: str = ""
    error_msg: str = ""
    input_token_count: int = 0
    output_token_count: int = 0
    raw_result: object = None


# ═══════════════════════════════════════════════════════════
#  版本号常量
# ═══════════════════════════════════════════════════════════

EVALUATOR_VERSION_V1 = "V1.0"
EVALUATOR_VERSION = EVALUATOR_VERSION_V1  # 默认版本
EVALUATOR_VERSIONS = {EVALUATOR_VERSION_V1}


def _safe_int_or_null(val: Any) -> int | None:
    """安全地转换为整数或 None，处理 NaN/inf 等非有限值"""
    if val is None:
        return None
    try:
        if isinstance(val, float):
            import math
            if not math.isfinite(val):
                return None
        n = int(val)
        return n if n >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


@dataclass
class SessionReportResult(EvaluatorResult):
    """Session 基本评估结果

    字段对齐 dws_sec_log_teamclaw_arca_judge_result_di 表结构
    """
    evaluator_name: str = "session_report"

    # === 原始会话信息 ===
    user_id: str = ""
    bot_id: str = ""
    start_time: str = ""
    end_time: str = ""

    # === LLM 评估结果 ===
    llm_version: str = ""               # JSON: {"task_split": "model", ...}
    llm_judged_at: str = ""

    # === 多任务检测 ===
    llm_is_multitask: int = 0
    llm_task_count: int = 0
    llm_tasks_json: str = ""            # JSON 序列化

    # === Skill 汇总 ===
    llm_skill_total_cnt: int = 0
    llm_skill_correct_cnt: int = 0
    llm_skill_incorrect_cnt: int = 0
    llm_skill_success_cnt: int = 0
    llm_skill_failed_cnt: int = 0
    llm_skill_unclear_cnt: int = 0

    # === Skill 明细 (逗号分隔) ===
    llm_skill_names: str = ""
    llm_skill_is_correct: str = ""
    llm_skill_exec_status: str = ""
    llm_skill_failure_category: str = ""
    llm_skill_task_idx: str = ""

    # === MCP 汇总 ===
    llm_mcp_total_cnt: int = 0
    llm_mcp_correct_cnt: int = 0
    llm_mcp_incorrect_cnt: int = 0
    llm_mcp_success_cnt: int = 0
    llm_mcp_failed_cnt: int = 0
    llm_mcp_unclear_cnt: int = 0

    # === MCP 明细 (逗号分隔) ===
    llm_mcp_names: str = ""
    llm_mcp_is_correct: str = ""
    llm_mcp_exec_status: str = ""
    llm_mcp_failure_category: str = ""
    llm_mcp_task_idx: str = ""

    # === 抽样标识 ===
    is_sampled: int = 0
    sampling_group_key: str = ""

    # === 错误标识 ===
    llm_has_skill_error: int = 0
    llm_has_mcp_error: int = 0
    llm_failure_categories: str = ""

    # === 第一个任务便捷字段 ===
    llm_first_task_is_complete: int = 2  # 0/1/2 (unknown)
    llm_first_task_description: str = ""
    llm_first_task_reasoning: str = ""
    # 任务级失败分类 (is_complete 判定时由 LLM 产出, 与 llm_failure_categories 工具码正交)
    llm_first_task_failure_class: str = "UNKNOWN"
    llm_task_failure_classes: str = ""
    llm_first_task_human_intervention_level: str = "unknown"
    llm_first_task_human_intervention_reasoning: str = ""
    llm_first_task_human_intervention_evidence_idxs: str = "[]"
    llm_first_task_human_turn_count: int = 0
    llm_first_task_system_user_message_count: int = 0

    # === 原始 judge_report ===
    judge_report: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "evaluator_name": self.evaluator_name,
            "evaluator_model_name": self.evaluator_model_name,
            "evaluator_version": self.evaluator_version,
            "evaluator_judged_at": self.evaluator_judged_at,
            "user_id": self.user_id,
            "bot_id": self.bot_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "llm_version": self.llm_version,
            "llm_judged_at": self.llm_judged_at,
            "llm_is_multitask": self.llm_is_multitask,
            "llm_task_count": self.llm_task_count,
            "llm_tasks_json": self.llm_tasks_json,
            "llm_skill_total_cnt": self.llm_skill_total_cnt,
            "llm_skill_correct_cnt": self.llm_skill_correct_cnt,
            "llm_skill_incorrect_cnt": self.llm_skill_incorrect_cnt,
            "llm_skill_success_cnt": self.llm_skill_success_cnt,
            "llm_skill_failed_cnt": self.llm_skill_failed_cnt,
            "llm_skill_unclear_cnt": self.llm_skill_unclear_cnt,
            "llm_skill_names": self.llm_skill_names,
            "llm_skill_is_correct": self.llm_skill_is_correct,
            "llm_skill_exec_status": self.llm_skill_exec_status,
            "llm_skill_failure_category": self.llm_skill_failure_category,
            "llm_skill_task_idx": self.llm_skill_task_idx,
            "llm_mcp_total_cnt": self.llm_mcp_total_cnt,
            "llm_mcp_correct_cnt": self.llm_mcp_correct_cnt,
            "llm_mcp_incorrect_cnt": self.llm_mcp_incorrect_cnt,
            "llm_mcp_success_cnt": self.llm_mcp_success_cnt,
            "llm_mcp_failed_cnt": self.llm_mcp_failed_cnt,
            "llm_mcp_unclear_cnt": self.llm_mcp_unclear_cnt,
            "llm_mcp_names": self.llm_mcp_names,
            "llm_mcp_is_correct": self.llm_mcp_is_correct,
            "llm_mcp_exec_status": self.llm_mcp_exec_status,
            "llm_mcp_failure_category": self.llm_mcp_failure_category,
            "llm_mcp_task_idx": self.llm_mcp_task_idx,
            "is_sampled": self.is_sampled,
            "sampling_group_key": self.sampling_group_key,
            "llm_has_skill_error": self.llm_has_skill_error,
            "llm_has_mcp_error": self.llm_has_mcp_error,
            "llm_failure_categories": self.llm_failure_categories,
            "llm_first_task_is_complete": self.llm_first_task_is_complete,
            "llm_first_task_description": self.llm_first_task_description,
            "llm_first_task_reasoning": self.llm_first_task_reasoning,
            "llm_first_task_failure_class": self.llm_first_task_failure_class,
            "llm_task_failure_classes": self.llm_task_failure_classes,
            "llm_first_task_human_intervention_level": self.llm_first_task_human_intervention_level,
            "llm_first_task_human_intervention_reasoning": self.llm_first_task_human_intervention_reasoning,
            "llm_first_task_human_intervention_evidence_idxs": self.llm_first_task_human_intervention_evidence_idxs,
            "llm_first_task_human_turn_count": self.llm_first_task_human_turn_count,
            "llm_first_task_system_user_message_count": self.llm_first_task_system_user_message_count,
            "judge_report": self.judge_report,
            "error_msg": self.error_msg,
            "input_token_count": self.input_token_count,
            "output_token_count": self.output_token_count,
            "raw_result": self.raw_result,
        }

    def to_odps_fields(self) -> dict:
        """转换为 ODPS 表字段（与 convert_judge_report_to_odps_fields 输出一致）"""

        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "bot_id": self.bot_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "llm_version": self.llm_version,
            "llm_judged_at": self.llm_judged_at,
            "llm_is_multitask": self.llm_is_multitask,
            "llm_task_count": self.llm_task_count,
            "llm_tasks_json": self.llm_tasks_json,
            "llm_skill_total_cnt": self.llm_skill_total_cnt,
            "llm_skill_correct_cnt": self.llm_skill_correct_cnt,
            "llm_skill_incorrect_cnt": self.llm_skill_incorrect_cnt,
            "llm_skill_success_cnt": self.llm_skill_success_cnt,
            "llm_skill_failed_cnt": self.llm_skill_failed_cnt,
            "llm_skill_unclear_cnt": self.llm_skill_unclear_cnt,
            "llm_skill_names": self.llm_skill_names,
            "llm_skill_is_correct": self.llm_skill_is_correct,
            "llm_skill_exec_status": self.llm_skill_exec_status,
            "llm_skill_failure_category": self.llm_skill_failure_category,
            "llm_skill_task_idx": self.llm_skill_task_idx,
            "llm_mcp_total_cnt": self.llm_mcp_total_cnt,
            "llm_mcp_correct_cnt": self.llm_mcp_correct_cnt,
            "llm_mcp_incorrect_cnt": self.llm_mcp_incorrect_cnt,
            "llm_mcp_success_cnt": self.llm_mcp_success_cnt,
            "llm_mcp_failed_cnt": self.llm_mcp_failed_cnt,
            "llm_mcp_unclear_cnt": self.llm_mcp_unclear_cnt,
            "llm_mcp_names": self.llm_mcp_names,
            "llm_mcp_is_correct": self.llm_mcp_is_correct,
            "llm_mcp_exec_status": self.llm_mcp_exec_status,
            "llm_mcp_failure_category": self.llm_mcp_failure_category,
            "llm_mcp_task_idx": self.llm_mcp_task_idx,
            "is_sampled": self.is_sampled,
            "sampling_group_key": self.sampling_group_key,
            "llm_has_skill_error": self.llm_has_skill_error,
            "llm_has_mcp_error": self.llm_has_mcp_error,
            "llm_failure_categories": self.llm_failure_categories,
            "llm_first_task_is_complete": self.llm_first_task_is_complete,
            "llm_first_task_description": self.llm_first_task_description,
            "llm_first_task_reasoning": self.llm_first_task_reasoning,
            "llm_first_task_failure_class": self.llm_first_task_failure_class,
            "llm_task_failure_classes": self.llm_task_failure_classes,
            "llm_first_task_human_intervention_level": self.llm_first_task_human_intervention_level,
            "llm_first_task_human_intervention_reasoning": self.llm_first_task_human_intervention_reasoning,
            "llm_first_task_human_intervention_evidence_idxs": self.llm_first_task_human_intervention_evidence_idxs,
            "llm_first_task_human_turn_count": self.llm_first_task_human_turn_count,
            "llm_first_task_system_user_message_count": self.llm_first_task_system_user_message_count,
        }

    @classmethod
    def from_judge_report(cls, session: dict, judge_report: dict) -> "SessionReportResult":
        """从 judge_report 构建 SessionReportResult

        复用 infrastructure/odps/writer.py 中的转换逻辑
        """
        from ...infrastructure.odps.writer import convert_judge_report_to_odps_fields

        # 调用现有转换函数获取 ODPS 字段
        odps_fields = convert_judge_report_to_odps_fields(session, judge_report)

        return cls(
            session_id=odps_fields.get("session_id", ""),
            status="success",
            user_id=odps_fields.get("user_id", ""),
            bot_id=odps_fields.get("bot_id", ""),
            start_time=odps_fields.get("start_time", ""),
            end_time=odps_fields.get("end_time", ""),
            llm_version=odps_fields.get("llm_version", ""),
            llm_judged_at=odps_fields.get("llm_judged_at", ""),
            llm_is_multitask=odps_fields.get("llm_is_multitask", 0),
            llm_task_count=odps_fields.get("llm_task_count", 0),
            llm_tasks_json=odps_fields.get("llm_tasks_json", ""),
            llm_skill_total_cnt=odps_fields.get("llm_skill_total_cnt", 0),
            llm_skill_correct_cnt=odps_fields.get("llm_skill_correct_cnt", 0),
            llm_skill_incorrect_cnt=odps_fields.get("llm_skill_incorrect_cnt", 0),
            llm_skill_success_cnt=odps_fields.get("llm_skill_success_cnt", 0),
            llm_skill_failed_cnt=odps_fields.get("llm_skill_failed_cnt", 0),
            llm_skill_unclear_cnt=odps_fields.get("llm_skill_unclear_cnt", 0),
            llm_skill_names=odps_fields.get("llm_skill_names", ""),
            llm_skill_is_correct=odps_fields.get("llm_skill_is_correct", ""),
            llm_skill_exec_status=odps_fields.get("llm_skill_exec_status", ""),
            llm_skill_failure_category=odps_fields.get("llm_skill_failure_category", ""),
            llm_skill_task_idx=odps_fields.get("llm_skill_task_idx", ""),
            llm_mcp_total_cnt=odps_fields.get("llm_mcp_total_cnt", 0),
            llm_mcp_correct_cnt=odps_fields.get("llm_mcp_correct_cnt", 0),
            llm_mcp_incorrect_cnt=odps_fields.get("llm_mcp_incorrect_cnt", 0),
            llm_mcp_success_cnt=odps_fields.get("llm_mcp_success_cnt", 0),
            llm_mcp_failed_cnt=odps_fields.get("llm_mcp_failed_cnt", 0),
            llm_mcp_unclear_cnt=odps_fields.get("llm_mcp_unclear_cnt", 0),
            llm_mcp_names=odps_fields.get("llm_mcp_names", ""),
            llm_mcp_is_correct=odps_fields.get("llm_mcp_is_correct", ""),
            llm_mcp_exec_status=odps_fields.get("llm_mcp_exec_status", ""),
            llm_mcp_failure_category=odps_fields.get("llm_mcp_failure_category", ""),
            llm_mcp_task_idx=odps_fields.get("llm_mcp_task_idx", ""),
            is_sampled=odps_fields.get("is_sampled", 0),
            sampling_group_key=odps_fields.get("sampling_group_key", ""),
            llm_has_skill_error=odps_fields.get("llm_has_skill_error", 0),
            llm_has_mcp_error=odps_fields.get("llm_has_mcp_error", 0),
            llm_failure_categories=odps_fields.get("llm_failure_categories", ""),
            llm_first_task_is_complete=odps_fields.get("llm_first_task_is_complete", 2),
            llm_first_task_description=odps_fields.get("llm_first_task_description", ""),
            llm_first_task_reasoning=odps_fields.get("llm_first_task_reasoning", ""),
            llm_first_task_failure_class=odps_fields.get("llm_first_task_failure_class", "UNKNOWN"),
            llm_task_failure_classes=odps_fields.get("llm_task_failure_classes", ""),
            llm_first_task_human_intervention_level=odps_fields.get("llm_first_task_human_intervention_level", "unknown"),
            llm_first_task_human_intervention_reasoning=odps_fields.get("llm_first_task_human_intervention_reasoning", ""),
            llm_first_task_human_intervention_evidence_idxs=odps_fields.get("llm_first_task_human_intervention_evidence_idxs", "[]"),
            llm_first_task_human_turn_count=odps_fields.get("llm_first_task_human_turn_count", 0),
            llm_first_task_system_user_message_count=odps_fields.get("llm_first_task_system_user_message_count", 0),
            judge_report=judge_report,
            raw_result=judge_report.get("judge_report", {}),
        )