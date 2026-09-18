# BCS Bot `manage` 边权限与委托管理

- 日期：2026-09-18
- 状态：Draft / 已按技术评审修订，可生成实施计划；第 2 节产品假设待确认，不是业务代码实现授权
- 负责模块：BCS
- 本轮产物：修订设计 spec 与配套实施计划；不修改业务代码
- 实施计划：[Bot manage permission implementation plan](../plans/2026-09-18-bot-manage-permission.md)
- 源码基线：`03d4b7875bdf38f10540819c5f6a38bd5bd39028`（本组文档变更之前的稳定源码提交）；路径若无特别说明，均相对于 `src/bcs/`
- 文档版本说明：初稿调研基于 `acac76d251`；旧文档提交 `04c3d29c68` 已 amend 为评审输入版本 `1f55634547`，二者不是祖先关系，均不作为实现 checkout 目标。执行者读取当前分支最新 spec/plan，源码核对使用上面的稳定基线。
- 上位约束：根 `AGENTS.md`、`CONTEXT-MAP.md`、`docs/arch/arch.rules.md`、BCS `AGENTS.md` / `CLAUDE.md`

## 1. 目标与范围

为 BCS 的物理 Bot 增加显式的人类管理员，满足：

1. `GET /openapi/v1/collaboration/bots/mine` 返回当前用户 owned 与 managed 的 Bot，并标记 `owned` / `manage`。
2. 当前用户可以像 owner 一样，以 managed Bot 为视角访问它参与的 Group、Session、消息、文件及实时连接。
3. BCS 内当前允许 owner 代 Bot 执行的业务操作，也允许有效 manager 执行；授权事实变化不改变 Bot 的创建归属。
4. 管理关系复用边权限事实源，不在 Bot metadata、Frontend 或 Backend 再维护一套管理员名单。

**“平权”指相同 Bot、相同操作、相同资源角色下 owner 与 manager 的授权结果一致，不指 manager 成为全局管理员。** 管理普通群成员 Bot，不等于能够删除整个群；拥有该 Bot 的 owner 原本也不能绕过群角色规则。

本 spec 覆盖 BCS V1 以及仍挂载的相关 legacy HTTP / Workbench WS 授权路径。Frontend 是契约消费方，需要同步接入；页面布局不在本 spec 的实现范围内。Backend / Engine 的资产、运行时凭据、Provider 凭据、Skill、部署与计费权限不随 BCS manage 自动扩展。

## 2. 待评审的产品假设

为交付可评审的完整方案，本稿先采用以下明确默认值；这些不是已经获得确认的产品决策：

| 问题 | 本稿采用的语义 |
| --- | --- |
| manager 能否继续配置管理员？ | **可以**。与 owner 一样添加、撤销其他 manager，也可撤销自己；不能撤销 owner 的固有权力。 |
| manager 能否执行破坏性操作？ | 可以执行 owner 在 BCS 已有的删除、修改等操作，仍受对应资源角色与业务条件限制。 |
| 谁可以成为 manager？ | 已有 BCS Human Actor 对应的用户；不接受 Bot、App、AccessKey、组织或通配主体。 |
| 列表是否默认聚合所有 Bot 的群？ | **不改变现有视角契约**。`mine` 是身份集合；Group/Session 通过 `view_bot_id` 选择 owned 或 managed Bot，省略仍为本人 Human。 |
| 是否提供管理申请、审批、过期或多级角色？ | 不提供。只有明确授予和撤销 `manage`。 |

若希望“只有 owner 可以分配管理员”，可以收紧第一项，但那是“业务操作平权、授权分配不平权”，需要显式修改本 spec 的授权矩阵。若希望 Group/Session 默认返回多个身份的聚合列表，则应另行定义视角聚合、收藏与消息可见性合同，不能悄悄修改省略 `view_bot_id` 的含义。

### 2.1 再授权与非级联撤销的产品风险

“manager 可再授权”和“撤销不级联”必须作为**一组取舍**确认，而不是分别勾选功能：B 被撤前授予 C/D，撤销 B 后 C/D 仍能操作及继续授权。owner 可分页取得完整名单并逐项撤销；这不是一键收回 B 曾授出的全部能力，在恶意管理员持续再授权时，也不保证逐项清理能在有限次数内收敛。

第一版不提供按授予人反查的管理 API、批量撤销、授权链或冻结全部委托写入。理由是 Manage 是独立当前边，不是带父子关系的授权链；同一边幂等 grant、revoke/regrant 后，历史操作者不等于持有该授权的唯一授予人。若直接按某次审计的授予人批量撤销，可能误撤后续独立重授。审计仍完整保存每次实际状态变化，不因缺少治理 UI 而省略。

当前可验收的清理流程是：owner 先读取所有分页形成待撤名单快照，再逐项 DELETE，最后重新读取核对；不能边删除边按递增 offset 扫描而漏掉移位条目。并发授权时要重新核对新增项，不能宣称上述流程具备原子“清空管理者”保证。要求紧急封禁/可收敛回收的场景，必须先单独设计授权链治理或 owner-only 分配，再修订本假设；这些能力不在本轮实施。

## 3. 已核实的现状

现有代码并非“所有接口都只对 creator 可见”：Bot `get/query` 是 Human 控制面查询，不做 ownership 过滤；ownership 主要限制 mine、修改、代行与部分资源可见性。此次不收紧已有目录查询。

| 位置 | 当前行为与改造意义 |
| --- | --- |
| `crates/contracts/bcs-domain/src/edge_permission.rs` | `GrantKind` 仅有 `PermissionProfile` / `Rules`；没有独立管理权限。 |
| `crates/services/bcs-edge-permission-store/src/lib.rs` | `is_authorized` 以“任意 approved 出边”判断准入；`list_active_grants` 的查询/解码失败会退化为空或跳过行，不能作为新管理权限读取的错误合同。 |
| `crates/services/bcs-edge-permission/src/lib.rs` | Admission 使用上述任意边判断；`build_authz_context` 把有效边投影给运行时，必须隔离管理边。 |
| `crates/application/v1/bcs-app-bot/src/lib.rs` | `list_mine` 先物化本人 Human，再按 `created_by` 查询；`update`、candidate perspective 单独检查 owner。 |
| `crates/application/v1/bcs-app-group/src/lib.rs` | 列表通过一个 View Actor 查群；详情使用 owned Bot 集合，代行管理等部分操作还接受 legacy `is_creator` 关系。 |
| `crates/application/v1/bcs-app-session/src/lib.rs` | 列表、消息是单一 View Actor；详情、收藏、管理、成员变更分别有 owner 检查。 |
| `crates/application/v1/bcs-app-session/src/file.rs` | 文件成员判断、caller identities、upload mutation 各有 owned Bot 判定。 |
| `crates/application/v1/bcs-app-session/src/connection.rs` | 签发 session token 与 connect 时调用 V1 Session 详情授权；并非仅靠 token 签名决定访问权。 |
| `crates/service-api/bcs-service-api/src/application/v1/authorization.rs` | `HumanOrOwnedBot` 对同时携带 User/Bot 的调用做同步 `bot.owner_id == user.id` 判断。 |
| `crates/services/bcs-session/src/launch.rs` | Session 创建还有独立的 owned Bot 检查；只改 V1 facade 会在下游再次被拒。 |
| `crates/application/v1/bcs-app-invitation/src/lib.rs` | Bot 好友管理、邀请、审批与 acting actor 等仍检查 exact owner。 |
| `crates/services/bcs-bot/src/application/bot.rs`、`crates/services/bcs-group/src/application/management.rs` | legacy Bot 管理、群管理和 Workbench 授权还有下游 owner/creator 判断。 |
| `crates/adapters/http/bcs-http/src/router.rs` | legacy 入口仍有 `/bots/my`、`/groups/my`，不能只验收 V1。 |
| `migrations/mysql/014_edge_permission.sql` | edge 唯一键为 `(from_id,to_id,env,grant_ref_id)`，未包含 `grant_kind`。 |

