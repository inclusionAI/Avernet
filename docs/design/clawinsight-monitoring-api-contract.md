# 监控自愈：接口与字段约定

> 更新：2026-09-09；状态：待实现的接口设计。
>
> 本文定义三段契约：AIStudio 上报事件、ClawWeb 写入 ODC、ClawWeb 为页面提供查询。它不是现有 API 使用手册。
>
> 页面基准：[实时监控 Demo](clawinsight-realtime-monitoring-demo.html)。页面不展示完整对话原文。

## 1. 调用关系

```text
claw-validation / AIStudio
    POST 事件
        ↓
ClawWeb ingest route
    校验 + 幂等 + ODC 事务写入
        ↓
ODC
        ↑
ClawWeb read route
    GET Bot / status / diagnoses
        ↑
浏览器监控自愈页面
```

ClawWeb 不查询 claw-validation 的业务数据库。AIStudio 的本地 SQLite 只承担运行状态、去重和待上报 outbox，不是页面历史主库。

## 2. 公共规则

- API 版本：`/api/insight/v1`；文档版本不等于 HTTP 版本。
- 时间：事件字段统一使用带时区的 ISO 8601，推荐 UTC `Z`；页面按北京时间显示和筛选。
- `botId`：固定监控配置中的业务 ID，不能使用展示名或下拉序号。
- `eventId`：事件的稳定唯一键；同一诊断重试必须复用同一个值。
- `diagnosisId`：诊断结果稳定 ID。首版建议与 `eventId` 相同，或由事件明确携带并建立唯一约束；实现时必须二选一并固定。
- 空值使用 `null`，不使用字符串 `"null"`、`"未知"` 代替。
- 上报只传展示所需字段白名单，不传完整 payload、用户原文、工具调用、Span、Token 或内部路径。
- TE：一条 AI Vision Trace 是一个诊断单位；不传 `userTurn`，不在 ClawWeb 重新拆分 Trace。

## 3. 字段含义与本地 Mock

### 3.1 字段含义速查

| 字段 | 直观含义 | 生成方 | 页面用途 |
|---|---|---|---|
| `schemaVersion` | 当前事件遵循哪一版格式 | claw-validation | 不直接展示，用于兼容校验 |
| `eventId` | 这次上报事件的唯一编号 | claw-validation | 幂等和排查 |
| `botId` | 固定被监控 Bot 的 ID | claw-validation 配置 | Bot 切换和筛选 |
| `engine` | 诊断来源引擎，`OC` 或 `TE` | claw-validation | 记录来源，页面首版不单独筛选 |
| `occurredAt` | 被监控会话/Trace 实际发生时间 | 上游 Trace 或 Session | 记录排序、日期筛选 |
| `diagnosedAt` | 诊断模型生成结论的时间 | claw-validation | 详情和排查 |
| `decision` | `ALERT`、`PASS` 或 `UNRESOLVED` | claw-validation | 记录状态标签和分类筛选 |
| `confidence` | 诊断置信度，范围 0～1 | claw-validation | 展示百分比 |
| `sessionKey` / `sessionId` / `traceId` | 定位原始会话或 Trace 的标识 | 上游 Trace | 详情和搜索 |
| `tcFaultLabel` | TC 故障标签 | claw-validation | 展示故障类别 |
| `businessProblemCategory` / `businessProblemSubtype` | 业务问题的大类和子类 | claw-validation | 展示业务诊断分类 |
| `systemDiagnosis` / `businessDiagnosis` | 系统层和业务层诊断说明 | claw-validation | 展开详情 |
| `handlerName` | 可靠来源提供的处理人信息 | 上游/通知配置（可选） | 展示；没有则为 `null` |
| `humanIntervention` | 是否判断当前记录存在人工介入迹象 | claw-validation | 展示“是/否”，不代表已处理 |

### 3.2 本地 Mock 方式

由于本地 AIStudio 与 ClawWeb 可能因环境隔离无法直连，联调前不需要启动真实 AIStudio。可在本地用固定 JSON Mock 代替 claw-validation 的输出，并直接请求 ClawWeb 上报接口：

```text
mock diagnosis JSON
  → POST /api/insight/v1/internal/monitoring/diagnosis-events
  → Avernet 本地 ClawWeb
  → 本地数据库适配器（SQLite/mock）
  → 页面 GET 查询接口
```

Mock 至少覆盖 `ALERT`、`PASS`、`UNRESOLVED`、空定位字段和 `humanIntervention=true/false`。这样可以分别验证字段契约、ODC 落表和页面展示，而不依赖预发环境。

