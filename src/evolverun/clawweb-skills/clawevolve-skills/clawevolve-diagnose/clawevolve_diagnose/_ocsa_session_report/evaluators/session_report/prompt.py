"""
Session 基本评估 Prompt 模板

用于产出 judge_results 表数据。
对应表: dws_sec_log_teamclaw_arca_judge_result_di

新增版本的 Prompt 可在此文件新增，并在 version_registry.py 中注册。
"""

from __future__ import annotations

# 从 common 导入通用 System Prompt
from ...prompts.common import JUDGE_SYSTEM_PROMPT as JUDGE_SYSTEM_PROMPT


# ═══════════════════════════════════════════════════════════
#  is_complete 判断 + 非周期任务人工干预程度判断
# ═══════════════════════════════════════════════════════════

JUDGE_IS_COMPLETE_BASE_PROMPT = """请根据对话内容判断用户任务是否完成，并提取任务描述。

# 核心原则
完成判断以「用户目标是否达成」为准，而非用户是否继续回复。

# 判定流程（按顺序执行，命中即停止）

## Step 1：识别任务类型
判断是否为日报/周报/月报生成任务。

## Step 2：日报/周报/月报专用规则
仅当 Step 1 判定为日报/周报/月报生成任务时走此分支，否则跳至 Step 3。

* 查询成功，因周末/节假日/查询时段无记录导致无数据 → 已完成
  即使Cron失败或交付步骤未完成，根因是"客观无数据"而非技术故障仍判已完成
* OKR等附属数据源获取失败，但主数据源查询成功返回"无数据" → 仍判已完成（附属数据源非核心目标）
* 助手已告知"无数据"并附带可选建议（如切换日期），视为已交付结果 → 已完成
* 助手因查询无数据等待用户确认切换日期，且用户未回复 → 未完成（对话未闭环）

⚠️ 本分支仅适用于日报/周报/月报生成任务。非日报类查询返回空或0条不适用上述规则。

## Step 3：非日报通用规则

### 3a. 核心请求是否无法达成？
命中以下任一条件 → 未完成：
* 用户核心请求被助手拒绝（如功能不支持、超出职责范围）
* Cron/定时任务中助手拒绝执行
* 工具/MCP不支持或权限不足导致核心请求无法执行（提供替代方案≠核心目标达成）
* 网络异常导致核心数据获取失败（告知异常+提供替代方案≠核心目标达成）
* 权限不足、凭证缺失、登录失效

### 3b. 任务是否在执行中？
命中以下任一条件 → 未完成：
* 助手仍在分析、中间推理或执行过程中
* 助手等待用户确认、补充信息、上传文件或授权（含用户仅问候/激活群组时助手在询问需求）
* 异步任务已提交但结果未返回
* 用户提出追问但尚未得到回答
* 对话被截断或异常终止

### 3c. 是否存在其他未完成因素？
命中以下任一条件 → 未完成：
* 数据解析失败，重试后仍失败
* 任务待确认或待审批（含技术方案/决策），或讨论阶段尚未转化为可执行方案
* 交付步骤失败（如钉钉通知、语雀发布、消息发送）
* 查询返回空或0条记录，且用户期望具体数据

### 3d. 任务是否已完成？
* 用户目标已达成 → 已完成
* 无明确任务且对话自然结束 → 已完成
  ⚠️ 以下场景不算"自然结束"（已在3a/3b中判定为未完成）：
  · 助手正在等待用户回复
  · 用户核心请求被拒绝
  · Cron/定时任务助手拒绝执行

## Step 4：无法判断
* 缺少关键上下文 → unknown

# task_failure_class 失败分类（is_complete=0 时必填）
判完完成度后，若 is_complete=0，必须从以下 14 类中选最贴切的一类填入 task_failure_class；
is_complete=1 填 "COMPLETED"，is_complete="unknown" 填 "UNKNOWN"。
分类只针对“未完成的原因”，与是否调用 skill/mcp 工具无关（工具层失败码另算，不影响本字段）。
按下列顺序判定，命中即停，取最具体的一类（能力类优先于兜底）。

## A. 能力类（反映系统能力，agent 本应做到却没做到）
* CAPABILITY_BOUNDARY：任务场景超出 bot 当前能力范围，bot 主动判定不支持、无对应能力工具（“不支持该工单场景”“无 XX 邮箱读取工具”）。属能力边界，非临时错误。
* TOOL_FAILURE：skill/mcp/工具执行失败且 agent 未成功绕过（调用报错、退出码非零、返回空、重试仍失败），最终因工具失败未交付。
* WORKFLOW_FAILURE：工作流/编排流程节点失败（写入失败、分派失败、节点 crash），失败在工作流执行层而非单工具。
* CONFIG_MISSING：必要 skill/workflow/工具未安装或配置缺失导致无法执行（“XX 未安装”“工具未配置”——能力本应具备但部署缺失）。
* PERMISSION_NETWORK：权限拒绝、ACL 拦截、网络不可达（PERMISSION_DENIED、ACL 403、egress_blocked、连接拒绝）。
* DATA_ISSUE：所需数据缺失/过期/不存在（“目标 session 日志不存在”“数据已过期”“Unknown sessionId 无果”）。
* PARAMETER_ERROR：工具入参错误/缺失（INVALID_PARAMETER、MISSING_REQUIRED_PARAM、参数格式不符）。
* OUTPUT_WRONG：agent 交付了内容但内容错误/不符要求（输出乱码、答非所问、格式不符、回复无关内容）——流程跑完但交付物不对。

## B. 非能力类（不反映系统能力，agent 不该背锅）
* AWAITING_USER：任务已执行到需用户介入处（评审报告已出待确认、问了用户澄清问题、等待用户选方案），用户未响应导致未闭环。agent 已尽力，卡在用户侧。
* EXEC_INTERRUPTED：会话被系统/运行时强行中止（SIGTERM、aborted、异常终止/error 终止），非任务自然结束。（含 stop_reason 中 aborted>0 的场景）
* ASYNC_PENDING：异步任务已提交/仍在后台执行、结果未返回，对话即结束（命令仍运行、等 callback、pipeline in_progress）——任务可能完成但没等回结果。
* TRUNCATED：对话因上下文超限/长度限制被截断（stop_reason 中 length>0、读文件/执行过程中被截断），被超长截断而非系统中止。
* NO_REPLY_IDLE：agent 仅回复 NO_REPLY 或仅读取技能文件即停止、未实际启动任务执行——被触发但空转没干活。
* NO_TASK：无实际任务可执行（用户仅问候/激活、未提具体任务，cron 被触发但无目标）。

## 兜底
* UNKNOWN：依据现有信息确实无法归入上述任一类。尽量少用（目标 <5%）。

# task_description
提取用户核心目标，≤20字，保留关键动作和对象；无法确定填 "unknown"

{signals}

[对话内容]
{conversation}
"""

