# 方案：将工具执行信息持久化到会话历史

## 问题

当前 `SessionHistoryMessage` 只存储扁平文本 `{role, text}`，所有工具调用、工具结果、thinking 仅作为 WebSocket 事件实时发送，从不写入 history。导致后续轮次 Claude 看不到之前的工具使用记录，`chat.history` API 也无法返回完整会话。

## 设计：3 种 HistoryContentBlock

```typescript
export type HistoryContentBlock =
  | { type: 'text'; text: string }
  | { type: 'tool'; toolCallId: string; toolName: string; input?: Record<string, unknown>; output?: string; exitCode?: number; durationMs?: number; isError?: boolean }
  | { type: 'thinking'; text: string };
```

- **text** — 普通文本
- **tool** — 工具调用，合并调用与结果。`input` 是工具入参，`output` 是执行结果，同一 block 内配对
- **thinking** — 思考内容

`SessionHistoryMessage` 扩展：

```typescript
export type SessionHistoryMessage = {
  id: string;
  role: 'user' | 'assistant' | 'system';
  text: string;           // 向后兼容：纯文本摘要
  timestamp: string;
  runId?: string;
  content?: HistoryContentBlock[];  // 新增：结构化内容
};
```

`text` 保留作为纯文本摘要（向后兼容），`content` 可选，不存在时等价于 `[{ type: 'text', text }]`。

## 写入策略

在 bridge 事件流中收集当前 run 的工具事件到内存，run 完成时**一次性批量写入** history（避免频繁 I/O，保证原子性）。

### 收集逻辑（`orchestrator-bridge.ts`）

新增 `runToolEvents: Map<string, { toolName, input, output?, exitCode?, durationMs?, isError? }>`，按 `toolCallId` 索引：

| 事件 | 操作 |
|------|------|
| `toolEnd` | 创建条目 `{ toolName: tool.name, input: tool.input }` |
| `commandOutput` (phase='end') | 合并到已有条目：`output, exitCode, durationMs` |
| `thinkingDelta` | 收集到 `thinkingTexts: string[]`（run 结束时合并为一条 thinking block） |

Bridge 返回值新增 `getCollectedBlocks: () => HistoryContentBlock[]`。

### 写入时机（`chat.ts` handleChatSend）

run 完成后（成功路径），将收集到的 blocks 写入 history：

```typescript
const blocks = getCollectedBlocks();
if (blocks.length > 0) {
  deps.store.appendHistory(sessionKey, {
    id: randomUUID(),
    role: 'assistant',
    text: blocks.map(b => {
      if (b.type === 'text') return b.text;
      if (b.type === 'tool') return `[tool:${b.toolName}]`;
      if (b.type === 'thinking') return '[thinking]';
      return '';
    }).filter(Boolean).join('\n'),
    timestamp: nowIso(),
    runId,
    content: blocks,
  });
}
// 然后写入 assistant text（已有逻辑，保持不变）
```

**中断路径**：`wasInterrupted()` 时不写入不完整的工具记录（HITL 挂起后恢复时工具尚未执行完毕）。

**错误/中止路径**：现有逻辑写入 `[aborted]` system 消息，不额外写入工具记录。

### 交互决策

交互决策（allow/deny/submit/cancel）写入 system 消息的 `text` 字段摘要，不作为独立 content block。例如：

```typescript
deps.store.appendHistory(sessionKey, {
  id: randomUUID(),
  role: 'system',
  text: `[interaction:${pending.kind}:${params.decision}]`,
  timestamp: nowIso(),
  runId: pending.runId,
});
```

这样上下文构建时 Claude 能看到审批结果，且不需要额外 block 类型。

## 上下文构建增强（`chat-orchestrator.ts`）

扩展 `OrchestratorHistoryEntry` 加 `content?`，在 `buildConversationContext` 中优先用结构化内容格式化：

