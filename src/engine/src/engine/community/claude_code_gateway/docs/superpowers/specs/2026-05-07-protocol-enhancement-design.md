# WebSocket 协议增强设计

> **Spec Status:** DRAFT → 需要用户审批后才能进入实现阶段
> **目标：** 补齐当前 WebSocket 协议与 Claude Code TUI 交互能力的差距

## 背景与目标

本设计文档包含两大目标：

1. **补齐协议文档缺失**：将已实现但未写入 `websocket-protocol.md` 的方法文档化
2. **P2 功能扩展**：将原 P1 设计中未实现的 P2 功能纳入规划

---

## Part A0: 协议与 SDK 消息对照表

### A0.1 方法映射（Gateway Method → SDK 交互）

| 协议方法 | SDK 消息/事件 | 说明 |
|---|---|---|
| `skills.list` | 本地文件系统扫描 | 读取 `~/.claude/skills/` 目录 |
| `skills.get` | 本地文件系统扫描 | 读取单个 skill 的 SKILL.md |
| `skills.install` | 本地文件系统操作 | 创建 symlink 到 skills 目录 |
| `skills.uninstall` | 本地文件系统操作 | 删除 symlink |
| `skills.update` | 本地文件系统操作 | 更新 symlink 目标或 enabled 状态 |
| `skills.reload` | 本地文件系统扫描 | 重新扫描 skills 目录，计算差异 |
| `commands.list` | 本地文件系统扫描 | 读取 `~/.claude/commands/` + 项目命令 |
| `commands.get` | 本地文件系统扫描 | 读取单个 command 的 .md 文件 |
| `commands.reload` | 本本地文件系统扫描 | 重新扫描 commands 目录 |
| `mcp.config.list` | 本地配置文件读取 | 读取 `~/.claude/mcp.json` |
| `mcp.config.create` | 本地配置文件写入 | 写入 MCP 配置到 mcp.json |
| `mcp.config.update` | 本地配置文件写入 | 更新 MCP 配置 |
| `mcp.config.delete` | 本地配置文件写入 | 删除 MCP 配置 |
| `mcp.tools.list` | MCP Server 协议 | 调用 MCP server 的 tools/list |
| `mcp.tools.call` | MCP Server 协议 | 调用 MCP server 的 tools/call |
| `mcp.reload` | 本地配置文件读取 | 重新加载 MCP 配置 |

### A0.2 事件映射（SDK Event → 协议事件）

| SDK 消息类型 | 协议事件 | 说明 |
|---|---|---|
| `message_start` | `agent` stream: `message` | Assistant 消息开始 |
| `message_stop` | `agent` stream: `message` | Assistant 消息结束 |
| `message_delta` | `chat` | 文本流式输出 |
| `content_block_start` | `agent` stream: `content_block` | 内容块开始 |
| `content_block_delta` | `agent` stream: `thinking` / `tool` | 内容块增量 |
| `content_block_stop` | `agent` stream: `content_block` | 内容块结束 |
| `tool_use` (start) | `agent` stream: `tool` type: `start` | 工具调用开始 |
| `tool_use` (update) | `agent` stream: `tool` type: `update` | 工具调用增量 |
| `tool_use` (result) | `agent` stream: `tool` type: `result` | 工具调用结果 |
| `tool_progress` | `agent` stream: `tool` type: `progress` | 工具执行进度 |
| `tool_use_summary` | `agent` stream: `tool` type: `summary` | 工具使用摘要 |
| `task_started` | `agent` stream: `task` type: `task_started` | Subagent 任务开始 |
| `task_progress` | `agent` stream: `task` type: `task_progress` | Subagent 任务进度 |
| `task_notification` | `agent` stream: `task` type: `task_notification` | Subagent 任务通知 |
| `system:status` | `agent` stream: `system` type: `status_change` | 系统状态变化 |
| `system:api_retry` | `agent` stream: `system` type: `api_retry` | API 重试 |
| `system:compact_boundary` | `agent` stream: `system` type: `compact_boundary` | 上下文压缩边界 |
| `system:files_persisted` | `agent` stream: `system` type: `files_persisted` | 文件持久化 |
| `system:memory_recall` | `agent` stream: `memory` type: `recall` | 记忆召回 |
| `system:notification` | 顶层事件: `notification` | 全局通知 |
| `prompt_suggestion` | 顶层事件: `prompt.suggestions` | 后续问题建议 |
| `rate_limit_event` | `agent` stream: `system` type: `rate_limit` | 限额警告 |