## 4. AIStudio → ClawWeb 上报接口

### 4.1 请求

```http
POST /api/insight/v1/internal/monitoring/diagnosis-events
Content-Type: application/json
Authorization: Bearer <service-token>
Idempotency-Key: <eventId>
```

首版采用单条事件，不预先设计批量协议。这里的“单条”是一次 HTTP 请求携带一条诊断结果；多个诊断结果可以连续发送多个请求。

```json
{
  "schemaVersion": "claw-monitoring/diagnosis-event/v1",
  "eventId": "diag_20260907_461514_b0dad5f7",
  "diagnosisId": "diag_20260907_461514_b0dad5f7",
  "botId": "20260810_4vqhaw2p",
  "engine": "TE",
  "sessionKey": "agent:main:bcs-cli:20260514_6ln39h2j:461514:b0dad5f7",
  "sessionId": "session_example",
  "traceId": "trace_example",
  "occurredAt": "2026-09-07T09:46:17Z",
  "diagnosedAt": "2026-09-07T09:46:35Z",
  "decision": "ALERT",
  "tcFaultLabel": "TC.MCP.DATA",
  "confidence": 0.8,
  "businessProblemCategory": "外部服务异常",
  "businessProblemSubtype": "数据获取失败",
  "systemDiagnosis": "MCP 服务返回空响应，JSON 解析失败，活动数据未能获取。",
  "businessDiagnosis": "评审任务未能获得活动数据，任务最终以关单结束。",
  "handlerName": "仲彦",
  "humanIntervention": false
}
```

### 4.2 字段定义

| 字段 | 类型 | 必填 | 规则 |
|---|---|---:|---|
| `schemaVersion` | string | 是 | 当前为 `claw-monitoring/diagnosis-event/v1` |
| `eventId` | string | 是 | 稳定唯一键，最长 128 字符 |
| `diagnosisId` | string | 是 | 页面记录唯一键；建议与 `eventId` 相同 |
| `botId` | string | 是 | 必须属于 ClawWeb 固定 Bot 白名单 |
| `engine` | `OC`/`TE` | 是 | 诊断来源引擎 |
| `sessionKey` | string/null | 否 | 有真实值才传 |
| `sessionId` | string/null | 否 | 有真实值才传 |
| `traceId` | string/null | 否 | TE 优先传真实 Trace ID |
| `occurredAt` | ISO/null | 否 | 会话轮次或 Trace 发生/结束时间 |
| `diagnosedAt` | ISO | 是 | 诊断生成时间 |
| `decision` | enum | 是 | `ALERT`、`PASS`、`UNRESOLVED` |
| `tcFaultLabel` | string/null | 否 | 如 `TC.MCP.DATA` |
| `confidence` | number/null | 否 | 0～1；告警使用既有故障置信度 |
| `businessProblemCategory` | string/null | 否 | 业务问题类型 |
| `businessProblemSubtype` | string/null | 否 | 业务问题子类型 |
| `systemDiagnosis` | string/null | 否 | 系统诊断文本，限制长度 |
| `businessDiagnosis` | string/null | 否 | 业务诊断文本，限制长度 |
| `handlerName` | string/null | 否 | 可靠来源存在时传 |
| `humanIntervention` | boolean | 是（新事件） | 诊断是否判断存在人工介入迹象；只能为 `true`/`false`，不代表已有人工处理流程 |

`PASS` 和 `UNRESOLVED` 首版可以返回 `confidence: null`，不能把故障置信度误当成成功置信度。

最新 claw-validation 对新诊断始终输出布尔值。为兼容历史记录，ClawWeb 读取旧结果时若字段缺失按 `false` 处理；新上报事件不接受缺失、`null`、字符串或数字。

### 4.3 上报响应

首次写入：

```http
HTTP/1.1 201 Created
```

```json
{
  "accepted": true,
  "eventId": "diag_20260907_461514_b0dad5f7",
  "stored": true,
  "duplicate": false
}
```

重复上报：

```http
HTTP/1.1 200 OK
```

```json
{
  "accepted": true,
  "eventId": "diag_20260907_461514_b0dad5f7",
  "stored": true,
  "duplicate": true
}
```

重复请求表示“已经接收”，不能让 claw-validation 因重复而无限重试。若同一 `eventId` 的核心字段发生冲突，返回 `409`，不得静默覆盖。

### 4.4 上报错误

