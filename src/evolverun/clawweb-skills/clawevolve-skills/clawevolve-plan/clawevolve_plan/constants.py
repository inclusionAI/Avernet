from __future__ import annotations

import os

EVOLVE_RESULTS_BASE_DIR = "/home/admin/.openclaw/workspace/clawevolve_results"
DEFAULT_DIAGNOSE_HANDOFF_DIR = f"{EVOLVE_RESULTS_BASE_DIR}/_diagnose_handoff"
DIAGNOSE_RUN_SUBDIR = "diagnose"
CLAWWEB_BASE_URL = "http://127.0.0.1:5173"
DEFAULT_TIMEOUT_SECONDS = 600


def clawweb_base_url() -> str:
    return (os.environ.get("CLAWEVOLVE_CLAWWEB_URL") or os.environ.get("CLAWWEB_URL") or CLAWWEB_BASE_URL).rstrip("/")

DEFAULT_FORBIDDEN_CHANGES = [
    "不得修改 judge、scorer、ClawBench templates、隐藏测试或已上传的 ClawWeb domain 数据。",
    "不得直接修改生产 OpenClaw agent、生产 workspace、生产配置或线上密钥。",
    "不得写入、打印、持久化或推断 API key、cookie、token 等密钥。",
    "不得自动扩大生产 MCP/tool 权限、文件系统权限、网络权限或 sandbox 边界。",
    "不得修改全局 OpenClaw runtime 或共享依赖；除非后续人工批准的 SPEC 明确允许。",
]

DEFAULT_UPDATE_TARGETS = [
    "workspace skills",
    "Markdown/reference 文档",
    "agent prompt/bootstrap",
    "仅候选环境使用的配置诊断文档",
]

MODE_TARGETS = {
    "retrieval_not_called": [
        "agent prompt/bootstrap 中的检索路由策略",
        "workspace skill 路由说明",
        "必须检索场景识别规则",
    ],
    "retrieval_bad_query": [
        "query rewrite prompt/script",
        "单意图抽取示例",
        "检索 skill 参数示例",
    ],
    "retrieval_relevant_but_not_used": [
        "答案合成 prompt",
        "证据使用 checklist",
        "证据充分性保护规则",
    ],
    "retrieval_or_knowledge_failure": [
        "检索路由策略",
        "query rewrite 示例",
        "无证据降级规则",
    ],
    "runtime_config_missing": [
        "仅候选环境使用的配置诊断文档",
        "skill 配置说明",
        "安全降级话术",
    ],
    "tool_parameter_error": [
        "skill 脚本参数校验",
        "工具参数 schema 示例",
        "参数修复 checklist",
    ],
    "permission_or_network_blocked": [
        "候选配置诊断",
        "权限边界文档",
        "安全降级行为",
    ],
    "workspace_or_data_missing": [
        "workspace 路径校验",
        "数据存在性检查",
        "文件发现说明",
    ],
    "tool_execution_failure": [
        "工具错误处理",
        "retry/backoff 策略",
        "替代工具/降级路径",
    ],
    "workflow_planning_failure": [
        "planner 停止条件",
        "step budget 策略",
        "任务拆解示例",
    ],
    "premature_capability_boundary": [
        "能力探索策略",
        "skill 发现说明",
        "降级替代方案",
    ],
    "unnecessary_user_blocking": [
        "最小追问策略",
        "可自动推断参数规则",
        "置信度阈值 checklist",
    ],
    "context_or_process_truncated": [
        "上下文压缩 prompt",
        "长任务 checkpoint 机制",
        "step budget 管理",
    ],
    "incorrect_or_unverified_answer": [
        "答案验证 checklist",
        "证据充分性保护规则",
        "领域示例",
    ],
    "unknown_failure_mode": [
        "补充 trace instrumentation",
        "workspace skill 说明",
        "保守 guardrails",
    ],
}

MODE_BEHAVIOR = {
    "retrieval_not_called": "需要知识/外部证据的 case 必须先调用检索链路再回答。",
    "retrieval_bad_query": "把多句、嘈杂、上下文强相关输入改写为单一明确检索 query。",
    "retrieval_relevant_but_not_used": "最终回答必须引用并使用相关检索证据。",
    "retrieval_or_knowledge_failure": "检索不到可靠证据时不得编造，应说明不确定并给出最小追问或降级路径。",
    "runtime_config_missing": "候选环境中补齐配置诊断和降级路径，不因配置缺失直接失败。",
    "tool_parameter_error": "调用工具前校验参数并修正常见参数错误。",
    "permission_or_network_blocked": "识别权限/网络边界，不伪装成功，不扩大生产权限。",
    "workspace_or_data_missing": "访问文件或数据前验证存在性，缺失时给出替代路径。",
    "tool_execution_failure": "工具失败后有限重试、切换替代路径或安全降级。",
    "workflow_planning_failure": "优化 planner 收敛性，避免无效循环并遵守 step 预算。",
    "premature_capability_boundary": "拒绝前先探索可用 skill/tool/文档能力。",
    "unnecessary_user_blocking": "信息足够时继续推进，追问仅限最小必要信息。",
    "context_or_process_truncated": "长任务分阶段处理并保留关键状态。",
    "incorrect_or_unverified_answer": "输出前验证关键事实和结论，避免无依据答案。",
    "unknown_failure_mode": "先补充观测，再做小范围保守优化。",
}

# Final artifact OSS publish settings. Keep these in the same style as the
# repository-level upload_OSS.py so operators can fill credentials directly here.
OSS_ACCESS_KEY_ID = ""
OSS_ACCESS_KEY_SECRET = ""
OSS_ENDPOINT = os.environ.get("CLAWEVOLVE_OSS_ENDPOINT", "")
OSS_BUCKET_NAME = os.environ.get("CLAWEVOLVE_OSS_BUCKET", "")
OSS_EVOLVE_RESULTS_PREFIX = os.environ.get("CLAWEVOLVE_OSS_PREFIX", "clawevolve_results")
OSS_PACK_ZIP_NAME = "artifact_v0.zip"
PACK_IMAGE_VERSION = "1.0.0"
