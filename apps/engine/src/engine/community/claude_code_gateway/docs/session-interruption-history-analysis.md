# 会话中断历史记录保存：分析与优化

> 文档日期：2026-05-11
> 涉及模块：`src/store.ts`、`src/interaction/`、`src/gateway/handlers/chat.ts`、`src/gateway/orchestrator-bridge.ts`、`src/claude-sdk-bridge.ts`、`src/chat-orchestrator.ts`

---

## 一、整体架构概览

aicoding-relay 的对话中断历史记录存储涉及 **三层核心机制**：

1. **会话历史持久化**（`src/store.ts`）— 基于文件的 JSON 存储
2. **HITL 交互中断与恢复**（`src/interaction/` 模块）— 人工审批中断/恢复机制
3. **会话运行时管理**（`src/runtime/session-runtime-registry.ts`）— 运行状态跟踪

---

## 二、会话历史持久化层

### 2.1 存储结构

`SessionStore`（`src/store.ts`）使用 **单文件 JSON** 持久化所有会话数据：

- **文件路径**：构造时指定（通常为 `data/sessions.json`）
- **内存索引**：双 Map 索引 — `byAcpSessionId` 和 `byGatewaySessionKey`
- **写入策略**：防抖写入（默认 50ms）+ 原子写入（先写 `.tmp` 再 `rename`）

### 2.2 历史消息类型

`SessionHistoryMessage`（`src/types.ts`）支持 6 种角色：

```typescript
role: 'user' | 'assistant' | 'system' | 'tool_use' | 'tool_result' | 'thinking'
```

关键扩展字段：
- **`metadata`**：存放工具调用详情（`ToolUseMeta`、`ToolResultMeta`、`ThinkingMeta`）
- **`content`**：可选的结构化内容块（`HistoryContentBlock[]`），tool/thinking 消息为 `null`
- **`text`**：向后兼容的纯文本摘要

### 2.3 写入时机

历史消息 **不在事件流中逐条写入**，而是在 **run 完成时批量写入**：

1. `orchestrator-bridge.ts` 在事件流中收集 `CollectedEvent[]`（`tool_use`、`tool_result`、`thinking`、`assistant_text`）
2. Run 完成后，由 chat handler 将收集的事件转为 `SessionHistoryMessage` 写入

这样设计的好处：
- 避免频繁磁盘 I/O
- 保证同一 run 的消息原子性
- 中断/错误时可以选择不写入不完整的工具记录

---

## 三、HITL 中断与恢复机制

### 3.1 Continuation 模式（CLI bridge 默认）

流程：

```
chat.send → startChatRun → toolEnd 识别 HITL 工具
    → 注册 PendingInteraction → running.abort()（中断原运行）
    → 前端展示审批面板
    → interaction.resolve
    → continueByFollowUpChat()（新起一个 follow-up run）
```

**关键点**：
- 原 run 被 `abort()` 终止
- 用户回复后重新 `startChatRun()`，使用 continuation prompt 告诉模型"用户批准了/回答了"
- continuation prompt 由 `buildAskUserContinuationPrompt`、`buildExecContinuationPrompt`、`buildModeSwitchContinuationPrompt` 分别构建
- follow-up run 使用 `resumeSessionId` 继承 SDK 会话上下文

### 3.2 SDK Suspend/Resume 模式（SDK bridge 使用）

流程：

```
chat.send → startChatRun → SDK canUseTool hook 拦截
    → 创建 PendingToolWait（Promise 挂起）
    → onInteractionRequested 通知 gateway
    → 注册 PendingInteraction（带 resolver/rejecter）
    → SDK 原 run 在 Promise 上 await
    → interaction.resolve
    → pending.resolver() → Promise resolve
    → 原 run 在同一执行流中继续
```

**模式选择**：通过 `hitlRuntimeMode` 参数控制：
- CLI bridge → `continuation` 模式
- SDK bridge → `sdk_suspend_resume` 模式

### 3.3 Resolve 路径选择

`resolve.ts` 中的核心逻辑：

```typescript
if (pending.resolver) {
  pending.resolver({ ...params, phase });  // Stage-2: 恢复原运行
} else {
  continueByFollowUpChat({ ... });          // Stage-1: continuation fallback
}
```

