# BCS Bot owner / manager 简化设计

日期：2026-10-10

状态：需求已确认，配套实施计划待评审；本文件定义需求与边界，不授权实施或发布。

代码基线：`origin/dev`，编写时为 `7a363f2c68`。本文路径相对 `apps/bcs/`。

## 1. 目标与范围

本设计提供三项能力：

1. 在 BCS `edge_grants` 上表达 Bot 的 owner 和 manager，并让 manager 获得相同的 Bot 业务操作权限。
2. 可信应用通过 sync 将 Backend 的 Bot 协作者快照覆盖到 BCS。
3. 当前 owner 发起转交，目标 Human 或获授权的可信应用 accept/reject；accept 后原 owner 成为普通 manager。

BCS 对每个 Bot 只保存一份 manager 集合。手工添加、sync 添加、转交产生的 manager 完全等价；sync 是该集合的权威覆盖操作。

本期包含权限变更的事务审计、现有业务入口的统一鉴权、实时连接撤权、初始化与迁移。普通业务操作的全链路双身份事务审计另立需求；本期不向消息、文件、会话和 Chat run 的持久化模型传递新的审计上下文。

转交只改变 BCS 内部管理权。Backend 资产、Engine 配置、Provider 管理权、凭证及外部资源的所有权遵守各自既有契约。Frontend 页面建设由消费者单独实施，BCS 提供明确 API 与消费约束。

## 2. 已确定的产品语义

| 主题 | 约定 |
| --- | --- |
| manager 单项管理 | 保留列表、PUT 添加、DELETE 移除；owner 和 manager 均可管理 manager，manager 可移除自己 |
| API 前缀 | 本设计所有新增 authority 接口统一使用 `/api/v1/collaboration/`，Human 与可信应用使用同一组路由；既有业务/注册 URL 保持兼容 |
| manager 来源 | 不区分来源；没有 team/source 字段、来源合并、move 或按来源删除 |
| sync | 一次请求全量替换全部 manager；也会覆盖手工修改及转交留下的 manager |
| owner | 每个已初始化的存续 Bot 恰好一名；sync 不改变 owner |
| 转交地址 | 由 Bot ID 和目标 owner 的 Human actor ID（如 `human_alice`）定位，不暴露 `transfer_id` |
| 转交记录 | 同一 `(env, bot_id, target_owner_actor_id)` 只有一行，保存该目标最近一次请求的状态 |
| pending | 同一 Bot 当前最多一条；不同目标的新请求须等待原 pending 取消、到期或决议 |
| 再次发起 | 复用同一行并刷新请求时间、七天过期时间；可将终态重新置为 pending |
| 接受后 | 目标成为 owner，原 owner 成为普通 manager；其余 manager 保留 |
| 审计 | 保留权限变更及转交历史；不把当前转交状态表当作不可变历史表 |

未收到新一轮发起时，重复 accept/reject/cancel 返回同一终态，不重复修改权限。

## 3. 身份、权限与不变量

### 3.1 身份

- Human actor 是 BCS 中真实存在、未删除且环境匹配的 Human；`human_` 前缀本身不是身份凭据。Human 的权威存在性是 `bcs_bots` 中对应 env 的 `actor_kind='human'` 未删行（`human_<user_id>`）；OAuth 身份同步表不作为授权守卫依据。写入侧同样：所有 Human 驱动的权威写入（单项 manager PUT/DELETE、转交发起/取消/决议）在写事务（或内存原子写区间）内同时校验操作者 Human 行仍存续，仅残余 owner/manager 边加有效凭证不授予写入，目标与操作者为同一已删 Human 时错误码保持 `bot_authority_human_unknown`。
- manager API 的 `user_id` 使用已验证 User 的 ID，由服务统一映射到 Human actor；转交路径直接使用完整 Human actor ID。
- Human 身份来自既有可信认证链。请求中的 operator、owner 或 Bot token 的 `owner_id` 声明不能证明当前管理权。
- 服务身份复用现有 Gateway 签名认证后的 App identity；BCS 配置显式授予可信 app 的 tenant 与 action；规则以实例 env 绑定。不能仅凭普通 App 身份、请求体 app_id 或 App owners 字段得到权限。
- 同步与代办转交的权限分别授予，记录真实 service ID，不能伪造为 Human 操作；复用认证协议，不新增一套 service token 协议。
- env/tenant 由已验证认证上下文及配置确定，不能由普通 body 字段提升访问范围。需要代办转交时，凭证授权范围包含目标 Human。
- Provider 管理凭证是独立主体。验证成功的 Provider 退役记为 `Provider { provider_id }`，不冒充 Human、整数 App ID 或 system，也不记录 token；这种审计表达不授予 sync/代办转交权限。

App scope 使用显式 allow 规则：`app + tenant + actions`。`app` 与已验证 App 主体的 `app_name` 按字节精确匹配（网关侧 `(app_name, env)` 有唯一约束，不能做大小写折叠或通配）；审计仍记录主体的数字 `app_id`。规则在解析时绑定到本实例 env——配置加载已按 `SERVER_ENV` 选择 `bcs-config-{env}.toml`，不再为每条规则重复声明 `env`。配置规则不再携带 `bot_ids` 与 `target_owner_actor_ids`：规则信任域是本实例上的全部 Bot（含后续注册），仍受 sync 快照上限与完整审计约束，tenant 是规则层唯一的安全分隔；转交 read/accept/reject 不预声明目标集合，由发起 Human 与转交状态机守卫。缺失 tenant 仅能匹配配置中显式的 `tenant = absent`，不能匹配任意 tenant。空规则集、空 action 集合拒绝全部；未知 action、重复规则键 `(app, tenant)`、通配字符串和字段形状错误使配置加载失败；同一 app/tenant 只允许一条规则，不依赖规则顺序合并权限。允许动作仅为 `managers:sync / ownership:read / ownership:accept / ownership:reject`。携带旧字段（`env`/`app_id`/`bot_ids`/`target_owner_actor_ids`）的配置属未知字段，加载即失败，不做兼容降级。配置中引用的 App 一经改名，规则静默回落为拒绝（fail-safe 类停机）；改名必须先与配置侧同步。

### 3.2 管理权

`can_manage(human, bot)` 当且仅当同环境存续 Bot 上存在该 Human 的有效 owner 或 manager 边，且 owner 状态完整。

