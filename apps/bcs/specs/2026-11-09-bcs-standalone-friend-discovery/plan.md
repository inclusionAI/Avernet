# BCS 独立发现与好友关系 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 BCS 已有 v1 `candidates`、`candidates/search` 和 `friend-connections` 在已认证的 Human/Bot 视角下独立提供可见性正确、好友关系一致的发现、搜索及审批能力。

**Architecture:** 在 `bcs-service-api` 的查询输入中明确视角，将现有内存/SQL 控制面筛选移至分页前；v1 Bot 应用服务复用 Connect/edge，而旧版候选搜索调用方保持原行为。两个现有 HTTP 路由消费同一应用能力，`eligible-candidates` 只保留待废弃兼容路径；不新增发现路由，也不使 HTTP adapter 读取存储或判定业务策略。

**Tech Stack:** Rust、Axum、Tokio、BCS 应用／Core Service API、内存与 SQL store、v1 OpenAPI YAML、Cargo／pytest 契约测试。

**共同设计：** `docs/specs/2026-11-09-bcs-standalone-friend-discovery/design.md`。本文件只负责 BCS；`apps/frontend-nextgen/specs/2026-11-09-bcs-standalone-friend-discovery/plan.md` 独立负责调用方、UI 和部署。先使本计划的 v1 契约与测试可用，再接前端。

---

## 开工约束与文件职责

- 先读 `apps/bcs/AGENTS.md`、`apps/bcs/CLAUDE.md`、相关 crate 的 `CONTEXT.md` 和上述设计。**禁止全局 `cargo fmt`**；仅格式化自己改动的局部文件，避免无关 diff。提交前检查每个修改的源文件不超过 1,000 行；`crates/application/v1/bcs-app-bot/src/lib.rs` 已约 874 行，新的搜索编排放到该 crate 的独立模块。
- `crates/service-api/bcs-service-api/src/types/bot_control_plane.rs`：候选查询的视角字段；`crates/service-api/bcs-service-api/Cargo.toml`、`Cargo.lock` 和 `crates/service-api/bcs-service-api/CONTEXT.md`：Rust Service API 版本、锁定记录与传播范围。`src/core/candidate_search.rs`：现有共享 Core 的新增 v1 安全入口及输入；旧调用方继续用原入口。
- `crates/services/bcs-bot-store/src/memory_control_plane.rs`、`crates/services/bcs-bot-store/src/persistent_repo/control_plane.rs` 及新增 `candidate_friend_set.rs`：分页前 Human/Bot 可见性筛选，以及 SQLite/MySQL 参数化 JSON 好友集合；既有环境、排序、状态语义不变。`crates/services/bcs-bot/src/core/candidate_search_core.rs` 及其新的局部模块：复用 worker 推荐与控制面查询，返回经过同一权威可见性筛选的 v1 结果。
- `crates/services/bcs-fusion/src/core/fuse_backed_worker_profiles.rs`、`crates/external-clients/bcs-fuse-client/src/types.rs`：将类型化召回范围翻译为已有 Fuse filters 协议；legacy 不发送 filters。先核对 `apps/bcsfuse/src/interfaces/api/schemas/recommend_schemas.py` 的 `topK<=20` 与推荐默认 availability 过滤，本轮不修改 BCSFuse 服务。
- `crates/application/v1/bcs-app-bot/src/lib.rs`、新增 `src/candidate_friends.rs` 和 `src/candidate_search.rs`：验证所选角色、通过 Connect 的严格分页入口收集已批准 Bot 好友边、调用 Core；不要把真实 `created_by` 当管理权限。禁止在本轮新路径调用会吞错且逐好友查 profile 的 `list_friends`。
- `crates/adapters/http/bcs-api-http/src/v1/openapi/routes/bot.rs`、`crates/adapters/http/bcs-api-http/src/v1/internal/routes/bot.rs`：现有 handler／兼容别名；`api-contracts/v1/openapi/bots.yaml`、`api-contracts/v1/internal.yaml` 和 `api-contracts/README.md`：权威 wire 契约与待废弃标志。`crates/bootstrap/bcs/src/server/resource_builders.rs`、`crates/bootstrap/bcs/src/server/use_case_wiring.rs`、`crates/bootstrap/bcs/src/server/state.rs`：装配同一 Core 及其控制面端口，不让 adapter 选实现。
- 测试就近放在 `crates/services/bcs-bot-store/tests/control_plane_parts/`、`crates/services/bcs-bot/tests/candidate_search_core.rs`、`crates/application/v1/bcs-app-bot/tests/v1_bot_service/` 和 `crates/adapters/http/bcs-api-http/tests/bot_routes/`；在现有 harness 中补例，不新建通用测试框架。

### Task 1：候选数据层在分页之前按 Human/Bot 视角过滤

