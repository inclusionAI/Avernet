# Avernet / ClawInsight：Agent 监控自愈接入 Spec

- 状态：接收端与正式前端已实现；监控服务端 72 项、选定前端回归 28 项通过；Mock 经真实 POST→dev 表→GET→正式页面验证完成（OC/TE 各 30 条，已清理）。真实内部 Host、AIStudio→预发、原生 SQLite 驱动与 prod 仍待验证。宿主坏 JSON 错误 envelope 的已知差异见开发记录 §5.1。
- 本次验证：draft.3 的类型检查通过；使用显式 node:sqlite 辅助驱动的 73 项监控测试通过，未重跑 ODC 或完整 Host/AIStudio 联调。上行 72 项为此前记录，不是本修订结果。
- 日期：2026-09-11（取消监控上报 Token；既有页面与 dev 验收结果为历史记录）；对齐公共契约 `1.0.0-draft.3`。
- 本阶段开发基线：Avernet `3e0d54256`，分支 `feat/clawinsight-monitoring`；下方历史核对记录仍保留。claw-validation 未在本阶段修改；不代表已验证远端部署版本。2026-09-14 边界收敛：已撤回共享 migration 121；监控 DDL 和本地初始化归属 ClawInsight，受管库继续经 ODC 审批建表。
- 协议依据：[公共接口契约](clawinsight-monitoring-api-contract.md)。公共字段和行为不在本文另设第二套版本。
- 页面基准：[设计 HTML](../../design/clawinsight-realtime-monitoring-demo.html)；与该 HTML 或旧设计文字冲突时，以本次 Spec 的范围和字段为准。

## 0. 文档定位与本次更新

- 负责模块：ClawInsight server / web；依赖：ClawWeb Shared 的既有数据库接口、外部 claw-validation 上报端；本 PR 不修改 Shared、Host 或公共 CI。
- 本主题跨模块、跨仓库，按[领域文档规范](../../agents/domain.md)归入 `docs/specs/2026-09-09-clawinsight-agent-monitoring/`，本文 `spec.md` 为实现入口；同目录公共契约为双方协议依据。不额外拆 plan/tasks 文档。
- 遵守[架构规则](../../arch/arch.rules.md)、[CI 规则](../../arch/ci.enforce.md)、[Context Boundary](../../arch/context-boundary-format.md)和[协议测试规则](../../arch/protocol-contract-tests.md)。领域入口为[Context Map](../../../CONTEXT-MAP.md)；当前已列出的 Skill 生命周期 ADR 不直接规定本次监控上报，不修改这些 ADR。
- 上报端的独立实现合同在 claw-validation 仓库 `docs/specs/clawweb-reporting-integration-spec.md`。本仓库不复制 Python 实现 Spec；通过同修订号公共契约对齐，部署 / PR 交付时注明两端对应版本。

本次相对初稿的核对结论：

| 核对项 | pull 后实际变化 / 事实 | Spec 调整 |
|---|---|---|
| Avernet 基线 | 从 `00a23e51b` 更新为 `43dc46c2c` | 更新代码依据，不推翻独立上报架构 |
| 核心接入 | IDatabase、Insight runtime / 导出工厂、AIStudio service、InsightCenter 外层页面相对旧基线未改 | 原挂载、存储、两模块分离思路保持 |
| 现有治理与修复 | 管理员执行接口新增 diagnosticMode；验收支持显式 allowZeroSession；修复证据投影增强 | 作为必须保留的治理行为，不吸收到监控 DTO |
| 管理页 | InsightCenter 已有管理员专属 `tab=admin`，并非本轮新加 | 修正初稿“只有三个 tab”的不完整描述，新增监控时必须保留它 |
| 数据库 | 统一 schema 已新增 v119（Workflow 跨实例重试选项） | 不占用统一 migration 版本；模块本地初始化与 ODC 发布分工 |
| 进化室 | `/evolve/*` 新增 EvolveRoutes 具名路由包装 | 不覆盖该包装，不为监控重构 `/evolve` |
| Workflow | 新增跨实例 run/log 查询和 retry-request 能力 | 它们不是 CV 诊断接收接口；不接入任务 claim/complete 链路 |
| 测试依赖 | ClawInsight Vitest 与 coverage-v8 均为 3.2.7 | 沿用当前依赖及脚本，不从旧设计恢复旧版本 |

以上表格为实施前的本地 Git 差异和源码核对记录。第一阶段实施范围、运行方式、测试证据与未验证项见[接收端与正式前端开发验证记录](local-development.md)。公共 HTTP 路径、字段、样例和幂等语义保持 `1.0.0-draft.3`；宿主预解析 JSON 的传输限制与坏 JSON 错误格式差异见该说明第 5 节。

> 2026-09-11 联调决策：仅本次监控上报取消应用层 Token。内网访问不等于身份认证；任何可达调用方可能提交合法数据。网络隔离须由部署侧确认，对外开放前重新评审。字段、路径、幂等、存储及其他模块权限不变。

## 1. 一页理解本次工作

**在现有效果中心新增独立的 Agent 监控自愈模块：接收诊断、存入现有业务数据库、查询并展示。** 不在 Avernet 中运行 Python 诊断，不拉取 AIStudio 本地数据库，不接管 Bot 任务执行。

