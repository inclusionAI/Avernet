# BCS Bot owner / manager 边权限、委托管理与 ownership 转交

- 初稿日期：2026-09-18
- 合并修订：2026-09-22；重构对齐：2026-10-08；评审修订：2026-10-08（空快照撤权语义、DELETE 来源范围、owner 边来源编码、AC27/AC28 措辞、manager 审计独立表、WS 有界派发批次与出队复核、§15.2 合同路径；补遗：非角色边 none/none 编码、`bot_manager_changes` 时间列、SSE 通道口径、两阶段检查分工说明）
- 功能补充：2026-09-24，明确 Human 视角下 owner/manager Bot 的共同建群 sponsorship
- 状态：Draft / 核心需求与转交范围已确认，工程默认值待评审；不是实现授权
- 负责模块：BCS
- 当前代码核对基线：`94bae68a6cdfb606a6ee0bbc103d7772d5806213`（2026-10-08 fetch 的 `origin/dev`，本分支已 rebase 到该提交）；初稿基线 `acac76d251` 仅供历史溯源
- 路径若无特别说明均相对于 `apps/bcs/`；根架构文档路径相对于仓库根目录
- 本文合并管理权限与 ownership 转交设计，文件名不变，随仓库目录重构迁到 `apps/bcs/docs/superpowers/specs/`，作为本主题唯一设计来源；不在旧目录保留第二份副本
- 本轮仅修订设计并编写配套实施计划，不修改业务代码、OpenAPI schema 或数据库，不自动 commit/push；计划为 `../plans/2026-10-08-bot-owner-manager-implementation.md`，不代表已授权开始实现

阅读导航：第 1 节区分已确认需求与建议默认值；第 4—8 节定义角色、数据、业务平权及 Human 共同建群；第 9—11 节定义转交流程；第 12—17 节定义授权边界、传播、迁移与成本；第 18 节保留全部 56 项验收用例。

## 1. 目标、已确认需求与工程默认值

### 1.1 目标与范围

为 BCS 物理 Bot 增加显式的人类管理者，并支持在接收方确认后转交 ownership：

1. `GET /openapi/v1/collaboration/bots/mine` 返回当前用户拥有和管理的 Bot，每个列表 item 必须包含 `access_relation` 字段，明确当前用户是 `owner`，还是仅具有 `manager` 权限。
2. managed Bot 与 owned Bot 在同一资源角色下业务操作平权，包括 Group/Session 视角、消息、文件与实时连接；不把普通 Bot 的管理者升级成整个 Group 的管理者。
3. 当前控制权以边权限为事实源，不在 metadata、Frontend 或 Backend 再维护独立的 owner/manager 名单；创建来源与可转移 ownership 分离。
4. 覆盖 BCS V1 和仍挂载的相关 legacy HTTP / Workbench WS 路径；Frontend 是契约消费方，页面布局和跨模块业务实现不在本轮范围内。
5. Human 直接作为协作发起方时，可以把自己 owner 或 manager 的 Bot 同时拉入同一个 Group；这些 Bot 之间不需要互为好友。

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
| manager 能否配置其他管理员？ | 草案暂按可以添加、撤销其他 manager、自撤权设计；不能经 manager API 改变当前 owner。**这是实施前必须由需求方及权限安全评审明确确认的首要默认值，尚未获确认。** 团队同步不会调用此 direct 授权语义，而是只替换 team 来源的 manager。 |
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

不迁移 Backend/Engine 资产归属、Skill、部署、Provider 绑定/凭据、计费或 runtime token；不提供隐式组织继承，但提供显式、可信来源的 team manager sync；不提供多 owner、临时 manager 授权、细粒度 reader/writer、manager 申请审批或默认多视角消息聚合。本功能不是离职清权或整机资产移交方案。

## 2. 架构约束与现状证据

遵守根 `AGENTS.md`、`docs/arch/arch.rules.md`、`docs/arch/ci.enforce.md`、BCS `AGENTS.md` / `CLAUDE.md` 和各 crate `CONTEXT.md`。没有发现直接定义本功能的 accepted ADR；本稿不覆盖其他模块资产 ownership。部分上游 guidance 的路径示例仍有旧目录，按新目录内的同名文件读取其规则，不据此恢复旧布局。

### 2.1 重构后的目录与核对范围

本次以 2026-10-08 fetch 的 `origin/dev` 提交 `94bae68a6cdfb606a6ee0bbc103d7772d5806213` 为基线。目录重构提交 `cd175eefe6` 将 BCS 整体迁到 `apps/bcs/`，Frontend-nextgen 在 `apps/frontend-nextgen/`，统一覆盖率入口和产物在 `singlebox/`。此前 2026-09-22 的哈希/行数不代表当前代码。

Git rebase 已按目录重命名处理本分支两条文档提交；旧分支中的完整 spec 内容保留，再做本次修订。主要产品规则不变：owner/manager、Human sponsorship、team 来源隔离、确认式 ownership 转交和 authority cutover 均仍为拟议能力，不能把下面的现状误认为已实现。

### 2.2 当前实现与设计落点

以下源码路径相对于 `apps/bcs/`；函数名用于定位，避免依赖重构前大文件行号。

| 现有路径/契约 | 已核实的现状及本功能影响 |
| --- | --- |
| `crates/contracts/bcs-domain/src/edge_permission.rs` | GrantKind 仍仅有 PermissionProfile/Rules；Owner/Manager 和来源字段尚未实现。 |
| `crates/services/bcs-edge-permission-store/src/lib.rs` | is_authorized 仍以任意 approved 出边放行；has_friend_edge/list_friends 识别 default-profile 边；部分 Vec/bool 读会吞错，新增 authority 必须使用严格 Result 合同。 |
| `crates/services/bcs-edge-permission/src/lib.rs` | check_admission/build_authz_context 未隔离拟议角色边；DbConnectService 同时承担好友申请/同步，不能把角色授予接入 FriendAuthSyncPort。 |
| `crates/application/v1/bcs-app-bot/src/lib.rs` | list_mine 仍先 ensure_human_actor，再调用 control_plane.list_by_creator；update 与 candidate perspective 直接比 created_by。 |
| `crates/services/bcs-bot/src/core/bot_control_plane_core.rs`、`crates/service-api/bcs-service-api/src/port/repo/bot_control_plane.rs` | Bot 控制面有独立 Core/Repo 与 Provider hydration；新的 controllable 查询需走这条边界，不能在 HTTP 层 JOIN edges 或改变 list_by_creator 的字面含义。 |
| `crates/application/v1/bcs-app-group/src/authorization.rs`、`crates/application/v1/bcs-app-group/src/service.rs` | 原大 lib 的鉴权和用例已拆开；resolve_view_actor、human_actable_actor_id 与详情还读 created_by/creator。列表仍是单一 View Actor，并非所有可控 Bot 聚合。 |
| `crates/application/v1/bcs-app-group/src/create.rs` | originator 选择、Human materialization、策略/participant 校验后调用 GroupManagement；不是只改 ensure_collaboration_eligible 就覆盖建群。 |
| `crates/services/bcs-group/src/application/management/create.rs`、`crates/services/bcs-group/src/application/management/operations.rs`、`crates/services/bcs-group/src/application/management/guards.rs` | 创建时 Human 分支仅认 public 或 created_by；Bot 分支走 reachability。add_member 还按 driver 检查 friendship；Human sponsorship 必须消除这个下游误拒，并保留 public Group 只接纳 public Bot 的约束。 |
| `crates/application/v1/bcs-app-session/src/lib.rs`、`crates/application/v1/bcs-app-session/src/file.rs` | 视角、详情、收藏、文件变更仍有独立 owner 检查；消息投影的 scope/历史来源策略不随 ownership 变更。 |
| `crates/services/bcs-session/src/launch.rs` | 后续 Session launch 的 Human access/creator resolution 仍查 creator。Group 初始 Session 由 management/create.rs 直接调用 session_management，不能把两条链误写成同一路径。 |
| `crates/application/v1/bcs-app-session/src/connection.rs`、`crates/adapters/ws/bcs-ws/src/web/frontend_delivery.rs` | token 签发/connect 通过 Session facade 复核；广播/run fallback 当前不做拟议按事件角色读取，第 14/17 节仍是新增要求与成本。 |
| `crates/application/v1/bcs-app-invitation/src/lib.rs`、`crates/service-api/bcs-service-api/src/port/friend_auth_sync.rs` | owner gate 与 request_auth 传递仍存在；外部好友同步的 owner 后缀寻址不是 BCS 当前 owner，不能机械替换。 |
| `crates/service-api/bcs-service-api/src/application/v1/authorization.rs` | HumanOrOwnedBot 仍同步比较 signed owner_id；AuthorizationService 只有声明，没有覆盖所有用例的现成拦截链。 |
| `crates/adapters/http/bcs-api-http/src/v1/mod.rs`、`crates/adapters/http/bcs-api-http/src/v1/internal/mod.rs` | OpenAPI/internal protected routes 共用 Principal 和 invite-code middleware；internal 现有根为 /api/v1/collaboration，public_router 只代表未走通用 middleware，不等于无凭证。 |
| `crates/adapters/http/bcs-api-http/src/v1/internal/routes/bot_self.rs`、`crates/application/v1/bcs-app-bot/src/bot_self.rs` | /api/v1/collaboration/bots/me 在 middleware 外，但由 BotSelfService 验证 Agent token，仅返回该 Agent 注册投影；不能复用为 Human mine 或 team 同步凭证。 |
| `crates/application/v1/bcs-app-register/src/lib.rs` | 实际 RegisterService facade 在独立 register crate；v1 connect→admin_onboard 的失败当前只 warn 并返回成功，必须在实现本稿时显式改变并更新现有 onboard_failure_is_swallowed 回归测试。 |
| `crates/services/bcs-bot/src/core/provider_registration.rs` | v2 注册以签名 token 的 owner/Provider scope 调独立 Core；Bot 写入后再 ensure_human_actor/legacy ensure_owner_edges，失败传播但可能已留 Bot；不是原子新角色初始化。 |
| `crates/services/bcs-bot-store/src/bot_provider_storage.rs`、`crates/service-api/bcs-service-api/src/port/repo/bot_provider.rs` | create_provider_bot 原子创建 Bot 与 gateway compatibility projection，尚不含 owner edge/default profile；Provider/ref 唯一性含 tombstone，重复注册 409，不重放 runtime token。 |
| `crates/services/bcs-bot-store/src/registration_create.rs` | 普通 registry 注册具有 create-once 身份冲突处理，凭据读回查权威行、不从拟写入数据灌缓存；新初始化不能绕开这套重复注册/凭据规则。 |
| `crates/services/bcs-bot/src/application/onboarding.rs`、`crates/services/bcs-bot/src/application/human_actor.rs`、`crates/services/bcs-bot/src/application/provider.rs`、`crates/services/bcs-bot/src/application/bot.rs` | onboarding 按 ID 后缀覆盖 created_by、ensure-human 补 legacy creator、Provider-admin 注册/切换另有绑定写入；均需改为首次初始化/已初始化不重置。 |
| `crates/services/bcs-bot-store/src/lib.rs` | save_created_by 仍先改内存后写 DB；不适用于新的 ownership 事务。Provider 的 30 秒缓存也不能当角色授权缓存。 |
| `crates/service-api/bcs-service-api/src/port/repo/organization.rs` | 现有组织按 bot_uuid 管理成员，并非可信平台 Human 团队 manager 名单；team sync 仍需独立来源合同。 |
| `crates/bootstrap/bcs/src/server.rs` | build_openapi_v1_state 已分别装配 BotControlPlaneCore、Bot/Group/Session/Register facade 与 ProviderRegistrationCore；新增能力在这里注入，不在 handler 选实现。 |
| `migrations/mysql/014_edge_permission.sql`、`crates/bootstrap/bcs/src/migrations.rs` | edge 旧唯一键仍不含 grant_kind/source；MySQL 当前最高 030、SQLite 最高 31，历史 migration 不可改写。 |

