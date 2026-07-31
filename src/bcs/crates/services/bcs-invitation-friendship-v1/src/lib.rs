//! Versioned Invitation + Friendship application facade for the BCN V1 API.
//!
//! Implements both [`InvitationService`] and [`FriendshipService`]. The facade
//! owns Principal-based resource authorization and V1 projections while
//! delegating friendship/friend-request side effects to the legacy
//! [`FriendCoreService`] / [`FriendRequestCoreService`] cores and invitation
//! join side effects to the legacy [`GroupCoreService`] /
//! [`SessionManagementService`] cores. No HTTP type crosses this boundary.
//!
//! V1 invitation divergence from the legacy `InviteService`:
//! - Tokens are minted directly with `target_type: Some(Group|Session)` via
//!   `bcs_domain::invite_token_encode`, so the accept path can route without
//!   inspecting a join URL. Legacy tokens carry `target_type: None` and are
//!   rejected by V1 accept.
//! - Accept joins a **Bot** participant (Consultant role) rather than a Human.
//!   The join is authorized by the invitation token itself (minted by a group
//!   manager), so it bypasses legacy `GroupManagementService::add_member`
//!   manager-authorization and writes via `GroupCoreService::add_participant`
//!   directly, mirroring the legacy `join_*_by_invite` core path.

use std::sync::Arc;

use async_trait::async_trait;
use bcs_domain::{
    invite_token_decode_and_verify, invite_token_encode, InviteTargetType, InviteTokenError,
    InviteTokenPayload,
};
use bcs_service_api::application::v1::{
    friendship::FriendRequestDirection,
    invitation::InvitationState,
    AcceptFriendRequest, AcceptInvitation, ApplicationError, CreateBotFriendRequest,
    CreateGroupInvitation, CreateSessionInvitation, DeleteBotFriendship, DeleteResult,
    FriendshipService, InvitationAcceptResult, InvitationService, InvitationTargetType,
    Invitation, ListBotFriendRequests, ListBotFriendships, Page, Principal,
    RejectFriendRequest,
};
use bcs_service_api::{
    BotRegistryCoreService, FriendCoreService, FriendRequestCoreService,
    FriendRequest as DomainFriendRequest, FriendRequestDirection as DomainFriendRequestDirection,
    Friendship as DomainFriendship, Group as DomainGroup, GroupCoreService, GroupStrategy,
    Participant, ParticipantRole, RegisteredBot, RelationCoreService, ServiceError,
    SessionManagementService, SessionUseCaseError,
};

#[derive(Debug, Clone)]
pub struct InvitationFriendshipServiceConfig {
    /// Relation environment tag used for creator-edge authorization, mirroring
    /// the sibling Group V1 facade's `relation_env`.
    pub relation_env: String,
    /// Default invitation token lifetime in seconds when the caller does not
    /// supply `expires_in_seconds`.
    pub default_ttl_seconds: u64,
}

