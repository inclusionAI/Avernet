//! Versioned Invitation + Friendship application facade for the BCN V1 API.
//!
//! Implements both [`InvitationService`] and [`FriendshipService`] plus the
//! v1 [`FriendConnectionService`]. The facade owns Caller-based resource
//! authorization and V1 projections while delegating friendship/friend-request
//! side effects to the legacy [`FriendCoreService`] / [`FriendRequestCoreService`]
//! cores, invitation accept-join side effects to the legacy [`InviteService`]
//! (`join_group_by_invite` / `join_session_by_invite`), and the friend-connect
//! lifecycle to the application [`ConnectService`]. No HTTP type crosses this
//! boundary.
//!
//! Module layout (plan Task 12): [`authorization`] owns the struct + ctor
//! wiring and the acting-actor/manage-resource authorization surface (live
//! [`BotAuthorityHook`] role facts + §12.5 audit-context construction),
//! [`queries`] the read use cases, [`mutations`] the write use cases
//! (FriendshipService/InvitationService/FriendConnectionService trait impls
//! calling through the helper surface), and [`tests`] the pure mapping tests.
//!
//! V1 invitation divergence from the legacy `InviteService`:
//! - Tokens are minted directly with `target_type: Some(Group|Session)` via
//!   `bcs_domain::invite_token_encode`, so the accept path can route without
//!   inspecting a join URL. The legacy `bcs-http` invite routes also mint
//!   typed tokens now, so their links are accepted here; only pre-field
//!   tokens (`target_type: None`) remain rejected by V1 accept.
//! - V1 `create_*_invitation` mirrors the legacy DM/active-group guards but
//!   mints tokens directly (the legacy `create_*_invite_token` paths are not
//!   reused because they emit legacy join URLs).
//! - Accept pivots to the legacy Human-only join path. A Caller without User
//!   is rejected; the User's subject id is forwarded to
//!   `InviteService::join_*_by_invite`, which `ensure_human`s the actor and
//!   creates a Human Participant (Consultant role, Present mode). This matches
//!   the legacy invite-link accept semantics exactly.
//!
//! Friend authorization (plan Task 12): a former creator whose `created_by`
//! still matches but who holds no current owner/manager role is DENIED here —
//! the live authority facts are the only permission source (spec §12.2/§12.4),
//! and group management acting reaches through bots the caller currently owns
//! or manages, never through a historical creation list.

mod authorization;
mod mutations;
mod queries;

#[cfg(test)]
mod tests;

pub use authorization::{InvitationFriendshipServiceConfig, InvitationFriendshipServiceImpl};

use std::sync::Arc;

use bcs_domain::{
    invite_token_decode_and_verify, invite_token_encode, InviteTargetType,
    InviteTokenError, InviteTokenPayload,
};
use bcs_service_api::application::v1::friendship::{FriendRequest, Friendship};
use bcs_service_api::application::v1::{
    ApplicationError, Invitation, InvitationState, InvitationTargetType,
};
use bcs_service_api::{
    ActorKind,
    FriendRequest as DomainFriendRequest, Friendship as DomainFriendship,
    Group as DomainGroup, InviteService, InviteUseCaseError, JoinByInviteCommand,
    ServiceError, SessionManagementService,
};
use bcs_service_api::application::session::SessionUseCaseError;
use bcs_service_api::application::v1::friend_connection::{
    FriendConnectionActor, FriendConnectionActorType, FriendConnectionRequestStatus,
    FriendConnectionRequestView, FriendConnectionView,
};

// ── projection helpers ────────────────────────────────────────────────

pub(crate) fn project_friendship(friendship: &DomainFriendship) -> Friendship {
    Friendship {
        bot_uuid: friendship.bot_uuid.clone(),
        friend_bot_uuid: friendship.friend_bot_uuid.clone(),
        created_at: friendship.created_at,
    }
}

pub(crate) fn project_friend_request(request: &DomainFriendRequest) -> FriendRequest {
    FriendRequest {
        request_id: request.id.clone(),
        from_bot_uuid: request.from_bot.clone(),
        to_bot_uuid: request.to_bot.clone(),
        status: request.status.clone(),
        message: None,
        created_at: request.created_at,
        updated_at: request.updated_at,
    }
}

pub(crate) fn project_friend_connection_request(
    request: &bcs_domain::edge_permission::PermissionRequest,
) -> FriendConnectionRequestView {
    FriendConnectionRequestView {
        request_id: request.request_id.clone(),
        edge_id: request.edge_id.clone(),
        from_actor: InvitationFriendshipServiceImpl::actor_from_internal(&request.from_id),
        to_actor: InvitationFriendshipServiceImpl::actor_from_internal(&request.to_id),
        message: request.message.clone(),
        status: match request.status {
            bcs_domain::edge_permission::RequestStatus::Pending => {
                FriendConnectionRequestStatus::Pending
            }
            bcs_domain::edge_permission::RequestStatus::Approved => {
                FriendConnectionRequestStatus::Approved
            }
            bcs_domain::edge_permission::RequestStatus::Rejected => {
                FriendConnectionRequestStatus::Rejected
            }
            bcs_domain::edge_permission::RequestStatus::Cancelled => {
                FriendConnectionRequestStatus::Cancelled
            }
        },
        decision_reason: request.decision_reason.clone(),
        created_by: InvitationFriendshipServiceImpl::actor_from_internal(&request.created_by),
        decided_by: request
            .decided_by
            .as_deref()
            .map(InvitationFriendshipServiceImpl::actor_from_internal),
        decided_at: request.decided_at,
    }
}

