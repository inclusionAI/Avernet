# BCS Bot Authority Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用一份 manager 集合实现单项管理、可信应用全量 sync，以及按目标 Human 定位、可续期的 owner 转交。

**Architecture:** 复用 edge_grants，新增严格的 authority Core/Repo 和统一鉴权 Hook。权限写操作在 Bot 行锁保护下与权限审计同事务提交；所有新增 HTTP 接口共用 `/api/v1/collaboration/`，复用已有 Gateway 身份认证。既有业务只接入当前权限判断，WS 在最终发送前检查，普通业务存储不增加审计上下文。

**Tech Stack:** Rust、现有 Axum/Tokio、bcs-service-api、DbPlugin、MySQL/SQLite；不新增服务进程或第三方基础设施。

**Spec:** [BCS Bot owner / manager 简化设计](../specs/2026-10-10-bot-authority-simple-design.md)。执行前完整阅读本文与 spec。

**Baseline:** `origin/dev`，编写时 `7a363f2c68`。本文路径相对 `apps/bcs/`，另有标注除外。状态：待实施计划评审；复选框是未来执行步骤，不表示已经完成。提交步骤仅在另获提交授权后执行。

## Global Constraints

- 本设计所有新增 authority 接口统一使用 `/api/v1/collaboration/`，Human 与可信应用使用同一组路由；既有业务/注册 URL 保持兼容。
- BCS 对每个 Bot 只保存一份 manager 集合；sync 覆盖全部 manager，owner 不变。
- 每次最多 1,000 个去重后的用户，SQL 参数按最多 100 个主体分块，同一 Bot 的全部差异仍是一笔事务。
- 同一 `(env, bot_id, target_owner_actor_id)` 只有一行；同一 Bot 当前最多一条 pending。
- 重新发起刷新请求时间、七天过期时间；客户端不保存 transfer_id、轮次或 ownership version。
- 没有跨轮次防重放承诺；调用方按 Bot 串行处理并终止旧轮决议重试。
- 仅修改 `edge_grants`、`bcs_bots`；仅新增 `bot_ownership_transfers`、`bot_authority_changes`。
- 普通业务表和恢复链不增加权限审计 schema；本期审计仅覆盖权限变更。
- adapter → application → Core → Repo；SQL、锁和事务属于 Store，bootstrap 选择具体实现。
- 本计划 Rust 源文件（含测试）的 **2,500 物理行上限**以 [专项例外记录](../../../../../docs/arch/waivers/2026-10-10-bcs-authority-file-size.md) 和 BCS AGENTS 的限域规则为准，须满足 PR 确认/负责人交接/到期条件；不运行全仓 cargo fmt，不改历史 migration，不弱化 CI。
- 分步实现和评审；所有 authority 消费者切换后才能整体启用。中间步骤不单独发布转交能力。

## Review Focus

1. User 与 App 同时出现：双准入接口不能在 Human 被拒后回退到 App；sync 显式使用 App。Task 5 固定测试。
2. 合法用户 ID 为 `sync` 时，PUT 单项仍只能添加该用户；集合 PUT 才执行覆盖。Task 5 固定路由测试。
3. 响应丢失后，新一轮同目标请求已建立：迟到决议按当前 pending 处理，不能声称旧响应精确重放。Task 3 固定测试。
4. manager 数量超过 mine 首页，或 Bot 通过 legacy Provider 分支删除：仍正确鉴权且无残留有效边。Task 4/6 固定测试。
5. WS 在撤权前已排队，或资源帧经过 Run/fallback：发送前仍检查，失效后停止订阅，单帧不可见不误关连接。Task 7 固定测试。

---

## 1. 修改范围与行数预估

### 1.1 估算口径

以下是基于基线代码入口扫描的工程估算，不是已实现的 diff，也不是硬性行数配额。

- “功能代码”计 Rust/SQL 的新增行与删除行之和，不计纯搬移、生成文件、文档；不是净增行数。
- 测试含 conformance、fixture、HTTP/WS 集成和错误/竞争回归，复用现有框架，避免逐层复制同一案例。
- 表内文件数含新增及修改文件；同组汇总去重。拆超大文件的机械改动另计。

| 改动组 / crates | 主要职责 | 功能代码行 | 测试行 | 文件数 |
| --- | --- | ---: | ---: | ---: |
| bcs-domain、bcs-service-api | authority、生命周期、资源快照和保护绑定契约 | 800–1,200 | 600–950 | 13–19 |
| bcs-edge-permission、bcs-edge-permission-store | 权威读、manager/sync、转交、SQL 验证、权限审计 | 1,500–2,500 | 1,800–2,900 | 18–27 |
| bcs-bot、bcs-bot-store、bcs-app-register | 普通/Provider 原子注册退役、共享 Memory 含真实 profile、runtime 批量观测 | 1,300–2,200 | 1,200–2,000 | 25–36 |
| bcs-app-bot、bcs-api-http | 应用门面、mine 动态过滤、App scope、统一 API | 750–1,250 | 750–1,200 | 12–19 |
| bcs-app-group、bcs-app-session、bcs-app-invitation、bcs-group、bcs-session、bcs-session-file、bcs-message-flow、bcs-eventing、bcs-proposal、bcs-http | 接入、sponsorship、独立 Human 权限与 legacy | 950–1,650 | 1,250–2,100 | 36–53 |
| bcs-group-store、bcs-session-store | 绕过展示缓存的权威资源权限快照，无 schema 改动 | 250–450 | 300–550 | 5–8 |
| bcs-ws | writer、共享 Run 双 sink、alias/fallback、显式视角 | 550–900 | 750–1,250 | 10–16 |
| bcs、bcs-services-container、bcs-admin | 装配、配置、Run 内部适配、迁移及运维 | 450–800 | 500–850 | 12–18 |
| bcs-test-support | 共享 authority fixture 与故障注入 | 0 | 250–400 | 3–5 |
| SQL migration、API 契约等 crate 外文件 | 两个方言 migration；OpenAPI/运行说明另算文档行数 | 300–500 | 0 | 4–6 |
| **合计** | **26 个既有 crate，0 个新增 crate** | **6,850–11,450** | **7,400–12,200** | **138–207** |

功能与测试合计约 **14,250–23,650 行变动**，其中约 **55–70 个新文件**。独立文档、OpenAPI、专项例外和上下文说明另外预留约 1,100–1,800 行。此轮补入资源 Store、Provider 全事务/Memory、runtime 过滤和共享 Run 传播；Task 0 的 SQL/超时可行性结论如需修改 DB 插件，必须先增列 crate 和预算，不能藏入后续任务。

第二轮定点修订把 legacy 注册、独立凭证回执、完整 scope 投影和共享 Memory profile 纳入上述既有分组，不新增 crate/schema；现阶段仍采用该区间估算，Task 0 按补全后的文件清单复核，不能把这些遗漏项留到区间外另行实现。

这不是只改 edge-permission 两个 crate 的功能：manager 要真正操作既有资源，旧创建者要在撤权后失去权限，因此应用、legacy 和 WS 接入必须纳入预算。持久化变化则集中在权限与 Bot 生命周期，不涉及 Group/Session/File/Message/Chat-run Store schema。

### 1.2 本计划的 2,500 行上限与拆分预算

**CI 核查依据（2026-10-10，基线 `7a363f2c68`）：** 已核对 GitHub workflows、BCS 单测/架构检查、E2E、Singlebox 与 pre-push 调用链，未发现针对 BCS Rust 源文件的 1,000 行硬检查或对应白名单。实际存在的 1,000 行硬检查位于仓库根目录下 `apps/backend/tests/community/architecture/test_no_oversized_modules.py`，仅扫描 Backend 的 `src/agentclaw/**/*.py`。根 `AGENTS.md` 的文档要求与当前自动检查覆盖范围应区分。

**专项依据：** [BCS-AUTHORITY-SIZE-2026-10-10](../../../../../docs/arch/waivers/2026-10-10-bcs-authority-file-size.md) 记录本次会话需求方的明确批准、原话及提交 `53b57ba62c` 的证据；BCS AGENTS 引用该限域例外。适用文件为本计划实际新增/修改的 `crates/**/*.rs`，含测试；实现 PR 必须链接记录、登记具名执行负责人和逐文件清单并取得仓库评审确认。2026-11-10 或计划完成时（先到者）复审到期；不满足条件先复审，不能把“无 CI”当成规则已失效。不调整其他模块规则、任何 CI 或 Backend 白名单。

- 按修改后文件的完整物理行数检查；恰好 2,500 行允许，超过仍须按职责拆分。
- 原有 1,001–2,500 行文件无需仅为满足 1,000 行阈值而拆分；新增功能优先放入职责明确的小模块。
- 仅处理本计划实际触及且修改后超限的文件，不开展全仓文件大小治理。

已扫描的预计接入文件如下；“需拆分”按当前基线计算，实施时仍须检查最终行数：

| 文件 | 基线行数 | 按 2,500 行上限处理 |
| --- | ---: | --- |
| `crates/services/bcs-edge-permission/src/lib.rs` | 3,798 | 需拆分 |
| `crates/services/bcs-edge-permission-store/src/lib.rs` | 2,294 | 无需仅因现有行数拆分 |
| `crates/services/bcs-bot-store/src/lib.rs` | 3,936 | 需拆分 |
| `crates/services/bcs-bot-store/src/memory.rs` | 2,859 | 需拆分 |
| `crates/services/bcs-bot/src/application/bot.rs` | 1,608 | 无需仅因现有行数拆分 |
| `crates/bootstrap/bcs/src/server.rs` | 6,976 | 需拆分 |
| `crates/bootstrap/bcs/src/config.rs` | 3,409 | 需拆分 |
| `crates/application/v1/bcs-app-session/src/lib.rs` | 1,818 | 无需仅因现有行数拆分 |
| `crates/application/v1/bcs-app-invitation/src/lib.rs` | 1,153 | 无需仅因现有行数拆分 |
| `crates/services/bcs-eventing/src/subscription.rs` | 1,491 | 无需仅因现有行数拆分 |
| `crates/services/bcs-message-flow/src/group_history.rs` | 1,429 | 无需仅因现有行数拆分 |
| `crates/adapters/ws/bcs-ws/src/bot/dispatcher.rs` | 2,682 | 需拆分；本轮补入真实 Run producer |

