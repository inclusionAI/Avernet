# Agent 监控自愈：公共接口契约 Spec

- 状态：发送端独立开发契约；Avernet 接收/查询已实现并通过真实 dev 库 HTTP 写读验证，AIStudio 上报端与实际部署联调尚未完成，不是已上线 API 手册。
- 更新日期：2026-09-11；契约修订号：`1.0.0-draft.3`。本次取消监控上报应用层 Token 校验，采用部署侧内网访问限制，不改变路径、JSON schemaVersion、字段或幂等语义。
- 适用双方：claw-validation（生产并上报）与 Avernet / ClawWeb（接收、存储、查询）。
- 同名文件在两个仓库保存，必须逐字一致：
  - Avernet：`docs/specs/2026-09-09-clawinsight-agent-monitoring/clawinsight-monitoring-api-contract.md`。
  - claw-validation：`docs/specs/clawinsight-monitoring-api-contract.md`。
  公共协议改动在同一交付中同步两份副本；实现细节只写各自 Spec。跨仓库 PR 应互相标明契约修订号。
- 当前发送端核对基线 `0c24350`；接收端为尚未提交的功能分支工作区，真实 dev 库验证 17 PASS / 0 FAIL，不代表完整宿主/生产/AIStudio 网络已验收。旧版“两个 POST 尚未实现”的进度说明已失效。
- **AIStudio 独立阅读指引**：只需本文件与同目录 `clawweb-reporting-integration-spec.md`；优先阅读 §2～5、§7 和 §8 样例。§6 是页面查询参考，发送端不实现/调用这些 GET。Avernet 路径和 HTML 只供另一团队定位，绝非开发依赖。AIStudio 不需要数据库凭据、DDL、OCB 或 Node 环境。
- 本 Spec 是本次联调的接口依据；与旧 `docs/design/` 方案有冲突时，以本 Spec 为准。已有设计 HTML 仍是页面布局基准。

## 1. 一页理解链路

```text
claw-validation（AIStudio；local/clawweb 为启动模式）
  ├─ local：沿用本地诊断与原有钉钉配置，不访问 ClawWeb
  └─ clawweb：诊断 → 可靠待发记录 → 单条 POST → ClawWeb → 业务数据库
                                                        ↑
                                            页面 → GET 查询
```

本文所说“ODC 落表”，是 ClawWeb 通过现有 `IDatabase` 写入部署配置指定的业务数据库、表可在 ODC 中查询；不是向一个名叫 ODC 的 HTTP 服务发数据。连接配置沿用 ClawInsight；Avernet 集成验收使用真实 dev 业务库，本地 SQLite 只可作为辅助测试。AIStudio 仅通过 HTTP 上报，不选择或连接业务数据库。

三个概念不能混用：

| 概念 | 用途 | 不代表 |
|---|---|---|
| 诊断结果 | 某条会话轮次 / Trace 的 ALERT、PASS、UNRESOLVED | Bot 监控进程是否在线 |
| Bot 检查状态 | 某个 Bot 最近检查是否成功、状态是否过期 | Agent 的所有任务都成功 |
| 人工干预 | 当前用户输入是否在干预前序失败 / 未完成任务 | 需要人工处理、已认领、已自愈 |

首版不做完整原文展示、Bot 名称、页面添加 Bot、全量扫描、通知状态、任务下发、认领或自愈。`clawweb` 模式也不代表把 AIStudio 的任务执行调度迁到 ClawWeb。

## 2. 接口总览与安全边界

基础路径沿用 `/api/insight/v1`：

| 方法 / 相对路径 | 调用方 → 提供方 | 作用 |
|---|---|---|
| `POST /internal/monitoring/diagnosis-events` | CV → ClawWeb | 一次写入一条诊断，持久落库后 ACK |
| `POST /internal/monitoring/bot-checks` | CV → ClawWeb | 一次上报一个 Bot 的检查状态 |
| `GET /monitoring/bots` | 页面 → ClawWeb | 固定 Bot 清单 |
| `GET /monitoring/bots/{botId}/status` | 页面 → ClawWeb | 最新检查状态和累计诊断数 |
| `GET /monitoring/bots/{botId}/diagnoses` | 页面 → ClawWeb | 筛选、搜索、分页、展开所需诊断字段 |

