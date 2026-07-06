# BCS 平台级协同调用模式设计补充

- 日期：2026-06-30
- 状态：草稿（待评审）
- 范围：BCS 主从 / `manager_worker` 协同中的上下文注入与 provider/plugin 协同回传
- 基于：
  - `docs/superpowers/specs/2026-06-11-bcs-master-slave-engine-neutral-coordination-design.md`
  - `docs/superpowers/specs/2026-05-24-bcs-downlink-provider-design.md`

## 1. 背景

`2026-06-11-bcs-master-slave-engine-neutral-coordination-design.md` 已把主从协同的 canonical protocol 收敛到 BCS
拥有的 `task.dispatch` / `task.message` / `task.complete`，并用 MCP server 回显 + exec tool result 的方式解决
TeamClaw / OpenClaw / Claude Code 这类 `mcporter` 调 MCP 的接入路径。

但实际接入形态不止一种：

1. Provider downlink 平台中，有的平台通过 `mcporter` / `exec` 调 BCS MCP。
2. 有的平台原生支持 MCP tool，不经过 `mcporter`。
3. 有的平台或插件运行环境直接注入原生 BCS 协同 tool，并不是 MCP。
4. 主从群里还可能混入原来的上行 WebSocket bot。

因此，主从上下文注入不能再用 group 级别的“是否存在 provider downlink bot”来统一决定文案，而必须按每个
recipient bot 的实际平台能力生成对应上下文。

## 2. 决策

核心决策：

1. 协同调用模式归平台，不归 bot。
2. Provider downlink bot 的模式只来自 `provider.config.coordination`。
3. ProviderBotBinding 不提供 coordination profile 或 override。
4. Plugin 连接方式是平台能力信号，不是 bot profile。当前插件默认按 `native_tool` 处理。
5. 传统 WebSocket / 无法识别来源的上行 bot 继续走 legacy 兼容上下文。

如果同一个业务平台确实需要同时支持两种调用面，应注册成两个 Provider，而不是在一个 Provider 下面让不同 bot
覆盖模式：

```text
provider_teamclaw_mcporter: mode = mcporter_mcp
provider_teamclaw_native:   mode = native_tool 或 native_mcp
```

## 3. 模式定义

新增平台级枚举 `CoordinationMode`：

| mode | 含义 | 上下文写法 | 回传形态 |
| --- | --- | --- | --- |
| `mcporter_mcp` | 运行环境通过 `mcporter` / `exec` 调 BCS MCP | `mcporter call <server>.<tool> ...` | `tool_result`，从 stdout 解析 MCP 回显 |
| `native_mcp` | 运行环境原生挂载 BCS MCP server/tool | 直接调用 MCP server 上的 tool | `coordination_intent` |
| `native_tool` | 运行环境原生注入 BCS 协同 tool，但不是 MCP | 直接调用原生 tool | `coordination_intent` 或现有 native tool RPC |
| `disabled` | 平台不参与结构化协同 | 不注入结构化协同工具文案 | 不接受协同回传 |
| `legacy_upstream` | 派生模式，仅用于传统 WS bot | 保持旧上行兼容文案 | 沿用现有 WS 行为 |

`legacy_upstream` 不是 Provider 可配置值，只是 BCS 对没有 provider/plugin 能力信号的 WebSocket bot 派生出的运行时视图。

## 4. Provider 配置

Provider 的 `config` 增加 `coordination` 节点：

```json
{
  "downlink": {
    "enabled": true,
    "webhook_url": "https://provider.example.com/bcs/webhook",
    "auth_mode": "static_bearer",
    "protocol_version": "1.0"
  },
  "coordination": {
    "mode": "mcporter_mcp",
    "mcp_server": "bcs",
    "mcporter_command": "mcporter"
  }
}
```

字段语义：

- `mode`：唯一核心分支，决定平台协同调用面。
- `mcp_server`：MCP server 名称，只对 `mcporter_mcp` 和 `native_mcp` 有意义。
- `mcporter_command`：mcporter 执行入口，只对 `mcporter_mcp` 有意义。

校验规则：

| mode | `mcp_server` | `mcporter_command` |
| --- | --- | --- |
| `mcporter_mcp` | 必填 | 必填 |
| `native_mcp` | 必填 | 不允许配置 |
| `native_tool` | 不允许配置 | 不允许配置 |
| `disabled` 或缺省 | 不需要 | 不需要 |

缺省策略：

