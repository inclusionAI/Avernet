# ClawInsight：Agent 监控自愈设计 Spec

本文是 ClawInsight「Agent 监控自愈」模块的公开设计文档，描述功能定位、接口语义、数据模型、运行时装配约定、页面交互与验收标准。与具体部署环境相关的内部配置、审批与验证记录不属于本文范围。

## 1. 功能定位

在效果中心新增独立的 Agent 监控自愈模块：**接收外部诊断上报、持久化、查询并展示**。本仓库不运行诊断逻辑本身，不接管 Bot 任务执行。

```text
外部诊断上报端（持续监控 Bot 会话并产出诊断）
       │ POST：一条诊断 / 一个 Bot 检查快照
       ▼
ClawWeb Host → /api/insight/v1 → monitoring Router
                                     │ 字段 + 引擎枚举校验
                                     ▼
                                 Service → Repository → IDatabase → 业务数据库
                                                                     ▲
页面：Agent 监控自愈 → GET：Bot / 状态 / 分页诊断 ────────────────────┘
```

与已有 Agent 治理的关系：

| 项目 | Agent 治理（现有） | Agent 监控自愈（本模块） |
|---|---|---|
| 目的 | 历史分析、问题证据、改进待办 | 已上报 Bot 的实时诊断结果 |
| 数据产生 | 导出 / 分析和治理流程 | 外部上报端主动单条 POST |
| 页面 | 效果概览 / 问题证据 / 我的待办 / 管理 | Bot / 日期 / 刷新、状态、诊断记录 |
| 复用 | Host、页面外壳、模块装配、数据库抽象 | 不复用治理统计模型表达实时诊断 |

## 2. 范围与边界

必须实现：

1. 两个 POST：诊断落库、最新 Bot 检查状态。
2. 三个 GET：已上报 Bot 清单、运行状态、分页诊断。
3. 两张业务表、索引、模块内 DDL、真实持久化与幂等。
4. React 页面接入现有 Host 外壳。
5. 不依赖上报端可达的本地真实 Router + SQLite + 页面闭环测试。

不实现：诊断逻辑迁入本仓库、Bot 名称 / 添加 Bot、全量扫描、用户专属监控范围、任务下发、会话原文存储、通知状态字段、认领处理按钮、自愈动作、MQ、SSE/WebSocket、历史回灌工具。

## 3. 代码导航

以下以 `src/evolverun/clawweb/public/` 为相对根目录。

| 路径 | 责任 | 本模块用法 |
|---|---|---|
| `modules/clawinsight/server/index.ts` | 模块导出入口 | 保持原导出和工厂兼容 |
| `modules/clawinsight/server/routes/insight.ts` | createInsightRouter、治理路由 | 在现有前缀内组合监控子路由 |
| `modules/clawinsight/server/services/insight/insight-runtime.ts` | 治理运行时装配模式 | 参考工厂 / 注入方式，监控独立 readiness |
| `modules/clawinsight/server/repositories/` | 治理数据持久化 | 参考 Repository 风格，新增独立表，不写治理指标表 |
| `shared/server/db.ts` | IDatabase、SQLite/MySQL 适配与事务 | 注入同一个业务库；监控明确拒绝 noop |
| `modules/clawinsight/server/services/monitoring/schema.ts` | 模块专属 DDL、本地 SQLite 初始化 | 受管库由部署方流程发布；线上运行时只检查和读写 |
| `modules/clawinsight/web/pages/InsightCenter/index.tsx` | 效果中心页面 | 增加模块切换，保留治理入口行为 |
| `modules/clawinsight/web/api/insight.ts` | 治理前端 API | 独立 monitoring API，不混用治理响应类型 |

## 4. 运行时装配与配置

### 4.1 装配要求

`createMonitoringRuntime` 只负责根据已解析的依赖创建 Repository 和 Service；不自行读取环境配置、创建数据库连接或启动进程。配置和数据库必须由 Host composition root 显式传入：

- Host 负责加载最终配置、创建业务数据库连接，并在装配时创建 Monitoring Runtime；
- `createInsightRouter(..., { monitoring })` 接收已创建的 Runtime，Router 只负责挂载，不负责隐式初始化；
- 核心业务代码不直接读取环境或依赖 Express；测试显式注入 fake DB / 配置。