**为何补充 `bot-checks`：**只上报诊断无法区分“没有新会话”和“监控已经停止”。这是支撑现有状态展示的最小写接口，不新增页面监控链路面板，也不增加任务控制接口。

部署提供的 `CLAWWEB_BASE_URL` 是 origin（如测试时 `http://127.0.0.1:3101`），不带 `/api/insight/v1`、query 或 fragment；客户端追加本节完整路径。不得把开发者电脑的 localhost 当成 AIStudio 可访问地址。

**当前 HTTP 兼容边界（发送端必须遵守）**：两个 POST 都发送非压缩、非流式 UTF-8 JSON，带准确的 `Content-Length`（UTF-8 bytes 数，不是字符数）；不使用 `Transfer-Encoding: chunked`。这是为了兼容真实宿主先解析 JSON 的顺序。HTTP 库可根据预先序列化的 bytes 自动生成长度；不要手写错误长度。请求上限 128 KiB。该边界不改变业务字段；若以后需要流式/压缩，双方先协调宿主能力。禁用自动重定向，不能向跳转地址发送诊断数据或将登录页面当 ACK。

所有写请求只要求 `Content-Type: application/json`；诊断事件另需 `Idempotency-Key`。不要求、也不校验 `Authorization`，不配置 `MONITORING_REPORT_TOKEN`，不复用 AIStudio 平台 Token，不新增 MIST 秘密映射或 HMAC。旧客户端多发 Authorization 不影响本模块处理，但新版发送端应不发送该头。真实服务使用 HTTPS；仅 loopback 本地测试允许 HTTP。

**部署安全边界**：本轮经需求方与联调负责人确认，两个监控 POST 依赖部署侧内网访问限制，不执行应用层调用方身份验证。内网可达不等于身份可信：任何可到达该路径的调用方均可能提交符合校验的数据。仅用于经确认的受限内网入口；对外开放或扩大调用范围前必须重新评审认证方案。固定 Bot 清单是数据范围校验，不是身份认证。联调验收负责人需确认实际入口及访问范围，本地测试不能证明网络隔离。

监控应用不再产生 Token 相关 401 或缺 Token 的 503；模块关闭、配置非法或存储未就绪仍返回 503。写接口不得受浏览器登录重定向影响；页面 GET 沿用 ClawWeb 登录态，固定 Bot 对所有已进入页面的用户共享，不新增 owner 范围或复杂 RBAC。现有治理、其他 internal API、出站 AIStudio 凭据及权限规则不变。

固定 Bot 清单由部署配置对齐：CV 的 `bots` 是采集范围，ClawWeb 配置相同的 `botId + engine` 用于读取清单和写校验。清单不能从已收到的诊断推导，否则无记录 Bot 不可见。配置维护不是前端添加 Bot API。

## 3. 字段含义、类型和来源

### 3.1 DiagnosisEvent

下表为允许的全部字段；未知字段拒绝。新发送端将可空字段显式传 `null`；接收端允许省略这些字段，统一归一化为 `null`。不接受空字符串冒充 ID，不传字符串 `"null"`。

