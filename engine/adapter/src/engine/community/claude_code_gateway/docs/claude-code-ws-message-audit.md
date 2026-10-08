# Claude Code 消息类型与本项目 WebSocket 协议支持审计

## 1. 目标

本文档用于回答以下问题：

1. **Claude Code / Claude CLI / Claude Agent SDK 实际会发出哪些消息类型**
2. **当前项目 WebSocket 协议已经承接了哪些类型**
3. **还有哪些内容缺少支持，或存在语义/实现不一致**
4. **不同消息类型在前端应该如何渲染与交互**

本文基于以下代码与文档整理：

- `src/claude-cli-bridge.ts`
- `src/claude-sdk-bridge.ts`
- `src/server.ts`
- `src/types.ts`
- `docs/websocket-protocol.md`
- `docs/websocket-protocol-v2-proposal.md`
- `docs/claude-code-integration-guide.md`

---

## 2. 结论先看

### 2.1 当前项目已经覆盖的 Claude Code 核心消息

当前 Relay 已经支持并映射了 Claude Code / CLI 流中的核心结构：

- `system`
- `stream_event.content_block_start`
- `stream_event.content_block_delta`
  - `text_delta`
  - `thinking_delta`
  - `input_json_delta`
- `stream_event.content_block_stop`
- `stream_event.message_start`
- `stream_event.message_delta`
- `stream_event.message_stop`
- `result`

并且在网关侧进一步抽象成：

- `chat` 文本流
- `agent.lifecycle`
- `agent.message`
- `agent.content_block`
- `agent.tool`
- `agent.thinking`
- `agent.command_output`
- `agent.interaction`
- `agent.mode_transition`
- `agent.assistant`

### 2.2 当前协议已经支持的 Claude Code 高层语义

已经支持：

1. **普通文本输出**
2. **thinking / reasoning 增量**
3. **工具调用开始 / 参数增量 / 结束**
4. **Bash / Read / Write / Edit 的执行型交互**
5. **AskUserQuestion 的用户问答交互**
6. **ExitPlanMode 的模式切换交互**
7. **消息级元信息**（message start/stop、usage）
8. **最终结果**（stopReason、usage、cost/duration/numTurns）
9. **chat.abort 中断**
10. **chat.history 历史拉取**

### 2.3 目前仍然缺少或值得补齐的部分

从“Claude Code 支持能力”对比“当前 WebSocket 协议实现”看，仍有这些缺口：

1. **没有把 text 类型内容块显式映射到 agent.content_block**  
   当前文本只走 `chat` 通道；如果前端需要严格复刻 Claude 原生消息块结构，信息不完整。

2. **thinking 内容块边界没有稳定保留**  
   `thinking_delta` 已支持，但 `content_block_start/stop` 对 thinking 块在 bridge 层会被当作普通块处理，server 也能转发；不过目前文档与 UI 设计重点仍在 thinking 文本，不足以支持“分段折叠的 thinking block UI”。

3. **exec 类型交互未保留更细的工具上下文**  
   现在 `interaction.requested(kind=exec)` 只带 `command/cwd`。对于 `Read/Edit/Write`，如果前端要做更友好的审批卡片，最好补充：
   - `toolName`
   - `filePath`
   - `diffPreview` / `writePreview`
   - `reason` / `justification`

4. **AskUserQuestion 响应字段名与草案文档不完全一致**  
   文档草案中偏向 `decision`，实际实现里兼容 `decision/action`，并主要使用 `action=submit|cancel`。建议统一字段约定。

5. **mode_transition 缺少顶层事件频道**  
   当前只通过 `agent` 流发 `mode_transition`，没有像 `interaction.requested` 那样提供顶层 `mode_transition.requested/resolved`。如果前端只监听顶层事件，会拿不到该交互。

6. **工具执行结果与工具输入仍有语义混用**  
   当前 `tool:result.result` 实际存的是工具输入参数，而不是工具真实执行结果。这是当前 CLI 流限制导致，但文档和客户端必须明确，否则容易误解。