---

## 四、中断时历史记录保存现状

### 4.1 HITL 中断时（`wasInterrupted() && !result.aborted`）

`chat.ts` 第 641-653 行：

```typescript
if (wasInterrupted() && !result.aborted) {
  const intCollectedEvents = getCollectedEvents();
  const intHasAssistantText = persistCollectedEvents(deps.store, sessionKey, intCollectedEvents, runId);
  if (!intHasAssistantText) {
    const intPartialText = getLastStreamedText();
    if (intPartialText) {
      deps.store.appendHistory(sessionKey, { id: randomUUID(), role: 'assistant', text: intPartialText, timestamp: nowIso(), runId });
    }
  }
  deps.runtimeRegistry.updateRunState(sessionKey, 'paused_for_interaction');
  return;
}
```

**保留的内容**：
- ✅ 用户消息（`chat.send` 开始时就写入）
- ✅ 中断前已流式输出的 assistant 文本
- ✅ 中断前已收集的 `tool_use` / `tool_result` / `thinking` 事件
- ✅ 如果没有完整的 assistant_text，则用 `getLastStreamedText()` 保存部分流式文本

**不保留的内容**：
- ❌ 被中断的工具调用没有对应的 `tool_result`（因为工具还没执行就被 abort 了）
- ❌ 不会写入 `[aborted]` 系统消息（只有 `result.aborted` 为 true 时才写）

### 4.2 Continuation 恢复时

`continuation.ts` 第 186 行：

```typescript
store.appendHistory(pending.sessionKey, { id: randomUUID(), role: 'assistant', text, timestamp: nowIso(), runId: followUpRunId });
```

**保留的内容**：
- ✅ follow-up run 的 assistant 回复
- ⚠️ **continuation prompt 本身不作为 user 消息写入历史**
- ⚠️ **continuation run 中收集的 tool_use/tool_result/thinking 事件不保存**（只保存了最终 assistant text）

### 4.3 SDK Suspend/Resume 模式

- 原 run **不会被 abort**，在 `canUseTool` 的 Promise 上 await
- `wasInterrupted()` 返回 `false`
- run 正常完成后，所有收集的事件在**同一个 runId** 下写入历史
- **tool_use 和 tool_result 成对完整**

---

## 五、需要优化的问题

### 🔴 P0 — 数据正确性问题

#### 1. Continuation 模式下 `tool_use` 无配对 `tool_result`

中断时，`persistCollectedEvents()` 会写入中断前收集的 `tool_use`，但被中断的工具实际未执行，所以没有 `tool_result`。这导致历史中出现"孤儿" `tool_use`。

- **影响**：后续 `buildConversationContext()` 构建上下文时，Claude 看到自己调用了工具但没结果，可能产生困惑
- **建议**：中断时对未执行的 `tool_use` 写入一条合成的 `tool_result`（`isSynthetic: true`），内容为 `"[interrupted] Tool execution was pending user approval"`，或者干脆不写入未执行工具的 `tool_use`

#### 2. Continuation 模式下 continuation prompt 不写入历史

`continueByFollowUpChat()` 中，continuation prompt 作为 `message` 传给 `startChatRun()`，但不会作为 `user` 消息写入历史。后续轮次构建上下文时，Claude 看不到"用户批准了/拒绝了"这个信息。

- **影响**：上下文断裂，Claude 可能重复请求相同操作
- **建议**：在 continuation run 开始前，将 continuation prompt 作为一条 `user` 或 `system` 消息写入历史

#### 3. Continuation 的 `toOrchestratorHistory()` 只取 user/assistant

`continuation.ts` 第 22-28 行中的 `toOrchestratorHistory()` 只过滤 `user` 和 `assistant`，忽略了 `tool_use`/`tool_result`/`thinking`：

```typescript
// continuation.ts 第 22-28 行
for (const m of history) {
  if (m.role === 'user' || m.role === 'assistant') {  // ← 缺少 tool_use/tool_result/thinking
    result.push({ role: m.role, text: m.text });
  }
}
```

而 `chat.ts` 中的同名函数已经包含了所有角色和 `metadata`。