```text
AIStudio 中 claw-validation
       │ POST：一条诊断 / 一个 Bot 检查快照
       ▼
现有 ClawWeb Host → /api/insight/v1 → monitoring Router
                                          │ 字段 + 引擎枚举校验
                                          ▼
                                     Service → Repository
                                                    │ IDatabase
                                                    ▼
                                                业务数据库 ← ODC 查询
                                                    ▲
页面：Agent 监控自愈 → GET：Bot / 状态 / 分页诊断 ───────┘
```

和已有 Agent 治理的关系：

| 项目 | Agent 治理（现有） | Agent 监控自愈（本次） |
|---|---|---|
| 目的 | 历史分析、问题证据、改进待办 | 已上报 Bot 实时诊断结果 |
| 数据产生 | 现有导出 / 分析和治理流程 | 外部 CV 主动单条 POST |
| 页面 | 保留效果概览 / 问题证据 / 我的待办，以及管理员专属“管理” | Bot/日期/刷新、状态、诊断记录 |
| 复用 | Host、页面外壳、模块装配、数据库抽象 | 不复用治理统计模型表达实时诊断 |

“ODC 落表”是通过既有数据库连接写表，再用 ODC 查看同一张表，不新增所谓 ODC HTTP 上传接口。

## 2. 改动范围与边界

必须实现：

1. 公共契约的两个 POST：诊断落库、最新 Bot 检查状态。
2. 同契约三个 GET：已上报 Bot 清单、运行状态、分页诊断。
3. 两张业务表、索引、模块内 DDL、真实持久化与幂等。
4. 设计 HTML 对应的 React 页面，接入现有 Host 外壳。
5. 不连 AIStudio 的本地真实 Router + SQLite + 页面闭环测试。

不实现：Python 迁仓、直接访问 AI Vision、Bot 名称 / 添加 Bot、全量扫描、用户专属监控范围、任务下发、原文存储、钉钉通知字段、认领处理按钮、自愈动作、MQ、SSE/WebSocket、历史回灌工具。

**OCB 无计划业务代码改动。** 沿用当前 Host 装配和 `/api/insight/v1` 挂载。不能把“不改 OCB 业务代码”解释为“不需要部署配置”：内网访问限制、业务库和网关可达性仍须配置及验证；Bot 接入名单不再需要配置。如果既有入口拦截服务请求，应报告具体阻塞点并单独确认最小配置 / 装配调整，不擅自新增另一套 Host。

## 3. 代码导航：已有能力在哪里

以下以 `src/evolverun/clawweb/public/` 为相对根目录。

| 路径 | 已有责任 | 本次用法 / 缺口 |
|---|---|---|
| `modules/clawinsight/server/index.ts` | 模块导出入口 | 保持原导出和工厂兼容，导出新增能力 |
| `modules/clawinsight/server/routes/insight.ts` | createInsightRouter、治理读写路由 | 在现有前缀内组合监控子路由，不新增 OCB app.use |
| `modules/clawinsight/server/services/insight/insight-runtime.ts` | 现有治理运行时装配模式 | 参考工厂 / 注入方式，监控独立 readiness |
| `modules/clawinsight/server/services/aistudio-service.ts` | 现有 AIStudio 出站请求 / 配置 | 只参考服务端配置注入模式；不是本次入站上报实现 |
| `modules/clawinsight/server/repositories/` | 治理数据持久化 | 参考 Repository 风格，新增独立表，不写入治理指标表 |
| `shared/server/db.ts` | IDatabase、SQLite/MySQL/ZDAS/noop 适配与事务 | 注入同一个业务库；监控明确拒绝 noop |
| `modules/clawinsight/server/services/monitoring/schema.ts` | 模块专属 DDL、类型适配、本地 SQLite 初始化 | 受管库由 ODC 发布；线上运行时只检查和读写，不执行监控 DDL |
| `modules/clawinsight/web/pages/InsightCenter/index.tsx` | 现有效果中心页面 | 增加模块切换，保留治理入口行为 |
| `modules/clawinsight/web/api/insight.ts` | 治理前端 API | 新增独立 monitoring API，避免混用治理响应类型；保留新 diagnosticMode 参数 |

注意两处不能照抄的行为：

- 本轮监控 POST 不执行应用层 Token 校验，依赖部署侧受限内网入口。不得修改旧治理、其他 internal API 的权限行为。
- 现有 AIStudio service 的平台 Token 仅用于出站调用，保留不变；本次不增加监控上报秘密配置。

### 3.1 本次不包含 Host 外观重构

本次只保留 ClawInsight 页面自身的 Monitoring 入口和必要样式。为页面构建服务的 Tailwind source 声明可以保留，例如：

```css
@source "../../../modules/clawinsight/web";
```

公共 Header、Logo、主导航及其他 Host 外观调整不属于本需求；如果确有必要，必须拆成独立 PR，避免借 Monitoring 接入扩大改动范围。

## 4. 运行时装配与配置

### 4.1 装配要求

新增 `createMonitoringRuntime`，但它只负责根据已解析的依赖创建 Monitoring Repository 和 Service；不得自行读取最终环境配置、创建数据库连接或启动另一套进程。配置和数据库必须由 composition root 显式传入。

