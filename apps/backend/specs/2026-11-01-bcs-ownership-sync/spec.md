# Backend → BCS Bot Owner best-effort 同步

- 日期：2026-11-01。
- 状态：生产实现已完成，Backend CI 通过，Owner 功能已在受控本地真实 BCS 栈验证；不是全产品统一门禁通过声明。
- 实施证据：[validation.md](validation.md)，Tasks 1–10 已分别通过 spec/quality 审阅；生产实现至 `eaaa069f53`，验收至 `a30e750462` / `ee2c738deb`；Task 11 已补齐覆盖登记并提交 `066df82529`，双阶段及最终独立代码审阅通过；最终默认全模块门禁仍未全绿，见验证记录。
- 验证边界：Core 初始 runtime 非回归阈值 65.90%（完整 87/132 分母），Owner Plugin replace/aclose 实测 2/2、门槛 100%（SIGTERM coverage 修正后真实 shutdown 已覆盖）；新 focused runner 通过但独立 artifact verifier 因 BaaS 44.50<45 未通过；既有 manager 54.03% 不变。统一门禁的 BaaS 端口耗尽、覆盖 baseline 和 BCS 间歇 group story 失败须按 validation.md 的实际结果判断，不以 focused 模块通过替代。真实外部 AAC/设备、企业在职身份与生产凭证安全性仍未验证。
- 所属模块：Backend。BCS 是现有版本化接口的提供方，本次不修改其接口语义。
- 基线分支：`feat/bot-ownership-sync`。
- 基线提交：`2c12ae7900577df1f1b558d17162c9e63582d685`。
- 文档工作分支：`feat/ownership-sync-backend`，从上述基线继续。
- 参考：`inclusionAI/Avernet` PR #3208（Backend managers 同步）和 #3290（BCS trusted ownership sync）。
- 用户确认：异步 best-effort；不新增持久化补偿任务；不改变已完成的 Backend 业务结果。

## 1. 背景与目标

Backend 的 Owner handoff 在凭证换代、运行时更新、旧凭证退役后提交新的 Bot Owner。
现有 post-commit 通知只刷新 BCS managers，不能改变 BCS Owner。
因此 Backend 已成功完成责任转交，不代表 BCS 的 Owner 授权已随之更新。

本设计在 Backend Owner 变更的完整业务流程成功后，异步调用：

```http
PUT /api/v1/collaboration/bots/{bot_id}/ownership
X-Avernet-Principal: <Backend service principal>
Content-Type: application/json

{"owner_user_id": "<current Backend owner_id>"}
```

目标是以 Backend 当前已提交的 Owner 事实刷新 BCS；不是把 BCS 纳入 Backend 事务，
也不是把 BCS 的人工转交协议搬到 Backend。同步结果不参与 handoff 的 `SUCCEEDED` 判定。

### 1.1 来源与约束

- [架构宪法](../../../../docs/arch/arch.rules.md)。
- [CI 约束](../../../../docs/arch/ci.enforce.md)。
- [Context Boundary 格式](../../../../docs/arch/context-boundary-format.md)。
- [Plugin 消费者契约测试](../../../../docs/arch/protocol-contract-tests.md)。
- [领域文档入口](../../../../CONTEXT-MAP.md)及[既有 ADR](../../../../docs/adr/)。
  本次未发现直接规定 Owner 投递可靠性的已接受 ADR；不修改已有 ADR，
  不引入新的跨系统权威划分决策。
- [现有 managers 同步规范](../2026-10-27-bcs-manager-sync/spec.md)。
- [Owner handoff 合约](../../src/agentclaw/community/core/bot_management/owner_handoff/README.md)。
- [BCS Bot Authority OpenAPI](../../../bcs/api-contracts/v1/openapi/bot-authority.yaml)。

本设计只扩展 Backend 的出站消费能力。BCS 仍负责执行自身授权、映射、事务和审计规则。
与旧 managers spec 中“handoff 不调用 BCS Owner 同步”的差异在实现时显式更新；
managers 单独同步不改变 Owner 的原有语义继续成立。

