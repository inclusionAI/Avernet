# WebSocket/HITL Interaction Protocol Design

## 1. 文档目标

本文档定义当前项目 WebSocket 网关中的 **HITL（Human-in-the-loop）Interaction 协议**，用于统一 Claude Agent SDK 运行过程中所有“需要用户决策”的场景。

本文档目标如下：

1. 为前后端提供一套稳定、明确、可扩展的 Interaction 协议定义
2. 将所有 HITL 场景统一收敛到 `interaction.*` 协议族
3. 控制顶层 `event` / `method` 数量，避免协议类型持续膨胀
4. 为后续从“follow-up continuation 模拟继续”升级到“原运行挂起/恢复”提供兼容演进路径

本文档是**正式协议文档**，优先约束：

- WebSocket 顶层 frame
- `interaction.requested` / `interaction.resolved` 事件格式
- `interaction.resolve` 方法格式
- Claude Agent SDK 交互型工具到 Interaction 协议的映射规则
- 前后端状态机、兼容策略与实现约束

---

## 2. 设计原则

### 2.1 单一 Interaction 出口

所有需要用户介入的场景，都必须统一通过以下协议表达：

- 事件：`interaction.requested`
- 事件：`interaction.resolved`
- 方法：`interaction.resolve`

无论底层来源是：

- `AskUserQuestion`
- `ExitPlanMode`
- `Bash`
- `Edit`
- `Write`
- 未来新增的其他交互型工具

都不得再为其设计新的顶层交互事件或顶层 resolve 方法。

### 2.2 顶层类型尽量少

协议复杂度优先放在 payload 字段中，而不是通过增加更多 `event` / `method` 名称来表达。

允许稳定公开的核心顶层事件仅包括：

- `chat`
- `agent`
- `interaction.requested`
- `interaction.resolved`
- 少量系统事件（如 `tick`、`connect.challenge`）

允许稳定公开的核心顶层方法仅包括：

- `chat.send`
- `chat.abort`
- `chat.history`
- `interaction.resolve`
- session 管理相关方法

### 2.3 Interaction 语义边界清晰

`interaction.*` 只用于表达 **run 被某个待决用户输入所阻塞** 的场景。

以下内容不应纳入 `interaction.*` 主协议：

- 纯展示型 tool use / tool result
- agent 生命周期与 stream 细节
- thinking / usage / command output 等观测型信息
- 默认无需审批的只读行为

判断标准为：

1. 当前 run 是否因为该节点而暂停推进
2. 是否必须由用户给出 `decision`、`answer` 或 `selectedOptions` 才能继续

只有同时满足这两个条件，才应发出 `interaction.requested`。

### 2.4 交互稳定、实现可演进

协议设计必须允许以下两种服务端实现共存：

- **阶段一实现**：通过 `interaction.resolve` 后触发 follow-up continuation 来模拟继续执行
- **阶段二实现**：通过 SDK hook / resolver 在原运行上下文中挂起并恢复

前端不应依赖这两种服务端实现差异，而只依赖 `interaction.*` 协议本身。

### 2.5 前端按交互语义渲染，而不是按 SDK 细节渲染

前端渲染应基于：

- `interaction.kind`
- `interaction.subject`
- `interaction.options`
- `interaction.questions`
- `interaction.inputSchema`
- `interaction.uiHints`

而不应依赖：

- 原始 `content_block_*`
- 原始 tool raw json 结构
- 独立 `mode_transition` 概念

### 2.6 run 与 interaction 的基本约束

为降低第一阶段实现复杂度，协议推荐以下默认约束：

- **一个 run 在任一时刻最多只有一个 active interaction**
- 一个 `interactionId` 只能进入一个最终态
- `interaction.resolve` 被接受后，服务端必须移除 pending 项
- resolve 后 run 可能继续，也可能结束，前端不得假设一定会继续输出

如果未来需要支持一个 run 下并行存在多个 interaction，则必须显式扩展：

- 并行是否允许
- 顺序约束
- replay 排序规则
- 前端冲突处理方式

在这些规则未被正式定义前，不建议默认进入多 pending interaction 模式。

---

