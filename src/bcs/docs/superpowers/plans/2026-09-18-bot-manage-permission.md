# BCS Bot Manage Permission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不转移 ownership、不扩大 runtime grants 的前提下，让显式 manager 获得同一 Bot 的 BCS owner 代行能力，并在撤权、删除和多实例推送中保持一致的授权结果。

**Architecture:** `edge_grants` 保存 Manage 事实，composition root 注册 transport-neutral `BotAuthorityHook`；应用层保留资源参与和角色规则。Core 仅依赖 repo trait，SQL store 与 Memory registry 实现严格读、共同生命周期锁和原子审计；HTTP/WS 只调用应用合同。管理写 API 在所有消费路径和持续授权接入后才正式挂载。

**Tech Stack:** Rust workspace、async-trait、Tokio、Axum、现有 DbPlugin transaction API、SQLite / MySQL、现有 OpenAPI / Cargo / Singlebox gates；不增加第三方依赖或运行时配置项。

**Spec:** [2026-09-18-bot-manage-permission-design.md](../specs/2026-09-18-bot-manage-permission-design.md)（2026-09-18 技术评审修订版，AC01..AC28）。

## Global Constraints

- 本计划获准编写，不代表已获准执行业务代码修改；执行前记录 spec 第 2 节产品确认，尤其是 manager 可继续授权、撤销不级联、收藏共享。
- **“平权”指相同 Bot、相同操作、相同资源角色下 owner 与 manager 的授权结果一致，不指 manager 成为全局管理员。**
- `created_by`、Gateway Bot `owner_id`、资源原始创建者与历史审计不因 manage 改写。
- `view_bot_id` 省略：保持 Human 本人视角。WS 省略 `view_actor_id` 则保持既有 LegacyFull，两者不是同一默认合同。
- 合法管理边：Human → physical Bot，`grant_kind=manage`、`grant_ref_id=0`、`rules=null`、`originator_policy_type=same_as_from`、`originator_policy_data=null`、`status=approved`。
- 唯一键改为 `(from_id, to_id, env, grant_kind, grant_ref_id)`；grants 保留 revoked 行。
- 管理权初版不使用跨请求正向缓存；每次新请求读当前授权事实。
- grant/revoke、Actor 删除与审计遵守 spec §6.3 同一事务和共同锁顺序；持久化失败返回错误。
- 管理合同中 User ID（HTTP 目标字段 `id`，内部 user_id）为 1..250 个 Unicode scalar values，Actor/Bot ID 最多 256 个；MySQL 审计 `created_by/decided_by` 扩为 `VARCHAR(256)`。
- 新增/修改 source file 不超过 1,000 行；不运行全局 cargo fmt；不增加 allowlist 掩盖超限。
- contracts / application / core / port / store / delivery / composition root 按 `CLAUDE.md` 分层；adapter 不查边，application 不直连 repo，Core 不直连 DbPlugin。
- 没有新的 Plugin API、通用 RBAC、默认多身份聚合、组织继承或 Engine/Provider/Skill 权限。
- 管理 HTTP 合同固定为 `GET/POST/DELETE /openapi/v1/collaboration/bots/{bot_id}/managers`；POST 的目标 id 来自必填 JSON body，DELETE 来自必填 query，不能缺参批量撤销。
- 管理写 API 不在中间阶段对外开放；不支持新旧二进制混跑，按 spec §15 维护窗口迁移。
- 纯移动先以原测试 green 为基线，移动后再次 green 并单独 `refactor(bcs)` 提交；行为任务在该提交之后写失败测试、实现、回归，再提交。不得把纯移动混入 feat/fix 提交。
- 持续授权固定 deadline 为 250 ms、SQLite/MySQL 授权 P99 分别 ≤25/50 ms；按 spec §12.2 完整负载验收，未达标不得 fail-open 或静默降低预算。这是拟定工程门槛，不是已确认生产 SLO。
- 文中命令与代码是未来执行步骤，不是本次已运行的结果。

---

## 0. 执行边界、路径和交付顺序

稳定源码基线为 `03d4b7875bdf38f10540819c5f6a38bd5bd39028`。联合评审读取的文档版本为 `1f55634547`，它是旧文档提交 `04c3d29c68` 的 amend 替代，旧提交不是当前 HEAD 的祖先。不要 checkout 任一旧文档 hash 执行计划；从当前分支读取最新两份文档，再按稳定源码基线检查漂移。为保持当前 PR 链接稳定，沿用已有 BCS `docs/superpowers/specs` / `plans` 目录，不移动历史 spec。除特别注明“仓库根目录”的文件外，下文路径和命令均相对于 `src/bcs/`；先 `cd src/bcs`。表格中 `foo/{a,b}.rs` 表示两个明确文件 `foo/a.rs` 和 `foo/b.rs`，同目录拆分落点相对于原文件所在目录。所有 `Create` 文件是本计划明确的新文件，不是假定已有实现。

先读根 `AGENTS.md`、`CONTEXT-MAP.md`、`docs/arch/{arch.rules,ci.enforce,context-boundary-format,protocol-contract-tests}.md`、BCS `AGENTS.md` / `CLAUDE.md` 和受影响 crate 的 `CONTEXT.md`。当前系统 ADR 目录没有直接定义 Bot 委托管理的决定；本计划不覆盖已有 Skills ADR。

执行前确认当前已在隔离 worktree，不再嵌套创建；安装本 worktree hooks。此任务只覆盖 BCS；Frontend UI/消费方有独立交付门，不在此计划里跨模块实现。

```bash
git status --short
git rev-parse --git-dir --git-common-dir
git merge-base --is-ancestor 03d4b7875bdf38f10540819c5f6a38bd5bd39028 HEAD
git diff 03d4b7875bdf38f10540819c5f6a38bd5bd39028 HEAD -- crates migrations scripts Cargo.toml
../../scripts/install_git_hooks.sh
cargo test -p bcs-edge-permission-store -p bcs-app-bot -p bcs-app-session
```

保留基线失败证据。以下提交按依赖顺序完成，单个任务可分多次 red/green cycle；不能把“大任务的全部代码”当成一个 2–5 分钟步骤。

```text
R1/R2/R3 pure-refactor commits → 1 schema/runtime isolation
R4/R5 pure-refactor commits → 2 authority reads/contracts → 3 atomic mutation
                                                    └→ 4 actor lifecycle/delete
2+3 → 5 managers application/HTTP (unmounted)
2 → 6 mine/Bot → 7 mixed identity → 8 Group/Workbench → 9 Session/launch
7+9 → 10 files → 11 invitations/eventing
8+9 → 12 WS connect binding → 13 outbound/run/SSE revocation
1..13 → 14 production assembly + route mount → 15 live conformance/release gate
```

不要在任务 14 前向共享部署数据库 seed Manage；中间提交仅在隔离环境验证。

**编译传播规则：**签名变化的任务必须同时更新全部 constructor 调用、trait impl 和 test doubles，并跑 `cargo check --workspace --all-targets`；不能把编译失败留给任务 14。R4/R5 先独立提交 server / repo harness 纯移动，任务 2 再完成 SQL Core/Hook factory，任务 3 接上共享 Memory 实现；任务 4–13 各自在真实 composition root 注入新必需参数，但仍不挂管理写路由。任务 14 是全链路装配验收与最终挂载，不是第一次修复所有调用点。

## 1. 文件结构与拆分约束

### 1.1 新增功能单元

| 文件/目录（Create） | 单一职责 | 所属任务 |
| --- | --- | --- |
| `crates/contracts/bcs-domain/src/bot_authority.rs` | 管理身份长度、边形状、授权结果与 mutation 数据类型 | 1–2 |
| `crates/service-api/bcs-service-api/src/core/bot_authority.rs` | authority Core、严格读写与生命周期合同 | 2–4 |
| `crates/service-api/bcs-service-api/src/application/v1/bot_authority.rs` | 注册 Hook、当前 caller 解析、持续授权上下文 | 2、7、12 |
| `crates/service-api/bcs-service-api/src/application/v1/bot_manager.rs` | manager 用例 DTO / Service API | 5 |
| `crates/service-api/bcs-service-api/src/port/repo/bot_authority.rs` | authority Repo 与事务/生命周期语义 | 2–4 |
| `crates/services/bcs-edge-permission/src/authority.rs` | repo → Core 授权事实解释 | 2 |
| `crates/services/bcs-edge-permission-store/src/authority/{mod,read,mutation,lifecycle,sql}.rs` | 严格查询、管理事务、删除事务、方言 SQL | 2–4 |
| `crates/services/bcs-bot-store/src/memory/authority.rs` | MemoryBotRepo 共享 Actor 状态的 authority repo 实现 | 3–4 |
| `crates/application/v1/bcs-app-bot/src/{authority,managers,mine}.rs` | Hook 应用适配、管理用例、mine 投影 | 2、5–6 |
| `crates/adapters/http/bcs-api-http/src/v1/openapi/{dto,routes}/bot_manager.rs` | 管理 API wire DTO 与薄 handler | 5 |
| `crates/adapters/ws/bcs-ws/src/web/{connect,continuous_authorization}.rs` | WS 帧翻译、出站复核调用与连接失效 | 12–13 |
| `crates/bootstrap/bcs/src/bot_authority_wiring.rs` | production/Memory 共用 authority 装配入口 | 2–3、14 |
| `crates/test-support/bcs-test-support/src/contract/{repo,core,application}/bot_authority.rs` | 跨实现 conformance 与 Hook recording 断言 | 2–4 |
| `crates/bootstrap/bcs/src/bot_authority_metrics.rs` | authority / manager / continuous Service 的观测 decorator，复用现有 recorder | 13–14 |
| `scripts/e2e-test/bot_manage_load.py` | 固定负载、owner 基线对比、性能预算和 JSON 报告 | 13、15 |
| `docs/superpowers/specs/2026-09-18-bot-manage-permission-propagation.md` | 入口→应用→下游→测试逐项证据 | 8–15 |

新增模块必须在其父 `mod.rs` / `lib.rs` 显式导出；所有受影响 `Cargo.toml` 只补已有 workspace crate 的依赖/dev-dependency。新增 contract 同时登记 conformance harness，而不是先加空 trait 等最后补测试。

### 1.2 已超限文件：纯移动先独立提交，行为任务不得夹带移动

下表是具体分拆落点；保持已有 public exports，迁移相关 impl 的方法，不重新格式化函数体。表中原文件全部为 Modify，箭头后的文件全部为 Create；每个文件目标最多 900 行，为本轮增量留余量。每个原 crate/职责的移动独立成一个可回滚、可评审的 refactor commit；只允许必要 mod/use/pub(crate) 可见性调整，保持函数体、SQL、migration checksum、构造器签名和测试断言不变。移动前后均运行同一组原测试；该提交不得出现 Manage enum、schema、Hook 参数或新业务断言。完成提交后再开始对应行为任务的 red/green。