## 2. 已确认的范围与非目标

### 2.1 范围

1. Owner 变更成功后的统一非阻塞通知入口。
2. 独立的 Owner 事实读取、同步编排和 BCS 出站 Plugin API。
3. 复用现有有界后台执行设施，显式区分 Owner 与 managers 工作。
4. 有限重试、诊断，以及单 Bot / 分页人工重新提交。
5. Owner 同步确认成功后，追加一次读取最新事实的 managers 刷新。
6. 默认关闭的 Owner 开关、配置校验、契约测试和真实单盒读回验收。

### 2.2 非目标

- 不新增数据库表/列、迁移、outbox、`Bot.ext` 同步状态或 `ac_task_queue` 补偿任务。
  原有 handoff 自身的持久化任务与恢复流程保持不变。
- 不新增周期性补偿、启动全量扫描、Redis 锁、分布式版本屏障或跨副本投递协调。
- 不保证持久投递、最终一致性、跨副本有序、远端 exactly-once 或 Owner/managers 原子更新。
- 不修改凭证签发、退役、运行时更新、Space 转移及 handoff 成功条件。
- 不自动注册 BCS Bot/Human，不初始化 version-0 ownership，不调用转交申请/接受接口。
- 不新增 Owner 修改业务入口、不捕获任意直接 SQL 更新、不为未来入口预造业务流程。
- 不新增公开 HTTP 管理接口，不调整前端，不扩展 Skills 或其他实体的 Owner 同步。

## 3. 现状审计与业务完成边界

### 3.1 当前正式写入路径

基线扫描定位到既有 Bot Owner 替换的正式生产写入点：

- `core/repository/implementations/bot/owner_handoff.py` 的 `finish()`：同一事务修改
  `Bot.owner_id`、Owner 名称、Space、相关凭证引用、旧 Owner 协作者记录以及 journal 终态。
- `core/bot_management/owner_handoff/service.py` 的 `step()`：
  在 `COMMITTING_OWNER` 分支调用 `finish()`，返回后已有 managers 通知。
- `core/repository/implementations/bot/mutations.py` 的通用更新白名单不含 `owner_id`；
  普通 Bot 编辑不是 Owner 变更入口。
- `core/repository/implementations/bot/bot.py` 的新建模型写入 `owner_id` 是初始归属，
  不是已存在 Bot 的责任转交。

审计针对本基线的公开 Backend 代码，不声称覆盖外部企业扩展或仓库外脚本。
实现时复核变更基线内的 ORM 赋值、批量 update、SQL 和调用链，记录新增的实际写入入口；
只有真实存在且会改变既有 Bot Owner 的入口才接入，不把名称含 owner 的操作都当作转交。

### 3.2 触发矩阵

| 场景 | Owner 通知规则 |
| --- | --- |
| 正常 handoff | `finish()` 成功返回、数据库事务提交后通知 |
| 可恢复 handoff 的后续执行 | 最终到达同一个成功提交点时通知，不因重试入口本身通知 |
| 发起、等待授权、更新运行时、退役旧凭证 | 不通知；流程尚未完整完成 |
| `finish()` 失败、事务回滚、取消 | 不通知 |
| 已 `SUCCEEDED` 的状态查询、幂等 create/retry 或任务终态检查 | 不新增通知；这些不是新的完成事件 |
| 其他正式 Owner 替换入口 | 在其完整业务流程成功且事实提交后接入相同通知入口 |
| 仅编辑 Owner 显示名、普通 Bot 编辑、Space 移动 | 不触发 Owner 同步；保留原有 managers 行为 |
| 新建/代建/克隆 Bot 时赋初始 Owner | 不纳入本次；本接口不替代 BCS 初始化 |
| 直接 SQL 修正、历史成功操作的投递丢失 | 不自动捕获；通过显式人工重新提交当前事实处理 |