## 3. 协议范围

本文档只定义 **HITL Interaction 协议**，不重新定义完整聊天协议。

本文档覆盖：

- Interaction 请求事件
- Interaction 决议事件
- Interaction 提交方法
- Interaction 生命周期
- Interaction 对象模型
- Claude Agent SDK 工具到 Interaction 协议的映射

本文档不覆盖：

- 普通文本流式输出协议
- 非交互型 agent tool 的完整展示协议
- session/history 存储格式
- ACL / 鉴权细节

---

## 4. 顶层 WebSocket Frame

沿用现有统一 frame：

```ts
export type GatewayRequestFrame = {
  type: 'req';
  id: string;
  method: string;
  params?: Record<string, unknown>;
};

export type GatewayResponseFrame = {
  type: 'res';
  id: string;
  ok: boolean;
  payload?: unknown;
  error?: {
    code: string;
    message: string;
    details?: unknown;
    retryable?: boolean;
    retryAfterMs?: number;
  };
};

export type GatewayEventFrame = {
  type: 'event';
  event: string;
  payload?: unknown;
  seq?: number;
  stateVersion?: { presence: number; health: number };
};
```

HITL Interaction 协议只使用其中：

- `event = 'interaction.requested'`
- `event = 'interaction.resolved'`
- `method = 'interaction.resolve'`

---

## 5. Interaction 对象模型

### 5.1 InteractionKind

推荐稳定 kind 集合：

```ts
export type InteractionKind =
  | 'ask_user'
  | 'confirm'
  | 'mode_switch';
```

说明：

- `ask_user`：用于承载 `AskUserQuestion` 这类需要用户提供回答/选择的交互
- `confirm`：用于承载命令执行、文件修改、危险动作确认等“是否允许”的交互
- `mode_switch`：用于承载如 `ExitPlanMode` 这类模式切换确认

补充约束：

- `kind` 是**高层语义分类**，不是对底层工具的一一映射
- 同一个 `kind` 下的具体渲染差异，应优先通过 `subject.type`、`options`、`inputSchema`、`uiHints` 表达
- 未来若要新增 kind，必须证明现有 `kind + payload` 组合无法稳定表达，而不是仅为了命名更细

### 5.2 InteractionSubject

`subject` 用于表达本次 interaction 所关联的对象。它是区分同一 `kind` 下不同渲染形态的核心字段。

```ts
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
```

### 5.3 InteractionOption

用于表达具名选项，适用于模式切换、单选、多选题等场景。

```ts
export type InteractionOption = {
  value: string;
  label: string;
  description?: string;
  recommended?: boolean;
};
```

### 5.4 InteractionQuestion

用于表达问题型输入，适用于 `ask_user` 等场景。

```ts
export type InteractionQuestion = {
  question: string;
  header?: string;
  multiSelect?: boolean;
  options?: Array<{
    label: string;
    description?: string;
    preview?: string;
  }>;
};
```

### 5.5 InteractionUiHints

用于表达非业务语义的 UI 提示，不参与后端决策逻辑。

```ts
export type InteractionUiHints = {
  variant?: 'question' | 'warning' | 'plan';
  severity?: 'info' | 'warning' | 'danger';
  collapsible?: boolean;
};
```

### 5.6 InteractionInputSchema

用于帮助前端理解用户输入方式。

```ts
export type InteractionInputSchema = {
  type: 'none' | 'text' | 'choices' | 'form';
  multiSelect?: boolean;
};
```

---

## 6. `interaction.requested` 事件

### 6.1 语义

`interaction.requested` 表示：

> 当前某个 run 在某个执行节点进入等待用户决策状态。

它是所有 HITL Interaction 的统一入口事件。

### 6.2 Payload 定义

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

### 6.3 字段约束

- `interactionId`：必填，唯一标识一次待处理 interaction
- `runId`：必填，所属 run 标识
- `sessionKey`：必填，所属会话标识
- `kind`：必填，交互语义主类型
- `createdAtMs/expiresAtMs`：必填，用于前端倒计时与过期处理
- `subject`：推荐必填，除非该 interaction 不对应任何主体对象
- `questions`：`ask_user` 场景推荐提供
- `options`：`mode_switch` 和显式选项类场景推荐提供
- `inputSchema`：建议提供，帮助前端选择组件
- `uiHints`：可选，仅用于体验优化