1. owner 边是唯一所有权事实；`created_by`、历史 creator relation、Bot ID 后缀只作历史数据。
2. `ownership_version = 0` 表示未初始化，拒绝 Human 管理；大于零但 owner 缺失或重复属于完整性错误。
3. owner 优先于 manager。owner 不占 manager 名额，也不需要重复 manager 边。
4. 管理边方向固定为 Human → 物理 Bot；不能把 Human 本身当作被管理 Bot。
5. 管理边不构成 Bot friendship，不授予 permission profile/rules 的消息路由能力。
6. 所有 ID 按精确、区分大小写的身份比较；数据库默认 collation 不得合并不同主体。
7. Bot 被删除时，同事务撤销 owner/manager 并关闭 pending 请求。删除仍拥有存续 Bot 的 Human 必须拒绝。
8. 已删除 Bot ID 不复用；注册、重连、onboard、ensure 和凭证刷新不得重设已有 owner。

### 3.3 授权来源与撤权边界

| 请求所依赖的授权事实 | 撤掉 Human A → Bot X 后 |
| --- | --- |
| A 代表 X 操作、订阅 X 视角；无其他独立权利 | 拒绝，所有仅依赖该边的 HTTP/WS 入口失效 |
| A 自身是 Group/Session 的 Human creator、participant 或管理角色 | 按该资源规则继续授权；Group originator/driver 为当前 Human 自己时以自身身份直接准入（Hook 不承载 Human→Human 管理边），driver 转交或旧管理边撤销后该独立资源权限保留；Session 的 created_by 不是 Bot 的历史 created_by |
| 新 owner B 代表 X | 只取得 X 的资源角色，不继承 A 的 Human 私有视角或个人资产 |
| 仅持 X 的有效 Bot 凭证，没有 Human-bound 身份 | 保留既有 Bot-only 契约；管理边撤销不会轮换已发出的 Bot token |
| Provider 凭证、Gateway App scope | 分别按独立的 Provider/App 授权，不由 Human 管理边派生 |

不将“旧使用者所有访问立即消失”作为保证。Bot/Engine/Provider 及已分发外部凭证的轮换或吊销属于独立需求；入口测试必须记录允许/拒绝依赖的授权事实，而不只记录最终布尔值。

## 4. 最小数据模型

仅修改两张现有表，新增两张权限相关表；消息、会话、文件及运行记录表不增加字段。

### 4.1 `edge_grants`：复用现有字段

- `grant_kind` 增加 `owner`、`manager`。
- 管理边统一 `grant_ref_id = 0`、`rules = NULL`、`originator_policy = same_as_from`；仅认可 `approved` 状态。
- 复用 `revoked` 状态及原行恢复授权，不增加管理来源字段。
- 唯一键调整为 `(from_id, to_id, env, grant_kind, grant_ref_id)`，保留运行时 grant 的 ref 维度。
- 数据库约束保证每个 `(env, to_id)` 最多一条 approved owner。SQLite 用部分唯一索引；MySQL 用可空生成列唯一索引。
- 查询、插入、恢复与撤销按完整新键定位；原有 runtime grant 的行为及历史数据必须通过兼容测试。

### 4.2 `bcs_bots`：一个内部版本

新增非空 `ownership_version`，默认 `0`；首次初始化置 `1`，每次成功转交递增。

该版本用于初始化判定、转交快照校验和 CAS，防止 A→B→A 后旧请求误用。manager 变更不递增此版本。客户端不保存或提交它。

### 4.3 `bot_ownership_transfers`：可复用的当前状态

| 字段组 | 含义 |
| --- | --- |
| `env, bot_id, target_owner_actor_id` | 联合唯一键，也是 API 的定位信息 |
| `from_owner_actor_id, expected_ownership_version` | 本轮发起时读取的 owner 和内部版本 |
| `status` | `pending / accepted / rejected / cancelled / expired / invalidated` |
| `requested_at, expires_at, updated_at` | 数据库时间；expires_at 为本轮 requested_at 加七天 |
| `decided_at, decided_by_kind, decided_by_id, reason` | 本轮决议信息；pending 时为空 |
| `result_ownership_version` | accepted 后的内部版本，其他状态为空 |
| `bot_name_snapshot` | 供最小收件箱展示，不附带 Bot 配置或资源内容 |

另设每个 Bot 的 pending 唯一约束，以及按目标 Human、发起 Human、状态和时间的列表索引。重新发起时覆盖本轮快照、重置决议字段；历史由审计表保留。

### 4.4 `bot_authority_changes`：权限范围内的追加审计

每条记录包含：ID、env、Bot ID、服务端生成的 mutation ID、序号、action、实际操作者种类与 ID、变更主体、必要的前后状态、数据库时间。唯一键为 `(env, mutation_id, sequence)`。

- 审计初始化、manager grant/revoke、sync 差异、转交发起/续期/决议、Bot 退役及其权限清理。
- manager 差异逐项记录；转交记录完整的本轮 owner/version、目标、时间与决议，允许从审计还原各轮历史。
- 操作者明确区分 Human、Gateway App service、Provider、System；service/Provider ID 表示实际发起者，不构成 manager 的另一种授权来源。自动关闭 pending 的决议使用固定 system 原因，同时保留发起退役的真实操作者审计。
- 实际数据变更与审计在同一事务提交。失败全部回滚；相同 manager 快照等无变更操作不新增审计。
- 刷新转交过期时间属于实际状态变更，应记录续期；纯 GET 不写审计。
- 初始化和迁移使用这张表，不增加初始化账本；sync 不保存历史响应账本。

## 5. manager 管理与权威 sync

### 5.1 单项管理

本设计所有接口统一前缀：`/api/v1/collaboration/`。下表及转交表的相对路径追加在该前缀后，不按调用方身份设置另一组路径。

| 方法与路径 | 行为 |
| --- | --- |
| `GET /bots/{bot_id}/managers` | 分页返回当前 manager；同时返回当前 owner 摘要 |
| `PUT /bots/{bot_id}/managers/{user_id}` | 添加或恢复 manager；已存在返回成功且不写重复审计 |
| `DELETE /bots/{bot_id}/managers/{user_id}` | 移除 manager；已不存在返回成功 |

操作者须为当前 owner 或 manager，并在写事务内复验。PUT/DELETE 的目标若是 owner，返回 `409 owner_not_manager`。新增目标必须是有效 Human；不自动创建陌生用户。撤销后的主体不能凭重复 DELETE 绕过调用方鉴权。

### 5.2 sync

可信应用调用 `PUT /api/v1/collaboration/bots/{bot_id}/managers`（集合替换，即 sync）：

GET 集合用于列表，PUT 集合用于覆盖，PUT/DELETE 单项用于手工管理。不设置 `/managers/sync` 静态子路径，避免与合法用户 ID `sync` 冲突。

```json
{
  "manager_user_ids": ["alice", "bob"]
}
```

