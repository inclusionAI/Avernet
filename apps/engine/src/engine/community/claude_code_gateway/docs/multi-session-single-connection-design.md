# 单连接多会话并发支持方案设计

## 1. 背景

当前 `claude-code-gateway` 已具备以下能力：

- 基于 WebSocket 的网关通信
- 通过 `sessionKey` 维度管理会话历史
- 通过 `chat.send` 启动 Claude SDK / CLI run
- 通过 unified interaction 协议处理 HITL（approval / ask_user / mode_switch）
- 基于 `sdkSessionId` 支持后续会话续接

但从运行态归属模型来看，当前系统仍以**连接（connId）**为中心：

- active run 挂在 `ConnectionContext.activeRuns`
- pending interaction 绑定 `connId`
- `interaction.resolve` 只能由原连接处理
- 连接关闭时会 abort 该连接下所有 run，并 cancel 该连接下所有 pending interaction

这会导致系统虽然可以在一个连接上发起多个请求，但并不真正支持：

- 一个连接承载多个会话的稳定并发
- 连接断开后的会话恢复
- 同一会话跨连接恢复 pending interaction
- 多连接共同观察同一会话

---

## 2. 设计目标与范围

### 2.1 核心目标

本方案目标是支持：

1. **一个 WebSocket 连接可以同时承载多个会话**
2. **不同 session 之间可以并发运行**
3. **session 的运行态独立于连接**
4. **连接断开后，session 可在短时间内保活并被新连接接管**
5. **pending interaction 按 session 恢复，而不是按 connection 恢复**

### 2.2 第一阶段的明确范围（Phase 1）

Phase 1 只解决“单连接多 session 并发”和“session 级恢复”两个核心问题，不追求一步到位支持所有观察者/控制者模型。

**Phase 1 支持：**

- 一个 connection attach 多个 session
- 不同 session 并发运行
- 同一 session 在连接断开后短时间保活
- 新连接在 grace 期内重新 attach 并恢复 control
- pending interaction 按 session 查询与处理

**Phase 1 不支持：**

- 同一 session 内多个 run 并发
- 同一 session 的多连接实时广播
- 完整 event replay / cursor catch-up
- 复杂的 observer / controller 角色模型

### 2.3 Phase 1 约束

为降低复杂度并保持现有 store / history / sdkSessionId 语义稳定，第一阶段采用：

- **单 session 单 active run**
- **单 session 单 controller connection**
- **单连接多 session 并发**

说明：

- 同一 session 在任意时刻只能有一个 active run，状态可能是 `running` 或 `paused_for_interaction`
- 同一 session 在 Phase 1 可以被新连接接管，但不支持多个连接同时接收该 session 的实时事件流
- “controller connection” 指当前负责接收该 session 实时事件并可执行控制操作的连接

---

## 3. 当前实现的问题

### 3.1 Active Run 属于 Connection，而不是 Session

文件：`src/gateway/connection-context.ts`

当前 `ConnectionContext` 内维护：

```ts
activeRuns = new Map<string, { sessionKey: string; abort:() => void }>();
```

并在 `dispose()` 中：

```ts
for (const [ , run ] of this.activeRuns) {
  run.abort();
}
```

问题：

- 连接关闭会中断该连接下所有 session run
- run 生命周期不能跨连接迁移
- 无法实现“页面刷新后继续接管 session”

### 3.2 Pending Interaction 绑定 connId

文件：`src/gateway/handlers/chat.ts`

注册 pending interaction 时写入：

```ts
connId: ctx.connId
```

文件：`src/interaction/resolve.ts`

处理 resolve 时要求：

```ts
pending.connId === ctx.connId
```

问题：

- 只有原连接能处理交互
- reconnect 后无法恢复操作
- 新连接即使知道 sessionKey，也无法接管审批

### 3.3 Registry API 以 Connection 为主语

文件：`src/interaction/registry.ts`

当前接口主要围绕 connId：

- `cancelForConnection(connId)`
- `takeForRun(connId, runId)`
- `getForConnection(connId)`

问题：

- 缺少按 session 查询与恢复 pending interaction 的能力
- registry 的 authority 模型是 connection，而不是 session

### 3.4 Chat Abort 也是 connection-local 的

文件：`src/gateway/handlers/chat.ts`

