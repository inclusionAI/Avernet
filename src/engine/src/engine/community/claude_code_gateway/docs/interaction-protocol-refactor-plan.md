# WebSocket/HITL Interaction Protocol Refactor Plan

## 1. 文档目标

本文档基于 `docs/interaction-protocol-design.md`，给出当前项目的具体改造方案，目标是：

- 将所有 HITL 场景统一收敛到 `interaction.*`
- 减少顶层 `event` / `method` 类型
- 为后续从 follow-up continuation 迁移到原运行挂起/恢复保留演进空间
- 在尽量少破坏当前功能的前提下，完成协议层收口

本文档定位为**落地实施文档**，重点覆盖：

- `src/types.ts`
- `src/server.ts`
- `src/chat-orchestrator.ts`
- `src/claude-sdk-bridge.ts`
- 文档、兼容策略与测试建议

---

## 2. 改造目标与范围

### 2.1 当前已有能力

当前项目已经具备如下能力：

- 统一 WebSocket frame：`req / res / event`
- `chat` / `agent` 双通道
- `interaction.requested` / `interaction.resolved`
- 可识别 `AskUserQuestion`、`ExitPlanMode`、`Bash/Edit/Write/Read`
- 可通过 follow-up continuation 继续任务

### 2.2 当前主要问题

当前仍存在以下问题：

1. `ExitPlanMode` 仍然走独立 `mode_transition.resolve`
2. `interaction` payload 结构不统一
3. 执行审批模型过粗，缺少稳定的 `subject` 结构
4. 顶层协议与 `agent.stream` 概念仍有重叠
5. 服务端内部 registry 仍按 approval / interaction / modeTransition 分裂维护
6. follow-up continuation 逻辑还未形成统一 contract

### 2.3 本次改造边界

本次改造目标不是“重写整套桥接逻辑”，而是：

- **先统一 Interaction 协议出口**
- **再统一 Interaction payload schema**
- **最后收敛内部状态和 handler 结构**

P0 / P1 阶段不追求立即重写 SDK 运行时挂起/恢复逻辑。

---

## 3. 优先级与阶段划分

### 3.1 P0：必须改

1. `src/types.ts` 中 interaction 相关类型重构
2. `src/server.ts` 中统一 `interaction.resolve`，弱化/兼容 `mode_transition.resolve`
3. `src/server.ts` 中统一 interaction payload 构造逻辑
4. `ExitPlanMode` 改为走 `interaction.requested(kind='mode_switch')`
5. `Bash/Edit/Write(/Read)` 统一走 `interaction.requested(kind='confirm')`，并补齐 `subject`
6. `interaction.resolved` 统一 phase / decision 映射

### 3.2 P1：建议同步改

7. 服务端 pending registries 收敛为统一 `pendingInteractions`
8. `agent.stream = mode_transition / approval` 标记为兼容流，降低前端依赖性
9. 文档和注释同步收口，避免未来继续长出新协议分支
10. 明确 **single-run single-active-interaction** 约束，并在代码中显式校验
11. 为 follow-up continuation 建立统一 contract，避免各 handler 各自拼 prompt

### 3.3 P2：后续阶段改

12. `claude-sdk-bridge.ts` 升级为支持运行时挂起/恢复
13. 接入 pending interaction 重连回放
14. 精简 `agent.stream` 对前端暴露的稳定集合
15. 将 interaction 所有权从 `connId` 约束演进到 session/principal 约束

---

## 4. 统一实现约束

### 4.1 Interaction 边界

只有真正阻塞 run、且必须等待用户输入才能继续的节点，才应进入 `interaction.*` 主协议。

以下内容不应在本轮继续扩散为新的 interaction 分支：

- 普通 tool 展示流
- agent 内部生命周期流
- thinking / command output 等观测流
- 默认无需审批的只读行为

### 4.2 run 与 interaction 约束

P0 / P1 阶段建议显式采用：

- **single-run single-active-interaction**

即：

- 同一个 run 在前一个 interaction `resolved/expired` 之前，不再注册新的 interaction
- resolve 后 run 可能继续，也可能结束
- 同一个 interaction 只能进入一个最终态

### 4.3 所有权模型提前留口

不要把 `connId` 固化成最终 authority 模型。建议策略：