该清单当前共 12 个文件，其中 **6 个**超过 2,500 行；`sum(max(lines - 2500, 0))` 为 **8,660 行**。若仅通过搬移使原文件达到上限，这些既有代码对应约 **17,320 行增删**，尚未计新增模块声明、功能变化及其他实际触及文件。

额外机械改动仍暂估 **20,000–35,000 行增删、10–20 个拆分文件**（去重）；功能+测试+机械合计 **34,250–58,650 行、148–227 个文件**，再加文档约 **35,350–60,450 行增删**。这是原始 diff 而非净增；新增调用链中若还有 >2,500 行接入文件，Task 0/1 重新计数后更新，不能把已扫描的六个视作穷尽证明。机械成本不是现有 BCS CI 强制成本。

机械拆分单独评审，获提交授权后单独提交，按职责移动现有函数/测试并保持行为。不得通过扩大 allowlist、删除测试或全仓格式化缩小账面差异。若本次预计改动的新文件或功能逻辑明显超出表中范围，先更新计划及原因。

### 1.3 schema 与依赖边界

- `edge_grants`：新 grant kinds、包含 kind/ref 的唯一键、单 owner 约束；无 manager source 字段。
- `bcs_bots`：新增 ownership_version。
- 两张新表：可复用转交状态、权限追加审计；无 sync receipt、team 状态、初始化账本或普通业务审计表。
- 新功能只使用现有依赖；允许为既有 crate 补显式内部依赖与测试依赖，不引入新的数据库/事务框架。
- token 选型已固定：保持 bcs-jwt 的签名字段、TTL 和 Gateway 协议；签发仅接收 caller/session_id 并验证当前 Session 读取资格，具体 view 在 connect/reconnect 通过既有 `view_actor_id` 选择并重新授权，旧 token 缺省 self。在 app-session/WS 测试中使用真实既有 JWT 实现，不新增第二个 view wire 字段；共享 Run port 的内部 String 签名保留，Workbench 走专用保护 sink。

## 2. 文件组织与责任

下列为功能文件的计划位置；Task 内列出既有接入点。新增模块均在现有 crate 的 lib/mod 中显式导出。

| 范围 | 新建文件 | 职责 |
| --- | --- | --- |
| service-api | `src/types/bot_authority.rs`、`src/core/bot_authority.rs`、`src/port/repo/bot_authority.rs`、`src/port/bot_authority.rs`、`src/application/v1/bot_authority.rs` | 分离值类型、Core、Repo、Hook 和应用门面 |
| edge-permission | `src/authority.rs` | Core 转发、输入和角色策略；不包含 SQL |
| edge-permission-store | `src/authority/{mod,reads,managers,transfers,audit,lifecycle}.rs` | 一个 SQL authority Store；按事务职责拆分；不逐方言复制整个状态机 |
| bot-store | `src/ownership.rs`、`src/controllable.rs`、`src/memory_authority.rs`、`src/memory_permission_profile.rs` | 生命周期组合、mine 查询；在 MemoryBotRepo 同一状态上实现 authority/profile 两个 Repo port |
| app-bot | `src/authority.rs`、`src/authority_policy.rs` | 应用门面、Human/App 选择和授权策略 |
| app-session | `src/delivery_authorization.rs` | WS 每帧资源/视角判定 |
| group-store / session-store | 各自 `src/authorization_snapshot.rs` | 仅主数据源的轻量资源权限读，不改普通展示缓存 |
| bcs-ws | `src/shared/protected_delivery.rs` | 受保护帧/sender 与双 Run sink；不从业务 JSON 还原身份 |
| api-http | `src/v1/bot_authority/{mod,routes,dto}.rs` | 一组统一路由和 DTO；复用签名验证与响应封装 |
| bootstrap | `src/bot_authority_wiring.rs`、`src/config/bot_authority.rs` | 同源装配、可信 app 配置 |
| bcs-admin | `src/bot_authority.rs` | dry-run / apply 入口 |
| test-support | `src/bot_authority.rs`、`src/bot_authority_contract.rs` | 共用场景与 Repo conformance，不在生产代码造测试捷径 |

`bcs-bot-store` 单向依赖 `bcs-edge-permission-store`，仅复用 `authority/lifecycle.rs` 的纯 SQL 事务步骤；后者不能反向依赖前者。MemoryBotRepo、MemoryBotProviderStore、binding/ref projection 共用 `MemoryBotState` 的一个 Arc 和原子更新边界，含 Bot/Human、角色、转交、审计、profile 记录/active-default 索引/ID 分配器及 Provider Bot projection；Provider 配置/凭证不并入此状态。MemoryBotRepo 同时实现既有 `PermissionProfileRepoPort`，注册和 runtime 注入同一个 Arc，不使用 Noop 或第二份 profile Store。禁止 application/Core 依赖 Store，也不能以跨 Store 顺序写冒充原子提交。

## 3. 共享接口约定

这些是拟新增契约，不是基线已有符号。任务不得各自创建同义接口。

```rust
enum BotAccessRelation { Owner, Manager }
enum AuthorityActor {
    Human { user_id: String },
    Service { app_id: i64 },
    Provider { provider_id: String },
    System { name: String },
}
struct AuthorityMutation { mutation_id: String, actor: AuthorityActor }
struct OwnershipInitialization { owner_user_id: String, mutation: AuthorityMutation }
struct BotRoleQuery { user_id: String, bot_id: String }
struct TransferKey { bot_id: String, target_owner_actor_id: String }
enum ManagerEditAction { Grant, Revoke }
enum TransferAction { RequestOrRefresh, Accept, Reject, Cancel }
enum TransferChange { Created, Updated, Unchanged }
enum TransferOutcome {
    Applied { transfer: TransferView, change: TransferChange },
    Conflict { transfer: TransferView, code: AuthorityConflictCode },
}
```

Store/Core 实例绑定 bootstrap 注入的 env。用户参数中的 env 不得覆盖实例范围；tenant 与 App scope 在应用边界核验。

| 契约 | 拟新增签名 / 返回值 |
| --- | --- |
| BotAuthorityRepoPort / BotAuthorityCoreService | `role(&self, user_id: &str, bot_id: &str) -> ServiceResult<Option<BotAccessRelation>>`；`roles_for(&self, queries: &[BotRoleQuery]) -> ServiceResult<Vec<Option<BotAccessRelation>>>`，与输入等长同序 |
| 同上，查询 | `ownership(&self, bot_id: &str) -> ServiceResult<BotOwnershipView>`；`managers(&self, query: ManagerListQuery) -> ServiceResult<ManagerPage>`；`transfer(&self, key: &TransferKey) -> ServiceResult<Option<TransferView>>`；`transfers(&self, query: TransferListQuery) -> ServiceResult<TransferPage>` |
| 同上，写 | `edit_manager(&self, command: ManagerEdit) -> ServiceResult<ManagerSetChange>`；`sync_managers(&self, command: ManagerSync) -> ServiceResult<ManagerSetChange>`；`change_transfer(&self, command: TransferCommand) -> ServiceResult<TransferOutcome>` |
| BotAuthorityPort | `role`、`roles_for` 同上；`can_manage(user_id, bot_id) -> ServiceResult<bool>`；`require_owner(user_id, bot_id)`、`require_manage(user_id, bot_id) -> ServiceResult<()>` |
| BotAuthorityService（Application） | 对外暴露同名查询/命令方法，增加 `caller: &AuthenticatedCaller`，返回 `Result<T, ApplicationError>`；创建 AuthorityMutation，验证 Human/App 权限后调用 Core |
| BotControlPlaneCoreService / Repo | `list_controllable(&self, query: ControllableCandidateQuery) -> ServiceResult<Vec<ControlledBotCandidate>>`，只持久化筛选、稳定排序，无客户端分页；保留 list_by_creator |
| BotRegistryCoreService | `try_runtime_activity_for(&self, bot_ids: &[String]) -> ServiceResult<RuntimeActivitySnapshot>`；一次 WS 集合观测及批量 Provider 解析，错误不吞成离线 |
| BotRegistryCoreService / BotRepoPort | `initialize_ownership(bot_id, owner_user_id, mutation)`、`retire_with_authority(bot_id, mutation)`；前者返回当前 BotOwnershipView，后者返回 bool；均为 ServiceResult |
| 既有 PermissionProfileRepoPort，由 MemoryBotRepo 新增实现 | `ensure_default_profile(&self, bot_id: &str, env: &str) -> ServiceResult<u64>`；`get_active_default(&self, bot_id: &str, env: &str) -> Option<PermissionProfile>`；`upsert_revision(&self, profile: PermissionProfile) -> ServiceResult<()>`；不改 port 签名 |
| GroupCoreService / GroupRepoPort | `read_authorization_snapshot(&self, group_id: &str) -> ServiceResult<Option<GroupAuthorizationSnapshot>>` |
| 新增窄 SessionAuthorizationCoreService / 既有 SessionRepoPort | `read_authorization_snapshot(&self, session_id: &str) -> ServiceResult<Option<SessionAuthorizationSnapshot>>`；Core 实现在 bcs-session，不假定基线已有 SessionCoreService |
| DeliveryAuthorizationService（Application） | `authorize_delivery(&self, binding: &ProtectedDeliveryBinding, message: &DeliveryVisibility) -> Result<DeliveryDecision, ApplicationError>`；DB/Core 故障去敏并映射 InvalidateBinding |

