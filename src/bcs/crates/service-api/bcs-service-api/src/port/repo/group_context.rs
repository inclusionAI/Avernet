//! Group Context 持久化端口（Repo Port）。
//!
//! 对应 `plan.md` §3.1 / §3.2 分层：这是 `port::repo` 下的出站端口，
//! 由 `services/bcs-group-context-store` 实现。core 层通过 `Arc<dyn GroupContextRepoPort>`
//! 注入，**不直接依赖** `bcs-db-api`（保持分层干净，这是 CLAUDE.md 的硬约束）。
//!
//! 命名约定（核对 `bcs-group` 后）：repo port 统一以 `RepoPort` 结尾。
//!
//! ╭── Rust 速记 ──────────────────────────────────────────────────────────╮
//! │ async fn 在 trait 里返回 ServiceResult<T> = Result<T, ServiceError>。   │
//! │ #[async_trait] 让 async fn 能出现在 trait 中（Rust 原生 async trait   │
//! │   以前有约束，这个宏抹平它）。trait 声明和 impl 都要标它。                │
//! │ Arc<dyn GroupContextRepoPort> ≈ Spring 里注入一个接口实现 Bean。        │
//! ╰────────────────────────────────────────────────────────────────────────╯

use async_trait::async_trait;

use crate::types::ServiceResult;

use bcs_domain::{
    Consistency, ContextEntry, ContextView, Flow, Granularity, Origin, PolicySnapshot,
    PolicyTemplate, RetrievalItem, TemplateView,
};

// ─────────────────────────────────────────────────────────────────────────
// 支撑类型：把 multi-value 的返回包成 struct，比裸 tuple 好 review
// ─────────────────────────────────────────────────────────────────────────

/// 版本链作用域定位键（plan.md updateContent §4 「版本链定位」）。
///
/// 同一个 domain 名下可能有多条版本链（不同 granularity / 不同 scope），
/// SupersedeRequest 用它精确定位「要取代哪一条」。
/// 字段哪几个参与过滤，由 granularity 决定（见 ScopeKey::instantiate）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScopeKey {
    pub tenant_id: String,
    pub group_id: Option<String>,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub domain: String,
}

impl ScopeKey {
    /// 按 store-plan §2.1 的 unique_id 规则拼版本链主键。
    /// 同一条版本链的所有版本共享一个 unique_id。
    pub fn unique_id(&self, granularity: Granularity) -> String {
        match granularity {
            Granularity::Tenant => format!("{}:{}", self.tenant_id, self.domain),
            Granularity::Group => format!(
                "{}:{}:{}",
                self.tenant_id,
                self.group_id.as_deref().unwrap_or(""),
                self.domain
            ),
            Granularity::Session => format!(
                "{}:{}:{}:{}",
                self.tenant_id,
                self.group_id.as_deref().unwrap_or(""),
                self.session_id.as_deref().unwrap_or(""),
                self.domain
            ),
            Granularity::Run => format!(
                "{}:{}:{}:{}:{}",
                self.tenant_id,
                self.group_id.as_deref().unwrap_or(""),
                self.session_id.as_deref().unwrap_or(""),
                self.run_id.as_deref().unwrap_or(""),
                self.domain
            ),
        }
    }
}

/// 取代写入的请求（对应 plan.md updateContent step 5-10）。
///
/// store 收到后用 *一个 transaction* 完成「回填旧 valid_to + 写新 entry +
/// 写新 policy 快照」，保证原子性（store-plan §3）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SupersedeRequest {
    /// 版本链作用域定位。
    pub scope_key: ScopeKey,
    pub granularity: Granularity,
    /// 新内容。
    pub new_content: String,
    /// 来自 API 的 change_reason，落新条目 lineage.superseded_reason。
    pub change_reason: Option<String>,
    /// 新条目的写入者（actor_id 等，来自框架注入）。
    pub new_origin: Origin,
    /// 新条目的策略（flow + consistency 实例化快照）。consistency.domain 应等于
    /// scope_key.domain；granularity 等于本请求 granularity。
    pub new_flow: Flow,
    pub new_consistency: Consistency,
    /// 新 policy 的 obligations（JSON 字符串）。参与 policy_version 比对。
    pub policy_obligations: Option<String>,
    /// 新条目 context_id，由 application 层生成 UUID。
    pub new_context_id: String,
}

/// supersede 的结果。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SupersedeOutcome {
    pub new_context_id: String,
    /// 被取代的旧条目 context_id（supersedes 指向它）。
    pub superseded_id: String,
    /// 本次写下的 policy 快照版本。
    pub policy_version: String,
}

/// createByTemplate 的写入请求（plan.md createByTemplate step 8）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InsertContextRequest {
    pub entry: ContextEntry,
    pub policy: PolicySnapshot,
}

/// retrievable entry 的最小投影（带冻结的策略，用于可见性判定与过期过滤）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoredEntry {
    pub entry: ContextEntry,
    pub policy: PolicySnapshot,
}

// ─────────────────────────────────────────────────────────────────────────
// 审计
// ─────────────────────────────────────────────────────────────────────────

