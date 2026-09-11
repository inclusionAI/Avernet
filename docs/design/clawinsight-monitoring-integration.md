# 效果中心「监控自愈」开发方案

> 更新：2026-09-09
>
> 本文是监控功能的开发方案，不是现有服务使用手册。当前阶段只实现“监控”：claw-validation 采集并诊断，ClawWeb 接收结果并展示；自愈动作、告警处理、改进项创建均不在本期实现。
>
> 页面基准：[实时监控 Demo](clawinsight-realtime-monitoring-demo.html)。页面只展示诊断结果及其必要详情，不展示完整对话原文。

## 1. 先用一句话说清楚要做什么

我们要补上的不是另一套诊断逻辑，而是诊断结果从 AIStudio 到 ClawWeb 的**正式传输和落库链路**：

```text
固定 Bot / Teamclaw
        ↓
claw-validation（AIStudio 容器）
  采集 → 诊断 → 组织标准事件 → HTTP 上报
        ↓
ClawWeb（Avernet / ClawInsight）
  接收 → 校验 → 幂等写入 ODC → 提供查询接口
        ↓
效果中心「监控自愈」页面
  Bot 切换 → 时间筛选 → 状态 → 诊断记录 → 分页
```

ODC 是页面历史记录的长期来源。页面刷新只是查询 ClawWeb，不能触发重新采集或重新诊断。

### 本期范围

| 本期必须实现 | 本期明确不做 |
|---|---|
| claw-validation 主动上报诊断事件 | ClawWeb 直接查询 claw-validation 内部数据库 |
| ClawWeb 接收、校验、幂等、落 ODC | 将 Python 监控核心迁入 Avernet |
| 固定 Bot 清单、状态和诊断记录查询 | 前端添加 Bot、全量 Bot 扫描 |
| Bot、开始日期、结束日期、刷新、筛选、搜索、分页 | 完整对话原文、原始 Trace/Span 浏览 |
| 诊断详情展开 | 自愈执行、告警认领、处理按钮、改进项操作 |
| 页面从 ODC 查询 | 另造缓存平台、消息队列或快照分页系统 |

这里的“固定 Bot”仍由 claw-validation 配置维护。用户只能在页面切换已接入的 Bot，不能通过页面修改 AIStudio 容器配置。

## 2. 三个仓库各自负责什么

### 2.1 claw-validation：生产诊断事件

保留现有核心能力：

1. 从 OC / TE / AI Vision 获取监控数据；
2. 按现有诊断流程生成 `ALERT`、`PASS` 或 `UNRESOLVED`；
3. 将诊断结果转换为版本化的上报 DTO；
4. 通过 HTTP POST 发送给 ClawWeb；
5. 对网络失败进行有限重试，并保留待上报记录，避免服务短暂不可用导致结果丢失。

调整职责：

- 本地 SQLite 可以保留为运行状态、诊断去重和待上报 outbox；它服务 claw-validation 自身的监控与钉钉支路；
- 不再把本地 SQLite 作为 ClawWeb 页面长期查询的主库；
- 不再为 ClawWeb 提供“查询历史诊断”的主接口；
- 钉钉通知可以继续保留，但它与 ClawWeb 上报是两条独立消费支路，不能以钉钉发送成功作为网页落库成功的依据；后续下线钉钉时只移除通知支路，不改变 ClawWeb 上报和 ODC 主链路。

### 2.2 Avernet / ClawInsight：接收、落库、查询和页面

Avernet 是公开业务代码的 Owner，新增能力包括：

- ClawWeb 服务端接收 AIStudio 上报事件的内部 HTTP 路由；
- 上报 DTO 校验、Bot 白名单校验、幂等处理和 ODC Repository；
- 监控 Bot、状态、诊断列表的读取接口；
- 按 Demo 实现监控自愈页面；
- 将 ODC 数据库迁移纳入 Avernet 现有 migration 机制。

监控业务数据链路不应复用治理中心的统计表和“效果概览 / 问题证据 / 我的待办”状态机。两者共用 ClawInsight 页面入口和数据库抽象，但业务数据模型不同。

### 2.3 OCB：默认不改业务代码

本期默认判断是：**监控业务代码不进入 OCB，OCB 不需要为了监控新增业务逻辑。**

但要区分两件事：

- OCB 不负责诊断、上报 DTO、页面或 ODC Repository；
- ClawWeb 继续沿用现有 ClawInsight 的 `/api/insight/v1` 路由挂载方式；本期不新增 OCB 业务路由，也不把监控逻辑复制到 OCB。

OCB 仍然只是组合宿主和部署配置承载方。只要现有 Host 已统一挂载 `createInsightRouter`，新增监控接口由 Avernet 的 ClawInsight Router 导出即可；开发时只需按部署流程验证组合构建，不把“是否新增 OCB 路由”作为本期设计问题。