**Files:** Modify `crates/service-api/bcs-service-api/src/types/bot_control_plane.rs`; Modify `crates/service-api/bcs-service-api/Cargo.toml`, `Cargo.lock`, `crates/service-api/bcs-service-api/CONTEXT.md`; `crates/services/bcs-bot-store/src/memory_control_plane.rs`; `crates/services/bcs-bot-store/src/persistent_repo/control_plane.rs`; `crates/services/bcs-bot-store/tests/control_plane_parts/cases_1.rs`; `crates/services/bcs-bot-store/tests/conformance_bot_control_plane_repo.rs`; Create `crates/services/bcs-bot-store/src/persistent_repo/candidate_friend_set.rs` 并在 `crates/services/bcs-bot-store/src/persistent_repo/mod.rs` 声明；Create `crates/services/bcs-bot-store/tests/support/candidate_friend_set_contract.rs`（由 SQLite 契约入口和 MySQL 门禁共用）；Modify `crates/tools/bcs-admin/src/migrate_mysql_chain_tests.rs` 挂载该用例；update all `BotCandidateReadQuery` and `BotSearchCandidateQuery` literals found by `rg`.

- [ ] **Step 1: 写失败的 Memory+SQLite 契约测试。** 复用 `cases_1.rs` 现有 `seed_bot` 与持久化 fixture，在同环境加入 A：`visibility=private,user_visibility=public`、B：`visibility=public,user_visibility=private`、C：两侧 protected、D：已有批准 edge 的 private Bot；分别执行 `purpose=Discovery` 的 Human/Bot 查询，`offset=0,limit=1` 及下一页，断言 Human 命中 A 不命中 B，Bot 命中 B 不命中 A，`total` 是**过滤之后、分页之前**的值；`purpose=Collaboration` 还需断言各自公开项及私有好友项。沿用该测试现有 Memory/SQLite 双存储实例，不增测试用 SQL 后门。共享容量用例构造 0、100、500、501、1,000 个好友，同时调用普通列表和搜索，候选 limit 分别为 1 和 100；断言后分页 total、不同 offset 的连续排序及 private 好友可见性。补空集、重复来源去重、引号／反斜杠及最长合法 ID 的绑定测试，禁止通过截断好友集规避 SQL 限制。SQLite 先运行，新旧实现对 501/1,000 的差异必须有真实 SQL 证据；同一用例接入真实 MySQL 完整迁移链门禁，参照现有 `avatar_store_contract` 的挂载方式，共享用例只依赖传入的 repo／DbPlugin 和方言，不依赖 SQLite 专属 fixture。

```rust
let human_page = repo.list_control_plane_candidates(BotCandidateReadQuery {
    acting_bot_id: "human_staff-1".into(), env: env.clone(),
    visibility: BotCandidateVisibility::Discovery,
    viewer_kind: ActorKind::Human,
    friend_ids: HashSet::new(), name: None, offset: 0, limit: 1,
}).await.expect("Human 候选查询");
assert!(human_page.0.iter().all(|row| row.bot.user_visibility != UserVisibility::Private));
assert_eq!(human_page.0.len(), 1);
```

- [ ] **Step 2: 执行红灯。** Run: `cd apps/bcs && cargo test -p bcs-bot-store --test conformance_bot_control_plane_repo -- --nocapture`。Expected: 新的 `viewer_kind` 字段不存在或 Human 可见性断言失败；不要先更新实现使测试变绿。
- [ ] **Step 3: 最小实现并更新所有构造位置。** 在 `BotCandidateReadQuery` 加必需的 `viewer_kind: ActorKind`；在旧版 `BotSearchCandidateQuery` 加 `viewer_kind: Option<ActorKind>` 和 `name_only: bool`；旧调用方设 `None/false` 保持现有筛选语义，v1 Human 设置 `Some(Human)`，v1 名称回退设置 `name_only=true`。这很重要：现有控制面 `q` 默认还匹配摘要／Bot ID，而现有候选名称回退只匹配 Bot 名称，不能无意扩大回退语义。Memory/SQL 的 `list_control_plane_candidates` 对 `viewer_kind=Human` 选 `user_visibility`，否则选 `visibility`；协作时分别采用对应维度 `public OR approved_friend`。`search_control_plane_candidates` 的 Human 分支也支持该维度；`name_only=true` 时只匹配 Bot 名称（Memory/SQL 均在分页前完成），其它查询保持旧行为。由于对公开 Rust 结构体增加必填字段属于源码级不兼容的 Service API 变更，`bcs-service-api` 从 `3.1.0` 升到 `4.0.0` 并更新 `Cargo.lock`；在 `CONTEXT.md` 记录全工作区调用点重编译／兼容性范围，不改无关 Plugin API 版本。SQL page 和 COUNT 必须使用同一组条件及绑定参数，不允许把分页结果在应用层过滤后伪造总数。

同一步修复普通列表和搜索当前的 `friend_rows` 构造：在新增 `candidate_friend_set.rs` 中将排序去重后的好友 ID 序列化为单个 JSON 字符串绑定参数，由已有 `DbSqlFlavor` 选择展开 SQL。MySQL 列宽对齐现有 `bcs_bots.bot_uuid VARCHAR(256)`，身份比较规则与现有契约一致；序列化／执行／解码错误传播，不回退旧 UNION 链或静默截断。两个查询共用该局部 helper，page/COUNT 使用相同 CTE 与参数。无需修改已提交迁移，也不允许拆成多个各自 LIMIT 的候选查询后合并。