- **影响**：continuation run 的上下文缺少工具调用历史，Claude 可能重复执行相同工具
- **建议**：统一使用 `chat.ts` 中的 `toOrchestratorHistory()`，或提取为共享函数

### 🟡 P1 — 健壮性问题

#### 4. 中断时 `sdkSessionId` 可能丢失

`chat.ts` 第 620-639 行：`sdkSessionId` 只在 `result.ok` 时持久化。但 Continuation 模式中断时 `result.ok = false`（因为 abort），所以 `sdkSessionId` 不会被保存。

- **影响**：如果这是首次 run（之前没有 `sdkSessionId`），continuation run 无法 `resume`，上下文完全依赖 system prompt
- **建议**：中断时如果 `result.sdkSessionId` 存在，也应该保存（可加条件判断是否为首次）

#### 5. Continuation run 中再次中断时历史不完整

`continuation.ts` 第 155-162 行：continuation run 如果再次被中断（嵌套 HITL），只更新了 run state，**没有调用 `persistCollectedEvents()`** 保存已收集的事件。

```typescript
if (wasInterrupted()) {
  runtimeRegistry.updateRunState(pending.sessionKey, 'paused_for_interaction');
  // ← 缺少 persistCollectedEvents()！
  return;
}
```

- **影响**：嵌套中断场景下，continuation run 中断前的 assistant 文本和工具调用全部丢失
- **建议**：与 `chat.ts` 中断逻辑对齐，调用 `persistCollectedEvents()`

#### 6. Continuation run 失败时历史不完整

`continuation.ts` 第 164-170 行：continuation run 失败时只发送了 error 事件，**没有保存部分 assistant 文本和收集的事件**。

```typescript
if (!result.ok) {
  ctx.chatEvent(followUpRunId, pending.sessionKey, { state: 'error', ... });
  return;  // ← 没有保存任何历史！
}
```

- **影响**：continuation run 失败时，整个 follow-up 的输出全部丢失
- **建议**：失败时也调用 `persistCollectedEvents()` + 保存部分文本

#### 7. `appendHistory()` 对未知 sessionKey 静默失败

`store.ts` 第 104 行：如果 `gatewaySessionKey` 找不到 binding，`appendHistory()` 直接 return，不报错。

- **影响**：如果 session 被意外删除或 key 不匹配，历史消息会静默丢失
- **建议**：至少打一条 warn 日志

### 🟢 P2 — 一致性/体验问题

#### 8. 中断时没有写入系统消息标记

正常 abort 时会写入 `{ role: 'system', text: '[aborted]' }`，但 HITL 中断时没有类似的标记。

- **影响**：前端无法区分"正常完成"和"中断等待审批"的历史消息
- **建议**：中断时写入 `{ role: 'system', text: '[paused_for_interaction]' }`

#### 9. `chat.history` API 返回不包含中断状态信息

`handleChatHistory` 只返回消息列表，不包含当前 run 状态（是否中断中）。

- **影响**：前端刷新后无法知道当前会话是否有 pending interaction
- **建议**：在 history 响应中增加 `runState` 字段

#### 10. 历史消息无上限保护

`appendHistory()` 只做 `push`，没有历史消息数量上限。长时间会话可能导致 JSON 文件膨胀。

- **建议**：增加历史消息上限（如 1000 条），超出时裁剪最早的消息

---

## 六、测试清单

### A. 基础历史持久化测试

| # | 测试场景 | 验证点 |
|---|---------|--------|
| A1 | 正常对话完成 | user + assistant 消息完整写入 history |
| A2 | 对话包含工具调用 | tool_use + tool_result 成对写入，metadata 正确 |
| A3 | 对话包含 thinking | thinking 消息写入，metadata.text 正确 |
| A4 | 多轮对话 | 历史按时间顺序排列，runId 正确关联 |
| A5 | 服务重启后 | 历史从磁盘正确加载 |

### B. Continuation 模式中断测试

