# Agent 监控自愈：Bot 搜索、权限、新监控表与诊断分页最终实施 Spec

**版本：v2.7 · 紧凑分页与适中列间距**
**日期：2026-09-20**
**状态：功能已实现；本地验证结果见提交说明，仍待生产联调与部署验收。**
**修改仓库：Avernet（公共实现）和 OCB（最小接线）；不修改 claw-validation。**

> 本版完整替代 v1.0 与 v2.0；保留 v2.0 的数据、权限和兼容决策，仅将已确认的 V7 局部界面调整纳入最终实施与验收范围。用户已完成新表创建，本轮不执行 DDL、不修改旧 UNIQUE、不自动迁移数据库。claw-validation 正在更新，本轮不修改其源码、配置格式、本地队列、报送协议或事件 ID 生成方式。根据用户最新确认，允许 OCB 最小接线，复用已有登录服务、管理员名单和 ac_bots 连接；不另建认证系统。

---

## 1. 决策摘要与范围

### 1.1 本期必须实现

1. 普通已认证用户可以进入监控页面，只能搜索和查看自己拥有的 Bot。
2. 管理员默认查看“我的 Bot”，可切换“诊断范围”“全部 Bot”。
3. 普通用户搜索 Bot 名称、Bot ID；管理员额外支持工号搜索。
4. 选择器默认收起，点击打开候选；有限高度约展示 2–3 行，内部滚动配合服务端分页，不下载全部 Bot。
5. 候选统一头像位、卡片样式、右侧监控状态，展示名称、真实 Bot ID、归属工号和诊断摘要，不显示“我的 Bot/用户空间”等归属描述。
6. 所有符合权限的有效目录 Bot 可搜索，不以是否被监控限制目录可见性。
7. 新 checks 表中存在完整目标对应行才表示接入；仅有诊断不表示接入。
8. 未监控 Bot 的“加入监控”只放在主界面；本期为固定提示，不写表、不注册、不启动任务。
9. 使用新表和 `(bot_id, entity_id, env)` 身份查询、统计和授权。
10. Avernet 兼容现有 v1 报送，在接收端补齐身份；ACK、事件 ID、旧采集端发现契约保持兼容。
11. 在已确认页面的诊断列表底部增加首页/末页、动态页码、直接跳页和每页条数，沿用服务端分页，不全量加载诊断。
12. TC 故障标签使用紧凑固定列宽，额外右侧留白 16px，单行超长省略并提供全文读取，表头与内容对齐；宿主顶栏和侧栏外观不变。
13. 本次 V7 视觉增量严格限定为第 11、12 项；不得借此重新设计标题、导航、Bot 选择器、筛选、记录样式、统计或详情区域。

### 1.2 不在本期范围

- 修改 claw-validation，升级 v2 DTO、ACK、SQLite 队列或幂等算法。
- 保证一个现有采集进程能够同时监控多个上报 ID 都是裸 `default` 的目标。
- 新增第三张登记/身份映射表、自动创建 checks、真实加入监控。
- 在 OCB 复制公共监控业务或重做全局认证。允许最小依赖接线及复用已有登录服务的验证身份入口。
- 改造诊断算法、采集调度、通知、自愈执行、治理权限。
- 自动 ALTER/DROP 旧表、自动迁移历史记录、改写旧 event_id。
- 强制接入真实头像。本期统一默认头像，可信头像能力可后续接入。
- 对已确认页面做整页重设计、替换为另一套表格、增加表体内部固定高度滚动、修改演示记录以伪造多页效果。
- 为本次诊断分页和列间距另建接口、统计表、游标平台、数据库列或索引；不因此扩大 OCB / claw-validation 修改范围。

### 1.3 能力边界

“全部 Bot 可见”是目录能力，不代表“全部 Bot 已采集”。两个用户的 default 可以同时出现在目录里，但没有对应新 checks 行的目标仍为未监控。

Avernet 可以补齐缺少的身份，不能恢复在发送前就被合并的数据。若旧上报在接收端仍无法唯一归属，应阻断该报送并给出运维错误；不能猜测归属。上线前必须确认当前正在监控的目标都能解析，不能以“安全拒绝”代替现有监控连续性验收。

---

## 2. 已确认基线与代码证据

以下为本地源码检查结果，不代表线上数据库或正在更新的 claw-validation 分支已经具备相同实现。

| 位置 | 当前实现与影响 |
|---|---|
| Avernet `server/services/monitoring/schema.ts` | 旧表名为复数，checks 只有裸 bot_id 唯一键 |
| `server/repositories/monitoring-schema-check.ts` | 表名常量集中于此；旧校验要求裸 bot_id 唯一索引 |
| `server/repositories/monitoring-repository.ts` | `applyCheck/readStatus/listDiagnoses` 按裸 bot_id；旧 `listBots` UNION 两张表 |
| `server/services/monitoring/monitoring-service.ts` | 校验 DTO 后直接写 store；check ACK 回显输入 botId |
| `server/services/monitoring/monitoring-runtime.ts` | 延迟装配；OCB 显式传入既有 db、目录及已验证身份 |
| `server/routes/monitoring.ts` | 普通监控读路由统一管理员限制；内部写路由依赖部署侧网络边界 |
| `web/pages/InsightCenter/index.tsx` | `canViewMonitoring` 仅判断监控管理员 |
| `web/pages/InsightCenter/monitoring/MonitoringPanel.tsx` | 已有上一页/下一页、10/20/50 条切换及最多 5 个连续页码；不是后端仅支持 5 页，但缺少直接跳页和显式首页/末页 |
| `web/types/monitoring.ts`、`server/services/monitoring/validation.ts` | 已有 page/pageSize/total/totalPages；page 为正整数，pageSize 支持 10/20/50 |
| `server/repositories/monitoring-repository.ts` 的 `listDiagnoses` | 已有 COUNT 与 LIMIT/OFFSET 分页，计数/记录在同一 SQL 语句读取；保持其一致性与排序语义 |
| `web/pages/InsightCenter/monitoring/DiagnosisRecord.tsx`、`monitoring.css` | 已有 TC 标签 title、详情全文和 ellipsis；需补足与时间列的实际留白，不重写详情组件 |
| claw-validation `online_config.py` | 当前可见配置只有 bot_id/engine，并拒绝重复裸 ID |
| claw-validation `reporting/contracts.py` | v1 严格字段集合，没有 entityId/env；事件 ID 由发送端生成 |
| claw-validation `reporting/storage.py` | 本地 checks 队列以 bot_id 为主键 |
| claw-validation `reporting/client.py` | check ACK 核对原输入 botId；diagnosis ACK 核对 eventId、stored、duplicate 和 HTTP 状态 |
| claw-validation `reporting/discovery.py` | 旧列表要求 botId 不重复，报送后目标不得 missing |
| OCB `src/evolverun/clawweb/package.json` | 使用 `.build/avernet/.../public` 公共模块 |

Avernet 路径基准：`src/evolverun/clawweb/public/modules/clawinsight/`。claw-validation 仅作为只读兼容参考；其当前更新不属于本轮写入范围。实际部署版本的请求样例、发现行为需在发布前复核。

---

## 3. 数据源、身份与数据库契约

### 3.1 新表及旧表

| 用途 | 本期新表 | 停用但保留的旧表 |
|---|---|---|
| 最新检查状态 | `insight_monitoring_bot_check` | `insight_monitoring_bot_checks` |
| 诊断事件 | `insight_monitoring_diagnose` | `insight_monitoring_diagnoses` |

