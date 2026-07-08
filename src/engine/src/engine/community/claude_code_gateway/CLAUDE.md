# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本仓库中工作时提供指引。

## 项目简介

一个 WebSocket 网关（`claude-code-gateway`），通过 CLI 或 SDK 桥接将 AI 聊天请求转发给 Claude，并实时流式返回响应。支持人机交互审批（HITL）、多供应商模型路由、会话持久化和定时任务调度。

## 常用命令

```bash
npm install                   # 安装依赖
npm run dev                   # 启动开发服务器，支持热重载（tsx, ws://127.0.0.1:18900）
npm run lint -- --fix         # 代码检查 src + test
npm run test-local            # 运行测试（egg-bin test，内部使用 mocha）
npm run prepublishOnly        # 通过 tshy 构建双格式 ESM/CJS
npm run ci                    # 完整 CI 流程：lint + 覆盖率 + 类型检查（attw）
```

运行单个测试文件：
```bash
npx egg-bin test test/store.test.ts
```

## 架构

- **入口文件**：`src/server.ts`（WebSocket 网关）
- **库导出**：`src/index.ts` 统一导出所有公开 API
- **Claude 桥接层**：两个可互换的后端，通过 `CLAUDE_BRIDGE` 环境变量选择：
  - `claude-sdk-bridge.ts` — 使用 `@anthropic-ai/claude-agent-sdk`（默认）
  - `claude-cli-bridge.ts` — 启动 `claude` CLI 二进制程序
