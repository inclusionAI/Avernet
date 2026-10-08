# claude-code-gateway

## 安装使用

```bash
npm i claude-code-gateway --save
```

## 功能特性

- **WebSocket 网关** — 通过 WebSocket 将 AI 聊天请求转发给 Claude CLI，实时流式返回响应。
- **双通道事件** — 每次运行发出 `chat`（文本流）和 `agent`（结构化事件）两个通道，客户端可独立渲染文本、思考过程、工具调用和审批。
- **人机交互（HITL）** — 当 Claude 调用 `AskUserQuestion` 或 shell/文件工具时，网关暂停并发出 `approval` 事件。客户端通过 `approval.resolve` 方法完成审批。
- **多供应商模型注册** — 声明多个 AI 供应商；Claude 已接入执行，其他供应商为未来扩展预留。
- **会话持久化** — 内存 `Map` 以 `.data/sessions.json` 为后端存储，首次使用时自动创建。
- **调试面板** — `public/` 目录下的静态 HTML 面板，用于交互式测试聊天、工具调用和审批流程。

## 快速开始

```bash
# 安装依赖
npm install

# 启动开发服务器（热重载）
npm run dev

# 服务监听 ws://127.0.0.1:18900
```

## 协议

所有通信使用自定义帧协议（v3）：

| 帧类型 | 方向 | 字段 |
|---|---|---|
| `req` | 客户端 -> 服务端 | `id`, `method`, `params` |
| `res` | 服务端 -> 客户端 | `id`, `ok`, `payload`, `error` |
| `event` | 服务端 -> 客户端 | `event`, `payload`, `seq` |

### 连接流程

1. 客户端连接 -> 服务端发送 `connect.challenge`
2. 客户端回复 `connect` 请求 -> 服务端返回能力列表和方法列表

### 网关方法

| 方法 | 说明 |
|---|---|
| `chat.send` | 发起 Claude 提示词；流式推送 `chat` 和 `agent` 事件 |
| `chat.abort` | 中止正在进行的运行 |
| `chat.history` | 获取会话消息历史 |
| `approval.resolve` | 解决待处理的 HITL 审批（支持 `chat.respond` 别名） |
| `session.new` | 创建新会话 |
| `sessions.list` | 列出所有会话 |
| `sessions.patch` | 更新会话元数据 |
| `sessions.delete` | 删除会话 |
| `sessions.reset` | 重置会话历史 |
| `health.claude` | 检查 Claude CLI 可用性 |
| `providers.available` | 列出可用供应商 |
| `providers.list` | 列出所有已声明的供应商 |
| `models.list` | 列出所有模型 |

### 事件通道

**聊天通道**（`event: 'chat'`）— 纯文本流式推送，包含 `textDelta` / `fullText`。

**Agent 通道**（`event: 'agent'`）— 结构化事件，通过 `stream` 字段区分：

| 流类型 | 阶段 | 说明 |
|---|---|---|
| `lifecycle` | `start`, `end`, `error` | 运行生命周期事件 |
| `tool` | `start`, `delta`, `end` | 工具调用生命周期（名称、输入、结果） |
| `thinking` | 持续增量 | Claude 的内部推理文本 |
| `command_output` | `delta`, `end` | Shell/文件工具输出（退出码、耗时） |
| `approval` | `requested`, `resolved` | HITL 审批请求与解决 |
| `assistant` | — | 使用量信息（token 数量、缓存统计） |

### HITL 审批流程

1. 服务端检测到 `AskUserQuestion` 或 shell/文件工具调用
2. 发出 `agent` 事件，`stream: 'approval'`，`phase: 'requested'`
3. 客户端展示 UI 并调用 `approval.resolve`，传入 `{ approvalId, decision, message }`
4. 服务端发出 `agent` 事件，`phase: 'resolved'`，并将人工响应追加到历史记录

## 调试日志

通过 `CLAUDE_CODE_GATEWAY_DEBUG` 环境变量启用（兼容旧变量 `TEAMCLAW_AICODING_RELAY_DEBUG` 与 `AIX_DEBUG`）：

```bash
# 开启所有命名空间的日志
CLAUDE_CODE_GATEWAY_DEBUG=1 npm run dev

# 仅开启指定命名空间（逗号分隔）
CLAUDE_CODE_GATEWAY_DEBUG=server,sdk npm run dev

# 也可使用 true 或 * 开启全部
CLAUDE_CODE_GATEWAY_DEBUG=* npm run dev
```

可用的命名空间：

| 命名空间 | 对应模块 |
|---|---|
| `server` | WebSocket 网关主服务 |
| `sdk` | Claude SDK 桥接层 |
| `cli` | Claude CLI 桥接层 |
| `router` | 多供应商模型路由 |
| `orchestrator` | 聊天编排器 |
| `cron` | 定时任务调度器 |

输出格式：`[claude-code-gateway:<namespace>] <message> <json-context?>`

> `warn` 和 `error` 级别始终输出，不受开关控制；`debug` 级别仅在对应命名空间启用时输出。

## 环境变量

| 变量 | 默认值 | 用途 |
|---|---|---|
| `PORT` / `WS_PORT` | 18900 | WebSocket 服务端口 |
| `CONTEXT_TURNS` | 8 | 注入上下文的历史轮次 |
| `MAX_CONTEXT_CHARS` | 12000 | 上下文提示词最大字符数 |
| `TICK_INTERVAL_MS` | 30000 | 心跳保活间隔 |
| `CLAUDE_BRIDGE` | sdk | 桥接选择器 — `sdk` 使用 `@anthropic-ai/claude-agent-sdk`（默认）；设为 `cli` 则启动 `claude` 二进制程序 |
| `CLAUDE_CODE_GATEWAY_DEBUG` | off | 基于命名空间的调试日志（兼容 `TEAMCLAW_AICODING_RELAY_DEBUG`、`AIX_DEBUG`） |

## 开发

```bash
npm run dev              # 启动开发服务器（tsx 热重载，默认 CLAUDE_BRIDGE=sdk）
CLAUDE_BRIDGE=cli npm run dev # 强制使用 CLI 桥接（需要 PATH 中有 `claude` 二进制程序）
npm run build            # 构建 dist/
npm run lint             # ESLint 检查（src + test）
npm run test             # 运行所有测试
npm run ci               # 完整 CI：lint + 覆盖率 + 类型检查
npm run prepublishOnly   # 通过 tshy 构建双格式 ESM/CJS
```

### Debugging

```bash
CLAUDE_CODE_GATEWAY_DEBUG=1   npm run dev
```