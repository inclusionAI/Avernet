# 外部应用（App）Actor 调用授权契约 · Spec 草案

Nov 23, 2026 · @Hayden

让  复用当前 BCN 插件框架

本 spec 为外部业务应用定义一种只发起调用的网络 Actor（App），并规定它与 Agent、Group 之间的授权契约。

## 1. 背景、场景与目标

### 背景

BCN 的网络 Actor 目前只有 Human 和 Bot 两类。一个 Actor 能否调用另一个，由边权限决定：有向授权边经申请、审批、撤销流程管理。Group as an Agent 之后，Group 也能像 Agent 一样对外提供服务。

### 场景与问题

普通业务应用需要调用 Agent 或 Group 完成任务，并获取结果、订阅进度、必要时取消。它只具备 A2A Client 能力：只发起调用，不是 Agent，也不接受调用。现在接入这类应用有三个问题：

1. **没有合适的身份。** 业务应用只能借用某个 Bot 的身份，或使用 `/services` 的静态 service-key。Gateway 能认证出 App 身份，但 App 不是网络 Actor，边权限无从作用于它。
2. **调用范围不受治理。** 借用 Bot 身份会带上该 Bot 的全部权限；service-key 的 Group 白名单写在静态配置里，没有申请、审批和撤销，`bound_groups` 为空时还能调用任意 Group。
3. **任务归属不统一。** chat run、service session 和分布式 Gateway 各用一套调用方标识，service-key 的标识还会随 key 轮换而变化。

### Goals

1. 让外部应用复用当前 BCN 认证插件框架，以独立身份（App Actor）接入网络。
2. 复用边权限机制，用 App→目标的有向边管理 App 能调用哪些 Agent 和 Group。
3. 明确 App 对自己的任务能做哪些操作，以及撤销授权后的行为。

### Non-Goals

- App 不作为 A2A Server，不接受任何 Actor 的调用。
- 不改变 Group 内成员之间的协作授权。
- 不定义 Bot 调用 Group-as-Agent 的授权，但模型为此留有余地。
- 不涉及存储与接口的实现设计。

## 2. 术语

本文把外部应用称为 App，把它获得的授权称为 invoke 边。

| 术语 | 含义 |
| --- | --- |
| App | 外部应用 Actor：有独立身份和凭证，只发起调用，不接受调用。 |
| 目标 | App 可调用的 Agent（Bot）或 Group。 |
| invoke 边 | 从 App 指向目标的有向授权边，表示允许 App 向该目标提交新工作。 |
| 新工作 | 会让目标继续执行的输入：新会话、新任务、对 input-required 的回复、同一 context 的新一轮。 |
| 任务归属 | 任务创建时记录的发起方，即 App 的稳定 actor id；读取、订阅、取消据此判断。 |
| 撤销 | 把一条 invoke 边置为 revoked，只影响新工作。 |
| 停用 | 让 App 身份整体失效，影响它的全部请求。 |

## 3. 现状

外部调用方在仓库里已有三种形态，但都不是网络 Actor，也都不受边权限约束。以下基于 avernet `dev` 分支 commit `ae17393`。

- **三种既有形态。** Gateway 的 `AppPrincipal`（BCS 侧为 V1 `AuthenticatedAppIdentity`）；BCS 的 `CallerContext::Integration`；`/services/{group_id}` 使用的 `X-BCS-Service-Key`。2026-08-02 的 principal verifier 设计只做认证，App 在该设计中不成为 BCS 业务 Actor，业务语义留待后续设计；本 spec 补上 App 调用 Agent 与 Group 这一部分。
- **service-key 缺少治理。** `bound_groups` 为空即可调用任意 Group；`api_keys` 为空时接受任意非空 key，而 local 与 example 配置都为空。`caller_principal` 由 key 的哈希生成，换 key 后旧会话不可达。
- **`/services` 未校验 Bot 调用方与 Group 的关系。** `post_invocation` 对 Bot token 调用方只做 Provider scope 检查。外层网关是否另有限制，待确认。
- **类型靠 id 前缀推断。** `actor_kind_of` 把非 `human_` 前缀一律视为 Bot；两端都是 Bot 时，connect 会自动建反向 request 和反向边，并为发起方建 default profile。非测试代码中另有 60 多处 `starts_with("human_")`。
- **可达性判定放行 public，且对称。** A2A chat 的 `ensure_target_reachable` 对 public Bot 直接放行，对 protected、private 目标使用对称的 `are_friends`。
- **`Integration` 分支有休眠的宽松授权。** state-machine 会话历史对它直接放行；它的 `client_id` 被当作 `from_bot_id`，与 bot\_uuid 共用命名空间。生产代码未构造该变体，所以目前不生效。
- **分布式 Gateway 已有接入点。** `/a2a/{group}` 按 `(tenant, principal)` 与创建时固定的 group 判定任务归属，访问他人会话返回 not found。group 访问只在创建 session 时检查，`DistGroupDirectoryPort` 尚无生产实现。

