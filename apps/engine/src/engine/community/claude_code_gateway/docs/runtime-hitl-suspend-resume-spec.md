# WebSocket HITL Runtime Suspend/Resume Spec

## 1. 文档目标

本文档用于定义当前 WebSocket HITL（Human-in-the-loop）能力从 **stage-1 follow-up continuation** 演进到 **stage-2 原运行挂起/恢复** 的具体开发方案。

本文档基于以下输入整理：

- `docs/websocket-hitl-protocol-analysis.md`
- `docs/interaction-protocol-refactor-plan.md`
- 当前 `src/` 实现

本文重点解决 3 个问题：

1. 如何将已存在但未接通主链路的 `canUseTool` 能力接入当前 WebSocket 流程
2. 如何把当前 pending interaction registry 升级为真正的 interaction resolver registry
3. 如何将 `AskUserQuestion` / `ExitPlanMode` / `Bash|Edit|Write|Read` 从“abort + follow-up continuation”升级为“原运行挂起并恢复”

---

## 2. 当前实现结论

### 2.1 `canUseTool`

当前代码在 `src/claude-sdk-bridge.ts` 中已经实现了 SDK 侧 `canUseTool` hook，包括：

- 基于 `interactionId` 建立 pending wait
- 支持 `resolveToolApproval(...)`
- 支持 `rejectToolApproval(...)`
- 支持超时和 abort 清理
- 支持通过 `onInteractionRequested(...)` 把挂起事件抛给上层

但当前 WebSocket 主流程并没有使用这条链路。

当前实际运行路径仍然是：

- `chat.send`
- `startChatRun(...)`
- `gateway/orchestrator-bridge.ts` 在 `toolEnd` 时识别 HITL 工具
- 注册 pending interaction
- `running.abort()`
- `interaction.resolve` 后走 `continueByFollowUpChat(...)`

因此 `canUseTool` 当前状态应判断为：

- **代码存在**
- **机制成立**
- **未接入主链路**
- **不能视为功能已完成**

### 2.2 interaction resolver registry

当前代码已经有统一的 pending interaction registry：

- `src/interaction/registry.ts`

并且 `PendingInteraction` 已预留：

```ts
resolver?: (resolution: ResolvedInteractionInput) => void;
```

但当前 resolver 并未真正用于恢复运行。

当前 `interaction.resolve` 的行为仍是：

- 取出 pending interaction
- 发出 `interaction.resolved`
- 返回 RPC 成功
- 调用 `continueByFollowUpChat(...)`

而不是：

- 取出 pending interaction
- 调用 `pending.resolver(...)`
- 恢复原 SDK run

因此当前状态应判断为：

- **pending interaction registry 已有**
- **resolver registry 只完成了结构预留，未完成实际接线**

### 2.3 原运行挂起/恢复

当前未真正实现。

现有实现仍然明确属于：

- 原 run 被 `abort()`
- 用户回复后重新 `startChatRun(...)`
- 使用 continuation prompt 告诉模型“用户批准了 / 回答了，请继续”

这不等价于 Claude Agent SDK 原生的：

- 在 `canUseTool` 中挂起
- 等待用户输入
- 在同一条 run 中继续执行

因此原运行挂起/恢复能力当前状态应判断为：

- **未实现**

---

## 3. 问题定义

当前 stage-1 实现虽然可用，但存在以下结构性问题：

### 3.1 交互拦截点错误

当前 HITL 是在 `toolEnd` 事件后由 `gateway/orchestrator-bridge.ts` 识别的，而不是在 SDK 准备执行工具前由 `canUseTool` 拦截。

结果是：

- 服务端只能“看到一个即将执行的动作”
- 但无法让 SDK 在原位置等待用户决策
- 只能中断当前 run，再靠后续 prompt 续写

### 3.2 有两套 pending 体系，未统一

当前至少存在两套等待状态：

1. SDK 侧 `pendingToolWaits`（`src/claude-sdk-bridge.ts`）
2. 网关侧 `PendingInteractionRegistry`（`src/interaction/registry.ts`）

它们没有统一成一个 runtime contract，导致：

- interactionId 生命周期分离
- resolve 路径不统一
- timeout / abort / replay 处理分散

### 3.3 resolve 逻辑仍绑定 continuation

当前 `src/interaction/resolve.ts` 的主逻辑并不是恢复原运行，而是继续触发 `continueByFollowUpChat(...)`。

### 3.4 `mode_switch` 仍是协议分叉

`mode_transition.resolve` 仍然与 `interaction.resolve` 分离，这会增加 runtime resume 统一化成本。

---

## 4. 改造目标

本次改造目标如下：