通知不能下沉为 ORM 的通用写后钩子：一次字段落库未必代表完整业务流程结束。
数据库、journal 或业务持久化失败必须继续报错；只隔离 post-commit 通知异常。

## 4. 方案选择

| 方案 | 结果 |
| --- | --- |
| **独立 Owner 契约，复用现有有界后台设施** | 采用。明确权威写入范围，避免重复线程/队列/关闭逻辑 |
| 独立复制一套 Owner 执行器 | 不采用。重试与生命周期重复，资源上界翻倍且更难维护 |
| 每次 managers 同步前一律覆盖 Owner | 不采用。协作者或 Space 通知不应隐式升级成 Owner 权威写入 |

持久化补偿和将 BCS 确认纳入 handoff 成功条件均已由用户排除。
共享执行器只需支持这两种明确工作，不建设通用任务框架。

## 5. 边界、契约与责任

本节新增的 Backend Service API、Plugin API 和数据契约均定义为 v1；
BCS 消费接口继续使用现有 `/api/v1`。内部名称可按本地风格落地，行为与必填性不得改变。

### 5.1 Service API：业务向 Core 提交通知

新增 `BotOwnershipSyncProtocol`，提供 `request_ownership_sync`：

- 必填参数：`bot_pk: int`、`env: str`、`avernet_tenant: str`、有限枚举 `reason`。
- 返回复用的 `SubmissionResult`：`QUEUED / COALESCED / DISABLED / REJECTED`。
- 方法只做有界内存准入，不查数据库、不签名、不等待 HTTP。
- `reason` 只用于诊断；工作类型由方法明确指定，不从 `OWNER_HANDOFF` 等 reason 推断。
- 业务使用统一的 transport-free post-commit helper 隔离通知异常。
- 准入成功不代表远端成功；禁用和队列满不改变业务返回值。

现有 `BotManagerSyncProtocol` 的方法签名和 managers-only 语义保持不变。
两个契约在 composition root 绑定到同一个共享调度设施；不让业务依赖具体 dispatcher。

### 5.2 Core 与事实读取

Owner 编排服务只负责读取当前事实、生成快照、调用 Plugin、有限重试和分类结果。
新增独立的 Owner read contract 与实现，按 `tenant + env + bot_pk` 读取未删除 Bot。

Owner 快照仅包含 key、稳定 BCS Bot ID 和必填 `owner_user_id`：

- BCS Bot ID 为 `Backend bot_id + ':' + entity_id`，`entity_id` 是稳定资源身份。
- 不得用当前或历史 Owner 代替缺失的 `entity_id`。
- Owner 值使用当前 `Bot.owner_id`，不是操作发起人、执行身份、旧 Owner 或 `entity_id`。
- 拒绝缺失、空白或非法身份；不默认到调用人，不发空值“清空 Owner”。
- 每次重试重新查询，通知中不携带可过期的 Owner 快照。
- key 不匹配、Bot 已删除、身份缺失或数据库读失败时不发送。
- 不查询协作者名单或 Space 资格，不受 1,000 managers 上限影响。

现有 managers reader 不承担 Owner 的前置校验。人工分页使用独立 Owner 范围查询，
不要求 Bot 存在协作者，不依赖 managers 事实读取成功。

### 5.3 Plugin API：Core 向 BCS 投递

新增 transport-free `BcsOwnershipPlugin`：

- `replace_ownership(snapshot, *, timeout_seconds)`：单次发送，无隐式重试。
- 返回显式结果：`bot_id`、`owner_user_id`、`previous_owner_user_id`、`changed`、`request_id`。
- 结果中的两个 Owner 字段允许 `None`，仅因为 BCS 外部响应契约确实允许该值；
  请求快照的 `owner_user_id` 必须始终是非空字符串。
- 使用 `NOT_SENT / UNKNOWN / REJECTED` 分类的领域异常，不外泄 HTTP 对象或原始响应。
- `aclose()` 在所属后台 loop 释放自有连接；注入的外部 client 不由 Plugin 关闭。

