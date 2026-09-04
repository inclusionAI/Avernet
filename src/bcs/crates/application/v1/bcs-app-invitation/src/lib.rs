//! Versioned Invitation + Friendship application facade for the BCN V1 API.
//!
//! Implements both [`InvitationService`] and [`FriendshipService`]. The facade
//! owns Caller-based resource authorization and V1 projections while
//! delegating friendship/friend-request side effects to the legacy
//! [`FriendCoreService`] / [`FriendRequestCoreService`] cores and invitation
//! accept-join side effects to the legacy [`InviteService`]
//! (`join_group_by_invite` / `join_session_by_invite`). No HTTP type crosses
//! this boundary.
//!
//! V1 invitation divergence from the legacy `InviteService`:
//! - Tokens are minted directly with `target_type: Some(Group|Session)` via
//!   `bcs_domain::invite_token_encode`, so the accept path can route without
//!   inspecting a join URL. Legacy tokens carry `target_type: None` and are
//!   rejected by V1 accept.
//! - V1 `create_*_invitation` mirrors the legacy DM/active-group guards but
//!   mints tokens directly (the legacy `create_*_invite_token` paths are not
//!   reused because they emit legacy join URLs).
//! - Accept pivots to the legacy Human-only join path. A Caller without User
//!   is rejected; the User's subject id is forwarded to
//!   `InviteService::join_*_by_invite`, which `ensure_human`s the actor and
//!   creates a Human Participant (Consultant role, Present mode). This matches
//!   the legacy invite-link accept semantics exactly.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{
    invite_token_decode_and_verify, invite_token_encode, ActorKind, InviteTargetType,
    InviteTokenError, InviteTokenPayload,
};
use bcs_service_api::application::v1::{
    friend_connection::{
        AcceptFriendConnectionRequest, CancelFriendConnectionRequest,
        CreateFriendConnectionRequest, DeleteFriendConnection, FriendConnectionActor,
        FriendConnectionActorType, FriendConnectionCreateResult, FriendConnectionCreateStatus,
        FriendConnectionPage, FriendConnectionRequestDirection, FriendConnectionRequestPage,
        FriendConnectionRequestStatus, FriendConnectionRequestView, FriendConnectionService,
        FriendConnectionView, ListFriendConnectionRequests, ListFriendConnections,
        RejectFriendConnectionRequest,
    },
    friendship::FriendRequestDirection,
    invitation::InvitationState,
    AcceptFriendRequest, AcceptInvitation, ApplicationError, CreateBotFriendRequest,
    CreateGroupInvitation, CreateSessionInvitation, DeleteBotFriendship, DeleteResult,
    FriendshipService, InvitationAcceptResult, InvitationService, InvitationTargetType,
    Invitation, ListBotFriendRequests, ListBotFriendships, Page, Principal,
    RejectFriendRequest, require_authenticated_user, require_human,
};
use bcs_service_api::{
    application::{ConnectService, ConnectStatus, RequestDirection},
    BotRegistryCoreService, FriendCoreService, FriendRequestCoreService,
    FriendRequest as DomainFriendRequest, FriendRequestDirection as DomainFriendRequestDirection,
    Friendship as DomainFriendship, Group as DomainGroup, GroupCoreService, GroupKind,
    GroupStatus, GroupStrategy, InviteService, InviteUseCaseError, JoinByInviteCommand,
    ParticipantRole, RegisteredBot, ServiceError, SessionManagementService, SessionUseCaseError,
};

#[derive(Debug, Clone)]
pub struct InvitationFriendshipServiceConfig {
    /// Default invitation token lifetime in seconds when the caller does not
    /// supply `expires_in_seconds`.
    pub default_ttl_seconds: u64,
}

/// OpenAPI v1 Invitation + Friendship facade.
///
/// Holds the legacy cores needed for friendship management, invitation token
/// mint/verify (via the shared `bcs_domain` HMAC helpers and `token_secret`),
/// and Human-only invitation accept-join. `GroupManagementService` is
/// intentionally absent: V1 accept delegates to the legacy `InviteService`
/// `join_*_by_invite`, which routes through `GroupCoreService` /
/// `SessionManagementService` directly (see module docs).
pub struct InvitationFriendshipServiceImpl {
    friends: Arc<dyn FriendCoreService>,
    friend_requests: Arc<dyn FriendRequestCoreService>,
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    registry: Arc<dyn BotRegistryCoreService>,
    invite: Arc<dyn InviteService>,
    connect: Option<Arc<dyn ConnectService>>,
    token_secret: Vec<u8>,
    config: InvitationFriendshipServiceConfig,
}

