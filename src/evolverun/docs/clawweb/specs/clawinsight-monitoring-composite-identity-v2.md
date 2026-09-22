# ClawInsight monitoring：显式三元组 v2 接口升级

日期：2026-09-22。状态：本地实现及测试通过，尚未部署或生产联调。
需求来源：`claw-validation-dev_murao/docs/specs/monitoring-composite-identity-avernet-20260922.md`。
基线：Avernet `origin/dev`，`08bb8f79cf55062f82f113641690012d199d0df9`。

本说明替代 `clawinsight-monitoring-bot-search.md` 中 v1 写入、写入别名和旧发现响应的规定，不改变其 UI/权限范围。

## 1. 关键实施差异：沿用现有三元组存储，不迁移为目录主键

需求 spec 假定已有监控表通过 `ac_bots.id` 关联。同步后的代码实际使用：

- `insight_monitoring_diagnose`：全局唯一 `event_id`，三元组归属及索引；
- `insight_monitoring_bot_check`：`(bot_id, entity_id, env)` 唯一键；
- 诊断、状态 CAS、计数、摘要及缓存键均使用完整三元组；
- 页面使用编码了三元组和可信 tenant 的既有 `botRef`，不是裸 botId。

本次先精确确认唯一目录记录，再将**已验证且与请求相等的完整三元组**传入现有 store。
没有新增 `ac_bots.id` 外键、迁移历史表或重写历史诊断。因此实现了同名 Bot 隔离与无单字段回查，
但**没有按需求 spec 字面要求将存储改为内部主键关联**；合并/发布评审应确认这一基于实际基线的最小实现差异。
若必须切换主键，需要独立 schema/历史数据迁移方案，不属于本次无迁移升级。

监控表本身不含 tenant。继续要求宿主提供 `isolatedStorageTenant === scope.tenant`，且真实存储按 tenant 隔离。
不能把此配置声明当作数据库隔离的替代；共享多租户存储且三元组可重复仍是发布阻断项。

## 2. 当前请求、ACK 与错误契约

URL 不变：

```text
POST /api/insight/v1/internal/monitoring/diagnosis-events
POST /api/insight/v1/internal/monitoring/bot-checks
```

仅接受 `claw-monitoring/diagnosis-event/v2`、`claw-monitoring/bot-check/v2`。
两个 DTO 均必填真实 `botId`、字符串 `entityId`、字符串 `env`；其余业务字段/时间校验沿用原规则。
缺身份、首尾空白、空字符串、控制字符、不成对 UTF-16 surrogate、超长身份和旧版本均拒绝；不 trim、不转数字、不转大小写、不补默认环境。
旧 v1 即使带齐三元组也不被接受，不存在 v1 写入兼容分支。

成功响应：

```typescript
// HTTP 200；较旧检查或完全一致重试 applied=false
{ accepted: true, applied: boolean, botId: string, entityId: string, env: string }
// 新写入 HTTP 201 / duplicate=false；相同重试 HTTP 200 / duplicate=true
{ accepted: true, stored: true, duplicate: boolean, eventId: string,
  botId: string, entityId: string, env: string }
```

ACK 必须在现有 durable adapter 完成持久化后返回。`Idempotency-Key = eventId = diagnosisId` 保持不变；
eventId 不重算。规范化后的业务内容及三元组均参与重复比较；同 eventId 异内容/身份不覆盖。
状态 CAS、旧检查不覆盖新检查、同时间异内容冲突仍按完整三元组执行。

写入错误体只保留现有 envelope 内的安全 code 和 requestId：

```json
{"error":{"code":"INVALID_IDENTITY","requestId":"<generated-id>"}}
```

