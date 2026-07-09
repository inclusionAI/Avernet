# WebSocket 协议增强设计：Subagent 归属 + 系统状态 + 通知

> 日期：2026-04-30
> 状态：Approved (v2 — 修正 SDK/UI 对照问题)
> 协议版本：v3（向后兼容扩展）

## 目标

补齐当前 WebSocket 协议与 Claude Code TUI 交互能力的差距，使前端能还原 TUI 的核心体验。

覆盖范围：
- **P0**：Subagent 归属关系、Compaction 状态、Notification 通知、Prompt Suggestions、Tool Progress
- **P1**：API Retry、Rate Limit、Memory Recall、Compact Boundary、Files Persisted、Tool Use Summary
- **P2**（记录，本次不实现）：Hook 事件、Auth 状态、Plugin 安装、Session 状态变更、Elicitation

## 1. AgentContext — Subagent 归属关系

### 1.1 类型定义

```typescript
type AgentContext = {
  parentToolUseId?: string;  // 主键。非空 = 属于 subagent，值 = 启动该 subagent 的 Agent tool ID
  taskId?: string;           // 关联的 task 事件 ID（可选，来自 SDKToolProgressMessage.task_id）
  agentId?: string;          // SDK agent_id（仅 permission/hook 场景可用）
  agentType?: string;        // "general-purpose", "code-reviewer" 等
  agentName?: string;        // 用户自定义名称（Agent tool 的 description）
};
```

**设计依据：** SDK 标识 subagent 的主要机制是 `parent_tool_use_id`（出现在 `SDKAssistantMessage`、`SDKPartialAssistantMessage`、`SDKToolProgressMessage` 上）。`agent_id` 仅出现在 hook 和 permission request 中，覆盖范围窄。`parent_tool_use_id` 为 `null` 表示主进程，非空表示 subagent。

**规则：**
- 主进程的事件：不携带 `agentContext`（或为 `undefined`）
- subagent 内的事件：携带 `agentContext`，`parentToolUseId` 必须非空
- 前端判断：`agentContext?.parentToolUseId` 非空 → 属于 subagent，匹配对应的 Task tool_use 节点

### 1.2 影响的事件

#### tool stream 事件

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "sessionKey": "session:abc",
    "stream": "tool",
    "data": {
      "type": "start",
      "toolCallId": "toolu:001",
      "toolName": "Bash",
      "input": { "command": "npm test" },
      "agentContext": {
        "parentToolUseId": "toolu-agent-001",
        "taskId": "task-abc",
        "agentType": "general-purpose",
        "agentName": "Run test suite"
      }
    }
  }
}
```

#### interaction.requested 事件

```json
{
  "type": "event",
  "event": "interaction.requested",
  "payload": {
    "interactionId": "int:uuid",
    "kind": "exec",
    "subject": { "type": "command", "toolName": "Bash" },
    "agentContext": {
      "parentToolUseId": "toolu-agent-001",
      "agentId": "agent-xyz",
      "agentType": "code-reviewer"
    }
  }
}
```

#### 主进程事件（无 agentContext）

```json
{
  "data": {
    "type": "start",
    "toolCallId": "toolu:002",
    "toolName": "Edit"
  }
}
```

### 1.3 前端树构建规则

1. 收到 `task_started`（带 `toolUseId`）→ 创建 task 树节点，`toolUseId` = `parentToolUseId` 的值
2. 后续 `tool`/`interaction` 事件若 `agentContext.parentToolUseId` 匹配 → 挂为该 task 节点的子节点
3. 无 `agentContext` → 归属主进程（根级）

### 1.4 SubagentTools 聚合（兼容 UI 现有模式）

Claude Code UI 通过 `subagentTools` 数组一次性渲染子工具列表。与 `AgentContext` 实时流式方案共存：

在 `tool` stream 的 `result` phase 中，对 `toolName === 'Task'` 的工具增加 `subagentTools` 聚合字段：

```json
{
  "type": "result",
  "toolCallId": "toolu-agent-001",
  "toolName": "Task",
  "output": "...",
  "subagentTools": [
    {
      "toolId": "toolu-inner-001",
      "toolName": "Read",
      "toolInput": { "file_path": "/repo/src/index.ts" },
      "toolResult": { "content": "file content...", "isError": false },
      "timestamp": 1710000001000
    }
  ]
}
```

`subagentTools` 提供完整快照便于一次性渲染，`AgentContext` 提供实时归属便于流式更新。两者互补。

## 2. System 状态通道

### 2.1 新增 agent stream：`system`

```typescript
type AgentEventStream =
  | ... // 现有
  | 'system';   // 系统状态事件