生产 HTTP Plugin 负责 URL 编码、JSON、签名头和响应解析；Core 不导入 HTTP 框架。
TEST/CORP_TEST 使用 recording local Plugin；singlebox 使用真实 HTTP Plugin。
签名工具可复用，具体 Plugin 不直接依赖另一个具体 Plugin。

### 5.4 预计影响面

| 边界 | 实现时需要同步更新 |
| --- | --- |
| `api/`、`kernel/` | Owner Service API、工作类型、快照/结果/诊断词汇 |
| handoff Core | 成功提交后的 Owner 通知；保留现有 managers 通知 |
| 后台同步 Core | 两类工作的有界调度、合并、重试、生命周期、人工重新提交 |
| Repository contract / implementation | 独立 Owner 读取与受控分页，不新增写入 |
| `plugin_api/`、`plugins/community/`、`plugins/local/` | Owner 出站契约、HTTP 和 recording 实现 |
| DI / 配置 / profile | 同一执行器的装配、Owner 开关、签名与资源释放 |
| README / 架构测试 / 消费者契约测试 | Context Boundary、协议映射、依赖方向 |
| 旧 managers spec / 运维文档 | 明确新增 Owner 通道、开关、诊断和补偿用法 |

不为本功能修改超长 `bot_service.py` 中的无关流程。所有新增/修改源码在实施验收时
检查不超过 1,000 行；需要拆分时只拆本功能涉及的责任，不增加尺寸豁免。

## 6. 数据流与进程内调度

### 6.1 正常路径

```text
Backend 完整 Owner 变更成功并提交
  ├─ 原有 managers 通知（保持）
  └─ 新增 Owner 通知（内存准入）
       → 共享后台 worker 读取当前 Owner
       → PUT ownership
       → 确认响应对应本次目标 Owner
       → 追加 managers 刷新
       → managers worker 重新读取当前完整名单并发送
```

两个 post-commit helper 各自隔离通知异常，一个失败不能跳过另一个。
整个过程中不能重新进入 Passport 管理员同步或重新执行凭证换代。

### 6.2 合并规则与资源上限

- 保留一个专用后台 loop、4 个 worker；Bot entries 与 pending/expanding Space hints
  合计最多 1,024，包含运行中的 entry；Space/人工分页每页最多 100。
- 每个 `SyncKey` 只有一个 entry，显式记录 `OWNERSHIP`、`MANAGERS` 待执行集合。
  同 key 的重复通知取集合并集，不以最后一次 reason 覆盖工作类型。
- worker 在锁内领取待执行集合并清空该集合，随后释放锁执行。
  运行期间的新通知进入新的 pending 集合；本轮结束不得清除新通知。
- 同一进程内同 key 不并行执行。一次领取同时包含两类工作时先 Owner、再 managers。
  Owner 失败也继续原本已领取的 managers 工作，除非整个 worker 正在关闭/取消。
- Owner 成功须安排一次发生在该确认之后的 managers fresh-read；本轮尚未开始的
  managers 工作可满足此要求，无需额外重复发送；否则合并到该 entry 的 pending 集合。
- entry 仍占容量时添加这次后续工作不申请新 slot。不会因队列已满而丢弃已运行 key
  的后续 managers 刷新；关闭/取消仍遵守有界退出，不承诺投递完成。
- 有 pending 的 entry 完成本轮后重新排队；没有 pending 时删除。失败不得留下永久阻塞标记。
- Space fan-out 和原有 managers repair 只提交 `MANAGERS`，不能升级为 Owner 同步。

新建 key 遇到满队列返回 `REJECTED`；现有 key 正常合并。关闭准入后新外部通知拒绝，
不以 key 已存在为理由绕过关闭。Owner 开关关闭时不建立任何 Owner 工作。

### 6.3 顺序保证的边界