命令/结果字段规定：ManagerEdit = Bot ID、目标 user_id、action、mutation；ManagerSync = Bot ID、去重用户集合、mutation；ManagerSetChange = added/removed/total。TransferCommand = key/action/mutation；Conflict 是可已提交的业务冲突，不等于回滚错误；码包含 transfer_pending/owner_changed/transfer_expired/transfer_state_conflict。TransferView 按 spec 投影；ManagerPage 为 owner 摘要/items/total；列表 offset=0、limit=50、最大100，以 ID/时间+唯一键稳定排序。ControllableCandidateQuery 含 user_id 及既有持久化过滤，无 reachability/offset/limit；ControlledBotCandidate 含 id/kind/status/access_relation；RuntimeActivitySnapshot 含观测时刻与 active ID 集合。Application 持有完整请求，在所有候选上动态筛选后分页，public Page 不变。

ManagerListQuery = bot_id/offset/limit；TransferListQuery = user_id/direction(received 或 sent)/statuses/offset/limit，其中 user_id 由 Application 从认证身份填写。BotOwnershipView 至少返回 bot_id、owner_user_id、owner_actor_id；内部 version 另由 Core/Store 的内部快照类型持有，不要求客户端提交。上述初始化/退役方法入参按 `&str` 与 `AuthorityMutation` 传递；公开响应不得序列化内部 mutation 或 SQL 错误。

权限审计的 mutation 只在以上权限命令中传递，不扩散到 Group、Session、File、Message 等普通写接口。共享时间口径取事务数据库时间，测试用可控数据库时间参数或确定性时钟适配；不靠 sleep 构造到期。

### 3.1 生命周期的可调用契约

| 调用链 | 冻结的命令与返回值 |
| --- | --- |
| legacy `POST /register` 的 v1/v2 token 分支 → `RegisterService.register_bot` | `bcs-http` 按 token 版本保留 legacy 参数容忍和响应协议，两分支均委托 facade，不调用 connect/onboard |
| OpenAPI `POST /openapi/v1/collaboration/register` 的 v1/v2 token 分支 → 同一 RegisterService | `bcs-api-http` 保持既有严格参数解析和 envelope；不是新建第二个注册用例，也不迁移既有 URL |
| `RegisterService.register_bot` 验证 v1 Human token → Registry Core → BotRepo | Core 新增 `register_with_ownership(TrustedBotRegistration) -> ServiceResult<CommittedBotRegistration>`；命令含受信任 user_id、现有注册能力和 mutation，Core 分配 ID/token；Repo 调用 Task 4 的组合创建方法，Application 将已提交回执映射为既有 BotRegistration |
| `RegisterService` Provider v2 → ProviderRegistrationCoreService → BotProviderRepo | Core 新增 `register_with_ownership(RegisterProviderBot, OwnershipInitialization) -> ServiceResult<ProviderRegistrationResult>`；复用现有 Provider 认证、mode/ref/webhook 验证与结果类型，Repo 组合 Bot/Human/owner/profile/binding/audit |
| ProviderManagementService → ProviderCoreService → BotProviderRepo | Core 新增 `retire_registered_bot(RetireProviderBotParams) -> ServiceResult<ProviderRetirement>`，在既有凭证核验后构造 Provider actor；Repo 新增 `retire_with_authority(ProviderRetire) -> ServiceResult<ProviderRetirement>` |
| 普通 Bot Management → Registry Core → BotRepo | 使用既定 `retire_with_authority(bot_id, mutation)`；不得用它替代 Provider 专用事务后遗漏 projection |

`ProviderRetire` 含已验证 provider_id、bot_id、mutation 及预期分支 `Metadata | BindingFallback | LegacyUnbound`；Store 持锁复验分支和既有归属规则。`ProviderRetirement` 含 bot_id、changed、分支及既有外部清理所需的非凭证定位信息；从 tombstone 重试也可取回。实际物理表仍为现有 bcs_bots/binding projection，不新增生命周期表。`RetireProviderBotParams` 放 service-api `core/provider.rs`，字段为 provider_id、provider_admin_token、provider_bot_ref、allow_unbound_owner_suffixed_bot；Application 映射既有 Delete 命令，Core 在原有授权限制内解析 Bot，不能让该 bool 单独授予删除权。token 只用于核验，不能进入 mutation/审计/返回值。

`TrustedBotRegistration` 放 service-api `core/registry.rs`，含 `capabilities: BotCapabilities`、`initialization: OwnershipInitialization`；owner_user_id 只取已验证注册 token，mutation 保留真实发起者。同模块新增独立 `CommittedBotRegistration { bot_uuid: String, bot_name: String, bot_token: String }`，仅在事务成功后返回 Application；不复用 transport DTO，不增加通用查询/日志序列化能力，也不把凭证写入审计。保留该模块已 re-export 的 domain `RegisteredBot` 及其 get/list/discovery 字段，二者须能同时导入。Core 将初始化 owner 同时写入首次 created_by，后续两者独立。

两条 HTTP surface × 两种 token 的兼容矩阵固定如下；所有成功凭证均来自同一已提交 facade：

| Surface / token | 参数契约 | 成功返回 |
| --- | --- | --- |
| legacy `/register` / v1 | `bot-name`/`bot_name`、trim、2–64 字符；忽略 mode/provider_bot_ref/webhook_url，映射 facade 时均不传；未知 mode 也不触发 scoped parser 错误 | 200 扁平 BotRegistration JSON、no-store |
| legacy `/register` / v2 | 保留 scoped mode/ref/webhook 校验和既有错误映射 | 200 扁平 BotRegistration JSON、no-store |
| OpenAPI register / v1 | 保留严格 query 解析；mode 仅缺省/plugin，ref/webhook 不允许；未知枚举仍 400 | 201 既有 envelope、no-store |
| OpenAPI register / v2 | 保留 mode/ref/webhook 与 Provider 准入校验 | 201 既有 envelope、no-store |

legacy adapter 可复用已有经过验证的 token 版本分流来适配参数/错误；facade 仍自行验签与校验过期，不接受 adapter 传入的可信 owner。不得把 legacy v1 直接改为 scoped parser，或把字段容忍扩散到 OpenAPI。

快照类型放 service-api；以下字段完整冻结，复用 domain 的枚举，不含消息正文、凭证或全量配置：

| 类型 | 字段 |
| --- | --- |
| `ParticipantAuthorizationSnapshot` | `actor_id: String`、`actor_kind: ActorKind`、`role: ParticipantRole`、`effective_mode: ParticipantMode`、`message_view_scope: MessageViewScope` |
| `GroupAuthorizationSnapshot` | `env/id/live`、`status: GroupStatus`、`record_status`、`visibility`、`group_kind: GroupKind`、`group_strategy: GroupStrategy`、`driver_actor_id`、`originator_actor_id`（既有缺省 driver 规则）、`participants: Vec<ParticipantAuthorizationSnapshot>` |
| `SessionAuthorizationSnapshot` | `env/id/live/group_id`、`status: SessionStatus`、`session_kind: SessionKind`、`created_by: Option<String>`（真实 actor）、`message_visibility_version: u8`、`participant_join_seq: Option<serde_json::Value>`（沿用 domain 的 actor→sequence 数据及缺省语义）、`participants: Vec<ParticipantAuthorizationSnapshot>` |

GroupCore/SessionAuthorizationCore 只转发严格 port，不能以 try_get 补缓存失败。历史 mode/scope 缺省按既有持久化解码规则投影；新快照不能漏字段再默认 Full。Group strategy 决定额外 Manager 管理角色；Session 消息用当前 Session participant scope，关联 Group 仅提供 strategy/资源规则，不把 Group seed 覆盖进已存在 Session。writer 根据本次快照和可信 producer domain/audience 判定，连接时的 HumanMessageView 不能充当长期授权缓存。

## 4. 执行顺序

`Task 0（实施准入）→ Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6 → Task 7 → Task 8`。

阶段划分：1–4 形成可独立测试的权限内核；5 完成统一 API；6–7 完成全部消费者；8 验证整栈和发布。阶段完成不意味着可以提前开放转交。

### Task 0：契约闭合与事务可行性门禁

**Files:** 本 spec/plan、专项例外记录；未来证据写入 `docs/superpowers/plans/2026-10-10-bot-authority-feasibility.md`，生产文件仍不变；试验使用测试数据库和隔离 harness，不执行生产迁移。

**Interfaces:** 冻结 spec §3.3 授权事实矩阵、第 3.1 节两个 HTTP surface/token 兼容矩阵及生命周期调用链、§8.0 mine 算法及 §8.3 两类资源快照/Run sink。此步骤先验证现有 DbPlugin 能实现 SQL 方案，不提出 callback 事务框架。

