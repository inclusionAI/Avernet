# Avernet 接收端与正式前端：开发及验证记录

更新：2026-09-10。功能分支：`feat/clawinsight-monitoring`，开发起点：`3e0d54256`。

## 1. 当前结论（先看这里）

**Avernet 接收、落表、查询和正式监控页面已实现，并跑通「Mock → 真实 POST → dev 业务库 → 真实 GET → 正式页面」。这不是 AIStudio 已联通或预发已验收。**

| 原定步骤 | 当前进度 |
|---|---|
| 1. Avernet 能接收、能落表、能查出 | 已实现；此前真实 dev 库 17/17 验证通过，本轮正式 HTTP 回归也通过 |
| 2. Mock 跑真实接口并在页面展示 | 本轮完成：OC/TE 各 30 条、三类诊断、人工干预两种值、跨日数据、分页及状态；真实 dev 数据库，不是假 API |
| 3. claw-validation 可靠发送出口 | AIStudio 中独立开发，本轮不修改或宣称验收发送端 |
| 4. 按 Demo 实现正式前端 | 本地已实现并验证；真实内部 Host 和跨环境联调仍待完成 |

本轮未修改 OCB 或 claw-validation 业务代码；未 commit/push；未连接 prod、未执行远端 DDL 或 schema_version 更新。此前验收使用 `1.0.0-draft.2`；2026-09-11 已同步两份公共契约和发送端 Spec 至 `1.0.0-draft.3`，取消监控上报 Token，供 AIStudio 增量调整。

## 2. 实现位置

以下路径相对 `src/evolverun/clawweb/public/`。

### 接收和存储（前一阶段成果）

| 位置 | 职责 |
|---|---|
| `modules/clawinsight/server/routes/monitoring.ts` | 两个 POST、三个 GET，请求限制和错误映射 |
| `modules/clawinsight/server/routes/insight.ts` | 沿用 Insight 工厂挂载 monitoring，不增加 OCB 工厂参数 |
| `modules/clawinsight/server/services/monitoring/` | 契约、校验、业务逻辑、运行配置装配 |
| `modules/clawinsight/server/repositories/monitoring-repository.ts` | 复用 shared `IDatabase` 读写两张表 |
| `modules/clawinsight/server/repositories/monitoring-schema-check.ts` | 只读核验表和索引，禁止接口自行建表／ALTER |
| `shared/server/monitoring-schema.ts`、`schema.ts`、`db/dialect.ts`、`db.ts` | 共享迁移与既有存储适配；新增 migration 120，合入前重新核对是否被上游占用 |

表名：`insight_monitoring_diagnoses`、`insight_monitoring_bot_checks`。使用现有数据源，不给生产功能另建数据库连接。

### 正式前端（本轮新增）

| 位置 | 职责 |
|---|---|
| `modules/clawinsight/web/pages/InsightCenter/index.tsx` | 左侧 Agent 治理 / Agent 监控自愈；`module=monitoring` 选择监控；保留治理深链参数及管理员 tab |
| `modules/clawinsight/web/pages/InsightCenter/monitoring/MonitoringPanel.tsx` | 固定 Bot ID／日期／刷新、状态、诊断筛选、展开、分页、人工干预、错误和加载状态 |
| `modules/clawinsight/web/api/monitoring.ts` | 同源 GET，复用 shared fetchJson 和既有登录上下文；不包含上报 Token |
| `modules/clawinsight/web/types/monitoring.ts` | 浏览器只读 DTO，不引入服务端运行时 |
| `app/clawweb/web/style.css` | 添加 ClawInsight Tailwind 扫描路径，确保公开宿主构建包含模块样式 |

AgentEvolve 顶部导航由既有宿主保留，不在模块内复制。内部宿主的样式扫描已经覆盖合并后的 public 路径，本轮不改 OCB 样式。

页面可见时每 30 秒刷新，隐藏停止请求，重新可见立即更新；请求 15 秒超时。切 Bot／改条件中止旧请求并校验请求序号；查询变更重置页码和展开状态，自动刷新保留当前页和展开项。刷新失败保留旧记录并提示未更新，状态不伪装正常。原生日期控件使用 input 事件同步值，已在实际浏览器中验证。

