---
name: clawevolve-diagnose
description: Diagnose a bot by analyzing a bounded range of its original OpenClaw session records according to the user-provided natural-language intent, extracting evidence-backed good and bad cases, identifying behavioral and MCP/tool-use problems, and producing structured artifacts for subsequent clawevolve-plan evolution planning. Use when receiving /clawevolve-diagnose or when asked to inspect historical bot sessions, diagnose failures, extract evaluation cases, or prepare diagnosis input for self-evolution; Session Judge supports the Bot's OpenClaw Agent by default and an optional API-key backend.
---

# clawevolve-diagnose


## 最高优先级执行规则

1. **用户怎么调用，就怎么传给脚本**：不要改参数名、不要改 ID 字符、不要补参数、不要删除参数。
3. **只运行一次脚本**：禁止先空跑 `bash scripts/run.sh`，禁止失败后换参数重跑，禁止手工 curl ClawWeb 代替脚本。
4. **ID 必须原样保留**：`EV-...`、`STEP-...` 中的连字符 `-` 不能改成下划线 `_`；参数名必须是 `--task-id`、`--step-id`。

执行前自检最终 shell 命令：

- shell 命令必须包含用户原始 `--task-id`、`--step-id`；若用户提供了 `--api-key`、`--model` 等参数，也必须逐字符保留。
- shell 命令中的 task-id/step-id 必须与用户文本逐字符一致。

## 角色定位

收到 `/clawevolve-diagnose ...` 或用户要求“诊断历史 session / 抽取 eval case / 挖掘 good/bad case / 为 clawevolve-plan 准备输入”时，Agent 只做**脚本执行器**：运行脚本、等待结束、读取最终 stdout JSON、按 JSON 指示下一步。不要手工分析 session、不要重写流程、不要自行上传或生成模板。

固定链路：**发现本地 sessions → selected session judge 诊断 → 去重选 case → 写 artifacts/`plan-source.json` → 上报 ClawWeb step report**。模板生成属于 `clawevolve-plan`，不在本 skill 内完成。

## 必须执行的命令

收到 `/clawevolve-diagnose ...` 后，只运行一次前台脚本，并把用户的完整 slash command **作为一个单独引号参数**传给 `scripts/run.sh`。这是唯一推荐方式，可最大程度避免自然语言、日期范围和 ID 被拆坏：

```bash
cd clawevolve-diagnose && bash scripts/run.sh '<用户完整/clawevolve-diagnose命令原文>'
```

稳定脚本调用建议使用显式 `--intent`：

```bash
cd clawevolve-diagnose && bash scripts/run.sh \
  --task-id "$TASK_ID" \
  --step-id "$STEP_ID" \
  --judge-backend subagent \
  --intent "诊断工具调用失败和异步任务未完成的问题，抽取最多10个case，重点关注最近7天"
```

示例：用户输入是：

```text
/clawevolve-diagnose --api-key *** --model GLM-5 --intent "抽取 20260723 至 20260804 的5个case，有good和bad，bad占比至少80%。" --task-id EV-20260804-444491B4 --step-id STEP-A70F0B24A0804F33
```

则执行：

```bash
cd clawevolve-diagnose && bash scripts/run.sh '/clawevolve-diagnose --api-key *** --model GLM-5 --intent "抽取 20260723 至 20260804 的5个case，有good和bad，bad占比至少80%。" --task-id EV-20260804-444491B4 --step-id STEP-A70F0B24A0804F33'
```

注意：不要把示例中的 `***` 当真实 key。`--judge-backend subagent` 使用当前 Bot 的 OpenClaw Agent，
不需要 API Key；`--judge-backend api` 才要求 `--api-key` 或 `OPENAI_API_KEY`。未显式选择 backend 时，
有 Key 兼容走 API，否则默认走 subagent。ARCA Message 模式只允许 subagent，不得在 Message 中传 Key。

执行规则：

- **必须逐字符保留用户给出的 `--task-id`、`--step-id`、`--api-key`、`--model` 等参数值**；不要改写、删减、重排、补造用户参数。
- **运行可能超过 30 分钟，耗时长是正常现象**；触发后必须让同一个进程运行到退出，不能因短时间无最终 JSON、进度慢、LLM/上传重试、shell tool 返回仍在运行而中断。
- **不要启动第二个 diagnose**；进程未退出就持续等待/轮询同一个进程。
- stderr 是进度日志，不是最终结果；最终结果只看脚本退出后 stdout 的 JSON。

## 参数契约

业务输入来自**显式命令参数、`--intent` 自然语言解析、代码默认值**。仅 API Judge 的 Key 支持
`OPENAI_API_KEY` 环境变量（BaaS 注入）；`clawweb-url` 支持部署环境注入的默认值。

显式 CLI 参数：

