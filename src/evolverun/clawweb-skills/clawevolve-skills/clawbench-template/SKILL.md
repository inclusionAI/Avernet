---
name: clawbench-template
description: 创建 ClawBench 评测用例模板。当用户需要设计 benchmark case、MCP 工具调用 case、skill case 或 session baseline 模板时使用。
---

# ClawBench 用例模板

用于创建可发布的 ClawBench 评测用例模板，包括 benchmark、MCP、skill 和 session baseline 等类型。

对外统一称为 **ClawBench**，不要暴露内部运行时名称。

## 参考模板

当用户要求生成完整 ClawBench 用例、迁移旧用例、补齐评分逻辑，或输出可直接发布的 benchmark markdown 时，必须参考 `references/TASK_TEMPLATE.md`。

`references/TASK_TEMPLATE.md` 保留了原始任务模板的完整结构，包括：

- frontmatter 字段与 `workspace_files` 写法。
- `Prompt`、`Expected Behavior`、`Grading Criteria`、`Automated Checks`。
- `LLM Judge Rubric`、`Workspace Files`、`Additional Notes`。
- 自动评分函数签名：`grade(transcript: list, workspace_path: str) -> dict`。

不要在主 `SKILL.md` 中复制原始模板全文；主文件负责约束和决策，reference 文件负责保留完整样例，避免上下文过长且不丢信息。


## 历史 Session 与 Ground Truth

ClawBench 用例优先来自真实任务、真实失败或真实用户会话。用户提供历史 session、run id、transcript/jsonl 文件或失败日志时，应优先从真实执行轨迹中抽取：

- 原始用户意图和任务边界。
- Agent 实际采取的步骤、工具调用、参数和输出。
- 成功证据、失败证据、缺失步骤和可复现断言。
- 适合沉淀为 `Prompt`、`Expected Behavior`、`Grading Criteria` 和 `Automated Checks` 的信息。

历史 session 常见结构是 JSONL 事件流，可能包含 `session`、`model_change`、`custom`、`message` 等事件；`message.role` 可能是 `user`、`assistant`、`toolResult`；assistant 内容中可能包含 `thinking`、`toolCall`、`text`；toolResult 内容中可能包含工具输出。解析时应兼容字段缺失和不同事件形态。

如果用户提供 ground truth，应把它转化为明确可验证的断言。如果用户没有 ground truth，应主动引导用户补齐最小可验证事实，例如：

- 期望最终答案必须包含哪些字段或结论。
- 期望调用哪些能力或等价能力，以及关键参数是什么。
- 期望生成、修改或读取哪些文件。
- 哪些结果可接受，哪些结果必须判失败。
- 是否存在成功样例、失败样例或边界样例。


## Session JSONL 格式参考

历史 session 通常是 JSONL，每行一个事件对象。常见事件包括：

- `type: "session"`：会话元信息。
- `type: "model_change"`：模型切换信息。
- `type: "custom"`：自定义事件。
- `type: "message"`：用户、助手或工具结果消息。

`message` 事件常见结构：

```json
{
  "type": "message",
  "id": "event-id",
  "timestamp": "2026-01-01T00:00:00.000Z",
  "message": {
    "role": "user | assistant | toolResult",
    "content": [
      {
        "type": "text | thinking | toolCall",
        "text": "文本内容",
        "name": "tool_name",
        "arguments": {}
      }
    ]
  }
}
```

工具结果常见结构：

```json
{
  "type": "message",
  "message": {
    "role": "toolResult",
    "toolName": "tool_name",
    "toolCallId": "tool-call-id",
    "content": [
      {
        "type": "text",
        "text": "工具输出"
      }
    ],
    "isError": false
  }
}
```

评分脚本应容错处理：

- 缺少 `message`、`content`、`arguments` 的情况。
- `content` 可能是数组、字符串、对象或空值。
- tool call 可能在 `message.content[]` 中，也可能通过其他兼容字段出现。
- tool result 可能是 `role=toolResult`，也可能带有 `toolName`、`toolCallId` 或 `isError`。
- `arguments` 可能是 dict，也可能是 JSON 字符串或命令行文本，需要做兼容解析。


## 输出要求