| 字段 | 类型 / 必填 | 含义与来源 | 限制 / 页面用途 |
|---|---|---|---|
| `schemaVersion` | string / 是 | 事件格式版本，发送器固定值 | `claw-monitoring/diagnosis-event/v1` |
| `eventId` | string / 是 | 稳定上报编号，重试不重建 | 1～128 ASCII 字符；`[A-Za-z0-9_.:-]+` |
| `diagnosisId` | string / 是 | 页面记录编号 | 首版强制等于 `eventId`，相同字符限制 |
| `botId` | string / 是 | 被监控业务 Bot 的真实 ID，来自显式配置和任务归属 | 1～128；同上字符限制；必须在固定清单 |
| `engine` | enum / 是 | 引擎类型，来自 Bot 配置 / job locator | `OC` 或 `TE`，必须与清单一致 |
| `sessionKey` | string/null | 上游会话键，不是处理人或 Bot 名称 | 最多 1024 Unicode 码点；展示、搜索 |
| `sessionId` | string/null | 上游真实 Session ID | 最多 255 码点；没有真实值则 null |
| `traceId` | string/null | AI Vision Trace ID | 最多 255 码点；TE 新事件必填非空，OC 可为 null |
| `occurredAt` | ISO string/null | 本条 OC 轮次或 TE Trace 结束时间；TE 无结束时间时取真实开始时间 | 记录排序和日期筛选；无可靠时间则 null，不用诊断时间代替 |
| `diagnosedAt` | ISO string / 是 | 诊断最终结论完成的时间 | 发送端在完成后记录；重试沿用原值 |
| `decision` | enum / 是 | `monitor_decision` | `ALERT` 故障；`PASS` 通过；`UNRESOLVED` 无法判断 |
| `tcFaultLabel` | string/null | 最主要 TC 故障标签 | 最多 128 码点；如 `TC.MCP.DATA`，PASS 为 null |
| `confidence` | number/null | ALERT 的问题 / 故障置信度 | 有限数 0～1；布尔非法；PASS/UNRESOLVED 首版为 null；页面乘 100 显示 |
| `businessProblemCategory` | string/null | 业务问题大类 | 最多 128 码点 |
| `businessProblemSubtype` | string/null | 业务问题子类 | 最多 128 码点 |
| `systemDiagnosis` | string/null | 系统技术层诊断文本 | 最多 8000 码点 |
| `businessDiagnosis` | string/null | 面向任务目标的业务诊断文本 | 最多 8000 码点 |
| `handlerName` | string/null | 有可靠归属配置时的处理人显示信息 | 最多 128 码点；无值显示“—”；不可解释为已有人处理；不依赖钉钉启用 |
| `humanIntervention` | boolean / 是 | 当前用户输入是否在针对前序失败、未完成或不符预期任务追问、重试、纠正或补充指令 | 只允许 true/false；详情显示“是否人工干预：是/否” |

补充约束：

- 时间采用带时区的 ISO 8601 RFC3339 子集，必须含秒，可有 1～3 位小数，使用 `Z` 或 `±HH:MM`；校验真实日期，归一为 UTC 毫秒精度后存取和比较。拒绝无时区时间及闰秒表示。
- 一条 AI Vision Trace = 一个诊断单位。同 Session 不同 Trace 不合并；不向页面暴露 `user_turn=1`，不因此改分轮逻辑。
- `humanIntervention=false` 包括证据不足，不能据此证明用户从未干预。模型失败沿用上游有效结果 / 默认 false，不从 ALERT 推断为 true。不能映射自 `intervention_required`（另一种后续观察字段）。
- 当前轮出现用户消息并不自动意味着 true；判断只依据当前轮可见证据。新事件不接受 null 或缺失；若未来导入旧库，缺字段按旧反序列化 false 兼容，这不是新接口放宽校验。
- `notificationStatus` 已删除，不进入上报 DTO、ODC 诊断表、查询响应或页面。截图中该字段属于旧版。
- 字段白名单不等于自动脱敏。诊断文本也须经过发送端脱敏；不嵌入完整用户原文、工具调用、Span、模型原始响应、凭据和内部文件路径。浏览器按纯文本渲染。

### 3.2 稳定 ID：不把重试变成新记录

首版 `eventId = diagnosisId`，发送端生成后持久化，接收端不重新计算业务 ID：

- TE：`diag_te_` + SHA256(UTF-8 紧凑 JSON 数组 `["TE", botId, traceId]`) 的 64 位小写十六进制摘要。当前作用域假设一个部署中 Bot ID 唯一、只有一套采集范围；未来多 Space 同 ID 必须升级身份定义。
- OC：`diag_oc_` + SHA256(UTF-8 紧凑 JSON 数组 `["OC", botId, sourceId, boundaryId]`) 的摘要。`sourceId/boundaryId` 为发送端内部真实值，不额外暴露在公共 DTO。
- Python 使用 `json.dumps(parts, ensure_ascii=False, separators=(",", ":"))`；编码示意 `["TE","mock-bot-te","mock-trace-001"]`。Mock 可用满足字符规则的固定 ID，ClawWeb 不校验摘要格式。
- 不直接假设现有 `TurnDiagnosis.diagnosis_id` 对变化中的 Trace 永远稳定。TE 在发送端以真实 Trace 定义映射并“首次最终结果固化”，避免相同 Trace 由于 boundary 变化产生重复网页记录。
- 本期无重新诊断覆盖 / 结果修订协议。已固化结果不得因重算改写；需要修订时另行设计，不能换 ID 绕过唯一性。

