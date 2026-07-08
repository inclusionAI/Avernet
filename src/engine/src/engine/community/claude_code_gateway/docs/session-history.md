# 方案：将工具执行信息持久化到会话历史

## 问题

当前 `SessionHistoryMessage` 只存储扁平文本 `{role, text}`，所有工具调用（Bash/Edit/Write/Read/AskUserQuestion）、工具结果、thinking 仅作为 WebSocket 事件实时发送，**从不写入 history**。导致：

1. 后续轮次 Claude 看不到之前的工具使用记录（上下文丢失）
2. `chat.history` API 返回的消息缺少工具执行细节（前端无法还原完整会话）
3. 会话恢复后无法重建工具调用上下文
4. thinking 内容丢失，无法回溯 Agent 的推理过程

## 参考：OpenClaw 的历史消息结构

OpenClaw 的历史消息将工具结果详情放入 `metadata`，`content` 为 null 或摘要文本：

```json
{
    "id": "agent:main:session:05b462ec:user:401148_2",
    "session_id": "agent:main:session:05b462ec:user:401148",
    "role": "tool_result",
    "content": null,
    "metadata": {
        "tool_name": "exec",
        "tool_call_id": "fc-28a9a931-fae5-44a7-941c-b48e15464a7b",
        "success": true,
        "result": "{\n  \"status\": \"error\",\n  \"tool\": \"exec\",\n  \"error\": \"Refusing to traverse symlink...\"\n}",
        "arguments": null
    },
    "gmt_created": "2026-04-28T03:07:29.845000+00:00"
}
```

**关键设计**：
- `role` 直接用 `tool_result` 标识消息类型，不混在 `assistant` 消息中
- 工具参数和结果详情放在 `metadata`，`content` 为 null
- 通过 `tool_call_id` 关联 `tool_use` 和 `tool_result`

## 设计方案

### 核心思路

1. 扩展 `SessionHistoryMessage`，增加 `metadata` 字段
2. tool 类消息 `content` 为 null，工具参数/结果放入 `metadata`
3. thinking 消息 `content` 为 null，thinking 文本放入 `metadata`
4. **向后兼容**——没有 `metadata` 的旧消息仍然只有 `text` 字段，正常工作
5. interaction 不单独保存——交互型工具产生 `tool_use` + `tool_result`，以 tool 类型展示即可

### 1. 类型定义（`src/types.ts`）

```typescript
// 工具调用 metadata
export type ToolUseMeta = {
  toolCallId: string;
  toolName: string;
  input: Record<string, unknown>;
  title?: string;           // 人类可读标题，如 "Edit file"、"Execute command"
  description?: string;     // 简要描述，如文件路径或命令内容
  subject?: InteractionSubject; // 操作对象详情，用于前端还原 UI（diff 预览等）
};

// 工具结果 metadata
export type ToolResultMeta = {
  toolCallId: string;
  toolName: string;
  output: string;
  exitCode?: number;
  durationMs?: number;
  isError?: boolean;
  isSynthetic?: boolean;
  title?: string;
  description?: string;
  subject?: InteractionSubject;
};

// thinking metadata
export type ThinkingMeta = {
  text: string;
};

export type SessionHistoryMessage = {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool_use' | 'tool_result' | 'thinking';
  text: string;           // 向后兼容：纯文本摘要
  timestamp: string;
  runId?: string;
  content?: HistoryContentBlock[] | null;  // tool/thinking 消息为 null
  metadata?: ToolUseMeta | ToolResultMeta | ThinkingMeta;  // 工具详情、thinking 文本
};
```

**设计决策**：
- `text` 保留，向后兼容。tool 类消息的 `text` 使用 OpenClaw 格式：`\n\n<tool>{"name": "exec", "success": true, "running": false}</tool>\n\n`
- `content` 可选，tool_use / tool_result / thinking 消息设为 null
- `metadata` 存放工具参数/结果详情或 thinking 文本
- `tool_use` 使用独立 `role`（与 OpenClaw 一致），不混在 assistant 消息中
- **不存储 interaction**——AskUserQuestion 等交互型工具产生 `tool_use` + `tool_result`，以 tool 类型展示

### 2. 写入时机（在 `orchestrator-bridge.ts` 的事件流中收集，在 `chat.ts` 完成时批量写入）

不逐事件写入的原因：
- 避免频繁磁盘 I/O
- 保证同一 run 的消息原子性（中断/错误时可以选择不写入不完整的工具记录）
- 与现有模式一致（用户消息在 run 开始前写入，助手消息在完成后写入）

#### 具体改动

**`orchestrator-bridge.ts`**：

在 `bridgeOrchestratorToGateway` 中新增收集器：

