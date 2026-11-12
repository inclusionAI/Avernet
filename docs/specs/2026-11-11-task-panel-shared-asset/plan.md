---
status: approved
owner: task-module
---

# Plan：任务副屏单源资产与双宿主接入

## 1. Approach 与权威边界

将现有任务副屏迁入 `apps/frontend-nextgen/assets/task-panel/` 独立资产子工程（C6 用户确认的新归属，位于宿主 src 之外），提供单一 UMD 入口和受版本约束的宿主接口；两个产品仅提供 SDK、认证、路由、消息访问、文件访问和稳定挂载适配。共享实现覆盖展示、请求映射、轮询、声明识别、恢复、任务切换和导航编排。状态只在共享控制器持有，不能在宿主 store 再维护第二份任务状态机。

本变更跨 Backend 资产与前端宿主，故实施三件套位于根 `docs/specs`，而非某一个前端的私有 specs。

相关约束：[架构宪章](../../arch/arch.rules.md)、[开发规则](../../agents/development.md)、[任务领域导航](../../../apps/backend/src/agentclaw/community/core/task/README.md)。既有受保护合同：[Bot S1–S6](../../../apps/backend/specs/2026-09-16-distributed-relay-execution/bot-s1-s8-functional-contract.md)、[Relay 端到端](../../../apps/backend/specs/2026-09-23-task-cli/end-to-end-relay-design.md)、[Task CLI](../../../apps/backend/specs/2026-09-23-task-cli/design.md)。不以早期 task README 或过期 CodeGraph 索引覆盖这些合同。

- **已经用户确认的方向**：任务模块持有资产，参照 BCS 同仓独立包，保留两种调起体验。
- **本次新增宿主接口**：已随此次实施方向批准；它不是另一个任务执行协议。
- **不得改变**：HTTP 路径、领域对象、已有任务服务签名、上报事件、执行状态迁移、root instruction 转发语义。
- **旧宿主写入边界（已确认）**：用户于 2026-11-11 明确旧产品改动归 `open-claw` 仓库；仅在那里实现并验证旧宿主 adapter，不修改 OCB checkout 或 assembled output。

## 2. Affected components 与源码迁移

```text
# 唯一任务资产：新增子工程；不是 Python 包内的 UI，也不是新服务
apps/frontend-nextgen/assets/task-panel/
  package.json / package-lock.json / tsconfig.json / vite.config.ts
  src/index.ts                     # UMD 导出 TaskLoopView、控制器、接口版本
  src/contracts/                   # 宿主接口、声明、轻量消息、资源描述
  src/panel/                       # 现有资产实现迁移，非复制
  src/data/                        # task client、DTO mapper、共享纯函数
  src/controller/                  # 恢复、切换、导航、每任务状态
  src/history/                     # 声明解析、独立分页发现
  src/styles/                      # 自包含、作用域限定的资产样式
  test/ / scripts/ / README.md / PUBLISHING.md
```