固定 Bot 对所有页面用户开放，不代表写接口匿名。AIStudio → ClawWeb 使用当前 AIStudio 交互所采用的服务端 Token 配置方式：请求携带 `Authorization: Bearer <token>`，Token 由部署环境注入并由 ClawWeb 服务端校验，浏览器不可见，也不写入公开代码。

## 3. 为什么要取消“ClawWeb 查询 AIStudio 数据库”

旧方案让 ClawWeb 查询 claw-validation 的 SQLite，存在三个根本问题：

1. **存储归属不清楚**：页面历史依赖 AIStudio 容器内部文件，容器重启、迁移或备份失败都会影响页面；
2. **服务耦合**：ClawWeb 必须理解 claw-validation 的数据库表结构，诊断代码一改，页面接口也可能被迫修改；
3. **数据流倒置**：诊断结果产生后已经确定，应该作为事件主动交给业务服务，而不是由网页链路再去拉取诊断进程的内部状态。

新方案把边界分开：

- claw-validation 只负责“发现并判断”；
- ClawWeb 负责“接收并对外提供”；
- ODC 负责“长期保存”；
- 页面只依赖 ClawWeb 的查询接口。

这不是重复存储：AIStudio 的本地 outbox 是传输可靠性保障，ODC 才是页面历史主库。

## 4. 页面需求如何映射到后端能力

页面以 Demo 为准，主体只有三块：

1. **选择组件**：固定 Bot、开始日期、结束日期、刷新；
2. **当前 Bot 状态**：监控正常 / 异常 / 状态未知、最近检查时间、全部诊断数；
3. **诊断记录**：分类筛选、搜索、可展开详情、分页。

页面不增加任务来源、数据源、引擎选择、实时任务面板、链路健康大屏或自愈按钮。

### 页面字段对应关系

| 页面内容 | 读取字段 | 说明 |
|---|---|---|
| Bot 下拉框 | `botId` | 固定配置清单；页面只展示 Bot ID |
| 状态 | `status` | 来源于 ClawWeb 保存的 Bot 检查状态，不由“最近是否有告警”推断 |
| 最近检查 | `lastSuccessfulCheckAt` | 最近一次成功完成检查的时间 |
| 全部诊断 | `diagnosisCount` | 当前 Bot 全部可查记录数，不随日期和分类筛选变化 |
| 诊断分类 | `decision` | `ALERT / PASS / UNRESOLVED` |
| 会话时间 | `occurredAt` | OC 轮次或 TE Trace 的业务发生时间 |
| 诊断时间 | `diagnosedAt` | 诊断结果生成时间，详情中可使用 |
| 会话定位 | `sessionKey`, `sessionId`, `traceId` | 有真实值才返回，不伪造 |
| 诊断内容 | `tcFaultLabel`, `confidence`, `systemDiagnosis`, `businessDiagnosis` | 只返回展示所需白名单字段 |
| 人工干预 | `humanIntervention` | 诊断结果字段，详情中显示“是/否”，不提供人工处理操作 |

TE 的口径已经对齐：一条 AI Vision Trace 对应一个诊断单位，`user_turn=1` 不代表轮次错误。页面不展示 `user_turn`，也不重新计算 Session 内轮次；同一个 Session 的不同 Trace 保持为不同记录。

## 5. 数据生命周期和长期留存

### 5.1 ODC 是唯一页面历史主库

ClawWeb 接收事件后，在同一事务中完成：

1. 校验请求；
2. 检查 `eventId` / `diagnosisId` 是否已存在；
3. 首次事件插入诊断表；
4. 重复事件返回幂等成功，不重复插入；
5. 必要时更新允许变化的非核心字段。

诊断记录包含通过、告警和无法判断，不以是否发送钉钉作为是否保存的条件。不按用户选择的时间范围删除数据。本期不设计自动按天清理策略；容量治理另行评审。

### 5.2 AIStudio 只保留传输所需临时状态

claw-validation 可以保留本地数据库，但用途应收敛为：

- 采集水位和运行状态；
- 诊断去重；
- 未成功上报事件的 outbox；
- 上报重试和恢复。

它不再承担页面历史长期查询。若暂时仍使用 `/tmp + checkpoint`，只能作为过渡，不能宣称等价于 ODC 的可靠历史存储。备份失败不能静默删除旧数据并用空库继续运行。

### 5.3 通过运行模式兼容历史任务

为了兼容现有 claw-validation 能力，不建议立即删除本地数据库、钉钉通知和历史回溯代码；建议增加一个**部署级运行模式开关**，而不是让同一条诊断在两套主库之间自由切换。这个开关由启动配置决定，不是 ClawWeb 页面上的用户操作开关。

