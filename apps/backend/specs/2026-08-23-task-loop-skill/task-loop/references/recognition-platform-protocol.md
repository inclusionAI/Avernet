# task-recognition Skill · 平台层接口协议

> 本文档定义平台层与 task-recognition Skill 之间的交互协议。
> SKILL.md 是给 Bot 引擎读的指令（定义 Skill 怎么读标记、怎么输出标记）；
> 本文档是给平台层开发读的规范（定义平台层怎么触发 Skill、怎么注入标记、怎么解析输出、怎么接管后续动作）。

---

## 一、平台→Skill：任务来源

当前版本只保留两类任务来源：

| 路径 | 来源 | 触发方式 | Skill 行为 |
|---|---|---|---|
| 路径1 | 升级版 `/task` 消息指令 | 用户消息以 `/task` 开头 | 进入任务提交流程；先做多任务识别，单任务进入四要素追问 |
| 路径4 | `[RESUME_TASK]` 暂存任务/草稿回传 | 平台注入 `[RESUME_TASK]` + 任务信息 | 不重新做入口判断；读取任务信息后判断四要素完整度 |

以下来源已移除，不再触发 Skill：

| 已移除来源 | 当前处理方式 |
|---|---|
| `[EXPLICIT_TASK]` | 不再作为 Skill 入口；如仍有按钮入口，平台应转换为 `/task ...` 消息指令 |
| 自然语言明说“发起任务 / 做个任务 / 转任务”等 | 不再自动触发；用户需发送 `/task ...` 消息指令 |

---

## 二、路径1：升级版 `/task` 消息指令

### 2.1 动态任务消息指令

```text
/task xxx需求描述
```

| 项 | 说明 |
|---|---|
| 触发场景 | 用户发起动态任务 |
| 识别规则 | 用户消息以 `/task` 开头，且 `/task` 后第一个参数不是 `workflow_id='xxxxxxx'` |
| 任务模式 | 动态任务 |
| 需求描述 | `/task` 后的全部文本 |
| Skill 行为 | 从需求描述提取 goal / deliverables / acceptance_criteria / constraints / resources；缺失则追问 |

示例：

```text
/task 修一下 PR #1243 的命名问题，要 CI 通过，不动接口逻辑
```

### 2.2 工作流任务消息指令

```text
/task workflow_id='xxxxxxx' xxx需求描述
```

| 项 | 说明 |
|---|---|
| 触发场景 | 用户发起指定工作流任务 |
| 识别规则 | 用户消息以 `/task` 开头，且 `/task` 后第一个参数符合 `workflow_id='xxxxxxx'` |
| 任务模式 | 工作流任务 |
| workflow_id | 提取引号内的值，写入结构化字段 `workflow_id`；不要写入 `resources` |
| 需求描述 | `workflow_id='xxxxxxx'` 之后的文本 |
| Skill 行为 | 从需求描述提取四要素；workflow_id 只作为工作流执行字段，不替代四要素、不作为关联资源展示 |

示例：

```text
/task workflow_id='wf_pr_rename_001' 修一下 PR #1243 的命名问题，要 CI 通过，不动接口逻辑
```

解析结果要点：

```text
task_type: workflow
workflow_id: wf_pr_rename_001
resources: []
需求描述:
  修一下 PR #1243 的命名问题，要 CI 通过，不动接口逻辑
```

### 2.3 `/task` 无描述

```text
/task
```

Skill 行为：goal 留空进入 `missing_fields`，追问“你想做什么任务？能说说具体目标吗？”

---

## 三、路径4：`[RESUME_TASK]` 暂存任务/草稿回传

| 项 | 说明 |
|---|---|
| 触发场景 | ①多任务暂存回传：用户选了任务①，②被暂存，①完成后回传②；②暂存草稿回传：用户点了“暂存”按钮，之后回来继续 |
| 注入方式（多任务回传） | 平台层在上下文中追加 `[RESUME_TASK]` 标记 + 任务概要（goal + source） |
| 注入方式（草稿回传） | 平台层在上下文中追加 `[RESUME_TASK]` 标记 + 完整草案（四要素全齐） |
| Skill 行为 | 四要素全齐 → 直接输出 `task_ready`；四要素不全 → 输出 `task_clarify` 继续追问 |
| 平台层后续 | 等待 Skill 输出草案或确认卡片 |