当前 `chat.abort` 通过 `ctx.activeRuns` 找 run，并调用：

```ts
deps.registry.takeForRun(ctx.connId, runId)
```

问题：

- abort 的语义是“终止这个连接上下文中的 run”
- 不是“终止这个 session 当前的 active run”
- reconnect / handoff 后不再成立

### 3.5 Continuation / Resume 路径仍依赖 ctx

文件：`src/interaction/continuation.ts`

当前 continuation 仍直接操作：

```ts
ctx.activeRuns.set(...)
ctx.activeRuns.delete(...)
```

问题：

- 即使 `chat.send` 改成 session-owned，follow-up continuation 仍可能挂在旧连接上
- 运行态 ownership 不彻底迁移，会导致 reconnect 后恢复链路不完整

### 3.6 缺少显式的 Session Attach / Control 模型

当前服务端没有显式维护：

- 一个连接当前 attach 了哪些 session
- 一个 session 当前由哪个连接控制
- reconnect/handoff 时控制权如何迁移

因此系统无法稳定支持：

- 单连接多 session 订阅
- session 断线保活后重新 attach
- attach 后恢复 pending interaction / processing 状态

---

## 4. 设计原则

### 4.1 Session-Owned Runtime

session 是运行态主体：

- active run 属于 session
- pending interaction 属于 session
- session processing 状态属于 session
- connection 只是 transport / attach 关系

### 4.2 Connection as Transport

连接只负责：

- frame 收发
- 当前 attached sessions 集合
- 当前它是哪些 session 的 controller

连接不直接拥有：

- run 生命周期
- pending interaction 生命周期

### 4.3 Phase 1 采用 Single Controller 模型

第一阶段为降低改造复杂度，采用：

- 一个 session 同时只有一个 controller connection
- reconnect 时允许新连接接管 controller 身份
- 暂不支持多连接对同一 session 实时广播

### 4.4 Session-Level Recovery

恢复的最小单位是 session：

- attach session 后可恢复 processing 状态
- attach session 后可查询 pending interactions
- attach session 后前端可补拉 history

### 4.5 Store 与 Runtime 分层

需要区分“持久绑定信息”和“瞬时运行态”：

**SessionStore 继续负责：**

- history
- `sdkSessionId`
- cwd / model / permissionMode / additionalDirectories

**SessionRuntimeRegistry 负责：**

- attach / detach
- controller connection
- active run
- orphan grace timer
- processing / waiting_interaction 运行态

**PendingInteractionRegistry 继续独立存在，但改为 session-aware：**

- interaction register / take / resolve / reject
- 按 session 查询
- 按 run 查询

---

## 5. 参考实现分析

参考代码 `refer/claudecodeui` 中有三个重要能力值得借鉴：

### 5.1 Session Writer 可重连

在 `refer/claudecodeui/server/claude-sdk.js` 中：

- `reconnectSessionWriter(sessionId, newRawWs)`

说明 refer 的核心模型不是“连接拥有 session”，而是：

- session 拥有运行态
- WebSocket 只是当前附着的输出 writer

### 5.2 Pending Permissions 按 Session 查询

在 `refer/claudecodeui/server/claude-sdk.js` 中：

- `getPendingApprovalsForSession(sessionId)`

说明 pending approval 的恢复维度是 session，而不是 connId。

### 5.3 前端重连后主动补拉

在 refer 前端中：

- WebSocket reconnect 后主动重新拉取 session messages
- reconnect 或 session 切换时主动拉取 pending permissions

说明 refer 采用的是：

- 实时推流
- reconnect 后的状态补齐
- session 维度恢复

---

## 6. 总体架构方案

### 6.1 架构概览

Phase 1 引入一个新的运行态注册中心：

- `SessionRuntimeRegistry`

并保留现有：

- `PendingInteractionRegistry`
- `SessionStore`

三者职责如下：

```text
WebSocket Connection
   └─ ConnectionContext
       ├─ connId
       ├─ attachedSessions: Set<sessionKey>
       └─ controllerSessions: Set<sessionKey>

SessionRuntimeRegistry
   └─ sessionKey
       ├─ controllerConnId
       ├─ activeRun
       ├─ attachedConnIds (Phase 1 可只维护 controllerConnId)
       ├─ lastActiveAt
       └─ graceTimer

PendingInteractionRegistry
   └─ interactionId
       ├─ sessionKey
       ├─ runId
       ├─ kind
       └─ resolver / rejecter / expiresAtMs

SessionStore
   └─ sessionKey
       ├─ history
       ├─ sdkSessionId
       ├─ cwd / model / permissionMode
       └─ additionalDirectories
```

