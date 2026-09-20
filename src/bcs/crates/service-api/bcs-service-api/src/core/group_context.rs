//! Group Context 核心服务契约（Core Service）。
//!
//! 对应 `plan.md` §3.1：core 层持 `Arc<dyn GroupContextRepoPort>`，做行为与编排，
//! 不直接依赖 DB plugin。一个 trait 方法对应一个对外 API 的核心逻辑。
//!
//! 错误类型用 `ServiceResult<T>`（= `Result<T, ServiceError>`），沿核心层惯例。
//!
//! ╭── Rust 速记 ──────────────────────────────────────────────────────────╮
//! │ trait 里的「提供方法」（带默认 body）≈ Java 接口的 default method。       │
//! │ 调用方可以直接用默认实现，也可以在 impl 里覆盖。本文件只声明契约，不写     │
//! │ 实现——impl 在 `services/bcs-group-context` crate 里。                     │
//! ╰────────────────────────────────────────────────────────────────────────╯

use async_trait::async_trait;

use super::ServiceResult;
use bcs_domain::{
    Consistency, ContextView, Flow, Granularity, Origin, PolicySnapshot, RetrievalItem,
    TemplateView,
};

// ─────────────────────────────────────────────────────────────────────────
// 各 API 的请求 / 结果（core 层用，比起简化版 DTO 多了内部字段）
// ─────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StatusCommand {
    pub origin: Origin,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct StatusResult {
    pub contexts: Vec<ContextView>,
    pub context_templates: Vec<TemplateView>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CreateByTemplateCommand {
    pub origin: Origin,
    pub template_id: String,
    pub content: String,
    /// 模板形参键值对（如 {"player_id": "player_1"}）。
    pub params: std::collections::HashMap<String, String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct CreateResult {
    pub context_id: String,
    pub domain: String,
    pub granularity: Granularity,
    pub content: String,
    /// 首次创建为 None（无被取代条目）。
    pub superseded_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UpdateContentCommand {
    pub origin: Origin,
    pub domain: String,
    /// 同 domain 多条可写条目时显式消歧，否则 None 让系统自动匹配。
    pub granularity: Option<Granularity>,
    pub content: String,
    /// 本次取代原因，落新条目 lineage.superseded_reason。可选。
    pub change_reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct UpdateResult {
    pub context_id: String,
    pub domain: String,
    pub granularity: Granularity,
    pub content: String,
    /// 被取代的旧条目 context_id（supersedes 指向它）。
    pub superseded_id: String,
    /// 回显本次取代原因，便于调用方确认留痕。
    pub change_reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RetrieveCommand {
    pub origin: Origin,
    pub domain: String,
    pub limit: usize,
}

/// retrieve 结果不经 PolicySnapshot（落审计用），只返回精简视图。
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct RetrieveResult {
    pub items: Vec<RetrievalItem>,
}

// ─────────────────────────────────────────────────────────────────────────
// 端口
// ─────────────────────────────────────────────────────────────────────────

/// 组上下文核心服务。一个方法 = 一个 API 的核心编排。
///
/// 实现里每个方法都会带注释写「执行过程 + 伪代码」，标出调 repo 的哪个函数
/// 与 plan.md 的哪一步对应——这些注释在 `services/bcs-group-context` 的 impl
/// 里，方便逐函数 review。
#[async_trait]
pub trait GroupContextCoreService: Send + Sync {
    /// POST /groupcontext/status（plan.md §2.4 status）。
    async fn status(&self, command: StatusCommand) -> ServiceResult<StatusResult>;

    /// POST /groupcontext/createByTemplate（plan.md §2.4 createByTemplate）。
    async fn create_by_template(
        &self,
        command: CreateByTemplateCommand,
    ) -> ServiceResult<CreateResult>;

    /// POST /groupcontext/updateContent（plan.md §2.4 updateContent）。
    /// 取代链由 repo 在一个 transaction 内原子完成。
    async fn update_content(&self, command: UpdateContentCommand) -> ServiceResult<UpdateResult>;

    /// POST /groupcontext/retrieve（plan.md §2.4 retrieve）。
    /// retrieve 必须落审计（即使空集）——core 在拿到结果后调 repo.write_retrieve_audit。
    async fn retrieve(&self, command: RetrieveCommand) -> ServiceResult<RetrieveResult>;

    // ── 给 impl 用的辅助类型 re-export（让调用方少写 import）──────────────
    // 这里不暴露 Flow / Consistency / PolicySnapshot 的构造，仅占位让 trait 自洽。
    #[doc(hidden)]
    fn _types_armed(_: Flow, _: Consistency, _: PolicySnapshot) {
        // 只为了让 trait 引用到这些类型、防止 unused import 误判；不参与行为。
    }
}