- Provider 未配置 `coordination` 时，等同 `disabled`。
- BCS 不从 ProviderBotBinding 或 Bot metadata 补任何 coordination override。

## 5. Plugin / WebSocket 派生规则

WebSocket bot 需要区分来源：

```text
resolve_coordination_surface(bot_id):
  if bot is bound to HttpProvider:
      return provider.config.coordination

  if bot is connected through plugin:
      return native_tool

  return legacy_upstream
```

Plugin 来源应由连接元数据表达，例如 `bot.connect` 中的 `client_kind = "plugin"` 或现有等价字段。该字段只表达连接来源，
不表达 coordination profile；BCS 固定把当前 plugin 来源派生为 `native_tool`。

如果未来某个 plugin 明确暴露的是 MCP tool，而不是原生 tool，应该让该插件平台声明自己的平台级 mode，而不是让单个 bot
覆盖。第一版不引入 plugin-bot 级 override。

## 6. 上下文注入

上下文注入按 recipient bot 单独生成，不能按 group 统一生成。

### 6.1 `mcporter_mcp`

Manager：

```text
你当前平台通过 mcporter 调用 BCS MCP 工具。
需要派发子任务时，使用：
`<mcporter_command> call <mcp_server>.bcs_assign_task target_bot="<目标Bot名称或ID>" message="<任务内容>"`
任务可以结束时，使用：
`<mcporter_command> call <mcp_server>.bcs_task_complete summary="<最终总结>"`

不要直接调用原生发送工具来派发子任务。
不要在普通回复中伪造工具结果。
```

Worker：

```text
你当前平台通过 mcporter 调用 BCS MCP 工具。
收到 manager 派发的任务后，使用：
`<mcporter_command> call <mcp_server>.bcs_send_task_message message="<结果、进展、问题或阻塞>"`

不要直接面向用户输出最终答案；最终汇总由 manager 完成。
不要在普通回复中伪造工具结果。
```

### 6.2 `native_mcp`

Manager：

```text
你当前平台原生提供 BCS MCP 工具。
需要派发子任务时，直接调用 MCP server `<mcp_server>` 上的 `bcs_assign_task`。
任务可以结束时，直接调用 MCP server `<mcp_server>` 上的 `bcs_task_complete`。

不要使用 mcporter、exec、bash。
不要在普通回复中伪造工具结果。
```

Worker：

```text
你当前平台原生提供 BCS MCP 工具。
收到 manager 派发的任务后，直接调用 MCP server `<mcp_server>` 上的 `bcs_send_task_message`
回传结果、进展、问题或阻塞。

不要使用 mcporter、exec、bash。
不要直接面向用户输出最终答案。
```

### 6.3 `native_tool`

Manager：

```text
你当前平台原生提供 BCS 协同工具。这些工具是当前运行环境中的原生 tools，不是 MCP server 工具。
需要派发子任务时，直接调用原生工具 `bcs_assign_task`。
任务可以结束时，直接调用原生工具 `bcs_task_complete`。

不要使用 mcporter、exec、bash。
不要写 MCP server 名称。
不要在普通回复中伪造工具结果。
```

Worker：

```text
你当前平台原生提供 BCS 协同工具。这些工具是当前运行环境中的原生 tools，不是 MCP server 工具。
收到 manager 派发的任务后，直接调用原生工具 `bcs_send_task_message`
回传结果、进展、问题或阻塞。

不要使用 mcporter、exec、bash。
不要写 MCP server 名称。
不要直接面向用户输出最终答案。
```

### 6.4 `legacy_upstream`

保持现有上行兼容上下文，不注入 `mcporter`、MCP server 或 native tool 的新文案。

## 7. Provider 协同回传

Provider downlink 平台可以通过 `/bot/events` 或后续独立 endpoint 回传协同事件。回传必须和 Provider 的
`coordination.mode` 匹配。

### 7.1 `mcporter_mcp`

只接受 `kind = "tool_result"`：

```json
{
  "run_id": "run_xxx",
  "tool_call_id": "tc_001",
  "kind": "tool_result",
  "tool_name": "exec",
  "result_text": "{\"__bcs_coordination__\":true,\"v\":1,\"tool\":\"bcs_assign_task\",\"arguments\":{\"target_bot\":\"worker\",\"message\":\"...\"},\"status\":\"received\"}"
}
```

BCS 复用现有 `CoordinationCall` 解析、exec 工具白名单、角色校验、`run_id + tool_call_id` 去重和 session
guard。解析成功后再映射到现有 `task.dispatch` / `task.message` / `task.complete`。

