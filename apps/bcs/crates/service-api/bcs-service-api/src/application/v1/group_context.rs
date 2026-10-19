//! Transport-neutral OpenAPI V1 Group Context application contract.

use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::ApplicationError;
pub use bcs_domain::{
    GroupContextEntry, GroupContextPermission, GroupContextPolicy, GroupContextTime, ScopeLevel,
};

/// Spec-level statuses used by HTTP adapters when projecting application
/// results and errors into `/groupcontext/*` JSON responses.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GroupContextStatus {
    Ok,
    InvalidParam,
    PermissionDenied,
    NotFound,
    Conflict,
    PayloadTooLarge,
    InternalError,
}

/// Minimal success/error envelope shared by add/update/systemset.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GroupContextAck {
    pub status: GroupContextStatus,
    pub error_msg: Option<String>,
}

impl GroupContextAck {
    pub fn ok() -> Self {
        Self {
            status: GroupContextStatus::Ok,
            error_msg: None,
        }
    }
}

/// URL/query context supplied by the delivery adapter. The application service
/// resolves `scope` from these raw ids according to the requested scope level.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct GroupContextRequestContext {
    pub group_id: String,
    pub session_id: Option<String>,
    pub round_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AddGroupContext {
    pub actor_id: String,
    pub request_context: GroupContextRequestContext,
    pub name: String,
    pub scope_level: ScopeLevel,
    pub content: String,
    pub visible_to: GroupContextPolicy,
    pub collect_from: GroupContextPolicy,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub valid_from: Option<GroupContextTime>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub valid_to: Option<GroupContextTime>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct UpdateGroupContext {
    pub actor_id: String,
    pub request_context: GroupContextRequestContext,
    pub name: String,
    pub scope_level: ScopeLevel,
    pub content: Option<String>,
    pub change_reason: Option<String>,
    pub visible_to: Option<GroupContextPolicy>,
    pub collect_from: Option<GroupContextPolicy>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub valid_from: Option<GroupContextTime>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub valid_to: Option<GroupContextTime>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ListGroupContexts {
    pub actor_id: String,
    pub request_context: GroupContextRequestContext,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scope_levels: Option<Vec<ScopeLevel>>,
}

impl ListGroupContexts {
    pub fn effective_scope_levels(&self) -> Vec<ScopeLevel> {
        self.scope_levels
            .clone()
            .unwrap_or_else(|| infer_scope_levels_from_context(&self.request_context))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GetGroupContext {
    pub actor_id: String,
    pub request_context: GroupContextRequestContext,
    pub name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scope_levels: Option<Vec<ScopeLevel>>,
    /// Accepted for forward compatibility. In V1, selected group/session/round
    /// scopes can yield at most one active row each, so the service may return
    /// no more than three rows before `limit` becomes meaningful for future
    /// wildcard scope queries.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub limit: Option<u32>,
}

impl GetGroupContext {
    pub fn effective_scope_levels(&self) -> Vec<ScopeLevel> {
        self.scope_levels
            .clone()
            .unwrap_or_else(|| infer_scope_levels_from_context(&self.request_context))
    }
}

fn infer_scope_levels_from_context(
    request_context: &GroupContextRequestContext,
) -> Vec<ScopeLevel> {
    if request_context
        .round_id
        .as_deref()
        .is_some_and(|value| !value.trim().is_empty())
    {
        vec![ScopeLevel::Round]
    } else if request_context
        .session_id
        .as_deref()
        .is_some_and(|value| !value.trim().is_empty())
    {
        vec![ScopeLevel::Session]
    } else {
        vec![ScopeLevel::Group]
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SystemSetGroupContext {
    pub request_context: GroupContextRequestContext,
    pub name: String,
    pub scope_level: ScopeLevel,
    pub content: String,
    pub visible_to: GroupContextPolicy,
    pub collect_from: GroupContextPolicy,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub valid_from: Option<GroupContextTime>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub valid_to: Option<GroupContextTime>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GroupContextListItem {
    pub name: String,
    pub scope_level: ScopeLevel,
    pub scope: String,
    pub permission: GroupContextPermission,
    pub gmt_create: GroupContextTime,
    pub gmt_modified: GroupContextTime,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GroupContextListResponse {
    pub contexts: Vec<GroupContextListItem>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GroupContextGetItem {
    pub name: String,
    pub scope_level: ScopeLevel,
    pub content: String,
    pub valid_from: GroupContextTime,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub valid_to: Option<GroupContextTime>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GroupContextGetResponse {
    pub items: Vec<GroupContextGetItem>,
}

#[async_trait]
pub trait GroupContextApplicationService: Send + Sync {
    async fn add(&self, command: AddGroupContext) -> Result<GroupContextAck, ApplicationError>;

    async fn update(
        &self,
        command: UpdateGroupContext,
    ) -> Result<GroupContextAck, ApplicationError>;

    async fn list(
        &self,
        query: ListGroupContexts,
    ) -> Result<GroupContextListResponse, ApplicationError>;

    async fn get(
        &self,
        query: GetGroupContext,
    ) -> Result<GroupContextGetResponse, ApplicationError>;

    async fn systemset(
        &self,
        command: SystemSetGroupContext,
    ) -> Result<GroupContextAck, ApplicationError>;
}