### A0.3 P2 事件映射（待实现）

| SDK 消息类型 | 协议事件 | 说明 |
|---|---|---|
| `SDKHook*Message` | `agent` stream: `hook` | Hook 执行状态 |
| `SDKAuthStatusMessage` | 顶层事件: `auth.status` | 登录态变化 |
| `SDKPluginInstallMessage` | 顶层事件: `plugin.installed` | 插件安装 |
| `SDKSessionStateChangedMessage` | 顶层事件: `session.state_changed` | 会话状态变更 |
| `SDKElicitationCompleteMessage` | 顶层事件: `elicitation.complete` | 表单提交确认 |
| `SDKLocalCommandOutputMessage` | 顶层事件: `command.local_output` | 本地命令输出 |
| `SDKControlRequestUserDialogRequest` | 顶层事件: `dialog.request` | 自定义对话框 |
| `SDKControlGetContextUsageRequest` | 顶层事件: `context.usage` | 上下文用量 |

---

## Part A: 协议文档补齐

### A.1 Skills 方法（已实现，需文档化）

| 方法 | 说明 | 参数 | 返回 |
|---|---|---|---|
| `skills.list` | 列出所有 skills | — | `{ skills: Skill[] }` |
| `skills.get` | 获取单个 skill | `{ skillId: string }` | `{ skill: Skill }` |
| `skills.install` | 安装 skill (symlink) | `{ skillId, source, enabled? }` | `{ skill: Skill }` |
| `skills.uninstall` | 卸载 skill | `{ skillId: string }` | `{ removed: boolean }` |
| `skills.update` | 更新 skill | `{ skillId, enabled?, source? }` | `{ skill: Skill }` |
| `skills.reload` | 重载 skills 目录 | `{ scope?: 'all' \| 'user' \| 'project' }` | `{ added, removed, updated }` |
| `skills.reloadOne` | 重载指定 skill | `{ skillId: string }` | `{ skill: Skill }` |

### A.2 Commands 方法（已实现，需文档化）

| 方法 | 说明 | 参数 | 返回 |
|---|---|---|---|
| `commands.list` | 列出所有 commands | `{ cwd?: string }` | `{ commands: SlashCommand[] }` |
| `commands.get` | 获取单个 command | `{ id\|name: string, cwd?: string }` | `{ command: SlashCommand }` |
| `commands.reload` | 重载 commands 目录 | `{ scope?: 'all' \| 'user' \| 'project' \| 'plugin' }` | `{ added, removed }` |

### A.3 MCP 方法（已实现，需文档化）

| 方法 | 说明 | 参数 | 返回 |
|---|---|---|---|
| `mcp.config.list` | 列出 MCP 配置 | — | `{ servers: McpServer[] }` |
| `mcp.config.get` | 获取单个 MCP 配置 | `{ name: string }` | `{ server: McpServer }` |
| `mcp.config.create` | 创建 MCP 配置 | `{ name, config }` | `{ server: McpServer }` |
| `mcp.config.update` | 更新 MCP 配置 | `{ name, config }` | `{ server: McpServer }` |
| `mcp.config.delete` | 删除 MCP 配置 | `{ name: string }` | `{ removed: boolean }` |
| `mcp.tools.list` | 列出 MCP 工具 | `{ serverName?: string }` | `{ tools: McpTool[] }` |
| `mcp.tools.call` | 调用 MCP 工具 | `{ serverName, toolName, arguments }` | `{ result: unknown }` |
| `mcp.reload` | 重载 MCP 配置 | — | `{ added, removed }` |

---

## Part B: Plugin Reload 功能设计

### B.1 新增顶层事件

| 事件名 | 说明 | payload 结构 |
|---|---|---|
| `skills.reloaded` | skills 目录重载完成 | `{ added: string[], removed: string[], updated: string[] }` |
| `commands.reloaded` | commands 目录重载完成 | `{ added: string[], removed: string[] }` |
| `mcp.servers.reloaded` | MCP 服务重载完成 | `{ added: string[], removed: string[] }` |
| `plugin.reloaded` | 指定 plugin 重载完成 | `{ pluginId: string, reloaded: string[] }` |

### B.2 Slash 命令处理