```typescript
type CollectedEvent =
  | { kind: 'tool_use';  { toolCallId: string; toolName: string; input: Record<string, unknown>; title?: string; description?: string; subject?: InteractionSubject } }
  | { kind: 'tool_result';  { toolCallId: string; toolName: string; output: string; exitCode?: number; durationMs?: number; isError?: boolean; title?: string; description?: string; subject?: InteractionSubject } }
  | { kind: 'thinking'; fullText: string };

// 在 bridge 函数内
const collectedEvents: CollectedEvent[] = [];
```

在各事件处理分支中收集：

| 事件 | 收集内容 |
|------|---------|
| `toolEnd` | `{ kind: 'tool_use',  { toolCallId, toolName, input, title, description, subject } }` — subject/title/description 由 `deriveToolMeta()` 从 input 推导 |
| `commandOutput` (phase='end') | `{ kind: 'tool_result',  { toolCallId, toolName, output, exitCode, durationMs, isError } }` |
| `lifecycle` (phase='end') | 推入 thinking + 回填 tool_result 的 toolName/title/description/subject（从匹配的 tool_use 复制） |
| `thinkingDelta`（生命周期结束时） | `{ kind: 'thinking', fullText }` |

**`deriveToolMeta` 函数**：根据工具名和输入推导 `subject`/`title`/`description`，与 `buildExecInteraction` 逻辑对齐：

| 工具 | title | description | subject |
|------|-------|-------------|---------|
| Bash | "Execute command" | 命令内容 | `{ type: 'command', command, cwd }` |
| Edit | "Edit file" | 文件路径 | `{ type: 'file', filePath, old_string, new_string, operation: 'edit' }` |
| Write | "Write file" | 文件路径 | `{ type: 'file', filePath, operation: 'create' }` |
| Read | "Read file" | 文件路径 | `{ type: 'file', filePath, operation: 'read' }` |
| AskUserQuestion | "Ask user" | prompt 内容 | `{ type: 'tool' }` |
| ExitPlanMode | "Exit plan mode" | plan/summary | `{ type: 'mode', fromMode, toMode }` |
| 其他 | 工具名 | "" | `{ type: 'tool' }` |

Bridge 返回值新增 `getCollectedEvents`：

```typescript
return {
  getLastStreamedText: () => lastStreamedText,
  wasInterrupted: () => interrupted,
  getCollectedEvents: () => collectedEvents,  // 新增
};
```

**`chat.ts` handleChatSend**：

run 完成后（成功路径），将收集到的事件转为 history 消息写入：

```typescript
const collectedEvents = getCollectedEvents();

for (const event of collectedEvents) {
  if (event.kind === 'tool_use') {
    const toolJson = JSON.stringify({ name: event.data.toolName, input: event.data.input });
    deps.store.appendHistory(sessionKey, {
      id: randomUUID(),
      role: 'tool_use',
      text: `\n\n<tool>${toolJson}</tool>\n\n`,
      timestamp: nowIso(),
      runId,
      content: null,
      metadata: {
        toolCallId: event.data.toolCallId,
        toolName: event.data.toolName,
        input: event.data.input,
      },
    });
  } else if (event.kind === 'tool_result') {
    const toolJson = JSON.stringify({ name: event.data.toolName, success: !event.data.isError, running: false });
    deps.store.appendHistory(sessionKey, {
      id: randomUUID(),
      role: 'tool_result',
      text: `\n\n<tool>${toolJson}</tool>\n\n`,
      timestamp: nowIso(),
      runId,
      content: null,
      metadata: {
        toolCallId: event.data.toolCallId,
        toolName: event.data.toolName,
        output: event.data.output,
        exitCode: event.data.exitCode,
        durationMs: event.data.durationMs,
        isError: event.data.isError,
      },
    });
  } else if (event.kind === 'thinking') {
    deps.store.appendHistory(sessionKey, {
      id: randomUUID(),
      role: 'thinking',
      text: event.fullText,
      timestamp: nowIso(),
      runId,
      content: null,
      metadata: {
        text: event.fullText,
      },
    });
  }
}
// 然后写入 assistant text（已有逻辑）
```

### 3. 交互型工具的处理

**不需要单独保存 interaction 消息**。原因：

- AskUserQuestion → 产生 `tool_use`(AskUserQuestion) + `tool_result`（用户回答），以 tool 类型展示
- Bash/Edit/Write/Read → 产生 `tool_use` + `tool_result`（执行结果），以 tool 类型展示
- ExitPlanMode → 产生 `tool_use` + `tool_result`，以 tool 类型展示

交互审批的决策（allow/deny/submit/cancel）体现在 `tool_result` 的内容中：
- 允许执行：`tool_result` 包含实际执行结果
- 拒绝执行：`tool_result.isError = true`，output 包含拒绝原因
- AskUserQuestion 回答：`tool_result` 包含用户选择/回答

