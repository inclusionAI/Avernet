//! Versioned Invitation + Friendship application facade for the BCN V1 API.
//!
//! Implements both [`InvitationService`] and [`FriendshipService`]. The facade
//! owns Caller-based resource authorization and V1 projections while
//! delegating friendship/friend-request side effects to the legacy
//! [`FriendService`] use-case facade (itself converged onto the edge-permission
//! connect use case), friendship listing to [`FriendCoreService`], and
//! invitation accept-join side effects to the legacy [`InviteService`]
//! (`join_group_by_invite` / `join_session_by_invite`). No HTTP type crosses
//! this boundary.
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

mod invitations;

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{
    edge_permission::{PermissionRequest, RequestStatus as EdgeRequestStatus},
    invite_token_decode_and_verify, invite_token_encode, ActorKind, FriendRequestStatus,
    InviteTargetType, InviteTokenError, InviteTokenPayload,
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
use bcs_service_api::port::bot_authority::BotAuthorityPort;
use bcs_service_api::{
    application::{ConnectService, ConnectStatus, RequestDirection},
    BotRegistryCoreService, CreateFriendRequestCommand, FriendCoreService,
    FriendRequest as DomainFriendRequest, FriendRequestDecisionCommand,
    FriendRequestDirection as DomainFriendRequestDirection, FriendService, FriendUseCaseError,
    Friendship as DomainFriendship, Group as DomainGroup, GroupCoreService, GroupKind,
    GroupStatus, GroupStrategy, InviteService, InviteUseCaseError, JoinByInviteCommand,
    ListFriendRequestsCommand, ParticipantRole, RegisteredBot, ServiceError,
    SessionManagementService, SessionUseCaseError,
};

/// Map one authority read to application errors: a strict
/// deny (`Forbidden`) is 403; every other read failure
/// propagates as an internal error (spec §3.2: 存储错误
/// 返回错误 — never a deny-as-false).
pub(crate) fn map_authority_error(error: ServiceError) -> ApplicationError {
    match error {
        ServiceError::Forbidden(message) => ApplicationError::forbidden(message),
        other => ApplicationError::internal(format!(
            "bot authority lookup failed: {other}"
        )),
    }
}

#[derive(Debug, Clone)]
pub struct InvitationFriendshipServiceConfig {
    /// Default invitation token lifetime in seconds when the caller does not
    /// supply `expires_in_seconds`.
    pub default_ttl_seconds: u64,
}

/// OpenAPI v1 Invitation + Friendship facade.
///
/// Holds the legacy friend use-case facade (friend-request mutations/reads),
/// the `FriendCoreService` friendship listing, invitation token
/// mint/verify (via the shared `bcs_domain` HMAC helpers and `token_secret`),
/// and Human-only invitation accept-join. `GroupManagementService` is
/// intentionally absent: V1 accept delegates to the legacy `InviteService`
/// `join_*_by_invite`, which routes through `GroupCoreService` /
/// `SessionManagementService` directly (see module docs).
pub struct InvitationFriendshipServiceImpl {
    friends: Arc<dyn FriendCoreService>,
    /// Legacy friend use-case facade. It is converged onto the edge-permission
    /// connect use case, so every friend-request mutation and read below runs
    /// through the same owner-notification/edge lifecycle as the new friend
    /// APIs.
    legacy_friend: Arc<dyn FriendService>,
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    registry: Arc<dyn BotRegistryCoreService>,
    invite: Arc<dyn InviteService>,
    connect: Option<Arc<dyn ConnectService>>,
    /// The narrow authority hook (bot-authority plan Task
    /// 6.3): every Human→Bot control judgment consults it
    /// live; a missing wiring fails closed.
    bot_authority: Option<Arc<dyn BotAuthorityPort>>,
    session_snapshots: Option<Arc<dyn bcs_service_api::core::SessionAuthorizationCoreService>>,
    token_secret: Vec<u8>,
    config: InvitationFriendshipServiceConfig,
}

impl InvitationFriendshipServiceImpl {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        friends: Arc<dyn FriendCoreService>,
        legacy_friend: Arc<dyn FriendService>,
        groups: Arc<dyn GroupCoreService>,
        sessions: Arc<dyn SessionManagementService>,
        registry: Arc<dyn BotRegistryCoreService>,
        invite: Arc<dyn InviteService>,
        token_secret: Vec<u8>,
        config: InvitationFriendshipServiceConfig,
    ) -> Self {
        Self {
            friends,
            legacy_friend,
            groups,
            sessions,
            registry,
            invite,
            connect: None,
            bot_authority: None,
            session_snapshots: None,
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

    /// Wire the authority hook (bot-authority plan Task 6.3).
    pub fn with_bot_authority_hook(
        mut self,
        bot_authority: Arc<dyn BotAuthorityPort>,
    ) -> Self {
        self.bot_authority = Some(bot_authority);
        self
    }

    /// The wired authority hook; failing to wire one is a
    /// composition error and must fail closed (never deny-as-Ok).
    fn authority(&self) -> Result<&dyn BotAuthorityPort, ApplicationError> {
        self.bot_authority
            .as_deref()
            .ok_or_else(|| {
                ApplicationError::internal(
                    "bot authority hook is not wired",
                )
            })
    }

    /// Row 11 Human→Bot control leg: a live `can_manage`
    /// edge; the historical `created_by` no longer
    /// authorizes anything here.
    async fn human_controls_bot(
        &self,
        user_id: &str,
        bot_id: &str,
    ) -> Result<bool, ApplicationError> {
        self.authority()?
            .can_manage(user_id, bot_id)
            .await
            .map_err(map_authority_error)
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

    /// Row 11: the authenticated User may manage the Bot
    /// through its CURRENT `can_manage` edge; the
    /// historical `created_by` no longer authorizes
    /// (bot-authority plan Task 6.3, spec §3.2/§3.3).
    async fn authorize_bot_resource(
        &self,
        caller: &bcs_service_api::application::v1::AuthenticatedCaller,
        bot_uuid: &str,
    ) -> Result<(), ApplicationError> {
        let user = require_authenticated_user(caller)?;
        self.load_bot(bot_uuid).await?;
        let controls = self
            .human_controls_bot(&user.id, bot_uuid)
            .await?;
        if controls {
            Ok(())
        } else {
            Err(ApplicationError::forbidden(format!(
                "Authenticated User cannot manage Bot '{bot_uuid}'"
            )))
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

    /// Rebuild the V1 projection after a friend-request decision.
    ///
    /// The legacy `accept/reject_friend_request` commands answer with no row,
    /// so read the decided request from the connect store by id. The legacy
    /// facade listing cannot serve this: its `created_by == from_id`
    /// projection drops the Bot↔Bot reverse pending row, yet deciding that
    /// reverse row is legal and the mutation already happened. A missing row
    /// is a server inconsistency after a successful decision, so it maps to an
    /// internal error (never a 404).
    async fn decided_friend_request(
        &self,
        receiver: &str,
        request_id: &str,
    ) -> Result<FriendRequest, ApplicationError> {
        let request = self
            .connect_service()?
            .get_request(request_id)
            .await
            .map_err(|error| {
                ApplicationError::internal(format!(
                    "friend request '{request_id}' (receiver '{receiver}') is not readable from \
                     the connect store after the decision: {error}"
                ))
            })?;
        Ok(project_connect_friend_request(&request))
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
        // Friendship removal is owned by the edge-permission connect use case,
        // so the V1 delete cannot diverge from the friend edges the new
        // friend-connect APIs read. `deleted` stays "an edge was revoked".
        let revoked = self
            .connect_service()?
            .revoke_friend(&command.bot_uuid, &command.friend_bot_uuid, None)
            .await
            .map_err(map_service_error)?;
        Ok(DeleteResult {
            deleted: !revoked.is_empty(),
        })
    }

    async fn create_bot_friend_request(
        &self,
        command: CreateBotFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        self.ensure_bot_resource(&command.caller, &command.bot_uuid)
            .await?;
        let request = self
            .legacy_friend
            .create_friend_request(CreateFriendRequestCommand {
                caller_actor_id: command.bot_uuid,
                to_bot: command.to_bot_uuid,
                request_auth: command.request_auth.clone(),
            })
            .await
            .map_err(map_friend_use_case_error)?;
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
            .legacy_friend
            .list_friend_requests(ListFriendRequestsCommand {
                caller_actor_id: command.bot_uuid.clone(),
                direction,
                // V1 status is the domain vocabulary (`pending|accepted|
                // rejected`), and only an explicitly requested status is
                // pushed down to the facade.
                status_filter: command.status,
            })
            .await
            .map_err(map_friend_use_case_error)?;
        // The facade returns the matching rows without V1 ordering. Sort
        // `created_at` DESC with a `request_id` ASC tie-breaker, then apply
        // offset/limit so V1 pagination is stable. Listing failures propagate
        // (HTTP 500) instead of masking them as an empty 200 page.
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
        let to_bot = self
            .legacy_friend
            .friend_request_receiver(&command.request_id)
            .await
            .map_err(map_friend_use_case_error)?;
        // Only the receiver may accept; this also covers Human-owned bots via
        // `authorize_bot_resource`.
        self.ensure_bot_resource(&command.caller, &to_bot).await?;
        self.legacy_friend
            .accept_friend_request(FriendRequestDecisionCommand {
                caller_actor_id: to_bot.clone(),
                request_id: command.request_id.clone(),
                request_to_bot: Some(to_bot.clone()),
                request_auth: command.request_auth.clone(),
            })
            .await
            .map_err(map_friend_use_case_error)?;
        self.decided_friend_request(&to_bot, &command.request_id)
            .await
    }

    async fn reject_friend_request(
        &self,
        command: RejectFriendRequest,
    ) -> Result<FriendRequest, ApplicationError> {
        let to_bot = self
            .legacy_friend
            .friend_request_receiver(&command.request_id)
            .await
            .map_err(map_friend_use_case_error)?;
        self.ensure_bot_resource(&command.caller, &to_bot).await?;
        self.legacy_friend
            .reject_friend_request(FriendRequestDecisionCommand {
                caller_actor_id: to_bot.clone(),
                request_id: command.request_id.clone(),
                request_to_bot: Some(to_bot.clone()),
                request_auth: command.request_auth.clone(),
            })
            .await
            .map_err(map_friend_use_case_error)?;
        self.decided_friend_request(&to_bot, &command.request_id)
            .await
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

/// Project a decided connect request onto the V1 friend-request wire shape.
///
/// The connect vocabulary is `pending|approved|rejected|cancelled`; V1 speaks
/// `pending|accepted|rejected`, so `approved` maps to `accepted` and the
/// terminal `cancelled` state projects as `rejected` (mirroring the legacy
/// facade's status projection).
fn project_connect_friend_request(request: &PermissionRequest) -> FriendRequest {
    FriendRequest {
        request_id: request.request_id.clone(),
        from_bot_uuid: request.from_id.clone(),
        to_bot_uuid: request.to_id.clone(),
        status: match request.status {
            EdgeRequestStatus::Pending => FriendRequestStatus::Pending,
            EdgeRequestStatus::Approved => FriendRequestStatus::Accepted,
            EdgeRequestStatus::Rejected | EdgeRequestStatus::Cancelled => {
                FriendRequestStatus::Rejected
            }
        },
        message: None,
        created_at: request.created_at,
        updated_at: request.updated_at,
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

/// Map the legacy `FriendService` use-case error onto the V1 friendship
/// contract: a facade deny is a 403, everything else keeps the stable
/// `ServiceError` projection below.
fn map_friend_use_case_error(error: FriendUseCaseError) -> ApplicationError {
    match error {
        FriendUseCaseError::Forbidden(message) => ApplicationError::forbidden(message),
        FriendUseCaseError::Service(error) => map_service_error(error),
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
        // A hidden Bot and a Bot that refuses collaboration are both
        // authorization failures on the friend-request surface.
        hidden @ ServiceError::BotHidden(_) => ApplicationError::forbidden(hidden.to_string()),
        ServiceError::PrivateBotCannotCollaborate => {
            ApplicationError::forbidden(ServiceError::PrivateBotCannotCollaborate.to_string())
        }
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
                self.load_bot(&requested.id).await?;
                // Row 11: act-as-Bot through the CURRENT
                // `can_manage`; the historical `created_by`
                // no longer authorizes.
                if self
                    .human_controls_bot(&user.id, &requested.id)
                    .await?
                {
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
        request: &PermissionRequest,
    ) -> Result<String, ApplicationError> {
        let default_actor = Self::caller_default_actor(caller)?;
        if request.to_id == default_actor {
            return Ok(default_actor);
        }
        if let Some(user) = &caller.user {
            if !request.to_id.starts_with("human_") {
                self.load_bot(&request.to_id).await?;
                // Row 11: decide for a target Bot through the
                // CURRENT `can_manage`; the historical
                // `created_by` no longer authorizes.
                if self
                    .human_controls_bot(&user.id, &request.to_id)
                    .await?
                {
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
        request: &PermissionRequest,
    ) -> Result<(), ApplicationError> {
        let default_actor = Self::caller_default_actor(caller)?;
        if request.created_by == default_actor || request.from_id == default_actor {
            return Ok(());
        }
        if let Some(user) = &caller.user {
            if !request.from_id.starts_with("human_") {
                self.load_bot(&request.from_id).await?;
                // Row 11: cancel on behalf of a requesting Bot
                // through the CURRENT `can_manage`; the
                // historical `created_by` no longer
                // authorizes. The friend-request asset leg
                // above (`created_by == default_actor`) is
                // preserved (row 12).
                if self
                    .human_controls_bot(&user.id, &request.from_id)
                    .await?
                {
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
            .approve(&command.request_id, &decider, command.request_auth.clone())
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
            .cancel(&command.request_id, command.request_auth.clone())
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
        if command.page == 0 || command.page_size == 0 || command.page_size > 100 {
            return Err(ApplicationError::invalid(
                "invalid_request",
                "page must be at least 1 and page_size must be between 1 and 100",
            ));
        }
        let connect = self.connect_service()?;
        let actor = self
            .resolve_acting_actor(&command.caller, Some(&command.actor))
            .await?;
        let query = bcs_service_api::application::connect::FriendListQuery {
            target_type: command.target_type.map(|kind| match kind {
                FriendConnectionActorType::Human => ActorKind::Human,
                FriendConnectionActorType::Bot => ActorKind::Bot,
            }),
            offset: u64::from(command.page - 1) * u64::from(command.page_size),
            limit: command.page_size,
        };
        let result = connect.list_friends_paginated(&actor, query).await.map_err(map_service_error)?;
        let total = u32::try_from(result.total).map_err(|_| map_service_error(
            ServiceError::InternalError("friend count exceeds response range".into()),
        ))?;
        let items = result.items.iter().map(project_friend_connection).collect();
        Ok(FriendConnectionPage {
            items,
            page: command.page,
            page_size: command.page_size,
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
            .revoke_friend(&caller, &target, command.request_auth.clone())
            .await
            .map_err(map_service_error)?;
        Ok(DeleteResult {
            deleted: !revoked.is_empty(),
        })
    }
}

fn project_friend_connection_request(
    request: &PermissionRequest,
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
mod tests;