| 命令 | 说明 |
|---|---|
| `/plugin-reload` | 重载 skills + commands + MCP |
| `/plugin-reload skills` | 仅重载 skills |
| `/plugin-reload commands` | 仅重载 commands |
| `/plugin-reload mcp` | 仅重载 MCP |
| `/plugin-reload <plugin-id>` | 重载指定 plugin 的 skills/commands |

### B.3 核心设计原则

1. **本地拦截优先**：slash 命令在网关层拦截执行，不发送给 Claude
2. **差异驱动 UI**：重载结果返回增/删/改列表，前端精准更新
3. **事件驱动通知**：使用顶层事件通知前端变更，支持选择性监听

---

## Part C: P2 SDK 事件扩展

> 本节记录原 P1 设计中未实现的 P2 功能，作为后续迭代的参考。

### C.1 Hook 事件

对应 SDK `SDKHook*Message` 系列。自定义 hook 执行状态。

```json
{
  "type": "event",
  "event": "agent",
  "payload": {
    "runId": "run-uuid",
    "sessionKey": "session:abc",
    "stream": "hook",
    "data": {
      "type": "hook_start",
      "hookId": "hook-uuid",
      "hookName": "before_tool_use",
      "status": "running"
    }
  }
}
```

### C.2 Auth 状态

对应 SDK `SDKAuthStatusMessage`。登录态变化。

```json
{
  "type": "event",
  "event": "auth.status",
  "payload": {
    "status": "authenticated",
    "userId": "user-uuid",
    "expiresAt": "2024-01-01T00:00:00.000Z"
  }
}
```

### C.3 Plugin 安装事件

对应 SDK `SDKPluginInstallMessage`。插件管理相关事件。

```json
{
  "type": "event",
  "event": "plugin.installed",
  "payload": {
    "pluginId": "my-plugin",
    "status": "success",
    "version": "1.0.0"
  }
}
```

### C.4 Session 状态变更

对应 SDK `SDKSessionStateChangedMessage`。会话级状态变化。

```json
{
  "type": "event",
  "event": "session.state_changed",
  "payload": {
    "sessionKey": "session:abc",
    "state": "paused",
    "reason": "user_interaction"
  }
}
```

### C.5 Elicitation 表单确认

对应 SDK `SDKElicitationCompleteMessage`。表单提交确认。

```json
{
  "type": "event",
  "event": "elicitation.complete",
  "payload": {
    "elicitationId": "elicitation-uuid",
    "values": { "field1": "value1" },
    "submittedAt": "2024-01-01T00:00:00.000Z"
  }
}
```

### C.6 Local Command Output

对应 SDK `SDKLocalCommandOutputMessage`。本地命令输出。

```json
{
  "type": "event",
  "event": "command.local_output",
  "payload": {
    "commandId": "cmd-uuid",
    "output": "...",
    "exitCode": 0
  }
}
```

### C.7 Dialog Request

对应 SDK `SDKControlRequestUserDialogRequest`。自定义对话框请求。

```json
{
  "type": "event",
  "event": "dialog.request",
  "payload": {
    "dialogId": "dialog-uuid",
    "type": "confirm",
    "title": "Confirm Action",
    "message": "Are you sure?"
  }
}
```

### C.8 Context Usage 查询

对应 SDK `SDKControlGetContextUsageRequest`。上下文窗口用量查询。

```json
{
  "type": "event",
  "event": "context.usage",
  "payload": {
    "usedTokens": 50000,
    "maxTokens": 100000,
    "percentage": 0.5
  }
}
```

---

## Part D: 功能优先级

### D.1 Phase 1: 协议文档补齐（立即可做）

| 优先级 | 功能 | 说明 |
|---|---|---|
| P1-1 | 文档化 skills.* 方法 | skills.list/get/install/uninstall/update |
| P1-2 | 文档化 commands.* 方法 | commands.list/get |
| P1-3 | 文档化 mcp.* 方法 | mcp.config.*, mcp.tools.* |

### D.2 Phase 2: SDK 事件扩展（P2）

| 优先级 | 功能 | 说明 |
|---|---|---|
| P2-1 | Hook 事件 | 自定义 hook 执行状态 |
| P2-2 | Auth 状态 | 登录态变化 |
| P2-3 | Plugin 安装事件 | 插件管理 |
| P2-4 | Session 状态变更 | 会话级状态 |
| P2-5 | Elicitation | 表单提交确认 |
| P2-6 | Local Command Output | 本地命令输出 |
| P2-7 | Dialog Request | 自定义对话框 |
| P2-8 | Context Usage | 上下文窗口用量 |