1. 将 SDK `canUseTool` 接入 WebSocket HITL 主流程
2. 将 `PendingInteractionRegistry` 升级为统一的 interaction resolver registry
3. `interaction.resolve` / `mode_transition.resolve` 优先恢复原 run，而不是继续 follow-up chat
4. 保持现有前端协议兼容
5. 保留 continuation 作为 fallback，而不是主路径

---

## 5. 非目标

本次改造不要求：

- 一次性移除所有 `agent.stream` 兼容事件
- 一次性删除 `mode_transition.resolve`
- 立即完成 session/principal ownership 重构
- 一次性重写所有桥接逻辑

---

## 6. 目标行为

### 6.1 AskUserQuestion

目标行为：

1. SDK 即将执行 `AskUserQuestion`
2. `canUseTool` 命中 gating
3. 服务端注册 pending interaction
4. 发出 `interaction.requested(kind='ask_user')`
5. SDK 原 run 在此挂起
6. 前端提交 `interaction.resolve`
7. 服务端调用 resolver
8. 原 run 恢复，并继续当前任务

### 6.2 Bash / Edit / Write / Read

目标行为：

1. SDK 即将执行受控工具
2. `canUseTool` 挂起
3. 服务端发出 `interaction.requested(kind='exec')`
4. 用户允许 / 拒绝
5. resolver 将结果返回 SDK
6. 原 run 在同一执行流中继续

### 6.3 ExitPlanMode

短期保持前端协议兼容：

- 继续允许 `mode_transition.resolve`
- 但底层与其他 interaction 共用统一 registry / resolver 模型

长期可收敛到：

- 统一走 `interaction.resolve`

---

## 7. 设计原则

### 7.1 single-run single-active-interaction

P1 起明确采用约束：

- 同一个 run 在任一时刻最多只有一个 active interaction
- interaction resolve / expire 后才允许下一次 interaction 注册
- 同一个 interaction 只能成功 resolve 一次

### 7.2 统一 interactionId

interactionId 必须成为：

- 前端渲染标识
- registry 索引
- runtime resolver 关联键
- 超时 / abort / replay 的唯一主键

### 7.3 resolver 优先，continuation fallback

所有 `interaction.resolve` 逻辑都必须遵循：

1. 如果 pending 上有 runtime resolver，则优先恢复原 run
2. 如果没有 resolver，再 fallback 到 `continueByFollowUpChat(...)`

### 7.4 渐进式迁移

改造过程中允许 SDK 路径使用 suspend/resume，而旧路径继续保留 continuation。

---

## 8. 关键改造方案

## 8.1 `src/chat-orchestrator.ts`

### 目标

把 runtime HITL 能力从上层一路传到 Claude SDK runner。

### 当前问题

当前 `ChatRunnerFactory` / `OrchestratorInput` 只支持：

- `cwd`
- `message`
- `systemPrompt`
- `model`
- `mode`
- `resumeSessionId`

但不支持：

- `runId`
- `sessionKey`
- `onInteractionRequested`

因此 `claude-sdk-bridge.ts` 虽然已经支持 `canUseTool` 和 `onInteractionRequested`，但 orchestrator 层无法把它们传进去。

### 改造要求

扩展输入参数：

```ts
runId?: string;
sessionKey?: string;
onInteractionRequested?: (event: RuntimeInteractionRequestedEvent) => void;
```

并将其透传到 runner。

---

## 8.2 `src/claude-sdk-bridge.ts`

### 目标

把已实现的 `canUseTool` 正式接入主链路。

### 改造要求

#### A. 明确工具 gating 策略

新增：

```ts
function shouldGateTool(toolName: string, input: Record<string, unknown>): boolean
```

默认纳入：

- `AskUserQuestion`
- `ExitPlanMode`
- `Bash`
- `Edit`
- `Write`
- `Read`（是否默认审批可配置）

#### B. `canUseTool` 只拦截受控工具

对于未命中 gating 的工具，应直接返回允许，不进入 interaction 流程。

#### C. runtime wait 与 gateway registry 对齐

`canUseTool` 产生的等待点必须能绑定到 gateway 层 `PendingInteraction` 记录。

建议要求：

- `interactionId` 在 runtime wait 和 gateway pending record 中完全一致
- runtime wait 的 resolve / reject 能通过 registry 可达

#### D. 清理逻辑一致化

以下情况都必须同步清理 runtime wait 和 interaction registry：

- abort
- timeout
- fatal error
- duplicate resolve

---

## 8.3 `src/interaction/types.ts`

### 目标

将 `PendingInteraction` 升级为真正承载 runtime 恢复能力的结构。

### 改造要求

建议补充以下字段：

```ts
createdAtMs: number;
resolver?: (resolution: ResolvedInteractionInput) => void;
rejecter?: (error: Error) => void;
runtimeSource?: 'sdk-canUseTool' | 'followup';
status?: 'pending' | 'resolved' | 'expired' | 'cancelled';
```

