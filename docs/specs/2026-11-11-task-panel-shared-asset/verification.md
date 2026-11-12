# 验证与剩余验收 — 2026-11-11

状态：**实现已落地，但整体尚未完成验收；没有提交、推送、发布或部署。**

## 实际通过

| 检查 | 结果与范围 |
| --- | --- |
| 独立资产 `npm run verify` | TypeScript、20 项 controller/parser 测试、UMD/CSS 构建、真实 UMD + 宿主 React 静态渲染、边界扫描通过 |
| `frontend-nextgen` 全量 Jest | 763 suites / 6288 tests 通过；之后新增的 CDN 保留键测试另运行 sidePanel 全组，6 suites / 43 tests 通过 |
| `frontend-nextgen` 类型/门禁 | `npm run typecheck`、`npm run ci:guardrails` 通过 |
| `frontend-nextgen` 生产构建 | `npm run build` 成功输出；两个宿主都已执行同版本资产镜像/类型生成 |
| 旧宿主 adapter 测试 | 5 suites / 14 tests 通过：独立历史游标、连接参数、取消/错误、viewer 与 owner 分离、加载/重试、payload 映射、实际展示 tab 记忆、focus/展开和 presenter 换代确认 |
| 后端架构 | module boundaries / protocol contracts：5 tests passed；只有既有 deprecation warnings |
| 包准备 | `npm pack --dry-run`：58 files，约 127.5 kB packed；包含 copy script；`npm audit` 为 0 vulnerabilities |
| 浏览器合成宿主 | 真实 UMD 在浏览器中从第 2 页恢复 T1、节点点击记录 interaction=1、显式切 T2、关闭后 activeTask=null/recovery=idle，均可见验证 |

旧宿主本机虽然已声明 `jest-environment-jsdom`，当前 node_modules 未安装该包；上表旧测试使用
`NODE_PATH=<frontend-nextgen node_modules>` 提供测试环境运行。它不是源码依赖或发布要求；正常安装旧仓声明的依赖后应使用旧仓自身依赖。

包体：UMD 约 268 kB raw / 99.1 kB gzip，CSS 约 0.12 kB（主要展示样式由组件自身生成）。

## 已修 review 回归

- 会话变化取消旧导航；消息/显式焦点使晚到恢复失效；业务 403 不再伪装没有任务。
- 返回 opaque origin 时识别目标 handoff，viewer 变化时清除旧 origin/nav。
- 宿主等待 PanelHandle 时，旧展示请求必须重新检查当前任务/会话/存活状态。
- 旧产品 workspace 文件请求的 user_id 使用登录查看人，owner_id 仍是产物归属人。
- 跨会话不再仅凭 URL 更新提前宣告 hydration；取消后的 getMessages 不再滚动。
- 旧 SDK 使用 payload，已在宿主转换；direct card 记忆实际 tab，而非旧恢复 tab。
- 目标页通过真实 panelRef 确认接收，openTab 后 focusTab 展开已有 tab；等待旧 presenter 被换代时重等新 ack。
- taskPanel 不接受 Bot/manifest CDN 覆盖；历史 generic UMD 的 task key 回到本地 adapter，不执行消息给出的 CDN。

## 尚未通过／尚未执行

1. **旧产品完整 build 未通过。** 本机已有依赖缺失：Monaco loader、xterm、fflate、xyflow CSS 等；未为通过测试改动这些无关依赖、关闭门禁或伪造成功。
2. **旧产品完整 tsc 未通过。** 原安装 TypeScript 不能解析既有 d3 类型；用资产 TypeScript 5.6 补充检查，仍存在大量既有 SDK/页面类型错误。最新补充检查未发现新增 taskPanel 模块诊断，不等于旧仓全量类型通过。
3. **没有完成真实双产品 × 单 Bot/群 × 卡片/分页恢复 E2E。** 合成浏览器 fixture 仅验证共享 UMD，不能证明旧页面 SDK 稳定 portal、实际权限、跨路由消息定位、三类文件和发送时 flushContext。
4. **Task 4 / 7 状态 facade 清理未收口。** 活跃聊天和“我的任务”抽屉已使用共享控制器/切换条，旧宿主没有复制这些规则；frontend-nextgen 的兼容 store、旧 nav binding/switcher 入口仍在，尚不能宣称“宿主没有第二份可变任务状态”。它们不能作为另一份业务实现继续演进。
5. 旧页面原有文件已超过 1000 行（Assistant ChatPage 约 2111、GroupChatPage 约 1229）；本次只增加接入调用，没有扩大做无关整页拆分。所有新增资产源码均通过 1000 行扫描。
6. 共享 UI 的原测试在 frontend-nextgen 通过 alias 指向唯一共享源码，尚未全部物理迁入独立资产测试工程；独立资产自身不能替代这组 UI 回归。