正式合同参照 `api-contracts/v1/openapi/{bots,groups,sessions,session-files,connections,friendships,invitations}.yaml`。`CLAUDE.md` 的历史概要不能替代这些版本化合同。

## 4. 方案比较

### A. 显式 `manage` 管理边 + 统一授权 Hook（推荐）

增加 `GrantKind::Manage`，仍存储在 `edge_grants`，严格区分控制面管理边和运行时调用边。建立一个由 composition root 注册的 Bot authority Hook，各应用用例通过同一契约解析 owner/manage。

优点：语义清楚；没有第二事实源；不会与 default profile 的 wildcard 混淆；撤权与 mine 都能围绕同一条边验收。

代价：需要演进 edge 类型、唯一键、runtime 过滤，以及多处已有 owner 判定；不能只改 HTTP handler。

### B. 增加名为 `manage` 的 PermissionProfile

可复用 profile 引用，但当前 Profile/Rules 面向运行时工具授权，default profile 是 wildcard allow。仅新增 profile 名称不足以隔离管理权，需要再引入控制面/运行时分类并演进所有消费者。现有 profile 唯一键也不适合无约束扩展多个非默认角色。

不采用：表面改动小，实际隔离与兼容成本更高，容易把 wildcard 当成管理权。

### C. Bot metadata / 独立管理员表 / 多个 `is_creator`

不采用：前两者引入与边权限平行的事实源；后者把真实 ownership、legacy creator 兼容和可撤销委托混在一起。不能用改写 `created_by` 的方式让旧检查“自然通过”。

## 5. 权限模型

### 5.1 术语与判定

- **Owner**：`bot.created_by == authenticated_user.id`，比较认证后的真实 User ID，不从 Bot ID 后缀推测。
- **Manager**：当前环境有 `human_{user.id} -> bot.bot_id` 的有效 `manage` 边。
- **Controllable Bot**：owned 或 managed 的物理 Bot。此集合不是好友集合。
- **View Actor**：当前请求选择的资源视角；代表谁查看，不改变真实操作者身份。
- **Group Manager**：现有群角色/driver/originator 管理权限；与 Bot manager 是不同概念。

```text
owned(U, B)   := B.created_by == U.id
managed(U,B) := exists approved ManageEdge(env, human(U.id), B.id)
control(U,B) := B.kind == bot AND (owned(U,B) OR managed(U,B))
label(U,B)   := owned(U,B) ? owned : manage
```

同一用户既是 owner 又有历史管理边时只返回一条，`owned` 优先。本人 Human row 继续遵循既有 mine 行为并标为 `owned`；不允许管理其他 Human row。

### 5.2 不变量

1. owner 不依赖管理边存在，撤销任何管理边都不能撤销 owner。
2. 只能使用认证上下文中的 User；请求中的 `id` 只能指定被授权人，不能指定操作者。
3. `env` 从已装配的服务上下文取得，不能由请求任意选择；不新增 tenant 到 env 的推断。沿用已验证的身份/租户边界，manage 不提供跨边界映射。
4. manage 不产生反向边、不传递到 Bot 的好友、下属 Bot 或其他用户；manager 的 Bot 也不会继承该用户的管理权。manager 主动授予第三人的边是独立授权，撤销授予人的管理权不级联撤销第三人；owner 可读取完整名单并逐项撤销。
5. public/protected/private 与好友、通配 Rules、default profile 均不授予管理权。hidden Bot 仍可被 owner/manager 配置，但不能因此绕过原有协作禁用条件。
6. 不自动 claim `created_by = NULL` 的 Bot，也不根据读取行为新增管理边；缺失真实 owner 的数据恢复走独立治理流程。
7. `created_by`、Gateway Bot `owner_id`、资源原始创建者与历史审计不因 manage 改写。
8. 不自动把 manager 加成 Group/Session 成员，不复制聊天或收藏，不预创建 Session。

## 6. 存储与生命周期

### 6.1 管理边编码

```json
{
  "env": "local",
  "from_id": "human_user-b",
  "to_id": "bot-a",
  "grant_kind": "manage",
  "grant_ref_id": 0,
  "rules": null,
  "status": "approved",
  "originator_policy_type": "same_as_from",
  "originator_policy_data": null
}
```

这是拟新增的领域编码，不是当前已经支持的请求 JSON。`grant_ref_id = 0` 是 Manage 类别内的固定占位值，不指向 PermissionProfile；不在此轮把现有必填 ref 全面改成 nullable。

- 合法形状固定为 Human → physical Bot、上述 ref/rules/policy；authority evaluator 必须校验完整形状，不能只比较一段 JSON 或权限名。
- 有效性要求源 Human 与目标 Bot 存在、环境一致，边状态为 approved。未知枚举、非法主体、损坏行属于数据错误，不当作普通授权。
- 唯一键改为 `(from_id, to_id, env, grant_kind, grant_ref_id)`。同步修改 SQLite 冲突目标、MySQL 重复键处理和回查 SQL；禁止仍按旧四元组回查错边。
- 这是有意放宽旧四元组的跨 kind 互斥：相同 `(from,to,env,ref)` 的 PermissionProfile 与 Rules 可以共存且拥有独立 edge_id。好友只能由 approved PermissionProfile + 目标 active default profile 产生；同 ref 的 Rules/Manage 不成为 friend，也不被 revoke_friend 连带撤销。于源码基线核实 `crates/services/bcs-edge-permission-store/src/lib.rs` 的 `list_friends` 出/入向查询（原 195/243 行）、`has_default_edge`（原 414 行）及 `friend_list_statement` 均显式过滤 kind；此结论不扩展到旧 `is_authorized/list_active_grants`，它们仍须按第 11.2 节整改。
- conformance 必须 seed 同 ref 的 profile/rules，验证各自查询/回查/upsert 不混淆、friend 分页不重复、撤销 profile 后 friend 消失而 Rules 仍可作独立 runtime grant。完全旧版回滚发现跨 kind 的旧四元组冲突时应中止并报告，不得随意删一条边恢复索引。
- 同一 Human/Bot 可同时持有 friend edge 与 manage edge；删除好友不删 manage，撤销 manage 不删好友。
- grants 保留 revoked 行。再次授予必须把同一行恢复 approved；不能沿用 `INSERT IGNORE` 后取回 revoked ID 就返回成功。

### 6.2 原子性、并发与审计

增加专门的 manager mutation repository 契约，提供“校验当前授权 + 变更边 + 写审计”的单事务语义。不是在 application 层依次调用三个独立写入方法。