- OCB Host 负责加载最终合并配置、创建业务数据库连接，并在装配时创建 Monitoring Runtime；
- Avernet public Host（如用于本地/公开组合启动）负责使用其自身已解析的配置和数据库连接完成同样的装配；
- `createInsightRouter(..., { monitoring })` 接收已创建的 Runtime，Router 只负责挂载，不负责隐式初始化；
- `createMonitoringRuntime` 的参数至少包含 `db` 和已解析的 Monitoring 配置，不能在内部直接读取 `process.env` 或全局 `getRepositories()`；
- 测试必须显式传入 fake DB / 配置，不能依赖进程全局环境才能完成单元测试。

核心业务代码不直接读取环境或依赖 Express。

现有 `createInsightRouter(service, options)` 参数保持兼容，新增可选 monitoring 注入。生产调用方必须在 composition root 完成 Runtime 创建并通过 `options.monitoring` 注入；不再允许 Router 在缺少 Runtime 时自行读取环境或全局 Repository 进行隐式装配。测试可以显式注入。若某个 Host 未装配 Monitoring，则只返回可识别的未就绪结果，不影响既有治理路由。

监控子路由注册在治理兜底 / 错误处理之前；不能因为治理 `InsightService` 为 null，就提前返回导致监控所有路由不可用。监控不可用只影响自己的路径，既有治理继续运行。监控专用错误适配器仅处理自己的请求。现有 `server/index.ts` 只是导出，不是已经初始化好的 Monitoring Runtime；实际装配必须由 Host composition root 完成，并用 Host 装配测试证明确实可达。

### 4.2 监控默认装配与动态 Bot 发现（2026-09-15 修订）

移除 `CLAWWEB_MONITORING_ENABLED` 和 `CLAWWEB_MONITORING_BOTS_JSON`。
这两个旧变量无论缺失、为空、为 false 或包含非法 JSON，均不得影响 Host 启动、监控装配或合法上报。
2026-09-16 修订：同时移除 `CLAWWEB_MONITORING_STALE_SECONDS`，不读取或校验其残留值。
状态新鲜度固定为 300 秒：当前时间距离最新 `checkedAt` 超过 300 秒时显示 UNKNOWN；
最新检查为 PAUSED 时仍保持 PAUSED，`lastSuccessfulCheckAt` 仅保留真实成功时间。
配置为空或非法不再导致监控 503；显式未装配或数据库/表结构/读写故障仍返回 503。

- CV 决定采集哪些 Bot；ClawWeb 不再维护第二份接入名单。
- 合法检查或诊断成功持久化后，Bot 自动可见。列表查询两张既有表中 `bot_id` 的去重并集，按 ASCII 二进制顺序排序。
- 不新增注册表、内存名单或外部发现依赖；原有诊断和检查数据直接参与发现，多实例与重启后结果一致。
- 首次上报之前不预展示 Bot；空库返回 `{ "items": [] }`。已有诊断但没有检查的 Bot 状态为 UNKNOWN。
- 所有 GET 均依赖监控存储；存储不可用返回 503，不伪装空列表。数据库延迟解析，不因监控数据库尚未可用而阻止 Host 启动。
- 未知 Bot 的 status/diagnoses 返回 404；合法新 Bot 的 POST 不再返回名单相关 403。
- `engine` 仍校验为 OC/TE，但不再与环境名单比对；身份仍由全局 `botId` 标识，不引入 `(botId, engine)` 复合身份。两个不同 Bot 必须使用不同的真实 ID，不能都上报为 `default`。
- 暂停来自最新有效 BotCheck 的 PAUSED 状态，直到新检查替换；不再有配置级暂停。

数据库连接沿用既有 `DATABASE_MODE` 配置。不要求 MONITORING_REPORT_TOKEN。
OCB 删除启动脚本中两个旧变量的导出和兜底名单，不提供新名单变量。
完整兼容性与验收说明见 [无配置监控发现](clawinsight-monitoring-auto-discovery.md)。

### 4.3 服务请求安全

- 两个监控 POST 不要求或校验 Authorization。保留请求大小、JSON、字段、引擎枚举和幂等校验。
- 不产生 Token 相关 401/503；模块或存储未就绪仍拒绝。网关登录 HTML 不得被发送端当作成功 ACK。
- GET 沿用 Host 登录态；不套用 Agent 治理 owner / Bot ownership 范围；进入页面的用户共享已上报 Bot 结果。本原则只适用于新增监控路径，不得删除旧治理或修复接口的管理员 / owner 校验。
- 不把浏览器可随意提交的身份 Header 当新权限系统；沿用可信 Host 边界。
- 在 JSON parser 层实施 128 KiB 限额；若父级 parser 已先执行，须在 Avernet 装配点协调限额与错误适配，不能宣称子路由 parser 可以重新限制已消费的 body。
- 错误体、Header 校验、未知字段拒绝与 HTTPS 边界以公共契约为准。

## 5. 数据库：两张表即可

### 5.1 diagnosis 表

表名固定为 `insight_monitoring_diagnoses`，一条已固化诊断一行；首版不自动删除历史记录。

