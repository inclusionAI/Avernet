# WebSocket/HITL Interaction 协议分析

## 1. 文档目标

本文档用于分析当前项目 HITL（Human-in-the-loop）交互协议的现状、主要问题与演进方向，并与参考项目 `refer/claudecodeui` 做对照。

本文档重点回答三个问题：

1. 当前项目已经具备了哪些能力
2. 当前项目在交互协议层面还缺什么
3. 后续应该优先统一哪些协议语义与运行时能力

本文档定位为**现状分析文档**，用于为正式协议文档与改造计划文档提供依据。

---

## 2. 核心判断

基于当前项目 `src/` 与参考项目 `refer/claudecodeui` 的对照分析，可以先得出以下核心判断：

1. **当前项目已经具备统一网关协议的基础，但 Interaction 主出口尚未完全收敛。**
   当前实现已经有统一 WebSocket frame（`req/res/event`）、`chat` / `agent` 双通道，以及 `interaction.requested` / `interaction.resolved` 的基础能力；但在实际交互上，仍同时存在 `interaction.*`、`mode_transition.*`、以及部分需要通过 `agent.tool/content_block` 推断的语义，协议出口还不够统一。

2. **当前最大的缺口不是 SDK 事件解析，而是 Claude Agent SDK 的交互语义尚未完全转化为前端稳定可消费的 Interaction 协议。**
   当前项目已经能解析 `message_start`、`content_block_*`、`tool_use`、`thinking`、`result` 等事件，但这些事件还没有被彻底收束成统一的 HITL 模型。

3. **所有需要用户决策的场景，都应统一以 `interaction.*` 为主协议。**
   这意味着：
   - `AskUserQuestion`、`ExitPlanMode`、`Bash/Edit/Write` 等都应统一映射到 `interaction.requested` / `interaction.resolved`
   - 不应继续保留独立的 `mode_transition.resolve` 作为主协议分支
   - `approval`、`mode_transition` 等概念可以作为内部实现细节存在，但不应继续作为前端核心协议扩散

4. **当前项目对 `AskUserQuestion` / `ExitPlanMode` 的处理更像“follow-up continuation”，而不是“在原运行中挂起并恢复”。**
   这是与 `claudecodeui` 最大的行为差异之一。当前项目是在 tool result 之后重新触发一轮 follow-up chat；而 `claudecodeui` 更倾向于让 SDK 运行在原上下文中等待用户反馈后继续。这种实现短期可用，但从交互连续性、模型上下文一致性与协议语义正确性来看，都不是最终理想方案。

5. **当前项目缺少一个稳定的 Interaction Schema。**
   现在的 `interaction` payload 主要按 case 临时拼装：`ask_user` 有 `prompt/questions`，旧的执行审批只有 `command/cwd`，`ExitPlanMode` 又走另一套协议。这会让前端不得不理解多个来源、多种字段形态。最终应统一为一个稳定 schema：用较少的 `kind` 表示交互类别，用 `subject/options/uiHints` 等字段承载差异。

6. **协议层优先改造目标应是“统一出口”，运行时优先改造目标应是“恢复原执行流”。**
   建议分阶段推进：先统一协议与 payload，再逐步把 `AskUserQuestion` / `ExitPlanMode` 从 follow-up continuation 升级为真正的 SDK 运行中挂起/恢复。

---

## 3. Interaction 协议边界

虽然本文建议将所有 **HITL** 场景统一收敛到 `interaction.*`，但这并不意味着所有 agent 事件都应该升级为 interaction。`interaction` 的边界应当明确为：

> **只有当运行在某个节点必须等待用户决策，且在决策返回前不能继续推进时，才应进入 `interaction.requested`。**

因此，下列内容通常**不应**进入 `interaction.*` 主协议：

- 纯展示型 tool use / tool result
- 不需要审批的只读行为或观测行为
- agent 的内部生命周期变化（如 `message_start/stop`、`content_block_start/stop`）
- 普通 `command_output` / `thinking` / `usage` 更新