7. **尚未看到对更多 Claude Code 专项事件的扩展位设计**  
   例如未来 SDK/CLI 若暴露更细的：
   - MCP server 交互事件
   - patch/diff 级编辑结果
   - richer approval metadata
   当前协议能承载，但没有标准字段规范。

8. **没有专门的“tool result content”结构化回传**  
   Claude 工具输出多数仍被并入后续文本块。前端若要把“工具结果”和“自然语言回复”分开渲染，现协议颗粒度还不够。

---

## 3. Claude Code 消息类型梳理

这里的“Claude Code 消息”分两层理解：

1. **底层流消息**：CLI / SDK stream-json 里实际出现的 NDJSON 事件
2. **高层语义消息**：工具调用、提问、审批、退出 plan mode 等

### 3.1 底层 stream-json 消息类型

| Claude Code 原始消息 | 典型位置 | 含义 | 本项目是否处理 | 当前映射 |
|---|---|---|---|---|
| `system` | 顶层 `type` | 初始化信息，含 `session_id/cwd/tools` | 已处理 | `agent.lifecycle(start)` |
| `stream_event.content_block_start` | `event.type` | 内容块开始 | 已处理 | `agent.content_block(start)` / `agent.tool(start)` |
| `stream_event.content_block_delta` + `text_delta` | `event.delta.type` | 文本增量 | 已处理 | `chat(delta)` |
| `stream_event.content_block_delta` + `thinking_delta` | `event.delta.type` | 思考增量 | 已处理 | `agent.thinking` |
| `stream_event.content_block_delta` + `input_json_delta` | `event.delta.type` | 工具参数 JSON 增量 | 已处理 | `agent.tool(update)` |
| `stream_event.content_block_stop` | `event.type` | 内容块结束 | 已处理 | `agent.content_block(stop)` / `agent.tool(result)` |
| `stream_event.message_start` | `event.type` | 消息开始 | 已处理 | `agent.message(start)` |
| `stream_event.message_delta` | `event.type` | stop reason / usage 增量 | 已处理 | `agent.assistant(usage)` |
| `stream_event.message_stop` | `event.type` | 消息结束 | 已处理 | `agent.message(stop)` |
| `result` | 顶层 `type` | 最终结果、usage、stop_reason | 已处理 | `chat(final)` + `agent.lifecycle(end)` + `agent.assistant` |
| `assistant` | 顶层 `type`（SDK fallback） | 完整 assistant message | 部分处理 | 仅作为 streamedText fallback |

### 3.2 Claude Code 高层语义类型

| Claude Code 高层语义 | 触发来源 | 当前项目支持情况 | 网关映射 |
|---|---|---|---|
| Assistant 文本回复 | text block | 已支持 | `chat` |
| Thinking / Reasoning | thinking block | 已支持 | `agent.thinking` |
| 通用工具调用 | tool_use | 已支持 | `agent.tool` |
| Bash | tool_use name=`Bash` | 已支持 | `tool` + `command_output` + `interaction(exec)` |
| Read | tool_use name=`Read` | 已支持 | `tool` + `command_output` + `interaction(exec)` |
| Write | tool_use name=`Write` | 已支持 | `tool` + `command_output` + `interaction(exec)` |
| Edit | tool_use name=`Edit` | 已支持 | `tool` + `command_output` + `interaction(exec)` |
| AskUserQuestion | tool_use name=`AskUserQuestion` | 已支持 | `tool(result)` + `interaction(ask_user)` |
| ExitPlanMode | tool_use name=`ExitPlanMode` | 已支持 | `tool(result)` + `mode_transition` |
| 会话初始化 | `system` | 已支持 | `lifecycle(start)` |
| 停止原因 / 用量统计 | `message_delta` / `result` | 已支持 | `assistant` / `chat(final)` |
| 中断 | gateway abort | 已支持 | `chat(aborted)` |

---