### 6.2 关键设计决定

#### 决定 A：`sdkSessionId` 仍以 Store 为真源

虽然文档中的 session runtime 会读到 `sdkSessionId`，但第一阶段不建议把它作为 `SessionRuntimeRegistry` 的核心职责。

原因：

- `sdkSessionId` 是 session 绑定信息，不是瞬时运行态
- 当前 `chat.send` 已在成功后写回 store
- 若 runtime 与 store 双写，容易出现 source-of-truth 冲突

因此：

- `session.status` 若需要返回 `sdkSessionId`，应从 store 读取
- runtime registry 不单独维护一份 authoritative `sdkSessionId`

#### 决定 B：pending interaction 不并入 runtime registry

第一阶段不建议把 `pendingInteractionIds` 作为 session runtime 的主存储结构。

原因：

- 当前已有独立的 `PendingInteractionRegistry`
- 它已经承载 resolver / rejecter / expiry scanner 等逻辑
- 若并入 runtime registry，会导致职责膨胀

因此：

- `PendingInteractionRegistry` 保持独立
- 仅新增按 `sessionKey` 查询 / 清理能力

#### 决定 C：Phase 1 使用单 controller 输出模型

当前 `ctx.agentEvent()` / `ctx.chatEvent()` 天然是单连接写入模型。

为了降低改造量，Phase 1 约定：

- 每个 session 同时只有一个 controller connection
- 事件只发给 controller connection
- reconnect attach 时允许 handoff controller
- 多 observer 广播放到 Phase 2

---

## 7. 数据模型设计

### 7.1 ConnectionRuntime（逻辑视图）

```ts
type ConnectionRuntime = {
  connId: string;
  attachedSessions: Set<string>;
  controllerSessions: Set<string>;
  connectedAt: number;
  principal?: string;
};
```

说明：

- `ConnectionContext` 未必需要完整持久化这个结构，但语义上应具备这些字段
- `principal` 用于后续权限校验扩展

### 7.2 SessionRuntime

```ts
type SessionRuntime = {
  sessionKey: string;
  controllerConnId?: string;
  activeRun?: ActiveRunRecord;
  lastActiveAt: number;
  graceTimer?: NodeJS.Timeout;
  ownerPrincipal?: string;
};
```

说明：

- Phase 1 不强制要求存储多个 attached connections
- 如果当前实现里 attach 与 controller 等价，则可只维护 `controllerConnId`

### 7.3 ActiveRunRecord

```ts
type ActiveRunRecord = {
  runId: string;
  sessionKey: string;
  abort: () => void;
  startedAt: number;
  state: 'running' | 'paused_for_interaction';
};
```

说明：

- `paused_for_interaction` 仍视为 active
- 因此在该状态下新的 `chat.send` 必须拒绝

### 7.4 PendingInteraction

将当前 `PendingInteraction` 从 connection-owned 改为 session-aware。

建议结构调整为：

```ts
type PendingInteraction = {
  interactionId: string;
  runId: string;
  sessionKey: string;
  kind: 'ask_user' | 'exec' | 'mode_switch';

  createdByConnId?: string;
  ownerPrincipal?: string;

  status?: 'pending' | 'resolved' | 'cancelled' | 'expired';
  createdAtMs?: number;
  expiresAtMs: number;

  resolver?: (input) => void;
  rejecter?: (err) => void;
};
```

说明：

- `connId` 不再作为 authority 判定字段
- `sessionKey` 成为交互归属主体
- `createdByConnId` 仅保留审计能力
- `ownerPrincipal` 为后续安全模型预留

---

## 8. 协议设计

### 8.1 新增 `session.attach`

用途：

- 当前连接接管某个 session 的 controller 身份
- reconnect 后恢复会话控制权
- 获取当前 session 运行态概览

请求：

```json
{
  "type": "req",
  "id": "1",
  "method": "session.attach",
  "params": {
    "sessionKey": "agent:main:main"
  }
}
```