1. 验证 service 凭证、环境、目标 Bot 和 `managers:sync` 权限，读取当前 owner。
2. 输入去重（1,000 上限对去重后的原始输入计，含被跳过成员），过滤未知、已删除或非法形状的 Human 成员并记日志，最终集合为有效余集；当前 owner 若在数组中，从 manager 集合中归一化排除。
3. 一次事务令 manager 集合等于有效余集：撤销未列出的全部 manager，添加/恢复列出的 manager；owner 不变。输入非空但余集为空时同样清空集合（"最终集合 = 有效输入余集"一条不变量）。
4. 返回有效余集数量及按余集 diff 计算的 added/removed 数量。相同集合再次提交返回零差异。目标 Bot 不存在时返回成功全零并记日志，不写审计、不改状态；version 0 的 Bot 仍拒绝。

空数组是明确清空全部 manager。字段缺失、null 或请求格式错误仍使整个请求失败且不做部分覆盖；输入中的未知、已删除或非法形状成员被跳过并记日志，不使整个请求失败。

每次最多 1,000 个去重后的用户，SQL 参数按最多 100 个主体分块，同一 Bot 的全部差异仍是一笔事务。读取现有大集合须计入总工作量，不能把输入上限误当成存量上限。

**顺序语义：**可信应用为同一 Bot 提供串行、有序的快照；BCS 只保证数据库提交的串行结果。手工操作在 sync 之后仍可改变集合，下一次 sync 再覆盖。请求不带来源版本或幂等 key，也不提供历史响应重放。较早快照在较晚时重试会再次覆盖，因此应用须在进入下一次同步前处理上一请求的不确定结果。即使重试与手工操作相遇，sync 仍以自己的快照为准。

**消费者接入责任：**Backend 使用稳定 User ID，由 BCS 映射为 `human_{user_id}`；sync 从不自动创建身份，协作者须先通过既有可信 Human 流程（注册或任意协作入口的 ensure）获得 `bcs_bots` Human actor 行。快照内容由可信 App 负责：未知、已删除或非法形状的成员不使 sync 失败，而是被跳过并记 warn 日志，`total` 返回有效余集大小——快照与 BCS registry 的漂移靠日志与 total 核对，不靠 400 纠错。每个 Bot 只有一个逻辑同步 writer；多个 Backend 副本共享按 Bot 的队列/租约，失去租约的副本不能继续发送或重试。HTTP 超时不证明未提交：有不确定请求时暂停该 Bot 的新快照，确认旧请求已结束，再发送最新权威快照。不能靠换副本消除已经在网络中的旧请求。

400 仅修正请求体格式后重试（sync 不再因未知成员 400），401/403 修正认证或 scope，404 核对 Bot 生命周期（sync 对缺失 Bot 返回成功全零并记日志），409 核对初始化/状态；500、超时或断连先按不确定结果处理，不提交空快照。Backend 只有收到成功并核对本次快照后才记录初次 sync 完成；发布清单须逐 Bot 核对，未完成则阻止该 Bot 的接入切换。消费者的具体负责人及串行机制写入接入 PR，不新增 BCS 来源账本。

## 6. owner 转交协议

### 6.1 API

以下接口均使用 `/api/v1/collaboration/` 前缀：

| 方法与路径 | 权限与用途 |
| --- | --- |
| `GET /bots/{bot_id}/ownership` | owner/manager 查询当前 owner 和关系 |
| `PUT /bots/{bot_id}/ownership-transfers/{target_owner_actor_id}` | 当前 owner 发起或刷新请求 |
| `GET /bots/{bot_id}/ownership-transfers/{target_owner_actor_id}` | 本轮发起 Human、目标 Human 或获授权可信应用查询 |
| `POST /bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/accept` | 目标 Human 或获授权可信应用接受 |
| `POST /bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/reject` | 目标 Human 或获授权可信应用拒绝 |
| `POST /bots/{bot_id}/ownership-transfers/{target_owner_actor_id}/cancel` | 本轮发起 Human 且仍为 owner 时取消 pending |
| `GET /ownership-transfers?direction=received\|sent&status=...` | 本人收件箱/发件箱；返回每个 Bot/目标的最近一轮状态 |

单条 GET、accept、reject 的同一个 handler 同时接收 Human 和可信应用，后者分别要求 `ownership:read`、`ownership:accept`、`ownership:reject` 及匹配的 env/tenant/Bot/目标 Human 范围。sync 权限不会隐式允许接受转交。服务身份代办的决议明确记录为 service。

统一路径不代表权限合并：manager 单项管理、发起/取消和本人收发件箱仍按 Human 规则；sync 按可信应用规则；单条转交查询和 accept/reject 支持两类身份。双身份请求在这些双准入接口上优先按 Human 处理，Human 不满足条件时不回退到 App 提权；sync 始终显式选择 App 身份。Human 沿用现有邀请码门禁，获授权的 service 不被误当作未绑定邀请码的 Human；所有请求均先通过现有签名认证。

请求路径不含 `transfer_id`，body 不要求 idempotency key、轮次或 ownership version。可信应用只须知道 Bot ID 和目标 Human ID。

### 6.2 发起与续期

数据库事务内锁定 Bot，读取当前 owner/version 和 pending：

- 目标必须是存续 Human，且不能是当前 owner。
- 没有 pending：插入该目标行，或将该目标的终态行重新设为 pending。
- 已有同目标、同 owner/version 的有效 pending：复用该行，刷新 requested_at 和七天期限，不创建第二条。
- 已有其他目标的有效 pending：返回 `409 transfer_pending`，不能自动替换目标。
- 过期或 owner/version 已不匹配的 pending：先在本事务中关闭，再执行本次发起；审计保留关闭原因。

新行返回 201；复用或续期返回 200。这里的幂等指“不增加请求行、不执行转交”，不保证重复请求的时间字段完全相同。

### 6.3 接受

同一事务内完成：

1. 核验调用方、目标、有效期、Bot/Human 存续以及当前 owner/version 与本轮快照一致。
2. 撤销原 owner 边，创建/恢复目标 owner 边，递增 ownership_version。
3. 将原 owner 加为普通 manager，移除新 owner 的 manager 边，其他 manager 不变。
4. 将本行置 accepted，保存决议与结果版本；追加权限变更审计。

所有变更成功后才返回。sync 可在之后移除原 owner 的 manager，因为它与其他 manager 完全等价。accept 与 sync、DELETE manager、Bot 退役均串行化，结果遵循提交顺序。

### 6.4 状态、重试与到期