## AC 核对

- AC01/02/05/06/07/08/11：有共享实现及单测保护，**真实双宿主验收未完成**。
- AC03/04：独立分页、预算继续、取消和焦点单测通过，浏览器合成恢复通过；真实宿主链路仍待验证。
- AC09/10：固定同源版本、JS+CSS 就绪、错误/重试、保留键和包准备已有验证；未发布，真实宿主 render failure/回滚尚待验证。
- AC12：没有修改任何后端 Python 实现、HTTP 路径、领域模型、报告事件或执行状态迁移；后端架构门禁通过。

## 复现命令

在资产目录运行 `npm ci && npm run verify && npm pack --dry-run`。
资产独立 verify/serve；本地开发直接读取 `/task-panel/index.umd.js` 与同目录 CSS，
不请求 manifest。正式构建只读 `/api/v1/collaboration/tasks/manifest`；
然后运行各自类型、测试和 build。本地类型检查
仍需共享公共类型入口；生产构建不需要资产输入。
资产合成浏览器入口为 `node scripts/serve-smoke.mjs`，仅监听 loopback；不是产品部署入口。


## 后续目录清理验收 — 2026-11-11（替代前述兼容转发／UI 测试未迁移状态）

用户明确要求删除源码和测试两个旧 TaskPanel 目录。本轮已完成该范围，不代表前述
真实双产品 E2E／旧产品全量 build／状态 facade 的其他待验收项已完成。

- `frontend-nextgen/src/assets/TaskPanel/`、`test/assets/TaskPanel/` 均已物理删除；
  没有旧路径 alias 或代理目录兜底。原逐项位置见 migration-map.json 与 test-migration-map.json。
- SDK adapter/注册回到宿主；通用 SafeHtml/清洗移到共享 `src/rich-content/`，通过
  独立 `helpers.js` 构建和宿主 Common/RichContent 入口使用，普通页面不加载任务 UI。
- 34 套原共享组件／工具测试迁到资产自身（一个超 1000 行文件按 describe 分成两套）；
  原 231 项断言全部保留。12 套宿主／SDK 测试共 119 项按职责迁入宿主目录，全部通过。
- 独立资产最终 verify：37 suites / 251 tests、源码与测试类型检查、真实 UMD 渲染、
  helpers 安全渲染与实际复制交付、源码/测试反向依赖和行数扫描均通过。
- frontend-nextgen 最终全量测试：730 suites / 6064 tests 通过；类型检查、公开工程门禁、
  Open Core 扫描通过。测试数量减少来自 231 项迁到资产，不是删除测试断言；本轮增加了 5 项架构保护。
- 实际 `npm pack` 后解压，通过 TASK_PANEL_ASSET_DIR 指向仅含发布文件的包执行前端
  production build 成功，证明不依赖共享源码或共享 node_modules。
- workflow 的资产变更触发、独立 verify 和 typecheck 前 prepare 已补齐；Docker 增加独立
  资产 verify 阶段。YAML 解析和静态架构保护通过；当前机器没有 docker 命令，**未实际构建镜像**。
- 旧宿主重新准备当前资产后，adapter 5 suites / 14 tests 通过（仍使用前述 jsdom 测试环境补充）。
- 本轮限定范围独立复审通过；未修改后端 HTTP/执行协议，未提交、推送、发布或部署。