/// OpenAPI v1 Invitation + Friendship facade.
///
/// Holds the legacy cores needed for friendship management, invitation token
/// mint/verify (via the shared `bcs_domain` HMAC helpers and `token_secret`),
/// and Bot-participant joins. `GroupManagementService` is intentionally absent:
/// V1 accept-join is token-authorized and routes through `GroupCoreService` /
/// `SessionManagementService` directly (see module docs).
pub struct InvitationFriendshipServiceImpl {
    friends: Arc<dyn FriendCoreService>,
    friend_requests: Arc<dyn FriendRequestCoreService>,
    groups: Arc<dyn GroupCoreService>,
    sessions: Arc<dyn SessionManagementService>,
    registry: Arc<dyn BotRegistryCoreService>,
    relation: Arc<dyn RelationCoreService>,
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
        relation: Arc<dyn RelationCoreService>,
        token_secret: Vec<u8>,
        config: InvitationFriendshipServiceConfig,
    ) -> Self {
        Self {
            friends,
            friend_requests,
            groups,
            sessions,
            registry,
            relation,
            token_secret,
            config,
        }
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

    /// Principal must be the Bot itself or a Human that owns it (via
    /// `created_by` or a creator relation edge). Mirrors
    /// `bcs-group-v1::authorize_bot_resource`.
    async fn authorize_bot_resource(
        &self,
        principal: &Principal,
        bot_uuid: &str,
    ) -> Result<(), ApplicationError> {
        match principal {
            Principal::Bot(bot) if bot.bot_uuid == bot_uuid => {
                self.load_bot(bot_uuid).await?;
                Ok(())
            }
            Principal::Bot(_) => Err(ApplicationError::forbidden(
                "Bot Principal may act only on its own bot_uuid",
            )),
            Principal::Human(human) => {
                let bot = self.load_bot(bot_uuid).await?;
                if bot.created_by.as_deref() == Some(human.subject.id.as_str()) {
                    return Ok(());
                }
                let creator_edge = self
                    .relation
                    .get_edge(
                        &principal.actor_id(),
                        bot_uuid,
                        &self.config.relation_env,
                    )
                    .await
                    .map_err(map_service_error)?;
                if creator_edge.is_some_and(|edge| edge.is_creator) {
                    return Ok(());
                }
                Err(ApplicationError::forbidden(format!(
                    "Human Principal cannot manage Bot '{bot_uuid}'"
                )))
            }
        }
    }

    /// Manager of a group: driver, originator, or ManagerWorker manager.
    /// Mirrors `bcs-group-v1::can_manage_group`.
    fn can_manage_group(principal: &Principal, group: &DomainGroup) -> bool {
        let actor_id = principal.actor_id();
        actor_id == group.driver_bot
            || actor_id == group.originator()
            || (group.group_strategy == GroupStrategy::ManagerWorker
                && group.participants.iter().any(|p| {
                    p.bot_uuid == actor_id && p.role == ParticipantRole::Manager
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
        if !Self::can_manage_group(principal, &group) {
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

    /// Resolve the joining Bot for an accept call. A Bot Principal joins as
    /// itself (any supplied `bot_uuid` is ignored); a Human Principal must
    /// supply a `bot_uuid` it owns.
    async fn resolve_joining_bot(
        &self,
        principal: &Principal,
        bot_uuid: Option<&str>,
    ) -> Result<String, ApplicationError> {
        match principal {
            Principal::Bot(bot) => {
                self.authorize_bot_resource(principal, &bot.bot_uuid).await?;
                Ok(bot.bot_uuid.clone())
            }
            Principal::Human(_) => {
                let bot_uuid = bot_uuid.ok_or_else(|| {
                    ApplicationError::invalid(
                        "invalid_request",
                        "bot_uuid is required for a Human Principal accepting an invitation",
                    )
                })?;
                self.authorize_bot_resource(principal, bot_uuid).await?;
                Ok(bot_uuid.to_string())
            }
        }
    }

    // ── friendship projections ─────────────────────────────────────────

    async fn ensure_bot_resource(
        &self,
        principal: &Principal,
        bot_uuid: &str,
    ) -> Result<(), ApplicationError> {
        self.authorize_bot_resource(principal, bot_uuid).await
    }
}

#[async_trait]
impl InvitationService for InvitationFriendshipServiceImpl {
    async fn create_group_invitation(
        &self,
        command: CreateGroupInvitation,
    ) -> Result<Invitation, ApplicationError> {
        self.load_manageable_group(&command.principal, &command.group_id)
            .await?;
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
        // Manager of the parent group may mint a session invitation, mirroring
        // the legacy `create_session_invite_token` authorization.
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
        if !Self::can_manage_group(&command.principal, &group) {
            return Err(ApplicationError::forbidden(
                "Only the Group originator, driver, or manager may manage Sessions",
            ));
        }
        Ok(self.mint_invitation(
            InvitationTargetType::Session,
            &command.session_id,
            command.expires_in_seconds,
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
        let joining_bot = self
            .resolve_joining_bot(&command.principal, command.bot_uuid.as_deref())
            .await?;

        match target_type {
            InviteTargetType::Group => self.accept_group_invitation(&payload.id, &joining_bot).await,
            InviteTargetType::Session => {
                self.accept_session_invitation(&payload.id, &joining_bot)
                    .await
            }
        }
    }
}

impl InvitationFriendshipServiceImpl {
    async fn accept_group_invitation(
        &self,
        group_id: &str,
        joining_bot: &str,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        // The V1 `acceptInvitation` 404 contract declares only
        // `invitation_not_found`; map both a missing target (None) and a
        // `GroupNotFound` storage error to that code so the contract stays
        // clean and the target type existence is not leaked.
        let group = self
            .groups
            .try_get(group_id)
            .await
            .map_err(|e| match e {
                ServiceError::GroupNotFound(_) => ApplicationError::not_found(
                    "invitation_not_found",
                    format!("Invitation target Group '{group_id}' was not found"),
                ),
                other => map_service_error(other),
            })?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "invitation_not_found",
                    format!("Invitation target Group '{group_id}' was not found"),
                )
            })?;
        if group
            .participants
            .iter()
            .any(|p| p.bot_uuid == joining_bot)
        {
            return Ok(InvitationAcceptResult {
                target_type: InvitationTargetType::Group,
                target_id: group_id.to_string(),
                joined: false,
                already_joined: Some(true),
            });
        }
        let participant = Participant::bot(joining_bot.to_string(), ParticipantRole::Consultant);
        self.groups
            .add_participant(group_id, participant)
            .await
            .map_err(map_service_error)?;
        Ok(InvitationAcceptResult {
            target_type: InvitationTargetType::Group,
            target_id: group_id.to_string(),
            joined: true,
            already_joined: Some(false),
        })
    }

    async fn accept_session_invitation(
        &self,
        session_id: &str,
        joining_bot: &str,
    ) -> Result<InvitationAcceptResult, ApplicationError> {
        // The V1 `acceptInvitation` 404 contract declares only
        // `invitation_not_found`; map both a missing target (None) and a
        // `SessionUseCaseError::NotFound` to that code so the contract stays
        // clean and the target type existence is not leaked.
        let session = self
            .sessions
            .get(session_id)
            .await
            .map_err(|e| match e {
                SessionUseCaseError::NotFound(_) => ApplicationError::not_found(
                    "invitation_not_found",
                    format!("Invitation target Session '{session_id}' was not found"),
                ),
                other => map_session_error(other),
            })?
            .ok_or_else(|| {
                ApplicationError::not_found(
                    "invitation_not_found",
                    format!("Invitation target Session '{session_id}' was not found"),
                )
            })?;
        if session
            .participants
            .iter()
            .any(|p| p.bot_uuid == joining_bot)
        {
            return Ok(InvitationAcceptResult {
                target_type: InvitationTargetType::Session,
                target_id: session_id.to_string(),
                joined: false,
                already_joined: Some(true),
            });
        }
        let participant = Participant::bot(joining_bot.to_string(), ParticipantRole::Consultant);
        self.sessions
            .add_participant(session_id, participant)
            .await
            .map_err(map_session_error)?;
        Ok(InvitationAcceptResult {
            target_type: InvitationTargetType::Session,
            target_id: session_id.to_string(),
            joined: true,
            already_joined: Some(false),
        })
    }
}

#[async_trait]
impl FriendshipService for InvitationFriendshipServiceImpl {
    async fn list_bot_friendships(
        &self,
        command: ListBotFriendships,
    ) -> Result<Page<Friendship>, ApplicationError> {
        self.ensure_bot_resource(&command.principal, &command.bot_uuid)
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
        self.ensure_bot_resource(&command.principal, &command.bot_uuid)
            .await?;
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
        self.ensure_bot_resource(&command.principal, &command.bot_uuid)
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
        self.ensure_bot_resource(&command.principal, &command.bot_uuid)
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
        self.ensure_bot_resource(&command.principal, &request.to_bot)
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
        self.ensure_bot_resource(&command.principal, &request.to_bot)
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