当用户要求创建用例时，输出以下内容：

1. 建议文件名和默认输出路径。
2. 用例类型：benchmark、MCP、skill 或 baseline。
3. 完整 Markdown 用例内容。
4. `Grading Criteria`、`Automated Checks` 或 `LLM Judge Rubric` 说明。
5. timeout 建议。
6. 所需 fixture、输入文件、session reference 或环境前提。

如果用户没有指定目录，默认将模板生成到 `workspace/clawbench_template_generate/`。如果具备文件写入能力，应创建该目录并写入模板文件；如果当前环境不能写文件，再输出完整内容和建议路径。不要假设存在某个本地 `tasks/` 目录。

默认文件名使用时间戳加语义后缀，避免重复或覆盖已有文件。建议格式：`YYYYMMDD_HHMMSS_<short-task-name>.md`，例如 `20260611_203000_yuque_search_case.md`。`short-task-name` 使用小写英文、数字和下划线，避免空格和特殊字符。


## 交互式生成流程

交互时减少阻塞式二次确认。用户明确要求生成模板时，应先基于已有信息生成一份可修改的模板草案，并说明假设、评分点和待确认问题；只有关键输入完全缺失且无法合理假设时，才先用少量问题补齐。优先关注：

1. 要评测的能力、工具、skill 或业务场景是什么。
2. 真实用户请求或历史失败场景是什么。
3. 是否有历史 session、run id、transcript/jsonl 文件或失败日志可参考。
4. 是否有 ground truth，例如标准答案、期望文件、期望工具参数、期望业务状态或允许的等价结果。
5. 希望使用 `automated`、`llm_judge` 还是 `hybrid` 评分。
6. 成功、部分成功、失败分别应该如何判定。

推荐流程：生成模板草案，说明评分点、假设和风险；如需用户确认，应在草案之后提出具体待确认问题。信息充足且用户明确要求生成文件时，可以直接写入默认目录。

## Prompt 设计原则

`Prompt` 必须像真实用户需求，不能包含答案、工具名、命令、实现步骤或评分提示。

工具选择、参数、调用顺序、可接受替代方案和评分预期应放在 `Expected Behavior`、`Grading Criteria`、`Automated Checks` 或 `LLM Judge Rubric` 里。

错误示例：

```markdown
## Prompt
请查询已办任务列表。提示：使用 mcporter call xxx.query_completed_tasks。
```

正确示例：

```markdown
## Prompt
请帮我查询 2026 年 1 月 1 日到 2026 年 1 月 31 日期间，我的已办任务列表，最多返回 10 条记录。

## Expected Behavior
The agent should:
1. 探索可用工具，找到查询任务相关能力。
2. 调用已办任务查询能力。
3. 正确设置 fromDate、toDate、limit。
4. 返回已办任务列表。
```

## Benchmark Case 模板

适用于评测 Agent 是否完成一个明确任务，并通过自动评分逻辑验证结果。

````markdown
---
id: task_00_example
name: 示例任务
category: basic
grading_type: automated
timeout_seconds: 120
workspace_files: []
---

## Prompt

{{真实用户需求。不要写工具名、命令或解题提示。}}

## Expected Behavior

The agent should:
1. {{期望行为 1}}
2. {{期望行为 2}}
3. {{关键参数、输出格式或业务约束}}

## Grading Criteria

- [ ] {{评分项 1：明确可观察证据}}
- [ ] {{评分项 2：明确扣分条件}}
- [ ] {{评分项 3：最终产物要求}}

## Automated Checks

```python
def grade(transcript: list, workspace_path: str) -> dict:
    # transcript 通常包含 message、toolCall、toolResult 等事件。workspace_path 是隔离任务目录。
    # 评分逻辑应从真实执行轨迹中提取证据，不要只依赖最终文本。
    return {
        "requirement_met": 1.0,
        "key_parameter_correct": 1.0,
        "output_verified": 1.0,
    }
```
````

## MCP Case 注意事项

MCP case 用于评测 Agent 是否能自主发现并正确调用工具能力。Prompt 里不要直接写 MCP 工具名或命令。

评分脚本应兼容 Agent 可能采用的多种调用形态：

