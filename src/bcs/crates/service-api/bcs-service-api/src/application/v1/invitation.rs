use async_trait::async_trait;
use serde::{Deserialize, Serialize};

use super::{ApplicationError, AuthenticatedCaller};

/// Kind of resource an invitation grants access to.
///
/// Mirrors `InvitationTargetType` in the V1 domain model. V1 only exposes the
/// two joinable target kinds; the snake_case wire form is enforced via serde.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InvitationTargetType {
    Group,
    Session,
}

/// Lifecycle state of an invitation token.
///
/// Mirrors `InvitationState` in the V1 domain model.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InvitationState {
    Pending,
    Accepted,
    Expired,
}

/// V1 projection of an invitation.
///
/// `expires_at` is optional: servers may issue non-expiring tokens or omit the
/// field until materialized. `created_at` is always present.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Invitation {
    pub token: String,
    pub target_type: InvitationTargetType,
    pub target_id: String,
    pub state: InvitationState,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub expires_at: Option<u64>,
    pub created_at: u64,
}

/// Result of accepting an invitation.
///
/// `joined` indicates the acceptor joined on this call. `already_joined` is
/// `Some(true)` when the acceptor was already a member before this call (retry
/// idempotency) and `None`/omitted on first successful join.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InvitationAcceptResult {
    pub target_type: InvitationTargetType,
    pub target_id: String,
    pub joined: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub already_joined: Option<bool>,
}

/// Create an invitation for a Group target.
///
/// `expires_in_seconds` overrides the server default lifetime when supplied.
#[derive(Debug, Clone)]
pub struct CreateGroupInvitation {
    pub caller: AuthenticatedCaller,
    pub group_id: String,
    pub expires_in_seconds: Option<u64>,
}

/// Create an invitation for a Session target.
///
/// `expires_in_seconds` overrides the server default lifetime when supplied.
#[derive(Debug, Clone)]
pub struct CreateSessionInvitation {
    pub caller: AuthenticatedCaller,
    pub session_id: String,
    pub expires_in_seconds: Option<u64>,
}

/// Accept an invitation token and join its target.
///
/// Only a Caller with User identity authenticated by Gateway may accept. The
/// joining Human actor is derived from the User subject id and delegated to the
/// legacy `InviteService::join_*_by_invite`, mirroring the legacy Human-only
/// join path that creates a Human Participant (Consultant role, Present mode).
#[derive(Debug, Clone)]
pub struct AcceptInvitation {
    pub caller: AuthenticatedCaller,
    pub token: String,
}

/// Transport-independent invitation use cases for BCN OpenAPI v1.
///
/// Delivery adapters translate HTTP requests into these commands. The trait is
/// object-safe so an `Arc<dyn InvitationService>` can be shared across routes.
#[async_trait]
pub trait InvitationService: Send + Sync {
    async fn create_group_invitation(
        &self,
        command: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError>;

    async fn create_session_invitation(
        &self,
        command: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError>;

    async fn accept_invitation(
        &self,
        command: AcceptInvitation,
    ) -> Result<InvitationAcceptResult, ApplicationError>;
}