监控子路由注册在治理兜底 / 错误处理之前；治理 Service 为 null 不得导致监控路由不可用。监控不可用只影响自己的路径。监控专用错误适配器仅处理自己的请求。未装配 Monitoring 的 Host 返回可识别的未就绪结果，不影响既有治理路由。

### 4.2 动态 Bot 发现

不维护任何 Bot 名单配置：

- 上报端决定采集哪些 Bot；ClawWeb 不维护第二份接入名单。
- 合法检查或诊断成功持久化后，Bot 自动可见。列表查询两张表中 `bot_id` 的去重并集，按 ASCII 二进制顺序排序。
- 不新增注册表、内存名单或外部发现依赖；多实例与重启后结果一致。
- 首次上报前不预展示 Bot；空库返回 `{ "items": [] }`。已有诊断但没有检查的 Bot 状态为 UNKNOWN。
- 所有 GET 依赖监控存储；存储不可用返回 503，不伪装空列表。数据库延迟解析，不阻止 Host 启动。
- 未知 Bot 的 status/diagnoses 返回 404；合法新 Bot 的 POST 不报名单类错误。
- `engine` 校验为 OC/TE；身份由全局 `botId` 标识，不引入 `(botId, engine)` 复合身份。
- 暂停来自最新有效 BotCheck 的 PAUSED 状态，直到新检查替换。
- 状态新鲜度固定为 300 秒：当前时间距最新 `checkedAt` 超过阈值显示 UNKNOWN；`lastSuccessfulCheckAt` 仅保留真实成功时间。

数据库连接沿用既有 `DATABASE_MODE` 配置。

### 4.3 服务请求安全

- 两个监控 POST 不做应用层身份校验，**部署方必须将 `/api/insight/v1/internal/monitoring/*` 限制为仅受信上报端可达**；对外开放部署前必须重新评审这一边界。保留请求大小、JSON、字段、引擎枚举和幂等校验。
- 网关登录 HTML 不得被发送端当作成功 ACK。
- GET 沿用 Host 登录态与模块自身的访问控制；进入页面的用户共享已上报 Bot 结果。本原则只适用于监控路径，不得删除治理或修复接口既有的权限校验。
- 在 JSON parser 层实施 128 KiB 限额；若父级 parser 已先执行，须在装配点协调限额与错误适配。

## 5. 数据模型

### 5.1 diagnosis 表

表名 `insight_monitoring_diagnoses`，一条已固化诊断一行；不自动删除历史记录。

| 列 | 逻辑类型 / 约束 | 用途 |
|---|---|---|
| id | 各方言自增主键惯例 | 内部行标识，不暴露页面 |
| event_id | VARCHAR(128)，NOT NULL，唯一 | 公共 eventId；diagnosisId 等于该值 |
| schema_version | VARCHAR(64)，NOT NULL | 上报格式版本 |
| bot_id | VARCHAR(128)，NOT NULL | 上报的业务 Bot |
| engine | VARCHAR(2)，NOT NULL | OC / TE |
| session_key | VARCHAR(1024)，可空 | 上游键 |
| session_id / trace_id | VARCHAR(255)，可空 | 真实定位 |
| occurred_at_ms | BIGINT，可空 | UTC Unix 毫秒，会话时间 |
| diagnosed_at_ms | BIGINT，NOT NULL | 最终诊断完成时间 |
| decision | VARCHAR(16)，NOT NULL | ALERT / PASS / UNRESOLVED |
| tc_fault_label | VARCHAR(128)，可空 | TC 标签 |
| confidence_json | VARCHAR(32)，可空 | 有限 number 的规范 JSON 十进制表示，读取转 number |
| business_problem_category / business_problem_subtype | VARCHAR(128)，可空 | 业务问题分类 |
| system_diagnosis / business_diagnosis | TEXT，可空 | 已脱敏文本，入口限制各 8000 码点 |
| handler_name | VARCHAR(128)，可空 | 可选归属信息 |
| human_intervention | 0/1 整数，NOT NULL | API 映射为 boolean |
| received_at_ms | BIGINT，NOT NULL | 服务端首次接收时间，不参与幂等比较 |
| gmt_create / gmt_modified | 遵循现有方言惯例 | 数据库审计时间，不替代业务时间 |