## 4. 已确定的方向

已确定：App 是只发起调用的网络主体，它的调用权限经复用的连接机制、以有向边授予。

1. 新增外部应用(app)这一 Actor 类型，具备独立身份与认证凭证。
2. App 只发起调用，不作为 A2A Server 接受任何 Actor 的调用；它可以接收响应、获取结果、订阅自己任务的事件。
3. 成为网络 Actor 不等于可以调用所有节点。它能调用哪些 Agent、哪些 Group，以及允许的操作，都纳入网络治理。
4. 复用 边权限 机制：用 App→目标的有向边表达授权，复用申请、审批、撤销流程。
5. 建立连接时不产生反向调用授权。“不接受调用”是类型约束，不能被任何授权边绕过。
6. 获准调用 Group，不等于获得群成员身份、内部历史或管理权限，也不等于获得对群内各 Agent 的直接调用权。
7. 访问具体 Task 时，还要检查任务归属。

## 5. 契约一：身份、认证与类型约束

App 是独立的 Actor 类型：认证复用 Gateway App 凭证，“不接受调用”在解析调用目标时按类型强制。

身份与认证：

- App 的身份是稳定的 actor id，与凭证分离；轮换凭证不改变 actor id，也不影响已有任务的归属。
- 认证复用 Gateway App 凭证：BCS 从 `X-Avernet-Principal` 验证出 `AuthenticatedAppIdentity`，不另发一套 key。这与 Bot authority 设计“复用认证协议，不新增一套 service token 协议”一致。

类型约束：

- App 不能成为任何授权边的 to 端，也不能被解析为调用目标；App→App、App→Human、Agent→App 的边一律拒绝。
- 约束落在解析调用目标这一处，让 App 在类型上不可能被解析为目标。优先使用独立的 App 注册表；若放入 `bcs_bots`，所有读取 Bot 的路径都要按 kind 过滤。
- Actor 类型以注册表为准，不按 id 前缀推断；无法确定类型时拒绝，而不是当作 Bot。
- connect 不为 App 创建反向 request、反向边，也不为 App 建 default profile。
- App 不能成为群或会话的参与者，也不出现在 Bot 发现与协作候选结果中。

## 6. 契约二：Group 作为目标端点（建议）

建议：Group 直接作为 invoke 边的目标端点，不另建代表它的 Agent 身份。

&#91;embedded content: 授权关系 · App、Group 入口与成员 Bot\]

App 的 invoke 边只到 Group 入口；群内对成员的调用沿用 Group 自己的授权，App 不能直接调用成员。

理由：

- **现有寻址都按 group\_id。** `/a2a/{group}`、`/services/{group_id}`、`bound_groups`、Group Card 的 `local://groups/{id}` 与 `groupId`，以及 `DistGroupDirectoryPort::resolve(scope, group)` 都是如此。边直接挂在 group 上，无需映射，service-key 迁移也一一对应。
- **边模型已为 Group 预留。** 边权限改造 spec 的 D13 留了 `G→A` 范围边、`subject_type=group` 与 `gpp_<gid>_collaboration` 命名空间。
- **代表身份代价更大。** 一个对象会有两个身份，生命周期与可见性都要同步，而 D6 刚去掉双写。代表它的 Bot 还会进入所有 Bot 代码路径，每处都要加例外，漏一处就是一个绕过口。
- **Card 只做展示。** Group Card 是 store 生成的展示数据，目录投影“不授予任何权限”。Card 管展示，边管授权。