**注入格式示例（多任务回传，只有概要）**：

```text
[RESUME_TASK]
  生成本月月报
  source: "月报也帮我生成了"
```

**注入格式示例（草稿回传，五要素完整）**：

```text
[RESUME_TASK]
  goal: 修复 PR #1243 的命名问题
  deliverables: 代码 PR（命名修正）
  acceptance_criteria: CI 通过
  constraints: 不动接口逻辑
  task_type: dynamic
  resources:
    - https://git.example.com/pr/1243
  source: 用户暂存的草稿
```

---

## 四、Skill→平台：输出标记

平台层解析 Skill 的输出文本，根据标记做后续动作。

### 4.1 AixUI 卡片

| 输出 | 场景 | 平台层动作 |
|---|---|---|
| AixUI `task_ready` 卡片 | 草案完整 | 渲染确认卡片；点「执行」后调用 `POST /api/v1/collaboration/tasks/execute` |
| AixUI `task_clarify` 卡片 | 四要素不全 | 渲染追问卡片 |
| AixUI `task_multi_select` 卡片 | 多任务 | 渲染选择卡片 |

### 4.2 `[待处理任务]`

| 项 | 说明 |
|---|---|
| Skill 输出时机 | 多任务处理中，用户选了其中一个，其余任务输出此标记 |
| 输出格式 | `[待处理任务]` + 任务概要（goal + source） |
| 平台层动作 | 暂存这些任务概要 |
| 回传时机 | 选中任务处理完成后，逐个通过 `[RESUME_TASK]` 回传 |
| 注意 | Skill 无状态，不自行记忆；暂存和回传完全由平台层负责 |


### 4.3 用户点击 `task_ready`「执行」后的接口调用

用户点击确认卡片「执行」后，平台层不再调用 Skill，而是将 `task_ready.task` 转换为任务协作中心 `TaskRequest`，并调用：

```http
POST /api/v1/collaboration/tasks/execute
```

#### 4.3.1 字段映射

| Skill / 平台上下文字段 | `TaskRequest` 字段 | 说明 |
|---|---|---|
| `task.goal` | `task_spec.goal.objective` | 任务目标，必填 |
| `task.goal` 短摘要 | `task_spec.metadata.title` | 平台生成短标题，必填 |
| `task.goal + deliverables + acceptance_criteria + constraints + resources` | `task_spec.metadata.instruction` | 完整执行指令，必填；`resources` 仅包含关联资源，不包含 workflow_id |
| 当前会话背景 / `task.goal` | `task_spec.context.background` | 任务背景，可为空字符串 |
| `task.acceptance_criteria[]` | `task_spec.goal.acceptances[]` | 生成 `ac1/ac2/...`，每项 acceptance 必填 |
| 当前 session / 群 / 父任务 | `task_spec.context.extend_props.teamclaw_context` | 由平台注入 |
| 当前用户 | `owner_user_id` | 后端仍校验登录态 |
| 当前 Bot / 群主 Bot | `owner_bot_id` | 单 Bot 为当前 Bot；协作群为 Master/群主 Bot |
| 当前发起场景 | `source_type` | 单 Bot=`bot`，协作群=`coop_group`，API=`api` |
| `task.task_type` | `execution_config.task_type` | `dynamic` 或 `workflow` |
| `task.workflow_id` | `execution_config.workflow_id` | 仅工作流任务必填；优先读取结构化字段，兼容旧格式 `resources` 兜底 |

#### 4.3.2 execution_config 映射

| 入口消息指令 | `execution_config` |
|---|---|
| `/task xxx需求描述` | `{ "task_type": "dynamic" }` |
| `/task workflow_id='xxxxxxx' xxx需求描述` | `{ "task_type": "workflow", "workflow_id": "xxxxxxx" }` |

约束：

- `task_type=workflow` 时必须提供非空 `execution_config.workflow_id`，来源为结构化 `task.workflow_id`
- `task_type=dynamic` 时不要求 `workflow_id/yaml`，且不应输出结构化 `workflow_id`
- `task_id/status/create_time/finish_time` 是服务端字段，不属于创建请求