不保存原始请求报文、会话原文或通知状态。confidence 使用规范数字文本：既不使用 FLOAT/DOUBLE，也不让固定 DECIMAL 精度舍入导致重试冲突；API 严格是 number/null。

索引至少包含：`UNIQUE(event_id)`（大小写敏感）、`(bot_id, occurred_at_ms, event_id)`（分页）、`(bot_id, decision, occurred_at_ms, event_id)`（结果过滤）。

bot_id / event_id 的比较与排序采用 ASCII 二进制语义（SQLite BINARY；MySQL 列使用 `CHARACTER SET latin1 COLLATE latin1_bin`，入口仍严格限制 `[A-Za-z0-9_.:-]+`）。关键词搜索使用参数化字面子串查询，不为长文本建立前缀索引冒充全文搜索。

### 5.2 最新 Bot 检查表

表名 `insight_monitoring_bot_checks`：id 自增主键、bot_id 唯一 VARCHAR(128)、engine VARCHAR(2)、checked_at_ms BIGINT、last_successful_check_at_ms 可空 BIGINT、status VARCHAR(16)、received_at_ms BIGINT，以及 gmt_create / gmt_modified。

只存每 Bot 最新一行，不建心跳历史表，不把诊断总数回写到此表。PAUSED 来自最新有效检查上报。

### 5.3 Schema 归属与发布

- 两张表与既有 ClawInsight 表共用同一个 `IDatabase`。
- 模块专属 DDL 归属 `modules/clawinsight/server/services/monitoring/schema.ts`；`public/shared` 只保留通用 `IDatabase`、方言和 migration 执行能力，禁止 Shared 反向依赖 ClawInsight。
- 受管库以部署方的变更发布流程为唯一建表入口；模块 SQLite 初始化仅供本地 Host 和测试显式调用，并拒绝生产方言。应用运行时不执行建表或迁移，只做结构就绪校验和业务读写。
- 审批稿与模块 DDL 必须做结构一致性测试；表名 / 列 / 索引变化须同步 DDL 与本文。
- MySQL 方言的列 COMMENT、gmt 字段、内联索引等既有规范必须通过；不允许绕过入口校验的业务写入。
- SQLite boolean 为 0/1；MySQL 驱动数值可能返回字符串，Repository 必须显式转换并检查安全范围。
- 业务时间统一 UTC 毫秒；页面显示北京时间。
- 初始化 / 存储故障返回 503；`NoOpDatabase` 不得假落库；数据库失败不返回空数组、零计数或 accepted=true。
- 回滚应用不删除新表和记录。

## 6. 写入执行顺序

### 6.1 DiagnosisEvent

```text
限额 / JSON / 字段校验 → 归一化
 → 尝试持久插入（唯一键裁决）
     ├─ 新插入并提交：201
     ├─ 唯一冲突：读取已存字段，归一化比较
     │     ├─ 完全相同：200 duplicate=true
     │     └─ 不同：409，不覆盖
     └─ 其他数据库异常：503/500，不 ACK
```

按契约所有事件字段比较，不只比较摘要；服务器接收时间不参与；省略可空值与 null 等价；等价时区转同一毫秒。并发安全依赖数据库唯一约束，而不是进程内"先查再写"；事务提交完成之后才能返回 ACK。

### 6.2 BotCheck

以唯一 bot_id 和 checked_at_ms 做原子更新：

- 新时间更新，200 applied=true；旧时间忽略，200 applied=false。
- 同时间同内容 200 applied=false；同时间不同内容 409。
- checkedAt 超前服务时钟 5 分钟、或成功时间晚于检查时间，400。

并发乱序不能覆盖新状态；接收时间不是检查时间。

## 7. 查询与页面

### 7.1 后端语义