### D.3 Phase 3: Plugin Reload（设计中）

| 优先级 | 功能 | 说明 |
|---|---|---|
| P3-1 | skills/commands/mcp.reload 方法 | 重载 API |
| P3-2 | /plugin-reload slash 命令 | 命令行触发 |
| P3-3 | *_reloaded 事件 | 变更通知 |
| P3-5 | Elicitation | 表单提交确认 |
| P3-6 | Local Command Output | 本地命令输出 |
| P3-7 | Dialog Request | 自定义对话框 |
| P3-8 | Context Usage | 上下文窗口用量 |

---

## 文件变更清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `docs/websocket-protocol.md` | Modify | 补齐 skills/commands/mcp 方法；添加 reload 方法和事件 |
| `src/types.ts` | Modify | 新增 ReloadDiff、ReloadEvent 类型 |
| `src/skills/store.ts` | Modify | 新增 reload() 方法 |
| `src/commands/store.ts` | Modify | 新增 reload() 方法 |
| `src/mcp/store.ts` | Modify | 新增 reload() 方法 |
| `src/skills/handlers.ts` | Modify | 新增 skills.reload / skills.reloadOne |
| `src/commands/handlers.ts` | Modify | 新增 commands.reload |
| `src/mcp/handlers.ts` | Modify | 新增 mcp.reload |
| `src/gateway/handlers/chat.ts` | Modify | 拦截 /plugin-reload* 命令 |
| `src/gateway/connection-context.ts` | Modify | 新增 *_reloaded 事件发送方法 |
| `test/plugin-reload.test.ts` | Create | 端到端测试 |

---

## 协议文档更新要点

### D.1 方法表扩展（快速参考）

```markdown
### 方法一览（扩展）

| 方法 | 用途 | 关键参数 |
|---|---|---|
| ...existing methods... | ... | ... |
| `skills.list` | 列出所有 skills | — |
| `skills.get` | 获取单个 skill | `skillId` |
| `skills.install` | 安装 skill | `skillId`, `source`, `enabled?` |
| `skills.uninstall` | 卸载 skill | `skillId` |
| `skills.update` | 更新 skill | `skillId`, `enabled?`, `source?` |
| `skills.reload` | 重载 skills 目录 | `scope?` |
| `skills.reloadOne` | 重载指定 skill | `skillId` |
| `commands.list` | 列出所有 commands | `cwd?` |
| `commands.get` | 获取单个 command | `id` 或 `name` |
| `commands.reload` | 重载 commands 目录 | `scope?` |
| `mcp.config.list` | 列出 MCP 配置 | — |
| `mcp.config.get` | 获取 MCP 配置 | `name` |
| `mcp.config.create` | 创建 MCP 配置 | `name`, `config` |
| `mcp.config.update` | 更新 MCP 配置 | `name`, `config` |
| `mcp.config.delete` | 删除 MCP 配置 | `name` |
| `mcp.tools.list` | 列出 MCP 工具 | `serverName?` |
| `mcp.tools.call` | 调用 MCP 工具 | `serverName`, `toolName`, `arguments` |
| `mcp.reload` | 重载 MCP 配置 | — |
```

### D.2 事件表扩展

```markdown
### 事件一览（扩展）

| 事件 | 说明 | 定义章节 |
|---|---|---|
| ...existing events... | ... | ... |
| `skills.reloaded` | skills 目录重载完成 | [§新增] |
| `commands.reloaded` | commands 目录重载完成 | [§新增] |
| `mcp.servers.reloaded` | MCP 服务重载完成 | [§新增] |
| `plugin.reloaded` | 指定 plugin 重载完成 | [§新增] |
```

### D.3 agent stream 扩展（P2）

```markdown
### agent stream 子类型（扩展）

| stream | 说明 | 定义章节 |
|---|---|---|
| ...existing streams... | ... | ... |
| `hook` | Hook 执行状态 | [§新增] |
| `auth` | 认证状态 | [§新增] |
| `session` | 会话状态变更 | [§新增] |
| `elicitation` | 表单提交确认 | [§新增] |
| `dialog` | 自定义对话框 | [§新增] |
| `context` | 上下文用量 | [§新增] |
```