- 同一目标 Bot 的管理员变更序列化；在事务内再次确认执行者是 owner 或有效 manager。
- MySQL 使用相同目标 Bot 行的事务锁及当前读，SQLite 使用等价的写事务/条件写；Memory 实现采用同一临界区。不依赖进程内锁保证多实例一致性。
- 只对实际状态变化追加审计；重复授予/撤销返回当前状态，不重复制造业务事件。
- 复用 `permission_requests` 记录已决定的管理授权审计：新增 `request_kind=manage`；撤销记录使用 `revoke`，指向相应 manage edge。记录真实操作者、被授权人、目标 Bot、env、edge_id 和决定时间，不覆盖以前的记录。
- 审计中的 `from_id` 为被授权 Human Actor，`to_id` 为 Bot；`created_by/decided_by` 为真实操作者的 Human Actor ID，`edge_id` 必填，`requested_ref_id/requested_rules` 为空，`status=approved`。撤权同样是已批准的决定，当前权限以 edge 的 revoked 状态为准。此 Human 操作者要求针对管理员 API；Actor 生命周期自动撤权的审计保留该可信生命周期入口的 Human/System/Provider 操作者与删除原因，不伪造 owner，也不开放外部自报操作者。
- 此处是直接授权的审计记录，不是新的申请流程。好友 inbox、Connect 的 approve/reject/cancel 必须排除这些记录并拒绝处理其 ID，防止从旧流程恢复管理权。
- 边写入、审计写入或提交失败均回滚并返回错误；禁止 best-effort 持久化后报成功。
- Bot 删除或 Human 删除需撤销关联 manage edges，并保证同 ID 重建不会恢复旧委托。删除、授权失效与管理审计必须在同一持久化事务提交；registry 缓存/运行时连接清理只能发生在提交之后。使用第 6.3 节的共同锁协议，不能用两个各自原子的事务替代跨操作串行化。

### 6.3 管理变更与 Actor 生命周期的共同锁协议

第一版复用 `bcs_bots` 的 Actor row 与 `is_deleted` 墓碑，不引入另一份管理员名单或实体实例版本。锁协议属于 repository 合同，所有管理写入、Actor 删除和同 ID 恢复必须遵守；服务内互斥锁不能替代数据库事务。

1. **grant/revoke**：先按 Actor ID 的 UTF-8 字节序，锁定去重后的执行者 Human 与被授权 Human 行，再锁定目标 physical Bot 行。MySQL 使用同一事务连接的 `SELECT ... FOR UPDATE` 当前读；SQLite 使用包含实际写入的 Immediate transaction；Memory 的 Actor、边和审计读写共用同一生命周期临界区。任何路径不得持有 Bot 行锁后再申请 Human 行锁。
2. 锁定后重新读取 Actor 的 kind、env、`is_deleted`、真实 owner、执行者管理边及目标边完整形状；不存在、已删除或已失权时不写入。不能把事务外预检或旧快照作为授权证据。管理员 API 可幂等物化认证用户**本人**的 Human 行，以支持尚未调用 mine 的 owner；不创建请求指定的被授权 Human，也不把已删除的 Human 行自动恢复。
3. **Human 删除**：先锁定该 Human 行，再用当前读取得其全部 approved manage 出边，按 Bot ID 字节序锁定相关目标 Bot 行；同一事务标记 Human 删除、撤销这些边并追加删除导致的撤权审计。操作者/原因来自可信生命周期命令，不伪造 Bot owner。持有 Human 锁期间，任何涉及该 Human 的新 grant/revoke 都不能越过第 1 步，因此不会漏掉并发新增授权。
4. **Bot 删除**：锁定目标 Bot 行后，同一事务标记删除、撤销所有入向 manage 边并追加审计；此路径不再申请 Human 锁。Provider 删除与 registry unregister 等既有内部入口同样必须经过这条持久化路径，不能仅覆盖 legacy `DELETE /bots/{id}`。本轮不新增 Human 删除 HTTP API。
5. **恢复/同 ID 重建**：保留并锁定原 Actor 墓碑；恢复事务确认所有旧关联 manage 边仍为 revoked，不根据历史审计或 registry reload 重新 approved。常规注册/本人 Human 物化不得隐式清除 `is_deleted`。若独立治理流程必须硬删除数据，应在维护窗口先撤销并隔离旧边，不支持绕过协议的在线 SQL 写入。
6. 删除先取得相关锁时，并发 grant 在删除提交后必须失败；grant 先取得锁时，删除必须包含并撤销刚提交的边。删除失败则 Actor、边、审计均回滚。死锁/数据库繁忙只允许回滚整个事务后重试且重新校验，不把失败当作幂等成功。

不得复制 Actor 存活状态到独立 Memory authority store：Memory 测试及装配必须共享 registry 的 Actor 生命周期状态。删除后的跨实例 authority 查询以持久化 Actor row 为准，不使用 registry 的存活缓存作放行依据。

### 6.4 管理身份长度与审计存储

- 本管理合同内，用户身份值（管理 HTTP 目标字段 `id`，内部语义为 `user_id`）为 1..250 个 Unicode scalar values，`human_{user_id}` 为最多 256 个；physical Bot ID 最多 256 个。计数使用 Rust `chars().count()`，不按 UTF-8 字节数截断，不裁剪或大小写归一化身份值。输入空白字符串或超长值返回 `400 invalid_request`，且不得先写边或物化 Human。
- 此边界只应用于新增管理 API 与其 Core/Repo 命令；不收紧全局 Gateway 身份认证合同，也不批量改写已有 Actor ID。迁移预检发现不满足管理合同的历史身份时，报告待治理数据，不截断后继续迁移。
- MySQL 迁移将 `permission_requests.created_by` 和 `decided_by` 扩为 `VARCHAR(256)`，保留原 NOT NULL/NULL 属性；旧数据原样保留。SQLite 保持 TEXT，并由同一管理命令校验保证语义一致。
- 64 字符 User ID 的审计 Actor ID 长度为 70，必须完整 round-trip；250 字符 User ID 为合法上界，251 字符必须在写前拒绝。覆盖多字节字符，确保审计操作者与认证身份精确一致。
- 回滚至旧二进制时可保留向后兼容的加宽列；若必须收窄，先备份并隔离所有超出 64 字符的审计记录，禁止静默截断。

## 7. Bot 管理者 API

新增 Human-only API，三个方法统一挂在复数路径 `/openapi/v1/collaboration/bots/{bot_id}/managers`，沿用当前 envelope、认证要求和错误码体系；不修改全局认证链。

对外管理者标识统一为 `id`，其值是被授权人的真实 User ID，不是管理边 edge_id，也不是带 `human_` 前缀的 Actor ID。不接受 `user_id` 或 `manager_id` 请求别名；`owner_user_id` 仍为单列的真实归属字段。

| 方法与路径 | 语义 |
| --- | --- |
| `GET /openapi/v1/collaboration/bots/{bot_id}/managers?offset=0&limit=20` | owner/manager 查看显式管理员列表；按 `id ASC`（UTF-8 字节序、区分大小写）稳定排序，先去重再计数分页，limit 为 1..100。 |
| `POST /openapi/v1/collaboration/bots/{bot_id}/managers` | JSON body 必填 `id`，幂等授予该 Human 管理权；一次只操作一人，不覆盖名单。 |
| `DELETE /openapi/v1/collaboration/bots/{bot_id}/managers?id=user-b` | 必填 query `id`，幂等撤销该 Human 的显式管理权；无 body，不删除用户或 Bot。 |