/// retrieve 强制落审计的记录（plan.md retrieve step 5）。
/// 即使返回空集也要落审计（「查询过但无可见内容」）。
///
/// 存独立审计表 `bcs_group_context_audit`（store-plan 第 5 项决定）。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuditEntry {
    pub actor_id: String,
    pub tenant_id: String,
    pub group_id: Option<String>,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub domain: String,
    /// 命中的条目 context_id 列表（可能为空）。
    pub hit_context_ids: Vec<String>,
    /// Unix 毫秒。
    pub tx_time: i64,
}

// ─────────────────────────────────────────────────────────────────────────
// 端口
// ─────────────────────────────────────────────────────────────────────────

/// Group Context 持久化契约。
///
/// 与 `GroupContextCoreService` 刻意分离：repo 拥有持久化与 row↔domain 映射，
/// core 拥有行为与编排（参见 `port/repo/group.rs` 同款注释）。
#[async_trait]
pub trait GroupContextRepoPort: Send + Sync {
    /// 按 (granularity, scope_key) 查同作用域下是否已有活跃版本（valid_to IS NULL）。
    /// createByTemplate 幂等检查用（plan.md createByTemplate step 7）。
    async fn find_active_in_scope(
        &self,
        granularity: Granularity,
        scope_key: &ScopeKey,
    ) -> ServiceResult<Option<String>>;

    /// 写一条新 context（entry + policy 快照）。
    /// createByTemplate 「首次写入」用（plan.md createByTemplate step 8）。
    async fn insert_context(&self, request: InsertContextRequest) -> ServiceResult<()>;

    /// 取代写入（**原子三步 transaction**，store-plan §3）。
    ///
    /// 实现要点（store возвращ этим对齐 plan.md updateContent step 5-10）：
    ///   1. Query: 在目标 scope 下查活跃版本（valid_to IS NULL），MySQL 加 FOR UPDATE
    ///      锁该行，SQLite 无行锁（靠 transaction 顺序保证）。
    ///      —— 查不到返回 ServiceError 的 not_found 语义。
    ///   2. Execute: 回填旧条目 valid_to = tx_time（ExecuteChecked 期望 1 行，
    ///      防并发抢取代，见 store-plan §3）。
    ///   3. Execute: 写新 entry，supersedes 指向旧条目 **context_id**(UUID)，
    ///      superseded_reason = change_reason。
    ///   4. Execute: 写新 policy 快照（policy_version 按新旧比对决定 v0.1 还是递增）。
    ///
    /// 返回 `SupersedeNotFound` 表示该作用域无活跃版本可取代。
    async fn supersede(&self, request: SupersedeRequest) -> ServiceResult<SupersedeOutcome>;

    /// updateContent 候选集检索：按 domain 匹配所有条目（plan.md updateContent step 2）。
    /// 返回同 domain 名下全部（含历史版本）——core 在内存里按 collect_from 过滤写权限。
    async fn find_entries_by_domain(&self, domain: &str) -> ServiceResult<Vec<StoredEntry>>;

    /// retrieve 用：按 domain + 调用者作用域取候选条目（plan.md retrieve step 1-2）。
    /// 可见性/过期过滤在 core 层做（visible_to 判定依赖完整 flow），此处先粗筛 domain。
    async fn find_candidates_for_retrieve(
        &self,
        domain: &str,
        scope: &Origin,
    ) -> ServiceResult<Vec<StoredEntry>>;

    /// status：取当前 actor 作用域下已存在的活跃 contexts（plan.md status）。
    async fn list_active_contexts_for_actor(
        &self,
        scope: &Origin,
    ) -> ServiceResult<Vec<ContextView>>;

    /// status：取当前 actor 可创建的模板（collect_from 匹配），bot 据此调 createByTemplate。
    async fn list_creatable_templates_for_actor(
        &self,
        scope: &Origin,
    ) -> ServiceResult<Vec<TemplateView>>;

    /// 按 template_id 取模板（createByTemplate step 1）。
    async fn find_policy_template(&self, template_id: &str) -> ServiceResult<Option<PolicyTemplate>>;

    /// 列全部模板（status 可创建模板视图，可按 tenant/group 过滤）。
    async fn list_policy_templates(&self, scope: &Origin) -> ServiceResult<Vec<PolicyTemplate>>;

    /// retrieve 强制落审计（plan.md retrieve step 5）。即使空集也要写一条。
    async fn write_retrieve_audit(&self, entry: AuditEntry) -> ServiceResult<()>;

    /// retrieve 最终返回的精简条目集（core 过滤排序后存库前/后均可，
    /// 这里仅语义占位——实际 retrieve 结果由 core 组装，store 只负责落审计）。
    /// 该方法保留作为接口完整性，首期 core 用 `find_candidates_for_retrieve` 的返回。
    async fn as_retrieval_items(_entries: &[ContextEntry]) -> ServiceResult<Vec<RetrievalItem>> {
        // 默认占位实现：core 自行把 StoredEntry 映射成 RetrievalItem，store 不必实现。
        Ok(Vec::new())
    }
}