| 参数 | 是否必填 | 默认值 | 说明 |
|---|---:|---|---|
| `--task-id` | 是 | 无 | ClawWeb task ID；也用于默认输出目录。只接受字母/数字/`_`/`-`/`.`，禁止 `..`。注意参数名是连字符。 |
| `--step-id` | 是 | 无 | ClawWeb step ID；与 `--task-id` 共同用于上报 `/tasks/{task_id}/steps/{step_id}/report`。注意参数名是连字符。 |
| `--judge-backend` | 否 | 有 Key 时 `api`，否则 `subagent` | `subagent` 使用当前 Bot OpenClaw Agent；`api` 使用 OpenAI-compatible API。 |
| `--api-key` | API Judge 必填 | `OPENAI_API_KEY` | OpenAI-compatible LLM API key；命令行主要用于本地调试，BaaS 使用环境变量注入；subagent 不消费 Key。 |
| `--llm-base-url` | 否 | 代码默认 `DEFAULT_BASE_URL` | OpenAI-compatible base URL；未传时只用代码默认。LLM 请求最终走 `<base-url>/chat/completions`。 |
| `--model` | 否 | 代码默认 `DEFAULT_MODEL` | LLM model name。 |
| `--intent` | 否 | 空 | 本次诊断的自然语言意图；可描述问题类型、case 数量、时间范围和筛选偏好。自然语言只能通过该参数传入。 |
| `--max-sessions` | 否 | 10 | 快速过滤后最多保留并送 Judge 分析的最新 session 数；个人/服务 Bot 共用。 |
| `--debug-session-path` | 否 | 空 | 调试入口：只从指定 session JSONL 文件读取并分析该 session。 |
| `--openclaw-home` | 否 | `~/.openclaw` | 本地测试可指定 OpenClaw 根目录。 |
| `--clawweb-url` | 否 | 部署默认值 | ClawWeb Step Report 基础地址。 |
| `--skip-clawweb-report` | 否 | false | 仅本地测试使用，跳过网络上报。 |
| `--output-dir` | 否 | `/home/admin/.openclaw/workspace/clawevolve_results/{task_id}/diagnose/output/` | 隐藏兼容参数；一般不要主动添加。 |

已废弃/不支持：`--execution-id`、`--execution_id`、`--task_id`、`--step_id`、`--llm-model`、`--evolve-run-id`。必须使用 `--task-id`、`--step-id`。遇到旧下划线参数应让脚本报错，不要自动替换。

自然语言意图只能通过 `--intent "..."` 指定；不接受位置参数形式的自然语言。意图可指定：case 数量、good/bad 数量或比例、关注/排除关键词、失败模式、时间范围。时间范围支持今天/昨天/近7天/不限时间，以及显式日期区间如 `2026-07-01到2026-07-10`、`20260701~20260710`、`7月1日至7月10日`；必须把用户自然语言原样放入 `--intent` 参数并透传给脚本。默认时间窗口为近 3 天。


## ClawWeb 上报契约

`--task-id` 和 `--step-id` 校验通过后，脚本会向 ClawWeb 上报 step report；主流程成功上报 `succeeded`，失败上报 `failed`，上传失败只记录为 deferred，不掩盖本地 diagnose 结果。

固定接口：

```text
POST {clawweb_url}/api/evolve/internal/tasks/{task_id}/steps/{step_id}/report
Content-Type: application/json
```

成功 payload 形态：

```json
{
  "status": "succeeded",
  "summary": "完成近期 Case 抽取与问题诊断",
  "output": {
    "diagnosis": {"summary": "...", "issues": []},
    "cases": {"total": 5, "goodCount": 1, "badCount": 4, "items": []}
  }
}
```

当前 diagnose 只在本地流程结束后上报一次最终结果：成功上报 `status=succeeded`，失败上报 `status=failed`、`summary`、`error`。网络调用等价于 `curl --noproxy '*'`，最多重试 3 次（共 4 次尝试）；若后端返回 step 已处于终态，立即停止重试并记录 deferred。

## Judge 调用与排障要点

- session judge 调用由脚本内部完成；Agent 不要直接 curl、直接调用其他 LLM SDK，或手工分析 session 替代脚本。
- `--judge-backend subagent`：使用当前 Bot 的 OpenClaw Agent；脚本负责 Agent 调用、结构化结果校验、
  诊断产物落盘以及临时 Agent/Session 清理。该链路不读取也不需要 API Key。
- 传入 `--api-key`：请求格式对齐 OpenAI-compatible `/chat/completions`：`model`、字符串形式的 `messages[].content`、`stream:true`；代码只使用 Python 标准库 `urllib`，以 `stream:true` 调用用户指定 base URL 的 OpenAI-compatible `/chat/completions`，逐条解析 SSE；日志会输出 endpoint、attempt、payload/content preview、HTTP 响应、上游 request id 和重试信息。
- `--judge-backend api`：请求中携带压缩后的 session content，由 OCSA/session judge 返回结构化诊断；
  缺少 API Key 时失败，不回退到 subagent。