POST 请求 body 示例（`Content-Type: application/json`）：

```json
{"id": "user-b"}
```

POST 只从 body 读取被授权人，DELETE 只从 query 读取被授权人；不提供路径中的用户子资源、单数路径别名或另一套写入方法。缺失、空白、重复/歧义或超出第 6.4 节边界的 `id` 返回 `400 invalid_request`；DELETE 缺少 `id` 绝不能解释为撤销所有管理员。请求里的 `id` 不是操作者身份。

采用逐项幂等变更，而不是盲目整表覆盖：管理页面编辑列表时提交增删差集，避免两个管理员各自覆盖对方的新授权。整表 replace/CAS 本轮不引入。

GET 的 `data` 示例：

```json
{
  "bot_id": "bot-a",
  "owner_user_id": "user-a",
  "items": [{"id": "user-b", "actor_id": "human_user-b", "permission": "manage"}],
  "total": 1,
  "offset": 0,
  "limit": 20
}
```

- owner 单列为只读字段，不混入显式 manager 列表。owner 缺失的历史 Bot 可返回 `owner_user_id: null`，但不能由未授权用户初始化管理权。
- POST 成功返回 `bot_id/id/permission/changed`，permission 为 `manage`；DELETE 返回 `bot_id/id/revoked`，revoked 表示本次是否改变状态。均为 HTTP 200。执行者仍有权限时，重复 POST 返回 `changed=false`、重复 DELETE 返回 `revoked=false`，不重复追加状态变更审计。
- 对 owner 本人执行 POST/DELETE 返回 HTTP 409，错误码 `immutable_bot_owner`。owner 权力不能由此 API 授予或移除。
- 第 6.4 节身份格式/长度错误、未知 body/参数、Human 目标 Bot 使用管理者 API：400；缺认证：401；执行者无权：403；目标 Bot 不存在或已删除：404 `bot_not_found`；已获资源访问资格后发现被授权 Human 不存在：404；数据库失败：500，不暴露底层 SQL。
- 被授权人的 `id` 必须解析为已存在的、同环境 Human Actor；不凭输入创建任意 Human。不依赖私有组织目录完成公共本地流程。
- 外部只以 id 操作管理关系，不接受任意 edge_id、grant_kind、规则模板或反向关系写入。
- 管理员可撤销自己；成功后下一请求不再拥有委托权。已失权调用者再次 DELETE 不能利用幂等性绕过鉴权，应得到 403。

## 8. `mine` 契约

### 8.1 返回形状

保持现有 `data.items/total/offset/limit` 以及 Bot 字段平铺结构，仅在每个 mine item 新增必填字段：

```text
access_relation: "owned" | "manage"
```

示意（省略原 Bot 其他字段）：

```json
{
  "items": [
    {"bot_id": "bot-owned", "kind": "bot", "created_by": "user-a", "access_relation": "owned"},
    {"bot_id": "bot-managed", "kind": "bot", "created_by": "user-b", "access_relation": "manage"},
    {"bot_id": "human_user-a", "kind": "human", "created_by": "user-a", "access_relation": "owned"}
  ],
  "total": 3,
  "offset": 0,
  "limit": 20
}
```

Service API 用独立 `MyBot` / `MyBotAccessRelation` 投影，`list_mine` 返回 `Page<MyBot>`；HTTP 仍平铺，不把通用 Bot DTO 强行变成“处处都需要当前用户”的模型。`get/query/discovery` 不增加未经定义的空标签或管理名单泄露。

### 8.2 查询顺序与兼容

1. 校验认证、筛选和分页，然后沿用幂等的本人 Human 物化行为。
2. 在当前 env 求 owner 集合与 manage 集合的并集；managed 侧只包含 physical Bot。
3. 按 Bot ID 去重、owned 优先，再统一应用 kind/name/status/reachability。
4. 按既有 `created_at DESC, bot_id ASC` 排序，计算 total 后再 offset/limit。

不得分别分页两类 Bot 再拼接；必须覆盖重叠、空页及跨页交错案例。`kind=human + reachability` 仍为空；hidden 是否出现继续由现有 status 过滤合同决定。

第一版可复用现有 mine 对完整候选集计算 reachability 后分页的方式，但 managed 查询和 Bot 批量 hydration 必须成批执行，不能扫描全站 Bot 或逐 Bot 查边。若未来做 DB 分页优化，不能提前于 reachability 过滤截断候选集。

`list_bots_by_creator` / `list_by_creator` 保持字面意义，不暗中改成包含 manager；新增显式 controllable 查询，避免注册、身份绑定、统计等消费者被连带改变。legacy `/bots/my` 在自身既有筛选/排序合同下包含 managed Bot，并提供相同标签。

## 9. Group / Session 平权规则

### 9.1 视角与列表

- `view_bot_id` 省略：保持 Human 本人视角。
- `view_bot_id=human_{current_user}`：允许；其他 Human：拒绝。
- `view_bot_id=physical_bot`：统一验证 owner 或 manage；不要求 manager 再添加该 Bot 为好友。
- View Bot 必须真实参与待查询资源。拥有一个 Bot 不意味着能枚举它所在群内其他 Bot 的私有 Session。
- Group 的 `membership=direct/session_only/all` 保持原含义与默认值。Session-only 参与不能被伪装为正式群成员。
- 保持排序、过滤、分页、消息可见性及 sessionless Group 查询无副作用的合同；不因管理授权新增成员或消息。

### 9.2 操作矩阵

下表的“同 owner”始终针对同一个 Bot；现有 Human 直接参与和 Bot 自身身份权限另行保留。

| 操作 | owned Bot | managed Bot | 仅 friend/public |
| --- | --- | --- | --- |
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

- Bot 是普通 worker 时，manager 不能凭 manage 升级为 Group Manager。管理 driver/manager/originator Bot 时，才取得 owner 原本对应的群管理能力。
- 管理 Session creator Bot 可以获得已有 Session 管理权；但不能仅凭 creator/manager 身份跳过消息接口的 View Actor 参与条件。
- 获取 Group 列表中的 session-only 摘要，不意味着已满足更严格的 Group 详情访问条件；保持已有 owner 行为。
- 用户 Human 自己上传的文件不属于其 managed Bot。管理该 Bot 不获得另一 Human 的文件 ownership。
- 收藏归属于选定 Bot，而非为每个 manager 新建私人副本；真实操作人另记审计。

### 9.3 Legacy creator 兼容

当前一些代行/管理路径接受 `is_creator` relation，而 mine 和严格详情路径只认 `created_by`。本轮不把历史 relation 自动升级为全局 owner/manage，也不删除这些已声明的兼容入口。

统一 Hook 以明确的 action/purpose 区分：严格 Bot 控制面使用 owner/manage；仅原本接受 creator relation 的操作允许对应 legacy 证据。不得把任何 `is_creator` 放入通用 controllable 集合，导致原本看不到的 Bot 出现在 mine。

### 9.4 删除授权与资源删除限制分离

legacy `leave_bot` 中，`authorize_human_creator_required` 是操作者授权，而 TC/Provider 托管资源的删除限制是资源自身的不变量。扩展前者为 owner/manage 后，后者不得继续依赖当前操作者 `staff_no`。