## 4. 当前 WebSocket 协议的承接方式

### 4.1 顶层频道

当前服务端对客户端暴露的主要事件频道为：

- `connect.challenge`
- `tick`
- `chat`
- `agent`
- `interaction.requested`
- `interaction.resolved`

以及请求方法：

- `connect`
- `chat.send`
- `chat.abort`
- `chat.history`
- `interaction.resolve`
- `mode_transition.resolve`

### 4.2 agent 结构化流

当前 `agent.stream` 已定义：

- `lifecycle`
- `tool`
- `assistant`
- `thinking`
- `command_output`
- `approval`（类型里保留，但主实现已迁移到 interaction）
- `interaction`
- `mode_transition`
- `message`
- `content_block`

其中实际主线路已经是：

- **exec 审批** → `interaction(kind=exec)`
- **AskUserQuestion** → `interaction(kind=ask_user)`
- **ExitPlanMode** → `mode_transition`

这说明代码实现已经更接近 `docs/websocket-protocol-v2-proposal.md`，而不是旧版 `docs/websocket-protocol.md` 中的 `exec.approval.*` 语义。

---

## 5. 对照表：Claude Code 消息体 / WebSocket 服务消息体 / 对应功能 / 渲染交互

> 下面是最核心的对照表，可直接给产品、前端、协议维护者一起看。

