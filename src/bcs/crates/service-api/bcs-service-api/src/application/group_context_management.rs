//! Group Context 应用服务契约（Application Service）+ 请求/响应 DTO + 用例错误。
//!
//! 对应 `plan.md` §2.4 的四个 API 与 §2.5 错误码表。application 层是面向
//! HTTP 路由的 use-case 编排：它调 core::GroupContextCoreService，把
//! `ServiceResult` 翻成应用层的 `Result<_, GroupContextUseCaseError>`，
//! 供 delivery adapter 映射到 HTTP 状态码（ok/permission_denied/conflict/
//! not_found/ambiguous/payload_too_large/invalid_param/internal_error）。
//!
//! ╭── Rust 速记 ──────────────────────────────────────────────────────────╮
//! │ thiserror::Error：标准库之外的小依赖，给 enum 自动实现 std::error::Error  │
//! │   和 Display。#[error("...")] 描述每变体的字符串模板。                     │
//! │ #[from]：让 ServiceError 能被 `?` 自动转成 GroupContextUseCaseError。       │
//! │ #[derive(Debug, Clone)] 不对（thiserror enum 通常不 Clone，因为包了内部    │
//! │   error）。这里 DTO struct 才 Clone。                                       │
//! ╰────────────────────────────────────────────────────────────────────────╯
//!
//! DTO 与 trait 同放一文件，沿用 `application/group_management.rs` 的做法
//! （`GroupCreateCommand` 等就放在 trait 文件里）。

use async_trait::async_trait;

use bcs_domain::{Granularity, Origin, RetrievalItem};

use crate::core::ServiceResult;
use crate::types::ServiceError;

// ─────────────────────────────────────────────────────────────────────────
// 用例错误（对应 plan.md §2.5 错误码表）
// ─────────────────────────────────────────────────────────────────────────

#[derive(Debug, thiserror::Error)]
pub enum GroupContextUseCaseError {
    /// 400 invalid_param：缺必填 / 格式错误等参数校验失败。
    #[error("invalid_param: {0}")]
    InvalidParam(String),

    /// 403 permission_denied：调用方无权执行（collect_from / visible_to 不匹配）。
    #[error("permission_denied: {0}")]
    PermissionDenied(String),

    /// 404 not_found：指定 domain 下无匹配条目或无活跃版本。
    #[error("not_found: {0}")]
    NotFound(String),

    /// 409 conflict：同 (domain, granularity) 已有活跃版本，createByTemplate 拒绝。
    #[error("conflict: {0}")]
    Conflict(String),

    /// 409 ambiguous：同一 domain 对应多条可写版本链，需传 granularity 消歧。
    /// 附带可选的 granularity 字符串列表，供调用方展示。
    #[error("ambiguous: {} (candidates: {})", message, candidates.join(", "))]
    Ambiguous {
        message: String,
        candidates: Vec<String>,
    },

    /// 413 payload_too_large：content 超过单条上限（默认 1 KB）。
    #[error("payload_too_large: {0}")]
    PayloadTooLarge(String),