响应：

```json
{
  "type": "res",
  "id": "1",
  "ok": true,
  "payload": {
    "sessionKey": "agent:main:main",
    "attached": true,
    "controller": true,
    "processing": true,
    "activeRun": {
      "runId": "run-123",
      "state": "running"
    },
    "pendingInteractions": [
      {
        "interactionId": "itx-1",
        "kind": "exec"
      }
    ]
  }
}
```

语义约束：

- Phase 1 中 attach 成功即表示当前连接成为该 session 的 controller
- 若旧 controller 存在，则发生 handoff
- attach 应是幂等操作：重复 attach 返回当前状态，不报错

### 8.2 新增 `session.detach`

用途：

- 当前连接释放某个 session 的 controller / attach 关系
- 不直接影响 session 本身生命周期

请求：

```json
{
  "type": "req",
  "id": "2",
  "method": "session.detach",
  "params": {
    "sessionKey": "agent:main:main"
  }
}
```

语义约束：

- detach 应为幂等操作
- 若该连接不是当前 controller，则直接返回成功
- detach 后如 session 无 controller，应进入 orphan 评估流程

### 8.3 新增 `session.status`

用途：

- reconnect 后恢复状态
- 前端 attach 后补拉状态

响应示例：

```json
{
  "sessionKey": "agent:main:main",
  "attached": true,
  "controller": true,
  "processing": true,
  "sdkSessionId": "sess-abc",
  "activeRun": {
    "runId": "run-123",
    "state": "running"
  },
  "pendingInteractionCount": 1,
  "updatedAt": "2026-04-26T11:00:00.000Z"
}
```

说明：

- `sdkSessionId` 从 store 读取
- `processing=true` 当且仅当存在 active run
- `paused_for_interaction` 也算 processing

### 8.4 改造 `interaction.pending.list`

当前实现按 connection 查询：

- `getForConnection(ctx.connId)`

建议改成按 session 查询：

请求：

```json
{
  "type": "req",
  "id": "3",
  "method": "interaction.pending.list",
  "params": {
    "sessionKey": "agent:main:main"
  }
}
```

语义：

- 返回该 session 当前所有 pending interactions
- 调用前要求连接已 attach 该 session
- 结果建议按 `createdAtMs` 升序返回

### 8.5 改造 `interaction.resolve`

当前逻辑：

- 必须是原始 connId

改造后逻辑：

- `interactionId` 找到 pending interaction
- 校验当前连接是否为该 `sessionKey` 的已 attach/controller 连接
- 校验通过即可 resolve

建议：

- Phase 1 不强制要求请求参数中显式传 `sessionKey`
- 因为 `interactionId` 已可唯一定位目标 interaction

### 8.6 权限校验预留

虽然第一阶段主要解决运行态 ownership 问题，但协议层建议保留 principal 校验扩展位：

- `session.attach` 时可校验是否有权接管该 session
- `interaction.resolve` 时可校验 `ownerPrincipal`

如果当前还没有完整鉴权体系，至少要预留字段与 hook，不建议把“知道 sessionKey 即可控制”固化为最终模型。

---

## 9. 生命周期设计

### 9.1 `chat.send`

处理流程：

1. 若当前连接尚未 attach session，则自动 attach 并成为 controller
2. 检查该 session 是否已有 active run
3. 若已有 active run（包括 `paused_for_interaction`），则拒绝本次请求
4. 创建 run 并注册到 `SessionRuntimeRegistry`
5. 将 streaming events 路由到该 session 当前 controller connection
6. run 结束后从 session runtime 中移除 active run

建议错误码：

```ts
SESSION_BUSY
```

### 9.2 `interaction.requested`

当 Claude runtime 产生 interaction：

1. 创建 pending interaction
2. 注册到 `PendingInteractionRegistry`
3. 将 active run 状态更新为 `paused_for_interaction`
4. 向当前 controller connection 发送 `interaction.requested`

### 9.3 `interaction.resolve`

当 operator 处理交互：

1. 根据 `interactionId` 取到 pending interaction
2. 校验当前连接是否有权控制该 session
3. 成功后发送 `interaction.resolved`
4. 调用 resolver 恢复 runtime
5. run 状态从 `paused_for_interaction` 回到 `running`，或最终结束

