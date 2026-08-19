# ClawMind

通用 YAML DAG 工作流引擎，基于 TaskFlow 持久化编排。

## 概述

ClawMind 是一个基于 YAML 定义的有向无环图 (DAG) 工作流编排系统，构建于 OpenClaw 之上。它使用 TaskFlow 实现状态持久化，支持复杂的多节点工作流执行、人工审批、子工作流嵌套和条件分支。

产品文档: https://yuque.antfin.com/zeodup/vh3397/veerfl7vy7moazf9


### 核心特性

- **YAML 定义工作流** — 声明式 DAG，`dependsOn` + `triggerRule` 语义
- **多种节点执行器** — embedded-agent、subagent、cli-script、mcp-call、baas-call、bcs-route、bcs-approval-batch、human-wait、subworkflow、collaboration、action、done、loop-group
- **TaskFlow 持久化** — SQLite (开发) / ZDAS MySQL (生产) / API 模式 (解耦)
- **工作流包 (Packs)** — 通过 `workflow.pack.yaml` 清单分发业务工作流，支持 Facade 斜杠命令
- **多平台适配** — Phase 1: PlatformAdapter 抽象层，支持 OpenClaw / Claude Code / Hermes / TeClaw

## 架构

```
YAML 工作流定义
       │
       ▼
   Runner (纯逻辑 DAG 遍历)
       │
       ▼
  Controller (状态机 — 唯一写入 TaskFlow 的路径)
       │
       ▼
   TaskFlow (持久化层: SQLite / MySQL / API)
```

## 项目结构

| 路径 | 说明 |
|------|------|
| `src/controller.ts` | 核心状态机 — 唯一写入 TaskFlow 的路径 |
| `src/index.ts` | 插件入口，命令分发，执行器编排 |
| `src/runner.ts` | DAG 遍历逻辑 (纯函数，无副作用) |
| `src/types.ts` | 核心类型定义 (WorkflowSpec, FlowState, WorkflowNode 等) |
| `src/executors/` | 节点执行器实现 |
| `src/platform/` | 多平台适配层 (Phase 1) |
| `src/db/` | 数据库工厂 + 仓储实现 (sqlite/prod/api) |
| `src/validation/` | Zod 工作流校验 (schema + 语义 + 资源) |
| `src/facades/` | 斜杠命令 → 工作流映射 |
| `src/actions/` | Action 注册表 (Python 脚本, 模板解析) |
| `src/packs/` | Pack 发现与清单解析 |
| `packs/` | 生产工作流包 |
| `tests/` | 测试文件 |

## 开发

### 构建

```bash
npm install
npm run build           # tshy + 后置脚本 (bundle-runtime, copy-assets, generate-facade-skills)
npm run build:all       # 同时构建 clawweb
```

### 测试

```bash
npm test                # node --import tsx --test tests/*.test.ts

# 运行特定测试
node --import tsx --test tests/workflow-validation.test.ts
node --import tsx --test tests/controller-node-retry.test.ts
node --import tsx --test tests/platform/openclaw-adapter.test.ts   # 平台适配器测试
node --import tsx --test tests/platform/adapter-to-deps.test.ts    # 桥接函数测试
```

### 打包

```bash
npm run dist:pack                # 打包所有平台
npm run dist:pack:openclaw       # 仅 OpenClaw 插件包
npm run dist:pack:claudecode     # 仅 Claude Code 包
npm run dist:pack:hermes         # 仅 Hermes 包
npm run dist:pack:teclaw         # 仅 TeClaw 包
```

### 本地同步

将构建产物同步到本机各平台的安装目录，便于本地开发调试：

```bash
npm run sync                     # 同步所有平台 (openclaw + claudecode + hermes)
npm run sync:openclaw            # 仅同步 OpenClaw
npm run sync:claudecode          # 仅同步 Claude Code
npm run sync:hermes              # 仅同步 Hermes

# 先构建再同步 (一键完成)
npm run sync:all                 # npm run build + dist:pack + 同步所有平台

# 也可以直接带 --build 参数
bash scripts/local_sync/sync.sh --build
bash scripts/local_sync/sync.sh openclaw --build
```

**各平台同步目标：**

| 平台 | 同步目标 | 说明 |
|------|---------|------|
| OpenClaw | `~/.openclaw/extensions/clawmind/` | rsync dist/esm + packs/configs/skills/node_modules |
| Claude Code | `~/.claude/mcp.json` | 注册 clawmind MCP server 条目 |
| Hermes | `~/.hermes/extensions/clawmind/` | rsync dist/esm + packs/configs (如已安装) |

**配置详解：**

- **OpenClaw**: 同步后执行 `openclaw restart` 重启生效
- **Claude Code**: MCP 配置自动写入 `~/.claude/mcp.json`，重启 Claude Code 生效。
  手动配置格式：
  ```json
  {
    "mcpServers": {
      "clawmind": {
        "command": "node",
        "args": ["<项目路径>/dist/esm/index.js"],
        "env": {
          "DATABASE_MODE": "sqlite",
          "SQLITE_PATH": "~/.openclaw/workflow/engine.db"
        },
        "description": "ClawMind — YAML DAG workflow engine (MCP server)"
      }
    }
  }
  ```
- **Hermes**: MCP SSE 传输，支持审批 UI、多租户隔离、实时进度推送