- bots 从检查和诊断表读取去重并集，按 botId 排序。
- status 的 diagnosisCount 为该 Bot 全部已存诊断数，不受页面日期影响；检查过期默认 UNKNOWN。
- 日期为北京时间自然日，结束日转换为下一天零点的半开区间；任一日期过滤排除 occurredAt=null，全部时间 null 排最后。
- page / pageSize / keyword / decision / counts / total 严格遵守公共契约；数据库分页。
- 同时间按 event_id ASCII 倒序；显式 CASE / null 排序保证各方言一致。
- keyword 对 `%`、`_`、反斜杠按字面匹配，参数化条件加方言测试过的转义；ASCII 英文字母大小写不敏感，与 ID 精确匹配规则分开。
- counts 忽略 decision 但应用 Bot / 日期 / 关键词；items 和 total 应用全部条件。同次响应的 counts、total、items 通过同一 SQL 快照获取；不承诺跨页 / 跨刷新快照。
- GET 设置 no-store；存储故障清晰报错，不伪装"暂无诊断"。一页响应已含详情，不再发详情请求。

### 7.2 页面布局

保留 Host 顶部全局导航和效果中心外壳。左侧两个模块入口：Agent 治理（原有行为不变，含管理员管理页）与 Agent 监控自愈。右侧三块主内容：

- **筛选组件**：Bot ID 切换、时间范围、刷新同一组件；默认第一个 Bot、北京时间当天、每页 20 条。无 Bot 名称、手动添加、任务来源标签。
- **状态区**：HEALTHY「监控正常」、ERROR「检查异常」、UNKNOWN「状态未知」、PAUSED「已暂停」；最后成功检查时间、累计诊断数。状态描述采集检查，不暗示任务全部成功。
- **诊断记录**：结果筛选、问题类型筛选、关键词搜索、可展开条目和分页。

折叠条目展示诊断名称、Session Key、TC 故障标签和会话时间；展开呈现诊断编号、Session ID / Trace ID（按有值字段）、置信度、系统 / 业务诊断、处理人、是否人工干预（是 / 否）。空值显示"—"，null 时间显示"时间未知"。不出现通知状态徽标、认领、修复、处理按钮。

### 7.3 交互与更新

- 模块以 `module=monitoring` 表示；缺省沿用治理，保留旧链接；切换不覆盖治理 tab 状态。
- 页大小 10 / 20 / 50；越界页提示返回首页，后端不悄悄调整页码。
- Bot、时间、decision、keyword、pageSize 改变回到第 1 页并清理展开状态；手动刷新保留筛选和当前页；切换 Bot 立即清除旧 Bot 数据。
- 搜索去抖 300ms；每次查询绑定完整筛选 key 和请求序号 / AbortController，旧响应不可覆盖新筛选结果。
- 页面可见且停留监控模块时每 30 秒轮询 Bot 清单、status 与当前诊断列表；隐藏 / 切到治理时暂停，返回时立即刷新。
- 同筛选刷新保留已有列表并显示轻量刷新态；失败保留旧数据并显示错误，不伪装最新结果。请求失败时标注状态未更新，不自行推断 PAUSED。
- 治理模块的初始数据请求收敛到治理模块生命周期，监控页面挂载不隐式触发。

## 8. 问题类型筛选

复用 `GET /api/insight/v1/monitoring/bots/{botId}/diagnoses`，新增可选查询参数：

| 参数 | 含义 | 约束 |
| --- | --- | --- |
| `businessProblemCategory` | 业务问题类型 | 去除两端空白后最多 128 个字符；空值或缺省不筛选 |
| `businessProblemSubtype` | 业务问题子类型 | 同上；非空时必须同时传入类型 |

数据库使用绑定参数的 `=` 条件匹配，不用关键词模糊搜索代替分类筛选。与 Bot、日期、关键词和诊断结果组合，先筛选再分页。`counts` 应用类型 / 子类型、关键词、日期条件，但不应用当前 `decision`，保留结果页签的同口径计数；`total` 对应选中的结果页签。

响应新增 `problemTypes: Array<{ category: string; subtypes: string[] }>`。选项在数据库中按当前 Bot 和日期范围去重，不受当前页、关键词、结果页签及已选类型影响；不从当前页记录生成选项。空类型不生成选项；类型非空、子类型为空时仍返回该类型。日期筛选排除会话时间为空的记录。