| 调用形态 | 示例 | 建议优先级 |
| --- | --- | --- |
| `key=value` | `userId="xxx" startTime="yyy"` | P0 |
| JSON args | `--args '{"userId":"xxx"}'` | P1 |
| flag style | `--user_id xxx --start_time yyy` | P2 |
| 直接结构化 tool call | `{"userId":"xxx"}` | P0 |

评分时建议同时检查：

- 是否调用了正确能力或等价能力。
- 是否传入关键参数。
- 时间范围、数量限制、枚举值等边界参数是否正确。
- 最终回答是否基于工具返回结果，而不是编造。

## Skill Case 注意事项

Skill case 用于评测 Agent 是否能正确应用某个领域 skill。

Prompt 可以描述业务目标，但不要写“请使用某某 skill”。Expected Behavior 中可以写明期望观察到的 skill 使用证据，例如：

- 是否读取或遵循了目标 skill 的核心约束。
- 是否调用了 skill 推荐的脚本或工作流。
- 是否避免了 skill 明确禁止的行为。
- 最终产物是否符合 skill 的格式、命名和质量要求。

## Baseline Case 模板

Baseline case 适用于基于已有 session transcript 或历史会话进行 rubric 评估。

```markdown
---
id: baseline_00_example
name: 示例会话基线
category: baseline
grading_type: llm_judge
timeout_seconds: 120
---

## Scope

{{说明要评估的会话类型、业务目标和输入来源。}}

## Session Reference

{{填写 session id、transcript 路径、run id，或说明由执行环境注入。}}

## Rubric

- {{评分项 1：明确可观察证据}}
- {{评分项 2：明确扣分条件}}
- {{评分项 3：最终产物要求}}

## Evidence Requirements

- {{需要从 session 中观察到的工具调用、文件变更、日志或回复内容。}}
- {{缺少证据时应如何扣分。}}
```


## Workflow 评测模板

适用于评测 Workflow（多节点 DAG）的端到端执行链路。生成过程完全**数据驱动**：
从 YAML 结构、各节点 SKILL.md、用户 Query 和可选的 Ground Truth / Sample Session
出发，系统性地派生模板的每个部分。

### 前置：文件发现（必须先做，禁止猜测）

**所有输入必须从磁盘文件读取，禁止根据 workflowId 名称猜测内容或结构。**
读不到文件时**必须告知用户**缺失了哪些，让用户提供，而不是自己编造。

**Step 0a. Workflow YAML 定位**：
1. **API 获取（推荐，覆盖 ClawWeb 托管的 workflow）**：
   ```bash
   curl -s 'http://127.0.0.1:5173/api/workflows/<workflowId>'
   ```
   或使用脚本：
   ```bash
   python3 scripts/fetch_workflow_yaml.py <workflowId> [--format yaml|json] [--output <path>]
   ```
   脚本位置：`clawbench_template_generate/scripts/fetch_workflow_yaml.py`
   如果 curl/脚本成功返回 JSON → 直接解析使用。
2. 本地 packs 目录：`/opt/openclawExt/clawmind/packs/<workflowId>/workflows/<workflowId>.yaml`
3. 回退：`find /opt/openclawExt/clawmind/packs -name "<workflowId>.yaml" 2>/dev/null`
4. 如果用户直接粘贴了 YAML 内容，直接使用，但需确认完整性
5. 都找不到 → 告知用户，列出已安装的 workflow，问用户提供 YAML

**说明**：ClawWeb 数据库模式（`database.mode: api`）的 workflow YAML 定义在
ClawWeb 服务端，不在本地 packs 目录。此时必须通过 API 获取。本机 ClawMind
的 ClawWeb API 地址在 `/opt/openclawExt/clawmind/configs/application.yaml`
中 `api.baseUrl` 配置，固定 API 路径为 `<baseUrl>/api/workflows/<workflowId>`。

**Step 0b. Skill SKILL.md 定位**：
YAML 中每个 `embedded-agent` 和 `subagent` 节点有 `skillName` 字段。对每个 skillName，
按以下顺序搜索 SKILL.md：
1. `/home/admin/.openclaw/workspace/skills/<skillName>/SKILL.md`
2. `/home/admin/.openclaw/workspace/skills/skills-repo/**/<skillName>/SKILL.md`
   （使用 `find ... -path "*/<skillName>/SKILL.md"`）