| # | 测试场景 | 验证点 |
|---|---------|--------|
| B1 | Bash 审批中断 | 中断前 assistant 文本已保存；tool_use 已写入但无 tool_result |
| B2 | AskUserQuestion 中断 | 中断前内容已保存；interaction 信息完整 |
| B3 | ExitPlanMode 中断 | mode_switch 信息已保存 |
| B4 | 审批通过后 continuation | continuation 的 assistant 回复写入历史；runId 与中断前一致 |
| B5 | 审批拒绝后 continuation | 拒绝后的替代方案写入历史 |
| B6 | **嵌套中断**（continuation run 再次中断） | ⚠️ 当前代码有 bug：continuation 中断时不保存已收集事件 |
| B7 | **continuation run 失败** | ⚠️ 当前代码有 bug：失败时不保存任何历史 |
| B8 | 中断时 sdkSessionId 保存 | ⚠️ 当前代码有 bug：中断时 sdkSessionId 不保存 |

### C. SDK Suspend/Resume 模式中断测试

| # | 测试场景 | 验证点 |
|---|---------|--------|
| C1 | Bash 审批挂起 | canUseTool 挂起期间，已流式输出的文本已保存 |
| C2 | 审批通过后恢复 | 同一 runId 下 tool_use + tool_result 成对完整 |
| C3 | 审批拒绝后恢复 | deny 结果写入 tool_result |
| C4 | AskUserQuestion 挂起/恢复 | 用户回答通过 updatedInput 传回 SDK |
| C5 | 超时自动过期 | expired 事件发出，rejecter 被调用，历史中无残留 |
| C6 | abort 信号 | pending wait 被清理，rejecter 被调用 |

### D. 边界场景测试

| # | 测试场景 | 验证点 |
|---|---------|--------|
| D1 | 中断前无任何 assistant 输出 | history 中只有 user 消息，无空 assistant |
| D2 | 中断前有部分流式文本 | 部分文本通过 `getLastStreamedText()` 保存 |
| D3 | 中断前有多个 tool_use 但都未执行 | 所有 tool_use 写入但无 tool_result（孤儿问题） |
| D4 | 用户主动 chat.abort | `[aborted]` 系统消息写入；pending interaction 被清理 |
| D5 | WebSocket 断开重连 | pending interaction 可 replay；历史完整 |
| D6 | Orphan grace 期间 run 完成 | 历史正常保存 |
| D7 | Orphan grace 超时 | run 被 abort，`[aborted]` 写入历史 |
| D8 | 同一 session 多次中断-恢复 | 历史消息按时间顺序正确，无重复或丢失 |

### E. 上下文构建测试

| # | 测试场景 | 验证点 |
|---|---------|--------|
| E1 | 中断后新 chat.send 的上下文 | `buildConversationContext()` 包含中断前的历史 |
| E2 | continuation run 的上下文 | ⚠️ 当前 `continuation.ts` 的 `toOrchestratorHistory()` 缺少 tool_use/tool_result/thinking |
| E3 | resumeSessionId 传递 | SDK 通过 resume 恢复上游对话状态 |
| E4 | 历史超长裁剪 | `maxContextChars` 生效，不会超出限制 |

### F. 数据一致性测试

| # | 测试场景 | 验证点 |
|---|---------|--------|
| F1 | 并发 appendHistory | 防抖写入不丢数据 |
| F2 | 写入过程中进程崩溃 | 原子写入保证：要么旧文件完整，要么新文件完整 |
| F3 | 大量历史消息 | JSON 文件不会无限膨胀（当前无上限） |

---

## 七、优先修复建议

按影响程度排序：

1. **🔴 修复 continuation.ts 嵌套中断不保存历史**（P0，数据丢失）
   - 在 `wasInterrupted()` 分支中添加 `persistCollectedEvents()` 调用
   - 与 `chat.ts` 中断逻辑对齐

2. **🔴 修复 continuation.ts 失败时不保存历史**（P0，数据丢失）
   - 在 `!result.ok` 分支中添加 `persistCollectedEvents()` + 部分文本保存
   - 与 `chat.ts` 失败逻辑对齐

3. **🔴 统一 continuation.ts 的 `toOrchestratorHistory()`**（P0，上下文缺失）
   - 提取为共享函数，或直接使用 `chat.ts` 中已增强的版本
   - 确保 tool_use/tool_result/thinking 和 metadata 都被传递

