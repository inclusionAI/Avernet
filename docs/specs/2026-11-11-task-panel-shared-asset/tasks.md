---
status: approved
owner: task-module
---

# Tasks：任务副屏单源资产与双宿主接入

> 状态：`[ ]` 待做 · `[~]` 进行中 · `[x]` 完成 · `[!]` 阻断。
> 用户已明确批准开始实现；不执行未授权提交、推送或发布。
> 2026-11-11：共享资产与双宿主接线已落地；Task 4 / 7 状态 facade 和 Task 8 / 9 真实双宿主验收未完成。实际结果见 [verification.md](verification.md)，不得将接线/合成 fixture 视作双宿主验收。

## Task 1 [x]：确认接入权威源并冻结迁移与行为清单

- **Goal**：明确真正可写的两个宿主及单源资产归属，给抽取建立可判等基线。
- **Files**：本目录 `research.md`、`plan.md`、`tasks.md`；本地唯一 ownership memory；当前 `apps/frontend-nextgen/src/assets/TaskPanel/**` 及全仓调用只读清单。
- **Done when**：
  - [x] 用户于 2026-11-11 确认旧产品权威源为 `open-claw` 仓库；不使用 OCB 镜像或组装产物。
  - [x] 已更新同一份 ownership memory，明确 legacy 的 open-claw 和共享资产的 `apps/frontend-nextgen/assets/task-panel/`；未改 AGENTS/CONTEXT 私人规则。
  - [x] 已记录 migration-map.json：41 个资产文件迁入任务模块，2 个 SDK glue 文件保留宿主；其余位置只保留转发入口。
  - [x] 已核对实际调用并建立独立恢复测试；节点/原会话与焦点竞态已有可执行回归用例。
  - [x] 用户明确“好的，开始实现吧”，批准三件套和实施。
- **Depends on**：—
- **Coverage**：AC01–AC12 的实施前提。

## Task 2 [x]：建立独立任务资产子工程及宿主合同

- **Goal**：无需任何宿主工程或 Python 运行时依赖即可构建和验证资产。
- **Files**：`apps/frontend-nextgen/assets/task-panel/{package.json,package-lock.json,tsconfig.json,vite.config.ts}`、`src/contracts/**`、`src/index.ts`、`test/contracts/host.conformance.test.ts`、`test/umd-contract.test.mjs`。
- **Done when**：
  - [x] 先写合同/UMD 导出失败测试，再实现 hostApiVersion、assetVersion、组件和控制器导出壳。
  - [x] React/ReactDOM external；禁止 @tc-chat、@aix-chat、应用别名和绝对开发机路径。
  - [x] 严格类型和公开依赖锁文件可复现；版本从 package 唯一注入，包入口和导出一致。
  - [x] Python 后端安装和启动路径无 Node 构建依赖。
- **Depends on**：Task 1。
- **Coverage**：AC10、AC12。

## Task 3 [~]：迁移副屏 UI、数据派生及资源访问

- **Goal**：任务展示与请求行为成为一份独立实现，而不是仅搬一个组件文件。
- **Files**：现有 `apps/frontend-nextgen/src/assets/TaskPanel/**` → `apps/frontend-nextgen/assets/task-panel/src/{panel,data,styles}/**`；原 `test/assets/TaskPanel/**` → 新资产 `test/panel/**`；所需 `src/shared/taskStatus.ts`、`services/workspace/sessionFileUtils.ts` 的纯函数消费者；nextgen `taskPanelArtifactAdapter.ts`。
- **Done when**：
  - [ ] 现有 mapper、action log/trace、产物、DAG、总览/节点 UI 测试随唯一源码迁移并通过。
  - [ ] Fetcher 登录/信封处理、三类产物存储访问、Markdown 渲染等 app/SDK imports 消除，失败语义不退化。
  - [x] 迁移的共享纯函数有单一 owner，不能复制一份近似逻辑到宿主。
  - [x] CSS/portal 自包含；匹配 SDK 外壳的规则移回宿主 presentation，不依赖宿主 Tailwind 扫描资产源码。
  - [ ] 切任务/卸载取消请求、过期响应丢弃；object URL 释放和三类产物访问通过测试。