3. `/opt/openclawExt/clawmind/packs/<workflowId>/skills/<skillName>/SKILL.md`
   （pack 自带的 skill 定义）
4. 都找不到 → **记录该节点，告知用户**缺失了哪些 skill 的 SKILL.md，
   对该节点仅基于 YAML 中的 `outputContract` + `prompt` 做最小检查，不编造工具名。

**Skill 识别说明**：
- 如果所有 agent 节点都没有 `skillName`（全是 cli-script/done），则跳过 SKILL.md 搜索
- 如果 workflow 的 agent 节点不引用外部 skill 而是内联 prompt，则不需要 SKILL.md，
  检查项从 YAML 的 `outputContract` 和 `prompt` 内容派生

**Step 0c. Ground Truth / Sample Session**：
- 用户提供路径或内容时直接使用
- 未提供时：Ground Truth 从 YAML 的 `outputContract` 推断最小可验证约束；
  Sample Session 跳过，分支推断步骤生成全分支模板

### 评测数据格式概览（与模板对应）

生成的 `grade()` 函数将消费 `workflow_merge.py` 产出的 **merged transcript（v2.1 格式）**。
Agent 必须在生成模板前理解以下数据格式，确保检查项和 helper
函数使用与 transcript 结构一致：

**Transcript 结构**：
```
第 1 行：__manifest__ event（含 workflow_trace）
第 2 行：__workflow_start__ event（DAG 结构、goal）
第 3~N 行：按节点顺序排列的 events
  - 每个节点前有 __node_boundary__ 分隔
  - agent 类节点：message 事件（role=assistant/toolResult），含 toolCall
  - cli-script/done 节点：__node_synthetic__ 事件（含 __node_output__）
最后 1 行：__workflow_end__ event（status、workflow_outputs、duration）
```

**grade() 中可用的数据源与 helper**：

| 数据源 | 获取方式 | 适用节点类型 |
|--------|---------|------------|
| 节点 session 事件 | `node_entries["<node_id>"]` — 含所有 toolCall/text/toolResult | embedded-agent, subagent |
| 节点执行状态 | `node_executed(node_id)` — 布尔 | 所有类型，agent 类门禁 |
| trace 中节点存在性 | `node_in_trace(node_id)` — 布尔 | cli-script, done（门禁守卫） |
| 节点结构化输出 | `get_trace_node_output(node_id)` — dict 或 None | 所有类型（兜底） |
| workflow 级产出 | `get_workflow_outputs()` — dict（来自 outputContract） | done/finish 节点配合使用 |
| agent 输出 JSON | `get_final_json(entries)` — 从 assistant text 解析 JSON | embedded-agent, subagent |
| 工具调用列表 | `extract_tool_names(entries)` / `extract_exec_commands(entries)` | embedded-agent, subagent |

**关键约束（生成 grade() 时必须遵守）**：
- agent 类节点的存在性检查用 `node_executed()`（不走 trace 路径，避免 None 误判）
- cli-script/done 节点用 `node_in_trace()` 守卫，trace 缺失时豁免不扣分
- done 节点的 workflow 产出用 `get_workflow_outputs()`，不用 `get_trace_node_output("finish")`
- 所有 helper 函数签名和用法见 `references/WORKFLOW_TASK_TEMPLATE.md` §3.2

### 核心输入（4 项）

| 输入 | 必填 | 用途 |
|------|------|------|
| Workflow YAML | ✅ | 提取节点 DAG（id, executor.type, dependsOn, branchId）、分支规则（onResult.branches）、outputContract schema、input 参数定义 |
| 各节点 SKILL.md | ✅ | 对 `embedded-agent` / `subagent` 节点，读取 skill 目录下的 SKILL.md，提取：必须调用的工具/脚本、关键参数、输出 schema、禁止行为 |
| Query / Prompt | ✅ | 用户指定的评测意图。在线评测时此 Prompt 发给 Agent（格式 `/<workflowId> <参数>`） |
| Ground Truth | 可选 | 期望输出（字段/值），用于生成具体断言。没有时从 outputContract schema 推断最小可验证约束 |
| Sample Session | 可选 | merged JSONL（可选），用于自动推断实际执行分支，校准 grade() 中的断言 |