没有 Bot 名称、数据源、手动添加 Bot、钉钉通知状态、会话原文、自愈操作按钮。

## 3. 验证证据

### 3.1 真实 dev 数据库 + 正式页面（本轮）

验收脚本：同目录 `verify-odc-dev-ui.cjs`。它是本地测试宿主，不是生产服务启动入口：

1. 读取通过环境变量指定的既有私有开发配置；只接受 `127.0.0.1:11306`、逻辑库 `clawweb_ds`，并检查物理库 `agentclawdb`。
2. 复用实际 `MysqlDatabase`、监控 runtime 和 Router，不执行迁移；监控上报不配置 Token，不发送 Authorization。
3. 为随机独立 Bot 经真实 HTTP 上报 **60 条诊断（OC/TE 各 30）和 3 条检查快照**，重复首条验证去重。
4. 验证三类结论计数、第二页、北京时间日期范围、关键词、时间未知排序、人工干预及 HEALTHY/ERROR/UNKNOWN/PAUSED。
5. 独立 SQL 回读确认 60 条已落表。
6. 启动公开 ClawWeb 应用，浏览器实际访问 `/insight?module=monitoring`，GET 代理到上述真实路由。

本轮批次：`3cc3d33d-5c1b-48d6-b173-a07907e006fc`。输出 `API_ACCEPTANCE_OK`。

实际浏览器验证：
- 顶部导航、左侧两模块、固定 Bot ID 与页面样式正常；诊断详情包含 86.0% 置信度、人工干预“是”。
- 第二页显示第 11–20 条；日期限制到当天后显示第 1–10 条／共 10 条；倒置日期明确提示。
- 切 OC 显示检查异常；暂停 Bot 显示已暂停和空记录；过期后状态变为未知。
- 关键词与结论组合筛选实际改变返回记录。
- 窄屏无水平溢出，恢复原视口；浏览器正常使用阶段没有 console error。
- 关闭测试 API 后点击刷新：保留 10 条旧记录，明确显示“状态未更新／当前保留上次结果”，不是伪空或伪正常。

**本轮数据已清理**：脚本按精确 event_id + bot_id 删除本批次，输出 `CLEANUP_OK`。独立只读探针再次确认两张表均为 **0 行**。不要为清理测试执行无条件 DELETE、TRUNCATE 或宽泛 mock 前缀删除。

### 3.2 自动化回归与构建

- 监控服务端 **72 项通过**：字段／业务配置 44、Repository SQL 10、HTTP 17、丰富样例生成器 1。
- 前端 **28 项通过**：监控组件／查询封装／模块导航，以及选定的原有治理页面回归。
- 组件测试覆盖过时响应隔离、分页与过滤重置、刷新失败保留旧记录、可见性／轮询／卸载清理、空 Bot 与错误区分、诊断文本安全展示。
- ClawInsight TypeScript 构建、clawevolve／workflow 依赖构建、公开 ClawWeb 完整构建通过。构建仍有既有大 chunk 警告，本轮不为监控重构无关模块。
- SQL／HTTP 回归使用测试专用 `node:sqlite` 适配器，不代表 better-sqlite3 原生驱动已验证；实际 dev 验收使用真实 MySQL/ZDAS 适配器。

## 4. 如何复验（不依赖 AIStudio）

前提：已确认 meshboot 指向 **dev**，代理 11306 可用。私有配置文件和数据库凭据不提交仓库。仅有本地代理地址护栏不足以替代操作者确认当前代理环境，禁止把代理切到 prod 后执行。

在 `Avernet/src/evolverun/clawweb` 中构建（依赖已按项目要求安装）：

```bash
npm run build --workspace @avernet/clawweb-shared
npm run build --workspace @avernet/clawevolve
npm run build --workspace @avernet/workflow
npm run build --workspace @avernet/clawinsight
npm run build --workspace @avernet/clawweb
```

在 Avernet 根目录的终端一：

```bash
export CLAWWEB_DEV_CONFIG_FILE="/你的私有开发配置/application-default.yaml"
node docs/specs/2026-09-09-clawinsight-agent-monitoring/verify-odc-dev-ui.cjs
```

输出 `UI_READY` 后，在终端二进入 `src/evolverun/clawweb/public/app/clawweb`：

