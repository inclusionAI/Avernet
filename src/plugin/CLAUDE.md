# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

OpenClaw 插件仓库，用于开发和发布可安装的 OpenClaw 插件包。采用 `pnpm workspace` 组织多个插件包。

## 技术栈

- **运行时**: Node.js >= 18.19.0
- **语言**: TypeScript 5.2+
- **包管理**: pnpm workspace
- **构建工具**: tshy (输出 ESM + CommonJS 双格式)
- **测试框架**: egg-bin + mocha
- **Lint**: ESLint + eslint-config-egg
- **类型检查**: attw (@arethetypeswrong/cli)

## 仓库结构

```
openclaw-plugins/
├── packages/          # 插件包目录
├── apps/              # 应用目录 (workspace)
├── paulApps/          # 应用目录 (workspace)
├── templates/         # 插件生成模板
│   └── current-plugin-package-standard/
├── scripts/           # 工具脚本
│   └── init-plugin-package.mjs  # 插件生成脚本
└── package.json       # 根配置，声明 workspace
```

## 插件包结构

标准插件包包含：

```
packages/<plugin-name>/
├── package.json           # 包配置、脚本、openclaw 入口
├── openclaw.plugin.json   # OpenClaw 插件元数据
├── README.md              # 插件说明
├── tsconfig.json          # TypeScript 配置
├── src/index.ts           # 插件主入口
└── test/index.test.ts     # 测试文件
```

### package.json 关键字段

- `openclaw.extensions`: 插件入口文件路径
- `openclaw.channel`: channel 类型插件的配置 (id, label, order 等)
- `openclaw.setupEntry`: setup 插件入口
- `tshy.exports`: 定义导出路径
- `exports`: ESM/CommonJS 双格式导出配置

### openclaw.plugin.json 字段

- `id`: 插件唯一标识
- `name`: 插件展示名
- `description`: 插件描述
- `configSchema`: 配置 JSON Schema
- `channels`: 支持的 channel 列表

## 常用命令

### 根目录命令

```bash
pnpm install                    # 安装所有依赖
pnpm init:plugin <name>         # 生成新插件骨架
pnpm --filter <package> build   # 构建指定插件
pnpm --filter <package> test    # 测试指定插件
pnpm --filter <package> lint    # Lint 指定插件
```

### 插件包内命令

```bash
pnpm build          # 构建 (tshy)
pnpm test-local     # 本地测试
pnpm cov            # 测试覆盖率
pnpm lint           # ESLint
pnpm ci             # 完整 CI 流程
```

## 开发流程

1. **创建新插件**: `pnpm init:plugin <plugin-name> --description "..." --author "..."`
2. **修改配置**: 更新 `package.json` 和 `openclaw.plugin.json`
3. **实现逻辑**: 编辑 `src/index.ts`，导出默认插件对象并实现 `register(api)`
4. **编写测试**: 补充 `test/index.test.ts`
5. **验证**: 依次运行 `lint` → `test-local` → `build`

## 插件注册模式

```typescript
import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/core';
import { emptyPluginConfigSchema } from 'openclaw/plugin-sdk/core';

const plugin = {
  id: 'my-plugin',
  name: 'My Plugin',
  description: '...',
  configSchema: emptyPluginConfigSchema(),
  register(api: OpenClawPluginApi) {
    // 注册 channel/tool/hook 等能力
    api.registerChannel(myChannel);
  },
};

export default plugin;
```

## 构建产物

tshy 构建后生成：

```
dist/
├── esm/           # ESM 格式
├── commonjs/      # CommonJS 格式
└── package.json   # 导出映射
```

## 发布相关

- 发布方式：当前以源码构建和本地 extension 软链为主；正式 npm 发布前需确认公开 registry 策略。
- CI 验证 Node.js 版本：18.19.0, 18, 20, 22