## 4. 单条诊断上报与响应

```http
POST /api/insight/v1/internal/monitoring/diagnosis-events
Content-Type: application/json
Idempotency-Key: <eventId>
```

请求体是一条 DiagnosisEvent（完整样例见 §8）。`Idempotency-Key` 必填且与 body.eventId 相同，否则 400。请求 JSON UTF-8 解码前总字节上限 128 KiB；数组和超长字段拒绝，不静默截断。

| 场景 | HTTP | 结果 |
|---|---:|---|
| 首次事务提交成功 | 201 | `accepted=true, stored=true, duplicate=false` |
| 同 ID、归一化全部字段相同 | 200 | `accepted=true, stored=true, duplicate=true` |
| 同 ID、任意事件字段不同 | 409 | 不覆盖既有记录 |

```json
{"accepted":true,"eventId":"mock-diag-te-alert-001","stored":true,"duplicate":false}
```

重复响应仅将 `duplicate` 改为 true。ACK 必须在业务数据库提交后返回；`noop`、仅内存接收、排队未落库都不能返回成功。两个并发相同 POST 依靠数据库唯一约束裁决，不只依赖“先查再插”。接收时间等服务器字段不参与冲突比较；字段顺序、null 与省略、等价时区表示不构成冲突。

发送端只在收到 200/201 且 ACK 的 accepted/stored 为 true、eventId 匹配、duplicate 类型和语义正确后确认成功。200 登录页、空 body、错误 ID、202 不能清除待发记录。

## 5. Bot 检查状态上报

```http
POST /api/insight/v1/internal/monitoring/bot-checks
Content-Type: application/json
```

| 字段 | 类型 / 必填 | 定义 |
|---|---|---|
| `schemaVersion` | string / 是 | `claw-monitoring/bot-check/v1` |
| `botId`, `engine` | 同诊断 / 是 | 必须与固定配置匹配 |
| `checkedAt` | ISO / 是 | 本次真实检查结束时间；UNKNOWN/PAUSED 取状态观察时间 |
| `lastSuccessfulCheckAt` | ISO/null / 是 | 本 Bot 最近成功完成检查时间，不是最后诊断时间；不晚于 checkedAt |
| `status` | enum / 是 | `HEALTHY / ERROR / UNKNOWN / PAUSED` |

检查成功但没有新 Trace 仍可报 HEALTHY；失败报 ERROR；归属不明、初始化未完成报 UNKNOWN。PAUSED 只在显式暂停配置或有序停机时报告；进程突然中断不可伪造 PAUSED。失败记录不能更新 lastSuccessfulCheckAt 为当前时间。

接收端保存每 Bot 一条最新状态，按 checkedAt 原子比较：新时间覆盖，旧时间忽略，相同时间同内容视为重复，相同时间冲突返回 409。禁止重试时重写 checkedAt。服务端拒绝 checkedAt 超过自身时间 5 分钟的请求，避免错误未来时间长期压住新状态。

成功统一 200：

```json
{"accepted":true,"botId":"mock-bot-te","applied":true}
```

发送端仅接受 HTTP 200 且 accepted 严格为 true、botId 与请求一致、applied 为 boolean 的 ACK；applied=true/false 都表示该快照已被接收端处理。空响应、HTML、错误 botId、202 或非法字段类型不能清除待发送快照。

旧状态 / 重复状态返回 `applied=false`。无需历史心跳表、MQ 或心跳 eventId。默认每 Bot 最多每 30 秒发送一次最新快照，错误转换可立即发送；没有新检查不能刷新时间。重试可合并为最新快照，但不得与诊断 outbox 争用至相互阻塞。

## 6. 页面查询契约

所有返回 `Cache-Control: no-store`。未知 Bot 返回 404；字段含义与写接口一致，返回所有约定键，可空值使用 null。

### 6.1 固定 Bot 清单

`GET /api/insight/v1/monitoring/bots`

```json
{"items":[{"botId":"mock-bot-te"},{"botId":"mock-bot-oc"}]}
```

按部署配置顺序排列；无记录 Bot 也出现。不返回 Bot 名称。

### 6.2 Bot 状态

`GET /api/insight/v1/monitoring/bots/{botId}/status`

```json
{"botId":"mock-bot-te","status":"HEALTHY","lastSuccessfulCheckAt":"2026-09-09T09:00:20Z","diagnosisCount":2}
```