type AgentSystemData =
  | SystemStatusChange
  | SystemApiRetry
  | SystemRateLimit
  | SystemCompactBoundary
  | SystemFilesPersisted;
```

### 2.2 Compaction 状态（P0）

对应 SDK `SDKStatusMessage`。

```json
{
  "data": {
    "type": "status_change",
    "status": "compacting",
    "compactResult": null,
    "compactError": null
  }
}
```

status 结束时发送 `null`，并携带压缩结果：

```json
{
  "data": {
    "type": "status_change",
    "status": null,
    "compactResult": "success",
    "compactError": null
  }
}
```

压缩失败时：

```json
{
  "data": {
    "type": "status_change",
    "status": null,
    "compactResult": "failed",
    "compactError": "Context too large to compact"
  }
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `status` | `'compacting' \| 'requesting' \| null` | 当前系统状态 |
| `compactResult` | `'success' \| 'failed' \| null` | 压缩结果（仅在 status 回到 null 时有值） |
| `compactError` | `string?` | 压缩失败原因 |

### 2.3 API Retry（P1）

对应 SDK `SDKAPIRetryMessage`。字段名对齐 SDK，统一转 camelCase：

```json
{
  "data": {
    "type": "api_retry",
    "attempt": 2,
    "maxRetries": 3,
    "retryDelayMs": 3000,
    "errorStatus": 529,
    "error": "overloaded"
  }
}
```

| 字段 | 类型 | 说明 | SDK 原字段 |
|---|---|---|---|
| `attempt` | `number` | 当前重试次数 | `attempt` |
| `maxRetries` | `number` | 最大重试次数 | `max_retries` |
| `retryDelayMs` | `number` | 预计重试等待时间 | `retry_delay_ms` |
| `errorStatus` | `number?` | HTTP 状态码 | `error_status` |
| `error` | `string` | 错误类型枚举 | `error` (SDKAssistantMessageError) |

### 2.4 Rate Limit 警告（P1）

对应 SDK `SDKRateLimitEvent` + `SDKRateLimitInfo`。SDK 不提供原始用量数字，只提供 `utilization` 和 `status`：

```json
{
  "data": {
    "type": "rate_limit",
    "status": "allowed_warning",
    "rateLimitType": "seven_day",
    "utilization": 0.85,
    "resetsAt": 1710000060000,
    "overageStatus": "allowed",
    "overageResetsAt": 1710000120000
  }
}
```

| 字段 | 类型 | 说明 | SDK 原字段 |
|---|---|---|---|
| `status` | `'allowed' \| 'allowed_warning' \| 'rejected'` | 限额状态 | `rate_limit_info.status` |
| `rateLimitType` | `'five_hour' \| 'seven_day' \| 'seven_day_opus' \| 'seven_day_sonnet' \| 'overage'` | 限额类型 | `rate_limit_info.rateLimitType` |
| `utilization` | `number?` | 用量百分比（0-1） | `rate_limit_info.utilization` |
| `resetsAt` | `number?` | 限额重置时间戳 | `rate_limit_info.resetsAt` |
| `overageStatus` | `'allowed' \| 'allowed_warning' \| 'rejected'?` | 超额状态 | `rate_limit_info.overageStatus` |
| `overageResetsAt` | `number?` | 超额重置时间戳 | `rate_limit_info.overageResetsAt` |

### 2.5 Compact Boundary（P1）

对应 SDK `SDKCompactBoundaryMessage`。提供压缩元数据：

```json
{
  "data": {
    "type": "compact_boundary",
    "trigger": "auto",
    "preTokens": 185000,
    "postTokens": 45000,
    "durationMs": 1200,
    "compactedTurns": 12
  }
}
```

| 字段 | 类型 | 说明 | SDK 原字段 |
|---|---|---|---|
| `trigger` | `'manual' \| 'auto'` | 压缩触发方式 | `compact_meta.trigger` |
| `preTokens` | `number` | 压缩前 token 数 | `compact_meta.pre_tokens` |
| `postTokens` | `number?` | 压缩后 token 数 | `compact_meta.post_tokens` |
| `durationMs` | `number?` | 压缩耗时 | `compact_meta.duration_ms` |
| `compactedTurns` | `number` | 被压缩的轮次数 | 推算值 |

前端行为：在消息流中对应位置插入 "--- context compacted (185k → 45k tokens) ---" 分割线。

### 2.6 Files Persisted（P1）

对应 SDK `SDKFilesPersistedEvent`：

```json
{
  "data": {
    "type": "files_persisted",
    "files": [
      { "filename": "src/index.ts", "fileId": "file-uuid-1" }
    ],
    "failed": [
      { "filename": "src/locked.ts", "error": "Permission denied" }
    ],
    "processedAt": "2024-01-01T00:00:05.000Z"
  }
}
```

| 字段 | 类型 | 说明 | SDK 原字段 |
|---|---|---|---|
| `files` | `{ filename: string, fileId: string }[]` | 成功持久化文件 | `files` |
| `failed` | `{ filename: string, error: string }[]` | 失败文件 | `failed` |
| `processedAt` | `string` | 处理时间 ISO | `processed_at` |

## 3. Notification 通知

### 3.1 新增顶层事件：`notification`

Notification 是全局性 toast 消息，不属于某个 run 的 agent 事件流。对应 SDK `SDKNotificationMessage`。

```json
{
  "type": "event",
  "event": "notification",
  "payload": {
    "key": "notif-uuid",
    "text": "File saved successfully",
    "priority": "medium",
    "color": "green",
    "timeoutMs": 5000,
    "sessionKey": "session:abc",
    "runId": "run-uuid"
  }
}
```

### 3.2 字段定义

| 字段 | 类型 | 必填 | 说明 | SDK 原字段 |
|---|---|---|---|---|
| `key` | `string` | 是 | 唯一标识，用于去重和关闭 | `key` |
| `text` | `string` | 是 | 显示文本 | `text` |
| `priority` | `'low' \| 'medium' \| 'high' \| 'immediate'` | 是 | 前端决定展示行为 | `priority` |
| `color` | `string` | 否 | 颜色提示 | `color` |
| `timeoutMs` | `number` | 否 | 自动关闭时间 | `timeout_ms` |
| `sessionKey` | `string` | 否 | 关联的 session | `session_id` |
| `runId` | `string` | 否 | 关联的 run | — |

### 3.3 前端行为

| priority | 建议行为 |
|---|---|
| `low` | 状态栏小字，或不显示 |
| `medium` | Toast 气泡，自动消失 |
| `high` | Toast 气泡，需手动关闭 |
| `immediate` | 模态或固定位置强提醒 |

## 4. Prompt Suggestions

### 4.1 新增顶层事件：`prompt.suggestions`

对应 SDK `SDKPromptSuggestionMessage`。SDK 每次推送单条 suggestion，relay 可选择逐条转发或缓冲后批量推送。协议支持两种模式。

```json
{
  "type": "event",
  "event": "prompt.suggestions",
  "payload": {
    "runId": "run-uuid",
    "sessionKey": "session:abc",
    "suggestions": [
      { "text": "Run the test suite to verify" }
    ]
  }
}
```

### 4.2 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `runId` | `string` | 是 | 产生建议的 run |
| `sessionKey` | `string` | 是 | 所属 session |
| `suggestions` | `Suggestion[]` | 是 | 建议列表（单条或多条） |

```typescript
type Suggestion = {
  text: string;  // 建议的 prompt 文本
};
```

**与初版差异：** 移除 `type` 字段（`'action' | 'question' | 'explore'`），因为 SDK 只提供 `suggestion: string`，没有类型分类。前端如需区分可自行从文本内容推断。

### 4.3 前端行为

1. 收到 `prompt.suggestions` → 追加到当前 suggestions 列表
2. 用户点击某条 → 前端将 `text` 作为 `chat.send` 的 `message` 发送
3. 新一轮 `chat.send` 开始后 → 清除旧的 suggestions
4. 同一 `runId` 的多次 `prompt.suggestions` → 追加（非替换）

## 5. Tool Progress

### 5.1 扩展 AgentToolPhase

```typescript
type AgentToolPhase = 'start' | 'update' | 'progress' | 'result' | 'summary';
```

### 5.2 progress phase

对应 SDK `SDKToolProgressMessage`。SDK 提供的字段：

```typescript
type SDKToolProgressMessage = {
  type: 'tool_progress';
  tool_use_id: string;
  tool_name: string;
  parent_tool_use_id: string | null;  // ← 已映射到 agentContext
  elapsed_time_seconds: number;
  task_id?: string;                    // ← 已映射到 agentContext
};
```

协议事件：

```json
{
  "data": {
    "type": "progress",
    "toolCallId": "toolu:001",
    "toolName": "Bash",
    "progress": {
      "elapsedSeconds": 12
    },
    "agentContext": {
      "parentToolUseId": "toolu-agent-001",
      "taskId": "task-abc"
    }
  }
}
```

### 5.3 progress 子对象

| 字段 | 类型 | 必填 | 说明 | SDK 原字段 |
|---|---|---|---|---|
| `elapsedSeconds` | `number` | 是 | 已耗时秒数 | `elapsed_time_seconds` |

**与初版差异：** 移除 `message`/`percentage`/`stage`，因为 SDK `SDKToolProgressMessage` 不提供这些字段。只提供 `elapsed_time_seconds`。前端可自行用 `elapsedSeconds` 渲染 spinner + 时间显示。

### 5.4 与现有 phase 的关系

```
start → [progress]* → [update]* → result → [summary]?
```

- `update`：输入/输出内容的增量推送（已有）
- `progress`：执行状态的元信息推送（新增）
- 两者可交错出现

### 5.5 前端行为

1. 收到 `progress` → 在 tool 卡片上显示 spinner + 已耗时
2. 收到 `result` → 进度消失，显示最终结果

## 6. Memory Recall

### 6.1 新增 agent stream：`memory`

对应 SDK `SDKMemoryRecallMessage`：

```json
{
  "data": {
    "type": "recall",
    "mode": "select",
    "memories": [
      { "path": "user_role.md", "scope": "personal", "content": null },
      { "path": "project_auth.md", "scope": "team", "content": "Auth rewrite driven by compliance" }
    ]
  }
}
```

### 6.2 字段定义

| 字段 | 类型 | 说明 | SDK 原字段 |
|---|---|---|---|
| `mode` | `'select' \| 'synthesize'` | 召回方式 | `mode` |
| `memories` | `MemoryItem[]` | 召回的记忆列表 | `memories` |
| `memories[].path` | `string` | 记忆文件路径 | `path` |
| `memories[].scope` | `'personal' \| 'team'` | 来源作用域 | `scope` |
| `memories[].content` | `string?` | 召回内容（synthesize 模式下） | `content` |

**与初版差异：** `synthesis` → `content`，与 SDK 字段名对齐。

## 7. Tool Use Summary

### 7.1 复用 tool stream，新增 phase `summary`

对应 SDK `SDKToolUseSummaryMessage`。注意：摘要关联的是**多个** tool，不是单个：

```json
{
  "data": {
    "type": "summary",
    "precedingToolUseIds": ["toolu:001", "toolu:002"],
    "summary": "Ran 2 bash commands — all tests passed",
    "agentContext": null
  }
}
```

### 7.2 字段定义

| 字段 | 类型 | 说明 | SDK 原字段 |
|---|---|---|---|
| `precedingToolUseIds` | `string[]` | 摘要覆盖的 tool call ID 列表 | `preceding_tool_use_ids` |
| `summary` | `string` | 摘要文本 | `summary` |

**与初版差异：** 移除 `toolCallId`/`toolName`/`durationMs`，改为 `precedingToolUseIds` 数组。SDK 的 summary 是 1:N 关系，不绑定单个 tool。

前端行为：收到 `summary` → 在最后一个对应的 tool 卡片下方附加一行简短总结文字。

## 8. Task 事件补充

### 8.1 补充缺失字段

现有 `AgentTaskData` 缺少 SDK 提供的关键字段：

```typescript
type AgentTaskData = {
  type: 'task_started' | 'task_progress' | 'task_notification' | 'task_updated';
  taskId: string;
  toolUseId?: string;
  // task_started 补充
  taskType?: string;         // SDK: task_type — 'agent' | 'workflow' 等
  workflowName?: string;     // SDK: workflow_name
  prompt?: string;           // SDK: prompt — subagent 收到的原始 prompt
  // task_progress 补充
  lastToolName?: string;     // SDK: last_tool_name — 最近执行的工具名
  summary?: string;          // SDK: summary — 进度摘要
  // 现有
  status?: string;
  description?: string;
  outputFile?: string;
  usage?: { totalTokens: number; toolUses: number; durationMs: number };
  patch?: {
    status?: string;
    description?: string;
    endTime?: number;
    totalPausedMs?: number;
    error?: string;
    isBackgrounded?: boolean;
  };
};
```

### 8.2 新增字段来源

| 字段 | SDK 原字段 | 出现在 |
|---|---|---|
| `taskType` | `task_type` | `task_started` |
| `workflowName` | `workflow_name` | `task_started` |
| `prompt` | `prompt` | `task_started` |
| `lastToolName` | `last_tool_name` | `task_progress` |
| `summary` | `summary` | `task_progress` |

## 9. agent stream 分类总表（更新后）

| stream | 说明 | 变更 |
|---|---|---|
| `lifecycle` | run 生命周期 | - |
| `message` | assistant 消息 | - |
| `content_block` | 内容块 | - |
| `thinking` | thinking 流式输出 | - |
| `tool` | 工具调用 | 新增 `progress` / `summary` phase；`result` phase 对 Task 工具新增 `subagentTools` |
| `todo` | 任务面板 | - |
| `task` | 子 Agent 生命周期 | 补充 `taskType`/`workflowName`/`prompt`/`lastToolName`/`summary` |
| `command_output` | 命令输出流 | - |
| `assistant` | 统计信息 | - |
| `phase` | Agent 模式切换 | - |
| **`system`** | **系统状态** | **新增** |
| **`memory`** | **记忆召回** | **新增** |

## 10. 顶层事件总表（更新后）

| event | 说明 | 变更 |
|---|---|---|
| `chat` | 文本流式输出 | - |
| `agent` | 结构化事件流 | - |
| `tick` | 心跳 | - |
| `connect.challenge` | 握手 challenge | - |
| `interaction.requested` | 用户交互请求 | 新增 `agentContext` |
| `interaction.resolved` | 用户交互完成 | - |
| **`notification`** | **全局通知（toast/snackbar）** | **新增** |
| **`prompt.suggestions`** | **后续问题建议** | **新增** |

## 11. 向后兼容性

- `AgentContext` 是 optional 字段，旧前端忽略不影响
- `system` / `memory` 是新 stream，旧前端不监听不报错
- `notification` / `prompt.suggestions` 是新顶层事件，旧前端不处理不报错
- `progress` / `summary` 是 tool stream 新 phase，旧前端按 unknown type 忽略
- `subagentTools` 是 tool result 的新增字段，旧前端忽略不影响
- 协议版本保持 v3

## 12. 命名约定

协议字段统一使用 camelCase（与现有 websocket-protocol.md 一致）。SDK 原字段为 snake_case 的，映射关系在各节字段定义表中标注。

## 13. P2 记录（本次不实现）

| 缺失项 | SDK 事件 | 说明 |
|---|---|---|
| Hook 事件 | `SDKHook*Message` | 自定义 hook 执行状态 |
| Auth 状态 | `SDKAuthStatusMessage` | 登录态变化 |
| Plugin 安装 | `SDKPluginInstallMessage` | 插件管理 |
| Session 状态变更 | `SDKSessionStateChangedMessage` | 会话级状态 |
| Elicitation | `SDKElicitationCompleteMessage` | 表单提交确认 |
| Local Command Output | `SDKLocalCommandOutputMessage` | 本地命令输出 |
| Dialog Request | `SDKControlRequestUserDialogRequest` | 自定义对话框 |
| Context Usage | `SDKControlGetContextUsageRequest` | 上下文窗口用量查询 |

## 附录 A：v1 → v2 修正清单

| # | 问题 | 修正 |
|---|---|---|
| 1 | AgentContext 以 `taskId` 为主键 | 改为以 `parentToolUseId` 为主键，`taskId` 为辅 |
| 2 | Rate Limit 结构自造 `current`/`limit` | 映射 SDK `SDKRateLimitInfo`：`status`/`utilization`/`rateLimitType` |
| 3 | API Retry 字段名不匹配 SDK | `maxAttempts`→`maxRetries`，`retryAfterMs`→`retryDelayMs`，`reason`→`errorStatus`+`error` |
| 4 | Compact Boundary 过于简化 | 补充 `trigger`/`preTokens`/`postTokens`/`durationMs` |
| 5 | Status 缺少 compaction 结果 | 新增 `compactResult`/`compactError` |
| 6 | Files Persisted 结构不符 SDK | `path`/`action`→`filename`/`fileId`，新增 `failed` 数组 |
| 7 | Memory Recall 字段名错误 | `synthesis`→`content` |
| 8 | Tool Use Summary 绑定单个 toolCallId | 改为 `precedingToolUseIds` 数组 |
| 9 | Prompt Suggestion 假设数组批量推送 | 改为逐条追加模式，移除 `type` 字段 |
| 10 | Task 事件缺少关键字段 | 补充 `taskType`/`workflowName`/`prompt`/`lastToolName`/`summary` |
| 11 | UI 用 `subagentTools` 聚合而非纯树匹配 | Task 工具 result 增加 `subagentTools` 字段 |