| 当前有效状态 | 操作 | 结果 |
| --- | --- | --- |
| pending | accept / reject / cancel，调用方合法 | 转入对应终态 |
| accepted / rejected / cancelled | 重复同一种决议且调用方合法 | 200 返回该行终态，无写入 |
| 任意终态 | 不同的决议 | 409，不改变状态 |
| expired / invalidated | 任意决议 | 409，不改变 owner |
| 终态或无记录 | 当前 owner 对有效其他 Human 再次发起 | pending，刷新本轮字段 |

重复取消终态只核验本行发起 Human，不要求其此时仍为 owner；操作 pending 的取消仍必须核验当前 owner。历史成功重放不依赖当下 owner，不会再次执行转交。

GET 按数据库时间投影有效状态，到期即呈现 expired，不写库；过滤、计数、分页必须使用同一有效状态口径。写操作惰性落盘过期/失效状态，无后台到期任务。返回 owner_changed 时，invalidated 与审计须先成功提交；数据库错误仍应整笔回滚。Applied/重复决议的响应携带本命令在同一事务内读到并提交的自身状态快照（写事务末尾的同一事务 SELECT，复用同一有效状态透镜），不在提交后重新读取可复用行——同一目标新一轮抢先落库时，原操作响应仍反映其自身提交的终态，客户端不会误把新一轮 pending 当作本次决议结果而重试。

**当前请求语义：**同目标重新发起会覆盖该行终态。因此，一个没有轮次的迟到 accept/reject 会作用于该目标现在的 pending，请求无法证明自己属于上一轮。本协议不承诺跨轮次防重放；调用方在重新发起前须终止旧轮决议重试，并按 Bot 串行处理流程。内部 version 防止未重新发起的旧快照误转交，不能区分同一地址的新旧调用。

这项已确认取舍同时适用于浏览器旧页面、人工请求、多个可信应用及已发出的网络请求；停止重试不能撤回在途调用。确认界面和服务接入说明必须表述为“对这个 Bot/目标当前请求作决议”，不能承诺确认的是旧页面展示的 owner/时间快照。发布负责人在消费者验收中确认此边界；本期不增加轮次令牌。

转交列表不是不可变历史：同一目标的新一轮可能替换其发件人和时间。权限审计保留历史，普通 API 只暴露本行当前双方所需的最小摘要；pending 不授予目标任何 Bot 资源访问权。

## 7. 数据一致性与错误

- 初始化、manager 变更、sync、转交和 Bot 退役均以同一 Bot 数据库行作为串行化点；进程内 mutex 不足以保证多实例正确性。
- MySQL 使用行锁和条件更新；SQLite 使用写事务及条件更新。有 gateway binding 的生命周期 writer 统一为 binding → 物理 Bot → 转交行 → 涉及的 Human；每一类内按稳定 ID 排序。无 binding 的 authority 操作从 Bot 开始，不能持 Bot 后再请求 binding。Provider attach/webhook/metadata/退役均须核对并统一此顺序，不能只改新退役方法。Human 删除预读关联 Bot 后按同序加锁，再锁 Human 并复验完整关联集合；集合变化回滚重试，不能先锁 Human 再等待 Bot。
- 使用现有 `DbPlugin::transaction`。事务前读只用于准备参数；权限、版本、状态和实际变更必须在事务内通过受保护的 SQL 条件再次验证。
- 条件更新与唯一约束负责并发冲突；`ExecuteChecked` 不能依赖 MySQL 无变化 UPDATE 的不稳定 affected-rows 语义。有限重试仅用于确定已回滚的本地冲突。
- Memory 的 Bot、Human、authority、转交、审计、默认 runtime profile 记录及 ID 分配、Provider Bot metadata、binding 和 ref 索引共享一个原子状态边界；Provider 配置/凭证可保持独立。不能在锁内依次调用两个可独立提交的 Repo 来模拟事务。
- 缺失/无效凭证 401；身份合法但无操作权限 403；目标不存在 404；状态、版本及未初始化冲突 409；输入不合法 400；数据库或完整性错误 500。不得以 false、空列表或成功吞掉持久化错误。
- 公开错误使用稳定错误码，不能把 SQL 或内部凭证传给调用方。

事务容量是实施准入条件：在写路径实施前，用现有静态 DbPlugin steps 在 SQLite/真实 MySQL 证明大存量清空和并发集合替换；冻结目标部署的最大工作量、锁等待、事务/statement 超时、取消后的回滚证据与重试出口。单条 SQL 的100主体分块不代表总事务有界；不能凭外层超时宣称已回滚，也不能未经契约修订截断清空或新增 manager 数量上限。实施计划 Task 0 明确证据及阻塞条件。

### 7.1 Provider 退役与提交后清理

普通 Bot 退役和 Provider 退役是两个有明确契约的命令，不能把“已提交 Provider 删除”再串接普通 retire。Provider Core 验证 Provider 凭证及既有管理范围后，将 metadata、binding fallback 或无 binding legacy 情况交给同一个 Provider Repo 原子退役命令。该命令复验实际分支，一次提交 Bot tombstone、已有 Provider/ref tombstone、必要 binding 禁用、owner/manager 撤销、pending 关闭及审计；gateway 与 plugin 均覆盖。legacy 的既有准入条件保留，不因能审计 Provider 就扩大可删除范围。

提交后先失效 runtime/token/metadata 缓存，再执行既有外部清理。外部清理失败返回稳定的 `retirement_cleanup_pending` 错误，明确持久化退役已完成，不回滚已提交事务，也不报告完整成功。重试允许通过保留的 metadata/binding 或被删 Bot 的受权 tombstone 查回身份（包括无 binding legacy），重复执行幂等清理；不复活 Bot、不重复权限审计、不新增 outbox。缓存失败不得继续凭旧缓存恢复有效 Bot 身份。

## 8. 统一接入既有业务

新增窄接口 `BotAuthorityPort`：查询单 Bot 角色、批量角色、要求 owner、要求 owner-or-manager。授权读取返回 `Result`，不复用吞错误的布尔查询。Hook 委托 authority Core；adapter 不直接访问 Repo。

| 接入面 | 必须兑现的行为 |
| --- | --- |
| mine、legacy `/bots/my` | owner/manager 并集、去重，逐项 `access_relation=owner\|manager`；Human 自身条目保持 self 语义 |
| Bot 配置、状态、删除、onboard | 使用当前角色；转交不改 created_by，旧创建者在失去 manager 后不再有特权 |
| Group CRUD、成员、Session 列表/详情/修改/launch | manager 可使用对应 Bot 视角，但仍受该 Bot 的资源角色约束 |
| 文件、workspace、收藏、消息发送/中止、邀请和好友代操作 | 只替换 Human→Bot 的控制权判定，保留资源自己的范围与可见性规则 |
| event subscription 与 inline provisioner | 混合 User+Bot 凭证走同一实时 Hook，不能再检查历史 owner_id |
| token 签发、WS connect/reconnect、入站操作 | 签发核验 Human 取得 Session token 的资格；连接/重连及入站操作核验实际选择的视角 |

