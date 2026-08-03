//! Session-scoped Workbench connection-token use cases.

use async_trait::async_trait;
use time::OffsetDateTime;

use super::{ApplicationError, AuthenticatedCaller, SessionParticipant};

pub const GROUP_SESSION_WS_TOKEN_TTL_SECONDS: u64 = 300;

#[derive(Debug, Clone)]
pub struct IssueGroupSessionConnectionToken {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifyGroupSessionConnectionToken {
    pub token: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IssuedGroupSessionConnectionToken {
    pub token: String,
    pub expires_at: OffsetDateTime,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupSessionConnectionBinding {
    pub tenant: String,
    pub user_id: String,
    pub group_id: String,
    pub session_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorizeGroupSessionConnection {
    pub binding: GroupSessionConnectionBinding,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorizedGroupSessionConnection {
    pub participants: Vec<SessionParticipant>,
}

#[derive(Debug, thiserror::Error)]
pub enum GroupSessionConnectionError {
    #[error(transparent)]
    Application(#[from] ApplicationError),
    #[error("invalid group-session connection token")]
    InvalidConnectionToken,
    #[error("group-session token service unavailable")]
    TokenServiceUnavailable,
    #[error("group-session connection failed: {0}")]
    Internal(String),
}

#[async_trait]
pub trait GroupSessionConnectionService: Send + Sync {
    async fn issue_token(
        &self,
        command: IssueGroupSessionConnectionToken,
    ) -> Result<IssuedGroupSessionConnectionToken, GroupSessionConnectionError>;

    async fn verify_token(
        &self,
        command: VerifyGroupSessionConnectionToken,
    ) -> Result<GroupSessionConnectionBinding, GroupSessionConnectionError>;

    async fn authorize_connect(
        &self,
        command: AuthorizeGroupSessionConnection,
    ) -> Result<AuthorizedGroupSessionConnection, GroupSessionConnectionError>;
}