当前 [assets/TaskPanel](../../../apps/frontend-nextgen/src/assets/TaskPanel/index.ts#L1) 的 43 个文件逐项列入迁移清单。资产内部只保留功能实现；SDK 注册、产品路由、登录跳转留在宿主。已有 [GroupDrillDown](../../../apps/frontend-nextgen/src/assets/TaskPanel/GroupDrillDown.tsx#L14)、[Fetcher](../../../apps/frontend-nextgen/src/assets/TaskPanel/TaskPanelFetcher.tsx#L11)、[artifact preview hook](../../../apps/frontend-nextgen/src/assets/TaskPanel/hooks/useTaskArtifactPreview.ts#L2) 的 app/SDK imports 必须剥离。

```text
# nextgen：预期新增/收敛的宿主代码
apps/frontend-nextgen/src/services/tasks/panel/
  taskPanelAssetLoader.ts / taskPanelRuntime.ts
  nextgenTaskPanelHost.ts / taskPanelHistoryAdapter.ts
  taskPanelArtifactAdapter.ts / taskPanelConversationAdapter.ts
apps/frontend-nextgen/src/components/Workspace/TaskPanelHost.tsx
apps/frontend-nextgen/src/extensions/empty.ts                 # 注册稳定宿主入口
apps/frontend-nextgen/src/assets/TaskPanel/                    # 已删除，不保留 re-export 壳
```

旧产品在已确认的 `open-claw` 仓库提供同等职责的 adapter，使用同一资产版本，不复制任务 mapper/controller/parser。该仓库源码位于仓库根 `src/`，而非 `apps/frontend/src/`；适配接线范围为 `src/components/Panels/`、`src/pages/Assistant/`、`src/pages/GroupChat/` 以及对应服务、配置和测试。具体调用链将在该权威仓重新核验，不能沿用 OCB 文件行号。

## 3. Data model 与现有消息合同

数据库和任务执行模型无变更。继续读取现有 dashboard/list/by_session 和既有产物、历史消息接口。新增唯一已认证的 `GET /api/v1/collaboration/tasks/manifest`，不增加 OpenAPI 镜像或后端静态资产路由。manifest 读取既有 system-config 的 `task / panel` 配置，写入复用管理员 config API，不扩展 bool-only task settings。

```tsx
// 现有声明形状保持不变：services/tasks/taskPanelMessage.ts:19-32
<AixUI type="panel" component="taskPanel.TaskLoopView"
  tab='{"id":"task-<taskId>","title":"任务标题","closable":true,"defaultWidth":420}'
  params='{"taskId":"<taskId>","apiBaseUrl":"","bcsBaseUrl":""}' />
// 现有 task-loop 历史别名也映射同一宿主入口；不新增 legacy 协议。
```

声明中的 tab 宽度保留现有用户记忆逻辑，不因示例默认值覆盖实际值。params/payload 格式差异只在 SDK adapter 归一化，不能把回调、登录凭证或宿主运行期对象序列化落库。

## 4. 新宿主接口：实施评审面

共享资产依赖声明的接口，不 import 任一聊天 SDK。下面的 types 均是资产拥有的接口；详细 DTO 复用迁移后的现有合同，不重复造领域对象。

```ts
// apps/frontend-nextgen/assets/task-panel/src/contracts/conversation.ts（new）
export interface ConversationRef {
  sessionId: string;
  kind: 'bot' | 'group' | 'friend' | 'unknown';
  botId?: string | null;
  groupId?: string | null;
}
export interface ConversationTarget extends ConversationRef {
  startTime?: number | null;
  endTime?: number | null;
}
export type NavigationOutcome =
  | { status: 'ready' }
  | { status: 'denied' | 'unavailable' | 'cancelled'; reason: string };
```

```ts
// apps/frontend-nextgen/assets/task-panel/src/contracts/history.ts（new）
export interface HistoryMessage { id: string; content: string; timestamp: number; }
export interface HistoryPage {
  // 完整、非流式历史消息；统一为新到旧，adapter 保证次序，不重放卡片。
  messages: readonly HistoryMessage[];
  nextCursor: string | null; // opaque；null 仅表示确认无下一页
}
export interface HistoryReader {
  readPage(conversation: ConversationRef, cursor: string | null,
    signal: AbortSignal): Promise<HistoryPage>;
}
```

```ts
// apps/frontend-nextgen/assets/task-panel/src/contracts/transport.ts（new）
export interface TaskPanelTransport {
  // 任务路径、参数和 DTO 校验由资产 client 唯一持有；宿主处理认证。
  requestJson(path: string, signal: AbortSignal): Promise<unknown>;
}
export interface TaskArtifactReader {
  // 引用类型来自迁移后的 contract；宿主只适配既有文件基础能力。
  readContent(artifact: TaskArtifactView, owner: ArtifactOwner,
    signal: AbortSignal): Promise<Blob>;
  download(artifact: TaskArtifactView, owner: ArtifactOwner,
    signal: AbortSignal): Promise<void>;
}
```

```ts
// apps/frontend-nextgen/assets/task-panel/src/contracts/host.ts（new；结构草案）
export interface TaskPanelHost {
  getViewer(): { userId: string } | null;
  history: HistoryReader;
  transport: TaskPanelTransport;
  artifacts: TaskArtifactReader;
  getConversation(): ConversationRef | null;
  navigate(target: ConversationTarget, signal: AbortSignal): Promise<NavigationOutcome>;
  captureOrigin(): unknown; // 由宿主管理的 opaque handle，禁止共享层解析 URL
  restoreOrigin(origin: unknown, signal: AbortSignal): Promise<NavigationOutcome>;
  present(tab: TaskPanelDeclaration, source: OpenSource): void;
  preservePanel(preserve: boolean): void;
  recordInteraction(interaction: TaskPanelInteraction): void;
}
```

opaque origin 只在同一宿主运行期使用，不入 params；实施时类型化为宿主提供的不透明 token，不对 unknown 进行猜测和强制转换。`navigate` 返回 ready 表示会话已 hydration，且该次操作已尝试时间窗定位；无法命中消息不等于会话导航失败。调用取消后不得再滚动。

```ts
// apps/frontend-nextgen/assets/task-panel/src/controller/types.ts（new）
export type OpenSource = 'explicit' | 'message' | 'recovery';
export type RecoveryState =
  | { status: 'idle' | 'searching' | 'not-found' | 'opened' }
  | { status: 'incomplete'; pagesRead: number; resumeCursor: string }
  | { status: 'error'; reason: string };
export interface TaskPanelController {
  enterConversation(conversation: ConversationRef): void;
  openTask(declaration: TaskPanelDeclaration, source: OpenSource): void;
  ingestMessages(messages: readonly HistoryMessage[]): void;
  continueRecovery(): void;
  leaveConversation(): void;
  dispose(): void;
  // 同一个订阅快照驱动 UI；具体 snapshot 类型随现有 nav/attention 合同迁移。
}
```

宿主不另存 activeTask/nav/pending/recovery 的可变副本；确有历史消费者时，以只读 selector/facade 临时适配并在此工作内收敛。controller 由稳定应用宿主持有，不由每个任务组件实例创建。断开登录时 dispose，新的 viewer 不能沿用旧数据或请求。

```ts
// apps/frontend-nextgen/assets/task-panel/src/index.ts（new；唯一 UMD 入口）
export const hostApiVersion = 1;
export const assetVersion = '0.1.0'; // 从 package 版本构建注入，禁止双写漂移
export { TaskLoopView } from './panel/TaskLoopView';
export { createTaskPanelController } from './controller/createTaskPanelController';
export { parseTaskPanelDeclarations } from './history/parseTaskPanelDeclarations';
export type * from './contracts';
```

以上接口是本次评审对象，不是既有 TaskService 方法签名的变更。生产 UI、controller 都来自同一 UMD。编译期类型入口可以独立导出，但不得将带可变状态的运行时实现同时打进宿主和 UMD。

## 5. 恢复编排：两个触发入口，一个提交点

[当前 bootstrap](../../../apps/frontend-nextgen/src/hooks/useSessionTaskPanelBootstrap.ts#L118) 和 [已载入消息重建](../../../apps/frontend-nextgen/src/pages/Workspace/Chat/hooks/sidePaneContent.ts#L35) 不等于全历史扫描。新增行为按以下顺序，提交前再次检查 viewer、session epoch、activeTask 和最新显式 intent。

```text
# controller/recoverConversation.ts（new）
进入会话 → 等宿主可用，不阻塞聊天首屏
  → 显式 task intent？按 intent 打开
  → 已有活动任务？不抢焦点
  → by_session 有有效关联？按现有第一关联/标题补全规则恢复
  → 无关联？独立读取历史分页，识别最新有效任务声明
  → 历史确认耗尽且未命中？沿用 list 的同会话最新任务兜底
  → 只通过 openTask 提交；晚到恢复永远不覆盖显式选择
```

索引失败和空结果均允许进入历史路径，但保留可诊断来源；权限失败不得换用任务 owner 或其他身份重试。索引有效时不为“必须扫描”而全读历史。list 补标题失败不得阻止已有 taskId 打开。

历史扫描约束（新增预算需此次批准）：

- 每次至多一个分页请求；每页至多 50 条；自动一批至多 20 页。达到上限是 incomplete，而非 not-found；宿主副屏入口提供“继续查找”动作，使用保存的游标继续。
- 页和页内声明都按新到旧处理，找到最近的有效声明即可停止自动恢复；不为枚举所有任务扫描全历史。多任务切换继续使用已有任务关联/列表，而非依赖扫完历史。
- 不调用 provider.loadMoreHistory，不改变聊天分页游标、消息数量、滚动位置或 live/hydration 模式。
- 群查询使用独立 before 游标及正确 view identity；Bot 查询保留真实 Bot、owner、friend/runtime 参数。游标来自原始页而非过滤后的卡片数量；空内容过滤不能伪装为结束。
- null 游标仅表示确定耗尽；重复游标、未推进、分页错误形成明确结果，不能无限循环或假装无任务。
- 中断会话、关闭用户显式取消的恢复、登出与卸载时取消；即使宿主底层不能 abort，也用 epoch/request token 丢弃过期结果。
- 卡片显式调起、消息渲染调起和恢复均调用同一 openTask；保持既有 tab identity，确保一个任务一个实例/轮询。

## 6. 安全的声明识别

```ts
// history/parseTaskPanelDeclarations.ts（new）
export function parseTaskPanelDeclarations(content: string): readonly TaskPanelDeclaration[];
// 只接受现有任务组件键及历史别名；taskId 是必填非空字符串。
// 解析完整标签；属性和 JSON 不得因引号内的 '>' 被错误截断。
// 不 eval/new Function，不执行卡片 script，不加载消息提供的任意 cdn/entry。
```

保留现有单/双引号、无引号旧 type、跨行属性、params/payload 和闭合形式的真实 fixture；错误 JSON 和不完整流式标签不应抛出整会话错误。语义按现有声明合同处理；在代码块/示例中的标签不调起面板。非任务副屏继续走原 SDK 处理，不能因任务抽取破坏通用消息重建。

## 7. 迁移清单与旧实现删除

| 原位置与入口 | 迁移/修改方式 | 对应任务 |
| --- | --- | --- |
| `src/assets/TaskPanel/**`（43 文件） | UI/mapper/hooks 移入资产；SDK glue 留宿主；共享工具从 app import 改成资产自包含或注入 | 2、3 |
| `src/shared/taskStatus.ts`、`services/workspace/sessionFileUtils.ts` 的被用纯函数 | 识别唯一 owner；只迁移所需纯函数并更新调用，或暴露无宿主依赖公共入口；禁止复制近似实现 | 3 |
| `hooks/taskPanelNavBinding.ts`、`useTaskSwitcherTasks.ts`、`useSessionTaskPanelBootstrap.ts`、`useSessionTaskPanelTab.ts` | 规则迁移 controller，宿主 hook 只负责生命周期/订阅适配 | 4、5、7 |
| `stores/taskSidePanelStore.ts`、`taskSessionAssociationStore.ts`、`taskUpdateAttentionStore.ts`、`taskConversationNavigationStore.ts` | 单一共享状态；宿主 URL/hydration 部分保留 adapter，不保留双写状态 | 4、7 |
| `extensions/empty.ts:36-68,90-107` | 注册 taskPanel 专属稳定 wrapper；动态加载后注入 host/controller | 7 |
| `services/tasks/taskPanelMessage.ts:19-32` | 声明形状不变；生成器使用共享纯合同，不改 root execution instruction | 5、7 |
| `pages/Workspace/Chat/hooks/sidePaneContent.ts` | 任务声明移交 controller；其他副屏维持原行为 | 5、7 |
| chat/group 的 header hooks | 同一恢复 controller；保留侧栏互斥和开合 UI 语义 | 7 |
| `pages/Workspace/Chat/hooks/taskSessionNavigation.ts`、`services/tasks/taskConversationNavigation.ts` | 任务规则移出；真实 URL/hydration/定位留 adapter | 4、7 |
| `components/Workspace/PersistentChatLayoutPanelHost.tsx`、`ChatLayoutPanelSlot.tsx`、`persistentTaskPanelStaleRelease.ts` | 稳定挂载、SDK bridge 属宿主；保活决策消费共享导航结果 | 7 |
| `components/Workspace/TaskPanelSwitcherSlot.tsx`、`pages/MyTask/components/MyTaskDrawers.tsx` | 消费同一共享视图和切换状态，不能留下第二份面板实现 | 7 |
| `config/config.ts`、`package.json`、`tsconfig.json`、`jest.config.*` | 类型入口、版本化同源镜像脚本与测试解析接线，更新实际变化的锁文件 | 6、7 |
| `test/assets/TaskPanel/**` 及对应 hook/store 测试 | 与实现一起迁移共享测试；保留 SDK adapter 专测 | 3–7、9 |

表中 `src/` 均相对于 nextgen；当前证据参见 [research.md](research.md)。不通过全局重命名改不相关模块；必要的临时 re-export 是单源引用，不能带分支实现，最终扫描证明无旧实现副本。

## 8. 打包、加载与依赖

参考 BCS lib 模式，具体依赖版本从现有公开锁文件选取、经复现验证后锁定；不使用私有 registry、宿主的 node_modules 或开发机绝对路径作为构建依赖。

```ts
// apps/frontend-nextgen/assets/task-panel/vite.config.ts（new；sketch）
build: {
  lib: { entry: 'src/index.ts', name: 'taskPanel', formats: ['umd'],
    fileName: () => 'index.umd.js' },
  rollupOptions: { external: ['react', 'react-dom'],
    output: { globals: { react: 'React', 'react-dom': 'ReactDOM' } } }
}
```

```jsonc
// apps/frontend-nextgen/assets/task-panel/package.json（new；sketch）
{
  "name": "@avernet-assets/task-panel", "version": "0.1.0",
  "main": "./dist/index.umd.js", "types": "./dist/index.d.ts",
  "files": ["dist", "README.md", "PUBLISHING.md", "LICENSE"],
  "peerDependencies": { "react": ">=18 <19", "react-dom": ">=18 <19" },
  "scripts": { "build": "vite build && tsc --emitDeclarationOnly",
    "verify": "npm run typecheck && npm test && npm run build && npm run test:umd && npm run scan:public" }
}
```

新增依赖限现有组件真实所需的公开库及构建测试工具。实现时记录包体 gzip，重依赖懒加载，避免 SDK/antd 随资产引入。不能为方便打包而删除原有预览、安全清洗或 Markdown 行为。

正式构建的两宿主只读 `GET /api/v1/collaboration/tasks/manifest` 的 `data.bundles`，选择
`name=taskPanel` 的完整 JS URL，并从同目录加载 `task-panel.css`。配置在 system-config
按服务端环境隔离；只允许管理员写入固定 HTTPS URL。宿主不保存包版本 pin、不注入
资源地址变量。加载按页面去重，校验 hostApiVersion 和必要导出，错误可重试但不静默
回退或在活跃页面替换已求值的全局资产。配置更新在下次整页刷新生效。

本地开发直接加载 `/task-panel/index.umd.js` 与同目录 CSS，不请求 manifest。
独立资产 server 将固定本地别名指向当前 dist，两个宿主仅代理 `/task-panel` 到 loopback；
其余业务 API 仍走选定的预发后端。先验证未发布
资产，再合并、发布组件并进行真实预发 manifest/CDN 验证；不隐含生产发布。

- 公共类型仅经 `src/public-api.ts` 的 type-only 包入口提供，运行时擦除；本地类型检查可以指向拥有方源码入口，生产构建不需要 backend 资产输入。普通 RichContent、文件预览和一般状态工具由宿主独立拥有，不依赖任务资产。
- taskPanel 稳定注册不得依赖“先进过群聊”，不得被 Bot 渲染库配置覆盖或随 CDN map clear 消失。
- loader 必须真正注入 host：不能直接打开 SDK CDN 分支而绕过任务 wrapper。taskPanel 的消费解析在专属入口完成，不在通用 UmdPanel 增加大量任务分支。
- 同 URL 加载去重，校验导出与 hostApiVersion；已有全局 taskPanel 与配置不一致则明确拒绝混用，不在活动页面热换全局版本。
- script 加载失败与 render 失败都有边界和重试；不自动回退到第二实现。现有 CSP/nonce、CSS 和 portal 兼容在两个真实宿主验证。
- 不运行 npm publish、不更新生产 manifest、不提交推送；本工作完成本地构建、pack、双宿主接线与验证，正式分发单独授权。

## 9. 导航与交互保持

```text
# controller/navigation.ts（new）
用户节点操作 → 建立本次导航 token → preservePanel(true)
  → host.navigate(target, signal) → hydration/消息定位结果
  → 当前 token 仍有效才更新状态；异常/取消释放本次保活
普通目录切换/登出/关闭 → 取消 token → 释放保活并清理相关恢复
```

同会话 no-op 也应返回 ready，不留永久 lease。回到任务根节点使用最早记录的宿主 origin；默认选中只选中，不导航。保留原有 select/drill 差别，不把所有动作合并成“选中并跳转”。interaction 使用既有 SDK 结构经 adapter 回传，并由原 flushContext/flushInteractions 链进入后续消息；不扩展 SDK action 为未经批准的执行命令。

## 10. Risks、取舍与 rollout

| 风险 | 处理 |
| --- | --- |
| 抽取范围较大、其他页面深层引用现有资产 | Task 1 建调用/迁移清单；类型和测试随迁；扫描旧实现重复与 app imports |
| UI 共享但编排仍双份 | controller 单源，两个真实 adapter 跑同一 conformance；host stores 不双写 |
| SDK props/CSS/portal 差异 | 两 SDK adapter 专测及浏览器检查；资产自包含样式，外壳样式归宿主 |
| 扫描无限分页、A→B 串场、晚恢复抢焦点 | 明确分页预算/继续、游标推进校验、每会话 epoch、提交前重验 intent |
| 旧产品访问路径不同或无权限 | 配置/transport 适配现有合法路径；不添加 alternate task HTTP、不冒充 owner |
| UMD 任意代码执行和包泄漏 | 只加载受信任配置、锁定版本和可用完整性校验；公共依赖、npm pack/扫描 |
| 旧宿主与历史调研 checkout 不同 | 已确认 open-claw 为权威源；重新核对调用链并在该仓验证，OCB 仅作上下文 |

备选：复制实现（拒绝）；iframe 新页面（增加另一套通信/登录协议，拒绝）；单 npm 包分别内嵌（可以单源但不满足优选的独立资产交付）；新建微前端平台（超范围）。

顺序：先接口及失败测试 → 单源资产 → nextgen 行为等价 → 旧宿主同资产接入 → 双宿主验证。开发阶段旧实现仅在切换前存在；合入结果不保留两份业务实现。两产品都通过才称完成，不把“UMD 能构建”当作功能完成。

## 11. Test strategy 与完成证据

```text
# apps/frontend-nextgen/assets/task-panel/test/（new）
contracts/host.conformance.test.ts          # 两宿主复用的语义合同
history/parseTaskPanelDeclarations.test.ts # 引号/换行/>/代码块/坏 JSON/旧别名
history/recovery.test.ts                   # 第 2 页命中、20 页暂停继续、游标不动
controller/focus.test.ts                   # 双入口、显式意图、过期响应、多任务
controller/navigation.test.ts              # 失败/取消/同会话/原会话恢复
panel/                                    # 迁移现有 UI/mapper/产物测试
umd-contract.test.mjs                      # 真构建、真导出、React external、无 SDK
architecture.test.mjs                      # 禁反向 app imports/旧实现副本/私有依赖
```

```bash
# 在实施后运行，当前尚未执行这些新资产命令
cd apps/frontend-nextgen/assets/task-panel
npm ci && npm run verify && npm pack --dry-run
# Python backend 启动/依赖保持不变；按真实变更范围运行已有 architecture gates。
cd ../../../frontend-nextgen
npm run ci
# 旧宿主在获批源目录安装、类型/测试/构建及同一 host conformance；记录真实目录和结果。
```

浏览器矩阵：新旧 × 单 Bot/协作群 × 卡片/分页恢复；覆盖刷新、多任务、节点跨路由无卸载、原会话恢复、产物三来源、登录/403、加载失败重试、后续消息包含 interaction。增加 fixture 使任务声明在当前页以外；测试不能用始终返回卡片的 mock 掩盖分页问题。

知识更新仅在实现验证后记录事实：资产 README/PUBLISHING、backend/nextgen app README 和任务 domain README 的导航；不改 CONTEXT 领域词汇，不写私人规则入 AGENTS。接口、模块职责和回滚步骤归资产文档，实施与发布状态归此 spec。所有变更文件检查行数；不放宽已有门禁。

## 12. 当前状态

本轮只完成研究、基线及三件套；业务实现未开始。已有基线 14 suites / 135 tests 通过，详见 [research.md](research.md)。需在 SDD 唯一评审点批准三件套与新宿主接口，后进入 implement。旧产品权威源已由用户确认为 open-claw，源码归属阻断已解除。

## 2026-11-11 清理收口

用户确认删除旧源码与测试目录。最终 SDK adapter/注册回到宿主；纯 helpers 与 rich-content 通过独立构建产物交付；共享 UI 测试迁到资产自身，宿主仅保留 SDK/产品端口集成测试。细目与验证见 tasks.md 的 Follow-up C1、test-migration-map.json、verification.md 后续小节；不以此改写其他未完成验收项。

## C3 — 功能边界整改（2026-11-12，用户批准）

本轮仅演进任务副屏前端资产与宿主契约，不修改任何受保护的后端任务执行协议。

- 共享 controller 是活动任务／节点选中／下钻／恢复阶段的唯一业务所有者。宿主已展示
  声明按当前登录 viewer 与会话绑定，早于 historyReady 的卡片同样参与仲裁；上一会话的
  裸 activeTaskId 不能当作当前会话已展示证据。
- UI 契约 `enterConversation(ref, presentedDeclaration?, { recover?: boolean })` 将会话接管
  与历史扫描解耦。任务导航先接管；普通历史未就绪时不发起恢复查询。导航 ready 不提前
  丢弃尚未接管的目标证据。初次资产失败后的重试重新初始化宿主绑定，而非继续空 scanner。
- 两宿主的认证失效均强制 dispose，并使晚到 load/controller/presenter/nav 失效；同 viewer
  快速重登也有 runtime generation 边界。已 dispose 控制器的命令、旧回调与重复 dispose
  不得影响后续实例。SDK 自身 onTabClose 和 ref.closeTab 走同一关闭确认；收起只取消导航。
- 宿主挂载层统一加载／可重置 React 渲染错误边界；所有辅助任务视图也参与身份换代。
  群聊 incomplete/error 的操作入口可继续，不复制恢复算法。
- 新公开 TaskArtifactsView 封装完整产物视图。宿主 Drawer 仅提供 open/close、尺寸、标题、
  portal、拥有者与 API 配置。UMD 不再导出内部 Fetcher/List/Mapper/switcher 组件。
- 任务列表 DTO 不兼容必须报错；旧宿主适配 Umi/Axios 及 Blob 错误体，成功 Blob 不窥探内容。
- 无生产调用的旧 switcher/nav hook 与重复策略删除；原有效断言按共享规则／宿主 SDK
  能力迁移，不用跳过或 expected-failure 作为验收。原始页面与宿主 UI 容器不整体迁移。


## C4：旧产品宿主接入范围收敛

通用 UMD 渲染器不依赖 task 模块。task 专属 `TaskPanel/UmdAdapter.tsx` 在注册处接管 `umd`，仅对已支持的任务声明交给任务 Slot，其他声明及事件原样委托给原 `UmdPanel`。两个直接 task 类型继续由 SDK registry 注册；没有新增注册协议。保留 Bot/manifest 的 taskPanel 覆盖保护，不以减少 diff 为由移除可信资产限制。

已有非 task 文件只承担必要接线：`config/config.ts`、`src/app.tsx`、`src/components/Panels/bootstrap.ts`、`src/components/Panels/registerBotCdnPanels.ts`、单聊页面、群聊页面、`tsconfig.json`。共享类型解析保留一个 task 专属路径映射；删除本次增加的三个 SDK 映射和无关格式化。当前未发布声明包，因此开发期类型检查仍要求相邻 Avernet checkout；运行时只加载独立 UMD/CSS。这一限制没有通过复制类型、生成宿主资产或增加不可安装的发布依赖掩盖。

## C5：任务副屏发布接线

按 BCS panel 的已有发布流程，task 包使用 `task-panel-v<version>` 标签触发独立
GitHub Actions，在确认提交属于 dev 且 tag 与包版本一致后，以 npm OIDC 发布
`@avernet-assets/task-panel`。prepack 强制完整资产 verify。CDN 固定版本目录包含
UMD/CSS，与两个宿主期望版本 pin 一致；发布不隐式修改宿主部署、BCS manifest
或 backend HTTP 协议。npm 首包和 Trusted Publisher 权限由 owner 另行确认，
本轮只实现发布接线，不进行任何远程发布操作。


## C6：共享资产目录迁移（2026-11-12，用户已批准）

独立前端包整体迁至 `apps/frontend-nextgen/assets/task-panel/`，而不是重新放回
宿主 `src/assets/TaskPanel`。源码位置不改变 npm 名称、版本、UMD/CSS、接口或双宿主
运行逻辑。更新发布 workflow 工作目录、repository.directory、CI cache/install 路径、
两宿主 type-only 映射、共享实现的集成测试入口与迁移清单。保留现有包版本，不触发发布。

边界由 owning README 规范与硬门禁共同保证：资产 AST 检查相对 import 不越出包、
禁宿主 alias/SDK 及未声明依赖；宿主检查归一化路径，禁实现源码 import 和运行时包
import，仅允许公共 type-only 入口。CI 将包单独复制到临时目录执行 npm ci + verify。
Docker context 排除该包，避免迁移后被宿主整目录 COPY 隐式带入。宿主也做无 assets
的隔离 production build，证明不需要任务源码构建输入。通用内容和 SDK 不归入资产。

本地资产服务从新目录启动，保留端口43820和当前0.1.1 URL；不修改已配置的预发业务
代理，不改账号、npm发布状态或任务执行协议。非 task README 保持无改动。


## C7：允许正式迭代分支发布

发布 tag 的提交可来自远端 dev，也可来自远端严格 `RELYYYYMMDD` 分支，正则
`^REL[0-9]{8}$`。禁止带任何后缀，不要求先合入 dev；明确选定发布提交并推送
到所选正式分支之后打版本 tag。CI 读取 fetch-depth0 的 origin 分支验证提交可达性，
不以排序推测最新分支，不放开未推送或仅存在于工作分支上的提交。tag 名称、版本
一致性和 OIDC/prepack 门禁不变。C7 替代前文 C5 中必须 dev 的历史描述。
