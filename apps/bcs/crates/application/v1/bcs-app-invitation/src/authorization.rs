//! V1 Invitation + Friendship facade: authorization surface
//! (plan Task 12, spec §12.1/§12.4/§12.5).
//!
//! Every mixed-identity question — whether the verified Human may act as a
//! particular Bot, may manage a Bot's friendships, or may decide a request —
//! resolves through the asynchronous [`BotAuthorityHook`] over the LIVE role
//! facts. The legacy `created_by` value is NOT an authority answer anymore:
//! a creator who transferred ownership away (or lost their manager role)
//! cannot manage the Bot's friendships through this facade (spec §12.2).
//! The verified Human stays the operator of every write, the application
//! selects the effective actor, and the connect writes receive the REQUIRED
//! [`BotOperationContext`] which the edge-permission store commits with its
//! business audit records in one transaction (spec §12.5).

use std::sync::Arc;

use bcs_service_api::application::v1::friend_connection::FriendConnectionActor;
use bcs_service_api::application::v1::{
    require_authenticated_user, ApplicationError, AuthenticatedCaller, BotAuthorityHook,
    HumanPrincipal, Principal,
};
use bcs_service_api::application::v1::friend_connection::FriendConnectionActorType;
use bcs_service_api::types::{
    BotOperationActor, BotOperationContext, Group as DomainGroup, RegisteredBot,
};
use bcs_service_api::{
    ActorKind, BotRegistryCoreService, GroupCoreService, GroupStrategy, ParticipantRole,
    ServiceError, SessionManagementService,
};

use crate::map_service_error;

/// V1 invitation/friendship facade configuration.
#[derive(Debug, Clone)]
pub struct InvitationFriendshipServiceConfig {
    /// Default invitation token lifetime in seconds when the caller does not
    /// supply `expires_in_seconds`.
    pub default_ttl_seconds: u64,
}

/// OpenAPI v1 Invitation + Friendship facade (split module layout of the
/// former over-limit `lib.rs`, plan Task 12).
///
/// Holds the legacy cores needed for friendship management, invitation token
/// mint/verify (via the shared `bcs_domain` HMAC helpers and `token_secret`),
/// Human-only invitation accept-join, and the friend-connection
/// [`bcs_service_api::application::ConnectService`] delegation. Authorization
/// is answered exclusively by the injected [`BotAuthorityHook`]; the
/// construction-time default is the fail-closed Noop hook (every assembly
/// that serves traffic wires the real hook).
pub struct InvitationFriendshipServiceImpl {
    pub(crate) friends: Arc<dyn bcs_service_api::FriendCoreService>,
    pub(crate) friend_requests: Arc<dyn bcs_service_api::FriendRequestCoreService>,
    pub(crate) groups: Arc<dyn GroupCoreService>,
    pub(crate) sessions: Arc<dyn SessionManagementService>,
    pub(crate) registry: Arc<dyn BotRegistryCoreService>,
    pub(crate) invite: Arc<dyn bcs_service_api::InviteService>,
    pub(crate) connect: Option<Arc<dyn bcs_service_api::application::ConnectService>>,
    pub(crate) authority: Arc<dyn BotAuthorityHook>,
    pub(crate) token_secret: Vec<u8>,
    pub(crate) config: InvitationFriendshipServiceConfig,
}