混合 User+Bot 凭证先验证 Human 当前可控制该 Bot，再以 Bot 作为业务主体；不能因 Bot token 上的历史 owner 相同而绕过。纯 Bot 调用保持既有 Bot 自身认证契约。

mine 的 total 在全部筛选之后、分页之前计算；不能把前 500 个 mine 结果当作完整授权集合。按资源鉴权使用目标 Bot 的单项/批量角色查询。`list_by_creator` 保留字面历史含义，另加 `list_controllable`，其内部候选查询不提前应用客户端分页。

### 8.0 mine 动态过滤

1. Store 在一个读取快照中返回经过 env/kind/name/status 等持久化条件过滤、角色去重和稳定排序的轻量候选（ID、kind、status、access_relation），包含合法 Human self；不读取全量 Bot 配置来计数。
2. Application 经 Registry Core 获取这些候选的 runtime-active 结果：一次读取当前实例 WS 连接集合，Provider binding/provider/credential 使用已有批量 port，参数最多 128 ID/块，不逐 Bot 解析 target。保持现有 `status=Online && (WS active || HTTP Provider routable)` 语义；存储错误返回错误，不伪装成 unreachable。
3. 对全部候选应用 reachable/unreachable 后算 total，再 offset/limit，最后批量补本页展示字段。指定 reachability 时 Human self 仍按既有行为排除；未指定时 self 可展示 owner 标签，但不创建 Human→Human owner 边，Hook 仍拒绝把 Human 作为物理 Bot。
4. 候选数据库快照与 runtime 观测不是跨系统原子快照；响应代表本次采样结果，连续页面可因在线状态改变而漂移。页内 total/items 使用同一候选和 runtime 采样。补页时发现目标删除或角色变化，整次重试一次；仍变化返回 `409 bot_list_changed`，不补空页冒充精确 total。

候选空间/扫描为 O(N)，runtime 查询为有界块数而非 N 次 DB 往返；明确记录 N、Provider 数和批次数，不宣传为常数成本或使用截断后的 total。资源授权不依赖 mine 的采样结果。

### 8.1 Human 建群与视角

Human 可将自己控制的非 hidden Bot 引入 private Group，不强制先建立 Bot friendship；public Group 仍只接受 public Bot。纯 Bot 发起者沿用 friendship/public 规则。

该放行同时覆盖首个 Session 和后续 launch。manager 不自动成为 Human participant，不汇总读取其他视角。管理 Group 中普通 Worker Bot 不等于管理该 Group；只有被控制 Bot 本身在资源上有相应角色时才可操作。

### 8.2 已准入输出不追溯（operation-boundary admission）

沿用现有 WS registry、连接身份和 writer，在连接绑定中保存真实 Human、env/tenant、资源和视角。授权成本跟随操作而不是帧：业务操作在接受点（首个副作用前）为每个候选连接解析一次交付视角（一次共享快照批处理），按连接实例冻结为只读进程内许可；该操作的后续帧只携带可信生产者元数据（`operation_id`、canonical `run_id`、terminal 标记），在入队边界以纯谓词核对冻结许可。writer 不再逐帧查授权，也不因逐帧读取而取消连接。

已准入操作的输出不追溯：操作中途的转让、manager 边移除、成员移除或 scope 变化都不中断该操作把已放行内容排干到仍然存活的连接。新操作、迟到订阅、重连和视角替换针对当前事实重新准入，不从任何更早许可继承；许可以连接实例为键，replacement 连接不复用旧实例成果。Session 最后一条 Run 的 fallback 只服务 Internal sink，公共 Workbench 输出不借用其他 Run 的许可血统（§7.2）。

Run 终态不等于操作完成：一次操作可以驱动多条输出流（多 Bot @mention）。操作登记全部 Send Run 的时机在任何下游回调之前（WebSend 在 `register_send_context` 前登记全集）。单一流的终态只关闭该流自己的新帧，兄弟流继续；全部已登记流终态后操作才进入 Draining——已入队帧按原许可顺序排空，此后新帧拒绝并计数。终态判定幂等，允许快回调先于登记到达（登记时按全集抵扣）。重放、Run 通道、Group→Session 路径和 fallback 均携带同一保护绑定。

managed 队列的可执行点不晚于绑定：发送方在持久化准入**提交之前**为每个 Send 目标预分配 canonical Run 身份（`DeliveryAdmissionTarget.run_id`），并在提交前完成该 Run 的 operation 绑定、producer 事实冻结与**完整流集合**（managed ∪ direct）登记；准入存储原样持久化这个身份。因此一个被队列 worker 立即取走或扫描取出执行的行，其回调永远解析到已准入 operation 与冻结事实，发起方在提交边界暂停、被杀或进程切换都不改变这一点——已提交的 managed 工作由 worker 独立完成，不需要发起方在场收尾。提交侧失败、幂等重复与 admission 时刻即终态的投递（Failed/RejectedCapacity）的 fresh identity 被精确释放（墓碑化，不占住完成条件）；提前注册不是以"managed 子集"冒充"完整集合"，direct 兄弟不会被快终态误伤。

释放仅以"该流已无输出"的显式事实为据：`delivered = false` 是下游明确终态拒绝（可释放）；transport 级 `Err` 属不确定结果（远端可能已接收），该 Run 保留 operation 绑定与冻结事实直至其自身终态；`register_send_context` 失败中止发送时，只释放"当前注册失败的 Run 与尚未开始的 Run"——已成功发送或传输结果不确定的兄弟 Run 不被整体中止连坐，不需要为了"整体返回失败"撤销已经接受的输出。

生产者的可见性事实同样在准入时冻结：producer 的 domain 与会话内角色从接受时刻的 Group/Session 快照初始化进该 Run 的事实缓存，首个回调不再读取当前成员表；事件自带的明确 domain/audience 仍然生效，准入谓词不变地把 per-frame 可见性作用于冻结许可之上。独立资源事件（成员/系统/元数据）不属于任何操作，按当前事实每事件授权一次；读取失败只跳过该事件并计数，不关闭任何既有流。

