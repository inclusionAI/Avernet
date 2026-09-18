# BCS Bot owner / manager 边权限、委托管理与 ownership 转交

- 初稿日期：2026-09-18
- 合并修订：2026-09-22
- 状态：Draft / 核心需求与转交范围已确认，工程默认值待评审；不是实现授权
- 负责模块：BCS
- 初稿代码基线：`acac76d251`；2026-09-22 审阅后重新核对的代码基线：`08bb8f79cf55062f82f113641690012d199d0df9`（当前分支与已 fetch 的 `origin/dev` 的 merge-base）
- 路径若无特别说明均相对于 `src/bcs/`；根架构文档路径相对于仓库根目录
- 本文合并管理权限与 ownership 转交设计，保留已有 PR 的文档路径，作为本主题唯一设计来源
- 本轮只修改文档，不修改业务代码、OpenAPI schema 或数据库，不生成实施计划，不自动 commit/push

阅读导航：第 1 节区分已确认需求与建议默认值；第 4—8 节定义角色、数据及业务平权；第 9—11 节定义转交流程；第 12—17 节定义授权边界、传播、迁移与成本；第 18 节保留全部 43 项验收用例。

## 1. 目标、已确认需求与工程默认值

### 1.1 目标与范围

为 BCS 物理 Bot 增加显式的人类管理者，并支持在接收方确认后转交 ownership：

1. `GET /openapi/v1/collaboration/bots/mine` 返回当前用户拥有和管理的 Bot，每个列表 item 必须包含 `access_relation` 字段，明确当前用户是 `owner`，还是仅具有 `manager` 权限。
2. managed Bot 与 owned Bot 在同一资源角色下业务操作平权，包括 Group/Session 视角、消息、文件与实时连接；不把普通 Bot 的管理者升级成整个 Group 的管理者。
3. 当前控制权以边权限为事实源，不在 metadata、Frontend 或 Backend 再维护独立的 owner/manager 名单；创建来源与可转移 ownership 分离。
4. 覆盖 BCS V1 和仍挂载的相关 legacy HTTP / Workbench WS 路径；Frontend 是契约消费方，页面布局和跨模块业务实现不在本轮范围内。

**“平权”指业务管理操作平权，不包含处分 ownership，也不赋予全局管理员权限。** 本稿采用只有当前 owner 能发起转交的默认值。

### 1.2 已确认的功能与转交规则

1. **仅转交 BCS ownership**，不迁移 Backend/Engine 的资产所有权、部署、Provider 绑定、计费或运行时凭据。
2. 当前 owner 发起，由指定接收方确认后才生效；待确认时 ownership 不变。
3. 转交完成后，接收方成为 owner，原 owner **保留 manager 权限**。
4. 同一环境的同一 Bot **同时最多一笔待确认转交**。
5. `mine` 的每个 Bot item 明确返回当前用户对该 Bot 的权限关系；本合同使用 `access_relation: owner | manager`，不能要求前端通过 created_by 自行推断。

### 1.3 本稿采用、尚需评审的默认值

| 问题 | 本稿采用的语义 |
| --- | --- |
| manager 能否配置其他管理员？ | 草案暂按可以添加、撤销其他 manager、自撤权设计；不能经 manager API 改变当前 owner。**这是实施前必须由需求方及权限安全评审明确确认的首要默认值，尚未获确认。** |
| manager 能否执行破坏性业务操作？ | 与 owner 在同 Bot、同资源角色下平权，仍受删除等业务条件限制；不能发起 ownership 转交。 |
| manager 和转交接收方的主体要求 | 必须是当前身份作用域内已存在、未删除的 BCS Human，User ID 来自可信身份映射；禁止 Bot/App/AccessKey/组织/通配主体，不凭任意输入创建用户，不依赖私有目录。 |
| 接收方是否必须先是 manager？ | 不要求。禁止转给自己；不能用 hidden/visibility 配置代替账户可用性判断。 |
| 原 owner 的 manager 是否永久保留？ | 是普通、可撤销的 manager 身份，不是永久前任 owner 特权；其他 manager 不因转交改变。 |
| Group/Session 是否默认聚合多个身份？ | 不改变现有 `view_bot_id` 合同；省略仍为本人 Human，不隐式聚合全部可控 Bot。 |
| manager 授权是否需要申请/审批或有期限？ | 不需要，仍为直接授予/撤销；只有 ownership 转交要求接收确认。 |
| 转交有效期与修改规则 | 固定 **7 天**，客户端不能延长或修改接收人；拒绝、取消、过期后可创建新请求。 |
| 接收人如何发现请求？ | 第一版通过转交收件列表，无短信、邮件、审批人代理或外部通知依赖。 |

这些默认值用于形成完整可评审合同，不声称用户已逐项确认。manager 再授权意味着其拥有完整管理员成员管理能力；第 4.2 节规定授予第三人的边不随授予人撤权而级联删除，因而存在持续权限扩散面。文档审阅通过不等于产品已接受此默认值。实施前须记录明确的接受或收紧决定；如收紧为仅 owner 可分配 manager，应同步修改第 5.4/6/8 节及 AC09，不能由实现者自行选择。默认多视角聚合如需变更，也必须显式修订合同。

### 1.4 非目标

不迁移 Backend/Engine 资产归属、Skill、部署、Provider 绑定/凭据、计费或 runtime token；不提供多 owner、组织继承、临时 manager 授权、细粒度 reader/writer、manager 申请审批或默认多视角消息聚合。本功能不是离职清权或整机资产移交方案。

## 2. 架构约束与现状证据

遵守根 `AGENTS.md`、`CONTEXT-MAP.md`、`docs/arch/arch.rules.md`（特别是 Rules 1/7/12/16/25）、`docs/arch/ci.enforce.md`、BCS `AGENTS.md` / `CLAUDE.md` 及相关 crate `CONTEXT.md`。没有发现直接定义本功能的 accepted ADR；本稿不覆盖其他模块的资产 ownership 决策。

以下证据已于 2026-09-22 在代码基线 `08bb8f79cf` 重新核对，不是已经实现的新能力。fetch 后 `origin/dev` 已继续前进，但本分支 merge-base 仍为该提交；不把目标分支尚未进入本分支的代码当作现状。本次核对时 HEAD `fafacd54a5` 相对该基线只增加本文，以下引用文件与基线一致。此前 `9629a88919` 已不是当前 HEAD 的祖先，不再作为最近核对基线。以后 rebase 须重新验证本节证据和第 18.4 节行数，不能只替换哈希。当前分支的 owner/manager 角色仍为拟议合同。

现有代码并非“所有接口都只对 creator 可见”：Bot `get/query` 是 Human 控制面查询，不做 ownership 过滤；ownership 主要限制 mine、修改、代行与部分资源可见性。此次不收紧已有目录查询。

| 位置 | 当前行为与改造意义 |
| --- | --- |
| `crates/contracts/bcs-domain/src/edge_permission.rs` | `GrantKind` 仅有 `PermissionProfile` / `Rules`；没有独立管理权限。 |
| `crates/services/bcs-edge-permission-store/src/lib.rs` | `is_authorized` 以“任意 approved 出边”判断准入；`list_active_grants` 的查询/解码失败会退化为空或跳过行，不能作为新管理权限读取的错误合同。 |
| `crates/services/bcs-edge-permission/src/lib.rs` | 已复核：Admission 仍用 `is_authorized` 判断任意有效边，`build_authz_context` 仍遍历有效边。新增 Human→Bot 好友 grant/revoke 的 `FriendAuthSyncPort` 调用，以及从 created_by 读取 owner metadata 的 helper；owner/manager 必须与 runtime、好友同步隔离。 |
| `crates/application/v1/bcs-app-bot/src/lib.rs` | `list_mine` 先物化本人 Human，再按 `created_by` 查询；`update`、candidate perspective 单独检查 owner。 |
| `crates/application/v1/bcs-app-group/src/lib.rs` | 列表通过一个 View Actor 查群；详情使用 owned Bot 集合，代行管理等部分操作还接受 legacy `is_creator` 关系。 |
| `crates/application/v1/bcs-app-session/src/lib.rs` | 列表、消息是单一 View Actor；详情、收藏、管理、成员变更分别有 owner 检查。 |
| `crates/application/v1/bcs-app-session/src/file.rs` | 文件成员判断、caller identities、upload mutation 各有 owned Bot 判定。 |
| `crates/application/v1/bcs-app-session/src/connection.rs` | 签发 session token 与 connect 时调用 V1 Session 详情授权；并非仅靠 token 签名决定访问权。 |
| `crates/service-api/bcs-service-api/src/application/v1/authorization.rs` | `HumanOrOwnedBot` 对同时携带 User/Bot 的调用做同步 `bot.owner_id == user.id` 判断。 |
| `crates/services/bcs-session/src/launch.rs` | Session 创建还有独立的 owned Bot 检查；只改 V1 facade 会在下游再次被拒。 |
| `crates/application/v1/bcs-app-invitation/src/lib.rs` | Bot 好友管理、邀请、审批与 acting actor 仍检查 exact owner；新基线的 approve/revoke_friend 会继续向 ConnectService 转发 request_auth，不能改造权限时丢失该合同。 |
| `crates/services/bcs-bot/src/application/bot.rs`、`crates/services/bcs-group/src/application/management.rs` | legacy Bot 管理、群管理和 Workbench 授权还有下游 owner/creator 判断。 |
| `crates/adapters/http/bcs-http/src/router.rs` | legacy 入口仍有 `/bots/my`、`/groups/my`，不能只验收 V1。 |
| `migrations/mysql/014_edge_permission.sql` | edge 唯一键为 `(from_id,to_id,env,grant_ref_id)`，未包含 `grant_kind`。 |
| `api-contracts/v1/openapi/bots.yaml` | mine/修改以 `created_by` 判 owner，普通 PATCH 不允许写该字段。 |
| `crates/service-api/bcs-service-api/src/core/actor.rs` | legacy owner relation 是 `is_creator=true`，upsert 不允许降级；不能当作转交协议。 |
| `crates/services/bcs-bot/src/application/onboarding.rs` | Bot ID 后缀匹配当前用户时允许覆盖 created_by，随后补建 owner relation；会导致转交后被重新认领。 |
| `crates/services/bcs-bot/src/application/bot.rs` | Provider switch 也会覆盖 created_by 并补 owner relation，必须限制为元数据/传输切换而非 ownership 重置。 |
| `crates/services/bcs-bot/src/core/provider_registration.rs` | 注册会建立 owner relation，首次初始化与重复注册需区分。 |
| `crates/services/bcs-bot-store/src/lib.rs` | save_created_by 先改内存再写 DB；不具备 ownership 转交的原子性、版本检查或跨实例一致性。 |
| `crates/plugin-api/bcs-db-api/src/transaction.rs` | 已有条件写、事务结果绑定与原子事务合同，可在 store 实现条件状态切换；application 不操作 DB plugin。 |
| `crates/service-api/bcs-service-api/src/port/friend_auth_sync.rs` | owner_work_no 是 legacy metadata；合同明确 TC adapter 使用 Bot actor ID 的 owner 后缀寻址，不等于当前 BCS ownership。 |
| `crates/adapters/ws/bcs-ws/src/web/frontend_delivery.rs` | 当前 publish 按 visibility/audience 广播并处理 run fallback，没有逐目标查询新的角色事实；第 14/17 节的持续授权读取是拟新增热路径开销。 |