Owner 确认后的 managers 刷新用于修正 BCS Owner 替换对边的影响；它不是跨系统事务。
既有 managers 工作可能已在 Owner 通知前运行，其他副本也可能同时写入。
因此不承诺任何瞬间两种角色完全对齐，也不因后续 managers 失败撤销 Owner。

每种工作保留自身的 15 秒、最多 3 次发送预算，不重置预算绕过上限。
同时领取两类工作最多消耗两份预算，而不是宣称整个组合仍只需要 15 秒。
已运行的同步 SQL 读不能强制中断；超出截止时间的结果丢弃，不继续发 HTTP。

## 7. BCS 线协议与确认条件

### 7.1 身份与请求

- 直接请求配置的 BCS origin，不经过 Gateway，不传 Gateway Bearer 或用户请求凭证。
- 路径中的整个稳定 Bot ID 作为一个 segment 编码，不能拼入 Owner、tenant 或额外路径。
- 每次 attempt 重新签发服务主体：HS256、`iss=backend`、`aud=bcs`、`kid=bare`、
  60 秒 TTL、仅 `app_name=backend`；tenant 来自该工作的显式 key。
- 使用既有 signing-key 配置和 SecretResolver，不新增共享密钥或硬编码服务地址。
- BCS 必须允许该部署 env/tenant 的 `backend` 应用执行 `ownership:sync`；
  managers 刷新还需要原有 `managers:sync`。不附带 Human principal 以求权限回退。

### 7.2 响应判定

HTTP Plugin 校验 HTTP 200、`code=20000`、合法 envelope、非空 `request_id`，
以及结果字段的存在和精确类型。`changed` 必须是 bool，不能接受数字代替。
合法 nullable 字段不得被误判成缺字段；矛盾字段组合属于无效响应。

| 合法响应/情况 | Owner 编排结果 | 是否追加 managers 刷新 |
| --- | --- | --- |
| Bot ID 匹配、返回 Owner 等于发送目标、`changed=true` | `CONFIRMED`，实际替换 | 是 |
| Bot ID 匹配、返回 Owner 等于发送目标、`changed=false` | `CONFIRMED`，幂等确认 | 是 |
| Bot ID 匹配、Owner 与 previous Owner 均为 null、`changed=false` | `BOT_MISSING_NOOP`，未对齐 | 否 |
| 结构合法但 Bot ID 或非空 Owner 与发送目标不匹配 | `DRIFT`，本轮结束 | 否 |
| 错误 envelope、字段缺失、类型错误或矛盾组合 | `UNKNOWN`，在预算内重试 | 否 |

`changed=true` 必须有非空 previous Owner，且与结果 Owner 不同；
`changed=false` 的 previous Owner 必须为 null。读取确认只解释本次响应，
不代表以后不会被并发请求覆盖。成功路径不额外执行 GET，GET 留给验收和人工核对。

BCS 负责的已存在语义不可由 Backend 改写：旧 Owner 的 owner/manager 边完全移除，
新 Owner 的 manager 边剥离，其他 managers 不变，pending 转交按其契约失效。
后续 managers 刷新仍以 Backend 当前显式协作者为准；不会凭空把旧 Owner 降级为 manager。
若另一个合法业务操作随后显式授予旧 Owner 协作者身份，按新的 Backend 事实处理。

## 8. 重试、错误隔离与可靠性

每轮 Owner 工作最多 3 次 Plugin 调用，共享 15 秒读取/发送/退避预算；
约 1 秒和 2 秒退避加 jitter，受剩余预算约束。每次调用前重新读取权威事实。

| 情况 | 行为 |
| --- | --- |
| 连接/连接池失败、签名前失败（`NOT_SENT`） | 预算内重试 |
| 超时、断连、5xx、408/425/429、无法确认响应（`UNKNOWN`） | 预算内重试；不推断远端没有提交 |
| 其他 4xx，包括目标未知/已删除、鉴权拒绝、version-0 的 409 | `REJECTED`，不在本轮重试 |
| 本地读取/校验失败、跨 scope 数据 | 不发送，记录失败；不发送默认/空 Owner |
| 缺失 Bot 的合法 200 no-op | 记录 `BOT_MISSING_NOOP`，结束，不忙循环 |
| 明确响应漂移 | 记录 `DRIFT`，结束；人工检查映射/部署 |
| 次数耗尽或预算耗尽 | 记录失败并释放工作，不阻塞后续通知 |
| shutdown cancellation | 停止本地执行，不制造重试任务；远端结果可能未知 |