### 系统化生成流程（6 步）

#### Step 1: 解析 Workflow YAML — 建立节点词典

从 YAML 中提取以下结构化信息，作为后续所有决策的基础：

```
node_dictionary = {
  "<node_id>": {
    "executor_type": "embedded-agent" | "subagent" | "cli-script" | "human" | "done",
    "deps": ["dep_node_id", ...],
    "branch_id": "fast" | "thorough" | null,    # null = 公共节点
    "skill_name": "xxx",                          # 仅 agent 类节点
    "output_contract": { "required": [...], "schema": {...} },  # 存在时
    "on_result": {                                 # 存在时（分支跳转）
      "branches": [{"branchId": "...", "label": "..."}],
      "path": "dataExists"
    },
    "prompt": "...",                               # agent 类节点的 prompt 模板
    "command": "python3 ...",                      # cli-script 类节点
    "input_params": { "requiredParams": [...], ... }  # workflow 级 input
  }
}
```

**关键分析动作**：
- 根据 `branchId` 把节点分成：公共节点（无 branchId）、各分支专属节点
- 找出有 `onResult.branches` 的节点 → 它们是分支决策点
- 列出所有 `outputContract` 中的 `required` 字段 → 这些是评分 gate 的候选
- 找出 `{{nodeOutput.xxx.yyy}}` 引用 → 这些是跨节点数据依赖

#### Step 2: 读取 SKILL.md — 提取节点级检查项

对每个 `embedded-agent` / `subagent` 节点，读取其 `skillName` 对应的 SKILL.md。

**提取目标**（不从 SKILL.md 全文复制，只提取可验证的要素）：
- **工具/脚本调用**：SKILL.md 中列出的 `exec` 命令、`mcporter` 调用、MCP 工具名
- **关键参数**：脚本的必填参数（`--xxx`）、MCP 工具的参数名和类型
- **输出 schema**：期望的 JSON 字段、类型、枚举值
- **禁止行为**：SKILL.md 明确说「不要/禁止/警告」的行为
- **失败模式**：从 SKILL.md 的注意事项中推断常见错误

**产出**：一个 `node_checks` 字典，key 为 node_id，value 为：
```python
{
  "embedded-review": {
    "expected_tools": ["exec", "read", "mcporter"],
    "expected_commands": ["fraud-laundering-strategy-hit-analyzer"],
    "expected_mcp": ["riskfaas_mt_socplt_oa_workflow_action_finish"],
    "required_fields": ["summary", "conclusion", "risk_level"],
    "forbidden_patterns": ["JSON.parse on incident_replay"],
    "output_schema": { ... }  # 从 SKILL.md 或 outputContract 提取
  }
}
```

#### Step 3: 判断目标分支

- **用户明确指定分支** → 只评估该分支上的节点。公共节点 + 该分支专属节点。
- **用户提供 sample session** → 从 merged JSONL 的 `manifest["nodes"]` 自动推断。
  例如 `manifest["nodes"]` 包含 `"analysis-prepare"` 则为 analysis 分支。
- **均未提供** → 生成**全分支模板**。所有分支节点用 `if <branch_key_node> in node_entries`
  守卫，非本分支的板块跳过不扣分（exempt）。

#### Step 4: 生成 Automated Checks (grade 函数)

这是模板的核心。严格遵照 `references/WORKFLOW_TASK_TEMPLATE.md` §3.2 的骨架和 §3.1
的设计原则编写。

**4a. 确定板块划分**

板块 = 一个节点 OR 一组紧密关联的节点（如多轮 RPC 探查）。
默认按节点逐个建板块，仅当多个节点共享同一 skill 且输出不可分割时才合并。

**4b. 为每个板块生成检查项**

对每个板块，按以下规则生成 gate（门禁）和 bonus（加分），**所有检查项必须从输入数据派生，不自己杜撰**：