- P0：resolve 时继续校验 `connId`，保证最小改动
- 同时在 `PendingInteraction` 中保留 `sessionKey`
- 为后续 `actorId` / `principal` 预留字段

这样后续从“当前连接可处理”演进到“当前会话主体可处理”时，不需要再次推翻 registry 模型。

---

## 5. 分文件改造方案

## 5.1 `src/types.ts` 改造方案

### 当前问题

当前 `src/types.ts` 中：

- `InteractionRequestedEvent` 只覆盖 `ask_user`
- `InteractionResolvedEvent` 结构偏弱
- `ExecApproval*` 仍然独立存在
- `ModeTransition*` 仍然独立存在
- `InteractionResolveParams` 仍是旧格式：`action/message`

这会导致协议定义与目标设计不一致。

### 改造目标

在 `types.ts` 中建立统一 Interaction Schema，让：

- `ask_user`
- `confirm`
- `mode_switch`

三类交互共享一个正式类型体系。

### 建议改动

#### 新增或重构以下类型

```ts
export type InteractionKind = 'ask_user' | 'confirm' | 'mode_switch';

export type InteractionSubject = {
  type?: 'tool' | 'command' | 'file' | 'mode';
  toolName?: string;
  toolCallId?: string;
  command?: string;
  cwd?: string;
  filePath?: string;
  fromMode?: string;
  toMode?: string;
};

export type InteractionOption = {
  value: string;
  label: string;
  description?: string;
  recommended?: boolean;
};

export type InteractionUiHints = {
  variant?: 'question' | 'warning' | 'plan';
  severity?: 'info' | 'warning' | 'danger';
  collapsible?: boolean;
};

export type InteractionInputSchema = {
  type: 'none' | 'text' | 'choices' | 'form';
  multiSelect?: boolean;
};
```

#### 重构 `InteractionRequestedEvent`

```ts
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
  inputSchema?: InteractionInputSchema;
  uiHints?: InteractionUiHints;
  createdAtMs: number;
  expiresAtMs: number;
};
```

#### 重构 `InteractionResolvedEvent`

```ts
export type InteractionPhase = 'answered' | 'approved' | 'denied' | 'cancelled' | 'expired';

export type InteractionResolvedEvent = {
  interactionId: string;
  runId: string;
  sessionKey: string;
  kind: InteractionKind;
  phase: InteractionPhase;
  decision: string;
  answer?: string;
  values?: Record<string, unknown>;
  selectedOptions?: string[];
  resolvedBy: string;
  resolvedAtMs: number;
};
```

#### 重构 `InteractionResolveParams`

```ts
export type InteractionResolveParams = {
  interactionId: string;
  decision: string;
  answer?: string;
  values?: Record<string, unknown>;
  selectedOptions?: string[];
  meta?: Record<string, unknown>;

  // backward compat
  action?: string;
  message?: string;
};
```

#### 废弃类型处理建议

- `ModeTransition*`：短期保留并标记 `deprecated, use interaction.* instead`
- `ExecApproval*`：短期保留并标记 deprecated，仅用于内部兼容

### 结果

改造后，`types.ts` 会成为唯一协议真相源，避免 `server.ts` 中大量“按 case 临时拼对象”。

---

## 5.2 `src/server.ts` 改造方案

这是本次改造的核心文件。

### A. 顶层方法路由收口

#### 当前现状

当前请求路由里有：

- `interaction.resolve`
- `mode_transition.resolve`
- backward compat alias

#### 目标

正式协议只保留：

- `interaction.resolve`

#### 改造建议

保留 `mode_transition.resolve` 作为兼容 alias：

```ts
if (frame.method === 'interaction.resolve') return handleInteractionResolve(ctx, frame);
if (frame.method === 'mode_transition.resolve') return handleModeTransitionResolveCompat(ctx, frame);
```

兼容 handler 只做参数转换：

```ts
function handleModeTransitionResolveCompat(ctx, frame) {
  const params = frame.params ?? {};
  return handleInteractionResolve(ctx, {
    ...frame,
    method: 'interaction.resolve',
    params: {
      interactionId: params.transitionId,
      decision: params.decision,
    },
  });
}
```

同时在 `hello/capabilities` 中不再暴露 `mode_transition.resolve` 为推荐方法。