输出到 `dist_pack/<platform>/`:
- `openclaw/` → `clawmind-<version>.tgz`
- `claudecode/` → `clawmind-claudecode-<version>.tgz`
- `hermes/` → `clawmind-hermes-<version>.tgz`
- `teclaw/` → `clawmind-teclaw-<version>.tgz`

## 多平台适配 (Phase 1)

### 架构

Phase 1 引入 `PlatformAdapter` 抽象层，将 ClawMind 核心引擎与平台特定 API 解耦：

```
索引层 (index.ts)
      │
      ▼
createOpenClawAdapter()  ──→  PlatformAdapter
      │                          │
      ▼                          ▼
buildControllerDeps()    ──→  ControllerDeps
      │
      ▼
  Controller
```

### 模块说明

| 文件 | 说明 |
|------|------|
| `src/platform/types.ts` | PlatformAdapter 接口 + 6 个子接口 |
| `src/platform/openclaw-adapter.ts` | OpenClaw 适配器工厂 |
| `src/platform/openclaw-types.ts` | OpenClaw PluginApi 类型 (共享) |
| `src/platform/mcp-adapter.ts` | MCP Server 适配器 (Claude Code / TeClaw) |
| `src/platform/mcp-entry.ts` | MCP Server 入口 (stdio transport) |
| `src/platform/mcp-tools.ts` | 共享 MCP 工具注册 |
| `src/platform/mcp-server-factory.ts` | MCP Server 共享初始化工厂 |
| `src/platform/mcp-sampling-agent.ts` | MCP sampling 替代 runEmbeddedPiAgent |
| `src/platform/hermes-adapter.ts` | Hermes 适配器 (SSE, 审批 UI, 多租户) |
| `src/platform/hermes-entry.ts` | Hermes SSE 入口 |
| `src/platform/database-taskflow.ts` | 非 OpenClaw TaskFlow 实现 (内存/API) |
| `src/platform/default-executor.ts` | 默认执行器分发 |
| `src/platform/adapter-to-deps.ts` | PlatformAdapter → ControllerDeps 桥接 |
| `src/platform/logger.ts` | 平台结构化日志 |
| `src/dispatch.ts` | 平台无关命令分发 |
| `src/platform/index.ts` | Barrel 导出 |

### 设计原则

- **PlatformAdapter 包装 ControllerDeps 的构造，而非替换它**
- `executeNode` 不在适配器中 — 它依赖每次调用的上下文 (actionRegistry, toolCtx, 工作流规范)
- `api` 字段作为 Phase 1 透传保留在 ControllerDeps 中 (Phase 2 移除)
- `chatInjectFn` 作为回调参数注入 (DingTalk 逻辑不重复)

### Hermes Integration (SSE Transport)

ClawMind supports Hermes via MCP SSE transport. Start the Hermes MCP server:

```bash
# Start Hermes MCP server on port 3100
node dist/esm/platform/hermes-entry.js --port 3100
```

Hermes-specific features:
- **SSE push**: Workflow progress events pushed to browser via SSE
- **Approval UI**: `workflow_confirm` routed to Hermes console
- **Multi-tenant**: Session isolation via `tenantId`/`teamId` namespacing

Configuration:
```typescript
import { createHermesServer } from "@alipay/clawmind/platform/hermes-entry";

const { server } = await createHermesServer({ port: 3100 });
```

### Multi-Tenant Support

`DatabaseTaskFlowAdapter` supports `tenantId` for session isolation:

```typescript
const adapter = new DatabaseTaskFlowAdapter({
  sessionKey: "session-1",
  tenantId: "tenant-A",  // Flows stored under "tenant-A:session-1"
  flowRunApiRepo,        // Optional: for API mode persistence
});
```

Flows created with different `tenantId` values are fully isolated — tenant A cannot access tenant B's flow state.

### TeClaw MCP stdio Integration

TeClaw connects to ClawMind as an MCP client — **zero Rust code changes needed**. See [`docs/openspec/teclaw-mcp-integration.md`](docs/openspec/teclaw-mcp-integration.md) for the full migration guide.

The deprecated `clawmind/` Rust module should be replaced by MCP auto-discovery:
```json
{
  "mcpServers": {
    "clawmind": {
      "command": "node",
      "args": ["dist/esm/platform/mcp-entry.js"],
      "env": { "DATABASE_MODE": "sqlite" }
    }
  }
}
```

### 平台清单文件

| 文件 | 平台 |
|------|------|
| `openclaw.plugin.json` | OpenClaw 插件清单 |
| `claudecode.plugin.json` | Claude Code 插件清单 |
| `hermes.plugin.json` | Hermes 插件清单 |
| `teclaw.plugin.json` | TeClaw 插件清单 |

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DATABASE_MODE` | 数据库模式: sqlite / prod / api | sqlite |
| `SQLITE_PATH` | SQLite 数据库路径 | `~/.openclaw/workflow/engine.db` |
| `ZDAS_HOST` | ZDAS/MySQL 主机 | 127.0.0.1 |
| `ZDAS_PORT` | ZDAS/MySQL 端口 | 11306 |
| `CCT_SOP_MCP_SERVER_MODE` | CCT SOP MCP 服务器模式 | local |

## 许可证

内部使用 — 蚂蚁集团