| 原文件 | 分拆落点 | 任务 |
| --- | --- | --- |
| `crates/services/bcs-edge-permission/src/lib.rs`（3,641 行） | `connect.rs`、`connect_edges.rs`、`admission.rs`、`notifications.rs`、`tests/{connect,admission,friendship}.rs` | R1（Task 1 前） |
| `crates/services/bcs-edge-permission-store/src/lib.rs`（2,293 行） | `edge_grant.rs`、`permission_profile.rs`、`permission_request.rs`、`bot_actor_config.rs`、`decode.rs`、`tests/{grants,requests,profiles}.rs` | R2（Task 1 前） |
| `crates/bootstrap/bcs/src/migrations.rs`（3,169 行） | `migrations/{schema_actors,schema_sessions,schema_delivery,schema_permissions,versions,runner,upgrades,tests,collection_tests,eventing_tests}.rs` | R3（Task 1 前） |
| `crates/services/bcs-bot-store/src/{lib,memory}.rs`（3,870 / 2,832 行） | `persistent/{registry,persistence,control_plane,connections,lifecycle}.rs`、`memory/{registry,control_plane,connections,persistence,lifecycle}.rs` | 3–4 |
| `crates/services/bcs-bot/src/application/bot.rs`（1,582 行） | `bot/{query,management,projection,authorization}.rs` | 4、6 |
| `crates/application/v1/bcs-app-group/src/lib.rs`（2,970 行） | `authorization.rs`、`query.rs`、`create.rs`、`mutation.rs`、`participants.rs`、`projection.rs` | 8 |
| `crates/services/bcs-group/src/application/management.rs`（2,653 行） | `management/{authorization,query,create,mutation,participants,workbench,projection}.rs` | 8 |
| `crates/application/v1/bcs-app-session/src/lib.rs`（1,837 行） | `authorization.rs`、`query.rs`、`mutation.rs`、`participants.rs`、`projection.rs` | 9 |
| `crates/services/bcs-session/src/application.rs`（1,169 行） | `application/{query,mutation,participants}.rs` | 9 |
| `crates/application/v1/bcs-app-invitation/src/lib.rs`（1,153 行） | `authorization.rs`、`invitations.rs`、`friendships.rs`、`projection.rs` | 11 |
| `crates/services/bcs-eventing/src/subscription.rs`（1,491 行） | `subscription/{query,mutation,group_preparation,validation}.rs` | 11 |
| `crates/adapters/ws/bcs-ws/src/web/dispatcher.rs`（1,375 行） | `connect.rs`、`control.rs`、`replay.rs`；dispatcher 只保留 frame dispatch | 12–13 |
| `crates/bootstrap/bcs/src/server.rs`（6,755 行） | `server/{state,v1_services,memory_services,persistent_services,runtime_services,http_ws,tests}.rs`；新 authority wiring 由 Task 2 另建 | R4（Task 2 前） |

`server/tests.rs` 或任何分拆结果仍超限时，继续拆成同目录下按现有测试模块命名的文件；不能以 include 文本规避单文件上限。`crates/test-support/bcs-test-support/src/contract/repo/mod.rs` 已有 2,961 行；R5 在任务 2 增加导出前，将原 harness 按 trait 移到 `repo/{bot,friend,friend_request,group,session,relation,message}.rs`，保留同名 re-export 并运行原 drivers。`crates/test-support/bcs-test-support/src/noop.rs` 已有 2,142 行，本轮优先新增独立 `bot_authority_noop.rs`；若必须改原 noop，实现按 bot/group/session/delivery 拆到 `noop/`，原文件只保留 re-export。同一规则适用于修改到的现有超长 integration test 和 trait 文件。

### 1.3 前置纯重构提交清单（M2）

以下不是一个囊括 16,000 行移动的 Task 0；每行是独立提交，禁止合并成一个 feat commit。其 Files 就是 §1.2 对应原文件/落点，Interfaces 一律保持原 public API。Task 1 依赖 R1–R3，Task 2 依赖 R4–R5。

| 前置项 | 移动范围 | 移动前后都运行的原测试 | 独立提交标题 |
| --- | --- | --- | --- |
| R1 | edge-permission Core/application 与 inline tests | `cargo test -p bcs-edge-permission` | `refactor(bcs): split edge permission service responsibilities` |
| R2 | edge-permission-store 各 repo / decoder / tests | `cargo test -p bcs-edge-permission-store` | `refactor(bcs): split edge permission repositories` |
| R3 | SQLite DDL/runner/upgrades/tests；不新增 migration | `cargo test -p bcs migrations` | `refactor(bcs): separate sqlite migration modules` |
| R4 | server composition/state/tests；不添加 authority 依赖 | `cargo test -p bcs --lib` | `refactor(bcs): split server composition responsibilities` |
| R5 | centralized repo contract harness；不新增 trait/harness | `cargo test -p bcs-test-support -p bcs-bot-store -p bcs-edge-permission-store` | `refactor(bcs): split repository contract harnesses` |

每行分别执行以下步骤并记录 commit SHA，不能把步骤 5 延迟到行为实现完成：

- [ ] **R-Step 1 — 记录原测试 baseline。** 运行表中该行命令；若失败，保存原失败并先处理基线问题，不将失败归因于纯移动。
- [ ] **R-Step 2 — 只移动一个表中范围。** 按 §1.2 路径拆分并保留 exports；先把测试移到独立模块，再搬完整 impl/函数，不改授权、SQL 或断言。
- [ ] **R-Step 3 — 验证行为未变。** 重跑同一测试命令及 `cargo check --workspace --all-targets`，确认该提交涉及源文件 ≤1,000 行；R3 另外比较旧 migration checksum 与 schema DDL 内容未变化。
- [ ] **R-Step 4 — 审阅移动 diff。** 使用下列命令检查；`--color-moved` 只是辅助，不能替代逐段确认函数体/常量原样。

```bash
git diff --check
git diff --color-moved=blocks --color-moved-ws=no
```

- [ ] **R-Step 5 — 独立提交。** 仅 stage 该行原文件与拆分落点，使用表中对应 `refactor(bcs)` 标题；记录结果后再进入下一行或行为任务。

Task 3/4/8/9/11/12 涉及的剩余 §1.2 超限文件同样执行这套“原测试→纯移动→原测试→独立 refactor commit→行为 red/green”协议；每个 crate 分开提交。后续 feat/fix commit 只包含新语义，不再次 squash 掉这些评审边界。

## 2. 固定接口词汇（后续任务共用，不临时发明第二套）

这些是待新增类型；已有类型从 `bcs-domain` 或 `bcs-service-api` 导入。所有查询中的 `env` 由 Core 构造时注入，再传 repo；客户端命令不带 env。

```rust
// contracts/bcs-domain/src/bot_authority.rs
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BotAuthority { Owned, Manage { edge_id: u64 }, LegacyCreator, Denied }
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthorityPurpose {
    StrictControl,
    LegacyGroupActAs,
    LegacySessionActAs,
    LegacyWorkbenchActAs,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ManagerChange { Grant, Revoke }
#[derive(Debug, Clone)]
pub struct ManagerMutation {
    pub operator_user_id: String,
    pub bot_id: String,
    pub manager_user_id: String,
    pub change: ManagerChange,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ManagerMutationResult {
    pub changed: bool,
    // Revoke of a never-existing edge legitimately returns None.
    pub edge_id: Option<u64>,
}
#[derive(Debug, Clone)]
pub struct ActorRetirement {
    pub actor_id: String,
    pub audit_operator: String,
    pub reason: String,
}
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActorRetirementResult {
    pub changed: bool,
    pub revoked_edge_ids: Vec<u64>,
}
```

`audit_operator` 仅由既有可信生命周期应用入口构造：有人类操作者时为完整 Human Actor ID；Provider/System 生命周期沿用可信操作者标识，不接收 HTTP 自报字段。`reason` 固定为对应入口的删除原因，不允许借此增加授权。

```rust
// service-api/port/repo/bot_authority.rs
#[derive(Debug, Clone)]
pub struct AuthorityFacts {
    pub bot: BotControlPlaneRecord,
    pub human_exists: bool,
    pub manage_edges: Vec<EdgeGrant>,
    pub legacy_creator: bool,
}
#[derive(Debug, Clone)]
pub struct ExplicitManager {
    pub user_id: String,
    pub actor_id: String,
    pub edge_id: u64,
}
#[derive(Debug, Clone)]
pub struct ExplicitManagerPage {
    /// Unique approved explicit managers, ordered by user_id ASC in UTF-8 byte order.
    /// HTTP projects this internal user_id as id; ownership/audit fields keep their names.
    /// No case folding; owner excluded. Apply dedup/filter before offset/limit.
    pub items: Vec<ExplicitManager>,
    /// Complete eligible count before pagination, including when the page is empty.
    pub total: u64,
}
#[async_trait]
pub trait BotAuthorityRepoPort: Send + Sync {
    async fn read_facts(&self, user_id: &str, bot_ids: &[String], env: &str)
        -> ServiceResult<Vec<AuthorityFacts>>;
    async fn list_control_candidates(&self, user_id: &str, env: &str)
        -> ServiceResult<Vec<AuthorityFacts>>;
    async fn list_managers(&self, bot_id: &str, offset: u64, limit: u64, env: &str)
        -> ServiceResult<ExplicitManagerPage>;
    async fn mutate_manager(&self, command: ManagerMutation, env: &str)
        -> ServiceResult<ManagerMutationResult>;
    async fn retire_actor(&self, command: ActorRetirement, env: &str)
        -> ServiceResult<ActorRetirementResult>;
}
```

`read_facts` 的每条 `manage_edges` 只包含当前 user→该 Bot 的 approved Manage 边（坏行不跳过）；合法性由 Core 复核。缺失 Bot 不生成占位实体，Core 将该精确 ID 判为 Denied；读失败/解码失败不得省略行。`list_control_candidates` 通过 owner 索引与 Manage 出边候选并集查询，不扫描全站。`legacy_creator` 只在具体兼容 purpose 使用；永不进入 controllable 集合。

```rust
// service-api/core/bot_authority.rs
#[async_trait]
pub trait BotAuthorityCoreService: Send + Sync {
    async fn resolve(&self, user_id: &str, bot_id: &str, purpose: AuthorityPurpose)
        -> ServiceResult<BotAuthority>;
    async fn resolve_many(&self, user_id: &str, bot_ids: &[String], purpose: AuthorityPurpose)
        -> ServiceResult<std::collections::BTreeMap<String, BotAuthority>>;
    async fn controllable(&self, user_id: &str)
        -> ServiceResult<Vec<BotControlPlaneRecord>>;
    async fn managers(&self, bot_id: &str, offset: u64, limit: u64)
        -> ServiceResult<ExplicitManagerPage>;
    async fn mutate_manager(&self, command: ManagerMutation)
        -> ServiceResult<ManagerMutationResult>;
    async fn retire_actor(&self, command: ActorRetirement)
        -> ServiceResult<ActorRetirementResult>;
}
// service-api/application/v1/bot_authority.rs
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ViewActor {
    Human { actor_id: String },
    Bot { actor_id: String },
}
impl ViewActor {
    pub fn actor_id(&self) -> &str {
        match self {
            Self::Human { actor_id } | Self::Bot { actor_id } => actor_id,
        }
    }
}
#[async_trait]
pub trait BotAuthorityHook: Send + Sync {
    async fn resolve(&self, caller: &AuthenticatedCaller, bot_id: &str, purpose: AuthorityPurpose)
        -> Result<BotAuthority, ApplicationError>;
    async fn resolve_many(&self, caller: &AuthenticatedCaller, bot_ids: &[String], purpose: AuthorityPurpose)
        -> Result<std::collections::BTreeMap<String, BotAuthority>, ApplicationError>;
    async fn controllable(&self, caller: &AuthenticatedCaller)
        -> Result<Vec<BotControlPlaneRecord>, ApplicationError>;
    async fn resolve_view(&self, caller: &AuthenticatedCaller, requested: Option<&str>)
        -> Result<ViewActor, ApplicationError>;
}
```

Hook 应用适配在 `bcs-app-bot/src/authority.rs` 中验证可信 caller、处理本人 Human 视角并映射错误；Core 解释领域事实，repo 执行 SQL。`BotAuthorityCore::new(repo: Arc<dyn BotAuthorityRepoPort>, env: String)` 固定环境，不在方法内读取进程环境变量。ViewActor 是已解析的视角类型，不是可以跨请求缓存的授权凭证；wire 输入仍为 String，Hook 用本人身份或 Bot record.kind 确定变体，下游只 match 变体并用 `actor_id()`，不重新用 `starts_with("human_")` 判断。两层不能是 re-export alias。Noop 对所有授权返回 Denied/Forbidden，写入返回错误；不伪装空成功。

---

### Task 1: Manage 类型、schema 迁移与 runtime 隔离

**前置提交：**R1、R2、R3 已分别通过原测试并独立提交，记录三个 SHA；本任务只做行为增量，不在 red/green 中移动旧模块。