| 列 | 逻辑类型 / 约束 | 用途 |
|---|---|---|
| id | 各方言现有自增主键惯例 | 内部行标识，不暴露页面 |
| event_id | VARCHAR(128)，NOT NULL，唯一 | 公共 eventId；diagnosisId 等于该值，不重复存两列 |
| schema_version | VARCHAR(64)，NOT NULL | 上报格式版本 |
| bot_id | VARCHAR(128)，NOT NULL | 上报的业务 Bot |
| engine | VARCHAR(2)，NOT NULL | OC / TE |
| session_key | VARCHAR(1024)，可空 | 上游键 |
| session_id / trace_id | VARCHAR(255)，可空 | 真实定位 |
| occurred_at_ms | BIGINT，可空 | UTC Unix 毫秒，会话时间 |
| diagnosed_at_ms | BIGINT，NOT NULL | 最终诊断完成时间 |
| decision | VARCHAR(16)，NOT NULL | ALERT/PASS/UNRESOLVED |
| tc_fault_label | VARCHAR(128)，可空 | TC 标签 |
| confidence_json | VARCHAR(32)，可空 | 有限 JS number 的规范 JSON 十进制表示，读取转 number；见下文 |
| business_problem_category / business_problem_subtype | VARCHAR(128)，可空 | 业务问题分类 |
| system_diagnosis / business_diagnosis | TEXT，可空 | 已脱敏文本，入口限制各 8000 码点 |
| handler_name | VARCHAR(128)，可空 | 可选归属信息 |
| human_intervention | SQLite INTEGER / MySQL BIGINT，NOT NULL，只允许 0/1 | API 映射为 boolean |
| received_at_ms | BIGINT，NOT NULL | 服务端首次接收时间，不参与幂等比较 |
| gmt_create / gmt_modified | 遵循现有 migration 方言惯例 | 数据库审计时间，不替代业务时间 |

不保存 raw request、原文或 notification_status。confidence 使用规范数字文本，是为了遵守现有“不用 FLOAT/DOUBLE”规则，又不让任意小数被固定 DECIMAL 精度舍入后导致重试冲突；API 仍严格是 number/null，禁止透传字符串。ODC 如需数值统计可显式 CAST 到业务需要的 DECIMAL 精度。这是存储表示，不扩展公共字段。

索引至少包含：

- `UNIQUE(event_id)`，ID 大小写敏感；不能用默认不区分大小写排序规则把两个 ID 错当相同。
- `(bot_id, occurred_at_ms, event_id)` 支撑分页。
- `(bot_id, decision, occurred_at_ms, event_id)` 支撑 decision 过滤。

bot_id / event_id 的比较、排序采用 ASCII 二进制语义（SQLite BINARY；生产 ID 列使用 `CHARACTER SET latin1 COLLATE latin1_bin`，入口仍严格限制 `[A-Za-z0-9_.:-]+`），跨方言测试。不为长文本建立前缀索引冒充全文搜索；首版监控 Bot 的关键词使用参数化字面子串查询，数据增长后基于查询计划再优化。

### 5.2 最新 Bot 检查表

表名固定为 `insight_monitoring_bot_checks`：id 自增主键、bot_id 唯一 VARCHAR(128)、engine VARCHAR(2)、checked_at_ms BIGINT、last_successful_check_at_ms 可空 BIGINT、status VARCHAR(16)、received_at_ms BIGINT，以及 gmt_create/gmt_modified。

只存每 Bot 最新一行，不建每日心跳历史表，不把诊断总数反复回写到此表。PAUSED 来自最新有效检查上报，历史成功时间保留。

### 5.3 建表交付与数据库接入

**两张表与既有 ClawInsight 表共用同一个 `IDatabase`，不通过 ODC HTTP API 读写。**

- 核对证据、已确认结论与验收边界：[数据库接入核对记录](database-research.md)。
- 审批建表稿：[schema.mysql.sql](schema.mysql.sql)。表名采用 `insight_` 前缀，与既有 ClawInsight 表命名保持一致，Repository 和模块 DDL 使用上述完整固定名称；该前缀不是驱动自动添加的。
- 发布后核验：[verify.mysql.sql](verify.mysql.sql)。文件只含只读查询，不执行建表或写入业务数据。
- `schema.mysql.sql` 面向 OceanBase MySQL 兼容模式 / MySQL，尚未在目标库执行。ODC 单表结构编辑器每个结构填写一条 CREATE；本次截图所示“编写 SQL”批量入口允许不同表的多条 CREATE，可以粘贴本文件全部内容。每张表的发布目标名称须与 SQL 表名一致。确认目标库后，完成生成 SQL 任务、审批和实际执行；仅保存结构设计不表示已建表。
- 审批时检查生成的最终 DDL，而非只看输入 CREATE。ODC 会对已有同名表做差异比较；发现同名非本模块表或结构不符时停止，不自动改造旧表。本稿不含 USE、DROP、ALTER 或共享 schema_version 写入。
- 默认不分库分表、不加分区、不关联 `ac_bots` 外键；Bot 清单由两张监控表中的持久化数据发现。若部署目标实际为逻辑分片库，必须先确认单表路由/发布拓扑，不能直接把物理建表稿当成逻辑路由配置。

#### 连接与表名如何对应

```text
内部 Host / 公共配置 + 环境覆盖
  → configureClawWebRuntimeConfig
  → initDatabase / resolveDbConfig
  → mysql2 连接池（host、port、user、password、database）
  → 同一个 IDatabase 注入 ClawInsight / Monitoring Repository
  → SQL 中的 insight_monitoring_diagnoses / insight_monitoring_bot_checks
```

公开实现依据：