建议用一个简单判断标准来界定：

- **是否阻塞当前 run 继续执行？**
- **是否必须由用户提供 `decision` / `answer` / `selectedOptions` 才能继续？**

只有这两个条件同时满足时，才应该进入 `interaction.*`。否则应继续走 `chat` / `agent` 等普通事件流。

---

## 4. run 与 interaction 的关系约束

除了统一交互出口，协议还需要明确 **run 与 interaction 的约束关系**，否则前后端会各自做隐含假设。建议在第一阶段采用以下约束：

- 一个 run 在任一时刻最多只存在 **一个 active interaction**
- 同一个 interaction 只能被 resolve 一次，并进入唯一最终态
- resolve 后，run 可能继续，也可能直接结束；前端不应假设 resolve 后一定还有后续流

如果未来要支持同一 run 下多个 pending interaction，则必须额外定义：

- 是否允许并行
- 是否要求按发出顺序处理
- reconnect 时如何排序与回放
- 前端如何处理多个未决交互

建议第一阶段明确采用“**single-run single-active-interaction**”约束，这样可以显著降低前端状态管理和服务端 registry 复杂度。

---

## 5. 当前项目与 claudecodeui 对照

| Claude Agent SDK 发出的消息/语义 | 应有的 Interaction 协议语义 | 当前项目映射 | claudecodeui 映射 | 差距判断 |
|---|---|---|---|---|
| `system` | 表示一次 run 初始化，携带 `session_id/cwd/tools` 等上下文 | `agent.stream = lifecycle(start)`，并记录 `sdkSessionId` | 通常归一化为会话状态消息，必要时发 `session_created` | 当前项目已支持，但更适合内部状态，不必成为前端强依赖 |
| `message_start` | assistant 消息开始，可用于记录 messageId/model/usage | `agent.stream = message, phase=start` | 多数情况下不直接暴露为独立前端协议 | 当前项目解析完整，但对最终 UI 协议价值较低，可内收 |
| `message_delta` | 更新 stop reason / usage 等消息级状态 | `agent.stream = assistant` 携带 usage | 多用于 `status` 或最终 `complete` 附加 metadata | 当前已有 usage 通道，但前端消费模型还不够统一 |
| `message_stop` | assistant 消息结束 | `agent.stream = message, phase=stop` | 一般不单独暴露 | 当前项目有能力，但可不作为稳定前端协议 |
| `content_block_start(type=text)` | 文本块开始 | `agent.stream = content_block` | 通常不单独暴露 | 当前解析粒度较细，但对前端可以隐藏 |
| `content_block_delta(text_delta)` | assistant 文本流式输出 | `chat.state = delta` | `stream_delta` | 当前项目这部分已经基本完整 |
| `content_block_stop(type=text)` | 文本块结束 | `agent.stream = content_block stop` | 一般不单独暴露 | 可继续保留内部，但不建议让前端强依赖 |
| `content_block_delta(thinking_delta)` | thinking 增量输出 | `agent.stream = thinking` | `thinking` | 当前项目这部分已经完整 |
| `content_block_start(type=tool_use)` | 工具调用开始 | `agent.stream = tool, phase=start`；对某些交互型工具会 suppress | `tool_use` | 当前项目具备基础，但交互型工具已开始特殊处理 |
| `content_block_delta(input_json_delta)` | 工具参数增量 | `agent.stream = tool, phase=update` | 通常继续更新 `tool_use` | 当前项目已支持 |
| `content_block_stop(type=tool_use)` | 工具参数完成，可视为一次 `tool_use` 完成 | `agent.stream = tool, phase=result`，并根据工具名转具体协议 | 拆成 `tool_use` / `tool_result` 等前端消息 | 当前项目已把它当成协议分流点，这是正确方向 |
| `tool_use: AskUserQuestion` | 应触发问答型 interaction：展示问题、选项、支持提交/取消，并把答案回给运行中的 agent | `interaction.requested(kind=ask_user)`；resolve 后通过 `startFollowUpChat(...)` 模拟继续 | 作为 `permission_request` 进入前端，再通过 `resolveToolApproval()` 让原 SDK 流继续 | **关键差距：当前项目还不是原运行挂起/继续，而是后补一轮 follow-up continuation** |
| `tool_use: ExitPlanMode` | 应触发模式切换型 interaction：用户决定是否从 plan 进入 execute | `agent.stream = mode_transition` + `mode_transition.resolve`，并在决策后 follow-up continuation | 通常归为交互/权限请求，前端通过 plan 相关组件渲染 | **关键差距：当前项目仍保留独立 `mode_transition` 协议分支，不符合 interaction 统一原则** |
| `tool_use: Bash` | 应触发命令确认型 interaction：展示命令、cwd、风险、支持允许/拒绝 | 当前统一映射为较粗的交互审批 | 作为 `permission_request(toolName=Bash,input)` | 当前可工作，但 payload 太粗，前端拿不到足够的稳定结构 |
| `tool_use: Edit` | 应触发文件修改确认型 interaction：展示 `file_path`、编辑意图、必要时显示 diff 摘要 | 当前也统一归到较粗审批模型 | 作为 `permission_request(toolName=Edit,input)` | 当前映射过粗，建议补 `subject.filePath/toolName/risk` 等字段 |
| `tool_use: Write` | 应触发文件写入确认型 interaction：展示 `file_path`、覆盖风险、写入意图 | 当前也归到较粗审批模型 | 作为 `permission_request(toolName=Write,input)` | 与 Edit 类似，当前缺少细粒度 `subject` 信息 |
| `tool_use: Read` | 默认更偏观测行为，不一定适合作为主 Interaction 入口 | 当前也可能归到较粗审批模型 | 可作为 permission request，但前端通常不强调 | 当前设计偏粗，建议降低其交互优先级或弱化为观测 |
| `result` | 一轮运行结束，包含 stop reason / usage / cost / duration / 最终结果 | `chat.state = final` + `agent.stream = lifecycle(end)`，并保存 `sdkSessionId` | `complete`，并补 token budget/status 等信息 | 当前项目已具备主要能力 |
| abort / signal | 用户中断或运行终止 | `chat.state = aborted` 或 `error` | 多以 `complete(aborted)` 或 `error` 表达 | 当前项目已具备主要能力 |
| import/query error | SDK 引入失败、调用失败、流迭代异常 | `chat.state = error` + lifecycle error | `error` | 当前项目已具备主要能力 |
| SDK 中需要等待用户决策的 tool hook（逻辑语义） | 应在同一轮运行中挂起，等待前端通过统一 Interaction 协议返回结果后继续执行 | 当前项目尚未在 `claude-sdk-bridge.ts` 中建立通用 hook/resolver 机制；目前多数靠 `toolEnd -> interaction.requested -> resolve -> follow-up continuation` | `claudecodeui` 在 SDK 侧维护 pending approvals，通过 `waitForToolApproval/resolveToolApproval` 实现原运行继续 | **这是当前项目从“可用”迈向“正确”的关键缺口** |
| Pending interactions during reconnect | 前端重连后应能恢复当前 session 尚未处理的交互请求 | 当前项目有内存 registry，但尚未形成明显的重连恢复协议/自动回放机制 | `claudecodeui` 有 session 维度的 pending approvals 查询与 reconnect writer 恢复能力 | **当前项目还缺少 pending interaction 恢复与回放机制** |
| 统一前端渲染入口 | 前端应只依赖少量稳定消息：`chat` / `agent` / `interaction.*`，而不必理解大量底层 stream | 当前前端如果接入，将需要理解较多 `agent.stream` 类型 | `claudecodeui` 更偏前端消息模型 | **当前项目还缺少一层专门面向前端稳定渲染的 Interaction Schema 与 renderer 约定** |

