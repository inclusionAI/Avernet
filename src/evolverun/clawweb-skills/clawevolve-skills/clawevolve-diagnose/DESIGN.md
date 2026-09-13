# clawevolve-diagnose 设计文档

## 1. 模块定位

`clawevolve-diagnose` 是面向 Bot 使用者的 session 诊断产品：在用户指定的时间、数量和主题范围内读取原始 session，回答“Bot 当前主要有什么问题、证据是否充分、下一步优先优化什么”。

1. 解析 slash command 参数与用户自然语言，识别 `exploratory`（开放探索）或 `hypothesis`（验证假设）诊断模式。
2. 扫描本地 session，按时间范围、数量、good/bad 比例等要求筛选候选，并记录当前 runtime bot id 作为输出元数据。
3. 使用 judge 对任务完成情况、失败模式、根因和可优化价值做结构化判断：传入 `--api-key` 时走 OpenAI-compatible API + OCSA session_report；若用户提出具体要求，则追加 diagnose-owned request matcher；未传 key 时直接失败。
4. 汇总诊断状态、覆盖范围、问题优先级、假设结论和恢复动作，避免把数据不足误报为“没有问题”。
5. 将被选中的 session 抽成上下文无关的评测 case query，并输出用户可读报告、逐 case 证据和 plan handoff 文件。

本模块只负责“诊断与 case 抽取”，不执行进化修改。只有证据足够且存在可执行 case 时，结果才适合进入后续 Plan；ClawBench 模板生成、train/test 划分和 bench domain 上传由 `clawevolve-plan` 承担。

## 2. 输入参数与默认值

入口脚本支持直接命令行参数，也支持线上 bot 把完整 slash command 作为一个字符串传入；`cli._normalize_argv()` 会先拆分完整命令，再交给 argparse。

关键参数：

| 参数 | 是否必需 | 默认值 | 作用 |
| --- | --- | --- | --- |
| `message` | 否 | 空字符串 | 自然语言需求，如 case 数量、时间范围、good/bad 比例、故障类型偏好。 |
| `--task-id` | 是 | 无 | ClawWeb task id；同时决定默认输出目录。必须原样传入，不允许把 `-` 改成 `_`。 |
| `--step-id` | 是 | 无 | ClawWeb step id；用于步骤状态回传。必须原样传入。 |
| `--api-key` | 是 | 空 | OpenAI-compatible LLM API key；传入时走 direct API judge，未传或为空时直接失败；当前仅支持 API-key judge，OpenClaw subagent 暂时废弃；只用于请求头，不写入输出文件。 |
| `--llm-base-url` | 否 | 代码常量 `DEFAULT_BASE_URL` | OpenAI-compatible base URL；不读取环境变量。 |
| `--model` | 否 | `DEFAULT_MODEL` | LLM model name。 |
| `--max-sessions` | 否 | 0，实际使用 `DEFAULT_MAX_SESSIONS` | 最多实际送 judge 分析的 session 数；发现阶段可扫描更宽窗口。 |
| `--debug-session-path` | 否 | 空 | 调试入口：只读取并分析指定 session JSONL 文件。 |
| `--output-dir` | 否，隐藏 | 空 | 调试用输出目录；线上默认不需要传。 |

默认输出目录：

```text
/home/admin/.openclaw/workspace/clawevolve_results/{task_id}/diagnose/output
```

参数原则：影响业务行为的输入只能来自 CLI 显式参数、自然语言需求解析或代码默认值；不通过环境变量为业务参数赋值。日志模块可读取环境变量做脱敏辅助，但不改变诊断逻辑。

## 3. 主流程

`pipeline.run_pipeline(req)` 是真实链路主入口，执行顺序如下：

1. 创建输出目录并初始化 `clawevolve-diagnose.log`。
2. `discover_layout()` 发现本地运行环境、session 目录、agent 信息。
3. 组装 API-only judge runtime：`resolve_judge_runtime(req)` 要求 API key；API backend 使用压缩 session content + 用户要求 + taxonomy 的输入。OpenClaw subagent transport 已暂时废弃，不创建、不调用、不回退。
4. `parse_preference()` 解析自然语言需求，得到 case 数量、good/bad 配额、时间范围、故障模式、timeout、关键词等偏好。
5. `discover_sessions()` 按新到旧扫描本地 session。
6. `resolve_runtime_bot_id()` 识别当前 bot id，作为报告、case id 与 plan handoff 的稳定元数据。
7. `_diagnose_and_sample()` 调用本地 session judge，得到诊断候选并按偏好选择最终 case。
8. `_write_case_artifacts()` 写出诊断 JSONL、评测 JSONL、逐 case 文件夹和原始 session 副本。
9. `DiagnosisOutcomeBuilder` 形成稳定的产品结论：`diagnosis_status`、`diagnosis_mode`、`diagnosis_scope`、`hypothesis_result`、`prioritized_issues`、`overall_conclusion` 和 `recovery_actions`。
10. `_write_analysis_report()` 写出面向用户阅读、同时保留证据审计信息的 Markdown 报告。
11. `_write_plan_source()` 生成 `clawevolve-plan` 所需的 `plan-source/v2`；`insufficient_evidence` 不标记为 ready for plan。
12. 写 summary JSON，CLI 再基于 summary 构造 ClawWeb 成功回传 payload。

