# 研究与验证记录

日期：2026-11-11。对象为本地工作树，不代表远端最新提交或已部署版本。
本轮开始时 Avernet HEAD 为 `8b190640da`；前一轮调研记录的 HEAD 为 `3471c81d67`，因此本轮重新核对调用链并运行定向测试。

## 已验证源码事实

| 事实 | 来源 |
| --- | --- |
| BCS 副屏是同仓独立 UMD 资产，React/ReactDOM external | [Vite 配置](../../../apps/bcs/assets/panel/vite.config.ts#L13)、[包定义](../../../apps/bcs/assets/panel/package.json#L2) |
| 任务副屏当前由 nextgen 本地注册，UMD 仅预留；有既存历史别名 | [注册入口](../../../apps/frontend-nextgen/src/assets/TaskPanel/index.ts#L40) |
| 当前资产目录含 43 个文件、7488 行，不是只抽取一个 TSX | `Path.rglob('*')` 对该目录的只读统计；没有单文件超过 1000 行 |
| 任务副屏轮询依赖应用的登录和信封处理 | [Fetcher](../../../apps/frontend-nextgen/src/assets/TaskPanel/TaskPanelFetcher.tsx#L11) |
| 产物访问依赖三类应用服务 | [useTaskArtifactPreview](../../../apps/frontend-nextgen/src/assets/TaskPanel/hooks/useTaskArtifactPreview.ts#L2) |
| 进入会话自动恢复当前为 by_session + list，不是已证实的历史全分页查找 | [bootstrap](../../../apps/frontend-nextgen/src/hooks/useSessionTaskPanelBootstrap.ts#L118) |
| 已有消息声明重建仅遍历调用方传入的已载入消息，用于 Monitor 重开 | [sidePaneContent](../../../apps/frontend-nextgen/src/pages/Workspace/Chat/hooks/sidePaneContent.ts#L35)、[调用处](../../../apps/frontend-nextgen/src/pages/Workspace/Chat/hooks/useChatHeaderPanels.ts#L172) |
| 群侧也复用消息重建和 bootstrap | [群头部接线](../../../apps/frontend-nextgen/src/pages/Workspace/hooks/useGroupChatHeaderPanels.ts#L19) |
| 单聊历史读取可指定 page/pageSize，但请求身份含 owner/friend 差异，不能改成只传 sessionId | [listMessagesPage](../../../apps/frontend-nextgen/src/services/workspace/botSessionService.ts#L485) |
| 群聊已有 before 时间戳分页，当前 provider 的分页器会修改 UI 分页状态 | [GroupChatHistoryPaginator](../../../apps/frontend-nextgen/src/services/workspace/groupChatHistoryPaginator.ts#L20) |
| 因此恢复扫描应持有独立游标，不能调用聊天 provider 的 loadMoreHistory 来推进 | 同上；并参见 [provider](../../../apps/frontend-nextgen/src/services/workspace/groupChatProvider.ts#L654) |
| 当前应用导航保活含宿主 URL 和 SDK 类型，不能直接搬到共享资产 | [导航 store](../../../apps/frontend-nextgen/src/stores/taskConversationNavigationStore.ts#L1)、[持久容器](../../../apps/frontend-nextgen/src/components/Workspace/PersistentChatLayoutPanelHost.tsx#L1) |
| 副屏 CSS 会匹配宿主 SDK 内部类，需要移回宿主 presentation 适配 | [CSS](../../../apps/frontend-nextgen/src/assets/TaskPanel/v2/TaskPanelV2.css#L1) |

## 用户确认与源码证据的区分

用户说明现有两种调起方式，其中第二种在打开会话时遍历消息寻找卡片。本地 nextgen 源码能证实自动关联查询和已载入消息重建，但本轮没有找到“自动读取全部历史分页”的实现。不能把该功能写成已证实现状，也不能因此删除用户要求的体验；实施计划明确补齐独立分页查找并接入同一控制器。

已只读核对另一个产品的容器和加载器，确认存在可用的 SDK 副屏容器；其源码归属尚未批准。本公共文档不保存私有宿主路径、配置或运行证据。

## 本轮实际运行

工作目录为 `apps/frontend-nextgen`：

```bash
npm test -- --runInBand \
  test/assets/TaskPanel/TaskPanelAdapter.test.tsx \
  test/assets/TaskPanel/sessionResolution.test.ts \
  test/hooks/useSessionTaskPanelBootstrap.test.tsx \
  test/stores/taskConversationNavigationStore.test.ts \
  test/hooks/taskPanelNavBinding.test.tsx \
  test/pages/Workspace/Chat/taskSessionNavigation.test.tsx \
  test/components/Workspace/TaskPanelSwitcherSlot.test.tsx \
  test/hooks/useTaskSwitcherTasks.test.tsx \
  test/pages/Workspace/Chat/hooks/useChatHeaderPanels.test.ts \
  test/pages/Workspace/hooks/useGroupChatHeaderPanels.test.ts \
  test/sidePanel/UmdPanel.test.tsx \
  test/sidePanel/resolveUmdModule.test.ts \
  test/sidePanel/libraryCdnInjector.test.ts \
  test/sidePanel/libraryCdnInjector.refresh.test.ts
```

结果：14 suites passed，135 tests passed，3.143s。这是未抽取前的回归基线，不是共享资产或旧产品接入已完成的证明。

未运行：新资产构建与测试（尚未创建实现）、全量前端 CI、后端架构测试、双宿主浏览器联调、线上 CDN/manifest/权限验证。

## 工作树保护

开始时存在未跟踪目录 `apps/backend/specs/2026-11-02-task-architecture-refactor/`，归属其他工作，不修改、不暂存。没有提交、推送、发布或改变既有协议。

## 2026-11-11 旧产品源码归属更正

用户明确旧产品权威源码为独立的 `open-claw` 仓库。已核对该仓存在，HEAD 为 `f8731f5f7`，本次核对时 `git status --short` 为空；根目录有 `AGENTS.md`、`AGENTS.internal.md` 和 `.codegraph/`，源码位于 `src/`。后续旧产品实现和测试必须在该仓进行；前述 OCB 调研不能代替权威源码核验。统一 ownership memory 与三件套已更新，实施接口评审状态仍为 pending-review。

本次还观察到 Avernet 工作树出现已暂存的 hook 相关变更，它们不属于本任务，保持不动，不取消暂存。

## 实施进展：独立资产基线

- 用户明确批准实施，三件套状态已变为 approved。
- 41 个原资产文件已移入 `apps/frontend-nextgen/assets/task-panel/src/panel/`，2 个 SDK glue 文件留在宿主；原目录以转发入口保持导入兼容，详见 migration-map.json。
- 独立资产已通过类型检查、恢复/声明测试、UMD 构建、真实 UMD 在宿主 React 上的静态渲染和源码边界扫描。
- 初版依赖审计发现漏洞，已按公共 registry 的修复版本锁定依赖；升级后 npm install 报告 0 vulnerabilities。
- 首次共享 UI 测试发现测试解析到两份 React，已在 frontend-nextgen Jest 映射中固定 React 单例；测试通过新的 HostContext 注入原应用服务，不恢复反向依赖。
- 独立 review 已两轮发现并修复普通会话切换取消、消息/恢复焦点竞态、权限错误误报 not-found 和恢复原会话 handoff；每项添加回归测试。
- frontend-nextgen 生产构建已执行并成功输出；仍在完善共享控制器接线和完整回归。
- 旧宿主 build 实际执行失败，当前 node_modules 缺少既有 Monaco/xterm/fflate/xyflow 依赖。旧仓原 tsc 还不能解析已有 d3 类型中的新语法；使用资产的 TypeScript 执行补充检查后仍有既有 SDK 类型缺失和页面错误，不能声称旧宿主构建/类型通过。
- 尚未完成真实双宿主浏览器验证、所有验收项和整个实现，不提交、推送或发布。

## 当前验证边界

最新实际结果、review 修复、构建阻断和未完成验收统一记录在 [verification.md](verification.md)。
前述“尚未运行”为早期阶段事实，不代表最新执行结果；不将合成宿主浏览器验证当成真实双产品 E2E。