- [`shared/server/db.ts`](../../../src/evolverun/clawweb/public/shared/server/db.ts)：`resolveDbConfig` 读取 `DATABASE_MODE`，以及 `ZDAS_HOST/PORT/USER/PASSWORD/DATABASE`；环境变量优先于配置的 `database.zdas.datasources[0]`。`initMysql` 将这些值传给 `mysql.createPool`。旧值 `DATABASE_MODE=prod` 只是 `zdas` 别名，不等于选中了生产环境。
- [`insight-runtime.ts`](../../../src/evolverun/clawweb/public/modules/clawinsight/server/services/insight/insight-runtime.ts)：从注入的 `db` 建立各 Repository。
- [`insight-task-index-repository.ts`](../../../src/evolverun/clawweb/public/modules/clawinsight/server/repositories/insight-task-index-repository.ts)：SQL 直接访问 `insight_failure_task`，没有 `ac_` 自动改写。监控表采用相同方式，但使用新表名。

**已与项目方确认（2026-09-11）：沿用当前 ClawWeb 已有的数据源接入链路即可访问目标业务数据库。** 数据源名称与 ODC 展示名称的差异不再作为本需求的待确认项或开发阻塞项。监控 Repository 复用现有 ClawInsight 注入的 `IDatabase`，不新增连接、不替换共享 `ZDAS_DATABASE`、不另建 ODC HTTP 接口。ODC 项目名称和展示标签不作为应用连接参数。各环境按现有流程发布两张新表，不能从某一环境建表成功推断另一环境也已发布。

本需求不因数据库接入新增 OCB 业务代码或凭据配置。建表后按正常联调验收核验新表读写：用既有服务连接读表，再用本契约 mock 上报验证写入及查询；这属于新表验收，不是重新设计数据源。个人 ODC 权限与服务账号权限仍是不同概念，若验收出现权限错误，沿现有流程处理。不要向前端或 claw-validation 发放数据库密码。

#### 受管库与本地初始化的分工

本模块不再往 Shared 注册监控 migration，也不让 Shared 反向依赖 ClawInsight。线上监控只消费 Host 提供的 IDatabase，不负责建表；本地测试显式调用模块的 `initializeMonitoringSqlite`，只建立两张监控表及其索引和触发器，不运行全站迁移、不写共享 `schema_version`。执行分工如下：

| 场景 | 建表负责方 | 应用行为 |
|---|---|---|
| 本地 SQLite / 监控定向测试 | 模块显式 SQLite 初始化 | 只建立两张监控表，运行契约测试 |
| 禁止应用 DDL 的受管库 | 经审批的 ODC 结构发布 | 只做业务读写与结构就绪校验，不要求为监控授予 CREATE 权限 |
| 已人工建表的受管库（即使账户有 DDL 权限） | ODC 管理表结构 | 仍只做结构核验和业务读写，不执行建表或迁移 |

新增 monitoring 就绪校验须确认必需列、类型/长度、可空性、ID 大小写敏感语义及唯一/查询索引；不能只看连接成功或 `CREATE IF NOT EXISTS` 没报错。缺表、结构不兼容、无访问权限或 noop 时，依赖 DB 的监控接口返回 503，不假 ACK，不返回伪造空历史；不要拖垮原有 Agent 治理。不得手动抬高共享 `schema_version` 来跳过别的模块迁移。

#### 本次 ODC 校验兼容性修正

用户在“编写 SQL”入口校验旧稿时，两个错误均定位在 ID 列的 `CHARACTER SET ascii`。根据错误定位，优先判断为该入口解析器兼容性问题，尚未复现内部解析器，不能归因于数据库一定不支持 ASCII，也不能归因于批量输入两条 CREATE。

本稿将三处 ID 列改为 `CHARACTER SET latin1 COLLATE latin1_bin`，保留单字节存储、ASCII 合法 ID 的大小写敏感匹配与排序；其余中文字段仍继承表级 utf8mb4。SQL 文件只保留两条 CREATE，方便批量上传。此改动是待用户重新校验的兼容候选，不代表已通过 ODC 或真实建表测试；建表后需核验列字符集/字符序未被平台改写。

### 5.4 Schema 归属、migration 与类型约束

#### 目录与 Owner 约束

Monitoring 是 ClawInsight 的子能力，不新增独立 npm package。前端、Router、Repository、Service、schema helper、fixture 和测试均位于：

```text
public/modules/clawinsight/
```

不得将 Monitoring 代码复制到 OCB、Host 或 Archive；不得为此新增 ACI；不得通过跨 package 相对路径读取另一仓库源码。OCB 只负责最终配置、数据库创建和 Host composition。