CLI 层在主流程结束后负责 ClawWeb step report：成功回传 `succeeded`，异常回传 `failed`。上传失败不会覆盖本地诊断结果。

## 4. 用户意图、诊断模式与时间范围解析

`intent.parse_preference()` 采用 **LLM JSON 优先、确定性规则兜底** 的策略：自然语言非空且提供 API key 时先调用 LLM 解析；调用失败时记录 warning 并回退到规则解析；消息为空或没有 API key 时直接使用规则解析。该设计兼顾复杂需求理解和启动稳定性。

解析内容包括：

- 诊断模式：默认 `exploratory`，用于开放发现主要问题；当用户提出明确待验证判断时使用 `hypothesis`，保留 `hypothesis_text`。
- case 数量与 good/bad 分布。
- 时间范围，如“近 3 天”“20260720 至 20260727”。
- 关注、必含和排除关键词。
- 目标故障类型、timeout 和最多分析 session 数。

时间范围进入 `CasePreference.since/until/time_range_label`，在 session judge 前用于本地过滤。日志记录解析来源（`llm_json`、`rule_only` 或 `rule_fallback_after_llm_failure`）、message preview、时间字段和筛选数量，便于解释“实际分析了什么”。

## 5. Session 分析与选择机制

### 5.1 默认 `sequential` 模式

`sequential` 的语义是“不做规则预筛，按时间顺序直接分析”。API backend 使用 OCSA session_report 做细致分析；当用户提出具体 diagnose 要求时，追加 diagnose-owned request matcher 将 OCSA task report 匹配为 case；OpenClaw subagent transport 已废弃。

- session 扫描顺序：按 session 时间或文件 mtime 新到旧。
- runtime bot id 解析和时间范围过滤：在 judge 前完成。
- batch size 上限：`DEFAULT_JUDGE_SESSION_BATCH_SIZE = 8`。
- 每批 judge 分析数会按尚未满足的 case 总数及 good/bad 配额动态缩小；例如已选 4/5 时，下一批只分析 1 个 session。
- 最大并发任务数：`DEFAULT_API_JUDGE_MAX_CONCURRENT_TASKS = 8`。
- 最大 judge 轮数：`DEFAULT_MAX_JUDGE_ROUNDS = 100`。
- 单个 session judge 超时：`DEFAULT_SESSION_JUDGE_TIMEOUT_SECONDS = 600` 秒；subagent prompt 仍要求尽快完成，避免不必要的环境探索。

因此默认链路不会先做粗筛；API backend 会并发分析小批 session 以保留效率。一旦最终选择结果满足用户要求的 case 数量、good/bad 配额和质量约束，立即以 `selection_satisfied` 停止，不再为扩大召回扫描剩余 session。

### 5.2 相关性判断

所有候选按时间与去重后直接送 selected session judge：

1. 历史 subagent backend（已废弃） 在单 session prompt 中接收用户原始需求，并输出可评测或结构化拒绝。
2. API backend 直接把压缩后的 session evidence、用户原始需求和输出 schema 一并交给 LLM 分析；用户原始需求是主相关性契约。

### 5.3 去重与选择

Session judge 产出的 `Diagnosis` 会先补充优化元数据，再由 `selection.select_diagnoses()` 按用户偏好选择：

- 满足 case 总数。
- 尽量满足 good/bad 配额。
- 优先高质量、高可控、高优化价值 case。
- 根据 failure mode、root cause、query 意图做去重，避免多个几乎相同 case 占满名额。
- 当候选不足时保留 warning，并在 summary 中明确 underfilled 信息。

## 5.4 产品化诊断结论

`DiagnosisOutcomeBuilder` 将 session 级结果整理成稳定、可读、可行动的产品视图：

