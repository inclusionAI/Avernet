# AGENTS.md — Avernet (oc-web)

> 面向编码 agent 与贡献者的工程指南：架构、约定、流程、命令。工具无关，所有 AI 编码工具与人类贡献者通用。

---

## 1. 项目背景

Avernet 是一个**独立 AI 工作平台**，基于 OpenClaw 引擎，按 ToC 品质标准打造。

**核心功能**：AI Agent 对话、能力市场（Skill/MCP）、全局上下文（组织/项目信息）、定时任务、渠道管理、节点管理。

**核心概念 — Bot**：用户的 AI 数字分身。分个人 Bot / 团队 Bot / 项目 Bot / Default Bot（系统分配，不可删除）。

---

## 2. 技术选型

| 类别 | 技术 | 说明 |
| --- | --- | --- |
| 框架 | @umijs/max (UmiJS) | 仅构建时依赖 |
| UI | Tailwind CSS + shadcn/ui | lavender 色板 |
| 状态管理 | Zustand + React Context | 全局用 Zustand，局部用 Context |
| 类型系统 | TypeScript | 全量覆盖 |
| AI/Chat SDK | aix-chat-core / aix-chat-ui / aix-chat-adapters | 对话核心 SDK |
| Toast | sonner | `toast.success/error`，禁止 antd message |
| 动画 | motion | — |
| 样式变体 | CVA（class-variance-authority） | Button 等组件变体 |

---

## 3. 项目结构

```
src/
├── pages/                      # 页面模块
│   ├── Assistant/              # 对话页（核心页面）
│   │   ├── Chat/ChatPage.tsx   # 聊天主页面
│   │   ├── Sidebar/            # 会话/技能/资源侧边栏
│   │   └── index.tsx           # 路由入口（Provider 编排）
│   ├── Bootstrap/              # 启动流程（Bot 初始化、连接信息写入）
│   ├── GroupChat/              # 群聊
│   ├── PrivateChat/            # 私聊
│   ├── ServiceBot/             # 服务 Bot
│   ├── Market/ SkillMarket/    # 能力市场
│   ├── MCPMarket/              # MCP 市场
│   ├── ExpertMarket/           # 协作广场
│   ├── OrgContext/             # 组织/项目信息
│   ├── Cron/                   # 定时任务
│   ├── Node/                   # 节点管理
│   └── Layout/MainLayout/      # 主布局（导航 + 内容区）
├── stores/                     # Zustand stores（纯数据状态）
├── hooks/                      # 业务 Hook
├── services/backend-api/       # API Controller（类型定义 + 接口封装）
├── components/
│   ├── Common/                 # 项目自封装通用 UI（PascalCase）
│   ├── ui/                     # shadcn primitives（lowercase）
│   └── {Domain}/               # 业务组件
├── shell/                      # 模块注册表（buildMenus/buildRoutes + AppExt 契约）
├── capabilities/               # 模块契约体系（值/列表/行为/实现 四种差异形态）
├── contexts/                   # React Context
├── adapters/                   # 引擎/解析适配器层
├── requestConfig.ts            # request 拦截器（代理路由、token 注入）
└── utils/
    ├── env.ts                  # 环境配置
    └── platform.ts             # 平台判断（isElectron, isDingTalk, isVSCode）

config/
├── servers.config.ts           # 服务器地址配置（唯一配置源）
├── presets.config.ts           # 环境预设
└── routes.ts                   # 路由（按 UMI_ENV 条件注入模块路由）
```

---

## 4. 架构规范

### 四层架构（必须遵循）

```
Component 层（UI 渲染）
    ↓ 调用
Hook 层（业务逻辑、API 调用、错误处理、Toast）
    ↓ 读写
Store 层（Zustand，纯数据状态，无副作用）
    ↓ 调用
API Controller 层（backend-api，类型定义 + 接口封装）
```

**新功能开发步骤：**

1. **Store** — 定义 state 类型，loading 按操作区分（`isLoading`/`isCreating`/`isDeleting`），禁止 API 调用
2. **API Controller** — 定义请求/响应类型，每个方法对应一个 REST 接口
3. **Hook** — `useCallback` 包装，`try/catch` + `toast.success/error` + `console.error('[Module] xxx')`
4. **Component** — 从 Hook 获取数据和方法，不直接调用 Store 或 API

### 代码规范