- **Depends on**：Task 2。
- **Coverage**：AC01、AC02、AC08、AC09。

## Task 4 [~]：迁移任务状态、切换及导航编排

- **Goal**：两个宿主共享任务规则和状态，只保留真实路由与容器动作适配。
- **Files**：新资产 `src/controller/**`；现有 `hooks/{taskPanelNavBinding,useTaskSwitcherTasks,useSessionTaskPanelTab}.ts`、`stores/{taskSidePanelStore,taskSessionAssociationStore,taskUpdateAttentionStore,taskConversationNavigationStore}.ts`；nextgen `taskPanelConversationAdapter.ts`。
- **Done when**：
  - [ ] 控制器每任务选中/下钻、会话任务范围、attention、显式 intent、焦点及原会话恢复测试通过。
  - [ ] 宿主 store 不保留第二份可变任务状态；必要 facade 仅订阅/转发，不双写。
  - [ ] 默认选中不导航；选中、下钻、返回保持原语义；连续导航仅最新 token 生效。
  - [ ] 同会话 no-op、hydrate 失败、权限拒绝、取消/关闭/登出均不会留下永久保活。
  - [ ] 消息定位经宿主操作执行，共享模块不查询宿主 DOM、不解析产品 URL。
- **Depends on**：Task 3。
- **Coverage**：AC04、AC05、AC06、AC07、AC11。

## Task 5 [~]：实现安全声明解析及统一会话恢复

- **Goal**：卡片入口和分页历史恢复通过同一解析与 openTask 提交链执行。
- **Files**：新资产 `src/history/**`、`src/controller/recoverConversation.ts`、`test/history/**`、`test/controller/focus.test.ts`；现有 `useSessionTaskPanelBootstrap.ts`、`services/tasks/taskPanelMessage.ts`、`sidePaneContent.ts`；nextgen `taskPanelHistoryAdapter.ts`。
- **Done when**：
  - [ ] 先写卡片在第 2 页、跨行/引号/属性内 >、旧别名、坏 JSON/未闭合、代码块不触发的失败测试。
  - [ ] by_session → 历史查找 → list 兜底是单控制器的来源选择，不产生并发竞争提交。
  - [ ] 用户显式选择或已展示任务优先；双入口同 task 幂等；A→B 后 A 的响应不能开到 B。
  - [ ] 只读独立历史页，不改变聊天 provider 游标、消息渲染、滚动和 hydration/live 状态。
  - [ ] 每页身份和游标正确；重复游标/网络失败/空映射页不导致误终止或死循环。
  - [ ] 20 页预算后 incomplete 可继续，最终耗尽才 not-found；不 eval 或重放历史卡片代码。
  - [ ] 现有组件键、tab ID、params 形状及非任务副屏重建保持兼容；root instruction 转发不改。
- **Depends on**：Task 4。
- **Coverage**：AC02、AC03、AC04、AC05、AC07、AC09、AC12。

## Task 6 [~]：实现固定版本资产加载、同源交付和失败边界

- **Goal**：两个产品加载同一批准资产，不依赖协作群 manifest 的偶然就绪。
- **Files**：新资产 `scripts/**`、`test/umd-contract.test.mjs`、`test/architecture.test.mjs`；nextgen `taskPanelAssetLoader.ts`、`scripts/copy-task-panel-asset.mjs`、`config/config.ts`、`package.json` 及实际变化锁文件；获批旧源中对应配置脚本。
- **Done when**：
  - [ ] 固定版本同源镜像的 JS/CSS、入口、hostApiVersion 校验通过真构建测试。
  - [ ] 同 URL 请求去重；不同运行期版本不覆盖同名全局库；资源未就绪时声明不丢失。
  - [ ] taskPanel 注册不被 Bot CDN 配置覆盖，也不随切 Bot 的通用 CDN map 清除而丢失。
  - [ ] SDK CDN 分支不会绕过任务 wrapper 的 host 注入；其他副屏加载路径不受影响。
  - [ ] 加载/render 失败可重试，无第二份本地实现 fallback；记录 gzip、包内容和公开扫描结果。
  - [x] 仅本地 build/pack/镜像交付准备，不擅自 npm publish、上线或修改后端 HTTP 合同。