4. **🟡 中断时写入合成 tool_result 或不写入未执行 tool_use**（P1，数据一致性）
   - 方案 A：对中断时已写入的 tool_use 补写一条 `tool_result`（`isSynthetic: true, output: "[interrupted]"`）
   - 方案 B：中断时不写入未执行工具的 tool_use（需要区分已执行和未执行）

5. **🟡 中断时保存 sdkSessionId**（P1，恢复能力）
   - 在 `wasInterrupted()` 分支中，如果 `result.sdkSessionId` 存在且 binding 中尚无 sdkSessionId，则保存

6. **🟡 continuation prompt 写入历史**（P1，上下文完整性）
   - 在 `continueByFollowUpChat()` 调用 `startChatRun()` 前，将 continuation prompt 作为 `user` 消息写入历史

7. **🟢 增加中断状态标记**（P2，前端体验）
   - 中断时写入 `{ role: 'system', text: '[paused_for_interaction]' }`
   - `chat.history` API 增加当前 run 状态字段

8. **🟢 历史消息上限保护**（P2，存储安全）
   - `appendHistory()` 中增加上限检查（如 1000 条）
   - 超出时裁剪最早的消息

---

## 八、两种 HITL 模式对比

| 维度 | Continuation | SDK Suspend/Resume |
|------|-------------|-------------------|
| **运行连续性** | ❌ abort + 新 run | ✅ 同一 run 原位继续 |
| **工具执行** | 需要重新发起（可能不一致） | 原工具精确执行 |
| **历史完整性** | tool_use 可能无配对 tool_result | tool_use + tool_result 成对完整 |
| **上下文精度** | 依赖人工拼接的 continuation prompt | SDK 原生保持执行流 |
| **开销** | 每次审批 = 1 次 abort + 1 次新 run | 0 额外 run |
| **支持范围** | CLI bridge + SDK bridge | 仅 SDK bridge |
| **实现状态** | ✅ 完整可用 | 🔧 代码已写，主链路已接通 |

```
┌─────────────────────────────────────────────────────────┐
│  Continuation 模式                                       │
│                                                          │
│  Run 1: chat → tool(Bash) → [ABORT!]                    │
│                                    ↓                     │
│                              interaction.requested       │
│                                    ↓                     │
│                              interaction.resolve         │
│                                    ↓                     │
│  Run 2: continuation prompt → tool(Bash) → result → end │
│                                                          │
│  两个独立的 SDK 调用，靠 resumeSessionId + prompt 衔接    │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  SDK Suspend/Resume 模式                                 │
│                                                          │
│  Run 1: chat → canUseTool(Bash) → [SUSPEND: await]      │
│                                          ↓               │
│                                   interaction.requested  │
│                                          ↓               │
│                                   interaction.resolve    │
│                                          ↓               │
│                                   Promise resolve        │
│                                          ↓               │
│         ← tool(Bash) → result ← [RESUME: 原位继续]      │
│                                                          │
│  一个 SDK 调用，执行流从未中断                             │
└─────────────────────────────────────────────────────────┘
```

---

## 九、中断场景全景

### 场景分类总览

```
中断场景
├── A. HITL 交互中断（用户审批触发）
│   ├── A1. AskUserQuestion — Claude 询问用户
│   ├── A2. Exec 审批 — Bash/Edit/Write/Read 需要用户允许
│   └── A3. Mode Switch — ExitPlanMode 模式切换
│
├── B. 用户主动中断
│   ├── B1. chat.abort — 用户主动取消运行
│   └── B2. session.delete — 用户删除会话
│
├── C. 连接/网络中断
│   ├── C1. WebSocket 断开 — 客户端网络中断
│   ├── C2. Orphan Grace 超时 — 断开后未重连
│   └── C3. Controller Handoff — 新连接接管控制权
│
├── D. 超时中断
│   ├── D1. Interaction 超时 — 审批等待超时（5分钟）
│   ├── D2. Cron 任务超时 — 定时任务执行超时
│   └── D3. SDK canUseTool 超时 — SDK 侧 Promise 超时
│
├── E. 错误中断
│   ├── E1. SDK/CLI 运行错误 — Claude 运行崩溃
│   └── E3. 进程信号 — SIGTERM/SIGINT
│
└── F. 嵌套中断
    ├── F1. Continuation run 再次中断 — follow-up run 遇到新的 HITL
    └── F2. 多工具并发中断 — SDK 模式下多个 canUseTool 同时挂起
```