impl InvitationFriendshipServiceImpl {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        friends: Arc<dyn FriendCoreService>,
        friend_requests: Arc<dyn FriendRequestCoreService>,
        groups: Arc<dyn GroupCoreService>,
        sessions: Arc<dyn SessionManagementService>,
        registry: Arc<dyn BotRegistryCoreService>,
        invite: Arc<dyn InviteService>,
        token_secret: Vec<u8>,
        config: InvitationFriendshipServiceConfig,
    ) -> Self {
        Self {
            friends,
            friend_requests,
            groups,
            sessions,
            registry,
            invite,
            connect: None,
            token_secret,
            config,
        }
    }

    pub fn with_friend_connection_service(
        mut self,
        connect: Arc<dyn ConnectService>,
    ) -> Self {
        self.connect = Some(connect);
        self
    }

    fn connect_service(&self) -> Result<&Arc<dyn ConnectService>, ApplicationError> {
        self.connect.as_ref().ok_or_else(|| {
            ApplicationError::internal("friend connection service is not configured")
        })
    }

    // ── authorization helpers ──────────────────────────────────────────

    async fn load_bot(&self, bot_uuid: &str) -> Result<RegisteredBot, ApplicationError> {
        self.registry
            .try_get(bot_uuid)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "bot_not_found",
                    format!("Bot '{bot_uuid}' was not found"),
                )
            })
    }

    /// The authenticated User must own the Bot through exact `created_by`.
    async fn authorize_bot_resource(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        bot_uuid: &str,
    ) -> Result<(), ApplicationError> {
        let user = require_authenticated_user(caller)?;
        let bot = self.load_bot(bot_uuid).await?;
        if bot.created_by.as_deref() == Some(user.id.as_str()) {
            Ok(())
        } else {
            Err(ApplicationError::forbidden(format!(
                "Authenticated User cannot manage Bot '{bot_uuid}'"
            )))
        }
    }

    /// Manager of a group: driver, originator, or ManagerWorker manager.
    /// A Human may also act for an owned driver/originator Bot, preserving the
    /// legacy invitation authorization contract.
    async fn can_manage_group(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        let actor_id = principal.actor_id();
        if actor_id == group.driver_bot
            || actor_id == group.originator()
            || (group.group_strategy == GroupStrategy::ManagerWorker
                && group.participants.iter().any(|p| {
                    p.bot_uuid == actor_id && p.role == ParticipantRole::Manager
                }))
        {
            return Ok(true);
        }
        let Principal::Human(human) = principal else {
            return Ok(false);
        };
        let owned_bots = self
            .registry
            .try_list_bots_by_creator(&human.subject.id)
            .await
            .map_err(map_service_error)?;
        Ok(owned_bots.iter().any(|bot| {
            bot.actor_kind == ActorKind::Bot
                && (bot.bot_uuid == group.driver_bot || bot.bot_uuid == group.originator())
        }))
    }

    async fn load_manageable_group(
        &self,
        principal: &Principal,
        group_id: &str,
    ) -> Result<DomainGroup, ApplicationError> {
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{group_id}' was not found"),
                )
            })?;
        if !self.can_manage_group(principal, &group).await? {
            return Err(ApplicationError::forbidden(
                "Only the Group originator, driver, or manager may manage this Group",
            ));
        }
        Ok(group)
    }

    // ── invitation helpers ─────────────────────────────────────────────

    fn mint_invitation(
        &self,
        target_type: InvitationTargetType,
        target_id: &str,
        ttl_seconds: Option<u64>,
    ) -> Invitation {
        let now = now_secs();
        let exp = now.saturating_add(ttl_seconds.unwrap_or(self.config.default_ttl_seconds));
        let payload = InviteTokenPayload {
            v: 1,
            id: target_id.to_string(),
            exp,
            target_type: Some(map_v1_target_to_domain(target_type)),
        };
        let token = invite_token_encode(&payload, &self.token_secret);
        Invitation {
            token,
            target_type,
            target_id: target_id.to_string(),
            state: InvitationState::Pending,
            expires_at: Some(exp),
            created_at: now,
        }
    }

    // ── friendship projections ─────────────────────────────────────────

    async fn ensure_bot_resource(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        bot_uuid: &str,
    ) -> Result<(), ApplicationError> {
        self.authorize_bot_resource(caller, bot_uuid).await
    }
}

