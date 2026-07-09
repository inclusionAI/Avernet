# WebSocket 协议增强实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增强 WebSocket 协议，支持 subagent 归属关系、系统状态通道、通知、建议提示、工具进度等，使前端能还原 Claude Code TUI 核心体验。

**Architecture:** 从类型层（types.ts）开始，逐步扩展 SDK bridge 事件捕获、orchestrator 事件传递、gateway bridge 事件转发，最后更新协议文档。每层向后兼容。

**Tech Stack:** TypeScript, claude-agent-sdk, ws (WebSocket), egg-bin/mocha (testing)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/types.ts` | Modify | 新增 AgentContext、AgentSystemData、AgentMemoryData 等类型；扩展 AgentToolPhase、AgentTaskData、InteractionRequestedEvent |
| `src/claude-sdk-bridge.ts` | Modify | 捕获 SDK 新事件（tool_progress、tool_use_summary、status、api_retry、rate_limit_event、compact_boundary、files_persisted、memory_recall、notification、prompt_suggestion、task_updated）；传递 parent_tool_use_id / agent_id / task_id 到 handler |
| `src/chat-orchestrator.ts` | Modify | 扩展 OrchestratorEvent 联合类型，新增 system / memory / toolProgress / toolSummary / notification / promptSuggestion 事件 |
| `src/gateway/orchestrator-bridge.ts` | Modify | 处理新 OrchestratorEvent kind，转发到对应的 agent stream 或顶层事件 |
| `src/gateway/connection-context.ts` | Modify | 新增 notificationEvent / promptSuggestionEvent 辅助方法 |
| `src/interaction/builders.ts` | Modify | interaction builder 接受 agentContext 参数 |
| `src/interaction/types.ts` | Modify | PendingInteraction 增加 agentContext 字段 |
| `src/interaction/emitters.ts` | Modify | emitInteractionRequested 携带 agentContext |
| `docs/websocket-protocol.md` | Modify | 更新协议文档 |
| `test/protocol-enhancement.test.ts` | Create | 端到端协议增强测试 |

---

### Task 1: 扩展类型定义

**Files:**
- Modify: `src/types.ts:55-67` (AgentEventStream)
- Modify: `src/types.ts:100-129` (AgentToolData, AgentToolPhase)
- Modify: `src/types.ts:165-183` (AgentTaskData)
- Modify: `src/types.ts:292-310` (InteractionRequestedEvent)
- Test: `test/protocol-enhancement.test.ts`

- [ ] **Step 1: 写类型测试**

```typescript
// test/protocol-enhancement.test.ts
import assert from 'node:assert/strict';