**Files:**
- Modify: `crates/contracts/bcs-domain/src/{edge_permission,lib}.rs`
- Create: `crates/contracts/bcs-domain/src/bot_authority.rs`
- Create: `migrations/mysql/027_bot_manage_permission.sql`
- Modify: §1.2 的 edge permission、store、SQLite migrations 拆分文件
- Create: `crates/bootstrap/bcs/src/migrations/bot_manage.rs`
- Create: `crates/services/bcs-edge-permission/tests/manage_runtime_isolation.rs`
- Create: `crates/services/bcs-edge-permission-store/tests/conformance_cross_kind_grants.rs`
- Modify: `crates/test-support/bcs-test-support/src/contract/repo/edge_grant.rs`
- Create: `crates/bootstrap/bcs/tests/bot_manage_migration.rs`
- Modify: `docs/superpowers/specs/2026-08-18-friend-edge-permission-reform.md`

**Interfaces:** consumes `GrantKind`、`RequestKind`、`EdgeGrantRepoPort`、`DbPlugin::transaction`；produces `GrantKind::Manage`、`RequestKind::Manage`、`validate_manage_user_id(&str) -> Result<(), String>`、`validate_manage_edge(&EdgeGrant) -> Result<(), String>`。保留 `RequestKind::Revoke`，靠关联边类别区分管理撤权审计。

- [ ] **Step 1 — 写失败测试。** 新增序列化与长度测试；Admission 测试仅 seed Manage，断言 protected Bot 不准入、AuthzContext 不含 Manage；再 seed friend，断言原准入恢复。迁移测试从旧四元组索引建库并保存原 edge_id/audit_id；AC26 conformance 在迁移后 seed 同 from/to/env/ref 的 profile/rules，验证独立 ID、upsert 回查精确 kind、friend 去重与撤好友后 Rules 保留。

```rust
#[test]
fn manage_identity_length_counts_unicode_scalars() {
    assert!(validate_manage_user_id(&"界".repeat(250)).is_ok());
    assert!(validate_manage_user_id(&"u".repeat(251)).is_err());
    assert!(validate_manage_user_id("   ").is_err());
}
#[test]
fn manage_enum_round_trips() {
    let kind: GrantKind = serde_json::from_str("\"manage\"").unwrap();
    assert_eq!(kind, GrantKind::Manage);
    assert_eq!(serde_json::to_string(&kind).unwrap(), "\"manage\"");
}
```

- [ ] **Step 2 — 确认 red。** `cargo test -p bcs-domain manage_` 应先因新类型/函数缺失失败；`cargo test -p bcs-edge-permission --test manage_runtime_isolation` 记录 Manage 被错误接受的断言或新增枚举尚不支持的失败。
- [ ] **Step 3 — 在已提交的纯重构基础上实现类型、迁移及过滤。** MySQL 增量如下；实际执行前对 actor 长度和旧数据执行预检，不改历史 migration 文件内容。

```sql
ALTER TABLE edge_grants
  DROP INDEX uk_edge_from_to_env_ref,
  ADD UNIQUE KEY uk_edge_from_to_env_kind_ref
    (from_id, to_id, env, grant_kind, grant_ref_id);
ALTER TABLE permission_requests
  MODIFY created_by VARCHAR(256) NOT NULL,
  MODIFY decided_by VARCHAR(256) DEFAULT NULL;
```

SQLite 添加版本 **28 `bot_manage_permission`**，旧表 rebuild 保留所有列、ID、gmt、revoked 状态，移除旧 UNIQUE 并建立五元组唯一键；重跑无变化，失败整体回滚。新库 bootstrap 同样使用五元组。旧版本 checksum 不变化。SQL runtime 查询显式 `grant_kind IN ('permission_profile','rules')`；Rust projection 再做类型过滤，不仅依赖 decoder 跳过 Manage。

```rust
let runtime_grants = active.into_iter().filter(|grant| {
    matches!(grant.grant_kind, GrantKind::PermissionProfile | GrantKind::Rules)
});
```

- [ ] **Step 4 — green 与回归。** `cargo test -p bcs-domain -p bcs-edge-permission-store -p bcs-edge-permission`；`cargo test -p bcs --test bot_manage_migration`。证明旧 friend/default 行与 gmt 不变、旧四元组可因不同 kind 并存、SQLite 再启动可用。`cargo test -p bcs-edge-permission-store --test conformance_cross_kind_grants` 调用集中 edge harness 的 `cross_kind_grants_contract_tests`。下面是该 harness 的核心断言（driver 已建五元组 schema，传入同一四元组的两个合法 seed）：

```rust
pub async fn cross_kind_grants_contract_tests(
    repo: &dyn EdgeGrantRepoPort, profile: EdgeGrant, rules: EdgeGrant,
) {
    assert_eq!(profile.grant_kind, GrantKind::PermissionProfile);
    assert_eq!(rules.grant_kind, GrantKind::Rules);
    assert_eq!((&profile.from_id, &profile.to_id, &profile.env, profile.grant_ref_id),
               (&rules.from_id, &rules.to_id, &rules.env, rules.grant_ref_id));
    let profile_id = repo.insert_grant(profile.clone()).await.unwrap();
    let rules_id = repo.insert_grant(rules.clone()).await.unwrap();
    assert_ne!(profile_id, rules_id);
    assert_eq!(repo.insert_grant(rules.clone()).await.unwrap(), rules_id);
    assert!(repo.has_friend_edge(&profile.from_id, &profile.to_id, &profile.env).await);
    repo.revoke_grant(profile_id, &profile.env).await.unwrap();
    assert!(!repo.has_friend_edge(&profile.from_id, &profile.to_id, &profile.env).await);
    let active = repo.list_active_grants(&rules.from_id, &rules.to_id, &rules.env).await;
    assert!(active.iter().any(|edge| edge.edge_id == rules_id && edge.grant_kind == GrantKind::Rules));
}
```

另在 service 层执行真正 `revoke_friend` 并断言 Rules/runtime 保留、Manage 不受影响；名单分页计数不能因同 ref 双 kind 变为 2。MySQL live 放任务 15，不把 SQL 字符串测试当 live 通过。
- [ ] **Step 5 — 提交。** 检查本任务源文件行数和 `git diff --check` 后，按 Files 精确 stage；`git commit -m "feat(bcs): isolate manage grants and migrate permission storage"`。

### Task 2: 统一 Authority Hook、严格批量读取与 conformance

**前置提交：**R4/R5 的 server 与 repo harness 纯移动已分别提交且原测试通过。本任务新增 authority 栈；不在同一 feat commit 搬移 server.rs 或原 harness。

**Files:**
- Create: §1.1 中 authority Domain/Core/Hook/Repo 文件和 `bcs-app-bot/src/authority.rs`
- Create: `crates/services/bcs-edge-permission/src/authority.rs`
- Create: `crates/services/bcs-edge-permission-store/src/authority/{mod,read,sql}.rs`
- Create: `crates/test-support/bcs-test-support/src/bot_authority_noop.rs`
- Create: `crates/test-support/bcs-test-support/src/contract/{repo,core,application}/bot_authority.rs`
- Create: `crates/services/bcs-edge-permission/tests/authority.rs`
- Create: `crates/services/bcs-edge-permission-store/tests/conformance_bot_authority.rs`
- Modify: 对应 `mod.rs` / `lib.rs`、`CONTEXT.md`、测试 crate 的 `Cargo.toml`
- Create: `crates/bootstrap/bcs/src/bot_authority_wiring.rs`
- Modify: R4/R5 已提交的 server / repo harness 模块，仅追加新依赖/导出；production/test constructor 在本任务同步编译

**Interfaces:** §2 的三个 trait 与类型。Core 实现命名 `BotAuthorityCore`；SQL repo 为 `DbBotAuthorityStore::{sqlite,mysql}(Arc<dyn DbPlugin>)`；Hook 为 `BotAuthorityHookImpl::new(Arc<dyn BotAuthorityCoreService>)`。repo mutation/retire 在本任务暂返回明确 Unsupported/Internal error，不能成功；下一任务替换，不挂生产路由。

- [ ] **Step 1 — 写失败用例。** owner 优先、纯 manage、非物理 Bot、跨 env、非法边形状、损坏 JSON、SQL 错误、仅 legacy creator、默认 wildcard。建立集中 harness `bot_authority_repo_port_contract_tests`、`bot_authority_core_service_contract_tests`、`bot_authority_hook_contract_tests`；所有 conformance driver 调用对应 harness。

```rust
pub async fn assert_owner_priority(core: &dyn BotAuthorityCoreService) {
    // Driver seeds bot-x.created_by=alice AND an approved alice -> bot-x Manage edge.
    assert_eq!(core.resolve("alice", "bot-x", AuthorityPurpose::StrictControl)
        .await.unwrap(), BotAuthority::Owned);
    assert_eq!(core.resolve("mallory", "bot-x", AuthorityPurpose::StrictControl)
        .await.unwrap(), BotAuthority::Denied);
}
```

- [ ] **Step 2 — red。** `cargo test -p bcs-edge-permission --test authority` 和 `cargo test -p bcs-edge-permission-store --test conformance_bot_authority`；失败必须指向新能力而非 DB 不可连接。
- [ ] **Step 3 — 实现严格读与 pure evaluator。** `read_facts` 一批查询 Actor/Manage/relation，不循环逐 Bot 查边；当前 env 与 `is_deleted=0` 从持久化读取；未知枚举、坏行返回 Err。`resolve_many` 对缺失 Bot 填 Denied；owner 从 `created_by` 精确比较，manage 完整校验；legacy 仅在三个已命名 purpose 放行。owned/manages 候选并集不提前分页。Hook 的 `resolve_view(None)` 返回 `ViewActor::Human { actor_id: human_self }`，显式 Bot 通过后返回 `ViewActor::Bot { actor_id }`，显式其他 Human 拒绝。名单 SQL 用 MySQL binary / SQLite COLLATE BINARY 排序，Memory 用同样 String 字节序；大小写/中文 ID、空页 total 与分页顺序必须跨实现一致。

```rust
// evaluator receives already decoded facts, and validates every Manage row first.
let owned = facts.bot.created_by.as_deref() == Some(user_id);
if owned { return Ok(BotAuthority::Owned); }
if let Some(edge) = valid_manage_edge { return Ok(BotAuthority::Manage { edge_id: edge.edge_id }); }
if purpose != AuthorityPurpose::StrictControl && facts.legacy_creator {
    return Ok(BotAuthority::LegacyCreator);
}
Ok(BotAuthority::Denied)
```

`valid_manage_edge` 是同一 evaluator 内由完整形状、Human 存活、Bot kind/env/status 检验得到的局部变量，不是第二个 service 或缓存。只用 hidden 禁止协作，不因 hidden 否定管理权。`validate_manage_edge` 仅校验 Manage 形状；SQL 类型/状态 decoder 的错误也必须原样向上传播，不在 owner shortcut 前静默丢弃。

- [ ] **Step 4 — green。** 跑上述测试及 `cargo test -p bcs-service-api -p bcs-app-bot`；recording repo 断言 batch 调用次数不随 Bot 数量线性增加，Hook recording 断言应用确实调用注册扩展点。
- [ ] **Step 5 — 提交。** `git commit -m "feat(bcs): centralize strict bot authority resolution"`（先 stage 本任务 Files）。

### Task 3: 原子 grant/revoke、审计与共享 Memory 状态

**Files:**
- Create: `crates/services/bcs-edge-permission-store/src/authority/mutation.rs`
- Create: `crates/services/bcs-bot-store/src/memory/authority.rs`
- Modify: §1.2 MemoryBotRepo 拆分文件、`bcs-edge-permission/src/authority.rs`
- Modify: `crates/services/bcs-edge-permission/src/connect.rs`、store 的 `permission_request.rs`
- Create: `crates/services/bcs-edge-permission-store/tests/manage_mutation.rs`
- Create: `crates/services/bcs-bot-store/tests/conformance_bot_authority.rs`
- Modify: 集中 repo harness 与现有 `edge_permission_noop.rs` / recording implementations

**Interfaces:** 实现 `mutate_manager`；MemoryBotRepo 同时提供 `BotAuthorityRepoPort`，并在本任务补齐任务 2 定义的严格读/名单方法，运行同一 repo harness；不创建独立 Actor map。其管理边与审计放在同一 repo 的 authority 状态，Actor 更改和 authority 更改共同受 `authority_lifecycle_lock: tokio::sync::Mutex<()>` 保护；不把数据库模式的进程锁当成并发保证。