正式合同参照 `api-contracts/v1/openapi/{bots,groups,sessions,session-files,connections,friendships,invitations}.yaml`。`CLAUDE.md` 的历史概要不能替代这些版本化合同。

相对旧调研基线，原证据表的 21 个显式文件只有 edge-permission 与 invitation 两个文件变化。复核了这两处差异及其当前鉴权/准入路径；其余原引用文件内容未变。边权限服务新加的好友同步是独立外部行为，不是 manager/owner 角色同步机制。

**不能仅给枚举加一项或新增转交 endpoint，再调用 save_created_by / 连续增删关系边。** 身份初始化、鉴权、缓存与重连路径必须一起遵守新的事实源。

## 3. 方案选择

### 3.1 显式 owner / manager 角色边与统一授权 Hook（推荐）

增加 `GrantKind::Owner` / `GrantKind::Manager`，统一存储在 `edge_grants`，由 composition root 注册的 authority Hook 解析当前角色。created_by 保留来源，独立 transfer 记录承载邀请、确认和历史结果，不是另一份当前 owner 名单。严格隔离控制面角色与 runtime invocation grants。

优点：创建来源与可转移控制权分离；不产生第二份角色事实源；mine、撤权和转交围绕同一模型验收，不把 default profile wildcard 混同管理权。

成本：演进角色类型、唯一键、版本与事务，并迁移全部当前 ownership 消费方；不能只改 HTTP handler，也不能保留创建者兜底。

### 3.2 未采用的替代方案

| 方案 | 取舍 |
| --- | --- |
| 名为 manager 的 PermissionProfile | 当前 Profile/Rules 面向 runtime 工具调用，default 为 wildcard allow；只增加名称不能隔离控制面，还需额外分类和消费者改造，profile 唯一键也限制扩展。 |
| 新增 `bcs_bots.owner_user_id`，manager 仍在边中 | 唯一 owner 建模简单且可保留 created_by，但当前授权事实分散，不符合统一边权限方向；不同时维护这个可独立写入的字段或“缓存”。 |
| metadata / 独立管理员表 | 形成平行管理名单，增加双写与事实源冲突。 |
| 修改 created_by / 多个 legacy is_creator | 混淆创建来源、当前 owner 与不可降级旧关系；重复 onboarding、旧 claim 和旧边会残留权限。 |

不采用通过改写历史创建者让旧检查“自然通过”的捷径。

## 4. 权限模型与事实源

### 4.1 术语与判定

| 概念 | 新合同 |
| --- | --- |
| `created_by` | 切换前已有的创建来源记录；新建时记录创建者，转交不改写。不声称能恢复旧版本曾覆盖掉的真实历史创建人。 |
| `owner` edge | 当前唯一 BCS owner；可通过专用转交操作变更。 |
| `manager` edge | 显式管理者，可有多个；可执行本文定义的业务管理操作，不能发起 ownership 转交。 |
| `ownership_version` | Bot 上的单调 ownership 版本；不存用户 ID，不是第二份 owner 事实。 |
| transfer record | 某次转交的双方、版本快照、状态和审计；历史 accepted 不代表该接收方仍是当前 owner。 |

```text
role(U, B) = owner    if approved OwnerEdge(human(U.id), B.id, env)
           = manager if approved ManagerEdge(human(U.id), B.id, env)
           = none    otherwise

can_manage(U, B) = role(U, B) in {owner, manager}
can_transfer_ownership(U, B) = role(U, B) == owner
```

本人 Human row 的 mine 展示继续为 `owner`，仅表示 self identity；Human 不建可转交 owner 边，也不进入物理 Bot 转交流程。

- **Controllable Bot**：当前用户角色为 owner 或 manager 的物理 Bot，不等于好友集合。
- **View Actor**：当前请求选择的资源视角，代表谁查看，但不改变真实操作者身份。
- **Group Manager**：群角色/driver/originator 的管理能力，与 Bot manager 是不同授权范围。

`mine` 同 Bot 只返回一次，owner 标签优先。本人 Human 的 self identity 不允许扩展为管理其他 Human。

### 4.2 不变量

1. 当前 owner 由唯一 owner 边决定，不依赖 manager 边。普通 manager API 不能改 owner；owner 切换只允许经过专用初始化/转交等受治理入口。
2. 只能使用认证上下文中的 User；请求中的 `user_id` 只能指定被授权人，不能指定操作者。
3. `env` 从已装配的服务上下文取得，不能由请求任意选择；不新增 tenant 到 env 的推断。沿用已验证的身份/租户边界，manager 不提供跨边界映射。
4. manager 不产生反向边、不传递到 Bot 的好友、下属 Bot 或其他用户；manager 的 Bot 也不会继承该用户的管理权。manager 主动授予第三人的边是独立授权，撤销授予人的管理权不级联撤销第三人；owner 可读取完整名单并逐项撤销。
5. public/protected/private 与好友、通配 Rules、default profile 均不授予管理权。hidden Bot 仍可被 owner/manager 配置，但不能因此绕过原有协作禁用条件。
6. 不根据 created_by、Bot ID 或读取行为自动 claim/补造角色；未初始化 ownership 的 Bot 需完成治理。已初始化却缺失 owner 是一致性错误，不能回退 creator 或只凭 manager 放行。
7. `created_by`、资源原始创建者与历史审计不因 manager 或 ownership 转交改写。Gateway Bot `owner_id` 不被伪造，也不能被当作实时 BCS ownership。
8. 不自动把 manager 加成 Group/Session 成员，不复制聊天或收藏，不预创建 Session。
9. 已初始化的 live physical Bot 恰有一个有效 owner。允许历史对象显式未初始化，但不能依靠 created_by 或 ID 后缀自动赋予转交能力。
10. 角色边不进入好友列表或 A2A runtime grants。owner 不能经任意 edge CRUD 写入、撤销或复制；只有专用初始化、转交、受治理修复和删除入口可改变它。

## 5. 存储、数据库约束与 manager 生命周期

### 5.1 角色边编码

```json
{
  "env": "local",
  "from_id": "human_user-b",
  "to_id": "bot-a",
  "grant_kind": "manager",
  "grant_ref_id": 0,
  "rules": null,
  "status": "approved",
  "originator_policy_type": "same_as_from",
  "originator_policy_data": null
}
```

以上为 manager 示例；owner 使用相同的固定形状，仅 grant_kind 为 owner。增加 `GrantKind::Owner` / `GrantKind::Manager`，序列化为 owner/manager；role edge 均为 Human → physical Bot，不引用 PermissionProfile。

这是拟新增的领域编码，不是当前已经支持的请求 JSON。`grant_ref_id = 0` 是 Owner/Manager 类别内的固定占位值，不指向 PermissionProfile；不在此轮把现有必填 ref 全面改成 nullable。

- 合法形状固定为 Human → physical Bot、上述 ref/rules/policy；authority evaluator 必须校验完整形状，不能只比较一段 JSON 或权限名。
- 有效性要求源 Human 与目标 Bot 存在、环境一致，边状态为 approved。未知枚举、非法主体、损坏行属于数据错误，不当作普通授权。
- 同一 Human/Bot 可同时持有 friend edge 与 manager edge；删除好友不删 manager，撤销 manager 不删好友。
- grants 保留 revoked 行。再次授予必须把同一行恢复 approved；不能沿用 `INSERT IGNORE` 后取回 revoked ID 就返回成功。

### 5.2 ownership 与转交记录

1. `edge_grants` 支持 owner/manager，基础唯一键升级为 `(from_id,to_id,env,grant_kind,grant_ref_id)`；所有 upsert 和 ID 回查同步更新。
2. `bcs_bots` 新增 `ownership_version`，必填，未初始化为 0；新 owner 初始化为 1，每次成功转交递增。其他 manager 变更不递增此版本。
3. 新增 `bot_ownership_transfers`。复用 DbPlugin，但不把转交硬塞进 Connect 的 permission_requests 工作流。

拟议 transfer 字段：

| 字段 | 语义 |
| --- | --- |
| `transfer_id` | 不透明、不可变的业务 ID |
| `env`, `bot_id` | 精确资源作用域 |
| `from_user_id`, `to_user_id` | 发起 owner 与指定接收人，创建后不可修改 |
| `expected_owner_version` | 发起时的 ownership_version |
| `client_request_id` | 发起操作的幂等键，调用方生成的 UUID |
| `status` | 第 9 节定义的六态 |
| `expires_at` | 必填，创建时固定 |
| `decision_actor_kind`, `decided_by`, `decided_at` | pending 时为空；人工终态为 human + 真实 User ID，系统终态为 system + 固定系统标识；不把系统标识当用户 ID |
| `result_owner_version` | 仅 accepted 有值，是当次提交版本，不随未来转交修改 |
| `terminal_reason` | 可选的固定机器原因，如 bot_deleted/actor_unavailable/owner_changed；不存任意错误或凭据 |
| `bot_name_snapshot` | 创建时用于接收方确认的最小展示信息，不包含 summary、prompt、配置或凭据 |
| `gmt_create`, `gmt_modified` | 沿用仓库数据库时间约定 |

transfer 的终态结果就是本操作的持久审计：双方、操作者、前后版本、时间与状态在一次事务内记录，终态不可覆盖。不再建立另一张当前 owner 表；manager API 的独立增删按第 5.4 节审计合同记录。转交引起的角色变化由 accepted transfer 一次性解释，不需要伪装成好友申请。