- TC 后缀兼容识别仅使用数据库读取的 **真实 `bot.created_by`** 与 Bot ID；不能使用 manager 的 User ID，也不能为了识别而改写 `created_by`。这不是通过后缀推导 owner，owner 仍由数据库事实决定。
- 判定为 Bot ID 精确以 `":" + 完整 created_by` 结尾；不能用 `rsplit_once(':')` 截断包含冒号的 User ID。owner 为 `a:b` 时，`teamclaw-bot:a:b` 命中；owner 为 `b` 时按相同兼容规则也命中，不依靠前缀推断身份。对不含冒号的既有 owner，保留“任何 `:alice` 后缀均受保护”的宽启发式，不只限 `teamclaw-` 前缀；对含冒号 owner 的覆盖是本轮明确的保护边界修正，需独立行为测试，不混入纯移动提交。
- Provider binding 存在时继续拒绝从 legacy Bot 删除入口操作，由原 Provider 生命周期入口负责；manage 不增加 Provider 凭据或 Provider 管理权限。
- 普通非托管 Bot：owner 与 manager 满足授权时均可删除，并走第 6.3 节原子生命周期路径。
- `teamclaw-bot:alice` 且 owner 为 Alice，即使没有 Provider binding，Alice 与 manager Bob 均应命中 TC 删除保护；不存在“owner 拒绝、manager 放行”。
- 删除失败必须返回错误；不得沿用 `soft_delete -> bool` 静默吞掉持久化失败。新增/演进 Result 型生命周期入口并更新生产调用者、Noop 与 recording 实现。

## 10. 认证与授权分离

manage 是资源授权，不是新的登录方式。复用经验证的 `AuthenticatedCaller.user`，不要求 Gateway 重签 owner，不修改同时在工作区拟议的 V1 auth-plugin-chain 设计。

现有 `HumanOrOwnedBot` 的同步 owner claim 判断需要显式演进，而不是无条件放开不匹配：

1. 保留原 `HumanOnly` / `BotOnly` 含义；Bot-only 请求不能获得 Human 的管理者列表能力。
2. 对需要平权的混合身份用例，新增 `HumanOrAuthorizedBot` 应用策略，使用异步 authority Hook 选择 effective Principal。
3. User + Bot 同时存在时，根据当前数据库事实验证 User 对该精确 Bot 的 owner/manage 关系；Bot-only 仍只能代表已经验证的自身身份，不能借 manage 冒充其他 Bot。
4. HTTP adapter 只做身份存在性、DTO 解析与错误映射；不得在 adapter 中查管理边。`dto/session.rs` 及 internal session-file handler 中的同步 Principal 选择也必须迁入受保护的应用入口。
5. Human 仅通过请求参数选择 managed Bot 时，保留真实 Human caller，在应用授权后产生 effective actor；不伪造 Bot 凭证。
6. 写操作审计区分 `operator_user_id` 和 `effective_actor_id`。仅记录被代理 Bot 会丢失管理员身份，不能满足授权变更追溯。

不能直接删除 `owner_id != user.id` 的拒绝分支却不补实时管理权检查；也不能把 Gateway `owner_id` 改成 manager 的 User ID。

## 11. 应用与持久化边界

### 11.1 集中授权扩展点

在 `bcs-service-api` 声明 transport-neutral 的 `BotAuthorityHook`，由 bootstrap 注册实现。已有 `AuthorizationService` 目前只是声明，不能假定它已拦截全部生产用例。

Hook 至少承担：

- 单 Bot authority 解析，返回 `Owned/Manage/Denied` 及内部授权证据；
- 一批 Bot 的可代行判定与当前用户 controllable 集合；
- Human self / physical Bot View Actor 的解析，返回类型化 `ViewActor::Human` / `ViewActor::Bot` 及精确 actor_id；调用方通过变体和 `actor_id()` 访问，不重复解析 ID 前缀；
- 第 9.3 节按既有 action 限定的 legacy creator 兼容。

需要这些能力的 application/use-case 仅调用声明的 Hook，不复制 `created_by || exists manage`。资源角色判断仍由原 Group/Session 用例负责，Hook 不接管群生命周期或消息路由。

推荐代码职责：

| 层 | 拟改动 |
| --- | --- |
| `crates/contracts/bcs-domain` | Manage 枚举、合法管理边与审计类型；不引入 HTTP DTO。 |
| `crates/service-api/bcs-service-api` | Authority Hook、Core/Repo 契约、`MyBot`、BotManagerService、混合身份策略与连接持续授权合同。 |
| `crates/services/bcs-edge-permission` | 在独立模块实现 authority Core；只依赖 repo trait；隔离 manage 与 runtime admission。 |
| `crates/services/bcs-edge-permission-store` | 严格、带 Result 的 authority 查询、反查名单、批量主体查询、原子 grant/revoke、Actor 删除与审计；SQL/共同锁协议归此层。 |
| `crates/services/bcs-bot-store` 与 registry Core | Actor 生命周期入口委托同一原子 repo 合同；提交后清理缓存/连接。MemoryBotRepo 实现该合同并复用自身 Actor 状态，不建立第二存活事实源。 |
| `crates/application/v1/bcs-app-bot` | Authority Hook 的应用适配、manager CRUD、mine 投影与 Bot 修改授权；通过 Core，不直连 DB。 |
| `bcs-app-group/session/invitation` 与 legacy application | 注入同一 Hook；替换相关 owner-only gate 和下游二次检查。 |
| HTTP / WS adapters | 新 routes/DTO、错误映射、携带真实调用者与视角；持续授权回调只调用 application contract。 |
| `crates/bootstrap/bcs` / service containers | 构造并注入共享实例，Memory/SQLite/MySQL 语义一致；无服务内环境读取。 |

新增严格 repo 读接口必须返回 `ServiceResult`，找不到授权与数据库失败是不同结果。禁止用旧 `Vec/bool` 方法的静默空值默认实现新方法；所有真实实现和 recording/noop doubles 必须显式更新，Noop 默认拒绝。

这一 Hook 是 Rule 12 的授权扩展点，不是一个新的基础设施 Plugin。现有 DbPlugin 已有事务能力；本方案不要求新增 Plugin API、动态权限引擎或通用 RBAC 框架。

### 11.2 运行时隔离

必须同时修改两处行为：

- Admission 的 runtime grant 查询仅接受 `PermissionProfile/Rules`；Manage 不参与“任意边可调用”判断。
- A2A `AuthzContext.grants` 永不包含 Manage。public-default / collaboration-default 只产生原有运行时权限，不具备管理意义。

管理 Human 发起的 BCS 控制面代行操作，可以通过 authority Hook 进入原 owner 通道；这不等于把 Manage 作为任意 originator 可使用的运行时 grant。运行时调用者若没有独立调用权限，仍按原 admission 策略处理。

## 12. 撤权、缓存与长连接

