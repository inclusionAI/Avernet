# 异构引擎架构设计

## 概述

本文档定义 Engine 模块如何支持异构引擎（OpenClaw、Hermes Agent、Claude Code 等），确保所有引擎统一支持 Session、Chat、MCP、Skills、Approval、File、Node 等核心能力，并定义前后端协作规范。

---

## 目录

1. [设计目标](#1-设计目标)
2. [架构总览](#2-架构总览)
3. [核心抽象层](#3-核心抽象层)
4. [引擎能力矩阵](#4-引擎能力矩阵)
5. [插件系统设计](#5-插件系统设计)
6. [MCP 抽象层](#6-mcp-抽象层)
7. [Skills 抽象层](#7-skills-抽象层)
8. [Approval 抽象层](#8-approval-抽象层)
9. [File 抽象层](#9-file-抽象层)
10. [Node 抽象层](#10-node-抽象层)
11. [Channel 抽象层](#11-channel-抽象层)
12. [Health 抽象层](#12-health-抽象层)
13. [Effect 抽象层](#13-effect-抽象层)
14. [引擎生命周期](#14-引擎生命周期)
15. [水平扩容设计](#15-水平扩容设计)
16. [前端架构设计](#16-前端架构设计)
17. [端到端数据流](#17-端到端数据流)
18. [配置与注册](#18-配置与注册)
19. [实现指南](#19-实现指南)

---

## 1. 设计目标

### 1.1 核心目标

| 目标 | 说明 |
|------|------|
| **统一接口** | 所有引擎通过相同的 Plugin Protocol 提供能力 |
| **可插拔架构** | 新引擎可通过实现 Protocol 接入，无需修改核心代码 |
| **能力声明** | 引擎声明自己支持的能力，上层按需调用 |
| **优雅降级** | 不支持的能力有明确的 fallback 策略 |
| **前端适配** | 前端根据引擎能力动态调整 UI 和功能 |

### 1.2 支持的引擎类型

| 引擎 | 类型 | 特点 |
|------|------|------|
| **OpenClaw** | 内置引擎 | Node.js，WebSocket 通信，企业级功能完整 |
| **Hermes Agent** | 内置引擎 | Python，WebSocket 通信，AI Agent 能力 |
| **Claude Code** | 外部引擎 | CLI 工具，stdio 通信，代码生成能力强 |
| *(未来)* | 扩展引擎 | 通过插件机制接入 |

### 1.3 核心能力域

| 能力域 | 说明 | 涉及插件 |
|--------|------|----------|
| **Session** | 会话管理 | SessionPlugin |
| **Chat** | 对话交互 | ChatPlugin |
| **MCP** | 模型上下文协议 | MCPPlugin |
| **Skills** | 技能管理 | SkillsPlugin |
| **Cron** | 定时任务 | CronPlugin |
| **Approval** | 权限审批 | ApprovalPlugin |
| **File** | 文件操作 | FilePlugin |
| **Node** | 节点管理 | NodePlugin |
| **Channel** | 渠道配置 | ChannelPlugin |
| **Model** | 模型管理 | ModelPlugin |
| **Health** | 健康检查 | HealthPlugin |
| **Effect** | Agent效果追踪 | EffectPlugin |
| **BCN** | BCN | BCNPlugin |
| **权限** | 权限 | AuthPlugin |

---

## 2. 架构总览

### 2.1 后端分层架构

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           Web Layer (FastAPI)                                   │
│   /api/sessions  /api/chat  /api/mcp  /api/skills  /api/approvals              │
│   /api/files  /api/nodes  /api/channel  /api/models  /api/cron                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│                        Engine Manager                                            │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  Engine Registry (引擎注册表)                                             │   │
│   │    - openclaw: OpenClawEngine                                            │   │
│   │    - hermes: Hermes Agent Engine                                                │   │
│   │    - claude-code: ClaudeCodeEngine                                       │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                      Plugin Protocols (抽象层)                                   │
│   ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐     │
│   │ Session │ │  Chat   │ │   MCP   │ │ Skills  │ │ Approval│ │  File   │     │
│   │Protocol │ │Protocol │ │Protocol │ │Protocol │ │Protocol │ │Protocol │     │
│   └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘     │
│   ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐                                │
│   │  Node   │ │ Channel │ │  Model  │ │  Cron   │                                │
│   │Protocol │ │Protocol │ │Protocol │ │Protocol │                                │
│   └─────────┘ └─────────┘ └─────────┘ └─────────┘                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                      Engine Implementations                                      │
│   ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐                   │
│   │   OpenClaw      │ │     Hermes Agent      │ │   Claude Code   │                   │
│   │   Engine        │ │     Engine      │ │     Engine      │                   │
│   │                 │ │                 │ │                 │                   │
│   │ - SessionPlugin │ │ - SessionPlugin │ │ - SessionPlugin │                   │
│   │ - ChatPlugin    │ │ - ChatPlugin    │ │ - ChatPlugin    │                   │
│   │ - MCPPlugin     │ │ - MCPPlugin     │ │ - MCPPlugin     │                   │
│   │ - SkillsPlugin  │ │ - SkillsPlugin  │ │ - SkillsPlugin  │                   │
│   │ - ApprovalPlugin│ │ - ApprovalPlugin│ │ - ApprovalPlugin│                   │
│   │ - FilePlugin    │ │ - FilePlugin    │ │ - FilePlugin    │                   │
│   │ - NodePlugin    │ │ - NodePlugin    │ │ - NodePlugin    │                   │
│   │ - ChannelPlugin │ │ - ChannelPlugin │ │ - ChannelPlugin │                   │
│   └─────────────────┘ └─────────────────┘ └─────────────────┘                   │
├─────────────────────────────────────────────────────────────────────────────────┤
│                         Transport Layer                                          │
│   ┌───────────────┐ ┌───────────────┐ ┌───────────────┐                         │
│   │  WebSocket    │ │  WebSocket    │ │    stdio      │                         │
│   │  Gateway      │ │  Gateway      │ │   subprocess  │                         │
│   └───────────────┘ └───────────────┘ └───────────────┘                         │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 前端分层架构

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              UI Layer (React)                                   │
│   Pages: Assistant, Bootstrap, MCPMarket, SkillMarket, NodeManager, etc.       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                           Component Layer                                        │
│   ChatPage, EnginePanel, ChannelSettingsDrawer, MCPConfigPanel, SkillList, etc. │
├─────────────────────────────────────────────────────────────────────────────────┤
│                            Hook Layer                                            │
│   useEngine, useChannel, useMultiSessionChat, useMcpMarket, useSkills, etc.    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                            Store Layer (Zustand)                                │
│   connectionStore, channelStore, skillStore, mcpMarketStore, approvalStore, etc.│
├─────────────────────────────────────────────────────────────────────────────────┤
│                           API Layer (Controllers)                               │
│   EngineController, ChannelController, SessionController, McpController, etc.  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                          Transport Layer                                         │
│   ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                │
│   │  HTTP/Proxy     │  │  WebSocket      │  │  PostMessage    │                │
│   │  (REST APIs)    │  │  (Chat Stream)  │  │  (VSCode/Electron)│              │
│   └─────────────────┘  └─────────────────┘  └─────────────────┘                │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 2.3 核心组件目录结构

**后端 (src/engine/):**
```
src/engine/
├── core/                           # 核心抽象（不依赖具体引擎）
│   ├── engine/
│   │   ├── protocol.py             # Engine Protocol
│   │   ├── registry.py             # 引擎注册表
│   │   └── capability.py           # 能力声明与查询
│   ├── session/
│   │   ├── protocol.py             # SessionPlugin Protocol
│   │   └── models.py               # Session 数据模型
│   ├── chat/
│   │   ├── protocol.py             # ChatPlugin Protocol
│   │   └── models.py               # Chat 数据模型
│   ├── mcp/
│   │   ├── protocol.py             # MCPPlugin Protocol (新增)
│   │   └── models.py               # MCP 数据模型
│   ├── skills/
│   │   ├── protocol.py             # SkillsPlugin Protocol (新增)
│   │   └── models.py               # Skills 数据模型
│   ├── approval/
│   │   ├── protocol.py             # ApprovalPlugin Protocol (新增)
│   │   └── models.py               # Approval 数据模型
│   ├── file/
│   │   ├── protocol.py             # FilePlugin Protocol (新增)
│   │   └── models.py               # File 数据模型
│   ├── node/
│   │   ├── protocol.py             # NodePlugin Protocol (新增)
│   │   └── models.py               # Node 数据模型
│   ├── channel/
│   │   ├── protocol.py             # ChannelPlugin Protocol (新增)
│   │   └── models.py               # Channel 数据模型
│   └── cron/
│       ├── protocol.py             # CronPlugin Protocol
│       └── models.py               # Cron 数据模型
│
├── engines/                        # 引擎实现（新增目录）
│   ├── base.py                     # BaseEngine 抽象基类
│   ├── openclaw/                   # OpenClaw 引擎实现
│   │   ├── engine.py
│   │   ├── session.py
│   │   ├── chat.py
│   │   ├── mcp.py
│   │   ├── skills.py
│   │   ├── approval.py
│   │   ├── file.py
│   │   ├── node.py
│   │   └── channel.py
│   ├── hermes/                     # Hermes Agent 引擎实现 (原 Moltis)
│   │   └── ...
│   └── claude_code/                # Claude Code 引擎实现
│       └── ...
│
├── transport/                      # 引擎→上游 传输层抽象 (新增)
│   ├── protocol.py                 # Transport Protocol
│   ├── websocket.py                # WebSocket Transport
│   └── stdio.py                    # stdio Transport
│
├── manager.py                      # EngineManager
└── api/                            # API 层 (FastAPI 入口；与 backend/api 命名一致)
    ├── app.py                      # FastAPI app + 中间件 + 生命周期
    ├── caps.py                     # 能力守卫辅助 (check_capability)
    ├── mcp.py
    ├── skills.py
    ├── approvals.py
    ├── file.py
    ├── node.py
    ├── channel.py
    └── transport/                  # 入口传输层（前端 ↔ 适配器）
        └── ws_server.py            # 通用 WebSocket 服务（被 app.py 的 /ws 调用）
```

**命名说明：** 顶层 `transport/`（引擎→上游）与 `api/transport/`（前端→适配器入口）是两类传输：前者抽象引擎对接上游进程的方式（WS / stdio），后者是 FastAPI 应用对外暴露的 WebSocket 入口。同名不同层，不会冲突。

**前端 (src/frontend/src/):**
```
src/frontend/src/
├── services/
│   └── backend-api/               # API Controller 层
│       ├── EngineController.ts    # 引擎管理 API
│       ├── ChannelController.ts   # 渠道管理 API
│       ├── SessionController.ts   # 会话管理 API
│       ├── McpController.ts       # MCP 管理 API
│       ├── SkillController.ts     # Skills 管理 API
│       ├── FileController.ts      # 文件操作 API
│       └── NodeController.ts      # 节点管理 API
│
├── stores/                        # Zustand Store 层
│   ├── connectionStore.ts         # 连接状态（含引擎选择）
│   ├── channelStore.ts            # 渠道状态
│   ├── skillStore.ts              # Skill 状态
│   ├── mcpMarketStore.ts          # MCP 市场状态
│   ├── approvalStore.ts           # 审批状态
│   └── engineCapabilitiesStore.ts # 引擎能力缓存 (新增)
│
├── hooks/                         # Hook 层
│   ├── useEngine.ts               # 引擎管理 Hook
│   ├── useChannel.ts              # 渠道管理 Hook
│   ├── useEngineCapabilities.ts   # 能力查询 Hook (新增)
│   └── ...
│
├── components/
│   └── engine/                    # 引擎相关组件 (新增)
│       ├── EngineSelector.tsx     # 引擎选择器
│       ├── CapabilityBadge.tsx    # 能力标识
│       ├── CapabilityWarning.tsx  # 能力不足警告
│       └── EngineStatusPanel.tsx  # 引擎状态面板
│
└── types/
    ├── engine.ts                  # 引擎类型定义 (新增)
    └── capabilities.ts            # 能力类型定义 (新增)
```

---

## 3. 核心抽象层

### 3.1 Engine Protocol

统一的引擎接口，聚合所有能力插件：

```python
# core/engine/protocol.py

from typing import Protocol, runtime_checkable, Optional

@runtime_checkable
class Engine(Protocol):
    """统一引擎接口 - 所有引擎必须实现"""
    
    # ── 元信息 ──────────────────────────────────────────────
    @property
    def name(self) -> str:
        """引擎名称，如 'openclaw', 'hermes-agent', 'claude-code'"""
        ...
    
    @property
    def version(self) -> str:
        """引擎版本"""
        ...
    
    @property
    def capabilities(self) -> "EngineCapabilities":
        """引擎能力声明"""
        ...
    
    # ── 核心插件访问 ──────────────────────────────────────────────
    @property
    def session(self) -> "SessionPlugin":
        """Session 管理插件"""
        ...
    
    @property
    def chat(self) -> "ChatPlugin":
        """Chat 交互插件"""
        ...
    
    # ── 扩展插件访问（带可选性）──────────────────────────────────
    @property
    def mcp(self) -> Optional["MCPPlugin"]:
        """MCP 服务管理插件（可选）"""
        ...
    
    @property
    def skills(self) -> Optional["SkillsPlugin"]:
        """Skills 管理插件（可选）"""
        ...
    
    @property
    def approval(self) -> Optional["ApprovalPlugin"]:
        """审批管理插件（可选）"""
        ...
    
    @property
    def file(self) -> Optional["FilePlugin"]:
        """文件操作插件（可选）"""
        ...
    
    @property
    def node(self) -> Optional["NodePlugin"]:
        """节点管理插件（可选）"""
        ...
    
    @property
    def channel(self) -> Optional["ChannelPlugin"]:
        """渠道配置插件（可选）"""
        ...
    
    @property
    def cron(self) -> Optional["CronPlugin"]:
        """定时任务插件（可选）"""
        ...
    
    # ── 生命周期 ──────────────────────────────────────────────
    async def initialize(self) -> None:
        """初始化引擎（建立连接、加载配置等）"""
        ...
    
    async def shutdown(self) -> None:
        """关闭引擎（断开连接、清理资源等）"""
        ...
    
    async def health_check(self) -> "HealthStatus":
        """健康检查"""
        ...
```

### 3.2 EngineCapabilities

能力声明系统，用于查询引擎支持的功能：

```python
# core/engine/capability.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Set, Optional

class Capability(Enum):
    """引擎能力枚举"""
    
    # ── Session 能力 ──────────────────────────────────────
    SESSION_LIST = "session.list"
    SESSION_CREATE = "session.create"
    SESSION_DELETE = "session.delete"
    SESSION_UPDATE = "session.update"
    SESSION_HISTORY = "session.history"
    SESSION_ARCHIVE = "session.archive"
    
    # ── Chat 能力 ──────────────────────────────────────
    CHAT_STREAM = "chat.stream"
    CHAT_COMPLETE = "chat.complete"
    CHAT_ABORT = "chat.abort"
    CHAT_APPROVAL = "chat.approval"
    CHAT_HISTORY = "chat.history"
    
    # ── MCP 能力 ──────────────────────────────────────
    MCP_LIST = "mcp.list"
    MCP_CREATE = "mcp.create"
    MCP_UPDATE = "mcp.update"
    MCP_DELETE = "mcp.delete"
    MCP_START = "mcp.start"
    MCP_STOP = "mcp.stop"
    MCP_TOOLS_LIST = "mcp.tools.list"
    MCP_TOOLS_CALL = "mcp.tools.call"
    MCP_RESOURCES_LIST = "mcp.resources.list"
    MCP_RESOURCES_READ = "mcp.resources.read"
    MCP_PROMPTS_LIST = "mcp.prompts.list"
    MCP_PROMPTS_GET = "mcp.prompts.get"
    
    # ── Skills 能力 ──────────────────────────────────────
    SKILLS_LIST = "skills.list"
    SKILLS_INSTALL = "skills.install"
    SKILLS_UNINSTALL = "skills.uninstall"
    SKILLS_UPDATE = "skills.update"
    SKILLS_EXECUTE = "skills.execute"
    SKILLS_DISCOVER = "skills.discover"
    
    # ── Approval 能力 ──────────────────────────────────────
    APPROVAL_GET = "approval.get"
    APPROVAL_SET = "approval.set"
    APPROVAL_LIST = "approval.list"
    
    # ── File 能力 ──────────────────────────────────────
    FILE_READ = "file.read"
    FILE_WRITE = "file.write"
    FILE_UPLOAD = "file.upload"
    FILE_DELETE = "file.delete"
    FILE_LIST = "file.list"
    FILE_MKDIR = "file.mkdir"
    
    # ── Node 能力 ──────────────────────────────────────
    NODE_LIST = "node.list"
    NODE_REGISTER = "node.register"
    NODE_UNREGISTER = "node.unregister"
    NODE_STATUS = "node.status"
    
    # ── Channel 能力 ──────────────────────────────────────
    CHANNEL_CONFIG_GET = "channel.config.get"
    CHANNEL_CONFIG_SET = "channel.config.set"
    CHANNEL_STATUS = "channel.status"
    
    # ── Cron 能力 ──────────────────────────────────────
    CRON_LIST = "cron.list"
    CRON_CREATE = "cron.create"
    CRON_UPDATE = "cron.update"
    CRON_DELETE = "cron.delete"
    CRON_RUN = "cron.run"
    CRON_HISTORY = "cron.history"
    
    # ── Model 能力 ──────────────────────────────────────
    MODEL_LIST = "model.list"
    MODEL_SWITCH = "model.switch"


@dataclass
class EngineCapabilities:
    """引擎能力声明"""
    
    # 完全支持的 capabilities
    supported: Set[Capability] = field(default_factory=set)
    
    # 部分支持（有限制）的 capabilities
    limited: Dict[Capability, str] = field(default_factory=dict)
    # key: capability, value: 限制说明
    
    # 不支持但有 fallback 的 capabilities
    fallback: Dict[Capability, str] = field(default_factory=dict)
    # key: capability, value: fallback 说明
    
    def supports(self, cap: Capability) -> bool:
        """检查是否支持某能力（完全支持或有限支持）"""
        return cap in self.supported or cap in self.limited
    
    def is_limited(self, cap: Capability) -> bool:
        """检查某能力是否有限制"""
        return cap in self.limited
    
    def get_limitation(self, cap: Capability) -> Optional[str]:
        """获取能力限制说明"""
        return self.limited.get(cap)
    
    def has_fallback(self, cap: Capability) -> bool:
        """检查某能力是否有 fallback"""
        return cap in self.fallback
    
    def get_fallback(self, cap: Capability) -> Optional[str]:
        """获取 fallback 说明"""
        return self.fallback.get(cap)
    
    def to_dict(self) -> dict:
        """序列化为字典（供前端使用）"""
        return {
            "supported": [c.value for c in self.supported],
            "limited": {k.value: v for k, v in self.limited.items()},
            "fallback": {k.value: v for k, v in self.fallback.items()},
        }
```

### 3.3 BaseEngine 抽象基类

提供默认实现，简化新引擎开发：

```python
# engines/base.py

from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from engine.community.core.session.protocol import SessionPlugin
    from engine.community.core.chat.protocol import ChatPlugin
    from engine.community.core.mcp.protocol import MCPPlugin
    from engine.community.core.skills.protocol import SkillsPlugin
    from engine.community.core.approval.protocol import ApprovalPlugin
    from engine.community.core.file.protocol import FilePlugin
    from engine.community.core.node.protocol import NodePlugin
    from engine.community.core.channel.protocol import ChannelPlugin
    from engine.community.core.cron.protocol import CronPlugin

class BaseEngine(ABC):
    """引擎抽象基类 - 提供通用实现"""
    
    def __init__(self, config: dict):
        self._config = config
        self._session: Optional["SessionPlugin"] = None
        self._chat: Optional["ChatPlugin"] = None
        self._mcp: Optional["MCPPlugin"] = None
        self._skills: Optional["SkillsPlugin"] = None
        self._approval: Optional["ApprovalPlugin"] = None
        self._file: Optional["FilePlugin"] = None
        self._node: Optional["NodePlugin"] = None
        self._channel: Optional["ChannelPlugin"] = None
        self._cron: Optional["CronPlugin"] = None
    
    @property
    @abstractmethod
    def name(self) -> str: ...
    
    @property
    @abstractmethod
    def version(self) -> str: ...
    
    @property
    @abstractmethod
    def capabilities(self) -> EngineCapabilities: ...
    
    # ── 核心插件（必须实现）────────────────────────────────────
    
    @property
    def session(self) -> "SessionPlugin":
        if self._session is None:
            raise CapabilityNotSupportedError(self.name, Capability.SESSION_LIST)
        return self._session
    
    @property
    def chat(self) -> "ChatPlugin":
        if self._chat is None:
            raise CapabilityNotSupportedError(self.name, Capability.CHAT_STREAM)
        return self._chat
    
    # ── 扩展插件（可选实现）────────────────────────────────────
    
    @property
    def mcp(self) -> Optional["MCPPlugin"]:
        return self._mcp
    
    @property
    def skills(self) -> Optional["SkillsPlugin"]:
        return self._skills
    
    @property
    def approval(self) -> Optional["ApprovalPlugin"]:
        return self._approval
    
    @property
    def file(self) -> Optional["FilePlugin"]:
        return self._file
    
    @property
    def node(self) -> Optional["NodePlugin"]:
        return self._node
    
    @property
    def channel(self) -> Optional["ChannelPlugin"]:
        return self._channel
    
    @property
    def cron(self) -> Optional["CronPlugin"]:
        return self._cron
    
    # ── 生命周期 ──────────────────────────────────────────────
    
    async def initialize(self) -> None:
        """默认初始化 - 子类可覆盖"""
        pass
    
    async def shutdown(self) -> None:
        """默认关闭 - 子类可覆盖"""
        pass
    
    async def health_check(self) -> HealthStatus:
        """默认健康检查 - 子类可覆盖"""
        return HealthStatus(healthy=True, message="OK")
```

---

## 4. 引擎能力矩阵

### 4.1 完整能力对比表

| 能力域 | 能力 | OpenClaw | Hermes Agent | Claude Code | 说明 |
|--------|------|:--------:|:------:|:-----------:|------|
| **Session** |
| | session.list | ✅ | ✅ | ⚠️ 当前会话 | Claude Code 只能列出当前 |
| | session.create | ✅ | ✅ | ❌ | Claude Code 不支持显式创建 |
| | session.delete | ✅ | ✅ | ❌ | |
| | session.update | ✅ | ✅ | ❌ | |
| | session.history | ✅ | ✅ | ✅ | |
| **Chat** |
| | chat.stream | ✅ | ✅ | ✅ | 流式对话 |
| | chat.complete | ✅ | ✅ | ✅ | 非流式对话 |
| | chat.abort | ✅ | ✅ | ⚠️ kill | Claude Code 需要终止进程 |
| | chat.approval | ✅ | ✅ | ⚠️ 有限 | Claude Code 有内置 approval |
| **MCP** |
| | mcp.list | ✅ | ✅ | ✅ | |
| | mcp.create/update/delete | ✅ | ✅ | ⚠️ 配置 | Claude Code 通过配置文件 |
| | mcp.start/stop | ⚠️ mcporter | ⚠️ mcporter | ✅ 内置 | |
| | mcp.tools.list/call | ✅ | ✅ | ✅ | |
| | mcp.resources.* | ✅ | ✅ | ✅ | |
| | mcp.prompts.* | ✅ | ✅ | ⚠️ 有限 | |
| **Skills** |
| | skills.list | ✅ | ✅ | ✅ | |
| | skills.install/uninstall | ⚠️ 软链 | ⚠️ 软链 | ✅ 内置 | |
| | skills.execute | ❌ | ❌ | ✅ | Claude Code 可执行技能 |
| | skills.discover | ❌ | ❌ | ✅ | |
| **Approval** |
| | approval.get/set | ✅ | ✅ | ⚠️ 内置 | Claude Code 有自己的审批机制 |
| **File** |
| | file.read/write | ✅ | ✅ | ✅ | |
| | file.upload | ✅ | ✅ | ⚠️ 有限 | |
| | file.delete | ✅ | ✅ | ⚠️ 有限 | |
| | file.list | ✅ | ✅ | ✅ | |
| **Node** |
| | node.list | ✅ | ✅ | ❌ | Claude Code 单节点 |
| | node.register | ✅ | ✅ | ❌ | |
| **Channel** |
| | channel.config.get/set | ✅ | ✅ | ⚠️ CLI | Claude Code 通过 CLI 参数 |
| **Cron** |
| | cron.* | ✅ | ✅ | ❌ | Claude Code 不支持 |
| **Model** |
| | model.list | ✅ | ✅ | ✅ | |
| | model.switch | ✅ | ✅ | ⚠️ 重启 | |
| **Health** |
| | health.check | ✅ | ✅ | ✅ | 基础健康检查 |
| | health.components | ✅ | ✅ | ⚠️ 有限 | 组件级健康状态 |
| | health.metrics | ✅ | ✅ | ⚠️ 有限 | 性能指标采集 |
| **Effect** |
| | effect.track | ✅ | ✅ | ✅ | Agent效果追踪 |
| | effect.evaluate | ✅ | ✅ | ⚠️ 有限 | 效果评估 |
| | effect.feedback | ✅ | ✅ | ✅ | 用户反馈收集 |
| | effect.report | ✅ | ✅ | ✅ | 效果报告生成 |
| | model.switch | ✅ | ✅ | ⚠️ 重启 | Claude Code 切换模型需重启 |

**图例：**
- ✅ 完全支持
- ⚠️ 有限支持（需查看限制说明）
- ❌ 不支持

### 4.2 能力限制详解

```python
# OpenClaw 引擎能力
OPENCLAW_CAPABILITIES = EngineCapabilities(
    supported={
        # Session
        Capability.SESSION_LIST, Capability.SESSION_CREATE,
        Capability.SESSION_DELETE, Capability.SESSION_UPDATE, Capability.SESSION_HISTORY,
        # Chat
        Capability.CHAT_STREAM, Capability.CHAT_COMPLETE, Capability.CHAT_ABORT,
        Capability.CHAT_APPROVAL, Capability.CHAT_HISTORY,
        # MCP (完整)
        Capability.MCP_LIST, Capability.MCP_CREATE, Capability.MCP_UPDATE, Capability.MCP_DELETE,
        Capability.MCP_TOOLS_LIST, Capability.MCP_TOOLS_CALL,
        Capability.MCP_RESOURCES_LIST, Capability.MCP_RESOURCES_READ,
        Capability.MCP_PROMPTS_LIST, Capability.MCP_PROMPTS_GET,
        # Skills
        Capability.SKILLS_LIST,
        # Approval
        Capability.APPROVAL_GET, Capability.APPROVAL_SET,
        # File
        Capability.FILE_READ, Capability.FILE_WRITE, Capability.FILE_UPLOAD,
        Capability.FILE_DELETE, Capability.FILE_LIST,
        # Node
        Capability.NODE_LIST, Capability.NODE_REGISTER, Capability.NODE_STATUS,
        # Channel
        Capability.CHANNEL_CONFIG_GET, Capability.CHANNEL_CONFIG_SET, Capability.CHANNEL_STATUS,
        # Cron
        Capability.CRON_LIST, Capability.CRON_CREATE, Capability.CRON_UPDATE,
        Capability.CRON_DELETE, Capability.CRON_RUN, Capability.CRON_HISTORY,
        # Model
        Capability.MODEL_LIST, Capability.MODEL_SWITCH,
    },
    limited={
        Capability.MCP_START: "通过 mcporter 命令启动",
        Capability.MCP_STOP: "通过 mcporter 命令停止",
        Capability.SKILLS_INSTALL: "通过软链接方式安装",
        Capability.SKILLS_UNINSTALL: "通过软链接方式卸载",
    },
)

# Claude Code 引擎能力
CLAUDE_CODE_CAPABILITIES = EngineCapabilities(
    supported={
        # Session (有限)
        Capability.SESSION_HISTORY,
        # Chat
        Capability.CHAT_STREAM, Capability.CHAT_COMPLETE,
        # MCP (完整)
        Capability.MCP_LIST, Capability.MCP_TOOLS_LIST, Capability.MCP_TOOLS_CALL,
        Capability.MCP_RESOURCES_LIST, Capability.MCP_RESOURCES_READ,
        Capability.MCP_PROMPTS_LIST,
        # Skills (完整)
        Capability.SKILLS_LIST, Capability.SKILLS_INSTALL, 
        Capability.SKILLS_UNINSTALL, Capability.SKILLS_EXECUTE, Capability.SKILLS_DISCOVER,
        # File
        Capability.FILE_READ, Capability.FILE_WRITE, Capability.FILE_LIST,
        # Model
        Capability.MODEL_LIST,
    },
    limited={
        Capability.SESSION_LIST: "只能列出当前会话",
        Capability.CHAT_ABORT: "需要终止进程",
        Capability.CHAT_APPROVAL: "使用内置审批机制",
        Capability.MCP_CREATE: "通过配置文件 ~/.claude/mcp.json 管理",
        Capability.MCP_UPDATE: "通过配置文件管理",
        Capability.MCP_DELETE: "通过配置文件管理",
        Capability.MCP_START: "启动时自动加载所有 MCP",
        Capability.MCP_STOP: "需要重启进程",
        Capability.MODEL_SWITCH: "需要重启进程并指定 --model 参数",
        Capability.CHANNEL_CONFIG_GET: "通过 CLI 参数获取配置",
        Capability.CHANNEL_CONFIG_SET: "需要重启进程",
        Capability.FILE_UPLOAD: "不支持二进制上传，只能文本读写",
        Capability.FILE_DELETE: "权限受限",
    },
    fallback={
        Capability.APPROVAL_GET: "使用 Claude Code 内置 approval 机制",
        Capability.APPROVAL_SET: "使用 Claude Code 内置 approval 机制",
    },
)
```

---

## 5. 插件系统设计

### 5.1 插件 Protocol 概览

所有插件 Protocol 遵循统一设计模式：

```python
# 所有 Protocol 的通用模式
from typing import Protocol, runtime_checkable

@runtime_checkable
class XxxPlugin(Protocol):
    """插件接口说明"""
    
    # 1. 查询操作（返回列表或单个对象）
    async def list(...) -> list[Xxx]: ...
    async def get(...) -> Xxx | None: ...
    
    # 2. 创建操作
    async def create(...) -> Xxx: ...
    
    # 3. 更新操作
    async def update(...) -> Xxx: ...
    
    # 4. 删除操作
    async def delete(...) -> bool: ...
    
    # 5. 特殊操作（根据功能需求）
    async def execute(...) -> XxxResult: ...
```

---

## 6. MCP 抽象层

### 6.1 MCPPlugin Protocol

```python
# core/mcp/protocol.py

from typing import Protocol, runtime_checkable, Optional, List, AsyncIterator
from .models import (
    MCPServer, MCPServerConfig, MCPServerStatus,
    MCPTool, MCPToolCallRequest, MCPToolCallResult,
    MCPResource, MCPPrompt,
)

@runtime_checkable
class MCPPlugin(Protocol):
    """MCP (Model Context Protocol) 管理插件接口"""
    
    # ── Server 管理 ──────────────────────────────────────────────
    async def list_servers(self) -> List[MCPServer]:
        """列出所有 MCP Server"""
        ...
    
    async def get_server(self, server_code: str) -> Optional[MCPServer]:
        """获取指定 MCP Server"""
        ...
    
    async def create_server(self, config: MCPServerConfig) -> MCPServer:
        """创建 MCP Server 配置"""
        ...
    
    async def update_server(self, server_code: str, 
                           config: MCPServerConfig) -> MCPServer:
        """更新 MCP Server 配置"""
        ...
    
    async def delete_server(self, server_code: str) -> bool:
        """删除 MCP Server 配置"""
        ...
    
    # ── Server 生命周期 ──────────────────────────────────────────────
    async def start_server(self, server_code: str) -> bool:
        """启动 MCP Server"""
        ...
    
    async def stop_server(self, server_code: str) -> bool:
        """停止 MCP Server"""
        ...
    
    async def restart_server(self, server_code: str) -> bool:
        """重启 MCP Server"""
        ...
    
    async def get_server_status(self, server_code: str) -> MCPServerStatus:
        """获取 MCP Server 运行状态"""
        ...
    
    # ── Tools (工具) ──────────────────────────────────────────────
    async def list_tools(self, server_code: Optional[str] = None) -> List[MCPTool]:
        """列出可用工具（可指定 Server 或全部）"""
        ...
    
    async def call_tool(self, request: MCPToolCallRequest) -> MCPToolCallResult:
        """调用工具"""
        ...
    
    # ── Resources (资源) ──────────────────────────────────────────────
    async def list_resources(self, server_code: Optional[str] = None) -> List[MCPResource]:
        """列出可用资源"""
        ...
    
    async def read_resource(self, server_code: str, uri: str) -> str:
        """读取资源内容"""
        ...
    
    # ── Prompts (提示词模板) ──────────────────────────────────────────────
    async def list_prompts(self, server_code: Optional[str] = None) -> List[MCPPrompt]:
        """列出可用提示词模板"""
        ...
    
    async def get_prompt(self, server_code: str, name: str, 
                        arguments: Optional[dict] = None) -> str:
        """获取提示词模板"""
        ...
```

### 6.2 MCP Models

```python
# core/mcp/models.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, List, Dict

class TransportType(Enum):
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"

class MCPServerStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"

@dataclass
class MCPServerConfig:
    """MCP Server 配置"""
    server_code: str
    transport: TransportType = TransportType.SSE
    url: Optional[str] = None          # HTTP/SSE transport
    command: Optional[str] = None      # stdio transport
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 30
    enabled: bool = True

@dataclass
class MCPServer:
    """MCP Server 实例"""
    config: MCPServerConfig
    status: MCPServerStatus
    tools: List["MCPTool"] = field(default_factory=list)
    resources: List["MCPResource"] = field(default_factory=list)
    prompts: List["MCPPrompt"] = field(default_factory=list)

@dataclass
class MCPTool:
    """MCP 工具定义"""
    name: str
    description: str
    input_schema: Dict[str, Any]
    server_code: str

@dataclass
class MCPToolCallRequest:
    """MCP 工具调用请求"""
    tool_name: str
    arguments: Dict[str, Any]
    server_code: Optional[str] = None  # 不指定则自动查找

@dataclass
class MCPToolCallResult:
    """MCP 工具调用结果"""
    tool_name: str
    server_code: str
    content: List[Dict[str, Any]]  # MCP content format
    is_error: bool = False

@dataclass
class MCPResource:
    """MCP 资源定义"""
    uri: str
    name: str
    description: Optional[str]
    mime_type: Optional[str]
    server_code: str

@dataclass
class MCPPrompt:
    """MCP 提示词模板"""
    name: str
    description: str
    arguments: List[Dict[str, Any]]
    server_code: str
```

---

## 7. Skills 抽象层

### 7.1 SkillsPlugin Protocol

```python
# core/skills/protocol.py

from typing import Protocol, runtime_checkable, Optional, List
from .models import Skill, SkillConfig, SkillExecutionRequest, SkillExecutionResult

@runtime_checkable
class SkillsPlugin(Protocol):
    """Skills 管理插件接口"""
    
    # ── Skill 管理 ──────────────────────────────────────────────
    async def list_skills(self) -> List[Skill]:
        """列出所有可用技能"""
        ...
    
    async def get_skill(self, skill_id: str) -> Optional[Skill]:
        """获取指定技能"""
        ...
    
    async def install_skill(self, config: SkillConfig) -> Skill:
        """安装技能"""
        ...
    
    async def uninstall_skill(self, skill_id: str) -> bool:
        """卸载技能"""
        ...
    
    async def update_skill(self, skill_id: str, config: SkillConfig) -> Skill:
        """更新技能配置"""
        ...
    
    async def enable_skill(self, skill_id: str) -> bool:
        """启用技能"""
        ...
    
    async def disable_skill(self, skill_id: str) -> bool:
        """禁用技能"""
        ...
    
    # ── Skill 执行 ──────────────────────────────────────────────
    async def execute_skill(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        """执行技能"""
        ...
    
    async def validate_skill(self, skill_id: str) -> List[str]:
        """验证技能配置，返回错误列表"""
        ...
    
    # ── Skill 发现 ──────────────────────────────────────────────
    async def discover_skills(self, source: str) -> List[Skill]:
        """从指定源发现可安装的技能"""
        ...
```

### 7.2 Skills Models

```python
# core/skills/models.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, List, Dict

class SkillType(Enum):
    SYMLINK = "symlink"      # 软链接方式（当前实现）
    BUILTIN = "builtin"      # 内置技能
    PACKAGE = "package"      # 包管理器安装
    CUSTOM = "custom"        # 自定义实现

class SkillStatus(Enum):
    INSTALLED = "installed"
    AVAILABLE = "available"
    ERROR = "error"
    DISABLED = "disabled"
    INSTALLING = "installing"

@dataclass
class SkillConfig:
    """技能配置"""
    skill_id: str
    skill_type: SkillType = SkillType.SYMLINK
    source: Optional[str] = None       # 源路径或包名
    target: Optional[str] = None       # 目标路径
    enabled: bool = True
    parameters: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Skill:
    """技能实例"""
    skill_id: str
    name: str
    description: str
    config: SkillConfig
    status: SkillStatus
    version: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)

@dataclass
class SkillExecutionRequest:
    """技能执行请求"""
    skill_id: str
    action: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)

@dataclass
class SkillExecutionResult:
    """技能执行结果"""
    skill_id: str
    action: str
    success: bool
    output: Any
    error: Optional[str] = None
    duration_ms: int = 0
```

### 7.3 Skills Pool mapping wire contract

`SkillsService` 的 Pool activation、publish、verify 使用显式版本协商。当前
新契约版本为 `skills-pool-mapping-v2`：

```json
{
  "mapping_contract_version": "skills-pool-mapping-v2",
  "mappings": [
    {
      "corpus": "local",
      "relative_path": "writer",
      "link_name": "writer"
    },
    {
      "corpus": "repo",
      "relative_path": "business/reviewer",
      "link_name": "reviewer"
    }
  ],
  "retired_mappings": [
    {
      "corpus": "repo",
      "relative_path": "legacy/reviewer",
      "link_name": "reviewer"
    }
  ]
}
```

- `corpus` 只允许 `local` 或 `repo`；`relative_path` 必须是规范化的相对
  POSIX 路径；`link_name` 必须是单个规范化路径段。绝对路径、路径逃逸、
  重复 target、未知 corpus 和额外/混合字段全部 fail closed。
- Backend 不发送 engine-specific source/target。Engine 的
  `layout_planner.py` 使用当前 engine、layout state、repo delivery 与本地
  home 投影物理 source/active target；publish/verify 返回
  `resolved_mappings`，activation/rollback 返回 `local_locators`。这些路径是
  Engine evidence，Backend 只能校验和持久化，不能按 engine 重建。
- `retired_mappings` 是可选的精确旧 logical identity 集合，用于数据面 cutover
  与产品 activate/deactivate 并发时删除旧受管入口。Engine 必须先整批校验
  `mappings` 与 `retired_mappings`，且只删除 target 仍指向声明旧 source 的项；
  同名最新 mapping、外部 entry 和未登记文件系统 entry 不得被旧快照覆盖或删除。
  Backend 在 `POOL_ACTIVE` 提交前重读产品集合；变化时只重复 publish/verify，
  不重复物理 cutover。
- READY Probe 已进入 cutover evidence（存在 active marker）后，
  `checks.stable_repo_bridge_valid` 只在 AICoding/Hermes 已进入 `active`
  且稳定 repo bridge 已实际校验时存在并为 `true`。OpenClaw/Claude Code
  的 active layout 以及仍为 `finalizing` 的恢复窗口不适用或尚未完成该
  检查，必须省略此 key，不能用 `false` 或未经校验的 `true` 代替；
  preparation/Legacy evidence 仍按各 Engine 拓扑报告其必需 bridge 检查。
- 新 Backend 只有在 `/api/skills/layout/probe` 的 READY evidence 明确包含
  `"mapping_contract_version": "skills-pool-mapping-v2"` 后，才会在已通过
  rollout gate 且 migration claim 成功的 reconcile 中发送 v2。缺失或旧
  capability 归类为 `NOT_CAPABLE`，保持 Legacy；probe 本身不能触发
  claim/cutover。已处于 Pool 的 Bot 发起显式 rollback 时也会重新 probe
  当前 binding；若 runtime 已降级或缺少 capability，则 Backend 在首个 v2
  mapping 请求和文件系统 rollback 前停止，保留现状并返回可重试失败。
- 兼容窗口内，新 Engine 仍接受不带 `mapping_contract_version` 的 legacy
  physical item：`{"source": "...", "target": "..."}`。无版本 logical
  payload、带 v2 的 physical payload、logical/physical 混合 payload 和未知
  version 均在任何文件系统 mutation 前以
  `InvalidPoolMappingRequestError` 拒绝，HTTP delivery adapter 固定映射为
  `400`；Engine layout descriptor/invariant 异常不归入该输入错误。未知
  corpus 等不满足 HTTP schema 的结构错误由 FastAPI 在进入 Skills Service
  前以 `422` 拒绝，同样不能触发 plugin 调用或文件系统 mutation。
- 消费者包括 Backend `SkillsPoolRuntimeProtocol`/
  `SkillsPoolReconcileService`、Engine `/api/skills` router 与
  `SkillsService`，以及 OpenClaw、Claude Code、AICoding、Hermes 的
  filesystem composition roots。OpenClaw/Claude Code 内置 consumer
  直接广告 v2；AICoding/Hermes 由具体 composition root 在接入同一 resolver
  后显式广告。旧 composition root 不广告，混部期间保持
  `NOT_CAPABLE`/Legacy。四个 consumer 使用相同 contract tests；Teclaw
  保持 artifact delivery，不消费文件系统 mapping。
- v2 在混部期间不替换 legacy wire form；旧 Backend→新 Engine 可继续使用
  无版本 physical payload，新 Backend→旧 Engine 经 probe capability gate
  停留在 Legacy。移除 legacy form 需要独立版本与全量 runtime 升级证据，
  不属于当前迁移。

---

## 8. Approval 抽象层

### 8.1 ApprovalPlugin Protocol

```python
# core/approval/protocol.py

from typing import Protocol, runtime_checkable, Optional, List
from .models import ApprovalMode, ApprovalRequest, ApprovalResult

@runtime_checkable
class ApprovalPlugin(Protocol):
    """审批管理插件接口 - 控制 AI 执行操作前的确认行为"""
    
    # ── 审批模式管理 ──────────────────────────────────────────────
    async def get_mode(self, session_key: Optional[str] = None) -> ApprovalMode:
        """
        获取审批模式
        
        模式说明：
        - APPROVE: 每个操作都需要确认
        - ON_MISS: 不确定时询问
        - NEVER: 完全自动执行
        """
        ...
    
    async def set_mode(self, session_key: str, mode: ApprovalMode) -> bool:
        """设置审批模式"""
        ...
    
    # ── 审批操作 ──────────────────────────────────────────────
    async def list_pending(self, session_key: Optional[str] = None) -> List[ApprovalRequest]:
        """列出待审批请求"""
        ...
    
    async def approve(self, approval_id: str, comment: Optional[str] = None) -> ApprovalResult:
        """批准请求"""
        ...
    
    async def reject(self, approval_id: str, reason: Optional[str] = None) -> ApprovalResult:
        """拒绝请求"""
        ...
    
    async def get_approval(self, approval_id: str) -> Optional[ApprovalRequest]:
        """获取审批请求详情"""
        ...
```

### 8.2 Approval Models

```python
# core/approval/models.py

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Any, Optional, List, Dict

class ApprovalMode(Enum):
    """审批模式"""
    APPROVE = "approve"      # 每个操作都需要确认（谨慎模式）
    ON_MISS = "on-miss"      # 不确定时询问（半自动模式）
    NEVER = "never"          # 完全自动执行（自主模式）

class ApprovalStatus(Enum):
    """审批状态"""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

class ApprovalType(Enum):
    """审批类型"""
    FILE_WRITE = "file_write"       # 文件写入
    FILE_DELETE = "file_delete"     # 文件删除
    COMMAND_EXEC = "command_exec"   # 命令执行
    NETWORK = "network"             # 网络请求
    MCP_TOOL = "mcp_tool"           # MCP 工具调用
    CUSTOM = "custom"               # 自定义

@dataclass
class ApprovalRequest:
    """审批请求"""
    approval_id: str
    approval_type: ApprovalType
    session_key: str
    description: str                 # 操作描述
    details: Dict[str, Any]          # 详细信息
    created_at: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    timeout_seconds: int = 300
    auto_approve: bool = False

@dataclass
class ApprovalResult:
    """审批结果"""
    approval_id: str
    status: ApprovalStatus
    approved: bool
    comment: Optional[str] = None
    processed_at: Optional[datetime] = None
```

---

## 9. File 抽象层

### 9.1 FilePlugin Protocol

```python
# core/file/protocol.py

from typing import Protocol, runtime_checkable, Optional, List, AsyncIterator
from .models import FileInfo, FileUploadResult, FileListResult

@runtime_checkable
class FilePlugin(Protocol):
    """文件操作插件接口 - 提供工作区文件读写能力"""
    
    # ── 文件读取 ──────────────────────────────────────────────
    async def read(self, path: str, encoding: str = "utf-8") -> str:
        """读取文本文件内容"""
        ...
    
    async def read_bytes(self, path: str) -> bytes:
        """读取二进制文件内容"""
        ...
    
    async def read_stream(self, path: str, chunk_size: int = 65536) -> AsyncIterator[bytes]:
        """流式读取文件（用于大文件）"""
        ...
    
    # ── 文件写入 ──────────────────────────────────────────────
    async def write(self, path: str, content: str, encoding: str = "utf-8") -> bool:
        """写入文本文件"""
        ...
    
    async def write_bytes(self, path: str, content: bytes) -> bool:
        """写入二进制文件"""
        ...
    
    async def upload(self, path: str, file_data: bytes, filename: str) -> FileUploadResult:
        """上传文件到指定路径"""
        ...
    
    # ── 文件管理 ──────────────────────────────────────────────
    async def delete(self, path: str) -> bool:
        """删除文件或目录"""
        ...
    
    async def exists(self, path: str) -> bool:
        """检查文件是否存在"""
        ...
    
    async def list(self, dir_path: str, recursive: bool = False) -> FileListResult:
        """列出目录内容"""
        ...
    
    async def mkdir(self, path: str, parents: bool = True) -> bool:
        """创建目录"""
        ...
    
    async def move(self, src: str, dst: str) -> bool:
        """移动/重命名文件或目录"""
        ...
    
    async def copy(self, src: str, dst: str) -> bool:
        """复制文件或目录"""
        ...
    
    async def stat(self, path: str) -> FileInfo:
        """获取文件信息（大小、修改时间等）"""
        ...
    
    # ── 工作区管理 ──────────────────────────────────────────────
    async def get_workspace_root(self) -> str:
        """获取工作区根目录"""
        ...
    
    async def resolve_path(self, path: str) -> str:
        """解析路径（处理相对路径、符号链接等）"""
        ...
```

### 9.2 File Models

```python
# core/file/models.py

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

class FileType(Enum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    OTHER = "other"

@dataclass
class FileInfo:
    """文件信息"""
    path: str
    name: str
    file_type: FileType
    size: int                          # 字节数
    created_at: Optional[datetime]
    modified_at: Optional[datetime]
    is_readable: bool = True
    is_writable: bool = True
    mime_type: Optional[str] = None

@dataclass
class FileUploadResult:
    """文件上传结果"""
    path: str
    filename: str
    size: int
    overwritten: bool = False
    message: Optional[str] = None

@dataclass
class FileListResult:
    """文件列表结果"""
    dir_path: str
    recursive: bool
    files: List[FileInfo]
    total: int
```

---

## 10. Node 抽象层

### 10.1 NodePlugin Protocol

```python
# core/node/protocol.py

from typing import Protocol, runtime_checkable, Optional, List
from .models import Node, NodeConfig, NodeStatus

@runtime_checkable
class NodePlugin(Protocol):
    """节点管理插件接口 - 管理分布式引擎节点"""
    
    # ── 节点查询 ──────────────────────────────────────────────
    async def list(self, status: Optional[str] = None, 
                   platform: Optional[str] = None) -> List[Node]:
        """列出所有节点"""
        ...
    
    async def get(self, node_id: str) -> Optional[Node]:
        """获取指定节点"""
        ...
    
    async def get_status(self, node_id: str) -> NodeStatus:
        """获取节点状态"""
        ...
    
    # ── 节点注册 ──────────────────────────────────────────────
    async def register(self, config: NodeConfig) -> Node:
        """注册新节点"""
        ...
    
    async def unregister(self, node_id: str) -> bool:
        """注销节点"""
        ...
    
    async def heartbeat(self, node_id: str) -> bool:
        """节点心跳"""
        ...
    
    # ── 节点管理 ──────────────────────────────────────────────
    async def enable(self, node_id: str) -> bool:
        """启用节点"""
        ...
    
    async def disable(self, node_id: str) -> bool:
        """禁用节点"""
        ...
    
    async def reload(self, node_id: str) -> bool:
        """重载节点配置"""
        ...
```

### 10.2 Node Models

```python
# core/node/models.py

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List

class NodeStatus(Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    ERROR = "error"
    MAINTENANCE = "maintenance"

@dataclass
class Node:
    """节点信息"""
    node_id: str
    display_name: str
    platform: str                    # linux, macos, windows
    version: str
    status: NodeStatus
    capabilities: List[str] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)
    remote_ip: Optional[str] = None
    last_heartbeat: Optional[datetime] = None
    created_at: Optional[datetime] = None

@dataclass
class NodeConfig:
    """节点配置"""
    node_id: str
    display_name: str
    platform: str
    capabilities: List[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
```

---

## 11. Channel 抽象层

### 11.1 ChannelPlugin Protocol

```python
# core/channel/protocol.py

from typing import Protocol, runtime_checkable, Optional
from .models import ChannelConfig, ChannelStatus, ChannelVisibility

@runtime_checkable
class ChannelPlugin(Protocol):
    """渠道配置插件接口 - 管理渠道的配置和状态"""
    
    # ── 配置管理 ──────────────────────────────────────────────
    async def get_config(self) -> ChannelConfig:
        """获取渠道配置"""
        ...
    
    async def set_config(self, config: ChannelConfig) -> ChannelConfig:
        """更新渠道配置"""
        ...
    
    async def update_partial(self, **kwargs) -> ChannelConfig:
        """部分更新配置"""
        ...
    
    # ── 状态管理 ──────────────────────────────────────────────
    async def get_status(self) -> ChannelStatus:
        """获取渠道状态"""
        ...
    
    # ── 可见性管理 ──────────────────────────────────────────────
    async def get_visibility(self) -> ChannelVisibility:
        """获取可见性设置"""
        ...
    
    async def set_visibility(self, visibility: ChannelVisibility) -> bool:
        """设置可见性（公开/私有）"""
        ...
    
    # ── 角色管理 ──────────────────────────────────────────────
    async def get_role(self) -> str:
        """获取当前角色 (OWNER/CALLER)"""
        ...
    
    async def set_role(self, role: str) -> bool:
        """设置角色"""
        ...
```

### 11.2 Channel Models

```python
# core/channel/models.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List

class ChannelVisibility(Enum):
    PRIVATE = "PRIVATE"
    PUBLIC = "PUBLIC"

class ChannelRole(Enum):
    OWNER = "OWNER"       # 所有者（可修改配置）
    CALLER = "CALLER"     # 调用者（只能使用）

class ChannelStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    STARTING = "starting"
    STOPPING = "stopping"
    ERROR = "error"

@dataclass
class ChannelConfig:
    """渠道配置"""
    channel_id: str
    name: str
    description: Optional[str] = None
    model: Optional[str] = None
    visibility: ChannelVisibility = ChannelVisibility.PRIVATE
    role: ChannelRole = ChannelRole.OWNER
    max_tokens: int = 4096
    temperature: float = 0.7
    system_prompt: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
```

---

## 12. Health 抽象层

### 12.1 HealthPlugin Protocol

```python
# core/health/protocol.py

from typing import Protocol, runtime_checkable, Optional, List
from .models import HealthStatus, HealthCheckResult, ComponentHealth, HealthMetrics

@runtime_checkable
class HealthPlugin(Protocol):
    """健康检查插件接口 - 监控引擎和组件的健康状态"""
    
    # ── 基础健康检查 ──────────────────────────────────────────────
    async def check(self) -> HealthCheckResult:
        """
        执行健康检查
        
        Returns:
            HealthCheckResult: 包含整体状态和各组件状态
        """
        ...
    
    async def check_component(self, component: str) -> ComponentHealth:
        """
        检查单个组件健康状态
        
        Args:
            component: 组件名称 (session, chat, mcp, skills, etc.)
        
        Returns:
            ComponentHealth: 组件健康状态
        """
        ...
    
    # ── 组件管理 ──────────────────────────────────────────────
    async def list_components(self) -> List[str]:
        """列出所有可检查的组件"""
        ...
    
    async def get_component_status(self, component: str) -> ComponentHealth:
        """获取组件当前状态（不执行检查）"""
        ...
    
    # ── 指标采集 ──────────────────────────────────────────────
    async def collect_metrics(self) -> HealthMetrics:
        """
        采集性能指标
        
        Returns:
            HealthMetrics: 包含 CPU、内存、延迟、吞吐量等指标
        """
        ...
    
    async def get_latency(self, component: str) -> float:
        """获取组件响应延迟（毫秒）"""
        ...
    
    async def get_throughput(self, component: str) -> float:
        """获取组件吞吐量（请求/秒）"""
        ...
    
    # ── 健康配置 ──────────────────────────────────────────────
    async def set_threshold(self, component: str, metric: str, 
                           warning: float, critical: float) -> bool:
        """
        设置健康阈值
        
        Args:
            component: 组件名称
            metric: 指标名称 (latency, error_rate, etc.)
            warning: 警告阈值
            critical: 严重阈值
        """
        ...
    
    async def get_thresholds(self, component: str) -> dict:
        """获取组件健康阈值配置"""
        ...
```

### 12.2 Health Models

```python
# core/health/models.py

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

class HealthStatus(Enum):
    """健康状态"""
    HEALTHY = "healthy"           # 正常
    DEGRADED = "degraded"         # 降级（部分功能受限）
    UNHEALTHY = "unhealthy"       # 不健康（需要修复）
    UNKNOWN = "unknown"           # 未知（无法检查）

class ComponentType(Enum):
    """组件类型"""
    SESSION = "session"
    CHAT = "chat"
    MCP = "mcp"
    SKILLS = "skills"
    APPROVAL = "approval"
    FILE = "file"
    NODE = "node"
    CHANNEL = "channel"
    CRON = "cron"
    MODEL = "model"
    DATABASE = "database"
    NETWORK = "network"

@dataclass
class ComponentHealth:
    """组件健康状态"""
    component: str
    status: HealthStatus
    message: str = ""
    latency_ms: float = 0.0
    error_rate: float = 0.0
    last_check: datetime = field(default_factory=datetime.now)
    details: Dict[str, Any] = field(default_factory=dict)
    
    # 错误信息
    error_count: int = 0
    last_error: Optional[str] = None
    last_error_time: Optional[datetime] = None

@dataclass
class HealthCheckResult:
    """健康检查结果"""
    status: HealthStatus
    message: str
    engine: str
    version: str
    uptime_seconds: float
    checked_at: datetime = field(default_factory=datetime.now)
    
    # 各组件状态
    components: Dict[str, ComponentHealth] = field(default_factory=dict)
    
    # 总体指标
    total_requests: int = 0
    success_rate: float = 100.0
    avg_latency_ms: float = 0.0
    
    # 告警信息
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

@dataclass
class HealthMetrics:
    """健康指标"""
    # 系统指标
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    disk_percent: float = 0.0
    
    # 引擎指标
    active_sessions: int = 0
    active_connections: int = 0
    requests_per_second: float = 0.0
    avg_response_time_ms: float = 0.0
    
    # 错误指标
    error_count: int = 0
    error_rate: float = 0.0
    
    # 组件指标
    component_metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)
    
    # 时间戳
    collected_at: datetime = field(default_factory=datetime.now)

@dataclass
class HealthThreshold:
    """健康阈值配置"""
    component: str
    metric: str
    warning_threshold: float
    critical_threshold: float
    enabled: bool = True
```

### 12.3 各引擎健康检查实现

| 引擎 | 健康检查方式 | 组件支持 | 指标采集 |
|------|-------------|----------|----------|
| **OpenClaw** | WebSocket心跳 + HTTP健康端点 | 全部组件 | Prometheus格式 |
| **Hermes Agent** | WebSocket心跳 + 自定义协议 | 全部组件 | 自定义格式 |
| **Claude Code** | 进程存活检查 + CLI状态 | 部分组件 | 有限支持 |

### 12.4 健康检查 API

```python
# api/health.py

from fastapi import APIRouter, Response
from engine.community.manager import EngineManager

router = APIRouter(tags=["health"])

@router.get("/health")
async def health_check():
    """
    Kubernetes 探针兼容的健康检查端点
    
    Returns:
        200: 健康
        503: 不健康
    """
    manager = EngineManager.get_instance()
    result = await manager.health.check()
    
    if result.status == HealthStatus.HEALTHY:
        return {"status": "healthy", "message": result.message}
    elif result.status == HealthStatus.DEGRADED:
        return Response(
            content={"status": "degraded", "message": result.message},
            status_code=200  # 降级仍然返回 200
        )
    else:
        return Response(
            content={"status": "unhealthy", "message": result.message},
            status_code=503
        )

@router.get("/health/detail")
async def health_detail():
    """详细健康检查报告"""
    manager = EngineManager.get_instance()
    result = await manager.health.check()
    return result.to_dict()

@router.get("/health/components/{component}")
async def component_health(component: str):
    """单个组件健康检查"""
    manager = EngineManager.get_instance()
    health = await manager.health.check_component(component)
    return health.to_dict()

@router.get("/health/metrics")
async def health_metrics():
    """性能指标采集（Prometheus 格式）"""
    manager = EngineManager.get_instance()
    metrics = await manager.health.collect_metrics()
    
    # 返回 Prometheus 格式
    lines = [
        f"# HELP engine_cpu_percent CPU usage percent",
        f"# TYPE engine_cpu_percent gauge",
        f"engine_cpu_percent {metrics.cpu_percent}",
        f"# HELP engine_memory_percent Memory usage percent",
        f"# TYPE engine_memory_percent gauge",
        f"engine_memory_percent {metrics.memory_percent}",
        f"# HELP engine_active_sessions Number of active sessions",
        f"# TYPE engine_active_sessions gauge",
        f"engine_active_sessions {metrics.active_sessions}",
        f"# HELP engine_requests_per_second Requests per second",
        f"# TYPE engine_requests_per_second gauge",
        f"engine_requests_per_second {metrics.requests_per_second}",
        f"# HELP engine_avg_response_time_ms Average response time in ms",
        f"# TYPE engine_avg_response_time_ms gauge",
        f"engine_avg_response_time_ms {metrics.avg_response_time_ms}",
        f"# HELP engine_error_rate Error rate",
        f"# TYPE engine_error_rate gauge",
        f"engine_error_rate {metrics.error_rate}",
    ]
    return "\n".join(lines)
```

---

## 13. Effect 抽象层

### 13.1 EffectPlugin Protocol

```python
# core/effect/protocol.py

from typing import Protocol, runtime_checkable, Optional, List
from .models import (
    EffectEvent, EffectSession, EffectEvaluation, 
    UserFeedback, EffectReport, EffectMetrics
)

@runtime_checkable
class EffectPlugin(Protocol):
    """Agent 效果追踪插件接口 - 追踪、评估和反馈 Agent 执行效果"""
    
    # ── 效果追踪 ──────────────────────────────────────────────
    async def track_event(self, event: EffectEvent) -> str:
        """
        追踪效果事件
        
        Args:
            event: 效果事件（任务执行、工具调用、响应生成等）
        
        Returns:
            event_id: 事件唯一标识
        """
        ...
    
    async def track_session_start(self, session: EffectSession) -> str:
        """
        开始效果追踪会话
        
        Args:
            session: 效果会话信息
        
        Returns:
            session_id: 会话唯一标识
        """
        ...
    
    async def track_session_end(self, session_id: str, 
                                result: str, metrics: EffectMetrics) -> bool:
        """
        结束效果追踪会话
        
        Args:
            session_id: 会话唯一标识
            result: 结果状态 (success, failure, partial)
            metrics: 效果指标
        
        Returns:
            是否成功记录
        """
        ...
    
    # ── 效果评估 ──────────────────────────────────────────────
    async def evaluate(self, session_id: str) -> EffectEvaluation:
        """
        评估会话效果
        
        Args:
            session_id: 会话唯一标识
        
        Returns:
            EffectEvaluation: 效果评估结果
        """
        ...
    
    async def evaluate_task(self, task_id: str) -> EffectEvaluation:
        """
        评估单个任务效果
        
        Args:
            task_id: 任务唯一标识
        
        Returns:
            EffectEvaluation: 任务效果评估结果
        """
        ...
    
    async def get_metrics(self, session_id: str) -> EffectMetrics:
        """
        获取会话效果指标
        
        Args:
            session_id: 会话唯一标识
        
        Returns:
            EffectMetrics: 效果指标汇总
        """
        ...
    
    # ── 用户反馈 ──────────────────────────────────────────────
    async def collect_feedback(self, feedback: UserFeedback) -> bool:
        """
        收集用户反馈
        
        Args:
            feedback: 用户反馈信息
        
        Returns:
            是否成功收集
        """
        ...
    
    async def get_feedback(self, session_id: str) -> List[UserFeedback]:
        """
        获取会话的用户反馈
        
        Args:
            session_id: 会话唯一标识
        
        Returns:
            用户反馈列表
        """
        ...
    
    async def get_feedback_stats(self, 
                                  start_time: Optional[str] = None,
                                  end_time: Optional[str] = None) -> dict:
        """
        获取反馈统计
        
        Args:
            start_time: 开始时间
            end_time: 结束时间
        
        Returns:
            反馈统计数据
        """
        ...
    
    # ── 效果报告 ──────────────────────────────────────────────
    async def generate_report(self, 
                              session_id: str,
                              include_events: bool = True,
                              include_feedback: bool = True) -> EffectReport:
        """
        生成效果报告
        
        Args:
            session_id: 会话唯一标识
            include_events: 是否包含事件详情
            include_feedback: 是否包含用户反馈
        
        Returns:
            EffectReport: 效果报告
        """
        ...
    
    async def generate_summary(self,
                               start_time: str,
                               end_time: str,
                               group_by: str = "day") -> List[dict]:
        """
        生成效果汇总
        
        Args:
            start_time: 开始时间
            end_time: 结束时间
            group_by: 分组方式 (hour, day, week, month)
        
        Returns:
            汇总数据列表
        """
        ...
    
    # ── 历史查询 ──────────────────────────────────────────────
    async def list_sessions(self,
                            status: Optional[str] = None,
                            start_time: Optional[str] = None,
                            end_time: Optional[str] = None,
                            limit: int = 100) -> List[EffectSession]:
        """
        查询效果会话列表
        
        Args:
            status: 结果状态过滤
            start_time: 开始时间
            end_time: 结束时间
            limit: 返回数量限制
        
        Returns:
            效果会话列表
        """
        ...
    
    async def get_events(self, session_id: str) -> List[EffectEvent]:
        """
        获取会话的所有事件
        
        Args:
            session_id: 会话唯一标识
        
        Returns:
            效果事件列表
        """
        ...
```

### 13.2 Effect Models

```python
# core/effect/models.py

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

class EffectEventType(Enum):
    """效果事件类型"""
    TASK_START = "task_start"           # 任务开始
    TASK_END = "task_end"               # 任务结束
    TOOL_CALL = "tool_call"             # 工具调用
    TOOL_RESULT = "tool_result"         # 工具结果
    RESPONSE_START = "response_start"   # 响应开始
    RESPONSE_END = "response_end"       # 响应结束
    ERROR = "error"                     # 错误发生
    APPROVAL_REQUEST = "approval_request"  # 审批请求
    APPROVAL_RESULT = "approval_result"    # 审批结果

class EffectStatus(Enum):
    """效果状态"""
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"

class FeedbackType(Enum):
    """反馈类型"""
    THUMBS_UP = "thumbs_up"
    THUMBS_DOWN = "thumbs_down"
    RATING = "rating"           # 1-5 星
    COMMENT = "comment"
    CORRECTION = "correction"   # 用户修正

@dataclass
class EffectEvent:
    """效果事件"""
    event_id: str
    event_type: EffectEventType
    session_id: str
    timestamp: datetime = field(default_factory=datetime.now)
    
    # 事件详情
    name: str = ""
    description: str = ""
    input_data: Dict[str, Any] = field(default_factory=dict)
    output_data: Dict[str, Any] = field(default_factory=dict)
    
    # 执行信息
    duration_ms: float = 0.0
    success: bool = True
    error_message: Optional[str] = None
    
    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class EffectSession:
    """效果会话"""
    session_id: str
    channel_id: str
    user_id: str
    model: str
    
    # 时间信息
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None
    duration_ms: float = 0.0
    
    # 任务信息
    task_count: int = 0
    tool_call_count: int = 0
    error_count: int = 0
    
    # 结果
    status: EffectStatus = EffectStatus.SUCCESS
    result_message: str = ""
    
    # 输入输出 token
    input_tokens: int = 0
    output_tokens: int = 0

@dataclass
class EffectEvaluation:
    """效果评估"""
    session_id: str
    evaluated_at: datetime = field(default_factory=datetime.now)
    
    # 基础指标
    success_rate: float = 0.0
    avg_response_time_ms: float = 0.0
    tool_success_rate: float = 0.0
    error_rate: float = 0.0
    
    # 效率指标
    first_response_time_ms: float = 0.0  # 首次响应时间
    time_to_completion_ms: float = 0.0   # 完成时间
    token_efficiency: float = 0.0        # token 效率 (output/input)
    
    # 质量指标
    user_satisfaction: float = 0.0       # 用户满意度 (来自反馈)
    task_completion_rate: float = 0.0    # 任务完成率
    correction_rate: float = 0.0         # 用户修正率
    
    # 详细评估
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

@dataclass
class UserFeedback:
    """用户反馈"""
    feedback_id: str
    session_id: str
    feedback_type: FeedbackType
    created_at: datetime = field(default_factory=datetime.now)
    
    # 反馈内容
    rating: Optional[int] = None         # 1-5 星
    comment: Optional[str] = None
    correction: Optional[str] = None     # 用户修正内容
    
    # 元数据
    user_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class EffectMetrics:
    """效果指标汇总"""
    total_sessions: int = 0
    successful_sessions: int = 0
    failed_sessions: int = 0
    
    total_tasks: int = 0
    successful_tasks: int = 0
    
    total_tool_calls: int = 0
    successful_tool_calls: int = 0
    
    avg_session_duration_ms: float = 0.0
    avg_task_duration_ms: float = 0.0
    avg_tool_duration_ms: float = 0.0
    
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    
    avg_user_satisfaction: float = 0.0
    total_feedback_count: int = 0
    
    # 按时间段统计
    hourly_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)

@dataclass
class EffectReport:
    """效果报告"""
    report_id: str
    session_id: str
    generated_at: datetime = field(default_factory=datetime.now)
    
    # 会话信息
    session: EffectSession
    events: List[EffectEvent] = field(default_factory=list)
    feedbacks: List[UserFeedback] = field(default_factory=list)
    
    # 评估结果
    evaluation: Optional[EffectEvaluation] = None
    
    # 指标汇总
    metrics: Optional[EffectMetrics] = None
    
    # 分析总结
    summary: str = ""
    recommendations: List[str] = field(default_factory=list)
```

### 13.3 效果追踪 API

```python
# api/effect.py

from fastapi import APIRouter
from engine.community.manager import EngineManager

router = APIRouter(prefix="/api/effect", tags=["effect"])

@router.post("/track/event")
async def track_event(event: EffectEventRequest):
    """追踪效果事件"""
    effect = EngineManager.get_instance().effect
    if not effect:
        raise HTTPException(501, "Effect tracking not supported")
    
    event_id = await effect.track_event(EffectEvent(**event.dict()))
    return {"success": True, "event_id": event_id}

@router.post("/track/session/start")
async def start_session(session: EffectSessionRequest):
    """开始效果会话追踪"""
    effect = EngineManager.get_instance().effect
    if not effect:
        raise HTTPException(501, "Effect tracking not supported")
    
    session_id = await effect.track_session_start(EffectSession(**session.dict()))
    return {"success": True, "session_id": session_id}

@router.post("/track/session/{session_id}/end")
async def end_session(session_id: str, request: SessionEndRequest):
    """结束效果会话追踪"""
    effect = EngineManager.get_instance().effect
    await effect.track_session_end(session_id, request.result, request.metrics)
    return {"success": True}

@router.post("/evaluate/{session_id}")
async def evaluate_session(session_id: str):
    """评估会话效果"""
    effect = EngineManager.get_instance().effect
    if not effect:
        raise HTTPException(501, "Effect evaluation not supported")
    
    evaluation = await effect.evaluate(session_id)
    return {"success": True, "data": evaluation.to_dict()}

@router.post("/feedback")
async def submit_feedback(feedback: FeedbackRequest):
    """提交用户反馈"""
    effect = EngineManager.get_instance().effect
    await effect.collect_feedback(UserFeedback(**feedback.dict()))
    return {"success": True}

@router.get("/report/{session_id}")
async def get_report(session_id: str, include_events: bool = True, include_feedback: bool = True):
    """生成效果报告"""
    effect = EngineManager.get_instance().effect
    report = await effect.generate_report(session_id, include_events, include_feedback)
    return {"success": True, "data": report.to_dict()}

@router.get("/summary")
async def get_summary(start_time: str, end_time: str, group_by: str = "day"):
    """获取效果汇总"""
    effect = EngineManager.get_instance().effect
    summary = await effect.generate_summary(start_time, end_time, group_by)
    return {"success": True, "data": summary}
```

### 13.4 效果追踪数据流

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Agent 执行流程                                      │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│   1. Session 开始                                                                │
│   effect.track_session_start(session)                                           │
│   记录: session_id, channel_id, user_id, model, start_time                      │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│   2. 任务执行                                                                    │
│   effect.track_event({type: TASK_START, name, input})                           │
│   ... 执行任务 ...                                                               │
│   effect.track_event({type: TASK_END, output, duration, success})              │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
│ 3a. 工具调用          │ │ 3b. 响应生成          │ │ 3c. 错误发生          │
│ TOOL_CALL            │ │ RESPONSE_START       │ │ ERROR                │
│ TOOL_RESULT          │ │ RESPONSE_END         │ │ 记录错误信息和堆栈     │
└──────────────────────┘ └──────────────────────┘ └──────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│   4. Session 结束                                                                │
│   effect.track_session_end(session_id, result, metrics)                         │
│   记录: end_time, status, task_count, tool_call_count, error_count              │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│   5. 效果评估                                                                    │
│   evaluation = effect.evaluate(session_id)                                       │
│   计算: success_rate, avg_response_time, tool_success_rate, ...                  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│   6. 用户反馈                                                                    │
│   effect.collect_feedback({type: THUMBS_UP/RATING/COMMENT})                     │
│   用户对结果进行评价，用于计算 satisfaction 指标                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│   7. 效果报告                                                                    │
│   report = effect.generate_report(session_id)                                    │
│   包含: 会话信息, 事件列表, 评估结果, 用户反馈, 改进建议                           │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 14. 引擎生命周期

### 14.1 Engine Manager 重构

```python
# manager.py (重构后)

import asyncio
import logging
from typing import Dict, Type, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .core.engine.protocol import Engine
    from .core.engine.capability import EngineCapabilities

log = logging.getLogger("engine-manager")

class EngineManager:
    """引擎运行时管理器"""
    
    _instance: Optional["EngineManager"] = None
    
    def __init__(self):
        self._registry: Dict[str, Type["Engine"]] = {}
        self._engines: Dict[str, "Engine"] = {}
        self._current_engine: Optional["Engine"] = None
        self._lock = asyncio.Lock()
        self._capabilities_cache: Dict[str, "EngineCapabilities"] = {}
    
    @classmethod
    def get_instance(cls) -> "EngineManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None
    
    @property
    def engine(self) -> str:
        """当前引擎名称"""
        if self._current_engine is None:
            raise RuntimeError("No engine is active")
        return self._current_engine.name
    
    @property
    def current(self) -> "Engine":
        """获取当前引擎实例"""
        if self._current_engine is None:
            raise RuntimeError("No engine is active")
        return self._current_engine
    
    def get_capabilities(self, engine_name: Optional[str] = None) -> "EngineCapabilities":
        """获取引擎能力（支持缓存）"""
        name = engine_name or self.engine
        if name not in self._capabilities_cache:
            engine = self._engines.get(name)
            if engine:
                self._capabilities_cache[name] = engine.capabilities
        return self._capabilities_cache.get(name)
    
    # ── 引擎注册 ──────────────────────────────────────────────
    
    def register(self, engine_class: Type["Engine"]) -> None:
        """注册引擎类"""
        # 创建临时实例获取名称
        temp = engine_class({})
        name = temp.name
        self._registry[name] = engine_class
        log.info(f"Engine registered: {name}")
    
    def get_registered_engines(self) -> list[str]:
        """获取已注册的引擎列表"""
        return list(self._registry.keys())
    
    # ── 引擎生命周期 ──────────────────────────────────────────────
    
    async def create_engine(self, name: str, config: dict) -> "Engine":
        """创建引擎实例"""
        if name not in self._registry:
            raise ValueError(f"未注册的引擎: {name}")
        
        engine_class = self._registry[name]
        engine = engine_class(config)
        self._engines[name] = engine
        self._capabilities_cache[name] = engine.capabilities
        return engine
    
    async def initialize(self, default_engine: Optional[str] = None) -> None:
        """初始化引擎管理器"""
        engine_name = default_engine or self._get_default_engine()
        
        if engine_name not in self._registry:
            log.warning(f"默认引擎 {engine_name} 未注册，使用第一个可用引擎")
            engine_name = next(iter(self._registry.keys()), None)
        
        if engine_name is None:
            raise RuntimeError("没有可用的引擎")
        
        engine = await self.create_engine(engine_name, self._get_engine_config(engine_name))
        await engine.initialize()
        self._current_engine = engine
        log.info(f"Engine initialized: {engine_name}")
    
    async def switch(self, target: str, force: bool = False) -> dict:
        """切换引擎"""
        if target not in self._registry:
            raise ValueError(f"未注册的引擎: {target}")
        
        if target == self.engine:
            return {"switched": False, "engine": target, "reason": "already active"}
        
        async with self._lock:
            old_engine = self._current_engine
            
            # 检查活跃连接
            active = self._get_active_connections()
            if active > 0 and not force:
                raise RuntimeError(f"有 {active} 个活跃连接，使用 force=true 强制切换")
            
            # 关闭旧引擎
            if old_engine:
                await old_engine.shutdown()
            
            # 创建并初始化新引擎
            new_engine = await self.create_engine(target, self._get_engine_config(target))
            await new_engine.initialize()
            
            self._current_engine = new_engine
            
            return {
                "switched": True,
                "engine": target,
                "previous": old_engine.name if old_engine else None,
            }
    
    async def restart(self, force: bool = False) -> dict:
        """重启当前引擎"""
        async with self._lock:
            engine = self._current_engine
            
            active = self._get_active_connections()
            if active > 0 and not force:
                raise RuntimeError(f"有 {active} 个活跃连接，使用 force=true 强制重启")
            
            await engine.shutdown()
            await engine.initialize()
            
            return {"restarted": True, "engine": engine.name}
    
    async def shutdown(self) -> None:
        """关闭引擎管理器"""
        if self._current_engine:
            await self._current_engine.shutdown()
        self._current_engine = None
        self._engines.clear()
    
    # ── 便捷访问器（保持向后兼容）────────────────────────────────────
    
    @property
    def session(self):
        return self.current.session
    
    @property
    def chat(self):
        return self.current.chat
    
    @property
    def mcp(self):
        return self.current.mcp
    
    @property
    def skills(self):
        return self.current.skills
    
    @property
    def approval(self):
        return self.current.approval
    
    @property
    def file(self):
        return self.current.file
    
    @property
    def node(self):
        return self.current.node
    
    @property
    def channel(self):
        return self.current.channel
    
    @property
    def cron(self):
        return self.current.cron
    
    # ── 旧 API 兼容 ──────────────────────────────────────────────
    
    def get_session_api(self):
        """向后兼容：获取 SessionAPI"""
        return self.session
    
    def get_model_api(self):
        """向后兼容：获取 ModelAPI"""
        return self.current.model if hasattr(self.current, 'model') else None
    
    def get_cron_api(self):
        """向后兼容：获取 CronAPI"""
        return self.cron
    
    def get_node_api(self):
        """向后兼容：获取 NodeAPI"""
        return self.node
    
    # ── 内部方法 ──────────────────────────────────────────────
    
    def _get_default_engine(self) -> str:
        """获取默认引擎"""
        import os
        return os.getenv("CHAT_ENGINE", "openclaw")
    
    def _get_engine_config(self, name: str) -> dict:
        """获取引擎配置"""
        # TODO: 从配置文件加载
        return {}
    
    def _get_active_connections(self) -> int:
        """获取活跃 WebSocket 连接数"""
        # TODO: 实现连接计数
        return 0
```

---

## 15. 水平扩容设计

### 15.1 扩容架构概览

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              负载均衡层                                          │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                      Load Balancer (NGINX / HAProxy)                     │   │
│   │                                                                          │   │
│   │   - 会话亲和性 (Sticky Session)                                          │   │
│   │   - 健康检查路由                                                          │   │
│   │   - WebSocket 代理                                                        │   │
│   │   - 加权轮询 / 最少连接                                                   │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
│   Engine Instance 1  │ │   Engine Instance 2  │ │   Engine Instance N  │
│                      │ │                      │ │                      │
│   ┌──────────────┐   │ │   ┌──────────────┐   │ │   ┌──────────────┐   │
│   │  WebSocket   │   │ │   │  WebSocket   │   │ │   │  WebSocket   │   │
│   │   Server     │   │ │   │   Server     │   │ │   │   Server     │   │
│   └──────────────┘   │ │   └──────────────┘   │ │   └──────────────┘   │
│   ┌──────────────┐   │ │   ┌──────────────┐   │ │   ┌──────────────┐   │
│   │ Engine       │   │ │   │ Engine       │   │ │   │ Engine       │   │
│   │ Manager      │   │ │   │ Manager      │   │ │   │ Manager      │   │
│   └──────────────┘   │ │   └──────────────┘   │ │   └──────────────┘   │
│   ┌──────────────┐   │ │   ┌──────────────┐   │ │   ┌──────────────┐   │
│   │  Gateway     │   │ │   │  Gateway     │   │ │   │  Gateway     │   │
│   │  Client      │   │ │   │  Client      │   │ │   │  Client      │   │
│   └──────────────┘   │ │   └──────────────┘   │ │   └──────────────┘   │
└──────────────────────┘ └──────────────────────┘ └──────────────────────┘
                    │                 │                 │
                    └─────────────────┼─────────────────┘
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              共享状态层                                          │
│   ┌────────────────────┐ ┌────────────────────┐ ┌────────────────────┐         │
│   │   Session Store    │ │   Effect Store     │ │   Config Store     │         │
│   │   (Redis)          │ │   (Redis/DB)       │ │   (Redis/DB)       │         │
│   └────────────────────┘ └────────────────────┘ └────────────────────┘         │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              后端引擎层                                          │
│   ┌────────────────────┐ ┌────────────────────┐ ┌────────────────────┐         │
│   │   OpenClaw Engine  │ │   Hermes Agent Engine  │ │ Claude Code Engine│         │
│   │   (可独立扩容)      │ │   (可独立扩容)      │ │   (可独立扩容)      │         │
│   └────────────────────┘ └────────────────────┘ └────────────────────┘         │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 15.2 会话亲和性设计

```python
# core/scalability/session_affinity.py

from dataclasses import dataclass
from enum import Enum
from typing import Optional
import hashlib

class AffinityStrategy(Enum):
    """会话亲和性策略"""
    NONE = "none"                    # 无亲和性（无状态）
    SESSION_ID = "session_id"        # 基于会话 ID
    USER_ID = "user_id"              # 基于用户 ID
    CHANNEL_ID = "channel_id"        # 基于渠道 ID
    CONSISTENT_HASH = "consistent_hash"  # 一致性哈希

@dataclass
class AffinityConfig:
    """会话亲和性配置"""
    strategy: AffinityStrategy = AffinityStrategy.SESSION_ID
    ttl_seconds: int = 3600          # 亲和性过期时间
    fallback_enabled: bool = True    # 是否启用回退
    hash_replicas: int = 150         # 一致性哈希虚拟节点数

class SessionAffinity:
    """会话亲和性管理器"""
    
    def __init__(self, config: AffinityConfig):
        self._config = config
        self._ring: dict = {}         # 一致性哈希环
        self._nodes: list[str] = []   # 节点列表
    
    def get_node(self, key: str) -> Optional[str]:
        """
        根据 key 获取目标节点
        
        Args:
            key: 亲和性键（session_id / user_id / channel_id）
        
        Returns:
            目标节点 ID，如果无亲和性返回 None
        """
        if self._config.strategy == AffinityStrategy.NONE:
            return None
        
        if self._config.strategy == AffinityStrategy.CONSISTENT_HASH:
            return self._consistent_hash(key)
        
        # 简单哈希取模
        if not self._nodes:
            return None
        
        hash_value = int(hashlib.md5(key.encode()).hexdigest(), 16)
        node_index = hash_value % len(self._nodes)
        return self._nodes[node_index]
    
    def _consistent_hash(self, key: str) -> Optional[str]:
        """一致性哈希"""
        if not self._ring:
            return None
        
        hash_value = int(hashlib.md5(key.encode()).hexdigest(), 16)
        
        # 找到第一个大于等于 hash_value 的节点
        sorted_keys = sorted(self._ring.keys())
        for ring_key in sorted_keys:
            if hash_value <= ring_key:
                return self._ring[ring_key]
        
        # 环形，返回第一个节点
        return self._ring[sorted_keys[0]]
    
    def add_node(self, node_id: str) -> None:
        """添加节点"""
        if node_id not in self._nodes:
            self._nodes.append(node_id)
            self._rebuild_ring()
    
    def remove_node(self, node_id: str) -> None:
        """移除节点"""
        if node_id in self._nodes:
            self._nodes.remove(node_id)
            self._rebuild_ring()
    
    def _rebuild_ring(self) -> None:
        """重建一致性哈希环"""
        if self._config.strategy != AffinityStrategy.CONSISTENT_HASH:
            return
        
        self._ring = {}
        for node in self._nodes:
            for i in range(self._config.hash_replicas):
                virtual_key = f"{node}:{i}"
                hash_value = int(hashlib.md5(virtual_key.encode()).hexdigest(), 16)
                self._ring[hash_value] = node
```

### 15.3 分布式会话存储

```python
# core/scalability/session_store.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any
import json

@dataclass
class DistributedSession:
    """分布式会话"""
    session_id: str
    user_id: str
    channel_id: str
    engine_type: str
    instance_id: str              # 处理该会话的实例 ID
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None
    metadata: Dict[str, Any] = None
    
    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "engine_type": self.engine_type,
            "instance_id": self.instance_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "metadata": self.metadata or {},
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "DistributedSession":
        return cls(
            session_id=data["session_id"],
            user_id=data["user_id"],
            channel_id=data["channel_id"],
            engine_type=data["engine_type"],
            instance_id=data["instance_id"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            metadata=data.get("metadata"),
        )

class SessionStore(ABC):
    """分布式会话存储抽象"""
    
    @abstractmethod
    async def get(self, session_id: str) -> Optional[DistributedSession]:
        """获取会话"""
        ...
    
    @abstractmethod
    async def set(self, session: DistributedSession, ttl: int = 3600) -> bool:
        """设置会话"""
        ...
    
    @abstractmethod
    async def delete(self, session_id: str) -> bool:
        """删除会话"""
        ...
    
    @abstractmethod
    async def update_instance(self, session_id: str, instance_id: str) -> bool:
        """更新会话所属实例（迁移时使用）"""
        ...
    
    @abstractmethod
    async def get_sessions_by_instance(self, instance_id: str) -> list[DistributedSession]:
        """获取指定实例的所有会话"""
        ...
    
    @abstractmethod
    async def get_sessions_by_channel(self, channel_id: str) -> list[DistributedSession]:
        """获取指定渠道的所有会话"""
        ...

class RedisSessionStore(SessionStore):
    """Redis 会话存储实现"""
    
    def __init__(self, redis_client, key_prefix: str = "session:"):
        self._redis = redis_client
        self._key_prefix = key_prefix
    
    async def get(self, session_id: str) -> Optional[DistributedSession]:
        key = f"{self._key_prefix}{session_id}"
        data = await self._redis.get(key)
        if data:
            return DistributedSession.from_dict(json.loads(data))
        return None
    
    async def set(self, session: DistributedSession, ttl: int = 3600) -> bool:
        key = f"{self._key_prefix}{session.session_id}"
        await self._redis.setex(key, ttl, json.dumps(session.to_dict()))
        return True
    
    async def delete(self, session_id: str) -> bool:
        key = f"{self._key_prefix}{session_id}"
        await self._redis.delete(key)
        return True
    
    async def update_instance(self, session_id: str, instance_id: str) -> bool:
        session = await self.get(session_id)
        if session:
            session.instance_id = instance_id
            session.updated_at = datetime.now()
            await self.set(session)
            return True
        return False
    
    async def get_sessions_by_instance(self, instance_id: str) -> list[DistributedSession]:
        # 使用 SCAN 遍历所有会话
        sessions = []
        pattern = f"{self._key_prefix}*"
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                data = await self._redis.get(key)
                if data:
                    session = DistributedSession.from_dict(json.loads(data))
                    if session.instance_id == instance_id:
                        sessions.append(session)
            if cursor == 0:
                break
        return sessions
    
    async def get_sessions_by_channel(self, channel_id: str) -> list[DistributedSession]:
        sessions = []
        pattern = f"{self._key_prefix}*"
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                data = await self._redis.get(key)
                if data:
                    session = DistributedSession.from_dict(json.loads(data))
                    if session.channel_id == channel_id:
                        sessions.append(session)
            if cursor == 0:
                break
        return sessions
```

### 15.4 实例注册与发现

```python
# core/scalability/service_registry.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List
from enum import Enum

class InstanceStatus(Enum):
    """实例状态"""
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    DRAINING = "draining"       # 正在排空连接
    STOPPING = "stopping"

@dataclass
class ServiceInstance:
    """服务实例"""
    instance_id: str
    engine_type: str
    host: str
    port: int
    status: InstanceStatus
    registered_at: datetime
    last_heartbeat: datetime
    
    # 负载信息
    active_connections: int = 0
    active_sessions: int = 0
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    
    # 权重（用于负载均衡）
    weight: int = 100
    priority: int = 0
    
    # 元数据
    version: str = ""
    capabilities: List[str] = None
    metadata: dict = None
    
    @property
    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"
    
    @property
    def is_available(self) -> bool:
        return self.status in (InstanceStatus.HEALTHY, InstanceStatus.DEGRADED)

class ServiceRegistry(ABC):
    """服务注册中心抽象"""
    
    @abstractmethod
    async def register(self, instance: ServiceInstance) -> bool:
        """注册实例"""
        ...
    
    @abstractmethod
    async def deregister(self, instance_id: str) -> bool:
        """注销实例"""
        ...
    
    @abstractmethod
    async def heartbeat(self, instance_id: str, 
                        status: InstanceStatus,
                        load: dict = None) -> bool:
        """发送心跳"""
        ...
    
    @abstractmethod
    async def get_instance(self, instance_id: str) -> Optional[ServiceInstance]:
        """获取实例信息"""
        ...
    
    @abstractmethod
    async def list_instances(self, 
                             engine_type: str = None,
                             status: InstanceStatus = None) -> List[ServiceInstance]:
        """列出实例"""
        ...
    
    @abstractmethod
    async def select_instance(self, 
                              engine_type: str,
                              strategy: str = "least_connections") -> Optional[ServiceInstance]:
        """
        选择实例
        
        Args:
            engine_type: 引擎类型
            strategy: 选择策略
                - round_robin: 轮询
                - least_connections: 最少连接
                - weighted: 加权随机
                - random: 随机
        """
        ...

class RedisServiceRegistry(ServiceRegistry):
    """Redis 服务注册实现"""
    
    def __init__(self, redis_client, key_prefix: str = "registry:"):
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._ttl = 30  # 心跳 TTL
    
    async def register(self, instance: ServiceInstance) -> bool:
        key = f"{self._key_prefix}{instance.instance_id}"
        await self._redis.hset(key, mapping={
            "instance_id": instance.instance_id,
            "engine_type": instance.engine_type,
            "host": instance.host,
            "port": instance.port,
            "status": instance.status.value,
            "registered_at": instance.registered_at.isoformat(),
            "last_heartbeat": instance.last_heartbeat.isoformat(),
            "active_connections": instance.active_connections,
            "active_sessions": instance.active_sessions,
            "cpu_percent": instance.cpu_percent,
            "memory_percent": instance.memory_percent,
            "weight": instance.weight,
            "priority": instance.priority,
            "version": instance.version,
        })
        await self._redis.expire(key, self._ttl)
        return True
    
    async def deregister(self, instance_id: str) -> bool:
        key = f"{self._key_prefix}{instance_id}"
        await self._redis.delete(key)
        return True
    
    async def heartbeat(self, instance_id: str, 
                        status: InstanceStatus,
                        load: dict = None) -> bool:
        key = f"{self._key_prefix}{instance_id}"
        
        updates = {
            "status": status.value,
            "last_heartbeat": datetime.now().isoformat(),
        }
        
        if load:
            updates.update({
                "active_connections": load.get("active_connections", 0),
                "active_sessions": load.get("active_sessions", 0),
                "cpu_percent": load.get("cpu_percent", 0),
                "memory_percent": load.get("memory_percent", 0),
            })
        
        await self._redis.hset(key, mapping=updates)
        await self._redis.expire(key, self._ttl)
        return True
    
    async def get_instance(self, instance_id: str) -> Optional[ServiceInstance]:
        key = f"{self._key_prefix}{instance_id}"
        data = await self._redis.hgetall(key)
        if data:
            return self._parse_instance(data)
        return None
    
    async def list_instances(self, 
                             engine_type: str = None,
                             status: InstanceStatus = None) -> List[ServiceInstance]:
        instances = []
        pattern = f"{self._key_prefix}*"
        cursor = 0
        
        while True:
            cursor, keys = await self._redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                data = await self._redis.hgetall(key)
                if data:
                    instance = self._parse_instance(data)
                    if engine_type and instance.engine_type != engine_type:
                        continue
                    if status and instance.status != status:
                        continue
                    instances.append(instance)
            if cursor == 0:
                break
        
        return instances
    
    async def select_instance(self, 
                              engine_type: str,
                              strategy: str = "least_connections") -> Optional[ServiceInstance]:
        instances = await self.list_instances(
            engine_type=engine_type,
            status=InstanceStatus.HEALTHY
        )
        
        if not instances:
            # 降级：尝试获取 DEGRADED 状态的实例
            instances = await self.list_instances(
                engine_type=engine_type,
                status=InstanceStatus.DEGRADED
            )
        
        if not instances:
            return None
        
        if strategy == "round_robin":
            # 简单轮询
            return instances[0]
        
        elif strategy == "least_connections":
            # 最少连接
            return min(instances, key=lambda x: x.active_connections)
        
        elif strategy == "weighted":
            # 加权随机
            import random
            total_weight = sum(i.weight for i in instances if i.is_available)
            r = random.randint(1, total_weight)
            current = 0
            for instance in instances:
                if instance.is_available:
                    current += instance.weight
                    if current >= r:
                        return instance
            return instances[0]
        
        else:  # random
            import random
            available = [i for i in instances if i.is_available]
            return random.choice(available) if available else None
    
    def _parse_instance(self, data: dict) -> ServiceInstance:
        return ServiceInstance(
            instance_id=data["instance_id"],
            engine_type=data["engine_type"],
            host=data["host"],
            port=int(data["port"]),
            status=InstanceStatus(data["status"]),
            registered_at=datetime.fromisoformat(data["registered_at"]),
            last_heartbeat=datetime.fromisoformat(data["last_heartbeat"]),
            active_connections=int(data.get("active_connections", 0)),
            active_sessions=int(data.get("active_sessions", 0)),
            cpu_percent=float(data.get("cpu_percent", 0)),
            memory_percent=float(data.get("memory_percent", 0)),
            weight=int(data.get("weight", 100)),
            priority=int(data.get("priority", 0)),
            version=data.get("version", ""),
        )
```

### 15.5 负载均衡策略

```python
# core/scalability/load_balancer.py

from abc import ABC, abstractmethod
from typing import List, Optional
from .service_registry import ServiceInstance

class LoadBalancerStrategy(ABC):
    """负载均衡策略抽象"""
    
    @abstractmethod
    def select(self, instances: List[ServiceInstance]) -> Optional[ServiceInstance]:
        """选择实例"""
        ...

class RoundRobinStrategy(LoadBalancerStrategy):
    """轮询策略"""
    
    def __init__(self):
        self._current = 0
    
    def select(self, instances: List[ServiceInstance]) -> Optional[ServiceInstance]:
        if not instances:
            return None
        
        available = [i for i in instances if i.is_available]
        if not available:
            return None
        
        instance = available[self._current % len(available)]
        self._current += 1
        return instance

class LeastConnectionsStrategy(LoadBalancerStrategy):
    """最少连接策略"""
    
    def select(self, instances: List[ServiceInstance]) -> Optional[ServiceInstance]:
        available = [i for i in instances if i.is_available]
        if not available:
            return None
        
        return min(available, key=lambda x: x.active_connections)

class WeightedRoundRobinStrategy(LoadBalancerStrategy):
    """加权轮询策略"""
    
    def __init__(self):
        self._current_weights: dict = {}
        self._current_index = 0
    
    def select(self, instances: List[ServiceInstance]) -> Optional[ServiceInstance]:
        available = [i for i in instances if i.is_available]
        if not available:
            return None
        
        # 平滑加权轮询算法
        total_weight = sum(i.weight for i in available)
        max_weight = max(i.weight for i in available)
        
        for instance in available:
            if instance.instance_id not in self._current_weights:
                self._current_weights[instance.instance_id] = 0
            self._current_weights[instance.instance_id] += instance.weight
        
        selected = None
        for instance in available:
            if selected is None or self._current_weights[instance.instance_id] > self._current_weights[selected.instance_id]:
                selected = instance
        
        if selected:
            self._current_weights[selected.instance_id] -= total_weight
        
        return selected

class ConsistentHashStrategy(LoadBalancerStrategy):
    """一致性哈希策略"""
    
    def __init__(self, replicas: int = 150):
        self._replicas = replicas
        self._ring: dict = {}
        self._sorted_keys: list = []
    
    def _build_ring(self, instances: List[ServiceInstance]) -> None:
        import hashlib
        self._ring = {}
        self._sorted_keys = []
        
        for instance in instances:
            if instance.is_available:
                for i in range(self._replicas):
                    key = f"{instance.instance_id}:{i}"
                    hash_value = int(hashlib.md5(key.encode()).hexdigest(), 16)
                    self._ring[hash_value] = instance
                    self._sorted_keys.append(hash_value)
        
        self._sorted_keys.sort()
    
    def select(self, instances: List[ServiceInstance], hash_key: str = None) -> Optional[ServiceInstance]:
        if not hash_key:
            # 无哈希键时回退到最少连接
            return LeastConnectionsStrategy().select(instances)
        
        self._build_ring(instances)
        
        if not self._ring:
            return None
        
        import hashlib
        hash_value = int(hashlib.md5(hash_key.encode()).hexdigest(), 16)
        
        # 找到第一个大于等于 hash_value 的节点
        for key in self._sorted_keys:
            if hash_value <= key:
                return self._ring[key]
        
        # 环形，返回第一个节点
        return self._ring[self._sorted_keys[0]]


class LoadBalancer:
    """负载均衡器"""
    
    def __init__(self, strategy: str = "least_connections"):
        self._strategy = self._create_strategy(strategy)
        self._consistent_hash = ConsistentHashStrategy()
    
    def _create_strategy(self, strategy: str) -> LoadBalancerStrategy:
        strategies = {
            "round_robin": RoundRobinStrategy(),
            "least_connections": LeastConnectionsStrategy(),
            "weighted": WeightedRoundRobinStrategy(),
        }
        return strategies.get(strategy, LeastConnectionsStrategy())
    
    def select_instance(self, 
                       instances: List[ServiceInstance],
                       session_id: str = None,
                       strategy: str = None) -> Optional[ServiceInstance]:
        """
        选择实例
        
        Args:
            instances: 可用实例列表
            session_id: 会话 ID（用于会话亲和性）
            strategy: 覆盖默认策略
        """
        if not instances:
            return None
        
        # 如果有 session_id，使用一致性哈希
        if session_id:
            return self._consistent_hash.select(instances, session_id)
        
        # 使用配置的策略
        if strategy:
            return self._create_strategy(strategy).select(instances)
        
        return self._strategy.select(instances)
```

### 15.6 优雅关闭与迁移

```python
# core/scalability/graceful_shutdown.py

import asyncio
import signal
from typing import Callable, List
from dataclasses import dataclass
from enum import Enum
import logging

log = logging.getLogger("graceful-shutdown")

class ShutdownPhase(Enum):
    """关闭阶段"""
    RUNNING = "running"              # 正常运行
    DRAINING = "draining"            # 排空连接
    STOPPING = "stopping"            # 停止中
    STOPPED = "stopped"              # 已停止

@dataclass
class ShutdownConfig:
    """关闭配置"""
    drain_timeout: int = 30          # 排空超时（秒）
    shutdown_timeout: int = 10       # 关闭超时（秒）
    health_check_interval: int = 5   # 健康检查间隔（秒）
    min_drain_time: int = 5          # 最小排空时间（秒）

class GracefulShutdown:
    """优雅关闭管理器"""
    
    def __init__(self, config: ShutdownConfig = None):
        self._config = config or ShutdownConfig()
        self._phase = ShutdownPhase.RUNNING
        self._drain_start_time: float = 0
        self._shutdown_handlers: List[Callable] = []
        self._drain_handlers: List[Callable] = []
        self._session_migrator: Callable = None
    
    @property
    def phase(self) -> ShutdownPhase:
        return self._phase
    
    @property
    def is_accepting_connections(self) -> bool:
        return self._phase == ShutdownPhase.RUNNING
    
    @property
    def is_draining(self) -> bool:
        return self._phase == ShutdownPhase.DRAINING
    
    def register_shutdown_handler(self, handler: Callable) -> None:
        """注册关闭处理器"""
        self._shutdown_handlers.append(handler)
    
    def register_drain_handler(self, handler: Callable) -> None:
        """注册排空处理器"""
        self._drain_handlers.append(handler)
    
    def set_session_migrator(self, migrator: Callable) -> None:
        """设置会话迁移器"""
        self._session_migrator = migrator
    
    async def start_drain(self) -> None:
        """开始排空连接"""
        log.info("Starting graceful drain...")
        self._phase = ShutdownPhase.DRAINING
        self._drain_start_time = asyncio.get_event_loop().time()
        
        # 通知负载均衡器停止路由新连接
        for handler in self._drain_handlers:
            try:
                await handler()
            except Exception as e:
                log.error(f"Drain handler failed: {e}")
        
        # 等待连接排空
        await self._wait_for_drain()
    
    async def _wait_for_drain(self) -> None:
        """等待连接排空"""
        start_time = asyncio.get_event_loop().time()
        
        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            
            # 检查超时
            if elapsed >= self._config.drain_timeout:
                log.warning(f"Drain timeout after {elapsed:.1f}s, forcing shutdown")
                break
            
            # 检查最小排空时间
            if elapsed < self._config.min_drain_time:
                await asyncio.sleep(1)
                continue
            
            # 检查是否所有连接都已关闭
            active_connections = await self._get_active_connections()
            if active_connections == 0:
                log.info("All connections drained")
                break
            
            log.info(f"Waiting for {active_connections} connections to drain...")
            await asyncio.sleep(1)
    
    async def _get_active_connections(self) -> int:
        """获取活跃连接数"""
        # TODO: 从实际的连接管理器获取
        return 0
    
    async def shutdown(self) -> None:
        """执行关闭"""
        log.info("Starting graceful shutdown...")
        self._phase = ShutdownPhase.STOPPING
        
        # 执行关闭处理器
        for handler in self._shutdown_handlers:
            try:
                await asyncio.wait_for(handler(), timeout=5)
            except asyncio.TimeoutError:
                log.warning("Shutdown handler timeout")
            except Exception as e:
                log.error(f"Shutdown handler failed: {e}")
        
        self._phase = ShutdownPhase.STOPPED
        log.info("Shutdown complete")
    
    async def migrate_sessions(self, target_instance_id: str) -> int:
        """
        迁移会话到目标实例
        
        Args:
            target_instance_id: 目标实例 ID
        
        Returns:
            迁移的会话数量
        """
        if not self._session_migrator:
            log.warning("No session migrator configured")
            return 0
        
        try:
            return await self._session_migrator(target_instance_id)
        except Exception as e:
            log.error(f"Session migration failed: {e}")
            return 0
    
    def setup_signal_handlers(self) -> None:
        """设置信号处理器"""
        loop = asyncio.get_event_loop()
        
        def handle_sigterm():
            log.info("Received SIGTERM")
            asyncio.create_task(self.start_drain())
        
        def handle_sigint():
            log.info("Received SIGINT")
            asyncio.create_task(self.start_drain())
        
        try:
            loop.add_signal_handler(signal.SIGTERM, handle_sigterm)
            loop.add_signal_handler(signal.SIGINT, handle_sigint)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler
            pass


class SessionMigration:
    """会话迁移"""
    
    def __init__(self, session_store, service_registry, websocket_manager):
        self._session_store = session_store
        self._service_registry = service_registry
        self._ws_manager = websocket_manager
    
    async def migrate_to_instance(self, target_instance_id: str) -> int:
        """
        迁移当前实例的所有会话到目标实例
        
        Args:
            target_instance_id: 目标实例 ID
        
        Returns:
            迁移的会话数量
        """
        # 获取当前实例的所有会话
        current_instance = await self._service_registry.get_instance(
            self._get_current_instance_id()
        )
        sessions = await self._session_store.get_sessions_by_instance(current_instance.instance_id)
        
        if not sessions:
            return 0
        
        # 通知客户端重新连接到新实例
        migrated = 0
        for session in sessions:
            try:
                # 发送迁移通知
                await self._ws_manager.send_to_session(
                    session.session_id,
                    {
                        "type": "migration",
                        "target_instance": target_instance_id,
                        "session_id": session.session_id,
                    }
                )
                
                # 更新会话存储中的实例 ID
                await self._session_store.update_instance(
                    session.session_id,
                    target_instance_id
                )
                
                migrated += 1
            except Exception as e:
                log.error(f"Failed to migrate session {session.session_id}: {e}")
        
        return migrated
    
    async def migrate_channel(self, channel_id: str, target_instance_id: str) -> int:
        """
        迁移指定渠道的所有会话
        
        Args:
            channel_id: 渠道 ID
            target_instance_id: 目标实例 ID
        
        Returns:
            迁移的会话数量
        """
        sessions = await self._session_store.get_sessions_by_channel(channel_id)
        
        migrated = 0
        for session in sessions:
            try:
                await self._ws_manager.send_to_session(
                    session.session_id,
                    {
                        "type": "migration",
                        "target_instance": target_instance_id,
                        "session_id": session.session_id,
                    }
                )
                await self._session_store.update_instance(
                    session.session_id,
                    target_instance_id
                )
                migrated += 1
            except Exception as e:
                log.error(f"Failed to migrate session {session.session_id}: {e}")
        
        return migrated
    
    def _get_current_instance_id(self) -> str:
        """获取当前实例 ID"""
        import os
        return os.getenv("INSTANCE_ID", "unknown")
```

### 15.7 水平扩容配置示例

```yaml
# scalability.yaml

# 负载均衡配置
load_balancer:
  strategy: "least_connections"     # round_robin | least_connections | weighted
  health_check:
    interval: 5                     # 健康检查间隔（秒）
    timeout: 3                      # 超时时间（秒）
    unhealthy_threshold: 3          # 不健康阈值
    healthy_threshold: 2            # 健康阈值

# 会话亲和性配置
session_affinity:
  enabled: true
  strategy: "session_id"            # none | session_id | user_id | channel_id | consistent_hash
  ttl: 3600                         # 亲和性过期时间（秒）
  consistent_hash:
    replicas: 150                   # 虚拟节点数

# 服务注册配置
service_registry:
  backend: "redis"                  # redis | etcd | consul
  redis:
    host: "${REDIS_HOST}"
    port: 6379
    key_prefix: "engine:registry:"
    ttl: 30                         # 心跳 TTL（秒）
  
# 会话存储配置
session_store:
  backend: "redis"
  redis:
    host: "${REDIS_HOST}"
    port: 6379
    key_prefix: "engine:session:"
    ttl: 86400                      # 会话 TTL（秒）

# 优雅关闭配置
graceful_shutdown:
  enabled: true
  drain_timeout: 30                 # 排空超时（秒）
  shutdown_timeout: 10              # 关闭超时（秒）
  min_drain_time: 5                 # 最小排空时间（秒）

# 自动扩缩容配置 (Kubernetes HPA)
autoscaling:
  enabled: true
  min_replicas: 2
  max_replicas: 10
  metrics:
    - type: "cpu"
      target_average_utilization: 70
    - type: "connections"
      target_average_value: 100
  behavior:
    scale_down:
      stabilization_window_seconds: 300
      policies:
        - type: "Percent"
          value: 10
          period_seconds: 60
    scale_up:
      stabilization_window_seconds: 60
      policies:
        - type: "Percent"
          value: 100
          period_seconds: 15
        - type: "Pods"
          value: 4
          period_seconds: 15
      select_policy: "Max"
```

### 15.8 Kubernetes 部署示例

```yaml
# kubernetes/engine-deployment.yaml

apiVersion: apps/v1
kind: Deployment
metadata:
  name: engine
  labels:
    app: engine
spec:
  replicas: 3
  selector:
    matchLabels:
      app: engine
  template:
    metadata:
      labels:
        app: engine
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8888"
        prometheus.io/path: "/metrics"
    spec:
      terminationGracePeriodSeconds: 60  # 优雅关闭时间
      containers:
        - name: engine
          image: engine:latest
          ports:
            - containerPort: 8888
              name: http
            - containerPort: 18789
              name: websocket
          env:
            - name: INSTANCE_ID
              valueFrom:
                fieldRef:
                  fieldPath: metadata.name
            - name: CHAT_ENGINE
              value: "openclaw"
            - name: REDIS_HOST
              valueFrom:
                configMapKeyRef:
                  name: engine-config
                  key: redis_host
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "2000m"
              memory: "2Gi"
          livenessProbe:
            httpGet:
              path: /health
              port: 8888
            initialDelaySeconds: 10
            periodSeconds: 10
            timeoutSeconds: 3
            failureThreshold: 3
          readinessProbe:
            httpGet:
              path: /health
              port: 8888
            initialDelaySeconds: 5
            periodSeconds: 5
            timeoutSeconds: 2
            failureThreshold: 2
          lifecycle:
            preStop:
              exec:
                command: ["/bin/sh", "-c", "sleep 10"]  # 等待排空
---
apiVersion: v1
kind: Service
metadata:
  name: engine
spec:
  selector:
    app: engine
  ports:
    - port: 8888
      name: http
    - port: 18789
      name: websocket
  sessionAffinity: ClientIP  # 会话亲和性
  sessionAffinityConfig:
    clientIP:
      timeoutSeconds: 3600
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: engine-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: engine
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Pods
      pods:
        metric:
          name: active_connections
        target:
          type: AverageValue
          averageValue: "100"
```

---

## 16. 前端架构设计

### 16.1 引擎能力查询 Hook

```typescript
// src/frontend/src/hooks/useEngineCapabilities.ts

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { EngineController } from '@/services/backend-api/EngineController';

export interface EngineCapabilities {
  supported: string[];
  limited: Record<string, string>;
  fallback: Record<string, string>;
}

export function useEngineCapabilities(engineName?: string) {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['engine-capabilities', engineName],
    queryFn: () => EngineController.getCapabilities(engineName),
    staleTime: 5 * 60 * 1000, // 5 分钟缓存
  });

  const capabilities = data?.data;

  const supports = (cap: string): boolean => {
    if (!capabilities) return false;
    return capabilities.supported.includes(cap) || cap in capabilities.limited;
  };

  const isLimited = (cap: string): boolean => {
    if (!capabilities) return false;
    return cap in capabilities.limited;
  };

  const getLimitation = (cap: string): string | undefined => {
    if (!capabilities) return undefined;
    return capabilities.limited[cap];
  };

  const hasFallback = (cap: string): boolean => {
    if (!capabilities) return false;
    return cap in capabilities.fallback;
  };

  return {
    capabilities,
    isLoading,
    error,
    refetch,
    supports,
    isLimited,
    getLimitation,
    hasFallback,
  };
}

export function useCapabilityGuard(capability: string, engineName?: string) {
  const { supports, isLimited, getLimitation, hasFallback } = useEngineCapabilities(engineName);
  
  return {
    canUse: supports(capability),
    isLimited: isLimited(capability),
    limitation: getLimitation(capability),
    hasFallback: hasFallback(capability),
  };
}
```

### 16.2 能力感知组件

```tsx
// src/frontend/src/components/engine/CapabilityGuard.tsx

import React from 'react';
import { useCapabilityGuard } from '@/hooks/useEngineCapabilities';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { AlertTriangle } from 'lucide-react';

interface CapabilityGuardProps {
  capability: string;
  engineName?: string;
  children: React.ReactNode;
  fallback?: React.ReactNode;
  showWarning?: boolean;
  warningMessage?: string;
}

export function CapabilityGuard({
  capability,
  engineName,
  children,
  fallback = null,
  showWarning = true,
  warningMessage,
}: CapabilityGuardProps) {
  const { canUse, isLimited, limitation } = useCapabilityGuard(capability, engineName);

  if (!canUse) {
    return <>{fallback}</>;
  }

  return (
    <>
      {isLimited && showWarning && (
        <Alert variant="warning" className="mb-2">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            {warningMessage || limitation || `${capability} 功能受限`}
          </AlertDescription>
        </Alert>
      )}
      {children}
    </>
  );
}

// 使用示例
export function MCPServerControl({ serverCode }: { serverCode: string }) {
  return (
    <div>
      <CapabilityGuard capability="mcp.start" fallback={<StartButtonDisabled />}>
        <StartButton serverCode={serverCode} />
      </CapabilityGuard>
      
      <CapabilityGuard 
        capability="mcp.delete" 
        showWarning={true}
        warningMessage="删除配置需要重启生效"
      >
        <DeleteButton serverCode={serverCode} />
      </CapabilityGuard>
    </div>
  );
}
```

### 16.3 引擎状态 Store

```typescript
// src/frontend/src/stores/engineStore.ts

import { create } from 'zustand';
import { EngineCapabilities } from '@/hooks/useEngineCapabilities';

export type EngineType = 'openclaw' | 'hermes-agent' | 'claude-code';

interface EngineState {
  // 当前引擎
  activeEngine: EngineType;
  engineStatus: 'idle' | 'connecting' | 'running' | 'error';
  engineVersion: string | null;
  
  // 引擎能力
  capabilities: EngineCapabilities | null;
  capabilitiesLoading: boolean;
  
  // 引擎列表
  registeredEngines: string[];
  
  // Actions
  setActiveEngine: (engine: EngineType) => void;
  setEngineStatus: (status: EngineState['engineStatus']) => void;
  setCapabilities: (caps: EngineCapabilities) => void;
  setRegisteredEngines: (engines: string[]) => void;
  
  // 切换引擎
  switchEngine: (target: EngineType, force?: boolean) => Promise<void>;
  restartEngine: (force?: boolean) => Promise<void>;
}

export const useEngineStore = create<EngineState>((set, get) => ({
  activeEngine: 'openclaw',
  engineStatus: 'idle',
  engineVersion: null,
  capabilities: null,
  capabilitiesLoading: false,
  registeredEngines: ['openclaw', 'hermes-agent'],
  
  setActiveEngine: (engine) => set({ activeEngine: engine }),
  setEngineStatus: (status) => set({ engineStatus: status }),
  setCapabilities: (caps) => set({ capabilities: caps }),
  setRegisteredEngines: (engines) => set({ registeredEngines: engines }),
  
  switchEngine: async (target, force = false) => {
    set({ engineStatus: 'connecting' });
    try {
      await EngineController.switch(target, force);
      set({ activeEngine: target, engineStatus: 'running' });
      // 重新获取能力
      const caps = await EngineController.getCapabilities(target);
      set({ capabilities: caps.data });
    } catch (error) {
      set({ engineStatus: 'error' });
      throw error;
    }
  },
  
  restartEngine: async (force = false) => {
    const { activeEngine } = get();
    set({ engineStatus: 'connecting' });
    try {
      await EngineController.restart(force);
      set({ engineStatus: 'running' });
    } catch (error) {
      set({ engineStatus: 'error' });
      throw error;
    }
  },
}));
```

### 16.4 引擎选择器组件

```tsx
// src/frontend/src/components/engine/EngineSelector.tsx

import React from 'react';
import { useEngineStore, EngineType } from '@/stores/engineStore';
import { useEngineCapabilities } from '@/hooks/useEngineCapabilities';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

const ENGINE_INFO: Record<EngineType, { name: string; description: string; icon: string }> = {
  openclaw: { name: 'OpenClaw', description: '企业级 AI 引擎', icon: '🤖' },
  'hermes-agent': { name: 'Hermes Agent', description: 'AI Agent 引擎', icon: '🧠' },
  'claude-code': { name: 'Claude Code', description: '本地代码助手', icon: '⚡' },
};

export function EngineSelector() {
  const { 
    activeEngine, 
    engineStatus, 
    registeredEngines, 
    switchEngine 
  } = useEngineStore();
  
  const { capabilities } = useEngineCapabilities(activeEngine);

  const handleEngineChange = async (value: string) => {
    try {
      await switchEngine(value as EngineType);
    } catch (error) {
      console.error('Failed to switch engine:', error);
    }
  };

  const statusBadge = {
    idle: <Badge variant="secondary">空闲</Badge>,
    connecting: <Badge variant="warning">连接中</Badge>,
    running: <Badge variant="success">运行中</Badge>,
    error: <Badge variant="destructive">错误</Badge>,
  };

  return (
    <div className="flex items-center gap-2">
      <Select value={activeEngine} onValueChange={handleEngineChange}>
        <SelectTrigger className="w-[180px]">
          <SelectValue placeholder="选择引擎" />
        </SelectTrigger>
        <SelectContent>
          {registeredEngines.map((engine) => (
            <SelectItem key={engine} value={engine}>
              <div className="flex items-center gap-2">
                <span>{ENGINE_INFO[engine as EngineType]?.icon}</span>
                <span>{ENGINE_INFO[engine as EngineType]?.name}</span>
              </div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      
      {statusBadge[engineStatus]}
      
      {capabilities && (
        <Tooltip>
          <TooltipTrigger>
            <Badge variant="outline">
              {capabilities.supported.length} 能力
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            <div className="max-w-xs">
              <p className="font-semibold">支持的能力 ({capabilities.supported.length})</p>
              <ul className="text-xs mt-1 space-y-1">
                {capabilities.supported.slice(0, 5).map((cap) => (
                  <li key={cap}>{cap}</li>
                ))}
                {capabilities.supported.length > 5 && (
                  <li>... 还有 {capabilities.supported.length - 5} 个</li>
                )}
              </ul>
              {Object.keys(capabilities.limited).length > 0 && (
                <p className="font-semibold mt-2">受限能力 ({Object.keys(capabilities.limited).length})</p>
              )}
            </div>
          </TooltipContent>
        </Tooltip>
      )}
    </div>
  );
}
```

### 16.5 API Controller 扩展

```typescript
// src/frontend/src/services/backend-api/EngineController.ts

import { request } from '@/requestConfig';
import type { ApiResponse } from '@/types';

export type EngineType = 'openclaw' | 'hermes-agent' | 'claude-code';

export interface EngineCapabilities {
  supported: string[];
  limited: Record<string, string>;
  fallback: Record<string, string>;
}

export interface EngineStatus {
  engine: EngineType;
  status: 'idle' | 'connecting' | 'running' | 'error';
  version: string;
  activeConnections: number;
  capabilities: EngineCapabilities;
}

export const EngineController = {
  // 获取当前引擎状态
  getStatus: () => 
    request<ApiResponse<EngineStatus>>('/api/engine/status', { method: 'GET' }),

  // 获取引擎能力
  getCapabilities: (engine?: string) => {
    const url = engine 
      ? `/api/engine/capabilities?engine=${engine}`
      : '/api/engine/capabilities';
    return request<ApiResponse<EngineCapabilities>>(url, { method: 'GET' });
  },

  // 切换引擎
  switch: (target: EngineType, force: boolean = false) =>
    request<ApiResponse<{ switched: boolean; engine: string }>>('/api/engine/switch', {
      method: 'POST',
      data: { target, force },
    }),

  // 重启引擎
  restart: (force: boolean = false) =>
    request<ApiResponse<{ restarted: boolean; engine: string }>>('/api/engine/restart', {
      method: 'POST',
      data: { force },
    }),

  // 列出已注册的引擎
  listRegistered: () =>
    request<ApiResponse<{ engines: string[] }>>('/api/engine/list', { method: 'GET' }),
};
```

---

## 17. 端到端数据流

### 17.1 前端发起请求的完整流程

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              用户操作                                            │
│  例如：点击 "启动 MCP Server" 按钮                                               │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           Component Layer                                        │
│  MCPServerControl.tsx                                                           │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ 1. 检查能力                                                              │   │
│  │    const { canUse, isLimited } = useCapabilityGuard('mcp.start');       │   │
│  │                                                                          │   │
│  │ 2. 显示警告（如果有限制）                                                │   │
│  │    {isLimited && <Alert>{limitation}</Alert>}                           │   │
│  │                                                                          │   │
│  │ 3. 条件渲染                                                              │   │
│  │    {canUse ? <StartButton /> : <DisabledButton />}                      │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              Hook Layer                                          │
│  useMcpMarket.ts / useMCPServers.ts                                             │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ const startServer = async (serverCode: string) => {                     │   │
│  │   // 1. 调用 API                                                         │   │
│  │   const result = await McpController.startServer(serverCode);           │   │
│  │                                                                          │   │
│  │   // 2. 处理结果                                                         │   │
│  │   if (result.success) {                                                  │   │
│  │     toast.success('启动成功');                                           │   │
│  │     // 刷新列表                                                          │   │
│  │     queryClient.invalidateQueries(['mcp-servers']);                     │   │
│  │   } else {                                                               │   │
│  │     toast.error(result.message || '启动失败');                           │   │
│  │   }                                                                      │   │
│  │ };                                                                       │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                              API Layer                                           │
│  McpController.ts                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ export const McpController = {                                          │   │
│  │   startServer: (serverCode: string) =>                                  │   │
│  │     request<ApiResponse>(`/api/mcp/${serverCode}/start`, {             │   │
│  │       method: 'POST',                                                    │   │
│  │     }),                                                                  │   │
│  │ };                                                                       │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                             HTTP POST /api/mcp/{serverCode}/start
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           Backend API Layer                                      │
│  api/mcp.py                                                                     │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ @router.post("/{server_code}/start")                                    │   │
│  │ async def start_mcp_server(server_code: str):                           │   │
│  │     # 1. 获取当前引擎的 MCP Plugin                                       │   │
│  │     mcp = EngineManager.get_instance().mcp                              │   │
│  │                                                                          │   │
│  │     # 2. 检查能力                                                        │   │
│  │     caps = EngineManager.get_instance().get_capabilities()              │   │
│  │     if not caps.supports(Capability.MCP_START):                         │   │
│  │         raise HTTPException(501, "当前引擎不支持此功能")                  │   │
│  │                                                                          │   │
│  │     # 3. 调用 Plugin 方法                                                │   │
│  │     success = await mcp.start_server(server_code)                       │   │
│  │     return {"success": success}                                         │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          Engine Plugin Layer                                     │
│  engines/openclaw/mcp.py (OpenClaw 引擎)                                        │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ class OpenClawMCPPlugin(MCPPlugin):                                     │   │
│  │     async def start_server(self, server_code: str) -> bool:            │   │
│  │         # OpenClaw 实现：调用 mcporter 命令                              │   │
│  │         result = subprocess.run(                                         │   │
│  │             ["mcporter", "start", server_code],                          │   │
│  │             capture_output=True,                                         │   │
│  │         )                                                                │   │
│  │         return result.returncode == 0                                   │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│  engines/claude_code/mcp.py (Claude Code 引擎)                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐   │
│  │ class ClaudeCodeMCPPlugin(MCPPlugin):                                   │   │
│  │     async def start_server(self, server_code: str) -> bool:            │   │
│  │         # Claude Code 实现：MCP 在启动时自动加载                         │   │
│  │         # 返回 False 表示不支持运行时启动                                │   │
│  │         raise CapabilityNotSupportedError(                              │   │
│  │             "claude-code", Capability.MCP_START                         │   │
│  │         )                                                                │   │
│  └─────────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### 17.2 能力不匹配时的处理流程

```
前端发起请求 → 能力检查
                  │
                  ├── 支持 → 正常执行
                  │
                  ├── 有限制 → 显示警告 → 用户确认 → 执行（带限制处理）
                  │
                  ├── 有 Fallback → 显示警告 → 使用 Fallback 方法执行
                  │
                  └── 不支持 → 显示 "此功能在当前引擎不可用" → 隐藏/禁用按钮
```

---

## 18. 配置与注册

### 18.1 引擎配置 (engine.json)

```json
{
  "engine": {
    "default": "openclaw",
    "fallback": "hermes-agent"
  },
  
  "engines": {
    "openclaw": {
      "gateway_url": "ws://localhost:18789",
      "gateway_token": null,
      "capabilities": {
        "supported": ["session.*", "chat.*", "mcp.*", "skills.*", "cron.*"],
        "limited": {
          "mcp.start": "通过 mcporter 命令启动",
          "mcp.stop": "通过 mcporter 命令停止"
        }
      },
      "process": {
        "start_cmd": ["openclaw", "start"],
        "stop_cmd": ["openclaw", "stop"],
        "startup_timeout_sec": 30
      }
    },
    
    "hermes-agent": {
      "gateway_url": "ws://localhost:21000",
      "gateway_token": null,
      "capabilities": {
        "supported": ["session.*", "chat.*", "mcp.*", "skills.*", "cron.*"]
      },
      "process": {
        "start_cmd": ["cargo", "run", "--package", "bcs"],
        "stop_cmd": ["pkill", "-f", "bcs"],
        "startup_timeout_sec": 60
      }
    },
    
    "claude-code": {
      "cli_path": "claude",
      "model": "claude-opus-4-6",
      "mcp_config": "~/.claude/mcp.json",
      "skills_dir": "~/.claude/skills",
      "capabilities": {
        "supported": [
          "session.history",
          "chat.stream", "chat.complete",
          "mcp.*", "skills.*", "file.*"
        ],
        "limited": {
          "session.list": "只能列出当前会话",
          "mcp.start": "启动时自动加载所有 MCP",
          "mcp.stop": "需要重启进程"
        }
      }
    }
  },
  
  "mcp": {
    "config_path": "~/.mcporter/mcporter.json",
    "token_header": "x-openclaw-enterprise-mcp-token"
  },
  
  "skills": {
    "base_dir": "/home/admin/.extra-skills"
  }
}
```

### 18.2 API 层扩展

```python
# api/engine.py (新增)

from fastapi import APIRouter, Depends
from engine.community.manager import EngineManager
from engine.community.core.engine.capability import EngineCapabilities

router = APIRouter(prefix="/api/engine", tags=["engine"])

@router.get("/status")
async def get_engine_status():
    """获取当前引擎状态"""
    manager = EngineManager.get_instance()
    return {
        "engine": manager.engine,
        "status": "running",  # TODO: 从 manager 获取
        "capabilities": manager.get_capabilities().to_dict(),
    }

@router.get("/capabilities")
async def get_engine_capabilities(engine: str = None):
    """获取引擎能力"""
    manager = EngineManager.get_instance()
    caps = manager.get_capabilities(engine)
    return {
        "success": True,
        "data": caps.to_dict() if caps else None,
    }

@router.post("/switch")
async def switch_engine(target: str, force: bool = False):
    """切换引擎"""
    manager = EngineManager.get_instance()
    result = await manager.switch(target, force)
    return {"success": True, "data": result}

@router.post("/restart")
async def restart_engine(force: bool = False):
    """重启当前引擎"""
    manager = EngineManager.get_instance()
    result = await manager.restart(force)
    return {"success": True, "data": result}

@router.get("/list")
async def list_registered_engines():
    """列出已注册的引擎"""
    manager = EngineManager.get_instance()
    return {
        "success": True,
        "data": {
            "engines": manager.get_registered_engines(),
        },
    }
```

---

## 19. 实现指南

### 19.1 添加新引擎的步骤

1. **创建引擎目录**
   ```
   src/engine/engines/<engine_name>/
   ├── engine.py          # Engine 实现
   ├── session.py         # SessionPlugin 实现
   ├── chat.py            # ChatPlugin 实现
   ├── mcp.py             # MCPPlugin 实现
   ├── skills.py          # SkillsPlugin 实现
   ├── approval.py        # ApprovalPlugin 实现
   ├── file.py            # FilePlugin 实现
   ├── node.py            # NodePlugin 实现
   ├── channel.py             # ChannelPlugin 实现
   └── config.py          # 引擎配置
   ```

2. **实现核心类**
   ```python
   # engines/<engine_name>/engine.py
   
   from engine.community.core.engine.protocol import Engine
   from engine.community.core.engine.capability import EngineCapabilities, Capability
   from engine.engines.base import BaseEngine
   
   class MyEngine(BaseEngine):
       def __init__(self, config: dict):
           super().__init__(config)
           # 初始化插件
           self._session = MySessionPlugin(config)
           self._chat = MyChatPlugin(config)
           self._mcp = MyMCPPlugin(config)  # 可选
           # ...
       
       @property
       def name(self) -> str:
           return "my-engine"
       
       @property
       def version(self) -> str:
           return "1.0.0"
       
       @property
       def capabilities(self) -> EngineCapabilities:
           return EngineCapabilities(
               supported={
                   Capability.SESSION_LIST,
                   Capability.CHAT_STREAM,
                   # ...
               },
               limited={
                   Capability.MCP_START: "需要手动配置启动",
               },
           )
   ```

3. **注册引擎**
   ```python
   # engines/__init__.py 或 app.py
   
   from engine.community.core.engine.registry import EngineRegistry
   from engine.engines.my_engine.engine import MyEngine
   
   registry = EngineRegistry.get_instance()
   registry.register(MyEngine)
   ```

4. **配置引擎**
   ```json
   // engine.json
   {
     "engines": {
       "my-engine": {
         "option1": "value1",
         "option2": "value2",
         "capabilities": {
           "supported": ["session.*", "chat.*"],
           "limited": {}
         }
       }
     }
   }
   ```

### 19.2 能力检查最佳实践

```python
# 在调用前检查能力
from engine.community.core.engine.capability import Capability

async def start_mcp_server(server_code: str):
    manager = EngineManager.get_instance()
    caps = manager.get_capabilities()
    
    # 检查基本能力
    if not caps.supports(Capability.MCP_START):
        if caps.has_fallback(Capability.MCP_START):
            fallback = caps.get_fallback(Capability.MCP_START)
            log.warning(f"使用 fallback: {fallback}")
            # 执行 fallback 逻辑
            return await fallback_start_mcp(server_code)
        raise NotImplementedError("当前引擎不支持启动 MCP Server")
    
    # 检查有限制的能力
    if caps.is_limited(Capability.MCP_START):
        limitation = caps.get_limitation(Capability.MCP_START)
        log.warning(f"MCP 启动能力受限: {limitation}")
        # 可以在响应中返回警告信息
    
    return await manager.mcp.start_server(server_code)
```

### 19.3 文件迁移计划

The original M0–M5 plan + post-M4 Server-Generalization phases (A–D)
are documented in commit history. The follow-up plugin-dispatch
migration ("every engine-touching router goes through `manager.<plugin>.*`")
landed in seven phases on `totalfrank-backend-migration-review`:

| Phase | Router | Plugin | Notes |
|-------|--------|--------|-------|
| **1** | `api/models` | `OpenClawModelsPlugin` (existed; ported legacy logic) | Deleted `src/api/model.py` (89 lines) + `openclaw/adapter/model_adapter.py` (447 lines); added `manager.models` passthrough. |
| **2** | `api/mcp` | `OpenClawMCPPlugin` (new) | Extended `MCPPlugin` Protocol with `filter_servers` + new `MCPFilterRequest` / `MCPFilterResult` models; added `MCP_FILTER_SERVERS` capability. |
| **3** | `api/skills` | `OpenClawSkillsPlugin` (new) | Extended `SkillsPlugin` Protocol with bulk symlink methods (`sync_symlinks` / `sync_bindpaths` / `clean_symlinks`) + matching `SymlinkItem` / `SyncSymlinksRequest` / `SyncSymlinksResult` / `CleanSymlinksRequest` / `CleanSymlinksResult` models; added `SKILLS_SYNC_SYMLINKS` / `SKILLS_SYNC_BINDPATHS` / `SKILLS_CLEAN_SYMLINKS` capabilities. |
| **4** | `api/node` | `OpenClawNodePlugin` (new) | Created `core/node/{models,protocol}.py` from scratch; deleted `src/api/node.py` + `openclaw/adapter/node_adapter.py` + `engine/factory/node.py`. Whole `src/api/`, `src/engine/openclaw/adapter/`, `src/engine/factory/` directories removed. |
| **5** | `api/file` | `OpenClawFilePlugin` (new) | Created `core/file/{models,protocol}.py`; ports the legacy `/aidesktop/...→/home/admin/.openclaw/` workspace prefix rewrite. |
| **6** | `api/default_config` | `OpenClawDefaultConfigPlugin` (new) | Created `core/default_config/{models,protocol}.py`; new `DEFAULT_CONFIG_GET` capability. |
| **7** | `api/web_shell` | `OpenClawWebShellPlugin` (new) | Created `core/web_shell/protocol.py` with `WebShellSession` abstraction; new `WEB_SHELL_OPEN` capability. Static HTML page + health endpoint stay in router; the WS pump dispatches through plugin's `open_session()`. |

Routers deliberately left out of this migration: `api/bot` (backend-side
credentials, not engine state) and `api/engine` (meta endpoints —
already engine-agnostic).

---

## 附录

### A. 错误处理

```python
# core/engine/exceptions.py

class EngineError(Exception):
    """引擎基础异常"""
    pass

class CapabilityNotSupportedError(EngineError):
    """能力不支持异常"""
    def __init__(self, engine: str, capability: "Capability"):
        self.engine = engine
        self.capability = capability
        super().__init__(f"Engine '{engine}' does not support capability: {capability.value}")

class EngineNotInitializedError(EngineError):
    """引擎未初始化异常"""
    pass

class EngineStartupError(EngineError):
    """引擎启动失败异常"""
    pass
```

### B. 健康检查

```python
# core/engine/health.py

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"

@dataclass
class HealthCheckResult:
    status: HealthStatus
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    
    # 各组件状态
    session_status: Optional[HealthStatus] = None
    chat_status: Optional[HealthStatus] = None
    mcp_status: Optional[HealthStatus] = None
    skills_status: Optional[HealthStatus] = None
    approval_status: Optional[HealthStatus] = None
    file_status: Optional[HealthStatus] = None
    node_status: Optional[HealthStatus] = None
    channel_status: Optional[HealthStatus] = None
    cron_status: Optional[HealthStatus] = None
```

### C. 向后兼容

为确保现有代码平滑迁移，保持以下兼容性：

1. **EngineManager 属性访问**
   ```python
   # 旧方式仍然支持
   manager = EngineManager.get_instance()
   session_api = manager.get_session_api()
   
   # 新方式
   session_plugin = manager.session
   ```

2. **环境变量**
   - `CHAT_ENGINE` 仍然有效，用于选择默认引擎

3. **配置文件**
   - 现有 `engine.json` 格式兼容，新字段有默认值

4. **Web API**
   - 所有现有 API 路径保持不变
   - 新增 `/api/engine/capabilities` 端点

---

## 版本历史

| 版本 | 日期 | 描述 |
|------|------|------|
| v1.0 | 2026-04-20 | 初始版本 |
| v1.1 | 2026-04-20 | 补充 Approval/File/Node/Channel 抽象层 |
| v1.2 | 2026-04-20 | 添加前端架构设计 |
| v1.3 | 2026-04-20 | 完善端到端数据流和实现指南 |
| v1.4 | 2026-04-20 | 补充 Health 健康检查抽象层、Effect Agent效果追踪抽象层 |
| v1.5 | 2026-04-20 | 添加水平扩容设计（会话亲和性、分布式存储、负载均衡、优雅关闭）|