- [ ] **0.1 核对范围和责任。** 登记实现 PR 的负责人、2500 行例外文件清单/复审日期；入口矩阵逐项写清 Human→Bot、Human self、资源角色、Provider、App、Bot-only 的选择。补入 Group/Session Store 和共享 Run/内部 port 后复核 crate、文件和机械拆分预算。
- [ ] **0.2 给出静态事务步骤草图。** 事务外有界分页读取完整 manager 集合 S，只作参数准备；事务内锁 Bot→Human，并以当前锁定读验证 owner/调用者、`count(current)=|S|` 且 S 每个 <=100 ID 块仍完整存在且 shape/status 正确，所有 guard 成功后才写。空 S 必须验证 count=0。MySQL 不能让 RR 旧快照参与这些 guard；采用 current/locking read，并由两连接测试证明。集合中的重复/缺失/替换即回滚，不能只比数量。
- [ ] **0.3 验证 guard 和差异步骤。** 使用 `Query` 返回授权/集合 guard token，下一步骤以 `DbTransactionParam::query_result` 引用必需的结果行，缺行使事务失败；不能把普通零行查询或 `stop_on_no_rows` 当作回滚（后者提交前缀）。对 S\input 先按块 `INSERT ... SELECT` 记录原状态审计，再撤销；对 input\S 记录实际恢复/新增，所有步骤在一个 VecSteps/事务。mutation+sequence 按排序差异稳定生成，无差异零审计。注入 guard 失败、审计失败、相同数量不同主体替换，确认整个事务回滚。
- [ ] **0.4 在 SQLite/真实 MySQL 证明容量和退出。** 至少执行 1,001 和 10,000 存量→空、10,000 存量→1,000 不相交主体，计入最多 11,000 条差异及约 22,000 次边/审计行写，而非只报单 SQL 100 参数。记录预读/guard/写入往返、总步骤、峰值内存、锁等待、事务时长；MySQL 做索引 EXPLAIN。10,000 是最低容量证据，不是新增 manager 产品上限。
- [ ] **0.5 冻结容量记录再开始 Task 1。** 证据文件必须给出目标部署的存量上界、单请求总行/步骤/内存预算、锁等待和事务/statement 超时的具体数值、实际生效位置及失败结果。初始验证目标为锁等待 <=5s、事务执行 <=10s；这是待测目标，不冒充现有插件保证。现有 SQLite busy_timeout=5000 只限制锁等待，MySQL SELECT timeout 也不涵盖全部写。证明超时后已回滚才允许本地重试；外层 Tokio timeout/客户端断开不能作为回滚证据。若现有插件无法可靠约束总工作/取消，先提交最小插件方案、契约和预算修订，经评审后才进入写路径实现。不能默认跳过此门禁或悄悄引入数量截断/部分成功。

此容量记录是实施前的待验证交付物，当前文档修订没有运行它，也没有声称 SQL/超时已获产品验证。需要引入公开的容量拒绝语义时先修订 spec，而不是实现者临场设置 manager 上限。

### Task 1：契约、schema 和严格权限读

**Files**

- 新建：第 2 节 service-api 契约文件、edge-permission 的 `authority.rs`、edge-permission-store 的 `authority/{mod,reads}.rs`、bot-store 的 `memory_authority.rs`。
- 修改：`crates/contracts/bcs-domain/src/edge_permission.rs`；以上 crate 的 lib/mod/Cargo；`crates/services/bcs-bot-store/src/{lib,memory}.rs`；`crates/bootstrap/bcs/src/migrations.rs`。
- 新增 migration：`migrations/mysql/033_bot_authority.sql`、`migrations/sqlite/034_bot_authority.sql`，执行前确认编号仍空闲，否则顺延，禁止改已存在文件。
- 测试：`crates/services/bcs-edge-permission-store/tests/authority_reads.rs`、`crates/bootstrap/bcs/tests/bot_authority_migration.rs`；共享 harness 位于 `crates/test-support/bcs-test-support/src/bot_authority_contract.rs`。

**Interfaces:** 产出第 3 节值类型、Repo/Core/Hook 声明、`DbBotAuthorityStore::new(db, flavor, env)`、`BotAuthorityCoreServiceImpl::new(repo)`；MemoryBotRepo 实现同一个 Repo port。写方法尚未实现时必须显式报错，不能默认成功。

- [ ] **1.1 记录基线与拆分清单。** 确认 Task 0 容量证据与专项例外已就绪；核对 HEAD、CI、实际修改源文件行数和依赖方向；按 §1.2 有效的 **2,500 行上限**仅拆超限文件。每次纯搬移运行该 crate 已有测试再进入功能修改。
- [ ] **1.2 写 `authority_reads` 失败案例。** `owner_manager_union`、`case_and_env_are_exact`、`deleted_human_or_bot_is_not_authorized`、`version_zero_denies`、`missing_or_duplicate_owner_is_error`、`storage_error_is_not_none`；`roles_for` 输入 0/1/128/129 项保持长度、顺序和错误传播。
- [ ] **1.3 运行 RED。** `cargo test -p bcs-edge-permission-store --test authority_reads`；预期缺少新类型/实现或上述断言失败，修正 fixture 错误后才记作有效 RED。
- [ ] **1.4 定义四表 schema 与类型。** 两张现有表和两张新表严格按 spec §4；转交主键及 pending 唯一约束、角色 shape 检查、完整索引、大小写精确比较、审计 actor/result 字段一次确定。分页 DTO 默认 50、最大 100；新增公开错误码，使用已有 Service/Application 错误封装。
- [ ] **1.5 实现严格读取。** 单项返回完整 owner 状态后的角色；批量读取 owner 与目标角色采用有界集合查询，最多 128 个 query/chunk，不先逐项调 role。SQL Store 绑定 env；MemoryBotRepo 使用单一共享状态。owner/manager 为 metadata 边，尚不能成为 runtime permission。
- [ ] **1.6 写并运行迁移兼容测试。** 升级前插入多个 profile/ref、规则和 revoked 边；升级后继续调用真实 runtime `insert_grant`/幂等查回/恢复/撤销。同步修正 SQLite `ON CONFLICT(from_id,to_id,env,grant_kind,grant_ref_id)` 和 MySQL 幂等查回的 kind 条件，不能只验证旧行仍在。用同主体 owner/manager/profile/rules 共存证明互不命中；迁移重跑无变化。运行 `cargo test -p bcs --test bot_authority_migration`、`cargo test -p bcs-edge-permission-store --test authority_reads`；MySQL 显式命令见 §6。
- [ ] **1.7 评审并形成可选提交。** `feat(bcs): define bot authority contracts and persistence`；证据包含迁移差异及严格错误路径，不启用 HTTP。

### Task 2：单项 manager、全量 sync 与权限审计

**Files**

- 新建：`crates/services/bcs-edge-permission-store/src/authority/{managers,audit}.rs`。
- 修改：`crates/services/bcs-edge-permission/src/authority.rs`、`crates/services/bcs-bot-store/src/memory_authority.rs`。
- 测试：`crates/services/bcs-edge-permission-store/tests/authority_managers.rs`、`crates/services/bcs-bot-store/tests/memory_authority.rs`；复用 Task 1 harness。

**Interfaces:** 实现 `edit_manager(ManagerEdit)`、`sync_managers(ManagerSync)`。Actor 必须由 Application 构造；Store 在 Bot 锁内重验 Human 当前 owner/manager，service 必须走专用 sync 命令。

- [ ] **2.1 写失败案例。** manager 可授权/撤权和自撤权；owner 目标 409；未知 Human 拒绝；重复 PUT/DELETE 无差异审计。sync `{a,b}→{b,c}` 必须 added=1/removed=1；把手工及转交产生的 manager 一并覆盖，owner 保留；重复、空数组、null、缺字段分别断言，`1,000` 成功而 `1,001` 拒绝。
- [ ] **2.2 运行 RED。** `cargo test -p bcs-edge-permission-store --test authority_managers`；失败须落在未实现语义，而非连接配置问题。
- [ ] **2.3 实现同事务集合变更。** 使用 Task 0 已在两个数据库证明的静态 guard/差异步骤及容量退出机制，锁序遵循 spec §7；Bot 锁内精确复验集合与 Human 存续。最多 100 主体/块，所有边和审计一个事务，不实现持锁 Rust callback。仅确定回滚的冲突最多重试 3 次；超时结果不明不得盲重试。
- [ ] **2.4 实现审计与 Memory 原子提交。** action 区分 grant/revoke/request/refresh/accept/reject/cancel/expire/invalidate/initialize/retire；差异记录使用同 mutation_id 的稳定 sequence。No-op 不留审计。Memory 先验证并在临时状态应用，成功才一次替换共享状态，故障不能泄露半写。
- [ ] **2.5 注入并发与失败。** 在预读后更换同数量 manager 必须回滚重算；第2 SQL块/审计失败全部不变；两次 sync 与手工写按提交顺序；存量 >1,000→空不漏首页外成员。与 Human 删除竞争：grant/initialize/accept 不能产生指向已删 Human 的 approved owner，不出现 Human→Bot 反向加锁。Provider actor 用于退役审计但不能进入 sync/accept 命令。
- [ ] **2.6 运行 GREEN。** `cargo test -p bcs-edge-permission-store --test authority_managers`、`cargo test -p bcs-bot-store --test memory_authority`；同一 harness 的合法结果/错误须一致。保留 SQLite 与 MySQL 独立构造器，后者须真实 DB 验证。
- [ ] **2.7 评审并形成可选提交。** `feat(bcs): manage one authoritative bot manager set`；不添加来源、move、历史 sync 回执或普通业务审计。

### Task 3：目标 Human 定位的可续期转交

**Files**

- 新建：`crates/services/bcs-edge-permission-store/src/authority/transfers.rs`。
- 修改：Core `authority.rs`、Store `audit.rs`、Bot Store `memory_authority.rs`。
- 测试：`crates/services/bcs-edge-permission-store/tests/authority_transfers.rs`、`crates/services/bcs-bot-store/tests/memory_authority.rs`。

**Interfaces:** 实现 `change_transfer(TransferCommand)`、`transfer(&TransferKey)`、`transfers(TransferListQuery)`。调用方只给 Bot/目标 ID/action；owner/version/time 快照由 Store 读取，不能由 body 输入。