---

## 6. 关键问题分析

### 6.1 为什么 `interaction.*` 必须成为唯一主出口

如果前端最终要稳定接入，就必须把“需要人做决定”的所有场景统一起来。无论底层是：

- `AskUserQuestion`
- `ExitPlanMode`
- `Bash`
- `Edit`
- `Write`
- 未来可能出现的网络访问、危险操作、子 agent 启动等

只要它是 HITL，本质上都只有一件事：**agent 在某个节点等待用户给出决策**。因此最自然的协议出口应该是：

- `interaction.requested`
- `interaction.resolved`
- `interaction.resolve`

真正的差异，不应该靠新增更多 `event` / `method` 类型来表达，而应该靠 payload 内的：

- `kind`
- `subject`
- `options`
- `questions`
- `inputSchema`
- `uiHints`

来表达。

### 6.2 为什么当前 `AskUserQuestion` 的实现还不够理想

从 `server.ts` 逻辑看，当前项目对 `AskUserQuestion` 的做法是：

1. SDK 输出 tool use
2. 网关识别为 `AskUserQuestion`
3. 发出 `interaction.requested(kind=ask_user)`
4. 用户调用 `interaction.resolve`
5. 服务端把用户回答写入 history
6. 再调用 `startFollowUpChat(...)` 发起一轮新的聊天