JUDGE_HUMAN_INTERVENTION_SECTION = """

# 非周期任务人工干预程度判断
仅评估真实人类用户在任务完成过程中对任务成败路径的实质影响；role=user 中的系统信息、环境信息、metadata、自动注入上下文不算人工参与。

## 核心判定问题
如果去掉任务过程中的人工输入，Agent 是否仍能独立完成任务？任务完成的关键认知劳动是谁提供的？
关键认知劳动包括：定义问题、拆解任务、选择方案、发现错误、给出修复方向、判断最终可用性。

## 标签定义
- none：无人干干预。Agent 独立承担主要理解、规划、执行和交付；用户除初始任务、正常验收外没有实质介入。
- light：人工轻度参与。用户提供少量澄清、确认、补充信息、偏好调整或局部纠错，但 Agent 仍主导任务完成。
- deep：人工重度参与预。用户显著参与任务拆解、方案设计、关键纠错、执行指挥、多轮重构，或在 Agent 主动结束/遗漏交付后通过追问推动其补做关键交付；任务完成高度依赖用户推动。
- unknown：缺少上下文，无法判断。

## 边界规则
- 用户基于 Agent 问询回复“继续”“好的”“可以”，且这是 Agent 继续执行的前置条件 → light。
- 用户 query 明显正常，Agent 已正确完成，用户仅回复“收到”或类似验收确认 → none。
- 用户只是表达“收到/谢谢/已解决”等验收，不改变任务方向 → none。
- 用户补充必要参数、日期、文件名、业务背景，或回答 Agent 的必要澄清 → light。
- 用户指出局部错误，Agent 可自行修复 → light。
- 用户多次指出 Agent 理解错、改口径、改方向、要求重做，或给出核心框架/方案让 Agent 整理 → deep。
- 用户初始需求已完整明确，但 Agent 未完成全部需求就主动结束；用户追问结果/回传状态/是否已发送，促使 Agent 再次尝试 BCS/外部渠道发送并最终完成关键交付 → deep。
- 用户不断改变目标且不是 Agent 能力问题 → none，并在 human_intervention_reasoning 中标注“需求演化导致”。
- 只因 role=user 出现系统信息、untrusted metadata、environment_context、工具/运行时提示，不得判为 light/deep。

## reasoning 要求
human_intervention_reasoning 不超过50字，说明人工参与证据；若无则说明“无真实人工参与”。
"""