| 节点类型 | gate 来源 | bonus 来源 |
|---------|----------|----------|
| `embedded-agent` | ① node_executed() 存在性 ② 输出 JSON 可解析 ③ output_schema 的 required 字段全部存在 | ④ 非 required 但有价值的字段 ⑤ 工具调用符合 SKILL.md 规范 ⑥ 参数类型正确 |
| `subagent` | 同上，但用 node_executed() + get_trace_node_output() 兜底 | 同上 |
| `cli-script` | ① node_in_trace() 判断 trace 可用性 ② status == "succeeded" ③ output_keys 与 outputContract 一致 | ④ output 值域校验 ⑤ 错误处理（retry 后成功等） |
| `done` | ① node_in_trace() ② status == "succeeded" ③ output.done == true | ④ workflow_outputs 包含 outputContract 声明的键 |
| `human` | 在 manifest.nodes 中存在（用户已交互） | 无（人工节点不自动评分） |

**重要规则**：
- agent 节点用 **模式 A**（`node_executed()` 门禁），cli-script/done 用 **模式 B**（`node_in_trace()` 守卫）
- done 节点的 workflow 级输出用 `get_workflow_outputs()` 而非 `get_trace_node_output()`（类别错误）
- 禁止 `1.0 if trace_node_succeeded(...) else 0.0` 反模式
- 每节点最多 2-3 个 gate，最多 2-3 个 bonus

**4c. 板块权重分配**

使用 `references/WORKFLOW_TASK_TEMPLATE.md` §3.3 的加权票制。
默认每个板块 weight=5，各板块等权。特殊节点（如纯透传节点）可降为 2-3。

**4d. Ground Truth 注入**

如果用户提供了 Ground Truth（如"conclusion 应为 '需要交互'"），将其转化为具体的 gate：
```python
raw["XX_conclusion_value"] = 1.0 if output.get("conclusion") == "需要交互" else 0.0
gates["XX_conclusion_value"] = raw["XX_conclusion_value"] > 0
```
没有 GT 时从 outputContract schema 推断最小约束（如字段存在性、类型正确性）。

#### Step 5: 生成 LLM Judge Rubric

直接使用 `references/WORKFLOW_TASK_TEMPLATE.md` §4 的 **4 维度固定模板**，不需要根据 workflow
自定义维度：

| 维度 | 权重 | 评估内容 |
|------|------|---------|
| 链路完整性 | 25% | 节点是否按 DAG 顺序执行，分支选择是否正确 |
| 工具调用规范性 | 30% | 各节点工具选择、参数、调用次数是否合理 |
| 输出质量 | 25% | JSON 格式、字段完整性、值域合法性 |
| 错误处理 | 20% | 边界条件、异常恢复、重试逻辑 |

#### Step 6: 组装并输出

完整模板结构（按 WORKFLOW_TASK_TEMPLATE.md 参考）：

```markdown
---
id: task_<workflow_id>_e2e
name: <YAML title> 端到端链路合规性评估
category: workflow
grading_type: hybrid
timeout_seconds: <按最长节点 timeout 之和 + 120s 余量>
workspace_files: []
benchmark_kind: workflow
workflow_id: <workflowId>
grading_weights:
  automated: 0.6
  llm_judge: 0.4
---

## Prompt
/<workflowId> <参数>

## Expected Behavior
...（从 YAML DAG + SKILL.md + GT 自动生成）

## Grading Criteria
...（翻译 Step 4 的 gate/bonus 清单为 checkbox 格式）

## Automated Checks
...（Step 4 生成的 grade() 函数）

## LLM Judge Rubric
...（Step 5 的 4 维度模板）

## Additional Notes
...（输入数据来源、分支检测依据、使用方式）
```

### 参考模板

生成 Workflow 评测模板时，必须参考 `references/WORKFLOW_TASK_TEMPLATE.md`，其中包含:

- Merged Transcript v2.1 格式规范（含 `workflow_trace` 流转数据）
- 板块制 grade() 骨架（含分支检测模式、可复用 helper）
- LLM Judge Rubric 固定模板
- 代码规范与反例

### 关键约束

