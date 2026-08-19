# TeamClaw 系统架构（按需查阅，不默认加载）

> 本文件仅供排查技术问题时参考，正常答疑不需要阅读此文件。

---

## 系统架构总览

```
用户 (Web/Electron/钉钉/VSCode)
  │
  ▼
前端 open-claw (React + Zustand + Tailwind)
  │ WebSocket
  ▼
OpenClawEnterprise (Adapter)
  │ 协议转换 + 多引擎适配
  ▼
Moltis (Rust) 或 OpenClaw (Node.js) 引擎
  │ LLM 调用 + 工具执行
  ▼
MCP 服务 / 技能
```

---

## 1. 前端 - open-claw

**技术栈**: React + Zustand + Tailwind CSS + TypeScript + bigfish(tern部署)

**核心 Stores**:
| Store | 路径 | 功能 |
|-------|------|------|
| botStore | stores/botStore.ts | Bot列表、激活状态、轮询超时 |
| connectionStore | stores/connectionStore.ts | WebSocket连接、Token管理 |
| conversationStore | stores/conversationStore.ts | 会话列表、活动会话 |
| skillStore | stores/skillStore.ts | 技能状态 |
| mcpMarketStore | stores/mcpMarketStore.ts | MCP市场状态 |

**多端适配**:
| 平台 | 检测方式 | 特殊处理 |
|------|---------|---------|
| Web | URL自动判断 | preset切换环境 |
| Electron | `window.ELECTRON_ENV` | 请求走本地Agent |
| DingTalk | URL含`ddtab` | 跳过白名单检查 |
| VSCode | URL含`source=vscode` | postMessage通信 |

**架构分层**: Component层 → Hook层(业务逻辑) → Store层(Zustand) → API层(backend-api)

**关键文件**: config/routes.ts, hooks/useBot.ts, hooks/useConversations.ts, hooks/useMultiSessionChat.ts

---

## 2. 后端 - AgentClaw

**技术栈**: FastAPI + Uvicorn + SQLAlchemy + OceanBase(ZDAS)/SQLite + ARCA Sandbox

**核心 API**:
| 路由 | 功能 |
|-----|------|
| /api/bot | Bot管理增删改查 |
| /api/sessions | 会话管理 |
| /api/chat/stream | 聊天接口(SSE流式) |
| /api/skills | 技能管理 |
| /api/mcp | MCP配置管理 |
| /api/v1/devices | 设备管理 |
| /api/cron | 定时任务 |

**数据模型**: ac_skill_set, ac_skill, ac_bots, ac_resource, ac_entity_device_binding

**认证流程**: Cookie → BuserviceAuthService.get_login_user() → WhitelistService.is_allowed()

**关键文件**: src/agentclaw/main.py, servers/web/app.py, infrastructure/auth.py, services/device/repository.py

---

## 3. Adapter - OpenClawEnterprise

**核心定位**: 协议转换层 + 多引擎适配器

**目录结构**: src/api/(接口层) → src/engine/(引擎实现: moltis/, openclaw/, factory/) → src/intent_eval/(意图评测)

**引擎选择**: CHAT_ENGINE环境变量 → moltis 或 openclaw

---

## 4. 引擎 - Moltis

**技术栈**: Rust + Gateway Protocol v3 + moltis.toml配置

**关键文件**: ~/.moltis/moltis.toml, provider_keys.json

---

## 5. 引擎 - OpenClaw

**技术栈**: Node.js + Pi SDK

---

## 调用流程

**管理请求 (HTTP)**: 前端 → 后端API → 数据库/ARCA/技能扫描
**会话请求 (WebSocket)**: 前端 → Adapter(握手) → 引擎(LLM+工具) → 事件流返回

---

## 前端错误处理

| 状态码 | 含义 | 用户提示 |
|--------|------|---------|
| 401 | 未授权 | 登录已过期，请刷新页面 |
| 403 | 禁止访问 | 账号暂无权限，请联系管理员 |
| 404 | 资源不存在 | 服务暂时不可用，请稍后重试 |
| 429 | 请求频繁 | 请求过于频繁，请稍后重试 |
| 500+ | 服务器错误 | 服务器暂时异常，请稍后重试 |

**错误处理文件**: requestErrorHandler.ts, hooksErrorHandler.ts, useConnectionRefresh.ts

---

## 后端错误类型

```
ExpertChatError (基类)
├── BotNotFoundError
├── BotNotActiveError
├── SessionCreateError
└── ConnectionError

DeviceError (基类)
├── DeviceNotFoundError
├── DeviceLimitExceededError
└── ResourceInsufficientError
```

**重试策略**: Bot轮询(3秒间隔/5分钟超时), Token刷新(100秒间隔/10次失败停止)

---

## 关键配置

| 子系统 | 配置文件 |
|--------|---------|
| 前端 | open-claw/config/config.ts |
| 后端 | agentclaw/configs/application.yaml |
| 引擎 | ~/.moltis/moltis.toml |

**资源上传限制**: 最大500MB (config.py:366)，预览1MB (resources.py:940)

**性能配置**:
- ZDAS连接池: pool_size=20, timeout_secs=30
- ZCache Redis: host=127.0.0.1, port=16379, pool_size=10