用户早先曾输入 `nsight_monitoring_diagnose`，后续 DDL 按有首字母 i 的 `insight_monitoring_diagnose` 提供。本 Spec 采用后者。**用户已确认完成建表，但尚未读取线上 SHOW CREATE TABLE；上线前只读核对实际名称和结构。若实际少了 i，须先统一此文档与代码常量，不自动重建或偷偷尝试两种表名。**

新表是唯一生产监控数据源。切换后不 UNION 新旧表、不自动 fallback 旧表、不双写旧表，防止同一目标重复计数和歧义数据重新暴露。

### 3.2 新表必要结构

保留用户提供的全部旧业务字段、类型和时间语义，仅采用已讨论的新表变化：

| 字段/索引 | checks | diagnoses |
|---|---|---|
| bot_id | VARCHAR(128)，latin1_bin，NOT NULL | 同左 |
| entity_id | VARCHAR(128)，utf8mb4_bin，NOT NULL，无默认值 | 同左 |
| env | VARCHAR(20)，utf8mb4_bin，NOT NULL，无默认值 | 同左 |
| 主键 | id 自增 | id 自增 |
| 唯一键 | `uk_bot_id_entity_id_env(bot_id, entity_id, env)` | `uk_monitor_diag_event(event_id)` |
| 普通索引 | 本期不额外要求状态索引 | `idx_monitor_diag_target_time(bot_id, entity_id, env, occurred_at_ms, event_id)` |
| 普通索引 | — | `idx_monitor_diag_target_dec_time(bot_id, entity_id, env, decision, occurred_at_ms, event_id)` |

生产沿用已提供的 OceanBase MySQL 模式 DDL/LOCAL 索引；SQLite 测试使用等价复合约束。不得保留新 checks 表上单独的 UNIQUE(bot_id)，否则多个 default 仍无法共存。

schema verifier 检查实际列类型、长度、非空、字符排序规则、完整唯一键和索引列顺序，不只比较索引名称；同时拒绝限制更强的旧裸 bot_id 唯一键。只读检查，不执行生产建表、ALTER 或共享版本迁移。

entity_id 按字符串处理，保留前导零。两列必须为非空白且长度合法；超长/缺失拒绝，不截断、不默认空字符串，不用 `unknown` 或 `prod` 补位。VARCHAR(128/20) 是监控表契约，不是对线上 ac_bots 长度的已验证声明。

### 3.3 Bot 目录

以现有只读目录连接/provider 查询 `ac_bots`，不把名称、归属信息复制到监控表。

| 字段 | 用途 |
|---|---|
| bot_name | 展示和名称搜索；空值显示“未命名 Bot” |
| bot_id | 展示真实业务 ID，与监控目标关联 |
| entity_id | 实体身份，优先于 creator_id/owner_id 推断 |
| env | 被监控 Bot 所属环境 |
| owner_id | 归属工号、普通用户权限判断、管理员工号搜索 |
| owner_name | 归属用户名，可保留在服务端模型；本期不增加额外行 |
| is_delete | 排除已删除 Bot |
| avernet_tenant | 有此目录维度时由可信上下文限制，不能由普通请求覆盖 |
| id | 可作稳定分页键，不作为授权凭据 |

只采用自己拥有的 Bot，不复用“owner 或 collaborator”并集作为成员可见范围。owner 与 entity 概念不同，不用创建人替代所有者权限。

### 3.4 身份模型和隔离前提

```text
StorageTarget = (botId, entityId, env)
LogicalTarget = (trustedTenantContext, StorageTarget)
```

新表未保存 tenant，因此必须保证一个监控数据存储命名空间只属于一个可信 tenant，或数据库路由已做到 tenant 隔离。**如果同一物理两表存储多个 tenant 且三字段可能重复，仅靠目录过滤不够；这属于部署阻断项，不能声称当前 schema 已支持该场景。** 本期不新增 tenant 列。

env 是 Bot 的环境，不是必然等于 Avernet 机器环境。允许范围由可信部署配置/provider 给出；不能仅因服务部署在 prod 就把全部上报写为 prod。环境别名只有经过确认的统一转换可用，否则按目录原值匹配。

---

## 4. Avernet 接收端补齐身份（保持发送端不变）

### 4.1 外部 DTO 与内部存储对象分离

保持当前两个 URL 和 v1 schemaVersion：

```text
POST /api/insight/v1/internal/monitoring/bot-checks
POST /api/insight/v1/internal/monitoring/diagnosis-events

claw-monitoring/bot-check/v1
claw-monitoring/diagnosis-event/v1
```

不要求旧请求添加 entityId/env，不放宽原本的严格 DTO 校验，不新增 v2。

```ts
type MonitoringTarget = { botId: string; entityId: string; env: string };
type ResolvedReport<T> = {
  wire: T;                 // 原始已校验 DTO：保留 sender botId/eventId
  target: MonitoringTarget;
};
```

Repository 接收内部对象，持久化 target.botId/entityId/env；ACK 仍使用 wire.botId/eventId。不要直接把新字段塞进旧 parseCheck/parseDiagnosis 的严格 DTO，再触发未知字段拒绝。

### 4.2 字段来源

| 持久化字段 | 来源 |
|---|---|
| bot_id | 经核实目录目标的真实 bot_id，通常等于原报送 botId |
| entity_id | 唯一解析目标的 ac_bots.entity_id |
| env | 同一目录目标的 ac_bots.env，并校验允许的目标环境 |
| engine/status/时间/诊断内容 | 原 v1 报送，保持已有校验与脱敏规则 |
| event_id/schema_version | 原报送值，不重算、不改写 |
| received_at_ms | Avernet 当前接收时间 |
| id/gmt_create/gmt_modified | 数据库定义 |

Bot 名称、owner_id、owner_name 在查询时从目录获取，不从 eventId、handler_name、session_key、trace_id 或诊断文本猜测。

### 4.3 解析算法

接收路径统一经过 `resolveReportedTarget()`：

1. 校验原 DTO、报送接入边界和 Idempotency-Key（诊断）。
2. 取得服务端可信目录租户和目标环境范围；不能使用页面登录用户作为采集归属。
3. 如果存在经审核的明确绑定，按该完整目标查询并验证；否则在可信范围内以**精确、保留大小写的**原 botId 查询有效目录。
4. 唯一有效目标：读取三字段，验证长度、允许范围，形成内部对象。
5. 无目标：明确失败；多个目标：身份歧义失败；目录不可用：暂不可用。均不得写 checks 或 diagnosis。
6. 写入成功提交后，才返回原格式成功 ACK。

不能将“只有一个目标已有 checks”当成唯一归属证据；解析范围不能用用户页面搜索过滤条件缩小。UI 模糊搜索与写入精确匹配是两套规则。

### 4.4 最小的实例别名/绑定支持

优先使用目录唯一匹配，不先建设通用映射平台。只有当前上报确实使用实例别名、或有经核实的独占目标时，允许通过 Avernet 已有配置通道注入小规模只读绑定：

```ts
type LegacyReportBinding = {
  reportedBotId: string;
  target: MonitoringTarget;
};
```

- 绑定必须有实际采集配置/样本与目录核对依据；不通过拆字符串猜工号。
- 同一可信接收范围内，同一 reportedBotId 只能绑定一个目标；多个上报别名默认也不允许指向同一目标，以免无意合并队列状态。
- 裸 default 仅在可证明该接收范围全部发送者都指向同一个目标时允许固定绑定。不能用一条绑定掩盖其他用户同名报送。
- 当前路由没有逐发送者身份认证。不得把任意请求 header、来源 IP、engine 或收到的时间当作可信发送者身份。
- 不新建数据库映射表、不增加管理 UI、不改 claw-validation 配置。
- 映射变更必须停写、核对 pending/retry 和已存目标，审核后再启用；不能运行中把同一个报送 ID 重新绑定给另一目标。