```yaml
runtime:
  mode: "local" # local | clawweb
```

两种模式的职责如下：

| 模式 | 本地 SQLite | 钉钉推送 | ClawWeb 上报 | 页面历史主库 |
|---|---|---|---|---|
| `local` | 使用 | 可用 | 关闭 | 本地诊断/钉钉链路 |
| `clawweb` | 仅保留 watermark、运行状态、去重和 outbox | 默认关闭 | 开启 | ODC |

`local` 用于当前历史回溯和独立实时监控的兼容运行；`clawweb` 用于上线 ClawWeb 的任务。模式应在进程启动时确定，不建议运行中热切换，避免同一批事件部分写本地、部分上报 ClawWeb。

在 `clawweb` 模式下，本地 SQLite 仍不能立即删除：它需要保存采集水位、去重信息和 ClawWeb 暂不可用时的待上报事件。只有确认 ClawWeb 上报稳定、无需本地恢复后，才可以另行评估进一步缩减本地存储。

模式切换不是数据迁移：切换到 `clawweb` 后，历史本地数据不会自动回灌 ODC。若需要展示旧历史，应另做一次离线迁移工具，不放入实时监控主流程。

### 5.3 不保存完整对话原文

ODC 保存页面诊断详情需要的字段即可，不保存用户原文、完整工具调用、完整 Span、Token 或内部路径。页面展开记录时直接读取 ODC 中已经固化的诊断文本，不再回查 AI Vision。

## 6. Avernet 中应该怎样接入

Avernet 已有数据库抽象 `IDatabase`，支持 SQLite、本地 MySQL 和 ZDAS 适配；生产环境究竟将 ODC 映射为 `zdas` 还是其他连接方式，需要部署配置最终确认，方案不提前写死。

Avernet 也已有统一 migration 机制和 ClawInsight 的服务 / Repository / route 分层。建议新增以下边界，名称可按代码风格调整：

```text
src/evolverun/clawweb/public/modules/clawinsight/
├── server/routes/monitoring.ts
├── server/services/monitoring/
│   ├── contracts.ts
│   ├── monitoring-ingest-service.ts
│   ├── monitoring-read-service.ts
│   └── monitoring-runtime.ts
├── server/repositories/monitoring-diagnosis-repository.ts
├── web/api/monitoring.ts
└── web/pages/InsightCenter/MonitoringPage.tsx
```

职责分层：

- `monitoring.ts`：HTTP 参数、身份和错误码适配；
- `ingest-service`：校验、白名单、幂等和落库编排；
- `read-service`：Bot、状态、分页查询；
- `repository`：只负责 ODC SQL 和事务；
- `monitoring-runtime`：生产客户端、数据库和本地 mock 的装配；
- `MonitoringPage`：只处理页面状态和展示，不拼服务 Token，不直接访问 ODC。

治理中心现有的 `index.tsx`、`web/api/insight.ts`、`insight-runtime.ts` 可以作为页面入口、请求封装和 runtime 装配的参考，但不能把治理统计模型直接当成监控模型。

## 7. API 上报和查询的最小集合

### 7.1 AIStudio → ClawWeb

首版使用单条事件上报：

“单条”指一次 HTTP 请求只携带一条诊断结果。例如一个 Trace 诊断完成，就发送一次 POST。它不是说系统只能处理一条记录，也不是说每次只监控一个 Bot；多个结果只是分别发送多个请求。这样最容易定位失败和实现幂等。后续如果确认吞吐量不足，再增加一次请求携带多条事件的批量接口。

```http
POST /api/insight/v1/internal/monitoring/diagnosis-events
```

请求体中至少包含：

- `schemaVersion`
- `eventId`
- `botId`
- `engine`
- `occurredAt`
- `diagnosedAt`
- `decision`
- `confidence`
- `sessionKey / sessionId / traceId`
- `tcFaultLabel`
- `businessProblemCategory / businessProblemSubtype`
- `systemDiagnosis / businessDiagnosis`
- `handlerName`（如确有可靠来源）
- `humanIntervention`

返回明确 ACK，并区分首次接收与重复接收。详细字段、枚举、时间边界、错误码和幂等规则见配套文档。

首版不增加批量接口、消息队列和异步任务平台。若实测单条 HTTP 上报吞吐不足，再单独设计批量协议；不要预先引入复杂基础设施。

### 7.2 ClawWeb → 页面

```text
GET /api/insight/v1/monitoring/bots
GET /api/insight/v1/monitoring/bots/{botId}/status
GET /api/insight/v1/monitoring/bots/{botId}/diagnoses
```

这三个接口都由 ClawWeb 查询 ODC，不再请求 claw-validation。页面查询支持日期、分类、关键词和普通页码分页。

