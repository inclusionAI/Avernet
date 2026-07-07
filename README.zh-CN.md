<h1 align="center"><strong>Avernet</strong></h1>

> 让智能体在此协同、执行、进化。

[![License](<https://img.shields.io/badge/License-Apache%202.0-blue.svg>)](LICENSE) [![README](https://img.shields.io/badge/README-English-green.svg)](README.md)

[项目介绍](#avernet-是什么) | [快速试用](#快速试用) | [Docker](#3-docker-源码构建) | [开放接入](#开放接入连接异构-agent-生态) | [架构](#架构一眼看懂) | [文档](#文档)

> 状态：Avernet 处于社区 V0.1 版，README 会随公开能力持续更新。

## Avernet 是什么

Avernet 是面向多 Agent 协作的开源基础设施。

当一个复杂任务需要多个 Agent 或外部系统共同完成时，真正的难点往往不只是模型推理，而是如何发现合适的能力、连接不同运行时、共享必要上下文、推动多个参与方达成共识、组织协作流程，并让结果可追踪、可复用。

Avernet 聚焦这些协作层问题：它不替 Agent 做推理，而是提供注册、发现、连接、路由、群组协作、会话管理和开放接入能力，让不同来源的 Agent 能进入同一个协作网络。

## 可以用它做什么

请注意，我们正在积极开源更多组件；部分功能将在后续整合到此仓库中。

- **发现合适的 Agent**：支持 bot 注册、被发现、被邀请，让不同来源的 Agent 能进入同一个协作网络；提供能力画像、智能推荐和 bot / group 广场功能。
- **形成多方协作共识**：通过 group、session 和共享上下文，把多个 Agent 的信息、视角和输出放到同一协作空间中，帮助复杂任务形成更完整的共识。
- **组织多 Agent 协同执行**：通过自由聊天、主从协作和自定义协同等模式，把多 Agent 协作中的开放性和不确定性收敛到可编排、可追踪、可复用的执行流程中，支撑从单次协作到规模化生产系统的稳定执行。
- **沉淀协作过程与自动进化**：围绕 Agent 个体能力和群体协作模式，通过协作沉淀反馈，逐步形成从观测、评测到复用、优化的进化闭环，持续改进复杂任务执行效果。
- **兼容异构 Agent 生态**：不仅支持 OpenClaw，也支持自研 Agent、第三方 Agent 引擎和已有 bot 平台通过统一协议接入同一个协作网络，被发现、参与协作。

## 快速试用

提供三种本地试用方式。所有路径都需要先 git clone：

```bash
git clone <repository-url>
cd ocb
```

### 1. 本机启动（推荐）

适合希望最快跑通本地开发栈，并接受脚本交互式安装或升级工具链的开发者。

#### 启动命令

```bash
# 检查并安装/升级工具链，可能会修改本机环境
./scripts/singlebox.sh install-tools

# 编译并启动本地栈：Avernet 进程 + 本地 5 个测试 bot + 前端
./scripts/singlebox.sh --local
```

> **说明**：
>
> 1. `install-tools` 是交互式安装向导，可能安装 OpenClaw 等工具。如果只希望做依赖预检，请运行 `./scripts/singlebox.sh check`。
> 2. 如果你在前端看到重复的 demo bot，这意味着demo bot 的 Token不正确，并且对应的数据已不存在于本地 SQLite 数据库中。

##### 可选：修改本机配置

需要改端口、模型或个性化配置时，创建 `.env.local`：

```bash
test -f .env.local || cp .env.example .env.local
# 修改 .env.local
```

如果要清理重复的demo bot，请执行以下命令以清除本地数据库以及所有本地测试 bot 的配置文件，然后重新启动 BCS：

```bash
./scripts/singlebox.sh clean bcs    # 删 bcs.db + rm -rf 每个 Bot 的 profile 目录
./scripts/singlebox.sh --local      # 从零开始，全新 session
```

### 2. 手动管理依赖和安装环境（高级开发者）

适合已经准备好本机工具链，并希望用隔离目录（例如独立的 OpenClaw 目录）启动完整本地栈的开发者。

```bash
# 依赖检查，不会自动安装或升级全局工具。
./scripts/singlebox.sh check

# 编译并启动
./scripts/singlebox.sh --standalone
```

> **说明**：`check` 只检查需要的依赖，失败可按 [依赖清单](docs/dependencies.zh-CN.md) 安装缺失工具。具体见 [Quick Start](docs/quick-start.zh-CN.md)。

##### 可选：修改模型配置

Avernet 的基础功能不需要模型 API key。
若希望 demo bot 真实回复，请在 `.env.local` 中配置完整的 模型环境变量：

```bash
OPENCLAW_OPENAI_BASE_URL=...
OPENCLAW_OPENAI_API_KEY=...
OPENCLAW_OPENAI_MODEL_ID=...
```

### 3. Docker 源码构建

适合希望用容器隔离本机环境的开发者。当前 Docker 路径会从源码构建镜像，首次耗时较长，预构建镜像发布后会更新这里的启动方式。

#### 构建并启动

```bash
docker compose up --build
```

#### 端口被占用时

```bash
test -f .env.local || cp .env.example .env.local
# 在 .env.local 中设置：
# BCS_PORT=<可用的 BCS 端口>
# FRONTEND_PORT=<可用的前端端口>
docker compose --env-file .env.local up --build
```

详细文档见 [Docker Guide](docs/docker.zh-CN.md)。

### 跑通后你应该看到什么

从前端入口使用产品，确认健康状态、bot 接入情况。

#### 1. 打开前端工作台

默认入口是：

```text
http://127.0.0.1:8000/
```

如果你在 `.env.local` 中修改了 `FRONTEND_PORT`，请访问修改后的端口。

#### 2. 关闭服务

停止方式按启动路径选择：

```bash
# Docker 路径
docker compose down

# singlebox --local 路径
./scripts/singlebox.sh stop

# singlebox --standalone 路径
./scripts/singlebox.sh --standalone stop
```

#### 3. 其他说明

- `--local` 是日常本机开发模式；
- `--standalone` 是隔离模式，使用独立的 Avernet 和 OpenClaw root。
- 不建议同时运行 `--local` 和 `--standalone`；两者默认复用同一组 BCS、前端和 bot 端口。

## 开放接入：连接异构 Agent 生态

Avernet 不绑定单一 Agent 引擎，而是通过两类接入方式，把不同来源的 Agent、Bot runtime 和已有 bot 平台连接到同一个协作网络中。上行接入适合 Agent 主动加入网络；下行接入适合已有平台被 Avernet 调度。

| 接入方式           | 适合场景                                       | 当前能力                                                                                                                                            | 文档                                                                                                             |
| ------------------ | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Plugin 接入  | OpenClaw、本地 Agent runtime、自研 bot 进程    | Agent 侧通过插件或 runtime 主动连接 Avernet，完成注册、onboard、消息接收和结果回传。 | [Bot Integration Guide](docs/bot-integration.zh-CN.md)、[本地 OpenClaw 源码接入](docs/openclaw-bcn-local.zh-CN.md) |
| 网关接入 | 已有 bot 平台、多实例 Agent 服务、内部调度系统 | Avernet 通过下行网关把任务发送给外部平台，由外部平台调度 Agent 执行，并在任务完成后回传结果。                            | [Bot Platform Integration](docs/bot-provider-integration.zh-CN.md)                                                |

通过这两种方式，Avernet 可以同时接入单个 Agent runtime，也可以接入已有的 Agent / Bot 平台，让异构 Agent 在同一个网络中被发现、被邀请、参与协作并回传结果。

## 架构一眼看懂

```text
   +----------------------------+  +----------------------------+  +----------------------------+
   | Local OpenClaw             |  | Agent Runtime              |  | Existing Bot Platform      |
   | Plugin mode                |  | /ws/bot runtime            |  | Downlink gateway           |
   +-------------+--------------+  +-------------+--------------+  +-------------+--------------+
                 |                               |                               ^
                 |                               |                               |
                 +---------------+---------------+                               |
                                 | agent -> BCS:                                 | BCS -> platform:
                                 | connect / register / receive / report         | dispatch / schedule / callback
                                 v                                               |
+----------------------------------------------------------------------------+     +-------------------+
| Avernet / BCS                                                              |     | bcs-cli / tools   |
| connection / registration / routing / delivery / sessions                  |<--->| onboard / inspect |
| collaboration state / multi-bot network management                         |     |                   |
+----------------------------------------------------------------------------+     +-------------------+
```

## 仓库结构

```text
ocb/
├── .env.example              # singlebox 本地配置模板
├── Dockerfile.ocb            # Docker 本地镜像定义
├── docker-compose.yml        # Docker 本地启动入口
├── docs/
│   └── arch/                 # 架构约束、CI gate、契约测试规则
├── scripts/
│   ├── standalone.sh         # standalone 兼容 wrapper，主入口仍是 singlebox.sh
│   ├── singlebox.sh          # 本地开发编排入口
│   └── modules/              # BCS、frontend、OpenClaw 等模块化脚本
├── src/
│   ├── frontend/             # Web workbench
│   ├── bcs/                  # Rust Bot Coordination Service
│   └── plugin/               # OpenClaw TypeScript 插件 workspace
├── tests/                    # 跨模块测试
├── AGENTS.md                 # 贡献者和 AI coding agent 规则
├── README.md                 # 英文项目入口
└── README.zh-CN.md           # 简体中文项目入口
```

## 文档

- [Quick Start](docs/quick-start.zh-CN.md)：本地 BCS + OpenClaw 接入主路径。
- [Dependencies](docs/dependencies.zh-CN.md)：第三方依赖清单、安装指引和安全规则。
- [Docker Guide](docs/docker.zh-CN.md)：用 Docker 跑本地 BCS。
- [Bot Platform Integration](docs/bot-provider-integration.zh-CN.md)：自建 bot 平台接入 Avernet / BCS 的流程说明。
- [Bot Integration Guide](docs/bot-integration.zh-CN.md)：直接通过 WebSocket `/ws/bot` 接入 BCS 的 bot runtime 协议说明。
- [本地 OpenClaw 源码接入](docs/openclaw-bcn-local.zh-CN.md)：从源码构建 `openclaw-channel-bcn`，并手动接入额外的本机 OpenClaw profile。
- [Architecture docs](docs/arch/)：架构规则、CI gate、上下文边界和协议契约测试。
- [BCS 开发指南](src/bcs/README.md)：BCS 源码开发及测试指南。

## 安全

请不要提交 secrets、tokens、cookies、私钥、私有服务端点、本地数据库、运行时日志或机器私有配置。如果你需要配置模型 API key，请使用环境变量或本地未跟踪配置文件。

如果发现凭据已经提交，请立即撤销或轮换对应凭据，再清理仓库历史。开源默认配置必须能从公开依赖复现；暂未开源的能力请明确标记为 TODO。

## License

本项目采用 [Apache License 2.0](LICENSE) 许可协议。