### 6.4 示例

#### 示例 A：AskUserQuestion

```json
{
  "type": "event",
  "event": "interaction.requested",
  "payload": {
    "interactionId": "int_123",
    "runId": "run_001",
    "sessionKey": "session:demo",
    "kind": "ask_user",
    "title": "Claude needs your input",
    "prompt": "Please choose the deployment target",
    "subject": {
      "type": "tool",
      "toolName": "AskUserQuestion",
      "toolCallId": "toolu_001"
    },
    "questions": [
      {
        "question": "Which environment should be used?",
        "header": "Deploy",
        "multiSelect": false,
        "options": [
          { "label": "staging", "description": "Safer for verification" },
          { "label": "production", "description": "User-facing environment" }
        ]
      }
    ],
    "inputSchema": {
      "type": "choices",
      "multiSelect": false
    },
    "uiHints": {
      "variant": "question",
      "severity": "info"
    },
    "createdAtMs": 1710000000000,
    "expiresAtMs": 1710000055000
  }
}
```

#### 示例 B：Bash 命令确认

```json
{
  "type": "event",
  "event": "interaction.requested",
  "payload": {
    "interactionId": "int_124",
    "runId": "run_001",
    "sessionKey": "session:demo",
    "kind": "confirm",
    "title": "Command approval required",
    "description": "Claude wants to execute a shell command.",
    "subject": {
      "type": "command",
      "toolName": "Bash",
      "toolCallId": "toolu_002",
      "command": "npm test",
      "cwd": "/repo"
    },
    "inputSchema": {
      "type": "none"
    },
    "uiHints": {
      "variant": "warning",
      "severity": "warning"
    },
    "createdAtMs": 1710000000000,
    "expiresAtMs": 1710000055000
  }
}
```

#### 示例 C：ExitPlanMode

```json
{
  "type": "event",
  "event": "interaction.requested",
  "payload": {
    "interactionId": "int_125",
    "runId": "run_001",
    "sessionKey": "session:demo",
    "kind": "mode_switch",
    "title": "Switch from planning to execution",
    "description": "Claude proposes to leave plan mode and continue execution.",
    "subject": {
      "type": "mode",
      "toolName": "ExitPlanMode",
      "toolCallId": "toolu_003",
      "fromMode": "plan",
      "toMode": "execute"
    },
    "options": [
      { "value": "proceed", "label": "Continue to execution", "recommended": true },
      { "value": "stay", "label": "Stay in planning" }
    ],
    "inputSchema": {
      "type": "choices",
      "multiSelect": false
    },
    "uiHints": {
      "variant": "plan",
      "severity": "info"
    },
    "createdAtMs": 1710000000000,
    "expiresAtMs": 1710000055000
  }
}
```

---

## 7. `interaction.resolve` 方法

### 7.1 语义

`interaction.resolve` 表示：

> 客户端对某次待处理 interaction 提交决策结果。

该方法是所有 HITL 响应的唯一稳定入口。

### 7.2 Params 定义

```ts
export type InteractionResolveParams = {
  interactionId: string;

  /**
   * 统一决策字段。
   * wire format 为 string，但允许值必须受 kind 约束。
   * 推荐值：submit / cancel / approve / deny / proceed / stay
   */
  decision: string;

  /** 单条文本回答。 */
  answer?: string;

  /** 表单或结构化输入。 */
  values?: Record<string, unknown>;

  /** 选中的 option value 列表。 */
  selectedOptions?: string[];

  /** 可选元数据，用于兼容扩展。 */
  meta?: Record<string, unknown>;
};
```

### 7.3 决策值约定

`decision` 在 wire format 上是字符串，但在协议语义上应视为 **受 `kind` 约束的有限集合**。服务端必须校验当前 `kind` 是否支持该 `decision`。

推荐的 `decision` 取值：