## C2：workflow 核心交付方式 — 2026-11-11（最终方式，替代 C1 的复制链）

前文 C1 的 helpers/prepare/generated/Docker 独立资产构建阶段仅为历史验证记录；
**当前实现已删除这些机制，不应再执行其中的 prepare 命令。**

- 共享资产源码唯一位于 backend/assets/task-panel；独立构建和交付 UMD/CSS。
  两产品正式构建读取任务模块 manifest，本地开发直接读取当前本地 UMD/CSS；
  不接收消息/Bot 指定任务资源 URL；缺少配置明确报错，不默认回退镜像路径。
- 删除两个宿主 generated 任务目录、public 资产镜像、prepare/copy 脚本与 hooks、
  helpers 构建。frontend-nextgen Docker 不再 build/COPY 任务资产。
- 普通 Common/RichContent、filePreview、一般任务状态工具在宿主独立实现；
  不再 re-export 任务资产实现。SafeHtml 与两个 sanitizer 均有宿主自身安全测试。
- 公开类型入口为 `src/public-api.ts`，宿主只允许 `import type` / `export type`；
  本地 typecheck 经 tsconfig 指向拥有方入口，因此需要该 checkout（或匹配声明包与
  相应解析配置）。生产编译会擦除这些引用，不需要 backend 源码或资产产物。
- 新增独立开发静态 server，真实 HTTP 测试确认只暴露两个固定版本文件，源码、
  node_modules、helpers 和 latest 路径均 404。它不是生产服务部署。

### 本轮实际验证

- 资产完整 `npm run verify`：37 suites / 251 tests、源码/测试类型、UMD/CSS build、
  真实 UMD 静态渲染、独立 HTTP 交付、边界扫描均通过。
- frontend-nextgen 全量 Jest：730 suites / 6074 tests 通过；此后新增的宿主
  sanitizer 回归单独复跑，SafeHtml/sanitizers 共 11 tests 通过。
- frontend-nextgen typecheck、ci:guardrails、Open Core 扫描、普通 production build 通过。
- 隔离 production build 通过：临时工程只复制 frontend-nextgen 与 a2a-chat-client 源码，
  未复制 backend、资产 dist 或 `.umi*`，复用现有前端 node_modules；这证明当前前端
  **生产构建**无 backend 资产输入，不等于安装、typecheck、测试同样无任何源码依赖。
- 旧产品适配测试 5 suites / 19 tests 通过，仍通过 NODE_PATH 补充当前安装缺失的 jsdom
  测试环境。旧仓使用 TS5.6 的全量检查仍失败于既有类型问题，未发现本次 taskPanel
  服务/Hook/组件模块诊断；不声称旧仓全量类型通过。
- npm pack --dry-run：59 files，约 128.6 kB packed；包含独立静态 server、公开声明和
  UMD/CSS，不包含旧 copy/helpers 机制。
- 独立只读 review 未发现 C2 新增确定缺陷；源码/测试行数、无宿主产物镜像检查通过。

仍未配置或发布生产资源地址；真实双宿主 E2E、旧产品全量 build/类型、状态 facade
剩余工作保持前述待验收状态。未实际构建 Docker 镜像，未修改后端 HTTP 或执行协议，
未提交、推送、发布或部署。

补充：完整 lint 首跑暴露本次迁移文件的声明顺序、Promise executor 与断言等待规则问题，
已按原规则修复，未放宽检查；`npm run lint` 复跑通过。修复后 typecheck、受影响
任务集成/loader/宿主测试（15 suites / 143 tests）与 ci:guardrails 再次通过。
该 focused 测试运行曾提示一秒内仍有异步操作，随后正常 exit 0；未关闭门禁或使用 forceExit。


## C4：旧宿主最小接入范围（2026-11-12）

本节仅记录本轮范围收敛，不替代 C3 或原始双宿主真实 E2E 的验收。