- **Depends on**：Task 2、Task 5。
- **Coverage**：AC09、AC10、AC12。

## Task 7 [~]：接通 nextgen 并移除旧业务副本

- **Goal**：新产品经共享 UMD 和薄适配运行，作为旧产品接入前的行为等价门槛。
- **Files**：nextgen 新 `services/tasks/panel/**`、`components/Workspace/TaskPanelHost.tsx`；`extensions/empty.ts`；chat/group header hooks；`sidePaneContent.ts`；`taskSessionNavigation.ts`；`services/tasks/taskConversationNavigation.ts`；`PersistentChatLayoutPanelHost.tsx`、`ChatLayoutPanelSlot.tsx`、`persistentTaskPanelStaleRelease.ts`、`TaskPanelSwitcherSlot.tsx`；`pages/MyTask/components/MyTaskDrawers.tsx`；`tsconfig.json`、`jest.config.*`；对应测试及旧导出消费者。
- **Done when**：
  - [ ] 新旧消息声明、会话进入和 Monitor 重开均进入同一控制器；普通非任务副屏行为保留。
  - [ ] 单 Bot/群真实路由跳转时保留同一个面板实例；普通目录切换正确释放。
  - [ ] “我的任务”已有抽屉消费同一资产，不留下第二 UI 实现。
  - [ ] interaction 经原 SDK flushContext/flushInteractions 带入后续消息。
  - [ ] 原 14 suites/135 tests 的行为保护迁移或继续通过；新增 adapter conformance、类型和构建通过。
  - [ ] 全仓扫描不再存在旧任务实现副本；过渡 re-export 不带实现，生产不同时打包 UMD 和有状态实现。
- **Depends on**：Task 3、Task 4、Task 5、Task 6。
- **Coverage**：AC01–AC12（新产品）。

## Task 8 [~]：在获批旧产品源接入相同资产

- **Goal**：旧产品在现有界面、SDK 容器和路由中提供完整任务体验。
- **Files**：已确认的 `open-claw` 仓库中 `src/components/Panels/`、`src/pages/Assistant/`、`src/pages/GroupChat/`、应用注册及历史/会话/文件适配与测试；不修改 OCB checkout。
- **Done when**：
  - [ ] 旧宿主使用相同资产/接口版本，SDK props 和 identity 仅做适配，无任务规则复制。
  - [ ] 单聊和群聊卡片/分页恢复、Monitor 重开、任务切换、节点导航、返回及保活完整接通。
  - [ ] workspace/session/object 产物经合法现有路径可用；无权限不冒充 owner，不增 alternate task HTTP。
  - [ ] 同一 host conformance 在真实 adapter 上通过；旧产品类型、测试和构建结果记录。
  - [x] 跨仓均消费 0.1.0 本地资产；没有提交、发布或部署，不将本地接线等同于已部署。
- **Depends on**：Task 1（权威源码已确认，其余检查待做）、Task 7。
- **Coverage**：AC01–AC12（旧产品）。

## Task 9 [~]：执行双宿主集成验证与架构检查

- **Goal**：用真实构建和可定位证据验证所有验收项，而非只证明共享代码能单测。
- **Files**：新资产 `test/{contracts,history,controller,panel}/**`、`test/{umd-contract,architecture}.test.mjs`；nextgen 及获批旧宿主的 adapter/E2E 测试；本目录 `research.md`/验证记录。
- **Done when**：
  - [ ] 同一共享功能测试、两个 adapter conformance、真 UMD 全链路通过。
  - [ ] 浏览器完成新旧 × 单 Bot/群 × 卡片/分页恢复；含跨路由实例保留、产物、登录/403 和 interaction 后续发送。
  - [ ] 覆盖加载失败/重试、资源不兼容、长历史继续、旧请求 late reply、关闭/登出和普通会话切换。
  - [ ] 运行资产 verify/pack、两个宿主相关完整测试/类型/构建、受影响后端架构门禁；不关闭或放宽现有检查。
  - [ ] 检查所有新增/修改源文件行数、无反向宿主 import、无私有依赖和运行态数据泄漏；提交前复核无重复实现。
  - [x] 无法执行的检查列明原因和剩余风险；仅做合成浏览器，不标记真实双宿主功能完成。