只读预检输出当前目标清单与解析结果，可作为发布工件固定已审核绑定。重试、服务重启和滚动实例必须使用同一版本的解析范围/绑定。若无法保证同一旧 ID 的目标稳定，则该目标不具备安全切换条件。

### 4.5 ACK 和错误兼容

保持成功响应：

```json
{"accepted":true,"botId":"原上报botId","applied":true}
```

```json
{"accepted":true,"stored":true,"eventId":"原eventId","duplicate":false}
```

- check 成功 HTTP 200，applied 为 boolean；较旧检查不覆盖新检查并返回 applied=false。
- diagnosis 首次写入 HTTP 201 / duplicate=false；完全一致的重复 HTTP 200 / duplicate=true。
- event_id 已存在但 target 或任何原业务内容不一致：HTTP 409，不覆盖、不确认 duplicate。
- 相同检查时间、相同目标但内容不同：沿用冲突语义。
- 永久身份错误（无可解析目标、歧义、绑定冲突）：HTTP 409 + `MONITORING_TARGET_UNRESOLVED` / `MONITORING_TARGET_AMBIGUOUS` / `MONITORING_TARGET_BINDING_CONFLICT`。
- 数据源/配置/表结构暂不可用：HTTP 503，不能伪装 404、空列表或成功。
- 输入字段错误仍按现有 400/413；不能为了兼容返回 accepted=true 却未落库。

当前客户端把 409 置为 blocked；修复映射后不一定自动恢复。因此预检必须在切换前完成，恢复 blocked 数据要走现有人工操作流程，不能假设无需重放。503 沿用客户端重试。

### 4.6 重复事件比较与存储解码

不得拿数据库新 target.botId 重新构造原始别名 DTO 后直接比较 wire JSON。规范化比较由原 DTO 除 botId 外的业务字段 + 解析后的完整 target 构成（用完整 target 替代 wire.botId），忽略 received_at 等接收元数据。输入与库中记录分别规范化后比较，不要求新表额外保存别名列；别名的一对一约束与重试稳定性由第 4.4 节保证。

event_id 保持原值，不能在接收端重新哈希加入 entity/env，否则旧客户端的 Idempotency-Key 与 ACK 会不匹配。不同目标撞 event_id 时只能冲突报警，本期不承诺消除此类发送端碰撞。

checks 所有 SELECT、INSERT、CAS UPDATE 条件使用三字段；不能遗漏 env 或仅按 bot_id 更新时间戳。同一目标出现不同 engine 时作为配置冲突处理，不能通过本次改造允许多个引擎轮流覆盖唯一状态。

---

## 5. 旧发现与旧详情接口兼容

继续保留以下接口的现有权限（管理员/既有可信调用方式），不因新 UI 开放普通用户而放开它们：

```text
GET /api/insight/v1/monitoring/bots
GET /api/insight/v1/monitoring/bots/:botId/status
GET /api/insight/v1/monitoring/bots/:botId/diagnoses
```

- `/bots` 是旧采集兼容接口，不是新选择器目录接口。
- 基于新 checks 与新 diagnoses 中实际持久化目标的集合，映射回已核实的旧上报 ID，返回原 `{items:[{botId}]}` 形状，排序去重。
- 只提供当前可信兼容范围内可无歧义表达的目标；不能把两个 target 合并为一项并让详情汇总，也不能伪造 checks 来满足 discovery。
- 若有实例别名绑定，列表必须回显旧客户端配置的上报 ID，不可只返回目录真实 default 导致 missing。
- 当前客户端发现检查允许额外唯一项，但报送后不能缺少预期目标。上线验收必须覆盖所有现有配置 ID，不以“大致兼容”代替。
- 无法无歧义表示的目标从兼容视图排除并记录诊断；如果是当前采集预期目标则阻断切换。新目录 API 仍能展示这些目标。
- 旧详情参数先用同一解析规则得到三字段再查新表；歧义返回 409，不按裸 bot_id 聚合。
- 旧 status 的 diagnosisCount 原时间口径保持不变；新 UI 使用第 7 节的统一时间窗口摘要。

---

## 6. 权限、目录与最小依赖装配

### 6.1 可信上下文

```ts
type MonitoringPrincipal = {
  staffId: string;
  isAuthenticated: boolean;
  isClawInsightAdmin: boolean;
  tenant: string;
  allowedTargetEnvs: readonly string[];
};
```

通过现有宿主可信认证适配，不要求 OCB 新增相同命名字段。没有可信工号/环境范围时拒绝访问或返回明确 NOT_READY，不能从 query/body/header 自行拼身份，也不能回退成管理员。

| 入口 | 普通用户 | 监控管理员 |
|---|---|---|
| 新监控页面 | 可以 | 可以 |
| scope=mine | 仅 owner_id=staffId | 仅本人 |
| scope=monitored | 403 | 当前授权范围内新 checks 对应目录目标 |
| scope=all | 403 | 当前授权范围内全部有效目录目标 |
| 搜索字段 | bot_name / bot_id | 另含 owner_id |
| 详情/加入占位 | 每次校验 owner_id | 每次校验租户及环境范围 |

未经认证为 401；目标不存在或无权限统一 404，scope 不允许统一 403。前端按钮可见性不是权限控制。

内部写路由的网络/认证边界必须保持，普通用户能读不等于能报送。不能放宽整个 InsightCenter 的治理、维修或管理员权限。

### 6.2 目录 Provider

在 Avernet 的监控模块提供小型 `MonitoringBotDirectory`，职责：按权限搜索、精确解析目标、批量取得目录行。只读复用已存在的 botDb/provider；不能硬编码数据库地址或增加一套 OCB SQL。

已核对 OCB bootstrap：`EvolveRepository(db)` 使用既有 db 查询 ac_bots。新增 `internal/bootstrap/clawweb/server/monitoring-runtime.ts`，在相同连接上实例化 Avernet SQL 目录，经已有 `createInsightRouter(..., { monitoring })` 显式传入。不重复实现目录 SQL，不新增强制位置参数。

身份复用 archive/auth 现有 Buservice / Asfagent 服务端会话查询，导出 `resolveVerifiedLoginUser`。不接受浏览器自报工号、仅解码 IAM cookie 或 request.isAdmin 作为监控权限。身份缓存 30 秒；角色每次读取现有 `AdminUserRepository.listEnabled()` 的 clawInsightAdmins；仅在未配置动态 repository 时使用静态名单，动态角色库故障不降级授权。

内部 OCB 范围为既有内部租户 teamclaw、目录环境 dev/pre/prod/gray。只读检查 ac_bots schema：有 avernet_tenant 列则过滤；成功确认无列才视为内部旧 schema，检查失败不能取消过滤。该接线仅用于内部单租户监控存储，上线需核对实际隔离。

依赖延迟初始化：监控目录失败不阻塞整个 Host 启动；查询/报送失败不能自动改用另一库。目录操作仅执行只读查询；本轮复用宿主共享 db，不声称该连接账号只有 SELECT 权限，也不新增数据库账号。

### 6.3 Bot 目录搜索与游标分页