### 9.4 `chat.abort`

改造后语义：

- abort 的目标应是 session 当前 active run，而不是 ctx 私有 run

处理流程建议：

1. 根据 `runId` 或 `sessionKey` 找到目标 session 的 active run
2. 校验当前连接是否为该 session 的 controller
3. 清理该 run 下 pending interactions
4. 发送必要的 `interaction.resolved(cancelled)` 事件
5. 调用 active run 的 abort

### 9.5 `connection close`

旧行为：

- abort 全部 active runs
- cancel 全部 pending interactions

新行为：

1. 将连接从它控制的 session 中 detach
2. 若 session 仍有新 controller 接管，则不做额外处理
3. 若 session 失去 controller，则启动 orphan grace timer
4. grace 期内允许新连接 attach 并接管
5. grace 到期后仍无人接管，再清理该 session 的 runtime 资源

### 9.6 `reconnect attach`

新连接 attach 某 session 时：

1. 建立 attach / controller 关系
2. 取消 session orphan grace timer
3. 返回 active run 摘要
4. 返回 pending interactions 摘要
5. 前端再补拉 history 补齐 streaming gap

---

## 10. Orphan Grace Period 设计

### 10.1 目的

防止页面刷新或网络波动导致：

- run 被过早 abort
- pending interaction 被过早取消

### 10.2 建议配置

环境变量：

```env
SESSION_ORPHAN_GRACE_MS=60000
```

默认值：60 秒

### 10.3 行为

- session 最后一个 controller connection 断开时，进入 orphan 状态
- grace 期间内：
  - 不 abort active run
  - 不 cancel pending interactions
- grace 到期后：
  - abort active run
  - cancel / expire pending interactions
  - 清理 session runtime

### 10.4 实现注意点

为避免竞态，cleanup timer 触发时必须再次检查：

- session 当前是否已经重新 attach
- controller 是否已被新连接接管
- active run 是否已自然结束

---

## 11. 并发与竞态约束

这部分建议明确写进方案，否则实现时容易出现行为不一致。

### 11.1 单 Session Busy 判定

以下状态都视为 session busy：

- `activeRun.state === 'running'`
- `activeRun.state === 'paused_for_interaction'`

因此以下请求都应拒绝：

- 对同一 session 再次 `chat.send`

### 11.2 Resolve 与 Abort 的竞争

可能同时发生：

- operator 发送 `interaction.resolve`
- 用户或系统触发 `chat.abort` / orphan cleanup

建议语义：

- interaction 消费应为原子操作（take-once）
- 第一个成功拿到 pending interaction 的操作生效
- 其他并发操作统一返回 `NOT_FOUND` 或等价错误

### 11.3 Attach 与 Orphan Cleanup 的竞争

可能同时发生：

- grace timer 到期准备清理 session
- 新连接刚好 attach 同一 session

建议语义：

- attach 成功后必须取消 cleanup
- cleanup 执行前必须再次检查当前 controller 状态

### 11.4 Attach / Detach 幂等性

建议：

- 重复 attach 同一 session：返回成功与当前状态
- detach 未 attach 的 session：返回成功，不报错

---

## 12. 事件分发模型

### 12.1 Phase 1：保留单连接输出模型

当前 bridge 通过 `ctx.agentEvent()` / `ctx.chatEvent()` 直接写单连接。

第一阶段建议保留此模型，但明确：

- `ctx` 不再拥有 run 生命周期
- `ctx` 只是 session 当前 controller 的输出通道
- controller handoff 后，新的 run / interaction 输出应绑定到新 controller

### 12.2 Phase 2：演进到 Session Event Sink

后续可演进为：

```ts
sessionEventSink.emitToSession(sessionKey, frame)
```

由 runtime registry 查找 attached connections，并广播给多个 observer。

这部分属于 Phase 2，不纳入第一阶段的必须改造范围。

---

## 13. 实施方案

### 13.1 Phase 1：最小可用版

目标：

- 单连接多 session 并发
- session 运行态脱离 connId
- interaction 可跨连接恢复
- reconnect 后可在 grace 期内接管 session

#### 内容