JUDGE_IS_COMPLETE_OUTPUT_BASE = """

[输出格式]
只输出合法JSON：
{{
  "is_complete": 1 或 0 或 "unknown",
  "reasoning": "判断依据（≤50字）",
  "task_description": "任务描述（≤20字）",
  "task_failure_class": "失败分类码（见下方分类定义；is_complete=1 填 COMPLETED，is_complete="unknown" 填 UNKNOWN，is_complete=0 必填14类之一）"
}}"""

JUDGE_IS_COMPLETE_OUTPUT_WITH_HITL = """

[输出格式]
只输出合法JSON：
{{
  "is_complete": 1 或 0 或 "unknown",
  "reasoning": "完成度判断依据（≤50字）",
  "task_description": "任务描述（≤20字）",
  "task_failure_class": "失败分类码（见下方分类定义；is_complete=1 填 COMPLETED，is_complete="unknown" 填 UNKNOWN，is_complete=0 必填14类之一）",
  "human_intervention_level": "none/light/deep/unknown",
  "human_intervention_reasoning": "不超过50字，说明人工参与证据；若无则说明无真实人工参与",
  "human_intervention_evidence_message_indices": [人工参与证据消息编号，如无则空数组],
  "human_turn_count": 真实人类用户在任务过程中的实质参与轮次数，整数,
  "system_user_message_count": role=user 但判定为系统/元信息的消息数，整数
}}"""

# 向后兼容：默认常量保留为“非周期任务”完整 prompt。
JUDGE_IS_COMPLETE_PROMPT = (
    JUDGE_IS_COMPLETE_BASE_PROMPT
    + JUDGE_HUMAN_INTERVENTION_SECTION
    + JUDGE_IS_COMPLETE_OUTPUT_WITH_HITL
)


def build_judge_is_complete_prompt(*, conversation: str, signals: str, is_cron: bool = False) -> str:
    """根据 is_cron 动态拼接完成度/人工干预评估 prompt。

    - is_cron=True: 只拼接完成度判断，不拼接人工干预评判段。
    - is_cron=False: 在完成度判断后拼接人工干预评判段。
    """
    template = (
        JUDGE_IS_COMPLETE_BASE_PROMPT + JUDGE_IS_COMPLETE_OUTPUT_BASE
        if is_cron
        else JUDGE_IS_COMPLETE_PROMPT
    )
    return template.format(conversation=conversation, signals=signals)


# ═══════════════════════════════════════════════════════════
#  Skill Assessment (统一评估，合并原 Phase 1 + Phase 2)
# ═══════════════════════════════════════════════════════════