- **Depends on**：Task 7、Task 8。
- **Coverage**：AC01–AC12 最终验证。

## Task 10 [~]：更新知识导航和交付记录

- **Goal**：保留单源接口、构建、宿主接入和回滚的可复现说明，区分实现与发布状态。
- **Files**：新资产 `README.md`、`PUBLISHING.md`；任务 domain `README.md`；本目录三件套及验证记录。按用户追加要求，不修改非 task 模块 README 或全局导航。
- **Done when**：
  - [ ] 资产文档写明唯一源码、宿主接口、版本化交付/回滚、恢复预算及继续机制。
  - [ ] task domain 导航指向 owning module，不复制协议，不写个人规则进项目 AGENTS/CONTEXT；非 task 模块文档不扩散本次说明。
  - [ ] 文档链接/命令与实际源码和包内容一致；所有 AC 逐项指向真实验证。
  - [ ] 交付明确实现、构建、发布、跨仓集成和部署各自状态；未授权不提交/推送/发布。
- **Depends on**：Task 9。
- **Coverage**：AC10、AC12 及交付可复现性。

---

## Groups

- **Group A — 归属与接口：Tasks 1、2。** 确认可写源、迁移基线和可独立构建的受审接口。
- **Group B — 单源副屏与交互：Tasks 3、4。** 迁移完整任务展示和编排，不含第二份实现。
- **Group C — 双入口恢复与分发：Tasks 5、6。** 分页可恢复、焦点不竞争，资产可固定版本交付。
- **Group D — 双宿主接入：Tasks 7、8。** 新产品先等价，再让旧产品消费同一实现。
- **Group E — 验证与交付：Tasks 9、10。** 双宿主真实验证、门禁及知识导航收口。

## Review gate

用户已明确批准开始实现。源码归属、接口与恢复策略已确认；按组推进，实际完成及未验证边界分别记录。


## Follow-up C1 [x]：删除 frontend-nextgen 旧 TaskPanel 目录（用户已批准）

用户于 2026-11-11 明确批准迁移引用并删除过渡目录。本小组不改变后端协议、不提交或发布。

- [x] SDK adapter/注册移入宿主的 components/Workspace、services/tasks/panel。
- [x] 通用 HTML 能力独立于 panel 目录，由共享资产的独立 helpers 构建入口提供；普通页面不加载任务 UI。
- [x] 生产源码只消费固定资产及生成声明/显式 helpers 构建产物，不直接 import backend 的 TS/TSX 源码。
- [x] 测试显式指向共享实现；删除旧路径 Jest 特殊映射，不保留绕过删除的 alias。
- [x] 删除 src/assets/TaskPanel 与死宿主 export facade，添加防止目录/源依赖回流的门禁。
- [x] 用户追加要求：删除 test/assets/TaskPanel；纯共享测试迁到资产自身，宿主集成/SDK测试按职责保留原断言。
- [x] 修复 review 发现的干净 CI/Docker 资产输入遗漏，检查不能依赖本地已生成目录。
- [x] 共享资产 verify、helpers 真构建测试、frontend-nextgen 全量测试/类型/门禁/build 和受影响旧宿主适配测试通过。

本轮实际结果见 verification.md 后续目录清理小节；Docker 仅完成接线与静态检查，未在本机运行镜像构建。此前其他任务未因本小组完成而自动标为完成。


## Follow-up C2 [x]：按 workflow 核心方式交付（用户已批准）