选项查询与列表 / 计数是两次读取：持续上报期间选项可能短暂领先或落后于列表，下一次轮询更新；列表和计数本身在同一 SQL 快照内。该响应增量兼容忽略额外字段的旧客户端；新前端需与新后端一起部署。不修改上报协议，不新增数据表或索引；生产性能需在真实数据量下通过执行计划验证。

交互：诊断工具栏使用漏斗图标和固定文案"筛选问题类型"，生效时高亮并以 1/2 标记条件数量。非模态浮层中类型与子类型联动，切换类型清空草稿子类型。点"应用筛选"才提交并回到第一页；关闭 / 点击外部 / Escape 放弃草稿；"重置"仅清除两个类型条件。已选值不在新选项列表中时仍保留并允许清除，不静默扩大查询范围。

## 9. 拟新增 / 修改文件

以下路径均在 `src/evolverun/clawweb/public/` 下。

| 路径 | 责任 |
|---|---|
| `modules/clawinsight/server/services/monitoring/contracts.ts` | 领域 DTO 与枚举，不依赖 Express |
| `modules/clawinsight/server/services/monitoring/monitoring-service.ts` | 验证后归一化、Bot 发现、幂等、状态过期 |
| `modules/clawinsight/server/services/monitoring/monitoring-runtime.ts` | 依赖装配、read/write readiness |
| `modules/clawinsight/server/repositories/monitoring-repository.ts` | IDatabase、方言查询、唯一键处理、状态原子更新 |
| `modules/clawinsight/server/routes/monitoring.ts` | POST/GET 适配、校验、错误响应 |
| `modules/clawinsight/server/services/monitoring/schema.ts` | 监控 DDL、局部方言适配、仅本地 SQLite 初始化 |
| `modules/clawinsight/web/api/monitoring.ts` | 同源 GET、类型、错误映射 |
| `modules/clawinsight/web/pages/InsightCenter/monitoring/` | 筛选、状态、条目、分页、样式与页面测试 |
| `server/fixtures/monitoring/` | 公共样例 |

不新增顶级微服务，遵循仓库架构边界、环境读取位置和契约测试要求。公共顶部导航、全局样式、Shared 和 `scripts/ci` 保持不变。

## 10. 本地验证闭环

以公共契约样例为 fixtures；本地 Host 复用生产 monitoring Router、真实 SQLite Repository 和模块初始化，监听 loopback，注入仅测试凭据。

1. 准备独立 SQLite 文件；发送 mock 数据后验证 Bot 自动发现。
2. POST 三类样例（告警 / 通过 / 无法确定）；写当前时间的 BotCheck。
3. GET 验证分页、筛选、展开；重复 POST、冲突 POST、数据库失败分别验证。
4. 重启本地 Host，记录保持。
5. 至少 25 条合法唯一 ID fixtures 验证多页、counts 与人工干预展示。

## 11. 验收标准

| 范围 | 验收 |
|---|---|
| 挂载 | 原有工厂调用能提供新接口；治理 service 不可用不误伤监控；旧治理路由不回归 |
| 接入 | 无 Authorization 时两个 POST 在受信网络内成功；非法字段 / 非法引擎 / 存储失败仍拒绝；合法新 Bot 自动接入 |
| 入库 | 首次 201、重复 200、冲突 409；20 个并发相同 POST 只落一行 |
| 故障 | noop / DB 断开 / 缺表或结构不符无假 ACK；ACK 丢失重复不新增 |
| 字段 | humanIntervention true/false 保存一致；无通知状态；置信度 number/null |
| 状态 | 无新会话可 HEALTHY；超时 UNKNOWN；乱序旧检查不覆盖；不同 Bot 不串 |
| 查询 | 北京时间边界、单边日期、null 时间、大小写、通配符字面搜索、越界页、counts/total 一致 |
| UI | 顶部导航保留；两个左侧模块；分页 / 展开 / 筛选 / 刷新；无名称 / 来源 / 通知 / 操作按钮 |
| 前端竞态 | 慢请求不覆盖新筛选；切模块 / 卸载停止轮询；错误不展示假空数据 |
| 持久性 | 重启记录保持；数据库直查与页面展示一致 |