- **样式**：Tailwind CSS，lavender 色板，长类名用 CVA 变体
- **UI 组件**：新增用 shadcn/ui，不再引入新 antd 用法
- **Toast**：`sonner`（`toast.success/error`），禁止 antd message
- **文件大小**：Component ≤ 300 行，Hook ≤ 250 行，Store ≤ 150 行
- **命名**：组件 PascalCase，Hook `use` 前缀，工具函数 camelCase
- **导入路径**：用 `@/` 别名
- **日志**：`console.log('[ModuleName] xxx')`
- **类名合并**：`cn()`（`@/utils/utils`）
- **Zustand selector**：不得在 selector 内调用 `getState()`，必须使用传入的 `state` 参数
- **所有按钮**：必须使用 `<Button>` 组件（variant × soft/ghost）

### 通用 UI 组件白名单（强制）

新代码从 barrel 统一引：`import { Button, Segmented, Spin } from '@/components'`。

| 场景 | 必须使用 | 禁止 |
| --- | --- | --- |
| 按钮 | `<Button>` | 裸 `<button>` + className / antd Button |
| Tab 切换 | `<Segmented>` | 裸 `<button>` 写 tab / antd Tabs / shadcn Tabs |
| 加载提示 | `<Spin>` | 裸 `Loader2` / 文字 "加载中..." |
| 骨架屏 | `<Skeleton.*>` | 裸 `animate-pulse div` |
| 页面顶栏 | `<PageHeader>` | 裸 `<h1>` + `<p>` 组合 |
| 空状态 | `<Empty>` | 自写 "暂无数据" div |
| 弹窗 | `<Modal>` / `<ConfirmDialog>` / `<Drawer>` | 裸 `<dialog>` / antd Modal |
| Toast | `toast.*` from `sonner` | antd `message.*` |

### 性能（不可协商）

引入新依赖前必须评估体积、Tree-shaking、是否可复用现有依赖。禁止：gzip > 50KB 不懒加载、功能重叠的多库、全局入口导入仅特定页面的重型依赖。

---

## 5. 错误处理

### 统一模式

- **全局兜底**：`requestConfig.ts` 的 `errorHandler` 拦截未处理的 4xx/5xx
- **业务层覆盖**：传 `skipErrorHandler: true` 绕过全局 handler
- **错误信息提取**：统一用 `extractErrorMessage(error, fallback)`（`src/utils/requestErrorHandler.ts`），兼容多种后端响应格式

### 已知陷阱（Pitfalls）

**`request` 的 4xx 行为**：对 4xx/5xx 抛异常且默认弹 toast。轮询必须加 `skipErrorHandler: true`。error 结构：`error.response.status` / `error.data.message`。

**fatalError 模式（轮询终止）**：`catch` 内不能直接 `throw`（会被同级 catch 拦截）。正确做法：

```typescript
let fatalError: Error | null = null;
while (Date.now() < deadline && !fatalError) {
  try {
    const res = await getBotStatus({ bot_id }, { skipErrorHandler: true });
  } catch (err: any) {
    if (err?.response?.status >= 400 && err?.response?.status < 500) {
      fatalError = new Error(err?.data?.message || '权限不足');
      break;
    }
    await sleep(POLL_INTERVAL);
  }
}
if (fatalError) throw fatalError; // 循环外 throw
```

**`Object.assign` 不替换闭包引用**：`removeChild` 前检查 `container.parentNode` 是否存在。

**协作场景 owner_id 兜底被「显式传参」短路**：凡是 `targetUserId || resolveSkillOwnerUserId(botId, userId)` 这类「调用方显式优先 + bot owner 兜底」的 hook 签名（`useSkillMarket` 的 `createSkillSet` / `addSkillsToSet` / `loadUserSkillSets` 等），调用点**一律传 `undefined`**，让 hook 内部按 bot 归属解析 owner。一旦把登录 userId 塞进 `targetUserId` 槽位，`||` 短路会让兜底变成死代码——协作者操作他人服务 Bot 时鉴权失败或写错归属。

```typescript
// ❌ 错误：登录 userId 短路了 owner 兜底
addSkillsToSet(setId, skillIds, userId, botId);
// ✅ 正确：传 undefined，hook 内部 resolveSkillOwnerUserId(botId) 解析
addSkillsToSet(setId, skillIds, undefined, botId);
```

例外：确有跨 owner 的明确意图时才显式传值。

---

## 6. 体验规范

**风格**：Calm UI / Neutral Workspace，低饱和度背景 + 白色内容区。

**关键约束**：

- 主色 lavender，中性色 slate，禁止 `bg-gray-*`
- 页面顶栏用 `<PageHeader>` 组件，不自己写 h1
- 骨架屏用 `Skeleton.*` 组件，禁止裸 `animate-pulse div`
- 卡片边框 `border-slate-200/60`，禁止 `border-slate-100`
- antd → shadcn 替换：Popconfirm / Tooltip / Modal / ConfirmDialog / Drawer / Toast

---

## 7. 研发流程（SDD + TDD）