备选方案是为 Group 绑定一个对外 Agent 身份，好处是复用全部 Bot 机制，代价如上，未采纳。

前提：

- 边的两端带明确类型（from/to kind 或等价约束），以注册表为准。`edge_grants.to_id` 的列注释现为“bot uuid”，group id 也不保证以 `bcs_grp_` 开头，CLI 示例里就有 `svc-group-001`。
- 审批人复用 `can_manage_group`：driver、originator、ManagerWorker 群的 Manager 成员，以及当前能管理这些 Bot 的 Human。建议只接受 Human 审批。
- 可调用的 Group 与 Card 发布条件一致：Normal、`record_status=active`、策略为 manager\_worker 或 state\_machine；Chat 与 DM 不可作为目标。
- 日后 Bot 要调用 Group-as-Agent 时，增加 Bot→Group 边即可，身份模型不变。

## 7. 契约三：调用权限与任务归属（建议）

边上只有一种权限 invoke，即允许 App 向目标提交新工作；读取、订阅、取消由任务归属决定，不单独授权。

| 操作 | 授权依据 |
| --- | --- |
| 新会话、新任务（SendMessage / SendStreamingMessage） | App 有效 + invoke 边有效 + 目标可用 |
| 继续：回复 input-required，或在同一 context 开新一轮 | 同上 + 任务归属 |
| GetTask / ListTasks | App 有效 + 任务归属 |
| SubscribeToTask | App 有效 + 任务归属 |
| CancelTask | App 有效 + 任务归属 |
| Push 通知配置 | 首版不支持，见第 10 节 |

- **任务归属绑定 App 的稳定 actor id。** principal 写作 `app:<actor id>`，不随凭证轮换变化；chat run、service session 与分布式 Gateway 统一这种写法。
- **访问他人的任务一律返回 not found。** 分布式 Gateway 已这样做，`get_service_session` 现在返回 403 `caller_principal mismatch`，需统一。经由 Group 访问时，还须经由创建该会话的同一 group。
- **`on_behalf_of_opaque` 只做标注。** Gateway 传来的终端用户句柄不参与归属与授权判断。
- **App 必须有边才能调用。** `public_default` 与同群成员默认可调的 `collaboration_default` 都不适用于 App，否则 App 的可调用范围会随别人调整可见性而扩大。
- **自动批准单独设开关。** 目标侧可开 App 专用的自动批准，默认关闭，也不跟随 `friend_approval=auto`；自动批准同样生成可撤销、可审计的边。
- **App 的边引用目标侧的非 default profile。** D12 以 `grant_ref_id == target.default` 判定好友，引用 default 会让 App 被当成好友。领域里已有 `RequestKind::PermissionProfile`，但只有存储映射、没有服务流程；Group 侧 profile 需要 D13 预留的 `subject_type=group`。
- **细粒度规则由目标执行。** BCS 只强制“能否调用”。对 Bot 目标，技能或工具级规则通过 `AuthzContext.grants` 交给目标 Agent 执行；Group 目前只有 `group.execute` 一个 skill。

## 8. 契约四：撤销与停用（建议）

撤销 invoke 边只停止新工作，在途任务照常完成；需要全部切断时停用 App。

| 操作 | 撤销边之后 | 停用 App 之后 |
| --- | --- | --- |
| 新会话、新任务 | 拒绝 | 拒绝 |
| 继续（回复 input-required、同 context 新一轮） | 拒绝 | 拒绝 |
| GetTask / ListTasks | 允许，至结果保留期结束 | 拒绝 |
| SubscribeToTask（含已建立的流） | 允许，已有的流推到任务结束 | 拒绝，并关闭已有的流 |
| CancelTask | 允许 | 停用时选择是否一并取消在途任务 |