`diagnosisCount` 为该 Bot 全部持久记录数，不随日期、关键词、decision 改变。没有状态或数据库中 checkedAt 超过部署阈值（默认 300 秒）时返回 UNKNOWN，lastSuccessfulCheckAt 保留真实值 / null；超时不伪造 ERROR。显式配置暂停优先 PAUSED。HEALTHY/ERROR 只在证据未过期时有效，阈值须大于实际最长正常检查周期。数据库失败返回 503，不是假零条 / 假 UNKNOWN。

### 6.3 诊断列表（包含展开详情）

`GET /api/insight/v1/monitoring/bots/{botId}/diagnoses`

| 参数 | 规则 |
|---|---|
| `startDate`, `endDate` | 可省略，严格 `YYYY-MM-DD`，北京时间自然日；结束日期包含当天 |
| `decision` | 默认 ALL；ALL/ALERT/PASS/UNRESOLVED |
| `keyword` | 默认空；trim 后最多 200 码点；字面子串搜索 diagnosisId/sessionKey/sessionId/traceId/TC 标签/业务大类与子类 |
| `page` | 默认 1，正整数且不大于 2147483647 |
| `pageSize` | 默认 10，只允许 10/20/50 |

日期倒置、非法值、同名参数重复传值返回 400；未知查询参数拒绝。对 `%`、`_`、反斜杠转义，所有 SQL 参数化。搜索英文大小写不敏感，固定 ASCII ID 和中文样例在两种数据库一致，不承诺复杂 Unicode 大小写折叠。

例：2026-09-09 对应 `[2026-09-08T16:00:00Z, 2026-09-09T16:00:00Z)`。occurredAt 为 null 的记录：有任意日期筛选时排除，全部时间放末尾。默认 occurredAt 倒序，同时间按 diagnosisId ASCII 倒序。数据库做分页，不全量读入内存。

```json
{
  "botId":"mock-bot-te", "page":1, "pageSize":10,
  "total":1, "totalPages":1,
  "counts":{"all":2,"alert":1,"pass":1,"unresolved":0},
  "items":[{
    "diagnosisId":"mock-diag-te-alert-001","botId":"mock-bot-te",
    "decision":"ALERT","occurredAt":"2026-09-09T09:00:00Z",
    "diagnosedAt":"2026-09-09T09:00:18Z",
    "sessionKey":null,"sessionId":"mock-session-001","traceId":"mock-trace-001",
    "tcFaultLabel":"TC.MCP.DATA","confidence":0.86,
    "businessProblemCategory":"外部服务异常","businessProblemSubtype":"数据获取失败",
    "systemDiagnosis":"数据查询工具返回空响应，本轮数据获取失败。",
    "businessDiagnosis":"用户要求重试此前未完成的数据查询，本次重试仍未取得结果。",
    "handlerName":null,"humanIntervention":true
  }]
}
```

以上是 `decision=ALERT` 的响应示例。counts 应用 Bot/日期/关键词但不应用 decision；total 和 items 应用全部条件。`totalPages=ceil(total/pageSize)`，零条为 0，越界页 200 空 items、保留真实 total，不偷偷改 page。首版普通页码，不做分页快照；刷新时并发新数据可能使页面位置变化。

items 只含示例中的字段（不含 schemaVersion/eventId/engine）；一页数据已含详情，不新增完整原文接口或详情回查 AI Vision。

## 7. 错误与重试

写接口错误体（监控子路由统一，不改现有治理 API 错误结构）：

```json
{"error":{"code":"MONITORING_INVALID_EVENT","message":"上报字段不符合契约。","requestId":"req_mock_001"}}
```

| HTTP / code | 原因 | CV 行为 |
|---|---|---|
| 400 / MONITORING_INVALID_EVENT | 非法 JSON、版本、类型、Header 不一致 | 保留失败记录，修复后再发送，不盲重试 |
| 401（上游网关等） | 非本模块 Token 校验；入口访问限制 | 保留并阻断发送，核查网关/部署，不尝试添加 Token 或浏览器登录 |
| 403 / MONITORING_BOT_NOT_ALLOWED | Bot 未配置 / engine 不匹配 | 保留隔离，修复配置 |
| 409 / MONITORING_EVENT_CONFLICT | 同 ID 内容冲突 / 同时间状态冲突 | 保留排查，不覆盖 |
| 413 / MONITORING_PAYLOAD_TOO_LARGE | 请求 / 文本超限 | 修复脱敏及投影，不重试原包 |
| 429 / MONITORING_RATE_LIMITED | 限流 | 遵循合法 Retry-After，否则退避 |
| 503 / MONITORING_NOT_READY | 存储或模块未就绪 | 保留，退避 / 修复配置 |
| 500/502/504 | 服务或网关临时失败 | 保留，退避 |