这个方案短期可工作，而且协议上已经比很多原始实现更规整。但它的问题是：**回答并没有真正回到原来的工具调用上下文中继续执行**。换句话说，它更像是“Claude 问了一句，用户答了一句，然后我们把答复当成下一轮用户消息继续聊”。

而 `claudecodeui` 更接近 Claude Agent SDK 的原生交互模式：运行中的 agent 在某个节点挂起，等待人做决定，前端答复后，继续同一条运行流。这种方式有几个优势：

- tool call 与 answer 的语义更连续
- agent 内部上下文不会被拆成两轮“伪对话”
- 前端可以更精确表达“等待你的输入后继续当前任务”
- 日后扩展更多交互型工具时，不需要每种都走 follow-up continuation workaround

### 6.3 为什么 `ExitPlanMode` 不应该继续保留独立协议

`ExitPlanMode` 本质上也是一种 interaction，而不是必须新增 `mode_transition.resolve` 这种顶层方法的特殊概念。用户面对它时，做的仍然只是一个交互决策：

- 继续执行
- 继续规划

因此从协议设计上，它应该与 `AskUserQuestion`、`Bash` 审批共享同一种主协议：`interaction.*`。区别只在于：

- `kind = 'mode_switch'`
- `subject.type = 'mode'`
- `subject.fromMode / toMode`
- `options = proceed / stay`

### 6.4 为什么旧的粗粒度执行审批还不够

如果所有执行类交互都只有：

- `kind: 'exec'`
- `command`
- `cwd`

那么前端很难做出可靠的渲染与风险提示。比如：

- `Bash npm test` 与 `Bash rm -rf` 风险完全不同
- `Edit src/server.ts` 与 `Write package.json` 的风险提示和展示重点也不同
- 文件写入场景更适合展示 `filePath` / `overwrite risk`
- 命令执行场景更适合展示 `command` / `cwd` / `shell risk`

因此不建议继续扩散新的顶层 event 类型，而应统一在更稳定的 interaction schema 下，通过 `subject.type`、`subject.toolName`、`uiHints.severity` 等字段细分。

### 6.5 为什么 pending interaction 与重连恢复是必须项

`claudecodeui` 在交互型工具处理上有一个很实用的能力：当 websocket 重连或页面刷新时，仍然能恢复当前 session 尚未处理的 pending approvals。当前项目虽然已经有一些 in-memory pending registry，但还缺少明确的：

- session 维度 pending interaction 查询接口
- reconnect 时自动 replay `interaction.requested`
- 或在 session attach 时回放未决交互

如果这部分没有补上，那么任何“等待用户输入”的交互，在前端刷新后都可能丢失。这会严重影响 HITL 可用性，因此它是协议落地时必须补齐的能力，而不是纯粹的体验优化。