| HTTP | code | 含义 |
|---|---|---|
| 400 | `INVALID_IDENTITY` | 身份/版本无效 |
| 403 | `BOT_IDENTITY_UNAVAILABLE` | 无匹配、非当前租户、删除或目标环境不可访问（不可区分） |
| 409 | `BOT_IDENTITY_AMBIGUOUS` | 授权范围内精确三元组有多条 |
| 409 | `MONITORING_EVENT_CONFLICT` | 同 eventId 异内容或身份 |
| 409 | `MONITORING_CHECK_CONFLICT` | 同目标同检查时间异内容，或已存状态引擎冲突 |
| 400 | `MONITORING_INVALID_EVENT` | 原业务校验失败，或与唯一目录记录的引擎不一致 |
| 413 | `MONITORING_PAYLOAD_TOO_LARGE` | 原负载/业务字段大小限制 |
| 503 | `MONITORING_NOT_READY` | 目录、配置或持久化不可用 |

保留读接口原有错误消息供 UI 使用；内部写入错误不输出诊断正文、SQL 或异常细节。

## 3. 唯一查找与清单

两个写入共用 `createTargetResolver().resolveReport()`：

1. 校验 v2 DTO，再调用目录 `get()`；不调用裸 botId 的 `exact()`，不读取别名映射。
2. `bot_id/entity_id/env` 均参数化等值查询，并应用可信 tenant（部署配置含该列时）、allowedTargetEnvs、`is_delete = 0`。
3. SQLite 使用 `COLLATE BINARY`；MySQL/ZDAS 使用 `BINARY column`；保留大小写和前导零。
4. 有界读取两行（`LIMIT 2`）用于证明歧义，不使用 `LIMIT 1` 默选。
5. 唯一匹配后验证 `OC -> active_engine=openclaw`、`TE -> active_engine=teclaw`；engine 不参与查询、不消除歧义。
6. Store 再验证已解析 target 与 wire 身份一致，不能通过内部参数替换归属。

`GET /api/insight/v1/monitoring/bots` 保留现有管理员权限，响应改为
`{items:[{botId,entityId,env}]}`。按三元组列出已有诊断或状态的目标，并重新检查目录授权/有效性；
删除和不可访问目标不展示，目录不可用不能伪装为空清单。两个 default 可同时返回，不按 botId 去重、不转换为旧别名。

页面已有 `bot-options` / `targets/:botRef` 查询、权限检查和路由不变，不新建 monitorKey 或注册服务。
旧的 `bots/:botId` **只读详情**仍保留已有无歧义解析及旧别名能力；歧义会拒绝，不能用于访问两个 default 的合并视图。
这里保留的只读逻辑不参与任何 v2 报送。

## 4. 通过仓库核对的数据库事实

证据路径（均相对 Avernet 仓库根目录）：

- `src/backend/src/agentclaw/community/plugin_api/models.py`：`BotModel`。
- `src/backend/src/agentclaw/community/utils/env_utils.py`：`get_current_env()`。
- `src/backend/src/agentclaw/community/core/workspace/constants.py`：引擎类型。
- `src/evolverun/clawweb/public/modules/clawinsight/server/services/monitoring/schema.ts`：监控测试建表契约。
- 同模块 `server/repositories/monitoring-schema-check.ts`：部署 schema 的只读检查。

| 字段/规则 | 代码事实与本次边界 |
|---|---|
| `ac_bots.id` | MySQL BIGINT / SQLite INTEGER；目录以字符串 CAST 读取，避免 JavaScript 安全整数截断 |
| `ac_bots.bot_id` | `String(64)`；报送沿用原 1–128 位 ASCII ID 格式校验，必须实际匹配目录 |
| `ac_bots.entity_id` | `String(1024)`，不是数字；JSON 原字符串传输，`001` 不等于 `1` |
| 监控表 `entity_id` | 现有 `VARCHAR(128)`、二进制比较；本次报送上限 128 Unicode codepoints，不截断。超出监控容量的目录目标不能上报，需要另行评估迁移 |
| `env` | 两侧长度 20；数据库字段不是 ENUM。后端创建默认环境经 `get_current_env()` 得到 dev/pre/prod（gray→prod，prepub→pre） |
| 报送 env | 不调用后端环境归一化函数。必须原值精确匹配目录且在宿主可信 allowedTargetEnvs 内；测试使用 test/local 仅是合成环境，不证明生产存在这些值 |
| 唯一约束 | ORM 定义 `uk_bot_id_entity_id_env_tenant(bot_id,entity_id,env,avernet_tenant)`；不含 is_delete，软删除仍占用该元组 |
| 生产约束 | 源码注释提及 `uk_bot_id_entity_id_env`，并明确生产 GLOBAL/tenant 范围尚不确定；本次未查询生产元数据，不能据此确认实际部署约束 |