- [ ] **Step 1 — 写失败测试。** 初次 grant changed=true、重复 grant false、revoke true、重复 revoke false、再次 grant 恢复同一 edge_id。AC25 增加 Alice→Bob、Bob→Carol/Dave、Alice 撤 Bob 后 Carol/Dave 仍有效的用例；owner 以 limit=1 先读完整名单快照，再逐项撤销并重查，不边删边递增 offset。owner 对自身 mutation 409；manager 互撤按串行顺序；operator 被撤权后不允许再 grant。失败注入覆盖 edge 写、audit 写、commit。

```rust
pub async fn assert_manage_regrant(repo: &dyn BotAuthorityRepoPort) {
    // Driver seeds alive human_alice, human_bob and bot-x owned by alice in test env.
    let mut cmd = ManagerMutation {
        operator_user_id: "alice".into(), bot_id: "bot-x".into(),
        manager_user_id: "bob".into(), change: ManagerChange::Grant,
    };
    let first = repo.mutate_manager(cmd.clone(), "test").await.unwrap();
    assert!(first.changed);
    assert!(!repo.mutate_manager(cmd.clone(), "test").await.unwrap().changed);
    cmd.change = ManagerChange::Revoke;
    assert!(repo.mutate_manager(cmd.clone(), "test").await.unwrap().changed);
    cmd.change = ManagerChange::Grant;
    let again = repo.mutate_manager(cmd, "test").await.unwrap();
    assert!(again.changed);
    assert_eq!(again.edge_id, first.edge_id);
}
```

- [ ] **Step 2 — red。** `cargo test -p bcs-edge-permission-store --test manage_mutation`；`cargo test -p bcs-bot-store --test conformance_bot_authority`。现有方法的 Unsupported 不算完成。
- [ ] **Step 3 — 实现锁定与条件事务。** SQL builder 按 Human IDs 字节序发出锁定当前读，再锁 Bot；锁后重新验证 operator owner/manage、Human/Bot 存活与边形状；复用 DbTransactionParam 传递查询结果，条件写失败使整事务回滚。状态不变不 append audit；不存在的 revoke 返回 `{changed:false, edge_id:None}`。不要用 INSERT IGNORE 实现 regrant。

```text
lock sorted operator/grantee Human rows
→ lock target Bot row
→ assert alive/type/env + current operator authority
→ read exact (from,to,env,Manage,0) edge
→ mutate only if state differs
→ append one decided permission_request for each actual change
→ commit and return changed + edge_id
```

DbPlugin transaction 是 statement batch，不是可任意 await 的事务闭包：条件必须编码到 SQL guard / ExecuteChecked / query-result bindings，不能先提交再由 Rust 校验 cardinality。无效授权映射 Forbidden，坏数据/存储故障映射 Internal。Memory 在同一临界区验证、更新 Actor/边/审计；事务失败注入前后快照相同。

好友 inbox 与 Connect get/approve/reject/cancel 对管理记录拒绝处理，revoke 记录通过 edge.kind 识别；不能只过滤 `request_kind=manage` 而漏掉 `request_kind=revoke`。

- [ ] **Step 4 — green。** 跑本任务测试、原 edge grant / permission request conformance 和 `cargo test -p bcs-edge-permission`；确认 friend/manage 共存、互不撤销，审计记录数等于真实状态变化次数。
- [ ] **Step 5 — 提交。** `git commit -m "feat(bcs): transact manager grants with immutable audit decisions"`。

### Task 4: Actor 删除并发、Result 型生命周期与 TC/Provider 保护

**Files:**
- Create: `crates/services/bcs-edge-permission-store/src/authority/lifecycle.rs`
- Modify: `crates/service-api/bcs-service-api/src/{core/registry,port/repo/bot}.rs`
- Modify: `crates/services/bcs-bot/src/core/bot_core.rs`
- Modify: `crates/services/bcs-bot/src/application/bot/{management,authorization}.rs`、`application/provider.rs`
- Modify: `crates/services/bcs-bot-store/src/{persistent,memory}/lifecycle.rs` 与 Memory authority
- Create: `crates/services/bcs-edge-permission-store/tests/manage_lifecycle.rs`
- Create: `crates/services/bcs-bot/tests/manage_delete_policy.rs`
- Modify: `crates/services/bcs-bot-store/tests/conformance_bot_repo.rs`

**Interfaces:** `retire_actor` 执行 spec §6.3；registry/repo 将生产删除入口改为 `try_soft_delete(command: ActorRetirement) -> ServiceResult<ActorRetirementResult>`，并用必需的 `try_unregister(command: ActorRetirement) -> ServiceResult<ActorRetirementResult>` 替换生产 unregister 删除路径。`unregister` 的持久化删除也迁移到 Result 型命令；若保留兼容 bool wrapper，只能在非生产测试过渡使用，不能吞掉生产写失败。

- [ ] **Step 1 — 写失败测试。** 两个独立 DB 连接设置 barrier，分别控制 grant 先锁与 delete 先锁；删除后显式治理恢复同 ID，再查 authority 必须 Denied；恢复通过测试夹具中的治理事务锁原墓碑、确认旧边 revoked 后清除 is_deleted，或重启重新装载，不新增产品恢复 API。测试 Provider delete/unregister 走同一生命周期 repo。TC regression 使用无 Provider binding 的 `teamclaw-bot:alice`。

```rust
#[test]
fn tc_delete_restriction_uses_resource_owner() {
    // New pure helper in application/bot/authorization.rs, not an auth decision.
    assert!(is_tc_owned_resource("teamclaw-bot:alice", Some("alice")));
    assert!(is_tc_owned_resource("teamclaw-bot:a:b", Some("a:b")));
    assert!(is_tc_owned_resource("any-prefix:alice", Some("alice")));
    assert!(!is_tc_owned_resource("teamclaw-bot:a:b", Some("a")));
    assert!(!is_tc_owned_resource("teamclaw-bot:alicex", Some("alice")));
    assert!(!is_tc_owned_resource("ordinary-bot", Some("alice")));
    assert!(!is_tc_owned_resource("teamclaw-bot:alice", None));
    // There is deliberately no operator_user_id argument.
}
```

- [ ] **Step 2 — red。** `cargo test -p bcs-bot --test manage_delete_policy`；`cargo test -p bcs-edge-permission-store --test manage_lifecycle`。并发测试用 Notify/barrier 和单连接 transaction wrapper，不靠 sleep 猜顺序。
- [ ] **Step 3 — 实现生命周期与纯资源限制。** Human 删除锁本人 Human 后按排序锁全部关联 Bot；Bot 删除只锁 Bot，不反向申请 Human 锁；软删除、revoked 与 audit 同事务。Memory 注册/删除/授权均进入同一生命周期临界区，内层 helper 不重复获取锁。Actor tombstone 保留；常规注册/ensure-human 不恢复删除行。

```rust
fn is_tc_owned_resource(bot_id: &str, created_by: Option<&str>) -> bool {
    created_by.is_some_and(|owner| {
        bot_id.ends_with(&format!(":{owner}"))
    })
}
```

这保留普通 owner 的任意 `:owner` 后缀保护，并修正含冒号 owner 被 `rsplit_once` 截断的边界；不是重命名即无行为变化，应留在 Task 4 fix 提交。legacy delete 顺序为可信 Human → authority → 真实 Bot 的 TC/Provider 限制 → `try_soft_delete`；cache/token/connection 清理在成功提交后，失败不得返回 `{left:true}`。Provider route 继续使用其既有凭据，不给 manager Provider 管理能力。

- [ ] **Step 4 — green。** 上述测试加 `cargo test -p bcs-bot-store -p bcs-bot`。静态枚举 `rg 'soft_delete|unregister|is_deleted = 1|DELETE FROM bcs_bots' crates`，逐个生产写入口记录到 propagation 文档；不新增 Human 删除 HTTP API。
- [ ] **Step 5 — 提交。** `git commit -m "fix(bcs): serialize actor retirement with delegated authority"`。

### Task 5: Human-only 管理用例与未挂载 HTTP 合同

**Files:**
- Create: `crates/service-api/bcs-service-api/src/application/v1/bot_manager.rs`
- Create: `crates/application/v1/bcs-app-bot/src/managers.rs`
- Create: `crates/adapters/http/bcs-api-http/src/v1/openapi/{dto,routes}/bot_manager.rs`
- Modify: 对应模块导出与 `crates/adapters/http/bcs-api-http/src/v1/common/state.rs`
- Create: `crates/application/v1/bcs-app-bot/tests/manage_service.rs`
- Create: `crates/adapters/http/bcs-api-http/tests/bot_manager_routes.rs`
- Modify: `api-contracts/v1/{domain-models,openapi}.yaml`、`api-contracts/v1/openapi/bots.yaml`

**Interfaces:** 新增 `BotManagerService`，避免与现有 legacy `BotManagementService` 重名；沿用 spec 的管理用例语义。HTTP 层使用下表映射到相同 Service API；grant/revoke Core 与事务语义不随方法名变化：

| HTTP 方法与路径 | 输入及应用调用 |
| --- | --- |
| `GET /openapi/v1/collaboration/bots/{bot_id}/managers` | query offset/limit → `BotManagerService::list` |
| `POST /openapi/v1/collaboration/bots/{bot_id}/managers` | JSON body id → `BotManagerService::grant` |
| `DELETE /openapi/v1/collaboration/bots/{bot_id}/managers` | 必填 query id，无 body → `BotManagerService::revoke` |

wire DTO 在 `openapi/dto/bot_manager.rs` 定义，两个请求都只接受单个字符串 id；不把 id 移到 path，也不接受列表/整表替换：

```rust
#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GrantBotManagerRequest {
    pub id: String,
}
#[derive(Debug, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RevokeBotManagerQuery {
    pub id: String,
}
```

空白/超长校验继续在应用入口统一执行；adapter 的 JSON/query parse rejection 映射既有 `400 invalid_request`，不把缺失输入当作默认批量操作。

```rust
pub struct ListBotManagers {
    pub caller: AuthenticatedCaller, pub bot_id: String,
    pub offset: u64, pub limit: u64,
}
#[derive(Debug, Clone)]
pub struct ChangeBotManager {
    pub caller: AuthenticatedCaller, pub bot_id: String, pub user_id: String,
}
pub struct BotManagersPage {
    pub bot_id: String, pub owner_user_id: Option<String>,
    pub items: Vec<ExplicitManager>, pub total: u64, pub offset: u64, pub limit: u64,
}
#[async_trait]
pub trait BotManagerService: Send + Sync {
    async fn list(&self, query: ListBotManagers) -> Result<BotManagersPage, ApplicationError>;
    async fn grant(&self, command: ChangeBotManager) -> Result<ManagerMutationResult, ApplicationError>;
    async fn revoke(&self, command: ChangeBotManager) -> Result<ManagerMutationResult, ApplicationError>;
}
```

实现类命名 `BotManagerServiceImpl`，构造器注入 `BotAuthorityHook`、`BotAuthorityCoreService`、`BotControlPlaneCoreService` 与现有 registry Human 物化能力。GET 在授权后用 `get_record` 读取只读 owner 字段，不从 edge 推断 ownership。`ExplicitManager.edge_id` 是内部证据，不序列化到 HTTP items；wire 仅 `id/actor_id/permission`，其中 `id` 映射自 `ExplicitManager.user_id`。POST/DELETE handler 用命令中的 bot/user ID 与 outcome 构造 spec §7 的响应（目标字段同样为 `id`），不泄漏 edge ID。内部 `ChangeBotManager.user_id`、`ManagerMutation.manager_user_id` 和 owner/operator 字段保持语义化名称，不作全局重命名。