- 名称/ID 的用户搜索大小写不敏感，写入解析仍精确；LIKE 通配符转义并参数化，中文、前导零保留。
- 默认 scope=mine；每次打开可重置搜索和范围到本人，但不清空已选 Bot。
- 默认 limit=20，最大 50；不返回所有权范围外的 total/分组数量。
- 稳定排序建议目录 id DESC，并以完整身份作 tie-break；cursor 绑定 principal、scope、查询、允许环境、时间窗口和排序版本，校验结构、上下文和过期。游标是不可信编码位置，不声称防篡改；SQL 每次重新应用权限过滤。
- mine/all：目录先 ACL/搜索/分页，再批量读对应目标的 checks 和统计；不逐 Bot 调详情。
- monitored 必须先按接入条件过滤再决定分页结果，不能简单取目录前 20 条再过滤后宣称无数据。
- 同库且具备权限时可使用三字段 EXISTS/JOIN；跨连接时用分批、限额的候选扫描 + 批量 checks 过滤，cursor 记录已消费扫描位置。达到工作预算可返回短页/空页和 nextCursor，不能错误设置末页或丢弃未消费候选。客户端提供继续加载并避免自动无限空页循环。
- 不要求数据库跨库 JOIN；不能全量下载所有 checks 或目录到 Node 内存。

---

## 7. 新查询 API、统计与状态

### 7.1 选择器

```http
GET /api/insight/v1/monitoring/bot-options
  ?scope=mine|monitored|all&q=...&limit=20&cursor=...
  &start=<UTC epoch ms>&end=<UTC epoch ms>
```

```json
{
  "items": [{
    "botRef": "opaque-target-reference",
    "botName": "日程助手",
    "botId": "default",
    "ownerId": "490858",
    "env": "prod",
    "enrollmentState": "ENROLLED",
    "monitoring": {
      "status": "HEALTHY",
      "checkedAt": "2026-09-20T07:00:00.000Z",
      "lastSuccessfulCheckAt": "2026-09-20T07:00:00.000Z",
      "diagnosedSessionCount": 4,
      "unidentifiedSessionDiagnosisCount": 1,
      "diagnosisCount": 6,
      "alertCount": 2
    },
    "capabilities": {"canView": true, "canRequestEnrollment": false}
  }],
  "nextCursor": null
}
```

未接入：`enrollmentState=NOT_ENROLLED`、`monitoring=null`、canRequestEnrollment=true。该 capability 只表示可点击占位入口，不表示功能已开放。

botRef 是服务端编码的三字段及租户上下文引用，使用版本化 base64url 编码；不是签名凭证，不新增密钥配置或 token 表，也不作为授权凭据。每次请求重新查询目录和 ACL。React key、轮询、请求缓存、URL 使用 botRef；退出登录/切换账号清空缓存。

### 7.2 详情与加入占位

```http
GET /api/insight/v1/monitoring/targets/:botRef/status?start=&end=
GET /api/insight/v1/monitoring/targets/:botRef/diagnoses?start=&end=&page=&pageSize=&decision=&keyword=&businessProblemCategory=&businessProblemSubtype=
POST /api/insight/v1/monitoring/enrollments
Content-Type: application/json

{"botRef":"opaque-target-reference"}
```

状态返回目标身份、接入状态及与候选相同的摘要。诊断分页保留现有分类、结论、关键字筛选能力。查询必须先授权，再按三字段读取。

未接入但有历史 diagnoses：状态仍未接入，主界面不展示历史统计；本期详情诊断接口返回 409 `MONITORING_NOT_ENROLLED`，不删除底层历史记录。

占位加入：先登录/目标 ACL/读取 checks。未接入时固定 HTTP 501：

```json
{
  "code": "MONITORING_ENROLLMENT_NOT_IMPLEMENTED",
  "message": "加入监控功能正在开发中，敬请期待。"
}
```

已接入时 HTTP 409 `MONITORING_ALREADY_ENROLLED`，提示已接入并刷新状态。任何分支都不新增 checks、不发消息、不触发采集、不切换选择。网络错误或 401/403/404/503 不伪装成功或固定占位提示。

### 7.3 接入与显示状态

| 数据事实 | 候选右侧文字 | 补充健康说明 |
|---|---|---|
| 无 checks | 未监控 | 主区暂无监控数据 |
| checks.status=PAUSED | 已暂停 | 可以展示既有统计 |
| checks.status=HEALTHY | 监控中 | 最近检查正常 |
| checks.status=ERROR | 监控中 | 小型警示/tooltip“最近检查异常” |
| checks.status=UNKNOWN | 监控中 | tooltip/主区“检查状态未知” |

保持用户确认的三种接入/运行标签。**“监控中”不是进程实时存活保证**；保留原 status 和最近检查时间，不能把 ERROR/UNKNOWN 渲染成绿色健康结论，也不凭过期心跳自动改 PAUSED。

目录/监控读取失败属于加载失败，不是未监控；summary 计算失败返回整体 503（本期不另建部分成功协议）。

### 7.4 统计定义

默认最近 7 个北京时间自然日（含今日，结束为明日 00:00），转 UTC 毫秒，使用 `[start,end)`；所有候选/状态接口回传或共享同一窗口。允许沿用页面日期选择器修改窗口，验证 start<end 及合理查询上限。

实现协议细化：两端均省略使用上述默认窗口；显式窗口须同时提供 start/end，有限边界为 UTC 毫秒且跨度不超过 366 天。页面“全部时间”显式发送 `start=all&end=all`，包含 NULL 会话时间；单端 `all` 表示该端无界，另一端仍须有效，存在时间条件时排除 NULL。此约定不修改旧接口日期参数或统计含义。

- diagnosisCount：窗口内该三字段目标的诊断事件条数。
- alertCount：其中 decision=ALERT 的条数；不等于有告警的会话数。
- diagnosedSessionCount：目标内按 engine + 可靠 session_id 去重，无 session_id 时用 session_key，并给两类标识加类型前缀避免值碰撞；不以 trace_id 或 event_id 替代。
- 缺少可靠会话标识仍计入诊断/告警，另返回 unidentifiedSessionDiagnosisCount。全部事件都缺会话标识时会话数返回 null，显示“—”；部分缺失时展示已识别会话数并说明覆盖不全；真正无诊断时为 0。
- 不跨目标合并会话。不做未经验证的 session_id/session_key 别名合并；该数字是可靠标识去重口径，不是扫描覆盖总量。
- occurred_at_ms 为 NULL 的事件不计入指定发生时间窗口，不能偷偷改用 diagnosed_at_ms；说明该口径并保留历史原始行。
- 候选摘要不受详情当前页、关键字和结论筛选影响；详情筛选后的总条数可不同。
- 按候选目标批量聚合，避免只按 bot_id 分组；不存冗余计数列、不新建统计表。


### 7.5 诊断列表分页契约（复用既有能力）

本节只约束第 7.2 节 `targets/:botRef/diagnoses` 的数字页码分页。**Bot 选择器继续使用第 6.3 节 cursor 分页**，两者使用独立分页状态；仅搜索/翻动候选目录不重置诊断页，实际切换所选 Bot 时诊断回第 1 页。

- 沿用 `page`、`pageSize` 请求参数和 `page`、`pageSize`、`total`、`totalPages`、`items` 响应字段；保持已有 `counts` 和 `problemTypes` 口径。本次分页不要求额外响应字段。
- UI 初始 page=1、pageSize=20，每次显式传参；可选 10/20/50。保留旧接口未传 pageSize 时的默认值 10，不为页面默认值改变旧客户端契约。
- 服务端先校验登录、botRef 对应目录目标和 ACL，再按完整三字段、时间窗口和全部当前筛选查询。`total` 是全部匹配诊断数，不是当前页长度；`totalPages = ceil(total / pageSize)`，无记录时为 0。
- 保持现有稳定排序：发生时间未知项最后、`occurred_at_ms DESC`、`event_id DESC`。COUNT 与 items 必须使用相同目标和筛选条件及一致读取语义，不因增加跳页拆成可互相矛盾的计数/列表查询。
- 每次请求最多返回 pageSize 条；使用既有参数化 LIMIT/OFFSET，不先拉全量再在前端 slice。HTML 的本地 slice 只用于演示，不能移植为生产数据加载方式。
- 复用既有正整数校验和 page 上限 2147483647；非整数、非数字、负数、0、超上限、重复/嵌套参数、非法 pageSize 返回 400。UI 数字规范化后再发送，不能通过 parseInt 把 `2abc` 接受为第 2 页。
- 合法正整数但大于本次 totalPages：保持返回请求 page、实际 total/totalPages 和空 items 的语义，不悄悄返回另一页并谎报页码。前端按第 8.3 节处理数据减少后的越界。
- 目录候选摘要不随诊断翻页变化；详情 `counts`/分类选项沿用原查询口径，不从当前页重新统计。
- 首页即 page=1；末页即最近成功响应的 totalPages，不需要“获取末页”专用接口。大 OFFSET 延迟需实测，但本期不借此更改已建表索引；超时显示可重试错误，不能伪造成功、降低 total 或静默截成前 5 页。
- 稳定排序不等于跨请求快照锁定：持续上报时后续页可能随新记录移动。沿用当前实时查询语义，不承诺本期增加跨页快照 token 或无重复浏览会话。