#[async_trait]
impl InvitationService for InvitationFriendshipServiceImpl {
    async fn create_group_invitation(
        &self,
        command: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let group = self
            .load_manageable_group(&principal, &command.group_id)
            .await?;
        // VaGQI: DM (DirectMessage) groups are pairwise (participant_count=2);
        // minting an invitation + accept would add a third participant. Mirror
        // the legacy invite service, which rejects DM groups with Forbidden.
        if group.group_kind == GroupKind::Dm {
            return Err(ApplicationError::forbidden(
                "Invitations are not available for direct-message groups",
            ));
        }
        // Vcj6P: legacy `create_group_invite_token` L151-153 rejects minting on
        // a non-active group ("group is not active"). Mirror it so V1 does not
        // hand out tokens for Completed/Closed/Error targets.
        if group.status != GroupStatus::Active {
            return Err(ApplicationError::conflict(
                "conflict",
                "group is not active",
            ));
        }
        Ok(self.mint_invitation(
            InvitationTargetType::Group,
            &command.group_id,
            command.expires_in_seconds,
        ))
    }

    async fn create_session_invitation(
        &self,
        command: CreateSessionInvitation,
    ) -> Result<Invitation, ApplicationError> {
        let principal = require_human(&command.caller)?;
        let session = self
            .sessions
            .get(&command.session_id)
            .await
            .map_err(map_session_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "session_not_found",
                    format!("Session '{}' was not found", command.session_id),
                )
            })?;
        let group = self
            .groups
            .try_get(&session.group_id)
            .await
            .map_err(map_service_error)?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "group_not_found",
                    format!("Group '{}' was not found", session.group_id),
                )
            })?;
        // Vcj6M: legacy `create_session_invite_token` L186-189 rejects DM parent
        // groups. Mirror it so session invitations on pairwise DM targets are
        // not minted. The legacy session path skips `session.status` (it never
        // checked session status); V1 follows the same precedent.
        if group.group_kind == GroupKind::Dm {
            return Err(ApplicationError::forbidden(
                "Invitations are not available for direct-message groups",
            ));
        }
        // Vcj6P: legacy `create_group_invite_token` L151-153 rejects non-active
        // groups. The session path's parent shares the same lifecycle as the
        // group, so mirror the inactive guard on the parent here too.
        if group.status != GroupStatus::Active {
            return Err(ApplicationError::conflict(
                "conflict",
                "group is not active",
            ));
        }
        // Minting is gated on session membership only: any participant of the
        // session (any role) may create an invitation for it. A Human caller
        // also qualifies through any owned Bot that participates in the
        // session. Group-level roles (driver, originator, manager) are
        // intentionally NOT required, mirroring the relaxed legacy
        // `create_session_invite_token`.
        let is_member = |actor_id: &str| {
            session
                .participants
                .iter()
                .any(|p| p.bot_uuid == actor_id)
        };
        if is_member(&principal.actor_id()) {
            return Ok(self.mint_invitation(
                InvitationTargetType::Session,
                &command.session_id,
                command.expires_in_seconds,
            ));
        }
        if let Principal::Human(human) = principal {
            let owned_bots = self
                .registry
                .try_list_bots_by_creator(&human.subject.id)
                .await
                .map_err(map_service_error)?;
            if owned_bots.iter().any(|bot| {
                bot.actor_kind == ActorKind::Bot && is_member(bot.bot_uuid.as_str())
            }) {
                return Ok(self.mint_invitation(
                    InvitationTargetType::Session,
                    &command.session_id,
                    command.expires_in_seconds,
                ));
            }
        }
        Err(ApplicationError::forbidden(
            "Only Session participants may create invitations for this Session",
        ))
    }

    async fn accept_invitation(
        &self,
        command: AcceptInvitation,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        let payload = invite_token_decode_and_verify(&command.token, &self.token_secret)
            .map_err(map_invite_token_error)?;
        let target_type = payload.target_type.ok_or_else(|| {
            ApplicationError::invalid(
                "invalid_request",
                "legacy invitation token without target_type is not supported by V1",
            )
        })?;
        let user = require_authenticated_user(&command.caller)?;
        let nick_name = user
            .display_name
            .clone()
            .filter(|value| !value.is_empty())
            .or_else(|| (!user.username.is_empty()).then(|| user.username.clone()));
        let join_command = JoinByInviteCommand {
            token: command.token.clone(),
            staff_no: user.id.clone(),
            nick_name,
        };
        let result = match target_type {
            InviteTargetType::Group => self.invite.join_group_by_invite(join_command).await,
            InviteTargetType::Session => {
                self.invite.join_session_by_invite(join_command).await
            }
        };
        let result = result.map_err(map_invite_use_case_error)?;
        let mapped_target_type = match target_type {
            InviteTargetType::Group => InvitationTargetType::Group,
            InviteTargetType::Session => InvitationTargetType::Session,
        };
        Ok(InvitationAcceptResult {
            target_type: mapped_target_type,
            target_id: result.target_id,
            joined: result.joined,
            // `already_member == !joined` from the legacy result; flip the
            // boolean to populate the V1 `already_joined` idempotency flag.
            already_joined: Some(!result.joined),
        })
    }
}

