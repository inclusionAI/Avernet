# OpenClaw 插件制作与生成指南

这个仓库用于维护可安装的 OpenClaw 插件包。仓库采用 `pnpm workspace` 组织多个插件，每个插件通常放在 `packages/` 目录下，具备独立的源码、测试、构建配置和 `openclaw.plugin.json` 元数据。

如果你准备在这里新建一个插件，推荐先从 `pnpm` 和仓库结构开始理解，再用仓库自带脚本生成一个标准插件骨架。

## 1. 先认识 `pnpm`

`pnpm` 是一个 Node.js 包管理工具，特点是安装快、磁盘占用低、对 monorepo 友好。

在这个仓库里，`pnpm` 主要承担三件事：

- 管理根仓库和各个插件包的依赖
- 管理 workspace，也就是一次维护多个 package
- 通过 `--filter` 只对某一个插件执行安装、构建、测试等命令

本仓库已经声明了 workspace：

```yaml
packages:
  - "packages/*"
  - "apps/*"
  - "paulApps/*"
```

这意味着：

- `packages/` 下的每个插件目录都会被当作一个独立 package
- 在根目录执行 `pnpm install` 时，依赖会统一安装并链接
- 可以只针对某个插件运行命令，例如：

```bash
pnpm --filter openclaw-channel-bcn build
pnpm --filter openclaw-channel-bcn test
```

## 2. 仓库里插件长什么样

一个标准 OpenClaw 插件包通常包含这些文件：

```text
packages/<your-plugin>/
├── package.json
├── openclaw.plugin.json
├── README.md
├── tsconfig.json
├── src/
│   └── index.ts
└── test/
    └── index.test.ts
```

它们的职责分别是：

- `package.json`
  - 包名、版本、依赖、构建脚本、`openclaw.extensions` 入口
- `openclaw.plugin.json`
  - 给 OpenClaw 识别插件、读取元信息、校验配置
- `src/index.ts`
  - 插件主实现，通常导出默认插件对象
- `test/index.test.ts`
  - 插件测试
- `README.md`
  - 该插件自身的安装和使用说明

## 3. 用仓库脚本生成新插件

仓库已经提供了一个生成脚本：

```bash
pnpm init:plugin <plugin-name> [--scope @scope] [--description "..."] [--author "..."]
```

例如：

```bash
pnpm init:plugin openclaw-hello-world \
  --description "Hello World plugin for OpenClaw." \
  --author "zhangsan"
```

执行后，脚本会：

- 基于 `templates/current-plugin-package-standard` 复制一份标准模板
- 在 `packages/<plugin-name>` 下创建新插件目录
- 自动替换包名、展示名、描述、作者等字段

生成完成后你会得到一个可以直接继续开发的插件骨架。

## 4. 生成脚本背后做了什么

根目录脚本是 [scripts/init-plugin-package.mjs](./scripts/init-plugin-package.mjs)，它会：

- 将输入的插件名规范化为 kebab-case
- 生成 package 名，例如 `openclaw-hello-world`；如果显式传入 `--scope @scope`，则生成 `@scope/openclaw-hello-world`
- 从模板目录复制文件
- 替换模板中的占位符

模板目录位于：

- [templates/current-plugin-package-standard](./templates/current-plugin-package-standard)

如果你想调整以后所有新插件的默认结构，可以直接改这个模板。

## 5. 生成后第一步改哪些地方

建议按下面顺序修改：

1. 修改 `package.json`
2. 修改 `openclaw.plugin.json`
3. 实现 `src/index.ts`
4. 补充 `test/index.test.ts`
5. 更新插件自己的 `README.md`

重点关注这些字段：

- `package.json`
  - `name`
  - `version`
  - `description`
  - `author`
  - `yuyanId`
  - `openclaw.extensions`
- `openclaw.plugin.json`
  - `id`
  - `name`
  - `description`
  - `configSchema`

## 6. `src/index.ts` 一般怎么写

最常见的做法是导出一个默认插件对象，并在 `register(api)` 中注册能力。

简化示意：

