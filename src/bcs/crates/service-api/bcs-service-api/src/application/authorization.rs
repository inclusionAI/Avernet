//! Authorization use-case contracts shared by adapters and services.

use async_trait::async_trait;

use crate::core::{AuthzContext, BuildA2aAuthzContextRequest, ServiceError};
use crate::types::{
    AuthzDecisionLog, GrantKind, GrantStatus, PermissionProfile, PermissionRequest,
    PermissionRequestStatus, Rule, RulesGrantMaterial,
};

#[derive(Debug, thiserror::Error)]
pub enum AuthorizationUseCaseError {
    #[error("not found: {0}")]
    NotFound(String),
    #[error("invalid request: {0}")]
    InvalidRequest(String),
    #[error("conflict: {0}")]
    Conflict(String),
    #[error(transparent)]
    Internal(ServiceError),
}

impl From<ServiceError> for AuthorizationUseCaseError {
    fn from(error: ServiceError) -> Self {
        match error {
            ServiceError::Conflict(message) => Self::Conflict(message),
            ServiceError::BotNotFound(message)
            | ServiceError::BotNotRegistered(message)
            | ServiceError::GroupNotFound(message)
            | ServiceError::ProposalNotFound(message)
            | ServiceError::ProviderNotFound(message)
            | ServiceError::SessionNotFound(message)
            | ServiceError::FriendRequestNotFound(message) => Self::NotFound(message),
            ServiceError::InvalidOperation { message, .. }
            | ServiceError::SessionInvalidParams(message)
            | ServiceError::SessionCallbackPending(message) => Self::InvalidRequest(message),
            other => Self::Internal(other),
        }
    }
}

/// Application-facing summary of a grant created or affected by a permission decision.
/// It intentionally omits EdgeGrant internals such as edge_id; BCS keeps EdgeGrant
/// as the internal persisted authorization fact.
#[derive(Debug, Clone, PartialEq)]
pub struct PermissionGrantDecisionView {
    pub from_id: String,
    pub to_id: String,
    pub env: String,
    pub grant_kind: GrantKind,
    pub grant_ref_id: String,
    pub rules: Option<Vec<Rule>>,
    pub rules_revision: Option<i64>,
    pub rules_digest: Option<String>,
    pub status: GrantStatus,
    pub request_id: Option<String>,
    pub expires_at: Option<i64>,
}

#[derive(Debug, Clone)]
pub struct ListPermissionProfilesCommand {
    pub caller_actor_id: String,
    pub bot_id: String,
    pub env: String,
}

#[derive(Debug, Clone)]
pub struct UpsertPermissionProfileCommand {
    pub caller_actor_id: String,
    pub permission_profile: PermissionProfile,
}

#[derive(Debug, Clone)]
pub struct DeletePermissionProfileCommand {
    pub caller_actor_id: String,
    pub permission_profile_id: String,
}

#[derive(Debug, Clone)]
pub struct ListPermissionRequestsCommand {
    pub caller_actor_id: String,
    pub to_id: String,
    pub status: Option<PermissionRequestStatus>,
}

#[derive(Debug, Clone)]
pub struct CreatePermissionRequestCommand {
    pub caller_actor_id: String,
    pub permission_request: PermissionRequest,
}

#[derive(Debug, Clone)]
pub struct DecidePermissionRequestCommand {
    pub caller_actor_id: String,
    pub request_id: String,
    pub decision: PermissionRequestStatus,
    pub decision_reason: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ResolvePermissionProfileCommand {
    pub caller_actor_id: String,
    pub permission_profile_id: String,
    pub revision: i64,
    pub digest: String,
    pub to_id: String,
    pub authz_context_id: Option<String>,
    pub run_id: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ResolveRulesGrantCommand {
    pub caller_actor_id: String,
    pub rules_grant_ref: String,
    pub revision: i64,
    pub digest: String,
    pub to_id: String,
    pub authz_context_id: Option<String>,
    pub run_id: Option<String>,
}

#[async_trait]
pub trait AuthorizationService: Send + Sync {
    async fn list_permission_profiles(
        &self,
        command: ListPermissionProfilesCommand,
    ) -> Result<Vec<PermissionProfile>, AuthorizationUseCaseError>;

    async fn upsert_permission_profile(
        &self,
        command: UpsertPermissionProfileCommand,
    ) -> Result<PermissionProfile, AuthorizationUseCaseError>;

    async fn delete_permission_profile(
        &self,
        command: DeletePermissionProfileCommand,
    ) -> Result<(), AuthorizationUseCaseError>;

    async fn list_permission_requests(
        &self,
        command: ListPermissionRequestsCommand,
    ) -> Result<Vec<PermissionRequest>, AuthorizationUseCaseError>;

    async fn create_permission_request(
        &self,
        command: CreatePermissionRequestCommand,
    ) -> Result<PermissionRequest, AuthorizationUseCaseError>;

    async fn decide_permission_request(
        &self,
        command: DecidePermissionRequestCommand,
    ) -> Result<Vec<PermissionGrantDecisionView>, AuthorizationUseCaseError>;

    async fn resolve_permission_profile(
        &self,
        command: ResolvePermissionProfileCommand,
    ) -> Result<PermissionProfile, AuthorizationUseCaseError>;

    async fn resolve_rules_grant(
        &self,
        command: ResolveRulesGrantCommand,
    ) -> Result<RulesGrantMaterial, AuthorizationUseCaseError>;

    async fn build_a2a_authz_context(
        &self,
        request: BuildA2aAuthzContextRequest,
    ) -> Result<AuthzContext, AuthorizationUseCaseError>;

    async fn append_authz_decision_log(
        &self,
        log: AuthzDecisionLog,
    ) -> Result<(), AuthorizationUseCaseError>;
}