#[async_trait]
impl FriendshipService for InvitationFriendshipServiceImpl {
    async fn list_bot_friendships(
        &self,
        command: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        self.ensure_bot_resource(&command.caller, &command.bot_uuid)
            .await?;
        if command.limit == 0 || command.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        let (friendships, total) = self
            .friends
            .list_friendships_paginated(&command.bot_uuid, command.offset, command.limit)
            .await
            .map_err(map_service_error)?;
        let items = friendships.iter().map(project_friendship).collect();
        Ok(Page {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }

    async fn delete_bot_friendship(
        &self,
        command: DeleteBotFriendship,
    ) -> Result<DeleteResult, ApplicationError> {
        // The contract allows either endpoint of the friendship to initiate
        // deletion ("Principal cannot manage either friendship endpoint").
        // Try the primary bot_uuid first; fall back to the friend endpoint.
        match self
            .ensure_bot_resource(&command.caller, &command.bot_uuid)
            .await
        {
            Ok(()) => {}
            Err(_) => {
                self.ensure_bot_resource(&command.caller, &command.friend_bot_uuid)
                    .await?;
            }
        }
        let deleted = self
            .friends
            .remove_friendship(&command.bot_uuid, &command.friend_bot_uuid)
            .await
            .map_err(map_service_error)?;
        Ok(DeleteResult { deleted })
    }

    async fn create_bot_friend_request(
        &self,
        command: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        self.ensure_bot_resource(&command.caller, &command.bot_uuid)
            .await?;
        let request = self
            .friend_requests
            .create_request(&command.bot_uuid, &command.to_bot_uuid)
            .await
            .map_err(map_service_error)?;
        Ok(project_friend_request(&request))
    }

    async fn list_bot_friend_requests(
        &self,
        command: ListBotFriendRequests,
    ) -> Result<Page<FriendRequest>, ApplicationError> {
        self.ensure_bot_resource(&command.caller, &command.bot_uuid)
            .await?;
        if command.limit == 0 || command.limit > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "limit must be between 1 and 100",
            ));
        }
        let direction = match command.direction {
            FriendRequestDirection::Sent => DomainFriendRequestDirection::Sent,
            FriendRequestDirection::Received => DomainFriendRequestDirection::Received,
        };
        let mut requests = self
            .friend_requests
            .try_list_requests(&command.bot_uuid, direction, command.status)
            .await
            .map_err(map_service_error)?;
        // The repo returns all matches without ordering or pagination. Sort
        // `created_at` DESC with a `request_id` ASC tie-breaker, then apply
        // offset/limit so V1 pagination is stable. `try_list_requests`
        // propagates persistence failures (HTTP 500) instead of masking them
        // as an empty 200 page.
        requests.sort_by(|a, b| {
            b.created_at
                .cmp(&a.created_at)
                .then_with(|| a.id.cmp(&b.id))
        });
        let total = requests.len() as u64;
        let items = requests
            .iter()
            .skip(saturating_usize(command.offset))
            .take(saturating_usize(command.limit))
            .map(project_friend_request)
            .collect();
        Ok(Page {
            items,
            total,
            offset: command.offset,
            limit: command.limit,
        })
    }

    async fn accept_friend_request(
        &self,
        command: AcceptFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        let request = self
            .friend_requests
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        // Only the receiver may accept; this also covers Human-owned bots via
        // `authorize_bot_resource`.
        self.ensure_bot_resource(&command.caller, &request.to_bot)
            .await?;
        self.friend_requests
            .accept_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        let updated = self
            .friend_requests
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        Ok(project_friend_request(&updated))
    }

    async fn reject_friend_request(
        &self,
        command: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        let request = self
            .friend_requests
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        self.ensure_bot_resource(&command.caller, &request.to_bot)
            .await?;
        self.friend_requests
            .reject_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        let updated = self
            .friend_requests
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        Ok(project_friend_request(&updated))
    }
}