1. 管理权初版不使用跨请求正向缓存；每次新请求读当前授权事实。单请求内可合并查询，不能跨请求复用过期 mine 结果作授权。
2. grant/revoke 成功指持久化事务已提交。随后开始的权限校验必须反映新状态；已授权并执行中的普通业务操作不承诺全局回滚。
3. 管理员变更自身采用第 6.2 节事务内重新校验，保证失权管理员不能依靠检查/写入间隙重新授权自己。
4. Session token 不固化“我是 manager”；签发、connect/reconnect、每个入站控制操作都重新检查当前资源权限。
5. **已有连接的出站推送也必须受撤权约束**。在连接和 run fallback 绑定中保留真实用户、env、资源与选定视角，推送前通过应用层持续授权 Hook 重新验证；拒绝/存储故障时停止该连接受保护推送并关闭或失效绑定。
6. 不能只在本机广播一个撤权通知就声称完成：多实例、断线重连、Run fallback、Interaction replay、SSE 等仍能绕过。第一版以当前持久化事实的逐次授权为正确性基线，后续优化必须维持同样撤权语义。
7. 若用户仍通过本人参与、其他可控 Bot 或另一个独立有效授权访问该资源，只移除已撤销路径。显式绑定到被撤权 Bot 的视角不能自动切换为另一个 Bot。
8. 已发送字节、之前下载的文件不可能召回；当前校验通过后已经开始发送的帧也不承诺原子撤回。既有独立 bearer 分享链接继续遵循其原生命周期，不在此轮改造成管理权绑定链接。

### 12.1 Session-bound WS 视角协商与兼容

第一版选择 **connect 时授权并固定视角**，不把视角或 manage 权力签入 token，也不改变既有 token claims、TTL 或签名算法。token 签发经由 V1 Session 详情授权（包含本轮 owner/manage 平权）验证该精确 Session；这是一项资源访问判定，不要求 Human 本人直接参与，owned/managed Bot 是成员时亦可满足原详情规则。token 只是会话范围凭据，不能替代之后的动态授权。

| 阶段 | 合同 |
| --- | --- |
| 签发/verify | 仍返回并验证现有 tenant/User/Group/Session scope；没有 `view_actor_id` claim。旧 token 在原过期时间内继续可验证。 |
| connect 显式 Human | `params.view_actor_id` 只能是本人 Human，且满足原有 Session 参与与 mode 条件；其他 Human 拒绝。 |
| connect 显式 Bot | authority Hook 检查此精确 Bot 当前 owner/manage；应用同时验证该 Bot 是 token-bound Session participant。Human 本人不必是该 Session 成员。 |
| connect 未传视角 | 保持既有 legacy full-view 行为与 Session 资源授权，不自动推断 Human participant，不自动选择一个可控 Bot。与 HTTP 列表默认 Human 视角是不同合同。 |
| connect 成功 | 应用返回规范化的持续授权 binding：真实调用者、服务 env、Group/Session，以及 `ExplicitActor(ViewActor)` 或 `LegacyFull`。连接内不可更换视角；切换身份必须新建连接。 |
| 入站/出站/重连 | 显式视角重新验证精确 Actor 控制权与参与条件；LegacyFull 重新验证原 Session 资源访问条件。资源视角通过后再应用既有消息 scope 投影，不能仅用宽松的 Session 详情读取代替显式视角校验。 |

`AuthorizeGroupSessionConnection` 增加可省略的请求视角；已签名的 `GroupSessionConnectionBinding` 与规范化的持续授权 binding 是不同类型，不能混用。HTTP/WS adapter 只传递视角并调用应用合同；移除 `web/dispatcher.rs` 在应用授权前“一律仅允许认证 Human 视角”的分支，不在 adapter 内改为查边。

显式 X 视角失权时必须失效，即使用户仍能通过 Human 或 Bot Y 访问 Session；LegacyFull 则可保留独立合法访问路径。恢复连接时可验证旧 token，但必须重新 connect 授权，不恢复旧 binding 的许可。run fallback、Interaction replay 与 SSE 复用同一种持续授权上下文；未经绑定的 Human 推送不能因无法判断操作者而默认放行。

### 12.2 持续授权性能预算与故障处理

逐连接、逐受保护帧的当前事实校验是正确性基线，不等于接受无限开销。以下为**拟定的工程验收门槛，不是已测结果或已确认的生产 SLO**；实现 PR 必须提供数据并通过，未达标先优化批量查询/索引/锁竞争或调整容量后重测，不能静默放宽预算。

- 固定本地性能场景：至少 8 vCPU / 16 GiB RAM，同机数据库，无外部服务依赖；两个 BCS 实例各 50 条连接，10 个 Session，每 Session 10 个接收连接，20 个源事件/秒，每 payload 1 KiB，总计 **2,000 次接收者投递/秒**。SQLite 使用同一临时磁盘库、独立连接；MySQL 两实例各自 pool=8。记录 CPU/内存、OS、DB 版本、持久化/池配置、commit 与消息生成参数，保证可复跑。
- 测量预热 60 秒、稳定 300 秒、独立重复 3 次，每次均须达标。完整持续授权（含 pool 排队、资源参与和 authority 查询）的 P99：SQLite **≤25 ms**，本地 MySQL **≤50 ms**。每个精确 Bot 视角决策平均 DB statement 数 **≤4**，记录 P50/P95/P99 与总调用数，不把一次 Hook 调用等同于一次 SQL。
- 同机同数据 owner 场景与稳定源码基线比较：完成的受保护接收者投递吞吐 **≥基线的 90%**，正常允许场景投递完成率 **≥99%**，端到端投递 P99 增量 **≤50 ms**；manage 场景单独达到相同绝对授权预算与完成率，不能只测无 DB 的 mock。
- 单次持续授权固定 deadline **250 ms**，从应用调用起计，包含排队。超时/存储故障按 error fail closed，停止该 binding 的受保护入站和推送并失效连接/fallback；不复用上一次 allow，也不退化为只验 token、只验本机通知或跨帧正向缓存。deadline 是内部常量，不新增部署配置。
- timeout 不代表后台 SQL 自动消失：使用有界现有连接池和取消安全查询；若驱动不能立即取消，连接在实际查询完成前不得复用，不为每帧 spawn 无界后台任务。过载使用既有有界发送队列与关闭慢连接策略，恢复后重新连接并授权，不用 fail-open 换吞吐。
- 性能实验附带故障阶段：人工延迟一次授权超过 deadline、注入查询失败、在另一实例撤权，验证没有新的未授权 payload 入队。已开始发送的帧仍遵循第 12 节第 8 条边界。

性能未达标时阻止委托功能放量/发布，按第 15 节回滚兼容版本，不能自行开“暂不逐帧鉴权”开关。性能优化不允许改动撤权生效语义。

### 12.3 可观测性与审计区分

复用现有 bootstrap metrics recorder 和结构化日志，通过 composition root 中的 Core/应用 Service decorator 观测，不向 Core/Repo 引入 metrics exporter，不新增基础设施 Plugin。记录以下固定名称和有界标签：

| 指标 | 有界标签与语义 |
| --- | --- |
| `bcs_bot_manage_mutations_total` | `action=grant/revoke`，`outcome=changed/noop/denied/invalid/error`；成功只有事务提交后才计入 changed/noop |
| `bcs_bot_authority_decisions_total` / `bcs_bot_authority_duration_seconds` | `operation=resolve/resolve_many/controllable`，计数另带 `outcome=allowed/denied/mixed/error`；批量一调用一计数，mixed 表示批次同时包含允许/拒绝 |
| `bcs_continuous_authorization_total` / `bcs_continuous_authorization_duration_seconds` | `phase=connect/inbound/ws_outbound/replay/run_fallback/sse`，计数另带 `outcome=allowed/denied/error/timeout`；延迟覆盖排队与完整校验 |