- `diagnosis_status`：`complete`、`partial` 或 `insufficient_evidence`。状态综合 session 发现量、judge 完成量、最终 case 数和配额满足情况，而不是简单以“脚本未报错”为成功。
- `diagnosis_mode`：明确本轮是开放探索还是假设验证。
- `diagnosis_scope`：记录发现、分析、选中数量、目标数量、时间范围和覆盖率，帮助用户判断结论边界。
- `prioritized_issues`：按频率、置信度、优化价值和可控性排序，并给出证据 session、代表根因和进化方向。
- `hypothesis_result`：仅在 hypothesis 模式输出 `supported`、`partially_supported`、`not_supported` 或 `insufficient_evidence`，并区分支持证据、反例和其他失败。
- `overall_conclusion`：用产品语言概括本轮最重要结论。
- `recovery_actions`：证据不足、judge 不可用、case 配额不足等情况下，给出扩大范围、修复运行环境或调整条件等下一步动作。

报告首先呈现结论、优先问题和下一步，再展示逐 case 技术证据。这样既适合用户快速决策，也保留开发者复核所需的可追溯信息。

## 6. 标准库流式 Direct API 调用实现

Direct API backend 的模型调用集中在 `judge.openai_chat_client.chat_json()`：

- 请求 OpenAI-compatible `/chat/completions`，不依赖 `openai`、`httpx` 或 `requests`。
- 请求严格使用 `stream:true`；system/user message 的 `content` 均为字符串，与线上验证通过的 curl 形态一致。
- 使用 Python 标准库 `urllib` 发起请求并逐条解析 SSE `data:` 事件，直到 `[DONE]` 或响应流结束。
- 同时聚合 `content`、`reasoning_content` 等兼容字段；优先从最终 `content` 解析 JSON，必要时回退到 reasoning 字段。
- 每次 `chat_json()` 只有一层传输重试：连接失败、超时、流中断、HTTP 408/409/425/429/5xx 重试 4 次，最多 5 次 HTTP attempt；鉴权和请求参数错误不重试。
- 优先遵循上游 `Retry-After`，否则指数退避；模型输出无法解析等应用层错误不重试。
- 默认单次连接/读取 timeout 为 600 秒。日志记录 session、attempt、HTTP 状态、响应预览、provider request id、流 chunk 数、耗时及底层异常；API key 始终脱敏。
- `urllib` 默认继承标准环境代理配置；未显式指定端口的 HTTPS URL 自然使用 443。

`validate_chat_runtime()` 会提前发现 API key 被省略号截断、base URL 非法、model 为空等配置错误，避免进入大量 session 后才重复失败。

## 7. Session judge 结果语义

每个被分析的 session 可能被判断为以下大类：

- `bad`：session 暴露出可优化问题，例如工具参数错误、检索没调用、检索 query 差、证据未使用、权限/网络阻断、运行配置缺失、流程规划失败、过早拒答、无意义等待用户、异步任务未跟进、上下文截断等。
- `good`：可作为回归保护的成功任务，后续优化不能让这类能力退化。
- `no_task` 或低价值候选：没有明确可复现用户任务、只是闲聊、信息不足、任务无法独立评测等，通常不会进入最终 case。

核心字段包括：

- `case_type`
- `symptom_class`
- `root_cause_class`
- `common_problem_key`
- `evolution_failure_mode`
- `root_cause_summary`
- `quality_score`
- `failure_controllability`
- `optimization_value`
- `evidence`
- `tool_hints`

这些字段既用于抽样选择，也会进入 Plan Source，帮助 Plan 生成目标和 spec。

## 8. Eval Query 生成

评测 case 的 `query` 不能直接依赖原始 session 上下文，必须是全新 agent 可直接执行的单轮用户任务。两条 judge backend 的 query 生成方式不同：

- Direct API diagnose-native backend：单 session judge 直接输出上下文无关 query；保留 `query_rewriter.rewrite_eval_query_with_llm()` 供 OCSA 兼容路径/其他调用点使用。
- Keyless 历史 subagent backend（已废弃）：由单 session subagent 在诊断输出 schema 中直接返回 `query`，prompt 明确要求合并必要路径、ID、参数、时间范围、成功标准，并禁止输出“阅读这个 session/继续上文”等上下文依赖任务。

两条链路最终都会经过 `replayability_issues()` 校验；无法独立复现的 query 会被丢弃或降级为 judge error 诊断，不进入最终可选 case。

## 9. 输出文件

真实链路输出都在 diagnose `output/` 下：