- **“继续”算新工作。** 否则一个任务可以靠反复进入 input-required，把撤销前的授权一直延续下去。卡在 input-required 的任务靠取消或超时结束。
- **撤销不追溯在途任务。** 这与现有 Bot 调用方一致：`get_run`、`wait_run`、`cancel_run` 只查 `ensure_run_owner`，不重新查边。
- **每次提交输入都查边。** 分布式 Gateway 目前只在创建 session 时检查 group 访问，需改为每次准入都检查，否则撤销后仍能在旧 session 里开新任务。
- **准入查边不缓存“已授权”的结果。** 否则撤销要等缓存过期才生效。
- **目标侧批准人可直接撤销。** 复用 `request_kind=revoke` 的流程。

## 9. 契约五：命名（建议）

类型名用 `app`（`ActorKind::App`），`system` 留给平台内部身份；产品文档可继续称“外部系统”。

| 位置 | 外部应用 | 平台内部 |
| --- | --- | --- |
| Gateway `PrincipalType` | `APP = "app"` | — |
| BCS 事件 envelope 的 `actor.type` | `app`（`Integration` 调用方映射到这里） | `system`：自动恢复、超时、reconciler |
| Bot authority 审计 `AuthorityActorKind` | `service`（受信 Gateway App） | `system`：自动关闭等决议 |
| `permission_profiles.created_by` | — | 默认 profile 写 `system` |

- 用 `System` 会与后三处撞名；`app` 与 Gateway、事件里的现有叫法一致。
- `CallerContext::Integration` 改名为 App，分布式 Gateway 的 `integration:` 前缀改为 `app:`。
- `AuthorityActorKind::Service` 已写入审计记录，保持不变；它表示同一个 App 在管理面的操作，见第 10 节。

## 10. 其他边界

App 的授权只覆盖它直接调用的目标和自己的任务，不沿调用链传递，也不扩展到管理面。

- **传递调用。** 在 App→Group→成员 Bot→外部 Bot Y 这条链上，成员调用 Y 靠的是成员到 Y 的边，App 不因此获得对 Y 的权限。`AuthzContext.originator` 记为 App，Y 一侧可用 `originator_policy` 加以限制。
- **订阅。** App 只能使用任务级的 `SubscribeToTask`。BCS 的 group 级 webhook 订阅属于 group 管理权限，且会带出其他调用方任务的事件，不因 invoke 边而开放。
- **caller identity。** caller identity spec 规定只有 `CallerContext::Human` 能作为调用者身份的起点。App 调用开启了 caller identity 的 Bot 时直接拒绝（caller required），不退回 Bot owner 身份执行。
- **push。** 首版不支持。日后支持时，回调地址登记在 App 上，不接受消息体里随意给的 URL，App 仍无需成为 A2A Server。
- **管理面。** App 的管理权限只来自 `bot_authority.trusted_apps` 配置，与 invoke 边互不推导：有 invoke 边不代表能做 manager 同步等管理操作，反之亦然。

## 11. 兼容与迁移

上线 App 之前，要先收紧 `Integration` 分支，并把 service-key 与 `/services` 纳入同一契约，否则治理可被绕过。

1. **衔接既有设计。** 2026-08-02 principal verifier 设计把 App 的业务语义留给后续设计，本 spec 补上 App 调用 Agent 与 Group 这一部分。该设计里“App 在本设计中不成为业务 Actor”的范围说明，需加注指向本 spec。
2. **收紧 `Integration` 分支。** 取消 state-machine 会话历史对它的直接放行，`client_id` 不再与 bot\_uuid 共用 `from_bot_id`。生产代码尚未构造该变体，现在改名几乎没有存量成本，最好赶在分布式 Gateway 接入认证之前。
3. **修正 connect 的类型判定。** `actor_kind_of` 改为以注册表为准，未知类型拒绝；只有两端都是 Bot 时才建反向边。
4. **迁移 service-key。** 每个 `ApiKeyEntry` 对应一个 App，加上它绑定的每个 group 各一条 invoke 边。`bound_groups` 为空的 admin key 不自动迁成“全部可调”，逐个确认。迁移完成后退役 `X-BCS-Service-Key`，并移除 `api_keys` 为空时接受任意 key 的行为。
5. **明确 `/services` 对 Bot 调用方的授权依据。** 否则 Group 的对外调用会留一个不受治理的入口。
6. **接入分布式 Gateway。** `DistGroupDirectoryPort` 的生产实现接入 App→Group 的边检查，并在每次准入时执行（见第 8 节）。