User/Bot/Session/edge ID、token、原始 SQL/错误文本不得作为 metric label；固定 phase 仅观测用途，不能改变授权规则。权限拒绝率由 denied/(allowed+denied) 推导；error/timeout 占全部调用的比例单列，且额外展示 (denied+error+timeout)/全部调用的总体失败率，不能只展示成功样本得出可用性结论。grant/revoke 结构化日志携带 request_id、action、outcome、可用的 edge_id；真实操作者/被授权人与 env 仍以事务审计为准，不在高频帧日志中重复输出身份/内容。失效日志每 binding 的首次失败记录 reason_code 与关联 request_id，指标全量计数，避免逐帧错误日志风暴。

metrics exporter 不可用或采样关闭不改变授权结果；没有 exporter 的本地测试通过 recording decorator 验证观测事件。操作计数不能取代持久化审计，也不能把审计失败变成“日志已记所以成功”。

## 13. 传播清单与非目标

实施时必须建立“入口 → 应用检查 → 下游检查 → 测试”映射，至少涵盖：

- Bot mine、get/query 保持原目录语义、patch、candidate/eligible/search perspective、legacy status/delete/chat 的 owner gate 与下游限制。当前 `/bots/status` 和 `/bots/{id}/chat-async` 的 Bot-token 入口仍保留自身认证/runtime admission，不把 manage 变成 Bot 凭据。
- Group list/detail/create/update/delete/participants，以及 originator、invite、workspace、消息与 Workbench chat/abort 入口。
- Session list/detail/launch/reactivate/update/delete/complete/participants/messages/collect/files/token。
- Friendship acting actor、request decider/cancel、invitation 和相关权限的下游 owner gate。
- Workbench HTTP/WS、session-bound token、connection registry、frontend delivery/run fallback 和 SSE 受保护通道。
- Memory/SQLite/MySQL repository、strict errors、Noop/recording fixtures、bootstrap 与独立测试装配；所有 Actor 删除/恢复入口及其共同锁协议。
- `bcs-eventing` 的 Group event subscription 准备路径也使用 `HumanOrOwnedBot`，必须纳入混合身份策略传播，不能只改 Group facade。

Frontend 联动位置已发现：`src/frontend-nextgen/src/services/backendApi/collaboration/collaborationBotController.ts`、`services/workspace/identityService.ts`、`services/workspace/groupService.ts` 与 `services/collaborationPrivacy/mappers.ts`（后两组相对于同一 frontend-nextgen/src）。它们需接收并保留标签、展示 owned/manage、复用身份切换，失权 403 后刷新身份列表。不能再通过 Bot ID 后缀或前端 `created_by == me` 筛掉 manager；`assets/TaskPanel/GroupDrillDown.tsx` 也存在这种视角推导，需要按显式可控集合改造。

这些前端变更由其模块按自身 AGENTS/契约实施；本轮不跨模块写业务代码。Backend 与 Gateway 若另有 owner-only 拦截，应在端到端接入验证中明确暴露，而非通过伪造 creator 绕过。

非目标：ownership 转移、多 owner、组织继承、临时授权、细粒度 reader/writer、管理申请审批、默认多视角消息聚合、修改机器人 runtime token、扩大 Provider/Engine/Skill 权限；按授予人反查/批量撤销 API、授权链级联及紧急冻结属于第 2.1 节的独立后续治理设计，本轮不承诺。

## 14. 合同与兼容性

必须随实现更新：

- `api-contracts/v1/openapi/bots.yaml`、`domain-models.yaml`、`openapi.yaml`：mine item、管理 API、错误响应与安全元数据。
- `groups.yaml`、`sessions.yaml`、`session-files.yaml`、`connections.yaml`、`friendships.yaml`、`invitations.yaml`：owned-or-managed 与仍然保留的参与/角色限制。
- `gateway-principal/contract.md` 及相关 identity-policy 标注：不改变身份声明的真实归属，明确应用层动态代行授权。
- edge permission 旧设计的“任意有效边均为调用授权”描述，标注本 spec 对新增 Manage 的隔离修订。
- `CHANGELOG.md`、相关 crate `CONTEXT.md`、conformance 映射、HTTP endpoint 与 CLI coverage 清单。

兼容性分类：

- HTTP：mine 新字段是结构增量，但返回集合扩大是有意的行为变化；消费者不得继续把 mine 当作纯 owner 清单。通用 Bot 响应不变化。
- Rust Service/Repo API：`Page<Bot>` → `Page<MyBot>`、新必需方法、混合身份异步授权是编译期合同变更，更新所有消费者/实现/test doubles。
- Persistence：新增 enum 值、管理审计与唯一键升级，需要显式迁移；不是“无 schema 变化”。
- 认证：不改变认证来源、token 签名/owner claims；新 Hook 依赖当前可信 User 身份，不依赖待实现的 auth-plugin-chain。
- 无新的硬编码 URL、私有目录依赖或运行时配置项；管理名单是业务数据，不写入部署 TOML。

## 15. 迁移与回滚

1. 发布前检查 edge 数据与唯一键、管理主体类型、引用使用情况；保留完整备份。不得把全部 friend/default 边迁成 manage。
2. 本轮采用明确维护窗口：停止旧版本 BCS 写入及相关 worker，执行 MySQL/SQLite 唯一键迁移及 MySQL 审计列扩容，并核对所有 Actor 生命周期写入已接入共同锁协议，然后切换全部实例到兼容版本。旧 SQLite `ON CONFLICT` 目标在新 schema 下不兼容，**不能宣称允许新旧二进制任意混跑**。
3. 新部署初始没有新增 manager，owner 行为应与原基线一致。验证调用边、好友、mine、Session 与连接授权后，再通过管理 API 授予首批管理员。
4. 新 Bot 继续只记录真实 owner，不 seed owner 的 manage 边。管理者名单由 API 建立，不改 onboarding 身份声明。
5. revoke 与重新 grant、服务重启、双实例查询、并发 Human/Bot 删除与 grant、旧 token 的显式 Bot connect、长 User ID 审计 round-trip 必须纳入发布演练。
6. 首选回滚到理解新 schema/Manage 的兼容版本，关闭新增授权入口并拒绝委托授权、保留 owner 通路。不能直接启动完全旧的二进制：旧 admission 可能把 manage 当调用权，旧 decoder 也可能跳过或报错。
7. 若必须退回完全旧版，先停止服务，备份并隔离管理边及相应管理审计，检查旧四元组唯一性，恢复旧索引后再启动；审计加宽列默认保留，必须收窄时遵守第 6.4 节；不得依赖把 manage 状态改 revoked 就解决旧唯一键/枚举兼容问题。

## 16. 验收与验证

### 16.1 必须通过的行为用例