### B. 统一 pending registry

#### 当前现状

`ConnectionContext` 中存在：

- `pendingApprovals`
- `pendingInteractions`
- `pendingModeTransitions`

同时外部 registry 也拆成：

- approval registry
- interaction registry
- mode transition registry

#### 目标

统一成单一 registry：

```ts
type PendingInteraction = {
  connId: string;
  runId: string;
  sessionKey: string;
  kind: InteractionKind;
  subject?: InteractionSubject;
  prompt?: string;
  questions?: InteractionQuestion[];
  options?: InteractionOption[];
  inputSchema?: InteractionInputSchema;
  uiHints?: InteractionUiHints;
  expiresAtMs: number;
  onExpire?: () => void;

  // ownership / authorization
  actorId?: string;

  // stage-2 runtime continuation support
  resolver?: (resolution: ResolvedInteractionInput) => void;
};
```

#### 改造方式

第一步：在 `ConnectionContext` 中统一为：

```ts
pendingInteractions = new Map<string, PendingInteraction>();
```

第二步：将 register/take helper 合并：

- `registerPendingInteraction(id, pending)`
- `takePendingInteraction(id)`
- `peekPendingInteraction(id)`
- `expirePendingInteraction(id)`

### C. 统一 interaction payload 构造

#### 当前现状

目前 `server.ts` 中分散存在：

- `emitInteractionForExec`
- `emitInteractionRequested`
- `emitModeTransitionRequested`

#### 目标

统一为一个标准构造路径：

```ts
function emitInteractionRequested(
  ctx,
  payload: InteractionRequestedEvent,
  pendingMeta: PendingInteraction,
)
```

#### 改造建议

新增三个 builder：

```ts
buildAskUserInteraction(...): InteractionRequestedEvent
buildConfirmInteraction(...): InteractionRequestedEvent
buildModeSwitchInteraction(...): InteractionRequestedEvent
```

然后所有 emit 都走统一函数：

```ts
emitInteractionRequested(ctx, requestedEvent, pendingMeta)
```

### D. `handleInteractionResolve` 重构

#### 当前现状

现在的 `handleInteractionResolve` 逻辑是：

1. 尝试从 approval registry 取 pending
2. 如果没有，再尝试从 interaction registry 取
3. ask_user 场景需要 special case
4. mode_switch 还在另一套 handler 里

#### 目标

改造成：

1. 从统一 `pendingInteractions` 中查一次
2. 根据 `kind` 决定合法 `decision`
3. 统一生成 `interaction.resolved`
4. 根据当前阶段实现选择：
   - follow-up continuation
   - 或 runtime resolver

#### 建议实现结构

```ts
async function handleInteractionResolve(ctx, frame) {
  const params = normalizeInteractionResolveParams(frame.params);
  const pending = takePendingInteraction(params.interactionId);
  if (!pending) return notFound;

  validateDecisionForKind(pending.kind, params.decision);

  const phase = mapDecisionToPhase(pending.kind, params.decision);
  const resolvedEvent = buildInteractionResolvedEvent(pending, params, phase);

  emitResolvedEvent(ctx, resolvedEvent);

  if (pending.resolver) {
    pending.resolver({ ...params, phase });
  } else {
    await continueByFollowUpChat(ctx, pending, params);
  }

  return ok;
}
```

#### 需要拆出的辅助函数

- `normalizeInteractionResolveParams()`
- `validateDecisionForKind()`
- `mapDecisionToPhase()`
- `buildInteractionResolvedEvent()`
- `continueByFollowUpChat()`

#### 补充建议：不要让 `continueByFollowUpChat()` 变成新的垃圾桶

`continueByFollowUpChat()` 适合作为阶段一兼容实现，但应限制其职责：

- 它只负责触发 follow-up continuation
- 各类 interaction 的“resolution -> continuation message”转换，应拆成独立 helper

建议新增：

- `buildAskUserContinuationPrompt(...)`
- `buildConfirmContinuationPrompt(...)`
- `buildModeSwitchContinuationPrompt(...)`

### E. `ExitPlanMode` 并入 interaction

#### 目标

改成：

- `buildModeSwitchInteraction(...)`
- `emitInteractionRequested(...)`
- `interaction.resolve(decision=proceed|stay)`

