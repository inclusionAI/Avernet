# Claude Code 对接基础功能参考手册

> 本文档覆盖 Claude Code 的 CLI、SDK、会话管理、权限控制、Hooks、MCP 等核心对接能力，作为团队内部综合参考。

---

## 目录

1. [概述](#1-概述)
2. [CLI 对接](#2-cli-对接)
3. [SDK 对接](#3-sdk-对接)
4. [会话管理](#4-会话管理)
5. [项目配置](#5-项目配置)
6. [权限与工具控制](#6-权限与工具控制)
7. [Hooks 事件系统](#7-hooks-事件系统)
8. [MCP 服务器集成](#8-mcp-服务器集成)
9. [本项目 Relay 网关对接参考](#9-本项目-relay-网关对接参考)

---

## 1. 概述

**Claude Code** 是 Anthropic 推出的 AI 编程助手，以 CLI 工具和 SDK 两种形态提供服务。它能够读写文件、执行命令、搜索代码，并通过多轮对话持续协作完成复杂的软件工程任务。

### 两种对接模式

| 模式 | 适用场景 | 特点 |
|------|----------|------|
| **CLI 模式** | 脚本集成、CI/CD、快速原型 | 通过 `claude` 命令行调用，支持 stream-json 流式输出 |
| **SDK 模式** | 应用内嵌、多轮对话、精细控制 | TypeScript / Python SDK，原生异步迭代器 |

### 核心模型

| 模型 ID | 说明 |
|---------|------|
| `claude-opus-4-6` | Opus 4.6，最强能力 |
| `claude-sonnet-4-6` | Sonnet 4.6，能力与速度平衡 |
| `claude-haiku-4-5` | Haiku 4.5，高速低成本 |

---

## 2. CLI 对接

### 2.1 核心命令

```bash
# 非交互式单次调用（最常用的编程对接方式）
claude -p "你的提示词"

# 指定工作目录
claude -p "分析代码结构" --cwd /path/to/project

# 指定输出格式
claude -p "分析代码" --output-format json
claude -p "长任务" --output-format stream-json

# 输出到文件
claude -p "分析代码" --output-format json --output-file result.json
```

### 2.2 完整参数表

#### 会话相关

| 参数 | 缩写 | 说明 |
|------|------|------|
| `--cwd <path>` | | 指定工作目录 |
| `--resume <id>` | `-r` | 恢复指定会话（支持 ID 或名称） |
| `--continue` | `-c` | 续接上次会话 |
| `--session-id <uuid>` | | 精确指定会话 UUID |
| `--name <name>` | `-n` | 设置会话名称 |

#### 输出控制

| 参数 | 说明 |
|------|------|
| `--print` / `-p` | 非交互模式，输出到 stdout |
| `--output-format <fmt>` | 输出格式：`text`（默认）/ `json` / `stream-json` |
| `--output-file <path>` | 输出写入文件 |
| `--verbose` | 输出详细信息 |
| `--include-partial-messages` | 流式模式下包含部分消息 |

#### 工具与权限

| 参数 | 说明 |
|------|------|
| `--allowed-tools <tools>` | 逗号分隔的允许工具列表 |
| `--deny-tools <tools>` | 逗号分隔的禁用工具列表 |

#### 系统提示词

| 参数 | 说明 |
|------|------|
| `--system-prompt <text>` | 覆盖系统提示词 |
| `--system-prompt-file <path>` | 从文件加载系统提示词 |
| `--append-system-prompt <text>` | 追加到默认系统提示词之后 |
| `--prepend-system-prompt <text>` | 插入到默认系统提示词之前 |

#### 其他

| 参数 | 说明 |
|------|------|
| `--api-key <key>` | 直接设置 API Key |
| `--model <model>` | 指定模型 |
| `--debug` | 开启调试日志 |

### 2.3 输出格式详解

#### text 格式（默认）

```bash
claude -p "什么是 TypeScript？"
# 输出纯文本回复
```

#### json 格式

```bash
claude -p "分析代码" --output-format json
```

返回结构：

```json
{
  "type": "result",
  "result": "分析结果文本...",
  "stop_reason": "end_turn",
  "usage": {
    "input_tokens": 120,
    "output_tokens": 45
  }
}
```

#### stream-json 格式（推荐用于实时对接）

```bash
claude -p "长任务" --output-format stream-json
```

输出为 NDJSON（每行一个 JSON 对象）：

```jsonl
{"type":"system","subtype":"init","cwd":"/project","session_id":"sess-123","tools":["Bash","Read","Write"]}
{"type":"stream_event","event":{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"你好"}}}
{"type":"stream_event","event":{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"，世界"}}}
{"type":"stream_event","event":{"type":"content_block_stop","index":0}}
{"type":"stream_event","event":{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":10}}}
{"type":"result","result":"你好，世界","stop_reason":"end_turn","usage":{"input_tokens":50,"output_tokens":10}}
```

### 2.4 stream-json 事件类型速查

| 事件类型 | 位置 | 说明 |
|---------|------|------|
| `system` | 顶层 `type` | 初始化信息（cwd、session_id、tools） |
| `content_block_start` | `event.type` | 内容块开始（text 或 tool_use） |
| `content_block_delta` | `event.type` | 增量数据（text_delta / thinking_delta / input_json_delta） |
| `content_block_stop` | `event.type` | 内容块结束 |
| `message_delta` | `event.type` | 消息级元数据（stop_reason、usage） |
| `result` | 顶层 `type` | 最终结果（完整文本、用量统计） |

### 2.5 流式解析示例

#### TypeScript

```typescript
import { spawn } from 'node:child_process';

function streamClaude(prompt: string, cwd: string) {
  const child = spawn('claude', [
    '-p', '--output-format', 'stream-json',
    '--include-partial-messages', '--verbose',
    prompt,
  ], { cwd, stdio: ['pipe', 'pipe', 'pipe'] });

  let buffer = '';

  child.stdout.on('data', (chunk: Buffer) => {
    buffer += chunk.toString();
    let idx = buffer.indexOf('\n');
    while (idx >= 0) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (line.startsWith('{') && line.endsWith('}')) {
        const evt = JSON.parse(line);

        if (evt.type === 'stream_event') {
          const inner = evt.event;
          // 文本增量
          if (inner.type === 'content_block_delta' && inner.delta?.type === 'text_delta') {
            process.stdout.write(inner.delta.text);
          }
          // 思考增量
          if (inner.type === 'content_block_delta' && inner.delta?.type === 'thinking_delta') {
            console.log('[thinking]', inner.delta.thinking);
          }
          // 工具调用开始
          if (inner.type === 'content_block_start' && inner.content_block?.type === 'tool_use') {
            console.log('[tool:start]', inner.content_block.name);
          }
        }

        if (evt.type === 'result') {
          console.log('\n[done]', evt.stop_reason, evt.usage);
        }
      }
      idx = buffer.indexOf('\n');
    }
  });

  return child;
}
```

#### Python

```python
import subprocess
import json

def stream_claude(prompt: str, cwd: str):
    proc = subprocess.Popen(
        ['claude', '-p', '--output-format', 'stream-json',
         '--include-partial-messages', '--verbose', prompt],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    for line in proc.stdout:
        line = line.strip()
        if not line or not line.startswith('{'):
            continue
        evt = json.loads(line)

        if evt.get('type') == 'stream_event':
            inner = evt['event']
            delta = inner.get('delta', {})
            # 文本增量
            if inner.get('type') == 'content_block_delta' and delta.get('type') == 'text_delta':
                print(delta['text'], end='', flush=True)
            # 工具调用
            if inner.get('type') == 'content_block_start':
                cb = inner.get('content_block', {})
                if cb.get('type') == 'tool_use':
                    print(f"\n[tool:start] {cb['name']}")

        if evt.get('type') == 'result':
            print(f"\n[done] {evt.get('stop_reason')} {evt.get('usage')}")

    proc.wait()
    return proc.returncode
```

---

## 3. SDK 对接

### 3.1 TypeScript SDK

**安装：**

```bash
npm install @anthropic-ai/claude-code
```

#### query — 单次查询

```typescript
import { query } from '@anthropic-ai/claude-code';

const messages = await query({
  prompt: '分析这个项目的代码结构',
  options: {
    cwd: '/path/to/project',
    allowedTools: ['Read', 'Grep', 'Glob'],
  },
});
```

#### stream — 流式查询

```typescript
import { stream } from '@anthropic-ai/claude-code';

const iter = stream({
  prompt: '重构这个函数',
  options: {
    cwd: '/path/to/project',
    includePartialMessages: true,
    allowedTools: ['Read', 'Edit', 'Bash'],
    sessionId: 'existing-session-uuid',  // 可选，恢复会话
  },
});

for await (const msg of iter) {
  if (msg.type === 'stream_event') {
    const delta = msg.event?.delta;
    if (delta?.type === 'text_delta') {
      process.stdout.write(delta.text);
    }
  }
  if (msg.type === 'result') {
    console.log('完成:', msg.stop_reason);
  }
}
```

#### Agent 类 — 多轮对话

```typescript
import { Agent } from '@anthropic-ai/claude-code';

const agent = new Agent({
  cwd: '/path/to/project',
  model: 'claude-sonnet-4-6',
  allowedTools: ['Read', 'Grep', 'Bash', 'Write'],
});

// 第一轮
const r1 = await agent.query({ prompt: '这个仓库是做什么的？' });
console.log(r1);

// 第二轮（自动保持上下文）
const r2 = await agent.query({ prompt: '跑一下测试' });
console.log(r2);

// 获取会话 ID，后续可恢复
const sessionId = agent.getSessionId();
```

### 3.2 Python SDK

**安装：**

```bash
pip install anthropic-claude-code
```

#### query

```python
from anthropic_claude_code import query

result = query(
    prompt='查找所有 TODO 注释',
    cwd='/path/to/project',
    allowed_tools=['Read', 'Grep'],
)
print(result)
```

#### stream

```python
from anthropic_claude_code import stream

for msg in stream(
    prompt='优化这个函数的性能',
    cwd='/path/to/project',
    include_partial_messages=True,
    allowed_tools=['Read', 'Edit', 'Bash'],
    session_id='existing-session-uuid',
):
    if hasattr(msg, 'content'):
        print(msg.content, end='', flush=True)
```

#### Agent 类

```python
from anthropic_claude_code import Agent

agent = Agent(
    cwd='/path/to/project',
    model='claude-sonnet-4-6',
    allowed_tools=['Read', 'Grep', 'Bash', 'Write'],
)

r1 = agent.query(prompt='分析代码结构')
r2 = agent.query(prompt='跑测试')  # 上下文延续
session_id = agent.get_session_id()
```

### 3.3 SDK 关键参数表

| 参数 | 类型 | 说明 |
|------|------|------|
| `prompt` | string | 用户输入提示词 |
| `cwd` | string | 工作目录 |
| `sessionId` | string | 恢复已有会话 |
| `model` | string | 模型 ID |
| `allowedTools` | string[] | 允许使用的工具列表 |
| `denyTools` | string[] | 禁止使用的工具列表 |
| `includePartialMessages` | boolean | 是否包含流式部分消息 |
| `systemPrompt` | string | 自定义系统提示词 |
| `appendSystemPrompt` | string | 追加系统提示词 |
| `environment` | Record | 环境变量 |
| `timeout` | number | 超时时间（毫秒） |
| `abortController` | AbortController | 中止控制器（TypeScript） |

---

## 4. 会话管理

### 4.1 会话存储

- **存储位置：** `~/.claude/projects/<project-hash>/<session-id>.jsonl`
- **Session ID 格式：** UUID（如 `550e8400-e29b-41d4-a716-446655440000`）
- **Project Hash：** 由工作目录路径派生
- **会话内容：** JSONL 格式，每行一条消息记录

### 4.2 创建新会话

```bash
# CLI — 每次 -p 调用默认创建新会话
claude -p "你好"

# CLI — 指定会话名称
claude -n "feature-auth"

# SDK
const result = await query({ prompt: '你好', options: { cwd: '/project' } });
```

### 4.3 恢复已有会话

```bash
# 恢复指定会话（ID 或名称）
claude --resume 550e8400-e29b-41d4-a716-446655440000
claude --resume feature-auth

# 续接上次会话
claude --continue
# 或缩写
claude -c
```

```typescript
// SDK — 通过 sessionId 恢复
const result = await query({
  prompt: '继续上次的工作',
  options: { sessionId: '<uuid>' },
});
```

### 4.4 会话生命周期

```
创建会话 → 发送消息 → 流式响应 → 工具调用 → 审批交互 → 响应完成
                ↑                                         |
                └─────────── 续接会话（resume） ──────────┘
```

---

## 5. 项目配置

### 5.1 工作目录（--cwd）

`--cwd` 决定了 Claude Code 的工作上下文：
- 文件读写的根目录
- `CLAUDE.md` 和 `.claude/` 配置的加载位置
- Git 操作的仓库位置

```bash
claude -p "修复 bug" --cwd /Users/dev/my-project
```

### 5.2 CLAUDE.md 项目指令文件

`CLAUDE.md` 是项目根目录下的指令文件，Claude Code 启动时自动加载，用于向 AI 传达项目规范。

```markdown
# 项目概述
这是一个 TypeScript WebSocket 网关项目。

## 技术栈
- TypeScript 5.x + Node.js 18+
- WebSocket (ws 库)
- ESM 模块

## 编码规范
1. 使用 ESLint + 严格 TypeScript 模式
2. 所有导出函数必须有 JSDoc 注释
3. 错误处理使用自定义 Error 类

## 常用命令
- `npm run dev` — 开发模式
- `npm test` — 运行测试
- `npm run build` — 构建
```

**生成初始 CLAUDE.md：**

```bash
claude /init
```

### 5.3 .claude/settings.json

项目级配置文件，控制权限、环境变量等：

```json
{
  "permissions": {
    "allow": [
      "Read",
      "Grep",
      "Glob",
      "Bash(npm run:*)",
      "Edit(src/**)"
    ],
    "deny": [
      "Bash(sudo *)",
      "Bash(rm -rf *)"
    ]
  },
  "env": {
    "NODE_ENV": "development"
  }
}
```

**配置层级（优先级从高到低）：**

| 层级 | 路径 | 作用域 |
|------|------|--------|
| 项目级 | `.claude/settings.json` | 当前项目 |
| 用户级 | `~/.claude/settings.json` | 当前用户所有项目 |
| 企业级 | `/etc/claude/settings.json` | 整个组织 |

---

## 6. 权限与工具控制

### 6.1 内置工具列表

| 工具名 | 功能 | 说明 |
|--------|------|------|
| `Read` | 读取文件 | 支持文本、图片、PDF、Jupyter Notebook |
| `Write` | 创建文件 | 写入新文件或完全覆盖 |
| `Edit` | 编辑文件 | 精确字符串替换，只发送 diff |
| `Glob` | 文件搜索 | 按模式匹配文件路径 |
| `Grep` | 内容搜索 | 基于 ripgrep 的正则搜索 |
| `Bash` | 执行命令 | 运行 shell 命令 |
| `WebFetch` | 网络请求 | 抓取网页内容 |
| `WebSearch` | 网络搜索 | 搜索引擎查询 |
| `Agent` | 子代理 | 启动子代理并行处理任务 |
| `LSP` | 代码智能 | 跳转定义、查找引用等 |
| `NotebookEdit` | Notebook 编辑 | 编辑 Jupyter Notebook 单元格 |

### 6.2 权限模式

通过 CLI 参数或 settings.json 设置：

| 模式 | 说明 |
|------|------|
| `auto`（默认） | 智能判断，高风险操作提示用户确认 |
| `strict` | 所有工具调用都需确认 |
| `plan` | 只做规划不执行 |
| `bypassPermissions` | 跳过所有权限检查（谨慎使用） |

### 6.3 工具粒度控制

```bash
# CLI — 只允许读取和搜索
claude -p "分析代码" --allowed-tools Read,Grep,Glob

# CLI — 禁止执行命令
claude -p "修改代码" --deny-tools Bash
```

```typescript
// SDK — 同等控制
await query({
  prompt: '分析代码',
  options: {
    allowedTools: ['Read', 'Grep', 'Glob'],
    denyTools: ['Bash'],
  },
});
```

### 6.4 settings.json 权限规则语法

```
工具名(路径/命令模式)
```

示例：

```json
{
  "permissions": {
    "allow": [
      "Read",                           // 允许读取所有文件
      "Edit(src/**)",                    // 只允许编辑 src 目录
      "Bash(npm run:*)",                 // 只允许 npm run 系列命令
      "Bash(npm install:*)",             // 允许 npm install
      "WebFetch(https://api.github.com/**)" // 只允许访问 GitHub API
    ],
    "deny": [
      "Read(/etc/passwd)",               // 禁止读取敏感文件
      "Bash(sudo *)",                    // 禁止 sudo
      "Bash(rm -rf *)"                   // 禁止危险删除
    ]
  }
}
```

---

## 7. Hooks 事件系统

Hooks 允许在 Claude Code 的关键生命周期节点执行自定义脚本，实现自动化和安全控制。

### 7.1 Hook 类型

| Hook | 触发时机 | 典型用途 |
|------|----------|---------|
| `SessionStart` | 会话开始 | 日志记录、环境初始化 |
| `UserPromptSubmit` | 用户提交提示词 | 输入预处理、过滤 |
| `PreToolUse` | 工具执行前 | 验证、拦截危险操作 |
| `PostToolUse` | 工具执行后 | 后处理、通知 |
| `PostToolUseFailure` | 工具执行失败 | 错误处理、告警 |
| `Stop` | 会话结束 | 清理、统计 |
| `Notification` | 重要事件 | 告警推送 |

### 7.2 配置方式

在 `.claude/settings.json` 中配置：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/validate_bash.sh",
            "timeout": 10
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/on_file_change.sh"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "~/.claude/hooks/session_end.sh"
          }
        ]
      }
    ]
  }
}
```

### 7.3 Hook 脚本示例

Hook 脚本通过 stdin 接收 JSON 格式的上下文数据：

```bash
#!/bin/bash
# ~/.claude/hooks/validate_bash.sh
# 拦截危险的 Bash 命令

HOOK_DATA=$(cat)
COMMAND=$(echo "$HOOK_DATA" | jq -r '.tool_input.command // empty')

# 拦截 rm -rf 和 sudo
if [[ "$COMMAND" =~ ^rm\s+-rf ]] || [[ "$COMMAND" =~ sudo ]]; then
  echo '{"decision":"block","reason":"危险命令已被拦截"}' >&2
  exit 1
fi

# 放行
exit 0
```

### 7.4 返回码

| 返回码 | 含义 |
|--------|------|
| `0` | 允许操作继续 |
| `1` | 阻止操作 |
| 其他 | 错误（操作继续，但记录警告） |

---

## 8. MCP 服务器集成

MCP（Model Context Protocol）允许 Claude Code 连接外部工具和服务。

### 8.1 添加 MCP 服务器

```bash
# 交互式添加
claude mcp add

# 手动配置
```

### 8.2 配置格式

在 `.claude/settings.json` 中：

```json
{
  "mcpServers": {
    "github": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {
        "GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_xxx"
      }
    },
    "local-api": {
      "type": "stdio",
      "command": "node",
      "args": ["./mcp-server.js"]
    },
    "remote-service": {
      "type": "sse",
      "url": "https://mcp-server.example.com/sse"
    }
  }
}
```

### 8.3 MCP 传输类型

| 类型 | 说明 |
|------|------|
| `stdio` | 本地进程，通过 stdin/stdout 通信 |
| `sse` | 远程 HTTP，通过 Server-Sent Events 通信 |

### 8.4 自定义 MCP 工具示例

```typescript
// mcp-server.ts — 一个简单的 MCP 工具服务器
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';

const server = new Server({ name: 'my-tools', version: '1.0.0' }, {
  capabilities: { tools: {} },
});

server.setRequestHandler('tools/list', async () => ({
  tools: [{
    name: 'get_weather',
    description: '获取指定城市的天气',
    inputSchema: {
      type: 'object',
      properties: {
        city: { type: 'string', description: '城市名称' },
      },
      required: ['city'],
    },
  }],
}));

server.setRequestHandler('tools/call', async (request) => {
  if (request.params.name === 'get_weather') {
    const city = request.params.arguments?.city;
    return {
      content: [{ type: 'text', text: `${city}：晴，25°C` }],
    };
  }
  throw new Error('未知工具');
});

const transport = new StdioServerTransport();
await server.connect(transport);
```

---

## 9. 本项目 Relay 网关对接参考

本项目 `claude-code-gateway` 是一个 WebSocket 网关，将 Claude Code 的能力以结构化协议暴露给外部客户端。

### 9.1 架构概览

```
外部客户端 ←→ [WebSocket 网关] ←→ Claude Code (CLI / SDK)
   │              │                      │
   │  OpenClaw    │   CLI: spawn          │
   │  v3 帧协议   │   SDK: query()        │
   │              │                      │
   ├─ chat 通道   ├─ 文本流转发           │
   ├─ agent 通道  ├─ 结构化事件转发       │
   └─ approval    └─ HITL 审批流          │
```

### 9.2 两种 Bridge 实现

| Bridge | 文件 | 选择方式 | 特点 |
|--------|------|----------|------|
| CLI Bridge | `src/claude-cli-bridge.ts` | `CLAUDE_BRIDGE=cli` | 直接 spawn `claude` 进程 |
| SDK Bridge | `src/claude-sdk-bridge.ts` | `CLAUDE_BRIDGE=sdk`（默认） | 使用 `@anthropic-ai/claude-agent-sdk` |

两种 Bridge 暴露相同的回调接口 `ClaudePromptHandlers`：

```typescript
type ClaudePromptHandlers = {
  onTextDelta?: (fullText: string, delta: string) => void;
  onThinkingDelta?: (fullText: string, delta: string) => void;
  onToolStart?: (tool: ToolUseInfo) => void;
  onToolEnd?: (tool: ToolUseInfo) => void;
  onCommandOutput?: (toolCallId: string, phase: 'delta' | 'end', output: string, meta?: {...}) => void;
  onLifecycle?: (phase: 'start' | 'end' | 'error', data?: Record<string, unknown>) => void;
  onUsage?: (usage: { inputTokens?: number; outputTokens?: number; ... }) => void;
};
```

### 9.3 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` / `WS_PORT` | 18900 | WebSocket 服务端口 |
| `CLAUDE_BRIDGE` | sdk | Bridge 选择（sdk / cli） |
| `CONTEXT_TURNS` | 8 | 注入的历史对话轮次 |
| `MAX_CONTEXT_CHARS` | 12000 | 上下文最大字符数 |
| `TICK_INTERVAL_MS` | 30000 | 心跳间隔 |

### 9.4 更多协议细节

完整的 WebSocket 帧协议、双通道事件架构、HITL 审批流程等详见：

→ [`docs/websocket-protocol.md`](./websocket-protocol.md)

---

## 附录：快速对照表

### CLI vs SDK 功能对照

| 功能 | CLI | TypeScript SDK | Python SDK |
|------|-----|----------------|------------|
| 单次查询 | `claude -p "prompt"` | `query({prompt})` | `query(prompt=)` |
| 流式输出 | `--output-format stream-json` | `stream({prompt})` | `stream(prompt=)` |
| 指定目录 | `--cwd /path` | `options.cwd` | `cwd=` |
| 恢复会话 | `--resume <id>` | `options.sessionId` | `session_id=` |
| 工具控制 | `--allowed-tools` | `options.allowedTools` | `allowed_tools=` |
| 系统提示词 | `--append-system-prompt` | `options.appendSystemPrompt` | `append_system_prompt=` |
| 中止运行 | `kill <pid>` | `abortController.abort()` | `agent.abort()` |

### stream-json 事件 → 网关事件映射

| CLI stream-json 事件 | 网关 agent 通道 stream | 网关 agent 通道 phase |
|---------------------|----------------------|---------------------|
| `system` | `lifecycle` | `start` |
| `content_block_delta (text_delta)` | — (走 chat 通道) | — |
| `content_block_delta (thinking_delta)` | `thinking` | — |
| `content_block_start (tool_use)` | `tool` | `start` |
| `content_block_stop (tool_use)` | `tool` + `command_output` | `result` / `end` |
| `message_delta` | `assistant` | — |
| `result` | `lifecycle` | `end` |