- 不指定端口的 HTTPS 应走 443；若 API 日志出现 `:7002`，优先检查传入的 `--llm-base-url` 或运行环境参数透传。
- direct API judge 不实现外层业务重试；底层标准库 HTTP 客户端统一处理连接错误、流读取中断、超时、限流和服务端错误，重试 4 次、最多 5 次 HTTP attempt。请求使用 `stream:true`，默认单次连接/读取 timeout 为 600 秒。
- API key 会脱敏；但为开发排查，日志会包含 session/query/payload 预览。

## session 选择逻辑

默认 session 选择逻辑：

1. 自动发现当前 OpenClaw runtime、本地 session 目录和当前 bot identity。
2. 将发现到的本地 JSONL sessions 视为当前 bot 候选；不再按可能陈旧的内嵌 bot_id 二次过滤。
3. 按时间 newest-to-oldest 扫描，流式解析并限量，避免 OOM。
4. 每轮按最多 8 个的小批次取去重后的 session **直接送 selected session judge**；无规则质量预筛。批大小会根据尚缺的 case 数量及 good/bad 配额动态缩小；一旦满足用户要求立即停止，不继续扫描剩余 session。API backend 可并发；subagent 通过 OpenClaw Agent 逐 session 诊断。
5. 持续到满足 case 配额、候选池耗尽或达到代码默认最大 judge 轮数。

相关性由 selected session judge 基于用户原始需求判断。


## case query 抽取要求

评测集中的 `query` 必须是**上下文无关的单个用户任务**：

- 不输出“复现原始 session”“不要依赖原 session 文件”等诊断包装话术。
- 如果原 session 先给参数/约束、后续才说执行任务，`query` 必须合并这些必要参数和执行动作。
- 优先保留原始用户意图和必要约束；不可重放、缺参数、只含“继续/同上”的任务不能进入评测集。
- `original_query` 只用于溯源，真正评测只看 `query`。

## 输出与下一步

脚本成功或部分成功都会输出最终 stdout JSON，常见字段：

- `status`
- `run_dir`
- `task_id`
- `step_id`
- `ready_for_plan`
- `agent_next_action`
- `summary_json`
- `plan_source_json`
- `analysis_report_md`
- `diagnose_result_json`
- `log_file`
- `warnings`
- `clawweb_upload`

主要 artifacts：

- `<bot>_summary.json`：总摘要与 effective_parameters。
- `<bot>_diagnosis.jsonl`：所有诊断结果。
- `<bot>_eval_queries.jsonl`：最终可回放 queries。
- `<bot>_eval_cases.jsonl`：最终 case 元数据。
- `<bot>_analysis_report.md`：诊断报告。
- `<bot>_diagnose_result.json`：case artifact manifest。
- `diagnose_cases/<case_id>/session.json`：原始 session copy。
- `diagnose_cases/<case_id>/judge_result.json`：judge 结果。
- `diagnose_cases/<case_id>/analysis.md`：单 case 分析。
- `plan-source.json`：符合 `plan-source/v2` 的正式 Plan Source；Plan 本地读取后冻结为自己的 `plan/input/source.json`。
- `clawevolve-diagnose.log`：详细排查日志。

按最终 JSON 行动：

- `agent_next_action=run_plan` 且 `ready_for_plan=true`：如用户目标已明确，可继续运行 `clawevolve-plan --task-id <同一个 task_id> --step-id <plan步骤step_id> --run-dir <run_dir>`；否则先询问优化目标。
- `agent_next_action=inspect_warnings` 或 `ready_for_plan=false`：总结 warnings/underfill，不要自动运行 plan。

## 禁止行为

除非最终 JSON 明确要求或用户另行要求，不要：

- 手工查看随机 session 文件代替脚本。
- 询问 bot id、session 路径、ClawWeb URL 等脚本会自动发现/使用的信息。
- 调用 ODPS、cached judge rows 或外部 OpenclawSessionAnalysis checkout。
- 自行上传 ClawWeb 或重复发送事件。
- 生成 ClawBench 模板。
- 在脚本未结束时给出最终结论。
- 因运行超过数分钟、超过 30 分钟、进度慢或暂时无新日志而终止进程。
- 因等待时间长而降低 case 数、改模式、改 URL、改 model 或改 key 后重跑。
- 将失败 JSON 隐藏起来并启动另一个命令。
- 把 `EV-20260804-444491B4` 改成 `EV_20260804_444491B4`，或把 `STEP-A70F0B24A0804F33` 改成 `STEP_A70F0B24A0804F33`。


## 线上真实测试强约束