### 5.3 唯一性与索引

| 不变量 | SQLite | MySQL |
| --- | --- | --- |
| 每 Bot 至多一个 approved owner | 在 `(env,to_id)` 上建条件唯一索引，谓词为 owner+approved | nullable generated `active_owner_slot`，owner+approved 为 1，其余 NULL；唯一 `(env,to_id,active_owner_slot)` |
| 每 Bot 至多一条 stored pending transfer | 在 `(env,bot_id)` 上建条件唯一索引，谓词为 pending | nullable generated `pending_slot`，pending 为 1，其余 NULL；唯一 `(env,bot_id,pending_slot)` |

- MySQL nullable slot 必须采用可索引的显式生成列实现，不依赖 PostgreSQL 式 partial-index 语法。需在实际支持的 MySQL 上验证完整迁移与并发。
- 两类约束只能保证“至多一条”；初始化、接受和删除事务保证已初始化 live Bot “至少一个 owner”。发现版本>0而 owner 缺失/损坏时返回一致性错误并拒绝授权，不按 manager/creator 兜底。
- 幂等唯一键为 `(env,bot_id,from_user_id,client_request_id)`；同一键不同 payload 返回冲突。
- 建立接收/发起收件索引，至少支持 `(env,to_user_id,status,gmt_create,transfer_id)` 和对应 from 索引；有效过期状态查询需同时考虑 expires_at，不作全表扫描。
- 字符串主体 ID 使用精确、区分大小写的身份比较语义；沿用 edge store 已有的方言封装，验证 MySQL collation 不合并不同身份。

新增 schema 必须用**新编号迁移**，不能修改已提交的 `014_edge_permission.sql`、初始 schema 或历史 SQLite Rust migration body。实施时从各 dialect 当前最大版本分配，不在草案里抢占号码。

### 5.4 manager 变更、并发与审计

增加专门的 manager mutation repository 契约（所有转交事务另见第 10 节），提供“校验当前授权 + 变更边 + 写审计”的单事务语义。不是在 application 层依次调用三个独立写入方法。

- 同一目标 Bot 的管理员变更与 ownership 初始化、转交和删除使用同一序列化边界；在事务内再次确认执行者是当前 owner 或有效 manager，且 ownership 已初始化且完整。
- MySQL 使用相同目标 Bot 行的事务锁及当前读，SQLite 使用等价的写事务/条件写；Memory 实现采用同一临界区。不依赖进程内锁保证多实例一致性。
- 只对实际状态变化追加审计；重复授予/撤销返回当前状态，不重复制造业务事件。
- 复用 `permission_requests` 记录已决定的管理授权审计：新增 `request_kind=manager`；撤销记录使用 `revoke`，指向相应 manager edge。记录真实操作者、被授权人、目标 Bot、env、edge_id 和决定时间，不覆盖以前的记录。
- 审计中的 `from_id` 为被授权 Human Actor，`to_id` 为 Bot；`created_by/decided_by` 为真实操作者的 Human Actor ID，`edge_id` 必填，`requested_ref_id/requested_rules` 为空，`status=approved`。撤权同样是已批准的决定，当前权限以 edge 的 revoked 状态为准。
- 此处是直接授权的审计记录，不是新的申请流程。好友 inbox、Connect 的 approve/reject/cancel 必须排除这些记录并拒绝处理其 ID，防止从旧流程恢复管理权。
- 边写入、审计写入或提交失败均回滚并返回错误；禁止 best-effort 持久化后报成功。
- Bot 删除或 Human 删除需撤销关联 manager edges，并保证同 ID 重建不会恢复旧委托。删除动作与授权失效的持久化必须有原子或明确的失败恢复保证，不能只依靠 registry 缓存清理。

## 6. 管理者列表 API

新增在 `/openapi/v1/collaboration` 下的 Human-only API，沿用当前 envelope、认证要求和错误码体系；不修改全局认证链。

| 方法与路径 | 语义 |
| --- | --- |
| `GET /bots/{bot_id}/managers?offset=0&limit=20` | owner/manager 查看显式管理员列表；按 `user_id ASC` 稳定排序，limit 为 1..100。 |
| `PUT /bots/{bot_id}/managers/{user_id}` | 幂等授予一个 Human 管理权；请求无业务 body。 |
| `DELETE /bots/{bot_id}/managers/{user_id}` | 幂等撤销该 Human 的显式管理权，不删除用户或 Bot。 |

采用逐项幂等变更，而不是盲目整表覆盖：管理页面编辑列表时提交增删差集，避免两个管理员各自覆盖对方的新授权。整表 replace/CAS 本轮不引入。

GET 的 `data` 示例：

```json
{
  "bot_id": "bot-a",
  "owner_user_id": "user-a",
  "items": [{"user_id": "user-b", "actor_id": "human_user-b", "role": "manager"}],
  "total": 1,
  "offset": 0,
  "limit": 20
}
```

- 当前 owner 单列为只读字段，不混入显式 manager 列表。该 API 仅处理 ownership 已初始化的 Bot，owner_user_id 必填；未初始化返回 **HTTP 409 / `ownership_not_initialized`**，与第 11.2 节共享错误码表一致；数据损坏返回 HTTP 500，不用 null 掩盖。
- PUT 成功返回 `bot_id/user_id/role/changed`，role 为 `manager`；DELETE 返回 `bot_id/user_id/revoked`，revoked 表示本次是否改变状态。均为 HTTP 200。
- 对 owner 本人执行 PUT/DELETE 返回 HTTP 409，错误码 `owner_role_requires_transfer`。当前 owner 不能由此 API 授予或移除，应使用专用转交流程。
- 格式错误、未知 body/参数、Human 目标 Bot 使用管理者 API：400；缺认证：401；执行者无权：403；已获资源访问资格后发现被授权 Human 不存在：404；数据库失败：500，不暴露底层 SQL。共享的初始化/基础设施错误采用第 11.2 节的同名 code/HTTP 映射；其余资源特有错误仍按本节处理，不把转交专属错误机械套用到 manager API。
- 被授权人的 `user_id` 必须解析为已存在的、同环境 Human Actor；不凭输入创建任意 Human。不依赖私有组织目录完成公共本地流程。
- 外部只以 user_id 操作管理关系，不接受任意 edge_id、grant_kind、规则模板或反向关系写入。
- 管理员可撤销自己；成功后下一请求不再拥有委托权。已失权调用者再次 DELETE 不能利用幂等性绕过鉴权，应得到 403。

## 7. `mine` 契约

### 7.1 返回形状

保持现有 `data.items/total/offset/limit` 以及 Bot 字段平铺结构，仅在每个 mine item 新增必填字段：

```text
access_relation: "owner" | "manager"
```

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `access_relation` | string enum：`owner`、`manager` | 是，每个 `data.items[]` 均返回，不可为 null 或省略 | 当前认证用户对该 Bot 的最高有效控制面角色；不是 Bot 的全局固定属性。 |

判定规则：

- `owner`：当前用户是该物理 Bot 的当前所有者，以有效 owner 边为准。
- `manager`：当前用户**不是 owner**，但拥有该 Bot 的有效 manager 边；“仅 manager”不包含 ownership 转交能力。
- 同时命中 owner 与历史 manager 边时只返回一条 item，字段为 `owner`，不能返回角色数组或重复 Bot。
- 两种角色均不命中的物理 Bot 不进入 mine；本人 Human row 保留第 4.1 节的 self identity 兼容语义，返回 `owner`，不因此获得可转交 ownership。
- 字段由服务端按当前认证用户和当前角色事实计算，不使用 created_by、Bot ID 后缀或前端推断。它是本次读取的权限快照，不能替代后续业务请求的服务端鉴权。
- OpenAPI 的 mine item schema 必须将该字段列入 required，并限制上述两个枚举值；类型生成、HTTP 序列化和前端 DTO 同步更新，不只在 UI 临时拼标签。

示意：当前认证用户为 `user-a`（展示 `data`，省略原 Bot 其他字段）：

```json
{
  "items": [
    {"bot_id": "bot-owned", "kind": "bot", "created_by": "user-b", "access_relation": "owner"},
    {"bot_id": "bot-managed", "kind": "bot", "created_by": "user-a", "access_relation": "manager"},
    {"bot_id": "human_user-a", "kind": "human", "created_by": "user-a", "access_relation": "owner"}
  ],
  "total": 3,
  "offset": 0,
  "limit": 20
}
```

示例中 `bot-owned` 由别人创建、现已转交给 user-a，因此为 owner；`bot-managed` 由 user-a 创建、现已转交给其他人且保留 manager，因此为 manager。created_by 相同或不同都不能替代此字段。

Service API 用独立 `MyBot` / `MyBotAccessRelation` 投影，`list_mine` 返回 `Page<MyBot>`；HTTP 仍平铺，不把通用 Bot DTO 强行变成“处处都需要当前用户”的模型。`get/query/discovery` 不增加未经定义的空标签或管理名单泄露。

### 7.2 查询顺序与兼容

1. 校验认证、筛选和分页，然后沿用幂等的本人 Human 物化行为。
2. 在当前 env 求 owner 集合与 manager 集合的并集；managed 侧只包含 physical Bot。
3. 按 Bot ID 去重、owner 优先，再统一应用 kind/name/status/reachability。
4. 按既有 `created_at DESC, bot_id ASC` 排序，计算 total 后再 offset/limit。

不得分别分页两类 Bot 再拼接；必须覆盖重叠、空页及跨页交错案例。`kind=human + reachability` 仍为空；hidden 是否出现继续由现有 status 过滤合同决定。

第一版可复用现有 mine 对完整候选集计算 reachability 后分页的方式，但 managed 查询和 Bot 批量 hydration 必须成批执行，不能扫描全站 Bot 或逐 Bot 查边。若未来做 DB 分页优化，不能提前于 reachability 过滤截断候选集。

`list_bots_by_creator` / `list_by_creator` 保持字面意义，不暗中改成包含 manager；新增显式 controllable 查询，避免注册、身份绑定、统计等消费者被连带改变。legacy `/bots/my` 在自身既有筛选/排序合同下也按当前 owner/manager 集合查询，并提供相同标签。

### 7.3 转交与命名兼容

转交后 A.mine 从 owner 变 manager，B.mine 从无/manager 变 owner；关系以当前角色边为准，created_by 不随标签改变。本稿统一拟议角色值 `manage` → `manager`、mine 标签 `owned/manage` → `owner/manager`；manager 列表返回 `role`，动作仍可称 can_manage。