说明：

- `createdAtMs` 不应再依赖 `expiresAtMs - timeout` 倒推
- `resolver` / `rejecter` 需要成为正式 runtime contract
- `runtimeSource` 便于迁移期兼容
- `status` 便于防止重复 resolve

---

## 8.4 `src/interaction/registry.ts`

### 目标

将 registry 从“待处理交互列表”升级为“待处理 + resolver 分发中心”。

### 改造要求

新增能力：

```ts
resolve(id: string, input: ResolvedInteractionInput): boolean;
reject(id: string, err: Error): boolean;
hasActiveRunInteraction(runId: string): boolean;
getForSession(sessionKey: string): PendingInteraction[];
```

### 行为要求

1. `register()` 时校验 `single-run single-active-interaction`
2. `resolve()` 保证幂等，一个 interaction 只能 resolve 一次
3. `cancelForConnection()` / `takeForRun()` 不能只删除记录，需要触发 reject / expire 逻辑
4. scanner 超时删除时必须触发统一的 onExpire / rejecter

---

## 8.5 `src/interaction/resolve.ts`

### 目标

`interaction.resolve` / `mode_transition.resolve` 优先恢复原 run，而不再默认 continuation。

### 当前问题

当前核心逻辑是：

```ts
continueByFollowUpChat(...)
```

文件中甚至已经留有注释：

```ts
// In stage-2, replace with: if (pending.resolver) pending.resolver({ ...params, phase });
```

### 改造要求

将逻辑改为：

```ts
if (pending.resolver) {
  pending.resolver({ ...params, phase });
} else {
  continueByFollowUpChat(...)
}
```

#### `interaction.resolve`

- 优先使用 `pending.resolver`
- 无 resolver 时 fallback continuation

#### `mode_transition.resolve`

- 同样优先使用 `pending.resolver`
- 无 resolver 时 fallback continuation

### 结果

这样既能：

- 保持现有前端 RPC 兼容
- 又能逐步把底层切换到 runtime suspend/resume

---

## 8.6 `src/gateway/handlers/chat.ts`

### 目标

在 chat.send 启动时，把 runtime HITL 所需上下文传入 chat runner。

### 改造要求

启动 run 时传入：

- `runId`
- `sessionKey`
- `onInteractionRequested`

`onInteractionRequested` 回调职责：

1. 根据 runtime tool request 构造统一 interaction event
2. 构造 `PendingInteraction`
3. 绑定 resolver / rejecter
4. 注册到 registry
5. 发出 `interaction.requested` 或兼容的 `mode_transition` 请求事件

---

## 8.7 `src/interaction/builders.ts`

### 目标

让 interaction builder 能同时服务于：

- 旧的 `toolEnd` 拦截路径
- 新的 `canUseTool` runtime hook 路径

### 改造要求

新增基于 runtime tool request 的 builder：

```ts
buildInteractionFromRuntimeToolRequest({
  interactionId,
  runId,
  sessionKey,
  toolName,
  toolCallId,
  input,
  cwd,
})
```

避免 builder 只能依赖 `event.tool` 结构。

---

## 8.8 `src/gateway/orchestrator-bridge.ts`

### 目标

将其从 HITL 主实现降级为：

- 普通事件转发器
- continuation fallback 兼容路径

### 当前问题

当前主流程在 `toolEnd` 时：

- 识别 AskUserQuestion / ExitPlanMode / Bash/Edit/Write/Read
- 注册 interaction
- `running.abort()`

这正是当前不能原运行恢复的根源。

### 改造要求

引入模式开关，例如：

```ts
hitlRuntimeMode: 'continuation' | 'sdk_suspend_resume'
```

#### 当为 `sdk_suspend_resume`

- 不再在 `toolEnd` 上创建 pending interaction
- 不再在这里 `running.abort()`
- interaction 由 SDK `canUseTool` 路径产生

#### 当为 `continuation`

- 维持现有逻辑，作为兼容 fallback

---

## 8.9 `src/interaction/continuation.ts`

### 目标

保留该模块，但不再作为主路径。

### 改造要求

- 继续保留文件与逻辑
- 注释中明确标注为 fallback-only
- 仅当 pending interaction 不包含 runtime resolver 时才触发

---

## 9. 数据流设计

## 9.1 新主流程

### interaction.requested

1. `chat.send`
2. `startChatRun(...)`
3. SDK runner 设置 `canUseTool`
4. Claude 准备调用受控工具
5. `canUseTool` 创建 runtime wait
6. 通过 `onInteractionRequested(...)` 通知 gateway
7. gateway 构造 `PendingInteraction(resolver=...)`
8. registry 注册 pending
9. 发出 `interaction.requested`
10. SDK run 在 Promise 上 await