| 状态码 | 含义 | claw-validation 行为 |
|---:|---|---|
| 400 | JSON、字段、枚举或时间非法 | 修正代码/数据后再发送，不盲重试 |
| 401/403 | 服务身份失败或 Bot 不在白名单 | 告警并暂停该类请求，等待配置修复 |
| 409 | 同一 ID 内容冲突 | 保留冲突事件，人工排查，不覆盖原记录 |
| 413 | 文本超长 | 截断或修正上报，不盲重试 |
| 429 | 接收方限流 | 按退避策略重试 |
| 500/502/503/504 | ClawWeb 或 ODC 暂时不可用 | 写入 outbox，指数退避重试 |

服务端错误响应统一为：

```json
{
  "error": {
    "code": "MONITORING_INVALID_EVENT",
    "message": "上报数据不符合监控事件格式。",
    "requestId": "req_example"
  }
}
```

错误消息不能返回 Token、数据库连接、上游响应正文或用户原文。

## 5. ClawWeb 写入 ODC 的规则

### 5.1 主表建议

表名可按 ODC 命名规范调整，逻辑上是一张诊断事件主表，例如 `claw_monitoring_diagnosis`：

| 列 | 说明 |
|---|---|
| `diagnosis_id` | 唯一键 |
| `event_id` | 唯一键，幂等依据 |
| `bot_id` | 固定 Bot ID |
| `engine` | OC / TE |
| `session_key` | 可空 |
| `session_id` | 可空 |
| `trace_id` | 可空 |
| `occurred_at` | 业务发生时间，可空 |
| `diagnosed_at` | 诊断时间 |
| `decision` | ALERT / PASS / UNRESOLVED |
| `tc_fault_label` | 可空 |
| `confidence` | 0～1，可空 |
| `business_problem_category` | 可空 |
| `business_problem_subtype` | 可空 |
| `system_diagnosis` | 诊断文本 |
| `business_diagnosis` | 诊断文本 |
| `handler_name` | 可空 |
| `human_intervention` | 是否人工干预；新事件为布尔值 |
| `schema_version` | 上报版本 |
| `received_at` | ClawWeb 接收时间 |
| `gmt_create` / `gmt_modified` | 数据库审计时间 |

不建议在主表放完整 `payload_json`。如果为了排查需要保留原始事件，应单独评审脱敏、容量和访问权限，不能作为页面依赖。

### 5.2 索引和事务

至少需要：

- `UNIQUE(event_id)`；
- `UNIQUE(diagnosis_id)`；
- `(bot_id, occurred_at, diagnosis_id)`；
- `(bot_id, decision, occurred_at, diagnosis_id)`；
- 关键词搜索涉及的 ID / Session Key / Trace ID 索引，按 ODC 能力选择。

写入必须使用 ClawWeb 现有 `IDatabase.transaction` 和 migration 机制。Avernet 已支持 SQLite、本地 MySQL 和 ZDAS 方言；ODC 的具体生产类型需要部署确认，不能在没有依据时写死。

接收处理顺序：鉴权 → JSON/schema 校验 → 固定 Bot 校验 → 幂等检查 → 事务写入 → ACK。

## 6. ClawWeb → 页面查询接口

### 6.1 Bot 清单

```http
GET /api/insight/v1/monitoring/bots
```

```json
{
  "items": [
    {"botId": "20260810_4vqhaw2p"},
    {"botId": "20260731_0gu2ft1f"}
  ]
}
```

清单来自固定监控配置，不提供新增 Bot 接口；页面只展示 Bot ID。

### 6.2 Bot 状态

```http
GET /api/insight/v1/monitoring/bots/{botId}/status
```

```json
{
  "botId": "20260810_4vqhaw2p",
  "status": "HEALTHY",
  "lastSuccessfulCheckAt": "2026-09-09T02:24:30Z",
  "diagnosisCount": 128
}
```

状态枚举：

- `HEALTHY`：最近一次该 Bot 检查成功且未过期；
- `ERROR`：存在明确检查失败或阻断证据；
- `PAUSED`：有明确的停止监控配置；
- `UNKNOWN`：刚启动、无足够状态信息或状态数据不可用。

不能用“最近没有诊断”直接推断暂停或异常，也不能把整个进程状态复制为所有 Bot 的绿色状态。

### 6.3 诊断记录

```http
GET /api/insight/v1/monitoring/bots/{botId}/diagnoses
  ?startDate=2026-09-01
  &endDate=2026-09-09
  &decision=ALERT
  &keyword=TC.MCP.DATA
  &page=1
  &pageSize=10
```

参数：