当前分支未实现这些角色，不需要同时支持两套已发布 wire alias；若实施前发现其他分支已发布旧值，必须补版本化兼容方案，不能静默更改生产枚举。保留当前文档文件名以避免已有 PR 和链接失效。

## 8. Group / Session 业务平权

### 8.1 视角与列表

- `view_bot_id` 省略：保持 Human 本人视角。
- `view_bot_id=human_{current_user}`：允许；其他 Human：拒绝。
- `view_bot_id=physical_bot`：统一验证 owner 或 manager；不要求 manager 再添加该 Bot 为好友。
- View Bot 必须真实参与待查询资源。拥有一个 Bot 不意味着能枚举它所在群内其他 Bot 的私有 Session。
- Group 的 `membership=direct/session_only/all` 保持原含义与默认值。Session-only 参与不能被伪装为正式群成员。
- 保持排序、过滤、分页、消息可见性及 sessionless Group 查询无副作用的合同；不因管理授权新增成员或消息。

### 8.2 操作矩阵

下表的“同 owner”始终针对同一个 Bot；现有 Human 直接参与和 Bot 自身身份权限另行保留。

| 操作 | owned Bot | managed Bot | 仅 friend/public |
| --- | --- | --- | --- |
| 发起 ownership 转交 | 允许，且需接收人确认 | 不允许 | 不允许 |
| 进入 mine / 用作 View Actor | 允许 | 同 owner | 不允许 |
| 修改 Bot 可变字段、可见性、状态、任务开关 | 允许 | 同 owner | 不允许 |
| Bot 管理员列表读取、授权与撤权 | 允许 | 同 owner | 不允许 |
| candidate/search perspective、代 Bot 好友与邀请操作 | 允许 | 同 owner | 不因好友关系获得代行权 |
| Group/Session 详情读取 | 该 Bot 参与时允许，保留原路径约束 | 同 owner | 不因此获得读取权 |
| Group/Session 修改、删除、成员管理 | 对该 Bot 的角色/creator/driver/originator 权限进行判断 | 同 owner | 不因此获得管理权 |
| Session 创建、reactivate、acting_bot_id | 可代行且仍满足 launch 条件 | 同 owner | 不因此获得代行权 |
| 消息历史、发送/abort、交互响应、workspace | 保留对应参与、角色、scope 和 lifecycle 校验 | 同 owner | 不因此获得访问权 |
| 文件读取、上传后续操作、分享、删除 | 保留原成员和文件 owner/creator/driver 规则 | 同 owner，扩展其中“拥有该 Bot”的谓词 | 不因此获得访问权 |
| 收藏与取消收藏 | 操作指定参与者的收藏 | 同 owner，操作该 Bot 的共享收藏状态 | 不允许 |
| Session token、Workbench WS / SSE | 通过对应资源授权 | 同 owner，并支持撤权后重新校验 | 不因此获得授权 |

补充边界：

- Bot 是普通 worker 时，Bot manager 不能凭该角色升级为 Group Manager。管理 driver/manager/originator Bot 时，才取得 owner 原本对应的群管理能力。
- 管理 Session creator Bot 可以获得已有 Session 管理权；但不能仅凭 creator/manager 身份跳过消息接口的 View Actor 参与条件。
- 获取 Group 列表中的 session-only 摘要，不意味着已满足更严格的 Group 详情访问条件；保持已有 owner 行为。
- 用户 Human 自己上传的文件不属于其 managed Bot。管理该 Bot 不获得另一 Human 的文件 ownership。
- 收藏归属于选定 Bot，而非为每个 manager 新建私人副本；真实操作人另记审计。

## 9. ownership 转交状态机

```text
pending ── accept ──> accepted
        ├─ reject ──> rejected
        ├─ cancel ──> cancelled
        ├─ deadline ─> expired
        └─ resource/ownership invalidation ─> invalidated
```

`accepted/rejected/cancelled/expired/invalidated` 都是终态，不允许改回 pending，也不更新接收人。重新发起一定产生新 transfer_id 和新的版本快照。

| 操作 | 授权与效果 |
| --- | --- |
| 发起 | 当前 owner；目标为另一个合法 Human；只创建 pending，不授予目标任何资源权。 |
| 接受 | 指定接收 Human；请求有效且版本、双方、Bot 均通过重新校验；事务切换角色。 |
| 拒绝 | 指定接收 Human；终结请求，不改变 role edges。 |
| 取消 | 发起 owner，且 pending 时仍为当前 owner；终结请求，不改变 role edges。 |
| 过期 | 数据库时间达到 expires_at，不能再确认；不改变 role edges。 |
| 失效 | Bot 删除、参与主体删除/失效、ownership 快照不匹配；不能继续执行。 |

待确认不冻结普通 Bot 使用或 manager 名单变更；接受时总是依据当前事实。若原 owner 在确认前撤销了接收人的 manager 权限，请求仍可接受，因为成为 owner 不要求预先是 manager。

新 owner 一经确认即可另发下一次转交；原 owner 此时仅是 manager，不能以旧请求或旧 claim 再次发起。

### 9.1 过期与 pending 唯一性

- 建单时以数据库 UTC 时间固定 `expires_at = create_time + 7 days`；边界 `now >= expires_at` 一律不可接受。
- “最多一笔 pending”由数据库状态约束保证，包括尚未物化 expired 的过期记录，不能只靠先查后插。
- 接受/拒绝/取消/再次发起前，在同一 Bot 事务下将已过期 pending 物化为 expired，释放唯一槽位；这部分写失败则整个命令失败。
- GET/list 只返回有效状态投影，不写数据、不授予权限；物理 pending 已过期时返回 `status=expired`。系统决定者与 decided_at 同时投影为过期系统标识和 expires_at；之后物化保持同一逻辑决定时间，gmt_modified 才记录实际写入时间。状态筛选须在数据库中按同样规则执行，不能先分页再过滤。
- 第一版不依赖定时清理任务完成授权正确性；空闲过期行在下一次变更时清理即可。用户看到 expired 不代表后端曾执行一次转交。

## 10. ownership 事务与并发协议

所有 ownership 转交、owner 初始化、manager 变更和 Bot 删除使用同一个 per-Bot 序列化边界。MySQL 锁住精确 Bot 行并使用当前读；SQLite 用写事务或等价条件写；Memory 使用同一临界区。

### 10.1 创建请求

一次事务中：

1. 根据认证 User 和资源作用域查幂等键：同 payload 可直接返回既有最小回执；不同 payload 冲突。这个分支不需要重新取得 owner 权，也不返回 Bot 私有数据。
2. 不存在既有回执时，加载并锁定 Bot；在锁内先以当前读重查幂等键，处理等待锁期间已建单甚至已结束的同 key 请求，不沿用事务开始时的旧快照。
3. 仍无既有回执时，确认 live physical Bot、角色完整性、操作者是当前 owner、接收人合法且不是自己、expected version 一致。
4. 在同一事务内清理本 Bot 的失效 pending：已过期则物化为 expired；未过期但其 from_user_id/expected_owner_version 与合法当前 owner/version 不一致，则按第 10.2 节物化为 invalidated 并释放槽位。如果仍有有效 pending，才返回 `ownership_transfer_pending`。此处只复用失效记录的事务更新语义，由已验证的当前 owner 发起新建触发内部清理，不要求其也是旧请求的接收人；不向其返回旧请求的双方或详情，也不增加旧请求读取权限。不能要求旧请求的接收人先尝试 accept，才能解除旧请求对合法新建的阻塞。
5. 插入 pending，请求唯一约束作最终防线。返回已提交的记录。

幂等重放可以由原发起人读取同一键的历史回执，不要求其仍为当前 owner；它只返回既有记录，绝不创建/重新执行请求。新的 key 总是重新验证当前 owner。

### 10.2 接受请求

先按 transfer_id 认证并读取最小记录：同动作终态重试可以直接返回历史回执，不依赖 Bot 仍存在。需要执行新状态迁移时，按固定顺序锁 Bot、transfer 和相关主体（多个主体按 ID 排序，避免互转死锁），并重新读取状态、使用事务内当前事实检查：

1. caller 是指定接收 Human；请求状态、截止时间、双方 Human 存在性/可用性及 env 符合合同。已 accepted 的同接收人重试先返回历史回执，不要求当前 owner/version 仍等于旧快照。
2. 当前 owner 仍是 from_user_id，ownership_version 等于 expected_owner_version。版本检查必须存在：A→B→A 后的旧请求不能复活。不匹配时执行下述已提交失效分支，不进入角色变更步骤，也不将 pending 保留到过期。
3. revoke 原 owner 边；建立/恢复接收方 owner 边。
4. 建立/恢复原 owner 的 manager 边；revoke 接收方重复的 manager 边。其他 manager/friend/runtime edges 不变。
5. CAS 递增 ownership_version，并将 transfer 设为 accepted，保存 decided_by/at、result version。
6. 所有角色变更与 accepted 回执一起提交，再失效派生缓存；不得先改内存再尝试持久化。

**owner/version 校验失败的处置**：caller 与请求可见性校验已通过、请求仍 pending 且未过期、当前 authority 数据完整时，若 from_user_id 或 expected_owner_version 任一不匹配，在同一加锁事务内：

- 条件更新该请求 `pending -> invalidated`，`terminal_reason=owner_changed`，决定者为系统，decided_at 使用数据库时间，result_owner_version 保持为空。
- 不修改任何 owner/manager 边，也不递增当前 Bot 的 ownership_version；由状态变化释放唯一 pending 槽位。
- 提交该失效记录，再以领域结果 OwnerChanged 返回 application，由其映射 **HTTP 409 / `ownership_changed`**。不是先抛存储异常再把失效写入回滚。
- 同一接收人重试这笔因 owner_changed 失效的 accept，仍返回 409 / ownership_changed，不重写审计或恢复请求。GET 可读到持久化的 invalidated 终态；当前 owner 可立即用新 client_request_id 发起下一笔请求。
- 若读取/解码失败、owner 缺失或数据损坏，不能假定 ownership 已变化并主动失效请求；按一致性/基础设施错误拒绝。失效 UPDATE 或提交失败则回滚，返回 500，pending 槽位保持原状态，重试后才能释放。

