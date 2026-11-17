# @avernet-assets/task-panel

任务副屏前端组件包，为新产品 frontend-nextgen 和旧产品提供同一套任务展示与交互能力。组件独立构建、通过 npm 发布，宿主在浏览器中加载 UMD JS 和 CSS，不需要分别维护一套任务副屏代码。

## 提供的能力

- **任务查看**：任务概览、执行图、节点详情、执行轨迹和状态刷新。
- **产物查看**：汇总任务产物，支持预览和下载。
- **任务恢复**：支持会话消息卡片调起，也支持进入会话时查找任务关联和历史消息中的副屏声明，处理卡片不在当前分页的情况。任务 Tab 按发起先后排列，默认聚焦最新任务；恢复不覆盖用户已选择的任务。排序优先使用任务创建时间，缺少时间元信息时按关联索引的逆序稳定恢复，不使用任务更新时间推断发起时间。
- **任务导航**：多任务切换、子任务下钻，以及定位节点对应的 Bot 或群会话。

点击节点、切换查看位置和返回概览属于浏览操作，不应因此向 Bot 发送消息。

## 与宿主的职责划分

| 本组件负责 | 宿主负责 |
| --- | --- |
| 任务视图、任务数据解析与刷新 | 登录身份、认证请求和后端地址 |
| 任务选择、下钻状态和恢复流程 | 会话 SDK、副屏标签页的挂载与关闭 |
| 产物列表和预览交互 | 带身份的文件读取与下载 |
| 历史消息中的任务声明识别 | 独立的历史分页读取、会话跳转与消息定位 |

组件通过 `TaskPanelHost` 调用宿主能力，不依赖某个产品的 SDK、路由、请求库或 store。宿主不直接引用组件内部实现，也不从组件借用通用富文本或文件工具。

## 构建产物和运行要求

| 文件 | 用途 |
| --- | --- |
| `dist/recovery.umd.js` | 轻量恢复内核，全局导出 `taskPanelRecovery`，不依赖 React、DOM 或 CSS |
| `dist/index.umd.js` | 按需加载的任务视图，全局导出 `taskPanel` |
| `dist/task-panel.css` | 副屏样式，与 JS 使用同一发布版本 |
| `dist/public-api.d.ts` | 宿主接入所需的 TypeScript 类型 |

- 本地开发与测试使用 Node.js 22 或更高版本。
- 任务 UI 运行时由宿主提供 React 18 和 ReactDOM 18；恢复内核不需要这两个依赖。
- 类型通过 `import type` 从 `@avernet-assets/task-panel` 引用；运行代码通过 UMD 加载，不将包内实现编译进宿主。

## 本地开发与集成测试

在本包目录执行：

```bash
npm ci
npm run build
npm run serve
```

静态服务默认监听 `127.0.0.1:43820`。新老宿主的开发配置通过代理加载：

```text
/task-panel/recovery.umd.js
/task-panel/index.umd.js
/task-panel/task-panel.css
```

接着单独启动宿主前端，选择需要连接的业务后端环境。旧产品沿用 `tnpm run devs:pre`；frontend-nextgen 沿用自身的后端连接配置。

注意：

- 开发模式优先使用本地资产，不请求线上 manifest，也不需要先发布 npm 包。frontend-nextgen 在本地资源不可用时查询 npm 的 `latest` 稳定版本，再加载该版本的远端资源；同一页面内的恢复入口、UI 和 CSS 固定使用同一版本，刷新页面后重新查询。查询失败或资源尚未同步到 CDN 时明确报错，不静默回退旧版本。
- 验证尚未发布的本地修改时，静态服务必须保持运行；它不代理任务业务接口，也不负责启动宿主或后端。
- 修改组件代码后重新运行 `npm run build`，再刷新宿主页面。`npm run serve` 本身不监听源码变更。
- 本地联调验证组件与业务数据的交互，不代表线上 manifest 和 CDN 加载链路已经验证通过。

如需脱离产品检查 UMD 的基本交互，先构建，再运行：

```bash
node scripts/serve-smoke.mjs
```

打开输出的地址即可使用模拟宿主检查恢复、节点交互和关闭行为。该页面不替代真实登录态下的新老产品集成测试。

## 线上加载与版本更新

宿主的非开发模式通过已登录请求读取：

```http
GET /api/v1/collaboration/tasks/manifest
```

宿主从响应的 `data.bundles` 中找到 `name=taskPanel` 的 JS 地址：