- Monitoring 专属 DDL 与 schema helper 归属 `modules/clawinsight/server/services/monitoring/schema.ts`；`public/shared` 只保留通用 `IDatabase`、Dialect 和 migration 执行能力，不再新增 Monitoring 专属表定义。
- 不在 `shared/server/schema.ts` 注册监控 migration；禁止 Shared 反向依赖 ClawInsight。受管库以 ODC 发布为唯一监控建表入口；模块 SQLite 初始化仅供本地 Host 和测试显式调用，并拒绝 MySQL/ZDAS。
- 撤回本 PR 新增的共享 migration 121、共享监控触发器和方言中的监控表特判。已有数据库的监控表、数据和 schema_version 不做删除或回退；不要因为源码撤回注册而执行数据库清理。
- 审批稿与模块 DDL 必须做结构一致性测试；审批后的表名/列/索引变化须同步此文件和 SQL 稿，不只改一处。现有 renderer 会将所有 `VARCHAR(255)` 缩为 `VARCHAR(190)`，但本契约 session_id / trace_id 允许 255 码点且不建索引：模块 renderer 必须对监控 DDL 做窄范围适配以保留 255，禁止静默截断或全局改变旧模块规则。ID 列 `latin1_bin` 同样须验证 SQLite 转换与生产保留行为；API 仍只接受 ASCII ID，不因存储字符集可表示更多字符而放宽协议。
- 遵循 SQLite canonical / 方言 render 机制。MySQL / ZDAS 列 COMMENT、gmt 字段、内联索引、索引列 VARCHAR 等既有规范必须通过；不要仅写 SQLite DDL 后宣称生产已完成。
- API 入口及 Repository 共同限制 human_intervention 为 0/1、枚举合法值和 confidence 范围；本建表稿不依赖跨版本 CHECK/ENUM 行为，不允许绕过校验的业务写入。
- SQLite boolean 为 0/1，MySQL 驱动 BIGINT / 数字可能返回字符串，Repository 必须显式转换并检查安全范围。日期有效范围限制在公共 RFC3339 格式支持范围，不能溢出 JS 安全整数。
- 业务时间统一毫秒，不套用本地时区的通用格式化 helper；只在 API 序列化为 UTC，在页面显示北京时间。
- 初始化 / migration 失败返回 503；`NoOpDatabase` 不得假落库。数据库失败不返回空数组、零计数或 accepted=true。
- 回滚应用不删除新表和记录；不复用 / 修改治理统计表，保留可重新上线的数据。

## 6. 接收服务的执行顺序

### 6.1 DiagnosisEvent

```text
限额/JSON/字段校验 → Bot allowlist 校验 → 归一化
 → 尝试持久插入（唯一键裁决）
     ├─ 新插入并提交：201
     ├─ 唯一冲突：读取已存字段，归一化比较
     │     ├─ 完全相同：200 duplicate=true
     │     └─ 不同：409，不覆盖
     └─ 其他数据库异常：503/500，不 ACK
```

按契约所有事件字段比较，不只比较摘要或几项关键字段；服务器生成的接收时间不参与。省略可空值与 null 等价，等价时区转同一毫秒，键顺序无关。从列重建规范对象后比较即可，无须额外存完整原始请求。

并发安全依赖数据库唯一约束，而不是进程内“先查再写”。唯一冲突处理要兼容方言事务语义；事务回滚后再读取已提交结果，暂不可见时有限重读 / 返回可重试错误，不假报冲突。实际有事务时，提交完成之后才能返回 ACK。

SQLite 当前单连接异步 transaction 使用须特别测试并发 BEGIN / COMMIT；需要时在监控 Repository 的同连接事务入口做窄范围串行化，不大规模重写共享数据库层。进程内锁不是跨实例幂等依据。

### 6.2 BotCheck

同样先请求 / 字段校验；以唯一 bot_id 和 checked_at_ms 做原子更新或行锁事务：

- 新时间更新，200 applied=true。
- 旧时间忽略，200 applied=false。
- 同时间同内容，200 applied=false；同时间不同内容 409。
- checkedAt 比服务时钟超前 5 分钟、成功时间晚于检查时间，400。

并发乱序不能覆盖新状态。接收时间不是检查时间，不因失败重试把旧 HEALTHY 刷新成在线。

## 7. 查询与页面状态

### 7.1 后端

- bots 从检查和诊断表读取去重并集，按 botId 排序；首次成功上报后可选择。
- status 的 diagnosisCount 为该 Bot 全部已存诊断数，不受页面日期影响。检查过期默认 UNKNOWN；最新检查为 PAUSED 时优先显示 PAUSED；保留最后真实成功时间。
- 日期为北京时间自然日，结束日转换为下一天零点的半开区间；任一日期过滤排除 occurredAt=null，全部时间 null 排最后。
- page、pageSize、keyword、decision、counts / total 规则严格遵守公共契约；数据库分页，不调用已有全量 diagnoses() 之类方法加载全部记录。
- 同时间按 event_id ASCII 倒序；显式 CASE/null 排序保证各方言一致。
- keyword 对 `%`、`_`、反斜杠按字面匹配，使用参数化条件及经过方言测试的转义；不能直接拼 SQL。ASCII 英文字母大小写不敏感，与 ID 精确匹配的大小写规则分开。
- counts 忽略 decision 但应用 Bot/日期/关键词；items 和 total 应用全部条件。同次响应的 counts、total、items 通过同一一致读取事务或等价数据库快照获取，避免自相矛盾；不承诺跨页 / 跨刷新快照。
- GET 设置 no-store；存储故障清晰报错，不能伪装“暂无诊断”。一页响应已含详情，不再发详情 / 原文请求。

### 7.2 前端布局

保留 AgentEvolve 顶部全局导航、现有效果中心外壳；不要在模块内复制一套顶栏。效果中心左侧仅两项：

1. Agent 治理：普通用户原有三个 tab 和行为不变；管理员仍保留 `tab=admin` 的“管理”页及权限。这里的“两项左侧模块”不意味着删掉治理内部的管理功能。
2. **Agent 监控自愈**：本次新页面。