---

## 8. 前端实施与交互验收

### 8.1 唯一视觉基准与修改边界

视觉基准为本目录更新后的 **V7 `member.html` 与 `admin.html`**（`index.html` 等同 member）。V7 是已确认 V6 页面的局部补丁，不是一套新页面；仅作为布局/交互参考，不代表真实数据和权限已接通。

- 普通用户：`design-demos/agent-monitor-search/member.html`。
- 管理员：`design-demos/agent-monitor-search/admin.html`。
- 此前 `design-demos/agent-monitor-pagination/` 中的整页分页 Demo **不作为实现或视觉验收依据**；不得复制其页面框架、数据集、标题/统计改版或表体滚动设计。
- 相对已确认 V6，允许的视觉变化只有：诊断列表 footer，以及 TC 标签/时间列为增加间距而必需的列宽调整与溢出处理。保持原有导航、标题、上下文卡片、Bot 选择器、筛选区、记录内容、行高、展开详情和加入提示。
- V7 不额外增加可见统计、新筛选项或场景开关；不改变既定角色能力、日期口径、数据排序和真实数据量。第 7.4 节的生产时间窗口约定不因 HTML 固定日期演示而更改。

### 8.2 Bot 选择与监控交互（沿用已确认要求）

1. 普通用户 placeholder：“搜索 Bot 名称或 Bot ID”；管理员：“搜索 Bot 名称、Bot ID 或工号”。
2. 初次进入优先恢复已授权 botRef；无有效选择则从 mine 第一页确定默认目标或显示“请选择 Bot”。不能自动选中别人的 Bot；mine 为空时管理员可自行切范围。
3. 关闭时只显示当前目标摘要，不平铺自己的全部 Bot。
4. 候选区约 2–3 行可视高度，内部滚动，在底部通过“加载更多 Bot”按需分页；首屏可加载 20 条数据但不展开全部行，不自动循环请求空页。
5. 所有候选统一默认头像、实线卡片；不为未监控目标省略头像或使用虚线。
6. 展示 Bot 名称、ID、工号，不追加“我的 Bot/用户空间”描述。状态统一右侧，统计独立一行；env 可在 tooltip/可访问标签补充，跨环境同名同归属时必须有可见的小型环境标识以便区分。
7. 已监控候选显示“已诊断会话 / 诊断 / 告警”及对应数字；未监控显示“暂无监控数据”，不放假 0。
8. 搜索候选无加入按钮；主区未监控空态有独立、与既有样式一致的“加入监控”按钮。
9. 点击占位后对话框仅显示：标题“加入监控暂未开放”，正文“加入监控功能正在开发中，敬请期待。”，按钮“知道了”。不得出现接口预留、未写数据库等研发说明。
10. 防抖约 250ms，处理中文输入法组合输入；切范围/搜索/目标/日期时取消旧请求或用序号丢弃旧响应。
11. 轮询只针对当前目标；未接入不轮询诊断。切目标先清理旧统计，不能闪现别人的内容。
12. Loading、空目录、无搜索结果、权限过期、查询失败、加载下一页失败分别呈现；失败允许重试，不显示“暂无 Bot”代替错误。
13. Escape 关闭、外部点击关闭、键盘上下选择/Enter 确认、焦点返回触发器；不能只用颜色表达状态。窄屏弹层不溢出视口。
14. 当前目标不在搜索结果当前页不应清空选择。失去 owner 权限/被删除时停止轮询并清空敏感数据。


### 8.3 诊断列表底部分页

**布局**：保留原列表 footer；左侧显示“第 a–b 条 / 共 N 条”，右侧依次为每页条数、首页、上一页、最多三个相邻数字页码、下一页、末页、`当前页 / 总页数`、`前往 [页码] 页 [跳转]`。沿用现有字体、蓝色选中态、轻边框和紧凑样式，不新增醒目的整块背景或改变列表高度策略。Demo 的“演示记录”辅助文案不进入生产。

**页码生成规则**（v2.6 覆盖 V7 的长页码条）：

| 条件 | 页码区内容 |
|---|---|
| totalPages ≤ 3 | 显示全部页码；totalPages=0 时无数字按钮 |
| totalPages > 3 且 current ≤ 2 | `1 2 3` |
| totalPages > 3 且 current ≥ totalPages−1 | `最后三页` |
| 其他 | `current−1 current current+1` |

- 数字区始终最多三个按钮，移除省略号/±5 快跳及重复的首末页数字；已有独立首页/末页按钮和直接跳页负责远距离导航，绝不限制可访问页数。
- 数值页码具有 aria-label，当前数字页有 `aria-current="page"`；保留每页条数、当前/总页数、直接跳页输入及校验，不通过缩小字体或点击区域来压缩布局。
- 首页禁用首页/上一页；末页禁用下一页/末页。只有 1 页时保留一个当前数字按钮，禁用四个方向/边界按钮；点击当前页不重复发请求。
- 跳页输入是独立字符串草稿，使用 `inputmode="numeric"`；只有点击“跳转”或 Enter 才提交，输入过程不发请求、不切页。支持键盘 Tab 导航。
- 去除首尾空白后，只接受数字组成的安全整数，范围为 1..最新已知 totalPages 且不超过 API 上限；可将前导零规范化为数字。空值、0、负数、小数、科学计数、混入字符及超范围值不提交。
- 无效输入在 footer 内显示“请输入 1–N 之间的整数页码”，标记 `aria-invalid`、关联错误说明并保持输入焦点；不得用弹窗/toast 打断，也不能被父容器 overflow 裁掉。编辑时清除错误；Escape 清空草稿和错误，不影响当前页。合法提交后清空草稿。
- 每页条数改为 10/20/50 中任意值，回第 1 页；切 Bot、日期、结论、问题类型或已提交的关键词也回第 1 页。单纯打开 Bot 选择器、输入 Bot 搜索词而未切换目标，不改变诊断当前页。切 Bot 保留已选 pageSize。
- 普通刷新/轮询保留当前页、pageSize、筛选和仍存在记录的展开状态；切页清理原页展开状态。焦点不自动跳到页面顶部；渲染后保持在操作控件或当前页按钮，跳页操作保持输入框焦点。

**异步状态与越界**：