## 8. 关键可靠性要求

以下是本期必须做的最小保障，不是额外架构：

1. **服务身份**：只允许已配置的 claw-validation 上报；浏览器不能调用写接口；
2. **幂等**：同一事件重试不能产生重复诊断记录；
3. **白名单**：只接收固定 Bot，未知 Bot 拒绝或隔离，不写入页面主表；
4. **字段白名单**：不把整个 AIStudio payload 原样落表；
5. **时间一致**：接口传 UTC，页面按北京时间自然日筛选；
6. **失败可恢复**：ClawWeb 暂时不可用时，claw-validation 保留待上报事件并重试；
7. **状态可信**：没有成功检查证据时不显示绿色“正常”；
8. **迁移可回滚**：监控表通过 Avernet 现有 migration 管理，先在 SQLite 和生产数据库兼容模式验证。

## 9. 开发顺序

### 阶段一：冻结契约

- 确定字段名称、枚举、时间和 ID 语义；
- 确认固定 Bot 清单；
- 确认 ODC 在 Avernet 中使用的具体数据库连接模式；
- 用样例 JSON 做双方契约测试。

### 阶段二：先打通写链路

- claw-validation 构建事件 DTO、HTTP client、重试和 outbox；
- Avernet 增加接收路由、校验服务、幂等和诊断表 migration；
- 用一条 mock 事件确认可写入 ODC。

### 阶段三：再打通读链路

- Avernet 增加 Bot / status / diagnoses 查询；
- 先用 ODC 中的固定样例数据联调页面；
- 验证日期边界、分类计数和分页。

### 阶段四：接入真实监控

- 接入 OC、TE 两类真实诊断结果；
- 验证同一 Trace 重试不重复、同一 Session 多 Trace 不合并；
- 验证 ClawWeb 暂停时恢复上报；
- 保留钉钉通知作为独立回归支路。

### 阶段五：预发验收

按 ClawWeb 新开发与预发流程：Avernet 开发和检查 → 合入目标分支 → 同步内部 Mirror → 使用 OCB 的 ClawWeb 预发流水线组合构建。是否需要 OCB 提交，以路由装配和配置验证结果为准，不做空提交。

## 10. 验收标准

- 页面能切换固定 Bot，并仅展示 Bot ID；
- 日期按北京时间筛选，结束日期包含当天；
- 新诊断上报后，刷新页面能看到记录；
- PASS、ALERT、UNRESOLVED 都能保存和查询；
- 详情只显示诊断字段，不展示完整原文；
- 重复 POST 返回幂等成功且不重复落表；
- ClawWeb 短暂不可用后，claw-validation 能恢复上报；
- 重启 ClawWeb 后 ODC 中历史仍可查询；
- 数据库查询支持分页，不把全量历史读入内存；
- 页面查询失败显示错误状态，不伪装成空列表；
- 不新增用户添加 Bot、全量扫描、自愈动作或复杂权限系统。

## 11. 开发前只需核对的实现事实

以下不是重新讨论产品方向，而是联调前核对已有环境的少数事实：

1. ODC 在当前部署中对应 Avernet 的哪一种 `IDatabase` 配置（例如 `zdas` 或 MySQL 兼容模式）；
2. 按现有部署流程完成 Avernet/OCB 组合构建后，监控路由是否能随 ClawInsight Router 正常访问；
3. AIStudio 与 ClawWeb 两侧 Token 的环境变量名称、注入位置和预发配置值。

以下决策已经确定，不再作为待确认项：固定 Bot、只展示 Bot ID、ODC 作为页面历史主库、复用 `/api/insight/v1`、Token 鉴权、首版单条 POST、不展示完整对话原文、不做自愈操作。

## 12. 与最新 claw-validation 版本的对齐

当前对齐基线为 `claw-validation` `be1d5f2`（2026-09-09）。该版本对接入方案有三点影响：

- **多 Bot 并发**：claw-validation 已按 Bot 并发轮询，每个 Bot 独立 watermark 和错误状态；ClawWeb 不需要理解轮询并发，只需接收多个 Bot 的独立诊断事件，并按 `botId` 查询。
- **GLM-5.2**：诊断默认模型已切换为 GLM-5.2，属于 claw-validation 内部实现，不进入页面主接口，也不要求 ClawWeb 增加模型筛选。
- **人工干预字段**：诊断结果新增 `human_intervention`。本期可映射为上报/详情中的 `humanIntervention`，仅表示诊断判断“是否存在人工介入迹象”，不代表页面提供认领、处理、审批或自愈操作；不新增人工干预 API。

TE 的数据口径保持不变：一条 AI Vision Trace 对应一条诊断记录，ClawWeb 不重新拆分 `user_turn`。