| Claude Code 发出的消息体 | WebSocket 服务端转译消息体 | 对应功能 | 前端渲染 / 交互建议 |
|---|---|---|---|
| `{"type":"system","session_id":"sess-123","cwd":"/project","tools":["Bash","Read","Write"]}` | `event=agent, stream=lifecycle, data={ phase:"start", sessionId, cwd, tools }` | 一次 Claude 运行初始化 | 会话头部可显示“已连接 Claude / 当前 cwd / 可用工具”；一般不必进聊天气泡 |
| `{"type":"stream_event","event":{"type":"message_start","message":{"id":"msg_1","model":"claude-sonnet-4-5","usage":{"input_tokens":100}}}}` | `event=agent, stream=message, data={ phase:"start", messageId, model, usage }` | 一轮 assistant message 开始 | 可创建一条“生成中”的 assistant 容器；也可记录模型名、首屏 token 信息 |
| `{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}` | 当前实现理论可映射 `agent.content_block(start)`，但文本 UI 主要不依赖它 | 文本块开始 | 若前端要严格块级渲染可保留；普通聊天 UI 可忽略 |
| `{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"你好"}}}` | `event=chat, payload={ state:"delta", message.content[0].text:"<累积全文>" }` | assistant 文本流式输出 | 聊天气泡按累积文本实时刷新；这是主展示通道 |
| `{"type":"stream_event","event":{"type":"content_block_start","index":1,"content_block":{"type":"thinking","thinking":""}}}` | `event=agent, stream=content_block, data={ phase:"start", index, blockType:"thinking" }` | thinking 块开始 | 可作为“思考中”折叠面板起点；当前很多前端也可以直接忽略边界，仅消费 thinking delta |
| `{"type":"stream_event","event":{"type":"content_block_delta","index":1,"delta":{"type":"thinking_delta","thinking":"我先分析一下"}}}` | `event=agent, stream=thinking, data={ text:"<累积thinking>", delta:"我先分析一下" }` | 推理/思考流 | 建议渲染为可折叠 Thinking 面板，不进入主聊天正文，避免干扰普通用户 |
| `{"type":"stream_event","event":{"type":"content_block_start","index":2,"content_block":{"type":"tool_use","id":"tool_1","name":"Read","input":{}}}}` | `event=agent, stream=content_block, data={ phase:"start", index, blockType:"tool_use", toolCallId:"tool_1", name:"Read" }` + `event=agent, stream=tool, data={ phase:"start", toolCallId:"tool_1", name:"Read" }` | 工具调用开始 | UI 可显示“正在读取文件”的工具卡片，占位为 loading |
| `{"type":"stream_event","event":{"type":"content_block_delta","index":2,"delta":{"type":"input_json_delta","partial_json":"{\"file_path\":\"src/"}}}` | `event=agent, stream=tool, data={ phase:"update", toolCallId:"tool_1", partialArgs:"{\"file_path\":\"src/" }` | 工具参数增量 | 调试面板可展示流式参数；普通 UI 可不展示 |
| `{"type":"stream_event","event":{"type":"content_block_stop","index":2}}`（Read/Write/Edit/Bash 等普通工具） | `event=agent, stream=content_block, data={ phase:"stop", index, blockType:"tool_use" }` + `event=agent, stream=tool, data={ phase:"result", toolCallId:"tool_1", name:"Read", result:{...工具输入...} }` | 工具调用结束 | 工具卡片从 loading → done；注意这里 `result` 实际是 tool input，不是执行输出 |
| 同上，工具名是 `Bash` / `Read` / `Write` / `Edit` | 额外发 `event=agent, stream=command_output, data={ toolCallId, phase:"end", exitCode:0, cwd }` | 执行型工具完成 | 可在工具卡片上显示“执行结束”；但真实输出仍多在后续文本块中 |
| `Read/Edit/Write/Bash` 工具结束后，服务端主动构造交互 | `event=agent, stream=interaction, data={ phase:"requested", interactionId, kind:"exec", command, cwd }` + `event=interaction.requested, payload={ interactionId, runId, sessionKey, kind:"exec", command, cwd, createdAtMs, expiresAtMs }` | 执行授权审批 | 前端应弹出审批卡片/对话框，支持 allow-once / allow-always / deny；若当前实现只识别 submit/cancel，需要前后端再统一 |
| `AskUserQuestion` 的 `tool_use` 原始块 | 当前被压缩：`event=agent, stream=tool, data={ phase:"result", toolCallId, name:"AskUserQuestion", result:{ questions:[...] } }` + `event=agent, stream=interaction, data={ phase:"requested", interactionId, kind:"ask_user", prompt, questions }` + `event=interaction.requested, payload={ interactionId, kind:"ask_user", prompt, questions }` | 向人类发起提问 | 前端应渲染问答卡片：文本输入、单选、多选；提交后通过 `interaction.resolve` 回复 |
| `ExitPlanMode` 的 `tool_use` 原始块 | 当前被压缩：`event=agent, stream=tool, data={ phase:"result", toolCallId, name:"ExitPlanMode", result:{...} }` + `event=agent, stream=mode_transition, data={ phase:"requested", transitionId, kind:"exit_plan_mode", fromMode:"plan", toMode:"execute", summary }` | 请求退出 plan mode | 前端应显示模式切换确认卡片，按钮如“继续执行 / 继续规划”；提交走 `mode_transition.resolve` |
| `{"type":"stream_event","event":{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":20}}}` | `event=agent, stream=assistant, data={ usage:{...} }`（stopReason 在最终 final 中体现） | token 与 stop reason 更新 | UI 可显示 token 统计、结尾原因；通常在消息 footer 或 debug 面板展示 |
| `{"type":"stream_event","event":{"type":"message_stop"}}` | `event=agent, stream=message, data={ phase:"stop" }` | 消息块结束 | 可用于关闭当前 assistant 容器的 streaming 状态 |
| `{"type":"result","result":"最终文本","stop_reason":"end_turn","usage":{...}}` | `event=chat, payload={ state:"final", stopReason, message:{ role:"assistant", content:[{type:"text",text:"最终文本"}] } }` + `event=agent, stream=lifecycle, data={ phase:"end", stopReason }` + `event=agent, stream=assistant, data={ usage/cost/duration/numTurns }` | 整轮回答完成 | 聊天气泡定稿；结束 loading；展示 stop reason、usage、成本、时长 |
| 客户端发 `chat.abort` 后运行被中断 | `event=chat, payload={ state:"aborted", errorMessage:"aborted", stopReason:"stop" }` | 用户中止回答 | UI 应将当前消息标记为“已停止”，保留已生成部分 |
| 服务端执行失败 / bridge 出错 | `event=chat, payload={ state:"error", errorMessage:"..." }` | 本轮失败 | 聊天气泡显示错误态，支持重试 |
| 用户对 ask_user 提交答案 | `req interaction.resolve { interactionId, action:"submit", message:"..." }` → `event=agent, stream=interaction, data={ phase:"answered", ... }` + `event=interaction.resolved, payload={ phase:"answered", ... }` | 回复 Claude 的提问 | 提交后卡片变为已回答态；并等待 follow-up run 继续输出 |
| 用户取消 ask_user | `req interaction.resolve { interactionId, action:"cancel" }` → resolved events | 放弃回答 | 卡片标记已取消；Claude follow-up 可继续调整策略 |
| 用户确认/拒绝 mode transition | `req mode_transition.resolve { transitionId, decision:"proceed"|"stay" }` | 模式切换确认 | 卡片展示最终决策，并触发 follow-up run |