```sql
-- SQLite：friend_uuids 对空数组自然返回零行。
WITH friend_uuids AS (SELECT value AS bot_uuid FROM json_each(?))
-- MySQL：同一个绑定 JSON 数组；后续 JOIN、过滤、排序及分页仍在全局执行。
WITH friend_uuids AS (
  SELECT bot_uuid FROM JSON_TABLE(?, '$[*]'
    COLUMNS(bot_uuid VARCHAR(256) PATH '$' ERROR ON ERROR)) AS friends
)
```

JSON 展开复用 `src/a2a_source.rs` 已使用的 SQLite/MySQL 能力。只减少 SQL 语句项和占位符数量，不宣称集合构造或数据库扫描成本为常数；记录 1,000 个好友的 JSON 大小与两条候选 SQL 的读取成本。

```rust
// 查询语义，而非 HTTP 侧过滤：
let visible = match (query.viewer_kind, query.visibility) {
    (ActorKind::Human, BotCandidateVisibility::Discovery) =>
        matches!(bot.user_visibility, UserVisibility::Public | UserVisibility::Protected),
    (ActorKind::Human, BotCandidateVisibility::Collaboration) =>
        bot.user_visibility == UserVisibility::Public || is_friend,
    (_, BotCandidateVisibility::Discovery) =>
        matches!(bot.visibility.as_str(), "public" | "protected"),
    (_, BotCandidateVisibility::Collaboration) => bot.visibility == "public" || is_friend,
};
```

- [ ] **Step 4: 绿灯与全仓类型检查。** Run: `cd apps/bcs && cargo test -p bcs-bot-store --test conformance_bot_control_plane_repo && cargo check --workspace`。Expected: 现有及新增契约测试通过、无缺少字段的结构体字面量；再跑 `rg -n '(BotCandidateReadQuery|BotSearchCandidateQuery) \{' crates` 核对其它使用者已明确 `viewer_kind`。断言 0/100/500/501/1,000 好友时，每次普通列表或搜索的候选存储阶段仍为 page+COUNT 两次 SQL，好友集只占一个绑定参数，查询不含随好友数增长的 UNION 链；不要误认为候选 `limit<=100` 能约束好友集合大小。另在设置 `BCS_TEST_MYSQL_URL` 指向空的临时数据库后运行 `cargo test -p bcs-admin full_mysql_migration_chain_applies_and_preserves_history -- --ignored --nocapture`，验证同一容量用例；未配置 MySQL 时明确记录未验证，不能用生成 SQL 字符串或 SQLite 通过代替。Task 2/4 再核对包含好友读取和投影的整条链路成本。
- [ ] **Step 5: 局部提交。** `git add apps/bcs/crates/service-api apps/bcs/crates/services apps/bcs/crates/tools/bcs-admin/src/migrate_mysql_chain_tests.rs apps/bcs/Cargo.lock && git commit -m 'feat(bcs): filter candidates by authorized viewer visibility'`。

### Task 2：严格、分页读取 Connect/edge，正式列表与兼容别名共用

**Files:** Modify `crates/application/v1/bcs-app-bot/src/lib.rs`、`crates/application/v1/bcs-app-bot/Cargo.toml`（仅增加真实 store 测试所需的 `bcs-edge-permission-store` dev-dependency）、`crates/application/v1/bcs-app-bot/CONTEXT.md`; Create `crates/application/v1/bcs-app-bot/src/candidate_friends.rs`; Modify `crates/application/v1/bcs-app-bot/tests/v1_bot_service.rs` 中相关 Connect fixture 和 `tests/v1_bot_service/candidates_and_projection.rs`; Create `crates/application/v1/bcs-app-bot/tests/candidate_friend_reads.rs`（真实 `DbConnectService` + `DbEdgeGrantStore`，复用现有 SQLite fixture/计数 DbPlugin 模式）；Modify `crates/adapters/http/bcs-api-http/src/v1/openapi/routes/bot.rs`、`tests/bot_routes/read.rs`。不修改旧 `list_friends` 签名，不把不相关 legacy 调用方纳入本轮迁移。

- [ ] **Step 1: 写失败测试。** 正式 `list_candidates` 和 `list_eligible_candidates` 的 fixture 只通过 `list_friends_paginated` 返回 edge 好友；将旧 `list_friends` 调用设为测试失败，证明未走旧存储路径。保留旧 `FriendCoreService` 独有好友不算 approved edge、未管理 Bot／非本人 Human 返回 403 的用例，且未授权请求不能开始读取好友。

真实 store 测试种入 Human/Bot、approved/pending/revoked edge、active/inactive default profile、其它环境和非 Bot 对端。通过实际 `DbConnectService` 调用应用列表，再用 DbPlugin 包装器记录 SQL 并在好友集合 JOIN 查询（包括 permission_profiles 访问）及后续页注入错误；另注入畸形行验证解码失败。断言整次请求 Err、没有成功的空／部分好友集，也没有逐目标 `get_default_profile_id`。仅让 Connect stub 返回 Err 不能替代此证据。