### 2.3 保留的公开合同与行为变化

以 `api-contracts/v1/openapi/bots.yaml`、`api-contracts/v1/openapi/groups.yaml`、`api-contracts/v1/openapi/register.yaml`、`api-contracts/v1/internal.yaml` 及 Service/Repo trait 为准，不从旧目录/概要猜测语义。

相对 rebase 前分支基点 `01fc61a77f`，路径归一后原 spec 显式引用的源码主要变化在 ProviderRegistrationCore：`5bec091b54` 已允许获授权的 Provider self-service 注册设置 webhook override（仍验证 URL 和 Provider downlink credential）。这不是 Bot manager 获得 Provider 管理权。所有新 owner 行为必须保留现有 v1/v2 token、Provider/ref、AgentPass agent_code 和 plugin/gateway 语义。

**新设计不等于已有实现**：当前 mine/控制面仍依赖 created_by；owner/manager、新 authority Hook、team sync、ownership_version 与 transfer 均需实施，不能只添加 route 或重命名现有 creator helper。

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
4. manager 不产生反向边、不传递到 Bot 的好友、下属 Bot 或其他用户；manager 的 Bot 也不会继承该用户的管理权。manager 主动授予第三人的边是独立授权，撤销授予人的管理权不级联撤销第三人；owner 可读取完整名单，并按第 6 节撤销非 team 来源；team 来源须由可信平台同步治理。
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
  "management_source_kind": "direct",
  "management_source_id": "manual",
  "grant_ref_id": 0,
  "rules": null,
  "status": "approved",
  "originator_policy_type": "same_as_from",
  "originator_policy_data": null
}
```

以上为 manager 示例（来源字段已内联展示）；owner 使用相同的固定形状，grant_kind 为 owner 且来源固定为 owner/owner。增加 `GrantKind::Owner` / `GrantKind::Manager`，序列化为 owner/manager；role edge 均为 Human → physical Bot，不引用 PermissionProfile。

角色边必须携带来源元数据，不允许 NULL（NULL 会破坏复合唯一键的去重语义）：

```text
management_source_kind = owner | direct | team | ownership_transfer
management_source_id   = owner | manual | <team_id> | <transfer_id>
```

- owner 边使用固定编码 `management_source_kind = owner`、`management_source_id = owner`，与 manager 一样参与同样的 6 列去重、grants 行恢复 approved 和 revoke 语义；不能为 NULL，也不能由写入方选择其他值。
- **非角色边**（现有 GrantKind 只有 `permission_profile`/`rules` 两种；好友语义由 default-profile 的 permission_profile 边承载，不存在独立的 friend GrantKind，实施也不得新增）统一使用固定编码 `management_source_kind = none`、`management_source_id = none`；迁移回填、所有新写入和每个存储实现必须取同一值，不得由各 store 或方言自行发明其他常量（如 runtime/legacy），否则唯一键去重与跨实现 conformance 会漂移。
- 原 `PUT /bots/{bot_id}/managers/{user_id}` 产生 `direct/manual` 来源；它不接受可选 `team` 参数。
- team sync 产生 `team/<team_id>` 来源；同一个 Human 可以因多个团队拥有多条来源边。
- ownership 转交产生 `ownership_transfer/<transfer_id>` 来源；它不被 team sync 自动删除，由第 6 节 manager API 与第 10.2 节转交事务治理。
- `mine` 和管理者列表返回的是去重后的有效角色，owner 优先；管理者详情可附带 `sources` 供审计/诊断，不把来源暴露成新的权限等级。

这是拟新增的领域编码，不是当前已经支持的请求 JSON。`grant_ref_id = 0` 是 Owner/Manager 类别内的固定占位值，不指向 PermissionProfile；不在此轮把现有必填 ref 全面改成 nullable。

- 合法形状固定为 Human → physical Bot、上述 ref/rules/policy；authority evaluator 必须校验完整形状，不能只比较一段 JSON 或权限名。
- 有效性要求源 Human 与目标 Bot 存在、环境一致，边状态为 approved。未知枚举、非法主体、损坏行属于数据错误，不当作普通授权。
- 同一 Human/Bot 可同时持有 friend edge 与 manager edge；删除好友不删 manager，撤销 manager 不删好友。
- grants 保留 revoked 行。再次授予必须把同一行恢复 approved；不能沿用 `INSERT IGNORE` 后取回 revoked ID 就返回成功。

### 5.2 ownership 与转交记录

1. `edge_grants` 新增 role/source 时，Manager 唯一性按 `(from_id,to_id,env,grant_kind,management_source_kind,management_source_id)` 区分，role 的 grant_ref_id=0。现有 PermissionProfile/Rules 仍需按 grant_ref_id 区分同一对 actor 的多条授权；新来源索引不能替代并丢掉该维度。迁移按第 5.1 节为非角色边统一回填 `none/none` 编码，所有 insert/upsert/恢复/回查同步更新，friend/runtime conformance 不得退化。
2. `bcs_bots` 新增 `ownership_version`，必填，未初始化为 0；新 owner 初始化为 1，每次成功转交递增。其他 manager 变更不递增此版本。
3. 新增 `bot_ownership_transfers`。复用 DbPlugin，但不把转交硬塞进 Connect 的 permission_requests 工作流。
4. 第 12.5 节普通业务代行审计使用独立 `bcs_bot_action_audits`，不是 `bot_manager_changes` 的额外 action；其 schema 与本次其他表一并纳入首次新迁移。

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

新增 schema 必须用**新编号迁移**。当前 MySQL 在 `migrations/mysql/030_group_human_mention_notify_mode.sql`，SQLite version 31 由 `crates/bootstrap/bcs/src/migrations.rs` 及 migrations/schema.rs 管理；实施时重新检查各自最大版本，不把两种 dialect 编号当成相同。不得修改已提交的 014、初始 schema、历史 Rust migration body 或校验记录。

### 5.4 manager 变更、并发与审计

增加专门的 manager mutation repository 契约（所有转交事务另见第 10 节），提供“校验当前授权 + 变更边 + 写审计”的单事务语义。不是在 application 层依次调用三个独立写入方法。

- 同一目标 Bot 的管理员变更、team manager sync、ownership 初始化、转交和删除使用同一序列化边界。Human manager API 在事务内再次确认执行者是当前 owner 或有效 manager；team sync 则校验已验证的平台服务身份、env 与允许的来源/操作 scope，不要求平台服务冒充 Human owner/manager。普通 manager/team 变更均要求 ownership 已初始化且完整；首次初始化、转交和删除分别执行自身合同。team sync 只改 `team/*` 来源，不能覆盖 direct 或 ownership_transfer 来源。
- MySQL 使用相同目标 Bot 行的事务锁及当前读，SQLite 使用等价的写事务/条件写；Memory 实现采用同一临界区。不依赖进程内锁保证多实例一致性。
- 只对实际状态变化追加审计；重复授予/撤销返回当前状态，不重复制造业务事件。
- 管理授权审计写入专用表 `bot_manager_changes`，与 `bot_ownership_transfers` 对称独立，不复用好友域的 `permission_requests`：管理授权没有申请/审批生命周期，写入好友请求流水线会让 Connect 消费方把它当作可操作请求，也不得经其 ID 恢复管理权。
- `bot_manager_changes` 字段：`audit_id`、`env`、`bot_id`、`subject_user_id`（被授权 Human 的 User ID）、`edge_id` 必填、`management_source_kind`/`management_source_id`、`action`（`grant`/`revoke`）、`actor_kind`（`human`/`service`/`system`）、`actor_id`、`operation_id`、`decided_at`。actor_kind/actor_id 必填且必须一致：human 记录认证 User ID，service 记录凭证验证得到的平台服务 ID，system 记录受治理清理/迁移的固定系统标识；不将 service/system 标识伪装成 Human，也不以 owner 或被授权人代替实际操作者。`gmt_create`、`gmt_modified` 沿用仓库数据库时间约定。记录追加、不覆盖历史，当前权限始终以 edge 的 revoked 状态为准。
- 此处是直接授权的审计日志，不是申请流程。好友 inbox、Connect 的 approve/reject/cancel 从设计上接触不到该表的 ID 空间。team sync 的差异增删审计也写入同一表，operator 为 service。`operation_id` 必填，用于关联一次原子变更的所有审计行；team 请求还保存必填 `idempotency_key` 并关联持久化同步回执，幂等作用域包含 env、service、Bot 和 URL team，operation/payload 属于冲突校验内容。非 team 操作的 idempotency_key 为空，不伪造平台请求键。空快照、无差异同步也必须保存回执，但不制造没有状态变化的审计行；不能用“是否存在审计行”替代幂等回执。回执、差异边和审计同事务提交，重试不重复写审计。
- 边写入、审计写入或提交失败均回滚并返回错误；禁止 best-effort 持久化后报成功。
- Bot 删除或 Human 删除需撤销关联 manager edges，并保证同 ID 重建不会恢复旧委托。删除动作与授权失效的持久化必须有原子或明确的失败恢复保证，不能只依靠 registry 缓存清理。

## 6. 管理者列表 API

新增在 `/openapi/v1/collaboration` 下的 Human-only API，沿用当前 envelope、认证要求和错误码体系；不修改全局认证链。

| 方法与路径 | 语义 |
| --- | --- |
| `GET /bots/{bot_id}/managers?offset=0&limit=20` | owner/manager 查看显式管理员列表；按 `user_id ASC` 稳定排序，limit 为 1..100。 |
| `PUT /bots/{bot_id}/managers/{user_id}` | 幂等授予一个 Human 管理权；请求无业务 body。 |
| `DELETE /bots/{bot_id}/managers/{user_id}` | 幂等撤销该 Human 的 direct 与 ownership_transfer 来源管理权（不撤销 `team/*` 来源），不删除用户或 Bot。 |

采用逐项幂等变更，而不是盲目整表覆盖：管理页面编辑列表时提交增删差集，避免两个管理员各自覆盖对方的新授权。原 direct manager API 不新增可选 `team` 参数；它始终表示人工 direct 授权。

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
- PUT 成功返回 `bot_id/user_id/role/changed`，role 为 `manager`；DELETE 返回 `bot_id/user_id/revoked/remaining_team_sources`，revoked 表示本次是否改变 direct/ownership_transfer 来源状态，`remaining_team_sources` 列出该用户仍持有的 `team/*` 来源。本 API 不撤销 team 来源（与 §6.1 的来源隔离对称：人工 direct API 与 team sync 各管各的来源空间）；`remaining_team_sources` 非空意味着该用户仍是有效 manager，需经可信平台的 team sync 快照更新才能彻底失去管理权，调用方不得误认为 `revoked=true` 等于已移除全部管理权。均为 HTTP 200。
- 对 owner 本人执行 PUT/DELETE 返回 HTTP 409，错误码 `owner_role_requires_transfer`。当前 owner 不能由此 API 授予或移除，应使用专用转交流程。
- 格式错误、未知 body/参数、Human 目标 Bot 使用管理者 API：400；缺认证：401；执行者无权：403；已获资源访问资格后发现被授权 Human 不存在：404；数据库失败：500，不暴露底层 SQL。共享的初始化/基础设施错误采用第 11.2 节的同名 code/HTTP 映射；其余资源特有错误仍按本节处理，不把转交专属错误机械套用到 manager API。
- 被授权人的 `user_id` 必须解析为已存在的、同环境 Human Actor；不凭输入创建任意 Human。不依赖私有组织目录完成公共本地流程。
- 外部只以 user_id 操作管理关系，不接受任意 edge_id、grant_kind、规则模板或反向关系写入。
- 管理员可通过本接口撤销自己的 direct/ownership_transfer 来源；只有不存在其他有效来源时，成功后下一请求才不再拥有委托权。仍有 `team/*` 来源时保持 manager，下一合法请求应成功；team-only manager 自 DELETE 为无状态变化的 200（revoked=false，remaining_team_sources 非空），不能通过本接口退出平台维护的关系。已真正失权的调用者再次 DELETE 不能利用幂等性绕过鉴权，应得到 403。

### 6.1 Team manager sync API

可信平台的正常同步统一使用一个 team 来源快照接口：

```http
PUT /api/v1/bots/{bot_id}/manager-sources/teams/{team_id}
```

普通 team 快照同步：

```json
{
  "operation": "sync",
  "manager_user_ids": ["user-a", "user-c"],
  "idempotency_key": "sync-uuid"
}
```

Bot 从旧 team 移到新 team：

```json
{
  "operation": "move",
  "new_team_id": "team-new",
  "manager_user_ids": ["user-a", "user-c"],
  "idempotency_key": "move-sync-uuid"
}
```

接口语义：

- `operation` 是业务语义字段，取 `sync` 或 `move`；**不能用 `idempotency_key` 区分操作类型**。`idempotency_key` 只用于同一请求重试去重。
- `operation=sync` 用请求中的 `manager_user_ids` 替换当前 `{team_id}` 的 `team/{team_id}` manager 来源；列表中新增的用户建立来源边，列表中消失的用户撤销来源边。
- `operation=move` 在同一个 Bot 事务内移除 URL 中的旧 `{team_id}` 来源、建立 `new_team_id` 来源，并用 `manager_user_ids` 作为新 team 的完整快照；其他 team、direct、ownership_transfer 和 owner 来源不变。
- `manager_user_ids` 表示该 team 当前对该 Bot 的 manager 列表，不表示 team 的全部成员。字段名不能使用含义更宽的 `member_user_ids`。
- 外部平台当前没有可提供的 `membership_version` 时，本字段不作为必填输入。此时可信平台必须保证同一 Bot/team 的快照通知按顺序投递、至少一次投递，并且每次通知都是完整快照；BCS 通过 `idempotency_key` 处理重复通知，并按接收顺序应用不同快照。
- 如果未来平台可以提供单调版本，可增加可选 `membership_version`；提供后 BCS 应拒绝旧版本覆盖新版本。没有版本时，BCS 不能声称能够识别网络乱序的旧快照，需由可信平台保证有序投递或通过后续完整快照修复。
- `operation=move` 的 `new_team_id` 必须非空且不能等于 URL 中的旧 team；同一个 move 请求使用相同 `idempotency_key` 重试时返回原结果，不重复撤销/建立来源边。
- `owner`、direct manager、ownership-transfer manager 永远不被该接口撤销。多个 team 的有效 team manager 是所有当前 team 来源 manager 的并集。
- 该接口只接受可信平台的内部服务身份，不接受普通 Human、前端或公开 OpenAPI 调用。成员快照不可验证、操作类型非法、move 目标非法分别返回明确的 4xx 错误。
- `/api/v1` 不要求 Human、App 或 Bot 登录 Principal，但**不是完全匿名接口**：请求必须携带可信平台同步凭证。凭证可采用注册接口同类的 HMAC/signed token，但应放在 Authorization 或专用 Header 中，不能把长期凭证放在 URL query 中。凭证至少绑定 `env`、调用方服务和允许的 manager-source 操作；缺失返回 401，签名/作用域无效返回 403。
- 保留约定的 `/api/v1/bots/{bot_id}/manager-sources/teams/{team_id}`，不因源码迁目录改成 `/openapi/v1` 或擅自加入 `/collaboration`。现有 internal routers 默认 nest 在 `/api/v1/collaboration`；实施时需在 `crates/adapters/http/bcs-api-http/src/v1/internal/mod.rs` 显式 merge 独立 `/api/v1/bots` slice，放在通用 Principal/invite-code middleware 外，并由专用凭证边界保护。HTTP 只解析凭证/DTO 并调用应用契约；应用在验证平台身份与操作 scope 后写入，不把 body 中的 team_id 当身份。不能把 entire internal router 改成匿名。

POST/DELETE 可以保留为内部运维和故障修复接口，例如按单个 user 增加/撤销某个 `team/{team_id}` 来源，但不能作为可信平台的正常同步入口；它们必须使用相同的来源语义、审计、幂等和可信服务鉴权，不能操作 direct 或 ownership-transfer 来源。

- Team sync 的权限错误、成员快照不可验证、版本冲突和事务失败分别映射为 `403/invalid_manager_sync_source`、`400/invalid_membership_snapshot`、`409/manager_sync_conflict` 和 `500`。空列表是合法快照：`manager_user_ids` 为空表示该团队当前没有任何用户是本 Bot 的 manager，应撤销该 `team/*` 来源的全部 manager 边；但空列表必须先经过完整快照校验才生效，快照缺失字段、类型非法、不可验证来源结构或部分写失败一律不产生撤权效果。

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

Service API 用独立 `MyBot` / `BotAccessRelation` 投影（与实施计划公共命名一致，不再引入 MyBotAccessRelation 别名），`list_mine` 返回 `Page<MyBot>`；HTTP 仍平铺，不把通用 Bot DTO 强行变成“处处都需要当前用户”的模型。`get/query/discovery` 不增加未经定义的空标签或管理名单泄露。

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

### 8.3 Human-originated 的受控 Bot 共同建群

Human 直接作为协作 originator 发起建群时，引入独立的“Human sponsorship”资格，不要求被拉入的 Bot 之间存在好友关系：

```text
human_can_sponsor_bot(U, B)
  = B.status != hidden
    AND (
      B.visibility == public
      OR authority(U, B) == owner
      OR authority(U, B) == manager
    )
```

具体规则：

1. 只有已验证的 Human caller 可选择自己为 `originator = human_{U}`；不能仅信任请求字符串。driver_bot_uuid 和所有 Bot participants 都按上述规则检查。受保护/私有 Bot 在目标 Group 允许的可见性策略下可由 owner/manager 拉入；保留当前 **public Group 只能包含 public Bot** 的规则，混合 protected/private 的例子创建 private Group。
2. 同一 Human 同时控制 owner Bot 与 manager Bot 时，二者可以共同建群，即使二者没有 friend edge。该 sponsorship 是当前 Human 对 Bot 的控制面事实，不创建 Bot↔Bot friend edge、不修改 friendship 状态、不产生 A2A runtime grant。
3. Hidden Bot 仍然拒绝；Group 的 driver、manager、worker、consultant 等资源角色继续独立校验。Human 的 manager role 只证明“可以代表该 Bot 参与本次建群”，不自动成为 Group Manager。
4. 该规则适用于 Human-originated 创建及已有 Group 中 Human 本人通过群管理授权后的 add-member。create.rs、service.rs 的上游通过后，management/create.rs 与 operations.rs/guards.rs 必须接收或重新核实同一可信 Human sponsorship，不再以 driver 与目标非好友误拒；不是关闭全局 friendship 检查。
5. 当 `originator` 是 Bot 时，仍按该 Bot 的 public/friendship/reachability 规则检查目标 Bot。Human 与两个 Bot 的共同 owner/manager 关系不能替代 Bot-originated 的 friendship。
6. 详情、View Actor 和后续 Session launch 的 Human→Bot 判断复用同一 authority，但保留资源成员条件。Human 为 originator 不等于自动成为所有 Group/Session 的 participant：不带 view_bot_id 的列表/消息仍查 Human 实际成员关系；不在参与者中的 Human 可选合法受控 Bot 视角，不能隐式聚合或绕过 message_view_scope。初始 Session 在 management/create.rs 创建，后续 launch 在 services/bcs-session/src/launch.rs，分别验收。
7. Human sponsorship 只扩大本次 BCS 协作资源的资格，不改变 Bot 的 `created_by`、visibility、friend list、manager list 或外部资产归属。

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
4. 建立/恢复原 owner 的 manager 边（来源 `ownership_transfer/<transfer_id>`）；revoke 接收方在本 Bot 上的 direct/manual 与 ownership_transfer/* manager 边（其已升级为 owner），接收方原有的 `team/*` 来源保留、仍由 team sync 治理。其他 manager/friend/runtime edges 不变。
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
| owner/version 已变化 | 409 / ownership_changed；accept 对未过期 pending 按第 10.2 节先提交 invalidated（owner_changed）再返回；该失效原因的 accept 重试返回同码。新建请求仅 `expected_owner_version` 过时（转交双方与资格合法）同样返回 409/ownership_changed，提示客户端重读 ownership 后用新版本号重新发起；不修改任何有效请求、不落单；其他新建参数非法仍 400/invalid_request |
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
6. 写操作审计区分 `operator_user_id` 和 `effective_actor_id`，从已验证 caller/选定视角经 application→Core→拥有写事务的 Repo 传递；具体存储、Bot-only 语义、原子性与外部副作用见第 12.5 节。仅记录被代理 Bot 会丢失管理员身份，不能满足授权变更追溯。

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

### 12.4 Ownership authority cutover

所有“Human 当前是否拥有/可控制物理 Bot”的判断统一走 BotAuthorityHook/authority Core：owner 来自唯一 approved OwnerEdge，manager 来自任一有效来源；mine 对两者并集去重、owner 优先，并保留第 7 节本人 Human self row，不能简化成只返回物理 Bot。

必须切换 Bot mine/PATCH/candidates，Group/Session View Actor/detail/launch，Group add-member/workspace，消息/文件/Workbench，invitation/friend acting actor 及 legacy 下游二次鉴权。Group originator/Session creator/文件 owner 的资源策略不变，只替换其中“Human 可代表此 Bot”的判断。实现 PR 需建立调用点→替代合同→回归测试清单，不能只 grep 改字段名。

list_bots_by_creator/list_by_creator 只保留字面“创建来源”用途；不要悄悄扩展旧查询给身份修复等消费者。权限链禁止 `created_by == user OR is_creator OR Bot ID 后缀 OR signed owner_id` 兜底。created_by 留作来源/历史/审计和一次性迁移输入；legacy creator 为治理线索；外部 Provider/好友同步寻址按第 12.3 节单独保留，不可机械改成 BCS owner。

初始化版本 0 不得经 Human ownership 路径放行；对已可见资源按相应 API 返回 ownership_not_initialized，损坏的已初始化角色返回一致性错误。不改变原资源 concealment，不把该错误暴露给本来无权查看 Bot 的 caller；不影响独立 Bot 凭据握手/Agent self 和现有目录 get/query 的既有合同。

### 12.5 普通业务代行的持久审计合同

**存储与范围**：新增追加式 `bcs_bot_action_audits`，记录本次 authority cutover 涉及的 Bot 配置/状态、Group/Session/参与者、收藏、文件 mutation/share、workspace、launch/消息控制及好友/邀请等写操作的真实操作者与 effective actor。manager 来源增删仍写 `bot_manager_changes`，ownership 转交仍以 transfer 回执解释角色变更，不把同一角色操作重复包装成普通业务审计。该表不是当前权限事实源，也不授予凭据持有人或历史操作者任何访问权；不增加公开审计查询 API，不对普通读取、心跳或每个输出 token 写业务审计。

当前 `SessionRepoPort::collect(session_id, bot_uuid)` 只写收藏状态，文件 Repo 只维护文件 owner；已有 `AuditEntry.actor/details` 也不是上述统一、事务化的双身份合同。因此不能只在 facade 增加两个字段/日志就声称完成，需要演进实际写入端口及 Memory/SQLite/MySQL 实现。

| 字段 | 固定语义 |
| --- | --- |
| `audit_id`, `env`, `operation_id`, `step_key` | 服务生成的记录/操作 ID、装配环境、操作内稳定步骤键；唯一 `(env,operation_id,step_key)`，同 key 必须校验记录内容相同，不能覆盖不同操作者/动作 |
| `operator_kind`, `operator_id`, `operator_user_id` | kind 为 human/bot/system；human 的两个 ID 都是可信 User ID且必填；Bot-only 的 operator_id 为已验证 Bot ID、operator_user_id 为 NULL；独立系统动作记 system，不伪造 Human，恢复已记录用户操作则保留原操作身份 |
| `effective_actor_id` | 应用授权后选定的 Actor；Human 可代表合法 Bot 或本人 human actor，Bot-only 必须是验证过的自身。恢复既有操作保留原操作上下文，system 只用于独立系统动作 |
| `resource_kind`, `resource_id`, `action`, `phase` | 精确资源、受控动作枚举；phase 为 applied/admitted/completed/failed/unknown。步骤键覆盖动作/资源/阶段，不持久化任意请求 body |
| `reason_code`, `gmt_create`, `gmt_modified` | reason_code为可空固定机器原因，gmt_*为非空数据库时间；记录追加不更新，不保存消息正文、文件内容、token 或原始存储错误 |

**身份传播**：以必需的 `BotOperationContext` 携带 `operation_id` 与带类型的 Human/Bot/System 操作者；application 在完成认证和有效 Actor 选择后构造，不能从 body 中接受 operator。Core/Repo 的相应写命令显式接收它，不使用 `Option<Context>` 让生产调用静默跳过审计。Human 与 Bot-only 是不同合法枚举分支，NULL User ID 仅表示确实没有 Human，不是缺参数兜底；裸 Bot 不因此取得 Human-only manager/transfer API 的资格。

**同库操作**：收藏、配置/资源角色状态、资源成员等持久变更与其 applied 审计由拥有该资源的 store 在同一 DbPlugin 事务提交；Memory 在同一临界区内原子发布业务状态和审计，失败丢弃暂存状态。不能 application 先调用业务写、再调独立 append-audit 方法。审计插入或提交失败回滚该次业务变更并返回错误；幂等无变化不制造 applied 事件。同一命令内部重试复用 operation_id/step_key；既有 HTTP 幂等用例把该 ID 纳入既有回执，未定义 HTTP 幂等的接口不因此获得新的 exactly-once 承诺。

**外部或运行时副作用**：文件后端删除/分享、消息发送/abort、launch 的后续投递不与数据库组成虚假的全局事务。认证后先持久化 admitted（有既有 durable command/delivery 时与该记录一起提交），再执行外部 I/O；无法写 admitted 时不得开始副作用。外部成功后，在提交业务结果/文件 metadata 变更时同事务写 completed；明确失败写 failed，结果不确定写 unknown，均采用稳定步骤键并传播持久化失败。I/O 已发生但结果记录提交失败时返回错误，保留 admitted/既有操作标识供核查与原流程恢复，不记录虚假的 completed、不声称网络副作用已回滚，也不单凭审计键自动重放外部调用。恢复遵循现有业务幂等/不确定性合同，不新建通用 outbox 或全站审计平台。对持久消息投递/Chat run，在现有持久命令记录中保存 operation_id，并同事务写 admitted 身份快照；恢复按该 ID 和稳定步骤键读取原 context，而不是按资源“最近一条审计”猜操作者。新增列与审计表一起进入新迁移；历史行可显式无 context，不补造历史 Human，新版代行命令则必须有 context。通过切换时记录的历史边界区分旧行与新命令；新命令上下文缺失/损坏应停止执行并报错，不能降级成 system 代行；历史无 context 的独立运行时清理仍按现有合同执行，并以 system 记本次清理，而非声称恢复了原 Human 身份。

**落点与验证**：Bot store、Group store、Session store、Session-file store 及现有好友/邀请/消息持久命令边界分别承接本资源写入和同事务审计，复用同一纯上下文/审计描述类型，不互相导入 concrete store、不把 SQL 放进 Hook/application。Task 1 定义类型、Task 2 建 schema、Tasks 9/10/11/12 各自贯穿其用例；Task 11 不是替其他任务事后补审计。每个写类至少覆盖 Human 代 Bot、Bot-only（该入口允许时）、审计失败回滚/不发外部调用和幂等重试；文件等外部操作另测 I/O 成功后 DB 失败、结果不确定和恢复不重放。

## 13. 应用、Core 与持久化边界

### 13.1 集中授权扩展点与现有模块

在 `bcs-service-api` 声明 transport-neutral 的 `BotAuthorityHook`，由 `crates/bootstrap/bcs/src/server.rs` 装配。当前 AuthorizationService 未提供全链路实现；拟新增 Hook 负责 Owner/Manager/Denied、批量可控 Bot、View Actor 与 owner-only action，不复制 `created_by || role` 兜底。群角色/成员条件仍由 Group/Session 用例管理。

| 模块/路径（相对 crates/） | 本功能职责与传播 |
| --- | --- |
| `contracts/bcs-domain` | Owner/Manager、来源、ownership_version、transfer 状态等纯类型，不放 HTTP DTO。 |
| `service-api/bcs-service-api` | application BotManagement/OwnershipTransfer/TeamSync 契约、authority Hook；core 与 repo 合同分别演进，DTO 不用 concrete store。 |
| `services/bcs-edge-permission` | 新独立 authority 模块实现严格角色判断与事务编排，不把当前 DbConnectService 变成所有权限的单体，也不直接依赖 DbPlugin。 |
| `services/bcs-edge-permission-store` | 来源范围查询、manager/transfer 事务、审计及严格错误；新文件按责任拆分现有超限 lib。 |
| `application/v1/bcs-app-bot`、`services/bcs-bot/src/core/bot_control_plane_core.rs` | mine/patch/candidates 通过 authority 与独立 control-plane Core 做读投影；Agent self facade 仍只查已验证 Agent 自己的注册。 |
| `application/v1/bcs-app-group/src/authorization.rs`、create.rs、service.rs | Human 视角/代行、创建和加成员；不得只修改旧 lib 或未被创建链调用的 helper。 |
| `services/bcs-group/src/application/management/` | create/guards/operations/workbench/queries 分别接入统一 authority，保留角色、public Group 和成员限制。 |
| `application/v1/bcs-app-session`、`application/v1/bcs-app-invitation`、`services/bcs-session/src/launch.rs` | Session/file/connection/invite 与后续 launch 的二次授权；历史消息读取来源/cutoff 与投影 scope 不变。 |
| `application/v1/bcs-app-register`、`services/bcs-bot/src/core/provider_registration.rs` | 实际 v1/v2 注册与 token scope 编排，消费第 13.3 节新初始化合同；不要把注册迁入 bcs-app-bot 另起入口。 |
| `services/bcs-bot-store/src/registration_create.rs`、bot_provider_storage.rs | 复用既有 Bot create-once、Provider/ref 与 gateway projection 的一致性边界，扩展首次 ownership 初始化；不可复制另一套 Bot 插入 SQL 到 authority facade。 |
| `adapters/http/bcs-api-http/src/v1`、`adapters/ws/bcs-ws` | routes/DTO、独立凭证解析及错误映射，WS 调应用持续授权；不选实现或直接操作 repository。 |
| `bootstrap/bcs/src/server.rs`、`service-api/bcs-services-container` | 明确注入 application/Core/repo，覆盖独立 connection-token router 与 legacy 用例装配，Memory/SQLite/MySQL 语义一致。 |

所有新增权限读返回 Result，未授权与存储故障不可混淆；Noop 默认拒绝，所有 recording doubles 必须更新并断言 Hook 被调用。这是 Rule 12 的横切授权扩展点，不新增通用 RBAC、私有目录或动态权限引擎。

### 13.2 ownership 应用与聚合事务

OwnershipTransferService 负责身份和用例；authority Core 经声明的聚合 repo 完成角色边、Bot ownership_version、transfer 的同库事务。角色/转交 SQL 由 edge-permission-store 承担，首次 Bot/Provider 创建则沿用 bot-store 的现有职责；各 store 必须通过显式合同组合事务，不互相导入 concrete 实现。

分属不同 crate 不等于分属不同数据库：SQLite/MySQL 装配使用同一选定 DbPlugin 时应在存储边界组合为单事务，不能先调两个各自提交的 repo 方法再称“原子”。Memory 实现提供同等临界区；跨物理存储部署若不能实现该保证，必须先设计显式隔离/恢复合同并评审，不以 best-effort 或默认成功降级。普通 Handler 不持有 DB transaction/SQL。

### 13.3 Registration ownership initialization contract

适用入口：`bcs-app-register::RegisterServiceImpl` 的 v1/v2 注册、legacy register/onboard、Provider-admin registration。新模型需要修改它们背后的初始化用例，并同步 `application::v1::RegisterService`、`core::ProviderRegistrationCoreService`、`port::repo::BotProviderRepoPort` 和普通 registry create 合同；不是只在 HTTP 注册后附加一次写边调用。

| 入口 | 当前行为 | 目标变化与必须保留的边界 |
| --- | --- | --- |
| v1 register | BotManagement connect 后 admin_onboard，错误只 warn；已有测试固定此行为 | 用可信 register token 的 Human scope 初始化 owner，必要持久化失败不返回成功；更新错误合同与 onboard_failure_is_swallowed 测试，不延续假成功。 |
| Provider v2 register | Core 再鉴权→create_provider_bot 原子 Bot/gateway 写→Human/legacy owner edges 分开写 | 将新角色初始化纳入同一次成功提交，保留 Provider/ref 唯一性（含删除）、重复 ref 409、token 不重放、AgentPass agent_code 和 plugin 不建 gateway binding。 |
| legacy/admin onboarding | save_created_by、legacy owner relation、default profile 分次提交；有后缀覆盖分支 | 仅可信首次注册上下文可初始化；禁止重复 onboard、ensure-human 或 Provider switch 重新认领。 |

对有可信 Human owner 的首次成功注册，存储聚合必须提交：Bot control-plane record（created_by 保留可信创建来源）、必要的 Human Actor 幂等物化、唯一 approved OwnerEdge、ownership_version=1，以及 default profile 的 ensure（不改写已有 profile 的 ID/revision/rules）。Provider gateway 同次创建还需保留现有 binding projection 原子性；不接管后续 Provider 凭据或部署生命周期。

凭证验证、Provider 可用性、模式/URL/downlink credential 检查在创建前完成；初始 owner 来自已验证 register token 或注册专用可信身份，不能来自任意 body 的 created_by。公开 HMAC token 仍只用于注册，不能充当 team sync token。缺少 Human 的 Bot runtime 首次连接仍按既有握手建立未初始化对象，不伪造 owner；版本 0 对象不进入 Human controllable 集合，后续只能通过验证过的初始化/治理入口完成。

任一必要 DB 写或提交失败均返回错误，同库新注册事务整体回滚；不返回 2xx“稍后修复”，不先把 owner 写入进程缓存。已有 runtime/历史 Bot 行不属于这次事务的新建内容，初始化失败应保持其原状和未初始化状态，而不是删除该 Bot；重试必须验证同一注册身份和当前状态。公开 Provider 重复 ref 仍为 409，不能为恢复方便泄露或重放运行时凭据。

对已初始化 Bot，reconnect、ensure-human、Provider re-register/switch 和名称修复只可修改各自非 ownership 字段，不覆盖 created_by、owner、manager 或 ownership_version；发现 authority 不一致需报错进入治理。外部 Provider owner/self-service policy、Agent self 凭证与 Human Bot ownership 是独立边界，不能把所有 owner 字符串一律替换为 Bot role。

### 13.4 运行时隔离

- Admission 的 runtime grant 查询仅接受 PermissionProfile/Rules，Owner/Manager 不参与“任意边可调用”。
- A2A AuthzContext.grants 不包含角色边；public-default/collaboration-default 的既有来源不是控制面管理权。
- Human 受控 Bot 建群按第 8.3 节的控制面资格准入；群/Session 内现有协作投递不凭空写入 Bot↔Bot 授权边，也不授予群外任意工具调用能力。

## 14. 撤权、缓存、长连接与通知

1. 管理权初版不使用跨请求正向缓存；每次新请求读当前授权事实。单请求内可合并查询，不能跨请求复用过期 mine 结果作授权。
2. grant/revoke 成功指持久化事务已提交。随后开始的权限校验必须反映新状态；已授权并执行中的普通业务操作不承诺全局回滚。
3. 管理员变更自身采用第 5.4 节事务内重新校验，保证失权管理员不能依靠检查/写入间隙重新授权自己。
4. Session token 不固化“我是 manager”；签发、connect/reconnect、每个入站控制操作都重新检查当前资源权限。
5. **已有连接的出站推送也必须受撤权约束**。在连接和 run fallback 绑定中保留真实用户、env、资源与选定视角。第一版基线是**同一事件内、有界派发批次**的持续授权：批次在派发前调用应用层 Hook，批量读取当前 scoped authority。去重/决策键至少包含已验证身份边界（含 tenant/env）、真实 User、资源种类/ID、选定 View Actor、action 与消息 visibility/audience 范围；不能只按 user_id 共享最终派发决定。同一批次、相同完整上下文的连接可共享读取证据，仍逐连接检查绑定有效性和原有消息可见性。**不跨事件、跨连接 TTL、run 首次绑定或 token TTL 复用授权**。预算见第 17.2 节。资源/身份授权失效或存储故障时停止相应绑定的受保护推送并关闭或失效；消息本身不在当前 audience/view scope 内只跳过本条，不关连接。批量读取整体失败时该批所有尚未查证的目标均 fail closed。
6. 不能只在本机广播一个撤权通知就声称完成：多实例、断线重连、Run fallback、Interaction replay、SSE 均执行同一持续授权规则。此处 SSE 指**面向 Human 的出向受保护通道**：当前代码库中没有 Human 出向 SSE adapter（唯一 SSE 实现是 Provider **入站**的 `crates/adapters/http/bcs-provider-http/src/sse.rs`，它不承载 Human 权限数据、不套 Human 角色检查）；本条要求在（a）未来新增 Human 出向 SSE、或（b）Provider 事件经转发到达 Human 出口的链路上同样生效。当前 Workbench 广播先写每连接 mpsc 队列、再由独立 writer 发 socket；入队前的批量检查不能代替出队后的检查。受保护队列项须保留完整授权上下文，writer 在出队、实际开始发送前重新调用 Hook；重排队/重新调度/fallback/replay 都是新的派发，不能沿用此前批次结果。无敏感载荷心跳不在此范围。当前独立 writer 下出队复核可能退化为单目标查询，必须计入成本；后续若将实际发送前检查做有界合批，仍须保留相同一致性和逐绑定判定，不引入 TTL。
7. 若用户仍通过本人参与、其他可控 Bot 或另一个独立有效授权访问该资源，只移除已撤销路径。显式绑定到被撤权 Bot 的视角不能自动切换为另一个 Bot。
8. 已发送字节、之前下载的文件不可能召回；当前校验通过后已经开始发送的帧也不承诺原子撤回。既有独立 bearer 分享链接继续遵循其原生命周期，不在此轮改造成管理权绑定链接。

持续授权结果必须明确区分三态（基础设施错误另用 Result::Err）：

| 结果 | 条件与派发行为 |
| --- | --- |
| `Deliver` | 绑定身份/资源资格有效且本条消息可见；检查通过后才开始发送 |
| `SkipMessage` | 绑定仍有效，但本条是该 participant 不可见的 FullOnly 或发给其他 Actor 的 Directed 消息；仅丢弃本条，连接/订阅保持，后续 public/本人定向消息仍可正常送达 |
| `InvalidateBinding` | 当前身份/选定 Bot authority/资源成员资格已失效，或该绑定按现有 scope-change 合同必须重建；失效该绑定并停止其受保护积压，不波及其他独立合法绑定 |

先验证绑定的当前授权，再判断单条可见性；不能因为一帧不可见而跳过撤权复核，也不能把 FullOnly/Directed 过滤结果等同撤权。SkipMessage/InvalidateBinding 均不允许用 run/session fallback 重试同一不可发送目标；SkipMessage 不是投递故障或“缺少可用连接”。后续新事件仍独立鉴权；Err 按第 17.2 节 fail closed，不伪装成 SkipMessage。

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
- Human-originated Group create/add-member 的 sponsorship：owner/manager Bot 可共同入群而无需 Bot↔Bot friend edge；Bot-originated create/add-member 继续使用 public/friendship 规则；初始 Session 和后续 Human 视角访问必须保持同一 authority。
- Session list/detail/launch/reactivate/update/delete/complete/participants/messages/collect/files/token。
- Friendship acting actor、request decider/cancel、invitation 和相关权限的下游 owner gate。
- Workbench HTTP/WS、session-bound token、connection registry、frontend delivery/run fallback；另有 Human 出向 SSE 或 Provider 转发至 Human 出口的链路时同样覆盖（第 14 节已界定当前无 Human 出向 SSE adapter）。
- owner authority cutover、mine、Bot PATCH/candidate、Group/Session/legacy 二次鉴权、onboard、ensure-human、Provider 注册/切换与当前 owner 通知消费方；好友审批接收人需解析当前 BCS owner，历史展示继续读 created_by，禁止机械替换全部创建来源字段。首次注册必须写 owner edge/version/default profile，重复注册不得重置 ownership。
- Memory/SQLite/MySQL repository、strict errors、Noop/recording fixtures、bootstrap 与独立测试装配。
- Team manager sync 的可信调用方、团队对本 Bot 的 manager 快照/版本、Bot 团队绑定和 source-scoped manager 查询；当前 OrganizationMember 只表示 Bot 成员，不能替代 Human team membership。

Frontend 联动位置（仓库根相对路径）：`apps/frontend-nextgen/src/services/backendApi/collaboration/collaborationBotController.ts`、`apps/frontend-nextgen/src/services/workspace/identityService.ts`、`apps/frontend-nextgen/src/services/workspace/groupService.ts` 与 `apps/frontend-nextgen/src/services/collaborationPrivacy/mappers.ts`。它们需接收并保留标签、展示 owner/manager、复用身份切换，失权 403 后刷新身份列表。不能再通过 Bot ID 后缀或前端 `created_by == me` 判断当前 owner 或筛掉 manager；`apps/frontend-nextgen/src/assets/TaskPanel/GroupDrillDown.tsx` 也存在这种视角推导，需要按显式可控集合改造。

这些前端变更由其模块按自身 AGENTS/契约实施；本轮不跨模块写业务代码。Backend 与 Gateway 若另有 owner-only 拦截，应在端到端接入验证中明确暴露，而非通过伪造 creator 绕过。

Frontend 同步接入转交按钮、收件列表、确认/拒绝/取消与唯一 pending 展示；不得把 ownership 转交描述成外部资产移交。

### 15.2 合同修订与兼容性分类

必须随实现更新：

- `api-contracts/v1/openapi/bots.yaml` 承载 mine/manager/ownership 的 fragment；`api-contracts/v1/domain-models.yaml` 与 `api-contracts/v1/openapi.yaml`（均在 openapi/ 目录**之外**的顶层）同步新增 `access_relation` 枚举、manager/transfer 相关 schema。`api-contracts/v1/openapi/register.yaml` 及 Register/ProviderRegistration Service API 同步首次原子初始化与失败语义。Team sync 加入 `api-contracts/v1/internal.yaml` 的独立 fragment，声明无需 User/App/Bot Principal 但必须专用同步凭证。现有 validator 默认仅接纳 /api/v1/collaboration；新增 /api/v1/bots/manager-sources 路径需精确登记和负例测试，不能放宽成整个 /api/v1 均免检。
- `groups.yaml`、`sessions.yaml`、`session-files.yaml`、`connections.yaml`、`friendships.yaml`、`invitations.yaml`：owned-or-managed 与仍然保留的参与/角色限制。
  `groups.yaml` 还必须明确 Human originator 的 owner/manager sponsorship 与 Bot originator 的 friendship 规则不是同一个谓词。
- `api-contracts/v1/gateway-principal/contract.md` 及相关 identity-policy 标注：不改变身份声明的真实归属，明确应用层动态代行授权。
- edge permission 旧设计的“任意有效边均为调用授权”描述，标注本文对新增 Owner/Manager 的隔离修订；保留新基线 ConnectService 的 request_auth 传递及好友同步独立边界。
- `CHANGELOG.md`、相关 crate `CONTEXT.md`、conformance 映射、HTTP endpoint 与 CLI coverage 清单。

兼容性分类：

- HTTP：mine 新字段是结构增量，但返回集合扩大是有意的行为变化；消费者不得继续把 mine 当作纯 owner 清单。通用 Bot 响应不变化。
- Rust Service/Repo API：`Page<Bot>` → `Page<MyBot>`、新必需方法、混合身份异步授权是编译期合同变更，更新所有消费者/实现/test doubles。
- Persistence：新增角色枚举、ownership_version、manager 审计表 `bot_manager_changes`、业务代行审计表 `bcs_bot_action_audits`、transfer 表与角色/pending 唯一约束，需要显式迁移；不是“无 schema 变化”。业务写端口必须显式传播操作上下文，并更新对应 store/conformance。
- 认证：不改变认证来源、token 签名/owner claims；新 Hook 依赖当前可信 User 身份与实时 BCS role edges，不将签名 owner_id 等同当前 BCS owner，也不依赖待实现的 auth-plugin-chain。Team sync 是 service-to-service 凭证边界，不借用 Human owner claim。
- 管理名单不写部署 TOML；身份/URL/凭据校验沿用配置注入。team sync 的 verifier/密钥作用域需显式装配并纳入配置 schema/secret source，不在服务里读环境、复用注册 HMAC 的 purpose 或硬编码私有端点；此前“无新增配置”不应覆盖尚需设计的专用凭证配置。

## 16. 迁移、发布与回滚

### 16.1 初始化与旧数据治理

1. 用新增迁移建立 schema/索引，停止不兼容旧版本的 authority 写入口；不得改历史迁移或 checksums。
2. 仅对目标 env 的未删除 physical Bot 生成 candidate（Human row 不迁成可转交 Bot owner）：使用非空合法 created_by 作为候选并对照 legacy creator；不存在 Human、冲突 creator、已初始化 owner/version 不一致均进入治理。保存 Bot/env、候选、来源、批次与原因；无合法 created_by 不靠 ID 后缀自动 claim。
3. 对 owner 缺失、主体不存在、多个冲突 creator 或迁移前已有特殊授权的数据，阻止自动迁移并列入人工确认清单。初始化版本 0 的对象不得开启转交；完整权限读切换以受支持的 live Bots 完成治理为门禁。
4. 对 version=0 的无冲突 candidate，在同一存储事务写 owner edge、version=1 与迁移审计，created_by 不变；按有限批次提交/记录检查点，不做全库长事务。已初始化对象只核对/跳过，不在重跑中根据旧 created_by 重置转交后的 owner/version；冲突 fail closed。
5. 需要保留的旧 creator 访问须经审核转为明确来源 manager，不能永久 OR 到 Hook。保留现有 Provider/ref tombstone、Bot Provider metadata 和 gateway projection，不从 OwnerEdge 推导 Provider 所有权，也不重建 runtime token。
6. owner 边与 ownership_version=1 必须原子初始化，不同时 seed owner 的 manager 边。新 Bot 创建走第 13.3 节 initialization contract，重复 onboarding/Provider switch 不改已初始化 ownership。
7. role 查询、mine、HTTP/WS、legacy 二次鉴权、好友审批接收人等当前 owner 消费方在同一兼容版本中切换；不保留无限期双读兜底。切换前需要完成 candidate 对账、冲突处置和 owner edge 完整性检查。
8. 暂不转交的仅 Bot runtime/无 Human owner 对象不自动补造 Human；它们保留原 runtime 身份能力，但不冒充已初始化的可转交 Human-owned Bot。

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
| accept 正常提交 | 1 个 store 事务调用；读取同上及固定角色边，修改 2 条 owner 边、1 条新 manager 边、N 条接收方 direct/ownership_transfer 来源 manager 边（N 按来源数有界、不含 `team/*`）、1 Bot 版本、1 transfer；来源边按批量 IN 读、不按行逐查，SQL 步骤预算不超过 12（N 有界） |
| accept 发现 owner/version 失配 | 相同 scoped 加锁/校验，无角色边或 Bot 版本写；最多更新 1 条 pending 为 invalidated 后提交，SQL 步骤预算不超过正常 accept 的 12 步 |
| reject/cancel | 固定 1 Bot/1 transfer/必要身份检查，至多 1 transfer 终态写；不遍历 manager 集合 |
| 收件/发件分页 | count + page 至多 2 次 scoped 查询，最多返回 100 条；不逐条额外查 Bot/权限，使用名称快照与批量投影 |
| mine | owner/manager 一次集合查询加现有批量 Bot hydration；不能每个 Bot 单独查 owner/transfer |
| team manager sync | 1 个 Bot 级事务；批量读取当前 team 来源、成员快照与版本，批量 upsert/revoke 差异；不得对每个 Human/团队执行 N+1 查询，成员规模超过实现上限时拒绝或分批协议化 |
| 普通业务操作审计 | 同库实际状态变化在既有事务内增加一批审计 INSERT；无变化不造 applied 行。外部副作用另计 admitted 与终态各一次持久化（可合并其既有 durable command/结果事务），不在事务内等网络；批量操作批量写，不逐字段/N+1，计入各用例 SQL 与锁预算 |

DbPlugin 的一次 transaction 调用内部仍可能有多条 SQL 往返，不能将其报告为“只有一条 DB 查询”。索引 count 的实际扫描行数可能随收件箱历史增长；验证深分页/大量历史的 EXPLAIN，不能把返回 100 条说成只扫描 100 行。

锁竞争超时返回可重试基础设施错误；不无限自动重试或忙循环。客户端使用同一 client_request_id/transfer_id 安全重试。跨 Bot 并发、两个用户互相转交、数据库持续失败路径都需有查询次数与锁等待验证。事务内不做网络调用，不扫描 Group/Session 历史。

### 17.2 WS/SSE 出站持续授权预算

**基线选择：同一事件内按有界派发批次读取 scoped authority，而不是承诺整个事件永远只有一条查询。** 批次仅合并当前可一起派发且上下文明确的目标；不存在跨事件、TTL 或 run 绑定级的正向缓存。工程默认上限为每批 **128 个 distinct 授权上下文**，不是 128 个 user_id；不同视角和资源范围分别占位。该上限需在 SQLite/MySQL 最大参数数、查询计划与共享连接池上验证，不能为凑“一次查询”无限扩展 IN-list。

| 路径 | 新增角色读取预算与限制 |
| --- | --- |
| 同事件的单个派发批次 | 1 次 scoped 批量查询，最多 128 个完整上下文；逐连接区分 Deliver/SkipMessage/InvalidateBinding，相同 User 的不同视角不能互相放行 |
| 单事件首次 fan-out，K 个 distinct 上下文 | 初次批量查询数为 `ceil(K / 128)`；读取失败只允许停止/失效，不把未查证目标入队 |
| 已入队后实际出队发送 | 每次新的出队派发重新读取；当前独立 writer 每受保护帧可能增加 1 次单目标查询，不复用入队前结果，不跨事件复用 |
| Run fallback / Interaction replay / SSE | 每次实际重新派发另计新的批次/单目标读取，已拒绝绑定不能通过 fallback 绕过 |
| 授权查询失败 | 停止对应批次/连接的受保护派发并关闭/失效；不放行旧结果，不在后续积压帧上无限重试；停止前尚未查证的数据不能发送 |

测量窗口内新增角色 SQL 预算为 `Σ ceil(K_e / 128) + Q_dequeue + Q_redispatch`：第一项是各事件的首次批量检查，后两项为未计入首项的出队复核与重新派发读取，实际统计时不得重复计数。对直接送 writer、没有单独入队检查的路径，按其实际派发检查记一次，不强加不存在的 fan-out 查询。

`E 次/秒` 只适用于每事件不超过一个批次、无额外出队复核或重派发的理想路径。当前 per-connection mpsc 路径若 E=50、每事件 20 个目标均实际发送，入队前 50 次加出队前 1,000 次，约 **1,050 次/秒**，还不包含原有资源查询；该例不是已测吞吐或上线承诺。不得以“采用批量”宣称整体固定为 50 次/秒。若成本超出容量，应先评审实际发送前的有界合批实现并更新预算/测试，不能改用 TTL 或删除出队复核。

**两阶段检查的分工与可删冗余**：入队前的批量检查是有界早退优化——让明显无权帧不进受保护队列、尽早失效绑定；实际发送前的出队/重派发复核是**唯一权威判定点**，覆盖排队期间发生的撤权。仅保留出队复核同样满足第 14 节全部撤权语义（判定基于派发时点的已提交事实），代价是队列里堆积更多将被丢弃的帧、撤权检测推迟到发送循环；保留入队批次是容量权衡，不是双重许可。容量吃紧时，评审通过后可删入队检查并同步本节预算——这不削弱撤权语义；相对的，删除出队复核、或把入队批次当 writer 通行证，属于禁止的语义降级。

上述 SQL 预算针对 SQLite/MySQL 装配，Memory 只计等价 repo 调用。新增 authority 读取与现有 Group/Session 成员、角色、视角读取分别追踪到实际 store/cache；一次 Hook 不等于整个授权只有一次 SQL。显式 Bot 视角精确查角色；Human 可控参与者集合判定用资源内批量/exists，不按每个 Bot 查询、不枚举全站或全部 owned/managed Bot。扫描行数随相关参与者/索引候选数增长，返回布尔不代表只扫描一行。

派发并发与等待队列有界并服从共享连接池预算；出队后按第 14 节复核，不在数据库事务或锁内等待网络发送。批次查询前后不得长期持有 registry 全局写锁；查询返回后复核 conn_id/绑定代次/关闭状态，不能将授权结果投给替换连接。持续数据库故障使绑定失效并采用有界重连退避，关闭后停止积压事件的权限查询。

验证必须覆盖 1/128/129 个上下文、同用户同视角多连接、同用户不同视角（一个已撤权）、慢连接排队后撤权、双实例、连续帧、重放/fallback 与持续故障。记录首次批量、出队和重新派发 SQL 次数、批次大小、扫描行数、连接池等待；断言新的检查能看到已提交撤权，不可见帧只 SkipMessage 且下一条 public 消息可送达，真正失权才 InvalidateBinding，失败/关闭后不再发送或无限查询。预算是实施验收项，不是仅改变 OT22 的措辞。

## 18. 验收与验证要求

### 18.1 管理权限验收（AC01—AC29）

| 编号 | 场景与预期 |
| --- | --- |
| AC01 | owner A 授予 B 管理 Bot X；B.mine 的 X 返回 access_relation=manager，A.mine 的 X 返回 access_relation=owner。 |
| AC02 | owner/manager 重叠、多个管理边历史、Human row：不重复，标签稳定。 |
| AC03 | owned 与 managed 时间交错、多页、空页、全部现有 filters：先并集过滤再计数分页。 |
| AC04 | B 以 X 作为 view 查询 Group、Session、消息，与 A 以 X 查询结果与 scope 相同。 |
| AC05 | 不传 view 仍为 Human；session-only 不变 formal member；空群读取不写 Session。 |
| AC06 | X 不在某 Session，即使 X 在父 Group，B 也不能越过原 owner 条件读取消息/文件。 |
| AC07 | X 是 worker：B 不能改群管理设置；X 是 driver/manager：B 取得 A 原有对应权限。 |
| AC08 | Bot patch、Group/Session/参与者、好友审批/取消、invitation、launch/消息控制、收藏、文件 mutation/share、workspace 与 legacy gate 完成 owner/manager 对照；真实业务审计保留 operator/effective actor，Bot-only 不伪造 Human。同库审计失败回滚业务；外部 I/O 前审计失败不执行、I/O 后持久化失败不假报成功或自动重放；幂等重试不重复该操作的审计步骤。 |
| AC09 | manager 可增删 manager、自撤销 direct/ownership_transfer；owner 不可经 manager API 删除；并发互撤按事务次序决定结果。direct+team 自撤销后仍有 manager，team-only 自 DELETE 为 200/revoked=false；最后来源撤销后下一管理请求为 403。 |
| AC10 | 无权限用户、Bot-only、App-only、AccessKey-only、其他 Human 视角、跨 env 一律不能借管理 API 提权。 |
| AC11 | 默认 profile wildcard、public、friend、反向边、Bot→Bot、间接好友都不构成 manager。 |
| AC12 | 仅有 manager 时不出现在 friend 列表、不进入 A2A runtime grant；单独撤好友不影响 manager，反之亦然。 |
| AC13 | 授予/撤销幂等；revoke 后再次 grant 真正恢复 approved，不返回假成功。 |
| AC14 | 边写/审计写/提交失败全回滚；读或解码失败报错，不返回空 mine/空列表伪装成功。 |
| AC15 | 撤权后新 HTTP、旧 token 重连、已连接 WS 入站/出站、Interaction replay、run fallback/SSE 不再通过已撤销路径；双实例同样成立。连续帧/同 run 不复用旧授权，读取失败停止派发并失效绑定，不经 fallback 绕过。 |
| AC16 | B 还有独立合法参与关系时，撤销 X 的管理权不剥夺该独立访问；显式 X 视角仍拒绝。合法 participant 的 FullOnly/他人 Directed 帧只 SkipMessage，不失效绑定；下一条 public/本人 Directed 可收到，无 fallback 误发；实际撤权才 InvalidateBinding。 |
| AC17 | User+Bot owner claim 不等但当前 owner/manager 有效时合法代行；二者均无效时拒绝；不伪造 owner claim。 |
| AC18 | Bot/Human 删除后同 ID 重建不恢复旧管理边；重启持久化结果不变。 |
| AC19 | 转交并撤销原 owner 的 manager 后，所有 action 均不能因旧 is_creator、created_by、Bot ID 后缀或重新 onboard 恢复权限。 |
| AC20 | 管理审计写入专用 `bot_manager_changes`，不进入好友 inbox/Connect 决策；human/service/system 操作者分类准确、不伪造 Human。平台无 Human Principal 仍可按 scope 同步并留下 service 审计；operation_id/幂等回执可关联，空/无差异快照也保存回执且不造差异审计，重试不重复写。 |
| AC21 | mine 每个 item 必须序列化非空 access_relation，值仅为 owner/manager；覆盖多页、物理 Bot 与本人 Human、创建者不同但当前 owner、创建者相同但仅 manager 的情况；无权 Bot 不返回，OpenAPI required/enum 与 DTO 一致。 |
| AC22 | Human A owns Bot X and manages Bot Y；X/Y 均为 protected 且没有 friend edge；Human originator 创建 private Group(X,Y) 成功，不创建 friend edge；同样输入创建 public Group 仍按既有约束拒绝。 |
| AC23 | 上述 Group/初始 Session 详情与 X/Y 合法 Bot 视角支持 owner/manager；Human 实际参与时其无 view_bot_id 列表/消息可读，未参与时不绕过成员限制；后续 launch 不因 managed-only 关系被 creator 查询误拒。 |
| AC24 | Bot-originated 创建仍不使用共同 Human sponsorship；Bot X 拉 Bot Y 时，仍需目标 public 或既有 Bot friendship/reachability。 |
| AC25 | Human A 先通过已有 Group 的管理授权，再以有效 manager 身份添加非好友 protected/private Bot Y，private Group 下成功；仅管 Y 不足以管理群，hidden/失权均拒绝；public Group 仍拒绝非 public Bot。 |
| AC26 | 原 direct manager API 不接受 `team` 参数；成功授予的 manager source 为 direct/manual，team sync 不会删除；DELETE 撤销该用户的 direct 与 ownership_transfer 来源，但不撤销 `team/*` 来源并在响应中报告 remaining_team_sources，user 无其他来源时 revoked=true 才表示彻底失去管理权。 |
| AC27 | operation=move 将 Bot 从 team-old 移到 team-new：A 是旧 team/* 来源 manager 且仍出现在 move 请求的 manager_user_ids 快照中时保留 team manager；B 不在新快照的 manager_user_ids 中且没有来自其他当前 team 的来源时撤销 team manager；owner/direct/ownership_transfer 来源不变。 |
| AC28 | Bot 同时属于多个团队时，有效 team manager 是所有当前 `team/*` 来源 manager 的并集——来自各团队对本 Bot 的 manager_user_ids 快照，而非团队成员全集；团队中的普通成员不在快照列表中时不获得 manager；移除一个团队的来源不撤销仍由其他团队来源提供的 manager。 |
| AC29 | 缺少/无效可信平台凭证、非法 operation/move 目标、过期 membership snapshot 或部分写失败均不产生部分 manager 变更；重复 idempotency_key 不重复写入；空 manager_user_ids 快照经完整校验后撤销该 team/* 来源全部 manager 边，校验失败的空快照返回 400 不产生撤权；无 membership_version 时按有序完整快照语义运行，提供版本后拒绝旧版本覆盖新状态。 |

### 18.2 ownership 转交/初始化验收（OT01—OT27）

| 编号 | 验收 |
| --- | --- |
| OT01 | A 发起给 B，接受前 A 仍 owner；B 原本无权则不能读群、Session、文件或 manager 列表。 |
| OT02 | B 接受后唯一 owner=B，A 有 ownership_transfer 来源 manager；B 原 direct/ownership_transfer 来源撤销、team 来源保留且仍由平台治理，其他 manager/friend/runtime 边不变。B.mine 去重标 owner；B 再转给 C 后撤销 B 的非 team 来源，B 仍凭有效 team 来源保持 manager。 |
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
| OT22 | 大 manager 集合和大量历史请求下无 N+1，控制面满足预算。WS/SSE 分别计首次批量、出队复核、重新派发与既有资源查询，覆盖 1/128/129 上下文、同用户不同视角、排队后撤权、最大 fan-out、连续帧及故障后查询停止；不以跨事件缓存或省略出队检查降低成本。 |
| OT23 | 对未删除 physical Bot、version=0、合法无冲突 created_by 原子写 owner/version=1/审计，created_by 不改写；跳过 Human/tombstone；重跑不重置已转交 owner/version；有界批次恢复稳定。 |
| OT24 | 缺失 created_by、多个 creator、主体不存在或数据损坏的 Bot 不自动选 owner；进入治理清单并返回 ownership_not_initialized/一致性错误，不因旧 creator 继续放行。 |
| OT25 | 实际 RegisterService v1/v2、legacy/Admin 入口完成首次 owner/version/profile 初始化；同库必要写失败回滚且不成功，v1 不再吞 onboarding 错误；v2 保持 Provider/ref 重复409、gateway projection、AgentPass/模式和凭据不重放。 |
| OT26 | 已初始化 Bot 的 reconnect、ensure-human、Provider re-register/switch 和名称修复不覆盖 ownership；未注册 Agent 的 bots/me 保持只读/验证凭据，裸 runtime 握手不伪造 Human owner；初始 owner 只取可信注册 scope。 |
| OT27 | owner authority cutover 后，mine、Bot PATCH/candidate、Group/Session view/launch、消息/文件、legacy routes 均不再以 created_by 单独放行；created_by 仅用于来源/迁移/历史展示。 |

### 18.3 测试分层与执行入口

- Domain：枚举 round-trip、管理边形状、禁止主体与 owner/manager 标签优先级。
- Authority/Repo conformance：Memory、SQLite、MySQL 同一套授权/撤权/转交/并发/失败合同；MySQL live run 验证完整迁移、生成列唯一键、大小写身份、事务锁，不能用静态 SQL 测试代替。
- Application：Bot、Group、Session、Invitation、文件与 shared launch 的 owner/manager 成对测试和 ownership 用例，断言 Hook/Repo 确实被调用；Group create/add-member 覆盖 Human sponsorship 与 Bot-originated friendship 分支；team sync 覆盖来源隔离、迁移、并集和 CAS；authority cutover、registration initialization 和旧数据冲突治理覆盖失败/重试路径。
- Delivery：V1/legacy HTTP envelope、共享 ownership_not_initialized=409、owner_changed 已提交失效/重试的错误码、混合身份、分页与 WS/SSE 连续授权合同；按有界派发批次/出队/重派发分别计查询，同用户不同视角、排队与跨帧撤权、持续故障 fail-closed 断言必需。
- Integration：真实本地持久化、重启、双服务实例、好友/manager 并存、转交并发/回滚及撤权完整用户故事；前端身份和转交收件交互。
- 架构：依赖边界、delivery 仅调 application、core 无 transport/DbPlugin、授权检查通过注册 Hook、配置/环境访问约束和 conformance entries。

实现后的主要执行入口（不是本次已经运行的结果）：

```bash
cargo test --manifest-path apps/bcs/Cargo.toml \
  -p bcs-service-api -p bcs-edge-permission -p bcs-edge-permission-store \
  -p bcs-app-bot -p bcs-app-group -p bcs-app-session -p bcs-app-invitation -p bcs-app-register
cargo test --manifest-path apps/bcs/Cargo.toml \
  -p bcs-bot -p bcs-group -p bcs-session -p bcs-api-http -p bcs-http -p bcs-ws \
  -p bcs-jwt -p bcs-message-flow
(cd apps/bcs && bash scripts/ci/arch-check.sh)
python3 apps/bcs/scripts/validate_openapi_contract.py --root apps/bcs/api-contracts/v1
python3 apps/bcs/scripts/validate_openapi_contract.py --root apps/bcs/api-contracts/v1 \
  --entrypoint internal.yaml --path-prefix /api/v1/collaboration/
cargo test --manifest-path apps/bcs/Cargo.toml --workspace
singlebox/ci/singlebox_coverage.sh
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

以上 internal validator 命令验证当前基线；实现新增 team 路径后须按第 15.2 节更新精确允许集合和验证用例。注册回归还需覆盖 `bcs-app-register` 的 v1/v2 facade、`bcs-api-http` 的 register_routes/bot_self_routes，以及 bcs-bot/bcs-bot-store 的 Provider 与 create-once conformance。

Singlebox 产物必须随后使用 verifier 检查上述 reports 目录；保留现有 BCS 用户故事、覆盖率与 endpoint/CLI 覆盖门禁，不能为新 endpoint 降低基线。MySQL live conformance、双实例和所有未能执行的项目必须在实现 PR 中逐项说明原因。

### 18.4 文件规模约束

2026-10-08 在 `94bae68a6c` 上以 `wc -l`（换行符）重新核对；Group 已完成按责任拆分，不能继续引用旧 lib 的 2,970 行或已不存在的 management.rs。当前代表性落点：

| 源文件（相对 apps/bcs/） | 行数 |
| --- | ---: |
| `crates/application/v1/bcs-app-bot/src/lib.rs` | 737 |
| `crates/application/v1/bcs-app-group/src/authorization.rs` | 434 |
| `crates/application/v1/bcs-app-group/src/create.rs` | 582 |
| `crates/application/v1/bcs-app-group/src/service.rs` | 874 |
| `crates/services/bcs-group/src/application/management/create.rs` | 521 |
| `crates/services/bcs-group/src/application/management/guards.rs` | 510 |
| `crates/services/bcs-group/src/application/management/operations.rs` | 669 |
| `crates/application/v1/bcs-app-register/src/lib.rs` | 618 |
| `crates/application/v1/bcs-app-session/src/lib.rs` | 1,818 |
| `crates/application/v1/bcs-app-invitation/src/lib.rs` | 1,153 |
| `crates/services/bcs-edge-permission/src/lib.rs` | 3,797 |
| `crates/services/bcs-edge-permission-store/src/lib.rs` | 2,293 |

未来修改 source file 必须满足 1,000 行上限；已超限的 Session/Invitation/edge lib 需按此次触及职责拆分，不能机械照搬 Group 的旧大文件方案或新增 allowlist。本文是单份设计 Markdown，不是 Rust source；本轮不修改上述源码、不运行全局 cargo fmt。

## 19. 文档验证与评审入口

2026-10-08 上轮重构对齐将设计适配新的 `origin/dev`：文档随 BCS 搬迁，更新当前代码证据、Group 分拆职责、真实注册 facade/Provider 事务、internal 路由与凭证边界、Bot self 例外、迁移基线和测试入口。2026-09-22 的审阅修正（失配物化、持续授权成本、错误码与再授权待决）保留，不能以旧核对结果当作本轮验证。

2026-10-08 评审修订只消减内部矛盾，不改变已确认需求：明确空 manager 快照为经校验的全量撤权；direct manager DELETE 明确来源范围（direct/ownership_transfer，不动 `team/*`，响应报告 remaining_team_sources）；owner 边要求固定 `owner/owner` 来源编码；AC27/AC28 将"团队成员"改为"manager_user_ids 快照"；manager 审计从 `permission_requests` 改为专用 `bot_manager_changes` 表；WS/SSE 当时建议改为按事件 IN-list 批量读取（由本节下述复审的有界批次/出队复核规则进一步修正）；§15.2 合同路径修正至 `api-contracts/v1/` 下实际位置。验收条目总数保持 56 项（AC01—AC29、OT01—OT27），编号不变。

2026-10-08 计划评审补遗（撰写和实施计划后第二轮评审）：非角色边固定 `none/none` 来源编码，封堵迁移回填各方言自行发明常量的漂移面；`bot_manager_changes` 补齐 `gmt_create/gmt_modified`，与仓库每表时间列约定一致；SSE 条款明确仅当存在 Human 出向通道或 Provider 事件转发至 Human 出口时生效，Provider 入站 SSE（`bcs-provider-http`）不套 Human 角色检查；§17.2 显式记录入队/出队两阶段检查的分工，入队检查可经评审删除而不削弱撤权语义。实施计划本轮补充：§12.1(6) 的 operator/effective actor 写审计在追踪表和 Task 11 明确定位断言；Task 1/2 的 `none/none` 编码和 Task 16 的 Provider-vs-Human SSE 区分引述本稿对齐。本轮补遗不改变验收条目（仍为 56 项）。

2026-10-08 终审修订（spec + plan 合并的第三轮评审）：新建仅 expected_owner_version 过时明确归 409/ownership_changed；§7.1 的 Service API 角色枚举名与计划统一为 `BotAccessRelation`；清理 §5.1 误写的 "friend GrantKind"——现有仅 permission_profile/rules，好友是 default-profile permission_profile 边，实施不得新增 friend 枚举；聚焦门禁清单补 bcs-jwt、bcs-message-flow 两 crate。Invalidated/owner_changed 的重试同码语义改由计划 Task 14 的 terminal_reason 复核承载，spec §11.2 行不变。验收条目仍为 56 项。

上轮重构对齐的验证范围：rebase 后 spec 原始内容与备份逐字比对；基线/祖先、路径与源码锚点检查；12 个 source file 行数、8 个 JSON 示例、56 个验收编号、章节/命令路径及 git diff 检查。OpenAPI 检查验证当前基线合同，不代表拟议 API 已实现；上轮实际执行公开 OpenAPI 校验通过 72 个 operation、internal 校验通过 23 个 operation，未修改这些 YAML。

Cargo、完整 architecture gate、MySQL live conformance、双实例、WS/SSE E2E、负载和 Singlebox 未运行：上轮为文档与 Git 历史重整，本轮仅修订设计/计划，没有修改业务代码/schema/config。第 17 节仍是预算，不是压测结果；未来实现需跑第 18.3 节全部相关验证，不可降低 CI。

实施前仍需确认 manager 再授权、team 同步来源与幂等/排序、凭证作用域、初始化原子聚合及迁移治理。设计修订与实施计划不替代产品确认、不授权开始业务代码实现。

还需评审第 6.1 节 team manager sync 的可信调用方、membership snapshot 来源、内部凭证形态，以及 direct/ownership_transfer manager 是否按来源隔离保留。严格“无任何凭证”的匿名接口不在推荐方案内。

2026-10-08 复审修订：管理审计区分 human/service/system 并关联持久同步回执；自撤权按剩余来源判定；OT02 保留接收方 team 来源；WS 使用完整授权上下文、有界 128 项派发批次和出队重新鉴权，成本包含分批/出队/重派发。配套实施计划按任务映射全部 AC01—AC29、OT01—OT27，尚未执行。

本轮文档校验：8 个 JSON 示例解析通过，56 个验收编号完整且均映射到计划任务，代码围栏/路径/空白检查通过；再次运行当前公开/internal OpenAPI validator 分别通过 72/23 个 operation，仅证明未改动的基线合同仍合法，不表示新增接口已实现。

2026-10-08 本轮复审落实：§12.5 明确独立业务审计 schema、必需操作上下文、同库事务与外部副作用失败/重试边界，并分配到 Tasks 1/2/9—12；§14 与 Tasks 15/16 采用 Deliver/SkipMessage/InvalidateBinding 三态，保持合法 participant 的消息过滤不关连接。AC08/AC16 增补对应验证，56 个验收编号和 20 个计划任务均保留。本轮仍未实现业务代码，按用户明确请求 amend 文档提交，不 push。