- `submit`：用于提交 `ask_user` 的回答
- `cancel`：用于取消 `ask_user`
- `approve`：用于批准 `confirm`
- `deny`：用于拒绝 `confirm`
- `proceed`：用于同意 `mode_switch`
- `stay`：用于拒绝模式切换并继续当前模式

为了兼容旧实现，服务端可接受旧字段映射：

- `action=submit` -> `decision=submit`
- `action=cancel` -> `decision=cancel`
- `decision=approved/allow` -> 归一化为 `approve`
- `decision=denied/reject` -> 归一化为 `deny`

### 7.4 请求示例

#### AskUserQuestion 提交回答

```json
{
  "type": "req",
  "id": "req_001",
  "method": "interaction.resolve",
  "params": {
    "interactionId": "int_123",
    "decision": "submit",
    "answer": "Use staging",
    "selectedOptions": ["staging"],
    "values": {
      "Which environment should be used?": "staging"
    }
  }
}
```

#### Bash 批准

```json
{
  "type": "req",
  "id": "req_002",
  "method": "interaction.resolve",
  "params": {
    "interactionId": "int_124",
    "decision": "approve"
  }
}
```

#### ExitPlanMode 选择继续规划

```json
{
  "type": "req",
  "id": "req_003",
  "method": "interaction.resolve",
  "params": {
    "interactionId": "int_125",
    "decision": "stay"
  }
}
```

### 7.5 Response 定义

```ts
export type InteractionResolveResult = {
  accepted: boolean;
  interactionId: string;
  decision: string;
  kind?: InteractionKind;
};
```

### 7.6 错误码

推荐错误码：

- `INVALID_REQUEST`：参数缺失或不合法
- `NOT_FOUND`：找不到 pending interaction
- `EXPIRED`：interaction 已过期
- `CONFLICT`：interaction 已被其他连接处理
- `UNSUPPORTED_DECISION`：该 kind 不支持该 decision

---

## 8. `interaction.resolved` 事件

### 8.1 语义

`interaction.resolved` 表示：

> 某次 interaction 已经被处理完毕，进入最终状态。

该状态可能是：

- 用户已回答
- 用户已批准
- 用户已拒绝
- 用户已取消
- 系统自动过期

### 8.2 Payload 定义

```ts
export type InteractionPhase =
  | 'answered'
  | 'approved'
  | 'denied'
  | 'cancelled'
  | 'expired';

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

### 8.3 phase 映射规则

| kind | decision | phase |
|---|---|---|
| `ask_user` | `submit` | `answered` |
| `ask_user` | `cancel` | `cancelled` |
| `confirm` | `approve` | `approved` |
| `confirm` | `deny` | `denied` |
| `mode_switch` | `proceed` | `approved` |
| `mode_switch` | `stay` | `denied` |
| any | timeout/system expiry | `expired` |

### 8.4 示例

```json
{
  "type": "event",
  "event": "interaction.resolved",
  "payload": {
    "interactionId": "int_124",
    "runId": "run_001",
    "sessionKey": "session:demo",
    "kind": "confirm",
    "phase": "approved",
    "decision": "approve",
    "resolvedBy": "operator",
    "resolvedAtMs": 1710000003000
  }
}
```

---

## 9. Interaction 生命周期

### 9.1 状态机

统一 Interaction 状态机如下：

```text
requested
  ├─(interaction.resolve)──────────────> resolved(answered/approved/denied/cancelled)
  └─(timeout / disconnect cleanup)─────> resolved(expired)