- [ ] **Step 1 — 写失败测试。** 使用现有 HeaderVerifier 测试方式注入可信 caller，覆盖 Human-only、400/401/403/404/409/500、POST 必填 JSON body（缺失/null/空白/重复 id、旧 user_id/manager_id 别名、未知字段、误传 query 均拒绝）、DELETE 必填 query id（缺失/重复/旧别名/只传 body 均拒绝且不撤销任何边）、1..100 limit、排序、owner 单列、self-revoke 后第二次 403；验证三种方法共用复数集合路径，无其他写入路由；GET items 及 POST/DELETE 响应使用 id，不输出 user_id/manager_id 目标字段，owner_user_id 保持原义。

```rust
pub async fn assert_self_revoke_then_forbidden(
    service: &dyn BotManagerService, bob: AuthenticatedCaller,
) {
    let command = ChangeBotManager {
        caller: bob, bot_id: "bot-x".into(), user_id: "bob".into(),
    };
    assert!(service.revoke(command.clone()).await.unwrap().changed);
    assert!(matches!(service.revoke(command).await, Err(ApplicationError::Forbidden(_))));
}
```

为 command 派生 Clone；driver 先由 Alice 授权 Bob。对未知目标 Bot 固定返回既有 `404 bot_not_found`，无权用户不能用被授权 Human 的存在性枚举用户；先过资源授权再查询被授权人。

- [ ] **Step 2 — red。** `cargo test -p bcs-app-bot --test manage_service`；`cargo test -p bcs-api-http --test bot_manager_routes`。
- [ ] **Step 3 — 实现应用与薄 adapter。** POST 将 `GrantBotManagerRequest.id`、DELETE 将 `RevokeBotManagerQuery.id` 显式映射到内部 `ChangeBotManager.user_id`，并携带可信 caller 和 path bot_id；HTTP `id` 是真实 User ID，不是 edge_id 或 actor_id。先做身份/输入长度校验，再按需物化认证用户本人，不恢复墓碑；Core 做严格读取，repo 事务再检查写权限。错误映射保持 envelope；route factory 只在测试 Router 挂载，production `protected_router()` 留到任务 14。

```rust
let user = require_authenticated_user(&command.caller)?;
let mutation = ManagerMutation {
    operator_user_id: user.id.clone(), bot_id: command.bot_id,
    manager_user_id: command.user_id, change: ManagerChange::Grant,
};
// Core's result is mapped with the crate's existing map_service_error helper.
self.core.mutate_manager(mutation).await.map_err(map_service_error)
```

- [ ] **Step 4 — green。** 运行本任务测试、`python3 scripts/validate_openapi_contract.py`；响应 JSON 不含底层 SQL、凭据、edge_id。
- [ ] **Step 5 — 提交。** `git commit -m "feat(bcs): define human-only bot manager operations"`。

### Task 6: Mine 投影与 Bot 控制面 owner/manage 平权

**Files:**
- Modify: `crates/service-api/bcs-service-api/src/application/v1/bot.rs`
- Create: `crates/application/v1/bcs-app-bot/src/mine.rs`
- Modify: `crates/application/v1/bcs-app-bot/src/lib.rs`
- Modify: `crates/services/bcs-bot/src/application/bot/{query,management,authorization,projection}.rs`
- Modify: `crates/service-api/bcs-service-api/src/application/bot_query.rs`
- Modify: `crates/adapters/http/{bcs-api-http/src/v1/openapi/dto/bot.rs,bcs-http/src/routes/bots.rs}`
- Create: `crates/application/v1/bcs-app-bot/tests/manage_mine.rs`
- Modify: `api-contracts/v1/{domain-models.yaml,openapi/bots.yaml}` 与 BotService conformance

**Interfaces:** `MyBotAccessRelation::{Owned,Manage}`、`MyBot { bot: Bot, access_relation: MyBotAccessRelation }`，`BotService::list_mine(ListMyBots) -> Result<Page<MyBot>, ApplicationError>`。两个新投影类型派生 Serialize/Deserialize，关系 enum 使用 `#[serde(rename_all = "snake_case")]`，MyBot 的 bot 字段使用 `#[serde(flatten)]`。HTTP flatten `bot`；legacy `/bots/my` 专用投影，不给通用 get/query 添加标签。

- [ ] **Step 1 — 写失败测试。** owned/manage 时间交错、同 Bot 重叠、第一页/第二页/越界空页、kind/name/status/reachability 所有筛选；本人 Human 仅一条 owned，其他 Human 永不出现。

```rust
pub fn assert_mine_relation_wire(value: &serde_json::Value) {
    let items = value["data"]["items"].as_array().unwrap();
    let managed = items.iter().find(|item| item["bot_id"] == "bot-x").unwrap();
    assert_eq!(managed["access_relation"], "manage");
    assert_eq!(managed["created_by"], "alice");
    assert!(managed.get("bot").is_none());
}
```

- [ ] **Step 2 — red。** `cargo test -p bcs-app-bot --test manage_mine`；`cargo test -p bcs-api-http --test bot_routes`。
- [ ] **Step 3 — 实现并集分页与 Bot gates。** Human 物化后取 Hook controllable；批量 hydration 和 reachability；按 Bot ID 去重/owner 优先后统一 filters、`created_at DESC, bot_id ASC`、total、offset/limit。Bot patch、candidate/eligible/search 改用 StrictControl；get/query 目录语义不收紧。legacy mine 保持原 active_only/排序含义但加入相同关系标签。

```text
owned ∪ valid-managed + own Human
→ deduplicate by bot_id (Owned wins)
→ batch projection/reachability
→ kind/name/status/reachability filters
→ sort(created_at DESC, bot_id ASC)
→ total → offset/limit
```

`list_bots_by_creator` / `list_by_creator` 不改语义；记录测试禁止用旧 creator 列表单独决定 mine 或倒退到全站扫描。

- [ ] **Step 4 — green。** `cargo test -p bcs-app-bot -p bcs-bot -p bcs-api-http`；legacy mine HTTP 测试验证 manager 不被投影层再次过滤。
- [ ] **Step 5 — 提交。** `git commit -m "feat(bcs): expose owned and managed bot identities"`。

### Task 7: 异步混合身份策略与真实操作者传播

**Files:**
- Modify: `crates/service-api/bcs-service-api/src/application/v1/{authorization,bot_authority}.rs`
- Modify: `crates/adapters/http/bcs-api-http/src/v1/{common/identity_policy.rs,openapi/dto/session.rs,internal/routes/session_file.rs}`
- Modify: `crates/service-api/bcs-service-api/src/application/session_launch.rs`
- Create: `crates/service-api/bcs-service-api/tests/bot_authority_identity.rs`
- Modify: `api-contracts/v1/gateway-principal/contract.md`

**Interfaces:** 新增 `IdentityPolicy::HumanOrAuthorizedBot` 和 `select_authorized_principal(caller: &AuthenticatedCaller, policy: IdentityPolicy, hook: &dyn BotAuthorityHook) -> Result<AuthorizedActor, ApplicationError>`（async）。

```rust
pub struct AuthorizedActor {
    pub principal: Principal,
    // None only for an independently authenticated Bot-only caller.
    pub operator_user_id: Option<String>,
}
```

- [ ] **Step 1 — 写失败测试。** User+Bot owner claim 不匹配但 manage 有效可选 Bot；失权拒绝；Bot-only 只选自身，HumanOnly/BotOnly 不变；保留 operator_user_id，owner claim 原样。

```rust
pub async fn assert_mixed_identity_keeps_operator(
    caller: AuthenticatedCaller, hook: &dyn BotAuthorityHook,
) {
    let selected = select_authorized_principal(
        &caller, IdentityPolicy::HumanOrAuthorizedBot, hook,
    ).await.unwrap();
    assert_eq!(selected.operator_user_id.as_deref(), Some("bob"));
    assert_eq!(selected.principal.actor_id(), "bot-x");
    assert_eq!(caller.bot.as_ref().unwrap().owner_id, "alice");
}
```

- [ ] **Step 2 — red。** `cargo test -p bcs-service-api --test bot_authority_identity`。
- [ ] **Step 3 — 实现 selector 与边界迁移。** User+Bot 分支对精确 bot_uuid 调 Hook StrictControl；不把 bot-only 自动转换成 owner Human；纯 Human 视角选择不伪造 Bot 凭据。DTO 只传递原始 AuthenticatedCaller 和 acting 参数，在应用入口选择 Principal；internal file handler 不再执行同步 ownership 判断。SessionLaunch 命令保存 operator 与 effective actor，后续 Group/Session/文件写入审计使用两者。
- [ ] **Step 4 — green。** 上述测试加 `cargo test -p bcs-service-api -p bcs-api-http`。静态 `rg 'HumanOrOwnedBot|select_principal' crates` 的每个生产 consumer 在 propagation 中有保留理由或后续任务归属；不能无差别删除老策略。
- [ ] **Step 5 — 提交。** `git commit -m "feat(bcs): authorize mixed identities through current bot authority"`。

### Task 8: Group、legacy Workbench 与资源角色约束

**Files:**
- Modify/Create: §1.2 `bcs-app-group` 和 legacy GroupManagement 拆分文件
- Create: `crates/application/v1/bcs-app-group/tests/manage_group.rs`
- Create: `crates/services/bcs-group/tests/manage_workbench.rs`
- Create: `docs/superpowers/specs/2026-09-18-bot-manage-permission-propagation.md`
- Modify: `api-contracts/v1/openapi/groups.yaml`、两个 Group crate 的 `CONTEXT.md`

**Interfaces:** consumes `BotAuthorityHook`、`AuthorizedActor`；existing GroupService / WorkbenchSessionService 保留 resource DTO 形状。仅原来接受 creator relation 的 helper 使用 `LegacyGroupActAs` / `LegacyWorkbenchActAs`，详情、mine、显式视角使用 StrictControl。

- [ ] **Step 1 — 写失败测试。** driver/manager/originator/worker Bot × owner/manager/stranger × list/detail/update/delete/participants/workspace/chat/abort。重点断言 worker owner 和 manager 均不能管理群，空群读取不写 Session，session-only 列表不升级为正式成员。

```rust
pub async fn assert_group_view_parity(
    service: &dyn GroupService, mut query: ListGroups,
    owner: AuthenticatedCaller, manager: AuthenticatedCaller,
) {
    query.view_bot_id = Some("bot-x".into());
    query.caller = owner;
    let owned = service.list_groups(query.clone()).await.unwrap();
    query.caller = manager;
    let managed = service.list_groups(query).await.unwrap();
    assert_eq!(serde_json::to_value(owned).unwrap(), serde_json::to_value(managed).unwrap());
}
```

- [ ] **Step 2 — red。** `cargo test -p bcs-app-group --test manage_group`；`cargo test -p bcs-group --test manage_workbench`。
- [ ] **Step 3 — 替换控制谓词而非角色判定。** Query 用 Hook resolve_view / controllable；用 ViewActor 变体决定 Human/Bot 分支、`actor_id()` 传参与查询；管理操作先决定可代行 actor，再执行原 driver/manager/originator 规则。legacy Workbench sender、CanResolveInteraction、workspace 与 HTTP 下游 gate 使用同一 Hook；允许 existing creator 的入口逐行列入 propagation，禁止通用 `is_creator => controllable`。

```text
trusted caller → resolve exact controllable actor
→ existing participation / group role / lifecycle checks
→ execute existing command with real operator + effective actor
```

- [ ] **Step 4 — green。** `cargo test -p bcs-app-group -p bcs-group`；propagation 表至少记录 Group list/detail/create/mutate/delete/participants、Workbench connect/chat/abort、workspace、interaction 的函数和测试名。
- [ ] **Step 5 — 提交。** `git commit -m "feat(bcs): apply delegated authority to group and workbench operations"`。

### Task 9: Session 列表、消息、收藏与 shared launch

**Files:**
- Modify/Create: §1.2 `bcs-app-session`、`bcs-session/src/application` 拆分文件
- Modify: `crates/services/bcs-session/src/launch.rs`
- Create: `crates/application/v1/bcs-app-session/tests/manage_session.rs`
- Create: `crates/services/bcs-session/tests/manage_launch.rs`
- Modify: `api-contracts/v1/openapi/sessions.yaml`、相关 `CONTEXT.md`、propagation 文档