- [ ] **3.1 写状态机失败案例。** 同 Bot 同目标两次 PUT 只有一行、续期七天；不同目标同时发起只有一个 pending；accepted 后符合条件的新 owner 再次发起复用行；表驱动覆盖 spec §6.4 全部状态/action 组合。
- [ ] **3.2 运行 RED。** `cargo test -p bcs-edge-permission-store --test authority_transfers`，确认失败的状态转换和期望响应。
- [ ] **3.3 实现 request/refresh 与只读列表。** 锁顺序 Bot→转交→Human（本路径不持 binding），与 Provider binding→Bot 路径一致；关闭到期或失效 pending 后再判断目标；刷新清空上一轮决议。GET 有效状态/筛选/total/分页同一时间快照，不写库；只给当前双方最小摘要。
- [ ] **3.4 实现 accept/reject/cancel。** accept 一次提交 owner 边切换、version+1、原 owner manager、新 owner manager 撤销、accepted 与审计；其他 manager 保留。冲突失效返回已提交的 `TransferOutcome::Conflict`，与事务失败区分。终态重复相同决议先验证本行身份，再无写返回；不依赖当前 owner。
- [ ] **3.5 加入边界及竞争测试。** 七天前/恰到期/之后、ABA旧快照、accept/reject/cancel竞争、审计故障、Provider/普通退役竞争、已删目标、重启重试。覆盖旧页面、两个 App 和在途请求：同地址新一轮后的迟到 accept 作用于当前 pending，不能把内部 version 测成跨轮防重放；无新轮同终态 no-op、续期/失效只审计真实变更。
- [ ] **3.6 运行 GREEN 并核对审计。** 运行本 task 测试及 manager suite；直接查询 SQL 状态验证 owner 唯一、版本只在 accept 增长、终态/各轮审计准确。SQL/Memory 复用同一组状态案例。
- [ ] **3.7 评审并形成可选提交。** `feat(bcs): transfer bot ownership by target human`。

### Task 4：生命周期、mine 与治理迁移

**Files**

- 新建：`crates/services/bcs-edge-permission-store/src/authority/lifecycle.rs`、`crates/services/bcs-bot-store/src/{ownership,controllable,memory_permission_profile}.rs`、`crates/tools/bcs-admin/src/bot_authority.rs`。
- 修改：`crates/services/bcs-bot-store/src/{registration_create,bot_provider_storage,memory_registration_create,memory_bot_provider_storage,provider}.rs`；Bot `core/{bot_core,provider_core,provider_registration}.rs`、application 的 `bot.rs/provider.rs/onboarding.rs/human_actor.rs`；`crates/application/v1/bcs-app-register/src/lib.rs`、`bcs-app-bot/src/lib.rs`。
- 修改契约：service-api `core/{registry,provider,provider_registration}.rs`、`port/repo/bot_provider.rs` 及 BotRepo/control-plane 契约；runtime-active 批量查询接线和 bcs-admin `main.rs`。
- 修改：`crates/adapters/http/bcs-http/src/routes/register.rs`、`routes/register/scoped.rs`（相对该 crate 的 src）；核对 `router.rs` 的真实挂载。OpenAPI 对照入口为 `crates/adapters/http/bcs-api-http/src/v1/openapi/routes/register.rs`，仅必要适配，不改变其独立传输契约。
- 修改：Bot Store 的 Memory state/module 导出、bootstrap `server.rs` 的 Memory profile 接线；既有 `PermissionProfileRepoPort` 签名保持。
- 测试：Bot Store `tests/authority_lifecycle.rs`、Bot `tests/authority_provider.rs`、app-register `tests/authority_registration.rs`、app-bot `tests/authority_mine.rs`、bcs-admin `tests/authority_backfill.rs`。
- 测试：`crates/adapters/http/bcs-http/tests/authority_registration_routes.rs`、`crates/adapters/http/bcs-api-http/tests/authority_registration_routes.rs`；在 service-api `core/registry.rs` 增加回执/查询类型隔离单测。

**Interfaces:** 实现第 3 节生命周期和 `list_controllable`。在 BotRepoPort 新增 `create_registration_if_absent_with_ownership(bot_id: String, capabilities: BotCapabilities, created_by: &str, token: &str, initialization: OwnershipInitialization) -> ServiceResult<bool>`；在 BotProviderRepoPort 新增 `create_provider_bot_with_ownership(record: BotProviderRecord, capabilities: BotCapabilities, owner: &str, token: &str, initialization: OwnershipInitialization) -> ServiceResult<()>`，其原子语义含必要 gateway projection。Human 受信任的注册改用组合方法，不能先 create 再 initialize；无 Human 的 runtime 保留原入口。治理入口经 Bot application/Core facade，不在 CLI 直接写 SQL。

- [ ] **4.1 写完整入口失败案例。** 真实 RegisterService 的 v1 Human token 和 v2 Provider token 两条 Core 链接 SQL Store，分别在 Human/profile/owner/binding/审计注入失败：无半注册、无可用 token、无提前 runtime 激活，不 mock 掉 Core。gateway/plugin/metadata/binding fallback/无 binding legacy 删除从真实 Provider Core/应用进入，绑定写和审计失败后 Bot、projection、边、pending 全部原样；核对 Provider-only actor 无 token。
- [ ] **4.1a 固定 HTTP surface/token 四格测试。** 两个 `authority_registration_routes` suite 分别从真实 legacy/OpenAPI router 发请求，覆盖 §3.1 两种 token：`legacy_v1_registration_rolls_back_on_profile_owner_audit_failure` 须断言非成功响应、无成功凭证及无半注册；其他三格也执行对应生命周期故障。`legacy_v1_ignores_scoped_fields` 含未知 mode/ref/webhook，OpenAPI 同参数按既有契约拒绝；保留名字 alias/trim/边界、无效或过期 token、legacy 200 扁平与 OpenAPI 201 envelope/no-store，不以 facade 单测代替 HTTP 入口。
- [ ] **4.1b 固定 Memory/profile 与类型测试。** `memory_registration_profile_is_runtime_visible` 从实际注入 runtime 的 profile port 读取注册所得默认记录；`memory_registration_profile_or_audit_failure_is_atomic` 在临时 profile 创建及其后的审计故障处断言新 Human/Bot/owner/version/profile/Provider projection/审计均无半写，预存状态不受损；重试只有一个默认 profile、ensure 返回原 ID且不改 revision/digest/rules，upsert_revision 保持 ID。`registration_receipt_is_separate_from_registered_bot` 同时导入两种类型，验证普通 get/list/discovery 的既有 RegisteredBot 序列化不含 bot_token，注册 wire 字段仍为 bot_uuid/bot_name/bot_token。
- [ ] **4.2 运行 RED。** `cargo test -p bcs-bot-store --test authority_lifecycle`、`cargo test -p bcs-app-register --test authority_registration` 和 `cargo test -p bcs-http -p bcs-api-http --test authority_registration_routes`；类型隔离用 `cargo test -p bcs-service-api registration_receipt_is_separate_from_registered_bot`。
- [ ] **4.3 接通原子生命周期命令。** 按 §3.1 adapter→Application→Core→Repo 调用链实现，legacy v1/v2 全部委托 RegisterService，不保留 connect→onboard/warn 后成功；legacy v1 先适配掉历史忽略字段，不直接复用 scoped parser。新增独立 CommittedBotRegistration，保留 RegisteredBot。Bot Store 组合必要 SQL；普通退役用 Registry，Provider 三分支用专用 retire，消除先提交删除再 registry.soft_delete 的链路。统一 binding→Bot→转交→Human 锁序，核对 webhook/attach writer；自动决议固定 system 标记，发起者仍真实审计。
- [ ] **4.3a 实现共享 Memory profile 并接线。** `memory_permission_profile.rs` 为同一 MemoryBotRepo 实现完整既有 profile port，状态保存 PermissionProfile、`(env,bot_id)` active-default 索引和从 1 开始的 u64 ID 分配器。沿用 SQL default 的 wildcard/revision/digest 规则；注册在临时 MemoryBotState 内分配并写 profile，与 Provider projection/authority 同次提交，失败不发布记录或分配器更新。已有默认记录复用，ensure 不覆盖；runtime 的 get/upsert 使用同一状态。Memory bootstrap 用同一 bot_repo Arc 注入 profile port，替换该初始化/runtime 链路的 Noop，禁止注册后再 ensure 或另建副本；此接线在 Task 4 测试，不等 Task 5 才补。
- [ ] **4.4 完成提交后清理与生命周期回归。** 先失效缓存，再外部清理；失败返回 retirement_cleanup_pending，允许含 legacy 的 tombstone 查回并重试，不重复退役审计。屏障测试退役与 accept/webhook，Human 删除与初始化竞争；version0 runtime、转交后重新注册/onboard/ensure/刷新不重设 owner，已删 ID 全入口不复活。
- [ ] **4.5 实现 mine 动态算法。** 按 spec §8.0 先持久化候选，再一次 WS 快照+最多128ID块的 Provider 批量解析，最后 reachable/unreachable→total→分页→批量补页；查询失败上抛。A离线/B可达、limit1 必须返回B/total1；混合WS/HTTP Provider、self、>500、多页/首批全不可达和补页变化重试均测试，断言实际查询批次数，不只是权限 SQL。
- [ ] **4.6 实现 bcs-admin dry-run/apply。** 新子命令 `bot-authority inspect` 与 `bot-authority initialize --bot-id ...`，沿用配置/env；维护模式，单批 <=100 去重 Bot ID。分类可回填/缺Human/关系冲突/已初始化；inspect 零写，apply 逐 Bot 原子且逐项报告，无迁移账本。
- [ ] **4.7 运行 GREEN。** `cargo test -p bcs-bot --test authority_provider`、`cargo test -p bcs-app-bot --test authority_mine`、`cargo test -p bcs-admin --test authority_backfill` 及 4.2 全部命令；Memory 生命周期 harness 与 SQL 使用同一组 profile 观察/回滚断言，HTTP 四格均通过。验证清理重试、残边不污染 mine、回填不重置与明确失败报告。
- [ ] **4.8 评审并形成可选提交。** `feat(bcs): initialize and retire bot authority atomically`；文档明确 v1 不再吞初始化错误和已持久退役但清理待重试的兼容变化。