// V1 friendship types are imported unqualified via `application::v1`; the
// domain projections live under their aliased names so the two never clash.
use bcs_service_api::application::v1::{FriendRequest, Friendship};

// ── projection helpers ────────────────────────────────────────────────

fn project_friendship(friendship: &DomainFriendship) -> Friendship {
    Friendship {
        bot_uuid: friendship.bot_uuid.clone(),
        friend_bot_uuid: friendship.friend_bot_uuid.clone(),
        created_at: friendship.created_at,
    }
}

fn project_friend_request(request: &DomainFriendRequest) -> FriendRequest {
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

fn map_v1_target_to_domain(target: InvitationTargetType) -> InviteTargetType {
    match target {
        InvitationTargetType::Group => InviteTargetType::Group,
        InvitationTargetType::Session => InviteTargetType::Session,
    }
}

fn now_secs() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn saturating_usize(value: u64) -> usize {
    usize::try_from(value).unwrap_or(usize::MAX)
}

// ── error mappers ─────────────────────────────────────────────────────

fn map_invite_token_error(error: InviteTokenError) -> ApplicationError {
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
fn map_invite_use_case_error(error: InviteUseCaseError) -> ApplicationError {
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

fn map_session_error(error: SessionUseCaseError) -> ApplicationError {
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

fn map_service_error(error: ServiceError) -> ApplicationError {
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

// ── friend-connections (edge-permission) helpers ──────────────────────

impl InvitationFriendshipServiceImpl {
    fn caller_default_actor(
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
    ) -> Result<String, ApplicationError> {
        if let Some(bot) = &caller.bot {
            return Ok(bot.bot_uuid.clone());
        }
        let user = require_authenticated_user(caller)?;
        Ok(format!("human_{}", user.id))
    }

    fn actor_to_internal(actor: &FriendConnectionActor) -> String {
        match actor.actor_type {
            FriendConnectionActorType::Human => format!("human_{}", actor.id),
            FriendConnectionActorType::Bot => actor.id.clone(),
        }
    }

    fn actor_from_internal(actor_id: &str) -> FriendConnectionActor {
        if let Some(human_id) = actor_id.strip_prefix("human_") {
            FriendConnectionActor {
                actor_type: FriendConnectionActorType::Human,
                id: human_id.to_string(),
            }
        } else {
            FriendConnectionActor {
                actor_type: FriendConnectionActorType::Bot,
                id: actor_id.to_string(),
            }
        }
    }

    async fn resolve_acting_actor(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        requested: Option<&FriendConnectionActor>,
    ) -> Result<String, ApplicationError> {
        let default_actor = Self::caller_default_actor(caller)?;
        let Some(requested) = requested else {
            return Ok(default_actor);
        };
        let requested_id = Self::actor_to_internal(requested);
        if requested_id == default_actor {
            return Ok(requested_id);
        }
        if requested.actor_type == FriendConnectionActorType::Bot {
            if let Some(user) = &caller.user {
                let bot = self.load_bot(&requested.id).await?;
                if bot.created_by.as_deref() == Some(user.id.as_str()) {
                    return Ok(requested_id);
                }
            }
        }
        Err(ApplicationError::forbidden(format!(
            "Authenticated principal cannot act as actor '{requested_id}'"
        )))
    }

    async fn resolve_request_decider(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        request: &bcs_domain::edge_permission::PermissionRequest,
    ) -> Result<String, ApplicationError> {
        let default_actor = Self::caller_default_actor(caller)?;
        if request.to_id == default_actor {
            return Ok(default_actor);
        }
        if let Some(user) = &caller.user {
            if !request.to_id.starts_with("human_") {
                let bot = self.load_bot(&request.to_id).await?;
                if bot.created_by.as_deref() == Some(user.id.as_str()) {
                    return Ok(request.to_id.clone());
                }
            }
        }
        Err(ApplicationError::forbidden(format!(
            "Authenticated principal cannot decide request '{}'",
            request.request_id
        )))
    }

    async fn ensure_can_cancel_request(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        request: &bcs_domain::edge_permission::PermissionRequest,
    ) -> Result<(), ApplicationError> {
        let default_actor = Self::caller_default_actor(caller)?;
        if request.created_by == default_actor || request.from_id == default_actor {
            return Ok(());
        }
        if let Some(user) = &caller.user {
            if !request.from_id.starts_with("human_") {
                let bot = self.load_bot(&request.from_id).await?;
                if bot.created_by.as_deref() == Some(user.id.as_str()) {
                    return Ok(());
                }
            }
        }
        Err(ApplicationError::forbidden(format!(
            "Authenticated principal cannot cancel request '{}'",
            request.request_id
        )))
    }
}

#[async_trait]
impl FriendConnectionService for InvitationFriendshipServiceImpl {
    async fn create_friend_connection_request(
        &self,
        command: CreateFriendConnectionRequest,
    ) -> Result<FriendConnectionCreateResult, ApplicationError> {
        let connect = self.connect_service()?;
        let from = self
            .resolve_acting_actor(&command.caller, command.from_actor.as_ref())
            .await?;
        if command.to_actor.actor_type != FriendConnectionActorType::Bot {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "to_actor.type must be bot for friend connection requests",
            ));
        }
        let result = connect
            .create_connect(
                &from,
                &command.to_actor.id,
                command.message,
                command.request_auth.clone(),
            )
            .await
            .map_err(map_service_error)?;
        let status = match result.status {
            ConnectStatus::Pending => FriendConnectionCreateStatus::Pending,
            ConnectStatus::Approved => FriendConnectionCreateStatus::Approved,
            ConnectStatus::PublicNoEdge => FriendConnectionCreateStatus::PublicNoEdge,
        };
        Ok(FriendConnectionCreateResult {
            request_ids: result.request_ids,
            edge_ids: result.edge_ids,
            status,
            auto_accepted: result.auto_accepted,
        })
    }

    async fn list_friend_connection_requests(
        &self,
        command: ListFriendConnectionRequests,
    ) -> Result<FriendConnectionRequestPage, ApplicationError> {
        if command.page_size == 0 || command.page_size > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "page_size must be between 1 and 100",
            ));
        }
        let connect = self.connect_service()?;
        let actor = self
            .resolve_acting_actor(&command.caller, command.actor.as_ref())
            .await?;
        let direction = match command.direction {
            FriendConnectionRequestDirection::Received => RequestDirection::Received,
            FriendConnectionRequestDirection::Sent => RequestDirection::Sent,
            FriendConnectionRequestDirection::All => RequestDirection::All,
        };
        let status = command.status.map(|status| match status {
            FriendConnectionRequestStatus::Pending => {
                bcs_domain::edge_permission::RequestStatus::Pending
            }
            FriendConnectionRequestStatus::Approved => {
                bcs_domain::edge_permission::RequestStatus::Approved
            }
            FriendConnectionRequestStatus::Rejected => {
                bcs_domain::edge_permission::RequestStatus::Rejected
            }
            FriendConnectionRequestStatus::Cancelled => {
                bcs_domain::edge_permission::RequestStatus::Cancelled
            }
        });
        let page = connect
            .list_requests(&actor, direction, status, command.page, command.page_size)
            .await
            .map_err(map_service_error)?;
        Ok(FriendConnectionRequestPage {
            items: page.items.iter().map(project_friend_connection_request).collect(),
            total: page.total,
            page: page.page,
            page_size: page.page_size,
        })
    }

    async fn accept_friend_connection_request(
        &self,
        command: AcceptFriendConnectionRequest,
    ) -> Result<FriendConnectionRequestView, ApplicationError> {
        let connect = self.connect_service()?;
        let request = connect
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        let decider = self.resolve_request_decider(&command.caller, &request).await?;
        connect
            .approve(&command.request_id, &decider)
            .await
            .map_err(map_service_error)?;
        let updated = connect
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        Ok(project_friend_connection_request(&updated))
    }

    async fn reject_friend_connection_request(
        &self,
        command: RejectFriendConnectionRequest,
    ) -> Result<FriendConnectionRequestView, ApplicationError> {
        let connect = self.connect_service()?;
        let request = connect
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        let decider = self.resolve_request_decider(&command.caller, &request).await?;
        connect
            .reject(&command.request_id, &decider, command.reason)
            .await
            .map_err(map_service_error)?;
        let updated = connect
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        Ok(project_friend_connection_request(&updated))
    }

    async fn cancel_friend_connection_request(
        &self,
        command: CancelFriendConnectionRequest,
    ) -> Result<FriendConnectionRequestView, ApplicationError> {
        let connect = self.connect_service()?;
        let request = connect
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        self.ensure_can_cancel_request(&command.caller, &request).await?;
        connect
            .cancel(&command.request_id)
            .await
            .map_err(map_service_error)?;
        let updated = connect
            .get_request(&command.request_id)
            .await
            .map_err(map_service_error)?;
        Ok(project_friend_connection_request(&updated))
    }

    async fn list_friend_connections(
        &self,
        command: ListFriendConnections,
    ) -> Result<FriendConnectionPage, ApplicationError> {
        let connect = self.connect_service()?;
        let actor = self
            .resolve_acting_actor(&command.caller, Some(&command.actor))
            .await?;
        let items = connect
            .list_friends(&actor)
            .await
            .map_err(map_service_error)?;
        let total = items.len() as u32;
        Ok(FriendConnectionPage {
            items: items.iter().map(project_friend_connection).collect(),
            total,
        })
    }

    async fn delete_friend_connection(
        &self,
        command: DeleteFriendConnection,
    ) -> Result<DeleteResult, ApplicationError> {
        let connect = self.connect_service()?;
        let caller = Self::caller_default_actor(&command.caller)?;
        let target = Self::actor_to_internal(&command.target_actor);
        let revoked = connect
            .revoke_friend(&caller, &target)
            .await
            .map_err(map_service_error)?;
        Ok(DeleteResult {
            deleted: !revoked.is_empty(),
        })
    }
}