### interaction.resolve

1. 前端发送 `interaction.resolve`
2. `handleInteractionResolve` 从 registry 中取出 pending
3. 发出 `interaction.resolved`
4. 优先调用 `pending.resolver(...)`
5. runtime wait 被 resolve
6. SDK 原 run 恢复
7. 后续 chat / agent 事件继续沿用同一个 runId

---

## 9.2 fallback 流程

仅当 pending 没有 runtime resolver 时：

1. `interaction.resolve`
2. 发出 `interaction.resolved`
3. 调用 `continueByFollowUpChat(...)`

---

## 10. 协议兼容要求

### 10.1 保持现有前端协议

本期继续兼容：

- `interaction.requested`
- `interaction.resolved`
- `interaction.resolve`
- `mode_transition.resolve`

### 10.2 phase 映射保持稳定

建议继续沿用当前代码中的 phase 语义：

- `ask_user:submit -> answered`
- `ask_user:cancel -> cancelled`
- `exec:allow-once -> allowed`
- `exec:allow-always -> allowed`
- `exec:deny -> denied`
- `mode_switch:proceed -> allowed`
- `mode_switch:stay -> denied`

---

## 11. 测试要求

## 11.1 单元测试

### `src/claude-sdk-bridge.ts`

- `canUseTool` 命中时会注册 pending wait
- `resolveToolApproval(...)` 能恢复 Promise
- `rejectToolApproval(...)` 能拒绝 Promise
- abort 时 pending wait 被清理
- timeout 时 pending wait 被拒绝

### `src/interaction/registry.ts`

- 同一个 run 不能同时注册多个 active interaction
- resolve 幂等
- expire 会触发 onExpire / rejecter
- cancelForConnection 会清理并 reject

### `src/interaction/resolve.ts`

- 有 `pending.resolver` 时不会进入 continuation
- 无 resolver 时才 fallback continuation
- `mode_transition.resolve` 同样满足上述行为

---

## 11.2 集成测试

### AskUserQuestion

- 发起 chat
- 收到 `interaction.requested`
- `interaction.resolve` 回答问题
- 原 run 在同一个 runId 上继续输出
- 不产生新的 follow-up run

### Bash approval

- 发起 chat
- 收到 `interaction.requested(kind='exec')`
- `allow-once` 后工具在原 run 执行
- 后续 tool / command_output 正常流出

### deny path

- 发起受控工具请求
- `deny` 后原 run 不执行该工具
- 模型继续 alternative path 或结束

### reconnect replay

- interaction pending 时断开重连
- 可以 replay pending card
- resolve 后原 run 继续

---

## 12. 实施步骤建议

### Phase 1：最小接通

1. 扩展 `chat-orchestrator.ts` 输入，把 `onInteractionRequested` / `runId` / `sessionKey` 透传到 SDK runner
2. 在 `interaction.resolve` 和 `mode_transition.resolve` 中优先执行 `pending.resolver`
3. continuation 保留为 fallback

### Phase 2：统一 registry contract

4. 补强 `PendingInteraction` 的 runtime 字段
5. 给 `PendingInteractionRegistry` 增加 resolve / reject / run-level guard 能力
6. 清理 abort / timeout / expire 的统一行为

### Phase 3：切换主路径

7. SDK 模式下禁用 `orchestrator-bridge.ts` 的 `toolEnd -> abort -> continuation` 主逻辑
8. interaction requested 改由 SDK `canUseTool` 路径统一产生
9. continuation 仅保留给 CLI bridge 或旧兼容路径

---

## 13. 验收标准

当以下条件全部满足时，可认为该能力完成：

1. `AskUserQuestion` 不再依赖 follow-up continuation，而是在原 run 中恢复
2. `Bash/Edit/Write/Read` 的审批不再通过中断 run 实现
3. `mode_transition.resolve` 能通过统一 resolver 恢复原运行
4. `interaction.resolve` 有 resolver 时不会触发 `continueByFollowUpChat(...)`
5. pending interaction 的 replay / timeout / abort 行为保持可用
6. 同一 run 不会同时存在多个活跃 interaction

---

## 14. 总结

当前代码并非完全没有 stage-2 基础，相反，关键部件已经部分出现：

- `claude-sdk-bridge.ts` 已有 `canUseTool`
- `interaction/types.ts` 已预留 `resolver`
- `interaction/resolve.ts` 已明确写出 stage-2 替换点

真正缺失的是：

- orchestrator 到 SDK runner 的参数透传
- runtime wait 与 gateway registry 的统一建模
- resolve 路径从 continuation 切换到 resolver
- SDK 模式下停用 `toolEnd -> abort` 这条旧主路径

因此本功能属于：

- **基础已具备，主链路未接通**
- **适合按阶段渐进完成**