数据库事务失败或提交失败返回错误，不能用补偿性的“尽量改回来”假装原子。所有权改变不调用 Backend/Engine/Provider，不持锁执行网络请求。

### 10.3 幂等与互斥结果

- accept 与 cancel/reject/expire 并发：锁与条件状态切换选出唯一终态；失败方得到既有同动作回执或终态冲突，不能修改角色。
- 同一接收人重复 accept 已 accepted 请求：返回原 accepted 回执，不再递增版本。即使 Bot 后续转交给 C，也不把 owner 改回 B。
- reject/cancel 的同动作重试返回原终态；已 accepted 上执行 reject/cancel 返回 409。
- 同 Bot 并发创建不同请求最多成功一个，其余 409；同 key 同 payload 则重放同一请求。
- 删除 Bot 与 accept 共享序列化边界。删除先提交则 invalidated；accept 先提交则按新角色重新校验删除权限。原 owner 保留 manager，若对应删除规则允许 manager 删除，它仍可能合法删除，不得误报为 ownership 漏洞。
- Human 删除不能把无主 Bot 静默转给别人。拥有 live Bot 的 Human 删除应被拦截并要求先完成转交；其他相关主体失效使 pending 不可接受。跨模块账号删除同步不由本 spec 实现，BCS 的主体可用性以其已验证身份和可观察的 Actor 生命周期为边界。
- Bot 删除需在删除事务中终结 pending、撤销 role edges。若某入口支持重用 bot_id，重建前必须完成旧生命周期清理；旧 terminal transfer 永远不能作用于重建对象。绕过受控 API 的数据库重建不在支持范围。

终态幂等与新建的历史回执重放必须先做认证和原请求双方身份校验，但不重新执行已结束的资源变更。若初查后 Bot 被删除，应重新读取已终结的 transfer：按历史回执/invalidated 处理，而不是尝试重新创建 Bot 或恢复边。事务锁获得后才取 DB 时间并执行条件状态迁移；截止时间判定点是该条件迁移，不是请求到达时间或响应返回时间。

expired/invalidated 的物化应作为已提交的领域结果返回，再由 application 映射 409；不能把它当底层事务异常回滚，导致唯一 pending 永远不释放。真正的数据库失败始终回滚并报错。

现有 DbPlugin 的 transaction steps/ExecuteChecked 应足以表达条件更新；实施需用真实原子查询/条件写覆盖事务内再校验，不能在事务外预查后直接 blind write。若实际插件无法表达锁和条件，则先演进合同并补 conformance，不在应用层拼接多个事务。

## 11. ownership 转交 API

### 11.1 HTTP 面

统一前缀 `/openapi/v1/collaboration`，遵守现有认证 envelope、Human 身份要求与 request_id 规则。

| 方法与路径 | 语义 |
| --- | --- |
| `GET /bots/{bot_id}/ownership` | owner/manager 查询当前 owner_user_id 与 ownership_version |
| `POST /bots/{bot_id}/ownership-transfers` | owner 发起转交 |
| `GET /ownership-transfers?direction=received&status=pending&offset=0&limit=20` | 本人的转交收件箱/发件箱；direction 为 received/sent，status 可省略取全部 |
| `GET /ownership-transfers/{transfer_id}` | 转交双方查询这笔请求及回执 |
| `POST /ownership-transfers/{transfer_id}/accept` | 接收人确认 |
| `POST /ownership-transfers/{transfer_id}/reject` | 接收人拒绝 |
| `POST /ownership-transfers/{transfer_id}/cancel` | 发起人取消 |

发起 body：

```json
{
  "to_user_id": "user-b",
  "expected_owner_version": 3,
  "client_request_id": "b2baf5e4-069e-4ead-bd39-d6efc53bf2f1"
}
```

ownership 查询的 `data`：

```json
{
  "bot_id": "bot-x",
  "owner_user_id": "user-a",
  "ownership_version": 3
}
```

accepted 回执的核心 `data`（展示省略了时间等字段）：

```json
{
  "transfer_id": "transfer-01",
  "bot_id": "bot-x",
  "from_user_id": "user-a",
  "to_user_id": "user-b",
  "status": "accepted",
  "expected_owner_version": 3,
  "result_owner_version": 4
}
```

- GET 转交记录不要求接收人已经控制 Bot，只允许原发起人与指定接收人；不允许其他 manager 枚举转交对象。
- 回执始终表示历史事件，字段不命名为 current_owner。当前 owner 另走 ownership 查询。
- 收件接口只依据认证 User，不接受任意 user_id 代查。direction 默认 received，status 省略表示所有状态。offset >= 0、limit 1..100，默认 0/20；按 `gmt_create DESC,transfer_id ASC` 排序，统一过滤后计数分页。count/page 使用同一数据库时间和读快照，避免跨过过期边界得到矛盾 total；不隐含读取副作用。
- 转交对象可以看到 bot_id、创建时名称快照、双方 ID、状态、截止时间和本操作版本信息；不能借 pending 读取 summary、prompt、群、Session、文件、凭据或 manager 列表。
- 页面确认时应展示“仅 BCS ownership；原 owner 保留 manager；不迁移部署和凭据”，不得宣称整个 Bot 资产交接完成。
- create body 三字段均必填；to_user_id 为非空、精确身份字符串，expected_owner_version 为 >=1 的整数，client_request_id 为 UUID。同 payload 指这些经过格式校验的字段完全一致，不把过期请求复用为新请求。
- create 首次成功 201，幂等重放 200；accept/reject/cancel 成功或同动作重放 200。action 请求无业务 body，未知字段/参数拒绝。

### 11.2 错误语义

| 情况 | HTTP / code |
| --- | --- |
| 无可信 User / 身份认证失败 | 沿用既有 401/403 身份合同；Bot/App/AccessKey 不能代替 User |
| 参数错误、接收自己、Human 当作被转交 Bot | 400 / invalid_request |
| 可见 Bot 上非 owner 发起、owner-only action 被 manager 调用 | 403 / forbidden |
| 请求不存在或 caller 不是转交双方 | 404 / ownership_transfer_not_found，避免枚举 |
| 双方身份合法但角色不允许该动作（如发起人 accept） | 403 / forbidden |
| 接收方不是可解析的当前作用域 Human | 400 / invalid_transfer_recipient，仅已授权发起者获得该结果 |
| 已有有效 pending | 409 / ownership_transfer_pending |
| owner/version 已变化 | 409 / ownership_changed；accept 对未过期 pending 按第 10.2 节先提交 invalidated（owner_changed）再返回；该失效原因的 accept 重试返回同码。新建请求仅客户端版本过时则拒绝，不改有效请求 |
| 已过期 / 不兼容终态 | 409 / ownership_transfer_expired 或 ownership_transfer_not_pending |
| 同幂等键不同 body | 409 / idempotency_conflict |
| Bot 尚未完成 ownership 初始化（manager/ownership API 共享） | 409 / ownership_not_initialized，不自动 claim；第 6 节引用此映射 |
| 查询、解码、写入、审计回执或提交失败 | 500，fail closed，不返回空成功或泄露 SQL |

资源不可见/不存在时沿用相关 Bot API 的 concealment 合同。对具体转交 ID，先判断 caller 的最小查看资格，再暴露状态/版本等信息。

## 12. 认证、历史来源与外部归属边界

### 12.1 认证与资源授权分离

manager 是资源授权，不是新的登录方式。复用经验证的 `AuthenticatedCaller.user`，不要求 Gateway 为 BCS 转交重签 owner，不修改同时在工作区拟议的 V1 auth-plugin-chain 设计。

现有 `HumanOrOwnedBot` 的同步 owner claim 判断需要显式演进，而不是无条件放开不匹配：

1. 保留原 `HumanOnly` / `BotOnly` 含义；Bot-only 请求不能获得 Human 的管理者列表能力。
2. 对需要平权的混合身份用例，新增 `HumanOrAuthorizedBot` 应用策略，使用异步 authority Hook 选择 effective Principal。
3. User + Bot 同时存在时，根据当前数据库事实验证 User 对该精确 Bot 的 owner/manager 关系；Bot-only 仍只能代表已经验证的自身身份，不能借 manager 冒充其他 Bot。
4. HTTP adapter 只做身份存在性、DTO 解析与错误映射；不得在 adapter 中查管理边。`dto/session.rs` 及 internal session-file handler 中的同步 Principal 选择也必须迁入受保护的应用入口。
5. Human 仅通过请求参数选择 managed Bot 时，保留真实 Human caller，在应用授权后产生 effective actor；不伪造 Bot 凭证。
6. 写操作审计区分 `operator_user_id` 和 `effective_actor_id`。仅记录被代理 Bot 会丢失管理员身份，不能满足授权变更追溯。

不能直接删除 `owner_id != user.id` 的拒绝分支却不补实时管理权检查；也不能把 Gateway `owner_id` 改成 manager 的 User ID。

### 12.2 创建来源不能继续授权

对已切换新模型的 Bot，下列事实都**不能**单独赋予当前控制权：

- `created_by == user.id`；
- legacy `is_creator=true`；
- Bot ID 尾部包含用户 ID；
- 旧 Gateway Bot owner_id 声明；
- 过去的 accepted transfer 回执。

旧 creator relation 可保留为历史事实，但新 authority Hook 不再将它当权限兜底。初稿中“部分 action 保留 creator 授权”的做法在此明确被替代；迁移必须对旧有合法访问逐项对账，不能无审计地把所有 creator 边变成 owner/manager。

创建者 A 转交后仍可管理，是因为显式 manager 边，而不是 created_by 仍匹配。如果新 owner 之后撤销 A 的 manager 边，A 不能再经重新 onboard、ensure-human 修复、Provider switch 或旧连接恢复管理权。

### 12.3 不改变资源身份和外部归属

