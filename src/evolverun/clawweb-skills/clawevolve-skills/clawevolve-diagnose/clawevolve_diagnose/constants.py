from __future__ import annotations

EVOLVE_RESULTS_BASE_DIR = "/home/admin/.openclaw/workspace/clawevolve_results"
DIAGNOSE_RUN_SUBDIR = "diagnose"
DEFAULT_MODEL = ""
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_CASE_LIMIT = 5
DEFAULT_DIAGNOSIS_LIMIT = 20
DEFAULT_MAX_SESSIONS = 10
DEFAULT_TIMEOUT_SECONDS = 600
DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_SECONDS

# Local session discovery should ignore sessions whose first user message is an
# internal control prompt rather than a real user task.  Diagnose's own subagent
# prompt prefix is derived dynamically from the prompt builder; add only extra
# literal prefixes here for other future helper sessions.
SESSION_USER_PROMPT_EXCLUDED_PREFIXES = (
    "[Subagent Context]",
    "/clawevolve",
)
GOOD_CASE_MIN_RATIO = 0.25
GOOD_CASE_MAX_RATIO = 0.40

FAILURE_MODE_BY_ROOT = {
    "CONFIG_MISSING": "runtime_config_missing",
    "TOOL_FAILURE": "tool_execution_failure",
    "PARAMETER_ERROR": "tool_parameter_error",
    "PERMISSION_NETWORK": "permission_or_network_blocked",
    "DATA_ISSUE": "workspace_or_data_missing",
    "CAPABILITY_BOUNDARY": "premature_capability_boundary",
    "WORKFLOW_FAILURE": "workflow_planning_failure",
    "AWAITING_USER": "unnecessary_user_blocking",
    "EXEC_INTERRUPTED": "execution_interrupted",
    "ASYNC_PENDING": "async_task_pending",
    "TRUNCATED": "context_or_process_truncated",
    "NO_REPLY_IDLE": "no_reply_idle",
    "NO_TASK": "no_task",
    "OUTPUT_WRONG": "incorrect_or_unverified_answer",
    "RETRIEVAL_NOT_CALLED": "retrieval_not_called",
    "RETRIEVAL_BAD_QUERY": "retrieval_bad_query",
    "RETRIEVAL_RELEVANT_BUT_NOT_USED": "retrieval_relevant_but_not_used",
    "RETRIEVAL_OR_KNOWLEDGE_FAILURE": "retrieval_or_knowledge_failure",
    "COMPLETED": "good_regression",
    "UNKNOWN": "unknown_failure_mode",
}

MODE_TARGET_HINTS = {
    "retrieval_not_called": [
        "agent prompt/bootstrap retrieval policy",
        "workspace skill routing instructions",
        "search-required detector",
    ],
    "retrieval_bad_query": [
        "query rewrite prompt/script",
        "single-intent extraction examples",
        "retrieval skill argument examples",
    ],
    "retrieval_relevant_but_not_used": [
        "answer synthesis prompt",
        "evidence usage checklist",
        "evidence sufficiency guard",
    ],
    "retrieval_or_knowledge_failure": [
        "retrieval routing policy",
        "query rewrite examples",
        "no-evidence fallback rules",
    ],
    "runtime_config_missing": [
        "candidate-only config diagnosis docs",
        "skill setup instructions",
        "safe fallback wording",
    ],
    "tool_parameter_error": [
        "skill script argument validation",
        "tool argument schema examples",
        "parameter repair checklist",
    ],
    "permission_or_network_blocked": [
        "candidate config diagnosis",
        "permission boundary documentation",
        "safe fallback behavior",
    ],
    "workspace_or_data_missing": [
        "workspace path validation",
        "data existence checks",
        "file discovery instructions",
    ],
    "tool_execution_failure": [
        "tool error handling",
        "retry/backoff policy",
        "alternate tool/fallback path",
    ],
    "workflow_planning_failure": [
        "planner stop conditions",
        "step budget policy",
        "task decomposition examples",
    ],
    "premature_capability_boundary": [
        "capability exploration policy",
        "skill discovery instructions",
        "fallback alternatives",
    ],
    "unnecessary_user_blocking": [
        "minimal clarification policy",
        "auto-inferable parameter rules",
        "confidence threshold checklist",
    ],
    "execution_interrupted": [
        "stable run contract",
        "restart/idempotency guard",
        "long-task checkpointing",
    ],
    "async_task_pending": [
        "async completion policy",
        "polling/checkpoint instructions",
        "pending-result user messaging",
    ],
    "context_or_process_truncated": [
        "context condensation prompt",
        "long-task checkpointing",
        "step budget management",
    ],
    "no_reply_idle": [
        "assistant response guard",
        "idle detection and recovery",
        "final-answer requirement",
    ],
    "no_task": [
        "task extraction policy",
        "non-task conversation handling",
        "dataset filtering rules",
    ],
    "incorrect_or_unverified_answer": [
        "answer verification checklist",
        "evidence sufficiency guard",
        "domain-specific examples",
    ],
    "good_regression": ["regression protection policy", "golden behavior examples"],
    "unknown_failure_mode": [
        "additional trace instrumentation",
        "workspace skill instructions",
        "conservative guardrails",
    ],
}

MODE_OPTIMIZATION_GOALS = {
    "retrieval_not_called": "需要知识或外部事实的 case 必须先调用检索链路，再基于证据回答。",
    "retrieval_bad_query": "把多句、嘈杂、上下文强相关输入改写为单一明确检索 query。",
    "retrieval_relevant_but_not_used": "最终回答必须引用并使用相关检索证据，不能检索后忽略证据。",
    "retrieval_or_knowledge_failure": "检索不到可靠证据时不得编造，应说明不确定并给出最小追问或降级路径。",
    "runtime_config_missing": "候选环境中补齐配置诊断和降级路径，不因配置缺失直接失败。",
    "tool_parameter_error": "调用工具前校验参数并修正常见参数错误。",
    "permission_or_network_blocked": "识别权限/网络边界，不伪装成功，不扩大生产权限。",
    "workspace_or_data_missing": "访问文件或数据前验证存在性，缺失时给出替代路径。",
    "tool_execution_failure": "工具失败后有限重试、切换替代路径或安全降级。",
    "workflow_planning_failure": "优化 planner 收敛性，避免无效循环并遵守 step 预算。",
    "premature_capability_boundary": "拒绝前先探索可用 skill/tool/文档能力。",
    "unnecessary_user_blocking": "信息足够时继续推进，追问仅限最小必要信息。",
    "execution_interrupted": "确保 skill 运行链路单次启动、不中途自发重跑；长任务要可恢复并保留关键状态。",
    "async_task_pending": "异步任务未完成时应明确等待/轮询/交付方式，不把 pending 状态误报为完成。",
    "context_or_process_truncated": "长任务分阶段处理并保留关键状态。",
    "no_reply_idle": "识别无回复/空转会话，确保任务结束前产出明确结果或可执行下一步。",
    "no_task": "准确识别无真实任务的会话，避免把寒暄/噪声误抽为优化评测。",
    "incorrect_or_unverified_answer": "输出前验证关键事实和结论，避免无依据答案。",
    "good_regression": "作为回归保护，优化后不得破坏历史可完成任务。",
    "unknown_failure_mode": "先补充观测，再做小范围保守优化。",
}
