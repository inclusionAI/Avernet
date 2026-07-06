use async_trait::async_trait;
use serde::Serialize;

use crate::core::ServiceError;

#[derive(Debug, Clone)]
pub struct CreateInviteTokenCommand {
    pub caller_actor_id: Option<String>,
    pub caller_staff_no: Option<String>,
    pub target_id: String,
    pub ttl_seconds: Option<u64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct InviteTokenResult {
    pub invite_token: String,
    pub expires_at: u64,
    pub join_url: String,
}

#[derive(Debug, Clone)]
pub struct JoinByInviteCommand {
    pub token: String,
    pub staff_no: String,
    pub nick_name: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct JoinByInviteResult {
    pub joined: bool,
    pub already_member: bool,
    pub target_type: String,
    pub target_id: String,
    pub actor_id: String,
}

#[derive(Debug, thiserror::Error)]
pub enum InviteUseCaseError {
    #[error("invalid invite token: {0}")]
    InvalidToken(String),
    #[error("invite link has expired")]
    Expired,
    #[error("login required")]
    LoginRequired,
    #[error("forbidden: {0}")]
    Forbidden(String),
    #[error("not found: {0}")]
    NotFound(String),
    #[error("conflict: {0}")]
    Conflict(String),
    #[error(transparent)]
    Service(#[from] ServiceError),
}

#[async_trait]
pub trait InviteService: Send + Sync {
    async fn create_group_invite_token(
        &self,
        cmd: CreateInviteTokenCommand,
    ) -> Result<InviteTokenResult, InviteUseCaseError>;

    async fn create_session_invite_token(
        &self,
        cmd: CreateInviteTokenCommand,
    ) -> Result<InviteTokenResult, InviteUseCaseError>;

    async fn join_group_by_invite(
        &self,
        cmd: JoinByInviteCommand,
    ) -> Result<JoinByInviteResult, InviteUseCaseError>;

    async fn join_session_by_invite(
        &self,
        cmd: JoinByInviteCommand,
    ) -> Result<JoinByInviteResult, InviteUseCaseError>;
}