| 文件/目录 | 作用 |
| --- | --- |
| `clawevolve-diagnose.log` | 主日志，记录参数解析、时间解析、session 筛选、judge 调用、选择、ClawWeb 上传等关键细节。 |
| `{bot}_diagnosis.jsonl` | 所有被 session judge 诊断出的候选结果，便于审计未入选 case。 |
| `{bot}_eval_queries.jsonl` | 最终选中 case 的轻量 query 列表。 |
| `{bot}_eval_cases.jsonl` | 最终选中 case 的完整评测元数据。 |
| `{bot}_diagnose_result.json` | case manifest，汇总 case 文件夹路径和主要产物。 |
| `{bot}_analysis_report.md` | 面向用户的诊断报告，先呈现状态、范围、优先问题和下一步，再附代表 case 与证据。 |
| `plan-source.json` | diagnose 到 plan 的统一 `plan-source/v2` 交接文件。 |
| `{bot}_summary.json` | 本次运行总览、有效参数、warning、ClawWeb 回传结果。 |
| `diagnose_cases/{case_id}/session.json` | 归一化后的 session payload。 |
| `diagnose_cases/{case_id}/original_session*.jsonl` | 原始 session 文件副本，用于从 case 追溯回真实记录。 |
| `diagnose_cases/{case_id}/judge_result.json` | 单 case 的 judge 结构化结果。 |
| `diagnose_cases/{case_id}/analysis.md` | 单 case 的人工阅读分析。 |

每个抽取出的 case 都可以通过 `case_id -> diagnose_cases/{case_id}/... -> session_id/session_path/original_session*.jsonl` 追溯到原始 session。

## 10. Diagnose -> Plan 交接契约

`plan-source.json` 的 schema version 为 `plan-source/v2`，核心内容：

- `source`：Producer、Bot、版本和本次 Diagnose 身份；
- `problem`：用户意图与问题标题；
- `cases`：统一 case 上下文、证据、分析和规划提示；
- `analysis`：case 分布与根因聚类；
- `planning_hints`：默认优化目标、case 偏好和用户意图；
- `extensions.diagnose`：Diagnose 专属的 artifacts、selection report 与 agent context。

`cases` 中保留 `case_id`、`session_id`、`query`、context、evidence、analysis、planning hints 和逐 case 文件路径。Plan 只依赖这份 canonical Source 和 plan 前置检查生成的 discovery notes，不再识别 Diagnose 专属 schema。

## 11. ClawWeb 对接

Diagnose 使用 `clawweb_events.post_step_report()` 回传：

```text
POST {clawweb_url}/api/evolve/internal/tasks/{task_id}/steps/{step_id}/report
```

请求体格式：

```json
{
  "status": "running|succeeded|failed",
  "summary": "...",
  "progress": {},
  "output": {},
  "error": "..."
}
```

成功时 `output` 结构：

```json
{
  "diagnosis": {
    "summary": "...",
    "issues": [
      {
        "code": "TOOL_PARAMETER_ERROR",
        "title": "...",
        "severity": "high|medium",
        "caseCount": 4,
        "suggestion": "..."
      }
    ]
  },
  "cases": {
    "total": 5,
    "goodCount": 1,
    "badCount": 4,
    "items": [
      {"caseId": "...", "type": "bad|good", "summary": "..."}
    ]
  }
}
```

上报使用 `--noproxy '*'` 等价行为，不继承环境代理；最多 4 次 attempt。4xx/网络/网关等错误会以 `deferred` 记录，不阻断本地 artifact 产出。

## 13. 日志与可排查性

日志集中写入 `output/clawevolve-diagnose.log`。关键日志点包括：

- ClawWeb 最终 success/failure report 的 URL、payload preview、HTTP 状态、错误分类；执行过程中不发送 running/progress report。
- LLM SDK endpoint、SDK 重试配置、分阶段 timeout、payload/response preview、provider request id 和耗时。
- 意图解析、时间范围解析、session 发现和过滤数量。
- judge batch、并发、失败 session、停止原因。
- case 选择、配额不足、去重跳过数。
- eval query 改写输入/输出、缺失上下文、回退原因。
- 原始 session 副本拷贝结果。

日志中应避免明文密钥；`--api-key` 只作为 secret 传给 logger 做脱敏。

## 14. 已知边界与后续优化

- 自然语言意图解析依赖 LLM 时可能失败；当前会自动回退到确定性规则并在 warning 中说明，复杂语义在 fallback 下可能只被部分识别。
- 优先级是基于本轮已分析样本的启发式排序，不代表 Bot 全量历史的绝对问题分布；应结合 `diagnosis_scope` 阅读。
- Diagnose 中 `case_split` 仍是早期 train/validation 标记；正式 train/test 以 plan 的重新划分为准。
- Session judge 质量依赖输入 session 可读性和模型/agent 能力；低质量 session 会被 warning 或低分过滤。
- ClawWeb step report 失败只记录 deferred，当前不做异步补偿队列。