BCS 的 404 不是本契约的“Bot 缺失”确认，不等同于成功 no-op；可能为路由/部署错误，
按明确 4xx 拒绝记录。业务自身数据库写失败继续作为业务错误传播，绝不能吞掉。

### 8.1 用户接受的限制

- 数据库提交与内存通知之间有崩溃窗口；进程退出可以丢失已经准入的工作。
- 终态 handoff 的普通查询/重试不重新投递 Owner；丢失通知需后续正式变更或人工重新提交。
- 多副本可能读到不同时间的事实，晚到请求可以覆盖更新的 Owner；重试前 fresh-read
  只能降低风险，不能消除已在途旧请求。BCS 本地版本递增不是 Backend 的防陈旧版本条件。
- BCS 契约的单一逻辑同步写入方在本设计中是 Backend；不另设独立自动 Owner 写入系统。
  Backend 多副本不因此获得单写者排序保证；人工 BCS 转交也不属于分布式协调范围。
- 旧 BCS Owner 可能在后续成功同步前仍持有权限；这是一项授权一致性风险，不能只描述为显示延迟。
- 无自动永久恢复/收敛保证。关闭功能不撤销已成功写入 BCS 的事实。

若以后要求强制撤权时限、跨副本顺序或重启自动恢复，需要新的需求和设计，不能声称本方案已提供。

## 9. 配置、生命周期与兼容性

在现有 `bot_manager_sync` 配置块增加一个可选布尔字段 `ownership_enabled`，默认 `false`。
保留已有 `enabled`、`env`、`bcs_base_url`，不重命名配置块，不复制一组 endpoint 配置。

| enabled | ownership_enabled | 行为 |
| --- | --- | --- |
| false / 缺省 | false / 缺省 | 两类同步关闭，零后台资源 |
| true | false / 缺省 | 仅 managers，保持旧配置行为 |
| true | true | 共享执行器支持 managers 与显式 Owner 工作 |
| false | true | 无效配置，启动配置校验失败 |

`enabled` 是共享设施总开关；本次不提供 Owner-only 部署模式。
旧配置升级不会自动开启 Owner 覆盖。未知字段和错误类型继续严格拒绝；
启用时 env/origin 校验和 signing-key 可用性检查沿用现有规则。

- 配置结构错误在配置加载时失败；运行时签名资源不可用绑定 unavailable/disabled，诊断原因。
- Owner 关闭时不解析/创建 Owner HTTP Plugin；manager-only 部署不因 Owner 资源而失败。
- 共享 lifecycle 负责两个 Plugin 的启动可用性和后台 loop 上的关闭，无第二条 Owner loop。
- 保留原有并发 startup 安全、失败清理、关闭准入、有界 drain、取消和线程释放要求。
- singlebox 验收显式开启并配置 trusted app；TEST/CORP_TEST 不访问真实网络。
- 不自动修改生产 BCS 授权。先部署接口和 trusted-app 权限，再启用 Backend 开关。

本次不新增配置热加载。关闭/回滚通过设置 `ownership_enabled=false` 并重启或滚动替换生效；
旧实例按原有 lifecycle 先关闭准入，再有界 drain/退出，新实例不接受 Owner 工作。
若回滚到不识别新键的旧 Backend，须删除配置中的该键，不能只置为 false。
该开关不取消已经在途的远端请求，也不回滚已提交的 BCS Owner；恢复 Owner 需另一次授权写入。

## 10. 诊断与人工重新提交