成本用例分别使用 F=0、1、100、101、501、1,000 个 Bot 好友；静态集合的好友读取 SQL 次数应为 `max(1, ceil(F/100))`，不随每个好友增加一次 profile SELECT。记录候选查询与投影成本，不把它们误计成好友 SQL。对连续三次候选页请求检查三次完整读取的总成本，不引入跨请求缓存来掩盖开销。增加 total 改变、缺页、重复 ID 的 fixture，断言失败而非继续扫描或交付部分集。

- [ ] **Step 2: 红灯。** 分别运行 `cd apps/bcs && cargo test -p bcs-app-bot --test v1_bot_service eligible_candidates_use_edge_permission -- --nocapture` 和 `cargo test -p bcs-app-bot --test candidate_friend_reads -- --nocapture`。Expected: 现有路径仍调用旧 `list_friends`，或数据库错误／查询数断言失败；不要把缺测试依赖、fixture 初始化错误当作有效红灯。
- [ ] **Step 3: 最小实现。** 在 `candidate_friends.rs` 为 `BotServiceImpl` 实现 `pub(crate) async fn approved_bot_friend_ids(&self, actor_id: &str) -> Result<HashSet<String>, ApplicationError>`，只调用已有 fallible 的 `ConnectService::list_friends_paginated`。该 SQL 实现已经通过集合 JOIN 联合 edge 与 default profile，并每页一起返回 COUNT；不能退回旧无错误通道的仓储方法。列表先完成 `authorize_candidate_perspective`，再调用 helper；搜索在 Task 4 使用同一个 helper。分页参数、终止条件和失败策略如下：

```rust
use bcs_service_api::application::connect::FriendListQuery;
// BotServiceImpl::approved_bot_friend_ids 方法体；返回 ApplicationError。
let mut ids = HashSet::new();
let mut offset = 0_u64;
let mut first_total = None;
loop {
    let page = self.connect_service.list_friends_paginated(actor_id, FriendListQuery {
        target_type: Some(ActorKind::Bot), offset, limit: 100,
    }).await.map_err(map_service_error)?;
    let total = *first_total.get_or_insert(page.total);
    let expected = total.saturating_sub(offset).min(100);
    if page.total != total || page.items.len() as u64 != expected {
        return Err(ApplicationError::internal("friend page changed; retry the request"));
    }
    for entry in page.items {
        if entry.kind != ActorKind::Bot || entry.actor_id.is_empty()
            || !ids.insert(entry.actor_id) {
            return Err(ApplicationError::internal("invalid or changed friend page"));
        }
    }
    offset += expected;
    if offset >= total { return Ok(ids); }
}
```

首批 total 固定本次最多读取的页数，后续漂移立即失败，用户重试开启新读取；不自动无界重试，不宣称跨页快照隔离。F 个好友仍需 O(F) 返回行／内存，现有分页 JOIN/COUNT 可能重复扫描同一集合，需在成本记录中说明，不能仅以查询次数证明大规模性能。查询期间不持有跨网络调用的事务／锁。

`list_candidates` 将 helper 返回集传给原有 `list_candidates_with_friend_ids`，构造 `BotCandidateReadQuery` 时使用已授权 `acting.kind`。`list_eligible_candidates` 直接委托 `self.list_candidates(command).await`。`lib.rs` 接近 1,000 行时拆出 `candidate_listing.rs`，不要将扫描循环塞入 handler 或 Core。

```rust
let acting = self.authorize_candidate_perspective(&staff_no, &command.bot_id).await?;
let friend_ids = self.approved_bot_friend_ids(&command.bot_id).await?;
self.list_candidates_with_friend_ids(command, acting, friend_ids).await
```

- [ ] **Step 4: 绿灯与路由兼容测试。** Run: `cd apps/bcs && cargo test -p bcs-app-bot --test candidate_friend_reads && cargo test -p bcs-app-bot --test v1_bot_service && cargo test -p bcs-api-http --test bot_routes`。Expected: 两列表 DTO 相同、真实 SQL 错误传播、千级好友无逐条 profile SELECT、未经授权的视角均拒绝。Task 4 增加搜索入口后复跑同一个真实 store 测试文件。
- [ ] **Step 5: 局部提交。** `git add apps/bcs/crates/application/v1/bcs-app-bot apps/bcs/crates/adapters/http/bcs-api-http apps/bcs/Cargo.lock && git commit -m 'feat(bcs): read approved friend edges through strict bounded pages'`。

### Task 3：现有候选智能搜索在 v1 视角下复用 Core，BCSFuse 缺席时按名称回退