JUDGE_SKILL_PROMPT = """请评估以下对话中 skill 的选择正确性和执行状态。

评估 skill：通过 exec 参数调用的自定义业务工具。框架内置工具（read/write/search/edit 等）不属于 skill，无需评估。

[字段定义]
每个 skill 输出：
- name: 工具名称（必须来自下方已知列表）
- is_correct: 用户意图与工具用途是否匹配（1=正确，0=错误，"unknown"=无法确认用户意图）
- execution: 执行详情对象
  - status: "success" 或 "failure"
  - retry_detected: true | false | null（null=无法区分重试与正常多步调用）
  - retry_count: 重试次数（含首次失败尝试） | null（null规则：retry_detected=null 时必须为 null；retry_detected=false 时必须为 0）
  - success_experience: 若能总结可复用经验输出 {{problem, solution}}，否则 null
    - problem: 具体问题，必须包含工具名称和报错信息，≤80字，禁止"该skill"等需要上下文才能理解的代词
    - solution: 具体解决方案，≤80字
  - failure_category: UPPER_SNAKE_CASE 根因分类码 | null（成功时为 null，不确定时 "UNKNOWN"）

[根因分类示例]
- "ENOENT: no such file or directory"     → NO_SUCH_FILE
- "Command timed out after 600s"            → TIMEOUT
- "缺少必要参数 query"                     → MISSING_REQUIRED_PARAM

[成功经验示例 — 正确]
{{"problem": "调用 data-query-shortcut 时报错 'ENOENT: no such file or directory'",
  "solution": "先用 Bash mkdir -p 创建目标目录，再重新调用"}}
[成功经验示例 — 错误]
{{"problem": "该skill报错",               // 错误：缺工具名和具体报错
  "solution": "检查文件是否存在"}}         // 错误：方案不具体

[位置定位指引]
- 每条消息带 [#N] 编号，辅助信号中标注了每个 skill 出现的消息编号
- 评估时优先参考 skill 出现位置附近的对话内容
- 如有焦点上下文，以焦点上下文为主要判断依据

{signals}

[已知 skills 列表]
{skill_list}

{focus_context}

[对话内容]
{conversation}

[输出格式] 只输出 JSON，首字符 {{ 末字符 }}，无 Markdown/注释/解释：
未调用 skill 时输出 {{"skills": []}}，否则按下方格式填写。
{{
  "skills": [
    {{
      "name": "data-query-shortcut",
      "is_correct": 1,
      "execution": {{
        "status": "success",
        "retry_detected": false,
        "retry_count": 0,
        "success_experience": null,
        "failure_category": null
      }}
    }},
    {{
      "name": "deploy-service",
      "is_correct": 1,
      "execution": {{
        "status": "success",
        "retry_detected": true,
        "retry_count": 2,
        "success_experience": {{
          "problem": "调用 deploy-service 时报错 'port 8080 already in use'",
          "solution": "先 lsof 查端口占用，kill 占用进程后重试"
        }},
        "failure_category": null
      }}
    }}
  ]
}}"""


# ═══════════════════════════════════════════════════════════
#  MCP Assessment (统一评估，合并原 Phase 1 + Phase 2)
# ═══════════════════════════════════════════════════════════

JUDGE_MCP_PROMPT = """请评估以下对话中 MCP 工具的选择正确性和执行状态（仅评估 mcp.* 工具调用）。

[字段定义]
每个 mcp 输出：
- name: 工具名称（必须来自下方已知列表）
- is_correct: 用户意图与工具用途是否匹配（1=正确，0=错误，"unknown"=无法确认用户意图）
- execution: 执行详情对象
  - status: "success" 或 "failure"
  - retry_detected: true | false | null（null=无法区分重试与正常多步调用）
  - retry_count: 重试次数（含首次失败尝试） | null（null规则：retry_detected=null 时必须为 null；retry_detected=false 时必须为 0）
  - success_experience: 若能总结可复用经验输出 {{problem, solution}}，否则 null
    - problem: 具体问题，必须包含工具名称和报错信息，≤80字，禁止"该mcp"等需要上下文才能理解的代词
    - solution: 具体解决方案，≤80字
  - failure_category: UPPER_SNAKE_CASE 根因分类码 | null（成功时为 null，不确定时 "UNKNOWN"）

[根因分类示例]
- "错误: 未安装 python-docx\n请运行: pip install python-docx\n(Command exited with code 1)" → TOOL_UNINSTALLED
- "Permission denied: /etc/config.yaml"                                  → PERMISSION_DENIED

[成功经验示例 — 正确]
{{"problem": "调用 mcp.file_read 时报错 'Permission denied: /etc/config.yaml'，文件权限不足",
  "solution": "先 ls -la 检查文件权限，确认需要 root 权限后使用 sudo cat 读取"}}
[成功经验示例 — 错误]
{{"problem": "该mcp报错权限不足",             // 错误：缺工具名和具体路径
  "solution": "检查权限"}}                    // 错误：方案不具体

[位置定位指引]
- 每条消息带 [#N] 编号，辅助信号中标注了每个 MCP 出现的消息编号
- 评估执行状态时，优先参考 MCP 调用消息及其紧随的下一条消息（通常是 toolResult）
- 如有焦点上下文，以焦点上下文为主要判断依据

{signals}

[已知 mcps 列表]
{mcp_list}

{focus_context}

[对话内容]
{conversation}

[输出格式] 只输出 JSON，首字符 {{ 末字符 }}，无 Markdown/注释/解释：
未调用 mcp 时输出 {{"mcps": []}}，否则按下方格式填写。
{{
  "mcps": [
    {{
      "name": "mcp.file_read",
      "is_correct": 1,
      "execution": {{
        "status": "success",
        "retry_detected": false,
        "retry_count": 0,
        "success_experience": null,
        "failure_category": null
      }}
    }}
  ]
}}"""