1. 新增 `SessionRuntimeRegistry`
2. `ConnectionContext` 增加 attach / controller 语义，删除 run ownership
3. `chat.send` 自动 attach session，并将 run 注册到 session runtime
4. `chat.abort` 改为按 session active run 处理
5. `PendingInteractionRegistry` 支持按 session 查询 / 按 run 清理
6. `interaction.resolve` 改为 session/controller 校验
7. 连接关闭只 detach，不直接 abort/cancel
8. 引入 orphan grace period
9. 限制单 session 单 active run
10. 将 continuation 路径一并迁移到 session-owned runtime

### 13.2 Phase 2：多连接观察同一 Session

目标：

- 一个 session 支持多个连接订阅
- 事件广播给多个观察者

#### 内容

1. 引入显式 `ConnectionRegistry`（可选，视实现需要）
2. bridge 输出改为 session event sink
3. attach 后自动 replay pending interactions
4. 支持 observer / controller 角色（可选）

### 13.3 Phase 3：完整 reconnect catch-up

目标：

- 提升前端 reconnect 用户体验

#### 内容

1. attach 时自动下发 session 当前 processing 状态
2. 增加 last event anchor / event replay（可选）
3. 优化 history 补拉体验
4. leader handoff / observer 协作（可选）

---

## 14. 代码改造清单

### 14.1 新增文件

#### `src/runtime/session-runtime-registry.ts`

职责：

- session attach / detach
- controller handoff
- active run 注册 / 结束
- orphan grace 管理
- session status 查询

核心接口建议：

```ts
attachConnection(sessionKey: string, connId: string): SessionRuntime
detachConnection(sessionKey: string, connId: string): void
detachAllForConnection(connId: string): void
setController(sessionKey: string, connId: string): void
getController(sessionKey: string): string | undefined
registerRun(sessionKey: string, run: ActiveRunRecord): void
getActiveRun(sessionKey: string): ActiveRunRecord | undefined
updateRunState(sessionKey: string, state: 'running' | 'paused_for_interaction'): void
completeRun(sessionKey: string, runId: string): void
isController(sessionKey: string, connId: string): boolean
isAttached(sessionKey: string, connId: string): boolean
scheduleOrphanCleanup(sessionKey: string): void
cancelOrphanCleanup(sessionKey: string): void
getStatus(sessionKey: string): SessionStatus
```

### 14.2 修改 `src/gateway/connection-context.ts`

调整：

- 增加 `attachedSessions: Set<string>`
- 增加 `controllerSessions: Set<string>`
- 删除 `activeRuns`
- `dispose()` 改为只做 detach / stopTicks

### 14.3 修改 `src/gateway/handlers/chat.ts`

调整：

- `chat.send` 前自动 attach session
- run 注册到 `SessionRuntimeRegistry`
- 若 session 已有 active run，则返回 `SESSION_BUSY`
- interaction 注册时不再用 `connId` 做 authority
- `chat.abort` 改为按 session runtime 查 active run

### 14.4 修改 `src/interaction/registry.ts`

新增：

- `getForSession(sessionKey)`
- `takeForRun(runId)`（去掉 connId 限制）
- `cancelForSession(sessionKey)`
- `listForSession(sessionKey)`

删除 authority 中对 connId 的核心依赖，保留 `createdByConnId` 作为审计字段。

### 14.5 修改 `src/interaction/resolve.ts`

当前：

```ts
if (!pending || pending.connId !== ctx.connId)
```

改为：

```ts
if (!pending) ...
if (!runtimeRegistry.isController(pending.sessionKey, ctx.connId)) ...
```

同时：

- `interaction.pending.list` 改为按 session 查询
- `mode_transition.resolve` 的兼容路径也应同步迁移

### 14.6 修改 `src/interaction/continuation.ts`

这是本次改造的关键文件之一。

调整：

- 不再通过 `ctx.activeRuns` 挂载 follow-up run
- continuation 恢复时应写回 session runtime
- follow-up continuation 的输出目标应是当前 session controller，而不是历史 ctx 私有状态

### 14.7 修改 `src/gateway/handlers/sessions.ts`

新增方法：

- `session.attach`
- `session.detach`
- `session.status`

### 14.8 修改 `src/gateway/frame-dispatcher.ts`

注册新方法：

- `session.attach`
- `session.detach`
- `session.status`

### 14.9 修改 `src/server.ts`