**Files:** Modify `crates/service-api/bcs-service-api/src/core/candidate_search.rs` 及其 re-export（由 `rg` 定位）；`crates/service-api/bcs-service-api/CONTEXT.md`; `crates/services/bcs-fusion/src/core/fuse_backed_worker_profiles.rs`、`crates/services/bcs-fusion/CONTEXT.md`; `crates/external-clients/bcs-fuse-client/src/types.rs`、`crates/external-clients/bcs-fuse-client/CONTEXT.md`; `crates/services/bcs-bot/src/core/candidate_search_core.rs`; Create `crates/services/bcs-bot/src/core/candidate_search_authorized.rs`（在 `candidate_search_core.rs` 中使用显式 `#[path = "candidate_search_authorized.rs"] mod authorized;` 声明）； Modify `crates/bootstrap/bcs/src/server/resource_builders.rs`、`crates/bootstrap/bcs/src/server/use_case_wiring.rs`、`crates/bootstrap/bcs/src/server/state.rs`; Test `crates/services/bcs-bot/tests/candidate_search_core.rs`、`crates/application/v1/bcs-app-bot/tests/v1_bot_service/candidate_search.rs`、`crates/service-api/bcs-service-api/tests/candidate_search_core_contract.rs`、`crates/services/bcs-fusion/tests/conformance_fuse_worker_profile_service.rs`；Create `crates/services/bcs-bot/tests/authorized_candidate_registry_reads.rs`（复用现有 Memory/SQLite store；测试初始化参照 `tests/bot_profile.rs`，计数包装参照 `crates/services/bcs-bot-store/tests/bot_avatar_queries.rs`，不新建通用框架）。更新 `rg` 找到的所有 `WorkerRecommendCommand` / `RecommendWorkersRequest` 构造与解构位置。

- [ ] **Step 1: 定义先失败的 Core 行为测试。** 在 `candidate_search_core.rs` 现有 fixture 中种入 Human-only（`visibility=private,user_visibility=public`）与 Bot-only（反向）Bot，注入 worker 推荐排名，并以 `viewer_kind=Human` + edge 好友集调用新增 v1 Core 入口；断言 Human-only 命中、Bot-only 不泄露，排序保持推荐名次，缺少 BCSFuse 时名称回退可搜到 Human-only Bot，`name_fallback` 不含 score。注入失败的控制面 stub，断言可见性校验出错时返回 Err，不会把推荐结果当作可见 Bot 返回；旧 `search_candidates_for_legacy` 用例继续通过。

同时补两组不能只用 Core stub 替代的测试：

1. 扩展现有 `conformance_fuse_worker_profile_service` 的本地 HTTP fixture，运行真实 `FuseWorkerProfileService` 与 `FuseClient` 并捕获出站 JSON：Human discovery 及 Human/Bot collaboration 的 `filters.availability` 必须含 private，Bot discovery／legacy 仍省略 filters；`topK<=20`，不发送未知字段。在 Core 测试中另断言视角到 scope 的选择；组合覆盖“Human-only 与其它可见语义命中并存”，防止以名称回退掩盖召回缺失。语义授权子集不足 20 条不补召回，全部被过滤才名称回退；推荐顺序与创建时间相反时仍保持推荐顺序。
2. 新建严格 registry 回填测试，真实冷缓存 store 下分别读 1、20 个存在的 Bot，断言该回填阶段走 `try_get_many` 的单批 SQL 而非逐 ID SELECT，并单独记录控制面查询／投影的其它成本。注入 registry 数据库故障，搜索必须 Err，不返回成功空结果或触发名称回退；另测真实缺失 ID 可以跳过。单纯断言一次名为 `get_by_ids` 的方法调用不构成批量读证据。

```rust
// 与本次 bcs-service-api 4.0.0 变更一起传播，所有构造点显式选择。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkerRecommendationScope {
    LegacyDefault,
    AllVisibilities,
}
// 在已有 WorkerRecommendCommand 中增加必需字段：
// pub scope: WorkerRecommendationScope

pub struct AuthorizedCandidateSearchQuery {
    pub query: String,
    pub acting_actor_id: String,
    pub env: String,
    pub viewer_kind: ActorKind,
    pub visibility: BotCandidateVisibility,
    pub friend_ids: HashSet<String>,
    pub limit: usize,
}
// 在 BotCandidateSearchCoreService trait 中提供明确失败的默认实现，
// 现有测试替身及 legacy 实现因此保持可编译；生产 Core 必须覆盖：
async fn search_authorized_candidates(
    &self, query: AuthorizedCandidateSearchQuery,
) -> ServiceResult<BotCandidateSearchCoreResult> {
    let _ = query;
    Err(ServiceError::InternalError("authorized candidate search is not configured".into()))
}
```