### Task 5：统一 API、可信 App 策略与 authority 装配

**Files**

- 新建：`crates/application/v1/bcs-app-bot/src/{authority,authority_policy}.rs`、`crates/adapters/http/bcs-api-http/src/v1/bot_authority/{mod,routes,dto}.rs`、`crates/bootstrap/bcs/src/bot_authority_wiring.rs`、`crates/bootstrap/bcs/src/config/bot_authority.rs`。
- 修改：`crates/adapters/http/bcs-api-http/src/v1/{mod,internal/mod}.rs`、common 的 `principal.rs`、`invite_code_gate.rs`（仅必要接线）；`crates/service-api/bcs-services-container/src/{services,test_support}.rs`；bootstrap config/server；`api-contracts/v1/openapi.yaml`。
- 测试：`crates/application/v1/bcs-app-bot/tests/authority_policy.rs`、`crates/adapters/http/bcs-api-http/tests/bot_authority_routes.rs`。

**Interfaces:** `BotAuthorityServiceImpl` 实现第 3 节 Application；`AuthorityServicePolicy::authorize(caller, action, bot_id, target_owner_actor_id) -> Result<AuthorityActor, ApplicationError>`，仅转交操作的 target 可省略于其他操作。构造器注入 Hook/Core、邀请码服务、已解析 App scope 配置；不读取 raw env。

- [ ] **5.1 写认证/身份失败案例。** 真实 Gateway 签名：过期/篡改/audience、tenant缺失与显式absent、大小写/env/Bot/action/target 越界、空集合、普通App、伪造app_id、Human-only sync、sync-only accept、Provider-only 凭证逐项断言。未知 action/通配/重复配置键启动失败。User+App 的 Human 拒绝不可回退，sync 显式App；Provider-only 可按既有流程退役，但不能通过这些 App-only 权限。
- [ ] **5.2 运行 RED。** `cargo test -p bcs-app-bot --test authority_policy`、`cargo test -p bcs-api-http --test bot_authority_routes`。
- [ ] **5.3 实现统一路由。** 完整实现 spec §5/6 的 11 种 method/path 组合；只挂 `/api/v1/collaboration/`。集合 `GET/PUT /bots/{bot_id}/managers` 与单项 `PUT/DELETE .../{user_id}` 共存，不建立第二组 Human/App URL。显式测试 user_id=`sync` 的 PUT 只添加该用户，不替换集合。
- [ ] **5.4 实现动作级身份策略。** 所有请求先复用 PrincipalVerifier；Application 按 spec 的 Human-only/App-only/双准入表选择主体。Human 继续邀请码检查，App 验证已配置范围；通过 router 组合把这组接口从“一律要求 Human”的门禁中分离，但不进入 anonymous router，不以 URL 白名单绕过认证。当前 Human 角色在 Store 写事务仍重验。
- [ ] **5.5 接线与契约。** 本步骤仅向 ServicesContainer 注入已实现的 authority 门面/Core/Hook，核对 Task 4 的 Memory lifecycle/authority/profile/Provider projection 同状态接线；API 明确状态码，App scope 按 spec §3.1 精确配置、默认空集，配置 schema/authority bootstrap conformance 一并更新。Group/Session 严格资源读在 Task 6 实现后才正式注入其消费者，Task 5 不声明完成它，不用 AllowAll/缓存 fallback 填补依赖或取得 GREEN。
- [ ] **5.6 运行 GREEN。** 测试所有新增 route 均经过签名认证；受权 App 可读/accept/reject 同一个 handler；Human 路径/邀请码无回归；普通既有路由未获得 App 豁免。运行两组测试并检查 OpenAPI operationId 唯一，sync operationId 为 `syncBotManagers`。
- [ ] **5.7 评审并形成可选提交。** `feat(bcs): expose bot authority through unified collaboration APIs`；只完成装配，不在消费者改造前开放发布。

### Task 6：既有业务统一鉴权及 runtime 隔离

**Files**

- 修改：`crates/service-api/bcs-service-api/src/application/v1/authorization.rs`；`crates/application/v1/bcs-app-bot/src/lib.rs`，`bcs-app-group/src/{authorization,service,create}.rs`，`bcs-app-session/src/{lib,file,connection}.rs`，`bcs-app-invitation/src/lib.rs`。
- 修改：`crates/services/bcs-bot/src/application/{bot,provider,onboarding}.rs`；`bcs-group/src/application/management/{mod,guards,create,workbench}.rs`；`bcs-session/src/launch.rs` 及 `application/sessionmanagementserviceimpl{,_2}.rs`；`bcs-session-file/src/{authz,service}.rs`。
- 修改：`crates/services/bcs-message-flow/src/{a2a_chat/mod,group_flow/routing,delivery_control,group_history}.rs`；`bcs-eventing/src/{resource_authorizer,subscription}.rs`；`bcs-proposal/src/application/proposal.rs`。
- 修改：`crates/adapters/http/bcs-http/src/routes/` 中 Bot/Group/Session/文件/邀请等实际挂载的控制权调用；`crates/adapters/http/bcs-api-http/src/v1/internal/routes/session_file.rs`；`crates/services/bcs-edge-permission-store/src/lib.rs` 与 runtime Core 的相关查询。
- 新建：`crates/service-api/bcs-service-api/src/core/session_authorization.rs`、`crates/services/bcs-session/src/authorization.rs`、Group/Session Store 各自 `src/authorization_snapshot.rs`。修改 service-api `core/group.rs`、`port/repo/{group,session}.rs`，Group `src/core/group_core.rs`，Group Store `store_repo.rs`，Session Store 的 Repo impl 与 Memory 实现。
- 修改正式资源读装配：`crates/bootstrap/bcs/src/{server,bot_authority_wiring}.rs`、`crates/service-api/bcs-services-container/src/{services,test_support}.rs`；此接线依赖本 Task 的真实 Core/Store，不能前移为 Task 5 的未实现依赖。
- 测试：上述业务 crate 的 `tests/authority_cutover.rs`（按消费者创建）；新增 `crates/services/bcs-edge-permission-store/tests/authority_runtime_isolation.rs`。修改既有 fixture 时保留原资源可见性断言，不把所有用户设成 owner。
- 测试：`crates/services/bcs-group-store/tests/authorization_snapshot.rs`、`crates/services/bcs-session-store/tests/authorization_snapshot.rs`，共享数据库、独立 Store 实例，不能只用同一缓存实例证明刷新。

**Interfaces:** 新增 `resolve_authorized_principal(caller: &AuthenticatedCaller, policy: IdentityPolicy, authority: &dyn BotAuthorityPort) -> Result<Principal, ApplicationError>`，扩展既有 IdentityPolicy 为 HumanOrAuthorizedBot，不引入同义 PrincipalPolicy。资源权限使用 §3 的 Group/Session 快照，保留独立 actor/role/membership 语义。

- [ ] **6.1 建立挂载入口表。** 从 router/bootstrap 反向记录每项当前判定、授权事实、替换位置及真实测试入口。搜索 created_by/is_creator/owner_id/HumanOrOwnedBot/list_by_creator，区分 Bot 历史、Session Human creator、外部 Provider 管理权，不能全文替换。
- [ ] **6.2 写身份矩阵 RED。** A owner、M manager、X无权，转交B后A仍为manager，撤掉A后仅依赖该边的入口/绑定拒绝。另让A是显式Human Session creator/participant：其self权限继续，B不继承A私有视角；纯Bot凭证不随边撤销。User-only、混合旧owner声明、Bot-only都测；Worker不升为Group管理员。每个消费者从真实入口调用，不能mock掉授权。
- [ ] **6.3 实现统一 resolver 与 Bot 接入。** 混合身份实时检查 Human 控制权后选择 Bot；纯 Bot 保持自身认证；Human-only 使用其实际 self 或合法选择视角。删除、配置、状态、legacy mine 和重连入口使用 Hook；错误传播，不能读取不到 Bot 时跳过鉴权，也不能把 mine 前 500 个条目当白名单。
- [ ] **6.4 实现权威资源快照并接入资源。** 按 §3.1 完整字段实现窄 Core/Repo 严格读，直接主数据库、不用 try_get缓存；应用先作资格判断，再走业务流程。新增真实实现后，在本 Task 正式装配到 Group/Session Application、event authorizer，bootstrap 测试读故障不退缓存；Memory 投影同字段。测试A先缓存Group、B撤资源角色、管理边仍有效，A后续HTTP/事件管理不得放行。private/public sponsorship、首个Session/后续launch、Human creator/participant按原规则；普通写Repo不添加mutation。
- [ ] **6.4a 固定 strategy/scope 投影测试。** `manager_role_is_strategy_gated` 覆盖 Chat/ManagerWorker/StateMachine，非 driver/originator 的 Manager 仅在 ManagerWorker 获管理角色。`scope_only_update_changes_authorization_snapshot` 用两独立 Store，保持 actor/role/mode/管理边不变，仅将 Full 改 Participant，第二次 Group/Session 投影须变化；`session_scope_is_seeded_then_independent` 覆盖创建时显式覆盖/继承/既有默认及创建后 Group 修改不覆盖 Session，也不回填缺失 Session 成员。两个快照及 event authorizer 都测，不能只测试移除成员。
- [ ] **6.5 接入事件、消息和其他入口。** standalone/inline event authorizer 都使用实时管理边+资源快照，覆盖eventing开启且subscriptions=[]。已有webhook按持久资产继续投递，A不能再管理它、当前资源管理员可停用；不是每次HTTP投递重授权。好友/邀请/proposal/消息/legacy对齐；公开/friendship不升为管理权。
- [ ] **6.6 隔离 runtime grant。** runtime authorization、friend 查询和 connect/profile 解析显式只接收 `permission_profile / rules`；owner/manager 不能触发默认 wildcard。测试仅有管理边时无法建立 friendship/路由许可，添加合法 profile 后恢复既有行为，撤销管理边不破坏 profile。
- [ ] **6.7 运行 GREEN。** `cargo test -p bcs-bot -p bcs-app-group -p bcs-app-session -p bcs-app-invitation -p bcs-group -p bcs-session -p bcs-session-file -p bcs-message-flow -p bcs-eventing -p bcs-proposal -p bcs-http --test authority_cutover`；另跑 `cargo test -p bcs-edge-permission-store --test authority_runtime_isolation`。全部通过后，入口表逐项关联测试，不以 grep 零匹配代替调用链验证。
- [ ] **6.7a 运行资源快照 conformance。** `cargo test -p bcs-group-store -p bcs-session-store --test authorization_snapshot`，核对两实例缓存、scope-only 更新、资格撤销、环境隔离、读错误及独立 Human 来源；strategy/event 管理断言在 6.7 的业务 suite，正式资源读装配在 bootstrap conformance。MySQL 使用 §6 显式 suite。
- [ ] **6.8 评审并形成可选提交。** 按 Bot、Group/Session、其余业务分为三个可评审提交，共同完成 `refactor(bcs): resolve current bot authority across business entrypoints`；它们是实施拆分，不是独立发布单元。