- 进入会话时只加载同目录的 `recovery.umd.js`，由共享控制器查询 `by_session`、分页查找历史任务卡片并执行任务列表兜底。没有任务时不加载完整 UI 或 CSS。
- 发现任务或显式打开任务后，SDK 挂载任务视图，才加载 `index.umd.js` 和 `task-panel.css`；产物等显式任务页面同样按需加载。
- 两个入口来自同一源码包、同一发布目录并校验版本一致，宿主不另写一套恢复规则，不隐式回退到完整 UI 或其他发布版本。
- 恢复独立于聊天主链路，保留分页预算、继续查找、取消及局部错误重试；任务组件失败不得阻断普通会话。群历史扫描沿用当前 Bot/Human 查看身份，切换视角后取消旧扫描。

加载后检查 `hostApiVersion` 和所需导出是否兼容；不在宿主代码中写死组件版本。

组件发布完成、后端接口部署就绪后，通过以下接口保存完整配置：

```http
POST /api/v1/collaboration/tasks/manifest
Content-Type: application/json
```

```json
{
  "schema_version": 1,
  "bundles": [
    {
      "name": "taskPanel",
      "type": "url",
      "url": "https://cdn.jsdelivr.net/npm/@avernet-assets/task-panel@1.0.4/dist/index.umd.js"
    }
  ]
}
```

示例中的版本应替换为实际发布并验证的版本。GET 和 POST 使用服务端当前环境；POST 要求登录，不额外要求运营权限。配置存储复用现有 system-config 的 `task / panel`，无需手动创建分类。

更新配置后，用户完整刷新页面即可加载新版本，无需为了修改资产版本重新构建宿主。已打开页面不热切换 UMD；加载失败时显示错误，不静默回退到其他版本。回滚时将配置恢复为上一版验证过的资源地址，再刷新页面。

**宿主部署前，先发布包含两个入口的新组件版本，并把 manifest 指向该版本，验证三个资产均可访问。** 仅含 `index.umd.js` 的旧发布不支持轻量恢复入口；宿主不会为此回退到完整 UI。回滚新组件时需同步回滚依赖轻量入口的宿主，不能混用发布目录。

**npm 发布、后端部署和 manifest 配置是三个独立步骤。** 完成发布后仍需在目标环境检查 manifest、JS/CSS 加载，以及新老产品的卡片调起、历史恢复、节点导航和产物访问。

版本升级、首次发布、Trusted Publisher 和 tag 发布流程见 [发布指南](PUBLISHING.md)。

## 宿主接入要点

主要运行时导出：

| 导出 | 用途 |
| --- | --- |
| `TaskLoopView` | 完整任务副屏 |
| `TaskArtifactsView` | 任务产物视图 |
| `GroupSessionView` | 群会话视图 |
| `createTaskPanelController` | 创建任务恢复、选择和导航控制器 |
| `parseTaskPanelDeclarations` | 识别消息中的任务副屏声明 |
| `TaskPanelHostContext` | 为共享视图提供宿主能力 |
| `hostApiVersion` / `assetVersion` | 接口兼容性检查与资产版本诊断 |

`taskPanelRecovery` 只导出 `createTaskPanelController`、`hostApiVersion` 和 `assetVersion`；视图及宿主 Context 仍由完整 `taskPanel` 入口提供。两个入口复用同一控制器源码，宿主只创建一个控制器实例。

接入时应遵守：

1. 为当前登录工作区创建并复用控制器，不为每个任务标签页重复创建。进入会话时调用 `enterConversation()`；消息卡片调起时使用 `openTask()`。SDK 已挂载的任务通过 `adoptPresentedTask()` 确认，避免重复打开标签页。
2. 历史恢复使用独立分页游标，不推进聊天列表的可见分页。恢复状态为 `incomplete` 时允许用户通过 `continueRecovery()` 继续查找，不将未扫描完视为没有任务。
3. 宿主同时处理主动关闭和 SDK 标签页关闭事件；退出登录或切换用户时调用 `dispose()`，取消未完成的恢复与导航。
4. 只从可信配置加载资产。消息中的任务声明只作为数据解析，不执行其中代码，也不允许消息选择脚本来源。持久化消息参数中不存放回调、Cookie 或 Token。

## 验证命令

```bash
# 完整验证：类型、单元测试、构建、UMD、HTTP 交付及导入边界
npm run verify

# 打包预检；prepack 会再次执行完整验证
npm pack --dry-run
```

日常开发也可分别运行 `npm test`、`npm run typecheck` 和 `npm run build`。完整验证只覆盖本包；宿主的认证、SDK 接线与真实会话交互仍需分别验证。