```bash
node_modules/.bin/vite --host 127.0.0.1 --port 5173 --strictPort
```

浏览器打开 `http://127.0.0.1:5173/insight?module=monitoring`。使用 **app 自身的 Vite**，不要误用 workspace 根目录另一个版本。终端一监听 3001，与现有 Vite `/api` 代理一致。

结束时在终端一 Ctrl-C，等待 `CLEANUP_OK`，再停止终端二。脚本也在 30 分钟后自动清理；不要强制 kill -9。如进程被强制杀死／网络中断，应凭输出的 RUN_ID 人工核对残留，只清理该批次。

在 `src/evolverun/clawweb` 下运行自动化回归：

```bash
MONITORING_TEST_SQLITE_DRIVER=node npm run test:monitoring --workspace @avernet/clawinsight
npm run test --workspace @avernet/clawinsight -- web/pages/InsightCenter/__tests__ web/pages/InsightCenter/monitoring/__tests__ web/api/__tests__/monitoring.test.ts
```

`local-host.ts` / `local-mock.ts` 仍作为可选 SQLite 辅助工具保留，但本项目联调以真实 dev 验收为准。本机 better-sqlite3 与 Node 26 的兼容问题未通过修改依赖规避。

## 5. 配置与仍须验证的边界

| 环境变量 | 说明 |
|---|---|
| `CLAWWEB_MONITORING_ENABLED` | `true` 启用，默认关闭 |
| `CLAWWEB_MONITORING_BOTS_JSON` | 固定 Bot ID、engine，可选 paused；两端清单须对齐 |
| `CLAWWEB_MONITORING_STALE_SECONDS` | 默认 300，结合实际逐 Bot 检查周期设置 |

### 5.1 宿主先解析 JSON：已知差异，不隐瞒

内部 Host 在挂载 Insight 前执行 `express.json({limit:'10mb'})`：
- 正常上报使用带准确 Content-Length 的非压缩、非 chunked UTF-8 JSON；监控 Router 按原始长度限制 128 KiB。已被宿主消费又不可核验原始大小的请求拒绝，不重新序列化冒充原始字节数。
- **格式损坏的 JSON 会先在宿主解析器失败，Express 会跳过普通子 Router**。因此实际宿主可能返回 400 HTML，而非公共协议示例的 `MONITORING_INVALID_EVENT` JSON；正常字段校验由监控 Router 返回约定的 JSON。
- 本轮修正了之前错误假设子 Router 能捕获父解析错误的测试：分别验证宿主 400 拒绝、无写入，以及模块自己解析时的 JSON 错误格式，没有给测试添加生产宿主不存在的中间件。
- 未在公共协议中静默改变这一要求，也未修改 OCB 来强行统一错误格式。**这是预发验收／契约校准的明确待办**：发送端非 2xx 不能作为 ACK，应能处理非 JSON 错误；若要求任意坏 JSON 都有同一错误 envelope，需协调宿主错误适配，单改正常子路由做不到。

### 5.2 挂载和真实环境

- 内部实际 Host 路径 `/api/insight/v1`，本轮测试宿主使用同前缀；公开后端 bootstrap 的旧路径 `/api/insight` 未调整，不能混用。
- 本轮浏览器使用公开 app 外壳 + 实际监控 Router，不是完整内部 Host 启动验收。合并脚本、真实登录上下文、网关鉴权／重定向、实际服务账号读写和元数据权限，仍需预发验证。
- 本轮没有运行全仓测试，也没有验证 better-sqlite3 原生驱动或 prod。

## 6. 后续顺序

1. 等 AIStudio 发送端提供改动版本、自动测试结果和实际序列化的样例；先核对两个 POST、字段、幂等编号、ACK、非 JSON 错误、逐 Bot 状态与恢复语义，不要求它读取 Avernet。
2. 在本地检查其样例能通过当前接收契约；如果发现字段差异，先协调，不让两边各自改 schema。
3. 合入前同步上游，重点核对 Insight 工厂、共享迁移编号 120、已有治理页面及宿主装配；审查工作区，不自动提交。
4. 使用既有开发流程在预发注入监控配置，先跑真实 Host 的 POST→表→GET，再让 AIStudio 发少量真实诊断。
5. 验收跨环境故障恢复：重试去重、失败不假 ACK、人工干预传递、多 Bot 状态、容器重启后继续补发；通过后再决定生产安排。