#### 具体改法

在 `toolEnd` 分支中，将旧的 `emitModeTransitionRequested(...)` 替换为统一 interaction builder + emit 路径。

### F. `Bash/Edit/Write(/Read)` 统一为 `confirm`

#### 问题

当前旧执行审批模型过粗：

- `Edit/Write` 被粗暴伪装成 command string
- 缺少 `filePath/toolName/type`
- 前端无法稳定区分命令审批和文件审批

#### 改造建议

新增 builder：

```ts
buildConfirmInteraction({
  interactionId,
  runId,
  sessionKey,
  tool,
  cwd,
})
```

##### Bash

```ts
{
  kind: 'confirm',
  title: 'Command approval required',
  description: 'Claude wants to execute a shell command.',
  subject: {
    type: 'command',
    toolName: 'Bash',
    toolCallId: tool.id,
    command: tool.input.command,
    cwd,
  },
  inputSchema: { type: 'none' },
  uiHints: { variant: 'warning', severity: 'warning' }
}
```

##### Edit / Write

```ts
{
  kind: 'confirm',
  title: 'File change approval required',
  description: 'Claude wants to modify a file.',
  subject: {
    type: 'file',
    toolName: tool.name,
    toolCallId: tool.id,
    filePath: tool.input.file_path,
  },
  inputSchema: { type: 'none' },
  uiHints: { variant: 'warning', severity: 'warning' }
}
```

##### Read

建议短期：

- 先保持现状兼容
- 但配置一个开关，默认不进入主 interaction 流

例如：

```ts
const SHOULD_INTERACT_FOR_READ = false;
```

### G. `AskUserQuestion` 保持主出口，但统一 schema

#### 目标

- 统一 payload 字段命名
- 增加 `title/subject/inputSchema/uiHints`
- resolve 时统一使用 `decision/answer/values/selectedOptions`
- 不再用旧的 `action/message` 作为主模型

建议新增 builder：

```ts
buildAskUserInteraction({
  interactionId,
  runId,
  sessionKey,
  tool,
})
```

### H. `interaction.resolved` 统一化

#### 改造目标

统一 `phase` 集合：

- `answered`
- `approved`
- `denied`
- `cancelled`
- `expired`

建议新增：

```ts
function mapDecisionToPhase(kind: InteractionKind, decision: string): InteractionPhase
```

### I. `agent` 流兼容策略

短期不一定删，但要做两件事：

1. 在代码注释和文档中标为兼容/内部流
2. 不再让前端新实现依赖它们

推荐：

- 顶层 `interaction.*` 是正式协议
- `agent.stream='interaction'` 仅作为审计/调试镜像存在

---

## 5.3 `src/chat-orchestrator.ts` 改造建议

### 结论

**P0 阶段不建议大改。**

原因：

- 当前 interaction 主逻辑在 `server.ts` 的 gateway 层完成
- orchestrator 保持协议中立是优点

### 仅建议两点小改

1. 在注释中明确：
   - orchestrator 只输出中性事件
   - interaction 语义转换在 gateway 层完成
2. 为后续 P2 挂起/恢复能力预留 continuation hooks 扩展点

---

## 5.4 `src/claude-sdk-bridge.ts` 改造建议

### 当前现状

当前 bridge 已经能：

- 解析 SDK 事件
- 抽取 tool use / thinking / message / usage / session_id
- 把结果通过 handlers 回传给 orchestrator

但它还没有：

- `canUseTool`
- interaction resolver registry
- 原运行挂起/恢复能力

### P0 建议

**本轮不强改运行时逻辑。**

原因：

- 风险大
- 容易牵动完整 SDK 行为
- 当前协议统一工作主要在 gateway 层完成即可

### P2 方向建议

目标：

- 支持 tool-level await
- 服务端注入 resolver
- `interaction.resolve` 后直接恢复原 run

实现方向：

- 在 SDK options 中接入 `canUseTool`
- 遇到交互型工具时创建 pending interaction promise
- 将 resolver 存到 registry
- `interaction.resolve` 触发 resolver

注意：

- 不建议直接照搬 `claudecodeui` 的 `permission_request` 模型
- SDK hook 层只负责“挂起 / 等待 / 继续”
- Gateway 层仍负责“发 `interaction.requested` / 收 `interaction.resolve` / 发 `interaction.resolved`”