- [ ] **Step 2: 红灯。** Run: `cd apps/bcs && cargo test -p bcs-fusion --test conformance_fuse_worker_profile_service && cargo test -p bcs-bot --test candidate_search_core -- authorized --nocapture`；再单独运行 `cargo test -p bcs-bot --test authorized_candidate_registry_reads`。Expected: 新入口／召回范围字段缺失，或出站 filters、严格批量读取断言失败；分别记录失败原因，不把 fixture 自身错误当成红灯。
- [ ] **Step 3: 最小 Core 实现。** 复用当前 Core 的 `WorkerProfileCoreService::recommend_workers`、批量标签读取和推荐顺序，但先补齐其召回范围契约。`WorkerRecommendCommand.scope` 必填；legacy 调用显式 `LegacyDefault`，v1 Human 或 collaboration 为 `AllVisibilities`，Bot discovery 为 `LegacyDefault`。`RecommendWorkersRequest` 增加 `filters: Option<RecommendWorkerFilters>`（`None` 的合法含义是保留 provider 默认行为，序列化时省略字段）；`RecommendWorkerFilters` 只声明 `availability: Vec<String>`，由 `FuseWorkerProfileService` 将枚举映射为受控的三值数组，不把原始 JSON 或外部 HTTP 类型引入 Core。所有旧构造／解构位置更新并保留原出站 JSON；同 Task 1 的 4.0.0 版本传播一起交付，不改无关 Plugin API。新入口在 Core 侧使用注入的 `BotControlPlaneCoreService::search_candidates`：语义分支一次请求 `topK=query.limit.min(20)`，对 provider 返回的最多 20 个 UUID 去重后批量校验权威可见性；控制面查询的 limit 覆盖整批 UUID，不用较小的展示 limit 按创建时间预截断。按 provider 原顺序遍历授权集合，再限制展示数量；这是有界召回的授权子集，不保证全目录可见 top-20，不发第二次推荐补页。无推荐、provider 失败或权威筛选后无可用命中时按相同视角以名称查询控制面，不能复用旧 registry 的 Bot-only 回退。两分支用已有 `registry.try_get_many(&ids).await?` 严格批量回填 `RegisteredBot`，保留推荐次序及 `friend_ids` 标记；禁止换回逐条且吞错的 `get_by_ids`。缺少控制面 wiring、控制面查询或 registry 读取失败必须 Err，不得捕获后返回空列表或名称回退；真正不存在的 ID 才可跳过。旧版 `search_candidates` / `search_candidates_for_legacy` 不改策略；给 `BotCandidateSearchCore` 增加 `control_plane: Option<Arc<dyn BotControlPlaneCoreService>>` 和 `with_control_plane(...) -> Self`，只在 BCS composition root 装配 v1 安全入口，所有现有 `build_candidate_search_bindings(...)` 调用点同步更新。

```rust
// scope 是 Core 语义；出站 filters 的构造留在 FuseWorkerProfileService。
let scope = if query.viewer_kind == ActorKind::Human
    || query.visibility == BotCandidateVisibility::Collaboration {
    WorkerRecommendationScope::AllVisibilities
} else {
    WorkerRecommendationScope::LegacyDefault
};
// recommend_workers(WorkerRecommendCommand { query: query.query.clone(),
//     top_k: query.limit.min(20) as u32, min_score: self.recommend_min_score, scope })
// provider Err 可回退；控制面/registry Err 必须传播，不放进同一个 fallback catch。
// recommended_ids 已按 provider 顺序去重且不超过 20 个；空集直接走名称回退。
let validation_limit = recommended_ids.len() as u64;
let candidates = self.control_plane.as_ref()
    .ok_or_else(|| ServiceError::InternalError("candidate control plane is not configured".into()))?
    .search_candidates(BotSearchCandidateQuery {
        acting_bot_id: query.acting_actor_id.clone(), env: query.env.clone(),
        viewer_kind: Some(query.viewer_kind), friend_ids: query.friend_ids.clone(),
        bot_uuids: Some(recommended_ids), name: None, q: None,
        visibility_filter: None, user_visibility: None, status: None,
        friendship: None, tc_bot: None, offset: 0, limit: validation_limit,
        visibility: query.visibility, name_only: false,
    }).await?;
// 从 candidates.0 的 item.bot.record.bot_id 构造允许集合；按 provider 原顺序取允许的 ids。
// registry 回填使用：let registered = self.registry.try_get_many(&ids).await?;
// 回退分支使用 bot_uuids=None, name=Some(query.query.clone()), q=None,
// name_only=true, limit=query.limit.min(20) as u64；名称过滤在存储分页前完成。
```

```rust
// bcs-fuse-client::types：显式 None 保留旧请求体，不发送 filters:null。
#[derive(Debug, Clone, serde::Serialize)]
pub struct RecommendWorkerFilters {
    pub availability: Vec<String>,
}
// 在已有 RecommendWorkersRequest 结构中加入：
// #[serde(skip_serializing_if = "Option::is_none")]
// pub filters: Option<RecommendWorkerFilters>,
// FuseWorkerProfileService 构造请求时：
let filters = match command.scope {
    WorkerRecommendationScope::LegacyDefault => None,
    WorkerRecommendationScope::AllVisibilities => Some(RecommendWorkerFilters {
        availability: ["public", "protected", "private"].into_iter().map(String::from).collect(),
    }),
};
```