Owner 与 managers 的诊断显式带工作类型；按 tenant、env、bot_pk、有限 reason、attempt、
准入/终态结果、耗时和可用的 BCS request_id 记录。细分队列满、禁用、读取失败、
拒绝、预算耗尽、no-op、漂移及取消，避免把 `QUEUED` 或 HTTP 200 统称为已同步。
不记录 JWT、签名材料、原始响应、凭证、授权链接或完整协作者名单。

提供 Owner 专用的内部 repair service，通过同一个 Owner 通知入口提交：

- 单 Bot：输入显式 `SyncKey`。
- 分页：输入 tenant、env、exclusive `after_pk` 和 limit；按 PK 升序，每页最多 100。
- 页结果包含末尾游标、queued/coalesced 计数，以及未准入的准确 key 列表；
  禁用/拒绝不能记成成功。禁用时分页调用明确报告不可用，不假装全部处理完成。
- 重新提交永远读取当前 Owner，不接收调用者指定的替代 Owner，不绕过业务 Owner 修改权限。
- 使用受控运维执行环境调用，不新增公开路由、定时扫描或持久补偿任务。

运维文档需给出组合顺序：检查拒绝/no-op 原因 → 修复部署授权或完成既有注册/初始化 →
显式提交当前事实 → 查询诊断及使用有权限的 BCS 身份读回。缺失 Human/version-0 的修复
沿用现有治理入口，不能在同步器中自动越权处理。

## 11. 验收标准

### 11.1 业务触发与失败隔离

1. 正常和恢复执行均只在 `finish()` 成功返回后通知；断言通知时新 Owner/Space 和
   `SUCCEEDED` 已由另一数据库 session 可见。
2. 发起、等待、取消、提交失败不通知；读取已完成状态不制造 Owner 写入。
3. Owner 通知抛错、禁用、满队列或 BCS 故障不改变业务成功，不重入凭证逻辑；
   原有 managers 通知仍被调用。数据库写失败保持业务失败。
4. 审计确认的其他实际 Owner 修改入口分别覆盖完成点测试；不存在的入口不虚构。

### 11.2 Core、调度与 Repository

5. tenant/env/PK 精确读取；缺失/删除 Bot、缺失稳定身份、空 Owner、SQL 失败不发送。
6. managers 名单超限或 Space 资格读取失败不影响 Owner 读取/发送。
7. 每次重试 fresh-read；测试 A→B→C 后重试使用当前 C，而不是首次快照 B。
8. 同 key 的 Owner/managers 通知合并、运行中 dirty rerun、成功追加 managers、
   Owner 失败继续已有 managers、普通 managers 不触发 Owner 均有确定性测试。
9. 维持 4 workers、1,024 总容量、100 分页；容量统计包括运行项与 expanding hints。
   已运行 key 的成功后续工作不因满队列丢失，关闭后外部准入不绕过拒绝。
10. 证明次数/预算上限、取消、后续通知仍可执行，以及无永久屏障和无持久化写入。
11. 生命周期覆盖并发 startup、失败清理、两个 Plugin 关闭、manager-only 模式、
    有界 shutdown；不遗留额外后台线程。
12. 人工单 Bot/分页覆盖准确 rejected keys、无协作者 Bot、作用域隔离、禁用行为。

### 11.3 Plugin、消费者契约与配置

13. HTTP 测试验证精确路径编码、body、签名 claims/tenant/TTL、无 Bearer/Human 回退、无隐式重试。
14. 覆盖替换、幂等、缺失 Bot 200 no-op、返回身份不匹配、矛盾/损坏 envelope、
    未知 Human、401/403/404/409、408/425/429、5xx 和连接/读写超时分类。
15. 新消费者契约测试通过 `world` 取得真正 Owner consumer，覆盖成功和失败，
    并断言 recording Plugin 实际收到调用，不能只检查最终返回值。
16. 配置/profile 测试覆盖默认关闭、旧配置不升级权限、非法组合、签名资源不可用、
    TEST/CORP_TEST recording 与 singlebox real HTTP 装配。
