//! Outbound port for the session-scoped Workbench connection token.

use time::OffsetDateTime;

pub const GROUP_SESSION_TOKEN_MAX_COMPACT_LEN: usize = 4096;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupSessionTokenScope {
    pub tenant: String,
    pub user_id: String,
    pub group_id: String,
    pub session_id: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupSessionTokenClaims {
    pub scope: GroupSessionTokenScope,
    pub issued_at: u64,
    pub expires_at: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IssuedGroupSessionToken {
    pub token: String,
    pub expires_at: OffsetDateTime,
}

#[derive(Debug, thiserror::Error)]
pub enum GroupSessionTokenError {
    #[error("invalid group-session connection token")]
    Invalid,
    #[error("group-session connection token expired")]
    Expired,
    #[error("group-session token service unavailable: {0}")]
    Unavailable(String),
    #[error("group-session token service failed: {0}")]
    Internal(String),
}

pub trait GroupSessionTokenPort: Send + Sync + 'static {
    fn issue(
        &self,
        scope: GroupSessionTokenScope,
        ttl_seconds: u64,
    ) -> Result<IssuedGroupSessionToken, GroupSessionTokenError>;

    fn verify(&self, token: &str) -> Result<GroupSessionTokenClaims, GroupSessionTokenError>;
}