```typescript
function formatBlock(block: HistoryContentBlock): string {
  switch (block.type) {
    case 'text': return block.text;
    case 'tool': {
      const inputStr = block.input ? JSON.stringify(block.input) : '';
      const status = block.isError ? '（失败）' : '';
      const exit = block.exitCode != null ? `，退出码 ${block.exitCode}` : '';
      const outputStr = block.output ? `\n结果${status}${exit}: ${block.output}` : '';
      return `[调用工具 ${block.toolName}] ${inputStr}${outputStr}`;
    }
    case 'thinking': return `[思考] ${block.text}`;
  }
}
```

拼接时：有 `content` 且含非 text 块时用 `formatBlock`，否则 fallback 到 `text`。

## chat.history API 增强（`handleChatHistory`）

返回时包含 `content` 块：

```typescript
content: m.content ?? [{ type: 'text' as const, text: m.text }],
```

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `src/types.ts` | 新增 `HistoryContentBlock` 类型（text/tool/thinking）；扩展 `SessionHistoryMessage` 加 `content?` |
| `src/chat-orchestrator.ts` | 扩展 `OrchestratorHistoryEntry` 加 `content?`；`buildConversationContext` 用 `formatBlock` 格式化结构化内容 |
| `src/gateway/orchestrator-bridge.ts` | `BridgeOrchestratorFn` 返回类型加 `getCollectedBlocks`；收集 toolEnd/commandOutput/thinkingDelta 事件 |
| `src/gateway/handlers/chat.ts` | `toOrchestratorHistory` 传递 `content`；`handleChatSend` 完成时写入收集的 blocks；`handleChatHistory` 返回 `content` |
| `src/interaction/resolve.ts` | 审批完成后写入 system history 消息（纯 text 摘要） |
| `test/store.test.ts` | 验证带 `content` 的 history 消息存取 |

## 向后兼容性

1. **存储**：`content` 可选，旧数据无 `content` 正常加载
2. **上下文构建**：无 `content` 时 fallback 到 `text`，行为与现在完全一致
3. **chat.history API**：无 `content` 时自动生成 `[{ type: 'text', text }]`，前端无需改动
4. **SDK resume**：工具历史不影响 SDK 的 `resumeSessionId` 机制

---

## 实现记录

### 实际实现与 PLAN 的差异

| PLAN 设计 | 实际实现 | 原因 |
|-----------|---------|------|
| `content` 合并 tool_use/tool_result 为一个 `{ type: 'tool' }` block | 独立 role `tool_use`/`tool_result` + `metadata`（`ToolUseMeta`/`ToolResultMeta`） | 更灵活，前端可独立渲染 tool_use 和 tool_result，且与实时流事件模型一致 |
| `SessionHistoryMessage.role` 只有 `user \| assistant \| system` | 扩展为 `user \| assistant \| system \| tool_use \| tool_result \| thinking` | 独立 role 让存储和查询更清晰，每种消息类型有专属 metadata 结构 |
| run 完成时批量写入 | **增量持久化**（`onCollectedEvent` 回调，事件到达时立即写入） | 解决用户切换会话后历史丢失问题——批量写入在 run 未完成时不会执行 |
| `BridgeOrchestratorFn` 返回 `getCollectedBlocks` | 返回 `getCollectedEvents` + `onCollectedEvent` 回调 | 增量持久化需要回调模式，而非一次性获取 |

### 已实现功能清单