### Task 7：WS 最终发送前鉴权与失效解绑

**Files**

- 新建：`crates/service-api/bcs-service-api/src/application/v1/delivery_authorization.rs`、`crates/application/v1/bcs-app-session/src/delivery_authorization.rs`。
- 新建：`crates/adapters/ws/bcs-ws/src/shared/protected_delivery.rs`。
- 修改：WS `src/web/{auth,connection_registry,dispatcher,frontend_delivery,group_session,handler}.rs`、`src/shared/run_channels.rs`、`src/bot/dispatcher.rs` 的真实 event producer；service-api `application/v1/group_session_connection.rs` 和 `port/chat_run.rs` 的内部限定契约；app-session `connection.rs`；bootstrap `http_adapter.rs` 的 RunChannelPort 和 token/wiring。核对 `bcs-api-http/src/v1/group_session_connection.rs` 的 caller/session_id 签发契约保持；内部 HTTP `bcs-message-flow/src/a2a_chat/mod.rs` 注册/测试需验证原 String port 行为不变。
- 测试：`crates/application/v1/bcs-app-session/tests/delivery_authorization.rs`、`crates/adapters/ws/bcs-ws/tests/authority_delivery.rs`。

**Interfaces:** `DeliveryDecision = Deliver | SkipMessage | InvalidateBinding`；binding 含真实User、env/tenant、视角 actor、资源、连接实例ID，不缓存授权 role/scope；visibility 来自可信 producer domain/audience。`ConnectionView = HumanSelf | Bot { bot_id }` 由现有 wire `view_actor_id` 规范化（缺省或本人 Human 为 self，其他 Human 拒绝），加入 `AuthorizeGroupSessionConnection` 的 `view` 字段；Application 用现有 token 验范围并返回受权视角。`IssueGroupSessionConnectionToken { caller, session_id }` 不变，仅验证当前 Session 读取资格。`RunClientSink = Internal { tx: Sender<String> } | Workbench { tx: Sender<OutboundFrame>, binding }`，`OutboundFrame = Protected { binding, visibility, payload } | ConnectionControl { payload }`。Run manager 显式 `register_internal` / `register_workbench`；现有 ChatRunEventPort::register 仅由内部适配映射前者，公开writer不能消费Internal sender。

```text
已认证 connect + 显式 view → Application (token scope + Hook + resource snapshot)
  → WS registry (authorized scope + connection instance) → register_workbench(binding)
Bot event typed producer → Run/alias/session fallback [保留 Workbench sink/binding]
  → OutboundFrame::Protected → 有界 queue → 最终 Application 授权 → socket writer
内部 HTTP Run → ChatRunEventPort<String> → register_internal → 内部响应消费者
```

最后一行不是 Workbench 的降级通道；缺 binding 或资源不匹配直接拒绝。alias 保留连接实例，不以相同 run/session 字符串把其他 Human 的 sink 当作 fallback。

- [ ] **7.1 写 RED。** 从真实 connect→Run注册→Bot event producer 排队，另一实例撤管理边后drain拒绝；另令A缓存资源、B只撤Group/Session角色而管理边仍有效，writer必须失效。`scope_tightening_skips_full_only_without_unbinding` 分 Group 帧/Session-Run 帧：B仅收紧对应 participant scope，A排队的 ManagerWorker/StateMachine FullOnly 帧须 Skip，后续 Public/Directed-to-self 仍 Deliver，角色与边保持有效；含 Chat Group 中 producer domain=StateMachine 的帧。Invalidate后移除订阅且查询0，不能只手工向writer注入帧。
- [ ] **7.2 运行失败测试。** `cargo test -p bcs-app-session --test delivery_authorization`、`cargo test -p bcs-ws --test authority_delivery`；可控屏障协调 enqueue/revoke/write，禁止 sleep 猜时序。
- [ ] **7.3 实现绑定透传和 token 策略。** 签发只按 caller/session_id 验证当前 Session 读取资格，不猜测选定 view；connect/reconnect 从 view_actor_id 显式选view，再查token既有uid/tenant/gid/sid、当前管理边及资源快照，缺省self不能自动选Bot。实现双sink和生产链图，队列携带保护元数据，registry校验连接实例；派生Group→Session/Run/replay需验证关联。保留内部String port真实消费者，不允许缺binding落公共或Internal。
- [ ] **7.4 实现最终 writer 检查。** 每帧调用Application级DeliveryAuthorizationService，查严格资源快照及authority边，以本次 Group/Session participant 的 scope 构造 HumanMessageView，再按可信 producer domain/audience 判定；不复用连接时或 Run channel 缓存的旧 Full 上下文，不以 Group seed 覆盖 Session。底层错误在应用层去敏为失效。按三态发送/跳过/解绑，公共仅连接控制消息；registry锁不跨DB/network await。检查通过后至send之间撤权的该帧允许在途，下一帧重新检查；用屏障验证。
- [ ] **7.5 补齐回归。** `token_issuance_does_not_preselect_view` 用真实原签发 HTTP handler（无view）取得 token：合法管理者显式 Bot view 可连接，无权限 Bot view 拒绝，缺省仅按self资格，签发后撤角色仍拒绝连接。另测真实旧JWT、同Human多view、self独立权、Bot-only、慢队列、读错、Run alias/fallback/重放、Internal HTTP和连接实例替换。转交后原owner仍manager可用；移除后仅Bot-bound失效。沿用关闭通知和既有view_actor_id wire字段，内部绑定不变成新公共协议。
- [ ] **7.6 运行 GREEN 与成本记录。** 执行两组测试及真实Run producer相关既有回归；分别计authority SQL、Group/Session资格SQL、visibility SQL，队列上限与解绑后查询0。去掉入队检查不是零成本，也不允许正向TTL或普通Group缓存代替最终读。
- [ ] **7.7 评审并形成可选提交。** `fix(bcs): enforce current authority before protected websocket delivery`。

### Task 8：整栈验证、性能证据与发布说明

**Files**

- 新建：`crates/bootstrap/bcs/tests/e2e_bot_authority.rs`、`docs/runbooks/bot-authority.md`。
- 新建：edge-permission-store、bot-store、group-store、session-store 的 `tests/authority_mysql.rs`（各一项明确命名的聚合 conformance），CI 显式执行而非依赖 workspace 自动运行 ignored。
- 修改：既有 MySQL migration chain 测试、相关 crate 的 `CONTEXT.md`、API 契约说明和必要的 Singlebox 用户故事；只在新功能要求时更新检查注册，不修改门槛。

**Interfaces:** 使用前七个 task 的正式 ServicesContainer、HTTP router、共享数据库和 WS writer；测试不能手工替换为可放行 Hook。记录版本、数据库方言、命令、退出码、通过/失败/跳过数。