新增 registry：

- `SessionRuntimeRegistry`
- 后续 Phase 2 可选 `ConnectionRegistry`

并注入到：

- chat handlers
- interaction handlers
- session handlers

在 `wss.on('close')` 中改为：

- `runtimeRegistry.detachAllForConnection(connId)`

### 14.10 修改 `src/types.ts`

新增类型：

- `SessionAttachParams`
- `SessionDetachParams`
- `SessionStatusPayload`
- `ActiveRunSummary`

并更新协议文档相关类型定义。

---

## 15. 测试方案

### 15.1 单连接多 Session 并发

场景：

- conn1 attach sessionA
- conn1 attach sessionB
- 分别对 A / B 发 `chat.send`
- 断言两边 run 均启动
- 断言 event 中 sessionKey 正确隔离

### 15.2 单 Session 不允许双 Active Run

场景：

- sessionA 已在 processing
- 再次 `chat.send(sessionA)`
- 返回 `SESSION_BUSY`

### 15.3 Pending Interaction 跨连接恢复

场景：

- conn1 发起 sessionA，产生 pending interaction
- conn1 close
- conn2 attach sessionA
- `interaction.pending.list(sessionA)` 能拿到 pending
- conn2 resolve 成功

### 15.4 连接关闭不立即中断 Run

场景：

- conn1 启动 sessionA run
- conn1 关闭
- grace 期内 sessionA 仍 active
- conn2 attach sessionA 成功恢复

### 15.5 Grace Timeout 清理

场景：

- orphan session 超过 grace time
- active run 被 abort
- pending interaction 被 cancel / expired
- session runtime 被清理

### 15.6 Resolve / Abort 竞态

场景：

- sessionA 处于 pending interaction
- 一边发送 `interaction.resolve`
- 一边发送 `chat.abort`
- 断言最终只有一个操作成功消费 interaction

---

## 16. 风险与权衡

### 16.1 单 Session 多 Run 并发复杂度高

风险：

- 上下文串扰
- history 顺序混乱
- `sdkSessionId` 恢复语义复杂

结论：

- Phase 1 不做

### 16.2 Event Replay 成本高

如果做完整 event replay，需要：

- per-session event log
- seq / cursor 机制
- replay window 管理

结论：

- 第一阶段不做完整 replay
- 采用 `status + pending + history` 组合恢复

### 16.3 多连接同时控制同一 Session

风险：

- 双方同时输入
- 双方同时 resolve 同一 interaction
- seq / 输出归属复杂

结论：

- Phase 1 不支持多 controller
- Phase 2 再引入 observer / controller 分离模型

### 16.4 权限模型不能长期依赖 sessionKey

风险：

- 如果将来存在多 principal / 多用户场景，仅凭 attach sessionKey 即可控制会话会过宽

结论：

- 本次设计需预留 principal 校验位
- authority 最终应演进为 `session + principal`，而不是 `sessionKey` 字符串本身

---

## 17. 推荐结论

建议将当前 relay 从 **connection-owned runtime** 演进为 **session-owned runtime**。

第一阶段最值得优先落地的是：

1. `SessionRuntimeRegistry`
2. `session.attach / session.detach / session.status`
3. interaction ownership 改为 session 维度
4. orphan grace period
5. 单 session 单 active run
6. continuation 路径迁移到 session-owned runtime

这样即可满足“**一个连接多个会话并发**”和“**断线短期恢复**”的核心诉求，并与 `refer/claudecodeui` 的 session 恢复思路保持一致，同时保留当前项目作为 gateway 的扩展空间。

---

## 18. 建议里程碑

### 里程碑 M1：Runtime Ownership 迁移

- `SessionRuntimeRegistry`
- `session.attach / detach / status`
- 单连接多 session 并发
- 单 session 单 active run
- `chat.abort` 改为 session 语义

### 里程碑 M2：Interaction Recovery + Grace

- pending interaction 跨连接恢复
- `interaction.pending.list(sessionKey)`
- `interaction.resolve` 改为 session/controller 校验
- orphan grace
- reconnect 恢复流程
- continuation 路径迁移完成

### 里程碑 M3：多连接观察与更完整 catch-up

- 多 observer 广播
- session event sink
- 更完整的 catch-up / replay
- observer / controller 分离