---

### A. HITL 交互中断

#### A1. AskUserQuestion 中断

**触发条件**：Claude 调用 `AskUserQuestion` 工具

**Continuation 模式流程**：
1. `orchestrator-bridge.ts` 在 `toolEnd` 识别 `AskUserQuestion`
2. 构建 `PendingInteraction(kind='ask_user')`
3. 调用 `interruptForInteraction()` → `interrupted = true; running.abort()`
4. 发出 `interaction.requested` 事件
5. `chat.ts` 检测到 `wasInterrupted() && !result.aborted`
6. 保存已收集的事件，更新 run state 为 `paused_for_interaction`

**SDK Suspend/Resume 模式流程**：
1. SDK `canUseTool("AskUserQuestion", ...)` 返回 Promise
2. `onInteractionRequested` 回调触发
3. `chat.ts` 构建 `PendingInteraction`（带 `resolver`/`rejecter`）
4. 注册到 registry，发出 `interaction.requested`
5. SDK 运行在 Promise 上 await（**不 abort**）
6. `chat.ts` 中 `running.completed` 尚未 resolve

**历史记录影响**：
- Continuation：中断前已流式输出的文本和 tool_use 写入历史，但 AskUserQuestion 的 tool_result 缺失
- SDK：运行未中断，历史在 run 完成后统一写入，完整

#### A2. Exec 审批中断（Bash/Edit/Write/Read）

**触发条件**：Claude 调用受控工具（Bash/Edit/Write/Read）

**与 A1 的差异**：
- `kind='exec'`，前端展示审批面板而非问答面板
- 审批选项：`allow-once` / `allow-always` / `deny`
- Continuation 模式下，中断时**不会发出 command_output phase='end'**（因为命令未执行）

**历史记录影响**：
- Continuation：tool_use 写入但无 tool_result（命令未执行）
- SDK：canUseTool 挂起，审批后工具执行，tool_use + tool_result 成对

#### A3. Mode Switch 中断（ExitPlanMode）

**触发条件**：Claude 调用 `ExitPlanMode`

**特殊之处**：
- 同时发出 `interaction.requested(kind='mode_switch')` 和兼容的 `agent.stream='mode_transition'`
- 可通过 `interaction.resolve` 或 `mode_transition.resolve` 解析
- Continuation 模式下，中断前会发出 `phase` 变更事件

**历史记录影响**：同 A1/A2

---

### B. 用户主动中断

#### B1. chat.abort

**触发条件**：用户发送 `chat.abort` 请求

**代码路径**（`chat.ts` `handleChatAbort`）：
1. 通过 `runtimeRegistry.getActiveRun()` 或 `getActiveRunByRunId()` 找到活跃 run
2. 验证连接是否为 session controller
3. `registry.takeForRun()` — 取消该 run 的所有 pending interaction
4. 对每个 dropped interaction 发出 `interaction.resolved(phase='cancelled')`
5. `targetRun.abort()` — 终止运行
6. `runtimeRegistry.completeRun()` — 清理运行状态

**历史记录影响**：
- `running.abort()` 后，`running.completed` resolve 为 `{ ok: false, aborted: true }`
- `chat.ts` 进入 `!result.ok` 分支
- 保存已收集的事件 + 部分流式文本
- 写入 `{ role: 'system', text: '[aborted]' }`
- ⚠️ pending interaction 被 takeForRun 清理，但 **不会触发 continuation 或 resolver**

#### B2. session.delete

**触发条件**：用户删除会话

**代码路径**（`sessions.ts`）：
- 调用 `store.deleteByGatewaySessionKey()` 删除整个 session binding
- 所有历史记录被删除

**历史记录影响**：全部历史丢失（这是预期行为）

---

### C. 连接/网络中断

#### C1. WebSocket 断开