17. 完整 Backend architecture gates 通过，更新 Protocol 测试映射与 Context Boundary；
    不降低阈值，不为新依赖添加无理由豁免。

### 11.4 真实 BCS 验收与证据边界

18. singlebox 注册隔离 Bot/Humans 并完成现有正式 ownership 初始化；通过真实 Backend
    业务完成边界触发 Owner 同步，不直接调用生产 Plugin 或以测试 HTTP PUT 代替触发。
19. 从 BCS 正式读接口验证 Owner 变化、旧 Owner 不再保有既有 owner/manager 权限、
    新 Owner 不保留 manager 边，以及后续 managers 与当前 Backend 投影一致。
20. 本地 Passport 明确不支持真实 handoff 换证时，完整 handoff 测试使用显式测试 DI
    替身仅替换 Passport/运行时边界，保留真实业务编排、数据库提交、dispatcher、签名和 BCS。
    不修改生产 local Passport 使其假装支持；测试必须注明这是受控交接 + 真实 BCS 投递证据，
    不能声称证明真实凭证退役或设备更新。已有实际设备环境再运行完整部署验收。
21. 故障注入证明 BCS 拒绝/不可用不改变已提交业务结果；修复后人工重新提交可以读回当前 Owner。
    no-op、幂等和失败须分别断言，不以单纯覆盖率或 admission 成功替代交付证据。

实施后的验证集合：最近的 handoff/repository 单测、共享 managers 调度和生命周期回归、
Owner consumer/HTTP 契约、相关协作者/Space 间接触发回归、配置/DI、完整 architecture suite，
以及 Backend singlebox 验收。修改共享覆盖配置时运行统一 coverage 入口和 artifact verifier。
未运行的外部环境、真实凭证/设备或完整 CI 门禁必须单独披露，不借用参考 PR 的测试结果。

## 12. 文档审阅与当前验证状态

本轮只产出 spec，不修改生产代码、测试、配置或 BCS 契约；不生成实施计划。
用户已经确认设计方向，书面 spec 仍需审阅。

本轮已通过文档检查：9 个本地相对链接均存在、无未完成占位符、Markdown fence 配对、
无行尾空白。提交前另执行差异空白检查、变更范围和文件行数检查。正文已对配置回滚、响应 no-op、合并工作类型、
权限风险、单盒凭证替身的证据边界进行自审。

尚未运行任何实现单测、架构测试、singlebox 或外部部署验收，因为本轮没有实现。
后续实施 PR/commit 和实际测试结果由实施阶段补充；不得将本设计批准视为功能已交付。

## 2026-11-02 已授权兼容补齐

本次用户明确授权的新范围取代上文“不调整前端/不新增 HTTP 管理接口”对本轮的限制：
补齐已有新老前端使用稳定 BCN 地址，增加仅登录态 super_admin 可调用的单 Bot
补订正提交接口；不增加 Owner 修改入口、不执行历史订正、不增加持久投递保证。

- Owner/managers 使用同一注册地址规则：服务 Bot 的 entity 或转换时保留地址；
  个人/default/desktop 保持旧注册地址。事实读取携带真实 Bot 类型与保留地址。
- 列表/详情、协作目录、display metadata 增量返回 `bcn_bot_id`，原 Owner 字段语义不变。
  前端可兼容未返回该字段的旧服务器；不新增转交 UI。
- `POST /api/bots/owner-handoffs/bcn-repair` 仅接收正整数 `bot_pk`，认证管理员读取
  当前事实提交同一同步运行时。202 仅代表入队/合并，不代表 BCN 已完成更新。
- 既有转交接口支持已配置 `admin.super_admin` 代交接，记录真实操作者，仍执行
  服务 Bot、Space、审批与凭证校验。不得接受客户端声明的管理员/actor 权限。
- OCB 本轮只开启预发 ownership；正式环境保持关闭，历史数据订正另行操作。

操作说明与验收项见 [operations](../../docs/bot-ownership-sync-operations.md)。