#### 4.3.3 动态任务请求示例

```json
{
  "task_spec": {
    "metadata": {
      "title": "修复 PR #1243 命名问题",
      "instruction": "目标：修复 PR #1243 的命名问题
交付物：代码 PR（命名修正）
验收标准：CI 通过
约束：不动接口逻辑"
    },
    "context": {
      "background": "修复 PR #1243 命名问题",
      "extend_props": {
        "teamclaw_context": {
          "main_session_id": "session_xxx",
          "parent_task_id": null
        }
      }
    },
    "goal": {
      "objective": "修复 PR #1243 的命名问题",
      "acceptances": [
        { "id": "ac1", "acceptance": "CI 通过" }
      ]
    }
  },
  "source_type": "bot",
  "owner_user_id": "146836",
  "owner_bot_id": "bot_task_owner_001",
  "execution_config": {
    "task_type": "dynamic"
  }
}
```

#### 4.3.4 工作流任务请求示例

```json
{
  "task_spec": {
    "metadata": {
      "title": "修复 PR #1243 命名问题",
      "instruction": "目标：修复 PR #1243 的命名问题
交付物：代码 PR（命名修正）
验收标准：CI 通过
约束：不动接口逻辑"
    },
    "context": {
      "background": "修复 PR #1243 命名问题",
      "extend_props": {
        "teamclaw_context": {
          "main_session_id": "session_xxx",
          "parent_task_id": null
        }
      }
    },
    "goal": {
      "objective": "修复 PR #1243 的命名问题",
      "acceptances": [
        { "id": "ac1", "acceptance": "CI 通过" }
      ]
    }
  },
  "source_type": "bot",
  "owner_user_id": "146836",
  "owner_bot_id": "bot_task_owner_001",
  "execution_config": {
    "task_type": "workflow",
    "workflow_id": "wf_pr_rename_001"
  }
}
```

#### 4.3.5 execute 返回后的处理

- execute 正常受理后，前端使用返回的 `data.task_id` 进入任务副屏
- 后续通过 `GET /api/v1/collaboration/tasks/dashboard?task_id=<task_id>` 查询任务详情和进度
- `success=true` 只表示产品任务已创建并可查询；任务执行状态以 `data.status` 和 dashboard 返回状态为准

#### 4.3.6 workflow_id 兼容规则

新格式中，`workflow_id` 是结构化字段，不放入 `resources`。平台在处理历史草稿/历史卡片时需要做一次归一化：

```json
// 旧格式
{ "resources": ["workflow_id: wf_pr_rename_001"] }
```

归一化为：

```json
{
  "task_type": "workflow",
  "workflow_id": "wf_pr_rename_001",
  "resources": []
}
```

执行按钮映射时，优先读取结构化 `task.workflow_id`；如果缺失，再从旧格式 `resources` 中兜底解析并从展示资源中移除。

---

## 五、优先级

若同一上下文同时出现 `/task` 和 `[RESUME_TASK]`：

```text
路径1（/task）> 路径4（[RESUME_TASK]）
```

---

## 六、平台层待办事项

| 待办 | 优先级 | 说明 |
|---|---|---|
| Slash 命令解析 | 🔴 高 | 识别 `/task` 前缀，并区分动态任务 / 工作流任务消息指令 |
| workflow_id 解析 | 🔴 高 | 解析 `/task workflow_id='xxxxxxx' xxx需求描述` 中的 workflow_id |
| 标记解析器 | 🔴 高 | 解析 Skill 输出中的 AixUI 卡片和 `[待处理任务]` 文本标记 |
| `[RESUME_TASK]` 注入器 | 🔴 高 | 回传暂存任务/草稿时注入 `[RESUME_TASK]` |
| 确认卡片组件 | 🔴 高 | AixUI `task_ready` 卡片渲染，含执行/丢弃/暂存三个按钮；执行按钮需调用 `POST /api/v1/collaboration/tasks/execute` |
| 草稿暂存 | 🟡 中 | 保存完整草案，回传时注入 `[RESUME_TASK]` + 完整草案 |
| 多任务暂存队列 | 🟡 中 | `[待处理任务]` 的暂存和回传逻辑 |
| 多任务清单 UI | 🟡 中 | 多任务时展示清单让用户选 |