---

## 7. 推荐的统一 Interaction Schema

建议最终稳定为类似下面这种结构：

```ts
{
  interactionId,
  runId,
  sessionKey,
  kind,              // ask_user | confirm | mode_switch
  title,
  description,
  prompt,
  subject: {
    type,            // tool | command | file | mode
    toolName,
    toolCallId,
    command,
    cwd,
    filePath,
    fromMode,
    toMode,
  },
  questions,
  options,
  inputSchema,
  uiHints,
  createdAtMs,
  expiresAtMs,
}
```

有了这个 schema，前端就可以只写一个统一的 interaction renderer registry：

- `ask_user` -> 问题表单卡片
- `confirm + subject.type=command` -> 命令确认卡片
- `confirm + subject.type=file` -> 文件变更确认卡片
- `mode_switch` -> 计划模式切换卡片

而不必依赖底层 tool raw event。

---

## 8. 推荐演进方向

### 8.1 阶段一：先统一协议出口，不立即改造运行时

这一阶段的目标是先让协议对前端稳定，即使底层部分逻辑仍然暂时用 follow-up continuation 实现。

建议：

- 正式协议只保留 `interaction.resolve`
- 所有 HITL 请求统一发 `interaction.requested`
- 所有 HITL 完成统一发 `interaction.resolved`
- `mode_transition.resolve`、旧 `approval.resolve` 降级为兼容层
- 为所有交互场景统一 Interaction Schema

### 8.2 阶段二：补齐前端稳定渲染模型

这一阶段目标是让前端完全以 `interaction.*` 为交互入口，而不是依赖原始 tool 或 mode 流。

建议：

- 建立 interaction renderer registry
- 用 `kind + subject.type + uiHints.variant` 做渲染分发
- 维护前端 `pendingInteractions` store
- 接入 reconnect / replay 的 pending interaction 恢复能力

### 8.3 阶段三：升级 SDK 运行时为真正挂起/恢复

这一阶段目标是不再依赖 `startFollowUpChat(...)` 作为交互恢复手段。

建议：

- 调研并接入 Claude Agent SDK 的 hook / `canUseTool` / continuation 能力
- 在服务端建立 `interactionId -> resolver` registry
- `interaction.resolve` 后不再触发 follow-up chat，而是直接恢复原 run
- 迁移顺序建议为：
  1. `ExitPlanMode`
  2. `AskUserQuestion`
  3. `Bash/Edit/Write` 等确认型工具

### 8.4 阶段四：收敛 agent 内部流，明确稳定对外协议

当 interaction 层完成统一后，建议进一步梳理哪些 `agent.stream` 是对外稳定协议，哪些只是内部调试通道。

推荐对外稳定依赖的最小集合：

- `chat`
- `agent.stream = thinking`
- `agent.stream = tool`
- `agent.stream = lifecycle`
- `interaction.requested`
- `interaction.resolved`

其余例如：

- `message`
- `content_block`
- `command_output`
- `mode_transition`
- `approval`

应在文档中标为：

- 内部调试用途
- 兼容层用途
- 非推荐前端强依赖字段

---

## 9. 结论

最终建议如下：

- **唯一稳定的 HITL 请求事件**：`interaction.requested`
- **唯一稳定的 HITL 完成事件**：`interaction.resolved`
- **唯一稳定的 HITL 响应方法**：`interaction.resolve`
- **推荐最小 kind 集合**：`ask_user` / `confirm` / `mode_switch`
- **所有差异统一通过 payload 表达**：`subject` / `questions` / `options` / `inputSchema` / `uiHints`
- **前端应依赖 interaction 语义，不依赖 SDK 原始交互细节**
- **服务端允许先用 follow-up continuation 兼容实现，但长期必须升级为原运行挂起/恢复**

这套方向既能满足当前项目“协议网关化”的目标，也能吸收 `claudecodeui` 在交互连续性方面的经验，并为后续多端接入、协议稳定演进提供清晰边界。