**Interfaces:** consumes Hook / AuthorizedActor；保留 SessionService、SessionLaunchService 的参与、scope、lifecycle 合同，acting creator 走 Hook。收藏仍以指定 Bot participant 为键，不按 manager User 建私有副本。

- [ ] **Step 1 — 写失败测试。** owner/manager 的 list/detail/launch/reactivate/mutate/delete/complete/participants/messages/collect/uncollect 成对；管理 creator Bot 可管理 Session，但非 participant 的 Bot 不能读消息；Bot 在父 Group 不代表在子 Session。

```rust
pub async fn assert_collection_is_shared(
    service: &dyn SessionService, owner: AuthenticatedCaller, manager: AuthenticatedCaller,
) {
    service.collect(CollectSession {
        caller: manager, session_id: "session-x".into(), participant: "bot-x".into(),
    }).await.unwrap();
    service.uncollect(UncollectSession {
        caller: owner, session_id: "session-x".into(), participant: "bot-x".into(),
    }).await.unwrap();
    // Driver reads the one participant collection row: it is uncollected, not a second user row.
}
```

- [ ] **Step 2 — red。** `cargo test -p bcs-app-session --test manage_session`；`cargo test -p bcs-session --test manage_launch`。
- [ ] **Step 3 — 同时修改 facade 与下游。** `human_can_act_as_any` 的已兼容路径保留 LegacySessionActAs；strict detail / view 用 StrictControl。shared launch 的 `human_has_group_access`、`resolve_creator` 不再只读 owned list；仍检查创建者参与及 Group 可见性。History 继续使用类型化 ViewActor 的精确 `actor_id()` 过滤，普通消息操作不因管理身份跳过 scope。
- [ ] **Step 4 — green。** `cargo test -p bcs-app-session -p bcs-session`；测试 recording Hook 至少验证 launch、detail、collection 调用。原 Human 自动参与/launch 业务语义不重写，manage grant 本身不添加成员。
- [ ] **Step 5 — 提交。** `git commit -m "feat(bcs): preserve session semantics for managed bot actors"`。

### Task 10: Session 文件的成员与 mutation 授权

**Files:**
- Modify: `crates/application/v1/bcs-app-session/src/file.rs`
- Create: `crates/application/v1/bcs-app-session/tests/manage_session_files.rs`
- Modify: `crates/adapters/http/bcs-api-http/src/v1/internal/routes/session_file.rs`
- Modify: `api-contracts/v1/openapi/session-files.yaml`、propagation 文档

**Interfaces:** SessionFileApplicationService 复用异步 AuthorizedActor；caller identities = 本人 Human + controllable Bot，仅在原来接受 owned Bot 的位置扩展。独立 bearer share URL 不绑定 manage 生命周期。

- [ ] **Step 1 — 写失败测试。** read/prepare/upload/complete/share/delete 的 owner/manager 对照；另一 Human 上传的文件不是 managed Bot 的文件；父 Group 成员不能替代 Session 成员；授权查询错误不得触发 storage mutation。

```rust
pub async fn assert_unrelated_human_file_denied(
    service: &dyn SessionFileApplicationService, command: DeleteSessionFile,
) {
    // Driver: caller manages bot-x; file owner is human_alice; caller has no creator/driver role.
    assert!(matches!(service.delete(command).await, Err(ApplicationError::Forbidden(_))));
}
```

- [ ] **Step 2 — red。** `cargo test -p bcs-app-session --test manage_session_files`。
- [ ] **Step 3 — 实现窄替换。** file.rs 中 member、caller-identities、upload mutation 三处所有权逻辑统一调用 Hook；文件 creator/driver、状态转换、size/hash 校验保持原规则。真实操作者写审计，不把 Human 上传归属到其 managed Bot；通用文件 storage plugin 无需知道 Manage。
- [ ] **Step 4 — green。** `cargo test -p bcs-app-session --test manage_session_files`、`cargo test -p bcs-app-session --test session_file_facade`、`cargo test -p bcs-api-http --test session_file_dto`。
- [ ] **Step 5 — 提交。** `git commit -m "feat(bcs): authorize managed bot session file operations"`。

### Task 11: 邀请、好友代行与 Group eventing 混合身份下游

**Files:**
- Modify/Create: §1.2 invitation 与 eventing subscription 拆分文件
- Create: `crates/application/v1/bcs-app-invitation/tests/manage_invitation.rs`
- Create: `crates/services/bcs-eventing/tests/manage_group_preparation.rs`
- Modify: `api-contracts/v1/openapi/{friendships,invitations}.yaml`
- Modify: invitation/eventing 的 `CONTEXT.md`（invitation 当前缺失，Create）、propagation 文档

**Interfaces:** Invitation/Friendship/FriendConnection 服务通过 Hook 授权 acting actor；`GroupEventSubscriptionService` 的 prepare 路径使用 `select_authorized_principal`，不保留旧同步 owner claim 二次拒绝。普通 event subscription 的资源/secret/webhook 限制保持不变。

- [ ] **Step 1 — 写失败测试。** manager 代 Bot 发/批/取消好友请求、邀请/接受邀请；不允许代其他 Human；管理审计不能从 Connect 流程读写。带 User Bob + Bot X(owner Alice) 的 Group 创建携带 event subscription 时，整体与无 subscription 的身份授权一致。

```rust
pub async fn assert_strict_non_owner_is_denied(
    hook: &dyn BotAuthorityHook, caller: AuthenticatedCaller,
) {
    // Driver seeds only is_creator relation, no created_by match or Manage edge.
    assert_eq!(hook.resolve(&caller, "bot-x", AuthorityPurpose::StrictControl)
        .await.unwrap(), BotAuthority::Denied);
}
```

- [ ] **Step 2 — red。** `cargo test -p bcs-app-invitation --test manage_invitation`；`cargo test -p bcs-eventing --test manage_group_preparation`。
- [ ] **Step 3 — 实现完整代行链。** 对 acting actor 与 decider/cancel 的精确 Bot 查当前 authority；好友 eligibility 仍读独立 friend edge，不因 Manage 成为 friend。eventing 准备阶段保存真实 operator 与 effective actor，失败仍走原取消准备补偿，不改变 webhook 通知寿命。
- [ ] **Step 4 — green。** `cargo test -p bcs-app-invitation -p bcs-eventing -p bcs-edge-permission`；扫描残留 HumanOrOwnedBot，确认没有未记录的生产阻断路径。
- [ ] **Step 5 — 提交。** `git commit -m "feat(bcs): propagate bot delegation through invitations and group setup"`。

### Task 12: Session-bound WS connect 授权与固定视角

**Files:**
- Modify: `crates/service-api/bcs-service-api/src/application/v1/{group_session_connection,bot_authority}.rs`
- Modify: `crates/application/v1/bcs-app-session/src/connection.rs`
- Modify/Create: `crates/adapters/ws/bcs-ws/src/web/{dispatcher,connect,auth,group_session}.rs`
- Create: `crates/application/v1/bcs-app-session/tests/manage_connection.rs`
- Create: `crates/adapters/ws/bcs-ws/tests/manage_connect.rs`
- Modify: `api-contracts/v1/openapi/connections.yaml`、connection conformance