| 状态 | 必须行为 |
|---|---|
| 初次加载 | 显示加载态，不把未知 total 写成 0；页码/跳页不可提交 |
| 切页请求中 | 禁用分页动作，避免重复提交；维持原有列表加载模式，不把待请求页码与旧页记录混显示 |
| 请求成功 | 页码、范围、total、items 来自同一次被接受的响应；显示成功页，不用待请求页冒充 |
| 查询失败 | 保留现有错误/重试样式；同查询的上次结果如保留必须说明未更新，不能显示成功跳转；重试保持目标页与筛选 |
| total=0 | “共 0 条”，页数“0 / 0 页”；内部查询仍用 page=1，绝不发送 page=0；禁用方向、页码跳转，保留每页条数选择 |
| 数据减少导致 page>totalPages>0 | 按响应给出的新末页自动补查一次，成功后说明“记录数量已变化，已返回最后一页”；补查不循环，若仍越界或失败，保留明确提示和“返回首页/重试”入口 |
| 权限过期/目标不可访问 | 沿用第 6 节错误与清理规则，不保留越权目标的旧记录 |

所有列表请求按 botRef + 完整筛选 + page/pageSize 区分，使用现有 AbortController/序号丢弃旧响应。Bot 或筛选变化后，旧分页响应不得覆盖新目标。total 变化时校验跳页草稿，但不因普通轮询强制清空用户正在输入的有效草稿。

**响应式与可访问性**：窄屏允许 footer 自然换行，不挤压记录列、不引入页面横向滚动；≤760px 可隐藏数字区，但保留首末页、上一/下一页、当前/总页数和直接跳转。按钮状态不只靠颜色，禁用使用真实 disabled；焦点轮廓、加载状态、计数更新和错误须可被辅助技术识别。不要为实现分页增加固定高度表体或自动无限下拉。

### 8.4 TC 故障标签与会话时间的安全间距

- TC 列额外右侧 margin 为 16px，加原 grid gap 后，实际列间距为桌面 32px、中屏 28px/24px；表头和记录共用列定义。
- 视口 >1150px：`minmax(0,1fr) 176px 170px 20px`；651–1150px：`minmax(0,1fr) 156px 140px 15px`。TC track 固定为 176px/156px，扣除 16px 留白后文本宽度仍为 160px/140px，不随页面拉宽；时间列保持原宽度，剩余空间归摘要列。
- `.summary-tc` 使用 `min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis`，单行显示，超长省略，不侵入时间列。采用上述紧凑列宽，避免短标签后仍有大块空白；记录最小高度和上下 padding 保持原值。
- 标签始终作为普通文本渲染，不解释为 HTML。保留已有 title 和展开详情完整字段，悬停或展开详情可查看全文，键盘及触屏用户可通过展开读取。
- 会话日期/时间保持原格式和展示，不挪入 TC 标签、不省略时间来腾空间、不增加详情列或记录摘要字段。
- ≤650px 沿用原移动布局，将 TC 与会话时间分行；不叠加桌面横向留白。长内容在自己的行内省略，不能覆盖下一行。
- 仅改列 CSS 并补测试，不重复开发 tooltip 组件。分页 CSS 不再覆盖记录列布局。
- 宿主正式顶部导航文件不改；效果中心侧栏 markup/视觉样式保持原样，仅保留已确认的普通用户监控入口可见性变更。本地 preview 使用原 OCB 导航快照及相同基础字体/Tailwind reset；测试身份切换放入独立的“本地预览”折叠控件，不放进正式导航。preview 不属于生产构建入口，也不代表已部署完整 OCB。

---

## 9. Avernet 文件级实施清单

以下均位于 `Avernet/src/evolverun/clawweb/public/modules/clawinsight/`，除明确写出的公共装配文件。文件名为已有路径；标注新增的为拟新增。

| 文件 | 修改职责 |
|---|---|
| `server/services/monitoring/schema.ts` | 新表本地测试 DDL、触发器名，保留生产不自动建表约束 |
| `server/repositories/monitoring-schema-check.ts` | 新表常量、列/约束/索引只读校验 |
| `server/repositories/monitoring-repository.ts` | 三字段读写、CAS、重复事件比较、批量摘要、旧兼容视图；保留诊断现有 COUNT/LIMIT/OFFSET、排序及计数一致性，不为 V7 另建分页实现 |
| `server/services/monitoring/contracts.ts` | 保留 v1 wire DTO/ACK，新增内部 target/store 类型和错误码 |
| `server/services/monitoring/validation.ts` | 保留旧协议验证；增加新 UI 查询参数及内部目标验证 |
| `server/services/monitoring/monitoring-service.ts` | 接收身份补齐、兼容 ACK、新查询/加入占位业务 |
| `server/services/monitoring/monitoring-runtime.ts` | 延迟装配目录/provider、可信范围和可选绑定 |
| `server/routes/monitoring.ts` | 新用户路由 ACL，旧读管理员边界，内部写边界和错误映射 |
| `server/routes/insight.ts` | 保持挂载兼容，按需传递已有公共依赖 |
| `server/services/monitoring/bot-directory.ts`（新增） | 有权限的分页搜索、批量和精确目录读取 |
| `server/services/monitoring/target-resolver.ts`（新增） | 精确匹配、可选受控别名绑定、稳定性/歧义处理 |
| `server/services/monitoring/target-ref.ts`（新增或复用） | botRef 与 cursor 编解码、上下文绑定；不新建存储 |
| `web/api/monitoring.ts` | 新目录/目标接口，保留错误区分 |
| `web/types/monitoring.ts` | botRef、接入状态、摘要和分页 DTO |
| `web/pages/InsightCenter/index.tsx` | 普通已认证用户可见监控入口，不放宽治理页面 |
| `web/pages/InsightCenter/monitoring/MonitoringPanel.tsx` | 三字段引用选择、请求/轮询、未接入空态和占位弹窗；替换底部分页区与页码算法，增加跳页草稿/校验、边界/异步/越界处理，保留其他布局 |
| `web/pages/InsightCenter/monitoring/DiagnosisRecord.tsx` | 复用现有 TC title、完整详情与展开交互，补回归；无实际缺口不修改源码 |
| `web/pages/InsightCenter/monitoring/MonitoringControls.tsx` | 搜索、范围、有限滚动、分页、键盘交互 |
| `web/pages/InsightCenter/monitoring/monitoring.css` | V6 统一卡片、状态位置、弹层与响应式；仅叠加 V7 footer 样式与 TC/时间列安全留白，样式限于监控模块 |
| `web/pages/InsightCenter/monitoring/__tests__/MonitoringPanel.test.tsx` | 首末页、三页数字窗口、跳页校验、pageSize/筛选重置、加载/失败/越界/旧响应回归 |
| `web/pages/InsightCenter/monitoring/__tests__/DiagnosisRecord.test.tsx`（按现有测试组织补充或新增） | 长标签、普通文本渲染、title 与详情全文；实际列间距另做浏览器检查 |

依赖适配放在 OCB 的既有启动入口和 Avernet 监控入口；不重做全局认证策略。遵守仓库架构约束：业务服务不依赖 Express 请求对象，HTTP 错误映射留在路由；配置读取和具体依赖选择留在 composition root，不在 repository 内直接读取环境变量。不得直接编辑 `dist/**`，由现有构建生成。`monitoring/local-host.ts`、`local-mock.ts` 和测试 fixtures 如受新内部类型影响，仅同步本地演示用途。

### 9.1 实施顺序

- A：只读预检表结构、当前报送 ID、目录、权限注入和目标环境，形成覆盖报告。
- B：新 schema/store + 三字段测试 + 接收端解析，旧 v1/ACK 回归。
- C：旧 discovery/详情兼容，新目录/授权/摘要 API。
- D：按 V7 基准接入前端与普通用户入口；在 V6 页面上局部更新分页和 TC/时间列，保留其他页面布局与权限。
- E：Avernet 检查、OCB 接线/认证回归、本地真实界面验收；部署前另做采集端黑盒验收。

---

## 10. 验收矩阵

### 10.1 数据与旧报送