impl InvitationFriendshipServiceImpl {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        friends: Arc<dyn bcs_service_api::FriendCoreService>,
        friend_requests: Arc<dyn bcs_service_api::FriendRequestCoreService>,
        groups: Arc<dyn GroupCoreService>,
        sessions: Arc<dyn SessionManagementService>,
        registry: Arc<dyn BotRegistryCoreService>,
        invite: Arc<dyn bcs_service_api::InviteService>,
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
            // Fail-closed default: an assembly that forgot to wire the
            // authority hook denies manage decisions — it never falls back
            // to the legacy `created_by` comparison (spec §12.4).
            authority: Arc::new(bcs_service_api::application::v1::NoopBotAuthorityHook),
            token_secret,
            config,
        }
    }

    pub fn with_friend_connection_service(
        mut self,
        connect: Arc<dyn bcs_service_api::application::ConnectService>,
    ) -> Self {
        self.connect = Some(connect);
        self
    }

    /// Wire the live authority hook answering all acting-actor and
    /// manage-resource questions for this facade.
    pub fn with_authority(mut self, authority: Arc<dyn BotAuthorityHook>) -> Self {
        self.authority = authority;
        self
    }

    pub(crate) fn connect_service(
        &self,
    ) -> Result<Arc<dyn bcs_service_api::application::ConnectService>, ApplicationError> {
        self.connect
            .clone()
            .ok_or_else(|| ApplicationError::internal("friend connection service is not configured"))
    }

    // ── resource loads ────────────────────────────────────────────────────

    pub(crate) async fn load_bot(&self, bot_uuid: &str) -> Result<RegisteredBot, ApplicationError> {
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

    // ── authorization (spec §12.1/§12.4) ─────────────────────────────────

    /// Whether the authenticated Human currently owns OR manages the exact
    /// Bot — resolved through the live authority hook only. `created_by`
    /// equality, Bot-ID suffixes and signed owner claims are not answers
    /// here (spec §12.2).
    pub(crate) async fn authority_allows(
        &self,
        user_id: &str,
        bot_uuid: &str,
    ) -> Result<bool, ApplicationError> {
        let allowed = self
            .authority
            .can_manage(user_id, bot_uuid)
            .await
            .map_err(map_authority_hook_error)?;
        if !allowed {
            // A stale `created_by`/owner claim must not leak whether the
            // Bot exists; a miss is simply a deny.
            return Ok(false);
        }
        Ok(true)
    }

    /// The authenticated User must hold a current owner/manager role for the
    /// Bot resource (`created_by` never substitutes for the live role facts).
    pub(crate) async fn authorize_bot_resource(
        &self,
        caller: &AuthenticatedCaller,
        bot_uuid: &str,
    ) -> Result<(), ApplicationError> {
        let user = require_authenticated_user(caller)?;
        if !self.authority_allows(user.id.as_str(), bot_uuid).await? {
            return Err(ApplicationError::forbidden(format!(
                "Authenticated User cannot manage Bot '{bot_uuid}'"
            )));
        }
        Ok(())
    }

    pub(crate) async fn ensure_bot_resource(
        &self,
        caller: &AuthenticatedCaller,
        bot_uuid: &str,
    ) -> Result<(), ApplicationError> {
        self.authorize_bot_resource(caller, bot_uuid).await
    }

    /// Manager of a group: driver, originator, or ManagerWorker manager. A
    /// Human may additionally act through a Bot they currently own or
    /// manage — verified per-exact-Bot through the live role facts, never
    /// by listing historical creations (spec §12.4: `list_bots_by_creator`
    /// keeps a literal creation-source purpose only).
    pub(crate) async fn can_manage_group(
        &self,
        principal: &Principal,
        group: &DomainGroup,
    ) -> Result<bool, ApplicationError> {
        let mut candidate_actor_ids = vec![group.driver_bot.clone(), group.originator().to_string()];
        if group.group_strategy == GroupStrategy::ManagerWorker {
            candidate_actor_ids.extend(
                group
                    .participants
                    .iter()
                    .filter(|p| p.role == ParticipantRole::Manager)
                    .map(|p| p.bot_uuid.clone()),
            );
        }
        let actor_id = principal.actor_id();
        if candidate_actor_ids.iter().any(|candidate| candidate == &actor_id) {
            return Ok(true);
        }
        match principal {
            Principal::Human(human) => self.human_can_act_as_any(human, candidate_actor_ids).await,
            Principal::Bot(_) => Ok(false),
        }
    }

    pub(crate) async fn load_manageable_group(
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

    /// Whether the Human may act as any of `actor_ids`: their own Human
    /// actor, or a Bot they currently own or manage per the LIVE role facts.
    pub(crate) async fn human_can_act_as_any(
        &self,
        human: &HumanPrincipal,
        actor_ids: Vec<String>,
    ) -> Result<bool, ApplicationError> {
        let human_actor_id = format!("human_{}", human.subject.id);
        let mut seen = std::collections::HashSet::new();
        for actor_id in actor_ids {
            if !seen.insert(actor_id.clone()) {
                continue;
            }
            if actor_id == human_actor_id {
                return Ok(true);
            }
            if actor_id.starts_with("human_") {
                continue;
            }
            let Some(bot) = self
                .registry
                .try_get(&actor_id)
                .await
                .map_err(map_service_error)?
            else {
                continue;
            };
            if bot.actor_kind != ActorKind::Bot {
                continue;
            }
            if self.authority_allows(human.subject.id.as_str(), &actor_id).await? {
                return Ok(true);
            }
        }
        Ok(false)
    }

    // ── friend-connection acting-actor resolution ──────────────────────

    pub(crate) fn caller_default_actor(
        caller: &AuthenticatedCaller,
    ) -> Result<String, ApplicationError> {
        if let Some(bot) = &caller.bot {
            return Ok(bot.bot_uuid.clone());
        }
        let user = require_authenticated_user(caller)?;
        Ok(format!("human_{}", user.id))
    }

    pub(crate) fn actor_to_internal(actor: &FriendConnectionActor) -> String {
        match actor.actor_type {
            FriendConnectionActorType::Human => format!("human_{}", actor.id),
            FriendConnectionActorType::Bot => actor.id.clone(),
        }
    }

    pub(crate) fn actor_from_internal(actor_id: &str) -> FriendConnectionActor {
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

    /// Resolve the acting actor for a friend-connection command. The
    /// requested Bot requires the authenticated User to hold a CURRENT
    /// owner/manager role for it (spec §12.1(5)); the legacy `created_by`
    /// equality is not an answer. A Bot caller may only act as itself.
    pub(crate) async fn resolve_acting_actor(
        &self,
        caller: &AuthenticatedCaller,
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
        if let Some(user) = &caller.user {
            if requested.actor_type == FriendConnectionActorType::Bot {
                let _ = self.load_bot(&requested.id).await?;
                if self.authority_allows(user.id.as_str(), &requested.id).await? {
                    return Ok(requested_id);
                }
            }
        }
        Err(ApplicationError::forbidden(format!(
            "Authenticated principal cannot act as actor '{requested_id}'"
        )))
    }

    /// Resolve the decider for a pending request pointed at `request.to_id`.
    /// A Human deciding for the target Bot needs a CURRENT owner/manager
    /// role; a Bot caller decides as itself. Friend approvals never take a
    /// manager audit identity: the permission-request lane and the role
    /// lifecycle stay separate (spec §12.3), so the decider recorded on the
    /// request is an actor id from this lane only.
    pub(crate) async fn resolve_request_decider(
        &self,
        caller: &AuthenticatedCaller,
        request: &bcs_domain::edge_permission::PermissionRequest,
    ) -> Result<String, ApplicationError> {
        let default_actor = Self::caller_default_actor(caller)?;
        if request.to_id == default_actor {
            return Ok(default_actor);
        }
        if let Some(user) = &caller.user {
            if !request.to_id.starts_with("human_") {
                let _ = self.load_bot(&request.to_id).await?;
                if self.authority_allows(user.id.as_str(), &request.to_id).await? {
                    return Ok(request.to_id.clone());
                }
            }
        }
        Err(ApplicationError::forbidden(format!(
            "Authenticated principal cannot decide request '{}'",
            request.request_id
        )))
    }

    /// The canceller must be the request's own creator/requester actor —
    /// resolved through the same live-role rule for Humans acting as Bots.
    pub(crate) async fn ensure_can_cancel_request(
        &self,
        caller: &AuthenticatedCaller,
        request: &bcs_domain::edge_permission::PermissionRequest,
    ) -> Result<(), ApplicationError> {
        let default_actor = Self::caller_default_actor(caller)?;
        if request.created_by == default_actor || request.from_id == default_actor {
            return Ok(());
        }
        if let Some(user) = &caller.user {
            if !request.from_id.starts_with("human_") {
                let _ = self.load_bot(&request.from_id).await?;
                if self.authority_allows(user.id.as_str(), &request.from_id).await? {
                    return Ok(());
                }
            }
        }
        Err(ApplicationError::forbidden(format!(
            "Authenticated principal cannot cancel request '{}'",
            request.request_id
        )))
    }

    // ── §12.5 operation contexts for the connect lane ───────────────────

    /// Build the REQUIRED audit context of a connect-lane write: the
    /// operator is the verified caller (a Human keeps its trusted user id; a
    /// Bot-only caller keeps its verified Bot identity — spec §12.5 operator
    /// ids never come from a request body), and the effective actor is the
    /// acting actor this facade selected above.
    pub(crate) fn connect_operation_context(
        caller: &AuthenticatedCaller,
        effective_actor: &str,
        label: &str,
    ) -> BotOperationContext {
        let actor = match &caller.user {
            Some(user) => BotOperationActor::Human {
                user_id: user.id.clone(),
                effective_actor_id: effective_actor.to_string(),
            },
            // Bot-only caller: no Human was involved, never a forged one.
            None => BotOperationActor::Bot {
                bot_id: effective_actor.to_string(),
            },
        };
        BotOperationContext {
            operation_id: format!("v1-friend-{label}:{}", uuid::Uuid::new_v4()),
            actor,
        }
    }
}

/// Authority-hook failures stay application-visible fail-closed: an unreadable
/// authority never degrades into an allowance (spec §13.5).
pub(crate) fn map_authority_hook_error(error: ServiceError) -> ApplicationError {
    ApplicationError::internal(format!("bot authority resolution failed: {error}"))
}