连接失败、超时、ACK 不合法与 5xx 一样可重试；404/405 表示 URL/部署不匹配，阻断并排查。单次退避耗尽不删除事件。日志不得打印 Token、完整响应正文或原始会话。

GET 的 401 按页面登录态处理；参数 400、未知 Bot 404、存储 503、超时 504 有明确页面提示；不能把写接口的网关访问故障变成“请用户登录”。

## 8. 可复制的本地 Mock 样例与步骤

样例有两个独立用途：

- Avernet 团队：Mock 仅替代“AIStudio 产生诊断”，经真实 POST、Repository、dev 数据库和 GET 验证页面。
- AIStudio 团队：从本节复制 JSON 作为 fixtures，在同一 AIStudio 环境启动 Python loopback 测试接收端，验证本仓库发送器；不需要 Avernet 或 ODC。接收端的响应/故障注入要求见同目录实施 Spec §10。

静态 HTML 只能验证外观；假接收端只能验证发送协议和恢复行为，两者均不证明真实业务库落表或跨环境连通。

### 8.1 保存为 `alert.json`（TE，人工干预为是）

```json
{
  "schemaVersion":"claw-monitoring/diagnosis-event/v1",
  "eventId":"mock-diag-te-alert-001","diagnosisId":"mock-diag-te-alert-001",
  "botId":"mock-bot-te","engine":"TE",
  "sessionKey":null,"sessionId":"mock-session-001","traceId":"mock-trace-001",
  "occurredAt":"2026-09-09T09:00:00Z","diagnosedAt":"2026-09-09T09:00:18Z",
  "decision":"ALERT","tcFaultLabel":"TC.MCP.DATA","confidence":0.86,
  "businessProblemCategory":"外部服务异常","businessProblemSubtype":"数据获取失败",
  "systemDiagnosis":"数据查询工具返回空响应，本轮数据获取失败。",
  "businessDiagnosis":"用户要求重试此前未完成的数据查询，本次重试仍未取得结果。",
  "handlerName":null,"humanIntervention":true
}
```

### 8.2 保存为 `pass.json`（同 Session，不同 Trace，不合并）

```json
{
  "schemaVersion":"claw-monitoring/diagnosis-event/v1",
  "eventId":"mock-diag-te-pass-002","diagnosisId":"mock-diag-te-pass-002",
  "botId":"mock-bot-te","engine":"TE",
  "sessionKey":null,"sessionId":"mock-session-001","traceId":"mock-trace-002",
  "occurredAt":"2026-09-09T09:05:00Z","diagnosedAt":"2026-09-09T09:05:18Z",
  "decision":"PASS","tcFaultLabel":null,"confidence":null,
  "businessProblemCategory":"正常","businessProblemSubtype":"目标已完成",
  "systemDiagnosis":"本轮工具调用正常完成。","businessDiagnosis":"用户发布的新查询任务已完成。",
  "handlerName":null,"humanIntervention":false
}
```

### 8.3 保存为 `unresolved.json`（OC，可空时间 / 定位）

```json
{
  "schemaVersion":"claw-monitoring/diagnosis-event/v1",
  "eventId":"mock-diag-oc-unresolved-003","diagnosisId":"mock-diag-oc-unresolved-003",
  "botId":"mock-bot-oc","engine":"OC",
  "sessionKey":"agent:main:mock-session-003","sessionId":null,"traceId":null,
  "occurredAt":null,"diagnosedAt":"2026-09-09T09:10:18Z",
  "decision":"UNRESOLVED","tcFaultLabel":null,"confidence":null,
  "businessProblemCategory":null,"businessProblemSubtype":null,
  "systemDiagnosis":"诊断模型暂不可用，无法生成有效结论。","businessDiagnosis":null,
  "handlerName":null,"humanIntervention":false
}
```

