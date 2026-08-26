use async_trait::async_trait;

use crate::principal::RequestAuthHeaders;
use crate::ServiceResult;

/// Notification kind emitted from the friend-connect workflow.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FriendConnectNotificationKind {
    /// A friend request is pending and needs review.
    ApprovalRequested,
    /// A request was automatically approved.
    AutoApproved,
    /// A request was reviewed manually.
    Reviewed,
}

/// Outbound command for friend-connect notifications.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FriendConnectNotificationCommand {
    pub kind: FriendConnectNotificationKind,
    pub env: String,
    pub request_ids: Vec<String>,
    pub applicant_actor_id: String,
    pub target_bot_id: String,
    pub recipient_user_ids: Vec<String>,
    pub message: Option<String>,
    pub request_auth: Option<RequestAuthHeaders>,
}

#[async_trait]
pub trait FriendConnectNotificationPort: Send + Sync {
    async fn notify(&self, command: FriendConnectNotificationCommand) -> ServiceResult<()>;
}

/// No-op notification port for local tests and bootstrap wiring until a real
/// backend adapter is plugged in.
pub struct NoopFriendConnectNotificationPort;

#[async_trait]
impl FriendConnectNotificationPort for NoopFriendConnectNotificationPort {
    async fn notify(&self, _command: FriendConnectNotificationCommand) -> ServiceResult<()> {
        Ok(())
    }
}