- [x] 两宿主的生产运行时只从部署指定可信固定版本 URL 加载 UMD/CSS，不使用消息 URL，不依赖 BCS 业务 manifest。
- [x] 删除 frontend-nextgen/open-claw 的 generated 任务目录、public 资产镜像、prepare/copy/helpers 构建链和前端 Docker 的资产构建输入。
- [x] 共用公开 type-only 包入口，保留 source-of-truth 类型；禁止生产运行时 import 包、裸 backend source 或生成 helpers。
- [x] RichContent、文件预览工具和一般任务状态归一化在宿主拥有独立实现与安全测试；不再成为任务资产的消费者。
- [x] 资产模块提供独立可验证静态开发资源交付；没有新增后端路径、领域对象或执行协议，没有发布或部署。
- [x] 共享资产完整 verify、frontend-nextgen 全量类型/测试/门禁、旧产品受影响适配测试，以及完全不含 backend 资产输入的隔离 frontend-nextgen production build 通过；旧产品全量类型/build 仍有既有阻断，见验证记录。
- [x] 独立 review 及实际验证记录收口，区分源代码实现与尚未配置/发布的部署资源地址。

## Follow-up C3 [~]：功能边界与 review 整改（2026-11-12，用户已批准）

不修改后端任务执行 HTTP／领域字段／报告事件／状态推进，不提交、推送、发布或部署。

- [x] C3.1：已展示声明按会话与 viewer 绑定，统一由共享 controller 仲裁；覆盖卡片早于 historyReady、旧会话残留和异步加载／关闭／身份失效。
- [x] C3.2：导航保活仅用于会话交接，两个宿主身份失效必须 dispose 并屏蔽晚到异步；旧文件适配错误转换为共享语义，保留取消。
- [x] C3.3：共享 client 严格校验任务列表 DTO，合法空结果与不兼容响应分离。
- [x] C3.4：宿主统一资产加载与可重置渲染错误边界；群聊 incomplete/error 可继续／重试，语义不与单聊分叉。
- [ ] C3.5：共享 TaskArtifactsView 封装完整产物业务，宿主仅保留抽屉和 portal；清理确认无生产调用的旧业务入口，不把页面功能整体搬入资产。
- [ ] C3.6：资产 verify、两个宿主相关测试、frontend-nextgen 全量检查及构建、独立 review；记录旧仓基线阻断与真实 E2E 未验收项。


## Follow-up C4 [x]：旧宿主最小接入范围（2026-11-12，用户已批准）

行为不变；保留两种调起、分页恢复、跨会话稳定挂载和身份失效保护。

- [x] 通用 `UmdPanel/index.tsx` 撤回 task 分流，恢复与 HEAD 完全一致；旧卡片分流留在 `TaskPanel/UmdAdapter.tsx`，通过既有 bootstrap 注册。
- [x] `tsconfig.json` 撤回三个非 task SDK 路径映射及无关格式化；只保留既有批准的共享 type-only 入口。
- [x] task 专属实现和测试不为减少文件数而合并或删除；非 task 现有文件仅保留七处接入点（配置、根挂载、注册、CDN 防覆盖、单聊、群聊、类型解析）。
- [x] 回归覆盖旧卡片多种声明形态、直接 payload、普通 UMD 参数不变，以及 bootstrap 一次注册两个 task 路由。
- [x] 旧宿主 task 测试 11 suites / 163 tests 通过；记录 production build 的现有依赖缺失，未扩散修复范围。
- [x] 类型解析仍需相邻 Avernet checkout；不引入第二份类型实现、未发布 npm 依赖或宿主生成目录，不宣称旧宿主完全独立 typecheck。

## Follow-up C5 [x]：参照 BCS panel 发布流程（2026-11-12，用户指定）

- [x] task 包补齐公开 registry、repository、UMD CDN 入口和 prepack 完整验证。
- [x] 独立 `publish-task-panel.yml` 复用 BCS 的 tag、dev 祖先、版本一致、OIDC 最小权限检查；不修改 BCS 流程。
- [x] task PUBLISHING 写明 owner 配置、初次发布前置条件、审核合并/tag 发布、版本 pin、固定 CDN 目录和回滚。
- [x] 完整 verify（44 suites / 336 tests）、触发 prepack 的 pack dry-run（61 files）与发布接线静态检查通过。
- [x] 不实际创建 tag、push、发布或部署；预发地址尚未切换，CDN 可达性和真实 E2E 留待发布后验证。