不执行 DDL 或新增约束。若实际环境没有唯一约束，查询遇多条一律拒绝；重复审计和加约束需要单独批准。

## 5. 本地验收与边界

运行目录：`src/evolverun/clawweb`。
本地使用 Node **20.19.0**、`better-sqlite3` **11.10.0**（SQLite **3.49.2**）的实际 shared adapter。
默认 Node 26 环境缺原生模块；复用了本机已有、同包版本且经加载确认的 Node 20 原生模块，未改变依赖清单/锁文件。
没有用 `node:sqlite` supplemental driver 的结果代替正式驱动验收。

```sh
npm run check -w @avernet/clawinsight
npm run test:monitoring -w @avernet/clawinsight
npm run test -w @avernet/clawinsight
npm run check:monitoring-preview -w @avernet/clawinsight
npm run test:monitoring-preview -w @avernet/clawinsight
npm run build -w @avernet/clawinsight
```

本地结果：

| 检查 | 结果 |
|---|---|
| 模块 TypeScript check | 通过 |
| 监控专项 | 6 文件 / 159 项通过（含新增 28 项） |
| ClawInsight 全量 | 57 文件 / 801 项通过 |
| Preview TypeScript check | 通过 |
| Preview HTTP | 1 文件 / 4 项通过 |
| 模块 build | 通过 |
| `git diff --check`、修改源文件 1000 行限制 | 通过 |

测试日志仍有 shared 模块已有 sourcemap 缺源文件提示及部分治理页面 React `act(...)` 提示，未导致失败；本次未修改这些无关路径。

新增 `server/services/monitoring/__tests__/composite-report.test.ts` 使用真实 SQLite 目录、持久化 adapter 和两个原 HTTP URL，覆盖：
两个 default / 多环境隔离、ACK 回显、eventId 重试/内容及身份冲突、检查 CAS、身份格式、v1 拒绝、租户/环境/删除权限、
多匹配、引擎后验证、参数化/大小写/前导零、清单不合并及重授权、持久化前不得 ACK、落库失败、历史只读和防目标替换。
已有业务校验、页面、诊断分页、摘要、目录 ACL 回归一并执行。

历史 v1 诊断仅在数据库读模型中投影以维持 UI 可读性，不写回、不升级历史 payload；
v2 与已存 v1 使用同一 eventId 会冲突，不自动覆盖。检查表原本不存 schemaVersion，时间/CAS 规则不变。

本地 SQLite 不能替代 MySQL/OceanBase/ZDAS 授权测试环境的执行验收；本次不访问生产、未核对 25 项真实映射、未执行 DDL、历史队列修复或自愈写操作。

## 6. 协调发布前必须确认

1. 停止发送端 writer；对账所有旧 v1 PENDING/IN_FLIGHT，明确人工处理方式，不能恢复后直接发送给 v2-only 接口。
2. 接收端和 CV 协调切换；确保 host 仍提供正确目录、tenant 隔离和目标环境范围，schema 与接口版本一致。
3. 用户手工维护真实三元组；若有 entityId 超过现有监控容量，先决定独立容量变更，不静默裁剪。
4. 发布评审确认第 1 节“保留实际三元组存储而非新增内部主键关联”的实现差异。
5. 四条旧报告修复必须单独批准，执行前再查存在性；不得替换 eventId、覆盖已存事件或自动重放队列。
6. 回滚需同步停止 writer 并回滚双方协议版本；不是滚动混跑 v1/v2。仅回滚接收端而继续发送 v2 会被拒绝。
