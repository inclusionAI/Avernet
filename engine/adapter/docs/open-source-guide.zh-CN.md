# 开源指南 — Engine Adapter

> 🌏 English: [open-source-guide.md](./open-source-guide.md)

这是引擎适配器**开源（community）版本**的上手指南，说明发布内容、如何安装运行，以及如何驱动内置的两个引擎——**claude_code**（开箱即可运行）与 **openclaw**（需自备网关，不随本导出发布）。

> 只想先跑起来看效果：直接看
> [快速开始 — claude_code](#5-快速开始--claude_code)。

---

## 1. 这是什么

引擎适配器是一个轻量的 **Python / FastAPI 服务**（默认端口 `20003`），对外暴露统一的 WebSocket + HTTP 接口，并将其翻译到具体的 AI 编码引擎。它采用**防腐层（ACL）**设计：适配器本身不含任何引擎内部实现——每个引擎只是一个连接到外部 *gateway（网关）* 进程的 **WebSocket 客户端**，真正与模型对话的是网关。

```
                 ┌──────────────────────────┐
  你的客户端      │   engine adapter (本项目) │        引擎网关
  (WS / HTTP) ──▶ │   FastAPI · :20003       │ ──WS──▶ (驱动模型)
                 │   /api/{engine}/ws       │
                 └──────────────────────────┘
                        │            │
             claude_code│            │openclaw
                        ▼            ▼
            ws://…:18900          ws://…:18789
      community/claude_code_gateway   (外部 OpenClaw
      (已内置 — 见下方快速开始)         网关 — 不随本项目发布)
```

本版本注册了两个引擎：

| 引擎 | 开源可用性 | 网关 | 对接 |
|---|---|---|---|
| **claude_code** | ✅ 开箱即可运行 | **已内置**于 `community/claude_code_gateway/` | Claude，经 `@anthropic-ai/claude-agent-sdk` 或 `claude` CLI |
| **openclaw** | ✅ 配外部网关即可运行 | 公共 `openclaw` npm 包 **≥ 2026.5.x**（需自行安装） | OpenClaw 自身的模型/agent 配置 |

---

## 2. 开源版本包含哪些内容

公开导出仅包含 `community/` 命名空间。内部/corp 代码（`corp/`）、内部默认值、凭据、内部构建脚本均被剥离（见 `.github-export-exclude`）。具体包含：

- `src/engine/src/engine/community/**` — 适配器、两个引擎，以及内置的 `claude_code_gateway/` Node 服务。
- `start.py`、`pyproject.toml`、`scripts/run.sh`、`docs/`、`.env.example`。

所有 Python 依赖来自公共 PyPI；网关的所有 npm 依赖来自公共 npm registry。**不包含任何凭据或内部端点**。

---

## 3. 环境要求

- **Python 3.12+**
- **Node.js 18.19+** 与 **npm**（仅 claude_code 网关需要）
- claude_code 需要你**自备 Anthropic 访问凭据**：一个 `ANTHROPIC_API_KEY`，或已登录的
  [`claude` CLI](https://docs.claude.com/en/docs/claude-code)。适配器与网关**不内置**任何凭据——这是运行 Claude Code 的固有要求。

---

## 4. 安装

```bash
cd src/engine

# 方式 A — pip（标准）
python -m venv .venv && source .venv/bin/activate
pip install .

# 方式 B — uv（更快，项目本身使用）
uv sync
```

安装后得到 `engine` 包。ASGI 应用为 `engine.community.api.app:app`，入口为 `start.py`。

---

## 5. 快速开始 — claude_code

claude_code 引擎是一个 WS 客户端，需要其网关先运行。共两个进程：**(A)** 网关、**(B)** 适配器。

### A. 构建并启动网关

网关已内置在本仓库中，就地构建即可：

```bash
cd src/engine/src/engine/community/claude_code_gateway

npm install                 # 仅公共 npm 依赖
npm run prepublishOnly      # 构建 (tshy) -> dist/

# 启动。默认使用 Anthropic SDK 通道，需要 API key：
ANTHROPIC_API_KEY=sk-ant-... PORT=18900 node dist/esm/server.js
# -> "claude-code-gateway gateway ws: ws://127.0.0.1:18900 (bridge=sdk)"
```

通道（bridge）模式：

- `CLAUDE_BRIDGE=sdk`（默认）— 使用 `@anthropic-ai/claude-agent-sdk`，需 `ANTHROPIC_API_KEY`。
- `CLAUDE_BRIDGE=cli` — 驱动本地已登录的 `claude` CLI，无需 API key 环境变量。

网关还提供一个简易调试面板（`public/`），并使用 `req`/`res`/`event` 帧协议，详见
`community/claude_code_gateway/README.md`。

### B. 启动适配器并指向 claude_code

```bash
cd src/engine

CHAT_ENGINE=claude_code \
ENGINE_PROFILE=community \
CLAUDE_CODE_RELAY_URL=ws://127.0.0.1:18900 \
python start.py --port 20003
```

`CHAT_ENGINE=claude_code` 很关键——见[关于默认引擎的说明](#关于默认引擎)。`CLAUDE_CODE_RELAY_URL`
默认就是 `ws://127.0.0.1:18900`，若未改网关端口可省略。

### C. 验证

```bash
curl -s localhost:20003/health      # -> 200
curl -s localhost:20003/readiness   # 就绪探针
open  http://localhost:20003/docs   # OpenAPI 文档
```

然后用 WebSocket 客户端连接 `ws://localhost:20003/api/claude_code/ws`，发送 `chat.send` 帧
（帧协议见网关 README），或直接用网关自带的调试面板。

---

## 6. openclaw 引擎

openclaw 引擎是连接 **[OpenClaw](https://www.npmjs.com/package/openclaw)** 网关的 WS 客户端。
与 claude_code 的网关不同，它**未内置**在本导出中——但它是一个公共 npm 包，可自行安装运行：

```bash
npm i -g openclaw            # 需 >= 2026.5.x —— 见下方版本说明
openclaw gateway run --dev --allow-unconfigured    # 监听 ws://127.0.0.1:18789
```

然后让适配器指向它：

```bash
CHAT_ENGINE=openclaw ENGINE_PROFILE=community \
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789 \
python start.py --port 20003
# -> "Connected to OpenClaw Gateway: protocol=4"
```

> **网关版本很关键（已实测）。** 内置客户端使用 OpenClaw connect 协议 **v4**，需要
> **openclaw ≥ 2026.5.x**。较旧的网关（如 2026.3.x，协议 v3）会拒绝握手并报
> `Handshake failed: protocol mismatch`。若需对接其它网关协议版本，可通过
> `OPENCLAW_GATEWAY_PROTOCOL_VERSION`（默认 `4`）覆盖。

> 与 claude_code 一样，网关代表你驱动模型，完整对话需要 OpenClaw 自身的模型/凭据配置。
> 引擎 ↔ 网关的握手本身已在 openclaw 2026.6.x 上实测通过。

> 内置的 `engine.json` 还含 openclaw/hermes 的 `supervisorctl` 启动命令与内部路径——
> 遗留的内部部署提示，community 的 WS 客户端路径不使用它们。

---

## 7. 配置参考

环境变量（**不会**从 `.env` 自动加载——请 `export` 或内联传入；`.env.example` 记录了同一组变量）：

| 变量 | 作用对象 | 默认值 | 用途 |
|---|---|---|---|
| `ENGINE_PROFILE` | 适配器 | `community` | 装配的 profile。开源用 `community`。 |
| `CHAT_ENGINE` | 适配器 | （取自 `engine.json`，即 `openclaw`） | 启动引擎。可运行路径请设为 `claude_code`。 |
| `CLAUDE_CODE_RELAY_URL` | claude_code | `ws://127.0.0.1:18900` | 引擎连接的网关地址（也接受 `AICODING_RELAY_URL`）。 |
| `ANTHROPIC_API_KEY` | 网关 | — | SDK 通道所需的 Anthropic key。 |
| `CLAUDE_BRIDGE` | 网关 | `sdk` | `sdk`（需 API key）或 `cli`（需已登录 `claude`）。 |
| `PORT` | 网关 | `18900` | 网关监听端口。 |
| `OPENCLAW_GATEWAY_URL` | openclaw | `ws://127.0.0.1:18789` | 外部 OpenClaw 网关地址。 |

### 关于默认引擎

内置的 `engine.json` 设置 `engine.default = "openclaw"`，引擎解析顺序为
`CHAT_ENGINE 环境变量 → engine.json → "openclaw"`。由于 openclaw 网关未包含，直接
`python start.py` 会落到一个无法运行的引擎上。**开箱即用请务必设置 `CHAT_ENGINE=claude_code`**
（这也是 `.env.example` 里已经设好它的原因）。

---

## 8. 接口一览

适配器暴露的接口（见 `community/api/app.py`）：

| 路径 | 类型 | 用途 |
|---|---|---|
| `/api/{engine}/ws` | WebSocket | 路径绑定的引擎通道（`/api/claude_code/ws`、`/api/openclaw/ws`）。 |
| `/ws` | WebSocket | 默认引擎通道。 |
| `/health`、`/readiness` | HTTP GET | 存活 / 就绪探针。 |
| `/docs`、`/redoc`、`/openapi.json` | HTTP | 接口文档。 |

引擎与网关之间的协议（方法 `chat.send`、`chat.abort`、`session.new`、`approval.resolve` 等）
详见 `community/claude_code_gateway/README.md`。

---

## 9. 排障

| 现象 | 原因 | 解决 |
|---|---|---|
| 引擎启动但对话无响应；`connect failed … 18789` | 当前引擎是默认的 openclaw，且无网关 | 设置 `CHAT_ENGINE=claude_code`。 |
| `connect failed … 18900` | claude_code 网关未运行 | 启动网关（§5A）。 |
| 网关立即退出 / 鉴权报错 | `CLAUDE_BRIDGE=sdk` 但没有 `ANTHROPIC_API_KEY` | 提供 key，或用 `CLAUDE_BRIDGE=cli` 配合已登录的 `claude`。 |
| `Cannot find module …/dist/esm/server.js` | 网关未构建 | 在 `claude_code_gateway/` 执行 `npm install && npm run prepublishOnly`。 |
| `/config` 返回 "only support 'openclaw'" | 该遗留调试端点仅支持 openclaw | claude_code 用不到，忽略即可。 |

---

## 10. 目录结构

```
src/engine/
├── start.py                     # 入口 → uvicorn engine.community.api.app:app
├── pyproject.toml               # 公共 PyPI 依赖
├── .env.example                 # 环境变量说明
└── src/engine/community/
    ├── api/                     # FastAPI 应用、WS 端点
    ├── core/ · plugin_api/ · kernel/   # ACL 各层（适配器、port、基础原语）
    ├── di/                      # 组装根（profile 装配）
    ├── engines/
    │   ├── claude_code/         # claude_code 组装根（WS 客户端 + ACL）
    │   └── openclaw/            # openclaw 组装根（WS 客户端 + ACL）
    ├── plugins/                 # 具体传输层 leaf
    └── claude_code_gateway/     # 为 claude_code 内置的 Node 网关
```

架构细节参见 `docs/community-corp-architecture.md` 与 `docs/heterogeneous-engine-architecture.md`。