- [ ] **Step 4: 绿灯、装配与容量验证。** Run: `cd apps/bcs && cargo test -p bcs-service-api --test candidate_search_core_contract && cargo test -p bcs-fusion --test conformance_fuse_worker_profile_service && cargo test -p bcs-fuse-client && cargo test -p bcs-bot --test candidate_search_core && cargo test -p bcs-bot --test authorized_candidate_registry_reads && cargo test -p bcs --lib candidate_search_wiring_tests && cargo check --workspace`。Expected: 推荐／名称回退共用 v1 权威过滤，旧版调用方无行为漂移；单次召回与返回均最多 20 条，真实出站 filters 正确、严格 registry 冷读故障传播；批量控制面读及 registry 回填不按命中数逐条查 SQL。`BotSearchCandidateQuery` 字段使用 Task 1 加入的 `viewer_kind/name_only`，现有 `visibility`/`friend_ids`/`bot_uuids`/`q` 原名不变；不要引入第二套存储查询端口。
- [ ] **Step 5: 局部提交。** `git add apps/bcs/crates/service-api apps/bcs/crates/services/bcs-bot apps/bcs/crates/services/bcs-fusion apps/bcs/crates/external-clients/bcs-fuse-client apps/bcs/crates/bootstrap/bcs && git commit -m 'feat(bcs): reuse candidate search with authorized human visibility'`。

### Task 4：v1 应用搜索改读已批准 edge，并锁定好友生命周期

**Files:** Modify `crates/application/v1/bcs-app-bot/src/lib.rs`; Create `crates/application/v1/bcs-app-bot/src/candidate_search.rs`; Modify `crates/application/v1/bcs-app-bot/tests/v1_bot_service/candidate_search.rs`、`crates/application/v1/bcs-app-bot/tests/candidate_friend_reads.rs`; Test `crates/adapters/http/bcs-api-http/tests/bot_routes/read.rs` and `tests/friendship_routes/read.rs`。

- [ ] **Step 1: 写失败测试。** 现有 v1 搜索 fixture 中，先让 `RecordingCandidateSearch` 覆盖新入口并记录 `AuthorizedCandidateSearchQuery`（不能依赖 trait 默认报错实现），再添加以下案例：对 `human_staff-1` 和受管 Bot 分别查询，控制面指定交叉可见性，Connect 返回仅在 edge 存在的好友；断言新 Core 收到正确 `viewer_kind`、`env`、已批准 Bot 好友集，结果按原先分数／`short_profile` 映射。增加 `Noop/EmptyWorkerProfileCoreService` 回退例、另一个 Human／未管理 Bot 视角返回 403，以及列表和搜索的 `is_friend` 一致性；对 `direction=received|sent` 的申请列表保留现有 role 授权与审批测试。将 Task 2 的 `tests/candidate_friend_reads.rs` 扩展到搜索入口：真实 edge/profile 联合读失败或第二页失败必须返回 Err，不能转名称回退；空查询不读好友，语义无命中转名称时不重读好友。F=0/100/101/501/1,000 时分别计数好友页、控制面、registry 和投影阶段 SQL，并断言没有逐好友 profile SELECT；不能只统计 Task 3 的 registry 回填阶段。

```rust
let result = service.search_candidates(SearchBotCandidates {
    caller: human_caller("staff-1"), bot_id: "human_staff-1".into(),
    purpose: BotCandidatePurpose::Discovery, query: Some("规划".into()),
}).await.expect("Human 视角名称回退");
assert!(matches!(result.search_mode, BotCandidateSearchMode::NameFallback));
assert!(result.items.iter().all(|item| item.score.is_none()));
```

- [ ] **Step 2: 红灯。** Run: `cd apps/bcs && cargo test -p bcs-app-bot --test v1_bot_service search_candidates -- --nocapture`。Expected: v1 仍调用旧的 `search_candidates`，没有经 edge／Human 可见性新入口。
- [ ] **Step 3: 最小实现并拆文件。** 将已有的 v1 `search_candidates` trait 方法主体迁入新增 `src/candidate_search.rs` 的 `BotServiceImpl::search_candidates_impl`，`lib.rs` 保留一行委托。先校验当前 Human 与所选 `bot_id` 管理权限（沿用 `authorize_candidate_perspective`），空查询直接返回 `EmptyQuery`，非空查询仅调用一次 Task 2 的 `approved_bot_friend_ids`（内部严格分页），将完整集合传给 `candidate_search.search_authorized_candidates`；语义和名称回退复用该集合，不再读取好友；原有 `project_records` 批量映射与字段投影保持，不把 provider 原始响应投到 v1。

```rust
let acting = self.authorize_candidate_perspective(&staff_no, &command.bot_id).await?;
let friend_ids = self.approved_bot_friend_ids(&command.bot_id).await?;
let search = self.candidate_search.search_authorized_candidates(
    AuthorizedCandidateSearchQuery { query: normalized_query, acting_actor_id: command.bot_id,
        env: acting.env, viewer_kind: acting.kind, visibility: purpose, friend_ids, limit: 20 }
).await.map_err(map_service_error)?;
```

- [ ] **Step 4: 绿灯。** Run: `cd apps/bcs && cargo test -p bcs-app-bot --test candidate_friend_reads && cargo test -p bcs-app-bot --test v1_bot_service && cargo test -p bcs-api-http --test bot_routes && cargo test -p bcs-api-http --test friendship_routes`。Expected: 搜索 DTO 和好友申请／审批原有接口通过；无 Backend/通知端口时 pending、accept/reject 与 approved edge 读取得到同一状态；失败持久化写仍返回错误。
- [ ] **Step 5: 局部提交。** `git add apps/bcs/crates/application/v1/bcs-app-bot apps/bcs/crates/adapters/http/bcs-api-http && git commit -m 'feat(bcs): align candidate search and friend status with connect edges'`。