| 改动点 | 文件 | 状态 |
|--------|------|------|
| `HistoryContentBlock` 类型（text/thinking/tool_use） | `src/types.ts` | ✅ |
| `ToolUseMeta` / `ToolResultMeta` / `ThinkingMeta` 类型 | `src/types.ts` | ✅ |
| `SessionHistoryMessage` 扩展（`content?` + `metadata?` + 6 种 role） | `src/types.ts` | ✅ |
| `CollectedEvent` 类型（assistant_text/tool_use/tool_result/thinking） | `src/gateway/orchestrator-bridge.ts` | ✅ |
| `onCollectedEvent` 增量回调 + `pushEvent` 辅助函数 | `src/gateway/orchestrator-bridge.ts` | ✅ |
| thinking 在 `contentBlockStop` 时立即推送（而非等 `lifecycle.end`） | `src/gateway/orchestrator-bridge.ts` | ✅ |
| `lifecycle.end` 兜底推送未收集的 thinking | `src/gateway/orchestrator-bridge.ts` | ✅ |
| `persistSingleEvent` 单事件持久化函数 | `src/gateway/handlers/chat.ts` | ✅ |
| `persistCollectedEvents` 支持 `skipCount` 避免重复 | `src/gateway/handlers/chat.ts` | ✅ |
| `handleChatSend` 传入 `onCollectedEvent` 回调实现增量持久化 | `src/gateway/handlers/chat.ts` | ✅ |
| `handleChatHistory` 为 thinking/tool_use 生成 `content` blocks | `src/gateway/handlers/chat.ts` | ✅ |
| `toOrchestratorHistory` 传递 `metadata`（含 tool_use/tool_result/thinking） | `src/gateway/handlers/chat.ts` | ✅ |
| `toOrchestratorHistory`（continuation.ts）传递 `metadata` | `src/interaction/continuation.ts` | ✅ |
| `formatHistoryEntry` 支持 tool_use/tool_result/thinking 格式化 | `src/chat-orchestrator.ts` | ✅ |
| 交互决策记录写入 system history 消息 | `src/interaction/resolve.ts` | ✅ |
| HTTP API `/api/sessions/:id/messages`（PLAN 外额外添加） | `src/http-server.ts` | ✅ |
| HTTP API `/api/sessions` 列表 + `/api/sessions/:id` 详情 | `src/http-server.ts` | ✅ |
| 前端 `transformAICodingMessages` 支持 thinking/tool_use/tool_result | `open-claw/src/utils/aicodingMessageUtils.ts` | ✅ |
| 前端 `RawMessage` role 扩展（含 `tool_use`/`thinking`） | `open-claw/src/utils/messageUtils.ts` | ✅ |
| 前端 `filterInvalidMessages` 支持 thinking 消息过滤 | `open-claw/src/utils/aicodingMessageUtils.ts` | ✅ |
| 前端 `extractThinkingText` 辅助函数 | `open-claw/src/utils/aicodingMessageUtils.ts` | ✅ |
| 前端 `loadHistoryForSession` 添加 `limit: 1000` 参数 | `open-claw/src/pages/Assistant/Chat/ChatPage.tsx` | ✅ |
| OCB 前端同步更新 | `ocb/src/frontend/src/utils/aicodingMessageUtils.ts` | ✅ |
| `server.ts` 传递 `store` 给 `handleHttpRequest` | `src/server.ts` | ✅ |

### 未实现

| 改动点 | 说明 |
|--------|------|
| `test/store.test.ts` 测试 | 未添加带 `content` 的 history 消息存取测试 |

### 关键修复：增量持久化

原始 PLAN 设计为"run 完成时批量写入"，但实际使用中发现：用户在 AI 输出过程中切换会话再切回，thinking 和 tool 历史丢失。根因是批量写入只在 run 完成时执行，而用户切换会话时 run 可能仍在进行中。

修复方案：
1. `BridgeOrchestratorFn` 新增 `onCollectedEvent` 回调选项
2. `orchestrator-bridge.ts` 中每个 `CollectedEvent` 产生时立即调用 `pushEvent`（同时 push 到数组 + 调用回调）
3. `chat.ts` 中 `onCollectedEvent` 回调立即调用 `persistSingleEvent` 写入 store
4. run 结束时 `persistCollectedEvents` 传入 `skipCount` 跳过已持久化的事件

### 关键修复：thinking 在 contentBlockStop 时推送

原始设计中 thinking 只在 `lifecycle.end` 时收集。但 SDK bridge 的 `content_block_stop` 事件已经标记了 thinking block 的结束，此时 thinking 内容已完整。修改为在 `contentBlockStop` 时立即推送 thinking 事件，`lifecycle.end` 仅作为兜底。