fn project_friend_connection_request(
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

fn project_friend_connection(
    entry: &bcs_domain::edge_permission::FriendListEntry,
) -> FriendConnectionView {
    FriendConnectionView {
        actor: match entry.kind {
            ActorKind::Human => InvitationFriendshipServiceImpl::actor_from_internal(&entry.actor_id),
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn invite_token_errors_map_to_stable_v1_codes() {
        assert_eq!(
            map_invite_token_error(InviteTokenError::Expired).code(),
            "invitation_expired"
        );
        assert_eq!(
            map_invite_token_error(InviteTokenError::InvalidEncoding).code(),
            "invalid_request"
        );
        assert_eq!(
            map_invite_token_error(InviteTokenError::InvalidSignature).code(),
            "invalid_request"
        );
        assert_eq!(
            map_invite_token_error(InviteTokenError::UnsupportedVersion).code(),
            "invalid_request"
        );
        assert_eq!(
            map_invite_token_error(InviteTokenError::MalformedPayload("bad".into())).code(),
            "invalid_request"
        );
    }

    #[test]
    fn service_errors_map_to_stable_v1_codes() {
        assert_eq!(
            map_service_error(ServiceError::FriendRequestNotFound("r1".into())).code(),
            "friend_request_not_found"
        );
        assert_eq!(
            map_service_error(ServiceError::CannotAddSelf).code(),
            "cannot_add_self"
        );
        assert_eq!(
            map_service_error(ServiceError::PendingRequestExists {
                request_id: "r2".into(),
                from_bot: None,
                to_bot: None,
            })
            .code(),
            "friend_request_already_exists"
        );
        assert_eq!(
            map_service_error(ServiceError::CannotAcceptRejected).code(),
            "conflict"
        );
        assert_eq!(
            map_service_error(ServiceError::CannotRejectAccepted).code(),
            "conflict"
        );
        assert_eq!(
            map_service_error(ServiceError::Conflict("dup".into())).code(),
            "conflict"
        );
    }
}
