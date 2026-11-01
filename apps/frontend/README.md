# Avernet（open-claw）

> 一个独立的 **AI 工作平台** 前端，基于 OpenClaw 引擎。提供 AI Agent 对话、能力市场（Skill/MCP）、全局上下文、定时任务、渠道管理、节点管理等能力。

核心概念 **Bot** 是用户的 AI 数字分身：个人 Bot / 团队 Bot / 项目 Bot / Default Bot。

---

## 技术栈

- **框架**：[@umijs/max](https://umijs.org)（React + TypeScript）
- **UI**：Tailwind CSS + [shadcn/ui](https://ui.shadcn.com)
- **状态管理**：Zustand + React Context
- **Toast**：[sonner](https://sonner.emilkowal.ski)
- **测试**：Jest + Testing Library

---

## 快速开始

### 环境要求

- Node.js 20+
- npm（或兼容的包管理器）

### 安装与启动

```bash
# 1. 安装依赖
npm install

# 2. 启动开发服务器
npm run dev

# 3. 生产构建
npm run build
```

启动后按终端提示的地址访问。

### 配置后端地址

所有环境的服务端地址统一在 **`config/servers.config.ts`** 配置（唯一配置源），修改后重启生效：

```typescript
export const SERVERS = {
  LOCAL: {
    MANAGEMENT: 'http://localhost:8888', // 管理后端
    SESSION: 'http://localhost:8888', // 会话服务
    // ...
  },
  // 其他环境
};
```

---

## 常用命令

| 命令                 | 说明            |
| -------------------- | --------------- |
| `npm run dev`        | 启动开发服务器  |
| `npm run build`      | 生产构建        |
| `npm test`           | 运行测试        |
| `npm run test:watch` | 测试 Watch 模式 |
| `npm run lint`       | 代码检查        |

---

## 项目结构

```
src/
├── pages/            # 页面模块（Assistant 对话页为核心）
├── stores/           # Zustand 状态
├── hooks/            # 业务 Hook
├── services/         # API Controller（类型定义 + 接口封装）
├── components/       # UI 组件（Common 通用 / ui shadcn / 业务域）
├── shell/            # 模块注册表（菜单/路由生成）
├── capabilities/     # 模块契约体系
└── adapters/         # 引擎/解析适配器
config/
└── servers.config.ts # 服务器地址配置
```

完整的架构规范、代码约定、研发流程见 **[AGENTS.md](./AGENTS.md)**。

---

## 贡献

开发前请先阅读 **[AGENTS.md](./AGENTS.md)**（架构分层、代码规范、UI 组件白名单、SDD + TDD 研发流程）。提交遵循 [Conventional Commits](https://www.conventionalcommits.org)（`feat:` / `fix:` 等），pre-commit 会对暂存文件跑 lint，commit-msg 会校验格式。

---

## 许可证

本项目以 **Apache License 2.0** 授权（见各源码文件头部的 `SPDX-License-Identifier: Apache-2.0`）。

> 关于代码注释：中文注释为官方版本，其它语言注释仅作参考；不一致时以中文为准（详见 [LEGAL.md](./LEGAL.md)）。