- Bot ID、Group/Session 成员、资源 creator、历史消息作者、文件原始 owner 和收藏实体保持原样；仅当前 Human 对该 Bot 的 authority 改变。
- B 取得 Bot 对应的 BCS 历史访问能力，也仍受原资源角色/参与条件限制；不是把整个群无条件授予 B。
- A 保留 manager，因此大多数既有业务请求/连接可继续合法使用；必须失去 owner-only 能力，不应一刀切踢掉所有合法连接。
- Gateway 认证声明仍保证身份真实性，但其 owner_id 不能替代实时 BCS role 查询。不得伪造 claim 或要求 Backend 所有权随 BCS 转交同步改变；混合身份请求按 authority Hook 做代行检查。
- Backend/Engine/Provider 的绑定、runtime token、部署文件系统和相关凭据不更新、不轮换、不返回给接收方。
- “owner 变了”不等于原用户已失去全部系统访问；既有外部资产归属、Bot 凭据及合法 manager 权限仍独立存在。本功能不是离职清权或整机资产移交方案。
- 新基线的 FriendAuthSyncPort 是好友关系对外同步，非角色同步。初始化/授予/撤销 manager 或转交 owner 都不得触发它；既有好友同步的外部 Bot ID/owner 后缀寻址不因 BCS 转交改成新 owner。内部当前 owner 的通知/鉴权仍应走新 authority，不能把两类 owner 消费方混为一谈。

## 13. 应用、Core 与持久化边界

### 13.1 集中授权扩展点

在 `bcs-service-api` 声明 transport-neutral 的 `BotAuthorityHook`，由 bootstrap 注册实现。已有 `AuthorizationService` 目前只是声明，不能假定它已拦截全部生产用例。

Hook 至少承担：

- 单 Bot authority 解析，返回 `Owner/Manager/Denied` 及内部授权证据；
- 一批 Bot 的可代行判定与当前用户 controllable 集合；
- Human self / physical Bot View Actor 的解析；
- owner-only 的 transfer_ownership action，与普通 can_manage 区分；拒绝以历史 creator 兜底。

需要这些能力的 application/use-case 仅调用声明的 Hook，不复制角色边查询，也不保留 `created_by || role` 的兜底。资源角色判断仍由原 Group/Session 用例负责，Hook 不接管群生命周期或消息路由。

推荐代码职责：

| 层 | 拟改动 |
| --- | --- |
| `crates/contracts/bcs-domain` | Owner/Manager 枚举、ownership_version、transfer 状态/回执、合法角色边与审计类型；不引入 HTTP DTO。 |
| `crates/service-api/bcs-service-api` | Authority Hook、Core/Repo 契约、`MyBot`、BotManagementService、OwnershipTransferService、混合身份策略与连接持续授权合同。 |
| `crates/services/bcs-edge-permission` | 在独立模块实现 authority Core；只依赖 repo trait；隔离 owner/manager 与 runtime admission。 |
| `crates/services/bcs-edge-permission-store` | 严格、带 Result 的 authority 查询、反查名单、批量主体查询、原子 grant/revoke、ownership 转交及审计；SQL/事务归此层。 |
| `crates/application/v1/bcs-app-bot` | Authority Hook 的应用适配、manager CRUD、ownership API、mine 投影与 Bot 修改授权；通过 Core，不直连 DB。 |
| `bcs-app-group/session/invitation` 与 legacy application | 注入同一 Hook；替换相关 owner-only gate 和下游二次检查。 |
| HTTP / WS adapters | 新 routes/DTO、错误映射、携带真实调用者与视角；持续授权回调只调用 application contract。 |
| `crates/bootstrap/bcs` / service containers | 构造并注入共享实例，Memory/SQLite/MySQL 语义一致；无服务内环境读取。 |

新增严格 repo 读接口必须返回 `ServiceResult`，找不到授权与数据库失败是不同结果。禁止用旧 `Vec/bool` 方法的静默空值默认实现新方法；所有真实实现和 recording/noop doubles 必须显式更新，Noop 默认拒绝。

这一 Hook 是 Rule 12 的授权扩展点，不是一个新的基础设施 Plugin。现有 DbPlugin 已有事务能力；本方案不要求新增 Plugin API、动态权限引擎或通用 RBAC 框架。

### 13.2 ownership 应用与聚合事务

在 `bcs-service-api::application` 声明 OwnershipTransferService，由 Bot application 做身份选择、动作编排和回执投影。transport-neutral ownership Core 通过聚合 repository port 执行事务；实现在 `bcs-edge-permission-store` 独立模块中，拥有角色、Bot 版本与 transfer 的 SQL 一致性边界。

该聚合只覆盖 BCS Bot authority 数据，不接管 Bot runtime/Provider 的存储策略。Delivery 仅适配 routes/DTO/错误，不能查数据库或组装事务；ownership 初始化、manager 变更、转交和删除必须共享序列化边界。

bootstrap 注入同一权限实例和对应 store；Memory/SQLite/MySQL 都有 conformance，所有 recording/noop doubles 显式更新，未装配的 noop 默认拒绝。新 transfer repo 返回 Result，不复用 Vec/bool 的静默失败读取接口。

### 13.3 运行时隔离

必须同时修改两处行为：

- Admission 的 runtime grant 查询仅接受 `PermissionProfile/Rules`；Owner/Manager 不参与“任意边可调用”判断。
- A2A `AuthzContext.grants` 永不包含 Owner/Manager。public-default / collaboration-default 只产生原有运行时权限，不具备管理意义。

管理 Human 发起的 BCS 控制面代行操作，可以通过 authority Hook 进入原 owner 通道；这不等于把 Manager 作为任意 originator 可使用的运行时 grant。运行时调用者若没有独立调用权限，仍按原 admission 策略处理。

## 14. 撤权、缓存、长连接与通知

1. 管理权初版不使用跨请求正向缓存；每次新请求读当前授权事实。单请求内可合并查询，不能跨请求复用过期 mine 结果作授权。
2. grant/revoke 成功指持久化事务已提交。随后开始的权限校验必须反映新状态；已授权并执行中的普通业务操作不承诺全局回滚。
3. 管理员变更自身采用第 5.4 节事务内重新校验，保证失权管理员不能依靠检查/写入间隙重新授权自己。
4. Session token 不固化“我是 manager”；签发、connect/reconnect、每个入站控制操作都重新检查当前资源权限。
5. **已有连接的出站推送也必须受撤权约束**。在连接和 run fallback 绑定中保留真实用户、env、资源与选定视角。第一版按“每个受保护事件/帧 × 每个目标连接”在实际派发前调用应用层持续授权 Hook，重新读取 scoped authority；明确接受其逐目标数据库开销，预算见第 17.2 节。不使用连接级短时正向缓存，不按 run 首次绑定或 token TTL 复用授权；拒绝/存储故障时停止该连接受保护推送并关闭或失效绑定。
6. 不能只在本机广播一个撤权通知就声称完成：多实例、断线重连、Run fallback、Interaction replay、SSE 等仍能绕过，均按相同逐目标、逐次读取基线处理。排队或重新调度后的新派发不能沿用此前事件的授权结果；无敏感载荷的心跳不在此受保护推送范围内。后续若采用同批读取等优化，需显式评审其一致性并补撤权/故障 conformance，不能为性能悄悄改用 TTL 缓存。
7. 若用户仍通过本人参与、其他可控 Bot 或另一个独立有效授权访问该资源，只移除已撤销路径。显式绑定到被撤权 Bot 的视角不能自动切换为另一个 Bot。
8. 已发送字节、之前下载的文件不可能召回；当前校验通过后已经开始发送的帧也不承诺原子撤回。既有独立 bearer 分享链接继续遵循其原生命周期，不在此轮改造成管理权绑定链接。

ownership 转交还需遵守：

- 当前 role 不使用跨请求正向缓存，避免旧 owner-only 能力在其他实例继续有效；同请求可批量加载复用。
- 转交接受的内存/派生投影仅在提交后更新。投影更新失败不能回滚已提交 ownership，也不能返回“未转交”诱导重新执行；后续读取应可重建真实状态。
- 确认响应丢失时，重试 accept 或 GET 同一 transfer_id 获取持久回执，不依赖 UI 状态重复改变角色。
- 原 owner 因保留 manager，合法业务请求/WS 可以继续；只能失去 owner-only 能力，不应一刀切关闭全部连接。后续 manager 被撤销时按上述持续授权规则处理。
- 通知不进入数据库事务；初版以收件箱和详情为准，不因外部通知失败回滚或伪报角色变化。

## 15. 传播清单与合同兼容性

### 15.1 入口与消费方

实施时必须建立“入口 → 应用检查 → 下游检查 → 测试”映射，至少涵盖：

- Bot mine、get/query 保持原目录语义、patch、candidate/eligible/search perspective、legacy status/delete/chat 等 owner 操作，以及 manager/ownership API。
- Group list/detail/create/update/delete/participants，以及 originator、invite、workspace、消息与 Workbench chat/abort 入口。
- Session list/detail/launch/reactivate/update/delete/complete/participants/messages/collect/files/token。
- Friendship acting actor、request decider/cancel、invitation 和相关权限的下游 owner gate。
- Workbench HTTP/WS、session-bound token、connection registry、frontend delivery/run fallback 和 SSE 受保护通道。
- owner 初始化、onboard、ensure-human、Provider 注册/切换与当前 owner 通知消费方；好友审批接收人需解析当前 BCS owner，历史展示继续读 created_by，禁止机械替换全部创建来源字段。
- Memory/SQLite/MySQL repository、strict errors、Noop/recording fixtures、bootstrap 与独立测试装配。

Frontend 联动位置已发现：`src/frontend-nextgen/src/services/backendApi/collaboration/collaborationBotController.ts`、`services/workspace/identityService.ts`、`services/workspace/groupService.ts` 与 `collaborationPrivacy/mappers.ts`（后两组相对于同一 frontend-nextgen/src）。它们需接收并保留标签、展示 owner/manager、复用身份切换，失权 403 后刷新身份列表。不能再通过 Bot ID 后缀或前端 `created_by == me` 判断当前 owner 或筛掉 manager；`assets/TaskPanel/GroupDrillDown.tsx` 也存在这种视角推导，需要按显式可控集合改造。

这些前端变更由其模块按自身 AGENTS/契约实施；本轮不跨模块写业务代码。Backend 与 Gateway 若另有 owner-only 拦截，应在端到端接入验证中明确暴露，而非通过伪造 creator 绕过。

Frontend 同步接入转交按钮、收件列表、确认/拒绝/取消与唯一 pending 展示；不得把 ownership 转交描述成外部资产移交。

### 15.2 合同修订与兼容性分类

必须随实现更新：