---

## 6. 建议的代码改造顺序

### 第一步：类型先行

先改 `src/types.ts`：

1. 新增统一 Interaction Schema
2. 标记 `ModeTransition*` / `ExecApproval*` deprecated
3. 给 `InteractionResolveParams` 加 `decision/answer/values/selectedOptions`

### 第二步：server 统一 emit 逻辑

在 `src/server.ts` 中：

1. 新增 `buildAskUserInteraction`
2. 新增 `buildConfirmInteraction`
3. 新增 `buildModeSwitchInteraction`
4. 新增统一 `emitInteractionRequested`
5. 替换掉旧的三个专用 emitter

### 第三步：server 统一 resolve 逻辑

1. 新增统一 `pendingInteractions`
2. 重写 `handleInteractionResolve`
3. 把 `mode_transition.resolve` 改成 compat wrapper
4. 删除专用 mode transition handler 的业务主逻辑

### 第四步：收口 `bridgeOrchestratorToGateway`

在 `toolEnd` 分支中：

- `AskUserQuestion` -> `buildAskUserInteraction`
- `ExitPlanMode` -> `buildModeSwitchInteraction`
- `Bash/Edit/Write` -> `buildConfirmInteraction`

全部走统一 interaction path。

### 第五步：兼容与清理

1. 更新 `hello/capabilities`
2. 更新注释与文档
3. 将 `agent.stream='mode_transition'` 标为 compat only
4. 增加基础测试

---

## 7. 建议新增的辅助函数清单

### 7.1 参数归一化

- `normalizeInteractionResolveParams(params)`
- `normalizeDecision(decision)`

### 7.2 构建器

- `buildAskUserInteraction(opts)`
- `buildConfirmInteraction(opts)`
- `buildModeSwitchInteraction(opts)`
- `buildInteractionResolvedEvent(pending, params)`

### 7.3 校验器

- `validateDecisionForKind(kind, decision)`
- `mapDecisionToPhase(kind, decision)`

### 7.4 Registry

- `registerPendingInteraction(id, pending)`
- `takePendingInteraction(id)`
- `expirePendingInteraction(id)`
- `replayPendingInteractionsForSession(sessionKey)`（后续）

### 7.5 Continuation

- `continueByFollowUpChat(ctx, pending, params)`
- `buildAskUserContinuationPrompt(...)`
- `buildConfirmContinuationPrompt(...)`
- `buildModeSwitchContinuationPrompt(...)`
- `continueByResolver(pending, params)`（后续）

这样可以明显降低 `server.ts` 继续膨胀的风险。

---

## 8. 兼容策略

### 8.1 保留但降级的内容

- `mode_transition.resolve`：保留 1~2 个版本作为兼容 alias
- 旧 `action/message` 参数：服务端继续接受，但内部转为 `decision/answer`
- 旧 `ExecApproval*` 类型：仅内部兼容，不再推荐使用
- `agent.stream='mode_transition'`：保留镜像一段时间，但不再推荐前端依赖

### 8.2 应立即更新的内容

- 所有新文档只写 `interaction.*`
- 所有新前端只接 `interaction.*`
- 所有新逻辑都不要再新增 `approval.*` 或 `mode_transition.*` 顶层协议

---

## 9. 测试建议

### 9.1 协议层测试

1. `AskUserQuestion` -> 发出 `interaction.requested(kind=ask_user)`
2. `ExitPlanMode` -> 发出 `interaction.requested(kind=mode_switch)`
3. `Bash` -> 发出 `interaction.requested(kind=confirm, subject.type=command)`
4. `Edit/Write` -> 发出 `interaction.requested(kind=confirm, subject.type=file)`

### 9.2 resolve 测试

5. `interaction.resolve(submit)` -> `interaction.resolved(phase=answered)`
6. `interaction.resolve(approve)` -> `interaction.resolved(phase=approved)`
7. `interaction.resolve(deny)` -> `interaction.resolved(phase=denied)`
8. `interaction.resolve(proceed/stay)` -> `interaction.resolved(phase=approved/denied)`

### 9.3 兼容测试

9. `mode_transition.resolve` 仍可成功转发为 interaction resolve
10. 旧 `action=submit,message=...` 仍可兼容