**触发条件**：客户端网络中断、浏览器关闭、刷新页面

**代码路径**：
1. `ConnectionContext.dispose()` 被调用
2. `runtimeRegistry.detachAllForConnection(connId)` — 从所有 session 分离
3. 对每个有 activeRun 的 session，启动 orphan grace timer（默认 60 秒）

**历史记录影响**：
- 如果 run 正在执行（非 HITL 等待），run 继续执行直到完成
- 完成后历史正常写入，但 `chatEvent` 无法发送到已断开的 WebSocket
- 如果 run 处于 HITL 等待状态，pending interaction 保留在 registry 中

#### C2. Orphan Grace 超时

**触发条件**：WebSocket 断开后 60 秒内未重连

**代码路径**（`session-runtime-registry.ts`）：
1. Grace timer 到期
2. 检查 session 是否已被其他连接接管
3. 如果没有，调用 `onOrphanCleanup` 回调
4. `interactionRegistry.cancelForSession()` — 取消所有 pending interaction
5. `activeRun.abort()` — 终止运行
6. 删除 session 记录

**历史记录影响**：
- run 被 abort，与 B1 类似
- 但 `chat.ts` 的 `running.completed` 处理逻辑可能无法执行（因为连接已断开）
- ⚠️ **已收集的事件可能不会写入历史**（因为 `persistCollectedEvents` 在 `chat.ts` 的 await 之后，而连接已断开时 `ctx.chatEvent` 等调用无效，但 `store.appendHistory` 仍然有效）

#### C3. Controller Handoff

**触发条件**：新 WebSocket 连接 attach 到已有 controller 的 session

**代码路径**：
1. `runtimeRegistry.attachConnection()` — 新连接成为 controller
2. 旧连接自动降级
3. `replayPendingInteractions()` — 向新连接重放 pending interaction

**历史记录影响**：无直接影响，pending interaction 保留

---

### D. 超时中断

#### D1. Interaction 超时

**触发条件**：用户 5 分钟（`EXEC_APPROVAL_TIMEOUT_MS = 300_000`）未响应审批

**代码路径**（`interaction/registry.ts` + `interaction/emitters.ts`）：
1. Registry scanner（5 秒间隔）检测到 `rec.expiresAtMs <= now`
2. 删除 pending 记录，设置 `status = 'expired'`
3. 调用 `rec.onExpire()` — 发出 `interaction.resolved(phase='expired')`
4. 调用 `rec.rejecter?.(new Error('Interaction expired'))` — SDK 模式下 reject canUseTool Promise
5. `emitters.ts` 中还有 fallback setTimeout（同样 5 分钟），双重保险

**历史记录影响**：
- Continuation 模式：run 已被 abort，expired 事件发出后不会触发 continuation
- SDK 模式：`rejecter` 被调用，SDK 运行收到错误，`running.completed` resolve 为 `{ ok: false }`
- ⚠️ **expired 后 run 的后续处理取决于 `chat.ts` 中 `running.completed` 的结果**

#### D2. Cron 任务超时

**触发条件**：定时任务执行超过 `timeoutSeconds`（默认 24 小时）

**代码路径**（`cron/scheduler.ts`）：
1. `withTimeout(running.completed, timeoutMs, onTimeout)`
2. 超时后调用 `running.abort()`

**历史记录影响**：与 B1 类似，run 被 abort

#### D3. SDK canUseTool 超时

**触发条件**：SDK 侧 `canUseTool` Promise 等待超过 5 分钟

**代码路径**（`claude-sdk-bridge.ts`）：
```typescript
setTimeout(() => {
  if (pendingToolWaits.has(interactionId)) {
    rejectToolApproval(interactionId, new Error('Interaction expired'));
  }
}, EXEC_APPROVAL_TIMEOUT_MS);
```

**与 D1 的关系**：这是 SDK 侧的超时，与 registry 侧的超时是双重保险。两者都会触发 reject。

---

### E. 错误中断

#### E1. SDK/CLI 运行错误

**触发条件**：Claude 运行过程中抛出异常

**代码路径**：
- SDK：`for await (const msg of iter)` 抛出异常
- CLI：子进程异常退出
- `running.completed` resolve 为 `{ ok: false, error: '...' }`