- Workflow 的 `## Prompt` 写 `/<workflowId> <参数>` 格式
- `grading_type` 统一 `hybrid`，`grading_weights` 统一 `automated: 0.6, llm_judge: 0.4`
- **不要假设具体 workflow 的业务逻辑**（如「预期结论=需要交互」）。检查项从 SKILL.md + outputContract + GT 派生
- **板块划分不要过细**。一个节点的 gate/bonus 合在一起，不要拆成多个微小板块
- **bonus 不要杜撰**。每个 bonus 必须有来源：outputContract 的 optional 字段、SKILL.md 的最佳实践、GT 的附加验证
- `subagent` 类型节点的 session 由 `workflow_flow.py` 自动发现，`grade()` 可对其做详细检查
- 离线评测时需手动 `--external-sessions` 指定 subagent session 路径


## 评分模式选择

选择评分模式时遵循以下规则：

- 有明确工具调用、文件产物、结构化输出或 ground truth：优先 `automated`。
- 需要判断内容质量、完整性、推理合理性或复杂业务语义：使用 `llm_judge`。
- 同时需要过程校验和质量判断：使用 `hybrid`。
- MCP 工具调用类 case 通常使用 `automated` 或 `hybrid`。
- Skill 行为类 case 通常使用 `hybrid`。
- 长会话、历史 session 或 baseline 复盘通常使用 `llm_judge` 或 `hybrid`。

不要生成空泛评分项，例如“回答正确”“任务完成”。每个评分项都必须对应可观察证据，例如工具名或等价能力、关键参数、文件内容、最终回答字段、transcript 事件或 ground truth 断言。

## 评分脚本验证

生成 `automated` 或 `hybrid` 模板后，应尽量验证评分脚本是否可执行。验证目标是发现语法错误、字段访问错误和返回格式错误，不等同于证明评分完全正确。

建议验证方式：

1. 从模板中提取 `Automated Checks` 的 Python 代码。
2. 构造最小 mock transcript，至少包含一个成功样例和一个失败样例。
3. 构造临时 workspace，按需要放入 fixture 或输出文件。
4. 调用 `grade(transcript: list, workspace_path: str) -> dict`。
5. 确认返回值是 `dict`，key 与评分项对应，value 是 `0.0` 到 `1.0` 之间的数字。
6. 如果用户提供真实 session/transcript，优先使用真实数据做一次回放验证。

评分脚本应只使用 Python 标准库，优先容错解析 transcript 中的 `message`、`toolCall`、`toolResult`、`text`、`arguments` 等常见结构。

`grade()` 默认返回 criterion-score mapping，例如 `{ "tool_used": 1.0, "argument_correct": 0.5 }`。key 应对应 `Grading Criteria`，value 为 `0.0` 到 `1.0`，不要默认返回聚合后的 `score/max_score/breakdown/notes` 包装对象。

## Timeout 建议

| 场景 | 建议 timeout |
| --- | ---: |
| 简单文本或单次工具调用 | 60s |
| 需要探索工具或读取少量文件 | 120s |
| 多步骤工具调用、跨文件分析 | 180s |
| 长会话 baseline 或复杂诊断 | 300s+ |

## 评分逻辑要求

评分逻辑应该：

- 使用 transcript、tool call、tool result、文件内容等可验证证据。
- 返回 criterion-score mapping，key 对应 `Grading Criteria`，value 为 `0.0` 到 `1.0`。
- 对关键失败原因应体现在评分项命名、扣分逻辑、`LLM Judge Rubric` 或模板说明中。
- 兼容合理的等价实现，不把唯一命令路径写死。
- 对缺失证据明确扣分。

评分逻辑不应该：

- 依赖不可访问的本地私有路径。
- 假设某个内部目录一定存在。
- 只根据最终自然语言回答打满分。
- 在评分中执行破坏性命令。

## 最终检查清单

- Prompt 是真实用户需求，没有泄露答案。
- Expected Behavior 描述了期望行为和关键参数。
- `Grading Criteria`、`Automated Checks` 或 `LLM Judge Rubric` 能从 transcript 或产物中找到证据。
- timeout 与任务复杂度匹配。
- 文件名、id、name 稳定且语义清晰。
- 所有外部输入、fixture、session reference 都已说明。
- 对外内容只使用 ClawBench 名称。