因此 interaction 不需要额外的 history 消息类型，tool_use + tool_result 已足够表达。

### 4. 上下文构建增强（`chat-orchestrator.ts`）

扩展 `OrchestratorHistoryEntry` 和 `buildConversationContext`，识别 `metadata`：

```typescript
export type OrchestratorHistoryEntry = {
  role: HistoryRole;
  text: string;
  senderName?: string;
  metadata?: ToolUseMeta | ToolResultMeta | ThinkingMeta;
};
```

在 `buildConversationContext` 中，根据消息类型格式化为可读文本：

```typescript
function formatHistoryEntry(m: OrchestratorHistoryEntry): string {
  const meta = m.metadata;

  // tool_use
  if (meta && 'toolName' in meta && 'input' in meta) {
    const json = JSON.stringify({ name: meta.toolName, input: meta.input });
    return `\n<tool>${json}</tool>\n`;
  }

  // tool_result
  if (meta && 'output' in meta) {
    const result = JSON.stringify({ name: meta.toolName, success: !meta.isError, output: meta.output, exitCode: meta.exitCode, durationMs: meta.durationMs });
    return `\n<tool_result>${result}</tool_result>\n`;
  }

  // thinking
  if (meta && 'text' in meta && !('toolName' in meta)) {
    return `\n<thinking>${meta.text}</thinking>\n`;
  }

  // fallback
  return m.text;
}
```

在 `toOrchestratorHistory` 中传递 `metadata`：

```typescript
function toOrchestratorHistory(history: SessionHistoryMessage[] | undefined): OrchestratorHistoryEntry[] {
  if (!history) return [];
  return history
    .filter(m => m.role === 'user' || m.role === 'assistant' || m.role === 'tool_use' || m.role === 'tool_result' || m.role === 'thinking')
    .map(m => ({ role: m.role, text: m.text, meta m.metadata }));
}
```

### 5. chat.history API 增强（`chat.ts` handleChatHistory）

返回时包含 `content` 和 `metadata`，使前端可以渲染完整的工具调用和思考历史：

```typescript
const messages = (binding?.history ?? []).slice(-Math.max(1, limit)).map(m => ({
  id: m.id,
  role: m.role,
  text: m.text,
  content: m.content ?? (m.role === 'user' || m.role === 'assistant' ? [{ type: 'text' as const, text: m.text }] : null),
  timestamp: m.timestamp,
  meta m.metadata ? { runId: m.runId, ...m.metadata } : { runId: m.runId },
}));
```

### 6. CLI bridge 的工具结果处理

CLI bridge 的 `commandOutput` 事件已包含 `toolCallId`、`output`、`exitCode`、`durationMs`，无需额外改动——bridge 层的收集逻辑对两种 bridge 都生效。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/types.ts` | 新增 `ToolUseMeta`、`ToolResultMeta`、`ThinkingMeta` 类型（含 `title`/`description`/`subject` 字段）；扩展 `SessionHistoryMessage` 加 `content?: ... \| null`、`metadata?` 字段，`role` 增加 `'tool_use'`、`'tool_result'`、`'thinking'` |
| `src/chat-orchestrator.ts` | 扩展 `OrchestratorHistoryEntry` 加 `metadata?`；`buildConversationContext` 识别 `metadata` 中的 tool/thinking；新增 `formatHistoryEntry` |
| `src/gateway/orchestrator-bridge.ts` | `BridgeOrchestratorFn` 返回类型加 `getCollectedEvents`；新增 `deriveToolMeta()` 推导 `title`/`description`/`subject`；在事件流中收集 `tool_use`/`tool_result`/`thinking`；lifecycle end 时回填 tool_result 的 subject |
| `src/gateway/handlers/chat.ts` | `toOrchestratorHistory` 传递 `metadata`；`handleChatSend` 完成时将收集的事件写入 history（content: null, metadata 含 title/description/subject）；`handleChatHistory` 返回 `content` 和 `metadata` |
| `test/store.test.ts` | 验证带 `metadata` 的 history 消息的存取 |

## 向后兼容性

1. **存储**：`content` 和 `metadata` 是可选字段，旧数据没有这些字段仍然正常加载
2. **上下文构建**：无 `metadata` 时 fallback 到 `text`，行为与现在完全一致
3. **chat.history API**：无 `content` 时按角色自动生成（user/assistant 生成文本块，tool/thinking 为 null），前端无需改动
4. **SDK resume**：工具历史不影响 SDK 的 `resumeSessionId` 机制（SDK 自己维护上游对话状态）
5. **role 扩展**：新增 `'tool_use'`、`'tool_result'`、`'thinking'` 不影响旧的 `'user' | 'assistant' | 'system'` 消息