- `api-contracts/v1/openapi/bots.yaml`、`domain-models.yaml`、`openapi.yaml`：mine item、管理 API、ownership/version、错误响应与安全元数据；新增 ownership-transfer fragment。
- `groups.yaml`、`sessions.yaml`、`session-files.yaml`、`connections.yaml`、`friendships.yaml`、`invitations.yaml`：owned-or-managed 与仍然保留的参与/角色限制。
- `gateway-principal/contract.md` 及相关 identity-policy 标注：不改变身份声明的真实归属，明确应用层动态代行授权。
- edge permission 旧设计的“任意有效边均为调用授权”描述，标注本文对新增 Owner/Manager 的隔离修订；保留新基线 ConnectService 的 request_auth 传递及好友同步独立边界。
- `CHANGELOG.md`、相关 crate `CONTEXT.md`、conformance 映射、HTTP endpoint 与 CLI coverage 清单。

兼容性分类：

- HTTP：mine 新字段是结构增量，但返回集合扩大是有意的行为变化；消费者不得继续把 mine 当作纯 owner 清单。通用 Bot 响应不变化。
- Rust Service/Repo API：`Page<Bot>` → `Page<MyBot>`、新必需方法、混合身份异步授权是编译期合同变更，更新所有消费者/实现/test doubles。
- Persistence：新增角色枚举、ownership_version、manager 审计、transfer 表与角色/pending 唯一约束，需要显式迁移；不是“无 schema 变化”。
- 认证：不改变认证来源、token 签名/owner claims；新 Hook 依赖当前可信 User 身份与实时 BCS role edges，不将签名 owner_id 等同当前 BCS owner，也不依赖待实现的 auth-plugin-chain。
- 无新的硬编码 URL、私有目录依赖或运行时配置项；管理名单是业务数据，不写入部署 TOML。

## 16. 迁移、发布与回滚

### 16.1 初始化与旧数据治理

1. 用新增迁移建立 schema/索引，停止不兼容旧版本的 authority 写入口；不得改历史迁移或 checksums。
2. 在维护窗口按当前受支持的 created_by / creator 数据对账，选出每个 Bot 的唯一初始 owner；保存来源及治理结果。不能因为 Bot ID 后缀匹配就自动认领。
3. 对 owner 缺失、主体不存在、多个冲突 creator 或迁移前已有特殊授权的数据，阻止自动迁移并列入人工确认清单。初始化版本 0 的对象不得开启转交；完整权限读切换以受支持的 live Bots 完成治理为门禁。
4. 需要保留的旧 creator 访问只有经过明确审核才转为 manager；不能把历史 creator 证据永久 OR 到新 Hook 中。
5. owner 边与 ownership_version=1 必须原子初始化，不同时 seed owner 的 manager 边。新 Bot 创建走同一 initialization contract，重复 onboarding/Provider switch 不改已初始化 ownership。
6. role 查询、mine、HTTP/WS、legacy 二次鉴权、好友审批接收人等当前 owner 消费方在同一兼容版本中切换；不保留无限期双读兜底。
7. 暂不转交的仅 Bot runtime/无 Human owner 对象不自动补造 Human；它们保留原 runtime 身份能力，但不冒充已初始化的可转交 Human-owned Bot。

发布演练覆盖 manager 授予/撤销/再次授予、ownership 转交后的角色变化、服务重启和双实例读取；不将 friend/default 边批量转换成 manager。

### 16.2 兼容与回滚

- owner/manager 角色与 pending 唯一约束、transfer schema 都是持久化合同变更；相关 Rust API、HTTP DTO、context 和 conformance 同步传播。
- 不允许老 runtime admission 读取 role edge 后把它当任意调用授权。新旧服务不能在未验证隔离下混跑。基础唯一键升级后，旧 SQLite ON CONFLICT 目标也不再兼容；同步更新冲突目标与 ID 回查 SQL。
- 尚无成功转交时，可在停服和数据对账后撤销迁移；存在 accepted transfer 后，不能只删除 owner 边并恢复 created_by 授权，否则会把控制权交回历史创建者。
- 有成功转交后的首选回滚是保留新 authority 读取、禁用新建/接受转交的兼容版本。极端降级需独立审核的数据导出/映射方案，不在应用代码里自动重写历史 created_by。
- 回退产品 ownership 应由当前 owner 发起一笔反向转交并由对方确认；不修改原 accepted 记录。

## 17. 数据库访问成本

### 17.1 控制面操作预算

以下是拟议实现预算，不是已经测得的性能结果：

| 操作 | 有界数据与往返要求 |
| --- | --- |
| ownership 查询 | 1 次 scoped 查询联查版本和当前 owner；解码完整性检查，不按所有 manager 逐条读取 |
| 新建请求 | 1 个 store 事务调用；只涉及 1 Bot、2 Human、1 owner、最多 1 pending、1 幂等记录与新行；SQL 步骤预算不超过 10，禁止扫历史全表 |
| accept 正常提交 | 1 个 store 事务调用；读取同上及固定角色边，最多修改 4 条角色边、1 Bot 版本、1 transfer；SQL 步骤预算不超过 12 |
| accept 发现 owner/version 失配 | 相同 scoped 加锁/校验，无角色边或 Bot 版本写；最多更新 1 条 pending 为 invalidated 后提交，SQL 步骤预算不超过正常 accept 的 12 步 |
| reject/cancel | 固定 1 Bot/1 transfer/必要身份检查，至多 1 transfer 终态写；不遍历 manager 集合 |
| 收件/发件分页 | count + page 至多 2 次 scoped 查询，最多返回 100 条；不逐条额外查 Bot/权限，使用名称快照与批量投影 |
| mine | owner/manager 一次集合查询加现有批量 Bot hydration；不能每个 Bot 单独查 owner/transfer |

DbPlugin 的一次 transaction 调用内部仍可能有多条 SQL 往返，不能将其报告为“只有一条 DB 查询”。索引 count 的实际扫描行数可能随收件箱历史增长；验证深分页/大量历史的 EXPLAIN，不能把返回 100 条说成只扫描 100 行。

锁竞争超时返回可重试基础设施错误；不无限自动重试或忙循环。客户端使用同一 client_request_id/transfer_id 安全重试。跨 Bot 并发、两个用户互相转交、数据库持续失败路径都需有查询次数与锁等待验证。事务内不做网络调用，不扫描 Group/Session 历史。

### 17.2 WS/SSE 出站持续授权预算

**基线选择：每个受保护事件/帧对每个实际目标连接新增 1 次 scoped authority 读取，无跨事件、连接 TTL 或 run 绑定级的正向缓存。** 这是第 14 节撤权语义的显式成本，不将首次连接鉴权当成后续推送的充分条件。

| 路径 | 新增角色读取预算与限制 |
| --- | --- |
| 单个连接的受保护推送 | 1 次 scoped authority 查询，按 env、真实 User、目标资源及选定视角查当前证据；只投影决策所需数据，不拉整个 mine/manager 名单或聊天正文 |
| 单事件广播到 C 个连接 | 基线新增 C 次角色查询，不是一次；同一用户的多个连接也不跨连接缓存结果 |
| Run fallback / Interaction replay / SSE | 每个重新派发的受保护目标同样读取一次；已被拒绝或失效的绑定不能改走 fallback 再试一次绕过拒绝 |
| 授权查询失败 | 此连接停止受保护派发并关闭/失效；不放行旧结果、不在每条后续帧上无限重试。未查证的受保护数据不能进入可发送队列 |

若每秒 E 个事件、每事件 C 个连接，新增角色查询约为 `E × C` 次/秒。例如 E=50、C=20 时是 **1,000 次/秒**，还不包含原有资源读取。这是容量示例而非已测吞吐或上线承诺。

上述 SQL 预算针对 SQLite/MySQL 装配，Memory 实现只计等价 repo 调用，不声称产生数据库往返。这里的“1 次”只预算本功能新增的 scoped authority SQL。Hook 为完整校验成员关系、资源角色或视角而调用现有 Group/Session 服务时，必须追踪到实际 store/cache，把其真实查询次数另计；不能将一次 Hook 调用误报成整个授权只有一次 SQL。显式 Bot 视角做精确角色读取；Human 可控参与者的集合判定用资源内批量/exists 查询，不按每个 Bot 发起额外查询，不遍历全部 owned/managed Bot。读取行数可能随相关参与者/索引候选数增长，输出一个布尔结果不代表只扫描一行。

派发并发和等待队列必须有上限并服从共享连接池预算，队列延迟后的实际派发重新授权；不在数据库事务或锁内等待网络发送。持续数据库故障时失效绑定并采用有界重连退避，不能随积压事件无限增加数据库请求。

实施验证必须覆盖单连接、目标最大 fan-out、连续帧、重放/fallback 与持续故障，记录新增/原有 SQL 次数、扫描候选行、连接池等待和关闭后的查询停止情况；并验证每次新的派发读取都能看到已提交撤权。若目标负载下该基线不可接受，必须先评审同批读取等保持撤权语义的方案并更新预算与测试，不能在实现中擅自放宽为 TTL 缓存。

## 18. 验收与验证要求

### 18.1 管理权限验收（AC01—AC21）

| 编号 | 场景与预期 |
| --- | --- |
| AC01 | owner A 授予 B 管理 Bot X；B.mine 的 X 返回 access_relation=manager，A.mine 的 X 返回 access_relation=owner。 |
| AC02 | owner/manager 重叠、多个管理边历史、Human row：不重复，标签稳定。 |
| AC03 | owned 与 managed 时间交错、多页、空页、全部现有 filters：先并集过滤再计数分页。 |
| AC04 | B 以 X 作为 view 查询 Group、Session、消息，与 A 以 X 查询结果与 scope 相同。 |
| AC05 | 不传 view 仍为 Human；session-only 不变 formal member；空群读取不写 Session。 |
| AC06 | X 不在某 Session，即使 X 在父 Group，B 也不能越过原 owner 条件读取消息/文件。 |
| AC07 | X 是 worker：B 不能改群管理设置；X 是 driver/manager：B 取得 A 原有对应权限。 |
| AC08 | Bot patch、好友审批/取消、invitation、launch/acting creator、收藏、文件 mutation、workspace 与 legacy 下游 gate 均完成 owner/manager 对照测试。 |
| AC09 | manager 可增删 manager、自撤权；owner 不可经 manager API 删除；并发互撤按事务次序决定结果。 |
| AC10 | 无权限用户、Bot-only、App-only、AccessKey-only、其他 Human 视角、跨 env 一律不能借管理 API 提权。 |
| AC11 | 默认 profile wildcard、public、friend、反向边、Bot→Bot、间接好友都不构成 manager。 |
| AC12 | 仅有 manager 时不出现在 friend 列表、不进入 A2A runtime grant；单独撤好友不影响 manager，反之亦然。 |
| AC13 | 授予/撤销幂等；revoke 后再次 grant 真正恢复 approved，不返回假成功。 |
| AC14 | 边写/审计写/提交失败全回滚；读或解码失败报错，不返回空 mine/空列表伪装成功。 |
| AC15 | 撤权后新 HTTP、旧 token 重连、已连接 WS 入站/出站、Interaction replay、run fallback/SSE 不再通过已撤销路径；双实例同样成立。连续帧/同 run 不复用旧授权，读取失败停止派发并失效绑定，不经 fallback 绕过。 |
| AC16 | B 还有独立合法参与关系时，撤销 X 的管理权不剥夺该独立访问；显式 X 视角仍拒绝。 |
| AC17 | User+Bot owner claim 不等但当前 owner/manager 有效时合法代行；二者均无效时拒绝；不伪造 owner claim。 |
| AC18 | Bot/Human 删除后同 ID 重建不恢复旧管理边；重启持久化结果不变。 |
| AC19 | 转交并撤销原 owner 的 manager 后，所有 action 均不能因旧 is_creator、created_by、Bot ID 后缀或重新 onboard 恢复权限。 |
| AC20 | 管理审计记录不可通过 Connect approve/reject/cancel 操纵；操作者记 Human，非伪造 creator。 |
| AC21 | mine 每个 item 必须序列化非空 access_relation，值仅为 owner/manager；覆盖多页、物理 Bot 与本人 Human、创建者不同但当前 owner、创建者相同但仅 manager 的情况；无权 Bot 不返回，OpenAPI required/enum 与 DTO 一致。 |

