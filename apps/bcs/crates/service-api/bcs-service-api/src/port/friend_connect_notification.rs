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
    /// Resolved display name of the applicant (a human nick name or a bot
    /// name). When `None`, the adapter falls back to `applicant_actor_id`.
    pub applicant_name: Option<String>,
    /// Resolved display name of the target bot. When `None`, the adapter
    /// falls back to `target_bot_id`.
    pub target_bot_name: Option<String>,
    /// Resolved `applicant_user_id` to send to the backend work-order
    /// API. Only meaningful for a HUMAN applicant: the adapter maps the
    /// command value first, then strips `human_` to a staff_no.
    ///
    /// A BOT applicant never carries a Human applicant id — the
    /// work-order intake derives the acting Human (the owner/manager who
    /// performed the request) from the verified principal forwarded with
    /// the notification, not from this field; the Bot's id and its
    /// historical `created_by` are Bot facts, not the acting user, and
    /// must not be offered here. Producers leave this `None` for Bot
    /// applicants; the adapter additionally ignores any residual value
    /// for them. The Bot applicant always travels separately in
    /// `applicant_actor_id` / `biz_data.applicant_actor_id`.
    pub applicant_user_id: Option<String>,
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