### 8.4 HTTP 操作（Avernet 真实接收端验收参考）

前提：测试宿主已配置到经确认的 dev 数据源（SQLite 可作辅助，不替代真实库验收）；固定清单为 mock-bot-te/TE、mock-bot-oc/OC，无需上报凭据。下列端口替换为实际 Host 端口；不设置上报 Token。AIStudio 无真实接收端时只用 POST 样例测试同环境的假接收端，不执行下面的 GET/数据库验收，也不要求连接开发者电脑。测试数据仅写测试环境，结束按指定测试编号清理，不清空表。

```bash
export CLAWWEB_BASE_URL='http://127.0.0.1:3000'
# draft.3：无需上报 Token 或 Authorization。
curl -i --fail-with-body "$CLAWWEB_BASE_URL/api/insight/v1/internal/monitoring/diagnosis-events" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: mock-diag-te-alert-001' --data-binary @alert.json

curl -i --fail-with-body "$CLAWWEB_BASE_URL/api/insight/v1/internal/monitoring/diagnosis-events" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: mock-diag-te-pass-002' --data-binary @pass.json

curl -i --fail-with-body "$CLAWWEB_BASE_URL/api/insight/v1/internal/monitoring/diagnosis-events" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: mock-diag-oc-unresolved-003' --data-binary @unresolved.json

curl --fail-with-body "$CLAWWEB_BASE_URL/api/insight/v1/monitoring/bots/mock-bot-te/diagnoses?decision=ALERT&page=1&pageSize=10"
```

重复第一条 POST 预期 200 duplicate=true；改同 ID 的 businessDiagnosis 后预期 409；全量 GET 的 TE total=2，OC total=1；OC 加日期筛选 total=0。先重启本地服务再 GET，记录必须还在。

状态 Mock 保存为 `check.json`，checkedAt 与 lastSuccessfulCheckAt 使用本次测试当前 UTC 时间（如下 Python 在 loopback 测试目录生成，避免历史固定日期误报 UNKNOWN）：

```bash
python3 - <<'PY'
import json
from datetime import datetime, timezone
now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
with open('check.json', 'w') as f:
    json.dump({'schemaVersion':'claw-monitoring/bot-check/v1', 'botId':'mock-bot-te',
               'engine':'TE', 'checkedAt':now, 'lastSuccessfulCheckAt':now, 'status':'HEALTHY'}, f)
PY
curl -i --fail-with-body "$CLAWWEB_BASE_URL/api/insight/v1/internal/monitoring/bot-checks" \
  -H 'Content-Type: application/json' \
  --data-binary @check.json
curl --fail-with-body "$CLAWWEB_BASE_URL/api/insight/v1/monitoring/bots/mock-bot-te/status"
```

### 8.5 验收分层

| 层次 | 实测对象 | 不依赖 |
|---|---|---|
| CV 独立单元 / HTTP 测试（可在 AIStudio 内执行） | DTO 与 outbox → 同环境 Python 假接收端；超时 / ACK 丢失 / 401 / 409 | Avernet/OCB 仓库、ODC、跨环境网络 |
| Avernet dev 库闭环 | 上述 JSON → 真实监控 Router → dev 业务库 → GET → 正式页面 | AIStudio 发送端；数据库代理连接仍需可用 |
| 预发联调 | AIStudio → 实际 Host → 实际业务库，ODC 手工查询一致 | 不可用本地 mock 冒充通过 |

本地通过不等于生产 DB 方言、网关登录豁免、内网访问限制、AIStudio 出站网络和持久卷已验证。

## 9. 契约验收清单

- 两仓库副本 hash 相同（AIStudio 只提供本仓库 hash，本地团队完成比较），三类样例可解析；OC/TE、true/false、空定位都覆盖。
- 重复与并发写不重复；冲突 409；ODC 故障 / noop 不返回假 ACK。
- ACK 超时重试只保存一条；拒绝 200 HTML 和错 eventId。
- 空轮询仍可 HEALTHY；无检查变 UNKNOWN；旧状态不能覆盖新状态；不同 Bot 状态不串。
- UTC 与北京时间边界、null 时间、页码越界、关键词转义、counts/total 区别有测试。
- humanIntervention 在 POST → 存储 → GET → 展开详情完整保留，不加入通知状态和自愈操作。