右侧只组织三块主内容：

- **筛选组件**：已上报 Bot ID 切换、时间范围、刷新放在同一组件；默认第一个 Bot、全部时间。无 Bot 名称、手动添加、任务来源或数据源标签。
- **状态区**：HEALTHY 显示“监控正常”、ERROR“检查异常”、UNKNOWN“状态未知”、PAUSED“已暂停”；最后成功检查时间、累计诊断数。状态描述的是采集检查，不暗示任务全部成功。
- **诊断记录**：结果筛选、关键词搜索、清晰可展开条目和分页；不要拆成“实时任务”和“诊断历史”。

条目概要突出会话时间、诊断结果、问题分类 / 摘要和定位；展开呈现 Session Key / Session ID / Trace ID（按有值字段）、TC 标签、置信度、系统 / 业务诊断、处理人、是否人工干预。空值显示“—”，null 时间显示“时间未知”。详情 humanIntervention=true/false 显示“是/否”，不显示“需要处理”。

删除“通知状态：已通知钉钉”及相关徽标。不出现认领、执行修复、处理告警按钮。无需开发说明长段或“全部时间 · 按会话时间筛选（北京时间）全部时间”冗余小字；日期控件和时间格式自身清楚即可。

已有 HTML 若残留 Bot 名称 / 通知字段，实施时同步修正 fixture；不是为了机械复刻旧 mock 而保留废弃字段。本轮交付 Spec，不修改 HTML。

### 7.3 交互与更新

- 新模块建议以 `module=monitoring` 表示；缺省沿用治理，保留旧 `?tab=overview` 等链接。切换到监控不覆盖治理 tab 状态（含管理员 `admin`）、owner / improvement 定位；监控不得读取这些治理范围参数作为自己的权限或 Bot 过滤。模块 URL 由既有 router 管理，不操作 Host 全局路由体系，不修改新增的 `/evolve/*` EvolveRoutes 包装。
- 默认页大小 10，允许 10/20/50；零条隐藏无效翻页或禁用按钮，越界页提示可返回首页，后端不悄悄调整页码。
- Bot、时间、decision、keyword、pageSize 改变回到第 1 页，清理展开状态；手动刷新保留筛选和当前页。切换 Bot 立即清除旧 Bot 数据，不让旧记录暂挂在新标题下。
- 搜索可去抖 300ms；每次查询绑定完整筛选 key 和请求序号 / AbortController，旧响应不可覆盖新筛选结果。
- 首版页面可见且停留监控模块时每 30 秒轮询 Bot 清单、status 与当前诊断列表；隐藏 / 切到治理时暂停，返回时立即刷新。不请求后台原文，不新增推送通道。
- 同筛选刷新保留已有列表，显示轻量刷新态；失败保留旧数据显示错误，不能伪装最新结果。初始加载、空记录、未就绪、鉴权、错误分别展示。
- 旧状态需要随时间失效；请求失败不能让很久以前的“监控正常”无限保持，前端应标注状态未更新 / 暂不可用，不自行推断 PAUSED。
- 页面同时展示全部用户共享已上报 Bot 的结果，不给监控新增管理员控制面板；既有治理的管理员控制面板保留。治理 overview 的初始数据请求应收敛到治理模块生命周期，不能在监控页面挂载时继续隐式依赖该请求。

## 8. 拟新增 / 修改文件

以下路径均在 `src/evolverun/clawweb/public/` 下；新增名称可按项目风格微调。

| 路径 | 责任 |
|---|---|
| `modules/clawinsight/server/services/monitoring/contracts.ts`（新增） | 领域 DTO 与枚举，不依赖 Express |
| `modules/clawinsight/server/services/monitoring/monitoring-ingest-service.ts`（新增） | 验证后事件归一化、动态 Bot 发现策略、幂等 |
| `modules/clawinsight/server/services/monitoring/monitoring-read-service.ts`（新增） | 状态过期、分页查询语义 |
| `modules/clawinsight/server/services/monitoring/monitoring-runtime.ts`（新增） | 配置、依赖装配、read/write readiness |
| `modules/clawinsight/server/repositories/monitoring-repository.ts`（新增） | IDatabase、方言查询、唯一键处理、状态原子更新 |
| `modules/clawinsight/server/routes/monitoring.ts`（新增） | POST/GET 适配、Header/body/query 校验、错误响应 |
| `modules/clawinsight/server/routes/insight.ts`、`server/index.ts` | 在原工厂路径组合导出，不要求 OCB 新路由 |
| `modules/clawinsight/server/services/monitoring/schema.ts`（新增） | 监控 DDL、局部方言适配、仅本地 SQLite 初始化 |
| `modules/clawinsight/web/api/monitoring.ts`（新增） | 同源 GET、类型、错误映射，不含写 Token |
| `modules/clawinsight/web/pages/InsightCenter/index.tsx` | 两模块切换，旧治理兼容 |
| `modules/clawinsight/web/pages/InsightCenter/monitoring/`（新增） | 筛选、状态、条目、分页、样式与页面测试 |
| 模块现有测试目录 / `server/fixtures/monitoring/`（新增） | 公共样例与实际 Router 测试 |