不做逐帧撤权、正向 TTL 授权缓存或分布式撤权广播。入队授权核对不读数据库，帧发送路径与授权读取（authority 边、资源资格、可见性事实）的成本按操作计量而不是按帧计量。许可回收只走显式终态，永不按年龄撤销已准入 operation：全部流终态后经排空宽限回收；接收者连接全部关闭时立即回收；投递失败/准入即拒/发送中止的流由应用显式 release（墓碑化，之后的帧拒绝，operation 绑定同时移除）或队列配置过期、Provider 运行超时等正常终态机制收敛；抛弃的准备态按短准备超时回收。合法排队可以没有上限，许可层不假设等待时长；容量由万级上限显式拒绝兜底，永不为腾位驱逐存活许可。不得把缺失绑定降级为普通公共帧。某条消息不可见不能误关闭仍合法的其他订阅。

终态的传输地板（§9）：数据帧对满队列按慢消费者策略丢弃并计数；终端帧取共享的有界回压预算，超出预算仍丢的终端帧关闭该饱和连接——客户端观察到传输层失败并按既有重连协议恢复，不允许留下"存活但永远没有终态"的静默流。回压预算对 N 个饱和连接共享一个窗口，慢连接不串联拖累其他订阅。有限内存下不承诺对任意慢消费者无限无损交付。

### 8.3 权威资源读取与保护信息传播

新增 Group/Session `read_authorization_snapshot` Core/Repo 契约，返回严格 Result。Group 快照包含 env/id/live、status/record_status、visibility、group_kind、group_strategy、driver、有效 originator 和 participants；每个 participant 明确包含 actor ID、actor_kind、role、effective_mode、message_view_scope。Session 快照包含 env/id/live、group_id、status、session_kind、created_by、message_visibility_version、participant_join_seq 及同结构的 participants；created_by 保留真实 actor，不限定为 Human。均不包含消息正文和全量配置。SQL 实现直接读主数据源、不命中普通 `try_get` 缓存、不在失败时退回缓存；Memory 从当前状态投影。Group/Session Application、event subscription authorizer 和操作准入（§8.2 的每次操作一次批处理）通过 Core 使用该契约。普通展示缓存保持现状，资源表不改 schema。

快照的判定规则保持既有语义：

- Group driver/有效 originator 是管理角色候选；额外的 participant Manager 仅在 `group_strategy=ManagerWorker` 时成为管理候选，Chat/StateMachine 不因同名 role 自动放行。
- Group participants 是 Session 创建时的 seed；Session 创建或新增成员时，合法显式 scope 覆盖 Group seed，否则继承 seed，缺少 seed 才按既有默认 Full 初始化。创建后 Session 的 participant role/mode/scope 独立演化；Group scope 修改不追溯覆盖已存在的 Session。Group 级消息使用当前 Group scope，Session/Run 消息使用当前 Session scope，并读取其关联 Group 的 strategy；不存在的 Session 成员不能靠 Group seed 补成有效成员。
- 操作准入用接受时刻的严格快照解析每个候选连接的 `message_view_scope` 并冻结为本次操作的只读许可（§8.2），不放行"复用连接旧 Full 权限"的路径：新操作和新连接始终按本次快照重新判定。binding 只固定身份、视角 actor、资源范围和连接实例；帧保留 producer 的 domain/audience 等生产者元数据（生产者的角色事实同样在准入时随 Run 冻结）。仅收紧 scope 时，对新操作返回拒绝而对已准入输出完成排空；失去资源资格按当前事实影响后续操作与独立事件。沿用现有 legacy visibility 版本和 join-sequence 规则，不能根据 Group strategy 重写实际 producer domain。

连接 token 策略固定为“签名绑定 Human/tenant/Group/Session，连接时显式选择视角并重新授权”。签发保留 `IssueGroupSessionConnectionToken { caller, session_id }`：只根据当前 Session 读取资格核验是否可取得 token，不接收或返回选定视角，也不预先授予 Bot 视角。保持现有 JWT 格式和到期时间。connect/reconnect 复用既有 wire 字段 `view_actor_id`，缺省为 Human self；Bot 视角只是选择参数，不是凭据，应用须同时核验 token 范围、该 Human 当前管理边和所选 actor 的资源资格，再由 adapter 绑定连接实例 ID。签发成功不保证任意视角可连接；旧 token 不隐式升级，改变视角必须重新授权。

WS 使用类型化 `Protected` 帧（binding、visibility、payload）及明确的连接控制帧。共享 Run manager 区分 `Workbench`（受保护 sender+binding）与 `Internal`（既有服务内部 String sender）两种 sink；`ChatRunEventPort` 的 String sender 只注册 Internal，不可用于 Workbench socket。Run alias/session fallback 必须保留 sink 类型、scope 和连接实例，缺失 Workbench binding 返回错误而非公共发送。真实 Bot event producer 提供可见性元数据，身份来自受权注册，不从业务 JSON 推断。该选择保留内部 HTTP Run port 的数据签名，但补契约与适配测试。

`DeliveryAdmissionService` 使用 Application 级结果；Core/DB 故障在应用层去敏，该次操作安装零许可（记录日志与指标），不影响既有许可与流，adapter 不接收 SQL/Core 错误。

### 8.4 webhook 是持久集成资产

已建立的 event subscription 属于 Group/Bot 的持久集成资产，不是创建 Human 的个人实时授权通道。创建者失去 manager 后既有 webhook 继续按资源订阅投递/重试，由当前资源管理者查询、停用或删除；这些管理操作每次查当前权限。此选择不增加 Human 投递绑定字段或 webhook 末端重授权，不能把 WS 的撤权保证推广到 webhook。消费者和运行说明必须明确这一区别。

### 不确定结果下的输出许可（F1 R10/R11）

- `ManagedMessageDeliveryService::admit` 的 Conflict、Repository(Conflict/Invalid)
  表示确定拒绝，本次调用未提交新工作，可以释放预登记 Run。Storage 或其他错误不证明
  回滚：可能已完成 COMMIT 但回执丢失。此时只释放尚未执行 I/O 的 direct Run，保留
  managed Run 的稳定 ID、operation 关联和冻结输出许可；调用者仍收到错误，不伪装成功。
- 不用一次空查询推断未提交（读副本延迟、事务仍在结束和读失败均可能发生），也不以
  新身份自动重试。已提交工作由既有 durable queue 的正常调度/恢复继续处理；无法证明
  未提交的许可保守保留至真实终态或连接清理，不能用 prepared TTL 撤销可能存活的输出。
  对实际未提交但结果不确定的情况，本修复不新增后台判定器；长连接内可能保留对应登记。
- direct transport 的 `Err` 不执行 `discard_send_context`，保留 callback context、
  active index、deadline、可信 Bot/Session/Run 身份及 IM source message 关联。
  `delivered=false` 仍是明确拒绝，保留原清理语义。Provider/WS 入口继续校验身份、过期和
  terminal，不因为 transport 超时而绕过这些检查。
