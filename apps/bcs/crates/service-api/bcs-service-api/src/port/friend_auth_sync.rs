//! BCS -> backend friend-auth-sync port. BCS resolves a human->bot friend
//! relationship (EdgeGrant) and asks backend to sync it to AceAgent.

use async_trait::async_trait;

use crate::principal::RequestAuthHeaders;
use crate::ServiceResult;

/// Action to apply to the friend-auth grant synced to the backend.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FriendAuthSyncAction {
    Grant,
    Revoke,
}

impl FriendAuthSyncAction {
    pub fn as_str(self) -> &'static str {
        match self {
            FriendAuthSyncAction::Grant => "grant",
            FriendAuthSyncAction::Revoke => "revoke",
        }
    }
}

/// Outbound command for friend-auth sync towards the backend.
#[derive(Debug, Clone)]
pub struct FriendAuthSyncCommand {
    pub env: String,
    /// BCS target actor ID. The TC HTTP adapter accepts only `bot_id:workNo`
    /// and sends its bare bot ID and owner suffix to TC; other IDs are skipped.
    pub bot_id: String,
    /// Legacy bot owner metadata (bare). The TC HTTP adapter uses the actor
    /// suffix instead, including when this metadata is absent or stale.
    pub owner_work_no: String,
    /// Friend work no (bare, no `human_` prefix).
    pub human_work_no: String,
    pub action: FriendAuthSyncAction,
    pub request_id: Option<String>,
    /// Forwarded gateway principal + trace headers.
    pub request_auth: Option<RequestAuthHeaders>,
}

#[async_trait]
pub trait FriendAuthSyncPort: Send + Sync {
    async fn sync(&self, command: FriendAuthSyncCommand) -> ServiceResult<()>;
}

/// No-op sync port for local tests and bootstrap wiring until a real backend
/// adapter is plugged in.
pub struct NoopFriendAuthSyncPort;

#[async_trait]
impl FriendAuthSyncPort for NoopFriendAuthSyncPort {
    async fn sync(&self, _command: FriendAuthSyncCommand) -> ServiceResult<()> {
        Ok(())
    }
}