### 9.4 过期测试

11. interaction 超时后发出 `interaction.resolved(phase=expired)`
12. 已过期 interaction 再 resolve 返回 `NOT_FOUND` 或 `EXPIRED`

### 9.5 并发与重入测试

13. 同一个 interaction 被连续 resolve 两次，第二次返回 `NOT_FOUND` 或 `CONFLICT`
14. interaction 过期与客户端 resolve 并发发生时，只能产生一个最终态
15. 重连 replay 后 resolve 仍能命中正确 pending interaction
16. 同一 run 尝试注册第二个 active interaction 时被拒绝或显式串行化
17. 旧 alias 与新 `interaction.resolve` 混用时，仍只生成一次最终 resolved 事件

---

## 10. 最终建议

如果只做一轮“最值回票价”的改造，我建议优先完成下面 5 件事：

1. **重构 `src/types.ts` 的 Interaction Schema**
2. **把 `ExitPlanMode` 并入 `interaction.*`**
3. **把 `Bash/Edit/Write` 统一为 `confirm + subject` 模型**
4. **统一 `handleInteractionResolve`**
5. **把 pending registry 收敛成一套**

完成这 5 件事后：

- 协议层就已经符合“所有 HITL 交互都以 `interaction.*` 为主”的目标
- 前端可以开始按稳定 schema 接入
- 后续再升级 SDK 运行时，也不会再动顶层协议

这也是当前项目最合理、风险最低、收益最高的改造路径。

---

## 11. 实现状态（2026-04）

> **注**：以下实现状态记录了 HITL 挂起/恢复机制的落地进展。原详细设计文档 `hitl-suspend-resume-design.md` 已合并到本文档。

### 11.1 Phase 完成状态

| Phase | 描述 | 状态 | 代码位置 |
|-------|------|------|----------|
| Phase 1 | Bridge Runtime 基础能力 | ✅ 完成 | `src/claude-sdk-bridge.ts` |
| Phase 2 | Orchestrator 透传 Interaction Runtime Event | ✅ 完成 | `src/chat-orchestrator.ts` |
| Phase 3 | Server 统一接管 Interaction Pending | ✅ 完成 | `src/server.ts` |
| Phase 4 | 删除 Follow-up Continuation | ✅ 完成 | 已删除 `continueByFollowUpChat` 等函数 |
| Phase 5 | Reconnect 与 History 收口 | ✅ 完成 | `src/server.ts` |

### 11.2 关键实现

#### SDK Bridge (`src/claude-sdk-bridge.ts`)

- `canUseTool` hook 注入
- `PendingToolWait` registry
- `resolveToolApproval(interactionId, result)` - 接收 resolve 结果恢复 SDK 流
- `rejectToolApproval(interactionId, error)` - 处理 abort/expire
- `InteractionRequestedRuntimeEvent` 类型定义

#### Orchestrator (`src/chat-orchestrator.ts`)

- `OrchestratorEvent` 新增 `interactionRequested` 事件
- `StartChatRunOptions` 新增 `useSdkBridge` 选项
- `runId` / `sessionKey` 透传支持
- `startSdkBridgeRun()` 函数实现

#### Server (`src/server.ts`)

- `activeInteractionByRunId` 索引实现 single-run single-active-interaction 约束
- `pendingInteractions` registry 维护
- `registerPendingInteraction()` / `resolvePendingInteraction()` 生命周期管理
- interaction expire 定时扫描
- WebSocket 断开时不清理 pending interaction（支持 reconnect replay）

### 11.3 已删除的旧逻辑

- `continueByFollowUpChat`
- `buildAskUserContinuationPrompt`
- `buildConfirmContinuationPrompt`
- `buildModeSwitchContinuationPrompt`
- `toolEnd` 中的交互分流逻辑（迁移到 runtime hook 触发）

### 11.4 协议兼容性

- 前端协议保持 `interaction.*` 不变
- `interaction.requested` / `interaction.resolved` / `interaction.resolve` 协议保持兼容
- interaction 前后 runId 保持不变（不再创建 follow-up run）

### 11.5 遗留待办

- history 落库策略的 interaction-aware 记录（规划中）
- 多 active interaction 并发模型（非本次范围）