## 7. 人工验收预览（2026-09-10补充）

新增 `manual-ui-acceptance.md`，包含17条操作路径、预期结果及ODC只读核对SQL。
测试宿主新增可选 `MONITORING_UI_MINUTES`（默认30，范围1–480）和 `MONITORING_UI_KEEP_CHECKS=true`。
人工验收可保留4小时并通过真实POST持续刷新本批TE/OC检查，避免用户测试时两者均过期；不新增诊断、不变更业务状态规则。
此预览批次独立于第3节已清理的自动验收批次；预览期间允许本批数据留在dev，到期/正常退出仅清理对应批次。以启动日志中的RUN_ID和清理结果为准。

## 8. V2 正式前端落地（2026-09-10）

按已确认的 `docs/design/clawinsight-monitoring-redesign-v2.html` 替换正式页面展示层，不改变上报/查询协议或数据库结构。

- `InsightCenter/index.tsx`：效果中心双模块侧栏；保留治理入口和 URL 切换逻辑。
- `monitoring/MonitoringPanel.tsx`：组合筛选、轻量状态行、诊断列表和分页；保留真实 GET、30秒可见页轮询、请求取消和旧响应隔离。
- `MonitoringControls.tsx`：固定 Bot ID 菜单、快捷日期及自定义日期弹层。快捷范围立即应用；自定义范围先编辑后应用，取消不改变查询，日期以北京时间计算。
- `DiagnosisRecord.tsx` / `MonitoringIcon.tsx` / `monitoring.css`：诊断结论、人工干预、展开文本和元数据、标识复制反馈及响应式样式。无 Bot 名称、任务来源、通知状态和自愈动作。
- 模块构建复制 CSS 到 dist，避免宿主消费构建产物时丢失样式。
- 公开 app 的 `web/main.tsx` / `style.css`：统一品牌和导航视觉，保留三个真实入口。内部 OCB 宿主仍负责其原有四栏目和登录用户区，监控模块不复制顶部，也不伪造内源标记或用户身份。

浏览器使用既有 dev 验收批次，通过真实 GET 检查详情、下一页、结论/关键词筛选、快捷日期，以及正常/异常/过期/暂停四类 Bot。1440、1024、768、375 像素视口检查未出现页面水平溢出；375 像素下额外检查了日期弹层和展开详情。本次未生成新的 ODC 数据，既有验收进程仍按原计划维护/清理其批次。

自动验证：模块类型检查、前端相关 9 文件/32 用例、监控后端 4 文件/72 用例、模块及公开宿主构建。后端 HTTP 测试首次受沙箱监听端口限制，获准后重跑全部通过；使用临时测试库，不操作 ODC。构建仍有现有大 chunk 警告，治理测试仍有既有 act 警告。未验收完整内部宿主和 AIStudio 端到端，也未提交或推送代码。

人工清单已同步 V2 日期弹层操作和计数语义，并补充取消/Escape、复制反馈用例。


> 2026-09-11 / draft.3：两个监控 POST 不要求应用层 Token，依赖部署侧内网限制；其他模块鉴权不变。此前验收数字是历史结果，不能当作本修订已通过真库/完整 Host 联调的证明。

### 2026-09-11：draft.3 无 Token 接入验证

- 仅修改监控路由、运行配置、相关错误类型、Mock/验收脚本和测试；不修改其他模块鉴权、OCB 或 CV 业务代码，不操作 ODC。
- `npm run check` 通过。
- 默认测试因本机 better-sqlite3 原生 binding 缺失无法完成；采用现有显式辅助驱动 `MONITORING_TEST_SQLITE_DRIVER=node npm run test:monitoring`，4 个文件、73 项全部通过（包括 18 项 HTTP 测试）。临时 loopback 服务需要本机监听权限。
- 新增无凭据配置、两个 POST 无 Authorization 成功及旧 Token 配置/头不重新启用校验的测试。非法字段、固定 Bot 限制、幂等、存储失败继续验证。
- 辅助驱动通过不证明生产数据库驱动通过。此次未重跑 dev ODC 写读，不宣称完整 Host/AIStudio 联调已通过。