不新增顶级微服务或重构其他治理任务框架；遵循仓库架构边界、环境读取位置和契约测试要求。公共 App 顶部导航、全局样式、根 `.gitignore`、Shared 和 `scripts/ci` 保持目标分支基线。测试串行策略放在 ClawInsight 的 Vitest 配置中，测试不跳过；监控 `.local/` 忽略规则放在模块 `.gitignore`。

## 9. 本地 Mock：真实接口，不是假页面闭环

实现时将公共契约 §8 的 alert/pass/unresolved 样例保存为 fixtures；实现一个测试用本地 Host，复用生产 monitoring Router、真实 SQLite Repository 和模块初始化，监听 loopback，注入仅测试凭据。不要求 AIStudio 可达，不引入公司内网依赖来“完成本地开发”。

步骤：

1. 准备独立 SQLite 文件，不配置监控开关、Bot 清单和上报 Token；发送 mock 数据后验证 Bot 自动发现。
2. 用公共契约 curl POST 三个样例；写当前时间的 BotCheck。
3. GET 确认 TE 两条、OC 一条；打开 React 页面验证筛选 / 展开 / 翻页。
4. 重复 POST、冲突 POST、无 Token 上报、数据库失败分别验证。
5. 重启本地 Host，GET 记录保持。
6. 生成至少 25 条合法唯一 ID fixtures 验证多页、counts 和人工干预展示；不要只测一个静态条目。

测试 Host 的启动命令在实现时添加到 package scripts / 测试说明，当前不捏造不存在的 npm 命令。模块现有 `check`、`build`、`test` 脚本可作为实现后的基础检查入口，使用仓库实际包管理方式安装依赖；本 Spec 交付不代表已运行这些业务测试。

## 10. 验收与交付顺序

### 10.1 开发步骤

1. 评审公共契约，固定 fixtures 和配置默认值。
2. 模块 DDL / 本地初始化 + Repository + 两个写接口，先跑实际 SQLite 接收闭环。
3. 三个 GET 和跨方言查询测试。
4. React 页面、路由切换和竞态处理。
5. CV 发送器对本地服务联测；最后验证真实 Host、AIStudio 出站、生产库与 ODC 一致。

### 10.2 必须通过的用例

| 范围 | 验收 |
|---|---|
| 挂载 | 原有工厂调用能提供新接口；治理 service 不可用不误伤独立监控；旧治理路由、管理员专属管理页不回归 |
| 内网接入 | 无 Token 配置、无 Authorization 时两个 POST 均成功；旧 Token 配置/头不重新启用校验；非法字段/非法引擎/存储失败仍拒绝；合法新 Bot 自动接入；上游登录 HTML 不得假成功 |
| 入库 | 首次201、重复200、冲突409；20个并发相同POST只落一行 |
| 故障 | noop / DB断开 / migration失败 / 缺表或结构不符无假ACK；ACK丢失重复不新增 |
| 字段 | humanIntervention true/false保存一致；不含通知状态；置信度仍为number/null |
| 状态 | 无新会话可HEALTHY；超时UNKNOWN；乱序旧检查不覆盖；A/B Bot不串 |
| 查询 | 北京时间边界、单边日期、null时间、大小写、通配符字面搜索、越界页、counts/total |
| 并发查询 | 同响应统计一致；分页期间新数据不要求跨页快照，但不得错Bot或错筛选 |
| UI | Host顶部导航保留；两个左侧模块；分页/展开/筛选/刷新；无名称/来源/通知/操作按钮 |
| 前端竞态 | A慢请求晚于B返回不能覆盖B；切模块/卸载停止轮询；进入监控不触发治理 overview 请求；错误不展示假空数据 |
| 回归 | 普通用户三个治理tab、管理员管理tab及原URL可用；保留 diagnosticMode / allowZeroSession / 修复证据字段现有用例；新模块失败不影响旧模块 |
| 持久性 | 本地重启记录保持；真实库SQL/ODC查询与页面相同botId/diagnosisId/时间/人工干预 |

### 10.3 真实环境通过标准

本地通过后仍须确认：既有 Host 的上报路径能够接收服务请求；部署侧内网访问范围已确认；AIStudio 可访问实际入口；实际 `IDatabase` 方言迁移 / 唯一索引 / 时间 / 排序均通过；ODC 查询的库表确实与服务连接一致。记录所验证的版本和环境，不能以 SQLite 成功代替生产 SQL 成功。

这些是接入验收的前提，不是新增一套 OCB 业务实现。首次接入可选一个 Bot 上报验证后再扩到采集端目标清单；不删除历史，不自动从旧 SQLite 回灌。

出现问题时暂停 CV 发送或在既有网关关闭内部上报入口，并保留表；旧 Agent 治理仍可用；CV 保留 outbox，不能为了回滚删除未确认事件。恢复后相同事件重试依赖幂等完成交付。

> 真实库证据（2026-09-11）：经 meshboot 本地代理对 ODC dev 库跑通真实 HTTP 路由写读到 `insight_monitoring_diagnoses` / `insight_monitoring_bot_checks`，17/17 通过；表结构（`latin1_bin`、`gmt_*` 默认值、唯一索引）与 `monitoring-schema-check` 兼容。详见 [本地验证](local-development.md) 第 5.1 节与同目录 `verify-odc-dev-e2e.cjs`。此项不替代真实内部 Host 挂载、AIStudio 出站与 prod 库的预发验证。