## 12. 待决问题

以下问题定下来之后，才能进入实现设计。

- [ ] 确认第 6–9 节的建议：Group 作为目标端点、invoke 单一权限与撤销语义、`app` 命名。
- [ ] driver 或 originator 是 Bot 的 Group，是否允许 Bot 自己批准 App 接入（倾向不允许，只接受 Human）。
- [ ] 撤销后对 input-required 的回复：直接拒绝（当前建议），还是留一个宽限期。
- [ ] App 与 tenant 的关系：边只区分 env，而分布式 Gateway 的任务归属是 `(tenant, principal)`；一个 App 是否固定属于一个 tenant。
- [ ] 谁代表 App 发起和撤回接入申请：App 凭证本身，还是 App 注册信息里的 owners。
- [ ] service-key 迁移中，`bound_groups` 为空的 admin key 如何处理。

## 13. 参考

以下链接固定在 avernet `dev` 分支 commit `ae17393`。

设计文档：

- [principal verifier 设计（2026-08-02）](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/docs/superpowers/specs/2026-08-02-bcs-gateway-principal-verifier-design.md)
- [边权限改造 spec（2026-08-18）](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/docs/superpowers/specs/2026-08-18-friend-edge-permission-reform.md)
- [事件订阅 webhook 设计（2026-08-18）](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/docs/superpowers/specs/2026-08-18-bcs-event-subscription-webhook-design.md)
- [Bot authority 设计（2026-10-10）](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/docs/superpowers/specs/2026-10-10-bot-authority-simple-design.md)
- [Gateway A2A inbound plan（2026-11-17）](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/specs/2026-11-17-gateway-a2a-inbound/plan.md)
- [Gateway auth 设计（2026-07-21）](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/gateway/docs/2026-07-21-auth-design.md)
- [caller identity spec（2026-09-16）](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/docs/specs/2026-09-16-caller-identity-session-container/spec.md)
- [BCN A2A 接口参考](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/docs/bcn-a2a-api-reference.zh-CN.md)
- [BCSFuse 目录集成](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/docs/bcsfuse-catalog.md)

代码：

- [connect.rs](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/crates/services/bcs-edge-permission/src/connect.rs)：actor\_kind\_of、Bot↔Bot 反向边
- [a2a\_chat/mod.rs](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/crates/services/bcs-message-flow/src/a2a_chat/mod.rs)：ensure\_target\_reachable、bot\_caller\_id、ensure\_run\_owner
- [routes/services.rs](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/crates/adapters/http/bcs-http/src/routes/services.rs)：post\_invocation、get\_service\_session
- [service\_key.rs](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/crates/adapters/http/bcs-http/src/service_key.rs)：ApiKeyEntry、caller\_principal
- [sessions/history.rs](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/crates/adapters/http/bcs-http/src/routes/sessions/history.rs)：Integration 的会话历史授权
- [bcs-app-dist-gateway/src/lib.rs](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/crates/application/bcs-app-dist-gateway/src/lib.rs)：scope\_of
- [port/dist\_agent.rs](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/crates/service-api/bcs-service-api/src/port/dist_agent.rs)：DistGroupDirectoryPort
- [types/bot\_authority.rs](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/crates/service-api/bcs-service-api/src/types/bot_authority.rs)：AuthorityActor
- [types/authorization\_snapshot.rs](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/crates/service-api/bcs-service-api/src/types/authorization_snapshot.rs)：management\_actor\_ids
- [card\_template.rs](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/crates/services/bcs-group-store/src/card_template.rs)：Group Card 模板
- [014\_edge\_permission.sql](https://github.com/inclusionAI/avernet/blob/ae173932c11685883fe49326e99f57a72f479cc5/apps/bcs/migrations/mysql/014_edge_permission.sql)：edge\_grants 与 permission\_profiles