- 影响仅为 Web/persistent group send 的失败清理及内部 admission 错误语义说明；无
  HTTP/WS wire、配置、数据库 schema 或 Plugin API 变更。正常/最大批量/持续失败路径
  均新增 **0 次 DB 查询、0 次 DB 写入、0 个后台扫描/重试**；现有队列成本不变，保留量
  与不确定 Run 数相关。回归使用真实 Memory admission、RunContext 和 Provider callback
  application 入口，注入 commit 回执丢失/读失败/transport 超时；不替代真实 MySQL 断网测试。

## 9. 分层与实施边界

| 层 | 职责 |
| --- | --- |
| `bcs-domain` / `bcs-service-api` | GrantKind、角色/转交类型、应用/Core/Repo/Hook 契约及 conformance harness |
| `bcs-app-bot`、现有业务 Application | 身份与业务入口编排；调用 Hook/Core，构造仅权限写操作所需的 actor |
| `bcs-edge-permission` | authority Core；与 runtime connect/profile 语义分开 |
| `bcs-edge-permission-store` | 管理边、sync、转交及权限审计的 SQL 权威实现，提供存储层生命周期组合步骤 |
| `bcs-bot` / `bcs-bot-store` | 普通及 Provider 原子创建/退役；Memory 的 Bot/authority/profile/Provider Bot projection 共用原子状态 |
| `bcs-group` / `bcs-session` 及其 Store | 窄的权威资源权限快照读；保留展示缓存和原有 schema |
| HTTP / WS adapters | 参数与认证适配、协议映射、连接保护；只调用应用契约 |
| `bootstrap/bcs` | 凭证配置、同一数据源和共享 Memory 状态、具体实现装配 |
| `tools/bcs-admin` | 初始化检查和受控回填入口，复用配置与迁移工具 |

不新建权限服务进程、通用事务框架或跨模块审计平台。必要的存储层纯 SQL 构造复用可局部提取，但 core/application 不能感知 DbPlugin 或具体存储实现。

## 10. 初始化、发布与回退

1. 追加新版本 MySQL/SQLite migration，不改历史编号文件；上线前检查完整迁移链与已有唯一键数据。**本期例外记录（036/037）**：实现期间发现 036(MySQL)/ 037(SQLite)中 `edge_grants` 唯一键的"先 DROP 后 ADD"两步替换留了无约束窗口。两个文件均为**本 PR 首次引入、尚未合入 dev、未在任何共享环境执行过**，不属于冻结规则保护的已合入历史迁移，因此在原文件内修正而不是新增编号掩盖：MySQL 侧把 DROP+ADD 合并为单条原子 ALTER（元数据锁下同一把锁），SQLite 侧改为先 CREATE 加宽键、后 DROP 旧键（任何时刻都有覆盖旧维度的唯一约束在效）。兼容与恢复状态：终态模式（`uk_edge_from_to_env_kind_ref` 在、`uk_edge_from_to_env_ref` 不在）两种写法完全一致，不存在需要识别或补救的中间库态；重新执行由 `IF NOT EXISTS/IF EXISTS` 幂等控制。发布时按第 5 条维护窗口停写旧 writer，与本项的窗口防护彼此独立成立。核对方式：`bcs-admin db migrate --check-files` 双方言 + `bot_authority_migration` 迁移链测试断言终态键存在性。
2. 显式受信任的 Human 注册：Bot、Human 校验/必要创建、owner、version、默认运行时 profile 及必要 gateway binding 在同一持久化事务提交；不得部分成功后返回成功。
3. 未携带可信 Human 的 runtime Bot 可保持 version 0。已有初始化 Bot 的重新注册、onboard 与凭证刷新只更新其原有职责字段。
4. Provider-admin 注册面是身份权威的受信代理（provider admin token 认证 Backend，其声明的单数 owner 即验证身份）：新建 Bot 时同一调用完成 Human actor 必要创建（名称取自用户目录、缺省回落工号）与 owner 初始化（owner 边、version 1、`initialize` 审计），审计 actor 记 `Provider { provider_id}` 而不冒充所声明 Human；已有 Bot 的重注册拒绝不同 owner（既有 `created_by` 守卫），owner 永不重置。
5. 在维护窗口冻结旧 writer，通过 `bcs-admin` 先 dry-run。仅创建记录与历史关系不矛盾、指向有效 Human 的 version 0 Bot 可申请回填；缺失或冲突输出人工治理清单。
6. apply 仅处理显式确认的 Bot ID，每批最多 100；逐 Bot 原子写 owner/version/审计。重复执行已初始化 Bot 返回当前状态，不回写创建者，不重置已转交的 owner。
7. 所有挂载中的 Human 授权消费者一起切换；Backend 使用已验证的 service 凭证提交初始 manager 快照。禁止先开放转交而让部分入口继续按 created_by 鉴权。
8. 回退先停止权限写入口；保留读取当前管理边的兼容版本。发生过转交后不能回退到按 created_by 决定权限的版本。

### 10.2a 迁移窗口：created_by 读侧兼容墊片（临时条款，带日落）

为把本设计落到存量环境（联调与生产均有预授权期注册、无 owner 边的 Bot），允许一个**有时间边界的过渡姿态**，窗口期内：

1. `bot_authority.legacy_created_by_fallback`（默认开）。严格读（`role`/`roles_for`）在一场景下回退第二判据：仅当 Bot 活体、`ownership_version = 0`、且 `created_by`（裸工号）与请求者对应时，返回 Owner。actor 活体注册门**不适用**于此臂（未迁移者往往没有 actor 行）；version > 0 的完整性错误与已初始化/墓碑状态永不被该臂遮蔽。边仍然是唯一权威事实：墊片不写边、不产审计、不改变任何写路径判定。
2. 写路径不 fallback：manager 编辑、ownership transfer、退役的守卫仍读当前 owner/manager 边。
3. **按需迁移驱动器**:`GET /openapi/v1/collaboration/bots/mine` 处理时，对调用者名下全部活体、version 0、零冲突（bcs-admin classify 同义）的存量 Bot 执行逐台幂等 `initialize_ownership`，审计 actor = 调用者本人。工作台首次进入 mine 页即完成该用户名下迁移；失败逐台跳过不阻断列表。
4. 观测与日落：墊片命中与迁移执行写 `tracing` 日志供迁移进度巡检；全环境迁移完成后独立 PR 同时删除墊片代码、配置项与本段条款。

禁止将该窗口扩展为第二种长期授权事实：其存在仅服务于「先上线代码、再迁数据」的实施序。

### 10.1 注册调用边界