```

### 9.2 时序要求

1. 服务端发出 `interaction.requested`
2. 在 MVP 阶段，同一 run 发出新的 `interaction.requested` 前，应先结束前一个 active interaction
3. 在该 interaction 未决期间：
   - 客户端可展示倒计时
   - 客户端可调用 `interaction.resolve`
4. 一旦服务端接受处理，必须：
   - 发送 `interaction.resolved`
   - 从 pending registry 中移除该 interaction
5. 一次 interaction 最多只能进入一个最终状态

### 9.3 幂等要求

- 同一个 `interactionId` 的第一次有效 resolve 成功后，后续 resolve 应返回：
  - `NOT_FOUND`
  - 或 `CONFLICT`
- 客户端应以 `interactionId` 为幂等 key 处理 UI 状态

---

## 10. Claude Agent SDK 映射规则

### 10.1 AskUserQuestion -> `ask_user`

#### 映射规则

- `toolName = AskUserQuestion`
- 协议 kind -> `ask_user`
- `subject.type = 'tool'`
- `subject.toolName = 'AskUserQuestion'`
- `subject.toolCallId = <tool use id>`
- `questions` 从 tool input 提取
- `prompt` 可由问题文本拼接而成
- `inputSchema` 通常为 `choices` 或 `form`
- 推荐 `uiHints.variant = 'question'`

#### 服务端行为

阶段一：
- 发出 `interaction.requested`
- 等待 `interaction.resolve`
- resolve 后通过 follow-up continuation 模拟继续

阶段二：
- 发出 `interaction.requested`
- 挂起原 SDK 运行
- resolve 后将 `answer/values/selectedOptions` 回填给当前 tool interaction
- 继续同一轮运行

### 10.2 ExitPlanMode -> `mode_switch`

#### 映射规则

- `toolName = ExitPlanMode`
- 协议 kind -> `mode_switch`
- `subject.type = 'mode'`
- `subject.fromMode = 'plan'`
- `subject.toMode = 'execute'`
- `options = [{value:'proceed'}, {value:'stay'}]`
- 推荐 `uiHints.variant = 'plan'`

#### 服务端行为

阶段一：
- 发出 `interaction.requested(kind='mode_switch')`
- resolve 后通过 follow-up continuation 表达“继续执行”或“继续规划”

阶段二：
- 发出 `interaction.requested(kind='mode_switch')`
- 挂起原 SDK 运行
- resolve 后将决策回填给当前运行继续

### 10.3 Bash/Edit/Write -> `confirm`

#### 映射规则

统一 kind：`confirm`

##### Bash

```ts
subject = {
  type: 'command',
  toolName: 'Bash',
  toolCallId,
  command,
  cwd,
}
```

##### Edit / Write

```ts
subject = {
  type: 'file',
  toolName: 'Edit' | 'Write',
  toolCallId,
  filePath,
}
```

推荐：

- Bash -> `uiHints.variant='warning'`, `severity='warning'`
- 高风险命令 -> `severity='danger'`
- 文件覆盖类写入 -> `severity='warning'` 或 `danger`

#### 关于 Read

`Read` 默认不建议作为用户主 interaction 审批对象。若业务侧仍要求审批，也应映射为：

- `kind='confirm'`
- `subject.type='file'`
- `subject.toolName='Read'`

但不建议将其作为主要交互入口高频暴露。

---

## 11. 前端消费规范

### 11.1 前端必须依赖的最小集合

前端处理 HITL 时，只应稳定依赖：

- `interaction.requested`
- `interaction.resolved`
- `interaction.resolve`

前端不应把以下内部流视为核心交互协议：

- `mode_transition.*`
- `approval.*`
- 原始 `content_block_*`
- 原始 tool raw input

### 11.2 前端推荐渲染路由

推荐按以下规则选择渲染组件：

- `kind='ask_user'` -> 问答交互面板
- `kind='confirm' && subject.type='command'` -> 命令确认面板
- `kind='confirm' && subject.type='file'` -> 文件确认面板
- `kind='mode_switch'` -> 模式切换面板

### 11.3 前端待处理列表

前端应维护一个 `pendingInteractions` 集合，key 为 `interactionId`。

- 收到 `interaction.requested` -> 加入 pending
- 收到 `interaction.resolved` -> 从 pending 移除
- 页面刷新 / WebSocket 重连后，应能从服务端恢复 pending interactions

---

## 12. 服务端实现规范

### 12.1 Pending Registry

服务端必须维护待决 interaction registry，至少包含：

```ts
{
  interactionId,
  connId,
  runId,
  sessionKey,
  kind,
  subject,
  prompt,
  questions,
  options,
  expiresAtMs,
  resolver?,
  onExpire?,
}
```

补充要求：

- registry 中应预留 **所有权/授权主体** 概念，不应永久将 `connId` 视为唯一身份边界
- P0 阶段可以按 `connId` 限制 resolve
- 但数据结构建议同时保留 `sessionKey`，并为未来扩展 `principal/user/actor` 预留字段
- 否则后续做 reconnect、跨 tab、跨设备恢复时，容易再次触发 registry 重构

### 12.2 过期处理

- 到达 `expiresAtMs` 后若仍未 resolved，服务端必须：
  - 移除 pending interaction
  - 发出 `interaction.resolved(phase='expired')`
- 服务端应定义默认 TTL，并允许按 interaction kind 调整
- 过期后 run 的后续行为也应明确：终止、回退、或以系统拒绝语义继续，不能由不同 handler 各自决定

### 12.3 连接约束

如果当前实现要求 interaction 只能由原连接处理，则在 resolve 时应校验 `connId`。如后续支持跨连接恢复，则应改为按 session/user 授权校验。

### 12.4 会话重连恢复

服务端应至少支持以下一种机制：

- 提供 `interaction.pending.list(sessionKey)` 查询
- 或在 session attach / reconnect 后自动 replay pending `interaction.requested`

推荐目标：自动 replay，减少前端额外方法依赖。

---

## 13. 兼容性与迁移策略

### 13.1 对现有协议的兼容

在迁移期间允许保留以下兼容入口：

- `mode_transition.resolve` -> 内部转 `interaction.resolve`
- 旧 `approval.resolve` -> 内部转 `interaction.resolve`
- 旧 `action/message` 参数 -> 归一化到 `decision/answer`

但这些兼容入口不应再作为正式协议文档的推荐方式。

### 13.2 对现有 agent 流的兼容

当前项目中若仍发送：

- `agent.stream='interaction'`
- `agent.stream='mode_transition'`
- `agent.stream='approval'`

则应在文档中将它们标记为：

- 非推荐前端强依赖字段
- 调试/兼容用途

最终前端应以顶层 `interaction.*` 为准。

### 13.3 对 follow-up continuation 的兼容

在服务端尚未实现原运行挂起/恢复之前：

- `interaction.resolve` 后仍可通过 follow-up continuation 继续任务
- 这不影响协议正确性
- 但服务端不得把该内部行为泄漏为新的对外交互协议

---

## 14. 推荐实施步骤

### 阶段一：协议统一

1. 在 `src/types.ts` 中定义统一的 Interaction Schema
2. 将 `ExitPlanMode` 改为发 `interaction.requested(kind='mode_switch')`
3. 将 `mode_transition.resolve` 改为兼容入口，内部统一走 `interaction.resolve`
4. 为 `confirm` 场景补齐 `subject.type/toolName/filePath/command/cwd`
5. 统一 `interaction.resolved` 的最终状态映射

### 阶段二：前端接入

1. 前端建立 `pendingInteractions` store
2. 按 `kind + subject.type` 建立 interaction renderer registry
3. 将所有 HITL 入口统一从 `interaction.requested` 触发
4. 接入 reconnect 后 pending interaction 恢复逻辑

### 阶段三：运行时升级

1. 调研 Claude Agent SDK 是否支持稳定的 hook / `canUseTool` / continuation 机制
2. 在 `claude-sdk-bridge.ts` 中引入 resolver registry
3. 将 `AskUserQuestion` / `ExitPlanMode` 从 follow-up continuation 迁移为原运行挂起/恢复
4. 再逐步迁移 Bash/Edit/Write 确认逻辑

---

## 15. 协议摘要

最终正式建议如下：

- **唯一 Interaction 请求事件**：`interaction.requested`
- **唯一 Interaction 完成事件**：`interaction.resolved`
- **唯一 Interaction 响应方法**：`interaction.resolve`
- **推荐最小 kind 集合**：`ask_user` / `confirm` / `mode_switch`
- **所有差异统一通过 payload 表达**：`subject` / `questions` / `options` / `inputSchema` / `uiHints`
- **前端只依赖 interaction 语义，不依赖 SDK 原始交互细节**
- **服务端允许先用 follow-up continuation 兼容实现，但长期必须升级为原运行挂起/恢复**