describe('Protocol Enhancement Types', () => {
  describe('AgentContext', () => {
    it('should allow undefined agentContext for main process', () => {
      const toolData = {
        type: 'start' as const,
        toolCallId: 'toolu:001',
        toolName: 'Bash',
        input: { command: 'npm test' },
      };
      assert.equal(toolData.toolCallId, 'toolu:001');
    });

    it('should carry parentToolUseId for subagent context', () => {
      const agentContext = {
        parentToolUseId: 'toolu-agent-001',
        taskId: 'task-abc',
        agentType: 'general-purpose',
      };
      assert.equal(agentContext.parentToolUseId, 'toolu-agent-001');
    });
  });

  describe('AgentToolPhase', () => {
    it('should include progress and summary phases', () => {
      const phases: string[] = ['start', 'update', 'progress', 'result', 'summary'];
      assert.ok(phases.includes('progress'));
      assert.ok(phases.includes('summary'));
    });
  });

  describe('System stream data', () => {
    it('should define status_change with compactResult', () => {
      const data = {
        type: 'status_change' as const,
        status: 'compacting' as const,
        compactResult: null as string | null,
        compactError: null as string | null,
      };
      assert.equal(data.status, 'compacting');
    });

    it('should define api_retry with correct fields', () => {
      const data = {
        type: 'api_retry' as const,
        attempt: 2,
        maxRetries: 3,
        retryDelayMs: 3000,
        errorStatus: 529,
        error: 'overloaded' as const,
      };
      assert.equal(data.maxRetries, 3);
    });

    it('should define rate_limit matching SDK structure', () => {
      const data = {
        type: 'rate_limit' as const,
        status: 'allowed_warning' as const,
        rateLimitType: 'seven_day' as const,
        utilization: 0.85,
        resetsAt: 1710000060000,
      };
      assert.equal(data.utilization, 0.85);
    });

    it('should define compact_boundary with trigger and tokens', () => {
      const data = {
        type: 'compact_boundary' as const,
        trigger: 'auto' as const,
        preTokens: 185000,
        postTokens: 45000,
        durationMs: 1200,
        compactedTurns: 12,
      };
      assert.equal(data.trigger, 'auto');
    });

    it('should define files_persisted with failed array', () => {
      const data = {
        type: 'files_persisted' as const,
        files: [{ filename: 'src/index.ts', fileId: 'file-1' }],
        failed: [{ filename: 'src/locked.ts', error: 'Permission denied' }],
        processedAt: '2024-01-01T00:00:05.000Z',
      };
      assert.equal(data.failed.length, 1);
    });
  });

  describe('Memory stream data', () => {
    it('should use content field not synthesis', () => {
      const memory = {
        type: 'recall' as const,
        mode: 'select' as const,
        memories: [
          { path: 'user_role.md', scope: 'personal' as const, content: null },
          { path: 'project_auth.md', scope: 'team' as const, content: 'Auth rewrite' },
        ],
      };
      assert.equal(memory.memories[1].content, 'Auth rewrite');
    });
  });

  describe('Notification event', () => {
    it('should define notification payload', () => {
      const payload = {
        key: 'notif-uuid',
        text: 'File saved',
        priority: 'medium' as const,
        color: 'green',
        timeoutMs: 5000,
      };
      assert.equal(payload.priority, 'medium');
    });
  });

  describe('Prompt suggestion event', () => {
    it('should define suggestion with text only', () => {
      const suggestion = { text: 'Run the test suite' };
      assert.equal(suggestion.text, 'Run the test suite');
    });
  });

  describe('Task data enhancement', () => {
    it('should include taskType, workflowName, prompt on task_started', () => {
      const data = {
        type: 'task_started' as const,
        taskId: 'task-1',
        taskType: 'agent',
        workflowName: 'review',
        prompt: 'Review the code',
      };
      assert.equal(data.taskType, 'agent');
    });

    it('should include lastToolName and summary on task_progress', () => {
      const data = {
        type: 'task_progress' as const,
        taskId: 'task-1',
        lastToolName: 'Bash',
        summary: 'Running tests',
      };
      assert.equal(data.lastToolName, 'Bash');
    });
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `npx egg-bin test test/protocol-enhancement.test.ts 2>&1 | head -30`
Expected: 编译错误 — 新类型尚未定义

- [ ] **Step 3: 在 types.ts 中新增 AgentContext 类型**

在 `src/types.ts` 的 `AgentEventStream` 定义之前添加：

```typescript
/** Agent context for subagent attribution. Present on events originating from a subagent. */
export type AgentContext = {
  /** Primary key. Non-null = event belongs to subagent. Value = the Agent tool_use ID that spawned this subagent. */
  parentToolUseId?: string;
  /** Associated task event ID (from SDKToolProgressMessage.task_id). */
  taskId?: string;
  /** SDK agent_id (only available in permission/hook contexts). */
  agentId?: string;
  /** Agent type name (e.g. "general-purpose", "code-reviewer"). */
  agentType?: string;
  /** User-facing name (Agent tool description). */
  agentName?: string;
};
```

- [ ] **Step 4: 扩展 AgentEventStream — 添加 system 和 memory**

修改 `src/types.ts:55-67`：

```typescript
export type AgentEventStream =
  | 'lifecycle'
  | 'tool'
  | 'assistant'
  | 'thinking'
  | 'command_output'
  | 'interaction'
  | 'mode_transition'
  | 'message'
  | 'content_block'
  | 'todo'
  | 'task'
  | 'system'   // System status events (compaction, retry, rate limit, compact boundary, files persisted)
  | 'memory'   // Memory recall events
  | (string & {});
```

- [ ] **Step 5: 扩展 AgentToolPhase — 添加 progress 和 summary**

修改 `src/types.ts:100`：

```typescript
export type AgentToolPhase = 'start' | 'update' | 'progress' | 'result' | 'summary' | 'task';
```

- [ ] **Step 6: 扩展 AgentToolData — 添加 agentContext、progress、summary 字段**

修改 `src/types.ts:102-129`，在 `AgentToolData` 中添加字段：

```typescript
export type AgentToolData = {
  type: AgentToolPhase;
  toolCallId: string;
  toolName: string;
  input?: Record<string, unknown>;
  partialInput?: string;
  partialOutput?: unknown;
  output?: unknown;
  error?: string;
  requiresInteraction?: boolean;
  interaction?: {
    interactionId: string;
    kind: 'ask_user' | 'exec' | 'mode_switch';
    questions?: InteractionQuestion[];
    options?: Array<{ value: string; label: string; recommended?: boolean }>;
    inputSchema?: Record<string, unknown>;
    uiHints?: Record<string, unknown>;
  };
  task?: {
    taskId: string;
    status: 'in_progress' | 'completed' | 'failed';
    summary: string;
    outputFile?: string;
  };
  /** Subagent context — present when this tool call originates from a subagent. */
  agentContext?: AgentContext;
  /** Tool progress data (type='progress'). */
  progress?: { elapsedSeconds: number };
  /** Tool summary data (type='summary'). Maps to SDK preceding_tool_use_ids. */
  precedingToolUseIds?: string[];
  summary?: string;
  /** Aggregated subagent tools (toolName='Task' result only). */
  subagentTools?: Array<{
    toolId: string;
    toolName: string;
    toolInput: unknown;
    toolResult?: { content: string; isError: boolean } | null;
    timestamp: number;
  }>;
};
```

- [ ] **Step 7: 新增 AgentSystemData 类型**

在 `AgentTaskData` 定义之后添加：

```typescript
// -- system stream data --

export type SystemStatusChange = {
  type: 'status_change';
  status: 'compacting' | 'requesting' | null;
  compactResult?: 'success' | 'failed' | null;
  compactError?: string | null;
};

export type SystemApiRetry = {
  type: 'api_retry';
  attempt: number;
  maxRetries: number;
  retryDelayMs: number;
  errorStatus?: number | null;
  error: string;
};

export type SystemRateLimit = {
  type: 'rate_limit';
  status: 'allowed' | 'allowed_warning' | 'rejected';
  rateLimitType?: 'five_hour' | 'seven_day' | 'seven_day_opus' | 'seven_day_sonnet' | 'overage';
  utilization?: number;
  resetsAt?: number;
  overageStatus?: 'allowed' | 'allowed_warning' | 'rejected';
  overageResetsAt?: number;
};

export type SystemCompactBoundary = {
  type: 'compact_boundary';
  trigger: 'manual' | 'auto';
  preTokens: number;
  postTokens?: number;
  durationMs?: number;
  compactedTurns: number;
};

export type SystemFilesPersisted = {
  type: 'files_persisted';
  files: Array<{ filename: string; fileId: string }>;
  failed: Array<{ filename: string; error: string }>;
  processedAt: string;
};

export type AgentSystemData =
  | SystemStatusChange
  | SystemApiRetry
  | SystemRateLimit
  | SystemCompactBoundary
  | SystemFilesPersisted;
```

- [ ] **Step 8: 新增 AgentMemoryData 类型**

```typescript
// -- memory stream data --

export type AgentMemoryData = {
  type: 'recall';
  mode: 'select' | 'synthesize';
  memories: Array<{
    path: string;
    scope: 'personal' | 'team';
    content?: string | null;
  }>;
};
```

- [ ] **Step 9: 扩展 AgentTaskData — 补充缺失字段**

修改 `src/types.ts:165-183`：

```typescript
export type AgentTaskData = {
  type: 'task_started' | 'task_progress' | 'task_notification' | 'task_updated';
  taskId: string;
  toolUseId?: string;
  /** task_started: task type (e.g. 'agent', 'workflow'). */
  taskType?: string;
  /** task_started: workflow name. */
  workflowName?: string;
  /** task_started: the prompt sent to the subagent. */
  prompt?: string;
  /** task_progress: name of the last tool executed by the subagent. */
  lastToolName?: string;
  /** task_progress: progress summary text. */
  summary?: string;
  status?: 'pending' | 'running' | 'completed' | 'failed' | 'stopped' | 'killed';
  description?: string;
  outputFile?: string;
  usage?: { totalTokens: number; toolUses: number; durationMs: number };
  patch?: {
    status?: 'pending' | 'running' | 'completed' | 'failed' | 'killed';
    description?: string;
    endTime?: number;
    totalPausedMs?: number;
    error?: string;
    isBackgrounded?: boolean;
  };
};
```

- [ ] **Step 10: 扩展 InteractionRequestedEvent — 添加 agentContext**

修改 `src/types.ts:292-310`，在 `InteractionRequestedEvent` 中添加字段：

```typescript
export type InteractionRequestedEvent = {
  interactionId: string;
  runId: string;
  sessionKey: string;
  kind: InteractionKind;
  title?: string;
  description?: string;
  prompt?: string;
  subject?: InteractionSubject;
  questions?: InteractionQuestion[];
  options?: InteractionOption[];
  command?: string;
  cwd?: string;
  inputSchema?: InteractionInputSchema;
  uiHints?: InteractionUiHints;
  /** Subagent context — present when this interaction originates from a subagent. */
  agentContext?: AgentContext;
  createdAtMs: number;
  expiresAtMs: number;
};
```

- [ ] **Step 11: 新增 Notification 和 PromptSuggestion 类型**

在 `InteractionRequestedEvent` 之后添加：

```typescript
// -- Notification event (top-level, not agent stream) --

export type NotificationPriority = 'low' | 'medium' | 'high' | 'immediate';

export type NotificationEvent = {
  key: string;
  text: string;
  priority: NotificationPriority;
  color?: string;
  timeoutMs?: number;
  sessionKey?: string;
  runId?: string;
};

// -- Prompt suggestion event (top-level, not agent stream) --

export type PromptSuggestionEvent = {
  runId: string;
  sessionKey: string;
  suggestions: Array<{ text: string }>;
};
```

- [ ] **Step 12: 运行测试确认通过**

Run: `npx egg-bin test test/protocol-enhancement.test.ts`
Expected: PASS

- [ ] **Step 13: 运行 lint 检查**

Run: `tnpm run lint`
Expected: 无错误

- [ ] **Step 14: 提交**

```bash
git add src/types.ts test/protocol-enhancement.test.ts
git commit -m "feat: add protocol enhancement types (AgentContext, system, memory, notification, suggestion)"
```

---

### Task 2: 扩展 SDK Bridge 事件捕获

**Files:**
- Modify: `src/claude-sdk-bridge.ts:739-745` (tool_progress / tool_use_summary handlers)
- Modify: `src/claude-sdk-bridge.ts:747-761` (task event handler)
- Modify: `src/claude-sdk-bridge.ts` (新增 system/memory/notification/prompt_suggestion 捕获)
- Modify: `src/claude-sdk-bridge.ts` (传递 parent_tool_use_id 到 OrchestratorEvent)

- [ ] **Step 1: 捕获 tool_progress 事件**

在 `src/claude-sdk-bridge.ts` 中，将 `tool_progress` 的 `continue` 替换为：

```typescript
if (msg?.type === 'tool_progress') {
  handlers?.onToolProgress?.({
    toolCallId: msg.tool_use_id ?? '',
    toolName: msg.tool_name ?? '',
    parentToolUseId: msg.parent_tool_use_id ?? null,
    elapsedSeconds: msg.elapsed_time_seconds ?? 0,
    taskId: msg.task_id,
  });
  log.debug('tool_progress', { toolUseId: msg.tool_use_id, elapsed: msg.elapsed_time_seconds });
  continue;
}
```

- [ ] **Step 2: 捕获 tool_use_summary 事件**

将 `tool_use_summary` 的 `continue` 替换为：

```typescript
if (msg?.type === 'tool_use_summary') {
  handlers?.onToolSummary?.({
    summary: msg.summary ?? '',
    precedingToolUseIds: msg.preceding_tool_use_ids ?? [],
  });
  log.debug('tool_use_summary', { summary: msg.summary?.slice(0, 80) });
  continue;
}
```

- [ ] **Step 3: 捕获 system subtype 事件（status、api_retry、compact_boundary、files_persisted、memory_recall、notification）**

在 task 事件处理之后、`msg?.type === 'user'` 之前，添加：

```typescript
// System subtypes
if (msg?.type === 'system') {
  const subtype = msg.subtype;
  if (subtype === 'status') {
    handlers?.onSystemEvent?.({
      type: 'status_change',
      status: msg.status ?? null,
      compactResult: msg.compact_result ?? null,
      compactError: msg.compact_error ?? null,
    });
    log.debug('system:status', { status: msg.status });
  } else if (subtype === 'api_retry') {
    handlers?.onSystemEvent?.({
      type: 'api_retry',
      attempt: msg.attempt ?? 0,
      maxRetries: msg.max_retries ?? 0,
      retryDelayMs: msg.retry_delay_ms ?? 0,
      errorStatus: msg.error_status ?? null,
      error: msg.error ?? '',
    });
    log.debug('system:api_retry', { attempt: msg.attempt });
  } else if (subtype === 'compact_boundary') {
    const meta = msg.compact_meta as Record<string, unknown> | undefined;
    handlers?.onSystemEvent?.({
      type: 'compact_boundary',
      trigger: (meta?.trigger as string) ?? 'auto',
      preTokens: (meta?.pre_tokens as number) ?? 0,
      postTokens: meta?.post_tokens as number | undefined,
      durationMs: meta?.duration_ms as number | undefined,
      compactedTurns: 0, // derived at orchestrator level if needed
    });
    log.debug('system:compact_boundary');
  } else if (subtype === 'files_persisted') {
    handlers?.onSystemEvent?.({
      type: 'files_persisted',
      files: Array.isArray(msg.files) ? msg.files : [],
      failed: Array.isArray(msg.failed) ? msg.failed : [],
      processedAt: msg.processed_at ?? new Date().toISOString(),
    });
    log.debug('system:files_persisted');
  } else if (subtype === 'memory_recall') {
    handlers?.onMemoryRecall?.({
      mode: msg.mode ?? 'select',
      memories: Array.isArray(msg.memories) ? msg.memories : [],
    });
    log.debug('system:memory_recall', { mode: msg.mode });
  } else if (subtype === 'notification') {
    handlers?.onNotification?.({
      key: msg.key ?? '',
      text: msg.text ?? '',
      priority: msg.priority ?? 'medium',
      color: msg.color,
      timeoutMs: msg.timeout_ms,
    });
    log.debug('system:notification', { key: msg.key, priority: msg.priority });
  } else if (subtype === 'task_updated') {
    handlers?.onTaskEvent?.({
      type: 'task_updated',
      taskId: msg.task_id ?? '',
      patch: msg.patch,
    });
    log.debug('system:task_updated', { taskId: msg.task_id });
  }
  continue;
}
```

- [ ] **Step 4: 捕获 prompt_suggestion 事件（非 system subtype）**

在 system 事件处理之后添加：

```typescript
if (msg?.type === 'prompt_suggestion') {
  handlers?.onPromptSuggestion?.({
    suggestion: msg.suggestion ?? '',
  });
  log.debug('prompt_suggestion', { suggestion: msg.suggestion?.slice(0, 60) });
  continue;
}
```

- [ ] **Step 5: 捕获 rate_limit_event（非 system subtype）**

```typescript
if (msg?.type === 'rate_limit_event') {
  const info = msg.rate_limit_info as Record<string, unknown> | undefined;
  handlers?.onSystemEvent?.({
    type: 'rate_limit',
    status: info?.status ?? 'allowed',
    rateLimitType: info?.rateLimitType as string | undefined,
    utilization: info?.utilization as number | undefined,
    resetsAt: info?.resetsAt as number | undefined,
    overageStatus: info?.overageStatus as string | undefined,
    overageResetsAt: info?.overageResetsAt as number | undefined,
  });
  log.debug('rate_limit_event', { status: info?.status });
  continue;
}
```

- [ ] **Step 6: 扩展 task 事件传递新字段**

修改 `src/claude-sdk-bridge.ts:748-761`，在 task 事件 handler 中补充字段：

```typescript
if (msg?.type === 'task_started' || msg?.type === 'task_progress' || msg?.type === 'task_notification') {
  handlers?.onTaskEvent?.({
    type: msg.type,
    taskId: msg.task_id ?? msg.taskId ?? '',
    toolUseId: msg.tool_use_id ?? msg.toolUseId,
    status: msg.status,
    description: msg.description,
    summary: msg.summary,
    outputFile: msg.output_file ?? msg.outputFile,
    usage: msg.usage,
    // New fields
    taskType: msg.task_type,
    workflowName: msg.workflow_name,
    prompt: msg.prompt,
    lastToolName: msg.last_tool_name,
  });
  log.debug('task:event', { type: msg.type, taskId: msg.task_id ?? msg.taskId });
  continue;
}
```

- [ ] **Step 7: 扩展 ClaudePromptHandlers 类型**

在 `src/claude-cli-bridge.ts` 中，找到 `ClaudePromptHandlers` 类型定义，添加新的 handler 签名：

```typescript
export type ClaudePromptHandlers = {
  onTextDelta?: (fullText: string, delta: string) => void;
  onThinkingDelta?: (fullText: string, delta: string) => void;
  onToolStart?: (tool: ToolUseInfo) => void;
  onToolUpdate?: (toolCallId: string, partialJson: string) => void;
  onToolEnd?: (tool: ToolUseInfo) => void;
  onCommandOutput?: (toolCallId: string, phase: 'delta' | 'end', output: string, meta?: { exitCode?: number | null; durationMs?: number; cwd?: string }) => void;
  onLifecycle?: (phase: 'start' | 'end' | 'error', data?: Record<string, unknown>) => void;
  onUsage?: (usage: Record<string, unknown>) => void;
  onCost?: ( Record<string, unknown>) => void;
  onMessageStart?: ( Record<string, unknown>) => void;
  onMessageStop?: () => void;
  onContentBlockStart?: ( Record<string, unknown>) => void;
  onContentBlockStop?: ( Record<string, unknown>) => void;
  onTaskEvent?: ( Record<string, unknown>) => void;
  // New handlers
  onToolProgress?: (data: { toolCallId: string; toolName: string; parentToolUseId: string | null; elapsedSeconds: number; taskId?: string }) => void;
  onToolSummary?: ( { summary: string; precedingToolUseIds: string[] }) => void;
  onSystemEvent?: ( Record<string, unknown>) => void;
  onMemoryRecall?: ( { mode: string; memories: unknown[] }) => void;
  onNotification?: ( { key: string; text: string; priority: string; color?: string; timeoutMs?: number }) => void;
  onPromptSuggestion?: ( { suggestion: string }) => void;
};
```

- [ ] **Step 8: 运行编译检查**

Run: `npx tsc --noEmit 2>&1 | head -30`
Expected: 无新增类型错误

- [ ] **Step 9: 提交**

```bash
git add src/claude-sdk-bridge.ts src/claude-cli-bridge.ts
git commit -m "feat: capture SDK events for tool_progress, tool_use_summary, system subtypes, memory, notification, prompt_suggestion"
```

---

### Task 3: 扩展 Orchestrator 事件

**Files:**
- Modify: `src/chat-orchestrator.ts:49-64` (OrchestratorEvent)

- [ ] **Step 1: 扩展 OrchestratorEvent 联合类型**

在 `src/chat-orchestrator.ts` 的 `OrchestratorEvent` 类型中添加新 kind：

```typescript
export type OrchestratorEvent =
  | { kind: 'textDelta'; fullText: string; delta: string }
  | { kind: 'thinkingDelta'; fullText: string; delta: string }
  | { kind: 'toolStart'; tool: ToolUseInfo }
  | { kind: 'toolUpdate'; toolCallId: string; partialJson: string }
  | { kind: 'toolEnd'; tool: ToolUseInfo }
  | { kind: 'commandOutput'; toolCallId: string; phase: 'delta' | 'end'; output: string; meta?: { exitCode?: number | null; durationMs?: number; cwd?: string } }
  | { kind: 'lifecycle'; phase: 'start' | 'end' | 'error'; data?: Record<string, unknown> }
  | { kind: 'usage'; usage: OrchestratorUsage }
  | { kind: 'messageStart';  OrchestratorMessageData }
  | { kind: 'messageStop' }
  | { kind: 'contentBlockStart';  OrchestratorContentBlockData }
  | { kind: 'contentBlockStop';  { index: number; blockType: string } }
  | { kind: 'cost';  OrchestratorCost }
  | { kind: 'task';  Record<string, unknown> }
  | { kind: 'todoUpdate'; todos: TodoItem[]; toolCallId?: string }
  // New event kinds
  | { kind: 'toolProgress';  { toolCallId: string; toolName: string; parentToolUseId: string | null; elapsedSeconds: number; taskId?: string } }
  | { kind: 'toolSummary';  { summary: string; precedingToolUseIds: string[] } }
  | { kind: 'system';  Record<string, unknown> }
  | { kind: 'memoryRecall'; data: { mode: string; memories: unknown[] } }
  | { kind: 'notification';  { key: string; text: string; priority: string; color?: string; timeoutMs?: number } }
  | { kind: 'promptSuggestion';  { suggestion: string } };
```

- [ ] **Step 2: 在 chat-orchestrator.ts 的 subscribe 调用处转发新 handler**

在 `startChat` 函数中，找到 `handlers` 对象的构建位置，将新 handler 连接到 `running.subscribe`。在现有的 `onTaskEvent` handler 之后添加：

```typescript
onToolProgress: (data) => { emit({ kind: 'toolProgress', data }); },
onToolSummary: (data) => { emit({ kind: 'toolSummary', data }); },
onSystemEvent: (data) => { emit({ kind: 'system', data }); },
onMemoryRecall: (data) => { emit({ kind: 'memoryRecall', data }); },
onNotification: (data) => { emit({ kind: 'notification', data }); },
onPromptSuggestion: (data) => { emit({ kind: 'promptSuggestion', data }); },
```

注意：`emit` 是 `running.subscribe` 的 emitter 函数。需要确认具体变量名（可能是 `subscriber` 或直接在 subscribe 回调中处理）。

- [ ] **Step 3: 运行编译检查**

Run: `npx tsc --noEmit 2>&1 | head -30`
Expected: 编译通过

- [ ] **Step 4: 提交**

```bash
git add src/chat-orchestrator.ts
git commit -m "feat: extend OrchestratorEvent with toolProgress, toolSummary, system, memory, notification, promptSuggestion"
```

---

### Task 4: 扩展 Gateway Bridge 转发

**Files:**
- Modify: `src/gateway/orchestrator-bridge.ts:586-612` (task event handling)
- Modify: `src/gateway/orchestrator-bridge.ts:609` (default case — add new kind handlers)
- Modify: `src/gateway/connection-context.ts` (add notificationEvent / promptSuggestionEvent)
- Modify: `src/interaction/types.ts` (PendingInteraction add agentContext)
- Modify: `src/interaction/builders.ts` (builders accept agentContext)
- Modify: `src/interaction/emitters.ts` (emit with agentContext)

- [ ] **Step 1: 在 connection-context.ts 中添加顶层事件辅助方法**

在 `ConnectionContext` 类中添加：

```typescript
/** Emit a top-level notification event (toast/snackbar). */
notificationEvent(payload: { key: string; text: string; priority: string; color?: string; timeoutMs?: number; sessionKey?: string; runId?: string }) {
  this.send({ type: 'event', event: 'notification', payload, seq: ++this.seq });
}

/** Emit a top-level prompt.suggestions event. */
promptSuggestionEvent(payload: { runId: string; sessionKey: string; suggestions: Array<{ text: string }> }) {
  this.send({ type: 'event', event: 'prompt.suggestions', payload, seq: ++this.seq });
}
```

- [ ] **Step 2: 在 orchestrator-bridge.ts 中处理新 OrchestratorEvent kind**

在 `switch (event.kind)` 的 `default` 分支之前添加：

```typescript
case 'toolProgress': {
  const d = event.data;
  const agentContext: AgentContext | undefined = d.parentToolUseId
    ? { parentToolUseId: d.parentToolUseId, taskId: d.taskId }
    : undefined;
  ctx.agentEvent(runId, sessionKey, 'tool', {
    type: 'progress',
    toolCallId: d.toolCallId,
    toolName: d.toolName,
    progress: { elapsedSeconds: d.elapsedSeconds },
    ...(agentContext && { agentContext }),
  } as unknown as Record<string, unknown>);
  return;
}
case 'toolSummary': {
  ctx.agentEvent(runId, sessionKey, 'tool', {
    type: 'summary',
    precedingToolUseIds: event.data.precedingToolUseIds,
    summary: event.data.summary,
  } as unknown as Record<string, unknown>);
  return;
}
case 'system': {
  ctx.agentEvent(runId, sessionKey, 'system', event.data);
  return;
}
case 'memoryRecall': {
  ctx.agentEvent(runId, sessionKey, 'memory', {
    type: 'recall',
    mode: event.data.mode,
    memories: event.data.memories,
  } as unknown as Record<string, unknown>);
  return;
}
case 'notification': {
  ctx.notificationEvent({
    key: event.data.key,
    text: event.data.text,
    priority: event.data.priority,
    color: event.data.color,
    timeoutMs: event.data.timeoutMs,
    sessionKey,
    runId,
  });
  return;
}
case 'promptSuggestion': {
  ctx.promptSuggestionEvent({
    runId,
    sessionKey,
    suggestions: [{ text: event.data.suggestion }],
  });
  return;
}
```

需要在文件顶部 import 中添加 `AgentContext`：

```typescript
import type {
  AgentAssistantData,
  AgentContentBlockData,
  AgentContext,
  AgentLifecycleData,
  AgentMessageData,
  InteractionQuestion,
  InteractionSubject,
} from '../types.js';
```

- [ ] **Step 3: 扩展 task 事件转发，包含新字段**

修改 `orchestrator-bridge.ts:586-599` 的 task 事件处理：

```typescript
case 'task': {
  const taskEvent = event.data;
  ctx.agentEvent(runId, sessionKey, 'task', {
    type: taskEvent.type,
    taskId: taskEvent.taskId,
    toolUseId: taskEvent.toolUseId,
    status: taskEvent.status,
    description: taskEvent.description,
    summary: taskEvent.summary,
    outputFile: taskEvent.outputFile,
    usage: taskEvent.usage,
    taskType: taskEvent.taskType,
    workflowName: taskEvent.workflowName,
    prompt: taskEvent.prompt,
    lastToolName: taskEvent.lastToolName,
  } as unknown as Record<string, unknown>);
  return;
}
```

- [ ] **Step 4: 在 PendingInteraction 中添加 agentContext**

修改 `src/interaction/types.ts`，在 `PendingInteraction` 类型中添加：

```typescript
import type { AgentContext } from '../types.js';

export type PendingInteraction = {
  // ... existing fields ...
  /** Subagent context — present when this interaction originates from a subagent. */
  agentContext?: AgentContext;
  // ... rest of existing fields ...
};
```

- [ ] **Step 5: 在 interaction builder 中传递 agentContext**

修改 `src/interaction/builders.ts`，在 `buildAskUserInteraction` 和 `buildExecInteraction` 函数签名中添加可选 `agentContext` 参数，并包含在返回的事件中：

```typescript
export function buildAskUserInteraction(opts: {
  interactionId: string;
  runId: string;
  sessionKey: string;
  toolCallId: string;
  prompt?: string;
  questions?: InteractionQuestion[];
  agentContext?: AgentContext;
}): InteractionRequestedEvent {
  // ... existing builder logic ...
  return {
    // ... existing fields ...
    agentContext: opts.agentContext,
    createdAtMs,
    expiresAtMs,
  };
}
```

同样修改 `buildExecInteraction`：

```typescript
export function buildExecInteraction(opts: {
  interactionId: string;
  runId: string;
  sessionKey: string;
  tool: { id: string; name: string; input: Record<string, unknown> };
  cwd?: string;
  agentContext?: AgentContext;
}): InteractionRequestedEvent {
  // ... existing builder logic ...
  return {
    // ... existing fields ...
    agentContext: opts.agentContext,
    createdAtMs,
    expiresAtMs,
  };
}
```

需要在 builders.ts 顶部 import 中添加 `AgentContext`：

```typescript
import type {
  AgentContext,
  AgentModeTransitionData,
  InteractionQuestion,
  InteractionRequestedEvent,
  InteractionResolvedEvent,
  InteractionSubject,
  InteractionUiHints,
} from '../types.js';
```

- [ ] **Step 6: 运行编译检查**

Run: `npx tsc --noEmit 2>&1 | head -30`
Expected: 编译通过

- [ ] **Step 7: 运行测试**

Run: `npx egg-bin test`
Expected: 所有测试通过

- [ ] **Step 8: 提交**

```bash
git add src/gateway/orchestrator-bridge.ts src/gateway/connection-context.ts src/interaction/types.ts src/interaction/builders.ts
git commit -m "feat: wire gateway bridge for system, memory, notification, promptSuggestion, toolProgress, toolSummary, agentContext"
```

---

### Task 5: 在 SDK suspend/resume 路径中传递 agentContext

**Files:**
- Modify: `src/claude-sdk-bridge.ts` (canUseTool callback 中提取 agent_id)
- Modify: `src/server.ts` (interaction resolve 路径)

- [ ] **Step 1: 在 canUseTool callback 中提取 agent_id 并传递**

在 `src/claude-sdk-bridge.ts` 中找到 `canUseTool` 回调函数，提取 `agentID` 字段（注意 SDK 此处用 camelCase `agentID`），构建 `agentContext` 并传递到 `InteractionRequestedRuntimeEvent`：

```typescript
// Inside canUseTool callback, after extracting toolName, toolInput, etc:
const agentContext = (input as Record<string, unknown>).agentID
  ? { agentId: String((input as Record<string, unknown>).agentID) }
  : undefined;
```

将 `agentContext` 附加到 `InteractionRequestedRuntimeEvent` 上。具体实现需要查看 `canUseTool` 回调的完整上下文来确定在哪里传递。

- [ ] **Step 2: 在 server.ts 中将 agentContext 传递到 interaction builder**

在 `server.ts` 中找到处理 SDK suspend/resume interaction 的代码路径，将 `agentContext` 从 `InteractionRequestedRuntimeEvent` 传递到 `buildExecInteraction` / `buildAskUserInteraction` 的 `opts.agentContext`。

- [ ] **Step 3: 运行编译和测试**

Run: `npx tsc --noEmit && npx egg-bin test`
Expected: 编译通过，测试通过

- [ ] **Step 4: 提交**

```bash
git add src/claude-sdk-bridge.ts src/server.ts
git commit -m "feat: pass agentContext from SDK canUseTool through to interaction events"
```

---

### Task 6: 更新协议文档

**Files:**
- Modify: `docs/websocket-protocol.md`

- [ ] **Step 1: 更新 agent stream 分类表**

在 Section 3.2 的 stream 分类表中添加 `system` 和 `memory`：

```markdown
| `system` | 系统状态事件 (compaction/retry/rate_limit/compact_boundary/files_persisted) |
| `memory` | 记忆召回事件 (recall) |
```

更新 `tool` 行说明：`工具调用事件 (start/update/progress/result/summary)`

更新 `task` 行说明：`子 Agent 任务生命周期事件 (含 taskType/workflowName/prompt/lastToolName/summary)`

- [ ] **Step 2: 添加 Section 6.1 — AgentContext**

在 Section 6 (Task 事件) 之后添加新节：

```markdown
## 6.1 AgentContext — Subagent 归属

所有 tool stream 和 interaction 事件可携带 `agentContext` 字段标识 subagent 来源：

| 字段 | 类型 | 说明 |
|---|---|---|
| `parentToolUseId` | `string?` | 主键。非空 = 属于 subagent |
| `taskId` | `string?` | 关联的 task ID |
| `agentId` | `string?` | SDK agent_id |
| `agentType` | `string?` | agent 类型名 |
| `agentName` | `string?` | 用户自定义名称 |

前端树构建规则：`agentContext.parentToolUseId` 非空 → 归属对应 Task 节点；无 `agentContext` → 主进程。
```

- [ ] **Step 3: 添加 Section 7.1 — System 事件**

```markdown
## 7.1 System 事件

系统状态事件通过 `agent` event 的 `system` stream 发送。

| data.type | 说明 |
|---|---|
| `status_change` | 系统状态变化 (compacting/requesting/null)，含 compactResult/compactError |
| `api_retry` | API 重试，含 attempt/maxRetries/retryDelayMs/errorStatus/error |
| `rate_limit` | 限额警告，含 status/rateLimitType/utilization/resetsAt |
| `compact_boundary` | 上下文压缩边界，含 trigger/preTokens/postTokens/durationMs |
| `files_persisted` | 文件持久化确认，含 files/failed/processedAt |
```

- [ ] **Step 4: 添加 Section 9.1 — Notification 事件**

```markdown
## 9.1 Notification 事件

全局 toast 通知，独立顶层事件 `notification`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | `string` | 唯一标识 |
| `text` | `string` | 显示文本 |
| `priority` | `string` | low/medium/high/immediate |
| `color` | `string?` | 颜色提示 |
| `timeoutMs` | `number?` | 自动关闭时间 |
```

- [ ] **Step 5: 添加 Section 9.2 — Prompt Suggestions 事件**

```markdown
## 9.2 Prompt Suggestions 事件

后续问题建议，独立顶层事件 `prompt.suggestions`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `runId` | `string` | 产生建议的 run |
| `sessionKey` | `string` | 所属 session |
| `suggestions` | `{ text: string }[]` | 建议列表 |

同一 runId 的多次事件追加（非替换），新 chat.send 时清空。
```

- [ ] **Step 6: 更新顶层事件总表**

在 Section 3.1 的事件表中添加：

```markdown
| `notification` | 全局通知 (toast/snackbar) |
| `prompt.suggestions` | 后续问题建议 |
```

- [ ] **Step 7: 提交**

```bash
git add docs/websocket-protocol.md
git commit -m "docs: update websocket-protocol.md with system stream, memory stream, notification, prompt suggestions, agentContext"
```

---

### Task 7: 运行完整 CI 流程

**Files:** None (verification only)

- [ ] **Step 1: 运行 lint**

Run: `tnpm run lint`
Expected: 无错误

- [ ] **Step 2: 运行测试**

Run: `tnpm run test-local`
Expected: 所有测试通过

- [ ] **Step 3: 运行构建**

Run: `tnpm run prepublishOnly`
Expected: 构建成功

- [ ] **Step 4: 最终提交（如有 lint 修复）**

```bash
git add -A
git commit -m "chore: fix lint issues from protocol enhancement"
```