新功能采用 **Spec-Driven Development + Test-Driven Development**：Spec 定义 What，TDD 在每一层验证 How。

```
需求 → ① Spec 编写 → ② Spec Review(approved) → ③ 按功能类型分流开发 → ④ PR(测试全绿) → ⑤ Spec done
```

### Spec 适用范围

| 场景                                 | 需要 Spec |
| ------------------------------------ | --------- |
| 新功能（用户可感知的行为变化）       | 必须      |
| 跨层改动（Store + Hook + Component） | 必须      |
| 有 API 契约需对齐                    | 必须      |
| Bug 修复 / 纯样式 / 重构（行为不变） | 不需要    |

### Spec 核心约束

- **status 不是 approved，不允许开工写业务代码**（可做技术调研/PoC）
- **需求变了先改 Spec 再改代码**
- **AC 必须可测试**：写完后自问「这条能写成一个 `it()` 吗？」
- **AC 描述行为不描述 UI**：「按钮变灰」不是 AC，「操作进行中按钮 disabled」才是
- AC 格式：**当 [触发条件]，[主体] 应该 [可观测的结果]**

### 功能分类与测试策略

| 类型 | 代表 Hook | 开发路径 |
| --- | --- | --- |
| CRUD 型 | `useNodes`, `useScheduledTasks` | Store TDD → Hook TDD → Component |
| 状态机型 | `useSlashCommand`, `useVirtualScroll` | Hook TDD → Component |
| SDK 编排型 | `useMultiSessionChat`, `useBotInit` | Store TDD(如有) → Hook(无单测) → 手动验证 AC |

**为什么 SDK 编排型不做 Hook 单测**：Mock 整个 SDK 成本极高，测试退化成「mock 行为是否和 SDK 一致」的验证——真 Bug 抓不到，SDK 升级测试先挂。

### 测试规范

- **不追求覆盖率**，聚焦「改了会怕的代码」
- **Store 测试**：直接 `getState()` / action，驱动于 Spec 的技术契约
- **Hook 测试**：`@testing-library/react` 的 `renderHook` + `act` + `waitFor`
- **Component 测试**：可选，只保护关键路径（危险操作确认、权限控制等）
- **测试全绿是 PR 硬门槛**；只对新功能和改动文件补测试，不追加测试到老代码

---

## 8. 多端环境

| 平台     | 检测方式                  | 特殊处理                     |
| -------- | ------------------------- | ---------------------------- |
| Web      | 域名判断 PRE/PROD         | preset 切换                  |
| Electron | `window.ELECTRON_ENV`     | 请求走本地 Agent             |
| DingTalk | URL 含 `ddtab`            | 跳过初始化                   |
| VSCode   | `?source=vscode` + iframe | postMessage 通信，上下文注入 |

---

## 9. 核心运行机制速查

- **Bootstrap**：`?initMode=bot` → Bot 初始化 → 轮询 PENDING→ACTIVE → connectionStore → 工作台
- **connectionStore**：全局唯一连接信息源，React 用 `useConnectionStore()`，非 React 用 `getProxyTarget()`
- **请求路由**：`/api/sessions` 等走代理；`/api/v1/`、`api/bots/` 走管理后端；Electron 走本地 Agent
- **Token 刷新**：proxyToken 短期过期，`useConnectionRefresh` 定时刷新
- **引擎**：OpenClaw，WebSocket URL `/api/openclaw/ws`

---

## 10. 模块契约体系（内外形态差异收口）

主导航、创建 Bot 字段等内外差异，统一走 `src/capabilities` 契约 + `src/shell` 注册表，**核心文件不塞条件分支**：

- **菜单/路由**：`buildMenus(AppExt.modules)` / `buildRoutes(...)` 生成；基础清单在 `src/shell/extension.ts`，扩展项经 `extend()` 注入。
- **四种差异形态**：值 / 数据列表 / 行为列表 / 实现——都在核心写契约，默认值进 `src/app` 或模块 `extension.ts`。
- **构建形态**：由 `UMI_ENV` 控制（`@ext` 注入点指向不同 overlay）。

---

## 11. 工程命令

```bash
npm run dev            # 开发服务器
npm run build          # 构建
npm test               # 全部测试
npm run test:watch     # Watch 模式
npm run lint           # 代码检查（max lint）
npx tsc --noEmit 2>&1 | grep -v "node_modules"   # 类型检查
```

- 主分支：`master`
- commit：conventional 格式（`feat: xxx` / `fix: xxx`，中文描述）；commit-msg hook 校验
- pre-commit：lint-staged 对暂存文件跑 eslint/stylelint/prettier
- 拉取远程：`git fetch origin && git rebase origin/<目标分支>`