| 用例 | 预期 |
|---|---|
| v1 普通唯一 Bot 上报 | 补齐目录 entity/env，写新表；原 ACK 不变 |
| 实例别名上报 | 仅经审核绑定时解析，库中是真实 ID，ACK/旧列表仍是原上报 ID |
| 裸 default 多候选且无证据 | 409，不插入、不更新、不任意选 owner |
| 不同实体同 default / 相同实体跨 env | repository 能独立保存和查询，UI 不合并 |
| 旧采集器不能表达上述多个目标 | 仅验证存储/API能力，不宣称旧发送端已能采集 |
| 不同目标同 event_id | 409，不覆盖、不 duplicate |
| 同一事件重试/并发 | 只有一个事件，201 首次、200 重复 |
| checks 旧时间/同时间冲突/并发 | 单目标 CAS 正确，不影响另一 entity/env |
| 同目标不同 engine | 明确配置冲突，不覆盖原检查状态 |
| 目录不可用/新表不存在/权限不足 | 503，不 fallback 旧表、不假报成功 |
| 目录归属变化、解析绑定变更、pending 重试 | 不自动重分配历史目标，需核对和受控切换 |
| 旧发现 after_reporting | 所有既有目标存在，ID 不重复、别名不丢失 |
| 实际更新中的发送端版本 | 原请求样例、ACK/discovery 黑盒兼容，不要求改代码 |

### 10.2 权限与查询

- 普通用户只能见 owner_id 与可信工号相同的有效目录；不能看协作者名下或他人 Bot。
- 未登录 401，普通用户 all/monitored 403，猜测/修改 botRef 无越权。
- 管理员 scope 正确，不跨可信 tenant/env；旧列表仍非普通用户入口。
- botRef、cursor、缓存跨账号无数据泄漏；伪造客户端管理员标志无效。
- checks 有/diagnoses 无为有效 0；checks 无/diagnoses 有为未监控。
- 目录删除、转移 owner 后重算权限，历史数据不继续给旧 owner。
- 名称/ID模糊搜索、工号前导零、中文、LIKE 转义；身份精确比较不被搜索规则影响。
- 分页稳定，monitored 过滤不漏项、不伪装末页；大目录不全量载入，不 N+1。
- 同一 botId/entity 跨环境有可辨识候选，统计隔离。
- UTC窗口边界、NULL时间、会话缺失及会话标识类型碰撞符合口径。

### 10.3 前端和占位功能

- 两种角色搜索提示、默认 mine、范围按钮、有限高度和追加分页正确。
- 已监控/暂停/未监控样式统一，默认头像始终存在，ERROR/UNKNOWN 不宣称健康。
- 搜索候选没有加入按钮；主区点击只有确认文案，不改变选择或监控数据。
- 已接入竞争、网络失败、登录过期正确区分，不全部显示“功能待开发”。
- 防抖、IME、旧响应丢弃、轮询取消、键盘、焦点、窄屏通过交互测试。

### 10.4 V7 分页与列间距专项验收

| 用例 | 预期 |
|---|---|
| 总页数 0、1、5、7、8，以及 13 页的前/中/末位置 | 控件及最多三个数字页码符合第 8.3 节，绝不限制只能到第 5 页；0 条不发 page=0 |
| 首页/末页、上一/下一、数字按钮 | 请求正确页码；前/中/末位置数字窗口正确且不越界；当前页点击不重复请求 |
| 246 条、20 条/页 | 13 页；末页 6 条、范围 241–246；前端只持有服务端返回页，不下载全部记录 |
| 跳页 Enter/按钮与 Escape | 两种提交方式一致；Escape 只清草稿/提示；合法页请求及焦点正确 |
| 空、0、−1、1.5、1e1、2abc、超 totalPages/API 上限 | 不提交、原页不变，有关联输入框的可访问错误提示；服务端独立拒绝非法参数 |
| pageSize、Bot、日期、结论、问题类型、关键词变化 | 新查询从第 1 页开始，pageSize按规则保留；仅 Bot 候选搜索不重置诊断页 |
| 轮询、切页失败/重试、慢响应与快速切目标 | 成功页/计数/内容一致；轮询不抢草稿/焦点，不串目标，不假报跳转成功 |
| 页数缩减与 total 归零 | 最多一次自动末页补查；持续变化不无限循环，0 条状态正确 |
| 同时间多条记录、NULL 时间、跨 entity/env | 保持稳定排序、原时间口径、ACL 与三字段隔离；counts不按当前页计算 |
| 长英文标签、无空格长串、中文/混合文本、空标签 | 不与时间重叠；title/展开详情可读全文，缺失用原“—”，按文本安全渲染 |
| 1440、1150、900、760、650、390px 视口，200% 缩放 | TC/时间表头与行对齐、分行断点正确；footer换行无裁切/横向溢出，键盘可操作 |
| 普通用户/管理员两份视觉对比 | 除 footer 与 TC/时间列必需调整外，导航、标题、上下文、选择器、筛选、行高、详情、加入弹窗不变 |

JS/DOM 测试不能证明真实浏览器布局；长标签不重叠、断点和缩放必须以真实渲染几何/截图复核。至少使用一条超长标签和多页数据做**测试专用**验证，不改变正式 Demo 的原始数据量。

### 10.5 测试位置与命令

复用/更新已有：

```text
server/services/monitoring/__tests__/repository.test.ts
server/services/monitoring/__tests__/contracts.test.ts
server/routes/__tests__/monitoring.test.ts
web/api/__tests__/monitoring.test.ts
web/pages/InsightCenter/monitoring/__tests__/MonitoringPanel.test.tsx
```

新增目标解析、目录分页、ACL、cursor 和 legacy compatibility 测试；使用固定 v1 请求/ACK 样例，不修改 claw-validation 仓库。OceanBase schema 校验/查询需在授权测试环境验证，SQLite 通过不能代替生产方言验证。

已有 package scripts 可在依赖装配完成后运行：

```sh
cd Avernet/src/evolverun/clawweb/public/modules/clawinsight
npm run check
npm run test:monitoring
npm test
npm run build
```

本轮只更新文档，未运行上述业务构建/测试，也未声明生产用例已通过。

已交付 V7 HTML 的原型检查记录（上一轮完成）：`interaction.test.cjs` 76 项与 `pagination.test.cjs` 20 项 JSDOM 检查通过；结构对比确认 body 除 footer 外未改、CSS 为局部追加、演示数据不变。原型分页是本地数据逻辑，不覆盖真实 API、并发、鉴权和上述完整上线验收。浏览器本地文件预览受限，V7 尚未完成真实浏览器截图/几何验收，不能用 JSDOM 通过代替该门槛。

---

## 11. 新表切换、数据保留与回滚

### 11.1 切换前门槛（全部满足）

1. 用户已建新表；只读核对实际表名、列、长度、索引顺序及服务账号 SELECT/INSERT/UPDATE 权限。旧表不执行 ALTER/DROP。
2. 完整列出当前监控的报送 ID（checks 与诊断），确认每项原值与实际协议版本；不要只抽查一个唯一 Bot。
3. 每项都能在正确 tenant/目标环境范围唯一解析，或有独占且已审核的绑定。不能以当前 checks 只有一行来证明 default 属于谁。
4. OCB 最小接线通过既有登录服务、角色名单及 db 提供身份、目录、环境，不需要用户另行提供 Provider。
5. 旧 discovery 的全部目标仍可表达；现有发送者 event_id 不跨新目标冲突；解析规则版本在各实例一致。
6. 确认历史数据保留方式及切换窗口，避免旧 pending 数据继续只写旧表。

### 11.2 历史数据策略

默认不自动迁移：旧表只读保留，新表从新成功报送开始累计。新表初始为空意味着旧已监控 Bot 在新 UI 暂时显示未监控；应先等待有效 checks 覆盖现有监控目标，再开放新页面。