```ts
import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/core';

const plugin = {
  id: 'openclaw-hello-world',
  name: 'OpenClaw Hello World',
  description: 'A demo plugin.',
  configSchema: {
    type: 'object',
    additionalProperties: false,
    properties: {},
  },
  register(api: OpenClawPluginApi) {
    api.logger.info?.('hello plugin loaded');
  },
};

export default plugin;
```

如果你的插件是更复杂的 channel、tool、hook、provider 或 setup 插件，可以在这里扩展更多导出和注册逻辑。

## 7. `openclaw.plugin.json` 有什么用

这是 OpenClaw 的插件元数据文件。它的作用不是替代代码，而是让宿主在不执行插件源码时也能：

- 识别插件 id 和名称
- 展示插件描述
- 读取配置 schema
- 做安装或校验前的静态判断

一个最小示例：

```json
{
  "id": "openclaw-hello-world",
  "name": "OpenClaw Hello World",
  "description": "A demo plugin.",
  "configSchema": {
    "type": "object",
    "additionalProperties": false,
    "properties": {}
  }
}
```

## 8. 开发时常用命令

先安装依赖：

```bash
pnpm install
```

只构建某个插件：

```bash
pnpm --filter openclaw-hello-world build
```

只测试某个插件：

```bash
pnpm --filter openclaw-hello-world test-local
```

只跑某个插件的 lint：

```bash
pnpm --filter openclaw-hello-world lint
```

如果你更习惯在插件目录里执行，也可以：

```bash
cd packages/openclaw-hello-world
pnpm build
pnpm test-local
```

## 9. 构建产物是什么

本仓库当前模板默认使用 `tshy`，输出 ESM 和 CommonJS 两套产物。

通常构建后会生成：

- `dist/esm/index.js`
- `dist/esm/index.d.ts`
- `dist/commonjs/index.js`
- `dist/commonjs/index.d.ts`
- `dist/esm/package.json`
- `dist/commonjs/package.json`
- `dist/package.json`

这能保证插件既可以被现代 ESM 环境加载，也能兼容 CommonJS 使用方式。

## 10. 怎么判断插件算“做完了”

至少满足下面几项：

- 能通过 lint
- 能通过测试
- `openclaw.plugin.json` 和代码导出的插件信息一致
- `README.md` 说明了安装和配置方式
- `package.json` 的导出、依赖、构建脚本完整

建议在提交前执行：

```bash
pnpm --filter <your-plugin> lint
pnpm --filter <your-plugin> test-local
pnpm --filter <your-plugin> build
```

## 11. 一个推荐的新插件开发流程

1. 在根目录执行 `pnpm install`
2. 执行 `pnpm init:plugin <plugin-name>`
3. 修改新插件的 `package.json`、`openclaw.plugin.json`
4. 在 `src/index.ts` 实现插件逻辑
5. 在 `test/index.test.ts` 编写测试
6. 运行 `pnpm --filter <package-name> lint`
7. 运行 `pnpm --filter <package-name> test-local`
8. 运行 `pnpm --filter <package-name> build`
9. 补齐插件 README 后再提交

## 12. 对模板做统一升级

如果你发现新插件总要重复改同一套东西，优先考虑直接更新模板：

- [templates/current-plugin-package-standard/package.json](./templates/current-plugin-package-standard/package.json)
- [templates/current-plugin-package-standard/openclaw.plugin.json](./templates/current-plugin-package-standard/openclaw.plugin.json)
- [templates/current-plugin-package-standard/src/index.ts](./templates/current-plugin-package-standard/src/index.ts)
- [templates/current-plugin-package-standard/test/index.test.ts](./templates/current-plugin-package-standard/test/index.test.ts)
- [templates/current-plugin-package-standard/README.md](./templates/current-plugin-package-standard/README.md)

这样后续生成的插件都会自动带上新的默认标准。

## 13. 总结

这个仓库的最佳实践可以概括成一句话：

先用 `pnpm workspace` 管理插件，再用仓库模板和脚本统一生成插件骨架，最后按单插件维度开发、构建和验证。

如果你接下来要继续完善这份指南，自然的下一步是：

1. 我帮你再补一节“如何开发 channel 类型插件”的专门章节
2. 我帮你把这份 README 再补成“新手 5 分钟上手版”