两个既有 HTTP surface——legacy `POST /register` 和 OpenAPI `POST /openapi/v1/collaboration/register`——的 v1 Human token 与 v2 Provider token 分支均委托同一个 `RegisterService.register_bot`。这里 v1/v2 指注册 token 版本，不是路由版本；两个既有注册 URL 保持兼容，不属于新增 authority API。adapter 只做各自参数/响应适配，不能再编排 connect→onboard。

`RegisterService` 验证 v1 Human 注册 token 后，调用 Registry Core 的 `register_with_ownership(TrustedBotRegistration) -> CommittedBotRegistration`；Core 分配 Bot ID/token 并调用 BotRepo 的组合创建命令。`CommittedBotRegistration` 是独立的已提交凭证回执，不修改既有查询类型 `RegisteredBot`。Provider v2 由同一 Application 调用 `ProviderRegistrationCoreService::register_with_ownership(RegisterProviderBot, OwnershipInitialization)`，验证 Provider 准入后调用 BotProviderRepo 的组合创建命令。Application 不访问 Repo；必要 Human、Bot、默认 runtime profile、owner/version、gateway projection 和初始化审计均由相应 Bot Store 单事务组合。

legacy 仍兼容 `bot-name`/`bot_name`、名字去空白和 2–64 字符检查；v1 token 忽略历史未知的 mode/provider_bot_ref/webhook_url，即使 mode 不能按 Provider 枚举解析也不改变该容忍规则，映射到 facade 时不携带这些字段。v2 才解析并校验 Provider 参数。OpenAPI 保持自己的严格解析和 v1 token 不支持 Provider 参数的规则。成功响应分别保持 legacy 200 扁平 JSON、OpenAPI 201 envelope，均为 no-store，并保留各自错误映射；不能将 legacy v1 直接套入 scoped query parser。凭证只出现在既有注册响应，不进入普通 get/list/discovery。

MemoryBotRepo 使用同一 `MemoryBotState` 实现既有 `PermissionProfileRepoPort`，供注册与 runtime 消费者共享；bootstrap 替换该链路上的 `NoopPermissionProfileRepo`，不另建初始化专用副本。状态含 profile 记录、按 `(env, bot_id)` 的 active-default 索引及非零 `u64` ID 分配器。注册在临时状态内准备默认 profile，随全部生命周期状态一次提交；不能持锁调用会自行提交的 profile port。`ensure_default_profile` 重试返回原 ID，保留现有 profile 的 revision/digest/rules；`get_active_default` 可读到同一记录，`upsert_revision` 更新同一状态且不换 ID。profile/审计故障不得暴露半写，Memory 与 SQL 使用同一生命周期验收。

原 v1 `connect→onboard` 且 onboard 失败仍返回成功的行为在此受信任注册流程中明确取消。持久化失败不返回 Bot token、不激活连接/缓存；默认 profile 或必要身份写失败也不能降级成功。提交后只做可重建的本地缓存发布；网络断连可能使客户端没收到已完成注册，沿用既有凭证不重放契约，不能宣称跨网络恰好一次。重复 Provider/ref（含已删除记录）仍冲突。runtime 无 Human 入口保留，已删 ID 的任何重试均不得复活。

## 11. 验收矩阵

以下是功能完成标准；后续计划须逐项指定实现步骤和证据。

| ID | 验收 |
| --- | --- |
| A01 | owner/manager 角色、精确身份与环境隔离；未初始化/损坏/DB 失败均不放行 |
| A02 | mine 并集、self 展示、WS/Provider 动态过滤先于分页；前页全不可达及 >500 条正确，计入 runtime 查询成本 |
| A03 | owner/manager 均可添加移除 manager；自撤权下一请求拒绝，owner 不能被 manager 接口修改 |
| A04 | sync 覆盖全部来源的 manager、保留 owner；手工修改可被下次同步覆盖，空数组/空余集明确清空 |
| A05 | sync 请求格式错误不部分写入；未知/已删除/非法形状成员跳过并记日志、最终集合等于有效余集，去重、1,000 上限、分块与所有差异同事务，重复集合零审计，缺失 Bot 返回全零 |
| A06 | 所有新增接口只有统一前缀；同路由按身份授权，service 的 tenant/action 范围生效（规则以实例 env 绑定、App 按已验证名称匹配）；普通 Human、Bot token、sync-only 凭证不能代办转交 |
| A07 | 同 Bot 最多一条 pending；同目标复用行并续期，不同目标冲突；终态可由当前 owner 重新发起 |
| A08 | accept 原子切换 owner/version，原 owner 为普通 manager；之后可被 DELETE 或 sync 移除 |
| A09 | accept/reject/cancel 竞争只有一方生效；同终态重试无副作用，重新发起后按当前请求处理 |
| A10 | 七天有效期、GET 无写、筛选分页一致；owner/version 不符则提交 invalidated，ABA 不误转交 |
| A11 | 只有本行当前双方或受权 service 可读/决议；pending 不增加 Bot 资源权限，审计保存各轮历史 |
| A12 | manager、sync、accept、退役并发串行；数据库或审计失败全部回滚，Memory/SQLite/MySQL 契约一致 |
| A13 | 仅依赖管理边的业务/eventing/legacy 入口撤权；独立 Human creator/成员权限保留，新 owner 不继承它，Bot-only/持久 webhook 边界明确 |
| A14 | Human sponsorship 覆盖 Group 创建和后续 Session；资源角色及不同视角可见性保持隔离 |
| A15 | WS 覆盖真实 producer→Run/fallback→writer；两实例仅撤资格或仅收紧 scope 均读新事实；Group/Session scope 独立性、旧 token 显式视角、三态及在途边界正确 |
| A16 | owner/manager 不产生 runtime friendship 或路由许可；已有 profile/rules 保持行为 |
| A17 | legacy/OpenAPI × v1/v2 token 真实注册，Memory profile 对 runtime 可读且失败全回滚；Provider 三分支退役、清理重试、Human 删除竞争及 webhook/accept 锁序正确 |
| A18 | 回填 dry-run 零写、冲突人工治理、重跑不重置 owner；完整迁移链及兼容回退路径有证据 |
| A19 | 审计准确区分 Human/Gateway App/Provider/System、无凭证泄露、逐轮差异/续期可还原；普通业务表不新增审计 schema |
| A20 | role/list/sync/WS 的实际 SQL 次数、扫描与队列成本有量测；统一接口的两类调用身份及各 Store 的 conformance 全部通过 |

已确认的实施依据：单集合覆盖、统一路由前缀、目标 Human 定位、可续期当前请求及其重试边界。实施计划须保留这些语义。