| 参数 | 规则 |
|---|---|
| `startDate` | 北京时间开始日期，`YYYY-MM-DD`，可省略 |
| `endDate` | 北京时间结束日期，包含当天，可省略 |
| `decision` | `ALL`、`ALERT`、`PASS`、`UNRESOLVED`，默认 `ALL` |
| `keyword` | 对 ID、Session Key、Trace ID、分类和 TC 标签做字面查询，最多 200 字符 |
| `page` | 正整数，默认 1 |
| `pageSize` | 10、20 或 50，默认 10 |

日期转换为北京时间自然日的半开区间。例如 `endDate=2026-09-09` 的上界是 `2026-09-10T00:00:00+08:00`。日期倒置、非法日期、非法枚举返回 400。

返回：

```json
{
  "botId": "20260810_4vqhaw2p",
  "page": 1,
  "pageSize": 10,
  "total": 1,
  "totalPages": 1,
  "counts": {"all": 3, "alert": 1, "pass": 1, "unresolved": 1},
  "items": [
    {
      "diagnosisId": "diag_example_001",
      "botId": "20260810_4vqhaw2p",
      "decision": "ALERT",
      "occurredAt": "2026-09-07T09:46:17Z",
      "diagnosedAt": "2026-09-07T09:46:35Z",
      "sessionKey": "agent:main:bcs-cli:20260514_6ln39h2j:461514:b0dad5f7",
      "sessionId": "session_example",
      "traceId": "trace_example",
      "tcFaultLabel": "TC.MCP.DATA",
      "confidence": 0.8,
      "businessProblemCategory": "外部服务异常",
      "businessProblemSubtype": "数据获取失败",
      "systemDiagnosis": "MCP 服务返回空响应，JSON 解析失败，活动数据未能获取。",
      "businessDiagnosis": "评审任务未能获得活动数据，任务最终以关单结束。",
      "handlerName": "仲彦",
      "humanIntervention": false
    }
  ]
}
```

`counts` 只应用 Bot、日期和关键词，不应用 `decision`，用于页面四类筛选按钮；`total` 应用所有查询条件。后端必须使用数据库分页，禁止全量读入内存后再分页。

列表默认按 `occurredAt` 倒序；缺失时间的旧记录在启用日期筛选时排除，在“全部时间”查询中放到末尾。同时间按 `diagnosisId` 倒序。

## 7. 诊断字段语义

`humanIntervention` 只表示 claw-validation 对当前诊断是否判断存在人工介入迹象。它不是“处理状态”，也不对应认领、处理人、审批或自愈动作。本期页面在诊断详情中展示“是/否”，不提供任何人工操作按钮。

ClawWeb 页面不依赖钉钉通知结果。钉钉是否发送、发送是否成功属于 claw-validation 的独立通知支路，不进入本期页面查询接口，也不作为 ODC 诊断记录是否保存的条件。

`occurredAt` 是会话轮次或 Trace 的业务时间；`diagnosedAt` 是诊断生成时间，两者不能混用。TE 一条 Trace 对应一个诊断单位，同 Session 的不同 Trace 不合并。

## 8. 失败处理和安全边界

页面查询错误：

| HTTP | 页面处理 |
|---:|---|
| 400 | 保留输入并提示参数错误 |
| 401 | 按 ClawWeb 登录态处理 |
| 403/404 | 显示无权访问或 Bot 不存在，不泄露对象信息 |
| 503 | 显示数据服务暂不可用 |
| 504 | 显示请求超时，可重试 |

服务间鉴权首版沿用当前 ClawWeb 与 AIStudio 交互的 Token 配置模板：使用 HTTPS，并在请求中携带 `Authorization: Bearer <service-token>`。Token 由部署环境注入，ClawWeb 服务端校验；浏览器不接触 Token，公开代码不提供默认值。Token 的环境变量名称和预发配置值在联调前按现有部署配置核对。后续如平台已有更强的服务身份机制，再单独升级，不作为本期前置条件。

服务间上报的 401/403 是内部身份配置问题，不能原样展示成“请用户重新登录”。所有响应中的文本字段按普通文本渲染并做长度限制；不得向浏览器下发服务 Token。

## 9. 契约测试最小集合

- OC、TE 两种引擎；TE 同一 Session 多 Trace；
- 三种 `decision` 和空值字段；
- 重复事件返回幂等成功，冲突事件返回 409；
- 固定 Bot 白名单和未知 Bot；
- 北京时间跨日、单端日期、同日、非法日期；
- 分类计数与 `total` 的差异；
- 页码越界、不同 `pageSize`、关键词特殊字符；
- 上报服务超时后 outbox 重试；
- ODC 写入失败不返回假成功；
- ClawWeb 重启后历史仍可查询；
- 页面不请求完整对话原文，旧 Bot 请求不能覆盖新 Bot 页面状态。