---

## 6. 现状审计：还缺少哪些协议支持

下面按“是否建议补齐”来分级说明。

### 6.1 高优先级建议补齐

#### A. 明确并统一 exec 交互的响应协议

当前代码中 `handleInteractionResolve()` 对 exec 和 ask_user 走了同一入口，但：

- 文档草案偏向 `decision`
- 代码兼容 `decision` 和 `action`
- ask_user 主要语义是 `submit/cancel`
- exec 语义应该是 `allow-once/allow-always/deny`

**建议：**

- `kind=exec` 固定使用 `decision`
  - `allow-once`
  - `allow-always`
  - `deny`
- `kind=ask_user` 固定使用 `action`
  - `submit`
  - `cancel`
- 服务端文档明确“统一入口，不同 kind 不同字段”或者统一为 `decision`，但不要混写。

#### B. 给 mode_transition 增加顶层事件

现在只有：

- `agent.stream = mode_transition`
- 请求方法 `mode_transition.resolve`

没有：

- `mode_transition.requested`
- `mode_transition.resolved`

**问题：** 如果有些客户端只做顶层事件订阅，不消费 `agent` 明细流，就拿不到模式切换请求。

**建议：** 补充顶层事件，与 interaction 设计对齐。

#### C. exec 授权事件补充更丰富字段

当前 exec 交互只给：

- `command`
- `cwd`

对于 UI 还不够。

**建议补充：**

- `toolName`
- `filePath`（Read/Write/Edit 时）
- `host`（远端命令时预留）
- `security`
- `resolvedPath`
- `preview` / `diffPreview`
- `reason` / `ask`

这样审批弹窗才足够像 Claude Code 原生体验。

### 6.2 中优先级建议补齐

#### D. 显式支持文本块 content_block 事件的产品语义

虽然类型和代码链路里有 `content_block`，但实际主渲染仅依赖 `chat(delta)`。

**如果目标是“兼容 Claude Code 的块级消息渲染”**，建议前端正式支持：

- text block start/stop
- thinking block start/stop
- tool_use block start/stop

这样可以实现：

- 混合内容块渲染
- 块级折叠
- 更精确的 streaming 结束控制

#### E. 区分“工具输入”和“工具执行结果”

当前 `tool:result.result` 实际上是 **tool input**。

**建议：** 改名或补字段：

- `input`
- `executionResult`（若将来能拿到）

避免客户端把 `result` 错当输出结果。

### 6.3 低优先级/前瞻性建议

#### F. 预留 richer Claude Code 扩展事件

例如未来可能出现：

- MCP tool 调用明细
- Patch / diff 片段
- 文件写入前后的结构化摘要
- 多 agent / subtask 事件

当前协议可通过 `agent.stream` 扩展，但建议提前约定：

- `stream = mcp`
- `stream = patch`
- `stream = subtask`

至少在文档里声明扩展原则。

---