如业务要求连续历史，另行准备经批准的人工迁移清单/脚本：只迁移归属有证据的记录，补齐三字段并保留原 event_id；不能仅凭“今天目录只剩一个目标”断言旧历史全部属于它。歧义旧行留在旧表，不复制给多个用户。不把创建伪造 checks 当作迁移办法。

### 11.3 切换顺序

1. 不改线上读写，完成只读预检及测试环境回归。
2. 安排维护窗口或部署侧流量排空，暂停现有报送流量/进程并保留其本地队列，不修改发送端代码。
3. 确认没有旧 Avernet 实例继续接收写入，避免滚动期间新旧表分流。
4. OCB 同步新 Avernet 构建和最小接线，启用新表读写和旧协议兼容路径；如需批准的历史迁移，在协调停写窗口完成。
5. 恢复原发送端，观察真实成功 ACK、目标解析覆盖、新 checks 行、diagnosis 去重及旧 discovery。
6. 新界面可见性灰度开关控制普通用户开放；当前监控目标 checks 覆盖达标后开放。保持服务端 ACL 不依赖此开关。
7. 确认旧表停止写入并只读保留，不删除。

### 11.4 回滚

优先关闭新 UI 开关但保留新表后端，避免因页面问题丢失新采集数据。

如果必须整体回退旧 Avernet：先停报送、排空新实例，备份两套表和队列状态，确认旧库仅能表达旧裸 ID 范围后再回退。**已经获得 ACK 的新表事件不会自动被采集端重发；旧页面不会自动看见这些记录。** 需要人工数据补偿或接受明确的数据时间缺口，不能承诺无损自动回滚。

不得把多个新目标合并写回旧 checks。不能把新表改成旧名字假装兼容。旧表长期删除/归档不在本期执行范围。

### 11.5 可观测性

记录：身份无匹配/歧义/绑定冲突、目录失败、schema失败、旧协议成功/重复/冲突、旧发现 missing、目标 checks 覆盖、查询延迟和批量扫描预算耗尽。

日志只保留错误码、requestId、受控目标摘要/哈希和解析配置版本，不记录原始会话、诊断正文、认证信息或完整用户搜索词。永久错误和 blocked 数据要有人工排查入口说明，不增加新管理平台。

---

## 12. 完成定义与版本差异

只有同时满足以下条件，才可宣告功能实施完成：

- 公共实现位于 Avernet，OCB 仅复用既有能力接线，claw-validation 无修改。
- 现有发送端协议、ACK、事件 ID 和发现行为经实际版本兼容验收。
- 两张新表是唯一监控数据源，已停止旧表写入；不会自动改库或跨表 fallback。
- 已有监控目标全部可正确解析；不把无法解析的既有目标静默丢弃。
- 三字段身份用于内部存储、查询、统计、前端引用；同名 default 不串数据。
- 普通用户所有入口均受服务端 owner 权限保护，管理员范围和环境隔离正确。
- UI 达到 V7（V6 已确认交互 + 两处局部改动），未监控 Bot 可见但不伪造健康/计数，加入按钮只返回固定提示。
- 诊断分页覆盖全量匹配页、首末页/跳页/pageSize/边界和异步失败，不用目录 cursor 替代诊断页码；不因本次分页增加表结构或索引改造。
- TC 标签与会话时间额外留白及长文本处理经真实浏览器验收；除已批准局部区域外不改变页面。
- 历史可见性、切换维护窗口和回滚数据缺口已经明确。

### v1.0 → v2.0 决策替换

| 旧版要求 | 本版最终要求 |
|---|---|
| Avernet + claw-validation 跨仓改造 | 不改 claw-validation；v2.2 允许 OCB 最小接线 |
| v2 报送、entityId ACK、新事件哈希 | 保持 v1 wire/ACK/eventId，Avernet 内部补齐 |
| 改旧表列与 UNIQUE | 使用用户已建的新单数表，旧表停用保留 |
| 两表新增 tenant/env/entity | 新表使用 bot_id/entity_id/env；tenant 由可信存储隔离提供 |
| 修改采集端 SQLite 队列、配置、discovery | 不修改，明确旧发送端能力限制 |
| 新旧协议双写/升级 | 单一 v1 接收，新表单写，旧查询兼容投影 |
| 默认要求真实头像链路 | 本期统一默认头像，不扩大接入范围 |

### v2.0 → v2.1 Final 增量

| v2.0 | v2.1 Final |
|---|---|
| V6 Bot 选择界面基准 | 同路径 V7：原页面仅调整分页与 TC/时间列间距 |
| 诊断分页仅沿用既有能力，未明确交互细节 | 明确首末页、动态页码/省略号、直接跳页、每页条数、加载/失败/越界与响应式 |
| 未明确长标签与时间列安全间距 | 额外 24px、同列对齐、长文本省略/全文读取、移动分行验收 |
| 前端文件清单与一般交互验收 | 补充分页/标签的实际修改点、专项测试与禁止整页重设计边界 |
| Avernet-only、新表及 v1 兼容方案 | 不新增 DDL、不改 claw-validation、不改统计/身份决策；v2.2 调整 OCB 接线范围 |

本 Spec 是后续研发依据；与之前跨仓版本及 v2.0 不一致时，以本版为准。尚待部署核实的事项是表名/实表结构、当前发送端清单及归属、环境/租户隔离和宿主注入，不阻止在本地复用代码实现和测试界面。


### v2.2：既有能力接线与真实本地验收

- OCB：bootstrap/index.ts 接入小型 monitoring-runtime.ts；archive/auth.ts 导出已验证登录查询。此前工作区已有的 access-admin / admin-auth 改动保留。
- Avernet：引用/游标仅用于定位，每次访问重新授权；旧管理员读接口也不能凭 request 标记放行。不新增密钥或权限表。
- clawinsight/preview 使用实际 InsightCenter + Express 路由 + SQL；固定身份仅在本地入口使用，生产不导入。
- 命令、测试结果和剩余生产验证见 [实施记录](./clawinsight-monitoring-implementation.md)。

### v2.3：宿主外观与长标签修正（2026-09-21）

- 替代 v2.1/V7 的“额外 24px + 单行省略”规则，以第 8.4 节为准：额外 32px、弹性宽列、完整换行。
- 明确正式顶部/侧栏外观不变，修正此前本地简化顶栏及缺失宿主基础样式造成的预览偏差。
- 补充完整标签渲染/CSS 契约回归；真实浏览器验证桌面、中屏、手机布局，不把 JSDOM 作为几何验收。

### v2.4：按反馈收紧 TC 间距（2026-09-21）

- 用户确认允许超长标签截断；覆盖 v2.3 的完整换行要求，恢复单行省略及 title/详情全文读取。
- 仅把额外留白从 32px 减至 12px（桌面总间距从 48px 减至 28px），不改导航、侧栏、分页或其他元素。

### v2.5：进一步收紧 TC 列

- TC 列改为桌面 160px、中屏 140px，取消额外 margin；单行省略及全文读取不变。导航、侧栏和分页不变。

### v2.6：分页数字区精简

- 按用户反馈，将最多七个数字/省略号按钮改为最多三个相邻数字按钮，删除重复首末页数字与省略号快跳。
- 首页/末页、前后翻页、每页条数、当前/总页数和直接跳页保留；其他页面区域不变。

### v2.7：TC 间距适中微调

- 当前紧凑列基础上增加 16px 留白，同时 track 增加 16px，不减少标签文本宽度；桌面实际间距为 32px（本地复核后由 24px 调整）。
- 保留单行省略和最多三个数字页码，其他区域不变。