| 编号 | 场景与预期 |
| --- | --- |
| AC01 | owner A 授予 B 管理 Bot X；B.mine 出现 X/manage，A.mine 仍为 X/owned。 |
| AC02 | owned/manage 重叠、多个管理边历史、Human row：不重复，标签稳定。 |
| AC03 | owned 与 managed 时间交错、多页、空页、全部现有 filters：先并集过滤再计数分页。 |
| AC04 | B 以 X 作为 view 查询 Group、Session、消息，与 A 以 X 查询结果与 scope 相同。 |
| AC05 | 不传 view 仍为 Human；session-only 不变 formal member；空群读取不写 Session。 |
| AC06 | X 不在某 Session，即使 X 在父 Group，B 也不能越过原 owner 条件读取消息/文件。 |
| AC07 | X 是 worker：B 不能改群管理设置；X 是 driver/manager：B 取得 A 原有对应权限。 |
| AC08 | Bot patch、好友审批/取消、invitation、launch/acting creator、收藏、文件 mutation、workspace 与 legacy 下游 gate 均完成 owner/manage 对照测试。 |
| AC09 | manager 可增删 manager、自撤权；owner 不可经 manager API 删除；并发互撤按事务次序决定结果。 |
| AC10 | 无权限用户、Bot-only、App-only、AccessKey-only、其他 Human 视角、跨 env 一律不能借管理 API 提权。 |
| AC11 | 默认 profile wildcard、public、friend、反向边、Bot→Bot、间接好友都不构成 manage。 |
| AC12 | 仅有 manage 时不出现在 friend 列表、不进入 A2A runtime grant；单独撤好友不影响 manage，反之亦然。 |
| AC13 | POST 授予/DELETE 撤销幂等；POST 从 JSON body 取 id，DELETE 从必填 query 取 id；缺失/错位置输入拒绝且不变更任何边。revoke 后再次 grant 真正恢复 approved，不返回假成功。 |
| AC14 | 边写/审计写/提交失败全回滚；读或解码失败报错，不返回空 mine/空列表伪装成功。 |
| AC15 | 撤权后新 HTTP、旧 token 重连、已连接 WS 入站/出站、Interaction replay、run fallback/SSE 不再通过已撤销路径；双实例同样成立。 |
| AC16 | B 还有独立合法参与关系时，撤销 X 的管理权不剥夺该独立访问；显式 X 视角仍拒绝。 |
| AC17 | User+Bot owner claim 不等但 manage 有效时合法代行；manage 无效时拒绝；owner claim 不被改写。 |
| AC18 | Bot/Human 删除后同 ID 重建不恢复旧管理边；重启持久化结果不变。 |
| AC19 | 严格控制面不能用旧 is_creator 混入 mine；仅原本兼容的 action 保留 creator 行为。 |
| AC20 | 管理审计记录不可通过 Connect approve/reject/cancel 操纵；操作者记 Human，非伪造 creator。 |
| AC21 | grant 与 Human/Bot 删除的两种锁次序均可控复现；删除提交后同 ID 重建不恢复旧委托，失败时 Actor/边/审计共同回滚。覆盖双 DB 连接，不能只跑进程内锁。 |
| AC22 | 普通 Bot 的 owner/manager 均可删除；TC Bot（含无 Provider binding）与 Provider-managed Bot 在 legacy 入口均拒绝二者。覆盖 owner=a:b 的完整后缀、任意 :alice 前缀、错后缀和缺 owner，管理者不能绕过资源删除限制。 |
| AC23 | Human 非 Session 成员、仅 managed Bot X 参与：签发 token、显式 X connect 与推送成功；撤权后停止推送、旧 token 重连拒绝 X。覆盖本人/其他 Human、非成员 Bot、LegacyFull、独立合法路径与连接内视角不可变。 |
| AC24 | MySQL 审计保存 64 字符与 250 字符 User ID 的完整 Human Actor ID（含多字节）；251 字符输入写前拒绝；迁移保留旧记录，回滚不截断。 |
| AC25 | B 授予 C/D 后被 owner 撤权，C/D 的独立管理边仍有效；owner 跨页读取名单快照、逐项撤销并重新核对，在无并发授权时完成清理，不把此流程宣称为原子清空或授权链回收。 |
| AC26 | 相同 from/to/env/ref 的 profile 与 rules 各有独立 edge_id，精确 upsert 不混淆；friend 列表/分页不重复且只认 default profile，撤好友保留 Rules runtime grant；旧四元组冲突阻断完全旧版回滚。 |
| AC27 | SQLite/MySQL 按 §12.2 同机双实例负载完成 3 次测量，满足授权 P99、查询数、投递吞吐/完成率/延迟预算；超时与 DB 故障 fail closed，跨实例撤权不泄漏新的 payload。 |
| AC28 | grant/revoke 提交/幂等/失败、authority allow/deny/mixed/error、持续授权各 phase 的拒绝/故障/超时均有正确低基数指标；日志不含 token/SQL/消息内容，遥测故障不改变授权和审计事务结果。 |

### 16.2 测试分层

- Domain：枚举 round-trip、管理边形状、禁止主体与 managed 标签优先级。
- Authority/Repo conformance：Memory、SQLite、MySQL 同一套授权/撤权/并发/失败合同；不能只测字符串 SQL 构造就声称 MySQL 验证通过。
- Application：Bot、Group、Session、Invitation、文件与 shared launch 的 owner/manage 成对参数化测试，并断言 Hook 确实被调用。
- Delivery：V1/legacy HTTP envelope、错误码、混合身份、分页与 WS/SSE 连续授权合同。
- Integration：真实本地持久化、重启、双服务实例、好友/manage 并存、撤权后的完整用户故事；补充 §12.2 性能预算与 §12.3 遥测隐私/失败断言。
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

### 16.3 文件规模与本次验证边界

调研时已有 `bcs-app-group/src/lib.rs` 2,970 行、`bcs-app-session/src/lib.rs` 1,837 行、`bcs-app-invitation/src/lib.rs` 1,153 行、`bcs-edge-permission/src/lib.rs` 3,641 行、对应 store `lib.rs` 2,293 行。实施时按授权、投影、操作与测试责任拆分将被修改的超限文件，确保每个新增/修改 source file 不超过 1,000 行；不能只新建一个 helper 后继续修改超限 lib 并宣称通过。纯移动按 crate/职责分成独立 `refactor(bcs)` 提交，先运行原测试；新增接口、schema、授权规则与测试增量放在后续独立行为提交，不把数千行移动混入功能 diff。不做无关格式化，不运行全局 cargo fmt。

初稿仅新增 spec，记录过 Markdown 空白/围栏、3 个 JSON 示例、20 个验收编号与源码路径检查。本次技术评审修订补充共同生命周期锁协议、资源删除限制、Session-bound WS 视角协商及审计列扩容，第一轮验收扩展为 AC01..AC24 并生成配套 plan。本轮根据 spec/plan 联合评审修订基线、重构提交边界、授权治理风险、性能预算、跨 kind 共存、类型化视角及遥测，验收扩展为 AC01..AC28。文档检查结果以本次交付说明为准，不沿用初稿检查结果宣称修订已验证。Cargo、MySQL、双实例、WS/SSE E2E 与 Singlebox 仍未运行：本轮没有实现变更，以上是未来实施的验收要求，不宣称功能已经可用。

## 17. 评审结论入口

推荐接受方案 A。主要工作不是给枚举加一项，而是把“当前用户可以控制哪些 Bot”做成可复用、可撤销、可测试的应用授权边界，同时隔离 runtime grants。

进入实现前请优先确认第 2 节，尤其是 **manager 可以继续授予/撤销 manager**、**仍按 `view_bot_id` 切换而非默认聚合**。本轮已经获准修订 spec 并生成配套计划；计划按上述默认值编写，执行前仍须记录产品确认。文档编写授权不等于业务代码实现授权。