- [ ] **8.1 写整栈故事并先验证失败条件。** 真实注册→加manager→业务→App sync→转交/续期/accept→sync移除原owner→仅该Bot-bound HTTP/WS失效，原Human独立creator视角和持久webhook仍按契约工作→再次转交→Provider/普通退役。加入eventing explicit-Human launch、真实Run producer、两实例只撤资源资格/只收紧scope；故障覆盖 legacy/OpenAPI × v1/v2 token 四格，Memory 注册后由实际 runtime profile port 验证同一已提交记录。
- [ ] **8.2 核对 scope。** 新接口只有统一前缀和 11 个 method/path；只出现两张新表、两张改表，没有普通业务 operation_id/审计表、manager source、同步历史账本、新权限服务或新 token 协议。本计划新增或修改的 BCS 源文件（含测试）均 <=2,500 物理行，纯搬移与功能 diff 分开统计。
- [ ] **8.3 运行 Store/迁移门禁。** SQLite/真实MySQL执行同一conformance，包含迁移后runtime写兼容、锁序、超时回滚、Provider和资源快照。按 §6 `--ignored --exact` 显式执行四个 Store suite、一个 DbPlugin suite 和完整迁移链测试，共六项，每项必须 reported 1 passed/0 ignored，启动缺URL立即失败；不能以workspace全绿或SQLite模拟替代。
- [ ] **8.4 记录成本。** role 1/128/129、mine全部候选+WS集合+Provider批次+补页、sync Task0容量场景和超时、WS1/20连接及慢队列，记录SQL/扫描/行写/耗时/内存/锁等待。EXPLAIN证明索引；role/runtime不得N+1，sync不得部分提交，writer区分管理边与资源读，解绑后查询0。Task0预算必须有实际结果，超出先更新方案和范围。
- [ ] **8.5 运行完整验证。** 执行下面命令及仓库当前必需门禁；出现失败定位原因，不能删除测试或以“环境问题”无证据跳过。定向测试用于开发反馈，发布证据须含完整 workspace 和统一 Singlebox。
- [ ] **8.6 编写消费者与运行说明。** 精确App scope示例、Backend ID映射/可信Human预置/单逻辑writer跨副本串行/超时不确定结果/各状态码处理/初次sync成功标记和阻断切换；浏览器旧页面及多App的当前请求风险；Bot凭证不随边撤销、webhook持久资产和当前管理员停用；Provider清理待重试；维护/回退保留新权限读。接入PR记录具体负责人及容量配置，不声称停止重试能撤回在途请求。
- [ ] **8.7 交付评审。** 将下方 A01–A20 映射到测试名称/日志，更新实际 crates/文件/功能行数/机械搬移行数与预算差异。形成可选提交 `test(bcs): verify authority contracts and rollout`；获得发布授权前不开放新权限入口。

## 5. 验收映射

| Spec | 任务 | 必需证据 |
| --- | --- | --- |
| A01 | 1 | 严格 role/batch、大小写/env、owner 完整性及故障测试 |
| A02 | 4、6 | mine前页不可达仍正确、WS/Provider/self动态筛选先于total/page、>500与runtime查询计数 |
| A03 | 2、5 | 手工 manager、自撤权、owner 目标保护的 Store 与 HTTP 测试 |
| A04–A05 | 2、5 | 集合覆盖、owner 归一化、非法/空/重复/超限、事务回滚 |
| A06 | 5 | 同路由 Human/App 真实签名、scope 和身份优先级测试 |
| A07 | 3 | 唯一行/唯一 pending、续期及重新发起 |
| A08–A09 | 3、2 | 原子 accept、原 owner 可移除、竞争及当前轮重试语义 |
| A10–A11 | 3、5 | 只读到期投影、ABA、invalidated 提交、隐私/最小返回 |
| A12 | 2–4、8 | 共享三后端 conformance、故障和数据库并发测试 |
| A13–A14 | 6 | 授权事实矩阵、独立Human权利、持久webhook、sponsorship与主数据源资源快照 |
| A15 | 6–8 | strategy/scope 完整投影、Session seed 后独立演化、scope-only 收紧后 Skip/Deliver；真实producer/Run/alias、旧JWT签发与显式view边界、解绑零查询 |
| A16 | 1、6 | 新唯一键后runtime写/幂等/撤销兼容，管理边与runtime互不影响 |
| A17 | 4、8 | legacy/OpenAPI × v1/v2 token HTTP 兼容/故障矩阵、独立凭证回执、Memory profile 同状态可观察/回滚；Provider 三分支、清理重试、Human 删除竞争 |
| A18 | 4、8 | 治理迁移 dry-run/冲突/重跑、完整迁移链及兼容回退 |
| A19 | 2–5、8 | 权限审计逐轮还原；schema/依赖扫描证明范围未扩张 |
| A20 | 0–8 | 实测容量/超时、MySQL显式执行数、runtime与资源读成本、两类API身份conformance |

## 6. 验证命令与交付边界

在 `apps/bcs/`：

```bash
cargo test --workspace --all-targets --no-fail-fast
bash scripts/ci/arch-check.sh
```

真实 MySQL 单独执行。下列四个 `authority_mysql` 是 Task 8 要新建的测试入口，不是当前已存在或已通过的测试；各入口 `#[ignore]`，内部运行对应完整 conformance 场景并报告场景数，不能用空壳聚合测试充数。每项使用隔离的可丢弃测试数据库，完整迁移链要求空库；URL 由运行环境注入，不写日志。未提供数据库时交付记录为未验证并阻止该门禁通过。

```bash
test -n "${BCS_TEST_MYSQL_URL:?BCS_TEST_MYSQL_URL is required}"
cargo test -p bcs-edge-permission-store --test authority_mysql authority_mysql_conformance -- --ignored --exact --nocapture
cargo test -p bcs-bot-store --test authority_mysql provider_lifecycle_mysql_conformance -- --ignored --exact --nocapture
cargo test -p bcs-group-store --test authority_mysql group_authorization_mysql_conformance -- --ignored --exact --nocapture
cargo test -p bcs-session-store --test authority_mysql session_authorization_mysql_conformance -- --ignored --exact --nocapture
cargo test -p bcs-db-mysql --test conformance_db_plugin conformance_mysql_db_plugin_for_text_and_prepared_protocols -- --ignored --exact --nocapture
cargo test -p bcs-admin --bin bcs-admin migrate::mysql_chain_tests::full_mysql_migration_chain_applies_and_preserves_history -- --ignored --exact --nocapture
```

上述命令逐项运行并保存退出码和测试摘要；CI 必须断言每项 **1 passed、0 failed、0 ignored** 及 harness 场景清单齐全，`0 tests`、缺少入口、缺数据库或任一失败都不通过。工作流分别注入各测试数据库，不能在同一脏库并发运行迁移链；完整 workspace 默认忽略这些测试，不替代这六项证据。

在仓库根目录，按现有完整门禁运行一次共享栈并校验产物：

```bash
bash singlebox/ci/singlebox_coverage.sh
python3 singlebox/ci/verify_singlebox_coverage_artifacts.py --reports-dir singlebox/.dependencies/coverage/singlebox/reports
```

每次提交前使用 `git diff --check`，记录新增及修改源文件的完整物理行数并核对本计划的 2,500 行上限；这是本计划交付检查，不声称已有对应 BCS CI 门禁。只有文档发生变化时无需构建产品。实现阶段若受真实数据库、依赖或运行环境限制，交付说明精确列出未跑项目及原因，不能宣称全部验收通过。

## 7. 审阅落实索引

此表记录设计/计划如何承接审阅项，不代表未来产品测试已执行。

| 审阅项 | 明确的设计选择 | 实施与验证位置 |
| --- | --- | --- |
| R01 | Group/Session 主数据源权限快照，不复用展示缓存 | spec §8.3；Task 6、7，两实例只撤资源资格 |
| R02 | Provider 三分支一个退役事务，统一 binding 锁序及 Memory 状态 | spec §7.1；§3.1、Task 4，失败回滚及清理重试 |
| R03 | 独立 Provider actor，仅表示真实操作者 | spec §3.1/4.4；Task 4、5，Provider-only 不能 sync/accept |
| R04 | v1/v2 Application→Core→组合 Repo 原子注册 | spec §10.1；Task 4，真实入口故障和错误传播 |
| R05 | 全候选动态 reachability 先于 total/分页，计入 Provider 成本 | spec §8.0；Task 4、8，前页不可达和大集合 |
| R06 | Workbench/Internal 两类 Run sink；旧 token 连接时显式选 view | spec §8.3；Task 7，真实 producer→共享 Run→writer |
| R07 | 仅撤销依赖该管理边的访问，保留独立 Human/纯 Bot 权利 | spec §3.3；Task 6、8，按授权事实断言 |
| R08 | 有责任人、期限及模块入口的 2,500 行专项例外 | §1.2、例外记录、Task 0/8；未满足生效条件先复审 |
| Q01–Q02 | 当前请求迟到决议风险；Bot/外部凭证独立于管理边 | spec §3.3/6.4；Task 3、6、8 的消费者边界 |
| Q03 | webhook 为持久集成资产，由当前资源管理者治理 | spec §8.4；Task 6、8 |
| Q04 | Backend 负责 Human 预置、单逻辑 writer 和初次同步确认 | spec §5.2；Task 5、8 的接入契约 |
| Q05 | 先证明静态事务、集合 guard、总成本与超时回滚再实施 | spec §7；Task 0 为阻塞门禁，Task 2/8 复验 |

第二轮定点复审：

| 审阅项 | 明确的设计选择 | 实施与验证位置 |
| --- | --- | --- |
| RR01 | 两个 HTTP surface 的两种 token 都委托原子 RegisterService，保留各自参数与响应契约 | spec §10.1；§3.1 四格矩阵；Task 4.1a/4.3 的真实 router 故障测试 |
| RR02 | 新回执 CommittedBotRegistration 与现有查询 RegisteredBot 分离 | spec §10.1；§3.1；Task 4.1b/4.3，普通查询不暴露 token |
| RR03 | 快照含 strategy、actor kind 和当前 scope，Session seed 后独立，writer 不缓存正向视角 | spec §8.3；§3.1；Task 6.4a/7.1/7.4 的 scope-only 与 strategy 测试 |
| RR04 | Memory 默认 profile/ID 与生命周期同次提交，同一 profile port 注入 runtime | spec §7/10.1；Task 4.1b/4.3a，可观察性、重试和故障回滚 |
| 复审 §4.1 | token 签发只验证 Session 资格，connect 复用 view_actor_id 选择并实时验证 | spec §8.3；Task 7.3/7.5，真实无view签发与连接矩阵 |
| 复审 §4.2 | Task 5 只装配已实现 authority；资源严格读及其正式装配在 Task 6 | Task 5.5/6.4/6.7a，不引入过渡放行实现 |

本计划的完成物是可评审的实施步骤与估算；它没有授权开始产品实现、提交或发布。评审计划后再选择由同一实现者顺序执行，或在冻结共享接口后按任务分派并逐项独立评审。当前任务不生成产品代码。