- `open-claw/src/components/Panels/UmdPanel/index.tsx` 与 git HEAD 字节一致，不再 import task，也没有 task 条件分支。旧声明兼容留在 `TaskPanel/UmdAdapter.tsx`，普通 UMD 原样委托。
- `tsconfig.json` 恢复原有排版和 SDK 解析方式，只增加一个 task type-only 映射；源码依赖尚未通过已发布声明包解耦，仍需相邻 Avernet checkout。
- 现有文件 diff 从 8 个、65 增/5 删收敛为 7 个、28 增/3 删。新增文件仍属于 task 适配或 task 回归测试，不按文件数量牺牲职责分离。
- 旧宿主 task 测试：基线 9 suites / 150 tests；收敛后 11 suites / 163 tests 全通过。包括任务声明分流、普通 workflow/UMD 参数和事件透传、bootstrap 幂等注册及原有生命周期回归。命令为 `NODE_PATH=/Users/shangjian.msj/Github/Avernet/apps/frontend-nextgen/node_modules ./node_modules/.bin/jest test/services/taskPanel --runInBand`；NODE_PATH 补充当前安装缺少的测试环境，非干净安装证明。
- `npm run build` 实际运行失败：当前安装缺少 `@monaco-editor/loader`、`@xterm/addon-fit`、`@xterm/xterm`、`fflate`、`@xyflow/react` 等既有依赖。没有修改无关模块或依赖来绕过阻断。构建日志 `/tmp/task-panel-minimal-build.log`。
- 本轮未改共享资产运行时实现和 frontend-nextgen 源码；未进行真实登录双宿主浏览器 E2E，不声称旧仓全量 build/类型通过。没有提交、推送、发布或部署。
- 应用级三个 README 保持无 diff；文档只更新本次 task 专项记录。


## C5：参照 BCS 的发布流程（2026-11-12）

- 新增 task 专属 `.github/workflows/publish-task-panel.yml`；静态比较确认除 task 名称、tag 和工作目录外，与既有 BCS workflow 一致，保留 dev 祖先检查、版本一致、GitHub-hosted Node24、OIDC 最小权限、pack 和 publish。未运行远程 Actions。
- 包补齐 repository/public registry/CDN 元数据，prepack 运行完整 verify。没有版本提升、源码复制或发布操作。task PUBLISHING 说明版本 pin、owner 初包/OIDC 配置、CDN 目录、两宿主配置及回滚。
- `npm run verify`：44 suites / 336 tests、源码/测试类型、build、真 UMD、独立 HTTP 交付和边界扫描通过；`npm pack --dry-run` 实际触发 prepack 全流程且 exit0，61 files，约133.1kB。日志 `/tmp/task-panel-publishing-final.log`、`/tmp/task-panel-publishing-pack-final.log`。
- 发布接线静态检查和 `git diff --check` 通过。BCS 发布文件、非 task README 和老宿主源码未在本轮修改。
- 未配置 npm owner 权限、未核验远端包存在性、未发布 npm/CDN、未切换预发配置；实际 OIDC 首发、CDN 可达性、真实双宿主 E2E 尚未验证。


## C6：独立前端资产目录迁移（2026-11-12）

当前权威源码为 `apps/frontend-nextgen/assets/task-panel/`；前述 backend 路径属于
迁移前历史证据。旧源码目录不存在，没有副本或兼容转发。npm 名称、版本0.1.1、
UMD接口与运行时URL不变。同步两宿主类型和集成测试路径、repository.directory、
CI/workflow与task domain导航；应用级README无diff。

- 资产 verify 45 suites /337 tests、真UMD、HTTP交付、类型/build/边界扫描通过；
  pack dry-run（触发prepack）61 files，约133.6kB。
- 资产独立隔离验证通过：临时目录仅复制该包，以自身锁文件npm ci + verify，
  没有宿主源码、node_modules或生成目录；CI新增相同步骤。
- 新前端全量736 suites /6089 tests通过；typecheck、lint、门禁、普通production build
  和Open Core检查通过。最后两套边界/挂载测试19tests通过，普通运行有一秒内异步
  操作提示，随后正常退出，未使用forceExit或跳过断言。