- **chat-orchestrator.ts** — 聊天编排器：从历史记录构建上下文，调用活跃的桥接层，发出结构化事件（`OrchestratorEvent`）
- **claude-code-router.ts** — 多供应商模型路由；加载供应商配置，解析模型特定的环境变量，创建路由运行工厂
- **server.ts** — WebSocket 服务器，处理自定义帧协议（req/res/event）。管理会话、审批流程、定时任务及所有网关方法
- **store.ts** — 会话持久化，基于 `.data/sessions.json`
- **cron/** — 定时任务调度子系统（存储、调度器、处理器）
- **types.ts** — 所有共享 TypeScript 类型定义（帧、事件、审批、会话）

## 关键约定

- 通信协议基于 `docs/websocket-protocol.md` 定义，实现时要符合协议（InteractionKind、decision、phase 等字段值必须与协议文档一致）。
- ESM 优先（`"type": "module"`），通过 `tshy` 双格式发布。即使是 `.ts` 源文件，导入时也使用 `.js` 扩展名。
- 继承 `eslint-config-egg/typescript`，放宽了 `ban-types`、`semi` 和 `indent` 规则。
- 测试使用 Node.js 内置 `assert`（严格模式）配合 egg-bin/mocha。测试文件位于 `test/`，与 `src/` 文件名对应。
- 调试日志通过 `src/debug.ts` 的 `createLogger(namespace)` 实现，由 `CLAUDE_CODE_GATEWAY_DEBUG` 环境变量控制。
- 修改之后的代码都要能通过 npm run prepublishOnly 构建，且通过 CI 流程（lint + 覆盖率 + 类型检查）。
- 修改后的代码要能通过 lint 检查 npm run lint

## 调试日志

通过 `CLAUDE_CODE_GATEWAY_DEBUG` 环境变量启用（兼容 `AIX_DEBUG`）：

```bash
# 开启所有命名空间的日志
CLAUDE_CODE_GATEWAY_DEBUG=1 npm run dev

# 仅开启指定命名空间（逗号分隔）
CLAUDE_CODE_GATEWAY_DEBUG=server,sdk npm run dev
```

可用的命名空间：`server`、`cli`、`sdk`、`router`、`orchestrator`、`cron`

输出格式：`[claude-code-gateway:<namespace>] <message> <json-context?>`

`warn` 和 `error` 级别不受开关控制，始终输出；`debug` 级别仅在对应命名空间启用时输出。

## 环境变量

| 变量 | 默认值 | 用途 |
|---|---|---|
| `CLAUDE_BRIDGE` | `sdk` | `sdk` 或 `cli` — 选择 Claude 后端 |
| `PORT` / `WS_PORT` | `18900` | WebSocket 服务端口 |
| `CONTEXT_TURNS` | `8` | 上下文历史轮次 |
| `MAX_CONTEXT_CHARS` | `12000` | 上下文提示词最大字符数 |
| `RELAY_MODELS_FILE` | - | 模型路由配置文件路径 |
| `RELAY_DEFAULT_MODEL` | 见用途 | 新建 session 默认模型（填到 `binding.model`，前端不显式指定时使用）。解析优先级：`RELAY_DEFAULT_MODEL` > `settings.json` 的 `env.ANTHROPIC_MODEL`（目录同 SDK 子进程：`RELAY_CLAUDE_CONFIG_DIR`/`CLAUDE_CONFIG_DIR`，否则 `<RELAY_CLAUDE_HOME\|HOME>/.claude`）> 硬编码兜底 `GLM-5.1` |
| `RELAY_INTERACTION_TIMEOUT_MS` | `300000`（5min） | HITL 审批等待超时（毫秒）。所有 GATED_TOOLS（AskUserQuestion/ExitPlanMode/Bash/Edit/Write/Read）共享；非法值回退默认 |
| `CLAUDE_CODE_GATEWAY_DEBUG` | - | 调试日志开关（兼容 `AIX_DEBUG`） |

## 帧协议

网关使用自定义 JSON 帧协议（OpenClaw-compatible v3）：

```typescript
// 请求帧
{ type: 'req', id: string, method: string, params?: object }

// 响应帧
{ type: 'res', id: string, ok: boolean, payload?: unknown, error?: { code, message, details? } }

// 事件帧
{ type: 'event', event: string, payload?: unknown, seq?: number }
```

支持的方法见 `src/gateway/handlers/` 目录，主要方法：
- `chat.send` / `chat.inject` / `chat.abort` — 聊天控制
- `interaction.resolve` — HITL 审批响应
- `session.list` / `session.get` / `session.delete` — 会话管理
- `cron.create` / `cron.list` / `cron.delete` — 定时任务
- `mcp.list` / `mcp.add` / `mcp.remove` — MCP 配置
- `skills.list` / `commands.list` — 技能与命令

## HITL 人机交互审批

工具执行需要用户审批时的挂起/恢复流程：

1. SDK 调用 `canUseTool` 挂起执行（`src/claude-sdk-bridge.ts`）
2. 网关发送 `interaction.requested` 事件给前端
3. 用户审批后，前端调用 `interaction.resolve` 方法
4. 网关调用 `resolveToolApproval` 恢复执行

需要审批的工具（`GATED_TOOLS`）：`AskUserQuestion`、`ExitPlanMode`、`Bash`、`Edit`、`Write`、`Read`

相关文件：`src/interaction/`（注册与解析）、`src/gateway/handlers/`

## 目录结构

```
src/
├── server.ts              # WebSocket 服务入口
├── index.ts               # 公开 API 导出
├── types.ts               # 共享类型定义（帧、事件、HITL）
├── chat-orchestrator.ts   # 聊天编排：构建上下文、调用桥接、发出事件
├── claude-sdk-bridge.ts   # SDK 桥接（@anthropic-ai/claude-agent-sdk）
├── claude-cli-bridge.ts   # CLI 桥接（claude 二进制）
├── claude-code-router.ts  # 多供应商模型路由
├── store.ts               # 会话持久化（.data/sessions.json）
├── debug.ts               # 调试日志工具
├── gateway/               # 网关帧处理
│   ├── frame-dispatcher.ts    # 帧路由分发
│   ├── connection-context.ts  # 连接上下文
│   ├── orchestrator-bridge.ts # 编排器事件转换
│   └── handlers/              # 方法处理器
│       ├── chat.ts        # 聊天方法
│       ├── sessions.ts    # 会话方法
│       └── meta.ts        # 元信息方法
├── interaction/           # HITL 交互
│   ├── registry.ts        # 待处理交互注册表
│   ├── builders.ts        # 事件构建器
│   ├── resolve.ts         # 交互解析
│   └── types.ts           # 交互类型
├── mcp/                   # MCP 配置管理
├── skills/                # 技能管理
├── commands/              # 斜杠命令
└── cron/                  # 定时任务调度
    ├── store.ts
    ├── scheduler.ts
    ├── handlers.ts
    └── types.ts
```

## 常见开发场景

### 添加新网关方法
1. 在 `src/types.ts` 定义请求/响应类型
2. 在 `src/gateway/handlers/` 创建处理器函数
3. 在 `src/gateway/frame-dispatcher.ts` 的 `METHOD_HANDLERS` 中注册

### 添加新事件类型
1. 在 `src/types.ts` 扩展 `AgentEventStream` 或定义新事件类型
2. 在桥接层（`claude-sdk-bridge.ts`）或编排器中发出事件
3. 在 `src/gateway/orchestrator-bridge.ts` 转换为网关帧

### 添加需要审批的工具
1. 在 `src/claude-sdk-bridge.ts` 的 `GATED_TOOLS` 集合中添加工具名
2. 工具调用时会自动触发 HITL 流程

### 调试特定模块
```bash
# 调试网关帧处理
CLAUDE_CODE_GATEWAY_DEBUG=server npm run dev

# 调试 SDK 桥接层
CLAUDE_CODE_GATEWAY_DEBUG=sdk npm run dev

# 调试模型路由
CLAUDE_CODE_GATEWAY_DEBUG=router npm run dev
```