## Follow-up C6 [x]：共享资产移至 frontend-nextgen/assets（2026-11-12，用户已批准）

- [x] 整体移动独立包至 `apps/frontend-nextgen/assets/task-panel`，删除旧目录，不留转发或副本。
- [x] 同步两个宿主的类型/集成测试路径、workflow 工作目录、repository.directory、task domain 链接及迁移清单。
- [x] owning README 写明源码组织不等于宿主耦合；不修改应用级 README。
- [x] 双向依赖门禁及负例：资产相对路径逃逸/宿主 alias/SDK/未声明依赖，宿主直接/归一化/运行时引用均拒绝，公共类型入口允许。
- [x] 独立包隔离 npm ci + verify 通过；CI 接入隔离验证；Docker context 排除任务包。
- [x] 最终资产 verify/pack、两宿主回归、前端类型/lint/门禁/build 与无资产宿主隔离 build 验证收口。
- [x] 本地资产进程已从新目录启动，同源 HTTPS JS/CSS 均200，端口和版本URL不变；不发布、不推送、不改执行协议。


## Follow-up C7 [x]：发布分支约束调整（2026-11-12，用户已批准）

- [x] 不要求迭代发布先合入 dev；允许远端 dev 或严格 `RELYYYYMMDD`（`^REL[0-9]{8}$`）分支上的提交。
- [x] 拒绝任何后缀和位数不符的 REL 分支，覆盖 REL20261112_task、REL20261112-fix、REL20261029_task_3 等。
- [x] 保留 tag/package 版本一致、完整 prepack 验证、OIDC和公开包元数据门禁；文档同步。
- [x] 完整资产 verify/pack 与独立隔离验证通过（46 suites /338 tests，61 files）。
- [x] 本轮不提交、推送、打 tag 或发布；源分支由发布者明确选择，不靠排序猜测最新迭代。


## Follow-up C8 [x]：节点浏览误发Bot消息回归（2026-11-12）

- [x] 真实SDK Panel集成测试先红：节点/根节点/下钻/总览触发4次bridge.sendMessage，默认选中不发。
- [x] 对比迁移前导航绑定无交互上报，确认共享controller新增recordInteraction与SDK即时发送语义冲突。
- [x] 只删除浏览导航上报，保留状态与会话跳转；不改SDK或通用消息管线，不影响既有显式业务动作。
- [x] 共享controller、实际视图点击、任务切换自动选节点、真UMD及真实SDK正反对照测试通过。
- [x] 资产verify46 suites/340 tests；新宿主相关5 suites/22 tests；旧宿主11 suites/163 tests；新宿主types/lint/门禁通过。
- [x] 本地固定UMD已重建，同源HTTPS200且文件一致；需整页刷新后人工复测，不发布。


## C9：单一 manifest 与最小接入清理

- [x] 后端仅保留已认证的 `/api/v1/collaboration/tasks/manifest`，无 OpenAPI 镜像。
- [x] 配置读取使用环境隔离的 system-config `task / panel`；既有写口增加该键的管理员校验及严格格式校验。
- [x] 两端正式构建的 loader 读取 manifest，删除旧资源地址注入及宿主包版本 pin；校验接口、去重、重试、禁止热换与未知全局。
- [x] 本地开发直接加载独立 server 的当前 UMD/CSS，不请求 manifest，不模拟接口；业务 API 保持预发。
- [x] 恢复两端 tsconfig 原排版，仅保留必要 task 类型/测试映射；删除无效生产配置修改。
- [x] 后端接口/权限/架构、新旧宿主回归、资产与构建验证收口。
- [ ] 用户本地真实会话验收；组件发布后的预发真实 manifest/CDN 与两端验收。