### 18.2 ownership 转交验收（OT01—OT22）

| 编号 | 验收 |
| --- | --- |
| OT01 | A 发起给 B，接受前 A 仍 owner；B 原本无权则不能读群、Session、文件或 manager 列表。 |
| OT02 | B 接受后唯一 owner=B，manager 包含 A，B 原 manager 边被撤销，其他 manager/friend/runtime 边不变。 |
| OT03 | 转交确认后再次请求 mine，A 的 Bot item 返回 access_relation=manager，B 返回 access_relation=owner，且各自去重；created_by、Bot ID、历史消息与外部资产不变。 |
| OT04 | 同 Bot 并发发给 B/C，只生成一笔 pending；数据库唯一性直接并发测试通过。 |
| OT05 | 同 client_request_id 同 payload 重试返回同一请求；不同 payload 冲突，终态重放不再建单。 |
| OT06 | accept 重试只改变一次 owner/version；后续 B→C 后重放旧 accept 不把 owner 改回 B。 |
| OT07 | accept/cancel/reject 并发只能有一个终态，失败方不改边。 |
| OT08 | 截止时间前/等于/之后行为一致，时钟以数据库为准；过期请求不阻塞合法新建。 |
| OT09 | GET/list 不写库；expired 的筛选、分页、total 与有效状态一致。 |
| OT10 | 非接收人不能接受/拒绝；manager 不能发起；Bot/App/AccessKey-only 不能操作。 |
| OT11 | 跨 env、缺失/不合法 Human、自转交、Human 作为目标 Bot 均拒绝。 |
| OT12 | owner/version 失配和 A→B→A 的旧快照：非过期 pending 在角色完整的前提下提交为 invalidated/owner_changed，返回 409，角色与 Bot 版本不变，释放槽位并允许合法新建；同接收人重试仍为 409。物化/提交失败则回滚、500、槽位不释放。未初始化或损坏的 authority 不伪造失效结论。 |
| OT13 | 任一步边写、版本 CAS、回执写、提交失败均无部分角色切换；多实例读不到已提交的无 owner 状态。 |
| OT14 | A 转交后可作为 manager 继续管理，但不能发起下一次转交；新 owner 可以。 |
| OT15 | 撤销 A 的 manager 后，created_by/is_creator/旧 claim/Bot ID 后缀不能恢复权限。 |
| OT16 | 再次 onboard、ensure-human、Provider switch/re-register 不重置 owner，不改变历史来源。 |
| OT17 | Bot 删除与 accept、Human 删除/失效、Bot ID 重建均不使旧 pending/accepted 重获执行权。 |
| OT18 | 接收人仅可看转交最小信息，其他 manager 不能枚举请求；历史回执不是当前权限。 |
| OT19 | A 的合法业务 WS 因保留 manager 可继续；撤销 manager 后跨实例连接及 fallback 正确停止授权。 |
| OT20 | owner/manager 不进入 friend/runtime grants，public/default wildcard 不能转交。 |
| OT21 | 事务重试、重启、响应丢失仍返回持久结果；不调用 Backend/Engine/Provider 修改接口。 |
| OT22 | 大 manager 集合和大量历史请求下，无逐 manager/Bot 的 N+1；控制面正常/最大页/失败路径满足预算。WS/SSE 按事件×连接测量新增 scoped 查询和既有资源查询，覆盖最大 fan-out、连续帧及故障后查询停止；不以缓存旧权限降低成本。 |

### 18.3 测试分层与执行入口

- Domain：枚举 round-trip、管理边形状、禁止主体与 owner/manager 标签优先级。
- Authority/Repo conformance：Memory、SQLite、MySQL 同一套授权/撤权/转交/并发/失败合同；MySQL live run 验证完整迁移、生成列唯一键、大小写身份、事务锁，不能用静态 SQL 测试代替。
- Application：Bot、Group、Session、Invitation、文件与 shared launch 的 owner/manager 成对测试和 ownership 用例，断言 Hook/Repo 确实被调用。
- Delivery：V1/legacy HTTP envelope、共享 ownership_not_initialized=409、owner_changed 已提交失效/重试的错误码、混合身份、分页与 WS/SSE 连续授权合同；逐目标推送查询计数、跨帧撤权与持续故障 fail-closed 断言必需。
- Integration：真实本地持久化、重启、双服务实例、好友/manager 并存、转交并发/回滚及撤权完整用户故事；前端身份和转交收件交互。
- 架构：依赖边界、delivery 仅调 application、core 无 transport/DbPlugin、授权检查通过注册 Hook、配置/环境访问约束和 conformance entries。

实现后的主要执行入口（不是本次已经运行的结果）：

```bash
cargo test --manifest-path src/bcs/Cargo.toml \
  -p bcs-service-api -p bcs-edge-permission -p bcs-edge-permission-store \
  -p bcs-app-bot -p bcs-app-group -p bcs-app-session -p bcs-app-invitation
cargo test --manifest-path src/bcs/Cargo.toml \
  -p bcs-bot -p bcs-group -p bcs-session -p bcs-api-http -p bcs-http -p bcs-ws
(cd src/bcs && bash scripts/ci/arch-check.sh)
python3 src/bcs/scripts/validate_openapi_contract.py
cargo test --manifest-path src/bcs/Cargo.toml --workspace
scripts/ci/singlebox_coverage.sh
python3 scripts/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir scripts/.dependencies/coverage/singlebox/reports
```

Singlebox 产物必须随后使用 verifier 检查上述 reports 目录；保留现有 BCS 用户故事、覆盖率与 endpoint/CLI 覆盖门禁，不能为新 endpoint 降低基线。MySQL live conformance、双实例和所有未能执行的项目必须在实现 PR 中逐项说明原因。

### 18.4 文件规模约束

2026-09-22 在代码基线 `08bb8f79cf` 重新以 `wc -l`（换行符计数）核对：`bcs-app-group/src/lib.rs` 2,970 行、`bcs-app-session/src/lib.rs` 1,818 行、`bcs-app-invitation/src/lib.rs` 1,153 行、`bcs-edge-permission/src/lib.rs` 3,797 行、`bcs-edge-permission-store/src/lib.rs` 2,293 行。以上文件在当前 HEAD 与基线内容相同；edge-permission 的旧值 3,641 已更新为 3,797，不能把旧基线行数标作当前核对结果。实施时按授权、投影、操作与测试责任拆分将被修改的超限文件，确保每个新增/修改 source file 不超过 1,000 行；不能只新建一个 helper 后继续修改超限 lib 并宣称通过。不做无关格式化，不运行全局 cargo fmt。

## 19. 文档验证与评审入口

本文已合并 manager 权限与 ownership 转交，并明确 mine 的必填 access_relation。2026-09-22 按 `2026-09-18-bot-manage-permission-design-review.md` 修订如下：

| 审阅项 | 处置 |
| --- | --- |
| 1. 调研基线漂移 | fetch origin/dev 后按当前 merge-base 08bb8f79cf 重新核对第 2 节；补入好友同步/request_auth 变化、持续授权当前路径；更新第 18.4 节行数。不是把尚未合入本分支的新 dev 当代码基线。 |
| 2. accept 版本失配链路 | 第 10.2 节明确 pending→invalidated 的事务提交、pending 槽位释放、409、重试及存储失败回滚；第 10.1 节也清理失效旧请求，避免必须依赖接收人尝试确认。OT12 覆盖这些分支。 |
| 3. 持续授权成本 | 第 14/17.2 节明确逐事件×连接的 scoped 读取基线，不设跨帧正向缓存；额外计入现有资源查询，要求 fan-out/故障成本及撤权 conformance。 |
| 4. manager API 错误码 | 第 6 节明确 HTTP 409 / ownership_not_initialized，并引用第 11.2 节共享映射，保留资源特有错误区别。 |
| 5. manager 再授权风险 | 保留待决，不据审阅意见擅自收紧或视为已确认；第 1.3 节与本节将其标作进入实施前最优先的产品/安全决策。 |

本轮验证包括基线/祖先关系、证据文件差异及当前代码阅读、5 个源文件行数、全部 6 个 JSON 示例、43 个验收编号、源码路径、章节引用、Markdown 围栏/空白与 git diff。没有修改业务代码、OpenAPI schema、配置或数据库。

Cargo、MySQL live conformance、双实例、WS/SSE E2E、负载与 Singlebox 未运行：本轮只是文档修订和静态事实复核。第 17 节是实现预算，不是压测结果；不能据此声称运行时行为或吞吐已验证。

实施前必须由需求方及权限安全评审明确记录 manager 再授权的接受/收紧决定，并评审第 1.3 节其余默认值、第 5/10/16 节数据约束/事务/迁移及第 17.2 节热路径预算。文档审阅不替代产品授权；确认设计后再形成实施计划，本文不是业务代码实现授权。