# ═══════════════════════════════════════════════════════════
#  Skill DeepDive (Phase 2) — 已废弃，保留向后兼容
#  现已合并到 JUDGE_SKILL_PROMPT（统一评估）
# ═══════════════════════════════════════════════════════════
JUDGE_SKILL_DIVE_PROMPT = JUDGE_SKILL_PROMPT


# ═══════════════════════════════════════════════════════════
#  MCP DeepDive (Phase 2) — 已废弃，保留向后兼容
#  现已合并到 JUDGE_MCP_PROMPT（统一评估）
# ═══════════════════════════════════════════════════════════
JUDGE_MCP_DIVE_PROMPT = JUDGE_MCP_PROMPT


# ═══════════════════════════════════════════════════════════
#  多任务边界识别
# ═══════════════════════════════════════════════════════════

JUDGE_TASK_SPLIT_PROMPT = """以下对话可能包含多个独立任务，请识别任务边界。

**任务定义与层级：**
一个 session 可包含多个任务，每个任务包含多轮对话，每轮包含多条 message。
- 轮（turn）= 用户发一条指令起，到下一条用户指令（或对话结束）止；期间 agent 因思考/调研/工具调用产生多条消息，都属同一轮。
- 任务（task）= 围绕**同一个主题**展开的一系列**连贯**指令或补充。

**任务边界判定（关键）：**
- 新任务边界 = 用户**切换主题**。只有用户发起一个与当前任务主题无关的新目标，才算开新任务。
- 以下情形**仍是同一任务**，不要切开：
  · agent 缺信息，用户**补充/澄清**（如提供手机号、日期、参数、凭证、文件）；
  · 用户对当前主题**追问/确认/纠正/微调**（如"再用日期0701查一次""也查一下13800138000"）；
  · agent 请求用户确认/选择/授权，用户**回应**（如"选花呗""确认""继续"）；
  · 失败重试、换方案、换参数后继续围绕**原目标**推进；同类试探性查询换对象（如"查案件123没找到→那查案件456"）也属同一任务；
  · agent 自己拆分的子步骤（读文档、查数据、生成、上传、校验）都是同一任务的不同阶段，不另立任务。
- 以下情形**是新任务**：
  · 当前主题交付后，用户**转去另一主题**（如"报告生成完了，现在帮我查一下X"）；
  · 前一目标尚未闭环时插入的、与原目标**完全独立**的新请求（如"报告1还在等凭证，顺便也帮我生成另一项目报告2"），此时报告1与报告2为两个任务；
  · 用户明确提出全新的、与前文无关的诉求。

**判定优先级：** 先看用户是否切了主题；若仍围绕原主题补充/追加/重试，整段归同一任务，只在"主题真正切换"处切。

**用户消息编号规则：**
- 按出现的顺序给 [User] 消息编号，第1条 [User] 为序号 1
- start_at_user_message 和 end_at_user_message 均为 1-based 序号

**对话内容：**
{conversation}

[输出格式 —— 强制 JSON]
直接输出以下 JSON 结构，禁止包含任何其他文字、标记或解释：
{{
  "task_count": 任务数量,
  "tasks": [
    {{
      "task_index": 1,
      "start_at_user_message": 起始用户消息序号,
      "end_at_user_message": 结束用户消息序号,
      "task_description": "简短描述该任务目标"
    }}
  ]
}}"""


# ═══════════════════════════════════════════════════════════
#  LLM 调用函数复用说明
# ═══════════════════════════════════════════════════════════
# LLM 调用函数（call_judge_llm, call_judge_llm_batch, LLMCallTask, LLMCallResult）
# 已统一封装在 prompts/judge.py 中，请直接从该模块导入：
#
#   from OpenclawSessionAnalysis prompts.judge import (
#       call_judge_llm,
#       call_judge_llm_batch,
#       LLMCallTask,
#       LLMCallResult,
#   )
# ═══════════════════════════════════════════════════════════