**历史记录影响**：
- `chat.ts` 进入 `!result.ok` 分支
- 保存已收集的事件 + 部分流式文本
- 发出 `chatEvent(state='error')`
- 不写入 `[aborted]`（因为不是 abort）

#### E3. 进程信号

**触发条件**：SIGTERM / SIGINT

**代码路径**：
- `session-runtime-registry.ts` 的 `shutdown()` 方法
- 清理所有 grace timer
- abort 所有 activeRun
- `claude-sdk-bridge.ts` 的 `clearAllPendingToolWaits()`

**历史记录影响**：
- ⚠️ **进程退出前 `store.flush()` 可能未被调用**
- 防抖写入中（50ms 内）的数据可能丢失
- 需要在进程信号处理中确保 `await store.flush()`

---

### F. 嵌套中断

#### F1. Continuation run 再次中断

**触发条件**：follow-up run 执行过程中又遇到 HITL 工具

**代码路径**（`continuation.ts`）：
```typescript
if (wasInterrupted()) {
  runtimeRegistry.updateRunState(pending.sessionKey, 'paused_for_interaction');
  return;  // ← 没有保存已收集的事件！
}
```

**历史记录影响**：
- ⚠️ **Bug：continuation run 中断时不调用 `persistCollectedEvents()`**
- 中断前的 assistant 文本和工具调用全部丢失
- 需要与 `chat.ts` 的中断逻辑对齐

#### F2. 多工具并发中断（SDK 模式）

**触发条件**：SDK 模式下 Claude 同时发出多个 tool_use（并行工具调用）

**代码路径**：
- 多个 `canUseTool` 同时返回 Promise
- 每个 Promise 创建一个 `PendingToolWait`
- Registry 允许同一 run 有多个 pending interaction

**历史记录影响**：
- 所有挂起的工具都在等待审批
- 逐个 resolve 后，SDK 逐个恢复执行
- 历史在 run 最终完成时统一写入，完整

---

### 中断场景对历史记录的影响汇总

| 场景 | 中断方式 | 历史写入时机 | 已收集事件 | 部分 assistant 文本 | system 标记 | sdkSessionId |
|------|---------|------------|-----------|-------------------|------------|-------------|
| **A1-A3 HITL (Continuation)** | `running.abort()` | 中断时 | ✅ 写入 | ✅ 写入 | ❌ 无 | ❌ 不保存 |
| **A1-A3 HITL (SDK)** | Promise await | run 完成时 | ✅ 写入 | ✅ 写入 | ❌ 无 | ✅ 保存 |
| **B1 chat.abort** | `running.abort()` | abort 后 | ✅ 写入 | ✅ 写入 | ✅ `[aborted]` | ❌ 不保存 |
| **B2 session.delete** | 删除 binding | 即时删除 | N/A | N/A | N/A | N/A |
| **C1 WS 断开** | 不断开 run | run 完成时 | ✅ 写入 | ✅ 写入 | 取决于结果 | 取决于结果 |
| **C2 Orphan 超时** | `activeRun.abort()` | abort 后 | ⚠️ 可能丢失 | ⚠️ 可能丢失 | ⚠️ 可能缺失 | ❌ 不保存 |
| **D1 Interaction 超时** | rejecter/expire | 取决于模式 | 同 A1-A3 | 同 A1-A3 | ❌ 无 | 取决于结果 |
| **D2 Cron 超时** | `running.abort()` | abort 后 | ✅ 写入 | ✅ 写入 | ✅ `[aborted]` | ❌ 不保存 |
| **E1 运行错误** | 异常退出 | 错误后 | ✅ 写入 | ✅ 写入 | ❌ 无 | ❌ 不保存 |
| **E3 进程信号** | 进程退出 | ⚠️ 可能未 flush | ⚠️ 可能丢失 | ⚠️ 可能丢失 | ❌ 无 | ❌ 不保存 |
| **F1 嵌套中断** | `wasInterrupted()` | ⚠️ **不写入** | ⚠️ **丢失** | ⚠️ **丢失** | ❌ 无 | N/A |