**Interfaces:** `AuthorizeGroupSessionConnection` 新增 `view_actor_id: Option<String>`。`AuthorizedGroupSessionConnection` 新增 `authorization: ContinuousAuthorizationBinding`；已签名 `GroupSessionConnectionBinding` 和 GroupSessionTokenScope **不加字段**。

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConnectionView { LegacyFull, ExplicitActor(ViewActor) }
#[derive(Debug, Clone)]
pub struct ContinuousAuthorizationBinding {
    pub caller: AuthenticatedCaller,
    pub env: String,
    pub group_id: String,
    // None is only for existing group-scoped legacy connections, never for a Session token.
    pub session_id: Option<String>,
    pub view: ConnectionView,
}
pub const CONTINUOUS_AUTHORIZATION_DEADLINE: std::time::Duration =
    std::time::Duration::from_millis(250);
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ContinuousAuthorizationPhase { Connect, Inbound, WsOutbound, Replay, RunFallback, Sse }
#[derive(Debug, thiserror::Error)]
pub enum ContinuousAuthorizationError {
    #[error(transparent)]
    Application(#[from] ApplicationError),
    #[error("continuous authorization timed out")]
    Timeout,
}
#[async_trait]
pub trait ContinuousAuthorizationService: Send + Sync {
    async fn authorize(&self, binding: &ContinuousAuthorizationBinding, phase: ContinuousAuthorizationPhase)
        -> Result<(), ContinuousAuthorizationError>;
}
```

应用实现 `ConnectionAuthorizationServiceImpl` 定义在 `crates/application/v1/bcs-app-session/src/connection.rs`，组合 Group/Session 服务和 Hook；内部 `authorize_current(&self, binding: &ContinuousAuthorizationBinding) -> Result<(), ApplicationError>` 实现原判定，公开 authorize 用固定 deadline 包裹该调用；phase 只用于观测，不改权限。binding 的 env 来自服务装配并在复核时匹配，不信任 wire env。LegacyFull 走原资源详情授权；ExplicitActor 额外校验精确控制关系、Session/Group 参与与 mode；保留原消息 scope 投影，不扩大 scope。

- [ ] **Step 1 — 写失败测试。** Bob 非成员、仅 X 是 Session 成员：token 签发、显式 X connect 成功；其他 Human、非成员 Bot、失权 X 拒绝；省略视角保留 LegacyFull。既有 token 序列化 golden fixture 不变化。

```rust
pub async fn assert_connect_as_managed_bot(
    service: &dyn GroupSessionConnectionService,
    binding: GroupSessionConnectionBinding,
) {
    let connected = service.authorize_connect(AuthorizeGroupSessionConnection {
        binding, view_actor_id: Some("bot-x".into()),
    }).await.unwrap();
    assert_eq!(connected.authorization.view, ConnectionView::ExplicitActor(ViewActor::Bot { actor_id: "bot-x".into() }));
    assert_eq!(connected.authorization.caller.user.as_ref().unwrap().id, "bob");
}
```

- [ ] **Step 2 — red。** `cargo test -p bcs-app-session --test manage_connection`；`cargo test -p bcs-ws --test manage_connect`，先复现当前 adapter 的 forbidden_view_actor 拒绝。再覆盖 Bob Human participant 为 Absent 但显式 X 参与的情况：Human mode 不应覆盖合法 Bot 视角；显式 Human/LegacyFull 则保留原 mode 规则。
- [ ] **Step 3 — 实现 connect 协商。** 移除 adapter 的 Human-only 显式视角判断，改为传参数给应用；规范化 binding 随连接保存，第二次 connect 不能改视角。token verify 只恢复原用户/资源范围，不能恢复之前的正向授权。`authorize_connect` 形成规范化 binding 后调用注入的持续授权 Service（phase=Connect），通过后才生成 scope/participants 响应及 replay；该 Service 只依赖 Group/Session 资源服务与 Hook，不反向依赖 GroupSessionConnectionService，避免 DI 环。连接错误把 Timeout 映射成既有连接 Internal 错误并关闭，不泄漏内部异常。持续校验的固定 deadline 在应用实现中保证，不能只有安装 metrics wrapper 才生效。

```rust
// ConnectionAuthorizationServiceImpl::authorize; phase does not change policy.
tokio::time::timeout(CONTINUOUS_AUTHORIZATION_DEADLINE, self.authorize_current(binding))
    .await
    .map_err(|_| ContinuousAuthorizationError::Timeout)?
    .map_err(ContinuousAuthorizationError::from)
```
- [ ] **Step 4 — green。** `cargo test -p bcs-app-session --test group_session_connection`、本任务测试、`cargo test -p bcs-jwt --test group_session_jwt`、`cargo test -p bcs-api-http --test group_session_connection_routes`。
- [ ] **Step 5 — 提交。** `git commit -m "feat(bcs): bind session websocket views through application authority"`。

### Task 13: WS 入站/出站、Interaction replay、run fallback 与 SSE 持续授权

**Files:**
- Create: `crates/adapters/ws/bcs-ws/src/web/continuous_authorization.rs`
- Modify: `crates/adapters/ws/bcs-ws/src/web/{connection_registry,frontend_delivery,handler,control,replay}.rs`
- Modify: `crates/adapters/ws/bcs-ws/src/shared/run_channels.rs`
- Modify: `crates/service-api/bcs-service-api/src/port/delivery.rs`（受保护 fallback binding 传播）
- Modify: `crates/adapters/http/bcs-http/src/routes/{groups,collaboration_runs}.rs` 的实际受保护流入口
- Create: `crates/adapters/ws/bcs-ws/tests/manage_continuous_authorization.rs`
- Create: `crates/adapters/http/bcs-http/tests/manage_stream_authorization.rs`
- Create: `crates/bootstrap/bcs/src/bot_authority_metrics.rs`
- Modify: `crates/bootstrap/bcs/src/lib.rs`（本任务导出 metrics 模块，不等 Task 14）
- Create: `crates/bootstrap/bcs/tests/bot_authority_metrics.rs`
- Create: `scripts/e2e-test/bot_manage_load.py`
- Create: `scripts/tests/test_bot_manage_load.py`
- Modify: propagation 文档和受影响 `CONTEXT.md`

**Interfaces:** `WorkbenchConnectionRegistry` 和 RunChannelManager 接收 `Arc<dyn ContinuousAuthorizationService>`；订阅/注册受保护 Human 流时携带任务 12 binding。Bot-only 非委托 runtime 流维持其既有认证类型，不被伪装成人类管理连接。每个调用显式传 `ContinuousAuthorizationPhase`，不能从 payload 文本推断 phase。新增 `ObservedBotAuthorityCore`、`ObservedBotManagerService`、`ObservedContinuousAuthorizationService` decorator（均在 bootstrap 的 bot_authority_metrics.rs）分别实现已有 Core/管理/持续授权 trait，构造器分别为 `ObservedBotAuthorityCore::new(inner: Arc<dyn BotAuthorityCoreService>)`、`ObservedBotManagerService::new(inner: Arc<dyn BotManagerService>)`、`ObservedContinuousAuthorizationService::new(inner: Arc<dyn ContinuousAuthorizationService>)`，只转发并观察，不改变结果。管理 wrapper 覆盖输入校验和 self-revoke 等全部应用返回，避免只在 Core 计数而漏掉 invalid/denied。持续授权 wrapper 观察内层已执行的固定 deadline，不另造第二个 timeout。

待创建的 `bot_manage_load.py` 使用 Python unittest + asyncio 编写客户端测试脚本，复用 `group_session_ws_probe.py` 的连接依赖，不新增生产依赖。提供 `assess_budget(report: dict) -> list[str]` 返回全部预算失败项，CLI 非空则退出 1。`report` 包含 spec §12.2 的环境、基线/候选 commit、三次样本统计、负载参数及 fault-phase 断言；不输出认证凭据。

预算函数的固定输入是汇总对象 `mode`、`storage`、`runs`（必须3项），每轮包含 `auth_samples`、`auth_p99_ms`、`avg_db_statements`、`completion_ratio`；owner-candidate 另有 `throughput_ratio`、`e2e_p99_delta_ms`（由该轮客户端 received/offered/duration 与对应 baseline 计算）。managed-candidate 必须检查查询数；owner LegacyFull 只记录该数，不套精确 Bot 视角的 ≤4 限制。缺失/非数/NaN/Infinity、零样本、非3轮返回结构错误；owner-baseline 仅产生参照样本，不报告动态授权预算已达标。核心检查使用下面的阈值表，不写“合适的阈值”：

```python
# Within assess_budget(report), after validating the complete report schema.
errors = []
limit = {"sqlite": 25.0, "mysql": 50.0}[report["storage"]]
for index, run in enumerate(report["runs"], 1):
    checks = [("auth_p99_ms", run["auth_p99_ms"] <= limit),
              ("completion_ratio", run["completion_ratio"] >= 0.99)]
    if report["mode"] == "managed-candidate":
        checks.append(("avg_db_statements", run["avg_db_statements"] <= 4))
    if report["mode"] == "owner-candidate":
        checks.extend([("throughput_ratio", run["throughput_ratio"] >= 0.90),
                       ("e2e_p99_delta_ms", run["e2e_p99_delta_ms"] <= 50.0)])
    errors.extend(f"run{index}:{name}" for name, passed in checks if not passed)
return errors
```

`owner-baseline` 不进入上面的候选阈值循环；fault-phase 结果还必须全部通过，不能因正常负载数值达标忽略泄漏。采样/判定测试覆盖每个失败分支，并验证同一失败不会被其他轮的好值平均掩盖。

- [ ] **Step 1 — 写失败测试。** 假授权服务用 `AtomicBool` 切换 allow/deny，记录每次调用。连接已建立后撤权，下一入站 mutation / 出站帧 / replay / run fallback / SSE 均不得发送；store Err 同样阻断。X 视角撤权但 Y 仍有效时 X 仍失效，LegacyFull 可通过 Y 保留。

```rust
// Test recorder implements ContinuousAuthorizationService, returning this branch.
if !self.allowed.load(std::sync::atomic::Ordering::SeqCst) {
    return Err(ContinuousAuthorizationError::Application(
        ApplicationError::forbidden("session_access_revoked"),
    ));
}
Ok(())
```

该 recorder 的 `allowed: AtomicBool`、`calls: AtomicUsize` 在测试文件定义；authorize 每次先 `fetch_add(1, SeqCst)`。使用真实 mpsc 接收端断言 revoke 后没有受保护 payload，不只断言回调次数。增加 Tokio 可控时钟的 251 ms 延迟失败测试，确保 Timeout 失效 binding 且无 payload；遥测 recorder 验证 AC28 全部固定 outcome/phase，failed commit 不计 changed。为 assess_budget 写 P99=51 ms、完成率=98%、吞吐比=0.89 的负例及边界正例；缺失字段/空样本必须报错，不能默认 0 通过。

- [ ] **Step 2 — red。** `cargo test -p bcs-ws --test manage_continuous_authorization`；`cargo test -p bcs-http --test manage_stream_authorization`；`cargo test -p bcs --test bot_authority_metrics`；`python3 -m unittest discover -s scripts/tests -p test_bot_manage_load.py`。
- [ ] **Step 3 — 实现每次发送前的应用复核。** 从 registry 锁内取得连接快照后释放锁，await authorize，再按同一 conn_id 与取消状态发送；不把共享集合锁跨 DB await 持有。deny/Err 失效原连接及其 fallback，不能让“WS 没有成功投递”触发未经授权的 SSE 回退。

```text
snapshot exact connection + immutable authorization binding
→ authorize current persistent facts within 250 ms (no positive cache)
→ apply existing message visibility projection
→ verify same connection has not been cancelled/replaced
→ enqueue one protected frame
```

入站控制先复核再执行；replay 每批/每帧不得跨请求复用过期许可，按 spec 逐次授权基线实现。token keepalive、关闭通知等非受保护控制帧不要求暴露业务内容。传输已开始的帧不承诺撤回，拒绝后的帧不能再次排队。有界 pool/发送队列防止 timeout 后堆积无界查询任务；Query future 被取消时连接不得在语句未完成前复用。错误/过载关连接、恢复后重新授权，不建立陈旧 allow 降级通道。

按 spec §12.3 固定指标名/标签在 decorator 中记录；管理 mutation 仅返回 `Ok(changed=true)` 才计 changed，`Ok(changed=false)` 计 noop；Unauthenticated/Forbidden/ForbiddenCode 归 denied，InvalidInput/NotFound/Conflict 归 invalid，其他应用/存储故障归 error。持续授权 Forbidden/ForbiddenCode/NotFound 归 denied，其余应用故障归 error，超时使用独立 Timeout 变体；Core resolve_many 有允许/拒绝混合时计 mixed，controllable 空集计 denied、非空计 allowed。日志仅 request_id、action/outcome、可用 edge_id；帧失效日志按 binding 首次一次，敏感数据与高基数身份不进入标签。prometheus-metrics feature 关闭时保留普通结构化事件/测试 recorder，不要求私有遥测服务。
- [ ] **Step 4 — green。** 本任务测试加 `cargo test -p bcs-ws --test frontend_delivery_port`、`cargo test -p bcs-http`。运行 metrics 与预算判定单测；产出 spec §12.2 固定负载脚本与 JSON 报告结构，由 Task 15 对真实 SQLite/MySQL 双实例执行。P99/完成率/吞吐/查询数有明确失败断言，不以“已记录开销”代替达标，不以本机通知替代双实例正确性。
- [ ] **Step 5 — 提交。** `git commit -m "fix(bcs): reauthorize delegated streams before protected delivery"`。

### Task 14: Composition root 装配、完整路径注册与管理 API 正式挂载

**Files:**
- Modify: `crates/bootstrap/bcs/src/bot_authority_wiring.rs`（任务 2 创建）
- Modify: §1.2 的 server 拆分文件（R4 已独立提交结构拆分）
- Modify: `crates/bootstrap/bcs/src/{lib,http_adapter,eventing_wiring}.rs`
- Modify: `crates/bootstrap/bcs/src/bot_authority_metrics.rs`（Task 13 创建）
- Modify: `crates/service-api/bcs-services-container/src/lib.rs`
- Modify: `crates/adapters/http/bcs-api-http/src/v1/openapi/mod.rs`
- Create: `crates/bootstrap/bcs/tests/bot_manage_wiring.rs`
- Modify: 所有独立 test constructor / Noop / recording wiring、相关 `CONTEXT.md`

**Interfaces:** bootstrap 构造一个 `BotAuthorityCore`、一个 `BotAuthorityHookImpl`、一个管理应用实例和持续授权应用实例；同一 env、同一 repo Arc 注入所有 consumer；同时安装任务 13 的三个观测 decorator，共用既有 metrics recorder，不新增 exporter/配置。Memory 共享 `MemoryBotRepo` 的 authority/lifecycle 状态，SQL 则共享相同 DB plugin/pool 与五元组 schema。

- [ ] **Step 1 — 写装配失败测试。** 分别构造 Memory/SQLite 服务图，经管理应用授予，再通过 Bot/Group/Session/Invitation/WS 读取，不允许局部 Service 被装配成 Noop；revoke 后同一服务图全部拒绝。V1/legacy mine 同时变化。

```text
Alice POST /openapi/v1/collaboration/bots/bot-x/managers {"id":"bob"}
→ Bob V1 mine + legacy mine both contain X/manage
→ Bob Group/Session explicit X view succeeds
→ Bob session token + WS explicit X succeeds
→ Alice DELETE /openapi/v1/collaboration/bots/bot-x/managers?id=bob
→ every protected entry rejects X without restarting any service
```

- [ ] **Step 2 — red。** `cargo test -p bcs --test bot_manage_wiring`；先证明未挂载路由 404 / 缺少 authority 依赖导致断言失败。
- [ ] **Step 3 — 核对前序任务的集中装配并挂载管理路由。** 现有 build_v1_services、Memory 构造与 persistent 构造全部传 required Hook，不用生产 permissive default。所有 owner/manage consumer 接好后，才将 manager router merge 到 production collaboration router。

```rust
// In protected_router(), after Task 1..13 are green:
routes::bot::router().merge(routes::bot_manager::router())
```

既有其他 router merge 保留。Gateway 不改写 owner claims，既有 User/App route safety metadata随 bots.yaml 同步；管理 HTTP 无额外 env/body 权限字段。
- [ ] **Step 4 — green 与源文件门禁。** `cargo test -p bcs --test bot_manage_wiring`；`cargo check --workspace --all-targets`；核对 touched source files 全部 ≤1,000 行。追踪每个生产 legacy owner gate：授权扩展、Bot-only 保留、资源限制保留三类，不把 `/bots/status` / `/bots/{id}/chat-async` 的现有 Bot-token runtime admission 无条件放宽成人类管理入口。
- [ ] **Step 5 — 提交。** `git commit -m "feat(bcs): wire delegated bot authority across production entrypoints"`。

### Task 15: Live conformance、Singlebox、迁移回滚和消费方交接

**Files:**
- Create: `crates/services/bcs-edge-permission-store/tests/conformance_bot_authority_mysql.rs`
- Create: `crates/bootstrap/bcs/tests/bot_manage_multi_instance.rs`
- Modify: `scripts/e2e-test/bot_manage_load.py`、`scripts/tests/test_bot_manage_load.py`（Task 13 创建）
- Create: `scripts/e2e-test/bot_manage.sh`
- Modify: `scripts/e2e-test/{e2e,stories}.sh`、`scripts/e2e-test/group_session_ws_probe.py`
- Modify: `CHANGELOG.md`、propagation 文档、所有受影响 `CONTEXT.md`
- Create: `docs/superpowers/specs/2026-09-18-bot-manage-permission-release.md`
- Inspect/按新端点更新现有覆盖声明: `scripts/adapters_endpoint_coverage.py`、`scripts/cli_command_coverage.py`；没有新增 CLI 需求，不为了覆盖率发明 CLI 命令

**Interfaces:** 同一 repo conformance harness 在 Memory/SQLite/MySQL 上运行；MySQL driver 复用 `BCS_TEST_MYSQL_URL` 测试配置与 Text/Prepared 两种现有 statement protocol。两个服务实例必须使用独立 pool/registry，不能共享正向缓存或进程内锁。

- [ ] **Step 1 — 写失败用户故事。** 通过复数集合路径的真实 POST（JSON body id）/GET/DELETE（query id）授予/查看/撤销；缺参 DELETE 不影响既有管理者，通过真实 WS/SSE 验证生效。覆盖 28 个 AC；长 ID 使用 64 / 250 / 251 与中文字符，直接读审计确认未截断。数据库故障注入在独立测试 schema，禁止对用户工作库 DROP/删除数据。

```sql
SELECT request_kind, from_id, to_id, created_by, decided_by,
       CHAR_LENGTH(created_by) AS operator_length
FROM permission_requests
WHERE request_kind = 'manage' AND to_id = ? AND env = ?
ORDER BY id;
```

64 字符操作者对应 `operator_length=70`；250 字符对应 256；251 字符请求返回 400，边/审计计数均不增加。
- [ ] **Step 2 — red。** 在 tasks 未接通的基线或故意禁用 Hook 的测试装配上运行相关新故事，确认能捕获错误授权、推送泄漏或审计截断；保存失败断言，不把环境缺失当 red。
- [ ] **Step 3 — 完成测试驱动与发布 runbook。** MySQL 必须跑真实事务/锁/失败回滚，不只检查 SQL 构造。SQLite 使用临时磁盘文件+两个连接验证重启与并发。维护窗口演练：停全部旧写入→备份/预检→迁移→全部新实例→owner 回归→首次授予。回滚优先兼容二进制；完全旧版先隔离 manage 边/审计、恢复旧唯一键，加宽列默认保留，不截断。新 schema 下 profile/rules 可能产生相同旧四元组；恢复旧唯一键前发现冲突必须中止并保留报告，禁止任意删除 Rules/Profile 授权。
- [ ] **Step 4 — 运行完整 gates 并记录原始结果。** 另执行下述真实性能实验，不能用 mock/no-op authority 或测试代码绕过生产授权：

1. 在独立临时 schema 上分别启动稳定源码基线与候选 release 二进制；禁止向工作库 seed/delete。baseline 使用 owner + LegacyFull Session-bound WS，候选 owner 对照保持相同 scope/资源/数据；新 managed Bot 显式视角另跑绝对预算，不拿 baseline 不支持的显式 Bot connect 作无效对比。
2. 至少 8 vCPU/16 GiB、同机 DB、两实例各 50 连接；10 Session×10接收者×20源事件/秒×1 KiB = 2,000接收者投递/秒；MySQL 每实例 pool=8，SQLite 同一磁盘库两独立连接。预热60秒、稳定300秒，完整重复3次，每轮输出唯一 JSON（包含环境/commit/配置），保留未达标结果，不挑最好一次。
3. 每轮检查 SQLite/MySQL 授权 P99 ≤25/50ms、精确 Bot 视角平均 DB statements ≤4、投递完成率 ≥99%、owner相对吞吐 ≥90%、端到端P99增量 ≤50ms；用客户端实际收到的 payload 计投递，不以“入队成功”替代完成。
4. 延迟超过250ms/DB失败/另一实例撤权各跑独立 fault阶段，预期 fail closed 并关闭原 binding/fallback；故障样本与正常负载分开统计，但不能从总报告隐去故障。

负载/JSON CLI 在 Task 13 实现为 `--mode owner-baseline|owner-candidate|managed-candidate`、`--storage sqlite|mysql`、`--endpoints-file`、`--auth-file`、`--baseline-report`、`--output`；认证通过权限受限的临时 auth file，输出报告不含该文件内容。实际 smoke/load 启动用既有 Singlebox 配置，不加入硬编码 URL 或生产开关。每次 CLI 执行一组3轮，`--output` 保存汇总且派生 `.run1.json` / `.run2.json` / `.run3.json` 保存各轮原始计数/分位数；`--baseline-report` 是相同 storage 的 owner-baseline 汇总，候选 owner 按轮配对计算相对值。完成三种 mode、两种 storage 后，`assess_budget` 任一预算失败都阻断发布，不能删除逐帧校验“修复”性能。

记录 spec §12.3 的 mutation/authority/continuous 指标和日志隐私断言，metrics unavailable 测试确认不会 fail-open、回滚成功审计或反转业务结果。


```bash
# src/bcs/
cargo test -p bcs-service-api -p bcs-edge-permission -p bcs-edge-permission-store
cargo test -p bcs-app-bot -p bcs-app-group -p bcs-app-session -p bcs-app-invitation
cargo test -p bcs-bot -p bcs-bot-store -p bcs-group -p bcs-session -p bcs-eventing
cargo test -p bcs-api-http -p bcs-http -p bcs-ws -p bcs-jwt
cargo test -p bcs-edge-permission-store --test conformance_bot_authority_mysql -- --ignored
cargo test -p bcs --test bot_manage_multi_instance
cargo test -p bcs --features prometheus-metrics --test bot_authority_metrics
python3 -m unittest discover -s scripts/tests -p test_bot_manage_load.py
bash scripts/ci/arch-check.sh
bash scripts/ci/check-conformance-entries.sh
python3 scripts/validate_openapi_contract.py
cargo test --workspace
# still in src/bcs; invoke repository-root scripts by relative path
../../scripts/ci/singlebox_coverage.sh
python3 ../../scripts/ci/verify_singlebox_coverage_artifacts.py \
  --reports-dir ../../scripts/.dependencies/coverage/singlebox/reports
```

MySQL live command要求执行环境已安全提供 `BCS_TEST_MYSQL_URL`，不把密码写入文档/日志。新 test 标记 ignored 并用上面的显式命令运行；缺配置时应失败而不是 skip 后报告通过。保留 Singlebox line ≥40%、method ≥36%、HTTP/CLI coverage 100% 基线；不用降低阈值使新 endpoint 通过。

- [ ] **Step 5 — 消费方交接、提交与评审。** Frontend 独立变更入口：仓库根 `src/frontend-nextgen/src/services/backendApi/collaboration/collaborationBotController.ts`、`services/workspace/{identityService,groupService,groupChatProvider}.ts`、`services/collaborationPrivacy/mappers.ts`、`assets/TaskPanel/GroupDrillDown.tsx`（省略部分均相对于 frontend-nextgen/src）。交付必需标签、完整分页身份集合、显式视角、撤权 403 / WS close 后刷新，禁止使用后缀/created_by 过滤 manager。BCS 测试不宣称 UI 已完成；前端未接入前发布说明明确这一限制。

提交本任务文档/测试：`git commit -m "test(bcs): verify delegated authority lifecycle and revocation end to end"`。实现 PR 描述填 Problem/Solution/Validation/Compatibility and risk/Spec；未运行的 MySQL、双实例、E2E 项逐项写原因，不宣布功能验收完成。执行完后按 requesting-code-review / verification-before-completion 流程复核，是否推送由用户决定。

## 3. Spec 覆盖与验收索引

| Spec / AC | 负责任务 | 验收证据 |
| --- | --- | --- |
| §1–5；AC01、AC02、AC10、AC11 | 1、2、5、6、7 | Domain、Hook、Human-only、mine 标签与否定身份测试 |
| §6.1、§7；AC12、AC13、AC26 | 1、3、5、15 | 五元组迁移、跨 kind 同 ref 共存、friend/manage 独立、regrant、回滚冲突 |
| §2.1；AC25 | 3、5、15 | 再授权非级联、owner 先读名单快照再逐项撤销及重新核对 |
| §6.2–6.3；AC09、AC14、AC18、AC20、AC21 | 3、4、15 | SQL/Memory 原子性、双连接 grant/delete、审计隔离、重启 |
| §6.4；AC24 | 1、5、15 | 输入边界、审计列迁移、MySQL 长 Unicode ID round-trip |
| §7 | 5、14、15 | 同一复数集合路径的 GET/POST/DELETE、id 的 body/query 输入及响应投影约定、错误优先级与正式装配 |
| §8；AC03 | 6 | 并集统一过滤/排序/total/分页、legacy 标签 |
| §9.1–9.3；AC04、AC05、AC06、AC07、AC08、AC19 | 8、9、10、11 | 各资源 owner/manager 成对测试，strict 与 legacy 分离 |
| §9.4；AC22 | 4 | 普通 Bot 允许；无绑定 TC 与 Provider Bot 对两者均拒绝 |
| §10；AC17 | 7、11、14 | 异步 mixed selector、eventing 下游与真实 operator |
| §11 | 2、3、14、15 | 层次、Hook 调用记录、Noop、装配、conformance |
| §12；AC15、AC16、AC23 | 12、13、15 | 显式 Bot connect、旧 token、入站/出站/replay/fallback/SSE、双实例 |
| §12.2–12.3；AC27、AC28 | 12、13、14、15 | 固定负载预算、250ms deadline、遥测低基数/隐私和故障语义 |
| §13 | 6–14 | propagation 表覆盖 V1 + legacy 入口，不扩展 Provider/Engine 权限 |
| §14–16 | 1–15 | OpenAPI、Service/Repo 编译传播、迁移/回滚、全 gates 与文件行数 |
| §17 | 执行前门禁 | 第 2 节产品假设确认记录；本计划不代替确认 |

## 4. 联合评审修订记录与执行交接

| 意见 | 本轮落点 |
| --- | --- |
| M1 基线 | 稳定源码基线 `03d4b7875b`，文档 amend 历史单列，不 checkout 旧 docs commit |
| M2 提交范围 | §1.2–1.3 的 R1–R5 独立纯移动提交；Task 1/2 明确依赖，后续超限拆分同样分离 |
| S1 再授权清理 | spec §2.1 非目标与风险；Task 3/5/15、AC25 |
| S2 性能 | spec §12.2 拟定预算；Task 12 deadline、13测量脚本、15 live验收、AC27 |
| S3 跨 kind | spec §6.1 实证边界；Task 1 conformance / 15旧索引回滚冲突、AC26 |
| 次要 1–5 | token 详情授权措辞、TC完整后缀测试、ViewActor类型、观测decorator/AC28、ExplicitManagerPage排序注释 |

以下是执行前/执行后门禁，不代表本轮已完成业务实现：

- [ ] 执行者确认产品假设、实现授权及当前基线；若产品假设改变，先改 spec 和对应任务，不边实现边猜。
- [ ] 每个新增 trait 有集中 conformance harness，真实实现 driver 调用该 harness；Memory 与 SQL 均非 Noop。
- [ ] 每个入口在 propagation 文档有具体函数及测试，所有源文件 ≤1,000 行；没有边界 waiver 或新的私有服务依赖。
- [ ] 实现验收记录与本次文档验证分离；计划中的所有实现步骤当前均未执行。

本轮交付仅包含修订 spec 与本 plan。之后可选择：**逐任务代理执行并评审**，或 **使用 executing-plans 在当前会话内按批次执行**；未选择前不启动实现，也不自动提交/推送当前文档。