pub(crate) fn project_friend_connection(
    entry: &bcs_domain::edge_permission::FriendListEntry,
) -> FriendConnectionView {
    FriendConnectionView {
        actor: match entry.kind {
            ActorKind::Human => {
                InvitationFriendshipServiceImpl::actor_from_internal(&entry.actor_id)
            }
            ActorKind::Bot => FriendConnectionActor {
                actor_type: FriendConnectionActorType::Bot,
                id: entry.actor_id.clone(),
            },
        },
        name: entry.name.clone(),
        summary: entry.summary.clone(),
        is_online: entry.is_online,
    }
}

pub(crate) fn map_v1_target_to_domain(target: InvitationTargetType) -> InviteTargetType {
    match target {
        InvitationTargetType::Group => InviteTargetType::Group,
        InvitationTargetType::Session => InviteTargetType::Session,
    }
}

pub(crate) fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

pub(crate) fn saturating_usize(value: u64) -> usize {
    usize::try_from(value).unwrap_or(usize::MAX)
}

// ── error mappers ─────────────────────────────────────────────────────

pub(crate) fn map_invite_token_error(error: InviteTokenError) -> ApplicationError {
    match error {
        InviteTokenError::Expired => ApplicationError::Gone {
            code: "invitation_expired".to_string(),
            message: "invitation link has expired".to_string(),
        },
        InviteTokenError::InvalidEncoding | InviteTokenError::InvalidSignature => {
            ApplicationError::invalid("invalid_request", "invalid invitation token")
        }
        InviteTokenError::UnsupportedVersion => {
            ApplicationError::invalid("invalid_request", "unsupported invitation token version")
        }
        InviteTokenError::MalformedPayload(message) => {
            ApplicationError::invalid("invalid_request", format!("malformed invitation token: {message}"))
        }
    }
}

/// Map legacy `InviteService::join_*_by_invite` errors onto the V1
/// `acceptInvitation` contract surface. The contract declares `forbidden`
/// (403), `invitation_not_found` (404), `conflict` (409), and
/// `invitation_expired` (410); the join path may surface any of these via
/// `InviteUseCaseError`. `InvalidToken` maps to `invalid_request` (400) so a
/// malformed token is still rejected at the contract boundary. `LoginRequired`
/// and `Service` collapse to `internal_error` (500) — they are not part of the
/// V1 accept contract surface.
pub(crate) fn map_invite_use_case_error(error: InviteUseCaseError) -> ApplicationError {
    match error {
        InviteUseCaseError::Forbidden(message) => ApplicationError::forbidden(message),
        InviteUseCaseError::NotFound(target) => ApplicationError::not_found(
            "invitation_not_found",
            format!("Invitation target '{target}' was not found"),
        ),
        InviteUseCaseError::Conflict(message) => {
            ApplicationError::conflict("conflict", message)
        }
        InviteUseCaseError::Expired => ApplicationError::Gone {
            code: "invitation_expired".to_string(),
            message: "invitation link has expired".to_string(),
        },
        InviteUseCaseError::InvalidToken(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        InviteUseCaseError::LoginRequired => {
            ApplicationError::internal("login required to accept invitation")
        }
        InviteUseCaseError::Service(error) => map_service_error(error),
    }
}

pub(crate) fn map_session_error(error: SessionUseCaseError) -> ApplicationError {
    match error {
        SessionUseCaseError::NotFound(sid) => ApplicationError::not_found(
            "session_not_found",
            format!("Session '{sid}' was not found"),
        ),
        SessionUseCaseError::InvalidParams(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        SessionUseCaseError::CallbackPending(message) => {
            ApplicationError::conflict("conflict", message)
        }
        SessionUseCaseError::Conflict(message) => ApplicationError::conflict("conflict", message),
        SessionUseCaseError::Internal(service_error) => map_service_error(service_error),
    }
}

pub(crate) fn map_service_error(error: ServiceError) -> ApplicationError {
    match error {
        ServiceError::GroupNotFound(id) => {
            ApplicationError::not_found("group_not_found", format!("Group '{id}' was not found"))
        }
        ServiceError::SessionNotFound(id) => ApplicationError::not_found(
            "session_not_found",
            format!("Session '{id}' was not found"),
        ),
        ServiceError::BotNotFound(id) | ServiceError::BotNotRegistered(id) => {
            ApplicationError::not_found("bot_not_found", format!("Bot '{id}' was not found"))
        }
        ServiceError::ParticipantNotFound(id) => ApplicationError::not_found(
            "participant_not_found",
            format!("Participant '{id}' was not found"),
        ),
        ServiceError::FriendRequestNotFound(id) => ApplicationError::not_found(
            "friend_request_not_found",
            format!("Friend request '{id}' was not found"),
        ),
        ServiceError::CannotAddSelf => {
            ApplicationError::invalid("cannot_add_self", "cannot add yourself as a friend")
        }
        ServiceError::PendingRequestExists { .. } => ApplicationError::conflict(
            "friend_request_already_exists",
            "a pending friend request already exists",
        ),
        ServiceError::CannotAcceptRejected => ApplicationError::conflict(
            "conflict",
            "cannot accept a rejected friend request",
        ),
        ServiceError::CannotRejectAccepted => ApplicationError::conflict(
            "conflict",
            "cannot reject an accepted friend request",
        ),
        ServiceError::Unauthorized(_) => ApplicationError::Unauthenticated,
        ServiceError::Forbidden(message) => ApplicationError::forbidden(message),
        ServiceError::Conflict(message) => ApplicationError::conflict("conflict", message),
        ServiceError::InvalidOperation { message, .. }
        | ServiceError::SessionInvalidParams(message) => {
            ApplicationError::invalid("invalid_request", message)
        }
        other => ApplicationError::internal(other.to_string()),
    }
}