- 修正挂载测试写死旧资产版本的fixture，改为读取loader版本常量。
- 旧前端task相关11 suites /163 tests通过，继续用NODE_PATH提供当前安装缺少的测试
  环境；不声称旧仓全量类型/build或真实浏览器功能已验证。
- Docker context明确排除任务包。宿主隔离production build通过：只复制frontend和
  a2a-client，排除assets/task-panel及backend，复用宿主node_modules，生成目录重建。
- 双向门禁负例验证包相对路径越界、宿主alias/SDK、未声明依赖以及宿主直接/归一化
  路径/运行时包import均被拒绝，公共type-only入口允许。
- 本地服务从新目录重启；同源HTTPS JS/CSS200，JS与新构建一致。
- 用户报告新前端实际功能测试正常（迁移前）；本轮仅资源与自动化回归，未冒充迁移
  后真实业务E2E。没有提交、推送、发布、部署或修改执行协议。

日志前缀 `/tmp/task-panel-move-`，包括 `verify-final.log`、`isolated.log`、
`jest-final.log`、`legacy-tests.log`、`frontend-isolated-build.log`。


## C7：正式迭代分支发布门禁（2026-11-12）

按用户要求取消必须dev祖先的单一限制；允许远端dev或严格RELYYYYMMDD分支，
正则`^REL[0-9]{8}$`。CI验证tag提交在所选允许分支的历史上；不允许任意REL前缀
工作分支。不要求合入dev，tag仍固定具体提交。版本一致、prepack与OIDC未放宽。

临时git仓库回归覆盖dev、只在正式迭代分支上的提交、分支推进后的tag目标，及
未推送提交、普通feature、REL20261112_task、REL20261112-fix、REL20261029_task_3、
位数不符和非数字名称的拒绝情况。测试临时仓库关闭自动maintenance，清理增加
重试以避免macOS后台git维护导致ENOTEMPTY；未改变真实仓库hooks或门禁。

最终独立隔离npm ci + verify通过：46 suites /338 tests、类型/build/真UMD/HTTP
交付/边界扫描通过；npm pack --dry-run再次完整prepack验证通过，61 files。
发布workflow静态检查及两仓diff检查通过。日志
`/tmp/task-panel-release-date-isolated.log`、`/tmp/task-panel-release-date-pack.log`。
没有提交、推送、打tag或发布。当前REL20261029_task_3不是允许的正式迭代分支名；
实际发布需先将review后的提交推送到明确的RELYYYYMMDD分支或dev。


## C8：点击任务节点不应给Bot发送消息（2026-11-12）

用户反馈后，真实SDK Panel的interaction-to-send处理器复现：controller的selectNode、
selectRootNode、drillInto、backToOverview会将description即时作为注入消息发送，
例如`select 节点 node`。迁移前的导航绑定只更新store与导航，不调用onInteraction；
因此属于本次共享controller迁移引入的回归。之前只mock recordInteraction的测试
反而固化了错误的上报假设，未覆盖宿主SDK即时发送语义。

- 新增真实SDK测试先红（期望0发送、实际4），阳性对照确认显式interaction确实能发；
  修复后2tests通过。日志`/tmp/task-panel-node-send-red.log`、`sdk-final.log`（同前缀）。
- 只移除共享controller四处浏览导航的recordInteraction调用及其私有helper，状态/
  路由保持，未改SDK、泛用发送管线或后端协议；两个宿主共用修复。
- 共享controller和实际视图按钮点击均断言无interaction；切换任务的自动选节点也
  不上报；真UMD验证产物的静默浏览，防止源码绿而交付仍旧。
- 资产完整verify46 suites/340 tests通过；新增UMD断言单独复跑通过。新前端相关
  5 suites/22 tests通过，最终SDK单独2tests再次通过；typecheck最终exit0，lint与
  guardrails均exit0。旧前端相关11 suites/163 tests通过；不是旧仓全量构建验收。
- 本地资产重新构建，HTTPS同源URL200且与修复dist字节一致。需用户整页刷新验证
  实际业务体验；没有再在真实预发会话发送探测消息，没有提交、推送或发布。