    /// 500 internal_error：服务端内部错误（包装 core 的 ServiceError）。
    #[error(transparent)]
    Internal(#[from] ServiceError),
}

// ╭── 关于 #[from] ServiceError 的小段说明 ───────────────────────────────────╮
// │ application 层 impl 里对 core 调用用 `?`：core 返回 Err(ServiceError) 时，   │
// │ `?` 会把它包成 GroupContextUseCaseError::Internal。但 plan.md 错误码要求     │
// │ 区分 permission_denied / conflict / not_found——这些是 core 层用对应         │
// │ ServiceError 变体（Forbidden / Conflict / InvalidOperation）表达的，        │
// │ application impl 在翻译时按 ServiceError 变体手动映射成更细的用例错误       │
// │ （见实现里的 `map_core_error`）。这样 HTTP 层能给出对的错误码，而不是一律     │
// │ 500。                                                                       │
// ╰─────────────────────────────────────────────────────────────────────────────╯

// ─────────────────────────────────────────────────────────────────────────
// 请求 / 响应 DTO（对齐 plan.md §2.4 各接口参数表）
// ─────────────────────────────────────────────────────────────────────────
//
// 调用链：HTTP handler（adapter）→ GroupContextManagementService（application）
// → 透给 core 的 Command。application 层 DTO 带 actor_id 等框架注入字段，
// application impl 把它们组装成 core Command 与 Origin。

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct StatusRequest {
    pub tenant_id: String,
    pub group_id: Option<String>,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub actor_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct StatusContext {
    pub domain: String,
    pub granularity: Granularity,
    pub description: String,
    pub permission: String, // "W" | "R" | "WR"
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct StatusTemplate {
    pub template_id: String,
    pub granularity: Granularity,
    pub description: String,
    pub params: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct StatusResponse {
    pub contexts: Vec<StatusContext>,
    pub context_templates: Vec<StatusTemplate>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct CreateByTemplateRequest {
    pub tenant_id: String,
    pub group_id: Option<String>,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub actor_id: String,
    pub template_id: String,
    pub content: String,
    pub params: std::collections::HashMap<String, String>,
}

/// plan.md createByTemplate 响应：统一 status 字符串 + 数据字段。
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct CreateByTemplateResponse {
    pub status: String, // "ok" | "permission_denied" | "conflict" | "invalid_param"
    pub error_msg: Option<String>,
    pub context_id: Option<String>,
    pub domain: Option<String>,
    pub granularity: Option<Granularity>,
    pub content: Option<String>,
    pub superseded_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct UpdateContentRequest {
    pub tenant_id: String,
    pub group_id: Option<String>,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub actor_id: String,
    pub domain: String,
    pub granularity: Option<Granularity>,
    pub content: String,
    /// 本次取代原因（可选），落新条目 superseded_reason。
    pub change_reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct UpdateContentResponse {
    pub status: String, // "ok" | "permission_denied" | "not_found" | "ambiguous" | "invalid_operation"
    pub error_msg: Option<String>,
    pub context_id: Option<String>,
    pub domain: Option<String>,
    pub granularity: Option<Granularity>,
    pub content: Option<String>,
    pub superseded_id: Option<String>,
    pub change_reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct RetrieveRequest {
    pub tenant_id: String,
    pub group_id: Option<String>,
    pub session_id: Option<String>,
    pub run_id: Option<String>,
    pub actor_id: String,
    pub domain: String,
    pub limit: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct RetrieveResponseItem {
    pub context_id: String,
    pub domain: String,
    pub granularity: Granularity,
    pub content: String,
    /// ISO 8601 字符串（HTTP 层把 i64 毫秒格式化），DTO 层先留 String 直传。
    pub valid_from: String,
}

/// retrieve 响应（plan.md §2.4 retrieve）。
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct RetrieveResponse {
    pub items: Vec<RetrieveResponseItem>,
}

// 为应用 impl 复用，标注 RetrievalItem 来自 core 结果的精简类型。
pub use bcs_domain::RetrievalItem as CoreRetrievalItem;

// ─────────────────────────────────────────────────────────────────────────
// 端口
// ─────────────────────────────────────────────────────────────────────────

/// Group Context 应用服务（面向 HTTP 路由的 use-case 编排）。
///
/// impl 在 `services/bcs-group-context` crate 里（`GroupContextManagement` struct，
/// 持 `Arc<dyn GroupContextCoreService>`）。application 层职责：
///  - 从 HTTP DTO 组装 core Command 与 Origin；
///  - 调 core；
///  - 把 ServiceResult 翻成 GroupContextUseCaseError（HTTP 层据此定状态码）。
#[async_trait]
pub trait GroupContextManagementService: Send + Sync {
    async fn status(&self, req: StatusRequest) -> Result<StatusResponse, GroupContextUseCaseError>;

    async fn create_by_template(
        &self,
        req: CreateByTemplateRequest,
    ) -> Result<CreateByTemplateResponse, GroupContextUseCaseError>;

    async fn update_content(
        &self,
        req: UpdateContentRequest,
    ) -> Result<UpdateContentResponse, GroupContextUseCaseError>;

    async fn retrieve(
        &self,
        req: RetrieveRequest,
    ) -> Result<RetrieveResponse, GroupContextUseCaseError>;
}