### 7.2 `native_mcp`

只接受 `kind = "coordination_intent"`，并要求 intent 来自声明的 `mcp_server`：

```json
{
  "run_id": "run_xxx",
  "tool_call_id": "tc_001",
  "kind": "coordination_intent",
  "mcp_server": "bcs",
  "intent": {
    "v": 1,
    "tool": "bcs_assign_task",
    "arguments": {
      "target_bot": "worker",
      "message": "..."
    }
  }
}
```

### 7.3 `native_tool`

接受 `kind = "coordination_intent"`，但不要求 `mcp_server`：

```json
{
  "run_id": "run_xxx",
  "tool_call_id": "tc_001",
  "kind": "coordination_intent",
  "intent": {
    "v": 1,
    "tool": "bcs_send_task_message",
    "arguments": {
      "message": "worker result"
    }
  }
}
```

现有 plugin 原生 tool 如果已经直接调用 BCS task RPC，可以继续走现有 RPC；本设计只要求注入文案按 `native_tool`
表达，不强制把 plugin 迁到 provider callback。

### 7.4 mismatch 处理

BCS 必须拒绝 mode 与回传形态不匹配的事件：

| provider mode | 允许回传 | 拒绝示例 |
| --- | --- | --- |
| `mcporter_mcp` | `tool_result` | `coordination_intent` |
| `native_mcp` | `coordination_intent` + matching `mcp_server` | `tool_result`、缺失/不匹配 `mcp_server` |
| `native_tool` | `coordination_intent` without MCP requirement | `tool_result`、带 MCP-only 语义的回传 |
| `disabled` | 无 | 任意协同回传 |

拒绝时返回 `400 invalid_coordination_mode`，日志记录 `provider_id`、`bot_id`、`mode`、`kind`、`run_id`，但不记录
工具参数全文或 credential。

## 8. 代码落点

实现时保持现有分层：

1. 在 domain/protocol 层增加 `ProviderCoordinationConfig`、`CoordinationMode` 和 provider event DTO。
2. 在 Provider core 的注册/更新路径解析并校验 `config.coordination`。
3. 在 BotRegistry/Provider 解析路径提供 `resolve_coordination_surface(bot_id)` 或等价方法。
4. 在 `bcs-system-message` 的 session context producer 中，把 group 级判断改为 recipient 级模板选择。
5. 把 WS agent-event echo 和 Provider coordination callback 的解析执行逻辑收敛到同一个 application/core helper，避免两套
   `CoordinationCall -> task.*` 逻辑。
6. 保持 task canonical protocol 不变：最终仍执行 `task.dispatch` / `task.message` / `task.complete`。

## 9. 测试计划

最小测试集：

1. Provider 配置校验：
   - `mcporter_mcp` 缺 `mcp_server` 或 `mcporter_command` 返回 400。
   - `native_mcp` 缺 `mcp_server` 返回 400。
   - `native_tool` 携带 `mcp_server` 或 `mcporter_command` 返回 400。
2. 解析规则：
   - Provider bot 继承 provider mode。
   - ProviderBotBinding 不存在 coordination override。
   - Plugin WS bot 解析为 `native_tool`。
   - 普通 WS bot 解析为 `legacy_upstream`。
3. 上下文注入：
   - 同一主从群内，mcporter provider、native MCP provider、native tool plugin、legacy WS bot 收到不同文案。
   - Manager 和 worker 角色模板不同。
4. Provider 回传：
   - `mcporter_mcp` 接受 `tool_result` 并执行现有 task flow。
   - `native_mcp` 接受 matching `coordination_intent`。
   - `native_tool` 接受无 MCP server 要求的 `coordination_intent`。
   - mode/kind mismatch 全部拒绝。
5. 安全回归：
   - `run_id + tool_call_id` 去重仍生效。
   - session guard 仍生效。
   - 非 manager 调 `bcs_assign_task` / `bcs_task_complete` 被拒绝。
   - 非 worker 调 `bcs_send_task_message` 被拒绝。

## 10. 非目标

- 不引入 bot 级 coordination profile。
- 不允许同一个 Provider 内不同 bot 使用不同 coordination mode。
- 不改变 `task.dispatch` / `task.message` / `task.complete` canonical protocol。
- 不强制现有 plugin 原生 tool 改成 MCP。
- 不在第一版实现按 bot 或按 session 动态切换 mode。