## 7. 不同消息类型需要的前端交互类型总结

### 7.1 纯展示类

| 消息类型 | 是否需要用户操作 | 推荐 UI |
|---|---|---|
| `chat(delta/final)` | 否 | 普通 assistant 聊天气泡 |
| `agent.thinking` | 否 | 折叠的 Thinking 面板 |
| `agent.message` | 否 | 仅内部状态控制 / debug |
| `agent.lifecycle` | 否 | 会话状态、消息 footer、debug 面板 |
| `agent.assistant` | 否 | token/cost/耗时 footer |
| `agent.content_block` | 否 | 高级模式下的块级调试视图 |
| `agent.tool(start/update/result)` | 否 | 工具调用卡片 / 时间线 |
| `agent.command_output` | 否 | 工具卡片中的执行状态 |

### 7.2 需要用户响应类

| 消息类型 | 用户动作 | 返回方法 | 推荐 UI |
|---|---|---|---|
| `interaction.requested (kind=exec)` | 批准/拒绝执行 | `interaction.resolve` | 审批弹窗 / 安全卡片 |
| `interaction.requested (kind=ask_user)` | 输入答案或取消 | `interaction.resolve` | 问答卡片 / 表单 |
| `agent.mode_transition (phase=requested)` | 继续执行 / 留在 plan | `mode_transition.resolve` | 模式切换确认卡片 |

### 7.3 推荐的前端渲染层次

建议前端分三层处理：

1. **主聊天层**
   - 只看 `chat` 通道
   - 用于稳定输出 assistant 文本

2. **结构化时间线层**
   - 看 `agent` 通道
   - 展示 thinking、tool、usage、lifecycle

3. **交互层**
   - 看 `interaction.requested`、`interaction.resolved`
   - 看 `agent.mode_transition`
   - 负责审批、问答、模式切换

这样能避免把所有结构化消息都硬塞进聊天正文里。

---

## 8. 推荐的协议/文档修正方向

### 8.1 建议把正式协议基线切到 v2 语义

当前代码实际上已经采用：

- `interaction.requested/resolved`
- `mode_transition.resolve`
- `AskUserQuestion != approval`
- `ExitPlanMode != approval`

所以建议：

1. 将 `docs/websocket-protocol-v2-proposal.md` 升级为正式协议
2. 将 `docs/websocket-protocol.md` 标记为旧版或兼容说明
3. 在 README 或新文档中明确：
   - exec approval 已统一进 interaction(kind=exec)
   - ask_user 使用 interaction(kind=ask_user)
   - ExitPlanMode 使用 mode_transition

### 8.2 建议统一名词

- `interactionId`：统一用于 exec / ask_user
- `transitionId`：统一用于 mode transition
- `decision`：用于 exec / mode transition
- `action`：仅 ask_user 使用
- `tool.result`：建议文档标注为 `tool input snapshot`

---

## 9. 最终判断：项目协议还缺什么

如果目标只是“能跑通 Claude Code 的基本对话 + 工具 + 人机交互”，**当前项目已经基本支持到位**。

如果目标是“完整、稳定、可扩展地承接 Claude Code 支持的消息类型，并让前端可以做接近原生 Claude Code 的渲染/交互”，**还建议补这 5 项**：

1. **统一 exec / ask_user 的回复字段规范**
2. **为 mode_transition 增加顶层 requested/resolved 事件**
3. **为 exec 审批补充更多上下文字段（filePath/diff/toolName 等）**
4. **明确 tool.result 实际是 input snapshot，不是 execution result**
5. **把 text/thinking/tool_use 的块级渲染语义在协议文档中完全定稿**

---

## 10. 一句话总结

当前 Relay 已经覆盖 Claude Code 的主流消息类型，并且已经从旧的 `approval` 设计演进到更合理的 `interaction + mode_transition` 语义；剩余工作主要不是“能不能收消息”，而是 **把交互字段、块级语义和前端渲染协议再补齐到正式可依赖的程度**。