### Task 5：文档、路由弃用、单服务认证与发布门禁

**Files:** Modify `api-contracts/v1/openapi/bots.yaml`; `api-contracts/README.md`; `crates/adapters/http/bcs-api-http/src/v1/openapi/routes/bot.rs`; `tests/openapi/test_bot_v1_contract.py`; `tests/openapi/test_contract.py`; `crates/adapters/http/bcs-api-http/tests/bot_routes/read.rs`; Modify `api-contracts/v1/internal.yaml` 的只读消费者说明；在 `configs/bcs-config-example.toml` 的已有 OAuth-only 注释下补 BCS-only 使用说明，**不写入真实凭据**。

- [ ] **Step 1: 写失败契约测试。** 断言 `candidates` 描述 Human/Bot 的两维发现可见性和 approved-edge `is_friend`；`eligible-candidates` 的 operation 含 `deprecated: true` 且请求／返回形状不变；internal `candidates/search` 保留现有路径和 `search_mode` 枚举，明确允许认证的 Human 浏览器作为只读消费者；描述一次 provider 召回最多 20 条、返回其权威授权子集、不保证填满及无可用命中才名称回退的语义。运行旧 Bot 视角与路由拒绝用例，确保没有新匿名入口。

```python
candidate = _public_operation("get", "/openapi/v1/collaboration/bots/{bot_id}/candidates")
alias = _public_operation("get", "/openapi/v1/collaboration/bots/{bot_id}/eligible-candidates")
assert alias["deprecated"] is True
assert candidate["x-avernet-behavior"]["friend_source"] == "approved_connect_edge"
assert candidate["x-avernet-behavior"]["viewer_visibility"] == {
    "human": "user_visibility", "bot": "visibility"
}
```

- [ ] **Step 2: 红灯。** Run: `cd apps/bcs && python -m pytest tests/openapi/test_bot_v1_contract.py -q`。Expected: 契约缺少 `deprecated` / 新查询语义。
- [ ] **Step 3: 修改契约与兼容路由。** 在 OpenAPI operation 上加 `deprecated: true`、描述替代路径，不删除路径、不改 `operationId`；`README` 说明 `candidates` 是新的唯一推荐入口且两路同语义。HTTP handler 保持现有参数解析、认证及 envelope，不在 delivery layer 复制业务逻辑。必要时对 `eligible` 响应加标准弃用提示，但不能改变现有响应 JSON；不改其它 Gateway route-security 规则。BCS-only 配置指引用现成 `[api.auth]` OAuth-only chain、`trusted_browser_origins`，确认旧 `[auth]` 不会自动继承。

```yaml
# api-contracts/v1/openapi/bots.yaml 的 EligibleBotCandidatesPath.get
operationId: list_eligible_bot_candidates
deprecated: true
description: 使用 /openapi/v1/collaboration/bots/{bot_id}/candidates；本路径仅供兼容调用方迁移。
```

- [ ] **Step 4: 绿灯与部署验证。** Run: `cd apps/bcs && python -m pytest tests/openapi/test_bot_v1_contract.py tests/openapi/test_contract.py -q && cargo test -p bcs-api-http --test bot_routes && cargo test -p bcs-api-http --test friendship_routes && cargo test --workspace`。Expected: Python 契约、HTTP 契约、100% Cargo 单测全通过；有 OAuth-only `[api.auth]` 配置的 BCS v1 路由接受同源 `bcs_session`，他人 Bot 角色及无合法身份拒绝。不得使用固定测试用户冒充生产登录。
- [ ] **Step 5: 检查文件行数与提交。** Run: `git diff --check && git diff --name-only | grep -E '\.(rs|py)$' | xargs -r wc -l`；超过 1,000 行先按候选列表／搜索职责拆分。另运行 `cd apps/bcs && python -m pytest ../gateway/tests/unit/scripts/test_dump_and_publish_script.py -q` 核对 Gateway 公共契约目录不漂移。Commit: `git add apps/bcs && git commit -m 'docs(bcs): deprecate eligible candidates after edge cutover'`。

## 跨计划交接

向前端计划交付两条现有列表／搜索路由、真实 v1 envelope 样例、Human/Bot 双视角和 `is_friend`／`search_mode` 语义，以及 OAuth-only BCS 单服务启动方式、一次最多 20 条 provider 召回授权子集的限制（不保证填满）、真实 Fuse 出站 filters、registry 严格批量读取、真实 Connect/edge/profile 故障传播和查询计数，以及 SQLite/MySQL 千级好友集合容量的验证证据；注明严格好友分页的非快照边界和重试语义。BCS 本计划完成不等于前端已可用；前端在自己的计划中独立实现同源转发和浏览器全链路验收。实施者记录 Cargo、契约测试及数据库访问成本；无法运行测试时准确说明原因。BCS 不依赖前端或 Backend 的代码来完成以上测试。
